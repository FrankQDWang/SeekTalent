from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from seektalent_ui.server import create_app
from seektalent_ui.workbench_store import DEFAULT_TENANT_ID
from tests.settings_factory import make_settings


def _app(tmp_path: Path, **settings_overrides):
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True, **settings_overrides)
    return create_app(settings=settings)


def _client(tmp_path: Path, **settings_overrides) -> TestClient:
    return TestClient(_app(tmp_path, **settings_overrides), base_url="http://localhost", client=("127.0.0.1", 50000))


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / ".seektalent" / "workbench.sqlite3"


def _ensure_local_actor(client: TestClient) -> None:
    client.app.state.workbench_store.ensure_local_actor()


def _audit_actions(tmp_path: Path) -> list[str]:
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        rows = conn.execute("SELECT action FROM security_audit_events ORDER BY audit_id ASC").fetchall()
    return [row[0] for row in rows]


def test_source_actions_write_redacted_security_audit_events(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    session = client.post(
        "/api/workbench/sessions",
        json={"jobTitle": "Engineer", "jdText": "Own APIs and data stores."},
    ).json()

    connection = client.post("/api/workbench/source-connections/liepin")
    assert connection.status_code == 201
    policy = client.put(
        f"/api/workbench/sessions/{session['sessionId']}/source-runs/liepin/policy",
        json={"detailOpenMode": "bypass_confirm"},
    )
    assert policy.status_code == 200
    actions = _audit_actions(tmp_path)
    assert "source_connection_created" in actions
    assert "liepin_detail_policy_updated" in actions

    raw_audit = _db_path(tmp_path).read_text(encoding="utf-8", errors="ignore")
    assert "correct horse" not in raw_audit
    assert "seektalent_workbench_session" not in raw_audit
    assert "seektalent_workbench_csrf" not in raw_audit


def test_security_audit_route_is_admin_scoped_and_redacts_metadata(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    store = client.app.state.workbench_store
    user = store.ensure_local_actor()
    store.record_security_audit_event(
        actor_user_id=user.user_id,
        actor_role=user.role,
        workspace_id=user.workspace_id,
        target_type="test",
        target_id="redaction",
        action="redaction_probe",
        result="blocked",
        reason_code="test",
        metadata={
            "Cookie": "secret-cookie",
            "nested": {"Authorization": "Bearer secret"},
            "storage state": "secret-storage",
            "raw provider payload": "secret-payload",
            "auth header": "secret-header",
            "websocket endpoint": "ws://secret",
            "csrfToken": "secret-csrf",
            "X-CSRF-Token": "secret-x-csrf",
            "safeTextOne": "Bearer secret-value",
            "safeTextTwo": "session_token=secret-token",
            "safeTextThree": "password=hunter2",
            "safeTextFour": "api_key=sk-test-secret",
            "inputTokens": 1024,
            "tokenizer_revision": "cl100k_base",
            "redactionState": "raw_provider_payload",
            "candidateSummary": "Playwright automation and CDP Customer Data Platform experience.",
            "safe": "ok",
        },
    )

    response = client.get("/api/workbench/security-audit-events")

    assert response.status_code == 200
    payload = response.json()
    redaction_event = next(event for event in payload["events"] if event["action"] == "redaction_probe")
    serialized = json.dumps(redaction_event, sort_keys=True)
    assert "secret-cookie" not in serialized
    assert "Bearer secret" not in serialized
    assert "secret-storage" not in serialized
    assert "secret-payload" not in serialized
    assert "secret-header" not in serialized
    assert "ws://secret" not in serialized
    assert "secret-csrf" not in serialized
    assert "secret-x-csrf" not in serialized
    assert "secret-value" not in serialized
    assert "secret-token" not in serialized
    assert "hunter2" not in serialized
    assert "sk-test-secret" not in serialized
    assert redaction_event["metadata"]["inputTokens"] == 1024
    assert redaction_event["metadata"]["tokenizer_revision"] == "cl100k_base"
    assert redaction_event["metadata"]["redactionState"] == "raw_provider_payload"
    assert redaction_event["metadata"]["candidateSummary"] == (
        "Playwright automation and CDP Customer Data Platform experience."
    )
    assert redaction_event["metadata"]["safe"] == "ok"
    assert set(redaction_event["metadata"]) == {
        "candidateSummary",
        "inputTokens",
        "redactionState",
        "safe",
        "tokenizer_revision",
    }


def test_workbench_event_route_projects_payload_to_closed_profile(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)
    session = client.post(
        "/api/workbench/sessions",
        json={"jobTitle": "Engineer", "jdText": "Own APIs and data stores."},
    ).json()
    store = client.app.state.workbench_store
    user = store.ensure_local_actor()
    store.append_workbench_event(
        tenant_id=DEFAULT_TENANT_ID,
        workspace_id=user.workspace_id,
        user_id=user.user_id,
        session_id=session["sessionId"],
        source_run_id=None,
        source_kind=None,
        event_name="runtime_probe",
        payload={
            "message": "safe summary",
            "sourceKinds": ["cts", "liepin", "unknown"],
            "score": 92,
            "payload": {"raw": "dropped"},
            "unknown": "dropped",
        },
    )

    response = client.get("/api/workbench/events")

    assert response.status_code == 200
    event = next(item for item in response.json()["events"] if item["eventName"] == "runtime_probe")
    assert event["payload"] == {
        "message": "safe summary",
        "score": 92.0,
        "sourceKinds": ["cts", "liepin"],
    }


def test_workbench_public_event_and_audit_schemas_are_closed(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/openapi.json")

    assert response.status_code == 200
    schemas = response.json()["components"]["schemas"]
    assert schemas["WorkbenchEventPayloadResponse"]["additionalProperties"] is False
    assert schemas["WorkbenchSecurityAuditMetadataResponse"]["additionalProperties"] is False
    serialized_event_payload = json.dumps(schemas["WorkbenchEventResponse"]["properties"]["payload"], sort_keys=True)
    serialized_audit_metadata = json.dumps(
        schemas["WorkbenchSecurityAuditEventResponse"]["properties"]["metadata"],
        sort_keys=True,
    )
    assert "additionalProperties" not in serialized_event_payload
    assert "additionalProperties" not in serialized_audit_metadata


def test_workbench_feature_gate_disables_auth_and_workbench_routes(tmp_path: Path) -> None:
    client = _client(tmp_path, workbench_enabled=False)

    auth = client.get("/api/auth/me")
    non_workbench = client.post("/api/liepin/compliance-gates")

    assert auth.status_code == 503
    assert auth.json()["detail"] == "Workbench is disabled by feature gate."
    assert non_workbench.status_code != 503
    actions = _audit_actions(tmp_path)
    assert "workbench_feature_gate_evaluated" in actions
