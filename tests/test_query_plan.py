from seektalent.models import QueryTermCandidate
from seektalent.retrieval.query_plan import (
    canonicalize_controller_query_terms,
    select_query_terms,
    serialize_keyword_query,
)


def test_query_plan_enforces_round_budget() -> None:
    pool = [
        QueryTermCandidate(
            term="python",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="job title",
            first_added_round=0,
        ),
        QueryTermCandidate(
            term="resume matching",
            source="jd",
            category="domain",
            priority=2,
            evidence="jd",
            first_added_round=0,
        ),
    ]
    terms = canonicalize_controller_query_terms(
        [" python ", "resume matching"],
        round_no=1,
        title_anchor_term="python",
        query_term_pool=pool,
    )
    assert terms == ["python", "resume matching"]


def test_query_plan_serializes_terms_with_quotes() -> None:
    assert serialize_keyword_query(["python", 'resume matching', 'Pydantic "AI"']) == (
        'python "resume matching" "Pydantic \\"AI\\""'
    )


def test_query_plan_selects_only_active_terms() -> None:
    pool = [
        QueryTermCandidate(
            term="python",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="title",
            first_added_round=0,
        ),
        QueryTermCandidate(
            term="resume matching",
            source="jd",
            category="domain",
            priority=2,
            evidence="jd",
            first_added_round=0,
        ),
        QueryTermCandidate(
            term="trace",
            source="jd",
            category="tooling",
            priority=3,
            evidence="jd",
            first_added_round=0,
            active=False,
        ),
    ]

    assert select_query_terms(pool, round_no=1, title_anchor_term="python") == ["python", "resume matching"]
