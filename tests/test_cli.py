from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import ModuleType

import pytest

from seektalent import __version__
from seektalent.api import MatchRunResult
from seektalent.artifacts import ArtifactResolver
from seektalent.cli import _build_settings, _load_benchmark_directory, _load_benchmark_rows, main
from seektalent.evaluation import EvaluationResult, EvaluationStageResult
from seektalent.models import FinalResult
from seektalent.resources import REQUIRED_PROMPTS, package_prompt_dir, read_env_example_template
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
        terminal_stop_guidance=None,
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


def test_required_prompts_include_auxiliary_prompt_files() -> None:
    assert REQUIRED_PROMPTS == (
        "requirements",
        "controller",
        "scoring",
        "reflection",
        "finalize",
        "judge",
        "tui_summary",
        "candidate_feedback",
        "repair_requirements",
        "repair_controller",
        "repair_reflection",
    )


def test_required_prompts_exist_in_package_prompt_dir() -> None:
    prompt_dir = package_prompt_dir()
    for name in REQUIRED_PROMPTS:
        prompt_file = prompt_dir / f"{name}.md"
        assert prompt_file.is_file()
        assert prompt_file.read_text(encoding="utf-8").strip()


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
    assert "prf-sidecar-prefetch" in payload["commands"]
    assert "inspect" in payload["commands"]
    assert payload["environment"]["required_for_default_run"] == [
        "OPENAI_API_KEY",
        "SEEKTALENT_CTS_TENANT_KEY",
        "SEEKTALENT_CTS_TENANT_SECRET",
    ]
    assert payload["artifacts"]["top_level_files"] == [
        "runtime/trace.log",
        "runtime/events.jsonl",
        "runtime/run_config.json",
        "input/input_snapshot.json",
        "input/input_truth.json",
        "runtime/requirement_extraction_draft.json",
        "runtime/requirements_call.json",
        "runtime/requirement_sheet.json",
        "runtime/scoring_policy.json",
        "runtime/sent_query_history.json",
        "runtime/search_diagnostics.json",
        "runtime/term_surface_audit.json",
        "runtime/finalizer_context.json",
        "runtime/finalizer_call.json",
        "output/final_candidates.json",
        "output/final_answer.md",
        "output/judge_packet.json",
        "output/run_summary.md",
        "evaluation/evaluation.json",
    ]
    assert payload["artifacts"]["key_handoff_files"] == [
        "runtime/trace.log",
        "runtime/events.jsonl",
        "runtime/run_config.json",
        "output/final_answer.md",
        "output/final_candidates.json",
        "evaluation/evaluation.json",
    ]
    run_args = {item["name"]: item for item in payload["commands"]["run"]["arguments"]}
    assert run_args["--job-title"]["mutually_exclusive_with"] == ["--job-title-file"]
    assert run_args["--job-title-file"]["mutually_exclusive_with"] == ["--job-title"]
    assert run_args["--jd"]["mutually_exclusive_with"] == ["--jd-file"]
    assert run_args["--jd-file"]["mutually_exclusive_with"] == ["--jd"]
    assert run_args["--enable-eval"]["mutually_exclusive_with"] == ["--disable-eval"]
    benchmark_args = {item["name"]: item for item in payload["commands"]["benchmark"]["arguments"]}
    assert benchmark_args["--jds-file"]["default"] is None
    assert benchmark_args["--benchmarks-dir"]["default"] == "artifacts/benchmarks"
    assert benchmark_args["--benchmark-run-retries"]["default"] == 1
    assert benchmark_args["--benchmark-upload-retries"]["default"] == 1
    assert payload["json_contracts"]["run"]["stdout_success_fields"] == [
        "final_markdown",
        "run_id",
        "run_dir",
        "trace_log_path",
        "final_result",
        "evaluation_result",
    ]
    assert payload["json_contracts"]["run"]["nullable_fields"] == ["evaluation_result"]
    assert payload["json_contracts"]["benchmark"]["stdout_success_fields"] == ["count", "runs", "summary_path"]
    assert payload["json_contracts"]["benchmark"]["file_mode_fields"] == ["benchmark_file"]
    assert payload["json_contracts"]["benchmark"]["directory_mode_fields"] == ["benchmark_dir", "benchmark_files"]
    assert payload["json_contracts"]["migrate-judge-assets"]["stdout_success_fields"] == [
        "runs_scanned",
        "jd_assets_upserted",
        "resume_assets_upserted",
        "judge_labels_upserted",
        "conflicts",
        "missing_raw_resumes",
    ]
    assert payload["commands"]["prf-sidecar-prefetch"]["arguments"][0]["name"] == "--env-file"
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


