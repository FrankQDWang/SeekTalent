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
from seektalent_ui.agent_workbench_models import (
    AgentWorkbenchCandidateSummaryResponse,
    AgentWorkbenchDetailApprovalResponse,
    AgentWorkbenchFinalSummaryResponse,
    AgentWorkbenchReviewArtifactResponse,
    AgentWorkbenchRuntimeResponse,
    AgentWorkbenchSourceConnectionResponse,
)
from seektalent_ui.workbench_store import WorkbenchStore
from seektalent_ui.workbench_store_types import WorkbenchUser


class RuntimeEventPageLike(Protocol):
    events: Sequence[object]
    reason_code: str | None
    next_cursor: int


class RuntimeProjectionStore(Protocol):
    def get_run(self, runtime_run_id: str) -> object: ...

    def list_events(self, *, runtime_run_id: str, after_seq: int, limit: int) -> RuntimeEventPageLike: ...


@dataclass(frozen=True)
class AgentWorkbenchProjectionInput:
    conversation_reopen_state: ConversationReopenState
    messages: Sequence[TranscriptMessage] = field(default_factory=tuple)
    activity_items: Sequence[TranscriptActivityItem] = field(default_factory=tuple)
    tool_call_records: Sequence[AgentToolCallRecord] = field(default_factory=tuple)
    context_compactions: Sequence[ContextCompactionRecord] = field(default_factory=tuple)
    runtime_events: Sequence[object] = field(default_factory=tuple)
    source_connections: Sequence[AgentWorkbenchSourceConnectionResponse] = field(default_factory=tuple)
    runtime: AgentWorkbenchRuntimeResponse | None = None
    candidates: Sequence[AgentWorkbenchCandidateSummaryResponse] = field(default_factory=tuple)
    detail_approvals: Sequence[AgentWorkbenchDetailApprovalResponse] = field(default_factory=tuple)
    review_artifacts: Sequence[AgentWorkbenchReviewArtifactResponse] = field(default_factory=tuple)
    final_summary: AgentWorkbenchFinalSummaryResponse | None = None


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
    final_summary = _final_summary(runtime_store=runtime_store, summary_id=state.final_summary_id)
    candidates: Sequence[AgentWorkbenchCandidateSummaryResponse] = ()
    detail_approvals: Sequence[AgentWorkbenchDetailApprovalResponse] = ()
    if state.workbench_session_id is not None:
        candidates = _candidate_summaries(
            workbench_store.list_candidate_review_items(user=user, session_id=state.workbench_session_id) or ()
        )
        detail_approvals = _detail_approvals(
            workbench_store.list_liepin_detail_open_requests(user=user, session_id=state.workbench_session_id)
        )
    return AgentWorkbenchProjectionInput(
        conversation_reopen_state=state,
        messages=tuple(thread.messages),
        activity_items=tuple(thread.activity_items),
        tool_call_records=tuple(conversation_store.list_tool_calls(conversation_id=conversation_id)),
        context_compactions=tuple(conversation_store.list_context_compactions(conversation_id=conversation_id)),
        runtime_events=runtime_events,
        source_connections=_source_connections(workbench_store.list_source_connections(user=user)),
        runtime=runtime,
        candidates=tuple(candidates),
        detail_approvals=tuple(detail_approvals),
        review_artifacts=review_artifacts,
        final_summary=final_summary,
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
        runtime = runtime_response_from_run(runtime_store.get_run(runtime_run_id))
        runtime_events = tuple(_list_all_runtime_events(runtime_store=runtime_store, runtime_run_id=runtime_run_id))
    except LookupError:
        return None, (), ()
    return runtime, runtime_events, tuple(_review_artifacts(runtime_store=runtime_store, runtime_run_id=runtime_run_id))


def _list_all_runtime_events(
    *,
    runtime_store: RuntimeProjectionStore,
    runtime_run_id: str,
) -> Iterable[object]:
    after_seq = 0
    while True:
        page = runtime_store.list_events(runtime_run_id=runtime_run_id, after_seq=after_seq, limit=500)
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


def _candidate_summaries(items: Iterable[object]) -> tuple[AgentWorkbenchCandidateSummaryResponse, ...]:
    return tuple(
        AgentWorkbenchCandidateSummaryResponse(
            candidateId=_str_or_none(_attr(item, "review_item_id")) or "candidate",
            displayName=_str_or_none(_attr(item, "display_name")) or "Candidate",
            headline=_headline(item),
            matchSummary=_str_or_none(_attr(item, "summary")) or _str_or_none(_attr(item, "why_selected")),
            sourceKind=_candidate_source_kind(item),
            status=_str_or_none(_attr(item, "status")) or "new",
        )
        for item in items
    )


def _detail_approvals(items: Iterable[object]) -> tuple[AgentWorkbenchDetailApprovalResponse, ...]:
    return tuple(
        AgentWorkbenchDetailApprovalResponse(
            approvalId=_str_or_none(_attr(item, "request_id")) or "detail-request",
            candidateId=_str_or_none(_attr(item, "review_item_id")) or "candidate",
            status=_str_or_none(_attr(item, "status")) or "pending",
            reason=(
                _str_or_none(_attr(item, "decision_note"))
                or _str_or_none(_attr(item, "blocked_reason"))
                or _str_or_none(_attr(item, "detail_open_mode"))
                or "detail_open_review"
            ),
        )
        for item in items
    )


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


def _headline(item: object) -> str | None:
    parts = [
        _str_or_none(_attr(item, "title")),
        _str_or_none(_attr(item, "company")),
        _str_or_none(_attr(item, "location")),
    ]
    return " / ".join(part for part in parts if part) or None


def _candidate_source_kind(item: object) -> Literal["cts", "liepin", "all"]:
    badges = _attr(item, "source_badges")
    if isinstance(badges, list):
        if "liepin" in badges:
            return "liepin"
        if "cts" in badges:
            return "cts"
    return "all"


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


def _attr(value: object, name: str) -> object:
    return getattr(value, name, None)


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
