from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from seektalent.providers.liepin.store import LiepinStore
from seektalent_ui.server import RunRegistry, create_app
from tests.settings_factory import make_settings


API_HEADERS = {
    "X-SeekTalent-API-Key": "unit-api-token",
    "X-Tenant-ID": "tenant-a",
    "X-Workspace-ID": "workspace-a",
    "X-Actor-ID": "actor-a",
}


def _client(tmp_path: Path) -> TestClient:
    settings = make_settings(
        liepin_api_token="unit-api-token",
        liepin_connector_db_path=str(tmp_path / "liepin.sqlite3"),
        workspace_root=str(tmp_path),
        mock_cts=True,
    )
    return TestClient(create_app(RunRegistry(settings), settings=settings))


def _gate_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "providerAccountHash": "account-hash-a",
        "candidatePersonalInfoProcessingBasis": "candidate consent or recruiting legitimate interest",
        "personalInformationProcessor": "SeekTalent local operator",
        "operatorAuditOwner": "recruiting-ops",
        "accountHolderAuthorized": True,
        "humanInitiatedRecruiting": True,
        "allowedPurposes": ["search"],
        "retentionPolicy": "run_debug_short",
        "deletionSlaDays": 14,
        "deletionPath": "settings/delete-liepin-run",
        "rawPayloadAccessScope": "run_only",
        "rawDetailRetentionAllowedAfterDebug": False,
        "fixtureExportAllowed": False,
        "policyRef": "policy-live-search-v1",
    }
    payload.update(overrides)
    return payload


def _create_gate(client: TestClient, headers: dict[str, str] | None = None, **overrides: object) -> str:
    response = client.post(
        "/api/liepin/compliance-gates",
        headers=headers or API_HEADERS,
        json=_gate_payload(**overrides),
    )
    assert response.status_code == 201, response.text
    return response.json()["gateRef"]


def _create_connection(client: TestClient, gate_ref: str, headers: dict[str, str] | None = None) -> str:
    response = client.post(
        "/api/liepin/connections",
        headers=headers or API_HEADERS,
        json={"complianceGateRef": gate_ref, "providerAccountIdentityHint": "liepin-user-a"},
    )
    assert response.status_code == 201, response.text
    return response.json()["connectionId"]


def test_liepin_api_requires_local_api_key_and_scope_headers(tmp_path: Path) -> None:
    client = _client(tmp_path)

    missing_token = client.post("/api/liepin/compliance-gates", json=_gate_payload())
    assert missing_token.status_code == 401

    wrong_token_headers = {**API_HEADERS, "X-SeekTalent-API-Key": "wrong"}
    wrong_token = client.post("/api/liepin/compliance-gates", headers=wrong_token_headers, json=_gate_payload())
    assert wrong_token.status_code == 403

    for header_name in ["X-Tenant-ID", "X-Workspace-ID", "X-Actor-ID"]:
        scoped_headers = dict(API_HEADERS)
        scoped_headers.pop(header_name)
        response = client.post("/api/liepin/compliance-gates", headers=scoped_headers, json=_gate_payload())
        assert response.status_code == 400


def test_compliance_gate_and_connection_reads_are_workspace_scoped(tmp_path: Path) -> None:
    client = _client(tmp_path)
    gate_ref = _create_gate(client)
    connection_id = _create_connection(client, gate_ref)
    other_workspace = {**API_HEADERS, "X-Workspace-ID": "workspace-b"}

    gate_read = client.get(f"/api/liepin/compliance-gates/{gate_ref}", headers=other_workspace)
    assert gate_read.status_code == 404

    connection_read = client.get(f"/api/liepin/connections/{connection_id}", headers=other_workspace)
    assert connection_read.status_code == 404


def test_login_url_returns_domain_handoff_without_worker_internals(tmp_path: Path) -> None:
    client = _client(tmp_path)
    gate_ref = _create_gate(client, providerAccountHash=None)
    connection_id = _create_connection(client, gate_ref)

    response = client.post(f"/api/liepin/connections/{connection_id}/login-url", headers=API_HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "connectionId": connection_id,
        "loginUrl": "https://www.liepin.com/",
        "handoffState": "ready_for_browser_login",
    }
    forbidden = json.dumps(payload).lower()
    assert "cdp" not in forbidden
    assert "worker" not in forbidden
    assert "storage" not in forbidden
    assert "token" not in forbidden


