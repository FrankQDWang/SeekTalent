"""Build and restore the compact durable plan for one Liepin round."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
from typing import TYPE_CHECKING, cast

from pydantic import JsonValue

from seektalent.canonical_json import canonical_json_bytes
from seektalent.liepin_round_work_plan_contracts import (
    LiepinCompiledSearchRequestV1,
    LiepinRoundLaneWorkItemV1,
    LiepinRoundWorkPlanV1,
)
from seektalent.providers.liepin.source_compiler import LiepinCompiledQuery
from seektalent.source_contracts import (
    LogicalQueryDispatch,
    RuntimeSourceBudgetPolicy,
)
from seektalent.sources.liepin.context import RuntimeLiepinContext

if TYPE_CHECKING:
    from seektalent.models import RequirementSheet


def source_budget_from_liepin_round_work_plan(
    plan: LiepinRoundWorkPlanV1,
) -> RuntimeSourceBudgetPolicy:
    payload = cast(Mapping[str, object], plan.source_budget_policy)
    allowed_keys = {
        "card_target",
        "detail_target",
        "scan_limit",
        "page_size",
        "max_pages",
        "max_cards",
        "max_details",
        "max_detail_recommendations",
        "max_detail_opens_per_run",
        "policy_version",
    }
    if set(payload) - allowed_keys:
        raise ValueError("runtime_workflow_round_budget_invalid")

    defaults = RuntimeSourceBudgetPolicy.defaults()

    def integer(name: str) -> int:
        value = payload.get(name, getattr(defaults, name))
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("runtime_workflow_round_budget_invalid")
        return value

    policy_version = payload.get("policy_version", defaults.policy_version)
    if not isinstance(policy_version, str):
        raise ValueError("runtime_workflow_round_budget_invalid")
    try:
        return RuntimeSourceBudgetPolicy(
            card_target=integer("card_target"),
            detail_target=integer("detail_target"),
            scan_limit=integer("scan_limit"),
            page_size=integer("page_size"),
            max_pages=integer("max_pages"),
            max_cards=integer("max_cards"),
            max_details=integer("max_details"),
            max_detail_recommendations=integer("max_detail_recommendations"),
            max_detail_opens_per_run=integer("max_detail_opens_per_run"),
            policy_version=policy_version,
        )
    except ValueError:
        raise ValueError("runtime_workflow_round_budget_invalid") from None


def build_liepin_round_work_plan(
    *,
    runtime_run_id: str,
    base_checkpoint_id: str,
    accepted_requirement_revision_id: str,
    source_plan_id: str,
    job_title: str,
    jd: str,
    notes: str,
    requirement_sheet: RequirementSheet,
    logical_queries: tuple[LogicalQueryDispatch, ...],
    compiled_queries: tuple[LiepinCompiledQuery, ...],
    source_budget_policy: RuntimeSourceBudgetPolicy,
    context: RuntimeLiepinContext,
    detail_claim_aware: bool,
    resume_context: Mapping[str, object],
) -> LiepinRoundWorkPlanV1:
    round_numbers = {query.round_no for query in logical_queries}
    if len(round_numbers) != 1:
        raise ValueError("liepin_round_work_plan_round_mismatch")
    round_no = next(iter(round_numbers))
    lanes: list[LiepinRoundLaneWorkItemV1] = []
    for logical_index, logical_query in enumerate(
        logical_queries,
        start=1,
    ):
        matching = tuple(
            query for query in compiled_queries if query.intent.query_instance_id == logical_query.query_instance_id
        )
        targets: tuple[LiepinCompiledQuery | None, ...] = matching if matching else (None,)
        logical_target_total = matching[0].intent.requested_count if matching else logical_query.requested_count
        for target_index, compiled_query in enumerate(
            targets,
            start=1,
        ):
            source_lane_run_id = f"{source_plan_id}:round:{round_no}:lane:{logical_index}"
            if compiled_query is not None:
                source_lane_run_id = f"{source_lane_run_id}:target:{target_index}"
                source_query_terms = tuple(compiled_query.search_request.query_terms)
                query_role = compiled_query.intent.query_role
                provider_scan_limit = compiled_query.intent.provider_scan_limit
                unsupported_reason_codes = tuple(item.safe_reason_code for item in compiled_query.unsupported_filters)
                compiled_search_request = _compiled_search_request_work_item(compiled_query)
            else:
                source_query_terms = tuple(logical_query.query_terms)
                query_role = logical_query.query_role
                provider_scan_limit = min(
                    logical_query.requested_count,
                    source_budget_policy.max_cards,
                )
                unsupported_reason_codes = ()
                compiled_search_request = None
            lanes.append(
                LiepinRoundLaneWorkItemV1(
                    lane_ordinal=len(lanes) + 1,
                    logical_query_ordinal=logical_index,
                    target_ordinal=target_index,
                    source_lane_run_id=source_lane_run_id,
                    query_instance_id=logical_query.query_instance_id,
                    query_fingerprint=logical_query.query_fingerprint,
                    query_role=query_role,
                    lane_type=logical_query.lane_type,
                    term_group_key=logical_query.term_group_key,
                    primary_anchor_family_id=(logical_query.primary_anchor_family_id),
                    non_anchor_term_family_ids=(logical_query.non_anchor_term_family_ids),
                    source_plan_version=(logical_query.source_plan_version),
                    logical_query_terms=tuple(logical_query.query_terms),
                    query_terms=source_query_terms,
                    keyword_query=logical_query.keyword_query,
                    logical_target_total=logical_target_total,
                    logical_requested_count=(logical_query.requested_count),
                    provider_scan_limit=provider_scan_limit,
                    unsupported_filter_reason_codes=(unsupported_reason_codes),
                    compiled_search_request=compiled_search_request,
                )
            )
    requirement_payload = requirement_sheet.model_dump(mode="json")
    return LiepinRoundWorkPlanV1(
        contract_version=("seektalent.source.liepin-round-work-plan/v1"),
        runtime_run_id=runtime_run_id,
        base_checkpoint_id=base_checkpoint_id,
        accepted_requirement_revision_id=(accepted_requirement_revision_id),
        requirement_sheet_hash=hashlib.sha256(canonical_json_bytes(requirement_payload)).hexdigest(),
        source_plan_id=source_plan_id,
        round_no=round_no,
        job_title=job_title,
        jd=jd,
        notes=notes,
        requirement_sheet=cast(dict[str, JsonValue], requirement_payload),
        source_context=cast(dict[str, JsonValue], context.to_runtime_payload()),
        source_budget_policy=cast(
            dict[str, JsonValue],
            source_budget_policy.to_public_payload(),
        ),
        resume_context=cast(dict[str, JsonValue], dict(resume_context)),
        detail_claim_aware=detail_claim_aware,
        lanes=tuple(lanes),
    )


def _compiled_search_request_work_item(
    compiled_query: LiepinCompiledQuery,
) -> LiepinCompiledSearchRequestV1:
    request = compiled_query.search_request
    if request.runtime_constraints or request.provider_filters:
        raise ValueError("liepin_round_work_plan_compiled_request_unsupported")
    return LiepinCompiledSearchRequestV1(
        query_terms=tuple(request.query_terms),
        query_role=request.query_role,
        keyword_query=request.keyword_query,
        adapter_notes=tuple(request.adapter_notes),
        fetch_mode=request.fetch_mode,
        page_size=request.page_size,
        provider_context=dict(request.provider_context),
        cursor=request.cursor,
    )


__all__ = [
    "build_liepin_round_work_plan",
    "source_budget_from_liepin_round_work_plan",
]
