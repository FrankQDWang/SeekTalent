from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from seektalent.evaluation import (
    WANDB_REPORT_TITLE,
    _judge_cache_summary,
    _latest_runs_by_version_rows,
    _best_runs_by_version_rows,
    _worst_runs_by_version_rows,
    JudgeCache,
    ResumeJudge,
    ResumeJudgeResult,
    _upsert_wandb_report,
    _version_means_rows,
    _version_means_summary_markdown,
    _version_runs_markdown,
    EvaluatedCandidate,
    EvaluationResult,
    EvaluationStageResult,
    evaluate_run,
    migrate_judge_assets,
    ndcg_at_10,
    precision_at_10,
    snapshot_sha256,
    task_sha256,
)
from seektalent.models import ResumeCandidate
from seektalent.prompting import LoadedPrompt
from seektalent.resources import package_prompt_dir
from tests.settings_factory import make_settings


class FakeExprConfig:
    def __init__(self, name: str) -> None:
        self.name = name

    def __eq__(self, other: object):  # type: ignore[override]
        return (self.name, other)


class FakeExprMetric:
    def __init__(self, name: str) -> None:
        self.name = name

    def __eq__(self, other: object):  # type: ignore[override]
        return (self.name, other)


class FakeExprSummary:
    def __init__(self, name: str) -> None:
        self.name = name

    def __ge__(self, other: object):
        return (self.name, ">=", other)


def test_snapshot_sha256_is_stable_for_key_order() -> None:
    left = {"b": 2, "a": 1}
    right = {"a": 1, "b": 2}

    assert snapshot_sha256(left) == snapshot_sha256(right)


def test_task_sha256_keeps_empty_notes_compatible_with_jd_hash() -> None:
    jd = "Build AI agents."

    assert task_sha256(jd, "") == sha256(jd.encode("utf-8")).hexdigest()
    assert task_sha256(jd, "Prefer LangGraph") != task_sha256(jd, "Prefer RAG")


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    }


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_ndcg_at_10_is_one_for_full_ten_result_perfect_recall() -> None:
    assert ndcg_at_10([3] * 10) == 1.0


def test_ndcg_at_10_penalizes_short_recall_even_when_order_is_perfect() -> None:
    assert ndcg_at_10([3, 2, 2, 1, 0]) == pytest.approx(0.4176267724363269)


def test_ndcg_at_10_returns_zero_for_empty_recall() -> None:
    assert ndcg_at_10([]) == 0.0


def test_precision_at_10_counts_scores_two_and_above() -> None:
    assert precision_at_10([3, 2, 1, 0]) == 0.2


def test_best_runs_by_version_rows_keeps_highest_final_total_and_latest_tiebreak() -> None:
    rows = _best_runs_by_version_rows(
        [
            {
                "run_name": "older-best",
                "state": "finished",
                "eval_enabled": True,
                "seektalent_version": "0.4.1",
                "created_at": "2026-04-14T09:00:00Z",
                "final_total_score": 0.4,
                "final_precision_at_10": 0.1,
                "final_ndcg_at_10": 0.2,
                "round_01_total_score": 0.0,
                "round_01_precision_at_10": 0.0,
                "round_01_ndcg_at_10": 0.0,
            },
            {
                "run_name": "newer-best",
                "state": "finished",
                "eval_enabled": True,
                "seektalent_version": "0.4.1",
                "created_at": "2026-04-14T10:00:00Z",
                "final_total_score": 0.4,
                "final_precision_at_10": 0.2,
                "final_ndcg_at_10": 0.3,
                "round_01_total_score": 0.0,
                "round_01_precision_at_10": 0.0,
                "round_01_ndcg_at_10": 0.0,
            },
            {
                "run_name": "stronger",
                "state": "finished",
                "eval_enabled": True,
                "seektalent_version": "0.2.5",
                "created_at": "2026-04-14T08:00:00Z",
                "final_total_score": 0.7,
                "final_precision_at_10": 0.4,
                "final_ndcg_at_10": 0.5,
                "round_01_total_score": 0.1,
                "round_01_precision_at_10": 0.1,
                "round_01_ndcg_at_10": 0.1,
            },
            {"run_name": "skip-disabled", "state": "finished", "eval_enabled": False, "seektalent_version": "0.2.5"},
            {"run_name": "skip-crashed", "state": "crashed", "eval_enabled": True, "seektalent_version": "0.2.6"},
        ]
    )

    assert [row["run_name"] for row in rows] == ["newer-best", "stronger"]


def test_latest_runs_by_version_rows_keeps_newest_created_at() -> None:
    rows = _latest_runs_by_version_rows(
        [
            {
                "run_name": "older",
                "state": "finished",
                "eval_enabled": True,
                "version": "0.4.1",
                "created_at": "2026-04-14T09:00:00Z",
                "final_total_score": 0.1,
                "final_precision_at_10": 0.1,
                "final_ndcg_at_10": 0.1,
                "round_01_total_score": 0.0,
                "round_01_precision_at_10": 0.0,
                "round_01_ndcg_at_10": 0.0,
            },
            {
                "run_name": "newer",
                "state": "finished",
                "eval_enabled": True,
                "version": "0.4.1",
                "created_at": "2026-04-14T10:00:00Z",
                "final_total_score": 0.0,
                "final_precision_at_10": 0.0,
                "final_ndcg_at_10": 0.0,
                "round_01_total_score": 0.0,
                "round_01_precision_at_10": 0.0,
                "round_01_ndcg_at_10": 0.0,
            },
        ]
    )

    assert rows[0]["run_name"] == "newer"


def test_worst_runs_by_version_rows_keeps_lowest_final_total_and_latest_tiebreak() -> None:
    rows = _worst_runs_by_version_rows(
        [
            {
                "run_name": "older-worst",
                "state": "finished",
                "eval_enabled": True,
                "version": "0.4.1",
                "created_at": "2026-04-14T09:00:00Z",
                "final_total_score": 0.1,
                "final_precision_at_10": 0.1,
                "final_ndcg_at_10": 0.1,
                "round_01_total_score": 0.0,
                "round_01_precision_at_10": 0.0,
                "round_01_ndcg_at_10": 0.0,
            },
            {
                "run_name": "newer-worst",
                "state": "finished",
                "eval_enabled": True,
                "version": "0.4.1",
                "created_at": "2026-04-14T10:00:00Z",
                "final_total_score": 0.1,
                "final_precision_at_10": 0.2,
                "final_ndcg_at_10": 0.2,
                "round_01_total_score": 0.0,
                "round_01_precision_at_10": 0.0,
                "round_01_ndcg_at_10": 0.0,
            },
            {
                "run_name": "better",
                "state": "finished",
                "eval_enabled": True,
                "version": "0.4.1",
                "created_at": "2026-04-14T11:00:00Z",
                "final_total_score": 0.3,
                "final_precision_at_10": 0.3,
                "final_ndcg_at_10": 0.3,
                "round_01_total_score": 0.1,
                "round_01_precision_at_10": 0.1,
                "round_01_ndcg_at_10": 0.1,
            },
        ]
    )

    assert rows[0]["run_name"] == "newer-worst"


