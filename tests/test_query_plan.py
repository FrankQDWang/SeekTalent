import pytest

from seektalent.models import QueryTermCandidate, SentQueryRecord
from seektalent.retrieval.query_plan import (
    canonicalize_controller_query_terms,
    derive_explore_query_terms,
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


def test_query_plan_accepts_compiled_anchor_without_literal_title_anchor() -> None:
    pool = [
        QueryTermCandidate(
            term="Platform",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="compiled title",
            first_added_round=0,
            retrieval_role="role_anchor",
            queryability="admitted",
            family="role.platform",
        ),
        QueryTermCandidate(
            term="Python",
            source="jd",
            category="domain",
            priority=2,
            evidence="jd",
            first_added_round=0,
            retrieval_role="domain_context",
            queryability="admitted",
            family="domain.python",
        ),
    ]

    assert canonicalize_controller_query_terms(
        ["Platform", "Python"],
        round_no=1,
        title_anchor_term="Platform Engineer",
        query_term_pool=pool,
    ) == ["Platform", "Python"]


def test_query_plan_rejects_non_admitted_terms() -> None:
    pool = [
        QueryTermCandidate(
            term="Platform",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="compiled title",
            first_added_round=0,
            retrieval_role="role_anchor",
            queryability="admitted",
            family="role.platform",
        ),
        QueryTermCandidate(
            term="211",
            source="jd",
            category="domain",
            priority=2,
            evidence="jd",
            first_added_round=0,
            active=False,
            retrieval_role="filter_only",
            queryability="filter_only",
            family="constraint.school_type",
        ),
    ]

    with pytest.raises(ValueError, match="compiler-admitted"):
        canonicalize_controller_query_terms(
            ["Platform", "211"],
            round_no=1,
            title_anchor_term="Platform Engineer",
            query_term_pool=pool,
        )


def test_query_plan_rejects_duplicate_families() -> None:
    pool = [
        QueryTermCandidate(
            term="Platform",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="compiled title",
            first_added_round=0,
            retrieval_role="role_anchor",
            queryability="admitted",
            family="role.platform",
        ),
        QueryTermCandidate(
            term="搜索服务",
            source="jd",
            category="domain",
            priority=2,
            evidence="jd",
            first_added_round=0,
            retrieval_role="domain_context",
            queryability="admitted",
            family="domain.search",
        ),
        QueryTermCandidate(
            term="搜索系统",
            source="jd",
            category="domain",
            priority=3,
            evidence="jd",
            first_added_round=0,
            retrieval_role="domain_context",
            queryability="admitted",
            family="domain.search",
        ),
    ]

    with pytest.raises(ValueError, match="families"):
        canonicalize_controller_query_terms(
            ["Platform", "搜索服务", "搜索系统"],
            round_no=2,
            title_anchor_term="Platform Engineer",
            query_term_pool=pool,
        )


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


def test_query_plan_prefers_high_signal_non_anchor_roles() -> None:
    pool = [
        QueryTermCandidate(
            term="Backend",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="title",
            first_added_round=0,
            retrieval_role="role_anchor",
            queryability="admitted",
            family="role.backend",
        ),
        QueryTermCandidate(
            term="业务系统",
            source="jd",
            category="domain",
            priority=2,
            evidence="jd",
            first_added_round=0,
            retrieval_role="domain_context",
            queryability="admitted",
            family="domain.business",
        ),
        QueryTermCandidate(
            term="Python",
            source="jd",
            category="domain",
            priority=2,
            evidence="jd",
            first_added_round=0,
            retrieval_role="core_skill",
            queryability="admitted",
            family="skill.python",
        ),
        QueryTermCandidate(
            term="FastAPI",
            source="jd",
            category="tooling",
            priority=2,
            evidence="jd",
            first_added_round=0,
            retrieval_role="framework_tool",
            queryability="admitted",
            family="framework.fastapi",
        ),
    ]

    assert select_query_terms(pool, round_no=1, title_anchor_term="Backend Engineer") == ["Backend", "Python"]
    assert select_query_terms(pool, round_no=2, title_anchor_term="Backend Engineer") == [
        "Backend",
        "Python",
        "FastAPI",
    ]


def test_query_plan_derives_distinct_explore_query_from_active_and_reserve_terms() -> None:
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
        ),
        QueryTermCandidate(
            term="ranking",
            source="notes",
            category="expansion",
            priority=4,
            evidence="notes",
            first_added_round=0,
            active=False,
        ),
    ]

    explore_terms = derive_explore_query_terms(
        ["python", "resume matching", "trace"],
        title_anchor_term="python",
        query_term_pool=pool,
        sent_query_history=[
            SentQueryRecord(
                round_no=1,
                query_terms=["python", "resume matching"],
                keyword_query='python "resume matching"',
                batch_no=1,
                requested_count=10,
                source_plan_version=1,
                rationale="round 1",
            )
        ],
    )

    assert explore_terms == ["python", "ranking"]


def test_query_plan_explore_prefers_high_signal_alternatives() -> None:
    pool = [
        QueryTermCandidate(
            term="Backend",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="title",
            first_added_round=0,
            retrieval_role="role_anchor",
            queryability="admitted",
            family="role.backend",
        ),
        QueryTermCandidate(
            term="业务系统",
            source="jd",
            category="domain",
            priority=2,
            evidence="jd",
            first_added_round=0,
            retrieval_role="domain_context",
            queryability="admitted",
            family="domain.business",
        ),
        QueryTermCandidate(
            term="Python",
            source="jd",
            category="domain",
            priority=2,
            evidence="jd",
            first_added_round=0,
            retrieval_role="core_skill",
            queryability="admitted",
            family="skill.python",
        ),
        QueryTermCandidate(
            term="FastAPI",
            source="jd",
            category="tooling",
            priority=2,
            evidence="jd",
            first_added_round=0,
            retrieval_role="framework_tool",
            queryability="admitted",
            family="framework.fastapi",
        ),
    ]

    explore_terms = derive_explore_query_terms(
        ["Backend", "业务系统"],
        title_anchor_term="Backend Engineer",
        query_term_pool=pool,
        sent_query_history=[
            SentQueryRecord(
                round_no=1,
                query_terms=["Backend", "业务系统"],
                keyword_query='Backend 业务系统',
                batch_no=1,
                requested_count=10,
                source_plan_version=1,
                rationale="round 1",
            )
        ],
    )

    assert explore_terms == ["Backend", "Python"]


def test_query_plan_allows_explore_query_to_shrink_when_no_new_three_term_combo_exists() -> None:
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
        ),
    ]

    explore_terms = derive_explore_query_terms(
        ["python", "resume matching", "trace"],
        title_anchor_term="python",
        query_term_pool=pool,
        sent_query_history=[
            SentQueryRecord(
                round_no=1,
                query_terms=["python", "resume matching"],
                keyword_query='python "resume matching"',
                batch_no=1,
                requested_count=10,
                source_plan_version=1,
                rationale="round 1",
            )
        ],
    )

    assert explore_terms == ["python", "trace"]


def test_query_plan_returns_none_when_no_distinct_explore_query_is_possible() -> None:
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
    ]

    assert derive_explore_query_terms(
        ["python", "resume matching"],
        title_anchor_term="python",
        query_term_pool=pool,
        sent_query_history=[],
    ) is None
