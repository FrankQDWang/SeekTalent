import json
from pathlib import Path

from tools.audit_run_latency import _discover_run_dirs, audit_run_dir


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_audit_run_dir_groups_stage_latency(tmp_path: Path) -> None:
    run_dir = tmp_path / "20260423_120000_abc12345"
    _write_jsonl(
        run_dir / "events.jsonl",
        [
            {"timestamp": "2026-04-23T12:00:00+08:00", "event_type": "run_started"},
            {
                "timestamp": "2026-04-23T12:00:10+08:00",
                "event_type": "requirements_completed",
                "latency_ms": 10_000,
            },
            {
                "timestamp": "2026-04-23T12:01:00+08:00",
                "event_type": "controller_completed",
                "round_no": 1,
                "latency_ms": 50_000,
            },
            {
                "timestamp": "2026-04-23T12:01:02+08:00",
                "event_type": "tool_succeeded",
                "round_no": 1,
                "latency_ms": 2_000,
            },
            {
                "timestamp": "2026-04-23T12:01:12+08:00",
                "event_type": "score_branch_completed",
                "round_no": 1,
                "latency_ms": 10_000,
            },
            {
                "timestamp": "2026-04-23T12:02:00+08:00",
                "event_type": "reflection_completed",
                "round_no": 1,
                "latency_ms": 48_000,
            },
            {
                "timestamp": "2026-04-23T12:02:30+08:00",
                "event_type": "finalizer_completed",
                "latency_ms": 30_000,
            },
            {
                "timestamp": "2026-04-23T12:02:31+08:00",
                "event_type": "run_finished",
                "stop_reason": "controller_stop",
            },
        ],
    )

    summary = audit_run_dir(run_dir)

    assert summary["run_dir"] == str(run_dir)
    assert summary["observed_wall_clock_ms"] == 151_000
    assert summary["stop_reason"] == "controller_stop"
    assert summary["rounds_observed"] == [1]
    assert summary["stages"]["requirements"]["total_ms"] == 10_000
    assert summary["stages"]["controller"]["total_ms"] == 50_000
    assert summary["stages"]["cts_tool"]["total_ms"] == 2_000
    assert summary["stages"]["scoring"]["total_ms"] == 10_000
    assert summary["stages"]["reflection"]["total_ms"] == 48_000
    assert summary["stages"]["finalizer"]["total_ms"] == 30_000
    assert summary["top_stage_by_recorded_latency"] == "controller"


def test_audit_run_dir_reads_retry_counts_from_call_snapshots(tmp_path: Path) -> None:
    run_dir = tmp_path / "20260423_120000_def67890"
    _write_jsonl(
        run_dir / "events.jsonl",
        [
            {"timestamp": "2026-04-23T12:00:00+08:00", "event_type": "run_started"},
            {"timestamp": "2026-04-23T12:01:00+08:00", "event_type": "run_finished"},
        ],
    )
    _write_json(
        run_dir / "rounds" / "round_04" / "controller_call.json",
        {
            "stage": "controller",
            "call_id": "controller-r04",
            "round_no": 4,
            "latency_ms": 154_898,
            "validator_retry_count": 1,
            "validator_retry_reasons": ["response_to_reflection is required when previous_reflection exists."],
            "prompt_chars": 4097,
            "input_payload_chars": 5497,
            "output_chars": 742,
        },
    )
    _write_json(
        run_dir / "finalizer_call.json",
        {
            "stage": "finalize",
            "call_id": "finalizer",
            "latency_ms": 38_359,
            "validator_retry_count": 0,
            "validator_retry_reasons": [],
            "prompt_chars": 1201,
            "input_payload_chars": 2688,
            "output_chars": 2360,
        },
    )

    summary = audit_run_dir(run_dir)

    assert summary["llm_calls"]["controller"]["retry_count"] == 1
    assert summary["llm_calls"]["controller"]["retry_reasons"] == [
        "response_to_reflection is required when previous_reflection exists."
    ]
    assert summary["llm_calls"]["controller"]["max_latency_ms"] == 154_898
    assert summary["llm_calls"]["finalize"]["retry_count"] == 0


