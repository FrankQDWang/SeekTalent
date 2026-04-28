from __future__ import annotations

import re
import unicodedata

from seektalent.candidate_feedback.span_models import CandidateSpan

_CONFUSABLE_PAIR_REASONS: dict[frozenset[str], str] = {
    frozenset({"java", "javascript"}): "confusable_pair_java_javascript",
    frozenset({"react", "reactnative"}): "confusable_pair_react_native",
    frozenset({"数据仓库", "数据平台"}): "confusable_pair_data_platform",
}

_LEXICAL_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")


def canonicalize_surface(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\s\-_]+", "", normalized)


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


def _tokenize_surface(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return [match.group(0) for match in _LEXICAL_TOKEN_RE.finditer(normalized)]
