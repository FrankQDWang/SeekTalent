from __future__ import annotations

import inspect
import json
import sqlite3
from pathlib import Path

from seektalent.providers.liepin.compliance import ComplianceGate
from seektalent.providers.liepin.security import hmac_provider_account_hash
from seektalent.providers.liepin.store import LiepinStore


def _gate(**overrides: object) -> ComplianceGate:
    data: dict[str, object] = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "actor_id": "actor-a",
        "provider_account_hash": "account-hash-a",
        "status": "approved",
        "candidate_personal_info_processing_basis": "candidate recruiting lawful basis",
        "personal_information_processor": "Acme Recruiting",
        "operator_audit_owner": "Ops Owner",
        "account_holder_authorized": True,
        "human_initiated_recruiting": True,
        "allowed_purposes": ["search"],
        "retention_policy": "run_debug_short",
        "deletion_sla_days": 14,
        "deletion_path": "settings/delete",
        "raw_payload_access_scope": "run_only",
        "raw_detail_retention_allowed_after_debug": False,
        "fixture_export_allowed": False,
        "policy_ref": "policy-v1",
    }
    data.update(overrides)
    return ComplianceGate.model_validate(data)


def test_compliance_gate_uses_plan_contract_fields_exactly() -> None:
    assert set(ComplianceGate.model_fields) == {
        "tenant_id",
        "workspace_id",
        "actor_id",
        "provider_account_hash",
        "status",
        "candidate_personal_info_processing_basis",
        "personal_information_processor",
        "operator_audit_owner",
        "account_holder_authorized",
        "human_initiated_recruiting",
        "allowed_purposes",
        "retention_policy",
        "deletion_sla_days",
        "deletion_path",
        "raw_payload_access_scope",
        "raw_detail_retention_allowed_after_debug",
        "fixture_export_allowed",
        "policy_ref",
    }
    gate = _gate()
    assert gate.tenant_id == "tenant-a"
    assert gate.workspace_id == "workspace-a"
    assert gate.actor_id == "actor-a"
    assert gate.provider_account_hash == "account-hash-a"
    assert gate.status == "approved"
    assert gate.allowed_purposes == ["search"]

    try:
        _gate(account_binding_hash="legacy-name")
    except ValueError as exc:
        assert "account_binding_hash" in str(exc)
    else:
        raise AssertionError("legacy account_binding_hash field was accepted")


def test_live_search_requires_exact_approved_purpose_and_account_binding() -> None:
    assert _gate().allows_live_search(provider_account_hash="account-hash-a", purpose="search")
    assert not _gate(allowed_purposes=["research"]).allows_live_search(
        provider_account_hash="account-hash-a", purpose="search"
    )
    assert not _gate(allowed_purposes=["research-search"]).allows_live_search(
        provider_account_hash="account-hash-a", purpose="search"
    )
    assert not _gate(provider_account_hash="different").allows_live_search(
        provider_account_hash="account-hash-a", purpose="search"
    )
    assert not _gate(provider_account_hash=None).allows_live_search(
        provider_account_hash="account-hash-a", purpose="search"
    )
    assert not _gate(status="pending_account_binding").allows_live_search(
        provider_account_hash="account-hash-a", purpose="search"
    )
    assert not _gate(status="denied").allows_live_search(provider_account_hash="account-hash-a", purpose="search")
    assert not _gate(status="expired").allows_live_search(provider_account_hash="account-hash-a", purpose="search")


def test_gate_requires_personal_information_controls() -> None:
    required_fields = [
        "tenant_id",
        "workspace_id",
        "actor_id",
        "candidate_personal_info_processing_basis",
        "personal_information_processor",
        "operator_audit_owner",
        "deletion_path",
        "policy_ref",
    ]
    for field_name in required_fields:
        gate = _gate(**{field_name: ""})
        assert not gate.allows_live_search(provider_account_hash="account-hash-a", purpose="search")

    assert not _gate(deletion_sla_days=0).allows_live_search(provider_account_hash="account-hash-a", purpose="search")
    assert not _gate(account_holder_authorized=False).allows_live_search(
        provider_account_hash="account-hash-a", purpose="search"
    )
    assert not _gate(human_initiated_recruiting=False).allows_live_search(
        provider_account_hash="account-hash-a", purpose="search"
    )
    assert not _gate(raw_detail_retention_allowed_after_debug=True).allows_live_search(
        provider_account_hash="account-hash-a", purpose="search"
    )
    assert not _gate(fixture_export_allowed=True).allows_live_search(
        provider_account_hash="account-hash-a", purpose="search"
    )


