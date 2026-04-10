from __future__ import annotations

from seektalent.query_terms import query_terms_hit


def test_query_terms_hit_uses_token_boundaries_for_short_terms() -> None:
    assert query_terms_hit(["Go"], "MongoDB") == 0
    assert query_terms_hit(["C"], "React") == 0


def test_query_terms_hit_matches_exact_tokens_and_phrases() -> None:
    assert query_terms_hit(["python backend"], "python backend engineer") == 1
    assert query_terms_hit(["ranking"], "retrieval ranking") == 1
    assert query_terms_hit(["后端 工程师"], "资深 后端 工程师") == 1
