from __future__ import annotations

from pathlib import Path

from seektalent.cli import main
from seektalent.providers.liepin.store import LiepinStore


def test_liepin_compliance_gate_create_and_verify(capsys, tmp_path: Path) -> None:
    db_path = tmp_path / "liepin.sqlite3"

    create_status = main(
        [
            "liepin-compliance-gate",
            "create",
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--purpose",
            "search",
            "--policy-ref",
            "policy-v1",
            "--deletion-sla-days",
            "14",
            "--deletion-path",
            "settings/delete",
            "--org-name",
            "Acme Recruiting",
            "--org-domain",
            "acme.example",
            "--search-keyword",
            "python",
            "--pii-policy",
            "candidate recruiting lawful basis",
            "--operator-id",
            "operator-a",
            "--operator-name",
            "Ops Owner",
            "--db-path",
            str(db_path),
        ]
    )
    assert create_status == 0
    gate_ref = capsys.readouterr().out.strip()
    assert gate_ref.startswith("gate_")
    assert "token" not in gate_ref.lower()

    missing_verify = main(
        [
            "liepin-compliance-gate",
            "verify",
            "--gate-ref",
            gate_ref,
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--provider-account-hash",
            "account-hash-a",
            "--db-path",
            str(db_path),
        ]
    )
    assert missing_verify == 1
    assert "pending_account_binding" in capsys.readouterr().err

    store = LiepinStore(db_path)
    connection_id = store.create_connection(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        compliance_gate_ref=gate_ref,
    )

    bind_status = main(
        [
            "liepin-compliance-gate",
            "bind-account",
            "--gate-ref",
            gate_ref,
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_id,
            "--db-path",
            str(db_path),
            "--hmac-secret",
            "local-development",
        ]
    )
    assert bind_status == 0
    bind_output = capsys.readouterr().out
    assert "approved" in bind_output
    assert connection_id not in bind_output
    assert "subject" not in bind_output.lower()

    provider_account_hash = store.get_connection(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        connection_id=connection_id,
    ).account_binding_hash
    assert provider_account_hash is not None
    verify_status = main(
        [
            "liepin-compliance-gate",
            "verify",
            "--gate-ref",
            gate_ref,
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--provider-account-hash",
            provider_account_hash,
            "--db-path",
            str(db_path),
        ]
    )
    assert verify_status == 0
    verify_output = capsys.readouterr().out
    assert "approved" in verify_output
    assert provider_account_hash not in verify_output

    wrong_scope = main(
        [
            "liepin-compliance-gate",
            "verify",
            "--gate-ref",
            gate_ref,
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-b",
            "--actor-id",
            "actor-a",
            "--provider-account-hash",
            provider_account_hash,
            "--db-path",
            str(db_path),
        ]
    )
    assert wrong_scope == 1


def test_liepin_compliance_gate_bind_rejects_connection_for_different_gate(capsys, tmp_path: Path) -> None:
    db_path = tmp_path / "liepin.sqlite3"
    store = LiepinStore(db_path)
    gate_a = store.create_compliance_gate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        gate=_gate_for_cli("Acme Recruiting"),
        purpose="search",
    )
    gate_b = store.create_compliance_gate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        gate=_gate_for_cli("Other Recruiting"),
        purpose="search",
    )
    connection_for_b = store.create_connection(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        compliance_gate_ref=gate_b,
    )

    status = main(
        [
            "liepin-compliance-gate",
            "bind-account",
            "--gate-ref",
            gate_a,
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_for_b,
            "--db-path",
            str(db_path),
            "--hmac-secret",
            "local-development",
        ]
    )

    assert status == 1
    assert "account binding failed" in capsys.readouterr().err
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


def _gate_for_cli(org_name: str):
    from seektalent.providers.liepin.compliance import ComplianceGate

    return ComplianceGate(
        org_name=org_name,
        org_domain="example.test",
        approved_purposes=["search"],
        search_keywords=["python"],
        retention_days=14,
        pii_policy="candidate recruiting lawful basis",
        operator_id="operator-a",
        operator_name="Ops Owner",
        created_at="2026-05-07T00:00:00+00:00",
    )
