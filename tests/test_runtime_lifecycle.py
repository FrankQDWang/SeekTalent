from __future__ import annotations

from datetime import datetime
from pathlib import Path

from seektalent.runtime.exact_llm_cache import get_cached_json, put_cached_json
from seektalent.runtime.lifecycle import cleanup_runtime_artifacts
from tests.settings_factory import make_settings


def _write_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")


def test_dev_cleanup_keeps_runs_and_clears_cache(tmp_path: Path) -> None:
    settings = make_settings(
        runtime_mode="dev",
        runs_dir=str(tmp_path / "runs"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    run_dir = tmp_path / "runs" / "20260401_120000_deadbeef"
    _write_file(run_dir / "trace.log")
    put_cached_json(settings, namespace="scoring", key="k", payload={"value": 1})

    cleanup_runtime_artifacts(settings, now=datetime(2026, 4, 23, 12, 0, 0))

    assert run_dir.exists()
    assert get_cached_json(settings, namespace="scoring", key="k") is None


def test_prod_cleanup_deletes_old_runs_and_keeps_recent_runs(tmp_path: Path) -> None:
    settings = make_settings(
        runtime_mode="prod",
        runs_dir=str(tmp_path / "runs"),
        artifacts_dir=str(tmp_path / "artifacts"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    old_run = tmp_path / "artifacts" / "runs" / "2026" / "04" / "10" / "run_20260410OLD"
    recent_run = tmp_path / "artifacts" / "runs" / "2026" / "04" / "22" / "run_20260422NEW"
    old_benchmark = tmp_path / "artifacts" / "benchmark-executions" / "2026" / "04" / "10" / "benchmark_20260410OLD"
    recent_benchmark = tmp_path / "artifacts" / "benchmark-executions" / "2026" / "04" / "22" / "benchmark_20260422NEW"
    maintained_benchmarks_dir = tmp_path / "artifacts" / "benchmarks"
    _write_file(old_run / "trace.log")
    _write_file(recent_run / "trace.log")
    _write_file(old_benchmark / "output" / "summary.json")
    _write_file(recent_benchmark / "output" / "summary.json")
    _write_file(maintained_benchmarks_dir / "agent_jds.jsonl")

    cleanup_runtime_artifacts(settings, now=datetime(2026, 4, 23, 12, 0, 0))

    assert not old_run.exists()
    assert recent_run.exists()
    assert not old_benchmark.exists()
    assert recent_benchmark.exists()
    assert (maintained_benchmarks_dir / "agent_jds.jsonl").exists()


def test_prod_cleanup_clears_cache(tmp_path: Path) -> None:
    settings = make_settings(
        runtime_mode="prod",
        runs_dir=str(tmp_path / "runs"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    put_cached_json(settings, namespace="requirements", key="k", payload={"value": 1})

    cleanup_runtime_artifacts(settings, now=datetime(2026, 4, 23, 12, 0, 0))

    assert get_cached_json(settings, namespace="requirements", key="k") is None


def test_prod_cleanup_leaves_decommissioned_legacy_runs_root(tmp_path: Path) -> None:
    settings = make_settings(
        runtime_mode="prod",
        runs_dir=str(tmp_path / "runs"),
        artifacts_dir=str(tmp_path / "artifacts"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    legacy_runs = tmp_path / "runs"
    _write_file(legacy_runs / ".decommissioned")
    _write_file(legacy_runs / "README.md")

    cleanup_runtime_artifacts(settings, now=datetime(2026, 4, 23, 12, 0, 0))

    assert (legacy_runs / ".decommissioned").exists()
    assert (legacy_runs / "README.md").exists()
