from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from seektalent_ui.models import (
    RuntimeSourceCoverageStatus,
    RuntimeSourceDetailState,
    RuntimeSourceDisplayStatus,
    WorkbenchRuntimeSourceLaneStateResponse,
    WorkbenchRuntimeSourceStateResponse,
    WorkbenchRuntimeSourceWorkflowStepResponse,
)
from seektalent_ui.workbench_response import public_runtime_source_reason_code
from seektalent_ui.workbench_store import (
    RuntimeSourceCountProjection,
    WorkbenchRuntimeSourceLaneLatestState,
    WorkbenchSession,
    WorkbenchSourceRun,
    WorkbenchStore,
    WorkbenchUser,
)


DISPLAY_STATUSES: dict[str, RuntimeSourceDisplayStatus] = {
    "pending": "pending",
    "running": "running",
    "completed": "completed",
    "partial": "partial",
    "blocked": "blocked",
    "failed": "failed",
    "cancelled": "cancelled",
}

COVERAGE_STATUSES: dict[str, RuntimeSourceCoverageStatus] = {
    "pending": "pending",
    "complete": "complete",
    "degraded": "degraded",
    "empty": "empty",
}

DETAIL_STATES: dict[str, RuntimeSourceDetailState] = {
    "detail_recommended": "detail_recommended",
    "pending_approval": "pending_approval",
    "leased": "leased",
    "completed": "completed",
    "blocked": "blocked",
}


def runtime_source_state_response(
    *,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session: WorkbenchSession,
    runtime_source_count_projection: Mapping[Literal["cts", "liepin"], RuntimeSourceCountProjection] | None = None,
) -> WorkbenchRuntimeSourceStateResponse:
    latest_states = store.list_runtime_source_lane_latest_state(user=user, session_id=session.session_id)
    latest_by_source = {state.source_kind: state for state in latest_states}
    if runtime_source_count_projection is None:
        runtime_source_count_projection = store.latest_runtime_source_count_projection(
            user=user,
            session_id=session.session_id,
        )
    sources = [
        _runtime_source_lane_state_response(
            source_run,
            latest_by_source.get(source_run.source_kind),
            runtime_source_count_projection.get(source_run.source_kind),
        )
        for source_run in session.source_runs
    ]
    coverage_status, revision, reason_code = _runtime_source_coverage_fields(session, latest_states, sources)
    return WorkbenchRuntimeSourceStateResponse(
        selectedSourceKinds=[source_run.source_kind for source_run in session.source_runs],
        coverageStatus=coverage_status,
        finalizationRevision=revision,
        finalizationReasonCode=reason_code,
        identityMergeCount=_runtime_source_merge_count(latest_states, "identity_merge_count"),
        ambiguousDuplicateCount=_runtime_source_merge_count(latest_states, "ambiguous_duplicate_count"),
        canonicalResumeSelectedCount=_runtime_source_merge_count(latest_states, "canonical_resume_selected_count"),
        sources=sources,
    )


def _runtime_source_lane_state_response(
    source_run: WorkbenchSourceRun,
    latest_state: WorkbenchRuntimeSourceLaneLatestState | None,
    runtime_count_projection: RuntimeSourceCountProjection | None = None,
) -> WorkbenchRuntimeSourceLaneStateResponse:
    payload = latest_state.payload if latest_state is not None else {}
    typed_safe_counts = _string_keyed_object_dict(payload.get("safe_counts"))
    use_runtime_projection = (
        runtime_count_projection is not None
        and runtime_count_projection.status is not None
        and (latest_state is None or runtime_count_projection.event_seq >= latest_state.event_seq)
    )
    status_source = (
        runtime_count_projection.status
        if use_runtime_projection and runtime_count_projection is not None
        else latest_state.status if latest_state is not None else source_run.status
    )
    display_status = _runtime_display_status(status_source, default="pending")
    if latest_state is not None and not use_runtime_projection:
        source_run_reason_fallback = (
            source_run.warning_code if latest_state.status in {"blocked", "failed", "cancelled"} else None
        )
        reason_code = _runtime_source_reason_code(
            payload.get("safe_reason_code"),
            payload.get("blocked_reason_code"),
            payload.get("stop_reason_code"),
            source_run_reason_fallback,
        )
        event_type = latest_state.event_type
        event_seq = latest_state.event_seq
    elif runtime_count_projection is not None and runtime_count_projection.status is not None:
        reason_code = _runtime_source_reason_code(runtime_count_projection.warning_code)
        event_type = "source_result"
        event_seq = runtime_count_projection.event_seq
    else:
        reason_code = _runtime_source_reason_code(source_run.warning_code)
        event_type = None
        event_seq = None
    cards_scanned_fallback = (
        runtime_count_projection.cards_scanned_count
        if runtime_count_projection is not None and runtime_count_projection.cards_scanned_count is not None
        else source_run.cards_scanned_count
    )
    unique_candidates_fallback = (
        runtime_count_projection.unique_candidates_count
        if runtime_count_projection is not None and runtime_count_projection.unique_candidates_count is not None
        else source_run.unique_candidates_count
    )
    return WorkbenchRuntimeSourceLaneStateResponse(
        sourceKind=source_run.source_kind,
        status=display_status,
        reasonCode=reason_code,
        eventType=event_type,
        eventSeq=event_seq,
        cardsSeenCount=_safe_count(typed_safe_counts.get("cards_seen"), fallback=cards_scanned_fallback),
        cardsFilteredCount=_safe_count(typed_safe_counts.get("cards_filtered"), fallback=0),
        candidatesCount=_safe_count(typed_safe_counts.get("candidates"), fallback=unique_candidates_fallback),
        detailRecommendationsCount=_safe_count(typed_safe_counts.get("detail_recommendations"), fallback=0),
        detailState=_runtime_source_detail_state(latest_state, typed_safe_counts=typed_safe_counts),
        latestWorkflowStep=_latest_workflow_step_response(latest_state),
    )


