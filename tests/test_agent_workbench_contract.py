from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args, get_origin

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from seektalent.config import AppSettings
from seektalent.progress import ProgressEvent
from seektalent_conversation_agent.models import (
    AgentToolCallRecord,
    ContextCompactionRecord,
    ConversationReopenState,
    ConversationRuntimeRunLink,
    ConversationThreadView,
    TranscriptActivityItem,
    TranscriptMessage,
)
from seektalent_runtime_control.models import RuntimeControlEvent, RuntimeControlEventPage, RuntimeRunRecord
from seektalent_ui.agent_routes import LocalAgentRateLimiter
from seektalent_ui.agent_workbench_models import (
    AgentWorkbenchCandidateSummaryResponse,
    AgentWorkbenchDetailApprovalResponse,
    AgentWorkbenchMessageStreamPayloadResponse,
    AgentWorkbenchItemStreamPayloadResponse,
    AgentWorkbenchTranscriptPayloadResponse,
)
from seektalent_ui.agent_workbench_projection import (
    AgentWorkbenchProjectionInput,
    AgentWorkbenchWorkflowStartIntentProjection,
    build_agent_workbench_projection_input,
)
from seektalent_ui.agent_workbench_response import project_agent_workbench_view
from seektalent_ui.agent_workbench_stream import build_stream_envelope, replay_stream_envelopes
from seektalent_ui.agent_workbench_stream_projection import project_agent_workbench_stream_events
from seektalent_ui.agent_workbench_stream_store import AgentWorkbenchStreamStore
from seektalent_ui.server import create_app
from seektalent_ui.workbench_store_types import (
    WorkbenchCandidateReviewItem,
    WorkbenchDetailOpenRequest,
    WorkbenchSourceConnection,
    WorkbenchUser,
)
from tests.conversation_agent_test_support import sample_requirement_sheet
from tests.settings_factory import make_settings


class DeterministicRouteRuntime:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def extract_requirements(
        self,
        *,
        job_title: str,
        jd: str,
        notes: str,
        progress_callback=None,
        requirement_cache_scope: str | None = None,
    ) -> object:
        if callable(progress_callback):
            progress_callback(
                ProgressEvent(
                    type="requirements_completed",
                    message="岗位需求解析完成。",
                    payload={"stage": "requirements"},
                )
            )
        return sample_requirement_sheet(job_title=job_title)


@dataclass
class StreamingRequest:
    app: object
    query_params: dict[str, str] | None = None

    async def is_disconnected(self) -> bool:
        return False


def test_agent_workbench_view_projects_stable_frontend_contract() -> None:
    thread = _thread_view()
    projection_input = AgentWorkbenchProjectionInput(
        conversation_reopen_state=thread.conversation_reopen_state,
        messages=thread.messages,
        activity_items=thread.activity_items,
        tool_call_records=[
            AgentToolCallRecord(
                tool_call_id="tool_1",
                conversation_id="agent_conv_1",
                activity_id="activity_1",
                runtime_run_id="runtime_1",
                tool_name="runtime_search",
                status="completed",
                args={"source": "liepin", "rawPayload": "must not leak"},
                result={"summary": "Read 5 safe profile summaries.", "providerResponse": "must not leak"},
                reason_code=None,
                started_at=_now(),
                completed_at=_now(),
            )
        ],
        context_compactions=[
            ContextCompactionRecord(
                compaction_id="compact_1",
                conversation_id="agent_conv_1",
                status="completed",
                trigger_reason_code="token_budget",
                summary_id="summary_1",
                source_message_seq_start=1,
                source_message_seq_end=2,
                source_activity_seq_start=1,
                source_activity_seq_end=1,
                created_at=_now(),
                completed_at=_now(),
            )
        ],
        runtime_events=[],
        source_connections=[],
        candidates=[],
        detail_approvals=[],
        review_artifacts=[],
    )

    response = project_agent_workbench_view(projection_input)
    serialized = response.model_dump_json()

    assert response.schemaVersion == "agent.workbench.view.v2"
    assert response.conversation.conversationId == "agent_conv_1"
    assert response.conversation.runtimeRunId == "runtime_1"
    assert [link.runtimeRunId for link in response.conversation.linkedRuntimeRuns] == [
        "runtime_1",
        "runtime_2",
    ]
    assert response.conversation.linkedRuntimeRuns[0].isActive is True
    assert response.conversation.linkedRuntimeRuns[1].runKind == "rerun"
    assert response.streamCursor.latestMessageSeq == 2
    assert response.streamCursor.latestActivitySeq == 1
    assert response.streamCursor.latestRuntimeEventSeq == 7
    assert response.streamCursor.latestStreamSeq == 0
    assert response.streamCursor.snapshotSeq == 0
    assert response.streamCursor.viewRevision == 0
    assert [message.messageId for message in response.messages] == ["msg_1", "msg_2"]
    assert response.pendingActions.primary == "confirm_requirements"
    assert response.strategyGraph.nodes[0].nodeId == "requirements"
    assert response.strategyGraph.nodes[0].kind == "requirements"
    assert response.strategyGraph.nodes[-1].nodeId == "activity_1"
    assert response.strategyGraph.nodes[-1].status == "running"
    assert [event.kind for event in response.transcriptGroups[0].events] == [
        "message.completed",
        "message.completed",
        "tool.completed",
        "activity.upserted",
    ]
    assert response.transcriptGroups[1].events[0].kind == "context.compacted"
    last_payload = response.transcriptGroups[0].events[-1].payload
    assert last_payload.activityId == "activity_1"
    assert last_payload.activitySeq == 1
    assert last_payload.activityType == "runtime_event"
    assert last_payload.sourceRuntimeRunId == "runtime_1"
    assert response.thinkingProcess.activeRoundNo == 1
    assert [card.title for card in response.thinkingProcess.rounds[0].cards] == [
        "关键词",
        "observation",
        "反思和下一轮变更",
    ]
    assert response.thinkingProcess.rounds[0].cards[0].terms == ["AI agent", "LLM"]
    assert "覆盖面较好" in response.thinkingProcess.rounds[0].cards[1].text
    assert "LangChain" in response.thinkingProcess.rounds[0].cards[2].text
    assert "rawPayload" not in serialized
    assert "providerResponse" not in serialized


@pytest.mark.parametrize(
    ("intent_status", "intent_runtime_run_id", "reason_code", "expected_state"),
    [
        (None, None, None, "not_started"),
        ("pending", None, None, "queued"),
        ("started", None, None, "starting"),
        ("started", "runtime_started", None, "running"),
        ("failed", None, "source_policy_disallowed", "failed"),
        ("cancelled", None, "workflow_cancelled", "failed"),
    ],
)
def test_agent_workbench_projects_workflow_start_state(
    intent_status: str | None,
    intent_runtime_run_id: str | None,
    reason_code: str | None,
    expected_state: str,
) -> None:
    thread = _thread_view()
    state = thread.conversation_reopen_state.model_copy(
        update={
            "runtime_run_id": None,
            "workflow_start_intent_id": None,
            "linked_runtime_runs": [],
        }
    )
    intent = None
    if intent_status is not None:
        intent = AgentWorkbenchWorkflowStartIntentProjection(
            workflow_start_intent_id="workflow_intent_1",
            status=intent_status,
            runtime_run_id=intent_runtime_run_id,
            reason_code=reason_code,
        )

    response = project_agent_workbench_view(
        AgentWorkbenchProjectionInput(
            conversation_reopen_state=state,
            messages=thread.messages,
            activity_items=thread.activity_items,
            workflow_start_intent=intent,
        )
    )

    assert response.conversation.workflowStartState == expected_state
    assert response.conversation.workflowStartReasonCode == reason_code
    assert response.conversation.workflowStartIntentId == ("workflow_intent_1" if intent is not None else None)


def test_agent_workbench_projects_canonical_requirement_draft_sections() -> None:
    from seektalent_runtime_control.requirements import draft_from_requirement_sheet

    thread = _thread_view()
    draft = draft_from_requirement_sheet(
        conversation_id="agent_conv_1",
        draft_revision_id="reqdraft_1",
        base_revision_id="reqdraft_base",
        requirement_sheet=sample_requirement_sheet(job_title="Python 平台负责人"),
        source="extracted",
        created_at=_now(),
    )
    draft.sections[0].items[0].selected = False
    draft.sections[0].items[0].allowed_actions = ["set_selected", "edit_text"]

    response = project_agent_workbench_view(
        AgentWorkbenchProjectionInput(
            conversation_reopen_state=thread.conversation_reopen_state,
            messages=thread.messages,
            activity_items=thread.activity_items,
            requirement_draft=draft,
        )
    )

    requirement = response.requirementDraft
    assert requirement is not None
    assert requirement.draftRevisionId == "reqdraft_1"
    assert requirement.parentDraftRevisionId == "reqdraft_base"
    assert requirement.canConfirm is True
    assert [section.sectionId for section in requirement.sections] == [
        "must_have_capabilities",
        "preferred_capabilities",
        "hard_constraints",
        "exclusion_signals",
        "initial_query_term_pool",
    ]
    first_item = requirement.sections[0].items[0]
    assert first_item.itemId == draft.sections[0].items[0].item_id
    assert first_item.selected is False
    assert first_item.text == "Python API"
    assert first_item.allowedActions == ["set_selected", "edit_text"]
    assert requirement.otherInputPrompt == "其他"


