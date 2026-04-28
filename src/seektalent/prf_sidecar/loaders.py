from __future__ import annotations

import os
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Protocol, Sequence

from seektalent.config import AppSettings
from seektalent.prf_sidecar.models import SpanExtractRow


class _UnavailableSentenceTransformer:
    def __new__(cls, *args, **kwargs):  # pragma: no cover - exercised via monkeypatch in tests
        raise RuntimeError("sentence-transformers is not available")


SentenceTransformer = _UnavailableSentenceTransformer


class RemoteCodePolicyError(RuntimeError):
    """Raised when the selected sidecar model requires remote code but policy forbids it."""


class SpanModel(Protocol):
    def extract(self, texts: Sequence[str], labels: Sequence[str]) -> list[SpanExtractRow]:
        ...


class EmbeddingModel(Protocol):
    embedding_dimension: int
    normalized: bool
    pooling: str
    dtype: str
    max_input_tokens: int
    truncation: bool

    def embed(self, phrases: Sequence[str]) -> list[list[float]]:
        ...


@dataclass
class FakeSpanModel:
    rows: list[SpanExtractRow] = field(default_factory=list)

    def extract(self, texts: Sequence[str], labels: Sequence[str]) -> list[SpanExtractRow]:
        return list(self.rows)


@dataclass
class FakeEmbeddingModel:
    vectors: list[list[float]] = field(default_factory=lambda: [[0.1, 0.2]])
    embedding_dimension: int = 2
    normalized: bool = True
    pooling: str = "mean"
    dtype: str = "float32"
    max_input_tokens: int = 8192
    truncation: bool = True

    def embed(self, phrases: Sequence[str]) -> list[list[float]]:
        if not phrases:
            return []
        if len(self.vectors) >= len(phrases):
            return [list(vector) for vector in self.vectors[: len(phrases)]]
        return [list(self.vectors[0]) for _ in phrases]


@dataclass
class RealSpanModel:
    gliner_runtime: object

    def extract(self, texts: Sequence[str], labels: Sequence[str]) -> list[SpanExtractRow]:
        rows: list[SpanExtractRow] = []
        for request_text_index, text in enumerate(texts):
            extracted = _extract_runtime_entities(self.gliner_runtime, text, labels)
            rows.extend(
                _make_span_rows(
                    request_text_index=request_text_index,
                    extracted=extracted,
                )
            )
        return rows


@dataclass
class RealEmbeddingModel:
    encoder: object
    embedding_dimension: int = 768
    normalized: bool = True
    pooling: str = "mean"
    dtype: str = "float32"
    max_input_tokens: int = 8192
    truncation: bool = True

    def embed(self, phrases: Sequence[str]) -> list[list[float]]:
        if hasattr(self.encoder, "encode"):
            raw_vectors = self.encoder.encode(
                list(phrases),
                normalize_embeddings=self.normalized,
            )
            return [list(vector) for vector in raw_vectors]
        return [[0.0] * self.embedding_dimension for _ in phrases]


def configure_hf_runtime_environment(settings: AppSettings) -> None:
    if settings.prf_sidecar_serve_mode == "prod-serve":
        os.environ.setdefault("HF_HUB_OFFLINE", "1")


def _load_sentence_transformers_dependency() -> None:
    global SentenceTransformer
    sentence_transformers = import_module("sentence_transformers")
    SentenceTransformer = sentence_transformers.SentenceTransformer


def _load_gliner_runtime_class():
    gliner2 = import_module("gliner2")
    return gliner2.GLiNER2


def _load_gliner_runtime(settings: AppSettings) -> object:
    runtime_class = _load_gliner_runtime_class()
    return runtime_class.from_pretrained(
        settings.prf_span_model_name,
        revision=settings.prf_span_model_revision,
        local_files_only=settings.prf_sidecar_serve_mode == "prod-serve",
    )


def _embedding_model_requires_remote_code(settings: AppSettings) -> bool:
    return settings.prf_embedding_model_name.startswith("Alibaba-NLP/gte-multilingual")


def _embedding_remote_code_allowed(settings: AppSettings) -> bool:
    return settings.prf_allow_remote_code and bool(settings.prf_remote_code_audit_revision)


def _extract_runtime_entities(runtime: object, text: str, labels: Sequence[str]) -> object:
    if hasattr(runtime, "predict_entities"):
        return runtime.predict_entities(text, list(labels))
    if hasattr(runtime, "extract_entities"):
        return runtime.extract_entities(text, list(labels))
    raise RuntimeError("GLiNER2 runtime does not expose predict_entities or extract_entities")


def _make_span_rows(*, request_text_index: int, extracted: object) -> list[SpanExtractRow]:
    rows: list[SpanExtractRow] = []
    for entity in _iter_entity_candidates(extracted):
        surface = str(entity.get("surface") or entity.get("text") or "").strip()
        if not surface:
            continue
        label = str(entity.get("label") or "unknown")
        score = float(entity.get("score", 1.0))
        start = entity.get("start") or entity.get("start_char")
        end = entity.get("end") or entity.get("end_char")
        rows.append(
            SpanExtractRow(
                request_text_index=request_text_index,
                surface=surface,
                label=label,
                score=max(0.0, min(1.0, score)),
                model_start_char=int(start) if isinstance(start, int) else None,
                model_end_char=int(end) if isinstance(end, int) else None,
                alignment_hint_only=True,
            )
        )
    return rows


def _iter_entity_candidates(extracted: object) -> list[dict[str, Any]]:
    if isinstance(extracted, list):
        return [item for item in extracted if isinstance(item, dict)]
    if isinstance(extracted, dict):
        entities = extracted.get("entities", extracted)
        if isinstance(entities, list):
            return [item for item in entities if isinstance(item, dict)]
        if isinstance(entities, dict):
            candidates: list[dict[str, Any]] = []
            for label, values in entities.items():
                if not isinstance(values, list):
                    continue
                for value in values:
                    if isinstance(value, dict):
                        candidate = dict(value)
                        candidate.setdefault("label", label)
                        candidates.append(candidate)
                    else:
                        candidates.append({"label": label, "surface": str(value)})
            return candidates
    return []


def build_span_model(settings: AppSettings) -> SpanModel:
    configure_hf_runtime_environment(settings)
    gliner_runtime = _load_gliner_runtime(settings)
    return RealSpanModel(gliner_runtime=gliner_runtime)


def build_embedding_model(settings: AppSettings) -> EmbeddingModel:
    configure_hf_runtime_environment(settings)
    if _embedding_model_requires_remote_code(settings) and not _embedding_remote_code_allowed(settings):
        raise RemoteCodePolicyError(
            f"embedding model {settings.prf_embedding_model_name!r} requires approved remote code"
        )
    _load_sentence_transformers_dependency()
    encoder = SentenceTransformer(
        settings.prf_embedding_model_name,
        revision=settings.prf_embedding_model_revision,
        local_files_only=settings.prf_sidecar_serve_mode == "prod-serve",
        trust_remote_code=_embedding_remote_code_allowed(settings),
    )
    return RealEmbeddingModel(encoder=encoder)
