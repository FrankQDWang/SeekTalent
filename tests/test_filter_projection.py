import ast
from pathlib import Path

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
from seektalent.sources.cts.filter_projection import (
    project_constraints_to_cts,
)
from seektalent.sources.cts.filter_projection import project_constraints_to_cts as project_constraints_to_cts_from_cts
from seektalent.sources.filter_plan import build_default_filter_plan, canonicalize_filter_plan


def _imported_names(path: Path, module: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    }


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        job_title="Senior Python Engineer",
        title_anchor_terms=["python"],
        title_anchor_rationale="Title maps directly to the Python role anchor.",
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
    }


def test_filter_plan_canonicalization_has_single_source_neutral_home() -> None:
    cts_source = Path("src/seektalent/sources/cts/filter_projection.py").read_text(encoding="utf-8")
    runtime_source = Path("src/seektalent/runtime/source_filters.py").read_text(encoding="utf-8")

    assert not Path("src/seektalent/providers/cts/filter_projection.py").exists()
    assert "def build_default_filter_plan" not in cts_source
    assert "def canonicalize_filter_plan" not in cts_source
    assert "def build_default_filter_plan" not in runtime_source
    assert "def canonicalize_filter_plan" not in runtime_source
    assert "seektalent.providers" not in runtime_source


def test_runtime_does_not_reexport_filter_plan_canonicalization() -> None:
    canonical_names = {"build_default_filter_plan", "canonicalize_filter_plan"}
    runtime_source_filters = Path("src/seektalent/runtime/source_filters.py")

    assert not (_imported_names(runtime_source_filters, "seektalent.sources.filter_plan") & canonical_names)

    for path in Path("src/seektalent/runtime").glob("*.py"):
        if path.name == "source_filters.py":
            continue
        imported = _imported_names(path, "seektalent.runtime.source_filters")
        assert not imported & canonical_names, f"{path} imports canonical filter-plan helpers through runtime"


def test_provider_range_overlap_has_single_source_neutral_home() -> None:
    helper_path = Path("src/seektalent/sources/range_overlap.py")
    cts_source = Path("src/seektalent/sources/cts/filter_projection.py").read_text(encoding="utf-8")
    liepin_source = Path("src/seektalent/providers/liepin/filter_compiler.py").read_text(encoding="utf-8")

    assert helper_path.exists()
    helper_source = helper_path.read_text(encoding="utf-8")
    assert "def range_overlap" in helper_source
    assert "from seektalent.sources.range_overlap import range_overlap" in cts_source
    assert "from seektalent.sources.range_overlap import range_overlap" in liepin_source
    assert "def _range_overlap" not in cts_source
    assert "def _range_overlap" not in liepin_source


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
    assert "position" not in canonical.optional_filters
    assert canonical.optional_filters["age_requirement"] == ["max=35"]
    assert canonical.optional_filters["gender_requirement"] == "男"
    assert canonical.dropped_filter_fields == ["school_names"]
    assert "position" not in canonical.added_filter_fields


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

    assert projection.provider_filters == {
        "school": "复旦大学 | 上海交通大学",
        "degree": 2,
        "schoolType": 2,
        "workExperienceRange": 3,
        "gender": 1,
    }
    runtime_fields = {item.field for item in projection.runtime_only_constraints}
    assert runtime_fields == {"company_names"}
    assert any("degree_requirement mapped to CTS code 2 (本科及以上)." == note for note in projection.adapter_notes)
    assert any("school_type_requirement mapped to CTS code 2 (211)." == note for note in projection.adapter_notes)
    assert any("experience_requirement mapped to CTS code 3 (3-5年)." == note for note in projection.adapter_notes)
    assert any("gender_requirement mapped to CTS code 1 (男)." == note for note in projection.adapter_notes)
    assert any("age_requirement spans 3 or more CTS ranges" in note for note in projection.adapter_notes)


