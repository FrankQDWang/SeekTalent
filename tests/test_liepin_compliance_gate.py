from __future__ import annotations

from pathlib import Path

from seektalent.providers.liepin.compliance import ComplianceGate
from seektalent.providers.liepin.security import hmac_provider_account_hash
from seektalent.providers.liepin.store import LiepinStore


def _gate(**overrides: object) -> ComplianceGate:
    data: dict[str, object] = {
        "org_name": "Acme Recruiting",
        "org_domain": "acme.example",
        "approved_purposes": ["search"],
        "search_keywords": ["python", "backend"],
        "retention_days": 14,
        "pii_policy": "candidate recruiting lawful basis",
        "operator_id": "operator-a",
        "operator_name": "Ops Owner",
        "created_at": "2026-05-07T00:00:00+00:00",
        "approved_at": "2026-05-07T00:00:01+00:00",
        "account_binding_hash": "account-hash-a",
    }
    data.update(overrides)
    return ComplianceGate.model_validate(data)


def test_compliance_gate_uses_task2_contract_fields_and_rejects_old_provider_hash_shape() -> None:
    assert set(ComplianceGate.model_fields) == {
        "org_name",
        "org_domain",
        "approved_purposes",
        "search_keywords",
        "retention_days",
        "pii_policy",
        "operator_id",
        "operator_name",
        "created_at",
        "approved_at",
        "account_binding_hash",
    }
    gate = _gate()
    assert gate.org_name == "Acme Recruiting"
    assert gate.org_domain == "acme.example"
    assert gate.approved_purposes == ["search"]
    assert gate.search_keywords == ["python", "backend"]
    assert gate.retention_days == 14
    assert gate.pii_policy == "candidate recruiting lawful basis"
    assert gate.operator_id == "operator-a"
    assert gate.operator_name == "Ops Owner"
    assert gate.created_at == "2026-05-07T00:00:00+00:00"
    assert gate.approved_at == "2026-05-07T00:00:01+00:00"
    assert gate.account_binding_hash == "account-hash-a"

    try:
        _gate(tenant_id="tenant-a")
    except ValueError as exc:
        assert "tenant_id" in str(exc)
    else:
        raise AssertionError("scope field was accepted by pure ComplianceGate")


def test_live_search_requires_exact_approved_purpose_and_account_binding() -> None:
    assert _gate().allows_live_search(account_binding_hash="account-hash-a", purpose="search")
    assert not _gate(approved_purposes=["research"]).allows_live_search(
        account_binding_hash="account-hash-a", purpose="search"
    )
    assert not _gate(approved_purposes=["research-search"]).allows_live_search(
        account_binding_hash="account-hash-a", purpose="search"
    )
    assert not _gate(account_binding_hash="different").allows_live_search(
        account_binding_hash="account-hash-a", purpose="search"
    )
    assert not _gate(account_binding_hash=None).allows_live_search(
        account_binding_hash="account-hash-a", purpose="search"
    )
    assert not _gate(approved_at=None).allows_live_search(account_binding_hash="account-hash-a", purpose="search")


def test_gate_requires_personal_information_controls() -> None:
    required_fields = [
        "org_name",
        "org_domain",
        "pii_policy",
        "operator_id",
        "operator_name",
    ]
    for field_name in required_fields:
        gate = _gate(**{field_name: ""})
        assert not gate.allows_live_search(account_binding_hash="account-hash-a", purpose="search")

    assert not _gate(retention_days=0).allows_live_search(account_binding_hash="account-hash-a", purpose="search")
    assert not _gate(search_keywords=[]).allows_live_search(account_binding_hash="account-hash-a", purpose="search")


def test_pending_gate_allows_login_handoff_but_blocks_live_search_until_matching_account_bound(tmp_path: Path) -> None:
    store = LiepinStore(tmp_path / "liepin.sqlite3")
    gate_ref = store.create_compliance_gate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        gate=_gate(account_binding_hash=None, approved_at=None),
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
    assert not pending.allows_live_search(account_binding_hash="account-hash-a", purpose="search")

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
    assert not hasattr(connection, "observed_provider_account_subject")
    wrong_scope = store.bind_connection_account(
        gate_ref=gate_ref,
        tenant_id="tenant-a",
        workspace_id="workspace-b",
        actor_id="actor-a",
        connection_id=connection_id,
        secret="local-development",
    )
    assert wrong_scope is None

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
    assert approved.allows_live_search(account_binding_hash=approved_hash, purpose="search")
    assert not approved.allows_live_search(account_binding_hash="wrong-account-hash", purpose="search")


def test_binding_requires_connection_to_match_requested_gate(tmp_path: Path) -> None:
    store = LiepinStore(tmp_path / "liepin.sqlite3")
    gate_a = store.create_compliance_gate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        gate=_gate(account_binding_hash=None, approved_at=None),
        purpose="search",
    )
    gate_b = store.create_compliance_gate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        gate=_gate(account_binding_hash=None, approved_at=None, org_name="Other Recruiting"),
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


def test_store_parses_allowed_purposes_as_json_not_sql_like(tmp_path: Path) -> None:
    store = LiepinStore(tmp_path / "liepin.sqlite3")
    gate_ref = store.create_compliance_gate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        gate=_gate(approved_purposes=["research-search"]),
        purpose="research-search",
    )
    gate = store.get_compliance_gate(
        gate_ref=gate_ref,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
    )
    assert gate is not None
    assert gate.approved_purposes == ["research-search"]
    assert not gate.allows_live_search(account_binding_hash="account-hash-a", purpose="search")


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
