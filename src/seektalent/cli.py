from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import threading
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from queue import Queue
from typing import Any, Callable, Protocol, cast

from pydantic import ValidationError

from seektalent import __version__
from seektalent.api import MatchRunResult, run_match
from seektalent.artifacts import ArtifactSession, ArtifactStore
from seektalent.artifacts.legacy import execute_archive_migration
from seektalent.config import (
    DEV_ARTIFACTS_DIR,
    DEV_LLM_CACHE_DIR,
    DEV_RUNS_DIR,
    PROD_ARTIFACTS_DIR,
    PROD_LLM_CACHE_DIR,
    PROD_RUNS_DIR,
    AppSettings,
    RuntimeMode,
    TextLLMConfigMigrationError,
    _packaged_runtime_forces_prod,
    evaluate_local_data_root_policy,
    load_process_env,
)
from seektalent.corpus.runtime import materialize_corpus_artifacts
from seektalent.corpus.store import CorpusStore
from seektalent.core.retrieval.provider_contract import SearchRequest, SearchResult
from seektalent.evaluation import AsyncJudgeLimiter, _upsert_wandb_report, log_evaluation_remotely
from seektalent.flywheel.datasets import export_query_rewriting_dataset
from seektalent.flywheel.store import FlywheelStore
from seektalent.providers.liepin.client import build_liepin_worker_client
from seektalent.providers.liepin.compliance import ComplianceGate
from seektalent.providers.liepin.policy import LiepinCardCandidate, build_detail_open_plan
from seektalent.providers.liepin.store import LiepinStore
from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError
from seektalent.resources import (
    REQUIRED_PROMPTS,
    package_prompt_dir,
    package_spec_file,
    read_env_example_template,
    resolve_user_path,
)
from seektalent.runtime.lifecycle import cleanup_runtime_artifacts