def test_connection_stream_token_cookie_and_scoped_sse_events(tmp_path: Path) -> None:
    client = _client(tmp_path)
    gate_ref = _create_gate(client)
    connection_id = _create_connection(client, gate_ref)

    token_response = client.post(f"/api/liepin/connections/{connection_id}/stream-token", headers=API_HEADERS)

    assert token_response.status_code == 204
    assert token_response.content == b""
    set_cookie = token_response.headers["set-cookie"]
    assert "liepin_stream_token=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert f"Path=/api/liepin/connections/{connection_id}/events" in set_cookie
    assert "unit-api-token" not in set_cookie

    store = LiepinStore(tmp_path / "liepin.sqlite3")
    store.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        subject_type="connection",
        subject_id=connection_id,
        event_name="connection_status",
        payload={"status": "login_ready", "connectionId": connection_id},
    )
    store.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        subject_type="connection",
        subject_id=connection_id,
        event_name="connection_status",
        payload={"status": "connected", "connectionId": connection_id},
    )

    event_response = client.get(f"/api/liepin/connections/{connection_id}/events")
    assert event_response.status_code == 200
    assert event_response.headers["content-type"].startswith("text/event-stream")
    assert "id: 1" in event_response.text
    assert "event: connection_status" in event_response.text
    assert "login_ready" in event_response.text

    resumed = client.get(f"/api/liepin/connections/{connection_id}/events", headers={"Last-Event-ID": "1"})
    assert "id: 1" not in resumed.text
    assert "id: 2" in resumed.text
    assert "connected" in resumed.text

    query_token = client.get(f"/api/liepin/connections/{connection_id}/events?stream_token=abc")
    assert query_token.status_code == 400


def test_run_stream_token_events_results_and_liepin_gate_enforcement(tmp_path: Path) -> None:
    client = _client(tmp_path)
    no_gate = client.post(
        "/api/runs",
        headers=API_HEADERS,
        json={"provider": "liepin", "connectionId": "connection-a", "jobTitle": "Python", "jdText": "JD"},
    )
    assert no_gate.status_code == 403

    gate_ref = _create_gate(client)
    connection_id = _create_connection(client, gate_ref)
    run_response = client.post(
        "/api/runs",
        headers=API_HEADERS,
        json={
            "provider": "liepin",
            "connectionId": connection_id,
            "complianceGateRef": gate_ref,
            "jobTitle": "Python Engineer",
            "jdText": "JD",
        },
    )
    assert run_response.status_code == 201, run_response.text
    run_id = run_response.json()["runId"]

    token_response = client.post(f"/api/runs/{run_id}/stream-token", headers=API_HEADERS)
    assert token_response.status_code == 204
    assert f"Path=/api/runs/{run_id}/events" in token_response.headers["set-cookie"]
    assert "streamToken" not in token_response.text

    store = LiepinStore(tmp_path / "liepin.sqlite3")
    store.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        subject_type="run",
        subject_id=run_id,
        event_name="run_started",
        payload={"runId": run_id, "status": "queued"},
    )
    store.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        subject_type="run",
        subject_id=run_id,
        event_name="search_progress",
        payload={"seen": 3, "accepted": 1, "artifactRefs": ["artifact:summary"]},
    )

    events = client.get(f"/api/runs/{run_id}/events")
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "event: run_started" in events.text
    assert "event: search_progress" in events.text
    assert "rawProviderPayload" not in events.text
    assert "workerUrl" not in events.text
    assert "cdp" not in events.text.lower()

    results = client.get(f"/api/runs/{run_id}/results", headers=API_HEADERS)
    assert results.status_code == 200
    assert results.json() == {"runId": run_id, "results": []}

    query_token = client.get(f"/api/runs/{run_id}/events?token=abc")
    assert query_token.status_code == 400
