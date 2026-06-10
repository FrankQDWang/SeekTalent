from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from seektalent_ui.network_guard import build_network_guard
from seektalent_ui.server import create_app
from tests.settings_factory import make_settings
from tests.test_conversation_agent_routes import DeterministicRouteRuntime, _bootstrap_and_login, _csrf_header


def test_agent_write_routes_require_authenticated_csrf_session(tmp_path: Path) -> None:
    client = _client(tmp_path)

    unauthenticated = client.post("/api/agent/conversations", json={"title": "资深 Python 后端"})
    assert unauthenticated.status_code == 401

    _bootstrap_and_login(client)
    missing_csrf = client.post("/api/agent/conversations", json={"title": "资深 Python 后端"})
    assert missing_csrf.status_code == 403

    created = client.post(
        "/api/agent/conversations",
        json={"title": "资深 Python 后端"},
        headers=_csrf_header(client),
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
