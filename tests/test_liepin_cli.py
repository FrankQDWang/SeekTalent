from __future__ import annotations

from pathlib import Path

import seektalent.cli as cli
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
            "--candidate-personal-info-processing-basis",
            "candidate recruiting lawful basis",
            "--personal-information-processor",
            "Acme Recruiting",
            "--operator-audit-owner",
            "Ops Owner",
            "--account-holder-authorized",
            "--human-initiated-recruiting",
            "--retention-policy",
            "run_debug_short",
            "--raw-payload-access-scope",
            "run_only",
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

    not_ready_bind = main(
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
    assert not_ready_bind == 1
    assert "account binding failed" in capsys.readouterr().err

    assert store.record_connection_account_subject(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        connection_id=connection_id,
        observed_provider_account_subject="internal-worker-observed-account-a",
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
    ).provider_account_hash
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


def test_liepin_compliance_gate_create_rejects_non_search_purpose(capsys, tmp_path: Path) -> None:
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
            "research",
            "--policy-ref",
            "policy-v1",
            "--deletion-sla-days",
            "14",
            "--deletion-path",
            "settings/delete",
            "--candidate-personal-info-processing-basis",
            "candidate recruiting lawful basis",
            "--personal-information-processor",
            "Acme Recruiting",
            "--operator-audit-owner",
            "Ops Owner",
            "--account-holder-authorized",
            "--human-initiated-recruiting",
            "--retention-policy",
            "run_debug_short",
            "--raw-payload-access-scope",
            "run_only",
            "--db-path",
            str(db_path),
        ]
    )

    captured = capsys.readouterr()
    assert create_status == 1
    assert "purpose" in captured.err
    assert "gate_" not in captured.out
    store = LiepinStore(db_path)
    assert store.get_compliance_gate(
        gate_ref="gate_missing",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
    ) is None


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


def test_liepin_compliance_gate_bind_rejects_denied_and_expired_gates(capsys, tmp_path: Path) -> None:
    db_path = tmp_path / "liepin.sqlite3"
    store = LiepinStore(db_path)

    for status in ["denied", "expired"]:
        gate_ref = store.create_compliance_gate(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_id="actor-a",
            gate=_gate_for_cli(f"{status} Recruiting", status=status),
            purpose="search",
        )
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
        assert bind_status == 1
        assert "account binding failed" in capsys.readouterr().err

        gate = store.get_compliance_gate(
            gate_ref=gate_ref,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_id="actor-a",
        )
        assert gate is not None
        assert gate.status == status
        assert gate.provider_account_hash is None

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
                "account-hash-a",
                "--db-path",
                str(db_path),
            ]
        )
        assert verify_status == 1
        assert status in capsys.readouterr().err


def test_liepin_bun_compatibility_gate_command(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_runner(command: list[str], *, cwd: Path) -> int:
        calls.append({"command": command, "cwd": cwd})
        return 7

    monkeypatch.setattr(cli, "_run_liepin_bun_compatibility_gate_process", fake_runner, raising=False)

    status = main(["liepin-bun-compatibility-gate"])

    assert status == 7
    assert calls == [
        {
            "command": ["bun", "run", "compatibility-gate"],
            "cwd": Path(cli.__file__).resolve().parents[2] / "apps" / "liepin-worker",
        }
    ]


def test_liepin_bun_compatibility_gate_requires_source_worker_package(
    capsys, monkeypatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(cli, "_liepin_worker_package_dir", lambda: tmp_path / "missing-worker", raising=False)
    monkeypatch.setattr(
        cli,
        "_run_liepin_bun_compatibility_gate_process",
        lambda command, *, cwd: calls.append({"command": command, "cwd": cwd}) or 99,
    )

    status = main(["liepin-bun-compatibility-gate"])
    captured = capsys.readouterr()

    assert status == 1
    assert calls == []
    assert "worker package" in captured.err
    assert "source checkout" in captured.err
    assert "Bun executable" not in captured.err


def test_liepin_bun_compatibility_gate_reports_missing_bun_when_worker_package_exists(
    capsys, monkeypatch, tmp_path: Path
) -> None:
    worker_dir = tmp_path / "apps" / "liepin-worker"
    worker_dir.mkdir(parents=True)
    (worker_dir / "package.json").write_text('{"scripts":{"compatibility-gate":"bun test"}}\n', encoding="utf-8")

    monkeypatch.setattr(cli, "_liepin_worker_package_dir", lambda: worker_dir, raising=False)

    def missing_bun(command: list[str], *, cwd: Path, check: bool):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(cli.subprocess, "run", missing_bun)

    status = main(["liepin-bun-compatibility-gate"])
    captured = capsys.readouterr()

    assert status == 1
    assert "Bun executable" in captured.err
    assert "worker package" not in captured.err


def _gate_for_cli(org_name: str, *, status: str = "pending_account_binding"):
    from seektalent.providers.liepin.compliance import ComplianceGate

    return ComplianceGate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        provider_account_hash=None,
        status=status,
        candidate_personal_info_processing_basis="candidate recruiting lawful basis",
        personal_information_processor=org_name,
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
        policy_ref=f"policy-{org_name}",
    )
