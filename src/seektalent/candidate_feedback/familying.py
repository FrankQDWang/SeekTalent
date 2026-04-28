from __future__ import annotations

import unicodedata

from seektalent.candidate_feedback.span_models import CandidateSpan

_CONFUSABLE_PAIR_REASONS: dict[frozenset[str], str] = {
    frozenset({"java", "javascript"}): "confusable_pair_java_javascript",
    frozenset({"react", "reactnative"}): "confusable_pair_react_native",
    frozenset({"数据仓库", "数据平台"}): "confusable_pair_data_platform",
}


def canonicalize_surface(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def should_merge_spans(
    left: CandidateSpan,
    right: CandidateSpan,
    *,
    embedding_similarity: float,
    similarity_threshold: float = 0.92,
) -> tuple[bool, str]:
    left_surface = canonicalize_surface(left.raw_surface)
    right_surface = canonicalize_surface(right.raw_surface)
    pair_key = frozenset({left_surface, right_surface})

    if pair_key in _CONFUSABLE_PAIR_REASONS:
        return False, _CONFUSABLE_PAIR_REASONS[pair_key]

    if left_surface == right_surface:
        return True, "canonical_surface_match"

    if embedding_similarity < similarity_threshold:
        return False, "embedding_similarity_below_threshold"

    return True, "embedding_similarity_match"
