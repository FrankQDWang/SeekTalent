from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


STAGE_EVENTS = {
    "requirements_completed": "requirements",
    "controller_completed": "controller",
    "reflection_completed": "reflection",
    "finalizer_completed": "finalizer",
    "score_branch_completed": "scoring",
    "tool_succeeded": "cts_tool",
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _stage_bucket() -> dict[str, int]:
    return {"count": 0, "total_ms": 0, "max_ms": 0}


def _add_latency(bucket: dict[str, int], latency_ms: object) -> None:
    if not isinstance(latency_ms, int | float):
        return
    value = int(latency_ms)
    bucket["count"] += 1
    bucket["total_ms"] += value
    bucket["max_ms"] = max(bucket["max_ms"], value)


def _call_snapshot_paths(run_dir: Path) -> list[Path]:
    paths = [
        run_dir / "requirements_call.json",
        run_dir / "finalizer_call.json",
    ]
    rounds_dir = run_dir / "rounds"
    if rounds_dir.exists():
        for round_dir in sorted(rounds_dir.glob("round_*")):
            paths.extend(
                [
                    round_dir / "controller_call.json",
                    round_dir / "reflection_call.json",
                ]
            )
    return [path for path in paths if path.exists()]


def _scoring_snapshot_paths(run_dir: Path) -> list[Path]:
    rounds_dir = run_dir / "rounds"
    if not rounds_dir.exists():
        return []
    return sorted(
        [
            *rounds_dir.glob("round_*/scoring_calls.jsonl"),
            *rounds_dir.glob("round_*/scorecards.jsonl"),
        ]
    )


def _empty_llm_bucket() -> dict[str, Any]:
    return {
        "count": 0,
        "total_latency_ms": 0,
        "max_latency_ms": 0,
        "retry_count": 0,
        "retry_reasons": [],
        "max_input_payload_chars": 0,
        "max_output_chars": 0,
    }


def _add_call(bucket: dict[str, Any], snapshot: dict[str, Any]) -> None:
    bucket["count"] += 1
    latency = int(snapshot.get("latency_ms") or 0)
    bucket["total_latency_ms"] += latency
    bucket["max_latency_ms"] = max(bucket["max_latency_ms"], latency)
    bucket["retry_count"] += int(snapshot.get("validator_retry_count") or 0)
    for reason in snapshot.get("validator_retry_reasons") or []:
        if reason not in bucket["retry_reasons"]:
            bucket["retry_reasons"].append(reason)
    bucket["max_input_payload_chars"] = max(
        bucket["max_input_payload_chars"],
        int(snapshot.get("input_payload_chars") or 0),
    )
    bucket["max_output_chars"] = max(
        bucket["max_output_chars"],
        int(snapshot.get("output_chars") or 0),
    )


def _has_call_metadata(snapshot: dict[str, Any]) -> bool:
    return any(
        key in snapshot
        for key in ("call_id", "latency_ms", "validator_retry_count", "input_payload_chars", "output_chars")
    )


def audit_run_dir(run_dir: Path) -> dict[str, Any]:
    events = _read_jsonl(run_dir / "events.jsonl")
    stages: dict[str, dict[str, int]] = defaultdict(_stage_bucket)
    rounds: set[int] = set()
    stop_reason: str | None = None
    timestamps = []

    for event in events:
        timestamp = _parse_timestamp(event.get("timestamp"))
        if timestamp is not None:
            timestamps.append(timestamp)
        if isinstance(event.get("round_no"), int):
            rounds.add(int(event["round_no"]))
        if event.get("stop_reason"):
            stop_reason = str(event["stop_reason"])
        stage = STAGE_EVENTS.get(str(event.get("event_type")))
        if stage is not None:
            _add_latency(stages[stage], event.get("latency_ms"))

    llm_calls: dict[str, dict[str, Any]] = defaultdict(_empty_llm_bucket)
    for path in _call_snapshot_paths(run_dir):
        snapshot = _read_json(path)
        stage = str(snapshot.get("stage") or "unknown")
        _add_call(llm_calls[stage], snapshot)
    for path in _scoring_snapshot_paths(run_dir):
        for snapshot in _read_jsonl(path):
            if path.name == "scorecards.jsonl" and not _has_call_metadata(snapshot):
                continue
            _add_call(llm_calls["scoring"], snapshot)

    top_stage = None
    if stages:
        top_stage = max(stages.items(), key=lambda item: item[1]["total_ms"])[0]

    observed_wall_clock_ms = None
    if len(timestamps) >= 2:
        observed_wall_clock_ms = int((max(timestamps) - min(timestamps)).total_seconds() * 1000)

    return {
        "run_dir": str(run_dir),
        "observed_wall_clock_ms": observed_wall_clock_ms,
        "stop_reason": stop_reason,
        "rounds_observed": sorted(rounds),
        "stages": dict(sorted(stages.items())),
        "top_stage_by_recorded_latency": top_stage,
        "llm_calls": dict(sorted(llm_calls.items())),
    }


def _discover_run_dirs(paths: list[Path]) -> list[Path]:
    run_dirs: set[Path] = set()
    for path in paths:
        if (path / "events.jsonl").exists():
            run_dirs.add(path)
            continue
        run_dirs.update(events_path.parent for events_path in path.rglob("events.jsonl"))
    return sorted(run_dirs)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize SeekTalent run latency from artifact files.")
    parser.add_argument("paths", nargs="*", default=["runs"], help="Run directories or roots containing run directories.")
    parser.add_argument("--limit", type=int, default=20, help="Only include the newest N discovered runs.")
    args = parser.parse_args()

    run_dirs = _discover_run_dirs([Path(path) for path in args.paths])
    selected = sorted(run_dirs, key=lambda path: path.stat().st_mtime)[-args.limit :]
    payload = {
        "run_count": len(selected),
        "runs": [audit_run_dir(path) for path in selected],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
