from __future__ import annotations

import json

from seektalent.providers.liepin.filter_compiler import compile_liepin_native_filters
from seektalent.runtime.source_filters import (
    RuntimeFilterIntent,
    RuntimeLocationExecutionIntent,
)
from seektalent.runtime.source_lanes import DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY
from seektalent.runtime.source_query_intent import RuntimeSourceQueryIntent


def _intent() -> RuntimeSourceQueryIntent:
    return RuntimeSourceQueryIntent(
        round_no=2,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        query_terms=("数据开发专家",),
        keyword_query="数据开发专家",
        requested_count=12,
        provider_scan_limit=12,
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


def test_compile_liepin_native_filters_uses_runtime_location_and_range_filters() -> None:
    plan = compile_liepin_native_filters(_intent(), budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY)

    assert [(target.city, target.requested_count) for target in plan.targets] == [
        ("北京", 4),
        ("深圳", 4),
        ("上海", 4),
    ]
    first_target = plan.targets[0]
    assert first_target.experience_min_years == 3
    assert first_target.experience_max_years == 5
    assert first_target.age_max == 35
    assert first_target.to_safe_payload() == {
        "city": {"section": "expected", "label": "北京"},
        "experience": {"section": "experience", "label": "3-5年"},
        "age": {"section": "age", "label": "35岁以下"},
        "requiredFilterNames": ["city"],
        "optionalFilterNames": ["experience", "age"],
        "sourceTarget": {
            "phase": "balanced",
            "batchNo": 1,
            "requestedCount": 4,
        },
    }


def test_compile_liepin_native_filters_rejects_wrong_source() -> None:
    intent = _intent()
    cts_intent = RuntimeSourceQueryIntent(
        round_no=intent.round_no,
        source_kind="cts",
        query_role=intent.query_role,
        lane_type=intent.lane_type,
        query_instance_id=intent.query_instance_id,
        query_fingerprint=intent.query_fingerprint,
        query_terms=intent.query_terms,
        keyword_query=intent.keyword_query,
        requested_count=intent.requested_count,
        provider_scan_limit=intent.provider_scan_limit,
        source_plan_version=intent.source_plan_version,
        filter_intents=intent.filter_intents,
        location_intent=intent.location_intent,
        age_intent=intent.age_intent,
    )

    try:
        compile_liepin_native_filters(cts_intent, budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY)
    except ValueError as exc:
        assert str(exc) == "liepin_filter_compiler_wrong_source:cts"
    else:
        raise AssertionError("expected wrong-source compiler error")


def test_compile_liepin_native_filters_payload_is_json_safe() -> None:
    payloads = [
        target.to_safe_payload()
        for target in compile_liepin_native_filters(
            _intent(),
            budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY,
        ).targets
    ]
    encoded = json.dumps(payloads, ensure_ascii=False, sort_keys=True)

    assert "数据开发专家" not in encoded
    assert "cookie" not in encoded.lower()
    assert "authorization" not in encoded.lower()


def test_compile_liepin_native_filters_preserves_single_city_as_one_target() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        query_terms=("数据开发专家",),
        keyword_query="数据开发专家",
        requested_count=10,
        provider_scan_limit=10,
        source_plan_version="test",
        filter_intents=(),
        location_intent=RuntimeLocationExecutionIntent(
            mode="single",
            allowed_locations=("上海",),
            preferred_locations=(),
            priority_order=(),
            balanced_order=("上海",),
            rotation_offset=0,
            target_new=10,
        ),
        age_intent=None,
    )

    plan = compile_liepin_native_filters(
        intent,
        budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY,
    )

    assert len(plan.targets) == 1
    assert plan.targets[0].city == "上海"
    assert plan.targets[0].requested_count == 10


