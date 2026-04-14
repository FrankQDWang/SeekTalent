from __future__ import annotations

import json
from pathlib import Path

import pytest

from seektalent import __version__
from seektalent.api import MatchRunResult
from seektalent.evaluation import EvaluationResult, EvaluationStageResult
from seektalent.cli import main
from seektalent.models import FinalResult


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SEEKTALENT_CTS_TENANT_KEY", "cts-key")
    monkeypatch.setenv("SEEKTALENT_CTS_TENANT_SECRET", "cts-secret")


def _result(tmp_path: Path) -> MatchRunResult:
    trace_log = tmp_path / "trace.log"
    trace_log.write_text("", encoding="utf-8")
    return MatchRunResult(
        final_result=FinalResult(
            run_id="run-1",
            run_dir=str(tmp_path),
            rounds_executed=2,
            stop_reason="controller_stop",
            summary="done",
            candidates=[],
        ),
        final_markdown="# Final",
        run_id="run-1",
        run_dir=tmp_path,
        trace_log_path=trace_log,
        evaluation_result=EvaluationResult(
            run_id="run-1",
            judge_model="openai-chat:deepseek-v3.2",
            jd_sha256="jd",
            round_01=EvaluationStageResult(
                stage="round_01",
                ndcg_at_10=0.5,
                precision_at_10=0.4,
                total_score=0.43,
                candidates=[],
            ),
            final=EvaluationStageResult(
                stage="final",
                ndcg_at_10=0.7,
                precision_at_10=0.6,
                total_score=0.63,
                candidates=[],
            ),
        ),
    )


def test_main_shows_root_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "seektalent" in help_text
    assert "update" in help_text
    assert "inspect" in help_text
    assert "OPENAI_API_KEY" in help_text
    assert "seektalent doctor" in help_text
    assert "seektalent inspect --json" in help_text


def test_version_command_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip()


def test_update_command_prints_upgrade_instructions(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["update"]) == 0

    output = capsys.readouterr().out
    assert f"Current version: {__version__}" in output
    assert "pip install -U seektalent" in output
    assert f"pip install -U seektalent=={__version__}" in output
    assert "pipx upgrade seektalent" in output
    assert "does not modify your environment" in output


def test_inspect_command_points_to_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inspect"]) == 0

    output = capsys.readouterr().out
    assert "SeekTalent published CLI inspection summary" in output
    assert "seektalent inspect --json" in output


