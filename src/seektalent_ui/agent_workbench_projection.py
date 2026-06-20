from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, cast

from seektalent_conversation_agent.models import (
    AgentToolCallRecord,
    ContextCompactionRecord,
    ConversationReopenState,
    ConversationThreadView,
    TranscriptActivityItem,
    TranscriptMessage,
)
from seektalent_conversation_agent.service import ConversationAgentService
from seektalent_conversation_agent.store import ConversationStore
from seektalent_runtime_control.requirements import RequirementDraft
from seektalent_ui.agent_workbench_models import (
    AgentWorkbenchCandidateDetailResponse,
    AgentWorkbenchCandidateDetailSectionResponse,
    AgentWorkbenchCandidateSummaryResponse,
    AgentWorkbenchDetailApprovalResponse,
    AgentWorkbenchDetailApprovalStatus,
    AgentWorkbenchFinalSummaryResponse,
    AgentWorkbenchReviewArtifactResponse,
    AgentWorkbenchRuntimeResponse,
    AgentWorkbenchSourceConnectionResponse,
)
from seektalent_ui.workbench_store import WorkbenchStore
from seektalent_ui.workbench_store_types import WorkbenchUser


MAX_WORKBENCH_RUNTIME_EVENT_PAGE = 100


class RuntimeEventPageLike(Protocol):
    events: Sequence[object]
    reason_code: str | None
    next_cursor: int


class RuntimeProjectionStore(Protocol):
    def get_run(self, runtime_run_id: str) -> object: ...

    def list_events(self, *, runtime_run_id: str, after_seq: int, limit: int) -> RuntimeEventPageLike: ...

    def get_requirement_draft(self, draft_revision_id: str) -> RequirementDraft | None: ...


@dataclass(frozen=True)
class AgentWorkbenchWorkflowStartIntentProjection:
    workflow_start_intent_id: str
    status: str
    runtime_run_id: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class AgentWorkbenchProjectionInput:
    conversation_reopen_state: ConversationReopenState
    messages: Sequence[TranscriptMessage] = field(default_factory=tuple)
    activity_items: Sequence[TranscriptActivityItem] = field(default_factory=tuple)
    tool_call_records: Sequence[AgentToolCallRecord] = field(default_factory=tuple)
    context_compactions: Sequence[ContextCompactionRecord] = field(default_factory=tuple)
    runtime_events: Sequence[object] = field(default_factory=tuple)
    requirement_draft: RequirementDraft | None = None
    requirement_draft_missing: bool = False
    source_connections: Sequence[AgentWorkbenchSourceConnectionResponse] = field(default_factory=tuple)
    runtime: AgentWorkbenchRuntimeResponse | None = None
    candidates: Sequence[AgentWorkbenchCandidateSummaryResponse] = field(default_factory=tuple)
    detail_approvals: Sequence[AgentWorkbenchDetailApprovalResponse] = field(default_factory=tuple)
    review_artifacts: Sequence[AgentWorkbenchReviewArtifactResponse] = field(default_factory=tuple)
    final_summary: AgentWorkbenchFinalSummaryResponse | None = None
    workflow_start_intent: AgentWorkbenchWorkflowStartIntentProjection | None = None


def projection_input_from_thread_view(thread: ConversationThreadView) -> AgentWorkbenchProjectionInput:
    """Unit-test adapter for the existing reopen snapshot.

    Production routes use `build_agent_workbench_projection_input` so the React
    workbench launches against the full BFF boundary, not the old conversation
    snapshot alone.
    """
    return AgentWorkbenchProjectionInput(
        conversation_reopen_state=thread.conversation_reopen_state,
        messages=tuple(thread.messages),
        activity_items=tuple(thread.activity_items),
    )


def runtime_response_from_run(run: object) -> AgentWorkbenchRuntimeResponse:
    return AgentWorkbenchRuntimeResponse(
        runtimeRunId=_str_or_none(_attr(run, "runtime_run_id")) or "runtime",
        status=_str_or_none(_attr(run, "status")) or "unknown",
        currentStage=_str_or_none(_attr(run, "current_stage")) or "unknown",
        currentRound=_int_or_none(_attr(run, "current_round")),
        latestEventSeq=_int_or_none(_attr(run, "latest_event_seq")) or 0,
    )


