from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from seektalent.config import AppSettings
from seektalent.prf_sidecar.loaders import (
    EmbeddingModel,
    RemoteCodePolicyError,
    SpanModel,
    build_embedding_model,
    build_span_model,
)
from seektalent.prf_sidecar.models import (
    EmbedRequest,
    EmbedResponse,
    SidecarDependencyManifest,
    SpanExtractRequest,
    SpanExtractResponse,
)


class MissingPinnedModelCacheError(RuntimeError):
    """Raised when prod-serve requires pinned model cache that is unavailable."""


class BatchLimitError(ValueError):
    def __init__(self, *, request_id: str, limit: int) -> None:
        super().__init__(f"request exceeded max batch size {limit}")
        self.request_id = request_id
        self.limit = limit


class PayloadLimitError(ValueError):
    def __init__(self, *, request_id: str, limit: int) -> None:
        super().__init__(f"request exceeded max payload bytes {limit}")
        self.request_id = request_id
        self.limit = limit


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    request_id: str | None = None


class LiveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["alive"]


class ReadyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "not_ready"]
    endpoint_contract_version: str
    dependency_manifest_hash: str | None
    sidecar_image_digest: str | None
    span_model_loaded: bool
    embedding_model_loaded: bool
    span_model_name: str
    span_model_revision: str
    span_tokenizer_revision: str
    embedding_model_name: str
    embedding_model_revision: str
    not_ready_reason: str | None = None


@dataclass
class SidecarService:
    settings: AppSettings
    span_model: SpanModel | None = None
    embedding_model: EmbeddingModel | None = None
    dependency_manifest: SidecarDependencyManifest | None = None
    not_ready_reason: str | None = None

    def live(self) -> LiveResponse:
        return LiveResponse(status="alive")

    def ready(self) -> ReadyResponse:
        dependency_manifest_hash = (
            self.dependency_manifest.compute_hash() if self.dependency_manifest else None
        )
        ready = (
            self.span_model is not None
            and self.embedding_model is not None
            and dependency_manifest_hash is not None
            and self.not_ready_reason is None
        )
        return ReadyResponse(
            status="ready" if ready else "not_ready",
            endpoint_contract_version=self.settings.prf_sidecar_endpoint_contract_version,
            dependency_manifest_hash=dependency_manifest_hash,
            sidecar_image_digest=(
                self.dependency_manifest.sidecar_image_digest if self.dependency_manifest is not None else None
            ),
            span_model_loaded=self.span_model is not None,
            embedding_model_loaded=self.embedding_model is not None,
            span_model_name=self.settings.prf_span_model_name,
            span_model_revision=self.settings.prf_span_model_revision,
            span_tokenizer_revision=self.settings.prf_span_tokenizer_revision,
            embedding_model_name=self.settings.prf_embedding_model_name,
            embedding_model_revision=self.settings.prf_embedding_model_revision,
            not_ready_reason=self.not_ready_reason,
        )

    def span_extract(self, request: SpanExtractRequest) -> SpanExtractResponse:
        enforce_batch_limit(
            request_id=request.request_id,
            item_count=len(request.texts),
            max_batch_size=self.settings.prf_sidecar_max_batch_size,
        )
        enforce_payload_limit(
            request_id=request.request_id,
            payload={
                "texts": request.texts,
                "labels": request.labels,
                "schema_version": request.schema_version,
            },
            max_payload_bytes=self.settings.prf_sidecar_max_payload_bytes,
        )
        if self.span_model is None:
            raise RuntimeError("span model is not loaded")
        rows = self.span_model.extract(request.texts, request.labels)
        return SpanExtractResponse(
            schema_version="prf-sidecar-span-v1",
            model_name=request.model_name,
            model_revision=request.model_revision,
            rows=rows,
        )

    def embed(self, request: EmbedRequest) -> EmbedResponse:
        enforce_batch_limit(
            request_id=request.request_id,
            item_count=len(request.phrases),
            max_batch_size=self.settings.prf_sidecar_max_batch_size,
        )
        enforce_payload_limit(
            request_id=request.request_id,
            payload={"phrases": request.phrases},
            max_payload_bytes=self.settings.prf_sidecar_max_payload_bytes,
        )
        if self.embedding_model is None:
            raise RuntimeError("embedding model is not loaded")
        vectors = self.embedding_model.embed(request.phrases)
        return EmbedResponse(
            schema_version="prf-sidecar-embed-v1",
            model_name=request.model_name,
            model_revision=request.model_revision,
            embedding_dimension=self.embedding_model.embedding_dimension,
            normalized=self.embedding_model.normalized,
            pooling=self.embedding_model.pooling,
            dtype=self.embedding_model.dtype,
            max_input_tokens=self.embedding_model.max_input_tokens,
            truncation=self.embedding_model.truncation,
            vectors=vectors,
        )


