from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from seektalent.config import AppSettings
from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent.progress import ProgressEvent
from seektalent_ui.problem_details import problem_from_conversation_error, problem_from_reason, problem_status_from_reason
from seektalent_ui.server import create_app
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


def test_workbench_errors_preserve_reason_code_and_correlation_id(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.app.state.workbench_store.ensure_local_actor()
    response = client.post(
        "/api/agent/workbench/conversations/agent_conv_1/requirements/confirm",
        json={
            "draftRevisionId": "missing",
            "expectedDraftRevisionId": "missing",
            "idempotencyKey": "confirm-1",
        },
        headers={"X-Correlation-ID": "corr-test"},
    )

    assert response.status_code in {400, 404, 409, 422}
    body = response.json()
    assert body["type"].startswith("https://seektalent.local/problems/")
    assert body["reasonCode"]
    assert body["correlationId"] == "corr-test"
    assert body["instance"].endswith(
        "/api/agent/workbench/conversations/agent_conv_1/requirements/confirm"
    )


def test_workbench_validation_errors_use_problem_details(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.app.state.workbench_store.ensure_local_actor()
    response = client.post(
        "/api/agent/workbench/conversations/agent_conv_1/requirements/confirm",
        json={"draftRevisionId": "", "expectedDraftRevisionId": "", "idempotencyKey": ""},
        headers={"X-Correlation-ID": "corr-validation"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["reasonCode"] == "agent_request_invalid"
    assert body["correlationId"] == "corr-validation"
    assert body["regions"]


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/agent/workbench/conversations/agent_conv_1/messages",
            {
                "messageType": "userText",
                "text": "   \n\t ",
                "idempotencyKey": "message-1",
            },
        ),
        (
            "/api/agent/workbench/conversations/agent_conv_1/messages",
            {
                "messageType": "submitJd",
                "text": "JD 正文",
                "sourceKinds": ["cts"],
                "idempotencyKey": "   ",
            },
        ),
        (
            "/api/agent/workbench/conversations/agent_conv_1/requirements/confirm",
            {
                "draftRevisionId": "   ",
                "expectedDraftRevisionId": "draft_1",
                "idempotencyKey": "confirm-1",
            },
        ),
        (
            "/api/agent/workbench/conversations/agent_conv_1/requirements/operations",
            {
                "draftRevisionId": "draft_1",
                "expectedDraftRevisionId": "draft_1",
                "idempotencyKey": "update-1",
                "operations": [
                    {
                        "op": "edit_text",
                        "itemId": "item_1",
                        "text": "   ",
                    }
                ],
            },
        ),
        (
            "/api/agent/workbench/conversations/agent_conv_1/requirements/amend-from-text",
            {
                "draftRevisionId": "draft_1",
                "expectedDraftRevisionId": "draft_1",
                "text": "   ",
                "idempotencyKey": "amend-1",
            },
        ),
    ],
)
def test_workbench_validation_rejects_whitespace_only_strings(
    tmp_path: Path,
    path: str,
    payload: dict[str, object],
) -> None:
    client = _client(tmp_path)
    client.app.state.workbench_store.ensure_local_actor()

    response = client.post(
        path,
        json=payload,
        headers={"X-Correlation-ID": "corr-blank"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["reasonCode"] == "agent_request_invalid"
    assert body["correlationId"] == "corr-blank"
    assert body["regions"]


def test_problem_details_use_string_detail_and_conflict_status() -> None:
    problem = problem_from_reason(
        reason_code="idempotency_key_conflict",
        status=problem_status_from_reason("idempotency_key_conflict"),
        instance="/api/agent/workbench/conversations/agent_conv_1/requirements/confirm",
        correlation_id="corr-conflict",
    )

    assert problem.status == 409
    assert problem.reasonCode == "idempotency_key_conflict"
    assert isinstance(problem.detail, str)
    assert problem.correlationId == "corr-conflict"


def test_conversation_error_problem_detail_uses_public_reason_text() -> None:
    problem = problem_from_conversation_error(
        exc=ConversationAgentError(
            "agent_request_invalid",
            "raw provider token bearer secret should not leak",
        ),
        request=SimpleNamespace(url=SimpleNamespace(path="/api/agent/workbench/example")),
        correlation_id="corr-safe",
    )

    assert problem.detail == "The Workbench request body is invalid."
    assert "bearer" not in problem.model_dump_json().casefold()


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
