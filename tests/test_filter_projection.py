from seektalent.models import (
    AgeRequirement,
    DegreeRequirement,
    ExperienceRequirement,
    GenderRequirement,
    HardConstraintSlots,
    ProposedFilterPlan,
    QueryTermCandidate,
    RequirementSheet,
    SchoolTypeRequirement,
)
from seektalent.retrieval.filter_projection import (
    build_default_filter_plan,
    canonicalize_filter_plan,
    project_constraints_to_cts,
)


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        role_title="Senior Python Engineer",
        title_anchor_term="python",
        role_summary="Build resume matching workflows.",
        must_have_capabilities=["python", "resume matching", "retrieval"],
        hard_constraints=HardConstraintSlots(
            locations=["上海市"],
            school_names=["复旦大学", "上海交通大学"],
            degree_requirement=DegreeRequirement(canonical_degree="本科及以上", raw_text="本科及以上"),
            school_type_requirement=SchoolTypeRequirement(
                canonical_types=["985", "211"],
                raw_text="985/211",
            ),
            experience_requirement=ExperienceRequirement(min_years=3, max_years=5, raw_text="3-5年"),
            gender_requirement=GenderRequirement(canonical_gender="男", raw_text="男性优先"),
            age_requirement=AgeRequirement(max_age=35, raw_text="35岁以下"),
            company_names=["阿里巴巴", "蚂蚁集团"],
        ),
        initial_query_term_pool=[
            QueryTermCandidate(
                term="python",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Job title",
                first_added_round=0,
            ),
            QueryTermCandidate(
                term="resume matching",
                source="jd",
                category="domain",
                priority=2,
                evidence="JD body",
                first_added_round=0,
            ),
        ],
        scoring_rationale="Score Python fit first.",
    )


def test_build_default_filter_plan_uses_truth_fields() -> None:
    filter_plan = build_default_filter_plan(_requirement_sheet())

    assert filter_plan.pinned_filters == {}
    assert filter_plan.optional_filters == {
        "company_names": ["阿里巴巴", "蚂蚁集团"],
        "school_names": ["复旦大学", "上海交通大学"],
        "degree_requirement": "本科及以上",
        "school_type_requirement": ["985", "211"],
        "experience_requirement": ["min=3", "max=5"],
        "gender_requirement": "男",
        "age_requirement": ["max=35"],
        "position": "Senior Python Engineer",
    }


def test_canonicalize_filter_plan_repins_location_and_uses_truth_values() -> None:
    requirement_sheet = _requirement_sheet()
    filter_plan = ProposedFilterPlan(
        pinned_filters={"company_names": ["FakeCo"]},
        optional_filters={
            "degree_requirement": "博士及以上",
            "school_names": ["Fake School"],
            "position": "Random Title",
        },
        dropped_filter_fields=["school_names"],
        added_filter_fields=["age_requirement", "gender_requirement", "position"],
    )

    canonical = canonicalize_filter_plan(requirement_sheet=requirement_sheet, filter_plan=filter_plan)

    assert canonical.pinned_filters["company_names"] == ["阿里巴巴", "蚂蚁集团"]
    assert "school_names" not in canonical.optional_filters
    assert canonical.optional_filters["degree_requirement"] == "本科及以上"
    assert canonical.optional_filters["position"] == "Senior Python Engineer"
    assert canonical.optional_filters["age_requirement"] == ["max=35"]
    assert canonical.optional_filters["gender_requirement"] == "男"
    assert canonical.dropped_filter_fields == ["school_names"]


def test_project_constraints_to_cts_projects_text_and_keeps_enums_runtime_only() -> None:
    requirement_sheet = _requirement_sheet()
    filter_plan = ProposedFilterPlan(
        optional_filters={
            "company_names": ["阿里巴巴", "蚂蚁集团"],
            "school_names": ["复旦大学", "上海交通大学"],
            "degree_requirement": "本科及以上",
            "experience_requirement": ["min=3", "max=5"],
            "position": "Senior Python Engineer",
        },
        added_filter_fields=["school_type_requirement", "gender_requirement", "age_requirement"],
    )

    projection = project_constraints_to_cts(
        requirement_sheet=requirement_sheet,
        filter_plan=filter_plan,
    )

    assert projection.cts_native_filters == {
        "company": "阿里巴巴 | 蚂蚁集团",
        "school": "复旦大学 | 上海交通大学",
        "degree": 2,
        "schoolType": 2,
        "position": "Senior Python Engineer",
        "workExperienceRange": 3,
        "gender": 1,
    }
    runtime_fields = {item.field for item in projection.runtime_only_constraints}
    assert runtime_fields == set()
    assert any("degree_requirement mapped to CTS code 2 (本科及以上)." == note for note in projection.adapter_notes)
    assert any("school_type_requirement mapped to CTS code 2 (211)." == note for note in projection.adapter_notes)
    assert any("experience_requirement mapped to CTS code 3 (3-5年)." == note for note in projection.adapter_notes)
    assert any("gender_requirement mapped to CTS code 1 (男)." == note for note in projection.adapter_notes)
    assert any("age_requirement spans 3 or more CTS ranges" in note for note in projection.adapter_notes)