def test_agent_workbench_projection_loads_latest_requirement_draft_from_runtime_store() -> None:
    from seektalent_runtime_control.requirements import draft_from_requirement_sheet

    thread = _thread_view(latest_draft_revision_id="reqdraft_runtime")
    draft = draft_from_requirement_sheet(
        conversation_id="agent_conv_1",
        draft_revision_id="reqdraft_runtime",
        base_revision_id="reqdraft_base",
        requirement_sheet=sample_requirement_sheet(job_title="增长负责人"),
        source="extracted",
        created_at=_now(),
    )
    runtime_store = _runtime_projection_store_with_requirement_draft(draft)

    projection = build_agent_workbench_projection_input(
        service=_conversation_agent_service_for_thread(thread),
        conversation_store=_conversation_store_for_thread(thread),
        runtime_store=runtime_store,
        workbench_store=_empty_workbench_store(),
        conversation_id="agent_conv_1",
        user=_workbench_user(),
    )

    assert projection.requirement_draft is not None
    assert projection.requirement_draft.draft_revision_id == "reqdraft_runtime"
    assert runtime_store.requested_requirement_draft_ids == ["reqdraft_runtime"]


def test_agent_workbench_marks_requirement_projection_unavailable_when_latest_draft_is_missing() -> None:
    thread = _thread_view(latest_draft_revision_id="reqdraft_missing")
    runtime_store = _FakeRuntimeStore()

    projection = build_agent_workbench_projection_input(
        service=_conversation_agent_service_for_thread(thread),
        conversation_store=_conversation_store_for_thread(thread),
        runtime_store=runtime_store,
        workbench_store=_empty_workbench_store(),
        conversation_id="agent_conv_1",
        user=_workbench_user(),
    )
    response = project_agent_workbench_view(projection)

    assert projection.requirement_draft is None
    assert projection.requirement_draft_missing is True
    assert runtime_store.requested_requirement_draft_ids == ["reqdraft_missing"]
    assert response.requirementDraft is None
    assert response.reasonCode == "runtime_projection_unavailable"