def build_agent_workbench_projection_input(
    *,
    service: ConversationAgentService,
    conversation_store: ConversationStore,
    runtime_store: RuntimeProjectionStore,
    workbench_store: WorkbenchStore,
    conversation_id: str,
    user: WorkbenchUser,
) -> AgentWorkbenchProjectionInput:
    """Aggregate React BFF facts through named service/store boundaries."""
    thread = service.reopen_conversation(
        conversation_id=conversation_id,
        owner_user_id=user.user_id,
        workspace_id=user.workspace_id,
    )
    state = thread.conversation_reopen_state
    runtime = None
    runtime_events: tuple[object, ...] = ()
    review_artifacts: tuple[AgentWorkbenchReviewArtifactResponse, ...] = ()
    if state.runtime_run_id is not None:
        runtime, runtime_events, review_artifacts = _runtime_inputs(
            runtime_store=runtime_store,
            runtime_run_id=state.runtime_run_id,
        )
    requirement_draft = None
    requirement_draft_missing = False
    if state.latest_draft_revision_id is not None:
        requirement_draft = runtime_store.get_requirement_draft(state.latest_draft_revision_id)
        requirement_draft_missing = requirement_draft is None
    final_summary = _final_summary(runtime_store=runtime_store, summary_id=state.final_summary_id)
    candidates: Sequence[AgentWorkbenchCandidateSummaryResponse] = ()
    detail_approvals: Sequence[AgentWorkbenchDetailApprovalResponse] = ()
    if state.workbench_session_id is not None:
        runtime_final_top = workbench_store.list_runtime_final_top_review_items(
            user=user,
            session_id=state.workbench_session_id,
        )
        if runtime_final_top is not None:
            _, runtime_items = runtime_final_top
            candidates = _candidate_summaries(runtime_items, preserve_order=True)
        else:
            candidates = _candidate_summaries(
                workbench_store.list_candidate_review_items(user=user, session_id=state.workbench_session_id, limit=10)
                or ()
            )
        detail_approvals = _detail_approvals(
            workbench_store.list_liepin_detail_open_requests(user=user, session_id=state.workbench_session_id)
        )
    workflow_start_intent = _workflow_start_intent_projection(
        service=service,
        workspace_id=user.workspace_id,
        conversation_id=conversation_id,
    )
    return AgentWorkbenchProjectionInput(
        conversation_reopen_state=state,
        messages=tuple(thread.messages),
        activity_items=tuple(thread.activity_items),
        tool_call_records=tuple(conversation_store.list_tool_calls(conversation_id=conversation_id)),
        context_compactions=tuple(conversation_store.list_context_compactions(conversation_id=conversation_id)),
        runtime_events=runtime_events,
        requirement_draft=requirement_draft,
        requirement_draft_missing=requirement_draft_missing,
        source_connections=_source_connections(workbench_store.list_source_connections(user=user)),
        runtime=runtime,
        candidates=tuple(candidates),
        detail_approvals=tuple(detail_approvals),
        review_artifacts=review_artifacts,
        final_summary=final_summary,
        workflow_start_intent=workflow_start_intent,
    )


def _workflow_start_intent_projection(
    *,
    service: ConversationAgentService,
    workspace_id: str,
    conversation_id: str,
) -> AgentWorkbenchWorkflowStartIntentProjection | None:
    intent = service.workflow_start_intent_store.get_latest_for_conversation(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
    )
    if intent is None:
        return None
    return AgentWorkbenchWorkflowStartIntentProjection(
        workflow_start_intent_id=intent.workflow_start_intent_id,
        status=intent.status,
        runtime_run_id=intent.runtime_run_id,
        reason_code=intent.reason_code,
    )


def _runtime_inputs(
    *,
    runtime_store: RuntimeProjectionStore,
    runtime_run_id: str,
) -> tuple[
    AgentWorkbenchRuntimeResponse | None,
    tuple[object, ...],
    tuple[AgentWorkbenchReviewArtifactResponse, ...],
]:
    try:
        run = runtime_store.get_run(runtime_run_id)
        runtime = runtime_response_from_run(run)
        runtime_events = tuple(
            _list_recent_runtime_events(runtime_store=runtime_store, runtime_run_id=runtime_run_id, run=run)
        )
    except LookupError:
        return None, (), ()
    return runtime, runtime_events, tuple(_review_artifacts(runtime_store=runtime_store, runtime_run_id=runtime_run_id))