def test_project_constraints_skips_explicit_unlimited_enums() -> None:
    requirement_sheet = RequirementSheet(
        role_title="Python Engineer",
        title_anchor_term="python",
        role_summary="Build services.",
        hard_constraints=HardConstraintSlots(
            locations=["上海市"],
            degree_requirement=DegreeRequirement(canonical_degree="不限", raw_text="学历不限"),
            gender_requirement=GenderRequirement(canonical_gender="不限", raw_text="男女不限"),
        ),
        initial_query_term_pool=[],
        scoring_rationale="test",
    )
    filter_plan = ProposedFilterPlan(
        optional_filters={"degree_requirement": "不限", "gender_requirement": "不限"},
    )

    projection = project_constraints_to_cts(
        requirement_sheet=requirement_sheet,
        filter_plan=filter_plan,
    )

    assert projection.cts_native_filters == {}
    assert projection.runtime_only_constraints == []
    assert any("degree_requirement is explicitly unlimited" in note for note in projection.adapter_notes)
    assert any("gender_requirement is explicitly unlimited" in note for note in projection.adapter_notes)


def test_project_constraints_to_cts_keeps_unsupported_school_type_runtime_only() -> None:
    requirement_sheet = RequirementSheet(
        role_title="Python Engineer",
        title_anchor_term="python",
        role_summary="Build services.",
        hard_constraints=HardConstraintSlots(
            school_type_requirement=SchoolTypeRequirement(canonical_types=["海外"], raw_text="海外"),
        ),
        initial_query_term_pool=[],
        scoring_rationale="test",
    )
    filter_plan = ProposedFilterPlan(optional_filters={"school_type_requirement": ["海外"]})

    projection = project_constraints_to_cts(
        requirement_sheet=requirement_sheet,
        filter_plan=filter_plan,
    )

    assert projection.cts_native_filters == {}
    assert [item.field for item in projection.runtime_only_constraints] == ["school_type_requirement"]
    assert any("school_type_requirement stayed runtime-only" in note for note in projection.adapter_notes)


def test_project_constraints_to_cts_keeps_unsupported_degree_and_gender_runtime_only() -> None:
    requirement_sheet = RequirementSheet(
        role_title="Research Scientist",
        title_anchor_term="research",
        role_summary="Build models.",
        hard_constraints=HardConstraintSlots(
            degree_requirement=DegreeRequirement(canonical_degree="博士及以上", raw_text="博士及以上"),
            gender_requirement=GenderRequirement(canonical_gender="未知", raw_text="未知"),
        ),
        initial_query_term_pool=[],
        scoring_rationale="test",
    )
    filter_plan = ProposedFilterPlan(
        optional_filters={
            "degree_requirement": "博士及以上",
            "gender_requirement": "未知",
        }
    )

    projection = project_constraints_to_cts(
        requirement_sheet=requirement_sheet,
        filter_plan=filter_plan,
    )

    assert projection.cts_native_filters == {}
    assert {item.field for item in projection.runtime_only_constraints} == {
        "degree_requirement",
        "gender_requirement",
    }


def test_project_constraints_to_cts_picks_larger_experience_overlap() -> None:
    requirement_sheet = RequirementSheet(
        role_title="Python Engineer",
        title_anchor_term="python",
        role_summary="Build services.",
        hard_constraints=HardConstraintSlots(
            experience_requirement=ExperienceRequirement(min_years=3, max_years=8, raw_text="3-8年"),
        ),
        initial_query_term_pool=[],
        scoring_rationale="test",
    )
    filter_plan = ProposedFilterPlan(optional_filters={"experience_requirement": ["min=3", "max=8"]})

    projection = project_constraints_to_cts(
        requirement_sheet=requirement_sheet,
        filter_plan=filter_plan,
    )

    assert projection.cts_native_filters == {"workExperienceRange": 4}
    assert projection.runtime_only_constraints == []


def test_project_constraints_to_cts_uses_age_tie_break_order() -> None:
    requirement_sheet = RequirementSheet(
        role_title="Python Engineer",
        title_anchor_term="python",
        role_summary="Build services.",
        hard_constraints=HardConstraintSlots(
            age_requirement=AgeRequirement(min_age=25, max_age=35, raw_text="25-35岁"),
        ),
        initial_query_term_pool=[],
        scoring_rationale="test",
    )
    filter_plan = ProposedFilterPlan(optional_filters={"age_requirement": ["min=25", "max=35"]})

    projection = project_constraints_to_cts(
        requirement_sheet=requirement_sheet,
        filter_plan=filter_plan,
    )

    assert projection.cts_native_filters == {"age": 3}
    assert projection.runtime_only_constraints == []
