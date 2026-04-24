from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from seektalent import __version__
from seektalent.api import MatchRunResult
from seektalent.evaluation import EvaluationResult, EvaluationStageResult
from seektalent.cli import _load_benchmark_directory, _load_benchmark_rows, main
from seektalent.models import FinalResult
from seektalent.resources import read_env_example_template
from seektalent.runtime.exact_llm_cache import get_cached_json, put_cached_json
from tests.settings_factory import make_settings


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SEEKTALENT_CTS_TENANT_KEY", "cts-key")
    monkeypatch.setenv("SEEKTALENT_CTS_TENANT_SECRET", "cts-secret")


def _evaluation_result() -> EvaluationResult:
    return EvaluationResult(
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
    )


def _result(tmp_path: Path, *, include_evaluation: bool = True) -> MatchRunResult:
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
        evaluation_result=_evaluation_result() if include_evaluation else None,
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


def test_no_args_tty_launches_tui_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = make_settings(llm_cache_dir=str(tmp_path / "cache"))
    put_cached_json(settings, namespace="scoring", key="k", payload={"value": 1})
    called = {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEEKTALENT_RUNTIME_MODE", "dev")
    monkeypatch.setenv("SEEKTALENT_LLM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    def _launch() -> int:
        called["launched"] = True
        assert get_cached_json(settings, namespace="scoring", key="k") is None
        return 0

    monkeypatch.setattr("seektalent.cli._launch_tui", _launch)

    assert main([]) == 0

    assert called == {"launched": True}


def test_no_args_non_tty_prints_help(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    assert main([]) == 0

    assert "seektalent exec run" in capsys.readouterr().out


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
    assert "benchmark" in payload["commands"]
    assert "doctor" in payload["commands"]
    assert "migrate-judge-assets" in payload["commands"]
    assert "inspect" in payload["commands"]
    assert payload["environment"]["required_for_default_run"] == [
        "OPENAI_API_KEY",
        "SEEKTALENT_CTS_TENANT_KEY",
        "SEEKTALENT_CTS_TENANT_SECRET",
    ]
    run_args = {item["name"]: item for item in payload["commands"]["run"]["arguments"]}
    assert run_args["--job-title"]["mutually_exclusive_with"] == ["--job-title-file"]
    assert run_args["--job-title-file"]["mutually_exclusive_with"] == ["--job-title"]
    assert run_args["--jd"]["mutually_exclusive_with"] == ["--jd-file"]
    assert run_args["--jd-file"]["mutually_exclusive_with"] == ["--jd"]
    assert run_args["--enable-eval"]["mutually_exclusive_with"] == ["--disable-eval"]
    assert payload["json_contracts"]["run"]["stdout_success_fields"] == [
        "final_markdown",
        "run_id",
        "run_dir",
        "trace_log_path",
        "final_result",
        "evaluation_result",
    ]
    assert payload["json_contracts"]["run"]["nullable_fields"] == ["evaluation_result"]
    assert payload["json_contracts"]["benchmark"]["stdout_success_fields"] == [
        "benchmark_file",
        "count",
        "runs",
        "summary_path",
    ]
    assert payload["json_contracts"]["migrate-judge-assets"]["stdout_success_fields"] == [
        "runs_scanned",
        "jd_assets_upserted",
        "resume_assets_upserted",
        "judge_labels_upserted",
        "conflicts",
        "missing_raw_resumes",
    ]
    assert payload["json_contracts"]["doctor"]["stdout_success_fields"] == ["ok", "checks"]
    assert payload["failure_contract"]["stderr_json_fields"] == ["error", "error_type"]


def test_migrate_judge_assets_json_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: dict[str, Path] = {}

    def fake_migrate_judge_assets(*, project_root: Path, runs_dir: Path) -> dict[str, object]:
        seen["project_root"] = project_root
        seen["runs_dir"] = runs_dir
        return {
            "runs_scanned": 1,
            "jd_assets_upserted": 1,
            "resume_assets_upserted": 2,
            "judge_labels_upserted": 3,
            "conflicts": [],
            "missing_raw_resumes": [],
        }

    monkeypatch.setattr("seektalent.cli.migrate_judge_assets", fake_migrate_judge_assets)

    assert main(
        [
            "migrate-judge-assets",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--project-root",
            str(tmp_path),
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert seen == {"project_root": tmp_path, "runs_dir": tmp_path / "runs"}
    assert payload["runs_scanned"] == 1
    assert payload["judge_labels_upserted"] == 3


def test_init_writes_env_template(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"

    assert main(["init", "--env-file", str(env_file)]) == 0

    assert env_file.exists()
    text = env_file.read_text(encoding="utf-8")
    assert text == Path(".env.example").read_text(encoding="utf-8")
    assert text == read_env_example_template()
    assert "OPENAI_API_KEY=" in text
    assert "OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1" in text
    assert "SEEKTALENT_REQUIREMENTS_MODEL=openai-chat:deepseek-v3.2" in text
    assert "SEEKTALENT_JUDGE_MODEL=openai-responses:gpt-5.4" in text
    assert "SEEKTALENT_JUDGE_OPENAI_BASE_URL=http://127.0.0.1:8317/v1/responses" in text
    assert "SEEKTALENT_JUDGE_OPENAI_API_KEY=" in text
    assert "SEEKTALENT_REASONING_EFFORT=off" in text
    assert "SEEKTALENT_JUDGE_REASONING_EFFORT=high" in text
    assert "SEEKTALENT_MAX_ROUNDS=10" in text
    assert "SEEKTALENT_JUDGE_MAX_CONCURRENCY=5" in text
    assert "SEEKTALENT_ENABLE_EVAL=false" in text
    assert "SEEKTALENT_WANDB_PROJECT=seektalent" in text
    assert "SEEKTALENT_WEAVE_ENTITY=frankqdwang1-personal-creations" in text
    assert "SEEKTALENT_WEAVE_PROJECT=seektalent" in text
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
        "remote_eval_logging",
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


def test_doctor_requires_wandb_auth_when_eval_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=test-key",
                "SEEKTALENT_CTS_TENANT_KEY=cts-key",
                "SEEKTALENT_CTS_TENANT_SECRET=cts-secret",
                "SEEKTALENT_ENABLE_EVAL=true",
                "SEEKTALENT_WANDB_PROJECT=seektalent",
                "SEEKTALENT_WEAVE_PROJECT=seektalent",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("seektalent.cli._wandb_auth_configured", lambda: False)

    assert main(["doctor", "--env-file", str(env_file)]) == 1

    assert "FAIL remote_eval_logging" in capsys.readouterr().out


def test_run_json_errors_emit_single_object(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_required_env(monkeypatch)

    def _boom(**kwargs):
        raise ValueError("boom")

    monkeypatch.setattr("seektalent.cli.run_match", _boom)

    assert main(["run", "--job-title", "Python Engineer", "--jd", "JD", "--notes", "Notes", "--json"]) == 1

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
        captured["job_title"] = kwargs["job_title"]
        captured["notes"] = kwargs["notes"]
        return _result(tmp_path)

    monkeypatch.setattr("seektalent.cli.run_match", _fake_run_match)

    assert main(["run", "--job-title", "Python Engineer", "--jd", "JD"]) == 0
    assert captured["job_title"] == "Python Engineer"
    assert captured["notes"] == ""
    assert "run_id: run-1" in capsys.readouterr().out


def test_exec_run_uses_existing_run_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_required_env(monkeypatch)
    captured = {}

    def _fake_run_match(**kwargs):
        captured["kwargs"] = kwargs
        return _result(tmp_path)

    monkeypatch.setattr("seektalent.cli.run_match", _fake_run_match)

    assert main(["exec", "run", "--job-title", "Python Engineer", "--jd", "JD"]) == 0

    assert captured["kwargs"]["job_title"] == "Python Engineer"
    assert captured["kwargs"]["jd"] == "JD"
    assert "run_id: run-1" in capsys.readouterr().out


def test_run_json_allows_null_evaluation_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setattr("seektalent.cli.run_match", lambda **kwargs: _result(tmp_path, include_evaluation=False))

    assert main(["run", "--job-title", "Python Engineer", "--jd", "JD", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["evaluation_result"] is None


def test_run_cleans_runtime_artifacts_before_run_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_required_env(monkeypatch)
    runs_dir = tmp_path / "runs"
    old_run = runs_dir / "20000101_000000_deadbeef"
    old_summary = runs_dir / "benchmark_summary_20000101_000000.json"
    old_run.mkdir(parents=True)
    (old_run / "trace.log").write_text("", encoding="utf-8")
    old_summary.write_text("{}", encoding="utf-8")
    seed_settings = make_settings(
        runtime_mode="prod",
        runs_dir=str(runs_dir),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    put_cached_json(seed_settings, namespace="scoring", key="k", payload={"value": 1})
    monkeypatch.setenv("SEEKTALENT_RUNTIME_MODE", "prod")
    monkeypatch.setenv("SEEKTALENT_LLM_CACHE_DIR", str(tmp_path / "cache"))

    def _fake_run_match(**kwargs):
        settings = kwargs["settings"]
        assert get_cached_json(settings, namespace="scoring", key="k") is None
        assert not old_run.exists()
        assert not old_summary.exists()
        return _result(tmp_path)

    monkeypatch.setattr("seektalent.cli.run_match", _fake_run_match)

    assert main(
        [
            "run",
            "--job-title",
            "Python Engineer",
            "--jd",
            "JD",
            "--env-file",
            str(tmp_path / "missing.env"),
            "--output-dir",
            str(runs_dir),
        ]
    ) == 0

    assert "run_id: run-1" in capsys.readouterr().out


def test_benchmark_cleans_runtime_artifacts_before_first_run_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_required_env(monkeypatch)
    runs_dir = tmp_path / "runs"
    old_run = runs_dir / "20000101_000000_deadbeef"
    old_summary = runs_dir / "benchmark_summary_20000101_000000.json"
    benchmark_file = tmp_path / "agent_jds.jsonl"
    old_run.mkdir(parents=True)
    (old_run / "trace.log").write_text("", encoding="utf-8")
    old_summary.write_text("{}", encoding="utf-8")
    benchmark_file.write_text(
        json.dumps({"jd_id": "agent_jd_001", "job_title": "A", "job_description": "JD A"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    seed_settings = make_settings(
        runtime_mode="prod",
        runs_dir=str(runs_dir),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    put_cached_json(seed_settings, namespace="requirements", key="k", payload={"value": 1})
    monkeypatch.setenv("SEEKTALENT_RUNTIME_MODE", "prod")
    monkeypatch.setenv("SEEKTALENT_LLM_CACHE_DIR", str(tmp_path / "cache"))
    calls = 0

    def _fake_run_match(**kwargs):
        nonlocal calls
        if calls == 0:
            settings = kwargs["settings"]
            assert get_cached_json(settings, namespace="requirements", key="k") is None
            assert not old_run.exists()
            assert not old_summary.exists()
        calls += 1
        return _result(tmp_path)

    monkeypatch.setattr("seektalent.cli.run_match", _fake_run_match)

    assert main(
        [
            "benchmark",
            "--jds-file",
            str(benchmark_file),
            "--env-file",
            str(tmp_path / "missing.env"),
            "--output-dir",
            str(runs_dir),
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert calls == 1
    assert payload["count"] == 1


def test_load_benchmark_directory_skips_generated_and_temporary_files(tmp_path: Path) -> None:
    benchmarks_dir = tmp_path / "benchmarks"
    benchmarks_dir.mkdir()
    (benchmarks_dir / "agent_jds.jsonl").write_text(
        json.dumps({"jd_id": "agent_1", "job_title": "Agent", "job_description": "Agent JD"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    (benchmarks_dir / "bigdata.jsonl").write_text(
        json.dumps({"jd_id": "bigdata_1", "job_title": "Bigdata", "job_description": "Bigdata JD"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    for name in ("phase_2_2_pilot.jsonl", "scratch.tmp.jsonl", "agent.only.jsonl", "small.subset.jsonl"):
        (benchmarks_dir / name).write_text(
            json.dumps({"jd_id": name, "job_title": "Skip", "job_description": "Skip JD"}, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )

    rows, files = _load_benchmark_directory(benchmarks_dir)

    assert [row["jd_id"] for row in rows] == ["agent_1", "bigdata_1"]
    assert [Path(file).name for file in files] == ["agent_jds.jsonl", "bigdata.jsonl"]
    assert rows[0]["benchmark_file"] == str(benchmarks_dir / "agent_jds.jsonl")
    assert rows[0]["benchmark_group"] == "agent_jds"
    assert rows[0]["input_index"] == 0
    assert rows[1]["benchmark_group"] == "bigdata"
    assert rows[1]["input_index"] == 1


def test_load_benchmark_file_preserves_explicit_group_and_adds_source_metadata(tmp_path: Path) -> None:
    benchmark_file = tmp_path / "custom.jsonl"
    benchmark_file.write_text(
        json.dumps(
            {
                "jd_id": "custom_1",
                "job_title": "Custom",
                "job_description": "Custom JD",
                "benchmark_group": "manual_group",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    rows = _load_benchmark_rows(benchmark_file)

    assert rows == [
        {
            "jd_id": "custom_1",
            "job_title": "Custom",
            "job_description": "Custom JD",
            "benchmark_group": "manual_group",
            "benchmark_file": str(benchmark_file),
            "input_index": 0,
        }
    ]


def test_benchmark_json_runs_rows_sequentially(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    benchmark_file = tmp_path / "agent_jds.jsonl"
    benchmark_file.write_text(
        "\n".join(
            [
                json.dumps({"jd_id": "agent_jd_001", "job_title": "A", "job_description": "JD A", "hiring_notes": "N1"}, ensure_ascii=False),
                json.dumps({"jd_id": "agent_jd_002", "job_title": "B", "job_description": "JD B", "hiring_notes": ""}, ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    calls: list[tuple[str, str, str]] = []

    def fake_run_match(*, job_title: str, jd: str, notes: str = "", settings=None, env_file=".env") -> MatchRunResult:
        index = len(calls) + 1
        calls.append((job_title, jd, notes))
        run_dir = tmp_path / f"run-{index}"
        run_dir.mkdir()
        trace_log = run_dir / "trace.log"
        trace_log.write_text("", encoding="utf-8")
        (run_dir / "term_surface_audit.json").write_text("{}", encoding="utf-8")
        return MatchRunResult(
            final_result=FinalResult(
                run_id=f"run-{index}",
                run_dir=str(run_dir),
                rounds_executed=1,
                stop_reason="controller_stop",
                summary="done",
                candidates=[],
            ),
            final_markdown="# Final",
            run_id=f"run-{index}",
            run_dir=run_dir,
            trace_log_path=trace_log,
            evaluation_result=None,
        )

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)

    assert main(
        [
            "benchmark",
            "--jds-file",
            str(benchmark_file),
            "--output-dir",
            str(tmp_path / "runs"),
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert calls == [("A", "JD A", "N1"), ("B", "JD B", "")]
    assert payload["count"] == 2
    assert payload["runs"][0]["jd_id"] == "agent_jd_001"
    assert payload["runs"][1]["jd_id"] == "agent_jd_002"
    assert payload["runs"][0]["term_surface_audit_path"] == str(tmp_path / "run-1" / "term_surface_audit.json")
    assert payload["runs"][1]["term_surface_audit_path"] == str(tmp_path / "run-2" / "term_surface_audit.json")
    assert Path(payload["summary_path"]).exists()
    summary = json.loads(Path(payload["summary_path"]).read_text(encoding="utf-8"))
    assert summary["runs"] == payload["runs"]


def test_benchmark_json_can_run_rows_in_parallel(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    benchmark_file = tmp_path / "agent_jds.jsonl"
    benchmark_file.write_text(
        "\n".join(
            json.dumps(
                {"jd_id": f"agent_jd_{index:03d}", "job_title": f"Role {index}", "job_description": f"JD {index}"},
                ensure_ascii=False,
            )
            for index in range(1, 4)
        )
        + "\n",
        encoding="utf-8",
    )
    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_run_match(*, job_title: str, jd: str, notes: str = "", settings=None, env_file=".env") -> MatchRunResult:
        nonlocal active, max_active
        del jd, notes, settings, env_file
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        run_id = job_title.replace(" ", "-").lower()
        run_dir = tmp_path / run_id
        run_dir.mkdir()
        trace_log = run_dir / "trace.log"
        trace_log.write_text("", encoding="utf-8")
        return MatchRunResult(
            final_result=FinalResult(
                run_id=run_id,
                run_dir=str(run_dir),
                rounds_executed=1,
                stop_reason="controller_stop",
                summary="done",
                candidates=[],
            ),
            final_markdown="# Final",
            run_id=run_id,
            run_dir=run_dir,
            trace_log_path=trace_log,
            evaluation_result=None,
        )

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)

    assert main(
        [
            "benchmark",
            "--jds-file",
            str(benchmark_file),
            "--output-dir",
            str(tmp_path / "runs"),
            "--benchmark-max-concurrency",
            "2",
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert max_active == 2
    assert [row["jd_id"] for row in payload["runs"]] == ["agent_jd_001", "agent_jd_002", "agent_jd_003"]


def test_run_hides_human_eval_summary_when_evaluation_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setattr("seektalent.cli.run_match", lambda **kwargs: _result(tmp_path, include_evaluation=False))

    assert main(["run", "--job-title", "Python Engineer", "--jd", "JD"]) == 0

    output = capsys.readouterr().out
    assert "evaluation:" not in output
    assert "run_id: run-1" in output


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
        captured["job_title"] = kwargs["job_title"]
        captured["notes"] = kwargs["notes"]
        return _result(tmp_path)

    monkeypatch.setattr("seektalent.cli.run_match", _fake_run_match)

    assert main(["run", "--job-title", "Python Engineer", "--jd", "JD", "--notes-file", str(notes_file)]) == 0
    assert captured["job_title"] == "Python Engineer"
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

    assert main(["run", "--job-title", "Python Engineer", "--jd", "JD", "--jd-file", str(jd_file), "--notes", "Notes"]) == 1
    assert "Use only one of --jd or --jd-file." in capsys.readouterr().err


def test_run_fails_fast_with_missing_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SEEKTALENT_CTS_TENANT_KEY", raising=False)
    monkeypatch.delenv("SEEKTALENT_CTS_TENANT_SECRET", raising=False)

    assert main(["run", "--job-title", "Python Engineer", "--jd", "JD", "--env-file", str(tmp_path / "missing.env")]) == 1

    error = capsys.readouterr().err
    assert "Missing required environment variables" in error
    assert "OPENAI_API_KEY" in error
    assert "SEEKTALENT_CTS_TENANT_KEY" in error
    assert "SEEKTALENT_CTS_TENANT_SECRET" in error


def test_run_rejects_mock_cts_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["run", "--job-title", "Python Engineer", "--jd", "JD", "--mock-cts"])

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
        captured["job_title"] = kwargs["job_title"]
        captured["env_file"] = kwargs["env_file"]
        captured["runs_dir"] = kwargs["settings"].runs_dir
        return _result(tmp_path)

    monkeypatch.setattr("seektalent.cli.run_match", _fake_run_match)

    assert main(
        [
            "run",
            "--job-title",
            "Python Engineer",
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
    assert captured["job_title"] == "Python Engineer"
    assert captured["runs_dir"] == str((tmp_path / "explicit-runs").resolve())
    assert "run_id: run-1" in capsys.readouterr().out
