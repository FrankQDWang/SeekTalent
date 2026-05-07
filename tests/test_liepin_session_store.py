from __future__ import annotations

import hashlib
from dataclasses import asdict, is_dataclass
from pathlib import Path

import pytest

from seektalent.providers.liepin.compliance import ComplianceGate
from seektalent.providers.liepin.security import hmac_provider_account_hash
from seektalent.providers.liepin.session_store import ProtectedLiepinSessionStore
from seektalent.providers.liepin.store import LiepinStore


TENANT = "tenant-a"
WORKSPACE = "workspace-a"
ACTOR = "actor-a"
SECRET = "unit-hmac-secret"


def test_session_metadata_is_scoped_and_never_returns_state_material(tmp_path: Path) -> None:
    store = _session_store(tmp_path)
    gate_ref = _create_gate(store)
    connection_id = store.create_connection(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        compliance_gate_ref=gate_ref,
    )
    sessions = ProtectedLiepinSessionStore(store)

    metadata = sessions.record_ready_session(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        connection_id=connection_id,
        provider_account_subject="Recruiter@Liepin.Example",
        hmac_secret=SECRET,
        session_store_key_id="key-v1",
        encrypted_state_sha256="abc123",
    )

    assert metadata is not None
    assert metadata.connection_id == connection_id
    assert metadata.tenant_id == TENANT
    assert metadata.workspace_id == WORKSPACE
    assert metadata.provider_account_hash == hmac_provider_account_hash(SECRET, "Recruiter@Liepin.Example")
    assert metadata.provider_account_hash != hashlib.sha256(b"recruiter@liepin.example").hexdigest()
    assert _metadata_payload(metadata).keys().isdisjoint(
        {
            "session_state_path",
            "sessionStatePath",
            "state_path",
            "storage_state",
            "storageState",
            "session_state_bytes",
            "sessionStateBytes",
            "bytes",
        }
    )

    assert sessions.get_session_metadata(
        tenant_id=TENANT,
        workspace_id="workspace-b",
        actor_id=ACTOR,
        connection_id=connection_id,
    ) is None
    assert (
        sessions.get_session_metadata(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            connection_id=connection_id,
        )
        == metadata
    )


def test_revoke_records_event_and_clears_session_metadata(tmp_path: Path) -> None:
    store = _session_store(tmp_path)
    gate_ref = _create_gate(store)
    connection_id = store.create_connection(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        compliance_gate_ref=gate_ref,
    )
    sessions = ProtectedLiepinSessionStore(store)
    assert sessions.record_ready_session(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        connection_id=connection_id,
        provider_account_subject="account-a",
        hmac_secret=SECRET,
        session_store_key_id="key-v1",
        encrypted_state_sha256="abc123",
    )

    assert sessions.revoke_session(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        connection_id=connection_id,
        reason="user_requested",
    )

    metadata = sessions.get_session_metadata(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        connection_id=connection_id,
    )
    assert metadata is not None
    assert metadata.status == "revoked"
    assert metadata.provider_account_hash is None
    assert metadata.session_store_key_id is None
    assert metadata.encrypted_state_sha256 is None

    events = store.iter_events_after(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        subject_type="connection",
        subject_id=connection_id,
        after_sequence=0,
    )
    assert [(event.event_name, event.payload) for event in events] == [
        ("session_revoked", {"connectionId": connection_id, "reason": "user_requested"})
    ]


@pytest.mark.parametrize(
    "unsafe_reason",
    [
        "Bearer leaked-token",
        "storageState",
        "storageState leaked",
        "cookies=[...]",
        "localStorage contained auth",
        "sessionStorage contained token",
        "auth header copied",
        "cdp endpoint leaked",
        "debug websocket opened",
    ],
)
def test_revoke_reason_is_never_persisted_when_it_contains_sensitive_material(
    tmp_path: Path,
    unsafe_reason: str,
) -> None:
    store = _session_store(tmp_path)
    gate_ref = _create_gate(store)
    connection_id = store.create_connection(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        compliance_gate_ref=gate_ref,
    )
    sessions = ProtectedLiepinSessionStore(store)
    assert sessions.record_ready_session(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        connection_id=connection_id,
        provider_account_subject="account-a",
        hmac_secret=SECRET,
        session_store_key_id="key-v1",
        encrypted_state_sha256="abc123",
    )

    assert sessions.revoke_session(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        connection_id=connection_id,
        reason=unsafe_reason,
    )

    events = store.iter_events_after(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        subject_type="connection",
        subject_id=connection_id,
        after_sequence=0,
    )
    assert len(events) == 1
    assert events[0].payload == {"connectionId": connection_id, "reason": "unsafe_reason_redacted"}
    assert unsafe_reason not in str(events[0].payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"cookies": [{"name": "lt_auth", "value": "secret"}]},
        {"storageState": {"cookies": []}},
        {"headers": {"Authorization": "Bearer secret"}},
        {"cdpUrl": "http://127.0.0.1:9222/json/version"},
        {"debugWebsocketUrl": "ws://127.0.0.1:9222/devtools/browser/abc"},
        {"accessToken": "secret"},
        {"refresh_token": "secret"},
        {"localStorage": [{"name": "token", "value": "secret"}]},
        {"sessionStorage": [{"name": "token", "value": "secret"}]},
    ],
)
def test_artifact_and_log_payload_guard_rejects_sensitive_browser_material(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    store = _session_store(tmp_path)

    with pytest.raises(ValueError, match="unsafe Liepin event payload"):
        store.append_event(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            subject_type="connection",
            subject_id="conn_test",
            event_name="unsafe_payload",
            payload=payload,
        )


def _session_store(tmp_path: Path) -> LiepinStore:
    return LiepinStore(tmp_path / "liepin.sqlite3")


def _create_gate(store: LiepinStore) -> str:
    return store.create_compliance_gate(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        gate=_gate(),
        purpose="search",
    )


def _gate() -> ComplianceGate:
    return ComplianceGate(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        provider_account_hash=None,
        status="pending_account_binding",
        candidate_personal_info_processing_basis="candidate recruiting lawful basis",
        personal_information_processor="Acme Recruiting",
        operator_audit_owner="Ops Owner",
        account_holder_authorized=True,
        human_initiated_recruiting=True,
        allowed_purposes=["search"],
        retention_policy="run_debug_short",
        deletion_sla_days=14,
        deletion_path="settings/delete",
        raw_payload_access_scope="run_only",
        raw_detail_retention_allowed_after_debug=False,
        fixture_export_allowed=False,
        policy_ref="policy-v1",
    )


def _metadata_payload(metadata: object) -> dict[str, object]:
    if is_dataclass(metadata):
        return asdict(metadata)
    if hasattr(metadata, "model_dump"):
        return metadata.model_dump()
    return dict(vars(metadata))
