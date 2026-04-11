from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from seektalent import __version__
from seektalent.cli import main


def _fake_bundle() -> object:
    payload = {
        "phase": "v0.3.2_offline_artifacts_active",
        "run_id": "20260409T120000Z_deadbeef",
        "run_dir": "/tmp/runs/20260409T120000Z_deadbeef",
        "bootstrap": {"input_truth": {"job_description": "JD"}},
        "rounds": [],
        "finalization_audit": {"model_name": "test"},
        "final_result": {
            "final_shortlist_candidate_ids": ["c-1", "c-2"],
            "run_summary": "Ready for review.",
            "stop_reason": "controller_stop",
        },
        "eval": {"experiment_id": "E5", "metrics": []},
    }
    return SimpleNamespace(
        run_dir=payload["run_dir"],
        final_result=SimpleNamespace(
            final_shortlist_candidate_ids=payload["final_result"]["final_shortlist_candidate_ids"],
            run_summary=payload["final_result"]["run_summary"],
            stop_reason=payload["final_result"]["stop_reason"],
        ),
        model_dump=lambda mode="json": payload,
    )


def test_main_shows_root_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "Runtime status" in help_text
    assert "inspect" in help_text
    assert "doctor" in help_text


def test_version_command_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_update_command_prints_upgrade_instructions(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["update"]) == 0
    output = capsys.readouterr().out
    assert f"Current version: {__version__}" in output
    assert "pip install -U seektalent" in output
    assert "pipx upgrade seektalent" in output


def test_inspect_command_points_to_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inspect"]) == 0
    output = capsys.readouterr().out
    assert "v0.3.2 CLI inspection summary" in output
    assert "inspect --json" in output


def test_inspect_json_returns_machine_readable_contract(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inspect", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tool"] == "seektalent"
    assert payload["version"] == __version__
    assert payload["phase"] == "v0.3.2_offline_artifacts_active"
    assert payload["recommended_workflow"][-1] == "seektalent run --jd-file ./jd.md"
    assert "seektalent-rerank-api" in payload["recommended_workflow"]
    assert "run" in payload["commands"]
    assert "doctor" in payload["commands"]
    run_args = {item["name"]: item for item in payload["commands"]["run"]["arguments"]}
    assert run_args["--jd"]["mutually_exclusive_with"] == ["--jd-file"]
    assert run_args["--jd-file"]["mutually_exclusive_with"] == ["--jd"]
    assert run_args["--round-budget"]["kind"] == "integer"
    assert "--output-dir" not in run_args
    assert payload["json_contracts"]["run"]["stderr_json_fields"] == ["error", "error_type"]


def test_init_writes_env_template(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"
    assert main(["init", "--env-file", str(env_file)]) == 0
    assert env_file.exists()
    text = env_file.read_text(encoding="utf-8")
    assert "SEEKTALENT_CTS_TENANT_KEY=" in text
    assert "SEEKTALENT_ROUND_BUDGET=5" in text
    assert str(env_file) in capsys.readouterr().out


def test_init_refuses_to_overwrite_without_force(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SEEKTALENT_MOCK_CTS=true\n", encoding="utf-8")
    assert main(["init", "--env-file", str(env_file)]) == 1
    assert "already exists" in capsys.readouterr().err


def test_doctor_json_success_in_mock_mode(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SEEKTALENT_MOCK_CTS=true\n", encoding="utf-8")

    assert main(
        [
            "doctor",
            "--env-file",
            str(env_file),
            "--output-dir",
            str(tmp_path / "runs"),
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert {item["name"] for item in payload["checks"]} == {
        "packaged_spec",
        "output_dir",
        "runtime_manifest",
        "cts_credentials",
        "phase",
    }


def test_doctor_fails_for_missing_real_cts_credentials(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    assert main(["doctor", "--env-file", str(env_file), "--output-dir", str(tmp_path / "runs")]) == 1
    assert "FAIL cts_credentials" in capsys.readouterr().out


def test_run_json_success_emits_search_run_bundle(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "seektalent.cli.run_match",
        lambda **kwargs: _fake_bundle(),
    )

    assert main(["run", "--jd", "JD", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["phase"] == "v0.3.2_offline_artifacts_active"
    assert payload["final_result"]["final_shortlist_candidate_ids"] == ["c-1", "c-2"]
    assert payload["eval"]["experiment_id"] == "E5"


def test_run_human_success_prints_four_lines(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "seektalent.cli.run_match",
        lambda **kwargs: _fake_bundle(),
    )

    assert main(["run", "--jd", "JD"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "/tmp/runs/20260409T120000Z_deadbeef",
        "controller_stop",
        "c-1, c-2",
        "Ready for review.",
    ]


def test_main_no_longer_treats_bare_run_flags_as_implicit_run() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--jd", "JD"])
    assert exc.value.code == 2


def test_run_reads_notes_file_before_phase_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}
    notes_file = tmp_path / "notes.md"
    notes_file.write_text("Notes from file", encoding="utf-8")

    def fake_run_match(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("captured")

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)

    assert main(["run", "--jd", "JD", "--notes-file", str(notes_file), "--json"]) == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload == {"error": "captured", "error_type": "RuntimeError"}
    assert captured["job_description"] == "JD"
    assert captured["hiring_notes"] == "Notes from file"


def test_run_passes_round_budget_to_api(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def fake_run_match(**kwargs):
        captured.update(kwargs)
        return _fake_bundle()

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)

    assert main(["run", "--jd", "JD", "--round-budget", "8", "--json"]) == 0
    json.loads(capsys.readouterr().out)
    assert captured["round_budget"] == 8


def test_run_rejects_duplicate_input_sources(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    jd_file = tmp_path / "jd.md"
    jd_file.write_text("JD", encoding="utf-8")
    assert main(["run", "--jd", "JD", "--jd-file", str(jd_file)]) == 1
    assert "Use only one of --jd or --jd-file." in capsys.readouterr().err
