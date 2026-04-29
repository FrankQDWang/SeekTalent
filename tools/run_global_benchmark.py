from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from seektalent.config import AppSettings


PolicyComparisonMode = Literal["none", "primary"]


def build_policy_comparison_overrides(*, mode: PolicyComparisonMode) -> dict[str, object]:
    if mode == "none":
        return {}
    if mode == "primary":
        return {}
    raise ValueError(f"Unsupported policy comparison mode: {mode}")


def build_policy_comparison_settings(
    *,
    base_settings: AppSettings,
    mode: PolicyComparisonMode,
) -> AppSettings:
    return base_settings.with_overrides(**build_policy_comparison_overrides(mode=mode))


def build_policy_comparison_env(*, mode: PolicyComparisonMode) -> dict[str, str]:
    overrides = build_policy_comparison_overrides(mode=mode)
    return {
        f"SEEKTALENT_{key.upper()}": str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in overrides.items()
    }


def effective_policy_comparison_mode(*, mode: PolicyComparisonMode) -> PolicyComparisonMode:
    return mode if build_policy_comparison_overrides(mode=mode) else "none"


def _load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _tail(text: str, limit: int = 40) -> list[str]:
    return text.splitlines()[-limit:]


def _latest_trace_log(output_root: Path) -> Path | None:
    candidates = sorted(output_root.glob("*/trace.log"), key=lambda path: path.stat().st_mtime)
    return candidates[-1] if candidates else None


def _run_command(
    *,
    seektalent_bin: Path,
    jd_id: str,
    job_title: str,
    jd: str,
    notes: str,
    env_file: Path,
    output_root: Path,
    idle_timeout_seconds: int,
    policy_comparison_mode: PolicyComparisonMode,
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    jd_path = output_root / "jd.md"
    notes_path = output_root / "notes.md"
    jd_path.write_text(jd, encoding="utf-8")
    notes_path.write_text(notes, encoding="utf-8")
    command = [
        str(seektalent_bin),
        "run",
        "--jd-file",
        str(jd_path),
        "--env-file",
        str(env_file),
        "--output-dir",
        str(output_root),
        "--json",
        "--enable-eval",
    ]
    if notes.strip():
        command.extend(["--notes-file", str(notes_path)])

    started_at = datetime.now().astimezone()
    child_env = os.environ.copy()
    child_env.update(build_policy_comparison_env(mode=policy_comparison_mode))
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=child_env,
    )
    trace_path: Path | None = None
    last_activity = time.time()
    timed_out = False
    while proc.poll() is None:
        current_trace = _latest_trace_log(output_root)
        if current_trace is not None:
            trace_path = current_trace
            mtime = current_trace.stat().st_mtime
            if mtime > last_activity:
                last_activity = mtime
        if time.time() - last_activity > idle_timeout_seconds:
            timed_out = True
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            break
        time.sleep(5)

    stdout, stderr = proc.communicate()
    ended_at = datetime.now().astimezone()
    payload: dict[str, Any] = {
        "jd_id": jd_id,
        "job_title": job_title,
        "command": command,
        "started_at": started_at.isoformat(timespec="seconds"),
        "ended_at": ended_at.isoformat(timespec="seconds"),
        "returncode": proc.returncode,
        "timed_out": timed_out,
        "run_id": None,
        "run_dir": None,
        "evaluation_result": None,
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
    }
    if trace_path is not None:
        payload["trace_log_path"] = str(trace_path)
        payload["run_dir"] = str(trace_path.parent)
        payload["run_id"] = trace_path.parent.name.rsplit("_", 1)[-1]
    if proc.returncode == 0:
        result = json.loads(stdout)
        payload["run_id"] = result.get("run_id")
        payload["run_dir"] = result.get("run_dir")
        payload["trace_log_path"] = result.get("trace_log_path")
        payload["evaluation_result"] = result.get("evaluation_result")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the global seektalent benchmark sequentially.")
    parser.add_argument(
        "--jds-file",
        default="artifacts/benchmarks/agent_jds.jsonl",
        help="Benchmark JSONL file.",
    )
    parser.add_argument("--env-file", default=".env", help="Env file passed to global seektalent.")
    parser.add_argument(
        "--seektalent-bin",
        default=str(Path.home() / ".local" / "bin" / "seektalent"),
        help="Path to the global seektalent executable.",
    )
    parser.add_argument("--start-from", default="agent_jd_003", help="Resume from this jd_id.")
    parser.add_argument(
        "--output-root",
        default=None,
        help="Directory for this benchmark batch. Defaults under ./runs.",
    )
    parser.add_argument(
        "--idle-timeout-seconds",
        type=int,
        default=600,
        help="Terminate a run when trace.log stops growing for this long.",
    )
    parser.add_argument(
        "--policy-comparison-mode",
        choices=("none", "primary"),
        default="none",
        help="Optional experiment mode that adjusts runtime settings for policy comparison runs.",
    )
    args = parser.parse_args()

    project_root = Path.cwd()
    rows = _load_rows((project_root / args.jds_file).resolve())
    started = False
    selected_rows: list[dict[str, Any]] = []
    for row in rows:
        if not started and row.get("jd_id") == args.start_from:
            started = True
        if started:
            selected_rows.append(row)
    if not selected_rows:
        raise SystemExit(f"Could not find start jd_id={args.start_from!r} in {args.jds_file}")

    batch_root = (
        Path(args.output_root).resolve()
        if args.output_root
        else (project_root / "runs" / f"global_benchmark_resume_{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')}").resolve()
    )
    batch_root.mkdir(parents=True, exist_ok=True)
    completed: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    for row in selected_rows:
        jd_id = str(row["jd_id"])
        entry = _run_command(
            seektalent_bin=Path(args.seektalent_bin).expanduser().resolve(),
            jd_id=jd_id,
            job_title=str(row["job_title"]),
            jd=str(row["job_description"]),
            notes=str(row.get("hiring_notes", "") or ""),
            env_file=(project_root / args.env_file).resolve(),
            output_root=batch_root / jd_id,
            idle_timeout_seconds=args.idle_timeout_seconds,
            policy_comparison_mode=args.policy_comparison_mode,
        )
        attempts.append(entry)
        if entry["returncode"] == 0 and not entry["timed_out"] and entry["evaluation_result"] is not None:
            completed.append(entry)

    summary = {
        "benchmark_file": str((project_root / args.jds_file).resolve()),
        "env_file": str((project_root / args.env_file).resolve()),
        "start_from": args.start_from,
        "idle_timeout_seconds": args.idle_timeout_seconds,
        "policy_comparison_mode": effective_policy_comparison_mode(mode=args.policy_comparison_mode),
        "attempt_count": len(attempts),
        "completed_count": len(completed),
        "attempts": attempts,
        "completed_runs": completed,
    }
    summary_path = batch_root / "global_benchmark_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
