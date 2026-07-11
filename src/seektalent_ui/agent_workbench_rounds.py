from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, cast

from seektalent.public_payload_safety import public_source_identifier, public_text
from seektalent_runtime_control.models import RuntimeStageOutput


PUBLIC_ROUND_SUMMARY_OUTPUT_KINDS = {
    "runtime_public_round_query",
    "runtime_public_source_result",
    "runtime_public_merge",
    "runtime_public_scoring",
    "runtime_public_feedback",
}
PUBLIC_FINALIZATION_OUTPUT_KIND = "runtime_public_finalization"
PUBLIC_STAGE_OUTPUT_SCHEMA = "runtime-public-stage-output/v2"
PUBLIC_EVENT_SCHEMA = "runtime_public_event_v1"
AgentWorkbenchQueryGroupLifecycle = Literal["planned", "executed"]
AgentWorkbenchQueryExecutionStatus = Literal["completed", "partial", "blocked", "failed"]
_PUBLIC_STAGE_STATUSES = {
    "pending",
    "running",
    "completed",
    "partial",
    "blocked",
    "failed",
    "cancelled",
}
_PUBLIC_QUERY_EXECUTION_REASON_CODES = {
    "job_lease_expired",
    "relay_pending_worker",
    "runtime_failed",
    "source_login_required",
    "source_account_mismatch",
    "source_browser_timeout",
    "source_browser_backend_unavailable",
    "source_browser_extension_disconnected",
    "source_browser_policy_blocked",
    "source_risk_or_verification_required",
    "source_browser_interaction_required",
    "source_budget_exhausted",
    "source_filter_applied",
    "source_filter_partial",
    "source_filter_unavailable",
    "source_filter_unsupported",
    "source_filter_degraded",
    "source_location_filter_unsupported",
    "source_age_filter_unsupported",
    "source_provider_failed",
    "source_partial",
    "source_unknown",
}
_QUERY_EXECUTION_REASON_ALIASES = {
    "blocked_backend_unavailable": "source_browser_backend_unavailable",
    "blocked_login_required": "source_login_required",
    "failed_provider_error": "source_provider_failed",
    "login_required": "source_login_required",
    "partial_timeout": "source_browser_timeout",
    "runtime_failed": "source_provider_failed",
    "cancelled_by_user": "source_unknown",
    "source_location_filter_partial": "source_filter_partial",
    "source_age_filter_unsupported": "source_filter_unavailable",
    "source_location_filter_unsupported": "source_filter_unavailable",
    "source_filter_unsupported": "source_filter_unavailable",
    "source_filter_applied": "source_filter_applied",
}


class AgentWorkbenchProjectionError(RuntimeError):
    def __init__(self, reason_code: str, *, output_id: str | None = None) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.output_id = output_id


@dataclass(frozen=True)
class AgentWorkbenchQueryExecutionProjection:
    source_kind: str
    status: AgentWorkbenchQueryExecutionStatus
    raw_candidate_count: int = 0
    unique_candidate_count: int = 0
    duplicate_candidate_count: int = 0
    safe_reason_code: str | None = None


@dataclass(frozen=True)
class AgentWorkbenchQueryGroupProjection:
    query_instance_id: str
    term_group_key: str
    query_role: str
    lane_type: str
    query_terms: tuple[str, ...]
    keyword_query: str
    lifecycle: AgentWorkbenchQueryGroupLifecycle
    execution_status: AgentWorkbenchQueryExecutionStatus | None
    attempted: bool
    raw_candidate_count: int = 0
    unique_candidate_count: int = 0
    duplicate_candidate_count: int = 0
    executions: tuple[AgentWorkbenchQueryExecutionProjection, ...] = ()


@dataclass(frozen=True)
class AgentWorkbenchRoundStageProjection:
    stage: str
    source_kind: str | None = None
    status: str = "completed"


