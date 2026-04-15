from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "tools" / "refresh_wandb_report.py"
    spec = importlib.util.spec_from_file_location("refresh_wandb_report", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_best_runs_by_version_prefers_highest_final_total_then_latest() -> None:
    module = _load_module()
    rows = module._best_runs_by_version_rows(
        [
            {
                "run_name": "older",
                "run_url": "https://example.com/older",
                "created_at": "2026-04-14T09:00:00Z",
                "state": "finished",
                "eval_enabled": True,
                "seektalent_version": "0.4.1",
                "judge_model": "openai-responses:gpt-5.4",
                "final_total_score": 0.4,
                "final_precision_at_10": 0.2,
                "final_ndcg_at_10": 0.3,
                "round_01_total_score": 0.0,
                "round_01_precision_at_10": 0.0,
                "round_01_ndcg_at_10": 0.0,
            },
            {
                "run_name": "newer",
                "run_url": "https://example.com/newer",
                "created_at": "2026-04-14T10:00:00Z",
                "state": "finished",
                "eval_enabled": True,
                "seektalent_version": "0.4.1",
                "judge_model": "openai-responses:gpt-5.4",
                "final_total_score": 0.4,
                "final_precision_at_10": 0.2,
                "final_ndcg_at_10": 0.3,
                "round_01_total_score": 0.0,
                "round_01_precision_at_10": 0.0,
                "round_01_ndcg_at_10": 0.0,
            },
        ]
    )

    assert rows[0]["run_name"] == "newer"


def test_version_means_summary_markdown_contains_exact_values() -> None:
    module = _load_module()
    markdown = module._version_means_summary_markdown(
        [
            {
                "run_name": "run-1",
                "run_url": "https://example.com/run-1",
                "created_at": "2026-04-14T09:00:00Z",
                "state": "finished",
                "eval_enabled": True,
                "seektalent_version": "0.4.1",
                "judge_model": "openai-responses:gpt-5.4",
                "final_total_score": 0.0,
                "final_precision_at_10": 0.0,
                "final_ndcg_at_10": 0.0,
                "round_01_total_score": 0.0,
                "round_01_precision_at_10": 0.0,
                "round_01_ndcg_at_10": 0.0,
            },
            {
                "run_name": "run-2",
                "run_url": "https://example.com/run-2",
                "created_at": "2026-04-14T10:00:00Z",
                "state": "finished",
                "eval_enabled": True,
                "seektalent_version": "0.4.1",
                "judge_model": "openai-responses:gpt-5.4",
                "final_total_score": 0.4,
                "final_precision_at_10": 0.2,
                "final_ndcg_at_10": 0.3,
                "round_01_total_score": 0.1,
                "round_01_precision_at_10": 0.2,
                "round_01_ndcg_at_10": 0.3,
            },
        ]
    )

    assert "| 0.4.1 | 2 | 0.2000 | 0.1000 | 0.1500 | 0.0500 | 0.1000 | 0.1500 |" in markdown
