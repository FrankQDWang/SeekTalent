from __future__ import annotations

import re
from collections.abc import Sequence

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9+.#/-]+|[\u4e00-\u9fff]+")


def normalized_query_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def query_terms_hit(query_terms: Sequence[str], capability: str) -> int:
    capability_tokens = _normalized_tokens(capability)
    if not capability_tokens:
        return 0
    for term in query_terms:
        if _tokens_hit(_normalized_tokens(term), capability_tokens):
            return 1
    return 0


def _normalized_tokens(value: object) -> list[str]:
    return TOKEN_PATTERN.findall(normalized_query_text(value).casefold())


def _tokens_hit(term_tokens: list[str], capability_tokens: list[str]) -> bool:
    if not term_tokens or not capability_tokens:
        return False
    if term_tokens == capability_tokens:
        return True
    if len(term_tokens) == 1:
        return term_tokens[0] in capability_tokens
    if len(capability_tokens) == 1:
        return capability_tokens[0] in term_tokens
    return _is_contiguous_subsequence(term_tokens, capability_tokens) or _is_contiguous_subsequence(
        capability_tokens,
        term_tokens,
    )


def _is_contiguous_subsequence(needle: list[str], haystack: list[str]) -> bool:
    if len(needle) > len(haystack):
        return False
    return any(
        haystack[index : index + len(needle)] == needle
        for index in range(len(haystack) - len(needle) + 1)
    )


__all__ = ["normalized_query_text", "query_terms_hit"]
