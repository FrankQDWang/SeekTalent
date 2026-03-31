from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cv_match.config import AppSettings
from cv_match.runner import MatchRunner


def _read_text(*, inline_value: str | None, file_value: str | None, label: str) -> str:
    if file_value:
        return Path(file_value).read_text(encoding="utf-8")
    if inline_value:
        return inline_value
    raise ValueError(f"{label} is required via --{label} or --{label}-file.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic local resume matching CLI.")
    parser.add_argument("--jd")
    parser.add_argument("--notes")
    parser.add_argument("--jd-file")
    parser.add_argument("--notes-file")
    parser.add_argument("--mock-cts", dest="mock_cts", action="store_true", default=None)
    parser.add_argument("--real-cts", dest="mock_cts", action="store_false")
    parser.add_argument("--max-rounds", type=int)
    parser.add_argument("--min-rounds", type=int)
    parser.add_argument("--scoring-max-concurrency", type=int)
    parser.add_argument("--enable-reflection", dest="enable_reflection", action="store_true", default=None)
    parser.add_argument("--disable-reflection", dest="enable_reflection", action="store_false")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        jd = _read_text(inline_value=args.jd, file_value=args.jd_file, label="jd")
        notes = _read_text(inline_value=args.notes, file_value=args.notes_file, label="notes")
        settings = AppSettings().with_overrides(
            mock_cts=args.mock_cts,
            max_rounds=args.max_rounds,
            min_rounds=args.min_rounds,
            scoring_max_concurrency=args.scoring_max_concurrency,
            enable_reflection=args.enable_reflection,
        )
        runner = MatchRunner(settings)
        artifacts = runner.run(jd=jd, notes=notes)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(artifacts.final_markdown)
    print(f"run_id: {artifacts.run_id}")
    print(f"run_directory: {artifacts.run_dir}")
    print(f"trace_log: {artifacts.trace_log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