def test_audit_run_dir_reads_scoring_jsonl_snapshots(tmp_path: Path) -> None:
    run_dir = tmp_path / "20260423_120000_feedbeef"
    _write_jsonl(
        run_dir / "events.jsonl",
        [
            {"timestamp": "2026-04-23T12:00:00+08:00", "event_type": "run_started"},
            {"timestamp": "2026-04-23T12:01:00+08:00", "event_type": "run_finished"},
        ],
    )
    _write_jsonl(
        run_dir / "rounds" / "round_01" / "scoring_calls.jsonl",
        [
            {
                "stage": "scoring",
                "call_id": "scoring-r01-a",
                "latency_ms": 5_000,
                "validator_retry_count": 0,
                "input_payload_chars": 100,
                "output_chars": 200,
            },
            {
                "stage": "scoring",
                "call_id": "scoring-r01-b",
                "latency_ms": 8_000,
                "validator_retry_count": 1,
                "validator_retry_reasons": ["score validator retry"],
                "input_payload_chars": 300,
                "output_chars": 400,
            },
        ],
    )

    summary = audit_run_dir(run_dir)

    assert summary["llm_calls"]["scoring"]["count"] == 2
    assert summary["llm_calls"]["scoring"]["total_latency_ms"] == 13_000
    assert summary["llm_calls"]["scoring"]["max_latency_ms"] == 8_000
    assert summary["llm_calls"]["scoring"]["retry_count"] == 1
    assert summary["llm_calls"]["scoring"]["retry_reasons"] == ["score validator retry"]


def test_audit_run_dir_reads_legacy_scorecards_jsonl_snapshots(tmp_path: Path) -> None:
    run_dir = tmp_path / "20260423_120000_legacy"
    _write_jsonl(
        run_dir / "events.jsonl",
        [
            {"timestamp": "2026-04-23T12:00:00+08:00", "event_type": "run_started"},
            {"timestamp": "2026-04-23T12:01:00+08:00", "event_type": "run_finished"},
        ],
    )
    _write_jsonl(
        run_dir / "rounds" / "round_01" / "scorecards.jsonl",
        [
            {
                "stage": "scoring",
                "resume_id": "resume-1",
                "latency_ms": 7_000,
                "validator_retry_count": 0,
            }
        ],
    )

    summary = audit_run_dir(run_dir)

    assert summary["llm_calls"]["scoring"]["count"] == 1
    assert summary["llm_calls"]["scoring"]["total_latency_ms"] == 7_000


def test_audit_run_dir_ignores_current_scorecards_without_call_metadata(tmp_path: Path) -> None:
    run_dir = tmp_path / "20260423_120000_current"
    _write_jsonl(
        run_dir / "events.jsonl",
        [
            {"timestamp": "2026-04-23T12:00:00+08:00", "event_type": "run_started"},
            {"timestamp": "2026-04-23T12:01:00+08:00", "event_type": "run_finished"},
        ],
    )
    _write_jsonl(
        run_dir / "rounds" / "round_01" / "scorecards.jsonl",
        [
            {
                "resume_id": "resume-1",
                "fit_bucket": "fit",
                "overall_score": 88,
                "reasoning_summary": "Current scorecard, not an LLM call snapshot.",
            }
        ],
    )

    summary = audit_run_dir(run_dir)

    assert "scoring" not in summary["llm_calls"]


def test_discover_run_dirs_finds_nested_run_roots(tmp_path: Path) -> None:
    direct = tmp_path / "20260423_120000_direct"
    nested = tmp_path / "phase_2" / "20260423_120100_nested"
    ignored_parent = tmp_path / "phase_2"
    _write_jsonl(direct / "events.jsonl", [])
    _write_jsonl(nested / "events.jsonl", [])

    discovered = _discover_run_dirs([tmp_path])

    assert discovered == [direct, nested]
    assert ignored_parent not in discovered