def test_compile_liepin_native_filters_treats_unlimited_location_as_no_city_filter() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        query_terms=("AI Agent",),
        keyword_query="AI Agent",
        requested_count=10,
        provider_scan_limit=10,
        source_plan_version="test",
        filter_intents=(),
        location_intent=RuntimeLocationExecutionIntent(
            mode="single",
            allowed_locations=("不限",),
            preferred_locations=(),
            priority_order=("不限",),
            balanced_order=("不限",),
            rotation_offset=0,
            target_new=10,
        ),
        age_intent=None,
    )

    plan = compile_liepin_native_filters(intent, budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY)
    payload = plan.targets[0].to_safe_payload()

    assert len(plan.targets) == 1
    assert plan.targets[0].city is None
    assert plan.targets[0].city_section is None
    assert "city" not in payload
    assert "requiredFilterNames" not in payload


def test_compile_liepin_native_filters_targets_expected_city_section() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        query_terms=("数据开发",),
        keyword_query="数据开发 ETL",
        requested_count=10,
        provider_scan_limit=10,
        source_plan_version="test",
        filter_intents=(),
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

    target = compile_liepin_native_filters(intent, budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY).targets[0]

    assert target.city == "北京"
    assert target.city_section == "expected"
    assert target.to_safe_payload()["city"] == {"section": "expected", "label": "北京"}


def test_compile_liepin_native_filters_projects_degree_recruitment_and_school_type() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
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
            RuntimeFilterIntent(
                field="experience_requirement",
                value=["min=2", "max=4"],
                required=False,
                origin="controller",
            ),
        ),
        location_intent=None,
        age_intent=None,
    )

    target = compile_liepin_native_filters(intent, budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY).targets[0]

    assert target.degree_label == "本科"
    assert target.recruitment_type_label == "统招本科"
    assert target.school_type_labels == ("211", "985")
    assert target.experience_label in {"1-3年", "3-5年"}
    assert target.to_safe_payload()["degree"] == {"section": "education", "label": "本科"}
    assert target.to_safe_payload()["recruitmentType"] == {"section": "recruitment_type", "label": "统招本科"}
    assert target.to_safe_payload()["schoolTypes"] == [
        {"section": "school_type", "label": "211"},
        {"section": "school_type", "label": "985"},
    ]


def test_compile_liepin_native_filters_keeps_unpaired_recruitment_type_runtime_only() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        query_terms=("数据开发",),
        keyword_query="数据开发 ETL",
        requested_count=10,
        provider_scan_limit=10,
        source_plan_version="test",
        filter_intents=(
            RuntimeFilterIntent(
                field="school_type_requirement",
                value=["统招"],
                required=False,
                origin="controller",
            ),
        ),
        location_intent=None,
        age_intent=None,
    )

    target = compile_liepin_native_filters(intent, budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY).targets[0]

    assert target.recruitment_type_label is None
    assert "recruitmentType" not in target.to_safe_payload()
    assert any(reason.field == "school_type_requirement" for reason in target.partial_reasons)


def test_compile_liepin_native_filters_skips_experience_spanning_three_buckets() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
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

    target = compile_liepin_native_filters(intent, budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY).targets[0]

    assert target.experience_label is None
    assert "experience" not in target.to_safe_payload()
    assert any(reason.field == "experience_requirement" for reason in target.partial_reasons)


def test_compile_liepin_native_filters_keeps_open_min_experience_runtime_only() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        query_terms=("数据开发",),
        keyword_query="数据开发 ETL",
        requested_count=10,
        provider_scan_limit=10,
        source_plan_version="test",
        filter_intents=(
            RuntimeFilterIntent(
                field="experience_requirement",
                value=["min=5"],
                required=False,
                origin="controller",
            ),
        ),
        location_intent=None,
        age_intent=None,
    )

    target = compile_liepin_native_filters(intent, budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY).targets[0]

    assert target.experience_label is None
    assert "experience" not in target.to_safe_payload()
    assert any(reason.field == "experience_requirement" for reason in target.partial_reasons)