def resolve_sidecar_bind_host(settings: AppSettings) -> str:
    if settings.prf_sidecar_profile == "docker-internal":
        return "0.0.0.0"
    return settings.prf_sidecar_bind_host


def ensure_prod_cache_requirements(
    settings: AppSettings,
    *,
    cache_state: dict[str, bool] | None = None,
) -> None:
    if settings.prf_sidecar_serve_mode != "prod-serve":
        return
    current = cache_state or {}
    if not current.get("span") or not current.get("embed"):
        raise MissingPinnedModelCacheError("pinned model cache is incomplete for prod-serve")


def enforce_batch_limit(*, request_id: str, item_count: int, max_batch_size: int) -> None:
    if item_count > max_batch_size:
        raise BatchLimitError(request_id=request_id, limit=max_batch_size)


def enforce_payload_limit(*, request_id: str, payload: object, max_payload_bytes: int) -> None:
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if size > max_payload_bytes:
        raise PayloadLimitError(request_id=request_id, limit=max_payload_bytes)


def build_default_sidecar_service(
    *,
    settings: AppSettings,
    span_model: SpanModel | None = None,
    embedding_model: EmbeddingModel | None = None,
    dependency_manifest: SidecarDependencyManifest | None = None,
    load_models: bool = False,
) -> SidecarService:
    not_ready_reason: str | None = None
    if load_models:
        try:
            ensure_prod_cache_requirements(settings)
            span_model = span_model or build_span_model(settings)
            embedding_model = embedding_model or build_embedding_model(settings)
            dependency_manifest = dependency_manifest or build_dependency_manifest(
                settings,
                embedding_model=embedding_model,
            )
        except MissingPinnedModelCacheError as exc:
            not_ready_reason = str(exc)
        except RemoteCodePolicyError:
            not_ready_reason = "remote_code_disallowed"
        except Exception as exc:  # pragma: no cover - exercised in higher-level integration later
            not_ready_reason = f"loader_error:{exc.__class__.__name__}"
    return SidecarService(
        settings=settings,
        span_model=span_model,
        embedding_model=embedding_model,
        dependency_manifest=dependency_manifest,
        not_ready_reason=not_ready_reason,
    )


def build_dependency_manifest(
    settings: AppSettings,
    *,
    embedding_model: EmbeddingModel,
) -> SidecarDependencyManifest:
    return SidecarDependencyManifest(
        sidecar_image_digest=os.environ.get("SEEKTALENT_PRF_SIDECAR_IMAGE_DIGEST", "unknown"),
        python_lockfile_hash=_python_lockfile_hash(),
        torch_version=_package_version("torch"),
        transformers_version=_package_version("transformers"),
        sentence_transformers_version=_optional_package_version("sentence-transformers"),
        gliner_runtime_version=_package_version("gliner2"),
        span_model_name=settings.prf_span_model_name,
        span_model_commit=settings.prf_span_model_revision or "unpinned",
        span_tokenizer_commit=settings.prf_span_tokenizer_revision or "unpinned",
        embedding_model_name=settings.prf_embedding_model_name,
        embedding_model_commit=settings.prf_embedding_model_revision or "unpinned",
        remote_code_policy="approved_baked_code" if settings.prf_allow_remote_code else "disabled",
        remote_code_commit=settings.prf_remote_code_audit_revision if settings.prf_allow_remote_code else None,
        license_status="approved",
        embedding_normalization=embedding_model.normalized,
        embedding_dimension=embedding_model.embedding_dimension,
        dtype=embedding_model.dtype,
        max_input_tokens=embedding_model.max_input_tokens,
    )


def _python_lockfile_hash() -> str:
    lockfile = Path(__file__).resolve().parents[3] / "uv.lock"
    if not lockfile.exists():
        return "missing"
    return sha256(lockfile.read_bytes()).hexdigest()


def _optional_package_version(distribution_name: str) -> str | None:
    try:
        return version(distribution_name)
    except PackageNotFoundError:
        return None


def _package_version(distribution_name: str) -> str:
    return _optional_package_version(distribution_name) or "unavailable"
