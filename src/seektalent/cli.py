from __future__ import annotations

import argparse
import difflib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from seektalent import __version__
from seektalent.api import run_match
from seektalent.bootstrap_assets import default_bootstrap_assets
from seektalent.config import AppSettings
from seektalent.llm_config import CALLPOINT_ENV_PREFIXES, inspect_llm_callpoints, resolve_llm_config
from seektalent.progress import ProgressEvent
from seektalent.resources import read_env_template, resolve_user_path, runtime_active_file
from seektalent.run_artifacts import RUNTIME_STATUS

KNOWN_COMMANDS = ("run", "doctor", "init", "version", "update", "inspect")
REMOVED_INLINE_FLAGS = ("--jd", "--notes")
REQUEST_JSON_EXAMPLE = json.dumps(
    {
        "job_description": "Senior agent engineer with Python and LLM orchestration experience",
        "hiring_notes": "Shanghai preferred; startup background is a plus",
        "top_k": 10,
        "round_budget": 6,
    },
    ensure_ascii=False,
)
ROOT_HELP_EPILOG = """Human entry:
  seektalent
  Launch the chat-first Textual session when attached to a TTY.
  Paste Job Description, press Shift+Enter, then optionally add Hiring Notes.

Agent entry:
  seektalent run --request-file ./request.json --json --progress jsonl

Optional diagnostics:
  seektalent doctor --json
  seektalent inspect --json
"""
RUN_HELP_EPILOG = """Long inputs should go through files or stdin, not shell inline flags.

Examples:
  seektalent run --request-file ./request.json
  seektalent run --request-file ./request.json --json --progress jsonl
  cat request.json | seektalent run --request-stdin --json --progress jsonl
  seektalent run --jd-file ./jd.md --notes-file ./notes.md

Final candidate cards live at:
  final_result.final_candidate_cards
"""


class RunRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_description: str = Field(min_length=1)
    hiring_notes: str = ""
    top_k: int = Field(default=10, ge=1, le=10)
    round_budget: int | None = None


class GuidedCliError(ValueError):
    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


class SeekTalentArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if self.prog.endswith(" run"):
            raise GuidedCliError(
                "invalid_run_arguments",
                message,
                "Try: seektalent run --help\nAgent example: seektalent run --request-file ./request.json --json --progress jsonl",
            )
        raise GuidedCliError("invalid_arguments", message, "Try: seektalent --help")


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
    payload = {"error": str(exc), "error_type": type(exc).__name__}
    if isinstance(exc, GuidedCliError):
        payload["code"] = exc.code
        if exc.hint:
            payload["hint"] = exc.hint
    return payload


def _emit_error(exc: Exception, *, json_output: bool) -> None:
    if json_output:
        _emit_json(sys.stderr, _error_payload(exc))
        return
    if isinstance(exc, GuidedCliError):
        print(f"{exc.code}: {exc}", file=sys.stderr)
        if exc.hint:
            print(exc.hint, file=sys.stderr)
        return
    print(f"error: {exc}", file=sys.stderr)


def _build_settings(args: argparse.Namespace) -> AppSettings:
    runs_dir = None
    if getattr(args, "output_dir", None):
        runs_dir = str(resolve_user_path(args.output_dir))
    return AppSettings(_env_file=getattr(args, "env_file", ".env")).with_overrides(
        mock_cts=getattr(args, "mock_cts", None),
        runs_dir=runs_dir,
    )


def _doctor_checks(settings: AppSettings, *, env_file: str) -> list[DoctorCheck]:
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
            checks.append(DoctorCheck(name="runtime_manifest", ok=False, message=str(exc)))
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
                "v0.3.3 active. `seektalent` opens a chat-first Textual session in a TTY, "
                "`run` is the non-interactive protocol surface, and final candidate "
                "results live at final_result.final_candidate_cards."
            ),
        )
    )
    for callpoint in CALLPOINT_ENV_PREFIXES:
        try:
            config = resolve_llm_config(callpoint, env_file=env_file)
        except ValueError as exc:
            checks.append(DoctorCheck(f"llm_{callpoint}", False, str(exc)))
            continue
        checks.append(
            DoctorCheck(
                name=f"llm_{callpoint}",
                ok=True,
                message=(
                    f"provider={config.provider}, model={config.model}, "
                    f"output_mode={config.resolved_output_mode}"
                ),
            )
        )
    return checks


