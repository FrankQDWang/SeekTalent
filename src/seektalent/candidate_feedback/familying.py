from __future__ import annotations

import math
import re
import unicodedata
from typing import Protocol

from seektalent.candidate_feedback.span_models import CandidateSpan
from seektalent.prf_sidecar.client import (
    SidecarEmbeddingUnavailable,
    SidecarMalformedResponse,
    SidecarRevisionMismatch,
    SidecarSchemaMismatch,
    SidecarTimeout,
    SidecarUnavailable,
)
from seektalent.prf_sidecar.models import EmbedResponse

_CONFUSABLE_PAIR_REASONS: dict[frozenset[str], str] = {
    frozenset({"java", "javascript"}): "confusable_pair_java_javascript",
    frozenset({"react", "reactnative"}): "confusable_pair_react_native",
    frozenset({"数据仓库", "数据平台"}): "confusable_pair_data_platform",
}

_LEXICAL_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")
_EMBEDDING_BACKEND_ERRORS = (
    SidecarTimeout,
    SidecarUnavailable,
    SidecarSchemaMismatch,
    SidecarMalformedResponse,
    SidecarRevisionMismatch,
    SidecarEmbeddingUnavailable,
)


class EmbeddingBackend(Protocol):
    def embed(self, phrases: list[str]) -> EmbedResponse: ...


def canonicalize_surface(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\s\-_/]+", "", normalized)


def should_merge_spans(
    left: CandidateSpan,
    right: CandidateSpan,
    *,
    embedding_similarity: float,
    similarity_threshold: float = 0.92,
) -> tuple[bool, str]:
    left_surface = canonicalize_surface(left.normalized_surface)
    right_surface = canonicalize_surface(right.normalized_surface)
    pair_key = frozenset({left_surface, right_surface})

    if pair_key in _CONFUSABLE_PAIR_REASONS:
        return False, _CONFUSABLE_PAIR_REASONS[pair_key]

    if left_surface == right_surface:
        return True, "canonical_surface_match"

    if not _has_lexical_anchor(left.normalized_surface, right.normalized_surface):
        return False, "missing_lexical_anchor"

    if embedding_similarity < similarity_threshold:
        return False, "embedding_similarity_below_threshold"

    return True, "embedding_similarity_match"


def _has_lexical_anchor(left_surface: str, right_surface: str) -> bool:
    left_tokens = {token for token in _tokenize_surface(left_surface) if len(token) >= 2}
    right_tokens = {token for token in _tokenize_surface(right_surface) if len(token) >= 2}
    return bool(left_tokens & right_tokens)


def build_embedding_similarity(backend: EmbeddingBackend):
    def similarity(left: CandidateSpan, right: CandidateSpan) -> float:
        try:
            response = backend.embed([left.normalized_surface, right.normalized_surface])
            if len(response.vectors) != 2:
                raise SidecarSchemaMismatch("embedding backend must return exactly two vectors for familying")
            return _cosine_similarity(response.vectors[0], response.vectors[1])
        except _EMBEDDING_BACKEND_ERRORS:
            if hasattr(backend, "last_failure_reason"):
                setattr(backend, "last_failure_reason", "embedding_backend_unavailable")
            return _exact_surface_similarity(left, right)

    return similarity


def _tokenize_surface(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return [match.group(0) for match in _LEXICAL_TOKEN_RE.finditer(normalized)]


def _exact_surface_similarity(left: CandidateSpan, right: CandidateSpan) -> float:
    if left.normalized_surface.casefold() == right.normalized_surface.casefold():
        return 1.0
    return 0.0


def _cosine_similarity(left_vector: list[float], right_vector: list[float]) -> float:
    if len(left_vector) != len(right_vector):
        raise SidecarSchemaMismatch("embedding vectors must share the same dimension")

    numerator = sum(left * right for left, right in zip(left_vector, right_vector, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left_vector))
    right_norm = math.sqrt(sum(value * value for value in right_vector))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)
