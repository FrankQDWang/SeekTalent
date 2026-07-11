import pytest

from seektalent.models import LocationExecutionPlan, Queryability, QueryRetrievalRole, QueryTermCandidate
from seektalent.retrieval.query_plan import (
    build_round_retrieval_plan,
    canonicalize_controller_query_terms,
    derive_explore_query_terms,
    select_query_terms,
    serialize_keyword_query,
    try_project_secondary_title_anchor_after_round_one,
)
from seektalent.retrieval.query_identity import build_term_group_key


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
        title_anchor_terms=["python"],
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
        title_anchor_terms=["Platform Engineer"],
        query_term_pool=pool,
    ) == ["Platform", "Python"]


def test_query_plan_accepts_primary_role_anchor_from_compiler() -> None:
    pool = [
        QueryTermCandidate(
            term="Platform",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="compiled title",
            first_added_round=0,
            retrieval_role="primary_role_anchor",
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
        title_anchor_terms=["Platform Engineer"],
        query_term_pool=pool,
    ) == ["Platform", "Python"]


def test_query_plan_rejects_anchor_only_by_default() -> None:
    pool = [
        QueryTermCandidate(
            term="python",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="job title",
            first_added_round=0,
        )
    ]

    with pytest.raises(ValueError, match="at least 2 terms"):
        canonicalize_controller_query_terms(
            ["python"],
            round_no=2,
            title_anchor_terms=["python"],
            query_term_pool=pool,
        )


def test_query_plan_allows_runtime_anchor_only_when_explicitly_enabled() -> None:
    pool = [
        QueryTermCandidate(
            term="python",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="job title",
            first_added_round=0,
            retrieval_role="role_anchor",
            queryability="admitted",
            family="role.python",
        )
    ]

    assert canonicalize_controller_query_terms(
        [" python "],
        round_no=2,
        title_anchor_terms=["python"],
        query_term_pool=pool,
        allow_anchor_only=True,
    ) == ["python"]


@pytest.mark.parametrize(
    ("term", "retrieval_role", "queryability"),
    [
        ("python", "domain_context", "admitted"),
        ("211", "filter_only", "filter_only"),
        ("沟通能力", "score_only", "score_only"),
        ("AgentLoop", "score_only", "blocked"),
    ],
)
def test_query_plan_anchor_only_still_requires_admitted_role_anchor(
    term: str,
    retrieval_role: QueryRetrievalRole,
    queryability: Queryability,
) -> None:
    pool = [
        QueryTermCandidate(
            term=term,
            source="jd",
            category="domain",
            priority=1,
            evidence="jd",
            first_added_round=0,
            retrieval_role=retrieval_role,
            queryability=queryability,
            family=f"family.{term}",
        )
    ]

    with pytest.raises(ValueError, match="anchor"):
        canonicalize_controller_query_terms(
            [term],
            round_no=2,
            title_anchor_terms=["python"],
            query_term_pool=pool,
            allow_anchor_only=True,
        )


def test_query_plan_builds_runtime_anchor_only_retrieval_plan() -> None:
    pool = [
        QueryTermCandidate(
            term="python",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="job title",
            first_added_round=0,
        )
    ]

    plan = build_round_retrieval_plan(
        plan_version=2,
        round_no=4,
        query_terms=["python"],
        title_anchor_terms=["python"],
        query_term_pool=pool,
        projected_provider_filters={},
        runtime_only_constraints=[],
        location_execution_plan=LocationExecutionPlan(
            mode="single",
            allowed_locations=["上海"],
            preferred_locations=[],
            priority_order=[],
            balanced_order=["上海"],
            rotation_offset=0,
            target_new=10,
        ),
        target_new=10,
        rationale="Runtime broaden: anchor-only search.",
        allow_anchor_only_query=True,
    )

    assert plan.query_terms == ["python"]
    assert plan.keyword_query == "python"
    assert plan.projected_provider_filters == {}


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
            title_anchor_terms=["Platform Engineer"],
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
            title_anchor_terms=["Platform Engineer"],
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

    assert select_query_terms(pool, round_no=1, title_anchor_terms=["python"]) == ["python", "resume matching"]


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

    assert select_query_terms(pool, round_no=1, title_anchor_terms=["Backend Engineer"]) == ["Backend", "Python"]
    assert select_query_terms(pool, round_no=2, title_anchor_terms=["Backend Engineer"]) == [
        "Backend",
        "Python",
        "FastAPI",
    ]


def test_query_plan_round_one_prefers_primary_plus_secondary_title_anchor() -> None:
    pool = [
        QueryTermCandidate(
            term="Backend",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="compiled title",
            first_added_round=0,
            retrieval_role="primary_role_anchor",
            queryability="admitted",
            family="role.backend",
        ),
        QueryTermCandidate(
            term="Platform",
            source="job_title",
            category="role_anchor",
            priority=2,
            evidence="compiled title",
            first_added_round=0,
            retrieval_role="secondary_title_anchor",
            queryability="admitted",
            family="role.platform",
        ),
        QueryTermCandidate(
            term="Python",
            source="jd",
            category="domain",
            priority=1,
            evidence="jd",
            first_added_round=0,
            retrieval_role="core_skill",
            queryability="admitted",
            family="skill.python",
        ),
    ]

    assert select_query_terms(pool, round_no=1, title_anchor_terms=["Backend Engineer", "Platform Engineer"]) == [
        "Backend",
        "Platform",
    ]


def test_query_plan_round_one_falls_back_to_primary_plus_domain_term() -> None:
    pool = [
        QueryTermCandidate(
            term="Backend",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="compiled title",
            first_added_round=0,
            retrieval_role="primary_role_anchor",
            queryability="admitted",
            family="role.backend",
        ),
        QueryTermCandidate(
            term="Python",
            source="jd",
            category="domain",
            priority=1,
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

    assert select_query_terms(pool, round_no=1, title_anchor_terms=["Backend Engineer"]) == ["Backend", "Python"]


def test_query_plan_rejects_secondary_title_anchor_after_round_one() -> None:
    pool = [
        QueryTermCandidate(
            term="Backend",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="compiled title",
            first_added_round=0,
            retrieval_role="primary_role_anchor",
            queryability="admitted",
            family="role.backend",
        ),
        QueryTermCandidate(
            term="Platform",
            source="job_title",
            category="role_anchor",
            priority=2,
            evidence="compiled title",
            first_added_round=0,
            retrieval_role="secondary_title_anchor",
            queryability="admitted",
            family="role.platform",
        ),
        QueryTermCandidate(
            term="Python",
            source="jd",
            category="domain",
            priority=1,
            evidence="jd",
            first_added_round=0,
            retrieval_role="core_skill",
            queryability="admitted",
            family="skill.python",
        ),
    ]

    with pytest.raises(ValueError, match="secondary_title_anchor"):
        canonicalize_controller_query_terms(
            ["Backend", "Platform"],
            round_no=2,
            title_anchor_terms=["Backend Engineer", "Platform Engineer"],
            query_term_pool=pool,
        )


def _projection_pool() -> list[QueryTermCandidate]:
    return [
        QueryTermCandidate(
            term="AI",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="Compiled title",
            first_added_round=0,
            retrieval_role="primary_role_anchor",
            queryability="admitted",
            family="role.ai",
        ),
        QueryTermCandidate(
            term="主观投资",
            source="job_title",
            category="role_anchor",
            priority=2,
            evidence="Compiled title",
            first_added_round=0,
            retrieval_role="secondary_title_anchor",
            queryability="admitted",
            family="role.investment",
        ),
        QueryTermCandidate(
            term="模型部署",
            source="jd",
            category="domain",
            priority=3,
            evidence="JD body",
            first_added_round=0,
            retrieval_role="core_skill",
            queryability="admitted",
            family="skill.model-deploy",
        ),
    ]


def test_try_project_secondary_title_anchor_after_round_one_replaces_only_secondary_anchor() -> None:
    assert try_project_secondary_title_anchor_after_round_one(
        ["AI", "主观投资"],
        round_no=3,
        query_term_pool=_projection_pool(),
    ) == ["AI", "模型部署"]


def test_try_project_secondary_title_anchor_after_round_one_returns_none_for_round_one() -> None:
    assert try_project_secondary_title_anchor_after_round_one(
        ["AI", "主观投资"],
        round_no=1,
        query_term_pool=_projection_pool(),
    ) is None


def test_try_project_secondary_title_anchor_after_round_one_returns_none_without_secondary_anchor() -> None:
    assert try_project_secondary_title_anchor_after_round_one(
        ["AI", "模型部署"],
        round_no=3,
        query_term_pool=_projection_pool(),
    ) is None


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
        title_anchor_terms=["python"],
        query_term_pool=pool,
        used_term_group_keys={
            build_term_group_key(query_terms=["python", "resume matching"], query_term_pool=pool)
        },
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
        title_anchor_terms=["Backend Engineer"],
        query_term_pool=pool,
        used_term_group_keys={
            build_term_group_key(query_terms=["Backend", "业务系统"], query_term_pool=pool)
        },
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
        title_anchor_terms=["python"],
        query_term_pool=pool,
        used_term_group_keys={
            build_term_group_key(query_terms=["python", "resume matching"], query_term_pool=pool)
        },
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
        title_anchor_terms=["python"],
        query_term_pool=pool,
        used_term_group_keys=set(),
    ) is None


def test_derive_explore_returns_none_when_every_semantic_group_is_used() -> None:
    pool = [
        QueryTermCandidate(
            term="Platform",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="title",
            first_added_round=0,
            retrieval_role="primary_role_anchor",
            queryability="admitted",
            family="role.platform",
        ),
        QueryTermCandidate(
            term="Python",
            source="jd",
            category="tooling",
            priority=2,
            evidence="jd",
            first_added_round=0,
            retrieval_role="core_skill",
            queryability="admitted",
            family="skill.python",
        ),
        QueryTermCandidate(
            term="Rust",
            source="jd",
            category="tooling",
            priority=3,
            evidence="jd",
            first_added_round=0,
            retrieval_role="framework_tool",
            queryability="admitted",
            family="skill.rust",
        ),
    ]

    assert derive_explore_query_terms(
        ["Platform", "Python"],
        title_anchor_terms=[],
        query_term_pool=pool,
        used_term_group_keys={
            build_term_group_key(query_terms=["Platform", "Python"], query_term_pool=pool),
            build_term_group_key(query_terms=["Platform", "Rust"], query_term_pool=pool),
            build_term_group_key(query_terms=["Platform", "Python", "Rust"], query_term_pool=pool),
        },
    ) is None
