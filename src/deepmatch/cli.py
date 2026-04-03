from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import ValidationError

from deepmatch import __version__
from deepmatch.api import MatchRunResult, run_match
from deepmatch.config import AppSettings, load_process_env
from deepmatch.resources import (
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
SUBCOMMANDS = {"run", "init", "doctor", "version"}


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    message: str


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


def _write_human_result(result: MatchRunResult) -> None:
    if result.final_markdown:
        print(result.final_markdown.rstrip())
    print(f"run_id: {result.run_id}")
    print(f"run_directory: {result.run_dir}")
    print(f"trace_log: {result.trace_log_path}")


def _run_command(args: argparse.Namespace) -> int:
    settings = _build_settings(args)
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
    required_vars = sorted(
        {
            env_var
            for model_id in (
                settings.requirements_model,
                settings.controller_model,
                settings.scoring_model,
                settings.reflection_model,
                settings.finalize_model,
            )
            if (env_var := _provider_env_var(model_id)) is not None
        }
    )
    missing = [name for name in required_vars if not os.environ.get(name)]
    if missing:
        return DoctorCheck("provider_credentials", False, f"Missing credentials: {', '.join(missing)}")
    if required_vars:
        return DoctorCheck("provider_credentials", True, f"Found credentials: {', '.join(required_vars)}")
    return DoctorCheck("provider_credentials", True, "No provider credentials required by current models.")


def _cts_credentials_check(settings: AppSettings | None) -> DoctorCheck:
    assert settings is not None
    if settings.mock_cts:
        return DoctorCheck("cts_credentials", True, "Mock CTS enabled; CTS credentials not required.")
    missing = [
        name
        for name, value in (
            ("DEEPMATCH_CTS_TENANT_KEY", settings.cts_tenant_key),
            ("DEEPMATCH_CTS_TENANT_SECRET", settings.cts_tenant_secret),
        )
        if not value
    ]
    if missing:
        return DoctorCheck("cts_credentials", False, f"Missing CTS credentials: {', '.join(missing)}")
    return DoctorCheck("cts_credentials", True, "CTS credentials are configured.")


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
        checks.append(_output_dir_check(settings))
        checks.append(_provider_credentials_check(settings))
        checks.append(_cts_credentials_check(settings))

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deepmatch", description="Deterministic local resume matching CLI.")
    parser.add_argument("--version", action="store_true", help="Print the installed deepmatch version and exit.")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run one resume-matching workflow.")
    run_parser.add_argument("--jd", help="Inline job description text.")
    run_parser.add_argument("--jd-file", help="Path to a job description file.")
    run_parser.add_argument("--notes", help="Optional inline sourcing notes text.")
    run_parser.add_argument("--notes-file", help="Path to an optional sourcing notes file.")
    run_parser.add_argument("--env-file", default=".env", help="Path to the env file for this run.")
    run_parser.add_argument("--output-dir", help="Directory where run artifacts should be written.")
    run_parser.add_argument("--json", dest="json_output", action="store_true", help="Emit a single JSON object.")
    run_parser.add_argument("--mock-cts", dest="mock_cts", action="store_true", default=None, help="Use mock CTS.")
    run_parser.add_argument("--real-cts", dest="mock_cts", action="store_false", help="Use real CTS.")
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
    doctor_parser.add_argument("--mock-cts", dest="mock_cts", action="store_true", default=None, help="Force mock CTS checks.")
    doctor_parser.add_argument("--real-cts", dest="mock_cts", action="store_false", help="Force real CTS checks.")
    doctor_parser.set_defaults(handler=_doctor_command)

    version_parser = subparsers.add_parser("version", help="Print the installed deepmatch version.")
    version_parser.set_defaults(handler=_version_command)
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
