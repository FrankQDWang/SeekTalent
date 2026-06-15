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
    ConversationThreadView,
    TranscriptActivityItem,
    TranscriptMessage,
)
from seektalent_runtime_control.models import RuntimeControlEvent, RuntimeControlEventPage, RuntimeRunRecord
from seektalent_ui.agent_workbench_models import (
    AgentWorkbenchMessageStreamPayloadResponse,
    AgentWorkbenchItemStreamPayloadResponse,
    AgentWorkbenchTranscriptPayloadResponse,
)
from seektalent_ui.agent_workbench_projection import AgentWorkbenchProjectionInput, build_agent_workbench_projection_input
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


CSRF_COOKIE_NAME = "seektalent_workbench_csrf"


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

    assert response.schemaVersion == "agent.workbench.view.v1"
    assert response.conversation.conversationId == "agent_conv_1"
    assert response.conversation.runtimeRunId == "runtime_1"
    assert response.streamCursor.latestMessageSeq == 2
    assert response.streamCursor.latestActivitySeq == 1
    assert response.streamCursor.latestRuntimeEventSeq == 7
    assert response.streamCursor.latestStreamSeq == 0
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


def test_conversation_store_lists_context_compactions_without_raw_message_bodies(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)
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
    _bootstrap_and_login(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
        headers=_csrf_header(client),
    ).json()["conversation"]["conversationId"]

    response = client.get(f"/api/agent/workbench/conversations/{conversation_id}")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schemaVersion"] == "agent.workbench.view.v1"
    assert payload["conversation"]["conversationId"] == conversation_id
    assert "transcriptGroups" in payload
    assert "streamCursor" in payload
    assert payload["streamCursor"]["latestStreamSeq"] > 0


def test_agent_workbench_conversation_list_route_returns_typed_summaries(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
        headers=_csrf_header(client),
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
    assert isinstance(summary["updatedAt"], str)


def test_agent_workbench_event_replay_route_returns_typed_envelopes(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
        headers=_csrf_header(client),
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


def test_agent_workbench_event_replay_route_returns_live_message_delta(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
        headers=_csrf_header(client),
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
    }


def test_agent_workbench_stream_route_requires_session_cookie_and_rejects_auth_query(tmp_path: Path) -> None:
    client = _client(tmp_path)

    missing_session = client.get("/api/agent/workbench/conversations/agent_conv_1/events/stream")
    assert missing_session.status_code == 401

    _bootstrap_and_login(client)
    token_query = client.get("/api/agent/workbench/conversations/agent_conv_1/events/stream?authToken=abc")
    assert token_query.status_code == 400


def test_agent_workbench_sse_generator_rechecks_session_and_emits_generic_event(tmp_path: Path) -> None:
    from seektalent_ui.agent_workbench_routes import _event_generator
    from seektalent_ui.auth import session_token_digest

    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session_id = client.cookies.get("seektalent_workbench_session")
    assert session_id is not None
    digest = session_token_digest(session_id)
    user = client.app.state.workbench_store.get_user_by_session_readonly(session_digest=digest)
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
        session_digest=digest,
        stream_store=stream_store,
        conversation_id="agent_conv_1",
        after_seq=0,
    )

    async def consume() -> tuple[dict[str, str] | None, dict[str, str] | None]:
        first = await asyncio.wait_for(anext(generator), timeout=0.5)
        client.app.state.workbench_store.revoke_user_session(session_digest=digest)
        second = None
        try:
            second = await asyncio.wait_for(anext(generator), timeout=0.5)
        except StopAsyncIteration:
            second = None
        return first, second

    first, second = asyncio.run(consume())
    assert first is not None
    assert first["id"] == str(envelope.seq)
    assert first["event"] == "agent_workbench_event"
    assert json.loads(first["data"])["kind"] == "message.completed"
    assert second is None


def test_agent_workbench_sse_generator_appends_projection_catchup_before_replay(tmp_path: Path) -> None:
    from seektalent_ui.agent_workbench_routes import _event_generator
    from seektalent_ui.auth import session_token_digest

    client = _client(tmp_path)
    _bootstrap_and_login(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
        headers=_csrf_header(client),
    ).json()["conversation"]["conversationId"]
    session_id = client.cookies.get("seektalent_workbench_session")
    assert session_id is not None
    digest = session_token_digest(session_id)
    user = client.app.state.workbench_store.get_user_by_session_readonly(session_digest=digest)
    assert user is not None

    generator = _event_generator(
        request=StreamingRequest(app=client.app),
        user=user,
        session_digest=digest,
        stream_store=client.app.state.agent_workbench_stream_store,
        conversation_id=conversation_id,
        after_seq=0,
    )

    first = asyncio.run(asyncio.wait_for(anext(generator), timeout=0.5))
    payload = json.loads(first["data"])
    assert first["event"] == "agent_workbench_event"
    assert payload["kind"] in {"strategyGraph.changed", "pendingAction.changed", "message.completed"}


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
                    created_at=_now(),
                )
            ],
            next_cursor=7,
        )

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


class _FakeWorkbenchStore:
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
                status="pending",
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


def _project_workbench_response(thread: ConversationThreadView):
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
    return project_agent_workbench_view(projection_input)


def _thread_view(final_summary_id: str | None = None) -> ConversationThreadView:
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
            latest_draft_revision_id="draft_1",
            approved_requirement_revision_id="approved_1",
            final_summary_id=final_summary_id,
            pending_user_action="confirm_requirements",
            pending_command_count=0,
            pending_requirement_review_count=1,
            pending_memory_review_count=0,
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


def _bootstrap_and_login(client: TestClient) -> dict:
    bootstrap = client.post(
        "/api/auth/bootstrap",
        json={"email": "admin@example.com", "password": "correct horse", "displayName": "Admin User"},
    )
    assert bootstrap.status_code == 201, bootstrap.text
    login = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct horse"})
    assert login.status_code == 204, login.text
    return bootstrap.json()


def _csrf_header(client: TestClient) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE_NAME)
    assert token is not None
    return {"X-CSRF-Token": token}


def _now() -> str:
    return datetime(2026, 6, 12, 12, 0, tzinfo=UTC).isoformat()
