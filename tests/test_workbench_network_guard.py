from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from seektalent_ui.network_guard import build_network_guard, render_startup_diagnostics, require_allowed_bind
from seektalent_ui.server import RunRegistry, create_app
from tests.settings_factory import make_settings


CSRF_COOKIE_NAME = "seektalent_workbench_csrf"


def _client(tmp_path, *, allowed_hosts: set[str]) -> TestClient:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    guard = build_network_guard(
        bind_host="0.0.0.0",
        port=8011,
        lan_enabled=True,
        allowed_hosts=allowed_hosts,
    )
    return TestClient(
        create_app(RunRegistry(settings), settings=settings, network_guard=guard),
        base_url="http://recruiting.internal",
        client=("203.0.113.10", 50000),
    )


def test_non_loopback_bind_requires_explicit_lan_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEEKTALENT_UI_LAN", raising=False)

    with pytest.raises(ValueError, match="requires --lan"):
        require_allowed_bind("0.0.0.0", lan_flag=False)

    require_allowed_bind("127.0.0.1", lan_flag=False)
    require_allowed_bind("0.0.0.0", lan_flag=True)

    monkeypatch.setenv("SEEKTALENT_UI_LAN", "1")
    require_allowed_bind("0.0.0.0", lan_flag=False)


def test_host_guard_rejects_unknown_hosts_for_workbench_routes(tmp_path) -> None:
    client = _client(tmp_path, allowed_hosts={"recruiting.internal"})

    rejected = client.get("/api/workbench/settings", headers={"Host": "evil.example"})
    assert rejected.status_code == 403

    allowed = client.get("/api/workbench/settings", headers={"Host": "recruiting.internal"})
    assert allowed.status_code == 401

    legacy_api = client.post("/api/runs", headers={"Host": "evil.example"}, json={"jobTitle": "Engineer", "jdText": "JD"})
    assert legacy_api.status_code != 403


def test_http_lan_login_cookie_can_authenticate_when_host_allowed(tmp_path) -> None:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    guard = build_network_guard(bind_host="0.0.0.0", port=8011, lan_enabled=True, allowed_hosts={"recruiting.internal"})
    app = create_app(RunRegistry(settings), settings=settings, network_guard=guard)
    local_client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))
    remote_client = TestClient(
        app,
        base_url="http://recruiting.internal",
        client=("203.0.113.10", 50000),
        headers={"Host": "recruiting.internal"},
    )

    bootstrap = local_client.post(
        "/api/auth/bootstrap",
        json={"email": "admin@example.com", "password": "correct horse", "displayName": "Admin User"},
    )
    assert bootstrap.status_code == 201

    login = remote_client.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct horse"})
    assert login.status_code == 204
    assert "Secure" not in login.headers["set-cookie"]

    me = remote_client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "admin@example.com"


def test_origin_guard_rejects_unconfigured_origin_for_cookie_mutation(tmp_path) -> None:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    guard = build_network_guard(bind_host="0.0.0.0", port=8011, lan_enabled=True, allowed_hosts={"recruiting.internal"})
    app = create_app(RunRegistry(settings), settings=settings, network_guard=guard)
    local_client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))
    remote_client = TestClient(
        app,
        base_url="http://recruiting.internal",
        client=("203.0.113.10", 50000),
        headers={"Host": "recruiting.internal"},
    )
    assert local_client.post(
        "/api/auth/bootstrap",
        json={"email": "admin@example.com", "password": "correct horse", "displayName": "Admin User"},
    ).status_code == 201
    assert remote_client.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct horse"}).status_code == 204
    csrf_token = remote_client.cookies.get(CSRF_COOKIE_NAME)
    assert csrf_token is not None

    rejected = remote_client.post(
        "/api/workbench/sessions",
        headers={"Origin": "http://evil.example", "X-CSRF-Token": csrf_token},
        json={"jobTitle": "Engineer", "jdText": "Own APIs and data stores."},
    )

    assert rejected.status_code == 403


def test_loopback_guard_allows_default_vite_dev_origin(tmp_path) -> None:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    guard = build_network_guard(bind_host="127.0.0.1", port=8011, lan_enabled=False)
    client = TestClient(
        create_app(RunRegistry(settings), settings=settings, network_guard=guard),
        base_url="http://127.0.0.1:8011",
        client=("127.0.0.1", 50000),
    )

    response = client.post(
        "/api/auth/bootstrap",
        headers={"Origin": "http://127.0.0.1:5176"},
        json={"email": "admin@example.com", "password": "correct horse", "displayName": "Admin User"},
    )

    assert response.status_code == 201
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5176"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_allowed_origin_gets_credentialed_cors_headers(tmp_path) -> None:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    guard = build_network_guard(
        bind_host="0.0.0.0",
        port=8011,
        lan_enabled=True,
        allowed_hosts={"recruiting.internal"},
        allowed_origins={"http://ui.internal"},
    )
    app = create_app(RunRegistry(settings), settings=settings, network_guard=guard)
    local_client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))
    remote_client = TestClient(
        app,
        base_url="http://recruiting.internal",
        client=("203.0.113.10", 50000),
        headers={"Host": "recruiting.internal"},
    )
    assert local_client.post(
        "/api/auth/bootstrap",
        json={"email": "admin@example.com", "password": "correct horse", "displayName": "Admin User"},
    ).status_code == 201
    assert remote_client.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct horse"}).status_code == 204
    csrf_token = remote_client.cookies.get(CSRF_COOKIE_NAME)
    assert csrf_token is not None

    response = remote_client.post(
        "/api/workbench/sessions",
        headers={"Origin": "http://ui.internal", "X-CSRF-Token": csrf_token},
        json={"jobTitle": "Engineer", "jdText": "Own APIs and data stores."},
    )

    assert response.status_code == 201
    assert response.headers["access-control-allow-origin"] == "http://ui.internal"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_workbench_cors_preflight_allows_put_for_triage_updates(tmp_path) -> None:
    guard = build_network_guard(
        bind_host="0.0.0.0",
        port=8011,
        lan_enabled=True,
        allowed_hosts={"recruiting.internal"},
        allowed_origins={"http://ui.internal"},
    )
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    client = TestClient(
        create_app(RunRegistry(settings), settings=settings, network_guard=guard),
        base_url="http://recruiting.internal",
        client=("203.0.113.10", 50000),
        headers={"Host": "recruiting.internal"},
    )

    response = client.options(
        "/api/workbench/sessions/session-a/triage",
        headers={"Origin": "http://ui.internal", "Access-Control-Request-Method": "PUT"},
    )

    assert response.status_code == 204
    assert "PUT" in response.headers["access-control-allow-methods"]


def test_startup_diagnostics_include_bind_hosts_and_cookie_posture() -> None:
    guard = build_network_guard(
        bind_host="0.0.0.0",
        port=8011,
        lan_enabled=True,
        allowed_hosts={"recruiting.internal"},
        allowed_origins={"http://ui.internal"},
    )

    diagnostics = render_startup_diagnostics(guard)

    assert "0.0.0.0:8011" in diagnostics
    assert "recruiting.internal" in diagnostics
    assert "http://ui.internal" in diagnostics
    assert "HTTP cookies are not Secure" in diagnostics
    assert "trusted proxy headers ignored" in diagnostics