@dataclass(frozen=True)
class AgentWorkbenchRoundSummaryProjection:
    round_no: int
    status: str
    stage_outputs: tuple[AgentWorkbenchRoundStageProjection, ...] = ()
    query_groups: tuple[AgentWorkbenchQueryGroupProjection, ...] = ()
    raw_candidate_count: int | None = None
    source_identity_count: int | None = None
    unique_new_count: int | None = None
    total_merged_identity_count: int | None = None
    newly_scored_count: int | None = None
    top_pool_count: int | None = None
    resume_quality_comment: str | None = None
    reflection_summary: str | None = None
    suggested_activate_terms: tuple[str, ...] = ()
    suggested_keep_terms: tuple[str, ...] = ()
    suggested_deprioritize_terms: tuple[str, ...] = ()
    suggested_drop_terms: tuple[str, ...] = ()
    suggested_add_filter_fields: tuple[str, ...] = ()
    suggested_keep_filter_fields: tuple[str, ...] = ()
    suggested_drop_filter_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentWorkbenchRunFinalizationProjection:
    selected_identity_count: int | None = None
    revision: int | None = None
    reason_code: str | None = None
    status: str = "completed"


@dataclass
class _RoundFacts:
    round_no: int
    stages: set[str] = field(default_factory=set)
    statuses: set[str] = field(default_factory=set)
    stage_outputs: list[AgentWorkbenchRoundStageProjection] = field(default_factory=list)
    failed: bool = False
    query_groups: dict[str, AgentWorkbenchQueryGroupProjection] = field(default_factory=dict)
    raw_candidate_count: int | None = None
    source_identity_count: int | None = None
    unique_new_count: int | None = None
    total_merged_identity_count: int | None = None
    newly_scored_count: int | None = None
    top_pool_count: int | None = None
    resume_quality_comment: str | None = None
    reflection_summary: str | None = None
    suggested_activate_terms: tuple[str, ...] = ()
    suggested_keep_terms: tuple[str, ...] = ()
    suggested_deprioritize_terms: tuple[str, ...] = ()
    suggested_drop_terms: tuple[str, ...] = ()
    suggested_add_filter_fields: tuple[str, ...] = ()
    suggested_keep_filter_fields: tuple[str, ...] = ()
    suggested_drop_filter_fields: tuple[str, ...] = ()

    def freeze(self) -> AgentWorkbenchRoundSummaryProjection:
        return AgentWorkbenchRoundSummaryProjection(
            round_no=self.round_no,
            status=_derive_status(self.stages, statuses=self.statuses, failed=self.failed),
            stage_outputs=tuple(self.stage_outputs),
            query_groups=tuple(self.query_groups.values()),
            raw_candidate_count=self.raw_candidate_count,
            source_identity_count=self.source_identity_count,
            unique_new_count=self.unique_new_count,
            total_merged_identity_count=self.total_merged_identity_count,
            newly_scored_count=self.newly_scored_count,
            top_pool_count=self.top_pool_count,
            resume_quality_comment=self.resume_quality_comment,
            reflection_summary=self.reflection_summary,
            suggested_activate_terms=self.suggested_activate_terms,
            suggested_keep_terms=self.suggested_keep_terms,
            suggested_deprioritize_terms=self.suggested_deprioritize_terms,
            suggested_drop_terms=self.suggested_drop_terms,
            suggested_add_filter_fields=self.suggested_add_filter_fields,
            suggested_keep_filter_fields=self.suggested_keep_filter_fields,
            suggested_drop_filter_fields=self.suggested_drop_filter_fields,
        )


def round_summaries_from_stage_outputs(
    outputs: Sequence[RuntimeStageOutput],
    *,
    expected_runtime_run_id: str | None = None,
) -> tuple[AgentWorkbenchRoundSummaryProjection, ...]:
    by_round: dict[tuple[str, int], _RoundFacts] = {}
    for output in outputs:
        _validate_expected_runtime_run_id(output, expected_runtime_run_id=expected_runtime_run_id)
        if output.output_kind == PUBLIC_FINALIZATION_OUTPUT_KIND:
            continue
        if output.output_kind not in PUBLIC_ROUND_SUMMARY_OUTPUT_KINDS:
            continue
        payload = _validated_payload(output)
        if output.round_no is None:
            raise AgentWorkbenchProjectionError("workbench_round_output_round_required", output_id=output.output_id)
        key = (output.runtime_run_id, output.round_no)
        summary = by_round.setdefault(key, _RoundFacts(round_no=output.round_no))
        _apply_round_output(summary, output=output, payload=payload)
    return tuple(by_round[key].freeze() for key in sorted(by_round, key=lambda item: (item[0], item[1])))