def _list_recent_runtime_events(
    *,
    runtime_store: RuntimeProjectionStore,
    runtime_run_id: str,
    run: object,
) -> Iterable[object]:
    after_seq = max(0, (_int_or_none(_attr(run, "latest_event_seq")) or 0) - MAX_WORKBENCH_RUNTIME_EVENT_PAGE)
    while True:
        page = runtime_store.list_events(
            runtime_run_id=runtime_run_id,
            after_seq=after_seq,
            limit=MAX_WORKBENCH_RUNTIME_EVENT_PAGE,
        )
        yield from page.events
        if page.reason_code is not None or not page.events or page.next_cursor <= after_seq:
            return
        after_seq = page.next_cursor


def _source_connections(items: Iterable[object]) -> tuple[AgentWorkbenchSourceConnectionResponse, ...]:
    return tuple(
        AgentWorkbenchSourceConnectionResponse(
            sourceKind=_source_kind(_attr(item, "source_kind")),
            status=_source_status(_attr(item, "status")),
            displayName=_source_display_name(_attr(item, "source_kind")),
            lastCheckedAt=_str_or_none(_attr(item, "updated_at")),
        )
        for item in items
    )


def _candidate_summaries(
    items: Iterable[object],
    *,
    preserve_order: bool = False,
) -> tuple[AgentWorkbenchCandidateSummaryResponse, ...]:
    if preserve_order:
        ranked = list(items)[:10]
    else:
        ranked = sorted(
            items,
            key=lambda item: (
                -_candidate_score_for_sort(item),
                _str_or_none(_attr(item, "created_at")) or "",
                _str_or_none(_attr(item, "review_item_id")) or "",
            ),
        )[:10]
    return tuple(
        AgentWorkbenchCandidateSummaryResponse(
            candidateId=_str_or_none(_attr(item, "review_item_id")) or f"candidate_{index}",
            rank=index,
            displayName=_str_or_none(_attr(item, "display_name")) or f"Candidate {index}",
            headline=_str_or_none(_attr(item, "title")),
            company=_str_or_none(_attr(item, "company")),
            location=_str_or_none(_attr(item, "location")),
            education=_str_or_none(_attr(item, "education")),
            experienceYears=_int_or_none(_attr(item, "experience_years")),
            sourceKinds=_candidate_source_kinds(item),
            matchScore=_bounded_score(_attr(item, "aggregate_score")),
            matchSummary=_str_or_none(_attr(item, "summary")) or _str_or_none(_attr(item, "why_selected")),
            status=_str_or_none(_attr(item, "status")) or "new",
            detailAvailability=_candidate_detail_availability(item),
            accessState=_candidate_access_state(item),
            evidenceLevel=_candidate_evidence_level(item),
        )
        for index, item in enumerate(ranked, start=1)
    )


def candidate_detail_response_from_review_item(item: object) -> AgentWorkbenchCandidateDetailResponse:
    access_state = _candidate_access_state(item)
    evidence_level = _candidate_evidence_level(item)
    if access_state in {"denied", "approval_required"}:
        return AgentWorkbenchCandidateDetailResponse(
            candidateId=_str_or_none(_attr(item, "review_item_id")) or "candidate",
            displayName=_str_or_none(_attr(item, "display_name")) or "Candidate",
            headline=_str_or_none(_attr(item, "title")),
            sourceKinds=_candidate_source_kinds(item),
            matchScore=_bounded_score(_attr(item, "aggregate_score")),
            sections=[],
            evidence=[],
            detailAvailability="unavailable" if access_state == "denied" else "approval_required",
            accessState=access_state,
            evidenceLevel=evidence_level,
            reasonCode=_candidate_detail_reason_code(item),
        )

    sections = [
        AgentWorkbenchCandidateDetailSectionResponse(
            title="匹配亮点",
            items=_string_list(_attr(item, "strengths")) or _string_list(_attr(item, "matched_must_haves")),
        ),
        AgentWorkbenchCandidateDetailSectionResponse(
            title="加分项",
            items=_string_list(_attr(item, "matched_preferences")),
        ),
        AgentWorkbenchCandidateDetailSectionResponse(
            title="风险点",
            items=_string_list(_attr(item, "missing_risks")) or _string_list(_attr(item, "weaknesses")),
        ),
    ]
    return AgentWorkbenchCandidateDetailResponse(
        candidateId=_str_or_none(_attr(item, "review_item_id")) or "candidate",
        displayName=_str_or_none(_attr(item, "display_name")) or "Candidate",
        headline=_str_or_none(_attr(item, "title")),
        sourceKinds=_candidate_source_kinds(item),
        matchScore=_bounded_score(_attr(item, "aggregate_score")),
        sections=[section for section in sections if section.items],
        evidence=_source_badges(item),
        detailAvailability=_candidate_detail_availability(item),
        accessState=access_state,
        evidenceLevel=evidence_level,
        reasonCode=_candidate_detail_reason_code(item),
    )


