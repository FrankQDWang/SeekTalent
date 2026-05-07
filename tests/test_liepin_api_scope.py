from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import anyio
from fastapi.testclient import TestClient

from seektalent.providers.liepin.store import LiepinStore
from seektalent_ui.server import LiepinScope, RunRegistry, _event_generator, create_app
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
        "candidatePersonalInfoProcessingBasis": "candidate recruiting lawful basis",
        "personalInformationProcessor": "Acme Recruiting",
        "operatorAuditOwner": "Ops Owner",
        "accountHolderAuthorized": True,
        "humanInitiatedRecruiting": True,
        "allowedPurposes": ["search"],
        "retentionPolicy": "run_debug_short",
        "deletionSlaDays": 14,
        "deletionPath": "settings/delete",
        "rawPayloadAccessScope": "run_only",
        "rawDetailRetentionAllowedAfterDebug": False,
        "fixtureExportAllowed": False,
        "policyRef": "policy-v1",
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
        json={"complianceGateRef": gate_ref},
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
    gate_ref = _create_gate(client)
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


def test_connection_create_rejects_external_provider_account_identity_hints(tmp_path: Path) -> None:
    client = _client(tmp_path)
    gate_ref = _create_gate(client)

    response = client.post(
        "/api/liepin/connections",
        headers=API_HEADERS,
        json={"complianceGateRef": gate_ref, "providerAccountIdentityHint": "raw-account-id"},
    )

    assert response.status_code == 400
    assert "providerAccountIdentityHint" in response.text


def test_compliance_gate_bind_and_verify_api_flow_is_scoped_to_connection(tmp_path: Path) -> None:
    client = _client(tmp_path)
    gate_ref = _create_gate(client)
    connection_id = _create_connection(client, gate_ref)

    pending_verify = client.post(
        f"/api/liepin/compliance-gates/{gate_ref}/verify",
        headers=API_HEADERS,
        json={"connectionId": connection_id},
    )
    assert pending_verify.status_code == 403
    assert "connection_not_bound" in pending_verify.text

    bind_response = client.post(
        f"/api/liepin/compliance-gates/{gate_ref}/bind-account",
        headers=API_HEADERS,
        json={"connectionId": connection_id},
    )
    assert bind_response.status_code == 200
    assert bind_response.json() == {"gateRef": gate_ref, "status": "approved"}
    assert "subject" not in bind_response.text.lower()
    assert connection_id not in bind_response.text

    verify_response = client.post(
        f"/api/liepin/compliance-gates/{gate_ref}/verify",
        headers=API_HEADERS,
        json={"connectionId": connection_id},
    )
    assert verify_response.status_code == 200
    assert verify_response.json() == {"gateRef": gate_ref, "status": "approved"}
    assert "subject" not in verify_response.text.lower()
    assert connection_id not in verify_response.text

    other_workspace = {**API_HEADERS, "X-Workspace-ID": "workspace-b"}
    wrong_scope_bind = client.post(
        f"/api/liepin/compliance-gates/{gate_ref}/bind-account",
        headers=other_workspace,
        json={"connectionId": connection_id},
    )
    assert wrong_scope_bind.status_code == 404


def test_compliance_gate_bind_api_rejects_connection_for_different_gate(tmp_path: Path) -> None:
    client = _client(tmp_path)
    gate_a = _create_gate(client)
    gate_b = _create_gate(client, policyRef="policy-v2")
    connection_for_b = _create_connection(client, gate_b)

    mismatch = client.post(
        f"/api/liepin/compliance-gates/{gate_a}/bind-account",
        headers=API_HEADERS,
        json={"connectionId": connection_for_b},
    )

    assert mismatch.status_code == 404
    verify_b = client.post(
        f"/api/liepin/compliance-gates/{gate_b}/verify",
        headers=API_HEADERS,
        json={"connectionId": connection_for_b},
    )
    assert verify_b.status_code == 403
    assert "connection_not_bound" in verify_b.text


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
    store.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        subject_type="connection",
        subject_id=connection_id,
        event_name="stream_end",
        payload={"reason": "test_complete"},
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

    cookie_name_query_token = client.get(f"/api/liepin/connections/{connection_id}/events?liepin_stream_token=abc")
    assert cookie_name_query_token.status_code == 400


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
    store = LiepinStore(tmp_path / "liepin.sqlite3")
    bound_hash = store.bind_connection_account(
        gate_ref=gate_ref,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        connection_id=connection_id,
        secret="local-development",
    )
    assert bound_hash is not None
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
    store.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        subject_type="run",
        subject_id=run_id,
        event_name="stream_end",
        payload={"reason": "test_complete"},
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

    cookie_name_query_token = client.get(f"/api/runs/{run_id}/events?liepin_stream_token=abc")
    assert cookie_name_query_token.status_code == 400