def deterministic_finalization_from_stage_outputs(
    outputs: Sequence[RuntimeStageOutput],
    *,
    expected_runtime_run_id: str | None = None,
) -> AgentWorkbenchRunFinalizationProjection | None:
    for output in outputs:
        _validate_expected_runtime_run_id(output, expected_runtime_run_id=expected_runtime_run_id)
    finalizations = [output for output in outputs if output.output_kind == PUBLIC_FINALIZATION_OUTPUT_KIND]
    if not finalizations:
        return None
    output = sorted(finalizations, key=lambda item: (item.created_at, item.output_id))[-1]
    payload = _validated_payload(output)
    if output.round_no is not None:
        raise AgentWorkbenchProjectionError(
            "workbench_finalization_output_must_be_run_level", output_id=output.output_id
        )
    counts = _mapping(payload.get("counts"))
    details = _mapping(payload.get("details"))
    return AgentWorkbenchRunFinalizationProjection(
        selected_identity_count=_int_or_none(counts.get("selectedIdentityCount")),
        revision=_int_or_none(details.get("finalizationRevision")),
        reason_code=_safe_public_detail_text(details.get("finalizationReasonCode"), max_length=2000),
        status=_safe_public_stage_status(payload.get("status")),
    )


def _apply_round_output(summary: _RoundFacts, *, output: RuntimeStageOutput, payload: Mapping[str, object]) -> None:
    stage = str(output.stage)
    status = _safe_public_stage_status(payload.get("status"))
    if status in {"failed", "cancelled"}:
        summary.failed = True
    summary.statuses.add(status)
    summary.stages.add(stage)
    summary.stage_outputs.append(
        AgentWorkbenchRoundStageProjection(
            stage=stage,
            source_kind=_safe_public_source_identifier(output.node_id),
            status=status,
        )
    )
    counts = _mapping(payload.get("counts"))
    details = _mapping(payload.get("details"))
    if stage == "round_query":
        _merge_query_groups(
            summary,
            _query_groups(details.get("queryGroups"), expected_lifecycle="planned"),
            output_id=output.output_id,
        )
    elif stage == "source_result":
        raw_count = _int_or_none(counts.get("roundReturned"))
        if raw_count is not None:
            summary.raw_candidate_count = (summary.raw_candidate_count or 0) + raw_count
        source_identity_count = _int_or_none(counts.get("roundIdentities"))
        if source_identity_count is not None:
            summary.source_identity_count = (summary.source_identity_count or 0) + source_identity_count
    elif stage == "merge":
        round_unique = _int_or_none(counts.get("roundUniqueIdentities"))
        if round_unique is not None:
            summary.unique_new_count = round_unique
        total_merged = _int_or_none(counts.get("mergedIdentities"))
        if total_merged is not None:
            summary.total_merged_identity_count = total_merged
    elif stage == "scoring":
        newly_scored = _int_or_none(counts.get("roundIdentities"))
        if newly_scored is not None:
            summary.newly_scored_count = newly_scored
        top_pool = _int_or_none(counts.get("topPoolCount"))
        if top_pool is not None:
            summary.top_pool_count = top_pool
    elif stage == "feedback":
        _merge_query_groups(
            summary,
            _query_groups(details.get("queryGroups"), expected_lifecycle="executed"),
            output_id=output.output_id,
        )
        summary.resume_quality_comment = _replace_text(
            summary.resume_quality_comment, details.get("resumeQualityComment")
        )
        summary.reflection_summary = _replace_text(summary.reflection_summary, details.get("reflectionSummary"))
        summary.suggested_activate_terms = _string_tuple(details.get("suggestedActivateTerms"))
        summary.suggested_keep_terms = _string_tuple(details.get("suggestedKeepTerms"))
        summary.suggested_deprioritize_terms = _string_tuple(details.get("suggestedDeprioritizeTerms"))
        summary.suggested_drop_terms = _string_tuple(details.get("suggestedDropTerms"))
        summary.suggested_add_filter_fields = _string_tuple(details.get("suggestedAddFilterFields"))
        summary.suggested_keep_filter_fields = _string_tuple(details.get("suggestedKeepFilterFields"))
        summary.suggested_drop_filter_fields = _string_tuple(details.get("suggestedDropFilterFields"))