def _inspect_payload(*, env_file: str = ".env") -> dict[str, object]:
    commands = {
        "run": {
            "description": "Execute the non-interactive search protocol and return SearchRunBundle.",
            "machine_readable": True,
            "arguments": [
                _arg_spec(
                    "--request-file",
                    "path",
                    "Path to a JSON request with job_description, optional hiring_notes, top_k, and round_budget.",
                    mutually_exclusive_with=["--request-stdin", "--jd-file"],
                ),
                _arg_spec(
                    "--request-stdin",
                    "flag",
                    "Read the same request JSON contract from stdin.",
                    mutually_exclusive_with=["--request-file", "--jd-file"],
                ),
                _arg_spec(
                    "--jd-file",
                    "path",
                    "Path to a job description file.",
                    mutually_exclusive_with=["--request-file", "--request-stdin"],
                ),
                _arg_spec("--notes-file", "path", "Path to optional hiring notes when using --jd-file."),
                _arg_spec("--round-budget", "integer", "Override the runtime round budget."),
                _arg_spec("--progress", "enum", "Progress mode: text, jsonl, or off.", default="auto"),
                _arg_spec("--env-file", "path", "Env file to read settings from.", default=".env"),
                _arg_spec("--json", "flag", "Emit SearchRunBundle on stdout."),
            ],
        },
        "doctor": {
            "description": "Optional diagnostic: validate the local runtime surface without making network calls.",
            "machine_readable": True,
            "arguments": [
                _arg_spec("--env-file", "path", "Env file to inspect.", default=".env"),
                _arg_spec("--output-dir", "path", "Override the runs directory."),
                _arg_spec("--json", "flag", "Emit one JSON object to stdout."),
            ],
        },
        "init": {
            "description": "Write the repo env template.",
            "machine_readable": False,
            "arguments": [
                _arg_spec("--env-file", "path", "Where to write the env file.", default=".env"),
                _arg_spec("--force", "flag", "Overwrite the target file."),
            ],
        },
        "version": {"description": "Print the installed package version.", "machine_readable": False, "arguments": []},
        "update": {"description": "Print upgrade instructions.", "machine_readable": False, "arguments": []},
        "inspect": {
            "description": "Describe the current CLI contract and result pointers.",
            "machine_readable": True,
            "arguments": [
                _arg_spec("--env-file", "path", "Env file to inspect.", default=".env"),
                _arg_spec("--json", "flag", "Emit one JSON object describing the CLI."),
            ],
        },
    }
    llm_callpoints = {
        name: {
            "provider": status.provider,
            "model": status.model,
            "base_url_configured": status.base_url_configured,
            "requested_output_mode": status.requested_output_mode,
            "resolved_output_mode": status.resolved_output_mode,
        }
        for name, status in inspect_llm_callpoints(env_file).items()
    }
    return {
        "tool": "seektalent",
        "version": __version__,
        "phase": RUNTIME_STATUS,
        "summary": (
            "v0.3.3 active: no-arg TTY opens a one-shot chat-first Textual session, "
            "`run` is the agent-facing protocol command, and final candidate "
            "results are exposed through final_result.final_candidate_cards."
        ),
        "interactive_entry": {
            "command": "seektalent",
            "description": "Launch a one-shot chat-first Textual session when attached to a TTY.",
            "input_flow": ["job_description", "hiring_notes_optional"],
            "submit_key": "Shift+Enter",
            "session_behavior": "Single run per launch. Re-run `seektalent` to start a new session.",
        },
        "non_interactive_entry": {
            "command": "seektalent run --request-file ./request.json --json --progress jsonl",
            "description": "Machine-friendly entry for full bundle output plus stderr JSONL progress.",
        },
        "recommended_examples": [
            "seektalent",
            "seektalent run --request-file ./request.json",
            "seektalent run --request-file ./request.json --json --progress jsonl",
            "cat request.json | seektalent run --request-stdin --json --progress jsonl",
        ],
        "commands": commands,
        "environment": {
            "provider_credentials": [
                "OPENAI_API_KEY",
                "OPENAI_BASE_URL",
                "ANTHROPIC_API_KEY",
                "GOOGLE_API_KEY",
            ],
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
        "llm_callpoints": llm_callpoints,
        "request_contract": {
            "preferred": "--request-file",
            "stdin_flag": "--request-stdin",
            "file_pair_fallback": ["--jd-file", "--notes-file"],
            "json_schema": {
                "job_description": "string, required",
                "hiring_notes": "string, optional",
                "top_k": "integer 1..10, optional, default=10",
                "round_budget": "integer, optional",
            },
            "example": json.loads(REQUEST_JSON_EXAMPLE),
        },
        "progress_contract": {
            "channel": "stderr",
            "modes": {
                "text": "Human-readable business trace on stderr.",
                "jsonl": "Stable JSONL progress events on stderr.",
                "off": "Disable progress output.",
            },
            "event_fields": ["type", "message", "timestamp", "round_index", "payload"],
        },
        "result_pointer": "final_result.final_candidate_cards",
        "error_examples": [
            {
                "scenario": "unknown command",
                "stderr": "unknown_command: rn",
                "next_step": "Try: seektalent --help",
            },
            {
                "scenario": "missing input",
                "stderr": "missing_input: run requires --request-file, --request-stdin, or --jd-file.",
                "next_step": "Try: seektalent run --request-file ./request.json",
            },
        ],
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
                "stderr_json_fields": ["type", "message", "timestamp", "round_index", "payload"],
                "result_pointer": "final_result.final_candidate_cards",
                "current_behavior": "Runs the full runtime loop, writes run artifacts, and returns SearchRunBundle.",
            },
            "doctor": {"stdout_success_fields": ["ok", "checks"]},
        },
        "failure_contract": {"stderr_json_fields": ["error", "error_type", "code", "hint"]},
    }


