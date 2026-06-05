from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from seektalent import __version__
from seektalent.api import MatchRunResult, run_match as api_run_match
from seektalent.artifacts import ArtifactResolver, ArtifactStore
from seektalent.cli import (
    _build_settings,
    _load_benchmark_directory,
    _load_benchmark_rows,
    _local_product_path_writable,
    main,
)
from seektalent.corpus.documents import build_jd_document_row
from seektalent.corpus.store import CorpusStore
from seektalent.evaluation import EvaluationResult, EvaluationStageResult
from seektalent.models import FinalResult
from seektalent.resources import REQUIRED_PROMPTS, package_prompt_dir, read_env_example_template
from seektalent.runtime.exact_llm_cache import get_cached_json, put_cached_json
from tests.settings_factory import make_settings

LIEPIN_ENV_TEMPLATE_KEYS = [
    "SEEKTALENT_PROVIDER_NAME",
    "SEEKTALENT_LIEPIN_WORKER_MODE",
    "SEEKTALENT_LIEPIN_ALLOW_FAKE_FIXTURE_WORKER",
    "SEEKTALENT_LIEPIN_WORKER_BASE_URL",
    "SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND",
    "SEEKTALENT_LIEPIN_OPENCLI_COMMAND",
    "SEEKTALENT_LIEPIN_OPENCLI_SESSION",
    "SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_HOSTS_JSON",
    "SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_START_URLS_JSON",
    "SEEKTALENT_LIEPIN_OPENCLI_MAX_ACTIONS_PER_TASK",
    "SEEKTALENT_LIEPIN_OPENCLI_MAX_PAGES_PER_TASK",
    "SEEKTALENT_LIEPIN_OPENCLI_MAX_CARDS_PER_TASK",
    "SEEKTALENT_LIEPIN_OPENCLI_TIMEOUT_SECONDS",
    "SEEKTALENT_LIEPIN_OPENCLI_DETAIL_OPEN_TIMEOUT_SECONDS",
    "SEEKTALENT_LIEPIN_OPENCLI_IDLE_CLOSE_SECONDS",
    "SEEKTALENT_LIEPIN_OPENCLI_CLOSE_BLANK_WINDOW",
    "SEEKTALENT_LIEPIN_WORKER_HOST",
    "SEEKTALENT_LIEPIN_WORKER_PORT",
    "SEEKTALENT_LIEPIN_WORKER_STARTUP_TIMEOUT_SECONDS",
    "SEEKTALENT_LIEPIN_WORKER_TIMEOUT_SECONDS",
    "SEEKTALENT_LIEPIN_CONNECTOR_DB_PATH",
    "SEEKTALENT_LIEPIN_SESSION_STORE_DIR",
    "SEEKTALENT_LIEPIN_SESSION_STORE_KEY_ID",
    "SEEKTALENT_LIEPIN_API_TOKEN",
    "SEEKTALENT_LIEPIN_DETAIL_OPEN_APPROVAL_SECRET",
    "SEEKTALENT_LIEPIN_DEFAULT_DAILY_DETAIL_BUDGET",
    "SEEKTALENT_LIEPIN_LIVE_ENABLED",
]

def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_TEXT_LLM_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("SEEKTALENT_CTS_TENANT_KEY", "cts-key")
    monkeypatch.setenv("SEEKTALENT_CTS_TENANT_SECRET", "cts-secret")


def _clear_cts_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEEKTALENT_CTS_TENANT_KEY", raising=False)
    monkeypatch.delenv("SEEKTALENT_CTS_TENANT_SECRET", raising=False)


def _liepin_fake_fixture_env() -> str:
    return "\n".join(
        [
            "SEEKTALENT_TEXT_LLM_API_KEY=test-key",
            "SEEKTALENT_PROVIDER_NAME=liepin",
            "SEEKTALENT_LIEPIN_WORKER_MODE=fake_fixture",
            "SEEKTALENT_LIEPIN_ALLOW_FAKE_FIXTURE_WORKER=true",
        ]
    ) + "\n"


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


