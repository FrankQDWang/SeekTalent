from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import ValidationError

from seektalent import __version__
from seektalent.api import MatchRunResult, run_match
from seektalent.config import AppSettings, load_process_env
from seektalent.resources import (
    REQUIRED_PROMPTS,
    package_prompt_dir,
    package_spec_file,
    read_default_env_template,
    resolve_user_path,
)

PROVIDER_ENV_VAR_BY_PREFIX = {
    "openai": "OPENAI_API_KEY",
    "openai-chat": "OPENAI_API_KEY",
    "openai-responses": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google-gla": "GOOGLE_API_KEY",
}
OPTIONAL_RUNTIME_ENV_VARS = [
    "SEEKTALENT_CTS_BASE_URL",
    "SEEKTALENT_CTS_TIMEOUT_SECONDS",
    "SEEKTALENT_CTS_SPEC_PATH",
    "SEEKTALENT_REQUIREMENTS_MODEL",
    "SEEKTALENT_CONTROLLER_MODEL",
    "SEEKTALENT_SCORING_MODEL",
    "SEEKTALENT_FINALIZE_MODEL",
    "SEEKTALENT_REFLECTION_MODEL",
    "SEEKTALENT_JUDGE_MODEL",
    "SEEKTALENT_JUDGE_OPENAI_BASE_URL",
    "SEEKTALENT_JUDGE_OPENAI_API_KEY",
    "SEEKTALENT_REASONING_EFFORT",
    "SEEKTALENT_JUDGE_REASONING_EFFORT",
    "SEEKTALENT_MIN_ROUNDS",
    "SEEKTALENT_MAX_ROUNDS",
    "SEEKTALENT_SCORING_MAX_CONCURRENCY",
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
    "trace.log",
    "events.jsonl",
    "run_config.json",
    "input_snapshot.json",
    "input_truth.json",
    "requirement_extraction_draft.json",
    "requirements_call.json",
    "requirement_sheet.json",
    "scoring_policy.json",
    "sent_query_history.json",
    "finalizer_context.json",
    "finalizer_call.json",
    "final_candidates.json",
    "final_answer.md",
    "judge_packet.json",
    "run_summary.md",
    "evaluation/evaluation.json",
]
KEY_HANDOFF_FILES = [
    "trace.log",
    "events.jsonl",
    "run_config.json",
    "final_answer.md",
    "final_candidates.json",
    "evaluation/evaluation.json",
]
SUBCOMMANDS = {"run", "init", "doctor", "version", "update", "inspect"}
ROOT_HELP_EPILOG = """Primary workflow:
  1. seektalent doctor
  2. seektalent run --jd-file ./jd.md

Required environment variables:
  OPENAI_API_KEY
  SEEKTALENT_CTS_TENANT_KEY
  SEEKTALENT_CTS_TENANT_SECRET

Inputs:
  Provide the job description with --jd or --jd-file.

Artifacts:
  Runs write structured outputs under ./runs by default or --output-dir when set.

Upgrade:
  seektalent update

Machine-readable discovery:
  seektalent inspect --json
"""


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    message: str