def _print_doctor_human(checks: list[DoctorCheck]) -> None:
    for check in checks:
        status = "OK" if check.ok else "FAIL"
        print(f"{status} {check.name}: {check.message}")


def _parse_request_text(raw_text: str) -> RunRequestPayload:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise GuidedCliError(
            "invalid_request_json",
            f"request JSON could not be parsed: {exc.msg}",
            f"Minimal example:\n{REQUEST_JSON_EXAMPLE}",
        ) from exc
    try:
        request = RunRequestPayload.model_validate(payload)
    except ValidationError as exc:
        raise GuidedCliError(
            "invalid_request_payload",
            f"request payload is invalid: {exc.errors(include_url=False)}",
            f"Minimal example:\n{REQUEST_JSON_EXAMPLE}",
        ) from exc
    if not request.job_description.strip():
        raise GuidedCliError(
            "invalid_request_payload",
            "job_description must not be empty.",
            f"Minimal example:\n{REQUEST_JSON_EXAMPLE}",
        )
    return request


def _read_input_file(path_str: str, *, code: str, label: str, hint: str) -> str:
    try:
        return Path(path_str).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise GuidedCliError(code, f"{label} not found: {path_str}", hint) from exc


def _load_run_request(args: argparse.Namespace) -> RunRequestPayload:
    request_file = args.request_file
    request_stdin = bool(args.request_stdin)
    jd_file = args.jd_file
    notes_file = args.notes_file
    request_source_count = sum(bool(value) for value in (request_file, request_stdin, jd_file))
    if request_source_count == 0:
        raise GuidedCliError(
            "missing_input",
            "run requires --request-file, --request-stdin, or --jd-file.",
            "Try: seektalent run --request-file ./request.json",
        )
    if request_source_count > 1:
        raise GuidedCliError(
            "conflicting_inputs",
            "Choose exactly one input source: --request-file, --request-stdin, or --jd-file.",
            "Try: seektalent run --request-file ./request.json",
        )
    if (request_file or request_stdin) and notes_file:
        raise GuidedCliError(
            "conflicting_inputs",
            "--notes-file can only be used together with --jd-file.",
            "Try: seektalent run --jd-file ./jd.md --notes-file ./notes.md",
        )
    if request_file:
        request = _parse_request_text(
            _read_input_file(
                request_file,
                code="missing_request_file",
                label="request file",
                hint="Try: seektalent run --request-file ./request.json",
            )
        )
    elif request_stdin:
        raw_text = sys.stdin.read()
        if not raw_text.strip():
            raise GuidedCliError(
                "missing_request_stdin",
                "--request-stdin was set but stdin was empty.",
                f"Minimal example:\n{REQUEST_JSON_EXAMPLE}",
            )
        request = _parse_request_text(raw_text)
    else:
        request = RunRequestPayload(
            job_description=_read_input_file(
                jd_file,
                code="missing_jd_file",
                label="JD file",
                hint="Try: seektalent run --jd-file ./jd.md --notes-file ./notes.md",
            ),
            hiring_notes=(
                _read_input_file(
                    notes_file,
                    code="missing_notes_file",
                    label="notes file",
                    hint="Try: seektalent run --jd-file ./jd.md --notes-file ./notes.md",
                )
                if notes_file
                else ""
            ),
        )
    if args.round_budget is not None:
        request = request.model_copy(update={"round_budget": args.round_budget})
    return request


