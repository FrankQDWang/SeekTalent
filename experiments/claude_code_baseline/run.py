from __future__ import annotations

import argparse
import asyncio
import json
import sys

from experiments.claude_code_baseline import CLAUDE_CODE_MAX_ROUNDS
from experiments.claude_code_baseline.harness import run_claude_code_baseline
from seektalent.config import AppSettings, load_process_env
from seektalent.resources import resolve_user_path
from seektalent.text_inputs import read_optional_inline_or_file_text, read_required_inline_or_file_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m experiments.claude_code_baseline.run",
        description="Run the Claude Code baseline against the same CTS corpus and judge setup.",
    )
    parser.add_argument("--job-title", help="Inline job title text.")
    parser.add_argument("--job-title-file", help="Path to a job title file.")
    parser.add_argument("--jd", help="Inline job description text.")
    parser.add_argument("--jd-file", help="Path to a job description file.")
    parser.add_argument("--notes", help="Optional inline sourcing notes text.")
    parser.add_argument("--notes-file", help="Path to an optional sourcing notes file.")
    parser.add_argument("--env-file", default=".env", help="Path to the env file for this run.")
    parser.add_argument("--output-dir", default="runs/claude_code", help="Directory where run artifacts should be written.")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Emit a single JSON object.")
    parser.add_argument("--timeout-seconds", type=int, default=900, help="Claude Code subprocess timeout.")
    return parser


def _result_payload(result) -> dict[str, object]:  # noqa: ANN001
    return {
        "run_id": result.run_id,
        "run_dir": str(result.run_dir),
        "trace_log_path": str(result.trace_log_path),
        "rounds_executed": result.rounds_executed,
        "stop_reason": result.stop_reason,
        "round_01_candidates": result.round_01_candidates,
        "final_candidates": result.final_candidates,
        "evaluation_result": result.evaluation_result.model_dump(mode="json"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        job_title = read_required_inline_or_file_text(
            inline_value=args.job_title,
            file_value=args.job_title_file,
            label="job-title",
        )
        jd = read_required_inline_or_file_text(inline_value=args.jd, file_value=args.jd_file, label="jd")
        notes = read_optional_inline_or_file_text(inline_value=args.notes, file_value=args.notes_file, label="notes")
        load_process_env(args.env_file)
        settings = AppSettings(_env_file=args.env_file).with_overrides(
            runs_dir=str(resolve_user_path(args.output_dir)),
            enable_eval=True,
            max_rounds=CLAUDE_CODE_MAX_ROUNDS,
        )
        if not settings.mock_cts:
            settings.require_cts_credentials()
        result = asyncio.run(
            run_claude_code_baseline(
                job_title=job_title,
                jd=jd,
                notes=notes,
                settings=settings,
                env_file=args.env_file,
                timeout_seconds=args.timeout_seconds,
            )
        )
    except Exception as exc:  # noqa: BLE001
        if args.json_output:
            sys.stderr.write(json.dumps({"error": str(exc), "error_type": type(exc).__name__}, ensure_ascii=False) + "\n")
            return 1
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.json_output:
        sys.stdout.write(json.dumps(_result_payload(result), ensure_ascii=False) + "\n")
        return 0
    print(f"run_id: {result.run_id}")
    print(f"run_dir: {result.run_dir}")
    print(f"rounds_executed: {result.rounds_executed}")
    print(f"stop_reason: {result.stop_reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
