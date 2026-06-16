from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from seektalent_ui.network_guard import build_network_guard, render_startup_diagnostics, require_allowed_bind
from seektalent_ui.server import create_app
from tests.settings_factory import make_settings


def _client(tmp_path, *, allowed_hosts: set[str]) -> TestClient:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    guard = build_network_guard(
        bind_host="0.0.0.0",
        port=8011,
        lan_enabled=True,
        allowed_hosts=allowed_hosts,
    )
    return TestClient(
        create_app(settings=settings, network_guard=guard),
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
    assert allowed.status_code == 200

    create_session = client.post(
        "/api/workbench/sessions",
        headers={"Host": "evil.example"},
        json={"jobTitle": "Engineer", "jdText": "JD"},
    )
    assert create_session.status_code == 403


def test_host_guard_rejects_unknown_hosts_for_liepin_routes(tmp_path) -> None:
    client = _client(tmp_path, allowed_hosts={"recruiting.internal"})
    headers = {
        "X-SeekTalent-API-Key": "local-development-liepin-api-token",
        "X-Tenant-ID": "tenant-a",
        "X-Workspace-ID": "workspace-a",
        "X-Actor-ID": "actor-a",
    }

    rejected = client.get("/api/liepin/compliance-gates/gate-a", headers={**headers, "Host": "evil.example"})
    allowed = client.get("/api/liepin/compliance-gates/gate-a", headers={**headers, "Host": "recruiting.internal"})

    assert rejected.status_code == 403
    assert allowed.status_code == 404


def test_host_guard_rejects_unknown_hosts_for_packaged_frontend_routes(tmp_path, monkeypatch) -> None:
    frontend_root = tmp_path / "frontend"
    (frontend_root / "_app" / "immutable").mkdir(parents=True)
    (frontend_root / "200.html").write_text("<html>SeekTalent Workbench</html>", encoding="utf-8")
    monkeypatch.setattr("seektalent_ui.server.package_frontend_dir", lambda: frontend_root)
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    guard = build_network_guard(
        bind_host="0.0.0.0",
        port=8011,
        lan_enabled=True,
        allowed_hosts={"recruiting.internal"},
    )
    client = TestClient(
        create_app(settings=settings, network_guard=guard, serve_frontend=True),
        base_url="http://recruiting.internal",
        client=("203.0.113.10", 50000),
    )

    rejected_root = client.get("/", headers={"Host": "evil.example"})
    rejected_spa = client.get("/sessions/session-1", headers={"Host": "evil.example"})
    allowed_spa = client.get("/sessions/session-1", headers={"Host": "recruiting.internal"})

    assert rejected_root.status_code == 403
    assert rejected_spa.status_code == 403
    assert allowed_spa.status_code == 200


def test_http_lan_local_actor_can_use_workbench_when_host_allowed(tmp_path) -> None:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    guard = build_network_guard(bind_host="0.0.0.0", port=8011, lan_enabled=True, allowed_hosts={"recruiting.internal"})
    app = create_app(settings=settings, network_guard=guard)
    remote_client = TestClient(
        app,
        base_url="http://recruiting.internal",
        client=("203.0.113.10", 50000),
        headers={"Host": "recruiting.internal"},
    )

    created = remote_client.post(
        "/api/workbench/sessions",
        json={"jobTitle": "Engineer", "jdText": "Own APIs and data stores."},
    )

    assert created.status_code == 201, created.text
    assert "set-cookie" not in created.headers


def test_origin_guard_rejects_unconfigured_origin_for_cookie_mutation(tmp_path) -> None:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    guard = build_network_guard(bind_host="0.0.0.0", port=8011, lan_enabled=True, allowed_hosts={"recruiting.internal"})
    app = create_app(settings=settings, network_guard=guard)
    remote_client = TestClient(
        app,
        base_url="http://recruiting.internal",
        client=("203.0.113.10", 50000),
        headers={"Host": "recruiting.internal"},
    )

    rejected = remote_client.post(
        "/api/workbench/sessions",
        headers={"Origin": "http://evil.example"},
        json={"jobTitle": "Engineer", "jdText": "Own APIs and data stores."},
    )

    assert rejected.status_code == 403


def test_loopback_guard_allows_default_vite_dev_origin(tmp_path) -> None:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    guard = build_network_guard(bind_host="127.0.0.1", port=8011, lan_enabled=False)
    client = TestClient(
        create_app(settings=settings, network_guard=guard),
        base_url="http://127.0.0.1:8011",
        client=("127.0.0.1", 50000),
    )

    response = client.post(
        "/api/workbench/sessions",
        headers={"Origin": "http://127.0.0.1:5176"},
        json={"jobTitle": "Engineer", "jdText": "Own APIs and data stores."},
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
    app = create_app(settings=settings, network_guard=guard)
    remote_client = TestClient(
        app,
        base_url="http://recruiting.internal",
        client=("203.0.113.10", 50000),
        headers={"Host": "recruiting.internal"},
    )

    response = remote_client.post(
        "/api/workbench/sessions",
        headers={"Origin": "http://ui.internal"},
        json={"jobTitle": "Engineer", "jdText": "Own APIs and data stores."},
    )

    assert response.status_code == 201
    assert response.headers["access-control-allow-origin"] == "http://ui.internal"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_workbench_cors_preflight_allows_put_for_requirement_updates(tmp_path) -> None:
    guard = build_network_guard(
        bind_host="0.0.0.0",
        port=8011,
        lan_enabled=True,
        allowed_hosts={"recruiting.internal"},
        allowed_origins={"http://ui.internal"},
    )
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    client = TestClient(
        create_app(settings=settings, network_guard=guard),
        base_url="http://recruiting.internal",
        client=("203.0.113.10", 50000),
        headers={"Host": "recruiting.internal"},
    )

    response = client.options(
        "/api/workbench/sessions/session-a/requirements",
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