def test_pending_gate_allows_login_handoff_but_blocks_live_search_until_matching_account_bound(tmp_path: Path) -> None:
    store = LiepinStore(tmp_path / "liepin.sqlite3")
    gate_ref = store.create_compliance_gate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        gate=_gate(provider_account_hash=None, status="pending_account_binding"),
        purpose="search",
    )
    pending = store.get_compliance_gate(
        gate_ref=gate_ref,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
    )
    assert pending is not None
    assert pending.allows_connection_handoff(purpose="search")
    assert not pending.allows_live_search(provider_account_hash="account-hash-a", purpose="search")

    connection_id = store.create_connection(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        compliance_gate_ref=gate_ref,
    )
    connection = store.get_connection(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        connection_id=connection_id,
    )
    assert connection is not None
    assert connection.status == "pending_login"
    assert connection.provider_account_hash is None
    assert not hasattr(connection, "observed_provider_account_subject")
    not_ready = store.bind_connection_account(
        gate_ref=gate_ref,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        connection_id=connection_id,
        secret="local-development",
    )
    assert not_ready is None
    assert store.get_compliance_gate(
        gate_ref=gate_ref,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
    ).status == "pending_account_binding"

    wrong_scope = store.bind_connection_account(
        gate_ref=gate_ref,
        tenant_id="tenant-a",
        workspace_id="workspace-b",
        actor_id="actor-a",
        connection_id=connection_id,
        secret="local-development",
    )
    assert wrong_scope is None

    recorded = store.record_connection_account_subject(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        connection_id=connection_id,
        observed_provider_account_subject="internal-worker-observed-account-a",
    )
    assert recorded
    ready = store.get_connection(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        connection_id=connection_id,
    )
    assert ready is not None
    assert ready.status == "login_ready"
    assert ready.provider_account_hash is None

    approved_hash = store.bind_connection_account(
        gate_ref=gate_ref,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        connection_id=connection_id,
        secret="local-development",
    )
    assert approved_hash is not None
    assert approved_hash != hmac_provider_account_hash("local-development", connection_id)
    approved = store.get_compliance_gate(
        gate_ref=gate_ref,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
    )
    assert approved is not None
    assert approved.status == "approved"
    assert approved.provider_account_hash == approved_hash
    assert approved.allows_live_search(provider_account_hash=approved_hash, purpose="search")
    assert not approved.allows_live_search(provider_account_hash="wrong-account-hash", purpose="search")


def test_caller_supplied_account_hash_cannot_approve_pending_gate(tmp_path: Path) -> None:
    store = LiepinStore(tmp_path / "liepin.sqlite3")
    gate_ref = store.create_compliance_gate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        gate=_gate(provider_account_hash=None, status="pending_account_binding"),
        purpose="search",
    )
    connection_id = store.create_connection(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        compliance_gate_ref=gate_ref,
    )

    approved = store.approve_connection_account_hash(
        gate_ref=gate_ref,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        connection_id=connection_id,
        provider_account_hash="attacker-chosen-hash",
    )

    assert approved is False
    gate = store.get_compliance_gate(
        gate_ref=gate_ref,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
    )
    connection = store.get_connection(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        connection_id=connection_id,
    )
    assert gate is not None
    assert connection is not None
    assert gate.status == "pending_account_binding"
    assert gate.provider_account_hash is None
    assert connection.status == "pending_login"
    assert connection.provider_account_hash is None


def test_binding_requires_connection_to_match_requested_gate(tmp_path: Path) -> None:
    store = LiepinStore(tmp_path / "liepin.sqlite3")
    gate_a = store.create_compliance_gate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        gate=_gate(provider_account_hash=None, status="pending_account_binding"),
        purpose="search",
    )
    gate_b = store.create_compliance_gate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        gate=_gate(provider_account_hash=None, status="pending_account_binding", policy_ref="policy-v2"),
        purpose="search",
    )
    connection_for_b = store.create_connection(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        compliance_gate_ref=gate_b,
    )

    mismatch = store.bind_connection_account(
        gate_ref=gate_a,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        connection_id=connection_for_b,
        secret="local-development",
    )
    assert mismatch is None
    assert store.get_compliance_gate(
        gate_ref=gate_a,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
    ).status == "pending_account_binding"
    assert store.get_compliance_gate(
        gate_ref=gate_b,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
    ).status == "pending_account_binding"


