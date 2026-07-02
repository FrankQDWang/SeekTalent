from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args, get_origin

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from seektalent.config import AppSettings
from seektalent.progress import ProgressEvent
from seektalent_conversation_agent.models import (
    ContextCompactionRecord,
    ConversationReopenState,
    ConversationRuntimeRunLink,
    ConversationThreadView,
    OperationAuditRecord,
    TranscriptActivityItem,
    TranscriptMessage,
)
from seektalent_runtime_control.models import (
    RuntimeControlEvent,
    RuntimeControlEventPage,
    RuntimeRunRecord,
    RuntimeStageOutput,
    RuntimeStageOutputInput,
)
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_ui.agent_routes import LocalAgentRateLimiter
from seektalent_ui.agent_workbench_models import (
    AgentWorkbenchCandidateSummaryResponse,
    AgentWorkbenchConversationResponse,
    AgentWorkbenchConversationSummaryResponse,
    AgentWorkbenchDetailApprovalResponse,
    AgentWorkbenchFinalSummaryResponse,
    AgentWorkbenchGraphNodeResponse,
    AgentWorkbenchMessageStreamPayloadResponse,
    AgentWorkbenchItemStreamPayloadResponse,
    AgentWorkbenchPendingActionsResponse,
    AgentWorkbenchRunFinalizationResponse,
    AgentWorkbenchStrategyGraphResponse,
    AgentWorkbenchStreamCursorResponse,
    AgentWorkbenchThinkingProcessCardResponse,
    AgentWorkbenchThinkingProcessResponse,
    AgentWorkbenchThinkingProcessRoundResponse,
    AgentWorkbenchTranscriptPayloadResponse,
)
from seektalent_ui.agent_workbench_projection import (
    AgentWorkbenchProjectionInput,
    AgentWorkbenchWorkflowStartIntentProjection,
    build_agent_workbench_projection_input,
    candidate_detail_response_from_review_item,
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
from tests.conversation_agent_test_support import sample_requirement_sheet, save_approved_requirement
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


class BlockingRequirementRuntime:
    extract_call_count = 0

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
        del job_title, jd, notes, progress_callback, requirement_cache_scope
        type(self).extract_call_count += 1
        raise AssertionError("requirement extraction must not run inside first-turn HTTP acceptance")


class CapturingRequirementRuntime:
    extract_call_count = 0
    requirement_cache_scopes: list[str | None] = []

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
        del notes
        type(self).extract_call_count += 1
        type(self).requirement_cache_scopes.append(requirement_cache_scope)
        if callable(progress_callback):
            progress_callback(
                ProgressEvent(
                    type="requirements_completed",
                    message="岗位需求解析完成。",
                    payload={"stage": "requirements"},
                )
            )
        title = job_title.strip() if isinstance(job_title, str) and job_title.strip() else "AI Agent 平台工程师"
        return sample_requirement_sheet(job_title=title)


@dataclass
class StreamingRequest:
    app: object
    query_params: dict[str, str] | None = None

    async def is_disconnected(self) -> bool:
        return False


def test_workbench_from_jd_returns_transcript_before_requirement_extraction(tmp_path: Path) -> None:
    BlockingRequirementRuntime.extract_call_count = 0
    client = _client_with_runtime(tmp_path, BlockingRequirementRuntime)
    _ensure_local_actor(client)

    response = client.post(
        "/api/agent/workbench/conversations/from-jd",
        json={
            "idempotencyKey": "first-turn-1",
            "jobDescription": "上海 AI Agent 平台工程师，熟悉 RAG 和 workflow orchestration。",
            "jobTitle": None,
            "notes": None,
            "sourceKinds": ["liepin"],
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["conversation"]["conversationId"].startswith("agent_conv_")
    assert payload["conversation"]["workflowStartState"] == "not_started"
    assert payload["requirementDraft"] is None
    assert payload["strategyGraph"]["nodes"] == []
    transcript_text = _transcript_text(payload)
    assert "上海 AI Agent 平台工程师" in transcript_text
    assert "正在处理需求" in transcript_text or "正在思考" in transcript_text
    assert BlockingRequirementRuntime.extract_call_count == 0


def test_workbench_from_jd_idempotency_replays_same_conversation_and_conflicts_on_changed_payload(
    tmp_path: Path,
) -> None:
    client = _client_with_runtime(tmp_path, BlockingRequirementRuntime)
    _ensure_local_actor(client)
    request = {
        "idempotencyKey": "first-turn-replay-1",
        "jobDescription": "上海 AI Agent 平台工程师，熟悉 RAG 和 workflow orchestration。",
        "jobTitle": "AI Agent 平台工程师",
        "notes": None,
        "sourceKinds": ["liepin"],
    }

    first = client.post("/api/agent/workbench/conversations/from-jd", json=request)
    replay = client.post("/api/agent/workbench/conversations/from-jd", json=request)
    conflict = client.post(
        "/api/agent/workbench/conversations/from-jd",
        json={**request, "jobDescription": "杭州 AI Agent 平台工程师，熟悉 RAG。"},
    )

    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert first.json()["conversation"]["conversationId"] == replay.json()["conversation"]["conversationId"]
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["reasonCode"] == "idempotency_key_conflict"
    service = client.app.state.agent_conversation_service
    with sqlite3.connect(service.store.path) as conn:
        assert _table_count(conn, "wts_conversation_start_requests") == 1
        assert _table_count(conn, "agent_conversations") == 1
        assert _table_count(conn, "wts_job_request_revisions") == 1
        assert _table_count(conn, "wts_outbox") == 1


def test_workbench_from_jd_same_jd_new_conversation_does_not_reuse_old_requirement_draft(tmp_path: Path) -> None:
    CapturingRequirementRuntime.extract_call_count = 0
    CapturingRequirementRuntime.requirement_cache_scopes = []
    client = _client_with_runtime(tmp_path, CapturingRequirementRuntime)
    _ensure_local_actor(client)

    old_response = client.post(
        "/api/agent/workbench/conversations/from-jd",
        json={
            "idempotencyKey": "first-turn-old",
            "jobDescription": "上海 AI Agent 平台工程师，熟悉 RAG 和 workflow orchestration。",
            "jobTitle": "AI Agent 平台工程师",
            "notes": None,
            "sourceKinds": ["liepin"],
        },
    )
    assert old_response.status_code == 201, old_response.text
    old_conversation_id = old_response.json()["conversation"]["conversationId"]
    assert client.app.state.requirement_extraction_outbox_runner.run_once() == 1
    old_snapshot = client.get(f"/api/agent/workbench/conversations/{old_conversation_id}").json()
    old_draft_id = old_snapshot["requirementDraft"]["draftRevisionId"]

    new_response = client.post(
        "/api/agent/workbench/conversations/from-jd",
        json={
            "idempotencyKey": "first-turn-new",
            "jobDescription": "上海 AI Agent 平台工程师，熟悉 RAG 和 workflow orchestration。",
            "jobTitle": "AI Agent 平台工程师",
            "notes": None,
            "sourceKinds": ["liepin"],
        },
    )

    assert new_response.status_code == 201, new_response.text
    new_payload = new_response.json()
    new_conversation_id = new_payload["conversation"]["conversationId"]
    assert new_conversation_id != old_conversation_id
    assert new_payload["requirementDraft"] is None
    assert new_payload["strategyGraph"]["nodes"] == []
    assert "正在处理需求" in _transcript_text(new_payload) or "正在思考" in _transcript_text(new_payload)
    assert CapturingRequirementRuntime.extract_call_count == 1

    assert client.app.state.requirement_extraction_outbox_runner.run_once() == 1
    new_snapshot = client.get(f"/api/agent/workbench/conversations/{new_conversation_id}").json()
    new_draft_id = new_snapshot["requirementDraft"]["draftRevisionId"]
    assert new_draft_id != old_draft_id
    assert CapturingRequirementRuntime.extract_call_count == 2
    assert CapturingRequirementRuntime.requirement_cache_scopes[-1] == new_conversation_id


def test_requirement_extraction_outbox_completes_draft_after_fast_accept(tmp_path: Path) -> None:
    CapturingRequirementRuntime.extract_call_count = 0
    client = _client_with_runtime(tmp_path, CapturingRequirementRuntime)
    _ensure_local_actor(client)

    accepted = client.post(
        "/api/agent/workbench/conversations/from-jd",
        json={
            "idempotencyKey": "first-turn-outbox-1",
            "jobDescription": "上海 AI Agent 平台工程师，熟悉 RAG 和 workflow orchestration。",
            "jobTitle": "AI Agent 平台工程师",
            "notes": None,
            "sourceKinds": ["liepin"],
        },
    )

    assert accepted.status_code == 201, accepted.text
    service = client.app.state.agent_conversation_service
    with sqlite3.connect(service.store.path) as conn:
        [row] = conn.execute(
            "SELECT outbox_id, event_type, status FROM wts_outbox WHERE event_type = 'requirement_extraction_requested'"
        ).fetchall()
    assert row[2] == "pending"
    assert service.process_requirement_extraction_outbox_item(row[0]).draft_revision_id
    assert CapturingRequirementRuntime.extract_call_count == 1

    payload = client.get(
        f"/api/agent/workbench/conversations/{accepted.json()['conversation']['conversationId']}"
    ).json()
    assert payload["requirementDraft"] is not None
    transcript_text = _transcript_text(payload)
    assert "必须满足" in transcript_text
    assert "正在处理需求" not in transcript_text
    for section in payload["requirementDraft"]["sections"]:
        for item in section["items"]:
            if item.get("canSetSelected", True):
                assert item["selected"] is True


def test_workbench_messages_route_rejects_submit_jd(tmp_path: Path) -> None:
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
            "text": "JD",
            "jobTitle": None,
            "notes": None,
            "sourceKinds": ["liepin"],
            "idempotencyKey": "old-submit-jd",
        },
    )

    assert response.status_code in {400, 422}, response.text


def test_agent_workbench_view_projects_stable_frontend_contract() -> None:
    from seektalent_ui.agent_workbench_rounds import AgentWorkbenchRoundSummaryProjection

    thread = _thread_view()
    projection_input = AgentWorkbenchProjectionInput(
        conversation_reopen_state=thread.conversation_reopen_state,
        messages=thread.messages,
        activity_items=thread.activity_items,
        operation_audit_records=[
            OperationAuditRecord(
                operation_id="operation_1",
                conversation_id="agent_conv_1",
                activity_id="activity_1",
                runtime_run_id="runtime_1",
                operation_name="runtime_search",
                execution_origin="service",
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
        round_summaries=[
            AgentWorkbenchRoundSummaryProjection(
                round_no=1,
                status="completed",
                query_terms=("canonical", "LLM"),
                keyword_query="canonical LLM query",
                raw_candidate_count=5,
                unique_new_count=3,
                newly_scored_count=2,
                resume_quality_comment="canonical coverage summary",
                reflection_summary="canonical LangChain reflection",
            )
        ],
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
        "operation.completed",
        "activity.upserted",
    ]
    operation_event = response.transcriptGroups[0].events[2]
    assert operation_event.payload.kind == "operation"
    assert not operation_event.kind.startswith("tool.")
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
    assert response.thinkingProcess.rounds[0].cards[0].terms == ["canonical", "LLM"]
    assert "canonical coverage summary" in response.thinkingProcess.rounds[0].cards[1].text
    assert "canonical LangChain reflection" in response.thinkingProcess.rounds[0].cards[2].text
    assert "rawPayload" not in serialized
    assert "providerResponse" not in serialized


def test_thinking_process_is_empty_without_canonical_round_summaries() -> None:
    thread = _thread_view()
    response = project_agent_workbench_view(
        AgentWorkbenchProjectionInput(
            conversation_reopen_state=thread.conversation_reopen_state,
            messages=thread.messages,
            runtime_events=[
                RuntimeControlEvent(
                    event_id="runtime_event_raw_1",
                    runtime_run_id="runtime_1",
                    event_seq=1,
                    event_type="runtime_round",
                    stage="round",
                    round_no=1,
                    source_id="liepin",
                    status="running",
                    summary="Raw round must not drive thinkingProcess",
                    payload={"keyword_query": "raw query", "query_terms": ["raw"]},
                    payload_size_bytes=0,
                    created_at=_now(),
                )
            ],
            activity_items=thread.activity_items,
        )
    )

    assert response.thinkingProcess.activeRoundNo is None
    assert response.thinkingProcess.rounds == []


def test_thinking_process_uses_only_canonical_round_summaries() -> None:
    from seektalent_ui.agent_workbench_rounds import AgentWorkbenchRoundSummaryProjection

    thread = _thread_view()
    response = project_agent_workbench_view(
        AgentWorkbenchProjectionInput(
            conversation_reopen_state=thread.conversation_reopen_state,
            messages=thread.messages,
            round_summaries=[
                AgentWorkbenchRoundSummaryProjection(
                    round_no=2,
                    status="completed",
                    query_terms=("canonical",),
                    keyword_query="canonical query",
                    raw_candidate_count=1,
                    unique_new_count=1,
                    newly_scored_count=1,
                    reflection_summary="canonical reflection",
                )
            ],
            runtime_events=[
                RuntimeControlEvent(
                    event_id="runtime_event_raw_1",
                    runtime_run_id="runtime_1",
                    event_seq=1,
                    event_type="runtime_round",
                    stage="round",
                    round_no=1,
                    source_id="liepin",
                    status="running",
                    summary="Raw round must not drive thinkingProcess",
                    payload={"keyword_query": "raw query", "query_terms": ["raw"]},
                    payload_size_bytes=0,
                    created_at=_now(),
                )
            ],
            activity_items=thread.activity_items,
        )
    )

    assert [item.roundNo for item in response.thinkingProcess.rounds] == [2]
    assert response.thinkingProcess.activeRoundNo == 2
    assert response.thinkingProcess.rounds[0].cards[0].text == "canonical query"


def test_projection_loads_round_summaries_before_runtime_event_window_bound() -> None:
    store = _LargeRuntimeStore()
    store.stage_outputs = [
        _public_stage_output(
            output_id="rtout_round_1_query",
            runtime_run_id="runtime_1",
            stage="round_query",
            round_no=1,
            output={"details": {"queryTerms": ["AI agent"], "keywordQuery": "AI agent"}},
        )
    ]

    projection_input = build_agent_workbench_projection_input(
        service=_conversation_agent_service_for_thread(_thread_view()),
        conversation_store=_conversation_store_for_thread(_thread_view()),
        runtime_store=store,
        workbench_store=_empty_workbench_store(),
        conversation_id="agent_conv_1",
        user=_workbench_user(),
    )

    assert [summary.round_no for summary in projection_input.round_summaries] == [1]
    assert len(projection_input.runtime_events) <= 300


def test_strategy_graph_projects_public_round_outputs_with_swimlane_metadata() -> None:
    thread = _thread_view()
    projection_input = build_agent_workbench_projection_input(
        service=_FakeAgentService(thread),
        conversation_store=_FakeConversationStore(),
        runtime_store=_FakeRuntimeStore(
            stage_outputs=[
                _public_stage_output(
                    output_id="rtout_graph_round_query",
                    runtime_run_id="runtime_1",
                    stage="round_query",
                    round_no=1,
                    output={
                        "details": {
                            "queryTerms": ["AI agent"],
                            "keywordQuery": "AI agent platform engineer",
                            "plannedQueries": [
                                {
                                    "sourceKind": "liepin",
                                    "queryRole": "exploit",
                                    "laneType": "primary",
                                    "queryTerms": ["AI agent"],
                                    "keywordQuery": "AI agent platform engineer",
                                }
                            ],
                        }
                    },
                ),
                _public_stage_output(
                    output_id="rtout_graph_source_liepin",
                    runtime_run_id="runtime_1",
                    stage="source_result",
                    round_no=1,
                    source_kind="liepin",
                    output={"counts": {"roundReturned": 5, "roundIdentities": 4}},
                ),
                _public_stage_output(
                    output_id="rtout_graph_feedback",
                    runtime_run_id="runtime_1",
                    stage="feedback",
                    round_no=1,
                    output={
                        "details": {
                            "executedQueries": [
                                {
                                    "sourceKind": "liepin",
                                    "queryRole": "exploit",
                                    "laneType": "primary",
                                    "queryTerms": ["AI agent"],
                                    "keywordQuery": "AI agent platform engineer",
                                }
                            ],
                            "resumeQualityComment": "Public BFF observation.",
                            "reflectionSummary": "Public BFF reflection.",
                        }
                    },
                ),
            ]
        ),
        workbench_store=_FakeWorkbenchStore(),
        conversation_id="agent_conv_1",
        user=_workbench_user(),
    )

    response = project_agent_workbench_view(projection_input)

    nodes = {node.nodeId: node for node in response.strategyGraph.nodes}
    assert "round:1" not in nodes
    assert {node.kind for node in nodes.values()}.isdisjoint({"round"})
    assert {node.stage for node in nodes.values()}.isdisjoint({"round_summary"})
    lane_nodes = [node for node in nodes.values() if node.kind == "lane"]
    assert [(node.roundNo, node.laneType, node.sourceKind) for node in lane_nodes] == [(1, "primary", "liepin")]
    phase_nodes = [node for node in nodes.values() if node.kind == "phase"]
    assert {
        (node.roundNo, node.phase, node.stage, node.sourceKind, node.status)
        for node in phase_nodes
    } >= {
        (1, "query", "round_query", "all", "completed"),
        (1, "source", "source_result", "liepin", "completed"),
        (1, "feedback", "feedback", "all", "completed"),
    }

    projected_events = project_agent_workbench_stream_events(response)
    strategy_event = next(event for event in projected_events if event.kind == "strategyGraph.changed")
    thinking_event = next(event for event in projected_events if event.kind == "thinkingProcess.changed")
    strategy_envelope = build_stream_envelope(
        conversation_id="agent_conv_1",
        seq=1,
        kind=strategy_event.kind,
        payload=strategy_event.payload,
        created_at=strategy_event.created_at,
    )
    thinking_envelope = build_stream_envelope(
        conversation_id="agent_conv_1",
        seq=2,
        kind=thinking_event.kind,
        payload=thinking_event.payload,
        created_at=thinking_event.created_at,
    )

    assert strategy_envelope.payload.graphNodeCount == len(response.strategyGraph.nodes)
    assert strategy_envelope.payload.graphEdgeCount == len(response.strategyGraph.edges)
    assert thinking_envelope.payload.roundNo == 1
    assert thinking_envelope.payload.activeRoundNo == 1
    assert thinking_envelope.payload.status == "completed"


def test_strategy_graph_edges_only_reference_returned_nodes_under_budget() -> None:
    from seektalent_ui.agent_workbench_rounds import (
        AgentWorkbenchQueryPackageProjection,
        AgentWorkbenchRoundStageProjection,
        AgentWorkbenchRoundSummaryProjection,
    )

    thread = _thread_view()
    projection_input = AgentWorkbenchProjectionInput(
        conversation_reopen_state=thread.conversation_reopen_state,
        round_summaries=[
            AgentWorkbenchRoundSummaryProjection(
                round_no=index,
                status="completed",
                planned_queries=(
                    AgentWorkbenchQueryPackageProjection(source_kind="cts", lane_type=f"lane_{index}"),
                ),
                stage_outputs=(
                    AgentWorkbenchRoundStageProjection(stage="round_query", source_kind="cts"),
                    AgentWorkbenchRoundStageProjection(stage="source_result", source_kind="cts"),
                ),
            )
            for index in range(1, 50)
        ],
    )

    response = project_agent_workbench_view(projection_input)
    node_ids = {node.nodeId for node in response.strategyGraph.nodes}

    assert len(response.strategyGraph.nodes) <= 80
    assert len(response.strategyGraph.edges) <= 120
    assert all(edge.fromNodeId in node_ids and edge.toNodeId in node_ids for edge in response.strategyGraph.edges)


def test_strategy_graph_preserves_blocked_and_partial_phase_statuses() -> None:
    thread = _thread_view()
    projection_input = build_agent_workbench_projection_input(
        service=_FakeAgentService(thread),
        conversation_store=_FakeConversationStore(),
        runtime_store=_FakeRuntimeStore(
            stage_outputs=[
                _public_stage_output(
                    output_id="rtout_graph_status_query",
                    runtime_run_id="runtime_1",
                    stage="round_query",
                    round_no=1,
                    output={
                        "details": {
                            "plannedQueries": [
                                {
                                    "sourceKind": "liepin",
                                    "queryRole": "exploit",
                                    "laneType": "primary",
                                    "queryTerms": ["AI agent"],
                                    "keywordQuery": "AI agent platform engineer",
                                },
                                {
                                    "sourceKind": "cts",
                                    "queryRole": "expand",
                                    "laneType": "expansion",
                                    "queryTerms": ["platform"],
                                    "keywordQuery": "platform backend engineer",
                                },
                            ]
                        }
                    },
                ),
                _public_stage_output(
                    output_id="rtout_graph_source_blocked",
                    runtime_run_id="runtime_1",
                    stage="source_result",
                    round_no=1,
                    source_kind="liepin",
                    status="blocked",
                    output={"safeReasonCode": "blocked_backend_unavailable"},
                ),
                _public_stage_output(
                    output_id="rtout_graph_source_partial",
                    runtime_run_id="runtime_1",
                    stage="source_result",
                    round_no=1,
                    source_kind="cts",
                    status="partial",
                    output={"safeReasonCode": "partial_timeout"},
                ),
            ]
        ),
        workbench_store=_FakeWorkbenchStore(),
        conversation_id="agent_conv_1",
        user=_workbench_user(),
    )

    response = project_agent_workbench_view(projection_input)
    nodes = {node.nodeId: node for node in response.strategyGraph.nodes}

    assert nodes["round:1:lane:liepin:primary"].status == "blocked"
    assert nodes["round:1:lane:cts:expansion"].status == "partial"
    assert nodes["round:1:phase:source_result:liepin"].status == "blocked"
    assert nodes["round:1:phase:source_result:cts"].status == "partial"


def test_strategy_graph_node_status_rejects_unknown_values() -> None:
    with pytest.raises(ValidationError):
        AgentWorkbenchGraphNodeResponse(
            nodeId="round:1:phase:round_query:all",
            kind="phase",
            label="Round query",
            summary="summary",
            status="unknown-runtime-status",
        )


def test_strategy_graph_node_kind_rejects_round_summary_nodes() -> None:
    with pytest.raises(ValidationError):
        AgentWorkbenchGraphNodeResponse(
            nodeId="round:1",
            kind="round",
            label="Round 1",
            summary="summary",
            roundNo=1,
            phase="round",
            stage="round_summary",
            status="completed",
        )


def test_round_reducer_combines_public_stage_outputs_without_raw_fallback() -> None:
    from seektalent_ui.agent_workbench_rounds import round_summaries_from_stage_outputs

    summaries = round_summaries_from_stage_outputs(
        [
            _public_stage_output(
                output_id="rtout_r1_query",
                stage="round_query",
                round_no=1,
                output={
                    "details": {
                        "queryTerms": ["AI agent"],
                        "keywordQuery": "AI agent platform engineer",
                        "plannedQueries": [
                            {
                                "queryRole": "exploit",
                                "laneType": "primary",
                                "queryTerms": ["AI agent"],
                                "keywordQuery": "AI agent platform engineer",
                            }
                        ],
                    }
                },
            ),
            _public_stage_output(
                output_id="rtout_r1_source_cts",
                stage="source_result",
                round_no=1,
                source_kind="cts",
                output={"counts": {"roundReturned": 0, "roundIdentities": 0}},
            ),
            _public_stage_output(
                output_id="rtout_r1_merge",
                stage="merge",
                round_no=1,
                output={"counts": {"roundUniqueIdentities": 0, "mergedIdentities": 7}},
            ),
            _public_stage_output(
                output_id="rtout_r1_scoring",
                stage="scoring",
                round_no=1,
                output={"counts": {"roundIdentities": 0, "topPoolCount": 4}},
            ),
            _public_stage_output(
                output_id="rtout_r1_feedback",
                stage="feedback",
                round_no=1,
                output={
                    "details": {
                        "executedQueries": [
                            {
                                "queryRole": "exploit",
                                "laneType": "primary",
                                "queryTerms": ["AI agent"],
                                "keywordQuery": "AI agent platform engineer",
                            }
                        ],
                        "resumeQualityComment": "本轮候选人偏平台工程。",
                        "reflectionSummary": "继续保留 agent 平台关键词。",
                        "suggestedAddFilterFields": ["location"],
                    }
                },
            ),
            _public_stage_output(
                output_id="rtout_final",
                stage="finalization",
                round_no=None,
                output={"counts": {"selectedIdentityCount": 3}},
            ),
        ],
        expected_runtime_run_id="runtime_run_reducer",
    )

    assert [summary.round_no for summary in summaries] == [1]
    summary = summaries[0]
    assert summary.status == "completed"
    assert summary.raw_candidate_count == 0
    assert summary.unique_new_count == 0
    assert summary.total_merged_identity_count == 7
    assert summary.newly_scored_count == 0
    assert summary.top_pool_count == 4
    assert summary.planned_queries[0].keyword_query == "AI agent platform engineer"
    assert summary.executed_queries[0].query_terms == ("AI agent",)
    assert summary.suggested_add_filter_fields == ("location",)


def test_round_reducer_rejects_public_stage_output_metadata_mismatch() -> None:
    from seektalent_ui.agent_workbench_rounds import AgentWorkbenchProjectionError, round_summaries_from_stage_outputs

    output = _public_stage_output(
        output_id="rtout_bad",
        stage="round_query",
        round_no=1,
        output={"stage": "feedback"},
    )

    with pytest.raises(AgentWorkbenchProjectionError) as exc_info:
        round_summaries_from_stage_outputs([output], expected_runtime_run_id="runtime_run_reducer")

    assert exc_info.value.reason_code == "workbench_round_output_metadata_mismatch"


def test_round_reducer_rejects_row_schema_version_mismatch() -> None:
    from seektalent_ui.agent_workbench_rounds import AgentWorkbenchProjectionError, round_summaries_from_stage_outputs

    output = _public_stage_output(
        output_id="rtout_bad_schema",
        stage="round_query",
        round_no=1,
        schema_version="debug-output/v1",
        output={},
    )

    with pytest.raises(AgentWorkbenchProjectionError) as exc_info:
        round_summaries_from_stage_outputs([output], expected_runtime_run_id="runtime_run_reducer")

    assert exc_info.value.reason_code == "workbench_round_output_metadata_mismatch"


def test_deterministic_finalization_projection_is_run_level_only() -> None:
    from seektalent_ui.agent_workbench_rounds import deterministic_finalization_from_stage_outputs

    finalization = deterministic_finalization_from_stage_outputs(
        [
            _public_stage_output(
                output_id="rtout_final",
                stage="finalization",
                round_no=None,
                output={
                    "counts": {"selectedIdentityCount": 3},
                    "details": {
                        "finalizationRevision": 2,
                        "finalizationReasonCode": "target_satisfied",
                    },
                },
            )
        ],
        expected_runtime_run_id="runtime_run_reducer",
    )

    assert finalization is not None
    assert finalization.selected_identity_count == 3
    assert finalization.revision == 2
    assert finalization.reason_code == "target_satisfied"


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
    assert response.conversation.runtimeRunId == intent_runtime_run_id


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
    assert projection.requirement_draft_missing is False
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


def test_agent_workbench_keeps_existing_reason_code_when_requirement_draft_is_missing() -> None:
    thread = _thread_view(latest_draft_revision_id="reqdraft_missing")
    thread = thread.model_copy(
        update={
            "conversation_reopen_state": thread.conversation_reopen_state.model_copy(
                update={"reason_code": "runtime_event_gap_detected"}
            )
        }
    )
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
    assert response.requirementDraft is None
    assert response.reasonCode == "runtime_event_gap_detected"


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


def test_requirement_review_transcript_records_invalid_historical_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        "seektalent_ui.agent_workbench_response.record_requirement_snapshot_invalid",
        lambda *, error_count, correlation_id=None: calls.append(error_count),
    )
    thread = _thread_view()
    thread = thread.model_copy(
        update={
            "messages": [
                thread.messages[0],
                thread.messages[1].model_copy(
                    update={
                        "payload": {
                            "requirementDraft": {"draftRevisionId": "draft_1"},
                            "requirementDraftSnapshot": {"draftRevisionId": "draft_1"},
                        }
                    }
                ),
            ]
        }
    )

    response = project_agent_workbench_view(
        AgentWorkbenchProjectionInput(
            conversation_reopen_state=thread.conversation_reopen_state,
            messages=thread.messages,
            activity_items=thread.activity_items,
        )
    )

    requirement_message = next(
        message for message in response.messages if message.messageType == "requirement_review"
    )
    assert requirement_message.payload.requirementDraftSnapshot is None
    assert len(calls) == 1
    assert calls[0] > 0


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
        operation_audit_records=[],
        context_compactions=[],
        runtime_events=[],
        source_connections=[],
        candidates=[
            AgentWorkbenchCandidateSummaryResponse(
                candidateId=f"candidate_{index}",
                rank=index + 1,
                displayName=f"Candidate {index}",
                headline="Backend Engineer",
                company="Example",
                location="Shanghai",
                education="本科",
                experienceYears=8,
                sourceKinds=["cts"],
                matchScore=80,
                matchSummary="safe summary",
                status="new",
                detailAvailability="available",
                accessState="allowed",
                evidenceLevel="detail",
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
    graph_node_ids = {node.nodeId for node in response.strategyGraph.nodes}
    assert all(edge.fromNodeId in graph_node_ids and edge.toNodeId in graph_node_ids for edge in response.strategyGraph.edges)
    assert len(response.thinkingProcess.rounds) <= 50
    assert len(response.candidates) <= 10
    assert len(response.model_dump_json()) <= 750_000


def test_workbench_view_projects_recent_runtime_event_window() -> None:
    thread = _thread_view()
    response = project_agent_workbench_view(
        AgentWorkbenchProjectionInput(
            conversation_reopen_state=thread.conversation_reopen_state,
            messages=[],
            activity_items=[],
            runtime_events=[
                RuntimeControlEvent(
                    event_id=f"runtime_event_{event_seq}",
                    runtime_run_id="runtime_1",
                    event_seq=event_seq,
                    event_type="runtime_round",
                    stage="round",
                    round_no=event_seq,
                    source_id="liepin",
                    status="completed",
                    summary=f"Round {event_seq}",
                    payload={"query_terms": ["AI agent"]},
                    payload_size_bytes=0,
                    created_at=_now(),
                )
                for event_seq in range(1, 321)
            ],
        )
    )

    transcript_events = [event for group in response.transcriptGroups for event in group.events]
    transcript_item_ids = {event.itemId for event in transcript_events}
    assert len(transcript_events) == 300
    assert "runtime_event_20" not in transcript_item_ids
    assert "runtime_event_21" in transcript_item_ids
    assert "runtime_event_320" in transcript_item_ids


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


def test_thinking_process_stream_event_uses_projected_canonical_round() -> None:
    response = _conversation_response_with_thinking_round(
        round_no=2,
        keyword_text="canonical query",
        observation_text="1 candidate",
        reflection_text="canonical reflection",
    )

    events = project_agent_workbench_stream_events(response)
    thinking_events = [event for event in events if event.kind == "thinkingProcess.changed"]

    assert len(thinking_events) == 1
    assert thinking_events[0].source_seq == 2
    assert thinking_events[0].payload.itemId == "round:2"
    assert thinking_events[0].payload.summary == "canonical reflection"


def test_runtime_finalization_stream_event_is_separate_from_final_summary() -> None:
    response = _conversation_response_with_thinking_round(
        round_no=2,
        keyword_text="canonical query",
        observation_text="1 candidate",
        reflection_text="canonical reflection",
        runtime_finalization=AgentWorkbenchRunFinalizationResponse(
            selectedIdentityCount=3,
            revision=4,
            reasonCode="target_satisfied",
            status="completed",
        ),
        final_summary=AgentWorkbenchFinalSummaryResponse(
            summaryId="summary_1",
            text="Conversation Agent natural-language summary",
        ),
    )

    events = project_agent_workbench_stream_events(response)

    assert [event.kind for event in events].count("finalSummary.updated") == 1
    runtime_events = [event for event in events if event.kind == "runtimeFinalization.changed"]
    assert len(runtime_events) == 1
    assert runtime_events[0].source_seq == 4
    assert runtime_events[0].payload.itemId == "runtimeFinalization"
    assert runtime_events[0].payload.summary == "target_satisfied"


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
        runtime_store=_FakeRuntimeStore(
            stage_outputs=[
                _public_stage_output(
                    output_id="rtout_stream_round_query",
                    runtime_run_id="runtime_1",
                    stage="round_query",
                    round_no=1,
                    output={"details": {"queryTerms": ["AI agent"], "keywordQuery": "AI agent"}},
                ),
                _public_stage_output(
                    output_id="rtout_stream_feedback",
                    runtime_run_id="runtime_1",
                    stage="feedback",
                    round_no=1,
                    output={"details": {"reflectionSummary": "canonical reflection"}},
                ),
            ]
        ),
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


def test_completed_workbench_response_contains_final_transcript_item_and_summary_metadata() -> None:
    thread = _thread_view(final_summary_id="summary_1")
    final_message = TranscriptMessage(
        message_id="msg_final_1",
        conversation_id="agent_conv_1",
        message_seq=3,
        role="assistant",
        message_type="final_summary",
        text="Final shortlist ready.",
        payload={"summaryId": "summary_1"},
        source_runtime_run_id="runtime_1",
        created_at="2026-06-12T12:05:00+00:00",
    )
    thread = thread.model_copy(
        update={
            "conversation_reopen_state": thread.conversation_reopen_state.model_copy(
                update={"status": "completed", "latest_message_seq": 3}
            ),
            "messages": [*thread.messages, final_message],
        }
    )
    projection_input = build_agent_workbench_projection_input(
        service=_FakeAgentService(thread),
        conversation_store=_FakeConversationStore(),
        runtime_store=_FakeRuntimeStore(),
        workbench_store=_FakeWorkbenchStore(),
        conversation_id="agent_conv_1",
        user=_workbench_user(),
    )

    response = project_agent_workbench_view(projection_input)
    payload = response.model_dump(mode="json")
    transcript_events = [event for group in response.transcriptGroups for event in group.events]

    assert payload["finalSummary"] == {"summaryId": "summary_1", "text": "Final shortlist ready."}
    assert payload["messages"][-1]["messageId"] == "msg_final_1"
    assert payload["messages"][-1]["messageType"] == "final_summary"
    assert any(
        event.itemId == "msg_final_1" and event.kind == "message.completed"
        for event in transcript_events
    )


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


def test_agent_workbench_top_pool_is_ranked_and_capped_at_ten() -> None:
    thread = _thread_view(final_summary_id="summary_1")
    workbench_store = _TopPoolWorkbenchStore()

    projection_input = build_agent_workbench_projection_input(
        service=_FakeAgentService(thread),
        conversation_store=_FakeConversationStore(),
        runtime_store=_FakeRuntimeStore(),
        workbench_store=workbench_store,
        conversation_id="agent_conv_1",
        user=_workbench_user(),
    )
    response = project_agent_workbench_view(projection_input)

    assert workbench_store.list_limits == [10]
    assert len(response.candidates) == 10
    assert [candidate.rank for candidate in response.candidates] == list(range(1, 11))
    assert response.candidates[0].candidateId == "candidate_00"
    assert response.candidates[0].displayName == "Candidate 00"
    assert response.candidates[0].company == "Acme"
    assert response.candidates[0].education == "本科"
    assert response.candidates[0].experienceYears == 10
    assert response.candidates[0].sourceKinds == ["cts", "liepin"]
    assert response.candidates[0].matchScore == 100
    assert response.candidates[0].detailAvailability == "available"
    assert response.candidates[0].accessState == "allowed"
    assert response.candidates[0].evidenceLevel in {"detail", "final"}


def test_agent_workbench_top_pool_prefers_runtime_final_order() -> None:
    thread = _thread_view(final_summary_id="summary_1")
    workbench_store = _RuntimeFinalTopPoolWorkbenchStore()

    projection_input = build_agent_workbench_projection_input(
        service=_FakeAgentService(thread),
        conversation_store=_FakeConversationStore(),
        runtime_store=_FakeRuntimeStore(),
        workbench_store=workbench_store,
        conversation_id="agent_conv_1",
        user=_workbench_user(),
    )
    response = project_agent_workbench_view(projection_input)

    assert workbench_store.list_limits == []
    assert [candidate.candidateId for candidate in response.candidates[:3]] == [
        "candidate_05",
        "candidate_01",
        "candidate_03",
    ]
    assert [candidate.rank for candidate in response.candidates[:3]] == [1, 2, 3]


def test_workbench_candidate_detail_route_returns_safe_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from seektalent_ui import agent_workbench_routes

    client = _client(tmp_path)
    candidate = _candidate_review_item(0, evidence_level="detail")
    candidate.raw_payload = {"provider_url": "https://provider.example/resume", "cookie": "secret"}
    candidate.provider_action = {"authorization": "Bearer secret"}
    candidate.prompt = "internal prompt"

    class FakeDetailStore:
        def get_candidate_review_item(self, *, user: WorkbenchUser, session_id: str, review_item_id: str):
            assert user.user_id == "user_local"
            assert session_id == "session_1"
            assert review_item_id == "candidate_00"
            return candidate

    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]
    client.app.state.agent_conversation_service.store.link_runtime_run(
        conversation_id=conversation_id,
        runtime_run_id="runtime_detail_route_1",
        workbench_session_id="session_1",
        approved_requirement_revision_id="reqapproved_detail_route_1",
        linked_at="2026-06-09T00:00:20.000000Z",
    )
    monkeypatch.setattr(agent_workbench_routes, "get_workbench_store", lambda request: FakeDetailStore())

    with caplog.at_level("INFO", logger="seektalent_ui.workbench_observability"):
        response = client.get(f"/api/agent/workbench/conversations/{conversation_id}/candidates/candidate_00/detail")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["candidateId"] == "candidate_00"
    assert body["displayName"] == "Candidate 00"
    assert body["detailAvailability"] == "available"
    assert body["accessState"] == "allowed"
    assert body["evidenceLevel"] == "detail"
    assert body["sections"]
    assert any(record.event_name == "candidate_detail_read" for record in caplog.records)
    serialized = json.dumps(body, ensure_ascii=False).casefold()
    for forbidden in [
        "provider_action",
        "cookie",
        "authorization",
        "bearer ",
        "provider_url",
        "raw_payload",
        "artifact",
        "prompt",
        "resume_raw",
    ]:
        assert forbidden not in serialized


def test_workbench_candidate_detail_route_sets_no_store_on_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seektalent_ui import agent_workbench_routes

    client = _client(tmp_path)
    _ensure_local_actor(client)
    missing_session_conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]
    missing_session = client.get(
        f"/api/agent/workbench/conversations/{missing_session_conversation_id}/candidates/candidate_00/detail"
    )

    denied_candidate = _candidate_review_item(0, evidence_level="detail")
    denied_candidate.access_state = "denied"

    class FakeDeniedStore:
        def get_candidate_review_item(self, *, user: WorkbenchUser, session_id: str, review_item_id: str):
            return denied_candidate

    denied_conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]
    client.app.state.agent_conversation_service.store.link_runtime_run(
        conversation_id=denied_conversation_id,
        runtime_run_id="runtime_detail_route_2",
        workbench_session_id="session_1",
        approved_requirement_revision_id="reqapproved_detail_route_2",
        linked_at="2026-06-09T00:00:20.000000Z",
    )
    monkeypatch.setattr(agent_workbench_routes, "get_workbench_store", lambda request: FakeDeniedStore())
    denied = client.get(f"/api/agent/workbench/conversations/{denied_conversation_id}/candidates/candidate_00/detail")

    assert missing_session.status_code == 404
    assert missing_session.headers["cache-control"] == "no-store"
    assert denied.status_code == 403
    assert denied.headers["cache-control"] == "no-store"


def test_candidate_detail_projection_omits_sections_when_access_is_not_allowed() -> None:
    approval_required = candidate_detail_response_from_review_item(
        _candidate_review_item(1, evidence_level="card")
    )
    denied_candidate = _candidate_review_item(2, evidence_level="detail")
    denied_candidate.access_state = "denied"

    denied = candidate_detail_response_from_review_item(denied_candidate)

    assert approval_required.accessState == "approval_required"
    assert approval_required.detailAvailability == "approval_required"
    assert approval_required.reasonCode == "candidate_detail_requires_approval"
    assert approval_required.sections == []
    assert approval_required.evidence == []
    assert denied.accessState == "denied"
    assert denied.detailAvailability == "unavailable"
    assert denied.reasonCode == "permission_denied"
    assert denied.sections == []
    assert denied.evidence == []


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

    assert projection_input.operation_audit_records[0].operation_id == "operation_1"
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

    assert runtime_store.calls[0] == (150, 300)
    assert len(projection_input.runtime_events) == 300
    assert projection_input.runtime_events[0].event_seq == 151
    assert projection_input.runtime_events[-1].event_seq == 450


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


def test_agent_workbench_create_route_returns_bff_snapshot(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)

    response = client.post(
        "/api/agent/workbench/conversations",
        json={"title": "资深 Python 后端"},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["schemaVersion"] == "agent.workbench.view.v2"
    assert payload["conversation"]["conversationId"].startswith("agent_conv_")
    assert payload["conversation"]["title"] == "资深 Python 后端"
    assert payload["strategyGraph"]["nodes"] == []
    assert payload["streamCursor"]["latestStreamSeq"] == 0


def test_agent_workbench_route_does_not_fallback_to_raw_activity_runtime_state(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    created = client.post(
        "/api/agent/workbench/conversations",
        json={"title": "资深 Python 后端"},
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["conversation"]["conversationId"]
    service = client.app.state.agent_conversation_service
    service.store.link_runtime_run(
        conversation_id=conversation_id,
        runtime_run_id="runtime_run_missing_projection",
        workbench_session_id="workbench_session_missing_projection",
        approved_requirement_revision_id="reqapproved_missing_projection",
        linked_at="2026-06-22T00:00:00.000000Z",
    )
    service.store.upsert_activity_item(
        activity_id="activity_missing_projection",
        conversation_id=conversation_id,
        activity_key="runtime_run_missing_projection:round:7",
        activity_type="runtime_event",
        status="running",
        title="第 7 轮检索",
        summary="raw activity should not synthesize runtime state",
        payload={"stage": "raw_activity_stage", "round_no": 7},
        source_runtime_run_id="runtime_run_missing_projection",
        source_event_id_latest="rtevt_missing_projection",
        source_event_seq_start=1,
        source_event_seq_latest=7,
        created_at="2026-06-22T00:00:01.000000Z",
        updated_at="2026-06-22T00:00:01.000000Z",
    )

    response = client.get(f"/api/agent/workbench/conversations/{conversation_id}")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["conversation"]["runtimeRunId"] == "runtime_run_missing_projection"
    assert payload["runtime"] is None
    assert payload["reasonCode"] == "runtime_projection_unavailable"
    serialized_runtime = json.dumps(payload.get("runtime"), ensure_ascii=False)
    assert "raw_activity_stage" not in serialized_runtime
    assert "currentRound" not in serialized_runtime


def test_agent_workbench_routes_project_real_runtime_outputs_into_snapshot_and_events(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    created = client.post(
        "/api/agent/workbench/conversations",
        json={"title": "资深 Python 后端"},
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["conversation"]["conversationId"]
    service = client.app.state.agent_conversation_service
    runtime_store = service.service_action_adapter.runtime_store
    approved = save_approved_requirement(
        runtime_store,
        conversation_id=conversation_id,
        approved_requirement_revision_id="reqapproved_workbench_projection_1",
    )
    runtime_store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_workbench_projection_1",
            agent_conversation_id=conversation_id,
            workbench_session_id="workbench_session_projection_1",
            approved_requirement_revision_id=approved.approved_requirement_revision_id,
            status="running",
            current_stage="round",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["liepin", "cts"],
            stop_reason_code=None,
            created_at="2026-06-22T00:00:00.000000Z",
            updated_at="2026-06-22T00:00:00.000000Z",
            completed_at=None,
        )
    )
    service.store.link_runtime_run(
        conversation_id=conversation_id,
        runtime_run_id="runtime_run_workbench_projection_1",
        workbench_session_id="workbench_session_projection_1",
        approved_requirement_revision_id=approved.approved_requirement_revision_id,
        linked_at="2026-06-22T00:00:00.000000Z",
    )
    _save_public_stage_output(
        runtime_store,
        output_id="rtout_route_graph_query",
        runtime_run_id="runtime_run_workbench_projection_1",
        stage="round_query",
        round_no=1,
        output={
            "details": {
                "queryTerms": ["AI agent"],
                "keywordQuery": "AI agent platform engineer",
                "plannedQueries": [
                    {
                        "sourceKind": "liepin",
                        "queryRole": "exploit",
                        "laneType": "primary",
                        "queryTerms": ["AI agent"],
                        "keywordQuery": "AI agent platform engineer",
                    },
                    {
                        "sourceKind": "cts",
                        "queryRole": "expand",
                        "laneType": "expansion",
                        "queryTerms": ["platform"],
                        "keywordQuery": "platform backend engineer",
                    },
                ],
            }
        },
    )
    _save_public_stage_output(
        runtime_store,
        output_id="rtout_route_graph_liepin",
        runtime_run_id="runtime_run_workbench_projection_1",
        stage="source_result",
        round_no=1,
        source_kind="liepin",
        status="blocked",
        output={
            "counts": {"roundReturned": 0, "roundIdentities": 0},
            "safeReasonCode": "blocked_backend_unavailable",
        },
    )
    _save_public_stage_output(
        runtime_store,
        output_id="rtout_route_graph_cts",
        runtime_run_id="runtime_run_workbench_projection_1",
        stage="source_result",
        round_no=1,
        source_kind="cts",
        status="partial",
        output={
            "counts": {"roundReturned": 3, "roundIdentities": 2},
            "safeReasonCode": "partial_timeout",
        },
    )

    snapshot = client.get(f"/api/agent/workbench/conversations/{conversation_id}")
    assert snapshot.status_code == 200, snapshot.text
    payload = snapshot.json()
    nodes = {node["nodeId"]: node for node in payload["strategyGraph"]["nodes"]}
    assert "round:1" not in nodes
    assert all(node["kind"] != "round" for node in nodes.values())
    assert nodes["round:1:lane:liepin:primary"]["status"] == "blocked"
    assert nodes["round:1:lane:cts:expansion"]["status"] == "partial"
    assert nodes["round:1:phase:source_result:liepin"]["status"] == "blocked"
    assert nodes["round:1:phase:source_result:cts"]["status"] == "partial"
    thinking_round = payload["thinkingProcess"]["rounds"][0]
    assert thinking_round["roundNo"] == 1
    assert thinking_round["status"] == "blocked"
    assert "AI agent platform engineer" in json.dumps(thinking_round, ensure_ascii=False)

    events = client.get(f"/api/agent/workbench/conversations/{conversation_id}/events?after_seq=0")
    assert events.status_code == 200, events.text
    event_payload = events.json()
    event_by_kind = {event["kind"]: event for event in event_payload["events"]}
    assert event_by_kind["strategyGraph.changed"]["payload"]["graphNodeCount"] == len(nodes)
    assert event_by_kind["thinkingProcess.changed"]["payload"]["activeRoundNo"] == 1
    assert event_by_kind["thinkingProcess.changed"]["payload"]["status"] == "blocked"


def test_workbench_message_action_returns_refreshed_workbench_view(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)

    body = _start_workbench_from_jd_and_process_extraction(
        client,
        idempotency_key="submit-jd-workbench-view-1",
    )

    assert body["schemaVersion"] == "agent.workbench.view.v2"
    assert body["requirementDraft"]["sections"][0]["displayName"] == "必须满足"
    assert body["messages"][0]["payload"]["kind"] == "job_request"
    assert "confirm_requirements" in body["pendingActions"]["allowed"]


def test_workbench_message_action_uses_agent_write_rate_limiter(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.app.state.agent_rate_limiter = LocalAgentRateLimiter(max_writes_per_minute=0)
    _ensure_local_actor(client)

    response = client.post(
        "/api/agent/workbench/conversations/from-jd",
        json={
            "jobDescription": "需要 Python 平台负责人，负责 API 与平台工程。",
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
    submitted = _start_workbench_from_jd_and_process_extraction(
        client,
        idempotency_key="submit-jd-workbench-operation-1",
    )
    conversation_id = submitted["conversation"]["conversationId"]
    draft = submitted["requirementDraft"]
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


def test_workbench_requirement_operations_reject_request_amplification(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    submitted = _start_workbench_from_jd_and_process_extraction(
        client,
        idempotency_key="submit-jd-workbench-operation-cap-1",
    )
    conversation_id = submitted["conversation"]["conversationId"]
    draft = submitted["requirementDraft"]
    first_item = draft["sections"][0]["items"][0]

    response = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/requirements/operations",
        json={
            "draftRevisionId": draft["draftRevisionId"],
            "expectedDraftRevisionId": draft["draftRevisionId"],
            "operations": [
                {"op": "set_selected", "itemId": first_item["itemId"], "selected": False}
                for _ in range(51)
            ],
            "idempotencyKey": "deselect-workbench-operation-cap-1",
        },
    )

    assert response.status_code == 400, response.text
    problem = response.json()
    assert problem["reasonCode"] == "agent_request_invalid"
    assert any(region["field"] == "operations" for region in problem["regions"])


def test_workbench_requirement_operations_reject_oversized_edit_text(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    submitted = _start_workbench_from_jd_and_process_extraction(
        client,
        idempotency_key="submit-jd-workbench-operation-text-cap-1",
    )
    conversation_id = submitted["conversation"]["conversationId"]
    draft = submitted["requirementDraft"]
    first_item = draft["sections"][0]["items"][0]

    response = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/requirements/operations",
        json={
            "draftRevisionId": draft["draftRevisionId"],
            "expectedDraftRevisionId": draft["draftRevisionId"],
            "operations": [{"op": "edit_text", "itemId": first_item["itemId"], "text": "x" * 2001}],
            "idempotencyKey": "edit-workbench-operation-text-cap-1",
        },
    )

    assert response.status_code == 400, response.text
    problem = response.json()
    assert problem["reasonCode"] == "agent_request_invalid"
    assert any(region["field"] == "operations.0.text" for region in problem["regions"])


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
    assert {region["field"] for region in problem["regions"]} >= {
        "messageType",
        "jobTitle",
        "sourceIds",
    }


def test_agent_message_route_rejects_explicit_source_alias_conflict(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]

    response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={
            "messageType": "submitJd",
            "jobTitle": "Python 平台负责人",
            "text": "需要 Python API、平台工程和检索排序。",
            "notes": "优先 toB SaaS",
            "sourceKinds": ["liepin"],
            "sourceIds": ["cts"],
            "idempotencyKey": "submit-jd-source-alias-conflict-1",
        },
    )

    assert response.status_code == 400, response.text
    assert response.json()["reasonCode"] == "job_request_source_kinds_conflict"


def test_workbench_from_jd_route_does_not_offload_sync_submit_jd_work_to_threadpool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import seektalent_ui.agent_workbench_routes as routes

    calls: list[str] = []

    async def fake_run_in_threadpool(fn: object, *args: object, **kwargs: object) -> object:
        calls.append(getattr(fn, "__name__", repr(fn)))
        return fn(*args, **kwargs)

    monkeypatch.setattr(routes, "run_in_threadpool", fake_run_in_threadpool)
    client = _client_with_runtime(tmp_path, BlockingRequirementRuntime)
    _ensure_local_actor(client)

    response = client.post(
        "/api/agent/workbench/conversations/from-jd",
        json={
            "jobDescription": "需要 Python 平台负责人，负责 API 与平台工程。",
            "jobTitle": "Python 平台负责人",
            "notes": None,
            "sourceKinds": ["cts"],
            "idempotencyKey": "from-jd-workbench-no-threadpool-1",
        },
    )

    assert response.status_code == 201, response.text
    assert calls == []


def test_workbench_requirement_operation_stale_revision_returns_problem_details(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    submitted = _start_workbench_from_jd_and_process_extraction(
        client,
        idempotency_key="submit-jd-workbench-stale-operation-1",
    )
    conversation_id = submitted["conversation"]["conversationId"]
    draft = submitted["requirementDraft"]
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
    submitted = _start_workbench_from_jd_and_process_extraction(
        client,
        idempotency_key="submit-jd-workbench-empty-amend-1",
    )
    conversation_id = submitted["conversation"]["conversationId"]
    draft_id = submitted["requirementDraft"]["draftRevisionId"]

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


def test_agent_workbench_confirm_route_wakes_outbox_runner_and_starts_runtime_once(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    try:
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
        assert payload["conversation"]["workflowStartReasonCode"] is None
        service = client.app.state.agent_conversation_service
        runtime_store = service.service_action_adapter.runtime_store
        assert runtime_store is not None
        run_intent_id = f"wts:default:{conversation_id}:{draft_id}"
        runtime_run = _wait_for_runtime_run(runtime_store, run_intent_id=run_intent_id)
        assert runtime_run is not None
        assert _runtime_run_count_for_intent(runtime_store.path, run_intent_id) == 1

        projected_payload = _wait_for_projected_runtime_run(
            client,
            conversation_id=conversation_id,
            runtime_run_id=runtime_run.runtime_run_id,
        )
        assert projected_payload["conversation"]["runtimeRunId"] == runtime_run.runtime_run_id
        assert projected_payload["conversation"]["workflowStartState"] == "running"
        assert projected_payload["runtime"]["runtimeRunId"] == runtime_run.runtime_run_id
    finally:
        client.app.state.workflow_start_outbox_runner.stop()


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
    assert refreshed_summary["workflowStartState"] in {"queued", "starting", "running"}
    if refreshed_summary["workflowStartState"] == "running":
        assert refreshed_summary["runtimeRunId"]
    assert refreshed_summary["workflowStartReasonCode"] is None


def test_agent_workbench_workflow_command_route_handles_commands_and_problem_details(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]
    service = client.app.state.agent_conversation_service
    runtime_store = service.service_action_adapter.runtime_store
    approved = save_approved_requirement(
        runtime_store,
        conversation_id=conversation_id,
        approved_requirement_revision_id="reqapproved_workbench_command_1",
    )
    runtime_store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_workbench_command_1",
            agent_conversation_id=conversation_id,
            workbench_session_id="workbench_session_workbench_command_1",
            approved_requirement_revision_id=approved.approved_requirement_revision_id,
            status="running",
            current_stage="runtime",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-06-09T00:00:20.000000Z",
            updated_at="2026-06-09T00:00:20.000000Z",
            completed_at=None,
        )
    )
    service.store.link_runtime_run(
        conversation_id=conversation_id,
        runtime_run_id="runtime_run_workbench_command_1",
        workbench_session_id="workbench_session_workbench_command_1",
        approved_requirement_revision_id=approved.approved_requirement_revision_id,
        linked_at="2026-06-09T00:00:20.000000Z",
    )

    command = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/workflow/commands",
        json={"commandType": "pause", "idempotencyKey": "pause-workbench-route-1"},
    )
    missing_text = client.post(
        f"/api/agent/workbench/conversations/{conversation_id}/workflow/commands",
        json={"commandType": "nextRoundRequirement", "idempotencyKey": "next-round-workbench-missing-text-1"},
    )

    assert command.status_code == 200, command.text
    assert command.json()["conversation"]["runtimeRunId"] == "runtime_run_workbench_command_1"
    assert missing_text.status_code == 400, missing_text.text
    problem = missing_text.json()
    assert problem["reasonCode"] == "agent_free_text_empty"
    assert problem["status"] == 400


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


def _public_stage_output(
    *,
    output_id: str,
    stage: str,
    round_no: int | None,
    output: dict[str, object],
    runtime_run_id: str = "runtime_run_reducer",
    source_kind: str | None = None,
    status: str = "completed",
    schema_version: str = "runtime-public-stage-output/v1",
) -> RuntimeStageOutput:
    payload = {
        "schemaVersion": "runtime-public-stage-output/v1",
        "publicEventSchemaVersion": "runtime_public_event_v1",
        "stage": stage,
        "roundNo": round_no,
        "sourceKind": source_kind,
        "status": status,
        "counts": {},
        "details": {},
        "safeReasonCode": None,
    }
    payload.update(output)
    node_key = source_kind or ""
    round_key = round_no if round_no is not None else -1
    return RuntimeStageOutput(
        output_id=output_id,
        runtime_run_id=runtime_run_id,
        stage=stage,
        node_id=source_kind,
        node_key=node_key,
        round_no=round_no,
        round_key=round_key,
        output_kind=f"runtime_public_{stage}",
        schema_version=schema_version,
        output=payload,
        payload_hash="hash",
        payload_size_bytes=1,
        source_event_id=None,
        source_checkpoint_id=None,
        artifact_ref_id=None,
        created_at=f"2026-06-22T00:00:{len(output_id):02d}.000000Z",
    )


def _save_public_stage_output(
    runtime_store: RuntimeControlStore,
    *,
    output_id: str,
    runtime_run_id: str,
    stage: str,
    round_no: int | None,
    output: dict[str, object],
    source_kind: str | None = None,
    status: str = "completed",
) -> None:
    payload = {
        "schemaVersion": "runtime-public-stage-output/v1",
        "publicEventSchemaVersion": "runtime_public_event_v1",
        "stage": stage,
        "roundNo": round_no,
        "sourceKind": source_kind,
        "status": status,
        "counts": {},
        "details": {},
        "safeReasonCode": None,
    }
    payload.update(output)
    runtime_store.save_stage_output(
        RuntimeStageOutputInput(
            output_id=output_id,
            runtime_run_id=runtime_run_id,
            stage=stage,
            node_id=source_kind,
            round_no=round_no,
            output_kind=f"runtime_public_{stage}",
            schema_version="runtime-public-stage-output/v1",
            output=payload,
            source_event_id=None,
            source_checkpoint_id=None,
            artifact_ref_id=None,
            created_at=f"2026-06-22T00:01:{len(output_id):02d}.000000Z",
        )
    )


def _conversation_response_with_thinking_round(
    *,
    round_no: int,
    keyword_text: str,
    observation_text: str,
    reflection_text: str,
    runtime_finalization: AgentWorkbenchRunFinalizationResponse | None = None,
    final_summary: AgentWorkbenchFinalSummaryResponse | None = None,
) -> AgentWorkbenchConversationResponse:
    return AgentWorkbenchConversationResponse(
        conversation=AgentWorkbenchConversationSummaryResponse(
            conversationId="agent_conv_1",
            title="Find backend engineers",
            status="running",
            isArchived=False,
            updatedAt=_now(),
        ),
        strategyGraph=AgentWorkbenchStrategyGraphResponse(nodes=[], edges=[]),
        thinkingProcess=AgentWorkbenchThinkingProcessResponse(
            activeRoundNo=round_no,
            rounds=[
                AgentWorkbenchThinkingProcessRoundResponse(
                    roundNo=round_no,
                    status="completed",
                    cards=[
                        AgentWorkbenchThinkingProcessCardResponse(title="关键词", text=keyword_text),
                        AgentWorkbenchThinkingProcessCardResponse(title="observation", text=observation_text),
                        AgentWorkbenchThinkingProcessCardResponse(title="反思和下一轮变更", text=reflection_text),
                    ],
                )
            ],
        ),
        pendingActions=AgentWorkbenchPendingActionsResponse(),
        streamCursor=AgentWorkbenchStreamCursorResponse(),
        runtimeFinalization=runtime_finalization,
        finalSummary=final_summary,
    )


class _FakeAgentService:
    def __init__(self, thread: ConversationThreadView) -> None:
        self.thread = thread
        self.workflow_start_intent_store = _FakeWorkflowStartIntentStore()

    def reopen_conversation(self, *, conversation_id: str, owner_user_id: str, workspace_id: str) -> ConversationThreadView:
        assert conversation_id == "agent_conv_1"
        assert owner_user_id == "user_admin_example_com"
        assert workspace_id == "default"
        return self.thread


class _FakeWorkflowStartIntentStore:
    def get_latest_for_conversation(self, *, workspace_id: str, conversation_id: str) -> None:
        assert workspace_id == "default"
        assert conversation_id == "agent_conv_1"
        return None


class _FakeConversationStore:
    def list_operation_audits(self, *, conversation_id: str):
        assert conversation_id == "agent_conv_1"
        return [
            OperationAuditRecord(
                operation_id="operation_1",
                conversation_id="agent_conv_1",
                activity_id="activity_1",
                runtime_run_id="runtime_1",
                operation_name="runtime_search",
                execution_origin="service",
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
    def __init__(
        self,
        requirement_drafts: dict[str, object] | None = None,
        stage_outputs: list[RuntimeStageOutput] | None = None,
    ) -> None:
        self.requirement_drafts = requirement_drafts or {}
        self.stage_outputs = stage_outputs or []
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

    def list_stage_outputs(
        self,
        *,
        runtime_run_id: str,
        stage: str | None = None,
        round_no: int | None = None,
        output_kind: str | None = None,
    ) -> list[RuntimeStageOutput]:
        assert runtime_run_id == "runtime_1"
        outputs = self.stage_outputs
        if stage is not None:
            outputs = [output for output in outputs if output.stage == stage]
        if round_no is not None:
            outputs = [output for output in outputs if output.round_no == round_no]
        if output_kind is not None:
            outputs = [output for output in outputs if output.output_kind == output_kind]
        return outputs

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
            current_round=450,
            latest_event_seq=450,
            source_ids=["liepin"],
            created_at=_now(),
            updated_at=_now(),
        )

    def list_events(self, *, runtime_run_id: str, after_seq: int, limit: int) -> RuntimeControlEventPage:
        assert runtime_run_id == "runtime_1"
        self.calls.append((after_seq, limit))
        if after_seq >= 450:
            return RuntimeControlEventPage(events=[], next_cursor=after_seq)
        last_event_seq = min(after_seq + limit, 450)
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
            for event_seq in range(after_seq + 1, last_event_seq + 1)
        ]
        return RuntimeControlEventPage(events=events, next_cursor=last_event_seq)


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

    def list_candidate_review_items(self, *, user: WorkbenchUser, session_id: str, limit: int | None = None):
        assert session_id == "session_1"
        items = [
            WorkbenchCandidateReviewItem(
                review_item_id="candidate_1",
                session_id="session_1",
                status="new",
                note="",
                display_name="Ada",
                title="Backend Engineer",
                company="Example",
                location="Shanghai",
                education="本科",
                experience_years=8,
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
        return items[:limit] if limit is not None else items

    def list_runtime_final_top_review_items(self, *, user: WorkbenchUser, session_id: str):
        assert session_id == "session_1"
        return None

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


class _TopPoolWorkbenchStore(_FakeWorkbenchStore):
    def __init__(self) -> None:
        super().__init__()
        self.list_limits: list[int | None] = []

    def list_candidate_review_items(self, *, user: WorkbenchUser, session_id: str, limit: int | None = None):
        assert session_id == "session_1"
        self.list_limits.append(limit)
        items = [_candidate_review_item(index) for index in range(12)]
        return items[:limit] if limit is not None else items


class _RuntimeFinalTopPoolWorkbenchStore(_TopPoolWorkbenchStore):
    def list_runtime_final_top_review_items(self, *, user: WorkbenchUser, session_id: str):
        assert session_id == "session_1"
        return 7, [
            _candidate_review_item(5),
            _candidate_review_item(1),
            _candidate_review_item(3),
        ]


def _candidate_review_item(index: int, *, evidence_level: str | None = None) -> SimpleNamespace:
    level = evidence_level or ("final" if index % 3 == 0 else "detail")
    return SimpleNamespace(
        review_item_id=f"candidate_{index:02d}",
        session_id="session_1",
        status="new",
        note="",
        display_name=f"Candidate {index:02d}",
        title="Backend Engineer",
        company="Acme",
        location="Shanghai",
        education="本科",
        experience_years=10 + index,
        summary=f"Safe candidate summary {index}.",
        aggregate_score=100 - index,
        fit_bucket="strong",
        why_selected="Strong backend fit.",
        source_round=1,
        source_badges=["cts", "liepin"],
        evidence_level=level,
        matched_must_haves=["Python", "平台"],
        matched_preferences=["LLM"],
        missing_risks=[],
        strengths=["Distributed systems"],
        weaknesses=[],
        evidence=[
            SimpleNamespace(source_kind="cts", evidence_level="final"),
            SimpleNamespace(source_kind="liepin", evidence_level=level),
        ],
        created_at=_now(),
        updated_at=_now(),
    )


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


def _client_with_runtime(tmp_path: Path, runtime_factory: type) -> TestClient:
    settings = make_settings(
        workspace_root=str(tmp_path),
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
    )
    return TestClient(
        create_app(settings=settings, runtime_factory=runtime_factory),
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    )


def _start_workbench_from_jd_and_process_extraction(
    client: TestClient,
    *,
    idempotency_key: str,
    job_description: str = "需要 Python 平台负责人，负责 API 与平台工程。",
    job_title: str = "Python 平台负责人",
    notes: str | None = None,
    source_kinds: list[str] | None = None,
) -> dict[str, Any]:
    accepted = client.post(
        "/api/agent/workbench/conversations/from-jd",
        json={
            "jobDescription": job_description,
            "jobTitle": job_title,
            "notes": notes,
            "sourceKinds": source_kinds or ["cts"],
            "idempotencyKey": idempotency_key,
        },
    )
    assert accepted.status_code == 201, accepted.text
    assert client.app.state.requirement_extraction_outbox_runner.run_once() == 1
    conversation_id = accepted.json()["conversation"]["conversationId"]
    snapshot = client.get(f"/api/agent/workbench/conversations/{conversation_id}")
    assert snapshot.status_code == 200, snapshot.text
    return snapshot.json()


def _transcript_text(payload: dict[str, Any]) -> str:
    return json.dumps(
        {
            "messages": payload.get("messages"),
            "transcriptGroups": payload.get("transcriptGroups"),
        },
        ensure_ascii=False,
    )


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0])


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


def _wait_for_runtime_run(
    runtime_store: RuntimeControlStore,
    *,
    run_intent_id: str,
    timeout_seconds: float = 2.0,
) -> RuntimeRunRecord | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        run = runtime_store.get_run_by_run_intent_id(run_intent_id)
        if run is not None:
            return run
        time.sleep(0.02)
    return runtime_store.get_run_by_run_intent_id(run_intent_id)


def _wait_for_projected_runtime_run(
    client: TestClient,
    *,
    conversation_id: str,
    runtime_run_id: str,
    timeout_seconds: float = 2.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_payload: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        projected = client.get(f"/api/agent/workbench/conversations/{conversation_id}")
        assert projected.status_code == 200, projected.text
        last_payload = projected.json()
        if (
            last_payload["conversation"]["runtimeRunId"] == runtime_run_id
            and last_payload["runtime"] is not None
            and last_payload["runtime"]["runtimeRunId"] == runtime_run_id
        ):
            return last_payload
        time.sleep(0.02)
    assert last_payload is not None
    return last_payload


def _runtime_run_count_for_intent(db_path: Path, run_intent_id: str) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM runtime_control_runs
            WHERE run_intent_id = ?
            """,
            (run_intent_id,),
        ).fetchone()
    return int(row[0])


def _now() -> str:
    return datetime(2026, 6, 12, 12, 0, tzinfo=UTC).isoformat()