def _detail_approvals(items: Iterable[object]) -> tuple[AgentWorkbenchDetailApprovalResponse, ...]:
    return tuple(
        AgentWorkbenchDetailApprovalResponse(
            approvalId=_str_or_none(_attr(item, "request_id")) or "detail-request",
            candidateId=_str_or_none(_attr(item, "review_item_id")) or "candidate",
            status=_detail_approval_status(_attr(item, "status")),
            reason=(
                _str_or_none(_attr(item, "decision_note"))
                or _str_or_none(_attr(item, "blocked_reason"))
                or _str_or_none(_attr(item, "detail_open_mode"))
                or "detail_open_review"
            ),
        )
        for item in items
    )


def _detail_approval_status(value: object) -> AgentWorkbenchDetailApprovalStatus:
    if value == "pending":
        return "pending"
    if value == "approved":
        return "accepted"
    if value in {"completed", "applied"}:
        return "applied"
    if value == "bypassed":
        return "accepted"
    if value in {"denied", "rejected", "blocked", "failed", "expired"}:
        return "rejected"
    return "pending"


def _review_artifacts(
    *,
    runtime_store: object,
    runtime_run_id: str,
) -> Iterable[AgentWorkbenchReviewArtifactResponse]:
    list_refs = getattr(runtime_store, "list_artifact_refs", None)
    if not callable(list_refs):
        return ()
    refs = list_refs(runtime_run_id=runtime_run_id)
    return tuple(_review_artifact(ref) for ref in refs)


def _review_artifact(ref: object) -> AgentWorkbenchReviewArtifactResponse:
    artifact_id = (
        _mapping_str(ref, "artifact_id")
        or _mapping_str(ref, "artifact_ref_id")
        or _str_or_none(_attr(ref, "artifact_id"))
        or _str_or_none(_attr(ref, "artifact_ref_id"))
        or "artifact"
    )
    artifact_kind = _artifact_kind(
        _mapping_str(ref, "artifact_kind") or _str_or_none(_attr(ref, "artifact_kind"))
    )
    title = _mapping_str(ref, "title") or _str_or_none(_attr(ref, "title")) or artifact_kind.replace("_", " ").title()
    summary = (
        _mapping_str(ref, "safe_summary")
        or _mapping_str(ref, "safeSummary")
        or _str_or_none(_attr(ref, "safe_summary"))
        or title
    )
    return AgentWorkbenchReviewArtifactResponse(
        artifactId=artifact_id,
        title=title,
        artifactKind=artifact_kind,
        safeSummary=summary,
    )


def _final_summary(
    *,
    runtime_store: object,
    summary_id: str | None,
) -> AgentWorkbenchFinalSummaryResponse | None:
    if summary_id is None:
        return None
    get_summary = getattr(runtime_store, "get_final_summary", None)
    if not callable(get_summary):
        return None
    summary = get_summary(summary_id=summary_id)
    if summary is None:
        return None
    text = _mapping_str(summary, "text") or _mapping_str(summary, "summary") or _str_or_none(_attr(summary, "summary"))
    return AgentWorkbenchFinalSummaryResponse(
        summaryId=_mapping_str(summary, "summary_id") or _str_or_none(_attr(summary, "summary_id")) or summary_id,
        text=text or "",
    )


def _candidate_score_for_sort(item: object) -> int:
    score = _bounded_score(_attr(item, "aggregate_score"))
    return score if score is not None else -1