def test_denied_and_expired_gates_cannot_be_bound_or_resurrected(tmp_path: Path) -> None:
    store = LiepinStore(tmp_path / "liepin.sqlite3")

    for status in ["denied", "expired"]:
        gate_ref = store.create_compliance_gate(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_id="actor-a",
            gate=_gate(provider_account_hash=None, status=status),
            purpose="search",
        )
        connection_id = store.create_connection(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_id="actor-a",
            compliance_gate_ref=gate_ref,
        )

        account_hash = store.bind_connection_account(
            gate_ref=gate_ref,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_id="actor-a",
            connection_id=connection_id,
            secret="local-development",
        )

        assert account_hash is None
        gate = store.get_compliance_gate(
            gate_ref=gate_ref,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_id="actor-a",
        )
        assert gate is not None
        assert gate.status == status
        assert gate.provider_account_hash is None
        assert gate.denial_reason(provider_account_hash="account-hash-a", purpose="search") == status
        assert not gate.allows_live_search(provider_account_hash="account-hash-a", purpose="search")


def test_store_parses_allowed_purposes_as_json_not_sql_like(tmp_path: Path) -> None:
    store = LiepinStore(tmp_path / "liepin.sqlite3")
    gate_ref = store.create_compliance_gate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        gate=_gate(allowed_purposes=["research-search"]),
        purpose="research-search",
    )
    gate = store.get_compliance_gate(
        gate_ref=gate_ref,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
    )
    assert gate is not None
    assert gate.allowed_purposes == ["research-search"]
    assert not gate.allows_live_search(provider_account_hash="account-hash-a", purpose="search")


def test_store_create_run_rechecks_gate_policy_before_insert(tmp_path: Path) -> None:
    db_path = tmp_path / "liepin.sqlite3"
    store = LiepinStore(db_path)

    for field_name, value in [
        ("status", "denied"),
        ("status", "expired"),
        ("allowed_purposes_json", json.dumps(["research"])),
        ("account_holder_authorized", 0),
    ]:
        gate_ref = store.create_compliance_gate(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_id="actor-a",
            gate=_gate(provider_account_hash=None, status="pending_account_binding"),
            purpose="search",
        )
        connection_id = store.create_connection(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_id="actor-a",
            compliance_gate_ref=gate_ref,
        )
        assert store.record_connection_account_subject(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_id="actor-a",
            connection_id=connection_id,
            observed_provider_account_subject=f"internal-worker-observed-account-{field_name}-{value}",
        )
        account_hash = store.bind_connection_account(
            gate_ref=gate_ref,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_id="actor-a",
            connection_id=connection_id,
            secret="local-development",
        )
        assert account_hash is not None

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                f"UPDATE liepin_compliance_gates SET {field_name} = ? WHERE gate_ref = ?",
                (value, gate_ref),
            )

        try:
            store.create_run(
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                actor_id="actor-a",
                connection_id=connection_id,
                compliance_gate_ref=gate_ref,
            )
        except ValueError as exc:
            assert "compliance gate" in str(exc)
        else:
            raise AssertionError(f"run was inserted after gate policy changed: {field_name}={value!r}")


def test_store_create_run_starts_write_transaction_before_policy_check() -> None:
    source = inspect.getsource(LiepinStore.create_run)
    assert 'conn.execute("BEGIN IMMEDIATE")' in source
    assert source.index('conn.execute("BEGIN IMMEDIATE")') < source.index("SELECT c.provider_account_hash")