def _result_from_case_session(case_session, *, include_evaluation: bool = True) -> MatchRunResult:
    run_dir = case_session.root
    trace_log_path, trace_handle = case_session.open_text_stream("runtime.trace_log")
    trace_handle.close()
    return MatchRunResult(
        final_result=FinalResult(
            run_id=case_session.manifest.artifact_id,
            run_dir=str(run_dir),
            rounds_executed=1,
            stop_reason="controller_stop",
            summary="done",
            candidates=[],
        ),
        final_markdown="# Final",
        run_id=case_session.manifest.artifact_id,
        run_dir=run_dir,
        trace_log_path=trace_log_path,
        evaluation_result=_evaluation_result() if include_evaluation else None,
        terminal_stop_guidance=None,
    )


def test_api_run_match_uses_injected_artifact_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from seektalent.runtime.orchestrator import RunArtifacts
    from seektalent.runtime import orchestrator as orchestrator_module

    case_session = ArtifactStore(tmp_path / "artifacts").create_root(
        kind="run",
        display_name="benchmark case",
        producer="WorkflowRuntime",
    )

    class FakeRuntime:
        def __init__(self, settings, judge_limiter=None, eval_remote_logging=True) -> None:
            del judge_limiter, eval_remote_logging
            self.settings = settings

        def run(self, *, job_title: str, jd: str, notes: str, progress_callback=None) -> RunArtifacts:
            del job_title, jd, notes, progress_callback
            tracer = orchestrator_module.RunTracer(self.settings.artifacts_path)
            try:
                return RunArtifacts(
                    final_result=FinalResult(
                        run_id=tracer.run_id,
                        run_dir=str(tracer.run_dir),
                        rounds_executed=1,
                        stop_reason="controller_stop",
                        summary="done",
                        candidates=[],
                    ),
                    final_markdown="# Final",
                    run_id=tracer.run_id,
                    run_dir=tracer.run_dir,
                    trace_log_path=tracer.trace_log_path,
                    candidate_store={},
                    normalized_store={},
                    evaluation_result=None,
                    terminal_stop_guidance=None,
                )
            finally:
                tracer.close()

    monkeypatch.setattr("seektalent.api.build_source_enabled_runtime", FakeRuntime)

    settings = make_settings(artifacts_dir=str(tmp_path / "artifacts"))
    result = api_run_match(
        job_title="Python Engineer",
        jd="JD",
        settings=settings,
        env_file=None,
        artifact_session=case_session,
    )

    assert result.run_id == case_session.manifest.artifact_id
    assert result.run_dir == case_session.root
    assert result.trace_log_path == case_session.root / "runtime" / "trace.log"
    assert case_session.load_manifest().status == "completed"


def test_main_shows_root_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "seektalent" in help_text
    assert "update" in help_text
    assert "inspect" in help_text
    assert "SEEKTALENT_TEXT_LLM_API_KEY" in help_text
    assert "seektalent doctor" in help_text
    assert "seektalent inspect --json" in help_text
    assert "pi-agent" not in help_text
    assert "live-pi-agent" not in help_text


