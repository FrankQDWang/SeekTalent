from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from seektalent.models import (
    AgeRequirement,
    HardConstraintSlots,
    PreferenceSlots,
    ProposedFilterPlan,
    RequirementSheet,
)
from seektalent.runtime.logical_query_dispatch import LogicalQueryDispatch
from seektalent.runtime.orchestrator import _liepin_filter_warning_reason
from seektalent.runtime.source_lanes import RuntimeSourceBudgetPolicy
from seektalent.runtime.source_lanes import RuntimeSourceLaneEvent
from seektalent.runtime.public_events import public_source_reason_code
from seektalent.runtime.source_filters import (
    RuntimeFilterIntent,
    RuntimeLocationExecutionIntent,
    build_runtime_filter_intents,
    build_runtime_location_execution_intent,
)
from seektalent.runtime.source_query_intent import (
    RuntimeSourceQueryIntent,
    build_runtime_source_query_intents,
    normalize_source_search_action,
)
from seektalent.runtime.source_round_dispatch import (
    RuntimeSourceInvariantError,
    SourceRoundAdapterResult,
    SourceRoundDispatchRequest,
    dispatch_source_rounds,
)
from seektalent.providers.liepin.source_compiler import compile_liepin_source_query_intents


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        job_title="Data Engineer",
        title_anchor_terms=["data engineer"],
        title_anchor_rationale="Title anchor.",
        role_summary="Build data platforms.",
        must_have_capabilities=["python", "spark"],
        preferred_capabilities=["clickhouse", "hadoop"],
        hard_constraints=HardConstraintSlots(
            locations=["Shanghai"],
            age_requirement=AgeRequirement(max_age=35, raw_text="35 or younger"),
        ),
        preferences=PreferenceSlots(preferred_locations=["Shanghai"]),
        initial_query_term_pool=[],
        scoring_rationale="Prefer strong data platform fit.",
    )


def _filter_plan() -> ProposedFilterPlan:
    return ProposedFilterPlan(
        optional_filters={
            "age_requirement": ["max=35"],
            "position": "Data Engineer",
        },
        added_filter_fields=["age_requirement", "position"],
    )


def _logical_dispatches() -> tuple[LogicalQueryDispatch, ...]:
    return (
        LogicalQueryDispatch(
            round_no=2,
            query_role="exploit",
            lane_type="exploit",
            query_instance_id="query-exploit",
            query_fingerprint="fingerprint-exploit",
            query_terms=("data engineer", "spark"),
            keyword_query="data engineer spark",
            requested_count=7,
            source_plan_version="source-plan-v3",
        ),
        LogicalQueryDispatch(
            round_no=2,
            query_role="explore",
            lane_type="generic_explore",
            query_instance_id="query-explore",
            query_fingerprint="fingerprint-explore",
            query_terms=("data engineer", "flink"),
            keyword_query="data engineer flink",
            requested_count=3,
            source_plan_version="source-plan-v3",
        ),
    )