def test_event_ledger_rejects_raw_payloads_and_reads_bounded_batches(tmp_path: Path) -> None:
    store = LiepinStore(tmp_path / "liepin.sqlite3")
    first = store.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        subject_type="run",
        subject_id="run-a",
        event_name="run_started",
        payload={"status": "queued"},
    )
    second = store.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        subject_type="run",
        subject_id="run-a",
        event_name="search_progress",
        payload={"seen": 1},
    )
    assert (first, second) == (1, 2)
    batch = store.iter_events_after(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        subject_type="run",
        subject_id="run-a",
        after_sequence=0,
        limit=1,
    )
    assert [event.sequence for event in batch] == [1]

    try:
        store.append_event(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_id="actor-a",
            subject_type="run",
            subject_id="run-a",
            event_name="run_failed",
            payload={"rawProviderPayload": {"secret": "candidate"}},
        )
    except ValueError as exc:
        assert "unsafe" in str(exc)
    else:
        raise AssertionError("raw provider payload was persisted")


def test_event_ledger_rejects_worker_browser_internals_and_keeps_domain_payloads(tmp_path: Path) -> None:
    store = LiepinStore(tmp_path / "liepin.sqlite3")
    unsafe_payloads = [
        {"headers": {"Authorization": "Bearer secret-token"}},
        {"providerAccountSubject": "raw-liepin-account"},
        {"observedProviderAccountSubject": "raw-liepin-account"},
        {"accountSubject": "raw-liepin-account"},
        {"remoteDebuggingPort": 9222},
        {"browserContext": "context-1"},
        {"workerBaseUrl": "http://127.0.0.1:9999/internal"},
        {"debugWebsocketUrl": "ws://127.0.0.1:9222/devtools/browser/session"},
        {"browser": {"playwright": {"wsEndpoint": "wss://127.0.0.1/playwright/session"}}},
        {"handoff": {"authUrl": "https://www.liepin.com/login?token=secret"}},
        {"payload": {"providerPayload": {"candidate": "raw"}}},
        {"payload": {"raw_payload": {"candidate": "raw"}}},
        {"payload": {"cookie": "session=secret"}},
        {"diagnostics": "Bearer secret-token"},
        {"diagnostics": ["cdp://browser/session"]},
        {"url": "http://127.0.0.1:9222/json/version"},
        {"url": "ws://127.0.0.1:9222/devtools/page/abc"},
        {"url": "http://127.0.0.1:9999/internal"},
        {"diagnostics": "workerUrl=http://127.0.0.1:9999/internal/health"},
        {"diagnostics": "remote debugging port 9222"},
        {"diagnostics": "browserContext=context-1"},
        {"diagnostics": "http://127.0.0.1:9222/json/version"},
        {"diagnostics": "Authorization: Basic abc"},
        {"diagnostics": "internal-worker-observed-account-a"},
        {"diagnostics": "provider account subject raw-liepin-account"},
        {"diagnostics": "providerAccountSubject=raw-liepin-account"},
        {"diagnostics": "observedProviderAccountSubject=raw-liepin-account"},
        {"diagnostics": "Basic abc"},
        {"diagnostics": "Authorization Basic abc"},
        {"diagnostics": "Authorization: Token abc"},
        {"diagnostics": "Authorization: Digest abc"},
        {"diagnostics": "Token abc"},
        {"diagnostics": "Digest abc"},
        {"diagnostics": "Authorization Token abc"},
        {"diagnostics": "Authorization Digest abc"},
        {"diagnostics": "http://10.0.0.5:9222/json/version"},
        {"diagnostics": "http://worker-service/internal/health"},
        {"diagnostics": "https://worker.example/internal/health"},
    ]

    for payload in unsafe_payloads:
        try:
            store.append_event(
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                actor_id="actor-a",
                subject_type="run",
                subject_id="run-a",
                event_name="run_failed",
                payload=payload,
            )
        except ValueError as exc:
            assert "unsafe" in str(exc)
        else:
            raise AssertionError(f"unsafe payload was persisted: {payload!r}")

    sequence = store.append_event(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        subject_type="run",
        subject_id="run-a",
        event_name="search_progress",
        payload={"seen": 3, "accepted": 1, "artifactRefs": ["artifact:summary"]},
    )
    assert sequence == 1
    events = store.iter_events_after(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        subject_type="run",
        subject_id="run-a",
        after_sequence=0,
    )
    assert [event.payload for event in events] == [{"seen": 3, "accepted": 1, "artifactRefs": ["artifact:summary"]}]