def _arg_spec(
    name: str,
    kind: str,
    description: str,
    *,
    required: bool = False,
    repeatable: bool = False,
    mutually_exclusive_with: list[str] | None = None,
    default: object | None = None,
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
    if default is not None:
        spec["default"] = default
    if applies_to:
        spec["applies_to"] = applies_to
    return spec


def _normalize_legacy_argv(argv: list[str]) -> list[str]:
    if not argv:
        return argv
    if argv[0] in SUBCOMMANDS or argv[0] in {"-h", "--help", "--version"}:
        return argv
    return ["run", *argv]


def _build_settings(args: argparse.Namespace) -> AppSettings:
    overrides = {
        "mock_cts": getattr(args, "mock_cts", None),
        "max_rounds": getattr(args, "max_rounds", None),
        "min_rounds": getattr(args, "min_rounds", None),
        "scoring_max_concurrency": getattr(args, "scoring_max_concurrency", None),
        "search_max_pages_per_round": getattr(args, "search_max_pages_per_round", None),
        "search_max_attempts_per_round": getattr(args, "search_max_attempts_per_round", None),
        "search_no_progress_limit": getattr(args, "search_no_progress_limit", None),
        "enable_eval": getattr(args, "enable_eval", None),
        "enable_reflection": getattr(args, "enable_reflection", None),
        "runs_dir": str(resolve_user_path(args.output_dir)) if getattr(args, "output_dir", None) else None,
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


def _provider_env_var(model_id: str) -> str | None:
    provider = model_id.split(":", 1)[0]
    return PROVIDER_ENV_VAR_BY_PREFIX.get(provider)


def _emit_json(stream, payload: object) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _emit_error(exc: Exception, *, json_output: bool) -> None:
    if json_output:
        _emit_json(sys.stderr, _error_payload(exc))
        return
    print(f"Error: {exc}", file=sys.stderr)


def _required_provider_env_vars(settings: AppSettings) -> list[str]:
    model_ids = [
        settings.requirements_model,
        settings.controller_model,
        settings.scoring_model,
        settings.reflection_model,
        settings.finalize_model,
    ]
    if settings.enable_eval:
        model_ids.append(settings.effective_judge_model)
    return sorted(
        {
            env_var
            for model_id in model_ids
            if (env_var := _provider_env_var(model_id)) is not None
        }
    )


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


def _inspect_payload() -> dict[str, object]:
    commands = {
        "run": {
            "description": "Run one resume-matching workflow.",
            "machine_readable": False,
            "arguments": [
                _arg_spec("--jd", "string", "Inline job description text.", mutually_exclusive_with=["--jd-file"]),
                _arg_spec("--jd-file", "path", "Path to a job description file.", mutually_exclusive_with=["--jd"]),
                _arg_spec("--notes", "string", "Optional inline sourcing notes text.", mutually_exclusive_with=["--notes-file"]),
                _arg_spec("--notes-file", "path", "Path to an optional sourcing notes file.", mutually_exclusive_with=["--notes"]),
                _arg_spec("--env-file", "path", "Path to the env file for this run.", default=".env"),
                _arg_spec("--output-dir", "path", "Directory where run artifacts should be written."),
                _arg_spec("--json", "flag", "Emit a single JSON object."),
                _arg_spec("--max-rounds", "integer", "Override the maximum retrieval rounds (3-10)."),
                _arg_spec("--min-rounds", "integer", "Override the minimum retrieval rounds (3-10)."),
                _arg_spec("--scoring-max-concurrency", "integer", "Override max parallel scoring workers."),
                _arg_spec("--search-max-pages-per-round", "integer", "Override the per-round CTS page budget."),
                _arg_spec("--search-max-attempts-per-round", "integer", "Override the per-round CTS attempt budget."),
                _arg_spec("--search-no-progress-limit", "integer", "Override the repeated no-progress threshold."),
                _arg_spec("--enable-eval", "flag", "Enable judge + eval for this run.", mutually_exclusive_with=["--disable-eval"]),
                _arg_spec("--disable-eval", "flag", "Disable judge + eval for this run.", mutually_exclusive_with=["--enable-eval"]),
                _arg_spec("--enable-reflection", "flag", "Enable reflection for this run.", mutually_exclusive_with=["--disable-reflection"]),
                _arg_spec("--disable-reflection", "flag", "Disable reflection for this run.", mutually_exclusive_with=["--enable-reflection"]),
            ],
            "examples": [
                "seektalent run --jd-file ./jd.md",
                "seektalent run --jd 'Python engineer' --notes 'Shanghai preferred' --json",
            ],
            "outputs": "Human-readable shortlist on stdout by default. In --json mode, stdout contains one JSON object.",
            "side_effects": "Creates a run artifact directory under ./runs or the path passed to --output-dir.",
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
            "seektalent run --jd-file ./jd.md",
            "seektalent update",
        ],
        "commands": commands,
        "environment": {
            "required_for_default_run": [
                "OPENAI_API_KEY",
                "SEEKTALENT_CTS_TENANT_KEY",
                "SEEKTALENT_CTS_TENANT_SECRET",
            ],
            "optional_provider_vars": [
                "OPENAI_BASE_URL",
                "ANTHROPIC_API_KEY",
                "GOOGLE_API_KEY",
            ],
            "optional_runtime_vars": OPTIONAL_RUNTIME_ENV_VARS,
            "env_file_support": "run and doctor accept --env-file to load values from a file; shell environment variables remain first-class.",
        },
        "artifacts": {
            "default_runs_dir": "./runs",
            "override_flag": "--output-dir",
            "top_level_files": TOP_LEVEL_ARTIFACT_FILES,
            "key_handoff_files": KEY_HANDOFF_FILES,
        },
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
    load_process_env(args.env_file)
    settings = _build_settings(args)
    _reject_mock_cts(settings)
    missing_provider = _missing_provider_env_vars(settings)
    missing_cts = _missing_cts_env_vars(settings)
    if missing_provider or missing_cts:
        raise ValueError(
            _missing_credentials_message(
                missing_provider=missing_provider,
                missing_cts=missing_cts,
            )
        )
    result = run_match(
        jd=_read_text(inline_value=args.jd, file_value=args.jd_file, label="jd"),
        notes=_read_optional_text(inline_value=args.notes, file_value=args.notes_file, label="notes"),
        settings=settings,
        env_file=args.env_file,
    )
    if args.json_output:
        _emit_json(sys.stdout, _result_payload(result))
        return 0
    _write_human_result(result)
    return 0


def _init_command(args: argparse.Namespace) -> int:
    env_path = resolve_user_path(args.env_file)
    if env_path.exists() and not args.force:
        raise ValueError(f"{env_path} already exists. Use --force to overwrite it.")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(read_default_env_template(), encoding="utf-8")
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


def _settings_check(settings: AppSettings | None, error: ValidationError | None) -> DoctorCheck:
    if error is not None:
        message = "; ".join(item["msg"] for item in error.errors())
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
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
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


def _doctor_command(args: argparse.Namespace) -> int:
    load_process_env(args.env_file)
    checks = _package_resource_checks()
    settings: AppSettings | None = None
    settings_error: ValidationError | None = None
    try:
        settings = _build_settings(args)
    except ValidationError as exc:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seektalent",
        description="Deterministic local resume matching CLI.",
        epilog=ROOT_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="store_true", help="Print the installed seektalent version and exit.")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run one resume-matching workflow.")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(_normalize_legacy_argv(list(sys.argv[1:] if argv is None else argv)))
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


if __name__ == "__main__":
    raise SystemExit(main())