PROVIDER_ENV_VAR_BY_PROTOCOL_FAMILY = {
    "openai_chat_completions_compatible": "SEEKTALENT_TEXT_LLM_API_KEY",
    "anthropic_messages_compatible": "SEEKTALENT_TEXT_LLM_API_KEY",
}
OPTIONAL_RUNTIME_ENV_VARS = [
    "SEEKTALENT_CTS_BASE_URL",
    "SEEKTALENT_CTS_TIMEOUT_SECONDS",
    "SEEKTALENT_CTS_SPEC_PATH",
    "SEEKTALENT_PROVIDER_NAME",
    "SEEKTALENT_LIEPIN_WORKER_MODE",
    "SEEKTALENT_LIEPIN_ALLOW_FAKE_FIXTURE_WORKER",
    "SEEKTALENT_LIEPIN_WORKER_BASE_URL",
    "SEEKTALENT_LIEPIN_WORKER_HOST",
    "SEEKTALENT_LIEPIN_WORKER_PORT",
    "SEEKTALENT_LIEPIN_WORKER_STARTUP_TIMEOUT_SECONDS",
    "SEEKTALENT_LIEPIN_WORKER_TIMEOUT_SECONDS",
    "SEEKTALENT_LIEPIN_CONNECTOR_DB_PATH",
    "SEEKTALENT_LIEPIN_SESSION_STORE_DIR",
    "SEEKTALENT_LIEPIN_SESSION_STORE_KEY_ID",
    "SEEKTALENT_LIEPIN_API_TOKEN",
    "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET",
    "SEEKTALENT_LIEPIN_STREAM_TOKEN_SECRET",
    "SEEKTALENT_LIEPIN_DETAIL_OPEN_APPROVAL_SECRET",
    "SEEKTALENT_LIEPIN_DEFAULT_DAILY_DETAIL_BUDGET",
    "SEEKTALENT_LIEPIN_LIVE_ENABLED",
    "SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY",
    "SEEKTALENT_TEXT_LLM_PROVIDER_LABEL",
    "SEEKTALENT_TEXT_LLM_ENDPOINT_KIND",
    "SEEKTALENT_TEXT_LLM_ENDPOINT_REGION",
    "SEEKTALENT_TEXT_LLM_BASE_URL_OVERRIDE",
    "SEEKTALENT_WORKSPACE_ROOT",
    "SEEKTALENT_ARTIFACTS_DIR",
    "SEEKTALENT_RUNTIME_MODE",
    "SEEKTALENT_LLM_CACHE_DIR",
    "SEEKTALENT_REQUIREMENTS_MODEL_ID",
    "SEEKTALENT_CONTROLLER_MODEL_ID",
    "SEEKTALENT_SCORING_MODEL_ID",
    "SEEKTALENT_FINALIZE_MODEL_ID",
    "SEEKTALENT_REFLECTION_MODEL_ID",
    "SEEKTALENT_STRUCTURED_REPAIR_MODEL_ID",
    "SEEKTALENT_JUDGE_MODEL_ID",
    "SEEKTALENT_TUI_SUMMARY_MODEL_ID",
    "SEEKTALENT_CANDIDATE_FEEDBACK_MODEL_ID",
    "SEEKTALENT_WORKBENCH_NOTE_WRITER_MODEL_ID",
    "SEEKTALENT_WORKBENCH_NOTE_WRITER_REASONING_EFFORT",
    "SEEKTALENT_REASONING_EFFORT",
    "SEEKTALENT_JUDGE_REASONING_EFFORT",
    "SEEKTALENT_MIN_ROUNDS",
    "SEEKTALENT_MAX_ROUNDS",
    "SEEKTALENT_SCORING_MAX_CONCURRENCY",
    "SEEKTALENT_JUDGE_MAX_CONCURRENCY",
    "SEEKTALENT_SEARCH_MAX_PAGES_PER_ROUND",
    "SEEKTALENT_SEARCH_MAX_ATTEMPTS_PER_ROUND",
    "SEEKTALENT_SEARCH_NO_PROGRESS_LIMIT",
    "SEEKTALENT_ENABLE_EVAL",
    "SEEKTALENT_ENABLE_REFLECTION",
    "SEEKTALENT_WANDB_ENTITY",
    "SEEKTALENT_WANDB_PROJECT",
    "SEEKTALENT_WEAVE_ENTITY",
    "SEEKTALENT_WEAVE_PROJECT",
    "SEEKTALENT_RUNS_DIR",
]
TOP_LEVEL_ARTIFACT_FILES = [
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
KEY_HANDOFF_FILES = [
    "runtime/trace.log",
    "runtime/events.jsonl",
    "runtime/run_config.json",
    "output/final_answer.md",
    "output/final_candidates.json",
    "evaluation/evaluation.json",
]
DEFAULT_BENCHMARKS_DIR = Path("artifacts/benchmarks")
SKIPPED_BENCHMARK_FILE_PATTERNS = (
    "phase_*.jsonl",
    "*.tmp.jsonl",
    "*.only.jsonl",
    "*.subset.jsonl",
)
ROOT_HELP_EPILOG = """Primary workflow:
  1. seektalent doctor
  2. seektalent
  3. seektalent exec run --job-title-file ./job_title.md --jd-file ./jd.md
  4. seektalent exec benchmark

Required environment variables:
  SEEKTALENT_TEXT_LLM_API_KEY
  SEEKTALENT_CTS_TENANT_KEY
  SEEKTALENT_CTS_TENANT_SECRET

Inputs:
  Provide the job title with --job-title or --job-title-file, and the job description with --jd or --jd-file.

Artifacts:
  Runs write structured outputs under ./runs by default or --output-dir when set.

Upgrade:
  seektalent update

Machine-readable discovery:
  seektalent inspect --json
"""
KNOWN_COMMANDS = {
    "run",
    "benchmark",
    "archive-legacy-artifacts",
    "flywheel-export",
    "corpus-export",
    "llm-prf-live-validate",
    "init",
    "doctor",
    "version",
    "update",
    "inspect",
    "liepin-compliance-gate",
    "liepin-replay-fixtures",
    "liepin-bun-compatibility-gate",
    "liepin-smoke",
}
_NO_ARG_DEFAULT = object()


class _LiepinSmokeWorkerClient(Protocol):
    async def ensure_ready(self, *, on_event: Any = None) -> None: ...

    async def session_status(
        self,
        *,
        connection_id: str,
        tenant: str | None = None,
        workspace: str | None = None,
        provider_account_hash: str | None = None,
    ) -> object: ...

    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult: ...


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    message: str


@dataclass
class BenchmarkAttempt:
    row: dict[str, object]
    attempt: int
    started_at: str


@dataclass
class BenchmarkUploadTask:
    result_row: dict[str, object]
    result: MatchRunResult


@dataclass
class BenchmarkCaseRun:
    row: dict[str, object]
    session: ArtifactSession
    trace_log_path: Path


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


class BenchmarkUploader:
    def __init__(self, *, settings: AppSettings, retries: int) -> None:
        self.settings = settings
        self.retries = retries
        self.report_rows: list[dict[str, Any]] = []
        self.uploaded_result_rows: list[dict[str, object]] = []
        self.queue: Queue[BenchmarkUploadTask | None] = Queue()
        self.thread = threading.Thread(target=self._work, name="seektalent-benchmark-uploader")
        self.thread.start()

    def submit(self, task: BenchmarkUploadTask) -> None:
        self.queue.put(task)

    def close(self) -> None:
        self.queue.put(None)
        self.thread.join()
        if self.report_rows:
            try:
                _upsert_wandb_report(self.settings, extra_rows=self.report_rows)
            except Exception as exc:  # noqa: BLE001
                error = _error_text(exc)
                for row in self.uploaded_result_rows:
                    row["upload_status"] = "failed"
                    row["upload_error"] = error

    def _work(self) -> None:
        while True:
            task = self.queue.get()
            try:
                if task is None:
                    return
                self._upload(task)
            finally:
                self.queue.task_done()

    def _upload(self, task: BenchmarkUploadTask) -> None:
        attempts = 0
        last_error = ""
        for attempt in range(1, self.retries + 2):
            attempts = attempt
            try:
                if task.result.evaluation_result is None:
                    task.result_row["upload_status"] = "skipped"
                    task.result_row["upload_attempts"] = 0
                    return
                report_row = log_evaluation_remotely(
                    settings=self.settings,
                    artifact_root=task.result.run_dir,
                    evaluation=task.result.evaluation_result,
                    rounds_executed=task.result.final_result.rounds_executed,
                    terminal_stop_guidance=task.result.terminal_stop_guidance,
                    update_report=False,
                )
                if report_row is not None:
                    self.report_rows.append(report_row)
                task.result_row["upload_status"] = "succeeded"
                task.result_row["upload_attempts"] = attempts
                task.result_row.pop("upload_error", None)
                self.uploaded_result_rows.append(task.result_row)
                return
            except Exception as exc:  # noqa: BLE001
                last_error = _error_text(exc)
        task.result_row["upload_status"] = "failed"
        task.result_row["upload_attempts"] = attempts
        task.result_row["upload_error"] = last_error


def _arg_spec(
    name: str,
    kind: str,
    description: str,
    *,
    required: bool = False,
    repeatable: bool = False,
    mutually_exclusive_with: list[str] | None = None,
    default: object = _NO_ARG_DEFAULT,
    applies_to: str | None = None,
) -> dict[str, object]:
    spec: dict[str, object] = {
        "name": name,
        "kind": kind,
        "required": required,
        "repeatable": repeatable,
        "mutually_exclusive_with": mutually_exclusive_with or [],
        "description": description,
    }
    if default is not _NO_ARG_DEFAULT:
        spec["default"] = default
    if applies_to:
        spec["applies_to"] = applies_to
    return spec


def _build_settings(args: argparse.Namespace) -> AppSettings:
    workspace_root = Path.cwd().resolve()
    output_dir = getattr(args, "output_dir", None)
    output_path = resolve_user_path(output_dir) if output_dir else None
    artifacts_root = output_path.parent if output_path is not None and output_path.name == "runs" else output_path
    overrides = {
        "workspace_root": str(workspace_root),
        "mock_cts": getattr(args, "mock_cts", None),
        "max_rounds": getattr(args, "max_rounds", None),
        "min_rounds": getattr(args, "min_rounds", None),
        "scoring_max_concurrency": getattr(args, "scoring_max_concurrency", None),
        "search_max_pages_per_round": getattr(args, "search_max_pages_per_round", None),
        "search_max_attempts_per_round": getattr(args, "search_max_attempts_per_round", None),
        "search_no_progress_limit": getattr(args, "search_no_progress_limit", None),
        "enable_eval": getattr(args, "enable_eval", None),
        "enable_reflection": getattr(args, "enable_reflection", None),
        "artifacts_dir": str(artifacts_root) if artifacts_root is not None else None,
        "runs_dir": str(output_path) if output_path is not None else None,
    }
    return AppSettings(_env_file=args.env_file).with_overrides(**overrides)


def _read_text(*, inline_value: str | None, file_value: str | None, label: str) -> str:
    if inline_value is not None and file_value is not None:
        raise ValueError(f"Use only one of --{label} or --{label}-file.")
    if file_value is not None:
        return Path(file_value).read_text(encoding="utf-8")
    if inline_value:
        return inline_value
    raise ValueError(f"{label} is required via --{label} or --{label}-file.")


def _read_optional_text(*, inline_value: str | None, file_value: str | None, label: str) -> str:
    if inline_value is not None and file_value is not None:
        raise ValueError(f"Use only one of --{label} or --{label}-file.")
    if file_value is not None:
        return Path(file_value).read_text(encoding="utf-8")
    return inline_value or ""


def _result_payload(result: MatchRunResult) -> dict[str, object]:
    return {
        "final_markdown": result.final_markdown,
        "run_id": result.run_id,
        "run_dir": str(result.run_dir),
        "trace_log_path": str(result.trace_log_path),
        "final_result": result.final_result.model_dump(mode="json"),
        "evaluation_result": (
            result.evaluation_result.model_dump(mode="json") if result.evaluation_result is not None else None
        ),
    }


def _error_payload(exc: Exception) -> dict[str, str]:
    return {
        "error": str(exc),
        "error_type": type(exc).__name__,
    }


def _emit_json(stream, payload: object) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _emit_error(exc: Exception, *, json_output: bool) -> None:
    if json_output:
        _emit_json(sys.stderr, _error_payload(exc))
        return
    print(f"Error: {exc}", file=sys.stderr)


def _required_provider_env_vars(settings: AppSettings) -> list[str]:
    if settings.text_llm_api_key:
        return []
    env_var = PROVIDER_ENV_VAR_BY_PROTOCOL_FAMILY.get(settings.text_llm_protocol_family)
    if env_var is None:
        return []
    return [env_var]


def _missing_provider_env_vars(settings: AppSettings) -> list[str]:
    return [name for name in _required_provider_env_vars(settings) if not os.environ.get(name)]


def _missing_cts_env_vars(settings: AppSettings) -> list[str]:
    return [
        name
        for name, value in (
            ("SEEKTALENT_CTS_TENANT_KEY", settings.cts_tenant_key),
            ("SEEKTALENT_CTS_TENANT_SECRET", settings.cts_tenant_secret),
        )
        if not value
    ]


def _missing_active_provider_env_vars(settings: AppSettings) -> list[str]:
    if settings.provider_name == "cts":
        return _missing_cts_env_vars(settings)
    return []


def _missing_credentials_message(*, missing_provider: list[str], missing_cts: list[str]) -> str:
    missing = [*missing_provider, *missing_cts]
    return (
        f"Missing required environment variables: {', '.join(missing)}. "
        "Set them in your shell and rerun seektalent, or pass --env-file to load them from a file."
    )


def _reject_mock_cts(settings: AppSettings) -> None:
    if settings.mock_cts:
        raise ValueError("Mock CTS is not available in the published CLI.")


def _write_human_result(result: MatchRunResult) -> None:
    if result.final_markdown:
        print(result.final_markdown.rstrip())
    if result.evaluation_result is not None:
        print(
            "evaluation:"
            f" round_01(total={result.evaluation_result.round_01.total_score:.4f},"
            f" ndcg@10={result.evaluation_result.round_01.ndcg_at_10:.4f},"
            f" precision@10={result.evaluation_result.round_01.precision_at_10:.4f})"
            f" final(total={result.evaluation_result.final.total_score:.4f},"
            f" ndcg@10={result.evaluation_result.final.ndcg_at_10:.4f},"
            f" precision@10={result.evaluation_result.final.precision_at_10:.4f})"
        )
    print(f"run_id: {result.run_id}")
    print(f"run_directory: {result.run_dir}")
    print(f"trace_log: {result.trace_log_path}")


def _load_benchmark_rows(path: Path, *, input_index_start: int = 0) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL in {path} line {line_no}: {exc.msg}") from exc
        if "job_description" not in payload:
            raise ValueError(f"Missing job_description in {path} line {line_no}.")
        if "job_title" not in payload:
            raise ValueError(f"Missing job_title in {path} line {line_no}.")
        row = dict(payload)
        row["benchmark_file"] = str(path)
        row["benchmark_group"] = str(row.get("benchmark_group") or path.stem)
        row["input_index"] = input_index_start + len(rows)
        rows.append(row)
    if not rows:
        raise ValueError(f"No benchmark rows found in {path}.")
    return rows


def _skip_default_benchmark_file(path: Path) -> bool:
    return any(path.match(pattern) for pattern in SKIPPED_BENCHMARK_FILE_PATTERNS)


def _load_benchmark_directory(path: Path) -> tuple[list[dict[str, object]], list[str]]:
    files = [item for item in sorted(path.glob("*.jsonl")) if not _skip_default_benchmark_file(item)]
    if not files:
        raise ValueError(f"No benchmark JSONL files found in {path}.")
    rows: list[dict[str, object]] = []
    for file_path in files:
        rows.extend(_load_benchmark_rows(file_path, input_index_start=len(rows)))
    return rows, [str(file_path) for file_path in files]


def _raw_env_value(name: str, *, env_file: str | Path | None) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    if env_file is None:
        return None
    path = Path(env_file)
    if not path.exists():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if key.strip() != name:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return None


def _inspect_local_product_payload() -> dict[str, object]:
    settings, settings_source = _inspect_local_product_settings()
    if settings is None:
        settings_source = "root_only_fallback"
    return {
        "contract_version": "local-product-contract-v1",
        "entrypoints": ["cli", "local_workbench"],
        "default_backend": "seektalent-ui-api",
        "default_frontend": "apps/web",
        "settings_source": settings_source,
        "data_root_posture": _data_root_posture_payload(settings),
    }


def _inspect_local_product_settings() -> tuple[AppSettings | None, str]:
    try:
        return AppSettings(), "default_runtime_settings"
    except Exception:  # noqa: BLE001
        return None, "settings_unavailable"


def _local_product_data_path_builders(settings: AppSettings) -> dict[str, Callable[[], Path]]:
    workbench_root = settings.project_root
    return {
        "artifacts": lambda: settings.artifacts_path,
        "legacy_runs": lambda: settings.runs_path,
        "llm_cache": lambda: settings.llm_cache_path,
        "flywheel_db": lambda: settings.flywheel_path,
        "corpus_db": lambda: settings.corpus_path,
        "workbench_db": lambda: workbench_root / ".seektalent" / "workbench.sqlite3",
        "liepin_connector_db": lambda: settings.resolve_workspace_path(settings.liepin_connector_db_path),
        "liepin_session_store": lambda: settings.resolve_workspace_path(settings.liepin_session_store_dir),
        "workbench_backups": lambda: workbench_root / ".seektalent" / "backups",
        "browser_session_metadata": lambda: workbench_root / ".seektalent" / "browser_sessions",
        "logs": lambda: workbench_root / ".seektalent" / "logs",
    }


def _fallback_data_root_posture_payload() -> dict[str, object]:
    workspace_root = _fallback_workspace_root()
    runtime_mode = _fallback_runtime_mode()
    root_kinds = _local_product_root_kinds()
    roots = {
        name: _single_fallback_data_root_payload(
            path=path,
            kind=root_kinds[name],
            runtime_mode=runtime_mode,
        )
        for name, path in _fallback_local_product_data_paths(workspace_root, runtime_mode).items()
    }
    return {"overall_status": _overall_data_root_status(roots), "roots": roots}


def _fallback_local_product_data_paths(workspace_root: Path, runtime_mode: RuntimeMode) -> dict[str, Path]:
    artifacts_dir = _raw_env_value("SEEKTALENT_ARTIFACTS_DIR", env_file=".env") or (
        PROD_ARTIFACTS_DIR if runtime_mode == "prod" else DEV_ARTIFACTS_DIR
    )
    runs_dir = _raw_env_value("SEEKTALENT_RUNS_DIR", env_file=".env") or (
        PROD_RUNS_DIR if runtime_mode == "prod" else DEV_RUNS_DIR
    )
    llm_cache_dir = _raw_env_value("SEEKTALENT_LLM_CACHE_DIR", env_file=".env") or (
        PROD_LLM_CACHE_DIR if runtime_mode == "prod" else DEV_LLM_CACHE_DIR
    )
    flywheel_db = _raw_env_value("SEEKTALENT_FLYWHEEL_DB_PATH", env_file=".env") or ".seektalent/flywheel.sqlite3"
    corpus_db = _raw_env_value("SEEKTALENT_CORPUS_DB_PATH", env_file=".env") or ".seektalent/corpus.sqlite3"
    liepin_db = (
        _raw_env_value("SEEKTALENT_LIEPIN_CONNECTOR_DB_PATH", env_file=".env")
        or ".seektalent/liepin_connector.sqlite3"
    )
    liepin_sessions = (
        _raw_env_value("SEEKTALENT_LIEPIN_SESSION_STORE_DIR", env_file=".env") or ".seektalent/liepin_sessions"
    )
    workbench_root = workspace_root
    return {
        "artifacts": _fallback_resolve_workspace_path(artifacts_dir, workspace_root),
        "legacy_runs": _fallback_resolve_workspace_path(runs_dir, workspace_root),
        "llm_cache": _fallback_resolve_workspace_path(llm_cache_dir, workspace_root),
        "flywheel_db": _fallback_resolve_workspace_path(flywheel_db, workspace_root),
        "corpus_db": _fallback_resolve_workspace_path(corpus_db, workspace_root),
        "workbench_db": workbench_root / ".seektalent" / "workbench.sqlite3",
        "liepin_connector_db": _fallback_resolve_workspace_path(liepin_db, workspace_root),
        "liepin_session_store": _fallback_resolve_workspace_path(liepin_sessions, workspace_root),
        "workbench_backups": workbench_root / ".seektalent" / "backups",
        "browser_session_metadata": workbench_root / ".seektalent" / "browser_sessions",
        "logs": workbench_root / ".seektalent" / "logs",
    }


def _fallback_runtime_mode() -> RuntimeMode:
    return "prod" if _raw_env_value("SEEKTALENT_RUNTIME_MODE", env_file=".env") == "prod" else "dev"


def _fallback_workspace_root() -> Path:
    value = _raw_env_value("SEEKTALENT_WORKSPACE_ROOT", env_file=".env") or "."
    return _fallback_resolve_workspace_path(value, Path.cwd())


def _fallback_resolve_workspace_path(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return root / path


def _local_product_root_kinds() -> dict[str, str]:
    return {
        "artifacts": "directory",
        "legacy_runs": "directory",
        "llm_cache": "cache",
        "flywheel_db": "sqlite",
        "corpus_db": "sqlite",
        "workbench_db": "sqlite",
        "liepin_connector_db": "sqlite",
        "liepin_session_store": "session_store",
        "workbench_backups": "backup",
        "browser_session_metadata": "session_store",
        "logs": "log",
    }


def _data_root_posture_payload(settings: AppSettings | None) -> dict[str, object]:
    if settings is None:
        return _fallback_data_root_posture_payload()
    root_kinds = _local_product_root_kinds()
    roots = {
        name: _single_data_root_payload(
            build_path=build_path,
            kind=root_kinds[name],
            settings=settings,
        )
        for name, build_path in _local_product_data_path_builders(settings).items()
    }
    return {"overall_status": _overall_data_root_status(roots), "roots": roots}


def _single_data_root_payload(
    *,
    build_path: Callable[[], Path],
    kind: str,
    settings: AppSettings,
) -> dict[str, object]:
    try:
        path = build_path()
    except Exception:  # noqa: BLE001
        return {
            "kind": kind,
            "status": "unknown",
            "reason_code": "path_unavailable",
            "path": None,
            "exists": False,
            "writable": False,
        }
    return _single_fallback_data_root_payload(path=path, kind=kind, runtime_mode=settings.runtime_mode)


def _single_fallback_data_root_payload(
    *,
    path: Path,
    kind: str,
    runtime_mode: RuntimeMode,
) -> dict[str, object]:
    policy = evaluate_local_data_root_policy(
        path,
        runtime_mode=runtime_mode,
        packaged=_packaged_runtime_forces_prod(),
    )
    return {
        "kind": kind,
        "status": policy.status,
        "reason_code": policy.reason_code,
        "path": str(policy.posture.path),
        "exists": policy.posture.path.exists(),
        "writable": _local_product_path_writable(policy.posture.path),
    }


def _overall_data_root_status(roots: dict[str, dict[str, object]]) -> str:
    statuses = {str(payload["status"]) for payload in roots.values()}
    if "error" in statuses:
        return "error"
    if "warning" in statuses:
        return "warning"
    if statuses == {"safe"}:
        return "safe"
    return "unknown"


def _local_product_path_writable(path: Path) -> bool:
    target = path if path.exists() else path.parent
    return target.exists() and os.access(target, os.W_OK)


def _benchmark_artifacts_root(args: argparse.Namespace) -> Path:
    output_dir = getattr(args, "output_dir", None)
    if output_dir:
        output_path = resolve_user_path(output_dir)
        return output_path.parent if output_path.name == "runs" else output_path
    artifacts_dir = _raw_env_value("SEEKTALENT_ARTIFACTS_DIR", env_file=args.env_file)
    if artifacts_dir:
        return resolve_user_path(artifacts_dir)
    runtime_mode = _raw_env_value("SEEKTALENT_RUNTIME_MODE", env_file=args.env_file)
    return resolve_user_path(PROD_ARTIFACTS_DIR if runtime_mode == "prod" else DEV_ARTIFACTS_DIR)


def _create_benchmark_case_run(store: ArtifactStore, row: dict[str, object]) -> BenchmarkCaseRun:
    case_label = str(row.get("jd_id") or row.get("job_title") or row["input_index"])
    session = store.create_root(
        kind="run",
        display_name=f"seek talent benchmark case {case_label}",
        producer="WorkflowRuntime",
    )
    trace_log_path, trace_handle = session.open_text_stream("runtime.trace_log")
    trace_handle.close()
    _, events_handle = session.open_text_stream("runtime.events")
    events_handle.close()
    return BenchmarkCaseRun(row=row, session=session, trace_log_path=trace_log_path)


def _case_run_identity(case_run: BenchmarkCaseRun) -> dict[str, str]:
    return {
        "run_id": case_run.session.manifest.artifact_id,
        "run_dir": str(case_run.session.root),
        "trace_log_path": str(case_run.trace_log_path),
    }


def _record_case_run_failure(
    case_run: BenchmarkCaseRun,
    *,
    error: BaseException,
    stage: str,
) -> None:
    if case_run.session.manifest.status != "running":
        return
    timestamp = _now_iso()
    _, trace_handle = case_run.session.open_text_stream("runtime.trace_log")
    try:
        trace_handle.write(f"[{timestamp}] benchmark_case_failed stage={stage} error={_error_text(error)}\n")
    finally:
        trace_handle.close()
    case_run.session.append_jsonl(
        "runtime.events",
        {
            "timestamp": timestamp,
            "run_id": case_run.session.manifest.artifact_id,
            "event_type": "benchmark_case_failed",
            "status": "failed",
            "summary": str(error),
            "payload": {
                "stage": stage,
                "error_type": type(error).__name__,
                "error_message": str(error),
            },
        },
    )
    case_run.session.finalize(status="failed", failure_summary=str(error))


def _ensure_case_run_completed(case_run: BenchmarkCaseRun) -> None:
    if case_run.session.manifest.status != "running":
        return
    case_run.session.finalize(status="completed")


def _benchmark_result_row(
    row: dict[str, object],
    result: MatchRunResult,
    *,
    attempt: BenchmarkAttempt,
    completed_at: str,
    completion_index: int,
) -> dict[str, object]:
    result_row = {
        "jd_id": row.get("jd_id"),
        "job_title": row.get("job_title"),
        "benchmark_file": row["benchmark_file"],
        "benchmark_group": row["benchmark_group"],
        "input_index": row["input_index"],
        "status": "succeeded",
        "attempts": attempt.attempt,
        "started_at": attempt.started_at,
        "completed_at": completed_at,
        "completion_index": completion_index,
        "upload_status": "skipped",
        "upload_attempts": 0,
        "run_id": result.run_id,
        "run_dir": str(result.run_dir),
        "trace_log_path": str(result.trace_log_path),
        "evaluation_result": (
            result.evaluation_result.model_dump(mode="json") if result.evaluation_result is not None else None
        ),
    }
    term_surface_audit_path = result.run_dir / "runtime" / "term_surface_audit.json"
    if not term_surface_audit_path.exists():
        term_surface_audit_path = result.run_dir / "term_surface_audit.json"
    if term_surface_audit_path.exists():
        result_row["term_surface_audit_path"] = str(term_surface_audit_path)
    return result_row


def _failed_benchmark_result_row(
    row: dict[str, object],
    *,
    attempt: BenchmarkAttempt,
    completed_at: str,
    completion_index: int,
    error: str,
    case_run: BenchmarkCaseRun,
) -> dict[str, object]:
    return {
        "jd_id": row.get("jd_id"),
        "job_title": row.get("job_title"),
        "benchmark_file": row["benchmark_file"],
        "benchmark_group": row["benchmark_group"],
        "input_index": row["input_index"],
        "status": "failed",
        "attempts": attempt.attempt,
        "started_at": attempt.started_at,
        "completed_at": completed_at,
        "completion_index": completion_index,
        "upload_status": "skipped",
        "upload_attempts": 0,
        "error": error,
        **_case_run_identity(case_run),
    }


def _finalize_benchmark_execution(
    *,
    benchmark_session: ArtifactSession,
    benchmark_metadata: dict[str, object],
    case_runs: list[BenchmarkCaseRun],
    results: list[dict[str, object]],
) -> tuple[dict[str, object], Path, bool]:
    benchmark_session.set_child_artifacts(
        [
            {
                "artifact_kind": "run",
                "artifact_id": case_run.session.manifest.artifact_id,
                "role": "case_run",
                "case_id": case_run.row.get("jd_id"),
            }
            for case_run in case_runs
        ]
    )
    summary_payload = {
        **benchmark_metadata,
        "count": len(results),
        "runs": results,
    }
    summary_path = benchmark_session.write_json("output.summary", summary_payload)
    has_failed_rows = any(row.get("status") == "failed" for row in results)
    benchmark_session.finalize(
        status="failed" if has_failed_rows else "completed",
        failure_summary=(
            f"{sum(1 for row in results if row.get('status') == 'failed')} benchmark case(s) failed"
            if has_failed_rows
            else None
        ),
    )
    payload = {
        **summary_payload,
        "summary_path": str(summary_path),
    }
    return payload, summary_path, has_failed_rows


def _inspect_payload() -> dict[str, object]:
    commands = {
        "run": {
            "description": "Run one resume-matching workflow.",
            "machine_readable": False,
            "arguments": [
                _arg_spec(
                    "--job-title",
                    "string",
                    "Inline job title text.",
                    required=True,
                    mutually_exclusive_with=["--job-title-file"],
                ),
                _arg_spec(
                    "--job-title-file",
                    "path",
                    "Path to a job title file.",
                    required=True,
                    mutually_exclusive_with=["--job-title"],
                ),
                _arg_spec("--jd", "string", "Inline job description text.", mutually_exclusive_with=["--jd-file"]),
                _arg_spec("--jd-file", "path", "Path to a job description file.", mutually_exclusive_with=["--jd"]),
                _arg_spec(
                    "--notes",
                    "string",
                    "Optional inline sourcing notes text.",
                    mutually_exclusive_with=["--notes-file"],
                ),
                _arg_spec(
                    "--notes-file",
                    "path",
                    "Path to an optional sourcing notes file.",
                    mutually_exclusive_with=["--notes"],
                ),
                _arg_spec("--env-file", "path", "Path to the env file for this run.", default=".env"),
                _arg_spec("--output-dir", "path", "Directory where run artifacts should be written."),
                _arg_spec("--json", "flag", "Emit a single JSON object."),
                _arg_spec("--max-rounds", "integer", "Override the maximum retrieval rounds (3-10)."),
                _arg_spec("--min-rounds", "integer", "Override the minimum retrieval rounds (3-10)."),
                _arg_spec("--scoring-max-concurrency", "integer", "Override max parallel scoring workers."),
                _arg_spec("--search-max-pages-per-round", "integer", "Override the per-round CTS page budget."),
                _arg_spec("--search-max-attempts-per-round", "integer", "Override the per-round CTS attempt budget."),
                _arg_spec("--search-no-progress-limit", "integer", "Override the repeated no-progress threshold."),
                _arg_spec(
                    "--enable-eval",
                    "flag",
                    "Enable judge + eval for this run.",
                    mutually_exclusive_with=["--disable-eval"],
                ),
                _arg_spec(
                    "--disable-eval",
                    "flag",
                    "Disable judge + eval for this run.",
                    mutually_exclusive_with=["--enable-eval"],
                ),
                _arg_spec(
                    "--enable-reflection",
                    "flag",
                    "Enable reflection for this run.",
                    mutually_exclusive_with=["--disable-reflection"],
                ),
                _arg_spec(
                    "--disable-reflection",
                    "flag",
                    "Disable reflection for this run.",
                    mutually_exclusive_with=["--enable-reflection"],
                ),
            ],
            "examples": [
                "seektalent run --job-title-file ./job_title.md --jd-file ./jd.md",
                "seektalent run --job-title 'Python engineer' --jd 'Build retrieval systems' --notes 'Shanghai preferred' --json",
            ],
            "outputs": "Human-readable shortlist on stdout by default. In --json mode, stdout contains one JSON object.",
            "side_effects": "Creates a run artifact directory under ./runs or the path passed to --output-dir.",
        },
        "benchmark": {
            "description": "Run benchmark JDs from maintained domain JSONL files.",
            "machine_readable": False,
            "arguments": [
                _arg_spec(
                    "--jds-file",
                    "path",
                    "Path to one JSONL file with benchmark JDs. When omitted, --benchmarks-dir is scanned.",
                    default=None,
                ),
                _arg_spec(
                    "--benchmarks-dir",
                    "path",
                    "Directory of maintained benchmark JSONL files.",
                    default="artifacts/benchmarks",
                ),
                _arg_spec("--env-file", "path", "Path to the env file for this run.", default=".env"),
                _arg_spec("--output-dir", "path", "Directory where run artifacts should be written."),
                _arg_spec("--json", "flag", "Emit a single JSON object."),
                _arg_spec("--benchmark-max-concurrency", "integer", "Override max parallel benchmark rows.", default=1),
                _arg_spec(
                    "--benchmark-run-retries", "integer", "Retry each failed benchmark row this many times.", default=1
                ),
                _arg_spec(
                    "--benchmark-upload-retries",
                    "integer",
                    "Retry each failed remote eval upload this many times.",
                    default=1,
                ),
                _arg_spec(
                    "--enable-eval",
                    "flag",
                    "Enable judge + eval for this run.",
                    mutually_exclusive_with=["--disable-eval"],
                ),
                _arg_spec(
                    "--disable-eval",
                    "flag",
                    "Disable judge + eval for this run.",
                    mutually_exclusive_with=["--enable-eval"],
                ),
                _arg_spec(
                    "--enable-reflection",
                    "flag",
                    "Enable reflection for this run.",
                    mutually_exclusive_with=["--disable-reflection"],
                ),
                _arg_spec(
                    "--disable-reflection",
                    "flag",
                    "Disable reflection for this run.",
                    mutually_exclusive_with=["--enable-reflection"],
                ),
            ],
            "examples": [
                "seektalent benchmark",
                "seektalent benchmark --jds-file ./artifacts/benchmarks/agent_jds.jsonl --enable-eval --json",
            ],
            "outputs": "Human-readable per-JD run ids on stdout by default. In --json mode, stdout contains one JSON object.",
            "side_effects": "Scans maintained benchmark files in directory mode, runs each JD, writes benchmark_summary_*.json under the runs directory, and serializes remote uploads when eval is enabled.",
        },
        "doctor": {
            "description": "Run local configuration checks without network calls.",
            "machine_readable": False,
            "arguments": [
                _arg_spec("--env-file", "path", "Path to the env file to inspect.", default=".env"),
                _arg_spec("--output-dir", "path", "Directory to validate as the artifact root."),
                _arg_spec("--json", "flag", "Emit a single JSON object."),
            ],
            "examples": [
                "seektalent doctor",
                "seektalent doctor --env-file ./local.env --json",
            ],
            "outputs": "Human-readable checks on stdout by default. In --json mode, stdout contains one JSON object.",
            "side_effects": "May create the configured output directory to verify writability.",
        },
        "llm-prf-live-validate": {
            "description": "Run the manual live LLM PRF validation harness on checked input cases.",
            "machine_readable": False,
            "arguments": [
                _arg_spec("--cases", "path", "Path to JSONL live validation cases.", required=True),
                _arg_spec(
                    "--output-dir", "path", "Directory where validation artifacts should be written.", required=True
                ),
                _arg_spec("--env-file", "path", "Path to the env file for provider credentials.", default=".env"),
            ],
            "examples": [
                "seektalent llm-prf-live-validate --cases tests/fixtures/llm_prf_live_validation/cases.jsonl --output-dir artifacts/debug/llm-prf-live-validation",
            ],
            "outputs": "Delegates output handling to the LLM PRF bakeoff harness.",
            "side_effects": "May call the configured live text LLM provider and writes validation artifacts.",
        },
        "init": {
            "description": "Write a starter env file in the current directory.",
            "machine_readable": False,
            "arguments": [
                _arg_spec("--env-file", "path", "Where to write the generated env file.", default=".env"),
                _arg_spec("--force", "flag", "Overwrite the target file if it exists."),
            ],
            "examples": [
                "seektalent init",
                "seektalent init --env-file ./local.env --force",
            ],
            "outputs": "Writes the generated env-file path to stdout.",
            "side_effects": "Creates or overwrites an env file on disk.",
        },
        "version": {
            "description": "Print the installed seektalent version.",
            "machine_readable": False,
            "arguments": [],
            "examples": ["seektalent version"],
            "outputs": "Prints the installed version to stdout.",
            "side_effects": "No filesystem changes.",
        },
        "update": {
            "description": "Print upgrade instructions for pip and pipx installs.",
            "machine_readable": False,
            "arguments": [],
            "examples": ["seektalent update"],
            "outputs": "Prints upgrade instructions to stdout.",
            "side_effects": "No filesystem changes and no package modifications.",
        },
        "inspect": {
            "description": "Describe the published CLI for wrappers, agents, and automation.",
            "machine_readable": False,
            "arguments": [
                _arg_spec("--json", "flag", "Emit a single JSON object describing the CLI."),
            ],
            "examples": [
                "seektalent inspect",
                "seektalent inspect --json",
            ],
            "outputs": "Prints a short summary by default. In --json mode, stdout contains one JSON object.",
            "side_effects": "No filesystem changes. Mock CTS is not available in the published CLI.",
        },
    }
    commands["run"]["notes"] = [
        "Provide the job title with exactly one of --job-title or --job-title-file.",
        "Provide the job description with exactly one of --jd or --jd-file.",
        "Provide sourcing notes with at most one of --notes or --notes-file.",
    ]
    return {
        "tool": "seektalent",
        "version": __version__,
        "summary": "Deterministic local resume matching CLI for CTS retrieval and shortlist generation.",
        "recommended_workflow": [
            "seektalent --help",
            "seektalent doctor",
            "seektalent run --job-title-file ./job_title.md --jd-file ./jd.md",
            "seektalent update",
        ],
        "commands": commands,
        "environment": {
            "required_for_default_run": [
                "SEEKTALENT_TEXT_LLM_API_KEY",
                "SEEKTALENT_CTS_TENANT_KEY",
                "SEEKTALENT_CTS_TENANT_SECRET",
            ],
            "optional_provider_vars": [],
            "optional_runtime_vars": OPTIONAL_RUNTIME_ENV_VARS,
            "env_file_support": "run and doctor accept --env-file to load values from a file; shell environment variables remain first-class.",
        },
        "artifacts": {
            "default_runs_dir": "./runs",
            "override_flag": "--output-dir",
            "top_level_files": TOP_LEVEL_ARTIFACT_FILES,
            "key_handoff_files": KEY_HANDOFF_FILES,
        },
        "runtime_source_lanes": {
            "contract_version": "runtime-source-lane-v1",
            "default_sources": ["cts"],
            "supported_sources": ["cts", "liepin"],
            "workbench_lane_api": "WorkflowRuntime.run_source_lane_async",
            "public_payload_policy": "allowlist_serializers_only",
            "detail_open_boundary": "runtime_recommendation_workbench_approved_lease",
        },
        "local_product": _inspect_local_product_payload(),
        "json_contracts": {
            "run": {
                "flag": "--json",
                "stdout_success_fields": [
                    "final_markdown",
                    "run_id",
                    "run_dir",
                    "trace_log_path",
                    "final_result",
                    "evaluation_result",
                ],
                "nullable_fields": ["evaluation_result"],
            },
            "benchmark": {
                "flag": "--json",
                "stdout_success_fields": ["count", "runs", "summary_path"],
                "file_mode_fields": ["benchmark_file"],
                "directory_mode_fields": ["benchmark_dir", "benchmark_files"],
            },
            "doctor": {
                "flag": "--json",
                "stdout_success_fields": ["ok", "checks"],
            },
        },
        "failure_contract": {
            "stderr_json_fields": ["error", "error_type"],
            "known_failure_categories": [
                {
                    "name": "missing_env",
                    "description": "Required environment variables are missing for the selected workflow.",
                    "commands": ["run", "doctor"],
                },
                {
                    "name": "invalid_input",
                    "description": "CLI inputs are missing or mutually exclusive arguments were supplied together.",
                    "commands": ["run", "init"],
                },
                {
                    "name": "invalid_settings",
                    "description": "Configuration values or environment settings do not pass validation.",
                    "commands": ["run", "doctor"],
                },
                {
                    "name": "auth_failed",
                    "description": "A downstream provider or CTS request was rejected due to invalid credentials.",
                    "commands": ["run"],
                },
                {
                    "name": "runtime_exception",
                    "description": "A runtime stage raised an exception after the CLI had already started the workflow.",
                    "commands": ["run"],
                },
            ],
        },
        "notes": [
            "Use seektalent inspect --json as the preferred machine-readable discovery entrypoint.",
            "The published CLI rejects mock CTS even if SEEKTALENT_MOCK_CTS is set.",
            "Eval-off runs omit judge artifacts and return evaluation_result=null in --json mode.",
        ],
    }


def _run_command(args: argparse.Namespace) -> int:
    job_title = _read_text(inline_value=args.job_title, file_value=args.job_title_file, label="job-title")
    jd = _read_text(inline_value=args.jd, file_value=args.jd_file, label="jd")
    notes = _read_optional_text(inline_value=args.notes, file_value=args.notes_file, label="notes")
    load_process_env(args.env_file)
    settings = _build_settings(args)
    _reject_mock_cts(settings)
    missing_provider = _missing_provider_env_vars(settings)
    missing_cts = _missing_active_provider_env_vars(settings)
    if missing_provider or missing_cts:
        raise ValueError(
            _missing_credentials_message(
                missing_provider=missing_provider,
                missing_cts=missing_cts,
            )
        )
    cleanup_runtime_artifacts(settings)
    result = run_match(
        job_title=job_title,
        jd=jd,
        notes=notes,
        settings=settings,
        env_file=args.env_file,
    )
    if args.json_output:
        _emit_json(sys.stdout, _result_payload(result))
        return 0
    _write_human_result(result)
    return 0


def _benchmark_command(args: argparse.Namespace) -> int:
    load_process_env(args.env_file)
    if args.benchmark_max_concurrency < 1:
        raise ValueError("benchmark_max_concurrency must be >= 1")
    benchmark_run_retries = getattr(args, "benchmark_run_retries", 1)
    benchmark_upload_retries = getattr(args, "benchmark_upload_retries", 1)
    if benchmark_run_retries < 0:
        raise ValueError("benchmark_run_retries must be >= 0")
    if benchmark_upload_retries < 0:
        raise ValueError("benchmark_upload_retries must be >= 0")
    benchmark_file: Path | None = resolve_user_path(args.jds_file) if args.jds_file else None
    if benchmark_file is not None and benchmark_file.is_dir():
        rows, benchmark_files = _load_benchmark_directory(benchmark_file)
        benchmark_metadata = {"benchmark_dir": str(benchmark_file), "benchmark_files": benchmark_files}
    elif benchmark_file is not None:
        rows = _load_benchmark_rows(benchmark_file)
        benchmark_files = [str(benchmark_file)]
        benchmark_metadata = {"benchmark_file": str(benchmark_file)}
    else:
        benchmark_dir_path = resolve_user_path(args.benchmarks_dir)
        rows, benchmark_files = _load_benchmark_directory(benchmark_dir_path)
        benchmark_metadata = {"benchmark_dir": str(benchmark_dir_path), "benchmark_files": benchmark_files}
    artifact_store = ArtifactStore(_benchmark_artifacts_root(args))
    benchmark_session = artifact_store.create_root(
        kind="benchmark",
        display_name="seek talent benchmark execution",
        producer="BenchmarkCLI",
    )
    case_runs = {cast(int, row["input_index"]): _create_benchmark_case_run(artifact_store, row) for row in rows}
    all_case_runs = list(case_runs.values())

    try:
        settings = _build_settings(args)
        _reject_mock_cts(settings)
        missing_provider = _missing_provider_env_vars(settings)
        missing_cts = _missing_active_provider_env_vars(settings)
        if missing_provider or missing_cts:
            raise ValueError(
                _missing_credentials_message(
                    missing_provider=missing_provider,
                    missing_cts=missing_cts,
                )
            )
        cleanup_runtime_artifacts(settings)
    except Exception as exc:  # noqa: BLE001
        result_rows_by_index: dict[int, dict[str, object]] = {}
        for completion_index, row in enumerate(rows):
            attempt = BenchmarkAttempt(row=row, attempt=1, started_at=_now_iso())
            case_run = case_runs[cast(int, row["input_index"])]
            _record_case_run_failure(case_run, error=exc, stage="benchmark_preflight")
            result_rows_by_index[cast(int, row["input_index"])] = _failed_benchmark_result_row(
                row,
                attempt=attempt,
                completed_at=_now_iso(),
                completion_index=completion_index,
                error=_error_text(exc),
                case_run=case_run,
            )
        results = [result_rows_by_index[index] for index in sorted(result_rows_by_index)]
        payload, summary_path, _ = _finalize_benchmark_execution(
            benchmark_session=benchmark_session,
            benchmark_metadata=benchmark_metadata,  # ty:ignore[invalid-argument-type]
            case_runs=all_case_runs,
            results=results,
        )
        if args.json_output:
            _emit_json(sys.stdout, payload)
        else:
            if "benchmark_dir" in benchmark_metadata:
                print(f"benchmark_dir: {benchmark_metadata['benchmark_dir']}")
                for file_path in benchmark_files:
                    print(f"benchmark_file: {file_path}")
            else:
                print(f"benchmark_file: {benchmark_metadata['benchmark_file']}")
            print(f"count: {len(results)}")
            print(f"summary_path: {summary_path}")
            for item in results:
                print(f"{item['jd_id']}: failed attempts={item['attempts']} error={item['error']}")
        return 1

    judge_limiter = AsyncJudgeLimiter(settings.judge_max_concurrency) if settings.enable_eval else None
    uploader = (
        BenchmarkUploader(settings=settings, retries=benchmark_upload_retries)
        if settings.enable_eval and (settings.wandb_project or settings.weave_project)
        else None
    )

    def run_row(attempt: BenchmarkAttempt) -> MatchRunResult:
        row = attempt.row
        case_run = case_runs[cast(int, row["input_index"])]
        return run_match(
            job_title=cast(str, row["job_title"]),
            jd=cast(str, row["job_description"]),
            notes=cast(str, row.get("hiring_notes", "") or ""),
            settings=settings,
            env_file=args.env_file,
            judge_limiter=judge_limiter,
            eval_remote_logging=False if settings.enable_eval else True,
            artifact_session=case_run.session,
        )

    result_rows_by_index: dict[int, dict[str, object]] = {}
    pending = deque(BenchmarkAttempt(row=row, attempt=1, started_at=_now_iso()) for row in rows)
    running: dict[Future[MatchRunResult], BenchmarkAttempt] = {}
    completion_index = 0
    try:
        with ThreadPoolExecutor(max_workers=args.benchmark_max_concurrency) as executor:
            while pending or running:
                while pending and len(running) < args.benchmark_max_concurrency:
                    attempt = pending.popleft()
                    running[executor.submit(run_row, attempt)] = attempt
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    attempt = running.pop(future)
                    completed_at = _now_iso()
                    row = attempt.row
                    input_index = cast(int, row["input_index"])
                    try:
                        result = future.result()
                    except Exception as exc:  # noqa: BLE001
                        case_run = case_runs[input_index]
                        if attempt.attempt <= benchmark_run_retries:
                            _record_case_run_failure(case_run, error=exc, stage="benchmark_case")
                            next_case_run = _create_benchmark_case_run(artifact_store, row)
                            case_runs[input_index] = next_case_run
                            all_case_runs.append(next_case_run)
                            pending.append(
                                BenchmarkAttempt(
                                    row=row,
                                    attempt=attempt.attempt + 1,
                                    started_at=_now_iso(),
                                )
                            )
                            continue
                        _record_case_run_failure(case_run, error=exc, stage="benchmark_case")
                        result_rows_by_index[input_index] = _failed_benchmark_result_row(
                            row,
                            attempt=attempt,
                            completed_at=completed_at,
                            completion_index=completion_index,
                            error=_error_text(exc),
                            case_run=case_run,
                        )
                        completion_index += 1
                        continue
                    _ensure_case_run_completed(case_runs[input_index])
                    result_row = _benchmark_result_row(
                        row,
                        result,
                        attempt=attempt,
                        completed_at=completed_at,
                        completion_index=completion_index,
                    )
                    result_rows_by_index[input_index] = result_row
                    completion_index += 1
                    if uploader is not None and result.evaluation_result is not None:
                        uploader.submit(BenchmarkUploadTask(result_row=result_row, result=result))
    finally:
        if uploader is not None:
            uploader.close()

    results = [result_rows_by_index[index] for index in sorted(result_rows_by_index)]
    payload, summary_path, has_failed_rows = _finalize_benchmark_execution(
        benchmark_session=benchmark_session,
        benchmark_metadata=benchmark_metadata,  # ty:ignore[invalid-argument-type]
        case_runs=all_case_runs,
        results=results,
    )
    if args.json_output:
        _emit_json(sys.stdout, payload)
        return 1 if has_failed_rows else 0
    if "benchmark_dir" in benchmark_metadata:
        print(f"benchmark_dir: {benchmark_metadata['benchmark_dir']}")
        for file_path in benchmark_files:
            print(f"benchmark_file: {file_path}")
    else:
        print(f"benchmark_file: {benchmark_metadata['benchmark_file']}")
    print(f"count: {len(results)}")
    print(f"summary_path: {summary_path}")
    for item in results:
        if item.get("status") == "failed":
            print(f"{item['jd_id']}: failed attempts={item['attempts']} error={item['error']}")
        else:
            print(f"{item['jd_id']}: run_id={item['run_id']} run_dir={item['run_dir']}")
    return 1 if has_failed_rows else 0


def _archive_legacy_artifacts_command(args: argparse.Namespace) -> int:
    report = execute_archive_migration(
        project_root=resolve_user_path(args.project_root),
        legacy_runs_root=resolve_user_path(args.runs_dir),
        artifacts_root=resolve_user_path(args.artifacts_dir),
    )
    print(f"archive_plan: {report.plan_path}")
    print(f"archive_result: {report.result_path}")
    return 0


def _flywheel_export_command(args: argparse.Namespace) -> int:
    settings = _build_settings(args)
    builder_config = json.loads(args.builder_config_json) if args.builder_config_json else {}
    store = FlywheelStore(settings.flywheel_path)
    try:
        result = export_query_rewriting_dataset(
            store=store,
            artifact_store=ArtifactStore(settings.artifacts_path),
            dataset_version=args.dataset_version,
            builder_config=builder_config,
            run_ids=args.run_id,
        )
    finally:
        store.close()
    payload = asdict(result)
    payload["root"] = str(result.root)
    if args.json_output:
        _emit_json(sys.stdout, payload)
    else:
        print(f"export_id: {result.export_id}")
        print(f"export_directory: {result.root}")
        print(f"row_count: {result.row_count}")
        print(f"sha256: {result.sha256}")
    return 0


def _corpus_export_command(args: argparse.Namespace) -> int:
    settings = AppSettings()
    settings = settings.with_overrides(
        corpus_db_path=args.corpus_db,
        artifacts_dir=args.artifacts_dir,
    )
    db_path = settings.corpus_path
    artifacts_root = settings.artifacts_path
    store = CorpusStore(db_path)
    try:
        session = ArtifactStore(artifacts_root).create_root(
            kind="corpus",
            display_name="manual corpus export",
            producer="CorpusExportCLI",
        )
        materialize_corpus_artifacts(
            session=session,
            store=store,
            tenant_id=args.tenant_id,
            workspace_id=args.workspace_id,
        )
        session.finalize(status="completed")
        print(session.root)
        return 0
    finally:
        store.close()


def _llm_prf_live_validate_command(args: argparse.Namespace) -> int:
    from seektalent.candidate_feedback.llm_prf_bakeoff import main as llm_prf_live_main

    argv = [
        "--live",
        "--case-format",
        "llm-prf-input",
        "--cases",
        str(args.cases),
        "--output-dir",
        str(args.output_dir),
        "--env-file",
        str(args.env_file),
    ]
    return llm_prf_live_main(argv)


def _init_command(args: argparse.Namespace) -> int:
    env_path = resolve_user_path(args.env_file)
    if env_path.exists() and not args.force:
        raise ValueError(f"{env_path} already exists. Use --force to overwrite it.")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(read_env_example_template(), encoding="utf-8")
    print(f"Wrote env template to {env_path}")
    return 0


def _package_resource_checks() -> list[DoctorCheck]:
    prompt_dir = package_prompt_dir()
    checks: list[DoctorCheck] = []
    unreadable: list[str] = []
    for name in REQUIRED_PROMPTS:
        prompt_file = prompt_dir / f"{name}.md"
        try:
            prompt_file.read_text(encoding="utf-8")
        except OSError:
            unreadable.append(name)
    if unreadable:
        checks.append(DoctorCheck("packaged_prompts", False, f"Unreadable prompt files: {', '.join(unreadable)}"))
    else:
        checks.append(DoctorCheck("packaged_prompts", True, f"Loaded {len(REQUIRED_PROMPTS)} packaged prompts."))

    spec_file = package_spec_file()
    try:
        spec_file.read_text(encoding="utf-8")
        checks.append(DoctorCheck("default_spec", True, f"Found packaged spec: {spec_file}"))
    except OSError:
        checks.append(DoctorCheck("default_spec", False, f"Missing packaged spec: {spec_file}"))
    return checks


def _settings_check(
    settings: AppSettings | None,
    error: ValidationError | TextLLMConfigMigrationError | None,
) -> DoctorCheck:
    if error is not None:
        if isinstance(error, ValidationError):
            message = "; ".join(item["msg"] for item in error.errors())
        else:
            message = str(error)
        return DoctorCheck("settings", False, message)
    assert settings is not None
    return DoctorCheck("settings", True, "Configuration schema is valid.")


def _output_dir_check(settings: AppSettings | None) -> DoctorCheck:
    assert settings is not None
    runs_path = settings.runs_path
    runs_path.mkdir(parents=True, exist_ok=True)
    return DoctorCheck("output_dir", True, f"Writable output directory: {runs_path}")


def _provider_credentials_check(settings: AppSettings | None) -> DoctorCheck:
    assert settings is not None
    required_vars = _required_provider_env_vars(settings)
    missing = _missing_provider_env_vars(settings)
    if missing:
        return DoctorCheck(
            "provider_credentials",
            False,
            _missing_credentials_message(missing_provider=missing, missing_cts=[]),
        )
    if required_vars:
        return DoctorCheck("provider_credentials", True, f"Found credentials: {', '.join(required_vars)}")
    return DoctorCheck("provider_credentials", True, "No provider credentials required by current models.")


def _cts_credentials_check(settings: AppSettings | None) -> DoctorCheck:
    assert settings is not None
    if settings.provider_name != "cts":
        return DoctorCheck(
            "cts_credentials", True, f"CTS credentials are not required for provider {settings.provider_name}."
        )
    missing = _missing_cts_env_vars(settings)
    if missing:
        return DoctorCheck(
            "cts_credentials",
            False,
            _missing_credentials_message(missing_provider=[], missing_cts=missing),
        )
    return DoctorCheck("cts_credentials", True, "CTS credentials are configured.")


def _wandb_auth_configured() -> bool:
    if os.environ.get("WANDB_API_KEY"):
        return True
    for candidate in (Path.home() / ".netrc", Path.home() / "_netrc"):
        if not candidate.exists():
            continue
        text = candidate.read_text(encoding="utf-8")
        if "machine api.wandb.ai" in text:
            return True
    return False


def _remote_eval_logging_check(settings: AppSettings | None) -> DoctorCheck:
    assert settings is not None
    if not settings.enable_eval:
        return DoctorCheck("remote_eval_logging", True, "Eval disabled; W&B and Weave checks skipped.")
    if not settings.wandb_project or not settings.weave_project:
        return DoctorCheck(
            "remote_eval_logging",
            False,
            "Eval requires SEEKTALENT_WANDB_PROJECT and SEEKTALENT_WEAVE_PROJECT.",
        )
    if not _wandb_auth_configured():
        return DoctorCheck(
            "remote_eval_logging",
            False,
            "Eval requires WANDB_API_KEY or a saved W&B login for Weave and report logging.",
        )
    return DoctorCheck("remote_eval_logging", True, "W&B and Weave logging is configured.")


def _local_data_roots_check(settings: AppSettings | None) -> DoctorCheck:
    assert settings is not None
    posture = _data_root_posture_payload(settings)
    roots = posture["roots"]
    assert isinstance(roots, dict)
    root_summaries = [
        f"{name}={payload['status']}:{payload['reason_code']}"
        for name, payload in roots.items()
        if isinstance(payload, dict)
    ]
    overall_status = str(posture["overall_status"])
    return DoctorCheck(
        "local_data_roots",
        overall_status != "error",
        f"Local data roots posture={overall_status}; " + ", ".join(root_summaries),
    )


def _doctor_command(args: argparse.Namespace) -> int:
    load_process_env(args.env_file)
    checks = _package_resource_checks()
    settings: AppSettings | None = None
    settings_error: ValidationError | TextLLMConfigMigrationError | None = None
    try:
        settings = _build_settings(args)
    except (ValidationError, TextLLMConfigMigrationError) as exc:
        settings_error = exc

    checks.append(_settings_check(settings, settings_error))
    if settings is not None:
        try:
            _reject_mock_cts(settings)
        except ValueError as exc:
            checks.append(DoctorCheck("mock_cts", False, str(exc)))
            settings = None
    if settings is not None:
        checks.append(_output_dir_check(settings))
        checks.append(_provider_credentials_check(settings))
        checks.append(_cts_credentials_check(settings))
        checks.append(_remote_eval_logging_check(settings))
        checks.append(_local_data_roots_check(settings))

    ok = all(check.ok for check in checks)
    if args.json_output:
        _emit_json(sys.stdout, {"ok": ok, "checks": [asdict(check) for check in checks]})
        return 0 if ok else 1

    for check in checks:
        status = "OK" if check.ok else "FAIL"
        print(f"{status} {check.name}: {check.message}")
    print("Doctor passed." if ok else "Doctor failed.")
    return 0 if ok else 1


def _version_command(args: argparse.Namespace) -> int:
    del args
    print(__version__)
    return 0


def _update_command(args: argparse.Namespace) -> int:
    del args
    print(f"Current version: {__version__}")
    print("Upgrade with pip: pip install -U seektalent")
    print(f"Install this exact version: pip install -U seektalent=={__version__}")
    print("Upgrade with pipx: pipx upgrade seektalent")
    print("This command prints upgrade instructions only. It does not modify your environment.")
    return 0


def _inspect_command(args: argparse.Namespace) -> int:
    payload = _inspect_payload()
    if args.json_output:
        _emit_json(sys.stdout, payload)
        return 0
    print("SeekTalent published CLI inspection summary")
    print(f"Version: {payload['version']}")
    print("Use `seektalent inspect --json` for a machine-readable CLI description.")
    return 0


def _liepin_compliance_gate_command(args: argparse.Namespace) -> int:
    if args.liepin_compliance_command == "create":
        return _liepin_compliance_gate_create_command(args)
    if args.liepin_compliance_command == "bind-account":
        return _liepin_compliance_gate_bind_account_command(args)
    if args.liepin_compliance_command == "verify":
        return _liepin_compliance_gate_verify_command(args)
    print("Missing liepin-compliance-gate subcommand.", file=sys.stderr)
    return 1


def _liepin_compliance_gate_create_command(args: argparse.Namespace) -> int:
    if args.purpose != "search":
        print("validation failed: liepin-compliance-gate create requires --purpose search", file=sys.stderr)
        return 1
    store = LiepinStore(_liepin_cli_db_path(args))
    gate = ComplianceGate(
        tenant_id=args.tenant_id,
        workspace_id=args.workspace_id,
        actor_id=args.actor_id,
        provider_account_hash=None,
        status="pending_account_binding",
        candidate_personal_info_processing_basis=args.candidate_personal_info_processing_basis,
        personal_information_processor=args.personal_information_processor,
        operator_audit_owner=args.operator_audit_owner,
        account_holder_authorized=args.account_holder_authorized,
        human_initiated_recruiting=args.human_initiated_recruiting,
        allowed_purposes=[args.purpose],
        retention_policy=args.retention_policy,
        deletion_sla_days=args.deletion_sla_days,
        deletion_path=args.deletion_path,
        raw_payload_access_scope=args.raw_payload_access_scope,
        raw_detail_retention_allowed_after_debug=args.raw_detail_retention_allowed_after_debug,
        fixture_export_allowed=args.fixture_export_allowed,
        policy_ref=args.policy_ref,
    )
    if not gate.allows_connection_handoff(purpose=args.purpose):
        print("validation failed: policy requirements not satisfied", file=sys.stderr)
        return 1
    gate_ref = store.create_compliance_gate(
        tenant_id=args.tenant_id,
        workspace_id=args.workspace_id,
        actor_id=args.actor_id,
        gate=gate,
        purpose=args.purpose,
    )
    print(gate_ref)
    return 0


def _liepin_compliance_gate_bind_account_command(args: argparse.Namespace) -> int:
    store = LiepinStore(_liepin_cli_db_path(args))
    gate = store.get_compliance_gate(
        gate_ref=args.gate_ref,
        tenant_id=args.tenant_id,
        workspace_id=args.workspace_id,
        actor_id=args.actor_id,
    )
    if gate is None:
        print("validation failed: gate not found", file=sys.stderr)
        return 1
    account_hash = store.bind_connection_account(
        gate_ref=args.gate_ref,
        tenant_id=args.tenant_id,
        workspace_id=args.workspace_id,
        actor_id=args.actor_id,
        connection_id=args.connection_id,
        secret=args.hmac_secret or _required_liepin_account_binding_secret(AppSettings()),
    )
    if account_hash is None:
        print("validation failed: account binding failed", file=sys.stderr)
        return 1
    print("approved")
    return 0


def _required_liepin_account_binding_secret(settings: AppSettings) -> str:
    if not settings.liepin_account_binding_secret:
        raise ValueError("SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET is required")
    return settings.liepin_account_binding_secret


def _reject_raw_account_identity_hint(value: str) -> str:
    del value
    raise argparse.ArgumentTypeError("raw account identity hints cannot be passed as CLI args")


def _liepin_compliance_gate_verify_command(args: argparse.Namespace) -> int:
    if args.purpose != "search":
        print("validation failed: liepin-compliance-gate verify requires --purpose search", file=sys.stderr)
        return 1
    store = LiepinStore(_liepin_cli_db_path(args))
    gate = store.get_compliance_gate(
        gate_ref=args.gate_ref,
        tenant_id=args.tenant_id,
        workspace_id=args.workspace_id,
        actor_id=args.actor_id,
    )
    if gate is None:
        print("validation failed: gate not found", file=sys.stderr)
        return 1
    reason = gate.denial_reason(provider_account_hash=args.provider_account_hash, purpose=args.purpose)
    if reason is not None:
        print(f"validation failed: {reason}", file=sys.stderr)
        return 1
    print("approved")
    return 0


def _liepin_replay_fixtures_command(args: argparse.Namespace) -> int:
    del args
    worker_dir = _liepin_worker_package_dir()
    if not worker_dir.is_dir():
        print(
            "validation failed: Liepin Bun worker package not found; "
            "liepin-replay-fixtures requires a source checkout",
            file=sys.stderr,
        )
        return 1
    return _run_liepin_replay_fixtures_process(
        ["bun", "test", "tests/extraction.test.ts", "tests/redaction.test.ts"],
        cwd=worker_dir,
    )


def _liepin_bun_compatibility_gate_command(args: argparse.Namespace) -> int:
    del args
    worker_dir = _liepin_worker_package_dir()
    if not worker_dir.is_dir():
        print(
            "validation failed: Liepin Bun worker package not found; "
            "liepin-bun-compatibility-gate requires a source checkout",
            file=sys.stderr,
        )
        return 1
    return _run_liepin_bun_compatibility_gate_process(["bun", "run", "compatibility-gate"], cwd=worker_dir)


def _liepin_smoke_command(args: argparse.Namespace) -> int:
    if not args.live:
        print("validation failed: liepin-smoke requires --live", file=sys.stderr)
        return 1
    missing = [
        option_name
        for option_name, attr_name in [
            ("tenant-id", "tenant_id"),
            ("workspace-id", "workspace_id"),
            ("actor-id", "actor_id"),
            ("connection-id", "connection_id"),
            ("compliance-gate-ref", "compliance_gate_ref"),
        ]
        if not getattr(args, attr_name)
    ]
    if missing:
        required = ", ".join(f"--{option_name}" for option_name in missing)
        print(f"validation failed: liepin-smoke --live requires {required}", file=sys.stderr)
        return 1
    if args.max_detail_opens < 0:
        print("validation failed: --max-detail-opens must be >= 0", file=sys.stderr)
        return 1
    keyword = args.keyword.strip()
    if not keyword:
        print("validation failed: --keyword must not be empty", file=sys.stderr)
        return 1
    if args.page_size <= 0:
        print("validation failed: --page-size must be > 0", file=sys.stderr)
        return 1

    store = LiepinStore(_liepin_cli_db_path(args))
    gate = store.get_compliance_gate(
        gate_ref=args.compliance_gate_ref,
        tenant_id=args.tenant_id,
        workspace_id=args.workspace_id,
        actor_id=args.actor_id,
    )
    if gate is None:
        print("validation failed: gate not found", file=sys.stderr)
        return 1
    connection = store.get_connection(
        tenant_id=args.tenant_id,
        workspace_id=args.workspace_id,
        actor_id=args.actor_id,
        connection_id=args.connection_id,
    )
    if connection is None or connection.compliance_gate_ref != args.compliance_gate_ref:
        print("validation failed: connection does not belong to compliance gate", file=sys.stderr)
        return 1
    reason = gate.denial_reason(provider_account_hash=connection.provider_account_hash, purpose="search")
    if reason is not None:
        print(f"validation failed: {reason}", file=sys.stderr)
        return 1

    settings = _liepin_smoke_settings(args)
    if settings.liepin_worker_mode == "fake_fixture":
        print("validation failed: live smoke refuses fake fixture worker mode", file=sys.stderr)
        return 1

    print("compliance: approved")
    print(f"worker setup: {settings.liepin_worker_mode}")
    worker_events: list[tuple[str, dict[str, object]]] = []
    try:
        worker_client = cast(_LiepinSmokeWorkerClient, build_liepin_worker_client(settings))
        session = asyncio.run(
            _liepin_smoke_worker_session(
                worker_client=worker_client,
                connection_id=args.connection_id,
                tenant_id=args.tenant_id,
                workspace_id=args.workspace_id,
                provider_account_hash=connection.provider_account_hash or "",
                worker_events=worker_events,
            )
        )
    except LiepinWorkerModeError as exc:
        _print_liepin_worker_events(worker_events)
        setup_status = exc.setup_status or "failed"
        print(f"validation failed: worker setup failed: {setup_status}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, TimeoutError, ValueError):
        _print_liepin_worker_events(worker_events)
        print("validation failed: worker setup failed: unexpected_failure", file=sys.stderr)
        return 1

    print("worker health: ok")
    if getattr(session, "fixture_only", False):
        print("validation failed: live smoke refuses fake fixture worker mode", file=sys.stderr)
        return 1
    if getattr(session, "connection_id", None) != args.connection_id:
        print("validation failed: connection_id_mismatch", file=sys.stderr)
        return 1
    if getattr(session, "status", None) != "ready":
        print(f"validation failed: session not ready: {getattr(session, 'status', 'unknown')}", file=sys.stderr)
        return 1
    if getattr(session, "provider_account_hash", None) != connection.provider_account_hash:
        print("validation failed: provider_account_mismatch", file=sys.stderr)
        return 1

    try:
        search_result = asyncio.run(
            _liepin_smoke_worker_search(
                worker_client=worker_client,
                request=_liepin_smoke_search_request(args, keyword=keyword),
                provider_account_hash=connection.provider_account_hash or "",
            )
        )
    except LiepinWorkerModeError as exc:
        setup_status = exc.setup_status or "failed"
        print(f"validation failed: worker card search failed: {setup_status}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, TimeoutError, ValueError):
        print("validation failed: worker card search failed: unexpected_failure", file=sys.stderr)
        return 1

    plan = build_detail_open_plan(
        candidates=_liepin_smoke_card_candidates(search_result),
        already_opened_provider_ids=set(),
        daily_detail_budget=args.max_detail_opens,
        consumed_detail_budget=0,
    )
    planned_detail_opens = sum(1 for decision in plan.decisions if decision.action == "open_detail")
    print("session: ready")
    print(f"card_count: {len(search_result.candidates)}")
    print(f"raw_candidate_count: {search_result.raw_candidate_count}")
    print(f"detail_budget: {args.max_detail_opens}")
    print(f"detail_open_planned: {planned_detail_opens}")
    print("artifact_refs: []")
    return 0


def _liepin_smoke_settings(args: argparse.Namespace) -> AppSettings:
    base_settings = AppSettings()
    configured_mode = args.worker_mode or base_settings.liepin_worker_mode
    if args.worker_base_url is not None:
        configured_mode = "external_http"
    if configured_mode in {"external_http", "managed_local", "pi_agent"}:
        worker_mode = configured_mode
    elif configured_mode == "fake_fixture":
        worker_mode = "fake_fixture"
    else:
        worker_mode = "managed_local"
    updates: dict[str, object] = {
        "provider_name": "liepin",
        "liepin_live_enabled": True,
        "liepin_worker_mode": worker_mode,
        "liepin_default_daily_detail_budget": args.max_detail_opens,
    }
    if args.worker_base_url is not None:
        updates["liepin_worker_base_url"] = args.worker_base_url
    settings = base_settings.model_copy(update=updates)
    if configured_mode == "fake_fixture":
        settings = settings.model_copy(update={"liepin_worker_mode": "fake_fixture"})
    return settings


async def _liepin_smoke_worker_session(
    *,
    worker_client: _LiepinSmokeWorkerClient,
    connection_id: str,
    tenant_id: str,
    workspace_id: str,
    provider_account_hash: str,
    worker_events: list[tuple[str, dict[str, object]]],
) -> object:
    await worker_client.ensure_ready(on_event=lambda name, payload: worker_events.append((name, payload)))
    return await worker_client.session_status(
        connection_id=connection_id,
        tenant=tenant_id,
        workspace=workspace_id,
        provider_account_hash=provider_account_hash,
    )


async def _liepin_smoke_worker_search(
    *,
    worker_client: _LiepinSmokeWorkerClient,
    request: SearchRequest,
    provider_account_hash: str,
) -> SearchResult:
    return await worker_client.search(
        request,
        round_no=1,
        trace_id="liepin-smoke",
        provider_account_hash=provider_account_hash,
    )


def _liepin_smoke_search_request(args: argparse.Namespace, *, keyword: str) -> SearchRequest:
    return SearchRequest(
        query_terms=[keyword],
        query_role="primary",
        keyword_query=keyword,
        adapter_notes=[],
        runtime_constraints=[],
        fetch_mode="summary",
        page_size=args.page_size,
        provider_context={
            "liepin_tenant_id": args.tenant_id,
            "liepin_workspace_id": args.workspace_id,
            "liepin_actor_id": args.actor_id,
            "liepin_connection_id": args.connection_id,
            "liepin_compliance_gate_ref": args.compliance_gate_ref,
        },
    )


def _liepin_smoke_card_candidates(search_result: SearchResult) -> list[LiepinCardCandidate]:
    candidates: list[LiepinCardCandidate] = []
    for candidate in search_result.candidates:
        candidates.append(
            LiepinCardCandidate(
                candidate_id=candidate.resume_id,
                stable_provider_id=candidate.source_resume_id or candidate.resume_id,
                weak_fingerprint=candidate.dedup_key,
                card_value_score=1.0,
            )
        )
    return candidates


def _print_liepin_worker_events(events: list[tuple[str, dict[str, object]]]) -> None:
    for event_name, payload in events:
        setup_status = payload.get("setup_status")
        if isinstance(setup_status, str) and setup_status:
            print(f"worker event: {event_name} setup_status={setup_status}", file=sys.stderr)
        else:
            print(f"worker event: {event_name}", file=sys.stderr)


def _liepin_worker_package_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "apps" / "liepin-worker"


def _run_liepin_replay_fixtures_process(command: list[str], *, cwd: Path) -> int:
    try:
        completed = subprocess.run(command, cwd=cwd, check=False)
    except FileNotFoundError:
        print("validation failed: Bun executable not found for liepin fixture replay", file=sys.stderr)
        return 1
    return completed.returncode


def _run_liepin_bun_compatibility_gate_process(command: list[str], *, cwd: Path) -> int:
    try:
        completed = subprocess.run(command, cwd=cwd, check=False)
    except FileNotFoundError:
        print("validation failed: Bun executable not found for liepin compatibility gate", file=sys.stderr)
        return 1
    return completed.returncode


def _liepin_cli_db_path(args: argparse.Namespace) -> Path:
    if args.db_path is not None:
        return Path(args.db_path)
    settings = AppSettings()
    path = Path(settings.liepin_connector_db_path)
    if path.is_absolute() or settings.workspace_root is None:
        return path
    return Path(settings.workspace_root) / path


def build_exec_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seektalent exec",
        description="Deterministic local resume matching CLI.",
        epilog=ROOT_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="store_true", help="Print the installed seektalent version and exit.")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run one resume-matching workflow.")
    run_parser.add_argument("--job-title", help="Inline job title text.")
    run_parser.add_argument("--job-title-file", help="Path to a job title file.")
    run_parser.add_argument("--jd", help="Inline job description text.")
    run_parser.add_argument("--jd-file", help="Path to a job description file.")
    run_parser.add_argument("--notes", help="Optional inline sourcing notes text.")
    run_parser.add_argument("--notes-file", help="Path to an optional sourcing notes file.")
    run_parser.add_argument("--env-file", default=".env", help="Path to the env file for this run.")
    run_parser.add_argument("--output-dir", help="Directory where run artifacts should be written.")
    run_parser.add_argument("--json", dest="json_output", action="store_true", help="Emit a single JSON object.")
    run_parser.add_argument("--max-rounds", type=int, help="Override the max retrieval rounds.")
    run_parser.add_argument("--min-rounds", type=int, help="Override the min retrieval rounds.")
    run_parser.add_argument(
        "--scoring-max-concurrency",
        type=int,
        help="Override max parallel scoring workers.",
    )
    run_parser.add_argument(
        "--search-max-pages-per-round",
        type=int,
        help="Override the per-round CTS page budget.",
    )
    run_parser.add_argument(
        "--search-max-attempts-per-round",
        type=int,
        help="Override the per-round CTS attempt budget.",
    )
    run_parser.add_argument(
        "--search-no-progress-limit",
        type=int,
        help="Override the repeated no-progress threshold.",
    )
    run_parser.add_argument(
        "--enable-eval",
        dest="enable_eval",
        action="store_true",
        default=None,
        help="Enable judge + eval for this run.",
    )
    run_parser.add_argument(
        "--disable-eval",
        dest="enable_eval",
        action="store_false",
        help="Disable judge + eval for this run.",
    )
    run_parser.add_argument(
        "--enable-reflection",
        dest="enable_reflection",
        action="store_true",
        default=None,
        help="Enable reflection for this run.",
    )
    run_parser.add_argument(
        "--disable-reflection",
        dest="enable_reflection",
        action="store_false",
        help="Disable reflection for this run.",
    )
    run_parser.set_defaults(handler=_run_command)

    benchmark_parser = subparsers.add_parser("benchmark", help="Run benchmark JDs from domain JSONL files.")
    benchmark_parser.add_argument(
        "--jds-file",
        default=None,
        help="Path to one JSONL file with benchmark JDs. When omitted, --benchmarks-dir is scanned.",
    )
    benchmark_parser.add_argument(
        "--benchmarks-dir",
        default=str(DEFAULT_BENCHMARKS_DIR),
        help="Directory of maintained benchmark JSONL files.",
    )
    benchmark_parser.add_argument("--env-file", default=".env", help="Path to the env file for this run.")
    benchmark_parser.add_argument("--output-dir", help="Directory where run artifacts should be written.")
    benchmark_parser.add_argument("--json", dest="json_output", action="store_true", help="Emit a single JSON object.")
    benchmark_parser.add_argument(
        "--benchmark-max-concurrency",
        type=int,
        default=1,
        help="Override max parallel benchmark rows.",
    )
    benchmark_parser.add_argument(
        "--benchmark-run-retries",
        type=int,
        default=1,
        help="Retry each failed benchmark row this many times.",
    )
    benchmark_parser.add_argument(
        "--benchmark-upload-retries",
        type=int,
        default=1,
        help="Retry each failed remote eval upload this many times.",
    )
    benchmark_parser.add_argument(
        "--enable-eval",
        dest="enable_eval",
        action="store_true",
        default=None,
        help="Enable judge + eval for this run.",
    )
    benchmark_parser.add_argument(
        "--disable-eval",
        dest="enable_eval",
        action="store_false",
        help="Disable judge + eval for this run.",
    )
    benchmark_parser.add_argument(
        "--enable-reflection",
        dest="enable_reflection",
        action="store_true",
        default=None,
        help="Enable reflection for this run.",
    )
    benchmark_parser.add_argument(
        "--disable-reflection",
        dest="enable_reflection",
        action="store_false",
        help="Disable reflection for this run.",
    )
    benchmark_parser.set_defaults(handler=_benchmark_command)

    archive_parser = subparsers.add_parser(
        "archive-legacy-artifacts",
        help="Archive historical runs/ contents and decommission the legacy root.",
    )
    archive_parser.add_argument("--runs-dir", default="runs", help="Legacy runs directory to archive.")
    archive_parser.add_argument("--artifacts-dir", default="artifacts", help="Active artifacts root.")
    archive_parser.add_argument("--project-root", default=".", help="Workspace root containing both locations.")
    archive_parser.set_defaults(handler=_archive_legacy_artifacts_command)

    flywheel_export_parser = subparsers.add_parser(
        "flywheel-export",
        help="Export query rewriting flywheel dataset artifacts.",
    )
    flywheel_export_parser.add_argument("--env-file", default=".env", help="Path to the env file for this export.")
    flywheel_export_parser.add_argument("--output-dir", help="Artifact root where export artifacts should be written.")
    flywheel_export_parser.add_argument("--dataset-version", required=True)
    flywheel_export_parser.add_argument("--run-id", action="append", required=True, help="Run id to include.")
    flywheel_export_parser.add_argument("--builder-config-json", default="{}", help="JSON object for builder config.")
    flywheel_export_parser.add_argument(
        "--json", dest="json_output", action="store_true", help="Emit a single JSON object."
    )
    flywheel_export_parser.set_defaults(handler=_flywheel_export_command)

    corpus_export_parser = subparsers.add_parser(
        "corpus-export",
        help="Materialize local corpus rows into a corpus artifact.",
    )
    corpus_export_parser.add_argument("--corpus-db", default=None)
    corpus_export_parser.add_argument("--artifacts-dir", default=None)
    corpus_export_parser.add_argument("--tenant-id", default="local")
    corpus_export_parser.add_argument("--workspace-id", default="default")
    corpus_export_parser.set_defaults(handler=_corpus_export_command)

    live_prf_parser = subparsers.add_parser("llm-prf-live-validate", help="Run live LLM PRF validation cases.")
    live_prf_parser.add_argument("--cases", type=Path, required=True)
    live_prf_parser.add_argument("--output-dir", type=Path, required=True)
    live_prf_parser.add_argument("--env-file", type=Path, default=Path(".env"))
    live_prf_parser.set_defaults(handler=_llm_prf_live_validate_command)

    init_parser = subparsers.add_parser("init", help="Write a starter env file in the current directory.")
    init_parser.add_argument("--env-file", default=".env", help="Where to write the generated env file.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite the target file if it exists.")
    init_parser.set_defaults(handler=_init_command)

    doctor_parser = subparsers.add_parser("doctor", help="Run local configuration checks without network calls.")
    doctor_parser.add_argument("--env-file", default=".env", help="Path to the env file to inspect.")
    doctor_parser.add_argument("--output-dir", help="Directory to validate as the artifact root.")
    doctor_parser.add_argument("--json", dest="json_output", action="store_true", help="Emit a single JSON object.")
    doctor_parser.set_defaults(handler=_doctor_command)

    version_parser = subparsers.add_parser("version", help="Print the installed seektalent version.")
    version_parser.set_defaults(handler=_version_command)

    update_parser = subparsers.add_parser("update", help="Print upgrade instructions for pip and pipx installs.")
    update_parser.set_defaults(handler=_update_command)

    inspect_parser = subparsers.add_parser("inspect", help="Describe the published CLI for wrappers and agents.")
    inspect_parser.add_argument("--json", dest="json_output", action="store_true", help="Emit a single JSON object.")
    inspect_parser.set_defaults(handler=_inspect_command)

    liepin_gate_parser = subparsers.add_parser(
        "liepin-compliance-gate",
        help="Create, bind, and verify scoped Liepin compliance gates.",
    )
    liepin_gate_subparsers = liepin_gate_parser.add_subparsers(dest="liepin_compliance_command")

    gate_create_parser = liepin_gate_subparsers.add_parser("create", help="Create a pending scoped gate.")
    _add_liepin_scope_args(gate_create_parser)
    gate_create_parser.add_argument("--purpose", required=True)
    gate_create_parser.add_argument("--policy-ref", required=True)
    gate_create_parser.add_argument("--deletion-sla-days", type=int, required=True)
    gate_create_parser.add_argument("--deletion-path", required=True)
    gate_create_parser.add_argument("--candidate-personal-info-processing-basis", required=True)
    gate_create_parser.add_argument("--personal-information-processor", required=True)
    gate_create_parser.add_argument("--operator-audit-owner", required=True)
    gate_create_parser.add_argument("--account-holder-authorized", action="store_true")
    gate_create_parser.add_argument("--human-initiated-recruiting", action="store_true")
    gate_create_parser.add_argument(
        "--retention-policy",
        choices=["run_debug_short", "workspace_recruiting_record", "forbidden_persist"],
        required=True,
    )
    gate_create_parser.add_argument(
        "--raw-payload-access-scope",
        choices=["run_only", "workspace", "admin_only"],
        required=True,
    )
    gate_create_parser.add_argument("--raw-detail-retention-allowed-after-debug", action="store_true")
    gate_create_parser.add_argument("--fixture-export-allowed", action="store_true")
    gate_create_parser.add_argument("--db-path")
    gate_create_parser.set_defaults(handler=_liepin_compliance_gate_command)

    gate_bind_parser = liepin_gate_subparsers.add_parser(
        "bind-account",
        help="Bind a worker-observed account identity for a scoped connection.",
    )
    _add_liepin_scope_args(gate_bind_parser)
    gate_bind_parser.add_argument("--gate-ref", required=True)
    gate_bind_parser.add_argument("--connection-id", required=True)
    gate_bind_parser.add_argument(
        "--observed-provider-account-subject",
        type=_reject_raw_account_identity_hint,
        help=argparse.SUPPRESS,
    )
    gate_bind_parser.add_argument("--db-path")
    gate_bind_parser.add_argument("--hmac-secret")
    gate_bind_parser.set_defaults(handler=_liepin_compliance_gate_command)

    gate_verify_parser = liepin_gate_subparsers.add_parser("verify", help="Verify a gate for live Liepin search.")
    _add_liepin_scope_args(gate_verify_parser)
    gate_verify_parser.add_argument("--gate-ref", required=True)
    gate_verify_parser.add_argument("--provider-account-hash", required=True)
    gate_verify_parser.add_argument("--purpose", default="search")
    gate_verify_parser.add_argument("--db-path")
    gate_verify_parser.set_defaults(handler=_liepin_compliance_gate_command)

    liepin_replay_parser = subparsers.add_parser(
        "liepin-replay-fixtures",
        help="Replay redacted Liepin worker fixtures without live Liepin access.",
    )
    liepin_replay_parser.set_defaults(handler=_liepin_replay_fixtures_command)

    liepin_bun_gate_parser = subparsers.add_parser(
        "liepin-bun-compatibility-gate",
        help="Run the local Bun Playwright compatibility gate without live Liepin access.",
    )
    liepin_bun_gate_parser.set_defaults(handler=_liepin_bun_compatibility_gate_command)

    liepin_smoke_parser = subparsers.add_parser(
        "liepin-smoke",
        help="Run a manual low-budget live Liepin smoke check.",
    )
    liepin_smoke_parser.add_argument("--live", action="store_true")
    liepin_smoke_parser.add_argument("--tenant-id")
    liepin_smoke_parser.add_argument("--workspace-id")
    liepin_smoke_parser.add_argument("--actor-id")
    liepin_smoke_parser.add_argument("--connection-id")
    liepin_smoke_parser.add_argument("--compliance-gate-ref")
    liepin_smoke_parser.add_argument("--max-detail-opens", type=int, default=1)
    liepin_smoke_parser.add_argument("--keyword", default="python")
    liepin_smoke_parser.add_argument("--page-size", type=int, default=1)
    liepin_smoke_parser.add_argument(
        "--worker-mode",
        choices=["fake_fixture", "managed_local", "external_http", "pi_agent"],
    )
    liepin_smoke_parser.add_argument("--worker-base-url")
    liepin_smoke_parser.add_argument("--db-path")
    liepin_smoke_parser.set_defaults(handler=_liepin_smoke_command)
    return parser


def _add_liepin_scope_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--actor-id", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seektalent",
        description="Interactive terminal entry for SeekTalent.",
        epilog=ROOT_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="store_true", help="Print the installed seektalent version and exit.")
    subparsers = parser.add_subparsers(dest="command")
    exec_parser = subparsers.add_parser("exec", help="Run direct CLI commands.")
    exec_parser.add_argument("exec_args", nargs=argparse.REMAINDER)
    return parser


def _is_interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _launch_tui() -> int:
    from seektalent.tui import run_chat_session

    return run_chat_session()


def _run_exec(args_list: list[str]) -> int:
    parser = build_exec_parser()
    args = parser.parse_args(args_list)
    if args.version and args.command is None:
        print(__version__)
        return 0
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return args.handler(args)
    except Exception as exc:  # noqa: BLE001
        _emit_error(exc, json_output=getattr(args, "json_output", False))
        return 1


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list:
        if _is_interactive_terminal():
            load_process_env()
            cleanup_runtime_artifacts(AppSettings())
            return _launch_tui()
        parser = build_parser()
        parser.print_help()
        return 0
    if args_list == ["--version"]:
        print(__version__)
        return 0
    if args_list[0] == "exec":
        return _run_exec(args_list[1:])
    if args_list[0] in KNOWN_COMMANDS:
        return _run_exec(args_list)
    parser = build_parser()
    args = parser.parse_args(args_list)
    if args.version and args.command is None:
        print(__version__)
        return 0
    if args.command == "exec":
        return _run_exec(args.exec_args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
