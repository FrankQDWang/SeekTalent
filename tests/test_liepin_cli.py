from __future__ import annotations

from pathlib import Path

from seektalent.cli import main
from seektalent.providers.liepin.security import hmac_provider_account_hash
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
        provider_account_identity_hint="liepin-user-a",
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
    assert "liepin-user-a" not in bind_output

    provider_account_hash = hmac_provider_account_hash("local-development", "liepin-user-a")
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
