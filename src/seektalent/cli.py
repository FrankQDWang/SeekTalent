from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import ValidationError

from seektalent import __version__
from seektalent.api import run_match
from seektalent.bootstrap_assets import default_bootstrap_assets
from seektalent.config import AppSettings
from seektalent.resources import read_default_env_template, resolve_user_path, runtime_active_file
from seektalent.run_artifacts import RUNTIME_STATUS

ROOT_HELP_EPILOG = """Runtime status:
  `run` now executes the full v0.3.2 runtime loop and returns SearchRunBundle.
  Each run writes bundle.json, final_result.json, and eval.json under runs/<run_id>/.
  Start `seektalent-rerank-api` before running searches.

Recommended workflow:
  1. seektalent doctor
  2. seektalent inspect --json
  3. seektalent-rerank-api
  4. seektalent run --jd-file ./jd.md
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
    mutually_exclusive_with: list[str] | None = None,
    default: object | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": name,
        "kind": kind,
        "required": required,
        "repeatable": False,
        "mutually_exclusive_with": mutually_exclusive_with or [],
        "description": description,
    }
    if default is not None:
        payload["default"] = default
    return payload


def _emit_json(stream, payload: object) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _error_payload(exc: Exception) -> dict[str, str]:
    return {"error": str(exc), "error_type": type(exc).__name__}


def _emit_error(exc: Exception, *, json_output: bool) -> None:
    if json_output:
        _emit_json(sys.stderr, _error_payload(exc))
        return
    print(f"Error: {exc}", file=sys.stderr)


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


def _build_settings(args: argparse.Namespace) -> AppSettings:
    runs_dir = None
    if getattr(args, "output_dir", None):
        runs_dir = str(resolve_user_path(args.output_dir))
    return AppSettings(_env_file=getattr(args, "env_file", ".env")).with_overrides(
        mock_cts=getattr(args, "mock_cts", None),
        runs_dir=runs_dir,
    )


def _doctor_checks(settings: AppSettings) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    spec_file = settings.spec_file
    checks.append(
        DoctorCheck(
            name="packaged_spec",
            ok=spec_file.exists(),
            message=f"CTS spec file: {spec_file}",
        )
    )
    output_dir = settings.runs_path
    output_dir.mkdir(parents=True, exist_ok=True)
    checks.append(
        DoctorCheck(
            name="output_dir",
            ok=output_dir.is_dir(),
            message=f"Runs directory: {output_dir}",
        )
    )
    active_manifest_path = runtime_active_file()
    if not active_manifest_path.exists():
        checks.append(
            DoctorCheck(
                name="runtime_manifest",
                ok=True,
                message=(
                    "Active runtime manifest is not bundled in this install. "
                    "Source-checkout doctor validates repo-local runtime artifacts."
                ),
            )
        )
    else:
        try:
            assets = default_bootstrap_assets()
            checks.append(
                DoctorCheck(
                    name="runtime_manifest",
                    ok=True,
                    message=(
                        "Active runtime manifest loaded: "
                        f"knowledge_packs={list(assets.knowledge_pack_ids)}, "
                        f"policy={assets.policy_id}, calibration={assets.calibration_id}"
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                DoctorCheck(
                    name="runtime_manifest",
                    ok=False,
                    message=str(exc),
                )
            )
    if settings.mock_cts:
        checks.append(DoctorCheck("cts_credentials", True, "Skipped in mock CTS mode."))
    else:
        missing = [
            name
            for name, value in (
                ("SEEKTALENT_CTS_TENANT_KEY", settings.cts_tenant_key),
                ("SEEKTALENT_CTS_TENANT_SECRET", settings.cts_tenant_secret),
            )
            if not value
        ]
        checks.append(
            DoctorCheck(
                name="cts_credentials",
                ok=not missing,
                message="CTS credentials present." if not missing else f"Missing: {', '.join(missing)}",
            )
        )
    checks.append(
        DoctorCheck(
            name="phase",
            ok=True,
            message=(
                "v0.3.2 offline artifacts active. `run` emits SearchRunBundle, writes "
                "run artifacts under SEEKTALENT_RUNS_DIR, and expects a rerank API at "
                "SEEKTALENT_RERANK_BASE_URL."
            ),
        )
    )
    return checks


def _inspect_payload() -> dict[str, object]:
    commands = {
        "run": {
            "description": "Execute the full v0.3.2 runtime loop, persist run artifacts, and return SearchRunBundle.",
            "machine_readable": False,
            "arguments": [
                _arg_spec("--jd", "string", "Inline job description text.", mutually_exclusive_with=["--jd-file"]),
                _arg_spec("--jd-file", "path", "Path to a job description file.", mutually_exclusive_with=["--jd"]),
                _arg_spec("--notes", "string", "Optional inline hiring notes.", mutually_exclusive_with=["--notes-file"]),
                _arg_spec("--notes-file", "path", "Path to optional hiring notes.", mutually_exclusive_with=["--notes"]),
                _arg_spec("--round-budget", "integer", "Override the runtime round budget. Values are clamped to 5-12."),
                _arg_spec("--env-file", "path", "Env file to read settings from.", default=".env"),
                _arg_spec("--json", "flag", "Emit one JSON error object on stderr when the command fails."),
            ],
        },
        "doctor": {
            "description": "Validate the packaged CTS spec, settings, runs directory, and CTS credentials.",
            "machine_readable": False,
            "arguments": [
                _arg_spec("--env-file", "path", "Env file to inspect.", default=".env"),
                _arg_spec("--output-dir", "path", "Override the runs directory."),
                _arg_spec("--json", "flag", "Emit one JSON object to stdout."),
            ],
        },
        "init": {
            "description": "Write the minimal starter env file.",
            "machine_readable": False,
            "arguments": [
                _arg_spec("--env-file", "path", "Where to write the env file.", default=".env"),
                _arg_spec("--force", "flag", "Overwrite the target file."),
            ],
        },
        "version": {"description": "Print the installed package version.", "machine_readable": False, "arguments": []},
        "update": {"description": "Print upgrade instructions.", "machine_readable": False, "arguments": []},
        "inspect": {
            "description": "Describe the current v0.3.2 offline artifact surface.",
            "machine_readable": False,
            "arguments": [_arg_spec("--json", "flag", "Emit one JSON object describing the CLI.")],
        },
    }
    return {
        "tool": "seektalent",
        "version": __version__,
        "phase": RUNTIME_STATUS,
        "summary": (
            "v0.3.2 offline artifacts active: runtime returns SearchRunBundle, "
            "writes bundle/final_result/eval artifacts, and binds bootstrap assets from "
            "artifacts/runtime/active.json."
        ),
        "recommended_workflow": [
            "seektalent doctor",
            "seektalent inspect --json",
            "seektalent-rerank-api",
            "seektalent run --jd-file ./jd.md",
        ],
        "commands": commands,
        "environment": {
            "required_for_real_cts": [
                "SEEKTALENT_CTS_TENANT_KEY",
                "SEEKTALENT_CTS_TENANT_SECRET",
            ],
            "optional": [
                "SEEKTALENT_CTS_BASE_URL",
                "SEEKTALENT_CTS_TIMEOUT_SECONDS",
                "SEEKTALENT_CTS_SPEC_PATH",
                "SEEKTALENT_MOCK_CTS",
                "SEEKTALENT_RUNS_DIR",
                "SEEKTALENT_RERANK_BASE_URL",
                "SEEKTALENT_RERANK_TIMEOUT_SECONDS",
            ],
        },
        "json_contracts": {
            "run": {
                "stdout_success_fields": [
                    "phase",
                    "run_id",
                    "run_dir",
                    "bootstrap",
                    "rounds",
                    "finalization_audit",
                    "final_result",
                    "eval",
                ],
                "stderr_json_fields": ["error", "error_type"],
                "current_behavior": "Runs the full runtime loop, writes run artifacts, and returns SearchRunBundle.",
            },
            "doctor": {"stdout_success_fields": ["ok", "checks"]},
        },
        "failure_contract": {"stderr_json_fields": ["error", "error_type"]},
    }


def _print_doctor_human(checks: list[DoctorCheck]) -> None:
    for check in checks:
        status = "OK" if check.ok else "FAIL"
        print(f"{status} {check.name}: {check.message}")


def _handle_run(args: argparse.Namespace) -> int:
    try:
        settings = _build_settings(args)
        job_description = _read_text(inline_value=args.jd, file_value=args.jd_file, label="jd")
        hiring_notes = _read_optional_text(inline_value=args.notes, file_value=args.notes_file, label="notes")
        result = run_match(
            job_description=job_description,
            hiring_notes=hiring_notes,
            round_budget=args.round_budget,
            settings=settings,
            env_file=args.env_file,
        )
    except Exception as exc:  # noqa: BLE001
        _emit_error(exc, json_output=args.json)
        return 1
    if args.json:
        _emit_json(sys.stdout, result.model_dump(mode="json"))
        return 0
    print(result.run_dir)
    print(result.final_result.stop_reason)
    print(", ".join(result.final_result.final_shortlist_candidate_ids))
    print(result.final_result.run_summary)
    return 0


def _handle_doctor(args: argparse.Namespace) -> int:
    try:
        settings = _build_settings(args)
        checks = _doctor_checks(settings)
    except (ValidationError, ValueError) as exc:
        _emit_error(exc, json_output=args.json)
        return 1
    ok = all(check.ok for check in checks)
    if args.json:
        _emit_json(sys.stdout, {"ok": ok, "checks": [asdict(check) for check in checks]})
    else:
        _print_doctor_human(checks)
    return 0 if ok else 1


def _handle_init(args: argparse.Namespace) -> int:
    env_path = resolve_user_path(args.env_file)
    if env_path.exists() and not args.force:
        print(f"Error: {env_path} already exists. Use --force to overwrite it.", file=sys.stderr)
        return 1
    env_path.write_text(read_default_env_template(), encoding="utf-8")
    print(env_path)
    return 0


def _handle_version() -> int:
    print(__version__)
    return 0


def _handle_update() -> int:
    print(f"Current version: {__version__}")
    print("Upgrade with:")
    print("  pip install -U seektalent")
    print(f"  pip install -U seektalent=={__version__}")
    print("  pipx upgrade seektalent")
    print("This command only prints instructions.")
    return 0


def _handle_inspect(args: argparse.Namespace) -> int:
    payload = _inspect_payload()
    if args.json:
        _emit_json(sys.stdout, payload)
        return 0
    print("SeekTalent v0.3.2 CLI inspection summary")
    print("Use `seektalent inspect --json` for the machine-readable contract.")
    print(f"Current phase: {payload['phase']}")
    print(f"Run behavior: {payload['summary']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seektalent", epilog=ROOT_HELP_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run the full v0.3.2 runtime loop and write artifacts.")
    run_parser.add_argument("--jd")
    run_parser.add_argument("--jd-file")
    run_parser.add_argument("--notes")
    run_parser.add_argument("--notes-file")
    run_parser.add_argument("--round-budget", type=int)
    run_parser.add_argument("--env-file", default=".env")
    run_parser.add_argument("--json", action="store_true")
    run_parser.set_defaults(handler=_handle_run)

    doctor_parser = subparsers.add_parser("doctor", help="Validate local phase-6 runtime artifacts and settings.")
    doctor_parser.add_argument("--env-file", default=".env")
    doctor_parser.add_argument("--output-dir")
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.set_defaults(handler=_handle_doctor)

    init_parser = subparsers.add_parser("init", help="Write the default env template.")
    init_parser.add_argument("--env-file", default=".env")
    init_parser.add_argument("--force", action="store_true")
    init_parser.set_defaults(handler=_handle_init)

    version_parser = subparsers.add_parser("version", help="Print the installed version.")
    version_parser.set_defaults(handler=lambda args: _handle_version())

    update_parser = subparsers.add_parser("update", help="Print upgrade instructions.")
    update_parser.set_defaults(handler=lambda args: _handle_update())

    inspect_parser = subparsers.add_parser("inspect", help="Describe the current CLI surface.")
    inspect_parser.add_argument("--json", action="store_true")
    inspect_parser.set_defaults(handler=_handle_inspect)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)


__all__ = ["build_parser", "main"]
