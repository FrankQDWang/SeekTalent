from __future__ import annotations

from seektalent.models import HardConstraints, RequirementPreferences, RequirementSheet
from seektalent.rerank_text import build_rerank_query_text


def test_build_rerank_query_text_renders_natural_language_query() -> None:
    requirement_sheet = RequirementSheet(
        role_title="Senior Python / LLM Engineer",
        role_summary="Build Python, LLM, and retrieval systems.",
        must_have_capabilities=[
            "Python backend",
            "LLM application",
            "retrieval pipeline",
        ],
        preferred_capabilities=["workflow orchestration", "tool calling"],
        exclusion_signals=["frontend"],
        preferences=RequirementPreferences(),
        hard_constraints=HardConstraints(
            locations=["上海"],
            min_years=5,
            max_years=10,
            company_names=["阿里巴巴"],
            school_names=["复旦大学"],
            degree_requirement="硕士及以上",
            gender_requirement="男",
            min_age=28,
            max_age=35,
        ),
        scoring_rationale="must-have 优先，偏好次之。",
    )

    query = build_rerank_query_text(requirement_sheet)

    assert query == (
        "Hiring for Senior Python / LLM Engineer. "
        "Role summary: Build Python, LLM, and retrieval systems. "
        "Must have Python backend, LLM application, retrieval pipeline. "
        "Location: 上海. "
        "Minimum 5 years of experience. "
        "Maximum 10 years of experience. "
        "Degree requirement: 硕士及以上. "
        "Target company background: 阿里巴巴. "
        "Target school background: 复旦大学. "
        "Preferred workflow orchestration, tool calling."
    )
    assert "gender" not in query.casefold()
    assert "age" not in query.casefold()
    assert "frontend" not in query.casefold()