def test_doctor_help_does_not_advertise_removed_pi_live_probe(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["doctor", "--help"])
    assert exc.value.code == 0

    help_text = capsys.readouterr().out
    assert "live-pi-agent" not in help_text
    assert "pi-agent" not in help_text


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
        "prf_probe_phrase_proposal",
        "workbench_note_writer",
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
    assert "llm-prf-live-validate" in payload["commands"]
    assert "prf-sidecar-prefetch" not in payload["commands"]
    assert "inspect" in payload["commands"]
    assert "workbench" in payload["commands"]
    assert "pi-agent" not in payload["commands"]
    assert "live-pi-agent" not in json.dumps(payload)
    assert "liepin-smoke" in payload["commands"]
    liepin_smoke_examples = "\n".join(payload["commands"]["liepin-smoke"]["examples"])
    assert "--worker-mode opencli" in liepin_smoke_examples
    assert "pi-agent" not in liepin_smoke_examples
    assert payload["environment"]["required_for_default_run"] == [
        "SEEKTALENT_TEXT_LLM_API_KEY",
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
    live_prf_args = {item["name"]: item for item in payload["commands"]["llm-prf-live-validate"]["arguments"]}
    assert live_prf_args["--cases"]["required"] is True
    assert live_prf_args["--output-dir"]["required"] is True
    assert live_prf_args["--env-file"]["default"] == ".env"
    assert payload["json_contracts"]["doctor"]["stdout_success_fields"] == ["ok", "checks"]
    assert payload["failure_contract"]["stderr_json_fields"] == ["error", "error_type"]
    local_product = payload["local_product"]
    assert local_product["contract_version"] == "local-product-contract-v1"
    assert local_product["entrypoints"] == ["cli", "local_workbench"]
    assert local_product["data_root_posture"]["overall_status"] in {"safe", "warning", "error", "unknown"}
    source_lanes = payload["runtime_source_lanes"]
    assert source_lanes["contract_version"] == "runtime-source-lane-v1"
    assert source_lanes["default_sources"] == ["cts"]
    assert source_lanes["supported_sources"] == ["cts", "liepin"]

    root_names = set(local_product["data_root_posture"]["roots"])
    assert {
        "artifacts",
        "legacy_runs",
        "llm_cache",
        "flywheel_db",
        "corpus_db",
        "workbench_db",
        "liepin_connector_db",
        "liepin_session_store",
        "workbench_backups",
        "browser_session_metadata",
        "logs",
    } <= root_names
    for root_payload in local_product["data_root_posture"]["roots"].values():
        assert {"kind", "status", "reason_code", "path", "exists", "writable"} <= set(root_payload)


def test_inspect_json_defaults_to_packaged_frontend(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inspect", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["local_product"]["default_frontend"] == "packaged_static"


def test_workbench_help_uses_packaged_launcher(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["workbench", "--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "Start the local SeekTalent Workbench" in output
    assert "--host" in output
    assert "--port" in output


def test_workbench_command_runs_packaged_frontend_in_prod(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = []
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    class Completed:
        returncode = 0

    def fake_run(argv, *, check, env):
        assert check is False
        calls.append((argv, env))
        return Completed()

    monkeypatch.setattr("seektalent.cli._console_script_path", lambda name: name)
    monkeypatch.setattr("seektalent.cli.subprocess.run", fake_run)

    assert main(["workbench", "--port", "8123"]) == 0

    argv, env = calls[0]
    assert argv[0] == "seektalent-ui-api"
    assert "--serve-frontend" in argv
    assert argv[argv.index("--runtime-mode") + 1] == "prod"
    assert argv[argv.index("--port") + 1] == "8123"
    assert argv[argv.index("--liepin-worker-mode") + 1] == "opencli"
    assert argv[argv.index("--liepin-browser-action-backend") + 1] == "opencli"
    assert "--liepin-opencli-command" not in argv
    for name in (
        "SEEKTALENT_LIEPIN_API_TOKEN",
        "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET",
        "SEEKTALENT_LIEPIN_STREAM_TOKEN_SECRET",
    ):
        assert env[name]
        assert env[name] not in {"local-development", "local-development-liepin-api-token"}
    assert (home / ".seektalent" / "workbench-secrets.env").exists()


def test_inspect_json_hides_flywheel_root_in_prod(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SEEKTALENT_RUNTIME_MODE", "prod")
    monkeypatch.delenv("SEEKTALENT_ENABLE_FLYWHEEL", raising=False)

    assert main(["inspect", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    roots = payload["local_product"]["data_root_posture"]["roots"]
    assert "flywheel_db" not in roots
    assert "corpus_db" in roots


def test_inspect_json_does_not_leak_provider_secrets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_values = {
        "SEEKTALENT_TEXT_LLM_API_KEY": "secret-text-key-123",
        "SEEKTALENT_CTS_TENANT_KEY": "secret-cts-key-123",
        "SEEKTALENT_CTS_TENANT_SECRET": "secret-cts-secret-123",
        "SEEKTALENT_LIEPIN_API_TOKEN": "secret-liepin-token-123",
        "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "secret-binding-secret-123",
        "SEEKTALENT_LIEPIN_STREAM_TOKEN_SECRET": "secret-stream-secret-123",
    }
    for key, value in secret_values.items():
        monkeypatch.setenv(key, value)

    assert main(["inspect", "--json"]) == 0

    output = capsys.readouterr().out
    for value in secret_values.values():
        assert value not in output


def test_inspect_json_reports_roots_when_full_settings_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "SEEKTALENT_REQUIREMENTS_MODEL=openai-chat:legacy\n"
        "SEEKTALENT_WORKSPACE_ROOT=custom-data-root\n",
        encoding="utf-8",
    )

    assert main(["inspect", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    local_product = payload["local_product"]
    assert local_product["settings_source"] == "root_only_fallback"
    assert "custom-data-root" in local_product["data_root_posture"]["roots"]["workbench_db"]["path"]
    assert {
        "artifacts",
        "legacy_runs",
        "llm_cache",
        "flywheel_db",
        "corpus_db",
        "workbench_db",
        "liepin_connector_db",
        "liepin_session_store",
        "workbench_backups",
        "browser_session_metadata",
        "logs",
    } <= set(local_product["data_root_posture"]["roots"])


def test_corpus_export_command_materializes_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    home_dir = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home_dir))
    db_path = tmp_path / ".seektalent" / "corpus.sqlite3"
    artifacts_root = home_dir / "corpus-artifacts"
    store = CorpusStore(db_path)
    jd_doc_id = store.upsert_jd_document(
        build_jd_document_row(
            tenant_id="local",
            workspace_id="default",
            job_title="Backend Engineer",
            jd_text="Python APIs",
            notes_text="",
            source_kind="manual_input",
            source_ref=None,
        )
    )
    assert jd_doc_id
    store.close()

    result = main(
        [
            "corpus-export",
            "--corpus-db",
            str(db_path),
            "--artifacts-dir",
            "~/corpus-artifacts",
            "--tenant-id",
            "local",
            "--workspace-id",
            "default",
        ]
    )

    assert result == 0
    export_roots = list((artifacts_root / "corpus").glob("*/*/*/corpus_*"))
    assert export_roots
    assert not (tmp_path / "~").exists()
    export_manifest = json.loads((export_roots[0] / "corpus/export_manifest.json").read_text(encoding="utf-8"))
    assert export_manifest["corpus_artifact_role"] == "materialized_export"
    assert export_manifest["self_contained"] is False
    assert export_manifest["raw_payload_policy"] == "external_refs_only"

    assert main(
        [
            "corpus-export",
            "--corpus-db",
            str(db_path),
            "--artifacts-dir",
            "runs",
            "--tenant-id",
            "local",
            "--workspace-id",
            "default",
        ]
    ) == 1
    assert "legacy runs/ root is decommissioned" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()


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


def test_llm_prf_live_validate_command_dispatches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, list[str]] = {}

    def fake_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr("seektalent.candidate_feedback.llm_prf_bakeoff.main", fake_main)

    result = main(
        [
            "llm-prf-live-validate",
            "--cases",
            str(tmp_path / "cases.jsonl"),
            "--output-dir",
            str(tmp_path / "out"),
            "--env-file",
            str(tmp_path / ".env"),
        ]
    )

    assert result == 0
    assert "--live" in captured["argv"]
    assert "llm-prf-input" in captured["argv"]


def test_init_writes_env_template(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"

    assert main(["init", "--env-file", str(env_file)]) == 0

    assert env_file.exists()
    text = env_file.read_text(encoding="utf-8")
    assert text == Path(".env.example").read_text(encoding="utf-8")
    assert text == read_env_example_template()
    assert "SEEKTALENT_TEXT_LLM_API_KEY=" in text
    assert "SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY=openai_chat_completions_compatible" in text
    assert "SEEKTALENT_TEXT_LLM_ENDPOINT_KIND=bailian_openai_chat_completions" in text
    assert "SEEKTALENT_TEXT_LLM_ENDPOINT_REGION=beijing" in text
    assert "SEEKTALENT_CANDIDATE_FEEDBACK_MODEL_ID=deepseek-v4-flash" in text
    assert "SEEKTALENT_WORKBENCH_NOTE_WRITER_MODEL_ID=deepseek-v4-flash" in text
    assert "SEEKTALENT_WORKBENCH_NOTE_WRITER_REASONING_EFFORT=off" in text
    assert "SEEKTALENT_REQUIREMENTS_MODEL_ID=deepseek-v4-pro" in text
    assert "SEEKTALENT_JUDGE_MODEL_ID=deepseek-v4-pro" in text
    assert "SEEKTALENT_REQUIREMENTS_MODEL=" not in text
    assert "SEEKTALENT_JUDGE_OPENAI_BASE_URL=" not in text
    assert "SEEKTALENT_REASONING_EFFORT=off" in text
    assert "SEEKTALENT_JUDGE_REASONING_EFFORT=off" in text
    assert "SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MODEL_ID=deepseek-v4-flash" in text
    assert "SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_TIMEOUT_SECONDS=3.0" in text
    assert "SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_LIVE_HARNESS_TIMEOUT_SECONDS=30.0" in text
    assert "SEEKTALENT_MAX_ROUNDS=10" in text
    assert "SEEKTALENT_JUDGE_MAX_CONCURRENCY=5" in text
    assert "SEEKTALENT_ENABLE_EVAL=false" in text
    for key in LIEPIN_ENV_TEMPLATE_KEYS:
        assert f"{key}=" in text
    assert "SEEKTALENT_WANDB_PROJECT=seektalent" in text
    assert "SEEKTALENT_WEAVE_ENTITY=frankqdwang1-personal-creations" in text
    assert "SEEKTALENT_WEAVE_PROJECT=seektalent" in text
    assert str(env_file) in capsys.readouterr().out


def test_optional_runtime_env_vars_use_new_text_llm_keys() -> None:
    from seektalent.cli import OPTIONAL_RUNTIME_ENV_VARS

    assert "SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY" in OPTIONAL_RUNTIME_ENV_VARS
    assert "SEEKTALENT_WORKSPACE_ROOT" in OPTIONAL_RUNTIME_ENV_VARS
    assert "SEEKTALENT_ARTIFACTS_DIR" in OPTIONAL_RUNTIME_ENV_VARS
    assert "SEEKTALENT_RUNTIME_MODE" in OPTIONAL_RUNTIME_ENV_VARS
    assert "SEEKTALENT_LLM_CACHE_DIR" in OPTIONAL_RUNTIME_ENV_VARS
    assert "SEEKTALENT_REQUIREMENTS_MODEL_ID" in OPTIONAL_RUNTIME_ENV_VARS
    assert "SEEKTALENT_JUDGE_MODEL_ID" in OPTIONAL_RUNTIME_ENV_VARS
    assert "SEEKTALENT_WORKBENCH_NOTE_WRITER_MODEL_ID" in OPTIONAL_RUNTIME_ENV_VARS
    assert "SEEKTALENT_WORKBENCH_NOTE_WRITER_REASONING_EFFORT" in OPTIONAL_RUNTIME_ENV_VARS
    for key in LIEPIN_ENV_TEMPLATE_KEYS:
        assert key not in OPTIONAL_RUNTIME_ENV_VARS
    assert "SEEKTALENT_REQUIREMENTS_MODEL" not in OPTIONAL_RUNTIME_ENV_VARS
    assert "SEEKTALENT_JUDGE_OPENAI_BASE_URL" not in OPTIONAL_RUNTIME_ENV_VARS


def test_init_refuses_to_overwrite_without_force(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=existing\n", encoding="utf-8")

    assert main(["init", "--env-file", str(env_file)]) == 1

    assert "already exists" in capsys.readouterr().err


def test_doctor_json_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SEEKTALENT_TEXT_LLM_API_KEY=test-key\nSEEKTALENT_CTS_TENANT_KEY=cts-key\nSEEKTALENT_CTS_TENANT_SECRET=cts-secret\n",
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
        "local_data_roots",
    }


def test_doctor_json_does_not_leak_provider_secrets(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / ".env"
    secret_values = {
        "SEEKTALENT_TEXT_LLM_API_KEY": "secret-text-key-123",
        "SEEKTALENT_CTS_TENANT_KEY": "secret-cts-key-123",
        "SEEKTALENT_CTS_TENANT_SECRET": "secret-cts-secret-123",
        "SEEKTALENT_LIEPIN_API_TOKEN": "secret-liepin-token-123",
        "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "secret-binding-secret-123",
        "SEEKTALENT_LIEPIN_STREAM_TOKEN_SECRET": "secret-stream-secret-123",
    }
    env_file.write_text(
        "\n".join(f"{key}={value}" for key, value in secret_values.items()) + "\n",
        encoding="utf-8",
    )

    assert main(["doctor", "--env-file", str(env_file), "--output-dir", str(tmp_path / "runs"), "--json"]) == 0

    output = capsys.readouterr().out
    for value in secret_values.values():
        assert value not in output


def test_local_product_writable_checks_existing_file(tmp_path: Path) -> None:
    db_path = tmp_path / "workbench.sqlite3"
    db_path.write_text("sqlite placeholder", encoding="utf-8")
    db_path.chmod(0o444)

    assert not _local_product_path_writable(db_path)


def test_doctor_fails_for_missing_real_cts_credentials(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SEEKTALENT_TEXT_LLM_API_KEY=test-key\n", encoding="utf-8")

    assert main(["doctor", "--env-file", str(env_file)]) == 1

    assert "FAIL cts_credentials" in capsys.readouterr().out


def test_doctor_liepin_provider_does_not_require_cts_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _clear_cts_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(_liepin_fake_fixture_env(), encoding="utf-8")

    assert main(["doctor", "--env-file", str(env_file), "--output-dir", str(tmp_path / "runs"), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    cts_check = next(item for item in payload["checks"] if item["name"] == "cts_credentials")
    assert payload["ok"] is True
    assert cts_check["ok"] is True
    assert "SEEKTALENT_CTS_TENANT_KEY" not in cts_check["message"]
    assert "SEEKTALENT_CTS_TENANT_SECRET" not in cts_check["message"]


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
                "SEEKTALENT_TEXT_LLM_API_KEY=test-key",
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


def test_doctor_json_reports_legacy_text_llm_migration_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SEEKTALENT_REQUIREMENTS_MODEL=openai-chat:deepseek-v3.2\n",
        encoding="utf-8",
    )

    assert main(["doctor", "--env-file", str(env_file), "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    settings_check = next(item for item in payload["checks"] if item["name"] == "settings")
    assert settings_check["ok"] is False
    assert "legacy text-llm config detected" in settings_check["message"]


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
        calls.append((job_title, jd, notes))
        case_session = kwargs["artifact_session"]
        run_dir = case_session.root
        (run_dir / "runtime").mkdir(parents=True, exist_ok=True)
        (run_dir / "runtime" / "term_surface_audit.json").write_text("{}", encoding="utf-8")
        return _result_from_case_session(case_session, include_evaluation=False)

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
    assert payload["runs"][0]["term_surface_audit_path"] == str(
        Path(payload["runs"][0]["run_dir"]) / "runtime" / "term_surface_audit.json"
    )
    assert payload["runs"][1]["term_surface_audit_path"] == str(
        Path(payload["runs"][1]["run_dir"]) / "runtime" / "term_surface_audit.json"
    )
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
    expected_child_rows = [
        {
            "artifact_kind": "run",
            "artifact_id": payload["runs"][0]["run_id"],
            "role": "case_run",
            "case_id": "agent_jd_001",
        },
        {
            "artifact_kind": "run",
            "artifact_id": payload["runs"][1]["run_id"],
            "role": "case_run",
            "case_id": "agent_jd_002",
        },
    ]
    assert [entry.model_dump(mode="json") for entry in manifest.child_artifacts] == [
        *expected_child_rows,
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


def test_benchmark_settings_migration_failure_still_emits_failed_rows_with_child_run_linkage(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text("SEEKTALENT_REQUIREMENTS_MODEL=openai-chat:deepseek-v3.2\n", encoding="utf-8")
    benchmark_file = tmp_path / "agent_jds.jsonl"
    benchmark_file.write_text(
        json.dumps({"jd_id": "agent_jd_001", "job_title": "A", "job_description": "JD A"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    called = False

    def fail_if_called(**kwargs) -> MatchRunResult:
        nonlocal called
        called = True
        raise AssertionError("run_match should not execute when benchmark settings migration fails")

    monkeypatch.setattr("seektalent.cli.run_match", fail_if_called)

    assert (
        main(
            [
                "benchmark",
                "--jds-file",
                str(benchmark_file),
                "--env-file",
                str(env_file),
                "--output-dir",
                str(tmp_path / "runs"),
                "--json",
            ]
        )
        == 1
    )

    payload = json.loads(capsys.readouterr().out)
    assert called is False
    assert payload["runs"][0]["status"] == "failed"
    assert "legacy text-llm config detected" in payload["runs"][0]["error"]
    assert payload["runs"][0]["run_id"].startswith("run_")
    assert Path(payload["runs"][0]["run_dir"]).exists()
    assert Path(payload["runs"][0]["trace_log_path"]).exists()
    resolver = ArtifactResolver.for_root(Path(payload["summary_path"]).parents[1])
    assert [entry.model_dump(mode="json") for entry in resolver.manifest.child_artifacts] == [
        {
            "artifact_kind": "run",
            "artifact_id": payload["runs"][0]["run_id"],
            "role": "case_run",
            "case_id": "agent_jd_001",
        }
    ]


def test_benchmark_liepin_provider_preflight_does_not_require_cts_credentials(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_cts_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(_liepin_fake_fixture_env(), encoding="utf-8")
    benchmark_file = tmp_path / "agent_jds.jsonl"
    benchmark_file.write_text(
        json.dumps({"jd_id": "agent_jd_001", "job_title": "A", "job_description": "JD A"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    providers: list[str] = []

    def fake_run_match(*, settings=None, **kwargs) -> MatchRunResult:
        providers.append(settings.provider_name)
        return _result_from_case_session(kwargs["artifact_session"], include_evaluation=False)

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)

    assert (
        main(
            [
                "benchmark",
                "--jds-file",
                str(benchmark_file),
                "--env-file",
                str(env_file),
                "--output-dir",
                str(tmp_path / "runs"),
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert providers == ["liepin"]
    assert payload["runs"][0]["status"] == "succeeded"


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
    assert payload["runs"][0]["run_id"].startswith("run_")
    assert Path(payload["runs"][0]["run_dir"]).exists()
    assert Path(payload["runs"][0]["trace_log_path"]).exists()
    resolver = ArtifactResolver.for_root(Path(payload["summary_path"]).parents[1])
    child_rows = [entry.model_dump(mode="json") for entry in resolver.manifest.child_artifacts]
    assert len(child_rows) == 2
    assert child_rows[-1] == {
        "artifact_kind": "run",
        "artifact_id": payload["runs"][0]["run_id"],
        "role": "case_run",
        "case_id": "agent_jd_001",
    }


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
    monkeypatch.delenv("SEEKTALENT_TEXT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SEEKTALENT_CTS_TENANT_KEY", raising=False)
    monkeypatch.delenv("SEEKTALENT_CTS_TENANT_SECRET", raising=False)

    assert main(["run", "--job-title", "Python Engineer", "--jd", "JD", "--env-file", str(tmp_path / "missing.env")]) == 1

    error = capsys.readouterr().err
    assert "Missing required environment variables" in error
    assert "SEEKTALENT_TEXT_LLM_API_KEY" in error
    assert "SEEKTALENT_CTS_TENANT_KEY" in error
    assert "SEEKTALENT_CTS_TENANT_SECRET" in error


def test_run_liepin_provider_does_not_require_cts_credentials(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    _clear_cts_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(_liepin_fake_fixture_env(), encoding="utf-8")
    providers: list[str] = []

    def fake_run_match(**kwargs) -> MatchRunResult:
        providers.append(kwargs["settings"].provider_name)
        return _result(tmp_path)

    monkeypatch.setattr("seektalent.cli.run_match", fake_run_match)

    assert (
        main(
            [
                "run",
                "--job-title",
                "Python Engineer",
                "--jd",
                "JD",
                "--env-file",
                str(env_file),
                "--output-dir",
                str(tmp_path / "runs"),
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert providers == ["liepin"]
    assert "SEEKTALENT_CTS_TENANT_KEY" not in captured.err
    assert "SEEKTALENT_CTS_TENANT_SECRET" not in captured.err


def test_run_rejects_mock_cts_flag(capsys: pytest.CaptureFixture[str]) -> None:
    status = main(["run", "--job-title", "Python Engineer", "--jd", "JD", "--mock-cts"])

    assert status == 2
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