def test_runtime_source_intent_preserves_query_identity_role_filters_and_budget_for_selected_sources() -> None:
    filter_intents = build_runtime_filter_intents(
        requirement_sheet=_requirement_sheet(),
        proposed_filter_plan=_filter_plan(),
    )
    location_intent = build_runtime_location_execution_intent(
        requirement_sheet=_requirement_sheet(),
        proposed_filter_plan=_filter_plan(),
        round_no=2,
    )

    intents_by_source = build_runtime_source_query_intents(
        source_kinds=("cts", "liepin"),
        logical_dispatches=_logical_dispatches(),
        filter_intents=filter_intents,
        location_intent=location_intent,
        age_intent=None,
        source_budget_policy=RuntimeSourceBudgetPolicy(cts_page_size=10, liepin_max_cards=30),
        must_have_capabilities=tuple(_requirement_sheet().must_have_capabilities),
        preferred_capabilities=tuple(_requirement_sheet().preferred_capabilities),
    )

    assert set(intents_by_source) == {"cts", "liepin"}
    assert {intent.field for intent in filter_intents} == {"age_requirement"}
    assert location_intent is not None
    assert location_intent.allowed_locations == ("Shanghai",)

    for cts_intent, liepin_intent in zip(intents_by_source["cts"], intents_by_source["liepin"], strict=True):
        assert liepin_intent.query_instance_id == cts_intent.query_instance_id
        assert liepin_intent.query_fingerprint == cts_intent.query_fingerprint
        assert liepin_intent.query_role == cts_intent.query_role
        assert liepin_intent.lane_type == cts_intent.lane_type
        assert liepin_intent.query_terms == cts_intent.query_terms
        assert liepin_intent.keyword_query == cts_intent.keyword_query
        assert liepin_intent.filter_intents == cts_intent.filter_intents
        assert liepin_intent.location_intent == cts_intent.location_intent
        assert liepin_intent.must_have_capabilities == ("python", "spark")
        assert liepin_intent.preferred_capabilities == ("clickhouse", "hadoop")

    assert [intent.requested_count for intent in intents_by_source["cts"]] == [7, 3]
    assert [intent.requested_count for intent in intents_by_source["liepin"]] == [2, 1]
    assert [intent.provider_scan_limit for intent in intents_by_source["liepin"]] == [6, 3]


def test_runtime_source_intent_budgeting_does_not_branch_on_concrete_source_ids() -> None:
    source = Path("src/seektalent/runtime/source_query_intent.py").read_text(encoding="utf-8")

    assert 'source_kind == "liepin"' not in source
    assert 'source_kind != "liepin"' not in source


def test_liepin_source_compiler_preserves_runtime_role_budget_and_query_identity() -> None:
    filter_intents = build_runtime_filter_intents(
        requirement_sheet=_requirement_sheet(),
        proposed_filter_plan=_filter_plan(),
    )
    intents_by_source = build_runtime_source_query_intents(
        source_kinds=("liepin",),
        logical_dispatches=_logical_dispatches(),
        filter_intents=filter_intents,
        location_intent=build_runtime_location_execution_intent(
            requirement_sheet=_requirement_sheet(),
            proposed_filter_plan=_filter_plan(),
            round_no=2,
        ),
        age_intent=None,
        source_budget_policy=RuntimeSourceBudgetPolicy(liepin_card_page_size=30, liepin_max_cards=30),
        must_have_capabilities=tuple(_requirement_sheet().must_have_capabilities),
        preferred_capabilities=tuple(_requirement_sheet().preferred_capabilities),
    )

    bundle = compile_liepin_source_query_intents(intents_by_source["liepin"])

    compiled_requests = [query.search_request for query in bundle.queries]
    assert [request.query_role for request in compiled_requests] == ["primary", "expansion"]
    assert [request.fetch_mode for request in compiled_requests] == ["detail", "detail"]
    assert [request.page_size for request in compiled_requests] == [2, 1]
    assert [request.provider_context["liepin_max_cards"] for request in compiled_requests] == ["6", "3"]
    assert [request.provider_context["liepin_fetch_strategy"] for request in compiled_requests] == [
        "detail_backed_resume_search",
        "detail_backed_resume_search",
    ]
    assert [request.provider_context["query_instance_id"] for request in compiled_requests] == [
        "query-exploit",
        "query-explore",
    ]
    assert [request.provider_context["query_fingerprint"] for request in compiled_requests] == [
        "fingerprint-exploit",
        "fingerprint-explore",
    ]
    assert all("liepin_must_haves_json" not in request.provider_context for request in compiled_requests)
    assert all("liepin_nice_to_haves_json" not in request.provider_context for request in compiled_requests)
    native_payloads = [
        json.loads(str(request.provider_context["liepin_native_filters_json"]))
        for request in compiled_requests
    ]
    assert [payload["city"] for payload in native_payloads] == [
        {"section": "expected", "label": "Shanghai"},
        {"section": "expected", "label": "Shanghai"},
    ]
    assert [payload["age"] for payload in native_payloads] == [
        {"section": "age", "label": "35岁以下"},
        {"section": "age", "label": "35岁以下"},
    ]
    assert bundle.unsupported_filters == ()


