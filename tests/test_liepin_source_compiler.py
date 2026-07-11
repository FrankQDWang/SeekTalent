from __future__ import annotations

import json

from seektalent.providers.liepin.source_compiler import compile_liepin_source_query_intents
from seektalent.runtime.source_filters import RuntimeFilterIntent, RuntimeLocationExecutionIntent
from seektalent.runtime.source_query_intent import RuntimeSourceQueryIntent


def test_liepin_source_compiler_passes_native_filters_to_provider_context() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="explore",
        lane_type="generic_explore",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        term_group_key="term-group-data-platform",
        primary_anchor_family_id="role.data-engineer",
        non_anchor_term_family_ids=("skill.python",),
        query_terms=("数据开发专家",),
        keyword_query="数据开发专家",
        requested_count=10,
        provider_scan_limit=10,
        source_plan_version="test",
        filter_intents=(
            RuntimeFilterIntent(
                field="experience_requirement",
                value=["min=3", "max=5"],
                required=False,
                origin="controller",
            ),
            RuntimeFilterIntent(
                field="age_requirement",
                value=["max=35"],
                required=False,
                origin="controller",
            ),
        ),
        location_intent=RuntimeLocationExecutionIntent(
            mode="single",
            allowed_locations=("上海",),
            preferred_locations=(),
            priority_order=("上海",),
            balanced_order=("上海",),
            rotation_offset=0,
            target_new=10,
        ),
        age_intent=None,
    )

    compiled = compile_liepin_source_query_intents((intent,))
    request = compiled.queries[0].search_request

    assert compiled.unsupported_filters == ()
    assert len(compiled.queries) == 1
    assert request.provider_filters == {}
    assert json.loads(str(request.provider_context["liepin_native_filters_json"])) == {
        "city": {"section": "expected", "label": "上海"},
        "experience": {"section": "experience", "label": "3-5年"},
        "age": {"section": "age", "label": "35岁以下"},
        "requiredFilterNames": ["city"],
        "optionalFilterNames": ["experience", "age"],
        "sourceTarget": {"phase": "balanced", "batchNo": 1, "requestedCount": 10},
    }


def test_liepin_source_compiler_expands_balanced_city_targets() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=2,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        term_group_key="term-group-data-platform",
        primary_anchor_family_id="role.data-engineer",
        non_anchor_term_family_ids=("skill.python",),
        query_terms=("数据开发专家",),
        keyword_query="数据开发专家",
        requested_count=12,
        provider_scan_limit=12,
        source_plan_version="test",
        filter_intents=(),
        location_intent=RuntimeLocationExecutionIntent(
            mode="balanced_all",
            allowed_locations=("上海", "北京", "深圳"),
            preferred_locations=(),
            priority_order=(),
            balanced_order=("北京", "深圳", "上海"),
            rotation_offset=1,
            target_new=12,
        ),
        age_intent=None,
    )

    compiled = compile_liepin_source_query_intents((intent,))

    payloads = [
        json.loads(str(query.search_request.provider_context["liepin_native_filters_json"]))
        for query in compiled.queries
    ]
    assert [payload["city"] for payload in payloads] == [
        {"section": "expected", "label": "北京"},
        {"section": "expected", "label": "深圳"},
        {"section": "expected", "label": "上海"},
    ]
    assert [payload["sourceTarget"]["requestedCount"] for payload in payloads] == [4, 4, 4]


def test_liepin_source_compiler_payload_contains_expected_city_and_education_filters() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        term_group_key="term-group-data-etl",
        primary_anchor_family_id="role.data-engineer",
        non_anchor_term_family_ids=("skill.python",),
        query_terms=("数据开发",),
        keyword_query="数据开发 ETL",
        requested_count=10,
        provider_scan_limit=10,
        source_plan_version="test",
        filter_intents=(
            RuntimeFilterIntent(field="degree_requirement", value="本科", required=False, origin="controller"),
            RuntimeFilterIntent(
                field="school_type_requirement",
                value=["统招", "985", "211"],
                required=False,
                origin="controller",
            ),
        ),
        location_intent=RuntimeLocationExecutionIntent(
            mode="single",
            allowed_locations=("北京",),
            preferred_locations=(),
            priority_order=("北京",),
            balanced_order=("北京",),
            rotation_offset=0,
            target_new=10,
        ),
        age_intent=None,
    )

    compiled = compile_liepin_source_query_intents((intent,))
    payload = json.loads(str(compiled.queries[0].search_request.provider_context["liepin_native_filters_json"]))

    assert compiled.unsupported_filters == ()
    assert payload["city"] == {"section": "expected", "label": "北京"}
    assert payload["degree"] == {"section": "education", "label": "本科"}
    assert payload["recruitmentType"] == {"section": "recruitment_type", "label": "统招本科"}
    assert payload["schoolTypes"] == [
        {"section": "school_type", "label": "211"},
        {"section": "school_type", "label": "985"},
    ]


def test_liepin_source_compiler_records_partial_for_unprojected_filter() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        term_group_key="term-group-data-etl",
        primary_anchor_family_id="role.data-engineer",
        non_anchor_term_family_ids=("skill.python",),
        query_terms=("数据开发",),
        keyword_query="数据开发 ETL",
        requested_count=10,
        provider_scan_limit=10,
        source_plan_version="test",
        filter_intents=(
            RuntimeFilterIntent(
                field="experience_requirement",
                value=["min=1", "max=10"],
                required=False,
                origin="controller",
            ),
        ),
        location_intent=None,
        age_intent=None,
    )

    compiled = compile_liepin_source_query_intents((intent,))

    assert [item.safe_reason_code for item in compiled.unsupported_filters] == ["source_filter_partial"]
    assert "runtime-only" in compiled.queries[0].search_request.adapter_notes[0]