def test_project_constraints_skips_explicit_unlimited_enums() -> None:
    requirement_sheet = RequirementSheet(
        job_title="Python Engineer",
        title_anchor_terms=["python"],
        title_anchor_rationale="Title maps directly to the Python role anchor.",
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

    assert projection.provider_filters == {}
    assert projection.runtime_only_constraints == []
    assert any("degree_requirement is explicitly unlimited" in note for note in projection.adapter_notes)
    assert any("gender_requirement is explicitly unlimited" in note for note in projection.adapter_notes)


def test_project_constraints_to_cts_keeps_unsupported_school_type_runtime_only() -> None:
    requirement_sheet = RequirementSheet(
        job_title="Python Engineer",
        title_anchor_terms=["python"],
        title_anchor_rationale="Title maps directly to the Python role anchor.",
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

    assert projection.provider_filters == {}
    assert [item.field for item in projection.runtime_only_constraints] == ["school_type_requirement"]
    assert any("school_type_requirement stayed runtime-only" in note for note in projection.adapter_notes)


def test_project_constraints_to_cts_keeps_unsupported_degree_and_gender_runtime_only() -> None:
    requirement_sheet = RequirementSheet(
        job_title="Research Scientist",
        title_anchor_terms=["research"],
        title_anchor_rationale="Title maps directly to the research role anchor.",
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

    assert projection.provider_filters == {}
    assert {item.field for item in projection.runtime_only_constraints} == {
        "degree_requirement",
        "gender_requirement",
    }


def test_project_constraints_to_cts_picks_larger_experience_overlap() -> None:
    requirement_sheet = RequirementSheet(
        job_title="Python Engineer",
        title_anchor_terms=["python"],
        title_anchor_rationale="Title maps directly to the Python role anchor.",
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

    assert projection.provider_filters == {"workExperienceRange": 4}
    assert projection.runtime_only_constraints == []


def test_project_constraints_to_cts_keeps_optional_company_and_open_min_experience_runtime_only() -> None:
    requirement_sheet = RequirementSheet(
        job_title="数据开发专家",
        title_anchor_terms=["数据开发"],
        title_anchor_rationale="Title anchor.",
        role_summary="Build data platforms.",
        hard_constraints=HardConstraintSlots(
            company_names=["BAT", "TMD", "一线大模型创业企业"],
            experience_requirement=ExperienceRequirement(min_years=5, max_years=None, raw_text="5年及以上"),
        ),
        initial_query_term_pool=[],
        scoring_rationale="test",
    )
    filter_plan = ProposedFilterPlan(
        optional_filters={
            "company_names": ["BAT", "TMD", "一线大模型创业企业"],
            "experience_requirement": ["min=5"],
        }
    )

    projection = project_constraints_to_cts(
        requirement_sheet=requirement_sheet,
        filter_plan=filter_plan,
    )

    assert projection.provider_filters == {}
    assert {constraint.field for constraint in projection.runtime_only_constraints} == {
        "company_names",
        "experience_requirement",
    }
    assert any("open-ended minimum ranges" in note for note in projection.adapter_notes)


def test_project_constraints_to_cts_uses_age_tie_break_order() -> None:
    requirement_sheet = RequirementSheet(
        job_title="Python Engineer",
        title_anchor_terms=["python"],
        title_anchor_rationale="Title maps directly to the Python role anchor.",
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

    assert projection.provider_filters == {"age": 3}
    assert projection.runtime_only_constraints == []


def test_cts_filter_projection_projects_age_and_school_type() -> None:
    requirement_sheet = _requirement_sheet()
    requirement_sheet.hard_constraints.age_requirement = AgeRequirement(
        min_age=25,
        max_age=30,
        raw_text="25-30岁",
    )

    projection = project_constraints_to_cts_from_cts(
        requirement_sheet=requirement_sheet,
        filter_plan=ProposedFilterPlan(
            optional_filters={
                "age_requirement": ["min=25", "max=30"],
                "school_type_requirement": ["985", "211"],
            }
        ),
    )

    assert projection.provider_filters["age"] == 2
    assert projection.provider_filters["schoolType"] == 2