def test_archive_legacy_artifacts_command_prints_plan_and_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    legacy_runs = tmp_path / "runs"
    (legacy_runs / "20260422_192141_deadbeef" / "trace.log").parent.mkdir(parents=True, exist_ok=True)

    assert main(
        [
            "archive-legacy-artifacts",
            "--project-root",
            str(tmp_path),
            "--runs-dir",
            str(legacy_runs),
            "--artifacts-dir",
            str(tmp_path / "artifacts"),
        ]
    ) == 0

    output = capsys.readouterr().out
    assert "archive_plan:" in output
    assert "archive_result:" in output
    assert (tmp_path / "artifacts" / "archive" / "archive_migration_plan.json").exists()
    assert (tmp_path / "artifacts" / "archive" / "archive_migration_result.json").exists()


def test_prf_sidecar_prefetch_command_uses_lazy_module_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SEEKTALENT_PRF_SPAN_MODEL_REVISION=rev-span",
                "SEEKTALENT_PRF_SPAN_TOKENIZER_REVISION=rev-tokenizer",
                "SEEKTALENT_PRF_EMBEDDING_MODEL_REVISION=rev-embed",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_module = ModuleType("seektalent.prf_sidecar.prefetch")
    captured: dict[str, object] = {}

    def fake_prefetch_sidecar_models(settings, cache_dir=None):
        captured["revisions"] = (
            settings.prf_span_model_revision,
            settings.prf_span_tokenizer_revision,
            settings.prf_embedding_model_revision,
        )
        captured["cache_dir"] = cache_dir
        return ["/tmp/span", "/tmp/tokenizer", "/tmp/embed"]

    fake_module.prefetch_sidecar_models = fake_prefetch_sidecar_models
    monkeypatch.setitem(sys.modules, "seektalent.prf_sidecar.prefetch", fake_module)

    assert main(
        [
            "prf-sidecar-prefetch",
            "--env-file",
            str(env_file),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 3
    assert payload["cache_paths"] == ["/tmp/span", "/tmp/tokenizer", "/tmp/embed"]
    assert captured["revisions"] == ("rev-span", "rev-tokenizer", "rev-embed")
    assert captured["cache_dir"] == tmp_path / "cache"


def test_init_writes_env_template(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"

    assert main(["init", "--env-file", str(env_file)]) == 0

    assert env_file.exists()
    text = env_file.read_text(encoding="utf-8")
    assert text == Path(".env.example").read_text(encoding="utf-8")
    assert text == read_env_example_template()
    assert "OPENAI_API_KEY=" in text
    assert "SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY=anthropic_messages_compatible" in text
    assert "SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=bailian" in text
    assert "SEEKTALENT_TEXT_LLM_ENDPOINT_KIND=bailian_anthropic_messages" in text
    assert "SEEKTALENT_TEXT_LLM_ENDPOINT_REGION=beijing" in text
    assert "SEEKTALENT_TEXT_LLM_BASE_URL_OVERRIDE=" in text
    assert "SEEKTALENT_TEXT_LLM_API_KEY=" in text
    assert "SEEKTALENT_REQUIREMENTS_MODEL_ID=deepseek-v4-pro" in text
    assert "SEEKTALENT_JUDGE_MODEL_ID=deepseek-v4-pro" in text
    assert "SEEKTALENT_CANDIDATE_FEEDBACK_MODEL_ID=qwen3.5-flash" in text
    assert "SEEKTALENT_REASONING_EFFORT=off" in text
    assert "SEEKTALENT_JUDGE_REASONING_EFFORT=high" in text
    assert "SEEKTALENT_PRF_SPAN_MODEL_NAME=fastino/gliner2-multi-v1" in text
    assert "SEEKTALENT_PRF_SIDECAR_ENDPOINT=http://127.0.0.1:8741" in text
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
    artifacts_dir = tmp_path / "artifacts"
    runs_dir = artifacts_dir / "runs"
    old_run = runs_dir / "2000" / "01" / "01" / "run_20000101TEST"
    old_benchmark_execution = artifacts_dir / "benchmark-executions" / "2000" / "01" / "01" / "benchmark_20000101TEST"
    old_run.mkdir(parents=True)
    (old_run / "trace.log").write_text("", encoding="utf-8")
    (old_benchmark_execution / "output" / "summary.json").parent.mkdir(parents=True, exist_ok=True)
    (old_benchmark_execution / "output" / "summary.json").write_text("{}", encoding="utf-8")
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
        assert not old_benchmark_execution.exists()
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
    artifacts_dir = tmp_path / "artifacts"
    runs_dir = artifacts_dir / "runs"
    benchmark_executions_dir = artifacts_dir / "benchmark-executions"
    old_run = runs_dir / "2000" / "01" / "01" / "run_20000101TEST"
    old_benchmark_execution = benchmark_executions_dir / "2000" / "01" / "01" / "benchmark_20000101TEST"
    benchmark_inputs_dir = artifacts_dir / "benchmarks"
    benchmark_file = tmp_path / "agent_jds.jsonl"
    old_run.mkdir(parents=True)
    (old_run / "trace.log").write_text("", encoding="utf-8")
    old_benchmark_execution.mkdir(parents=True)
    (old_benchmark_execution / "output" / "summary.json").parent.mkdir(parents=True, exist_ok=True)
    (old_benchmark_execution / "output" / "summary.json").write_text("{}", encoding="utf-8")
    benchmark_inputs_dir.mkdir(parents=True)
    (benchmark_inputs_dir / "agent_jds.jsonl").write_text("[]", encoding="utf-8")
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
            assert not old_benchmark_execution.exists()
            assert (benchmark_inputs_dir / "agent_jds.jsonl").exists()
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

    def fake_run_match(*, job_title: str, jd: str, notes: str = "", settings=None, env_file=".env", **kwargs) -> MatchRunResult:
        index = len(calls) + 1
        calls.append((job_title, jd, notes))
        run_dir = tmp_path / f"run-{index}"
        run_dir.mkdir()
        trace_log = run_dir / "trace.log"
        trace_log.write_text("", encoding="utf-8")
        (run_dir / "runtime").mkdir(parents=True)
        (run_dir / "runtime" / "term_surface_audit.json").write_text("{}", encoding="utf-8")
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
            terminal_stop_guidance=None,
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
    assert payload["runs"][0]["term_surface_audit_path"] == str(tmp_path / "run-1" / "runtime" / "term_surface_audit.json")
    assert payload["runs"][1]["term_surface_audit_path"] == str(tmp_path / "run-2" / "runtime" / "term_surface_audit.json")
    summary_path = Path(payload["summary_path"])
    assert summary_path.exists()
    assert "benchmark-executions" in summary_path.parts
    assert summary_path.name == "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["runs"] == payload["runs"]
    resolver = ArtifactResolver.for_root(summary_path.parents[1])
    manifest = resolver.manifest
    assert manifest.artifact_kind.value == "benchmark"
    assert manifest.logical_artifacts["output.summary"].path == "output/summary.json"
    assert [entry.model_dump(mode="json") for entry in manifest.child_artifacts] == [
        {"artifact_kind": "run", "artifact_id": "run-1", "role": "case_run", "case_id": "agent_jd_001"},
        {"artifact_kind": "run", "artifact_id": "run-2", "role": "case_run", "case_id": "agent_jd_002"},
    ]


def test_benchmark_defaults_to_benchmarks_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    benchmarks_dir = tmp_path / "artifacts" / "benchmarks"
    benchmarks_dir.mkdir(parents=True)
    benchmark_file = benchmarks_dir / "agent_jds.jsonl"
    benchmark_file.write_text(
        json.dumps({"jd_id": "agent_jd_001", "job_title": "A", "job_description": "JD A"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_run_match(*, job_title: str, jd: str, notes: str = "", settings=None, env_file=".env", **kwargs) -> MatchRunResult:
        del jd, notes, settings, env_file, kwargs
        calls.append(job_title)
        run_dir = tmp_path / "run-1"
        run_dir.mkdir()
        trace_log = run_dir / "trace.log"
        trace_log.write_text("", encoding="utf-8")
        return MatchRunResult(
            final_result=FinalResult(
                run_id="run-1",
                run_dir=str(run_dir),
                rounds_executed=1,
                stop_reason="controller_stop",
                summary="done",
                candidates=[],
            ),
            final_markdown="# Final",
            run_id="run-1",
            run_dir=run_dir,
            trace_log_path=trace_log,
            evaluation_result=None,
            terminal_stop_guidance=None,
        )

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)

    assert main(
        [
            "benchmark",
            "--output-dir",
            str(tmp_path / "runs"),
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert calls == ["A"]
    assert payload["benchmark_dir"] == str(benchmarks_dir)
    assert payload["benchmark_files"] == [str(benchmark_file)]
    assert "benchmark_file" not in payload


def test_benchmark_validates_retry_flags_before_loading_default_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    assert main(
        [
            "benchmark",
            "--output-dir",
            str(tmp_path / "runs"),
            "--benchmark-run-retries",
            "-1",
            "--json",
        ]
    ) == 1

    assert "benchmark_run_retries must be >= 0" in capsys.readouterr().err


def test_benchmark_json_directory_reports_included_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    benchmarks_dir = tmp_path / "benchmarks"
    benchmarks_dir.mkdir()
    included_file = benchmarks_dir / "agent_jds.jsonl"
    skipped_file = benchmarks_dir / "phase_2_2_pilot.jsonl"
    included_file.write_text(
        json.dumps({"jd_id": "agent_jd_001", "job_title": "A", "job_description": "JD A"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    skipped_file.write_text(
        json.dumps({"jd_id": "skip", "job_title": "Skip", "job_description": "Skip JD"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    def fake_run_match(*, job_title: str, jd: str, notes: str = "", settings=None, env_file=".env", **kwargs) -> MatchRunResult:
        del job_title, jd, notes, settings, env_file
        run_dir = tmp_path / "run-1"
        run_dir.mkdir()
        trace_log = run_dir / "trace.log"
        trace_log.write_text("", encoding="utf-8")
        return MatchRunResult(
            final_result=FinalResult(
                run_id="run-1",
                run_dir=str(run_dir),
                rounds_executed=1,
                stop_reason="controller_stop",
                summary="done",
                candidates=[],
            ),
            final_markdown="# Final",
            run_id="run-1",
            run_dir=run_dir,
            trace_log_path=trace_log,
            evaluation_result=None,
            terminal_stop_guidance=None,
        )

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)

    assert main(
        [
            "benchmark",
            "--jds-file",
            str(benchmarks_dir),
            "--output-dir",
            str(tmp_path / "runs"),
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["benchmark_dir"] == str(benchmarks_dir)
    assert payload["benchmark_files"] == [str(included_file)]
    assert "benchmark_file" not in payload
    summary = json.loads(Path(payload["summary_path"]).read_text(encoding="utf-8"))
    assert summary["benchmark_dir"] == str(benchmarks_dir)
    assert summary["benchmark_files"] == [str(included_file)]
    assert "benchmark_file" not in summary


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

    def fake_run_match(*, job_title: str, jd: str, notes: str = "", settings=None, env_file=".env", **kwargs) -> MatchRunResult:
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
            terminal_stop_guidance=None,
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


def test_benchmark_shares_one_judge_limiter_across_parallel_runs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("SEEKTALENT_ENABLE_EVAL", "true")
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
    limiters: list[object] = []
    eval_remote_logging_values: list[bool] = []

    def fake_run_match(**kwargs) -> MatchRunResult:
        limiters.append(kwargs["judge_limiter"])
        eval_remote_logging_values.append(kwargs["eval_remote_logging"])
        run_id = kwargs["job_title"].replace(" ", "-").lower()
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
            terminal_stop_guidance=None,
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
            "3",
            "--enable-eval",
            "--json",
        ]
    ) == 0

    json.loads(capsys.readouterr().out)
    assert len(limiters) == 3
    assert limiters[0] is not None
    assert all(limiter is limiters[0] for limiter in limiters)
    assert eval_remote_logging_values == [False, False, False]


def test_benchmark_starts_next_row_when_any_active_row_finishes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    benchmark_file = tmp_path / "agent_jds.jsonl"
    benchmark_file.write_text(
        "\n".join(
            [
                json.dumps({"jd_id": "row-1", "job_title": "Row 1", "job_description": "JD 1"}, ensure_ascii=False),
                json.dumps({"jd_id": "row-2", "job_title": "Row 2", "job_description": "JD 2"}, ensure_ascii=False),
                json.dumps({"jd_id": "row-3", "job_title": "Row 3", "job_description": "JD 3"}, ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    row1_started = threading.Event()
    row1_release = threading.Event()
    row2_started = threading.Event()
    row3_started = threading.Event()
    outcome: dict[str, object] = {}

    def fake_run_match(**kwargs) -> MatchRunResult:
        run_id = kwargs["job_title"].lower().replace(" ", "-")
        if run_id == "row-1":
            row1_started.set()
            assert row1_release.wait(timeout=2)
        elif run_id == "row-2":
            row2_started.set()
        elif run_id == "row-3":
            row3_started.set()
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
            terminal_stop_guidance=None,
        )

    def run_benchmark() -> None:
        outcome["exit_code"] = main(
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
        )

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)
    thread = threading.Thread(target=run_benchmark)
    thread.start()

    assert row1_started.wait(timeout=2)
    assert row2_started.wait(timeout=2)
    assert row3_started.wait(timeout=2)
    assert row3_started.is_set()
    row1_release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()

    payload = json.loads(capsys.readouterr().out)
    assert outcome["exit_code"] == 0
    assert [row["jd_id"] for row in payload["runs"]] == ["row-1", "row-2", "row-3"]
    completion_by_jd = {row["jd_id"]: row["completion_index"] for row in payload["runs"]}
    assert completion_by_jd["row-1"] > completion_by_jd["row-2"]
    assert completion_by_jd["row-1"] > completion_by_jd["row-3"]


def test_benchmark_retries_failed_row_once_and_keeps_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    benchmark_file = tmp_path / "agent_jds.jsonl"
    benchmark_file.write_text(
        json.dumps({"jd_id": "agent_jd_001", "job_title": "A", "job_description": "JD A"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    calls = 0

    def fake_run_match(**kwargs) -> MatchRunResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")
        run_dir = tmp_path / "run-1"
        run_dir.mkdir()
        trace_log = run_dir / "trace.log"
        trace_log.write_text("", encoding="utf-8")
        return MatchRunResult(
            final_result=FinalResult(
                run_id="run-1",
                run_dir=str(run_dir),
                rounds_executed=1,
                stop_reason="controller_stop",
                summary="done",
                candidates=[],
            ),
            final_markdown="# Final",
            run_id="run-1",
            run_dir=run_dir,
            trace_log_path=trace_log,
            evaluation_result=None,
            terminal_stop_guidance=None,
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
    assert calls == 2
    assert payload["runs"][0]["status"] == "succeeded"
    assert payload["runs"][0]["attempts"] == 2
    assert Path(payload["summary_path"]).exists()


def test_benchmark_returns_one_when_row_exhausts_retries(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    benchmark_file = tmp_path / "agent_jds.jsonl"
    benchmark_file.write_text(
        json.dumps({"jd_id": "agent_jd_001", "job_title": "A", "job_description": "JD A"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    def fake_run_match(**kwargs) -> MatchRunResult:
        raise RuntimeError("permanent failure")

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)

    assert (
        main(
            [
                "benchmark",
                "--jds-file",
                str(benchmark_file),
                "--output-dir",
                str(tmp_path / "runs"),
                "--json",
            ]
        )
        == 1
    )

    payload = json.loads(capsys.readouterr().out)
    assert Path(payload["summary_path"]).exists()
    assert payload["runs"][0]["status"] == "failed"
    assert payload["runs"][0]["attempts"] == 2
    assert "permanent failure" in payload["runs"][0]["error"]


def test_benchmark_uploads_eval_results_serially_in_completion_order(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("SEEKTALENT_ENABLE_EVAL", "true")
    monkeypatch.setenv("SEEKTALENT_WANDB_PROJECT", "seektalent-test")
    benchmark_file = tmp_path / "agent_jds.jsonl"
    benchmark_file.write_text(
        "\n".join(
            [
                json.dumps({"jd_id": "slow", "job_title": "Slow", "job_description": "JD Slow"}, ensure_ascii=False),
                json.dumps({"jd_id": "fast", "job_title": "Fast", "job_description": "JD Fast"}, ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    upload_order: list[str] = []
    update_report_values: list[bool] = []
    report_refresh_rows: list[list[dict[str, object]]] = []
    active_uploads = 0
    max_active_uploads = 0
    lock = threading.Lock()

    def fake_run_match(**kwargs) -> MatchRunResult:
        if kwargs["job_title"] == "Slow":
            time.sleep(0.05)
        run_id = f"{kwargs['job_title'].lower()}-run"
        run_dir = tmp_path / run_id
        (run_dir / "evaluation").mkdir(parents=True)
        (run_dir / "raw_resumes").mkdir()
        (run_dir / "evaluation" / "evaluation.json").write_text("{}", encoding="utf-8")
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
            evaluation_result=_evaluation_result(),
            terminal_stop_guidance=None,
        )

    def fake_log_evaluation_remotely(**kwargs) -> dict[str, object]:
        nonlocal active_uploads, max_active_uploads
        update_report_values.append(kwargs["update_report"])
        run_id = kwargs["evaluation"].run_id
        artifact_root = kwargs["artifact_root"]
        if artifact_root.name == "fast-run":
            run_id = "fast-run"
        if artifact_root.name == "slow-run":
            run_id = "slow-run"
        with lock:
            active_uploads += 1
            max_active_uploads = max(max_active_uploads, active_uploads)
        time.sleep(0.03)
        upload_order.append(run_id)
        with lock:
            active_uploads -= 1
        return {"run_id": run_id}

    def fake_upsert_wandb_report(*args, **kwargs) -> None:
        report_refresh_rows.append(list(kwargs["extra_rows"]))

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)
    monkeypatch.setattr("seektalent.cli.log_evaluation_remotely", fake_log_evaluation_remotely)
    monkeypatch.setattr("seektalent.cli._upsert_wandb_report", fake_upsert_wandb_report)

    assert main(
        [
            "benchmark",
            "--jds-file",
            str(benchmark_file),
            "--output-dir",
            str(tmp_path / "runs"),
            "--benchmark-max-concurrency",
            "2",
            "--enable-eval",
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert upload_order == ["fast-run", "slow-run"]
    assert update_report_values == [False, False]
    assert report_refresh_rows == [[{"run_id": "fast-run"}, {"run_id": "slow-run"}]]
    assert max_active_uploads == 1
    assert payload["runs"][0]["completion_index"] == 1
    assert payload["runs"][1]["completion_index"] == 0
    assert [row["upload_status"] for row in payload["runs"]] == ["succeeded", "succeeded"]


def test_benchmark_report_refresh_failure_does_not_fail_successful_runs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("SEEKTALENT_ENABLE_EVAL", "true")
    monkeypatch.setenv("SEEKTALENT_WANDB_PROJECT", "seektalent-test")
    benchmark_file = tmp_path / "agent_jds.jsonl"
    benchmark_file.write_text(
        json.dumps({"jd_id": "agent_jd_001", "job_title": "A", "job_description": "JD A"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    per_run_upload_called = False

    def fake_run_match(**kwargs) -> MatchRunResult:
        run_dir = tmp_path / "run-1"
        (run_dir / "evaluation").mkdir(parents=True)
        (run_dir / "raw_resumes").mkdir()
        (run_dir / "evaluation" / "evaluation.json").write_text("{}", encoding="utf-8")
        trace_log = run_dir / "trace.log"
        trace_log.write_text("", encoding="utf-8")
        return MatchRunResult(
            final_result=FinalResult(
                run_id="run-1",
                run_dir=str(run_dir),
                rounds_executed=1,
                stop_reason="controller_stop",
                summary="done",
                candidates=[],
            ),
            final_markdown="# Final",
            run_id="run-1",
            run_dir=run_dir,
            trace_log_path=trace_log,
            evaluation_result=_evaluation_result(),
            terminal_stop_guidance=None,
        )

    def fake_log_evaluation_remotely(**kwargs) -> dict[str, object]:
        nonlocal per_run_upload_called
        per_run_upload_called = True
        return {"run_id": "run-1"}

    def fail_report_refresh(*args, **kwargs) -> None:
        raise RuntimeError("report failed")

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)
    monkeypatch.setattr("seektalent.cli.log_evaluation_remotely", fake_log_evaluation_remotely)
    monkeypatch.setattr("seektalent.cli._upsert_wandb_report", fail_report_refresh)

    exit_code = main(
        [
            "benchmark",
            "--jds-file",
            str(benchmark_file),
            "--output-dir",
            str(tmp_path / "runs"),
            "--enable-eval",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert per_run_upload_called is True
    assert Path(payload["summary_path"]).exists()
    assert payload["runs"][0]["status"] == "succeeded"
    assert payload["runs"][0]["upload_status"] == "failed"
    assert "report failed" in payload["runs"][0]["upload_error"]


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


def test_build_settings_uses_launch_directory_for_workspace_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    args = type(
        "Args",
        (),
        {
            "env_file": None,
            "output_dir": "runs",
            "mock_cts": None,
            "max_rounds": None,
            "min_rounds": None,
            "scoring_max_concurrency": None,
            "search_max_pages_per_round": None,
            "search_max_attempts_per_round": None,
            "search_no_progress_limit": None,
            "enable_eval": None,
            "enable_reflection": None,
        },
    )()

    settings = _build_settings(args)

    assert settings.project_root == tmp_path
    assert settings.runs_path == tmp_path / "runs"