def _runtime_source_reason_code(*values: object) -> str | None:
    for value in values:
        text = str(value).strip() if value is not None else ""
        public_code = public_runtime_source_reason_code(text)
        if public_code is not None:
            return public_code
    return None


def _runtime_source_coverage_fields(
    session: WorkbenchSession,
    latest_states: list[WorkbenchRuntimeSourceLaneLatestState],
    sources: list[WorkbenchRuntimeSourceLaneStateResponse],
) -> tuple[RuntimeSourceCoverageStatus, int | None, str | None]:
    for state in sorted(latest_states, key=lambda item: item.event_seq, reverse=True):
        coverage = state.payload.get("source_coverage_summary")
        finalization = state.payload.get("finalization_revision")
        typed_coverage = _string_keyed_object_dict(coverage)
        status = COVERAGE_STATUSES.get(str(typed_coverage.get("status") or ""))
        if status in {"complete", "degraded", "empty"}:
            typed_finalization = _string_keyed_object_dict(finalization)
            revision = _safe_int(typed_finalization.get("revision"))
            reason_value = typed_finalization.get("reason_code")
            reason = str(reason_value) if reason_value is not None else None
            return status, revision, reason

    source_statuses = {source.status for source in sources}
    if source_statuses.intersection({"running", "pending"}) or any(run.status == "queued" for run in session.source_runs):
        return "pending", None, None
    if all(source.status == "completed" for source in sources):
        if any(source.candidatesCount for source in sources):
            return "complete", None, None
        return "empty", None, None
    if source_statuses.intersection({"partial", "blocked", "failed", "cancelled"}):
        return "degraded", None, None
    return "pending", None, None


def _runtime_source_detail_state(
    latest_state: WorkbenchRuntimeSourceLaneLatestState | None,
    *,
    typed_safe_counts: Mapping[str, object] | None = None,
) -> RuntimeSourceDetailState | None:
    if latest_state is None or latest_state.source_kind != "liepin":
        return None
    if _safe_count((typed_safe_counts or {}).get("detail_recommendations"), fallback=0) > 0:
        return "detail_recommended"
    payload_value = latest_state.payload.get("detail_state")
    payload_state = DETAIL_STATES.get(payload_value) if isinstance(payload_value, str) else None
    if payload_state is not None:
        return payload_state
    if latest_state.event_type == "detail_recommended":
        return "detail_recommended"
    if latest_state.event_type == "detail_leased":
        return "leased"
    if latest_state.event_type == "detail_completed":
        return "completed"
    if latest_state.event_type == "detail_blocked":
        return "blocked"
    return None


def _latest_workflow_step_response(
    latest_state: WorkbenchRuntimeSourceLaneLatestState | None,
) -> WorkbenchRuntimeSourceWorkflowStepResponse | None:
    if latest_state is None or not latest_state.event_type.startswith("source_workflow_step_"):
        return None
    payload = latest_state.payload
    step_name = payload.get("step_name")
    if not isinstance(step_name, str) or not step_name.strip():
        return None
    typed_counts = _string_keyed_object_dict(payload.get("safe_counts"))
    status = _runtime_display_status_or_none(payload.get("status") or latest_state.status)
    return WorkbenchRuntimeSourceWorkflowStepResponse(
        eventType=latest_state.event_type,
        stepName=step_name.strip(),
        status=status,
        safeCounts={
            str(key): value
            for key, value in typed_counts.items()
            if isinstance(value, int) and not isinstance(value, bool)
        },
        safeReasonCode=_runtime_source_reason_code(payload.get("safe_reason_code")),
    )


def _runtime_source_merge_count(
    latest_states: list[WorkbenchRuntimeSourceLaneLatestState],
    key: str,
) -> int:
    for state in sorted(latest_states, key=lambda item: item.event_seq, reverse=True):
        merge_summary = state.payload.get("merge_summary")
        typed_merge_summary = _string_keyed_object_dict(merge_summary)
        if not typed_merge_summary:
            continue
        value = _safe_int(typed_merge_summary.get(key))
        if value is not None:
            return max(value, 0)
    if key == "canonical_resume_selected_count":
        for state in sorted(latest_states, key=lambda item: item.event_seq, reverse=True):
            finalization = state.payload.get("finalization_revision")
            typed_finalization = _string_keyed_object_dict(finalization)
            if not typed_finalization:
                continue
            candidate_ids = typed_finalization.get("candidate_identity_ids")
            if isinstance(candidate_ids, list):
                return len(candidate_ids)
    return 0


def _runtime_display_status(
    value: object,
    *,
    default: RuntimeSourceDisplayStatus,
) -> RuntimeSourceDisplayStatus:
    text = str(value or default)
    if text == "queued":
        text = "pending"
    return DISPLAY_STATUSES.get(text, default)


def _runtime_display_status_or_none(value: object) -> RuntimeSourceDisplayStatus | None:
    return DISPLAY_STATUSES.get(str(value or ""))


def _string_keyed_object_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _safe_count(value: object, *, fallback: int = 0) -> int:
    parsed = _safe_int(value)
    if parsed is None:
        return fallback
    return max(parsed, 0)


def _safe_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