def _validated_payload(output: RuntimeStageOutput) -> Mapping[str, object]:
    payload = _mapping(output.output)
    stage = _text(payload.get("stage"))
    if stage is None:
        raise AgentWorkbenchProjectionError("workbench_round_output_stage_missing", output_id=output.output_id)
    expected_output_kind = f"runtime_public_{stage}"
    payload_round = payload.get("roundNo")
    payload_source = payload.get("sourceKind")
    if (
        output.schema_version != PUBLIC_STAGE_OUTPUT_SCHEMA
        or output.output_kind != expected_output_kind
        or output.stage != stage
        or payload_round != output.round_no
        or payload_source != output.node_id
        or payload.get("schemaVersion") != PUBLIC_STAGE_OUTPUT_SCHEMA
        or payload.get("publicEventSchemaVersion") != PUBLIC_EVENT_SCHEMA
    ):
        raise AgentWorkbenchProjectionError("workbench_round_output_metadata_mismatch", output_id=output.output_id)
    return payload


def _validate_expected_runtime_run_id(
    output: RuntimeStageOutput,
    *,
    expected_runtime_run_id: str | None,
) -> None:
    if expected_runtime_run_id is not None and output.runtime_run_id != expected_runtime_run_id:
        raise AgentWorkbenchProjectionError("workbench_round_output_runtime_run_mismatch", output_id=output.output_id)


def _derive_status(stages: set[str], *, statuses: set[str], failed: bool) -> str:
    if failed or "failed" in statuses:
        return "failed"
    if "cancelled" in statuses:
        return "cancelled"
    if "blocked" in statuses:
        return "blocked"
    if "partial" in statuses:
        return "partial"
    if "feedback" in stages:
        return "completed"
    return "running"


def _merge_query_groups(
    summary: _RoundFacts,
    groups: Sequence[AgentWorkbenchQueryGroupProjection],
    *,
    output_id: str,
) -> None:
    for group in groups:
        existing = summary.query_groups.get(group.query_instance_id)
        if existing is None:
            summary.query_groups[group.query_instance_id] = group
            continue
        if _query_group_identity(existing) != _query_group_identity(group):
            raise AgentWorkbenchProjectionError("workbench_query_group_identity_mismatch", output_id=output_id)
        if existing.lifecycle == "executed" and group.lifecycle == "planned":
            continue
        summary.query_groups[group.query_instance_id] = group


def _query_group_identity(group: AgentWorkbenchQueryGroupProjection) -> tuple[object, ...]:
    return (
        group.term_group_key,
        group.query_role,
        group.lane_type,
        group.query_terms,
        group.keyword_query,
    )


