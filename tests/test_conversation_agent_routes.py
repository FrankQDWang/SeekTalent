from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from seektalent.config import AppSettings
from seektalent.progress import ProgressEvent
from seektalent_runtime_control.models import RuntimeControlEventInput, RuntimeRunRecord
from seektalent_ui.server import create_app
from tests.conversation_agent_test_support import sample_requirement_sheet, save_approved_requirement
from tests.settings_factory import make_settings


class DeterministicRouteRuntime:
    workflow_calls: list[dict[str, object]] = []
    requirement_calls: list[dict[str, object]] = []

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def extract_requirements(
        self,
        *,
        job_title: str | None,
        jd: str,
        notes: str,
        progress_callback=None,
        requirement_cache_scope: str | None = None,
    ) -> object:
        type(self).requirement_calls.append(
            {
                "job_title": job_title,
                "jd": jd,
                "notes": notes,
                "requirement_cache_scope": requirement_cache_scope,
            }
        )
        if callable(progress_callback):
            progress_callback(
                ProgressEvent(
                    type="requirements_completed",
                    message="岗位需求解析完成。",
                    payload={"stage": "requirements"},
                )
            )
        derived_title = job_title if isinstance(job_title, str) and job_title.strip() else "Python 平台负责人"
        return sample_requirement_sheet(job_title=derived_title)

    async def run_async(self, **kwargs: object) -> object:
        type(self).workflow_calls.append(dict(kwargs))
        runtime_start_callback = kwargs.get("runtime_start_callback")
        if callable(runtime_start_callback):
            runtime_start_callback("workflow_runtime_route_1")
        progress_callback = kwargs.get("progress_callback")
        if callable(progress_callback):
            progress_callback(
                ProgressEvent(
                    type="source_result",
                    message="CTS 返回 2 个候选人。",
                    round_no=1,
                    payload={"stage": "source", "sourceId": "cts", "candidateCount": 2},
                )
            )
        return {"status": "completed"}


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


def test_submit_jd_route_accepts_omitted_job_title_and_source_kinds(tmp_path: Path) -> None:
    DeterministicRouteRuntime.requirement_calls = []
    client = _client(tmp_path)
    _ensure_local_actor(client)
    created = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["conversation"]["conversationId"]

    message = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={
            "messageType": "submitJd",
            "text": "需要 Python 平台负责人，负责 API 与平台工程。",
            "notes": "优先华东，接受远程协作",
            "sourceKinds": ["cts", "liepin"],
            "idempotencyKey": "submit-jd-route-source-kinds-1",
        },
    )

    assert message.status_code == 200, message.text
    payload = message.json()
    assert payload["jobRequestRevisionId"]
    assert payload["requirementDraftRevisionId"]
    assert payload["requirementDraft"]["draftRevisionId"] == payload["requirementDraftRevisionId"]
    assert DeterministicRouteRuntime.requirement_calls[0]["job_title"] is None


def test_submit_jd_route_rejects_explicit_source_alias_conflict(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    conversation_id = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    ).json()["conversation"]["conversationId"]

    message = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={
            "messageType": "submitJd",
            "text": "需要 Python 平台负责人，负责 API 与平台工程。",
            "notes": "优先华东，接受远程协作",
            "sourceKinds": ["liepin"],
            "sourceIds": ["cts"],
            "idempotencyKey": "submit-jd-route-source-conflict-1",
        },
    )

    assert message.status_code == 400, message.text
    assert message.json()["reasonCode"] == "job_request_source_kinds_conflict"


def test_workflow_start_route_uses_app_factory_runtime_wrapper(tmp_path: Path) -> None:
    DeterministicRouteRuntime.workflow_calls = []
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
            "sourceIds": ["cts"],
            "idempotencyKey": "submit-jd-wrapper-1",
        },
    )
    draft_id = submitted.json()["requirementDraft"]["draftRevisionId"]
    confirmed = client.post(
        f"/api/agent/conversations/{conversation_id}/requirements/confirm",
        json={"draftRevisionId": draft_id, "baseRevisionId": draft_id, "idempotencyKey": "confirm-wrapper-1"},
    )
    started = client.post(
        f"/api/agent/conversations/{conversation_id}/workflow/start",
        json={
            "jobTitle": "Python 平台负责人",
            "jdText": "需要 Python API、平台工程和检索排序。",
            "notes": "优先 toB SaaS",
            "sourceIds": ["cts"],
        },
    )

    assert confirmed.status_code == 200, confirmed.text
    assert started.status_code == 200, started.text
    runtime_run_id = started.json()["conversationReopenState"]["runtimeRunId"]
    runtime_store = client.app.state.agent_conversation_service.tool_adapter.runtime_store
    executor = client.app.state.agent_conversation_service.tool_adapter.workflow_executor
    claim = runtime_store.claim_next_runnable_run(
        executor_id="route-wrapper-worker",
        claimed_at="2026-06-09T00:01:00.000000Z",
        lease_expires_at="2099-01-01T00:00:00.000000Z",
        runtime_run_id=runtime_run_id,
    )
    assert claim is not None

    asyncio.run(
        executor.execute_claimed_run(
            runtime_run_id=claim.runtime_run.runtime_run_id,
            executor_id=claim.lease.executor_id,
            attempt_no=claim.lease.attempt_no,
        )
    )

    run = runtime_store.get_run(runtime_run_id)
    events = runtime_store.list_events(runtime_run_id=runtime_run_id, after_seq=0, limit=20).events
    assert run.status == "completed"
    assert DeterministicRouteRuntime.workflow_calls
    assert DeterministicRouteRuntime.workflow_calls[0]["job_title"] == "Python 平台负责人"
    assert any(event.event_type == "runtime_source_result" for event in events)


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
        params={"limit": 10},
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

    command = client.post(
        f"/api/agent/conversations/{conversation_id}/workflow/commands",
        json={"commandType": "pause", "idempotencyKey": "pause-route-1"},
    )

    assert command.status_code == 200, command.text
    assert command.json()["messages"][-1]["sourceRuntimeRunId"] == "runtime_run_route_1"


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