def _resolve_progress_mode(requested: str | None) -> str:
    if requested is not None:
        return requested
    return "text" if _is_interactive_terminal() and sys.stderr.isatty() else "off"


def _progress_reporter(mode: str):
    if mode == "off":
        return None
    if mode == "jsonl":
        return lambda event: _emit_json(sys.stderr, event.to_dict())
    return lambda event: print(f"[{event.timestamp}] {event.message}", file=sys.stderr)


def _print_human_run_summary(result: Any) -> None:
    def _card_value(card: Any, key: str) -> str:
        if isinstance(card, dict):
            return str(card.get(key, ""))
        return str(getattr(card, key, ""))

    print(f"run_dir: {result.run_dir}")
    print(f"stop_reason: {result.final_result.stop_reason}")
    print("top_candidates:")
    for card in result.final_result.final_candidate_cards:
        print(f"- {_card_value(card, 'candidate_id')}\t{_card_value(card, 'review_recommendation')}")
    print(f"reviewer_summary: {result.final_result.reviewer_summary}")
    print(f"run_summary: {result.final_result.run_summary}")


def _handle_run(args: argparse.Namespace) -> int:
    try:
        settings = _build_settings(args)
        request = _load_run_request(args)
        progress_mode = _resolve_progress_mode(args.progress)
        result = run_match(
            job_description=request.job_description,
            hiring_notes=request.hiring_notes,
            top_k=request.top_k,
            round_budget=request.round_budget,
            settings=settings,
            env_file=args.env_file,
            progress_callback=_progress_reporter(progress_mode),
        )
    except Exception as exc:  # noqa: BLE001
        _emit_error(exc, json_output=args.json)
        return 1
    if args.json:
        _emit_json(sys.stdout, result.model_dump(mode="json"))
        return 0
    _print_human_run_summary(result)
    return 0