def _candidate_source_kinds(item: object) -> list[Literal["cts", "liepin"]]:
    kinds: list[Literal["cts", "liepin"]] = []
    evidence = _attr(item, "evidence")
    if isinstance(evidence, Iterable) and not isinstance(evidence, str | bytes | bytearray):
        for evidence_item in evidence:
            _append_source_kind(kinds, _str_or_none(_attr(evidence_item, "source_kind")))
    badges = _attr(item, "source_badges")
    if isinstance(badges, Iterable) and not isinstance(badges, str | bytes | bytearray):
        for badge in badges:
            text = _str_or_none(badge)
            if text is None:
                continue
            lowered = text.casefold()
            if "cts" in lowered:
                _append_source_kind(kinds, "cts")
            if "liepin" in lowered:
                _append_source_kind(kinds, "liepin")
    return kinds


def _append_source_kind(kinds: list[Literal["cts", "liepin"]], value: str | None) -> None:
    if value == "cts" and "cts" not in kinds:
        kinds.append("cts")
    if value == "liepin" and "liepin" not in kinds:
        kinds.append("liepin")


def _candidate_evidence_level(item: object) -> Literal["summary", "detail", "final", "unknown"]:
    raw = _str_or_none(_attr(item, "evidence_level"))
    if raw == "final":
        return "final"
    if raw == "detail":
        return "detail"
    if raw in {"card", "summary"}:
        return "summary"
    return "unknown"


def _candidate_access_state(item: object) -> Literal["allowed", "redacted", "approval_required", "denied"]:
    explicit = _str_or_none(_attr(item, "access_state"))
    if explicit in {"allowed", "redacted", "approval_required", "denied"}:
        return cast(Literal["allowed", "redacted", "approval_required", "denied"], explicit)
    evidence_level = _candidate_evidence_level(item)
    if evidence_level in {"detail", "final"}:
        return "allowed"
    if "liepin" in _candidate_source_kinds(item):
        return "approval_required"
    if evidence_level == "summary":
        return "redacted"
    return "denied"


def _candidate_detail_availability(
    item: object,
) -> Literal["available", "redacted", "approval_required", "unavailable"]:
    access_state = _candidate_access_state(item)
    if access_state == "allowed":
        return "available"
    if access_state == "redacted":
        return "redacted"
    if access_state == "approval_required":
        return "approval_required"
    return "unavailable"


def _candidate_detail_reason_code(item: object) -> str | None:
    explicit = _str_or_none(_attr(item, "reason_code"))
    if explicit is not None:
        return explicit
    access_state = _candidate_access_state(item)
    if access_state == "denied":
        return "permission_denied"
    if access_state == "approval_required":
        return "candidate_detail_requires_approval"
    if access_state == "redacted":
        return "candidate_detail_redacted"
    return None


def _source_badges(item: object) -> list[str]:
    badges = _string_list(_attr(item, "source_badges"))
    if badges:
        return badges
    return list(_candidate_source_kinds(item))


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Iterable) or isinstance(value, str | bytes | bytearray):
        return []
    result: list[str] = []
    for item in value:
        text = _str_or_none(item)
        if text is not None and text not in result:
            result.append(text)
    return result


def _source_kind(value: object) -> Literal["cts", "liepin"]:
    return "liepin" if value == "liepin" else "cts"


def _source_status(value: object) -> Literal["connected", "disconnected", "expired", "unknown"]:
    if value == "connected":
        return "connected"
    if value == "expired":
        return "expired"
    if value in {"login_required", "login_in_progress", "verification_required", "blocked", "disconnected"}:
        return "disconnected"
    return "unknown"


def _source_display_name(value: object) -> str:
    if value == "liepin":
        return "Liepin"
    if value == "cts":
        return "CTS"
    return "Source"


def _artifact_kind(value: str | None) -> Literal["source_evidence", "approval", "final_output", "stream_recovery"]:
    if value in {"source_evidence", "approval", "final_output", "stream_recovery"}:
        return cast(Literal["source_evidence", "approval", "final_output", "stream_recovery"], value)
    return "source_evidence"


def _mapping_str(value: object, key: str) -> str | None:
    if not isinstance(value, Mapping):
        return None
    mapping = cast(Mapping[object, object], value)
    item = mapping.get(key)
    return item if isinstance(item, str) and item else None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _bounded_score(value: object) -> int | None:
    score = _int_or_none(value)
    if score is None or score < 0 or score > 100:
        return None
    return score


def _attr(value: object, name: str) -> object:
    return getattr(value, name, None)


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