def test_approved_gate_fresh_connection_cannot_verify_or_start_liepin_run(tmp_path: Path) -> None:
    client = _client(tmp_path)
    gate_ref = _create_gate(client)
    bound_connection_id = _create_connection(client, gate_ref)
    bind_response = client.post(
        f"/api/liepin/compliance-gates/{gate_ref}/bind-account",
        headers=API_HEADERS,
        json={"connectionId": bound_connection_id},
    )
    assert bind_response.status_code == 200

    fresh_connection_id = _create_connection(client, gate_ref)
    fresh_connection = client.get(f"/api/liepin/connections/{fresh_connection_id}", headers=API_HEADERS)
    assert fresh_connection.status_code == 200
    assert fresh_connection.json()["status"] == "pending_login"

    verify_response = client.post(
        f"/api/liepin/compliance-gates/{gate_ref}/verify",
        headers=API_HEADERS,
        json={"connectionId": fresh_connection_id},
    )
    assert verify_response.status_code == 403
    assert "connection" in verify_response.text.lower()

    run_response = client.post(
        "/api/runs",
        headers=API_HEADERS,
        json={
            "provider": "liepin",
            "connectionId": fresh_connection_id,
            "complianceGateRef": gate_ref,
            "jobTitle": "Python Engineer",
            "jdText": "JD",
        },
    )
    assert run_response.status_code == 403
    assert "connection" in run_response.text.lower()


def test_liepin_run_rejects_connection_and_gate_mismatch_with_same_account_hash(tmp_path: Path) -> None:
    client = _client(tmp_path)
    gate_a = _create_gate(client)
    connection_a = _create_connection(client, gate_a)
    bind_a = client.post(
        f"/api/liepin/compliance-gates/{gate_a}/bind-account",
        headers=API_HEADERS,
        json={"connectionId": connection_a},
    )
    assert bind_a.status_code == 200

    store = LiepinStore(tmp_path / "liepin.sqlite3")
    connection_a_row = store.get_connection(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        connection_id=connection_a,
    )
    assert connection_a_row is not None
    gate_b = store.create_compliance_gate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        gate=store.get_compliance_gate(
            gate_ref=gate_a,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_id="actor-a",
        ).model_copy(
            update={
                "policy_ref": "policy-v2",
                "provider_account_hash": connection_a_row.provider_account_hash,
                "status": "approved",
            }
        ),
        purpose="search",
    )

    response = client.post(
        "/api/runs",
        headers=API_HEADERS,
        json={
            "provider": "liepin",
            "connectionId": connection_a,
            "complianceGateRef": gate_b,
            "jobTitle": "Python Engineer",
            "jdText": "JD",
        },
    )

    assert response.status_code == 403
    assert "connection" in response.text.lower()
    assert store.iter_events_after(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        subject_type="run",
        subject_id=response.json().get("runId", "missing-run"),
        after_sequence=0,
    ) == []


def test_idle_event_generator_keeps_stream_open_without_busy_loop(tmp_path: Path) -> None:
    store = LiepinStore(tmp_path / "liepin.sqlite3")

    class FakeRequest:
        app = SimpleNamespace(state=SimpleNamespace())

        async def is_disconnected(self) -> bool:
            return False

    async def assert_idle_stream_waits() -> None:
        generator = _event_generator(
            request=FakeRequest(),
            store=store,
            scope=LiepinScope(tenant_id="tenant-a", workspace_id="workspace-a", actor_id="actor-a"),
            subject_type="run",
            subject_id="run-a",
            after_sequence=0,
        )
        with anyio.move_on_after(0.6) as cancel_scope:
            try:
                await generator.__anext__()
            except StopAsyncIteration as exc:
                raise AssertionError("idle SSE generator stopped before client disconnect") from exc
        assert cancel_scope.cancel_called
        await generator.aclose()

    anyio.run(assert_idle_stream_waits)
