from __future__ import annotations

from seektalent.models import ChildFrontierNodeStub, HardConstraints, RuntimeOnlyConstraints, SearchExecutionPlan_t
from seektalent.retrieval import project_search_plan_to_cts


def _plan(hard_constraints: HardConstraints, **overrides) -> SearchExecutionPlan_t:
    payload = {
        "query_terms": ["python", "ranking"],
        "projected_filters": hard_constraints,
        "runtime_only_constraints": RuntimeOnlyConstraints(),
        "target_new_candidate_count": 3,
        "semantic_hash": "hash-1",
        "source_card_ids": ["card-1"],
        "child_frontier_node_stub": ChildFrontierNodeStub(
            frontier_node_id="node-1",
            parent_frontier_node_id="root",
            selected_operator_name="bootstrap",
        ),
        "derived_position": "Senior Python Engineer",
        "derived_work_content": "resume matching",
    }
    payload.update(overrides)
    return SearchExecutionPlan_t.model_validate(payload)


def test_projection_maps_supported_filters_to_cts() -> None:
    native_filters, notes = project_search_plan_to_cts(
        _plan(
            HardConstraints(
                locations=["上海市"],
                company_names=["阿里巴巴", "蚂蚁集团"],
                school_names=["复旦大学", "上海交通大学"],
                degree_requirement="本科及以上",
                school_type_requirement=["211"],
                min_years=3,
                max_years=8,
                gender_requirement="男",
                min_age=25,
                max_age=35,
            )
        )
    )

    assert native_filters == {
        "location": "上海市",
        "company": "阿里巴巴 | 蚂蚁集团",
        "school": "复旦大学 | 上海交通大学",
        "position": "Senior Python Engineer",
        "workContent": "resume matching",
        "degree": 2,
        "schoolType": 2,
        "workExperienceRange": 4,
        "gender": 1,
        "age": 3,
    }
    assert any("degree_requirement mapped to CTS code 2" in note for note in notes)
    assert any("school_type_requirement mapped to CTS code 2" in note for note in notes)
    assert any("experience_requirement mapped to CTS code 4" in note for note in notes)


def test_projection_omits_unlimited_and_unsupported_enums() -> None:
    native_filters, notes = project_search_plan_to_cts(
        _plan(
            HardConstraints(
                degree_requirement="不限",
                gender_requirement="未知",
                school_type_requirement=["海外"],
            ),
            derived_position=None,
            derived_work_content=None,
        )
    )

    assert native_filters == {}
    assert any("degree_requirement is explicitly unlimited" in note for note in notes)
    assert any("gender_requirement stayed outside CTS" in note for note in notes)
    assert any("school_type_requirement stayed outside CTS" in note for note in notes)


def test_projection_skips_ranges_that_span_too_many_buckets() -> None:
    native_filters, notes = project_search_plan_to_cts(
        _plan(
            HardConstraints(
                min_age=20,
                max_age=45,
            ),
            derived_position=None,
            derived_work_content=None,
        )
    )

    assert native_filters == {}
    assert any("age_requirement spans 3 or more CTS ranges" in note for note in notes)
