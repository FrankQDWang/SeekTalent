from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from seektalent import __version__
from seektalent.cli import main
from seektalent.progress import make_progress_event


def _minimal_llm_env() -> str:
    return "OPENAI_API_KEY=test-openai-key\n"


def _request_payload() -> dict[str, object]:
    return {
        "job_description": "JD",
        "hiring_notes": "Notes",
        "top_k": 2,
        "round_budget": 6,
    }


def _fake_bundle() -> object:
    payload = {
        "phase": "v0.3.3_active",
        "run_id": "20260409T120000Z_deadbeef",
        "run_dir": "/tmp/runs/20260409T120000Z_deadbeef",
        "bootstrap": {"input_truth": {"job_description": "JD"}},
        "rounds": [],
        "finalization_audit": {"model_name": "test"},
        "final_result": {
            "final_candidate_cards": [
                {"candidate_id": "c-1", "review_recommendation": "advance", "must_have_matrix": [], "preferred_evidence": [], "gap_signals": [], "risk_signals": [], "card_summary": "Advance"},
                {"candidate_id": "c-2", "review_recommendation": "hold", "must_have_matrix": [], "preferred_evidence": [], "gap_signals": [], "risk_signals": [], "card_summary": "Hold"},
            ],
            "reviewer_summary": "Reviewer summary: 1 advance-ready, 1 need manual review, 0 reject",
            "run_summary": "Ready for review.",
            "stop_reason": "controller_stop",
        },
        "eval": {"experiment_id": "E5", "metrics": []},
    }
    return SimpleNamespace(
        run_dir=payload["run_dir"],
        final_result=SimpleNamespace(
            final_candidate_cards=payload["final_result"]["final_candidate_cards"],
            reviewer_summary=payload["final_result"]["reviewer_summary"],
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
    assert "Human entry" in help_text
    assert "Agent entry" in help_text
    assert "inspect" in help_text


def test_run_help_describes_primary_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["run", "--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "Read a full JSON request object from a file." in help_text
    assert "Read a full JSON request object from stdin." in help_text
    assert "Progress stream format for stderr." in help_text
    assert "Final candidate cards live at:" in help_text


def test_main_launches_tui_without_args_in_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("seektalent.cli._is_interactive_terminal", lambda: True)
    monkeypatch.setattr("seektalent.cli._launch_tui", lambda: 0)
    assert main([]) == 0


def test_unknown_command_prints_guidance(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["rn"]) == 2
    stderr = capsys.readouterr().err
    assert "unknown_command: rn" in stderr
    assert "Did you mean: run?" in stderr
    assert "seektalent --help" in stderr


def test_removed_inline_flag_prints_guidance(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--jd", "JD"]) == 2
    stderr = capsys.readouterr().err
    assert "removed_flag: --jd is no longer supported." in stderr
    assert "--request-file" in stderr


def test_version_command_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_update_command_prints_upgrade_instructions(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["update"]) == 0
    output = capsys.readouterr().out
    assert f"Current version: {__version__}" in output
    assert "pip install -U seektalent" in output
    assert "pipx upgrade seektalent" in output


def test_inspect_command_points_to_protocol(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inspect"]) == 0
    output = capsys.readouterr().out
    assert "Human entry: `seektalent`" in output
    assert "--request-file ./request.json --json --progress jsonl" in output


def test_inspect_json_returns_machine_readable_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(_minimal_llm_env(), encoding="utf-8")

    assert main(["inspect", "--env-file", str(env_file), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tool"] == "seektalent"
    assert payload["version"] == __version__
    assert payload["phase"] == "v0.3.3_active"
    assert payload["interactive_entry"]["command"] == "seektalent"
    assert payload["interactive_entry"]["submit_key"] == "Shift+Enter"
    assert payload["non_interactive_entry"]["command"].endswith("--json --progress jsonl")
    assert payload["result_pointer"] == "final_result.final_candidate_cards"
    assert payload["request_contract"]["preferred"] == "--request-file"
    assert payload["progress_contract"]["channel"] == "stderr"
    assert payload["llm_callpoints"]["requirement_extraction"]["provider"] == "openai"
    run_args = {item["name"]: item for item in payload["commands"]["run"]["arguments"]}
    assert run_args["--request-file"]["mutually_exclusive_with"] == ["--request-stdin", "--jd-file"]
    assert run_args["--progress"]["default"] == "auto"
    assert payload["json_contracts"]["run"]["result_pointer"] == "final_result.final_candidate_cards"


def test_init_writes_env_template(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"
    assert main(["init", "--env-file", str(env_file)]) == 0
    assert env_file.exists()
    text = env_file.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=" in text
    assert "SEEKTALENT_REQUIREMENT_EXTRACTION_PROVIDER=openai" in text
    assert str(env_file) in capsys.readouterr().out


def test_init_refuses_to_overwrite_without_force(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SEEKTALENT_MOCK_CTS=true\n", encoding="utf-8")
    assert main(["init", "--env-file", str(env_file)]) == 1
    assert "already exists" in capsys.readouterr().err


def test_doctor_json_success_in_mock_mode(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(_minimal_llm_env() + "SEEKTALENT_MOCK_CTS=true\n", encoding="utf-8")
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


def test_run_json_request_file_success_emits_search_run_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(_request_payload()), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run_match(**kwargs):
        captured.update(kwargs)
        return _fake_bundle()

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)
    assert main(["run", "--request-file", str(request_file), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["phase"] == "v0.3.3_active"
    assert [card["candidate_id"] for card in payload["final_result"]["final_candidate_cards"]] == ["c-1", "c-2"]
    assert captured["top_k"] == 2
    assert captured["round_budget"] == 6


def test_run_request_stdin_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("seektalent.cli.run_match", lambda **kwargs: _fake_bundle())
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_request_payload())))
    assert main(["run", "--request-stdin", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["final_result"]["stop_reason"] == "controller_stop"


def test_run_human_success_prints_compact_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(_request_payload()), encoding="utf-8")
    monkeypatch.setattr("seektalent.cli.run_match", lambda **kwargs: _fake_bundle())
    assert main(["run", "--request-file", str(request_file)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "run_dir: /tmp/runs/20260409T120000Z_deadbeef",
        "stop_reason: controller_stop",
        "top_candidates:",
        "- c-1\tadvance",
        "- c-2\thold",
        "reviewer_summary: Reviewer summary: 1 advance-ready, 1 need manual review, 0 reject",
        "run_summary: Ready for review.",
    ]


def test_run_progress_jsonl_writes_stderr_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(_request_payload()), encoding="utf-8")

    def fake_run_match(**kwargs):
        kwargs["progress_callback"](
            make_progress_event(
                "controller_decision",
                "controller: selected core_precision",
                round_index=0,
                payload={"selected_operator_name": "core_precision"},
            )
        )
        return _fake_bundle()

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)
    assert main(["run", "--request-file", str(request_file), "--json", "--progress", "jsonl"]) == 0
    stderr_lines = capsys.readouterr().err.splitlines()
    assert len(stderr_lines) == 1
    event = json.loads(stderr_lines[0])
    assert event["type"] == "controller_decision"
    assert event["round_index"] == 0


def test_run_passes_round_budget_override_to_api(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(_request_payload()), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_run_match(**kwargs):
        captured.update(kwargs)
        return _fake_bundle()

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)
    assert main(["run", "--request-file", str(request_file), "--round-budget", "8", "--json"]) == 0
    json.loads(capsys.readouterr().out)
    assert captured["round_budget"] == 8


def test_run_rejects_duplicate_input_sources(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(_request_payload()), encoding="utf-8")
    jd_file = tmp_path / "jd.md"
    jd_file.write_text("JD", encoding="utf-8")
    assert main(["run", "--request-file", str(request_file), "--jd-file", str(jd_file)]) == 1
    stderr = capsys.readouterr().err
    assert "conflicting_inputs: Choose exactly one input source" in stderr


def test_run_requires_input(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run"]) == 1
    stderr = capsys.readouterr().err
    assert "missing_input: run requires --request-file, --request-stdin, or --jd-file." in stderr


def test_run_invalid_flag_prints_guidance(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--bad"]) == 2
    stderr = capsys.readouterr().err
    assert "invalid_run_arguments: unrecognized arguments: --bad" in stderr
    assert "Try: seektalent run --help" in stderr
    assert "--request-file ./request.json --json --progress jsonl" in stderr


def test_run_missing_request_file_prints_guidance(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_file = tmp_path / "missing-request.json"
    assert main(["run", "--request-file", str(missing_file)]) == 1
    stderr = capsys.readouterr().err
    assert f"missing_request_file: request file not found: {missing_file}" in stderr
    assert "Try: seektalent run --request-file ./request.json" in stderr


def test_run_invalid_request_json_prints_example(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request_file = tmp_path / "request.json"
    request_file.write_text("{bad json", encoding="utf-8")
    assert main(["run", "--request-file", str(request_file)]) == 1
    stderr = capsys.readouterr().err
    assert "invalid_request_json" in stderr
    assert '"job_description"' in stderr