def test_version_means_rows_averages_all_successful_runs() -> None:
    rows = _version_means_rows(
        [
            {
                "run_name": "run-1",
                "state": "finished",
                "eval_enabled": True,
                "version": "0.4.1",
                "rounds_executed": 3,
                "created_at": "2026-04-14T09:00:00Z",
                "final_total_score": 0.0,
                "final_precision_at_10": 0.0,
                "final_ndcg_at_10": 0.0,
                "round_01_total_score": 0.0,
                "round_01_precision_at_10": 0.0,
                "round_01_ndcg_at_10": 0.0,
            },
            {
                "run_name": "run-2",
                "state": "finished",
                "eval_enabled": True,
                "version": "0.4.1",
                "rounds_executed": 5,
                "created_at": "2026-04-14T10:00:00Z",
                "final_total_score": 0.4,
                "final_precision_at_10": 0.2,
                "final_ndcg_at_10": 0.3,
                "round_01_total_score": 0.1,
                "round_01_precision_at_10": 0.2,
                "round_01_ndcg_at_10": 0.3,
            },
        ]
    )

    assert rows == [
        {
            "version": "0.4.1",
            "run_count": 2,
            "avg_rounds": pytest.approx(4.0),
            "final_total_mean": pytest.approx(0.2),
            "final_precision_mean": pytest.approx(0.1),
            "final_ndcg_mean": pytest.approx(0.15),
            "round1_total_mean": pytest.approx(0.05),
            "round1_precision_mean": pytest.approx(0.1),
            "round1_ndcg_mean": pytest.approx(0.15),
            "judge_cache_reuse_pct": 0.0,
        }
    ]


def test_version_means_rows_aggregates_judge_cache_reuse_counts() -> None:
    rows = _version_means_rows(
        [
            {
                "run_name": "run-1",
                "state": "finished",
                "eval_enabled": True,
                "version": "0.4.7",
                "rounds_executed": 3,
                "created_at": "2026-04-14T09:00:00Z",
                "final_total_score": 0.0,
                "final_precision_at_10": 0.0,
                "final_ndcg_at_10": 0.0,
                "round_01_total_score": 0.0,
                "round_01_precision_at_10": 0.0,
                "round_01_ndcg_at_10": 0.0,
                "judge_candidate_count": 4,
                "judge_cache_hit_count": 1,
            },
            {
                "run_name": "run-2",
                "state": "finished",
                "eval_enabled": True,
                "version": "0.4.7",
                "rounds_executed": 5,
                "created_at": "2026-04-14T10:00:00Z",
                "final_total_score": 0.4,
                "final_precision_at_10": 0.2,
                "final_ndcg_at_10": 0.3,
                "round_01_total_score": 0.1,
                "round_01_precision_at_10": 0.2,
                "round_01_ndcg_at_10": 0.3,
                "judge_candidate_count": 6,
                "judge_cache_hit_count": 4,
            },
        ]
    )

    assert rows[0]["judge_cache_reuse_pct"] == pytest.approx(50.0)


def test_version_means_summary_markdown_includes_judge_cache_reuse_pct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "seektalent.evaluation._report_run_rows",
        lambda **kwargs: [  # noqa: ARG005
            {
                "run_name": "run-1",
                "state": "finished",
                "eval_enabled": True,
                "version": "0.4.7",
                "rounds_executed": 3,
                "created_at": "2026-04-14T09:00:00Z",
                "final_total_score": 0.0,
                "final_precision_at_10": 0.0,
                "final_ndcg_at_10": 0.0,
                "round_01_total_score": 0.0,
                "round_01_precision_at_10": 0.0,
                "round_01_ndcg_at_10": 0.0,
                "judge_candidate_count": 2,
                "judge_cache_hit_count": 1,
            }
        ],
    )

    markdown = _version_means_summary_markdown(entity="entity", project="project")

    assert "Judge cache reuse %" in markdown
    assert "50.00%" in markdown