def _query_groups(
    value: object,
    *,
    expected_lifecycle: AgentWorkbenchQueryGroupLifecycle,
) -> tuple[AgentWorkbenchQueryGroupProjection, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return ()
    groups: list[AgentWorkbenchQueryGroupProjection] = []
    seen_query_instance_ids: set[str] = set()
    for item in value:
        item_mapping = _mapping(item)
        query_instance_id = _safe_public_query_text(item_mapping.get("queryInstanceId"), max_length=160)
        term_group_key = _safe_public_query_text(item_mapping.get("termGroupKey"), max_length=160)
        query_role = _safe_public_query_text(item_mapping.get("queryRole"), max_length=80)
        lane_type = _safe_public_query_text(item_mapping.get("laneType"), max_length=80)
        query_terms = _safe_public_query_terms(item_mapping.get("queryTerms"))
        keyword_query = _safe_public_query_text(item_mapping.get("keywordQuery"), max_length=2000)
        lifecycle = _text(item_mapping.get("lifecycle"))
        if (
            query_instance_id is None
            or query_instance_id in seen_query_instance_ids
            or term_group_key is None
            or query_role is None
            or lane_type is None
            or not query_terms
            or keyword_query is None
            or lifecycle not in {"planned", "executed"}
            or lifecycle != expected_lifecycle
        ):
            continue
        if lifecycle == "planned":
            group = AgentWorkbenchQueryGroupProjection(
                query_instance_id=query_instance_id,
                term_group_key=term_group_key,
                query_role=query_role,
                lane_type=lane_type,
                query_terms=query_terms,
                keyword_query=keyword_query,
                lifecycle=cast(AgentWorkbenchQueryGroupLifecycle, lifecycle),
                execution_status=None,
                attempted=False,
            )
        else:
            execution_status = _text(item_mapping.get("executionStatus"))
            attempted = item_mapping.get("attempted")
            if execution_status not in {"completed", "partial", "blocked", "failed"} or not isinstance(attempted, bool):
                continue
            group = AgentWorkbenchQueryGroupProjection(
                query_instance_id=query_instance_id,
                term_group_key=term_group_key,
                query_role=query_role,
                lane_type=lane_type,
                query_terms=query_terms,
                keyword_query=keyword_query,
                lifecycle="executed",
                execution_status=cast(AgentWorkbenchQueryExecutionStatus, execution_status),
                attempted=attempted,
                raw_candidate_count=_int_or_none(item_mapping.get("rawCandidateCount")) or 0,
                unique_candidate_count=_int_or_none(item_mapping.get("uniqueCandidateCount")) or 0,
                duplicate_candidate_count=_int_or_none(item_mapping.get("duplicateCandidateCount")) or 0,
                executions=_query_executions(item_mapping.get("executions")),
            )
        groups.append(group)
        seen_query_instance_ids.add(query_instance_id)
        if len(groups) >= 2:
            break
    return tuple(groups)


def _query_executions(value: object) -> tuple[AgentWorkbenchQueryExecutionProjection, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return ()
    executions: list[AgentWorkbenchQueryExecutionProjection] = []
    seen_sources: set[str] = set()
    for item in value:
        item_mapping = _mapping(item)
        source_kind = _safe_public_source_identifier(item_mapping.get("sourceKind"))
        status = _text(item_mapping.get("status"))
        if (
            source_kind is None
            or source_kind in seen_sources
            or status not in {"completed", "partial", "blocked", "failed"}
        ):
            continue
        executions.append(
            AgentWorkbenchQueryExecutionProjection(
                source_kind=source_kind,
                status=cast(AgentWorkbenchQueryExecutionStatus, status),
                raw_candidate_count=_int_or_none(item_mapping.get("rawCandidateCount")) or 0,
                unique_candidate_count=_int_or_none(item_mapping.get("uniqueCandidateCount")) or 0,
                duplicate_candidate_count=_int_or_none(item_mapping.get("duplicateCandidateCount")) or 0,
                safe_reason_code=_public_query_execution_reason_code(item_mapping.get("safeReasonCode")),
            )
        )
        seen_sources.add(source_kind)
        if len(executions) >= 2:
            break
    return tuple(executions)


def _safe_public_query_text(value: object, *, max_length: int) -> str | None:
    return public_text(value, max_length=max_length)


def _safe_public_query_terms(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return ()
    terms: list[str] = []
    for item in value:
        text = _safe_public_query_text(item, max_length=160)
        if text is not None:
            terms.append(text)
        if len(terms) >= 40:
            break
    return tuple(terms)


def _safe_public_source_identifier(value: object) -> str | None:
    return public_source_identifier(value)


def _public_query_execution_reason_code(value: object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    if text in _PUBLIC_QUERY_EXECUTION_REASON_CODES:
        return text
    mapped = _QUERY_EXECUTION_REASON_ALIASES.get(text)
    return mapped if mapped in _PUBLIC_QUERY_EXECUTION_REASON_CODES else None


def _replace_text(current: str | None, value: object) -> str | None:
    text = _safe_public_detail_text(value, max_length=2000)
    return text if text is not None else current


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return ()
    values: list[str] = []
    for item in value:
        text = _safe_public_detail_text(item, max_length=200)
        if text is not None:
            values.append(text)
        if len(values) >= 50:
            break
    return tuple(values)


def _safe_public_detail_text(value: object, *, max_length: int) -> str | None:
    return _safe_public_query_text(value, max_length=max_length)


def _safe_public_stage_status(value: object) -> str:
    if not isinstance(value, str):
        return "completed"
    status = value.strip()
    return status if status in _PUBLIC_STAGE_STATUSES else "completed"


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None
