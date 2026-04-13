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
        "招聘岗位：Senior Python / LLM Engineer. "
        "岗位概述：Build Python, LLM, and retrieval systems. "
        "必须条件：Python backend, LLM application, retrieval pipeline. "
        "工作地点：上海. "
        "最低工作年限：5年. "
        "最高工作年限：10年. "
        "学历要求：硕士及以上. "
        "目标公司背景：阿里巴巴. "
        "目标学校背景：复旦大学. "
        "优先条件：workflow orchestration, tool calling."
    )
    assert "gender" not in query.casefold()
    assert "age" not in query.casefold()
    assert "frontend" not in query.casefold()


def test_build_rerank_query_text_prefers_chinese_surface_and_keeps_explicit_english_terms() -> None:
    requirement_sheet = RequirementSheet(
        role_title="AI Agent研发工程师",
        role_summary="负责 Agent Runtime、工具调用和上下文管理相关系统落地。",
        must_have_capabilities=["Agent框架", "工具调用", "上下文管理"],
        preferred_capabilities=["MCP", "A2A"],
        exclusion_signals=[],
        preferences=RequirementPreferences(),
        hard_constraints=HardConstraints(locations=["深圳"]),
        scoring_rationale="中文为主，英文术语保留。",
    )

    query = build_rerank_query_text(requirement_sheet)

    assert query == (
        "招聘岗位：AI Agent研发工程师. "
        "岗位概述：负责 Agent Runtime、工具调用和上下文管理相关系统落地。 "
        "必须条件：Agent框架, 工具调用, 上下文管理. "
        "工作地点：深圳. "
        "优先条件：MCP, A2A."
    )