def test_requirement_review_transcript_keeps_safe_historical_snapshot(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]

    submitted = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={
            "messageType": "submitJd",
            "jobTitle": "Python 平台负责人",
            "text": "需要 Python API、平台工程和检索排序。",
            "notes": "优先 toB SaaS",
            "sourceKinds": ["cts"],
            "idempotencyKey": "submit-jd-safe-snapshot-1",
        },
    )
    assert submitted.status_code == 200, submitted.text
    draft_id = submitted.json()["requirementDraftRevisionId"]

    view = client.get(f"/api/agent/workbench/conversations/{conversation_id}")
    assert view.status_code == 200, view.text
    payload = view.json()
    [requirement_message] = [
        message for message in payload["messages"] if message["messageType"] == "requirement_review"
    ]
    message_payload = requirement_message["payload"]
    snapshot = message_payload["requirementDraftSnapshot"]
    serialized_snapshot = json.dumps(snapshot, ensure_ascii=False)

    assert message_payload["kind"] == "requirement_review"
    assert message_payload["requirementDraftId"] == draft_id
    assert snapshot["draftRevisionId"] == draft_id
    assert snapshot["sections"][0]["items"][0]["text"] == "Python API"
    assert payload["requirementDraft"]["draftRevisionId"] == draft_id
    assert "value" not in serialized_snapshot
    assert "source_span_refs" not in serialized_snapshot
    assert "amendment" not in serialized_snapshot

    store_path = client.app.state.agent_conversation_store.path
    with sqlite3.connect(store_path) as conn:
        row = conn.execute(
            """
            SELECT transcript_message_id, draft_revision_id, snapshot_json
            FROM wts_requirement_transcript_snapshots
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()

    assert row is not None
    assert row[0] == requirement_message["messageId"]
    assert row[1] == draft_id
    assert json.loads(row[2])["draftRevisionId"] == draft_id


def test_workbench_view_enforces_product_payload_budgets() -> None:
    thread = _thread_view()
    thread = thread.model_copy(
        update={
            "messages": [
                thread.messages[index % len(thread.messages)].model_copy(
                    update={
                        "message_id": f"msg_budget_{index}",
                        "message_seq": index + 1,
                        "text": f"message {index}",
                    }
                )
                for index in range(140)
            ],
            "activity_items": [
                thread.activity_items[0].model_copy(
                    update={
                        "activity_id": f"activity_budget_{index}",
                        "activity_seq": index + 1,
                        "activity_key": f"runtime_1:round:{index}",
                        "title": f"第 {index} 轮检索",
                        "source_event_seq_start": index + 1,
                        "source_event_seq_latest": index + 1,
                        "source_event_id_latest": f"event_budget_{index}",
                        "payload": {
                            **thread.activity_items[0].payload,
                            "round_no": index + 1,
                            "keyword_query": f"query {index}",
                        },
                    }
                )
                for index in range(90)
            ],
        }
    )
    projection_input = AgentWorkbenchProjectionInput(
        conversation_reopen_state=thread.conversation_reopen_state.model_copy(
            update={
                "latest_message_seq": 140,
                "latest_activity_seq": 90,
                "latest_rendered_runtime_event_seq": 90,
            }
        ),
        messages=thread.messages,
        activity_items=thread.activity_items,
        tool_call_records=[],
        context_compactions=[],
        runtime_events=[],
        source_connections=[],
        candidates=[
            AgentWorkbenchCandidateSummaryResponse(
                candidateId=f"candidate_{index}",
                displayName=f"Candidate {index}",
                headline="Backend Engineer",
                matchSummary="safe summary",
                sourceKind="cts",
                status="new",
            )
            for index in range(140)
        ],
        detail_approvals=[],
        review_artifacts=[],
    )

    response = project_agent_workbench_view(projection_input)

    assert len(response.messages) <= 100
    assert len(response.activities) <= 100
    assert sum(len(group.events) for group in response.transcriptGroups) <= 200
    assert len(response.strategyGraph.nodes) <= 80
    assert len(response.strategyGraph.edges) <= 120
    assert len(response.thinkingProcess.rounds) <= 50
    assert len(response.candidates) <= 10
    assert len(response.model_dump_json()) <= 750_000


def test_detail_approval_status_schema_uses_public_design_vocabulary() -> None:
    status_schema = AgentWorkbenchDetailApprovalResponse.model_json_schema()["properties"]["status"]

    assert status_schema["enum"] == ["pending", "accepted", "rejected", "applied"]
    assert AgentWorkbenchDetailApprovalResponse(
        approvalId="approval_1",
        candidateId="candidate_1",
        status="applied",
        reason="Detail snapshot already applied.",
    ).status == "applied"


@pytest.mark.parametrize(
    ("source_status", "public_status"),
    [
        ("pending", "pending"),
        ("approved", "accepted"),
        ("denied", "rejected"),
        ("rejected", "rejected"),
        ("completed", "applied"),
        ("applied", "applied"),
        ("bypassed", "accepted"),
        ("blocked", "rejected"),
        ("failed", "rejected"),
        ("expired", "rejected"),
    ],
)
def test_detail_approval_projection_maps_source_status_to_public_vocabulary(
    source_status: str,
    public_status: str,
) -> None:
    thread = _thread_view()
    response = _project_workbench_response(
        thread,
        workbench_store=_FakeWorkbenchStore(detail_status=source_status),
    )

    assert response.detailApprovals[0].status == public_status


def test_stream_envelope_has_monotonic_cursor_and_semantic_kind() -> None:
    envelope = build_stream_envelope(
        conversation_id="agent_conv_1",
        seq=8,
        kind="activity.upserted",
        payload=AgentWorkbenchTranscriptPayloadResponse(kind="activity", activityId="activity_1"),
        created_at=_now(),
    )

    assert envelope.schemaVersion == "agent.workbench.stream.v1"
    assert envelope.conversationId == "agent_conv_1"
    assert envelope.seq == 8
    assert envelope.kind == "activity.upserted"
    assert envelope.payload.activityId == "activity_1"


def test_stream_envelope_payload_schema_is_discriminated_by_payload_kind() -> None:
    import seektalent_ui.agent_workbench_models as models

    schema = models.AgentWorkbenchStreamEnvelopeResponse.model_json_schema()
    payload_schema = schema["properties"]["payload"]
    serialized_payload_schema = json.dumps(payload_schema, sort_keys=True)

    assert payload_schema["discriminator"]["propertyName"] == "payloadType"
    assert "oneOf" in payload_schema
    assert "AgentWorkbenchMessageStreamPayloadResponse" in serialized_payload_schema
    assert "AgentWorkbenchEmptyStreamPayloadResponse" not in serialized_payload_schema
    assert "AgentWorkbenchTranscriptPayloadResponse" not in serialized_payload_schema


def test_stream_envelope_rejects_payload_type_that_does_not_match_kind() -> None:
    with pytest.raises(ValueError, match="payloadType must match envelope kind"):
        build_stream_envelope(
            conversation_id="agent_conv_1",
            seq=1,
            kind="strategyGraph.changed",
            payload=AgentWorkbenchItemStreamPayloadResponse(
                payloadType="candidate.upserted",
                kind="candidate",
                itemId="candidate_1",
            ),
            created_at=_now(),
        )


def test_agent_workbench_stream_store_replays_durable_bff_seq_and_gaps(tmp_path: Path) -> None:
    store = AgentWorkbenchStreamStore(tmp_path / "stream.sqlite3")
    first = store.append_event(
        conversation_id="agent_conv_1",
        kind="message.completed",
        payload=AgentWorkbenchTranscriptPayloadResponse(kind="message", messageId="msg_1"),
        source_fact_key="message:msg_1",
        created_at=_now(),
    )
    replay = store.append_event(
        conversation_id="agent_conv_1",
        kind="message.completed",
        payload=AgentWorkbenchTranscriptPayloadResponse(kind="message", messageId="msg_1"),
        source_fact_key="message:msg_1",
        created_at=_now(),
    )
    second = store.append_event(
        conversation_id="agent_conv_1",
        kind="activity.upserted",
        payload=AgentWorkbenchTranscriptPayloadResponse(kind="activity", activityId="activity_1"),
        source_fact_key="activity:activity_1",
        created_at=_now(),
    )

    assert first.seq == replay.seq == 1
    assert second.seq == 2
    assert [event.seq for event in store.replay_stream_envelopes(conversation_id="agent_conv_1", after_seq=0)] == [1, 2]
    assert [event.seq for event in store.replay_stream_envelopes(conversation_id="agent_conv_1", after_seq=1)] == [2]
    gap = store.build_gap_event(conversation_id="agent_conv_1", requested_after_seq=9, created_at=_now())
    assert gap.kind == "stream.gap"
    assert gap.payload.missingFromSeq == 10
    assert gap.payload.nextAvailableSeq == 3


def test_stream_store_prunes_only_explicitly_closed_conversation_prefix_and_preserves_gap(tmp_path: Path) -> None:
    store = AgentWorkbenchStreamStore(tmp_path / "stream.sqlite3")
    for index in range(3):
        store.append_event(
            conversation_id="agent_conv_closed",
            kind="message.completed",
            payload=AgentWorkbenchTranscriptPayloadResponse(kind="message", messageId=f"msg_{index}"),
            source_fact_key=f"message:closed:{index}",
            created_at=f"2026-05-0{index + 1}T00:00:00Z",
        )
        store.append_event(
            conversation_id="agent_conv_active",
            kind="message.completed",
            payload=AgentWorkbenchTranscriptPayloadResponse(kind="message", messageId=f"active_{index}"),
            source_fact_key=f"message:active:{index}",
            created_at=f"2026-05-0{index + 1}T00:00:00Z",
        )

    dry_run = store.prune_closed_conversation_events(
        ["agent_conv_closed"],
        created_before="2026-06-01T00:00:00Z",
        retain_last=1,
        dry_run=True,
    )
    applied = store.prune_closed_conversation_events(
        ["agent_conv_closed"],
        created_before="2026-06-01T00:00:00Z",
        retain_last=1,
    )
    replay = list(replay_stream_envelopes(store, conversation_id="agent_conv_closed", after_seq=0))

    assert dry_run == 2
    assert applied == 2
    assert store.first_seq(conversation_id="agent_conv_closed") == 3
    assert store.latest_seq(conversation_id="agent_conv_closed") == 3
    assert store.first_seq(conversation_id="agent_conv_active") == 1
    assert [event.seq for event in store.replay_stream_envelopes(conversation_id="agent_conv_active", after_seq=0)] == [
        1,
        2,
        3,
    ]
    assert replay[0].kind == "stream.gap"
    assert replay[0].payload.nextAvailableSeq == 3
    assert replay[1].seq == 3


def test_agent_workbench_stream_store_replays_after_process_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "stream.sqlite3"
    first_store = AgentWorkbenchStreamStore(db_path)
    first_store.append_event(
        conversation_id="agent_conv_1",
        kind="message.delta",
        payload=AgentWorkbenchMessageStreamPayloadResponse(
            payloadType="message.delta",
            kind="message",
            messageId="msg_1",
            delta="正在分析候选人",
        ),
        source_fact_key="message:msg_1:delta:1",
        created_at=_now(),
    )

    restarted_store = AgentWorkbenchStreamStore(db_path)
    replay = restarted_store.replay_stream_envelopes(conversation_id="agent_conv_1", after_seq=0)

    assert [event.seq for event in replay] == [1]
    assert replay[0].kind == "message.delta"
    assert replay[0].payload.payloadType == "message.delta"
    assert replay[0].payload.messageId == "msg_1"
    assert replay[0].payload.delta == "正在分析候选人"


def test_stream_store_logs_legacy_payload_fallback(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = AgentWorkbenchStreamStore(tmp_path / "stream.sqlite3")
    store.append_event(
        conversation_id="agent_conv_1",
        kind="message.completed",
        payload=AgentWorkbenchTranscriptPayloadResponse(kind="message", messageId="msg_legacy"),
        source_fact_key="message:msg_legacy",
        created_at=_now(),
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            """
            UPDATE agent_workbench_stream_events
            SET payload_json = ?
            WHERE conversation_id = ? AND seq = ?
            """,
            (
                AgentWorkbenchTranscriptPayloadResponse(kind="message", messageId="msg_legacy").model_dump_json(),
                "agent_conv_1",
                1,
            ),
        )

    with caplog.at_level("WARNING", logger="seektalent_ui.agent_workbench_stream_store"):
        replay = store.replay_stream_envelopes(conversation_id="agent_conv_1", after_seq=0)

    assert replay[0].payload.payloadType == "message.completed"
    assert any("legacy agent workbench stream payload" in record.message for record in caplog.records)


def test_stream_replay_emits_gap_for_sequence_discontinuity(tmp_path: Path) -> None:
    store = AgentWorkbenchStreamStore(tmp_path / "stream.sqlite3")
    for event_id in ["msg_1", "msg_2", "msg_3"]:
        store.append_event(
            conversation_id="agent_conv_1",
            kind="message.completed",
            payload=AgentWorkbenchTranscriptPayloadResponse(kind="message", messageId=event_id),
            source_fact_key=f"message:{event_id}",
            created_at=_now(),
        )
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "DELETE FROM agent_workbench_stream_events WHERE conversation_id = ? AND seq = ?",
            ("agent_conv_1", 2),
        )

    replay = list(replay_stream_envelopes(store, conversation_id="agent_conv_1", after_seq=0))

    assert [event.kind for event in replay] == ["message.completed", "stream.gap", "message.completed"]
    assert replay[1].seq == 2
    assert replay[1].payload.missingFromSeq == 2
    assert replay[1].payload.nextAvailableSeq == 3


def test_stream_store_schema_has_source_refs_and_idempotency_constraints(tmp_path: Path) -> None:
    store = AgentWorkbenchStreamStore(tmp_path / "stream.sqlite3")

    with sqlite3.connect(store.path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_workbench_stream_events)").fetchall()}
        indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list(agent_workbench_stream_events)").fetchall()
        }

    assert {"source_kind", "source_id", "source_seq", "idempotency_key"} <= columns
    assert "idx_agent_workbench_stream_events_source" in indexes


def test_projected_stream_events_cover_non_transcript_workbench_surfaces() -> None:
    thread = _thread_view(final_summary_id="summary_1")
    projection_input = build_agent_workbench_projection_input(
        service=_FakeAgentService(thread),
        conversation_store=_FakeConversationStore(),
        runtime_store=_FakeRuntimeStore(),
        workbench_store=_FakeWorkbenchStore(),
        conversation_id="agent_conv_1",
        user=WorkbenchUser(
            user_id="user_admin_example_com",
            email="admin@example.com",
            display_name="Admin User",
            role="admin",
            workspace_id="default",
        ),
    )
    response = project_agent_workbench_view(projection_input)

    events = project_agent_workbench_stream_events(response)

    assert {
        "strategyGraph.changed",
        "candidate.upserted",
        "detailApproval.changed",
        "finalSummary.updated",
        "pendingAction.changed",
        "sourceConnection.changed",
        "thinkingProcess.changed",
    } <= {event.kind for event in events}
    assert all(event.source_fact_key for event in events)
    assert "rawPayload" not in json.dumps([event.payload.model_dump(mode="json") for event in events])
    assert "raw_body" not in json.dumps([event.payload.model_dump(mode="json") for event in events])


def test_candidate_stream_idempotency_tracks_same_status_content_changes() -> None:
    thread = _thread_view(final_summary_id="summary_1")
    projection_input = build_agent_workbench_projection_input(
        service=_FakeAgentService(thread),
        conversation_store=_FakeConversationStore(),
        runtime_store=_FakeRuntimeStore(),
        workbench_store=_FakeWorkbenchStore(),
        conversation_id="agent_conv_1",
        user=WorkbenchUser(
            user_id="user_admin_example_com",
            email="admin@example.com",
            display_name="Admin User",
            role="admin",
            workspace_id="default",
        ),
    )
    response = project_agent_workbench_view(projection_input)
    updated_candidate = response.candidates[0].model_copy(update={"matchSummary": "Updated same-status summary."})
    updated_response = response.model_copy(update={"candidates": [updated_candidate]})

    [first_candidate_event] = [
        event for event in project_agent_workbench_stream_events(response) if event.kind == "candidate.upserted"
    ]
    [updated_candidate_event] = [
        event for event in project_agent_workbench_stream_events(updated_response) if event.kind == "candidate.upserted"
    ]

    assert first_candidate_event.source_fact_key != updated_candidate_event.source_fact_key


def test_activity_stream_idempotency_tracks_same_activity_updates(tmp_path: Path) -> None:
    first_thread = _thread_view(final_summary_id="summary_1")
    updated_thread = first_thread.model_copy(deep=True)
    updated_thread.activity_items[0] = updated_thread.activity_items[0].model_copy(
        update={
            "status": "completed",
            "summary": "检索完成，进入候选人评分。",
            "updated_at": "2026-06-12T12:01:00+00:00",
        }
    )
    first_response = _project_workbench_response(first_thread)
    updated_response = _project_workbench_response(updated_thread)

    [first_activity_event] = [
        event for event in project_agent_workbench_stream_events(first_response) if event.kind == "activity.upserted"
    ]
    [updated_activity_event] = [
        event for event in project_agent_workbench_stream_events(updated_response) if event.kind == "activity.upserted"
    ]
    store = AgentWorkbenchStreamStore(tmp_path / "stream.sqlite3")

    first_envelope = store.append_event(
        conversation_id=first_response.conversation.conversationId,
        kind=first_activity_event.kind,
        payload=first_activity_event.payload,
        source_fact_key=first_activity_event.source_fact_key,
        created_at=first_activity_event.created_at,
    )
    updated_envelope = store.append_event(
        conversation_id=updated_response.conversation.conversationId,
        kind=updated_activity_event.kind,
        payload=updated_activity_event.payload,
        source_fact_key=updated_activity_event.source_fact_key,
        created_at=updated_activity_event.created_at,
    )

    assert first_activity_event.source_fact_key != updated_activity_event.source_fact_key
    assert first_envelope.seq == 1
    assert updated_envelope.seq == 2
    replay = store.replay_stream_envelopes(conversation_id="agent_conv_1", after_seq=0)
    assert [event.payload.summary for event in replay] == ["正在检索候选人", "检索完成，进入候选人评分。"]


def test_projection_input_aggregator_loads_named_store_boundaries() -> None:
    thread = _thread_view(final_summary_id="summary_1")
    user = WorkbenchUser(
        user_id="user_admin_example_com",
        email="admin@example.com",
        display_name="Admin User",
        role="admin",
        workspace_id="default",
    )

    projection_input = build_agent_workbench_projection_input(
        service=_FakeAgentService(thread),
        conversation_store=_FakeConversationStore(),
        runtime_store=_FakeRuntimeStore(),
        workbench_store=_FakeWorkbenchStore(),
        conversation_id="agent_conv_1",
        user=user,
    )

    assert projection_input.tool_call_records[0].tool_call_id == "tool_1"
    assert projection_input.context_compactions[0].compaction_id == "compact_1"
    assert projection_input.runtime is not None
    assert projection_input.runtime.runtimeRunId == "runtime_1"
    assert projection_input.runtime_events[0].event_id == "runtime_event_1"
    assert projection_input.source_connections[0].sourceKind == "liepin"
    assert projection_input.candidates[0].candidateId == "candidate_1"
    assert projection_input.detail_approvals[0].approvalId == "detail_request_1"
    assert projection_input.review_artifacts[0].artifactId == "artifact_1"
    assert projection_input.final_summary is not None
    assert projection_input.final_summary.summaryId == "summary_1"


def test_projection_input_aggregator_reads_recent_runtime_event_window() -> None:
    thread = _thread_view()
    runtime_store = _LargeRuntimeStore()

    projection_input = build_agent_workbench_projection_input(
        service=_FakeAgentService(thread),
        conversation_store=_FakeConversationStore(),
        runtime_store=runtime_store,
        workbench_store=_FakeWorkbenchStore(),
        conversation_id="agent_conv_1",
        user=WorkbenchUser(
            user_id="user_admin_example_com",
            email="admin@example.com",
            display_name="Admin User",
            role="admin",
            workspace_id="default",
        ),
    )

    assert runtime_store.calls[0] == (150, 100)
    assert len(projection_input.runtime_events) == 100
    assert projection_input.runtime_events[0].event_seq == 151
    assert projection_input.runtime_events[-1].event_seq == 250


def test_transcript_groups_split_on_user_turns_and_context_compactions() -> None:
    first = _thread_view().model_copy(deep=True)
    second_user = TranscriptMessage(
        message_id="msg_3",
        conversation_id="agent_conv_1",
        message_seq=3,
        role="user",
        message_type="user_text",
        text="Add LangChain experience",
        payload={},
        created_at="2026-06-12T12:03:00+00:00",
    )
    second_assistant = TranscriptMessage(
        message_id="msg_4",
        conversation_id="agent_conv_1",
        message_seq=4,
        role="assistant",
        message_type="assistant_text",
        text="I will adjust the next round.",
        payload={},
        created_at="2026-06-12T12:04:00+00:00",
    )
    projection_input = AgentWorkbenchProjectionInput(
        conversation_reopen_state=first.conversation_reopen_state,
        messages=[*first.messages, second_user, second_assistant],
        activity_items=first.activity_items,
        context_compactions=[
            ContextCompactionRecord(
                compaction_id="compact_1",
                conversation_id="agent_conv_1",
                status="completed",
                trigger_reason_code="token_budget",
                summary_id="summary_1",
                source_message_seq_start=1,
                source_message_seq_end=2,
                source_activity_seq_start=1,
                source_activity_seq_end=1,
                created_at="2026-06-12T12:02:00+00:00",
                completed_at="2026-06-12T12:02:00+00:00",
            )
        ],
    )

    groups = project_agent_workbench_view(projection_input).transcriptGroups

    assert [group.groupId for group in groups] == [
        "conversation:agent_conv_1:segment:1",
        "context:compact_1",
        "conversation:agent_conv_1:segment:2",
    ]
    assert [event.kind for event in groups[0].events] == [
        "message.completed",
        "message.completed",
        "activity.upserted",
    ]
    assert [event.payload.messageId for event in groups[2].events] == ["msg_3", "msg_4"]


def test_transcript_projection_dedupes_runtime_events_materialized_as_activities() -> None:
    thread = _thread_view()
    covered_runtime_event = RuntimeControlEvent(
        event_id="runtime_event_7",
        runtime_run_id="runtime_1",
        event_seq=7,
        event_type="runtime_round",
        stage="round",
        round_no=1,
        source_id="liepin",
        status="running",
        summary="Round 1 duplicate",
        payload={},
        payload_size_bytes=0,
        created_at=_now(),
    )
    projection_input = AgentWorkbenchProjectionInput(
        conversation_reopen_state=thread.conversation_reopen_state,
        messages=thread.messages,
        activity_items=thread.activity_items,
        runtime_events=[covered_runtime_event],
    )

    events = project_agent_workbench_view(projection_input).transcriptGroups[0].events

    assert [event.kind for event in events] == [
        "message.completed",
        "message.completed",
        "activity.upserted",
    ]
    assert all(event.itemId != "runtime_event_7" for event in events)


def test_public_bff_models_do_not_expose_raw_object_payload_sinks() -> None:
    import seektalent_ui.agent_workbench_models as models

    for _, model_class in inspect.getmembers(models, inspect.isclass):
        if not issubclass(model_class, BaseModel) or model_class is BaseModel:
            continue
        for field_name, field in model_class.model_fields.items():
            assert not _annotation_contains_raw_object_sink(field.annotation), f"{model_class.__name__}.{field_name}"


def test_agent_route_deps_exposes_shared_store_dependencies() -> None:
    import seektalent_ui.agent_route_deps as deps
    import seektalent_ui.agent_routes as agent_routes

    assert hasattr(deps, "get_agent_conversation_store")
    assert hasattr(deps, "get_runtime_control_store")
    assert hasattr(deps, "get_agent_workbench_stream_store")
    assert agent_routes.AGENT_CONVERSATION_SCHEMA_VERSION == deps.AGENT_CONVERSATION_SCHEMA_VERSION


def test_agent_http_error_uses_typed_public_detail_without_raw_payload() -> None:
    from seektalent_conversation_agent.errors import ConversationAgentError
    from seektalent_ui.agent_route_deps import agent_http_error

    error = agent_http_error(
        ConversationAgentError(
            "agent_request_invalid",
            payload={"errors": [{"loc": ("body", "title"), "input": {"raw": "blocked"}}]},
        )
    )

    assert error.detail == {
        "schemaVersion": "agent.conversation.v1",
        "reasonCode": "agent_request_invalid",
        "validationErrorCount": 1,
    }


def test_agent_http_error_maps_missing_conversation_to_404() -> None:
    from seektalent_conversation_agent.errors import ConversationAgentError
    from seektalent_ui.agent_route_deps import agent_http_error

    error = agent_http_error(ConversationAgentError("conversation_not_found"))

    assert error.status_code == 404
    assert error.detail == {
        "schemaVersion": "agent.conversation.v1",
        "reasonCode": "conversation_not_found",
        "validationErrorCount": 0,
    }


def test_conversation_store_lists_context_compactions_without_raw_message_bodies(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    service = client.app.state.agent_conversation_service
    conversation = service.create_conversation(
        owner_user_id="user_admin_example_com",
        workspace_id="default",
        title="Find backend engineers",
    )
    service.store.save_context_compaction(
        compaction_id="compact_1",
        conversation_id=conversation.conversation_id,
        status="completed",
        trigger_reason_code="token_budget",
        summary_id=None,
        source_message_seq_start=1,
        source_message_seq_end=2,
        created_at=_now(),
        completed_at=_now(),
    )

    compactions = service.store.list_context_compactions(conversation_id=conversation.conversation_id)

    assert [item.compaction_id for item in compactions] == ["compact_1"]
    assert not hasattr(compactions[0], "summary_text")


def test_agent_workbench_routes_use_public_agent_route_deps() -> None:
    import seektalent_ui.agent_workbench_routes as workbench_routes

    source = inspect.getsource(workbench_routes)

    assert "from seektalent_ui.agent_routes import _" not in source
    assert "agent_http_error" in source


def test_agent_workbench_view_route_returns_typed_snapshot(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]

    stream_store = client.app.state.agent_workbench_stream_store
    assert stream_store.latest_seq(conversation_id=conversation_id) == 0

    response = client.get(f"/api/agent/workbench/conversations/{conversation_id}")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schemaVersion"] == "agent.workbench.view.v2"
    assert payload["conversation"]["conversationId"] == conversation_id
    assert "transcriptGroups" in payload
    assert "streamCursor" in payload
    assert payload["streamCursor"]["latestStreamSeq"] == 0
    assert payload["streamCursor"]["snapshotSeq"] == 0
    assert payload["streamCursor"]["viewRevision"] == 0
    assert stream_store.latest_seq(conversation_id=conversation_id) == 0


def test_workbench_message_action_returns_refreshed_workbench_view(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]

    response = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/messages",
        json={
            "messageType": "submitJd",
            "text": "需要 Python 平台负责人，负责 API 与平台工程。",
            "jobTitle": "Python 平台负责人",
            "notes": None,
            "sourceKinds": ["cts"],
            "idempotencyKey": "submit-jd-workbench-view-1",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schemaVersion"] == "agent.workbench.view.v2"
    assert body["requirementDraft"]["sections"][0]["displayName"] == "必须满足"
    assert body["messages"][0]["payload"]["kind"] == "job_request"
    assert "confirm_requirements" in body["pendingActions"]["allowed"]


def test_workbench_message_action_uses_agent_write_rate_limiter(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.app.state.agent_rate_limiter = LocalAgentRateLimiter(max_writes_per_minute=1)
    _ensure_local_actor(client)
    created = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["conversation"]["conversationId"]

    response = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/messages",
        json={
            "messageType": "submitJd",
            "text": "需要 Python 平台负责人，负责 API 与平台工程。",
            "jobTitle": "Python 平台负责人",
            "notes": None,
            "sourceKinds": ["cts"],
            "idempotencyKey": "submit-jd-workbench-rate-limited-1",
        },
    )

    assert response.status_code == 429
    problem = response.json()
    assert problem["type"].endswith("/agent_rate_limited")
    assert problem["status"] == 429
    assert problem["reasonCode"] == "agent_rate_limited"


def test_workbench_requirement_operation_returns_refreshed_workbench_view(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]
    submitted = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/messages",
        json={
            "messageType": "submitJd",
            "text": "需要 Python 平台负责人，负责 API 与平台工程。",
            "jobTitle": "Python 平台负责人",
            "notes": None,
            "sourceKinds": ["cts"],
            "idempotencyKey": "submit-jd-workbench-operation-1",
        },
    )
    assert submitted.status_code == 200, submitted.text
    draft = submitted.json()["requirementDraft"]
    first_item = draft["sections"][0]["items"][0]

    response = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/requirements/operations",
        json={
            "draftRevisionId": draft["draftRevisionId"],
            "expectedDraftRevisionId": draft["draftRevisionId"],
            "operations": [{"op": "set_selected", "itemId": first_item["itemId"], "selected": False}],
            "idempotencyKey": "deselect-workbench-1",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schemaVersion"] == "agent.workbench.view.v2"
    updated = body["requirementDraft"]["sections"][0]["items"][0]
    assert updated["itemId"] == first_item["itemId"]
    assert updated["selected"] is False


def test_workbench_message_action_rejects_source_ids_only_submit_jd_contract(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]

    response = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/messages",
        json={
            "messageType": "submitJd",
            "text": "需要 Python 平台负责人，负责 API 与平台工程。",
            "jobTitle": "Python 平台负责人",
            "notes": None,
            "sourceIds": ["cts"],
            "idempotencyKey": "submit-jd-workbench-source-ids-only-1",
        },
    )

    assert response.status_code == 400, response.text
    problem = response.json()
    assert problem["reasonCode"] == "agent_request_invalid"
    assert {region["field"] for region in problem["regions"]} >= {"submitJd.sourceIds", "submitJd.sourceKinds"}


def test_workbench_requirement_operation_stale_revision_returns_problem_details(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]
    submitted = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/messages",
        json={
            "messageType": "submitJd",
            "text": "需要 Python 平台负责人，负责 API 与平台工程。",
            "jobTitle": "Python 平台负责人",
            "notes": None,
            "sourceKinds": ["cts"],
            "idempotencyKey": "submit-jd-workbench-stale-operation-1",
        },
    )
    assert submitted.status_code == 200, submitted.text
    draft = submitted.json()["requirementDraft"]
    first_item = draft["sections"][0]["items"][0]

    response = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/requirements/operations",
        json={
            "draftRevisionId": draft["draftRevisionId"],
            "expectedDraftRevisionId": "stale-draft",
            "operations": [{"op": "set_selected", "itemId": first_item["itemId"], "selected": False}],
            "idempotencyKey": "deselect-workbench-stale-1",
        },
    )

    assert response.status_code == 409, response.text
    problem = response.json()
    assert problem["type"].endswith("/requirement_draft_stale")
    assert problem["reasonCode"] == "requirement_draft_stale"


def test_workbench_requirement_amend_empty_expected_revision_returns_validation_error(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]
    submitted = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/messages",
        json={
            "messageType": "submitJd",
            "text": "需要 Python 平台负责人，负责 API 与平台工程。",
            "jobTitle": "Python 平台负责人",
            "notes": None,
            "sourceKinds": ["cts"],
            "idempotencyKey": "submit-jd-workbench-empty-amend-1",
        },
    )
    assert submitted.status_code == 200, submitted.text
    draft_id = submitted.json()["requirementDraft"]["draftRevisionId"]

    response = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/requirements/amend-from-text",
        json={
            "draftRevisionId": draft_id,
            "expectedDraftRevisionId": "",
            "text": "补充要求：平台治理经验",
            "idempotencyKey": "amend-workbench-empty-expected-1",
        },
    )

    assert response.status_code == 400, response.text
    problem = response.json()
    assert problem["reasonCode"] == "agent_request_invalid"
    assert problem["regions"][0]["field"] == "expectedDraftRevisionId"


def test_agent_workbench_confirm_route_queues_start_intent_without_sync_runtime_start(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]
    submitted = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={
            "messageType": "submitJd",
            "jobTitle": "Python 平台负责人",
            "text": "需要 Python API、平台工程和检索排序。",
            "notes": "优先 toB SaaS",
            "sourceKinds": ["cts"],
            "idempotencyKey": "submit-jd-workbench-confirm-1",
        },
    )
    assert submitted.status_code == 200, submitted.text
    draft_id = submitted.json()["requirementDraftRevisionId"]

    confirmed = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/requirements/confirm",
        json={
            "draftRevisionId": draft_id,
            "expectedDraftRevisionId": draft_id,
            "idempotencyKey": "confirm-workbench-1",
        },
    )

    assert confirmed.status_code == 200, confirmed.text
    payload = confirmed.json()
    assert payload["schemaVersion"] == "agent.workbench.view.v2"
    assert payload["conversation"]["conversationId"] == conversation_id
    assert payload["streamCursor"]["snapshotSeq"] == payload["streamCursor"]["latestStreamSeq"]
    assert payload["streamCursor"]["viewRevision"] == payload["streamCursor"]["snapshotSeq"]
    assert payload["conversation"]["workflowStartIntentId"]
    assert payload["conversation"]["workflowStartState"] == "queued"
    assert payload["conversation"]["workflowStartReasonCode"] is None
    assert payload["conversation"]["runtimeRunId"] is None
    assert payload["runtime"] is None
    service = client.app.state.agent_conversation_service
    runtime_store = service.tool_adapter.runtime_store
    assert runtime_store.get_run_by_run_intent_id(
        f"wts:default:{conversation_id}:{draft_id}"
    ) is None


def test_agent_workbench_confirm_route_is_idempotent_across_http_keys_for_same_draft(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]
    submitted = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={
            "messageType": "submitJd",
            "jobTitle": "Python 平台负责人",
            "text": "需要 Python API、平台工程和检索排序。",
            "notes": "优先 toB SaaS",
            "sourceKinds": ["cts", "liepin"],
            "idempotencyKey": "submit-jd-workbench-confirm-duplicate-1",
        },
    )
    assert submitted.status_code == 200, submitted.text
    draft_id = submitted.json()["requirementDraftRevisionId"]

    first = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/requirements/confirm",
        json={
            "draftRevisionId": draft_id,
            "expectedDraftRevisionId": draft_id,
            "idempotencyKey": "confirm-workbench-duplicate-1",
        },
    )
    second = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/requirements/confirm",
        json={
            "draftRevisionId": draft_id,
            "expectedDraftRevisionId": draft_id,
            "idempotencyKey": "confirm-workbench-duplicate-2",
        },
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["conversation"]["workflowStartIntentId"] == second.json()["conversation"]["workflowStartIntentId"]
    service = client.app.state.agent_conversation_service
    assert service.workflow_start_intent_store.count_for_draft(draft_id) == 1


def test_agent_workbench_confirm_route_rejects_same_key_changed_expected_revision(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]
    submitted = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={
            "messageType": "submitJd",
            "jobTitle": "Python 平台负责人",
            "text": "需要 Python API、平台工程和检索排序。",
            "notes": "优先 toB SaaS",
            "sourceKinds": ["cts"],
            "idempotencyKey": "submit-jd-workbench-confirm-conflict-1",
        },
    )
    assert submitted.status_code == 200, submitted.text
    draft_id = submitted.json()["requirementDraftRevisionId"]
    first = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/requirements/confirm",
        json={
            "draftRevisionId": draft_id,
            "expectedDraftRevisionId": draft_id,
            "idempotencyKey": "confirm-workbench-conflict-1",
        },
    )
    assert first.status_code == 200, first.text

    conflict = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/requirements/confirm",
        json={
            "draftRevisionId": draft_id,
            "expectedDraftRevisionId": "stale-draft-revision",
            "idempotencyKey": "confirm-workbench-conflict-1",
        },
    )

    assert conflict.status_code == 409, conflict.text
    problem = conflict.json()
    assert problem["reasonCode"] == "idempotency_key_conflict"
    assert isinstance(problem["detail"], str)


def test_agent_workbench_conversation_list_route_returns_typed_summaries(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]

    response = client.get("/api/agent/workbench/conversations")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["conversations"]) == 1
    summary = payload["conversations"][0]
    assert summary["conversationId"] == conversation_id
    assert summary["title"] == "资深 Python 后端"
    assert summary["status"] == "draft"
    assert summary["isArchived"] is False
    assert summary["runtimeRunId"] is None
    assert summary["workbenchSessionId"] is None
    assert summary["workflowStartIntentId"] is None
    assert summary["workflowStartState"] == "not_started"
    assert summary["workflowStartReasonCode"] is None
    assert isinstance(summary["updatedAt"], str)

    submitted = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={
            "messageType": "submitJd",
            "jobTitle": "Python 平台负责人",
            "text": "需要 Python API、平台工程和检索排序。",
            "notes": "优先 toB SaaS",
            "sourceKinds": ["cts"],
            "idempotencyKey": "submit-jd-workbench-list-summary-1",
        },
    )
    assert submitted.status_code == 200, submitted.text
    draft_id = submitted.json()["requirementDraftRevisionId"]
    confirmed = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/requirements/confirm",
        json={
            "draftRevisionId": draft_id,
            "expectedDraftRevisionId": draft_id,
            "idempotencyKey": "confirm-workbench-list-summary-1",
        },
    )
    assert confirmed.status_code == 200, confirmed.text

    refreshed = client.get("/api/agent/workbench/conversations")

    assert refreshed.status_code == 200, refreshed.text
    refreshed_summary = refreshed.json()["conversations"][0]
    assert refreshed_summary["conversationId"] == conversation_id
    assert refreshed_summary["workflowStartIntentId"] == confirmed.json()["conversation"]["workflowStartIntentId"]
    assert refreshed_summary["workflowStartState"] == "queued"
    assert refreshed_summary["workflowStartReasonCode"] is None


def test_agent_workbench_event_replay_route_returns_typed_envelopes(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]
    client.app.state.agent_workbench_stream_store.append_event(
        conversation_id=conversation_id,
        kind="message.completed",
        payload=AgentWorkbenchTranscriptPayloadResponse(kind="message", messageId="msg_1"),
        source_fact_key="message:msg_1",
        created_at=_now(),
    )

    response = client.get(f"/api/agent/workbench/conversations/{conversation_id}/events?after_seq=0")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schemaVersion"] == "agent.workbench.stream.replay.v1"
    assert payload["conversationId"] == conversation_id
    assert payload["events"][0]["schemaVersion"] == "agent.workbench.stream.v1"
    assert payload["events"][0]["kind"] == "message.completed"
    assert payload["latestSeq"] >= payload["events"][-1]["seq"]
    assert isinstance(payload["hasMore"], bool)
    assert payload["nextAfterSeq"] is None or isinstance(payload["nextAfterSeq"], int)


def test_agent_workbench_event_replay_route_returns_live_message_delta(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]
    client.app.state.agent_workbench_stream_store.append_event(
        conversation_id=conversation_id,
        kind="message.delta",
        payload=AgentWorkbenchMessageStreamPayloadResponse(
            payloadType="message.delta",
            kind="message",
            messageId="msg_1",
            delta="正在分析候选人",
        ),
        source_fact_key="message:msg_1:delta:1",
        created_at=_now(),
    )

    response = client.get(f"/api/agent/workbench/conversations/{conversation_id}/events?after_seq=0")

    assert response.status_code == 200, response.text
    event = response.json()["events"][0]
    assert event["kind"] == "message.delta"
    assert event["payload"] == {
        "payloadType": "message.delta",
        "kind": "message",
        "messageId": "msg_1",
        "delta": "正在分析候选人",
        "summary": None,
    }


def test_agent_workbench_stream_route_rejects_auth_query_without_workbench_session(tmp_path: Path) -> None:
    client = _client(tmp_path)
    conversation_id = client.post("/api/agent/conversations", json={"title": "Python Agent Engineer"}).json()["conversation"][
        "conversationId"
    ]
    token_query = client.get(f"/api/agent/workbench/conversations/{conversation_id}/events/stream?authToken=abc")
    assert token_query.status_code == 400


def test_agent_workbench_stream_does_not_require_session_cookie(tmp_path: Path) -> None:
    from seektalent_ui.agent_workbench_routes import stream_agent_workbench_events

    client = _client(tmp_path)
    created = client.post("/api/agent/conversations", json={"title": "Python Agent Engineer"})
    conversation_id = created.json()["conversation"]["conversationId"]
    user = client.app.state.workbench_store.ensure_local_actor()

    response = stream_agent_workbench_events(
        conversation_id=conversation_id,
        request=StreamingRequest(app=client.app, query_params={}),
        after_seq=0,
        user=user,
    )

    assert response.media_type == "text/event-stream"


def test_agent_workbench_sse_generator_emits_generic_event_for_local_actor(tmp_path: Path) -> None:
    from seektalent_ui.agent_workbench_routes import _event_generator

    client = _client(tmp_path)
    _ensure_local_actor(client)
    user = client.app.state.workbench_store.ensure_local_actor()
    assert user is not None
    stream_store = client.app.state.agent_workbench_stream_store
    envelope = stream_store.append_event(
        conversation_id="agent_conv_1",
        kind="message.completed",
        payload=AgentWorkbenchTranscriptPayloadResponse(kind="message", messageId="msg_1"),
        source_fact_key="message:msg_1",
        created_at=_now(),
    )

    generator = _event_generator(
        request=StreamingRequest(app=client.app),
        user=user,
        stream_store=stream_store,
        conversation_id="agent_conv_1",
        after_seq=0,
    )

    async def consume() -> dict[str, str]:
        first = await asyncio.wait_for(anext(generator), timeout=0.5)
        await generator.aclose()
        return first

    first = asyncio.run(consume())
    assert first["id"] == str(envelope.seq)
    assert first["event"] == "agent_workbench_event"
    assert json.loads(first["data"])["kind"] == "message.completed"


def test_agent_workbench_sse_generator_appends_projection_catchup_before_replay(tmp_path: Path) -> None:
    from seektalent_ui.agent_workbench_routes import _event_generator

    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]
    user = client.app.state.workbench_store.ensure_local_actor()
    assert user is not None

    generator = _event_generator(
        request=StreamingRequest(app=client.app),
        user=user,
        stream_store=client.app.state.agent_workbench_stream_store,
        conversation_id=conversation_id,
        after_seq=0,
    )

    first = asyncio.run(asyncio.wait_for(anext(generator), timeout=0.5))
    payload = json.loads(first["data"])
    assert first["event"] == "agent_workbench_event"
    assert payload["kind"] in {"strategyGraph.changed", "pendingAction.changed", "message.completed"}


def test_agent_workbench_sse_generator_emits_terminal_error_on_projection_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from seektalent_ui.agent_workbench_routes import _event_generator

    client = _client(tmp_path)
    _ensure_local_actor(client)
    user = client.app.state.workbench_store.ensure_local_actor()
    assert user is not None

    generator = _event_generator(
        request=StreamingRequest(app=client.app),
        user=user,
        stream_store=client.app.state.agent_workbench_stream_store,
        conversation_id="missing_conversation",
        after_seq=0,
    )

    async def consume() -> tuple[dict[str, str], dict[str, str] | None]:
        first = await asyncio.wait_for(anext(generator), timeout=0.5)
        try:
            second = await asyncio.wait_for(anext(generator), timeout=0.5)
        except StopAsyncIteration:
            second = None
        return first, second

    with caplog.at_level("WARNING", logger="seektalent_ui.agent_workbench_routes"):
        first, second = asyncio.run(consume())
    payload = json.loads(first["data"])
    assert first["event"] == "agent_workbench_error"
    assert payload["schemaVersion"] == "agent.workbench.stream.error.v1"
    assert payload["conversationId"] == "missing_conversation"
    assert payload["reasonCode"] == "projection_unavailable"
    assert payload["statusCode"] == 404
    assert "correlationId" in payload
    assert second is None
    [record] = [
        record
        for record in caplog.records
        if record.message == "Agent workbench SSE projection catch-up failed."
    ]
    assert record.conversation_ref == "redacted"
    assert not hasattr(record, "conversation_id")


class _FakeAgentService:
    def __init__(self, thread: ConversationThreadView) -> None:
        self.thread = thread

    def reopen_conversation(self, *, conversation_id: str, owner_user_id: str, workspace_id: str) -> ConversationThreadView:
        assert conversation_id == "agent_conv_1"
        assert owner_user_id == "user_admin_example_com"
        assert workspace_id == "default"
        return self.thread


class _FakeConversationStore:
    def list_tool_calls(self, *, conversation_id: str):
        assert conversation_id == "agent_conv_1"
        return [
            AgentToolCallRecord(
                tool_call_id="tool_1",
                conversation_id="agent_conv_1",
                activity_id="activity_1",
                runtime_run_id="runtime_1",
                tool_name="runtime_search",
                status="completed",
                args={"rawPayload": "must not leak"},
                result={"summary": "Read safe profile summaries.", "providerResponse": "must not leak"},
                reason_code=None,
                started_at=_now(),
                completed_at=_now(),
            )
        ]

    def list_context_compactions(self, *, conversation_id: str):
        assert conversation_id == "agent_conv_1"
        return [
            ContextCompactionRecord(
                compaction_id="compact_1",
                conversation_id="agent_conv_1",
                status="completed",
                trigger_reason_code="token_budget",
                summary_id="summary_1",
                source_message_seq_start=1,
                source_message_seq_end=2,
                source_activity_seq_start=1,
                source_activity_seq_end=1,
                created_at=_now(),
                completed_at=_now(),
            )
        ]


class _FakeRuntimeStore:
    def __init__(self, requirement_drafts: dict[str, object] | None = None) -> None:
        self.requirement_drafts = requirement_drafts or {}
        self.requested_requirement_draft_ids: list[str] = []

    def get_run(self, runtime_run_id: str) -> RuntimeRunRecord:
        assert runtime_run_id == "runtime_1"
        return RuntimeRunRecord(
            runtime_run_id="runtime_1",
            agent_conversation_id="agent_conv_1",
            workbench_session_id="session_1",
            approved_requirement_revision_id="approved_1",
            status="running",
            current_stage="round",
            current_round=1,
            latest_event_seq=7,
            source_ids=["liepin"],
            created_at=_now(),
            updated_at=_now(),
        )

    def list_events(self, *, runtime_run_id: str, after_seq: int, limit: int) -> RuntimeControlEventPage:
        assert runtime_run_id == "runtime_1"
        if after_seq > 0:
            return RuntimeControlEventPage(events=[], next_cursor=after_seq)
        return RuntimeControlEventPage(
            events=[
                RuntimeControlEvent(
                    event_id="runtime_event_1",
                    runtime_run_id="runtime_1",
                    event_seq=7,
                    event_type="runtime_round",
                    stage="round",
                    round_no=1,
                    source_id="liepin",
                    status="running",
                    summary="Round 1",
                    payload={"query_terms": ["AI agent"], "raw_provider_payload": "must not leak"},
                    payload_size_bytes=0,
                    created_at=_now(),
                )
            ],
            next_cursor=7,
        )

    def get_requirement_draft(self, draft_revision_id: str) -> object | None:
        self.requested_requirement_draft_ids.append(draft_revision_id)
        return self.requirement_drafts.get(draft_revision_id)

    def list_artifact_refs(self, *, runtime_run_id: str):
        assert runtime_run_id == "runtime_1"
        return [
            {
                "artifact_id": "artifact_1",
                "artifact_kind": "source_evidence",
                "title": "Search evidence",
                "safe_summary": "Safe source evidence.",
                "raw_body": "must not leak",
            }
        ]

    def get_final_summary(self, *, summary_id: str):
        assert summary_id == "summary_1"
        return {"summary_id": "summary_1", "text": "Final shortlist ready.", "raw_body": "must not leak"}


class _LargeRuntimeStore(_FakeRuntimeStore):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[int, int]] = []

    def get_run(self, runtime_run_id: str) -> RuntimeRunRecord:
        assert runtime_run_id == "runtime_1"
        return RuntimeRunRecord(
            runtime_run_id="runtime_1",
            agent_conversation_id="agent_conv_1",
            workbench_session_id="session_1",
            approved_requirement_revision_id="approved_1",
            status="running",
            current_stage="round",
            current_round=250,
            latest_event_seq=250,
            source_ids=["liepin"],
            created_at=_now(),
            updated_at=_now(),
        )

    def list_events(self, *, runtime_run_id: str, after_seq: int, limit: int) -> RuntimeControlEventPage:
        assert runtime_run_id == "runtime_1"
        self.calls.append((after_seq, limit))
        if after_seq >= 250:
            return RuntimeControlEventPage(events=[], next_cursor=after_seq)
        events = [
            RuntimeControlEvent(
                event_id=f"runtime_event_{event_seq}",
                runtime_run_id="runtime_1",
                event_seq=event_seq,
                event_type="runtime_round",
                stage="round",
                round_no=event_seq,
                source_id="liepin",
                status="running",
                summary=f"Round {event_seq}",
                payload={"query_terms": ["AI agent"]},
                payload_size_bytes=0,
                created_at=_now(),
            )
            for event_seq in range(151, 251)
        ]
        return RuntimeControlEventPage(events=events, next_cursor=250)


class _FakeWorkbenchStore:
    def __init__(self, *, detail_status: str = "pending") -> None:
        self.detail_status = detail_status

    def list_source_connections(self, *, user: WorkbenchUser):
        assert user.user_id == "user_admin_example_com"
        return [
            WorkbenchSourceConnection(
                connection_id="connection_1",
                source_kind="liepin",
                status="connected",
                warning_code=None,
                warning_message=None,
                provider_account_hash="must-not-leak",
                compliance_gate_ref="gate_1",
                created_at=_now(),
                updated_at=_now(),
                connected_at=_now(),
            )
        ]

    def list_candidate_review_items(self, *, user: WorkbenchUser, session_id: str):
        assert session_id == "session_1"
        return [
            WorkbenchCandidateReviewItem(
                review_item_id="candidate_1",
                session_id="session_1",
                status="new",
                note="",
                display_name="Ada",
                title="Backend Engineer",
                company="Example",
                location="Shanghai",
                summary="Safe candidate summary.",
                aggregate_score=91,
                fit_bucket="strong",
                why_selected="Strong backend fit.",
                source_round=1,
                source_badges=["liepin"],
                evidence_level="card",
                matched_must_haves=["Python"],
                matched_preferences=["LLM"],
                missing_risks=[],
                strengths=["Distributed systems"],
                weaknesses=[],
                evidence=[],
                created_at=_now(),
                updated_at=_now(),
            )
        ]

    def list_liepin_detail_open_requests(self, *, user: WorkbenchUser, session_id: str | None = None, **_: object):
        assert session_id == "session_1"
        return [
            WorkbenchDetailOpenRequest(
                request_id="detail_request_1",
                session_id="session_1",
                review_item_id="candidate_1",
                status=self.detail_status,
                detail_open_mode="human_confirm",
                decision_note=None,
                candidate=None,
                blocked_reason="detail_open_requires_approval",
                ledger=None,
                provider_action=None,
                created_at=_now(),
                updated_at=_now(),
            )
        ]


def _annotation_contains_raw_object_sink(annotation: object) -> bool:
    if annotation is Any or annotation is object:
        return True
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is dict:
        return True
    if origin in {list, tuple, set} and any(arg is object or arg is Any for arg in args):
        return True
    return any(_annotation_contains_raw_object_sink(arg) for arg in args)


def _project_workbench_response(
    thread: ConversationThreadView,
    *,
    workbench_store: _FakeWorkbenchStore | None = None,
):
    projection_input = build_agent_workbench_projection_input(
        service=_FakeAgentService(thread),
        conversation_store=_FakeConversationStore(),
        runtime_store=_FakeRuntimeStore(),
        workbench_store=workbench_store or _FakeWorkbenchStore(),
        conversation_id="agent_conv_1",
        user=WorkbenchUser(
            user_id="user_admin_example_com",
            email="admin@example.com",
            display_name="Admin User",
            role="admin",
            workspace_id="default",
        ),
    )
    return project_agent_workbench_view(projection_input)


def _runtime_projection_store_with_requirement_draft(draft: object) -> _FakeRuntimeStore:
    draft_revision_id = getattr(draft, "draft_revision_id")
    assert isinstance(draft_revision_id, str)
    return _FakeRuntimeStore(requirement_drafts={draft_revision_id: draft})


def _conversation_agent_service_for_thread(thread: ConversationThreadView) -> _FakeAgentService:
    return _FakeAgentService(thread)


def _conversation_store_for_thread(_: ConversationThreadView) -> _FakeConversationStore:
    return _FakeConversationStore()


def _empty_workbench_store() -> _FakeWorkbenchStore:
    return _FakeWorkbenchStore()


def _workbench_user() -> WorkbenchUser:
    return WorkbenchUser(
        user_id="user_admin_example_com",
        email="admin@example.com",
        display_name="Admin User",
        role="admin",
        workspace_id="default",
    )


def _thread_view(
    final_summary_id: str | None = None,
    latest_draft_revision_id: str | None = "draft_1",
) -> ConversationThreadView:
    return ConversationThreadView(
        conversation_reopen_state=ConversationReopenState(
            conversation_id="agent_conv_1",
            title="Find backend engineers",
            status="running",
            is_archived=False,
            latest_message_seq=2,
            latest_activity_seq=1,
            latest_rendered_runtime_event_seq=7,
            runtime_run_id="runtime_1",
            workbench_session_id="session_1",
            latest_draft_revision_id=latest_draft_revision_id,
            approved_requirement_revision_id="approved_1",
            final_summary_id=final_summary_id,
            pending_user_action="confirm_requirements",
            pending_command_count=0,
            pending_requirement_review_count=1,
            pending_memory_review_count=0,
            linked_runtime_runs=[
                ConversationRuntimeRunLink(
                    conversation_id="agent_conv_1",
                    runtime_run_id="runtime_1",
                    status="active",
                    run_kind="primary",
                    workbench_session_id="session_1",
                    approved_requirement_revision_id="approved_1",
                    run_intent_id="workflow:agent_conv_1:approved_1:primary",
                    link_reason="start",
                    latest_event_seq=7,
                    linked_at=_now(),
                    updated_at=_now(),
                    active_at=_now(),
                    is_active=True,
                ),
                ConversationRuntimeRunLink(
                    conversation_id="agent_conv_1",
                    runtime_run_id="runtime_2",
                    status="linked",
                    run_kind="rerun",
                    workbench_session_id="session_2",
                    approved_requirement_revision_id="approved_1",
                    run_intent_id="workflow:agent_conv_1:approved_1:rerun",
                    link_reason="rerun",
                    latest_event_seq=3,
                    linked_at=_now(),
                    updated_at=_now(),
                    is_active=False,
                ),
            ],
            allowed_actions=["submit_message", "confirm_requirements", "start_workflow"],
            reason_code=None,
            last_opened_at=_now(),
        ),
        messages=[
            TranscriptMessage(
                message_id="msg_1",
                conversation_id="agent_conv_1",
                message_seq=1,
                role="user",
                message_type="user_text",
                text="Find backend engineers",
                payload={"jobTitle": "Backend Engineer", "rawPayload": "must not leak"},
                created_at=_now(),
            ),
            TranscriptMessage(
                message_id="msg_2",
                conversation_id="agent_conv_1",
                message_seq=2,
                role="assistant",
                message_type="requirement_review",
                text="已拆解岗位需求，请确认后再启动检索。",
                payload={"requirementDraft": {"draftRevisionId": "draft_1"}},
                created_at=_now(),
            ),
        ],
        activity_items=[
            TranscriptActivityItem(
                activity_id="activity_1",
                conversation_id="agent_conv_1",
                activity_seq=1,
                activity_key="runtime_1:round:1",
                activity_type="runtime_event",
                status="running",
                title="第 1 轮检索",
                summary="正在检索候选人",
                source_runtime_run_id="runtime_1",
                source_event_id_latest="event_7",
                source_event_seq_start=1,
                source_event_seq_latest=7,
                payload={
                    "stage": "round",
                    "round_no": 1,
                    "query_terms": ["AI agent", "LLM"],
                    "keyword_query": "AI agent LLM",
                    "executed_queries": [
                        {"query_terms": ["AI agent", "LLM"], "query_role": "exploit"},
                    ],
                    "raw_candidate_count": 10,
                    "unique_new_count": 10,
                    "newly_scored_count": 10,
                    "resume_quality_comment": "初次搜索拿到10位新候选人，覆盖面较好，但人群混杂。",
                    "reflection_summary": "下一轮保留 AI agent、LLM，并加入 LangChain、AutoGen、RAG。",
                    "reflection_rationale": "候选人质量接近岗位要求，但需要提升相关度。",
                    "suggestedActivateTerms": ["LangChain", "AutoGen", "RAG"],
                    "suggestedKeepTerms": ["AI agent", "LLM"],
                    "suggestedDeprioritizeTerms": ["frontend"],
                    "suggestedDropTerms": ["实习"],
                },
                updated_at=_now(),
                created_at=_now(),
            )
        ],
    )


def _client(tmp_path: Path) -> TestClient:
    settings = make_settings(
        workspace_root=str(tmp_path),
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
    )
    return TestClient(
        create_app(settings=settings, runtime_factory=DeterministicRouteRuntime),
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    )


def _ensure_local_actor(client: TestClient) -> dict:
    user = client.app.state.workbench_store.ensure_local_actor()
    return {
        "user": {
            "userId": user.user_id,
            "email": user.email,
            "displayName": user.display_name,
            "role": user.role,
            "workspaceId": user.workspace_id,
        }
    }


def _now() -> str:
    return datetime(2026, 6, 12, 12, 0, tzinfo=UTC).isoformat()
