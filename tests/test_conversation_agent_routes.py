from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from seektalent.config import AppSettings
from seektalent.progress import ProgressEvent
from seektalent_runtime_control.models import RuntimeControlEventInput, RuntimeRunRecord
from seektalent_ui.server import create_app
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


class CapturingRouteAgentRunner:
    def __init__(self) -> None:
        self.last_agent = None
        self.calls = 0

    async def run(self, agent, prompt: str) -> object:
        self.calls += 1
        self.last_agent = agent
        return {"final": "已收到。"}


def test_agent_conversation_routes_create_list_reopen_and_submit_jd(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)

    created = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["conversation"]["conversationId"]

    listed = client.get("/api/agent/conversations")
    assert listed.status_code == 200, listed.text
    assert listed.json()["conversations"][0]["conversationId"] == conversation_id

    message = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={
            "messageType": "submitJd",
            "jobTitle": "Python 平台负责人",
            "text": "需要 Python API、平台工程和检索排序。",
            "notes": "优先 toB SaaS",
            "sourceIds": ["cts"],
            "idempotencyKey": "submit-jd-1",
        },
    )
    assert message.status_code == 200, message.text
    payload = message.json()
    assert payload["schemaVersion"] == "agent.conversation.v1"
    assert payload["conversationReopenState"]["status"] == "awaiting_requirement_confirmation"
    assert [section["displayName"] for section in payload["requirementDraft"]["sections"]] == [
        "必须满足",
        "加分项",
        "硬性筛选条件",
        "排除信号",
        "检索关键词",
    ]

    reopened = client.get(f"/api/agent/conversations/{conversation_id}")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["conversationReopenState"]["latestMessageSeq"] == 2


def test_agent_message_user_text_route_uses_memory_recall_before_agent_run(tmp_path: Path) -> None:
    client = _client(tmp_path)
    actor_payload = _ensure_local_actor(client)
    user = actor_payload["user"]
    runner = CapturingRouteAgentRunner()
    client.app.state.agent_conversation_service.agent_runner = runner
    client.app.state.agent_memory_service.store.save_summary(
        summary_id="memsummary_1",
        owner_user_id=user["userId"],
        workspace_id=user["workspaceId"],
        summary_text="v1\n\n用户偏好候选人总结先讲业务匹配，再讲风险。",
        fact_ids=[],
        created_at="2026-06-10T00:00:00.000000Z",
    )
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]

    message = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={
            "messageType": "userText",
            "text": "请帮我组织下一步。",
            "idempotencyKey": "user-text-1",
        },
    )

    assert message.status_code == 200, message.text
    assert runner.last_agent is not None
    assert "[ADVISORY_MEMORY_CONTEXT_START]" in runner.last_agent.instructions
    assert "先讲业务匹配" in runner.last_agent.instructions
    assert message.json()["messages"][-1]["role"] == "assistant"


def test_agent_message_user_text_idempotency_replays_without_second_model_run(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    runner = CapturingRouteAgentRunner()
    client.app.state.agent_conversation_service.agent_runner = runner
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]
    request_body = {
        "messageType": "userText",
        "text": "请帮我组织下一步。",
        "idempotencyKey": "user-text-retry-1",
    }

    first = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json=request_body,
    )
    second = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json=request_body,
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert runner.calls == 1
    assert second.json()["conversationReopenState"]["latestMessageSeq"] == 2
    assert len(second.json()["messages"]) == 2


def test_agent_metadata_routes_rename_archive_and_unarchive(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]

    renamed = client.patch(
        f"/api/agent/conversations/{conversation_id}/title",
        json={"title": "Python 平台负责人"},
    )
    archived = client.post(f"/api/agent/conversations/{conversation_id}/archive")
    listed = client.get("/api/agent/conversations")
    unarchived = client.post(f"/api/agent/conversations/{conversation_id}/unarchive")

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["conversation"]["title"] == "Python 平台负责人"
    assert archived.status_code == 200, archived.text
    assert listed.json()["conversations"] == []
    assert unarchived.status_code == 200, unarchived.text
    assert unarchived.json()["conversation"]["isArchived"] is False


def test_workflow_events_route_returns_ui_ready_activity_lifecycle(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]
    service = client.app.state.agent_conversation_service
    runtime_store = service.tool_adapter.runtime_store
    approved = save_approved_requirement(
        runtime_store,
        conversation_id=conversation_id,
        approved_requirement_revision_id="reqapproved_route_1",
    )
    runtime_store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_route_1",
            agent_conversation_id=conversation_id,
            workbench_session_id="workbench_session_route_1",
            approved_requirement_revision_id=approved.approved_requirement_revision_id,
            status="running",
            current_stage="runtime",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["liepin"],
            stop_reason_code=None,
            created_at="2026-06-09T00:00:20.000000Z",
            updated_at="2026-06-09T00:00:20.000000Z",
            completed_at=None,
        )
    )
    service.store.link_runtime_run(
        conversation_id=conversation_id,
        runtime_run_id="runtime_run_route_1",
        workbench_session_id="workbench_session_route_1",
        approved_requirement_revision_id=approved.approved_requirement_revision_id,
        linked_at="2026-06-09T00:00:20.000000Z",
    )
    runtime_store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_route_dispatch_1",
            runtime_run_id="runtime_run_route_1",
            event_type="runtime_round_source_dispatch",
            stage="source",
            round_no=1,
            source_id="liepin",
            status="started",
            summary="开始分发猎聘来源。",
            payload={"candidateCount": 0},
            workbench_event_global_seq=None,
            created_at="2026-06-09T00:00:21.000000Z",
        )
    )

    response = client.get(
        f"/api/agent/conversations/{conversation_id}/workflow/events",
        params={"runtimeRunId": "runtime_run_route_1", "limit": 10},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schemaVersion"] == "agent.conversation.v1"
    activity = payload["activityItems"][0]
    assert activity["activityType"] == "source_dispatch"
    assert activity["status"] == "started"
    assert activity["sourceRuntimeRunId"] == "runtime_run_route_1"
    assert activity["sourceEventSeqStart"] == 1
    assert activity["sourceEventSeqLatest"] == 1
    assert activity["payload"]["candidateCount"] == 0
    progress = [message for message in payload["messages"] if message["messageType"] == "runtime_progress"]
    assert progress[0]["sourceRuntimeRunId"] == "runtime_run_route_1"
    assert progress[0]["sourceRuntimeEventSeq"] == 1


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
