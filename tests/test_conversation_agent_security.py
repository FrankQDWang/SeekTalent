from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from seektalent_ui.network_guard import build_network_guard
from seektalent_ui.server import create_app
from tests.settings_factory import make_settings
from tests.test_conversation_agent_routes import DeterministicRouteRuntime


def test_agent_write_routes_use_local_actor_without_workbench_auth(tmp_path: Path) -> None:
    client = _client(tmp_path)

    created = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
    )
    assert created.status_code == 201


def test_agent_routes_are_covered_by_host_guard(tmp_path: Path) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
    )
    client = TestClient(
        create_app(
            settings=settings,
            runtime_factory=DeterministicRouteRuntime,
            network_guard=build_network_guard(bind_host="127.0.0.1", port=8787, lan_enabled=False),
        ),
        base_url="http://evil.example",
        client=("127.0.0.1", 50000),
    )

    response = client.get("/api/agent/conversations")

    assert response.status_code == 403


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