def _handle_doctor(args: argparse.Namespace) -> int:
    try:
        settings = _build_settings(args)
        checks = _doctor_checks(settings, env_file=args.env_file)
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
        print(f"error: {env_path} already exists. Use --force to overwrite it.", file=sys.stderr)
        return 1
    try:
        template_text = read_env_template()
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    env_path.write_text(template_text, encoding="utf-8")
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
    payload = _inspect_payload(env_file=args.env_file)
    if args.json:
        _emit_json(sys.stdout, payload)
        return 0
    print("SeekTalent v0.3.3 CLI inspection summary")
    print("Human entry: `seektalent`")
    print("Interactive flow: paste JD, press Shift+Enter, then optionally add notes.")
    print("Agent entry: `seektalent run --request-file ./request.json --json --progress jsonl`")
    print("Use `seektalent inspect --json` for the machine-readable contract.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = SeekTalentArgumentParser(
        prog="seektalent",
        epilog=ROOT_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", parser_class=SeekTalentArgumentParser)

    run_parser = subparsers.add_parser(
        "run",
        help="Run the non-interactive search protocol.",
        epilog=RUN_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    run_parser.add_argument(
        "--request-file",
        help="Read a full JSON request object from a file. Preferred for long inputs.",
    )
    run_parser.add_argument(
        "--request-stdin",
        action="store_true",
        help="Read a full JSON request object from stdin.",
    )
    run_parser.add_argument(
        "--jd-file",
        help="Read the job description from a UTF-8 text file.",
    )
    run_parser.add_argument(
        "--notes-file",
        help="Read optional hiring notes from a UTF-8 text file. Only valid with --jd-file.",
    )
    run_parser.add_argument(
        "--round-budget",
        type=int,
        help="Override the search round budget for this run.",
    )
    run_parser.add_argument(
        "--progress",
        choices=("text", "jsonl", "off"),
        help="Progress stream format for stderr. Default is text in a TTY, otherwise off.",
    )
    run_parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to the environment file used to load runtime and LLM configuration.",
    )
    run_parser.add_argument(
        "--json",
        action="store_true",
        help="Write the full SearchRunBundle to stdout as JSON.",
    )
    run_parser.set_defaults(handler=_handle_run)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Optional diagnostic for the local runtime surface.",
    )
    doctor_parser.add_argument("--env-file", default=".env")
    doctor_parser.add_argument("--output-dir")
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.set_defaults(handler=_handle_doctor)

    init_parser = subparsers.add_parser("init", help="Write the repo env template.")
    init_parser.add_argument("--env-file", default=".env")
    init_parser.add_argument("--force", action="store_true")
    init_parser.set_defaults(handler=_handle_init)

    version_parser = subparsers.add_parser("version", help="Print the installed version.")
    version_parser.set_defaults(handler=lambda args: _handle_version())

    update_parser = subparsers.add_parser("update", help="Print upgrade instructions.")
    update_parser.set_defaults(handler=lambda args: _handle_update())

    inspect_parser = subparsers.add_parser("inspect", help="Describe the current CLI contract.")
    inspect_parser.add_argument("--env-file", default=".env")
    inspect_parser.add_argument("--json", action="store_true")
    inspect_parser.set_defaults(handler=_handle_inspect)
    return parser


def _suggest_command(command: str) -> str | None:
    matches = difflib.get_close_matches(command, KNOWN_COMMANDS, n=1)
    return matches[0] if matches else None


def _is_interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _emit_unknown_command(command: str) -> int:
    suggestion = _suggest_command(command)
    print(f"unknown_command: {command}", file=sys.stderr)
    if suggestion:
        print(f"Did you mean: {suggestion}?", file=sys.stderr)
    print("Try: seektalent --help", file=sys.stderr)
    print(
        "Agent example: seektalent run --request-file ./request.json --json --progress jsonl",
        file=sys.stderr,
    )
    return 2


def _emit_removed_inline_flag_error(flag: str) -> int:
    print(f"removed_flag: {flag} is no longer supported.", file=sys.stderr)
    print("Use: seektalent run --request-file ./request.json", file=sys.stderr)
    print("Or:  seektalent run --jd-file ./jd.md --notes-file ./notes.md", file=sys.stderr)
    return 2


def _launch_tui() -> int:
    from seektalent.tui import SeekTalentApp

    app = SeekTalentApp()
    app.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    args_list = sys.argv[1:] if argv is None else argv
    if any(flag in args_list for flag in REMOVED_INLINE_FLAGS):
        offending_flag = next(flag for flag in REMOVED_INLINE_FLAGS if flag in args_list)
        return _emit_removed_inline_flag_error(offending_flag)
    if not args_list:
        if _is_interactive_terminal():
            return _launch_tui()
        parser = build_parser()
        parser.print_help()
        return 0
    first = args_list[0]
    if not first.startswith("-") and first not in KNOWN_COMMANDS:
        return _emit_unknown_command(first)
    parser = build_parser()
    try:
        parsed = parser.parse_args(args_list)
    except GuidedCliError as exc:
        if first == "run" and exc.code == "invalid_arguments":
            exc = GuidedCliError(
                "invalid_run_arguments",
                str(exc),
                "Try: seektalent run --help\nAgent example: seektalent run --request-file ./request.json --json --progress jsonl",
            )
        _emit_error(exc, json_output=False)
        return 2
    handler = getattr(parsed, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(parsed)


__all__ = ["build_parser", "main"]