def test_version_report_markdown_includes_extra_current_run(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {
        "run_name": "run-current",
        "run_url": "https://example.com/run-current",
        "created_at": "2026-04-21T02:17:11Z",
        "state": "finished",
        "eval_enabled": True,
        "version": "0.4.10",
        "seektalent_version": "0.4.10",
        "judge_model": "openai-responses:gpt-5.4",
        "rounds_executed": 3,
        "final_total_score": 0.2,
        "final_precision_at_10": 0.1,
        "final_ndcg_at_10": 0.4,
        "round_01_total_score": 0.1,
        "round_01_precision_at_10": 0.1,
        "round_01_ndcg_at_10": 0.3,
    }
    indexed_row = {**row, "run_url": "https://example.com/indexed-run-current", "final_total_score": 0.4}
    monkeypatch.setattr("seektalent.evaluation._report_run_rows", lambda **kwargs: [indexed_row])  # noqa: ARG005

    latest = _version_runs_markdown(entity="entity", project="project", heading="latest", extra_rows=[row])
    means = _version_means_summary_markdown(entity="entity", project="project", extra_rows=[row])

    assert "0.4.10" in latest
    assert "run-current" in latest
    assert "| 0.4.10 | 1 |" in means
    assert "0.2000" in means


def test_judge_cache_round_trip(tmp_path: Path) -> None:
    cache = JudgeCache(tmp_path)
    try:
        result = ResumeJudgeResult(score=3, rationale="Strong direct match.")
        cache.put_label(
            task_sha256_value="task",
            snapshot_sha256_value="resume",
            judge_model="openai-chat:deepseek-v3.2",
            result=result,
        )

        loaded = cache.get_label(
            task_sha256_value="task",
            snapshot_sha256_value="resume",
        )
        loaded_with_other_model = cache.get(
            jd_sha256_value="task",
            snapshot_sha256_value="resume",
            model_id="openai-chat:qwen-plus",
        )

        assert loaded == result
        assert loaded_with_other_model == result
    finally:
        cache.close()


def test_judge_cache_summary_counts_unique_snapshots_once() -> None:
    cached = EvaluatedCandidate(
        rank=1,
        resume_id="cached-round",
        snapshot_sha256="snapshot-cached",
        raw_resume_path="raw_resumes/snapshot-cached.json",
        judge_score=3,
        judge_rationale="Cached.",
        cache_hit=True,
    )
    repeated_cached = cached.model_copy(update={"rank": 1, "resume_id": "cached-final"})
    fresh = EvaluatedCandidate(
        rank=2,
        resume_id="fresh-final",
        snapshot_sha256="snapshot-fresh",
        raw_resume_path="raw_resumes/snapshot-fresh.json",
        judge_score=2,
        judge_rationale="Fresh.",
        cache_hit=False,
    )
    evaluation = EvaluationResult(
        run_id="run-1",
        judge_model="openai-responses:gpt-5.4",
        jd_sha256="jd",
        round_01=EvaluationStageResult(
            stage="round_01",
            ndcg_at_10=0.0,
            precision_at_10=0.0,
            total_score=0.0,
            candidates=[cached],
        ),
        final=EvaluationStageResult(
            stage="final",
            ndcg_at_10=0.0,
            precision_at_10=0.0,
            total_score=0.0,
            candidates=[repeated_cached, fresh],
        ),
    )

    assert _judge_cache_summary(evaluation) == {
        "judge_candidate_count": 2,
        "judge_cache_hit_count": 1,
        "judge_cache_hit_rate_pct": 50.0,
    }


def test_evaluate_run_keeps_no_judge_artifacts_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    async def fake_judge_many(self, *, jd, notes, candidates, cache):  # noqa: ANN001
        del self, jd, notes, candidates, cache
        raise RuntimeError("judge failed")

    monkeypatch.setattr("seektalent.evaluation.ResumeJudge.judge_many", fake_judge_many)
    settings = make_settings(runs_dir=str(tmp_path / "runs"))
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge prompt", sha256="hash")
    candidate = ResumeCandidate(
        resume_id="resume-1",
        source_resume_id="resume-1",
        snapshot_sha256="snapshot-1",
        dedup_key="resume-1",
        expected_job_category="Engineer",
        now_location="上海",
        work_year=5,
        search_text="engineer",
        raw={"resume_id": "resume-1"},
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with pytest.raises(RuntimeError, match="judge failed"):
        asyncio.run(
            evaluate_run(
                settings=settings,
                prompt=prompt,
                run_id="run-1",
                run_dir=run_dir,
                jd="test jd",
                round_01_candidates=[candidate],
                final_candidates=[candidate],
                rounds_executed=1,
            )
        )

    assert not (run_dir / "evaluation").exists()
    assert not (run_dir / "raw_resumes").exists()
    assert (tmp_path / ".seektalent" / "judge_cache.sqlite3").exists()


def test_evaluate_run_does_not_log_wandb_when_weave_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    async def fake_judge_many(self, *, jd, notes, candidates, cache):  # noqa: ANN001
        del self, jd, notes, cache
        result = ResumeJudgeResult(score=3, rationale="Strong fit")
        return (
            {candidate.resume_id: (result, False, 1) for candidate in candidates},
            [("jd", candidate.snapshot_sha256, "openai-responses:gpt-5.4", result) for candidate in candidates],
        )

    wandb_calls: list[str] = []

    monkeypatch.setattr("seektalent.evaluation.ResumeJudge.judge_many", fake_judge_many)
    monkeypatch.setattr("seektalent.evaluation._log_to_weave", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("weave failed")))
    monkeypatch.setattr("seektalent.evaluation._log_to_wandb", lambda **kwargs: wandb_calls.append("wandb"))
    settings = make_settings(runs_dir=str(tmp_path / "runs"), enable_eval=True)
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge prompt", sha256="hash")
    candidate = ResumeCandidate(
        resume_id="resume-1",
        source_resume_id="resume-1",
        snapshot_sha256="snapshot-1",
        dedup_key="resume-1",
        expected_job_category="Engineer",
        now_location="上海",
        work_year=5,
        search_text="engineer",
        raw={"resume_id": "resume-1"},
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with pytest.raises(RuntimeError, match="weave failed"):
        asyncio.run(
            evaluate_run(
                settings=settings,
                prompt=prompt,
                run_id="run-1",
                run_dir=run_dir,
                jd="test jd",
                round_01_candidates=[candidate],
                final_candidates=[candidate],
                rounds_executed=1,
            )
        )

    assert wandb_calls == []
    assert not (run_dir / "evaluation").exists()
    assert (tmp_path / ".seektalent" / "judge_cache.sqlite3").exists()


def test_resume_judge_includes_notes_block_only_when_present(tmp_path: Path) -> None:
    settings = make_settings()
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge prompt", sha256="hash")
    candidate = ResumeCandidate(
        resume_id="resume-1",
        source_resume_id="resume-1",
        snapshot_sha256="snapshot-1",
        dedup_key="resume-1",
        expected_job_category="Engineer",
        now_location="上海",
        work_year=5,
        search_text="engineer",
        raw={"resume_id": "resume-1"},
    )
    cache = JudgeCache(tmp_path)
    prompts: list[str] = []

    class FakeAgent:
        async def run(self, prompt_text: str):  # noqa: ANN001
            prompts.append(prompt_text)
            return SimpleNamespace(output=ResumeJudgeResult(score=2, rationale="ok"))

    judge = ResumeJudge(settings, prompt)
    cast(Any, judge)._build_agent = lambda: FakeAgent()
    try:
        asyncio.run(judge.judge_many(jd="JD text", notes="Prefer agent experience", candidates=[candidate], cache=cache))
        asyncio.run(judge.judge_many(jd="JD text", notes="", candidates=[candidate], cache=JudgeCache(tmp_path / "other")))
    finally:
        cache.close()

    assert "JOB DESCRIPTION" in prompts[0]
    assert "NOTES" in prompts[0]
    assert "Prefer agent experience" in prompts[0]
    assert "RESUME SNAPSHOT" in prompts[0]
    assert "NOTES\n(none)" in prompts[1]


def test_resume_judge_cache_uses_task_and_resume_without_model(tmp_path: Path) -> None:
    cache = JudgeCache(tmp_path)
    candidate = ResumeCandidate(
        resume_id="resume-1",
        source_resume_id="resume-1",
        snapshot_sha256="snapshot-1",
        dedup_key="resume-1",
        expected_job_category="Engineer",
        now_location="上海",
        work_year=5,
        search_text="engineer",
        raw={"resume_id": "resume-1"},
    )
    cached = ResumeJudgeResult(score=3, rationale="Cached fit.")
    cache.put_label(
        task_sha256_value=task_sha256("JD text", "Prefer agent experience"),
        snapshot_sha256_value="snapshot-1",
        judge_model="openai-chat:old-model",
        result=cached,
    )
    prompts: list[str] = []

    class FakeAgent:
        async def run(self, prompt_text: str):  # noqa: ANN001
            prompts.append(prompt_text)
            return SimpleNamespace(output=ResumeJudgeResult(score=0, rationale="should not run"))

    settings = make_settings(judge_model="openai-chat:new-model")
    judge = ResumeJudge(settings, LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge", sha256="hash"))
    cast(Any, judge)._build_agent = lambda: FakeAgent()
    try:
        results, writes = asyncio.run(
            judge.judge_many(
                jd="JD text",
                notes="Prefer agent experience",
                candidates=[candidate],
                cache=cache,
            )
        )
        different_notes = cache.get_label(
            task_sha256_value=task_sha256("JD text", "Different notes"),
            snapshot_sha256_value="snapshot-1",
        )
    finally:
        cache.close()

    assert results["resume-1"] == (cached, True, 0)
    assert writes == []
    assert prompts == []
    assert different_notes is None


def test_resume_judge_reuses_pending_snapshot_within_batch(tmp_path: Path) -> None:
    settings = make_settings()
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge prompt", sha256="hash")
    candidates = [
        ResumeCandidate(
            resume_id="resume-1",
            source_resume_id="source-1",
            snapshot_sha256="same-snapshot",
            dedup_key="resume-1",
            expected_job_category="Engineer",
            now_location="上海",
            work_year=5,
            search_text="engineer",
            raw={"resume_id": "source-1"},
        ),
        ResumeCandidate(
            resume_id="resume-2",
            source_resume_id="source-2",
            snapshot_sha256="same-snapshot",
            dedup_key="resume-2",
            expected_job_category="Engineer",
            now_location="上海",
            work_year=5,
            search_text="engineer",
            raw={"resume_id": "source-1"},
        ),
    ]
    prompts: list[str] = []

    class FakeAgent:
        async def run(self, prompt_text: str):  # noqa: ANN001
            prompts.append(prompt_text)
            return SimpleNamespace(output=ResumeJudgeResult(score=3, rationale="Strong fit."))

    cache = JudgeCache(tmp_path)
    judge = ResumeJudge(settings, prompt)
    cast(Any, judge)._build_agent = lambda: FakeAgent()
    try:
        results, writes = asyncio.run(judge.judge_many(jd="JD text", candidates=candidates, cache=cache))
    finally:
        cache.close()

    assert len(prompts) == 1
    assert results["resume-1"][0] == results["resume-2"][0]
    assert len(writes) == 1
    assert writes[0].snapshot_sha256_value == "same-snapshot"


def test_resume_judge_uses_judge_concurrency_limit(tmp_path: Path) -> None:
    settings = make_settings(judge_max_concurrency=2, scoring_max_concurrency=9)
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge prompt", sha256="hash")
    candidates = [
        ResumeCandidate(
            resume_id=f"resume-{index}",
            source_resume_id=f"resume-{index}",
            snapshot_sha256=f"snapshot-{index}",
            dedup_key=f"resume-{index}",
            expected_job_category="Engineer",
            now_location="上海",
            work_year=5,
            search_text="engineer",
            raw={"resume_id": f"resume-{index}"},
        )
        for index in range(5)
    ]
    counters = {"active": 0, "max_active": 0}

    class FakeAgent:
        async def run(self, prompt_text: str):  # noqa: ANN001
            assert "RESUME SNAPSHOT" in prompt_text
            counters["active"] += 1
            counters["max_active"] = max(counters["max_active"], counters["active"])
            await asyncio.sleep(0.01)
            counters["active"] -= 1
            return SimpleNamespace(output=ResumeJudgeResult(score=2, rationale="ok"))

    cache = JudgeCache(tmp_path)
    judge = ResumeJudge(settings, prompt)
    cast(Any, judge)._build_agent = lambda: FakeAgent()
    try:
        results, writes = asyncio.run(judge.judge_many(jd="JD text", candidates=candidates, cache=cache))
    finally:
        cache.close()

    assert counters["max_active"] == 2
    assert set(results) == {candidate.resume_id for candidate in candidates}
    assert len(writes) == len(candidates)


def test_evaluate_run_passes_notes_to_judge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    seen: dict[str, object] = {}

    async def fake_judge_many(self, *, jd, notes, candidates, cache):  # noqa: ANN001
        del self, cache
        seen["jd"] = jd
        seen["notes"] = notes
        result = ResumeJudgeResult(score=3, rationale="Strong fit")
        return (
            {candidate.resume_id: (result, False, 1) for candidate in candidates},
            [("jd", candidate.snapshot_sha256, "openai-responses:gpt-5.4", result) for candidate in candidates],
        )

    monkeypatch.setattr("seektalent.evaluation.ResumeJudge.judge_many", fake_judge_many)
    monkeypatch.setattr("seektalent.evaluation._log_to_wandb", lambda **kwargs: None)
    monkeypatch.setattr("seektalent.evaluation._log_to_weave", lambda **kwargs: None)
    settings = make_settings(runs_dir=str(tmp_path / "runs"))
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge prompt", sha256="hash")
    candidate = ResumeCandidate(
        resume_id="resume-1",
        source_resume_id="resume-1",
        snapshot_sha256="snapshot-1",
        dedup_key="resume-1",
        expected_job_category="Engineer",
        now_location="上海",
        work_year=5,
        search_text="engineer",
        raw={"resume_id": "resume-1"},
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    asyncio.run(
        evaluate_run(
            settings=settings,
            prompt=prompt,
            run_id="run-1",
            run_dir=run_dir,
            jd="JD text",
            notes="Notes text",
            round_01_candidates=[candidate],
            final_candidates=[candidate],
            rounds_executed=2,
        )
    )

    assert seen == {"jd": "JD text", "notes": "Notes text"}


def test_evaluate_run_persists_jd_resume_and_label_assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("seektalent.evaluation._log_to_wandb", lambda **kwargs: None)
    monkeypatch.setattr("seektalent.evaluation._log_to_weave", lambda **kwargs: None)

    class FakeAgent:
        async def run(self, prompt_text: str):  # noqa: ANN001
            assert "JOB DESCRIPTION" in prompt_text
            return SimpleNamespace(output=ResumeJudgeResult(score=3, rationale="Strong fit."))

    monkeypatch.setattr("seektalent.evaluation.ResumeJudge._build_agent", lambda self: FakeAgent())
    settings = make_settings(runs_dir=str(tmp_path / "runs"), judge_model="openai-chat:deepseek-v3.2")
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge prompt", sha256="prompt-hash")
    candidate = ResumeCandidate(
        resume_id="resume-1",
        source_resume_id="source-1",
        snapshot_sha256="snapshot-1",
        dedup_key="resume-1",
        expected_job_category="Engineer",
        now_location="上海",
        work_year=5,
        search_text="engineer",
        raw={"resume_id": "resume-1", "skill": "agent"},
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    artifacts = asyncio.run(
        evaluate_run(
            settings=settings,
            prompt=prompt,
            run_id="run-1",
            run_dir=run_dir,
            jd="JD text",
            notes="Notes text",
            round_01_candidates=[candidate],
            final_candidates=[candidate],
            rounds_executed=1,
        )
    )

    conn = sqlite3.connect(tmp_path / ".seektalent" / "judge_cache.sqlite3")
    conn.row_factory = sqlite3.Row
    try:
        task_hash = task_sha256("JD text", "Notes text")
        jd_row = conn.execute("SELECT * FROM jd_assets WHERE task_sha256 = ?", (task_hash,)).fetchone()
        resume_row = conn.execute("SELECT * FROM resume_assets WHERE snapshot_sha256 = ?", ("snapshot-1",)).fetchone()
        label_row = conn.execute(
            "SELECT * FROM judge_labels WHERE task_sha256 = ? AND snapshot_sha256 = ?",
            (task_hash, "snapshot-1"),
        ).fetchone()
        tables = _table_names(conn)
        jd_columns = _column_names(conn, "jd_assets")
        resume_columns = _column_names(conn, "resume_assets")
        label_columns = _column_names(conn, "judge_labels")
    finally:
        conn.close()

    assert tables == {"jd_assets", "resume_assets", "judge_labels"}
    assert "source_run_id" not in jd_columns
    assert resume_columns == {"snapshot_sha256", "raw_json", "captured_at"}
    assert "label_source" not in label_columns
    assert "judge_prompt_sha256" not in label_columns
    assert artifacts.result.final.candidates[0].cache_hit is False
    assert jd_row["jd_text"] == "JD text"
    assert jd_row["notes_text"] == "Notes text"
    assert json.loads(resume_row["raw_json"]) == {"resume_id": "resume-1", "skill": "agent"}
    assert label_row["score"] == 3
    assert label_row["judge_model"] == "openai-chat:deepseek-v3.2"
    assert label_row["judge_prompt_text"] == "judge prompt"


def test_migrate_judge_assets_backfills_runs_and_reports_conflicts(tmp_path: Path) -> None:
    def write_run(run_name: str, *, score: int, rationale: str, include_prompt_snapshot: bool = True) -> None:
        run_dir = tmp_path / "runs" / run_name
        (run_dir / "raw_resumes").mkdir(parents=True)
        (run_dir / "evaluation").mkdir()
        if include_prompt_snapshot:
            (run_dir / "prompt_snapshots").mkdir()
            (run_dir / "prompt_snapshots" / "judge.md").write_text(
                f"{run_name} judge prompt",
                encoding="utf-8",
            )
        input_truth = {
            "job_title": "Agent Engineer",
            "jd": "JD text",
            "notes": "Notes text",
            "job_title_sha256": "title",
            "jd_sha256": "jd",
            "notes_sha256": "notes",
        }
        (run_dir / "input_truth.json").write_text(json.dumps(input_truth), encoding="utf-8")
        raw_resume = {
            "resume_id": "resume-1",
            "source_resume_id": "source-1",
            "snapshot_sha256": "snapshot-1",
            "captured_at": "2026-04-19T00:00:00+08:00",
            "candidate": {"resume_id": "resume-1", "skill": "agent"},
        }
        (run_dir / "raw_resumes" / "snapshot-1.json").write_text(json.dumps(raw_resume), encoding="utf-8")
        evaluation = {
            "run_id": run_name,
            "judge_model": "openai-responses:gpt-5.4",
            "jd_sha256": sha256("JD text".encode("utf-8")).hexdigest(),
            "round_01": {"stage": "round_01", "candidates": []},
            "final": {
                "stage": "final",
                "candidates": [
                    {
                        "rank": 1,
                        "resume_id": "resume-1",
                        "source_resume_id": "source-1",
                        "snapshot_sha256": "snapshot-1",
                        "raw_resume_path": "raw_resumes/snapshot-1.json",
                        "judge_score": score,
                        "judge_rationale": rationale,
                    }
                ],
            },
        }
        (run_dir / "evaluation" / "evaluation.json").write_text(json.dumps(evaluation), encoding="utf-8")

    db_path = tmp_path / ".seektalent" / "judge_cache.sqlite3"
    db_path.parent.mkdir()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE judge_cache (jd_sha256 TEXT)")
        conn.commit()
    finally:
        conn.close()
    write_run("20260418_000000_old", score=1, rationale="Old label.")
    write_run("20260419_000000_new", score=3, rationale="New label.")
    write_run("20260420_000000_fallback_prompt", score=2, rationale="Fallback prompt.", include_prompt_snapshot=False)

    report = migrate_judge_assets(project_root=tmp_path, runs_dir=tmp_path / "runs")

    conn = sqlite3.connect(tmp_path / ".seektalent" / "judge_cache.sqlite3")
    conn.row_factory = sqlite3.Row
    try:
        task_hash = task_sha256("JD text", "Notes text")
        label = conn.execute(
            "SELECT * FROM judge_labels WHERE task_sha256 = ? AND snapshot_sha256 = ?",
            (task_hash, "snapshot-1"),
        ).fetchone()
        resume = conn.execute("SELECT * FROM resume_assets WHERE snapshot_sha256 = ?", ("snapshot-1",)).fetchone()
        jd = conn.execute("SELECT * FROM jd_assets WHERE task_sha256 = ?", (task_hash,)).fetchone()
        tables = _table_names(conn)
        resume_columns = _column_names(conn, "resume_assets")
        label_columns = _column_names(conn, "judge_labels")
    finally:
        conn.close()

    assert report["runs_scanned"] == 3
    assert len(cast(list[object], report["conflicts"])) == 2
    assert "unresolved_legacy_rows" not in report
    assert tables == {"jd_assets", "resume_assets", "judge_labels"}
    assert resume_columns == {"snapshot_sha256", "raw_json", "captured_at"}
    assert "source_run_id" not in label_columns
    assert "judge_prompt_sha256" not in label_columns
    assert label["score"] == 2
    assert label["judge_prompt_text"] == (package_prompt_dir() / "judge.md").read_text(encoding="utf-8")
    assert json.loads(resume["raw_json"]) == {"resume_id": "resume-1", "skill": "agent"}
    assert jd["job_title"] == "Agent Engineer"


def test_migrate_judge_assets_stores_prompt_snapshot_text(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "20260419_000000_prompt"
    (run_dir / "raw_resumes").mkdir(parents=True)
    (run_dir / "evaluation").mkdir()
    (run_dir / "prompt_snapshots").mkdir()
    (run_dir / "input_truth.json").write_text(
        json.dumps({"job_title": "Agent Engineer", "jd": "JD text", "notes": "Notes text"}),
        encoding="utf-8",
    )
    (run_dir / "prompt_snapshots" / "judge.md").write_text("historical judge prompt", encoding="utf-8")
    (run_dir / "raw_resumes" / "snapshot-2.json").write_text(
        json.dumps({"snapshot_sha256": "snapshot-2", "candidate": {"skill": "rag"}}),
        encoding="utf-8",
    )
    (run_dir / "evaluation" / "evaluation.json").write_text(
        json.dumps(
            {
                "run_id": "20260419_000000_prompt",
                "judge_model": "openai-responses:gpt-5.4",
                "round_01": {"stage": "round_01", "candidates": []},
                "final": {
                    "stage": "final",
                    "candidates": [
                        {
                            "rank": 1,
                            "resume_id": "resume-2",
                            "snapshot_sha256": "snapshot-2",
                            "raw_resume_path": "raw_resumes/snapshot-2.json",
                            "judge_score": 3,
                            "judge_rationale": "Strong.",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    migrate_judge_assets(project_root=tmp_path, runs_dir=tmp_path / "runs")

    conn = sqlite3.connect(tmp_path / ".seektalent" / "judge_cache.sqlite3")
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT judge_prompt_text FROM judge_labels").fetchone()
    finally:
        conn.close()

    assert row["judge_prompt_text"] == "historical judge prompt"


def test_evaluate_run_logs_weave_and_wandb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    class FakePrediction:
        def __init__(self, inputs: dict, output: dict) -> None:
            self.inputs = inputs
            self.output = output
            self.scores: dict[str, object] = {}

        def log_score(self, name: str, value: object) -> None:
            self.scores[name] = value

        def finish(self) -> None:
            return None

    class FakeEvaluationLogger:
        instances: list["FakeEvaluationLogger"] = []

        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs
            self.predictions: list[FakePrediction] = []
            self.views: dict[str, str] = {}
            self.summary: dict | None = None
            self.auto_summarize: bool | None = None
            type(self).instances.append(self)

        def log_prediction(self, *, inputs: dict, output: dict) -> FakePrediction:
            prediction = FakePrediction(inputs, output)
            self.predictions.append(prediction)
            return prediction

        def set_view(self, name: str, content: str, **kwargs) -> None:  # noqa: ANN003
            del kwargs
            self.views[name] = content

        def log_summary(self, summary: dict | None = None, auto_summarize: bool = True) -> None:
            self.summary = summary
            self.auto_summarize = auto_summarize

    init_calls: list[str] = []

    def fake_init(project_name: str):  # noqa: ANN001
        init_calls.append(project_name)
        return object()

    monkeypatch.setitem(
        sys.modules,
        "weave",
        SimpleNamespace(init=fake_init, EvaluationLogger=FakeEvaluationLogger),
    )

    class FakeTable:
        def __init__(self, columns: list[str]) -> None:
            self.columns = columns
            self.rows: list[tuple[object, ...]] = []

        def add_data(self, *row: object) -> None:
            self.rows.append(row)

    class FakeArtifact:
        def __init__(self, name: str, type: str) -> None:  # noqa: A002
            self.name = name
            self.type = type
            self.files: list[str] = []
            self.dirs: list[tuple[str, str]] = []

        def add_file(self, path: str) -> None:
            self.files.append(path)

        def add_dir(self, path: str, *, name: str) -> None:
            self.dirs.append((path, name))

    class FakeRun:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs
            self.logged: list[dict[str, object]] = []
            self.artifacts: list[FakeArtifact] = []
            self.finished = False
            self.url = f"https://example.com/{kwargs['name']}"

        def log(self, payload: dict[str, object]) -> None:
            self.logged.append(payload)

        def log_artifact(self, artifact: FakeArtifact) -> None:
            self.artifacts.append(artifact)

        def finish(self) -> None:
            self.finished = True

    class FakeSavedReport:
        def __init__(self, url: str) -> None:
            self.url = url
            self.title = ""
            self.description = ""
            self.blocks: list[object] = []
            self.width = "readable"
            self.save_calls = 0

        def save(self) -> None:
            self.save_calls += 1

    class FakeApi:
        def reports(self, path: str, name: str | None = None, per_page: int = 50):  # noqa: ANN001
            del path, name, per_page
            return []

    class FakeWandb:
        def __init__(self) -> None:
            self.runs: list[FakeRun] = []

        def init(self, **kwargs) -> FakeRun:  # noqa: ANN003
            run = FakeRun(**kwargs)
            self.runs.append(run)
            return run

        def Api(self) -> FakeApi:  # noqa: N802
            return FakeApi()

        Artifact = FakeArtifact
        Table = FakeTable

    class FakeSummaryMetric:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeConfig:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeBarPlot:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs

    class FakeRunset:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs

    class FakePanelGrid:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs

    class FakeReport:
        instances: list["FakeReport"] = []

        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs
            self.title = kwargs["title"]
            self.description = kwargs["description"]
            self.blocks = kwargs["blocks"]
            self.width = kwargs["width"]
            self.save_calls = 0
            type(self).instances.append(self)

        @classmethod
        def from_url(cls, url: str) -> FakeSavedReport:
            return FakeSavedReport(url)

        def save(self) -> None:
            self.save_calls += 1

    fake_wandb = FakeWandb()
    monkeypatch.setitem(sys.modules, "wandb", fake_wandb)
    monkeypatch.setattr("seektalent.evaluation._version_runs_markdown", lambda **kwargs: f"| {kwargs['heading'].title()} run |")
    monkeypatch.setattr("seektalent.evaluation._version_means_summary_markdown", lambda **kwargs: "| Means |")
    monkeypatch.setattr(
        "seektalent.evaluation._report_run_rows",
        lambda **kwargs: [  # noqa: ARG005
            {
                "run_name": "run-1",
                "run_url": "https://example.com/run-1",
                "created_at": "2026-04-15T02:52:28Z",
                "state": "finished",
                "eval_enabled": True,
                "version": "0.4.7",
                "seektalent_version": "0.4.7",
                "judge_model": "openai-responses:gpt-5.4",
                "rounds_executed": 4,
                "final_total_score": 0.13602752988942404,
                "final_precision_at_10": 0.1,
                "final_ndcg_at_10": 0.22009176629808017,
                "round_01_total_score": 0.13602752988942404,
                "round_01_precision_at_10": 0.1,
                "round_01_ndcg_at_10": 0.22009176629808017,
            },
            {
                "run_name": "run-0",
                "run_url": "https://example.com/run-0",
                "created_at": "2026-04-14T02:52:28Z",
                "state": "finished",
                "eval_enabled": True,
                "version": "0.4.1",
                "seektalent_version": "0.4.1",
                "judge_model": "openai-responses:gpt-5.4",
                "rounds_executed": 3,
                "final_total_score": 0.0,
                "final_precision_at_10": 0.0,
                "final_ndcg_at_10": 0.0,
                "round_01_total_score": 0.0,
                "round_01_precision_at_10": 0.0,
                "round_01_ndcg_at_10": 0.0,
            },
        ],
    )
    monkeypatch.setitem(
        sys.modules,
        "wandb_workspaces.reports.v2",
        SimpleNamespace(
            BarPlot=FakeBarPlot,
            Config=FakeConfig,
            H1=lambda text: ("H1", text),
            H2=lambda text: ("H2", text),
            MarkdownBlock=lambda text: ("MarkdownBlock", text),
            P=lambda text: ("P", text),
            PanelGrid=FakePanelGrid,
            Report=FakeReport,
            Runset=FakeRunset,
            SummaryMetric=FakeSummaryMetric,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "wandb_workspaces.reports.v2.interface",
        SimpleNamespace(
                expr=SimpleNamespace(
                    Config=FakeExprConfig,
                    Metric=FakeExprMetric,
                    Summary=FakeExprSummary,
                )
            ),
        )

    async def fake_judge_many(self, *, jd, notes, candidates, cache):  # noqa: ANN001
        del self, jd, notes, cache
        result = ResumeJudgeResult(score=3, rationale="Strong fit")
        return (
            {candidate.resume_id: (result, False, 1) for candidate in candidates},
            [("jd", candidate.snapshot_sha256, "openai-responses:gpt-5.4", result) for candidate in candidates],
        )

    monkeypatch.setattr("seektalent.evaluation.ResumeJudge.judge_many", fake_judge_many)
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        enable_eval=True,
        wandb_entity="frankqdwang1-personal-creations",
        wandb_project="seektalent",
        weave_entity="frankqdwang1-personal-creations",
        weave_project="seektalent",
        judge_model="openai-responses:gpt-5.4",
    )
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge prompt", sha256="hash")
    candidate = ResumeCandidate(
        resume_id="resume-1",
        source_resume_id="resume-1",
        snapshot_sha256="snapshot-1",
        dedup_key="resume-1",
        expected_job_category="Engineer",
        now_location="上海",
        work_year=5,
        search_text="engineer",
        raw={"resume_id": "resume-1"},
    )

    first_run_dir = tmp_path / "run-1"
    first_run_dir.mkdir()
    artifacts = asyncio.run(
        evaluate_run(
            settings=settings,
            prompt=prompt,
            run_id="run-1",
            run_dir=first_run_dir,
            jd="test jd",
            round_01_candidates=[candidate],
            final_candidates=[candidate],
            rounds_executed=4,
        )
    )

    assert init_calls == ["frankqdwang1-personal-creations/seektalent"]
    assert len(FakeEvaluationLogger.instances) == 2
    assert FakeEvaluationLogger.instances[0].summary == {
        "ndcg_at_10": pytest.approx(0.22009176629808017),
        "precision_at_10": 0.1,
        "total_score": pytest.approx(0.13602752988942404),
    }
    assert FakeEvaluationLogger.instances[0].auto_summarize is False
    assert "SeekTalent version" in FakeEvaluationLogger.instances[0].views["summary"]
    assert fake_wandb.runs[0].kwargs["config"]["version"] == "0.6.1"
    assert fake_wandb.runs[0].kwargs["config"]["seektalent_version"] == "0.6.1"
    assert fake_wandb.runs[0].kwargs["config"]["eval_enabled"] is True
    assert any("final_total_score" in payload for payload in fake_wandb.runs[0].logged)
    assert any(payload.get("rounds_executed") == 4 for payload in fake_wandb.runs[0].logged)
    assert any(payload.get("terminal_quality_gate_status") is None for payload in fake_wandb.runs[0].logged)
    assert any(payload.get("terminal_top_pool_strength") is None for payload in fake_wandb.runs[0].logged)
    assert any(payload.get("terminal_strong_fit_count") is None for payload in fake_wandb.runs[0].logged)
    assert any(payload.get("terminal_broadening_attempted") is None for payload in fake_wandb.runs[0].logged)
    assert any(payload.get("judge_candidate_count") == 1 for payload in fake_wandb.runs[0].logged)
    assert any(payload.get("judge_cache_hit_count") == 0 for payload in fake_wandb.runs[0].logged)
    assert any(payload.get("judge_cache_hit_rate_pct") == 0.0 for payload in fake_wandb.runs[0].logged)
    assert fake_wandb.runs[0].artifacts
    assert FakeReport.instances[0].title == WANDB_REPORT_TITLE
    markdown_blocks = [block for block in FakeReport.instances[0].blocks if isinstance(block, tuple) and block[0] == "MarkdownBlock"]
    assert len(markdown_blocks) == 4
    assert "Latest run" in markdown_blocks[0][1]
    assert "Best run" in markdown_blocks[1][1]
    assert "Worst run" in markdown_blocks[2][1]
    assert "Means" in markdown_blocks[3][1]
    panel_grids = [block for block in FakeReport.instances[0].blocks if isinstance(block, FakePanelGrid)]
    assert len(panel_grids) == 2
    assert sum(len(panel.kwargs["panels"]) for panel in panel_grids) == 6
    assert panel_grids[0].kwargs["panels"][0].kwargs["groupby"] == "config.version"
    assert [runset.kwargs["name"] for runset in panel_grids[0].kwargs["runsets"]] == ["All versions"]
    filters = panel_grids[0].kwargs["runsets"][0].kwargs["filters"]
    assert ("State", "finished") in filters
    assert ("eval_enabled", True) in filters
    assert ("final_total_score", ">=", 0) in filters
    assert ("round_01_ndcg_at_10", ">=", 0) in filters
    assert artifacts.result.final.total_score == pytest.approx(0.13602752988942404)


def test_evaluate_run_logs_weave_before_wandb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    async def fake_judge_many(self, *, jd, notes, candidates, cache):  # noqa: ANN001
        del self, jd, notes, cache
        result = ResumeJudgeResult(score=3, rationale="Strong fit")
        return (
            {candidate.resume_id: (result, False, 1) for candidate in candidates},
            [("jd", candidate.snapshot_sha256, "openai-responses:gpt-5.4", result) for candidate in candidates],
        )

    calls: list[str] = []
    monkeypatch.setattr("seektalent.evaluation.ResumeJudge.judge_many", fake_judge_many)
    monkeypatch.setattr("seektalent.evaluation._log_to_weave", lambda **kwargs: calls.append("weave"))
    monkeypatch.setattr("seektalent.evaluation._log_to_wandb", lambda **kwargs: calls.append("wandb"))
    settings = make_settings(runs_dir=str(tmp_path / "runs"), enable_eval=True)
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge prompt", sha256="hash")
    candidate = ResumeCandidate(
        resume_id="resume-1",
        source_resume_id="resume-1",
        snapshot_sha256="snapshot-1",
        dedup_key="resume-1",
        expected_job_category="Engineer",
        now_location="上海",
        work_year=5,
        search_text="engineer",
        raw={"resume_id": "resume-1"},
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    asyncio.run(
        evaluate_run(
            settings=settings,
            prompt=prompt,
            run_id="run-1",
            run_dir=run_dir,
            jd="test jd",
            round_01_candidates=[candidate],
            final_candidates=[candidate],
            rounds_executed=1,
        )
    )

    assert calls == ["weave", "wandb"]


def test_evaluate_run_skips_empty_weave_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    class FakePrediction:
        def log_score(self, name: str, value: object) -> None:
            del name, value

        def finish(self) -> None:
            return None

    class FakeEvaluationLogger:
        instances: list["FakeEvaluationLogger"] = []

        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs
            type(self).instances.append(self)

        def log_prediction(self, *, inputs: dict, output: dict) -> FakePrediction:
            del inputs, output
            return FakePrediction()

        def set_view(self, name: str, content: str, **kwargs) -> None:  # noqa: ANN003
            del name, content, kwargs

        def log_summary(self, summary: dict | None = None, auto_summarize: bool = True) -> None:
            del summary, auto_summarize

    monkeypatch.setitem(sys.modules, "weave", SimpleNamespace(init=lambda project_name: project_name, EvaluationLogger=FakeEvaluationLogger))
    monkeypatch.setattr("seektalent.evaluation._log_to_wandb", lambda **kwargs: None)

    async def fake_judge_many(self, *, jd, notes, candidates, cache):  # noqa: ANN001
        del self, jd, notes, cache
        result = ResumeJudgeResult(score=3, rationale="Strong fit")
        return (
            {candidate.resume_id: (result, False, 1) for candidate in candidates},
            [("jd", candidate.snapshot_sha256, "openai-responses:gpt-5.4", result) for candidate in candidates],
        )

    monkeypatch.setattr("seektalent.evaluation.ResumeJudge.judge_many", fake_judge_many)
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        enable_eval=True,
        weave_entity="frankqdwang1-personal-creations",
        weave_project="seektalent",
        judge_model="openai-responses:gpt-5.4",
    )
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge prompt", sha256="hash")
    candidate = ResumeCandidate(
        resume_id="resume-1",
        source_resume_id="resume-1",
        snapshot_sha256="snapshot-1",
        dedup_key="resume-1",
        expected_job_category="Engineer",
        now_location="上海",
        work_year=5,
        search_text="engineer",
        raw={"resume_id": "resume-1"},
    )

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    artifacts = asyncio.run(
        evaluate_run(
            settings=settings,
            prompt=prompt,
            run_id="run-1",
            run_dir=run_dir,
            jd="test jd",
            round_01_candidates=[],
            final_candidates=[candidate],
            rounds_executed=3,
        )
    )

    assert len(FakeEvaluationLogger.instances) == 1
    assert FakeEvaluationLogger.instances[0].kwargs["name"] == "seektalent-final"
    assert artifacts.result.round_01.ndcg_at_10 == 0.0


def test_upsert_wandb_report_reuses_existing_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    saved_report = SimpleNamespace(
        title="Old",
        description="Old",
        blocks=[],
        width="readable",
        save_calls=0,
        url="https://example.com/report",
        save=lambda: None,
    )

    def _save() -> None:
        saved_report.save_calls += 1

    saved_report.save = _save

    class FakeApi:
        called_with: tuple[str, str | None, int] | None = None

        def reports(self, path: str, name: str | None = None, per_page: int = 50):  # noqa: ANN001
            self.called_with = (path, name, per_page)
            return [SimpleNamespace(url="https://example.com/report", display_name=WANDB_REPORT_TITLE)]

    api = FakeApi()

    class FakeWandb:
        def Api(self) -> FakeApi:  # noqa: N802
            return api

    class FakeReport:
        @classmethod
        def from_url(cls, url: str):  # noqa: ANN001
            assert url == "https://example.com/report"
            return saved_report

    monkeypatch.setitem(sys.modules, "wandb", FakeWandb())
    monkeypatch.setattr("seektalent.evaluation._version_runs_markdown", lambda **kwargs: f"| {kwargs['heading'].title()} run |")
    monkeypatch.setattr("seektalent.evaluation._version_means_summary_markdown", lambda **kwargs: "| Means |")
    monkeypatch.setattr(
        "seektalent.evaluation._report_run_rows",
        lambda **kwargs: [  # noqa: ARG005
            {
                "run_name": "run-1",
                "run_url": "https://example.com/run-1",
                "created_at": "2026-04-15T02:52:28Z",
                "state": "finished",
                "eval_enabled": True,
                "version": "0.4.7",
                "seektalent_version": "0.4.7",
                "judge_model": "openai-responses:gpt-5.4",
                "rounds_executed": 4,
                "final_total_score": 0.1,
                "final_precision_at_10": 0.0,
                "final_ndcg_at_10": 0.1,
                "round_01_total_score": 0.0,
                "round_01_precision_at_10": 0.0,
                "round_01_ndcg_at_10": 0.0,
            }
        ],
    )
    monkeypatch.setitem(
        sys.modules,
        "wandb_workspaces.reports.v2",
        SimpleNamespace(
            BarPlot=lambda **kwargs: ("BarPlot", kwargs),
            Config=lambda name: ("Config", name),
            H1=lambda text: ("H1", text),
            H2=lambda text: ("H2", text),
            MarkdownBlock=lambda text: ("MarkdownBlock", text),
            P=lambda text: ("P", text),
            PanelGrid=lambda **kwargs: ("PanelGrid", kwargs),
            Report=FakeReport,
            Runset=lambda **kwargs: ("Runset", kwargs),
            SummaryMetric=lambda name: ("SummaryMetric", name),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "wandb_workspaces.reports.v2.interface",
        SimpleNamespace(
                expr=SimpleNamespace(
                    Config=FakeExprConfig,
                    Metric=FakeExprMetric,
                    Summary=FakeExprSummary,
                )
            ),
        )

    settings = make_settings(
        wandb_entity="frankqdwang1-personal-creations",
        wandb_project="seektalent",
    )

    _upsert_wandb_report(settings)

    assert api.called_with == ("frankqdwang1-personal-creations/seektalent", None, 100)
    assert saved_report.title == WANDB_REPORT_TITLE
    assert saved_report.width == "fluid"
    assert saved_report.save_calls == 1


def test_upsert_wandb_report_deletes_duplicate_titles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    deleted: list[str] = []
    saved_report = SimpleNamespace(
        title="Old",
        description="Old",
        blocks=[],
        width="readable",
        save_calls=0,
        url="https://example.com/report-1",
        save=lambda: None,
    )

    def _save() -> None:
        saved_report.save_calls += 1

    saved_report.save = _save

    class FakeApi:
        def reports(self, path: str, name: str | None = None, per_page: int = 50):  # noqa: ANN001
            del path, name, per_page
            return [
                SimpleNamespace(url="https://example.com/report-1", display_name=WANDB_REPORT_TITLE),
                SimpleNamespace(url="https://example.com/report-2", display_name=WANDB_REPORT_TITLE),
            ]

    class FakeWandb:
        def Api(self) -> FakeApi:  # noqa: N802
            return FakeApi()

    class FakeReport:
        @classmethod
        def from_url(cls, url: str):  # noqa: ANN001
            if url == "https://example.com/report-1":
                return saved_report
            return SimpleNamespace(delete=lambda: deleted.append(url))

    monkeypatch.setitem(sys.modules, "wandb", FakeWandb())
    monkeypatch.setattr("seektalent.evaluation._version_runs_markdown", lambda **kwargs: f"| {kwargs['heading'].title()} run |")
    monkeypatch.setattr("seektalent.evaluation._version_means_summary_markdown", lambda **kwargs: "| Means |")
    monkeypatch.setattr(
        "seektalent.evaluation._report_run_rows",
        lambda **kwargs: [  # noqa: ARG005
            {
                "run_name": "run-1",
                "run_url": "https://example.com/run-1",
                "created_at": "2026-04-15T02:52:28Z",
                "state": "finished",
                "eval_enabled": True,
                "version": "0.4.7",
                "seektalent_version": "0.4.7",
                "judge_model": "openai-responses:gpt-5.4",
                "rounds_executed": 4,
                "final_total_score": 0.1,
                "final_precision_at_10": 0.0,
                "final_ndcg_at_10": 0.1,
                "round_01_total_score": 0.0,
                "round_01_precision_at_10": 0.0,
                "round_01_ndcg_at_10": 0.0,
            }
        ],
    )
    monkeypatch.setitem(
        sys.modules,
        "wandb_workspaces.reports.v2",
        SimpleNamespace(
            BarPlot=lambda **kwargs: ("BarPlot", kwargs),
            Config=lambda name: ("Config", name),
            H1=lambda text: ("H1", text),
            H2=lambda text: ("H2", text),
            MarkdownBlock=lambda text: ("MarkdownBlock", text),
            P=lambda text: ("P", text),
            PanelGrid=lambda **kwargs: ("PanelGrid", kwargs),
            Report=FakeReport,
            Runset=lambda **kwargs: ("Runset", kwargs),
            SummaryMetric=lambda name: ("SummaryMetric", name),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "wandb_workspaces.reports.v2.interface",
        SimpleNamespace(
                expr=SimpleNamespace(
                    Config=FakeExprConfig,
                    Metric=FakeExprMetric,
                    Summary=FakeExprSummary,
                )
            ),
        )

    settings = make_settings(
        wandb_entity="frankqdwang1-personal-creations",
        wandb_project="seektalent",
    )

    _upsert_wandb_report(settings)

    assert saved_report.save_calls == 1
    assert deleted == ["https://example.com/report-2"]
