from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

import seektalent.cli as cli
from seektalent.cli import main
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.models import ResumeCandidate
from seektalent.providers.liepin.store import LiepinStore
from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError
from tests.settings_factory import make_settings


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


def test_liepin_compliance_gate_bind_rejects_raw_account_identity_arg(
    capsys, tmp_path: Path
) -> None:
    db_path = tmp_path / "liepin.sqlite3"
    store = LiepinStore(db_path)
    gate_ref = store.create_compliance_gate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        gate=_gate_for_cli("Acme Recruiting"),
        purpose="search",
    )
    connection_id = store.create_connection(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        compliance_gate_ref=gate_ref,
    )

    with pytest.raises(SystemExit) as exit_info:
        main(
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
                "--observed-provider-account-subject",
                "internal-worker-observed-account-a",
                "--db-path",
                str(db_path),
            ]
        )

    captured = capsys.readouterr()
    assert exit_info.value.code == 2
    assert "raw account identity" in captured.err
    assert "internal-worker-observed-account-a" not in captured.err


def test_liepin_compliance_gate_verify_rejects_missing_wrong_account_and_no_search_gates(
    capsys, tmp_path: Path
) -> None:
    db_path = tmp_path / "liepin.sqlite3"
    store = LiepinStore(db_path)
    approved_gate = _gate_for_cli("Acme Recruiting", status="approved").model_copy(
        update={"provider_account_hash": "account-hash-a"}
    )
    gate_ref = store.create_compliance_gate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        gate=approved_gate,
        purpose="search",
    )
    no_search_gate_ref = store.create_compliance_gate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        gate=approved_gate.model_copy(update={"allowed_purposes": ["connection"]}),
        purpose="connection",
    )

    missing_status = main(
        [
            "liepin-compliance-gate",
            "verify",
            "--gate-ref",
            "gate_missing",
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
    assert missing_status == 1
    assert "gate not found" in capsys.readouterr().err

    wrong_account_status = main(
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
            "account-hash-b",
            "--db-path",
            str(db_path),
        ]
    )
    assert wrong_account_status == 1
    assert "provider_account_mismatch" in capsys.readouterr().err

    no_search_status = main(
        [
            "liepin-compliance-gate",
            "verify",
            "--gate-ref",
            no_search_gate_ref,
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
    assert no_search_status == 1
    assert "policy_requirements_not_satisfied" in capsys.readouterr().err

    no_search_explicit_connection_status = main(
        [
            "liepin-compliance-gate",
            "verify",
            "--gate-ref",
            no_search_gate_ref,
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--provider-account-hash",
            "account-hash-a",
            "--purpose",
            "connection",
            "--db-path",
            str(db_path),
        ]
    )
    assert no_search_explicit_connection_status == 1
    assert "requires --purpose search" in capsys.readouterr().err


def test_liepin_replay_fixtures_runs_without_live_account(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_runner(command: list[str], *, cwd: Path) -> int:
        calls.append({"command": command, "cwd": cwd})
        return 0

    monkeypatch.setattr(cli, "_run_liepin_replay_fixtures_process", fake_runner, raising=False)

    status = main(["liepin-replay-fixtures"])

    assert status == 0
    assert calls == [
        {
            "command": ["bun", "test", "tests/extraction.test.ts", "tests/redaction.test.ts"],
            "cwd": Path(cli.__file__).resolve().parents[2] / "apps" / "liepin-worker",
        }
    ]
    assert all("live" not in part for part in calls[0]["command"])


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


def test_liepin_smoke_requires_live_flag(capsys) -> None:
    status = main(["liepin-smoke"])

    captured = capsys.readouterr()
    assert status == 1
    assert "requires --live" in captured.err


def test_liepin_smoke_live_requires_scope_gate_and_connection(capsys) -> None:
    status = main(["liepin-smoke", "--live"])

    captured = capsys.readouterr()
    assert status == 1
    assert "tenant-id" in captured.err
    assert "workspace-id" in captured.err
    assert "actor-id" in captured.err
    assert "connection-id" in captured.err
    assert "compliance-gate-ref" in captured.err


def test_liepin_smoke_live_verifies_connection_gate_and_uses_managed_local_budget(
    capsys, monkeypatch, tmp_path: Path
) -> None:
    db_path, gate_ref, connection_id, provider_account_hash = _approved_gate_and_connection(tmp_path)
    worker = RecordingSmokeWorker(connection_id=connection_id, provider_account_hash=provider_account_hash)
    built_settings: list[object] = []
    detail_plan_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        cli,
        "AppSettings",
        lambda: make_settings(
            liepin_worker_mode="disabled",
            liepin_api_token="worker-token",
            liepin_detail_open_approval_secret="detail-approval-secret",
        ),
    )
    monkeypatch.setattr(
        cli,
        "build_liepin_worker_client",
        lambda settings: built_settings.append(settings) or worker,
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "build_detail_open_plan",
        lambda **kwargs: detail_plan_calls.append(kwargs)
        or SimpleNamespace(
            decisions=[
                SimpleNamespace(action="open_detail"),
                SimpleNamespace(action="card_only"),
            ]
        ),
        raising=False,
    )

    status = main(
        [
            "liepin-smoke",
            "--live",
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_id,
            "--compliance-gate-ref",
            gate_ref,
            "--max-detail-opens",
            "1",
            "--keyword",
            "算法",
            "--page-size",
            "2",
            "--db-path",
            str(db_path),
        ]
    )

    captured = capsys.readouterr()
    assert status == 0
    assert built_settings[0].liepin_worker_mode == "managed_local"
    assert built_settings[0].liepin_live_enabled is True
    assert worker.ensure_ready_called is True
    assert worker.session_status_calls == [
        {
            "connection_id": connection_id,
            "tenant": "tenant-a",
            "workspace": "workspace-a",
            "provider_account_hash": provider_account_hash,
        }
    ]
    assert len(worker.search_calls) == 1
    search_call = worker.search_calls[0]
    assert search_call["round_no"] == 1
    assert search_call["trace_id"] == "liepin-smoke"
    assert search_call["provider_account_hash"] == provider_account_hash
    request = search_call["request"]
    assert request.keyword_query == "算法"
    assert request.query_terms == ["算法"]
    assert request.page_size == 2
    assert request.provider_context["liepin_connection_id"] == connection_id
    assert detail_plan_calls[0]["daily_detail_budget"] == 1
    smoke_candidates = detail_plan_calls[0]["candidates"]
    assert smoke_candidates[0].candidate_id == "worker-candidate-1"
    assert smoke_candidates[0].stable_provider_id == "provider-subject-1"
    assert "compliance: approved" in captured.out
    assert "worker setup: managed_local" in captured.out
    assert "worker health: ok" in captured.out
    assert "session: ready" in captured.out
    assert "card_count: 1" in captured.out
    assert "raw_candidate_count: 3" in captured.out
    assert "detail_open_planned: 1" in captured.out
    assert provider_account_hash not in captured.out


def test_liepin_smoke_live_uses_external_http_when_configured(
    monkeypatch, tmp_path: Path
) -> None:
    db_path, gate_ref, connection_id, provider_account_hash = _approved_gate_and_connection(tmp_path)
    worker = RecordingSmokeWorker(connection_id=connection_id, provider_account_hash=provider_account_hash)
    built_settings: list[object] = []

    monkeypatch.setattr(
        cli,
        "AppSettings",
        lambda: make_settings(
            liepin_worker_mode="external_http",
            liepin_worker_base_url="http://127.0.0.1:8123",
            liepin_api_token="worker-token",
            liepin_detail_open_approval_secret="detail-approval-secret",
        ),
    )
    monkeypatch.setattr(
        cli,
        "build_liepin_worker_client",
        lambda settings: built_settings.append(settings) or worker,
        raising=False,
    )

    status = main(
        [
            "liepin-smoke",
            "--live",
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_id,
            "--compliance-gate-ref",
            gate_ref,
            "--db-path",
            str(db_path),
        ]
    )

    assert status == 0
    assert built_settings[0].liepin_worker_mode == "external_http"


def test_liepin_smoke_worker_base_url_implies_external_http(
    monkeypatch, tmp_path: Path
) -> None:
    db_path, gate_ref, connection_id, provider_account_hash = _approved_gate_and_connection(tmp_path)
    worker = RecordingSmokeWorker(connection_id=connection_id, provider_account_hash=provider_account_hash)
    built_settings: list[object] = []

    monkeypatch.setattr(
        cli,
        "AppSettings",
        lambda: make_settings(
            liepin_worker_mode="disabled",
            liepin_api_token="worker-token",
            liepin_detail_open_approval_secret="detail-approval-secret",
        ),
    )
    monkeypatch.setattr(
        cli,
        "build_liepin_worker_client",
        lambda settings: built_settings.append(settings) or worker,
        raising=False,
    )

    status = main(
        [
            "liepin-smoke",
            "--live",
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_id,
            "--compliance-gate-ref",
            gate_ref,
            "--worker-base-url",
            "http://127.0.0.1:8123",
            "--db-path",
            str(db_path),
        ]
    )

    assert status == 0
    assert built_settings[0].liepin_worker_mode == "external_http"
    assert built_settings[0].liepin_worker_base_url == "http://127.0.0.1:8123"


def test_liepin_smoke_preserves_explicit_pi_agent_mode(
    capsys, monkeypatch, tmp_path: Path
) -> None:
    db_path, gate_ref, connection_id, provider_account_hash = _approved_gate_and_connection(tmp_path)
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("---\nname: liepin-search-cards\n---\n", encoding="utf-8")
    worker = RecordingSmokeWorker(connection_id=connection_id, provider_account_hash=provider_account_hash)
    built_settings: list[object] = []

    monkeypatch.setattr(
        cli,
        "AppSettings",
        lambda: make_settings(
            liepin_worker_mode="disabled",
            liepin_api_token="worker-token",
            liepin_detail_open_approval_secret="detail-approval-secret",
            liepin_account_binding_secret="runtime-secret",
            liepin_pi_command="pi --mode rpc --no-session",
            liepin_pi_skill_path=str(skill_path),
            liepin_pi_dokobot_tool_name="dokobot",
        ),
    )
    monkeypatch.setattr(
        cli,
        "build_liepin_worker_client",
        lambda settings: built_settings.append(settings) or worker,
        raising=False,
    )

    status = main(
        [
            "liepin-smoke",
            "--live",
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_id,
            "--compliance-gate-ref",
            gate_ref,
            "--worker-mode",
            "pi_agent",
            "--db-path",
            str(db_path),
        ]
    )

    captured = capsys.readouterr()
    assert status == 0
    assert built_settings[0].liepin_worker_mode == "pi_agent"
    assert "worker setup: pi_agent" in captured.out


def test_liepin_smoke_worker_base_url_overrides_managed_local_mode(
    monkeypatch, tmp_path: Path
) -> None:
    db_path, gate_ref, connection_id, provider_account_hash = _approved_gate_and_connection(tmp_path)
    worker = RecordingSmokeWorker(connection_id=connection_id, provider_account_hash=provider_account_hash)
    built_settings: list[object] = []

    monkeypatch.setattr(
        cli,
        "AppSettings",
        lambda: make_settings(
            liepin_worker_mode="disabled",
            liepin_api_token="worker-token",
            liepin_detail_open_approval_secret="detail-approval-secret",
        ),
    )
    monkeypatch.setattr(
        cli,
        "build_liepin_worker_client",
        lambda settings: built_settings.append(settings) or worker,
        raising=False,
    )

    status = main(
        [
            "liepin-smoke",
            "--live",
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_id,
            "--compliance-gate-ref",
            gate_ref,
            "--worker-mode",
            "managed_local",
            "--worker-base-url",
            "http://127.0.0.1:8123",
            "--db-path",
            str(db_path),
        ]
    )

    assert status == 0
    assert built_settings[0].liepin_worker_mode == "external_http"
    assert built_settings[0].liepin_worker_base_url == "http://127.0.0.1:8123"


def test_liepin_smoke_live_rejects_session_connection_mismatch(capsys, monkeypatch, tmp_path: Path) -> None:
    db_path, gate_ref, connection_id, provider_account_hash = _approved_gate_and_connection(tmp_path)
    worker = RecordingSmokeWorker(connection_id="other-connection", provider_account_hash=provider_account_hash)

    monkeypatch.setattr(
        cli,
        "AppSettings",
        lambda: make_settings(
            liepin_worker_mode="disabled",
            liepin_api_token="worker-token",
            liepin_detail_open_approval_secret="detail-approval-secret",
        ),
    )
    monkeypatch.setattr(cli, "build_liepin_worker_client", lambda settings: worker, raising=False)

    status = main(
        [
            "liepin-smoke",
            "--live",
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_id,
            "--compliance-gate-ref",
            gate_ref,
            "--db-path",
            str(db_path),
        ]
    )

    captured = capsys.readouterr()
    assert status == 1
    assert "connection_id_mismatch" in captured.err
    assert worker.search_calls == []
    assert provider_account_hash not in captured.err


def test_liepin_smoke_live_refuses_fake_fixture_mode(capsys, monkeypatch, tmp_path: Path) -> None:
    db_path, gate_ref, connection_id, _provider_account_hash = _approved_gate_and_connection(tmp_path)
    build_calls: list[object] = []

    monkeypatch.setattr(
        cli,
        "AppSettings",
        lambda: make_settings(
            liepin_worker_mode="fake_fixture",
            liepin_allow_fake_fixture_worker=True,
            liepin_api_token="worker-token",
        ),
    )
    monkeypatch.setattr(
        cli,
        "build_liepin_worker_client",
        lambda settings: build_calls.append(settings),
        raising=False,
    )

    status = main(
        [
            "liepin-smoke",
            "--live",
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_id,
            "--compliance-gate-ref",
            gate_ref,
            "--db-path",
            str(db_path),
        ]
    )

    captured = capsys.readouterr()
    assert status == 1
    assert build_calls == []
    assert "fake fixture" in captured.err.lower()


def test_liepin_smoke_live_reports_worker_failure_without_raw_streams(
    capsys, monkeypatch, tmp_path: Path
) -> None:
    db_path, gate_ref, connection_id, provider_account_hash = _approved_gate_and_connection(tmp_path)
    worker = FailingSmokeWorker()

    monkeypatch.setattr(
        cli,
        "AppSettings",
        lambda: make_settings(
            liepin_worker_mode="disabled",
            liepin_api_token="worker-token",
            liepin_detail_open_approval_secret="detail-approval-secret",
        ),
    )
    monkeypatch.setattr(cli, "build_liepin_worker_client", lambda settings: worker, raising=False)

    status = main(
        [
            "liepin-smoke",
            "--live",
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_id,
            "--compliance-gate-ref",
            gate_ref,
            "--db-path",
            str(db_path),
        ]
    )

    captured = capsys.readouterr()
    assert status == 1
    assert "worker setup: managed_local" in captured.out
    assert "worker_failed" in captured.err
    assert provider_account_hash not in captured.out
    assert provider_account_hash not in captured.err
    assert "stdout secret" not in captured.err
    assert "stderr secret" not in captured.err


def test_liepin_smoke_live_reports_unexpected_worker_failure_without_raw_exception(
    capsys, monkeypatch, tmp_path: Path
) -> None:
    db_path, gate_ref, connection_id, provider_account_hash = _approved_gate_and_connection(tmp_path)

    monkeypatch.setattr(
        cli,
        "AppSettings",
        lambda: make_settings(
            liepin_worker_mode="disabled",
            liepin_api_token="worker-token",
            liepin_detail_open_approval_secret="detail-approval-secret",
        ),
    )
    monkeypatch.setattr(
        cli,
        "build_liepin_worker_client",
        lambda settings: (_ for _ in ()).throw(RuntimeError("raw stdout secret stderr secret")),
        raising=False,
    )

    status = main(
        [
            "liepin-smoke",
            "--live",
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_id,
            "--compliance-gate-ref",
            gate_ref,
            "--db-path",
            str(db_path),
        ]
    )

    captured = capsys.readouterr()
    assert status == 1
    assert "unexpected_failure" in captured.err
    assert provider_account_hash not in captured.err
    assert "raw stdout secret" not in captured.err
    assert "stderr secret" not in captured.err


class RecordingSmokeWorker:
    def __init__(self, *, provider_account_hash: str, connection_id: str = "conn-default", fixture_only: bool = False) -> None:
        self.connection_id = connection_id
        self.provider_account_hash = provider_account_hash
        self.fixture_only = fixture_only
        self.ensure_ready_called = False
        self.session_status_calls: list[dict[str, object]] = []
        self.search_calls: list[dict[str, object]] = []

    async def ensure_ready(self, *, on_event=None) -> None:
        self.ensure_ready_called = True

    async def session_status(
        self,
        *,
        connection_id: str,
        tenant: str | None = None,
        workspace: str | None = None,
        provider_account_hash: str | None = None,
    ):
        self.session_status_calls.append(
            {
                "connection_id": connection_id,
                "tenant": tenant,
                "workspace": workspace,
                "provider_account_hash": provider_account_hash,
            }
        )
        return SimpleNamespace(
            connection_id=self.connection_id,
            status="ready",
            provider_account_hash=self.provider_account_hash,
            fixture_only=self.fixture_only,
        )

    async def search(
        self,
        request,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult:
        self.search_calls.append(
            {
                "request": request,
                "round_no": round_no,
                "trace_id": trace_id,
                "provider_account_hash": provider_account_hash,
            }
        )
        return SearchResult(
            candidates=[
                ResumeCandidate(
                    resume_id="worker-candidate-1",
                    source_resume_id="provider-subject-1",
                    dedup_key="worker-fingerprint-1",
                    search_text="worker card",
                    raw={"provider": "liepin", "raw_payload_artifact_ref": "worker://cards/1.json"},
                )
            ],
            raw_candidate_count=3,
        )


class FailingSmokeWorker:
    async def ensure_ready(self, *, on_event=None) -> None:
        if on_event is not None:
            on_event(
                "worker_failed",
                {
                    "mode": "managed_local",
                    "setup_status": "worker_failed",
                    "diagnostics": {
                        "stdout": "stdout secret",
                        "stderr": "stderr secret",
                    },
                },
            )
        raise LiepinWorkerModeError(
            "worker failed with stdout secret and stderr secret",
            setup_status="worker_failed",
        )


def _approved_gate_and_connection(tmp_path: Path) -> tuple[Path, str, str, str]:
    db_path = tmp_path / "liepin.sqlite3"
    store = LiepinStore(db_path)
    gate_ref = store.create_compliance_gate(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        gate=_gate_for_cli("Acme Recruiting"),
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
        observed_provider_account_subject="internal-worker-observed-account-a",
    )
    provider_account_hash = store.bind_connection_account(
        gate_ref=gate_ref,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        actor_id="actor-a",
        connection_id=connection_id,
        secret="local-development",
    )
    assert provider_account_hash is not None
    return db_path, gate_ref, connection_id, provider_account_hash


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