def test_legacy_search_cts_action_is_normalized_before_source_planning() -> None:
    assert normalize_source_search_action("search_cts") == "source_search"
    assert normalize_source_search_action("stop") == "stop"


def test_source_dispatch_rejects_missing_intents_for_selected_source() -> None:
    intents_by_source = build_runtime_source_query_intents(
        source_kinds=("cts",),
        logical_dispatches=_logical_dispatches(),
        filter_intents=(),
        location_intent=None,
        age_intent=None,
        source_budget_policy=RuntimeSourceBudgetPolicy(),
    )

    async def adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        del request
        return SourceRoundAdapterResult(source="cts", status="completed")

    with pytest.raises(RuntimeSourceInvariantError, match="missing_source_query_intents:liepin"):
        asyncio.run(
            dispatch_source_rounds(
                request=SourceRoundDispatchRequest(
                    runtime_run_id="run-1",
                    round_no=2,
                    logical_queries=_logical_dispatches(),
                    selected_sources=("cts", "liepin"),
                    seen_resume_ids=frozenset(),
                    seen_dedup_keys=frozenset(),
                    requirement_sheet=_requirement_sheet(),
                    source_query_intents_by_source=intents_by_source,
                ),
                cts_adapter=adapter,
                liepin_adapter=adapter,
            )
        )


def test_filter_capability_reason_codes_are_public_safe() -> None:
    assert public_source_reason_code("source_filter_unsupported") == "source_filter_unsupported"
    assert public_source_reason_code("source_filter_degraded") == "source_filter_degraded"
    assert public_source_reason_code("source_location_filter_unsupported") == "source_location_filter_unsupported"
    assert public_source_reason_code("source_age_filter_unsupported") == "source_age_filter_unsupported"
    event = RuntimeSourceLaneEvent(
        schema_version="runtime_source_lane_event_v1",
        runtime_run_id="run-1",
        source_plan_id="plan-1",
        source_lane_run_id="lane-1",
        source="liepin",
        attempt=1,
        event_seq=1,
        event_type="source_lane_completed",
        status="completed",
        safe_reason_code="source_age_filter_unsupported",
    )

    assert event.to_public_payload()["safe_reason_code"] == "source_age_filter_unsupported"


def test_liepin_active_opencli_resume_path_does_not_use_old_requirement_fields() -> None:
    files = [
        "src/seektalent/providers/liepin/opencli_worker_client.py",
        "src/seektalent/providers/liepin/opencli_retriever.py",
        "src/seektalent/providers/liepin/source_compiler.py",
    ]
    active_text = "\n".join(Path(path).read_text() for path in files)

    assert "liepin_must_haves_json" not in active_text
    assert "liepin_nice_to_haves_json" not in active_text
    assert '"must_haves"' not in active_text
    assert '"nice_to_haves"' not in active_text


def test_liepin_runtime_full_source_path_is_detail_backed_not_recommendation_first() -> None:
    text = Path("src/seektalent/sources/liepin/runtime_lane.py").read_text()
    card_result_block = text.split("def _card_lane_result_from_search_result", 1)[1].split(
        "def _run_detail_lane",
        1,
    )[0]

    assert "detail_backed_resume_search" in text
    assert "detail_recommended" in text
    assert "if detail_backed\n        else _detail_recommendations_for_candidates(" in card_result_block


def test_liepin_supported_native_filters_do_not_emit_unsupported_warning() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        query_terms=("数据开发", "ETL"),
        keyword_query="数据开发 ETL",
        requested_count=10,
        provider_scan_limit=10,
        source_plan_version="test",
        filter_intents=(
            RuntimeFilterIntent(
                field="experience_requirement",
                value=["min=3"],
                required=False,
                origin="requirement_sheet",
            ),
            RuntimeFilterIntent(
                field="age_requirement",
                value=["max=35"],
                required=False,
                origin="requirement_sheet",
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

    assert _liepin_filter_warning_reason((intent,)) is None


def test_liepin_supported_education_filters_do_not_emit_unsupported_warning() -> None:
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
        ),
        location_intent=None,
        age_intent=None,
    )

    assert _liepin_filter_warning_reason((intent,)) is None