def test_inspect_json_returns_machine_readable_contract(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inspect", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["tool"] == "seektalent"
    assert payload["version"] == __version__
    assert payload["recommended_workflow"][-1] == "seektalent update"
    assert "run" in payload["commands"]
    assert "doctor" in payload["commands"]
    assert "inspect" in payload["commands"]
    assert payload["environment"]["required_for_default_run"] == [
        "OPENAI_API_KEY",
        "SEEKTALENT_CTS_TENANT_KEY",
        "SEEKTALENT_CTS_TENANT_SECRET",
    ]
    run_args = {item["name"]: item for item in payload["commands"]["run"]["arguments"]}
    assert run_args["--jd"]["mutually_exclusive_with"] == ["--jd-file"]
    assert run_args["--jd-file"]["mutually_exclusive_with"] == ["--jd"]
    assert payload["json_contracts"]["run"]["stdout_success_fields"] == [
        "final_markdown",
        "run_id",
        "run_dir",
        "trace_log_path",
        "final_result",
        "evaluation_result",
    ]
    assert payload["json_contracts"]["doctor"]["stdout_success_fields"] == ["ok", "checks"]
    assert payload["failure_contract"]["stderr_json_fields"] == ["error", "error_type"]


def test_init_writes_env_template(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"

    assert main(["init", "--env-file", str(env_file)]) == 0

    assert env_file.exists()
    text = env_file.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=" in text
    assert "OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1" in text
    assert "SEEKTALENT_REQUIREMENTS_MODEL=openai-chat:deepseek-v3.2" in text
    assert "SEEKTALENT_JUDGE_MODEL=openai-responses:gpt-5.4" in text
    assert "SEEKTALENT_JUDGE_OPENAI_BASE_URL=http://127.0.0.1:8317/v1/responses" in text
    assert "SEEKTALENT_REASONING_EFFORT=off" in text
    assert "SEEKTALENT_JUDGE_REASONING_EFFORT=high" in text
    assert "SEEKTALENT_MAX_ROUNDS=10" in text
    assert "SEEKTALENT_WANDB_PROJECT=seektalent-resume-eval" in text
    assert str(env_file) in capsys.readouterr().out


def test_init_refuses_to_overwrite_without_force(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=existing\n", encoding="utf-8")

    assert main(["init", "--env-file", str(env_file)]) == 1

    assert "already exists" in capsys.readouterr().err


def test_doctor_json_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=test-key\nSEEKTALENT_CTS_TENANT_KEY=cts-key\nSEEKTALENT_CTS_TENANT_SECRET=cts-secret\n",
        encoding="utf-8",
    )

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
    assert {item["name"] for item in payload["checks"]} >= {
        "packaged_prompts",
        "default_spec",
        "settings",
        "output_dir",
        "provider_credentials",
        "cts_credentials",
    }


def test_doctor_fails_for_missing_real_cts_credentials(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=test-key\n", encoding="utf-8")

    assert main(["doctor", "--env-file", str(env_file)]) == 1

    assert "FAIL cts_credentials" in capsys.readouterr().out


def test_doctor_rejects_mock_cts_from_env_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SEEKTALENT_MOCK_CTS=true\n", encoding="utf-8")

    assert main(["doctor", "--env-file", str(env_file)]) == 1

    assert "Mock CTS is not available in the published CLI." in capsys.readouterr().out


def test_run_supports_legacy_alias_and_json_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setattr("seektalent.cli.run_match", lambda **kwargs: _result(tmp_path))

    assert main(["--jd", "JD", "--notes", "Notes", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == "run-1"
    assert payload["final_markdown"] == "# Final"


def test_run_json_errors_emit_single_object(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_required_env(monkeypatch)

    def _boom(**kwargs):
        raise ValueError("boom")

    monkeypatch.setattr("seektalent.cli.run_match", _boom)

    assert main(["run", "--jd", "JD", "--notes", "Notes", "--json"]) == 1

    payload = json.loads(capsys.readouterr().err)
    assert payload == {"error": "boom", "error_type": "ValueError"}


def test_run_allows_missing_notes_and_defaults_empty_string(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_required_env(monkeypatch)
    captured = {}

    def _fake_run_match(**kwargs):
        captured["notes"] = kwargs["notes"]
        return _result(tmp_path)

    monkeypatch.setattr("seektalent.cli.run_match", _fake_run_match)

    assert main(["run", "--jd", "JD"]) == 0
    assert captured["notes"] == ""
    assert "run_id: run-1" in capsys.readouterr().out


def test_run_reads_notes_file_without_inline_notes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_required_env(monkeypatch)
    captured = {}
    notes_file = tmp_path / "notes.md"
    notes_file.write_text("Notes from file", encoding="utf-8")

    def _fake_run_match(**kwargs):
        captured["notes"] = kwargs["notes"]
        return _result(tmp_path)

    monkeypatch.setattr("seektalent.cli.run_match", _fake_run_match)

    assert main(["run", "--jd", "JD", "--notes-file", str(notes_file)]) == 0
    assert captured["notes"] == "Notes from file"
    assert "run_id: run-1" in capsys.readouterr().out


def test_run_rejects_duplicate_input_sources(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    _set_required_env(monkeypatch)
    jd_file = tmp_path / "jd.md"
    jd_file.write_text("JD", encoding="utf-8")

    assert main(["run", "--jd", "JD", "--jd-file", str(jd_file), "--notes", "Notes"]) == 1
    assert "Use only one of --jd or --jd-file." in capsys.readouterr().err


def test_run_fails_fast_with_missing_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SEEKTALENT_CTS_TENANT_KEY", raising=False)
    monkeypatch.delenv("SEEKTALENT_CTS_TENANT_SECRET", raising=False)

    assert main(["run", "--jd", "JD", "--env-file", str(tmp_path / "missing.env")]) == 1

    error = capsys.readouterr().err
    assert "Missing required environment variables" in error
    assert "OPENAI_API_KEY" in error
    assert "SEEKTALENT_CTS_TENANT_KEY" in error
    assert "SEEKTALENT_CTS_TENANT_SECRET" in error


def test_run_rejects_mock_cts_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["run", "--jd", "JD", "--mock-cts"])

    assert exc.value.code == 2
    assert "unrecognized arguments: --mock-cts" in capsys.readouterr().err


def test_output_dir_flag_overrides_env_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_required_env(monkeypatch)
    env_file = tmp_path / "custom.env"
    env_file.write_text("SEEKTALENT_RUNS_DIR=from-env\n", encoding="utf-8")
    captured = {}

    def _fake_run_match(**kwargs):
        captured["env_file"] = kwargs["env_file"]
        captured["runs_dir"] = kwargs["settings"].runs_dir
        return _result(tmp_path)

    monkeypatch.setattr("seektalent.cli.run_match", _fake_run_match)

    assert main(
        [
            "run",
            "--jd",
            "JD",
            "--notes",
            "Notes",
            "--env-file",
            str(env_file),
            "--output-dir",
            str(tmp_path / "explicit-runs"),
        ]
    ) == 0

    assert captured["env_file"] == str(env_file)
    assert captured["runs_dir"] == str((tmp_path / "explicit-runs").resolve())
    assert "run_id: run-1" in capsys.readouterr().out
