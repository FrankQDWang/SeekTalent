from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from seektalent.config import AppSettings
from seektalent.evaluation import JudgeCache, ResumeJudgeResult, evaluate_run, ndcg_at_10, precision_at_10, snapshot_sha256
from seektalent.models import ResumeCandidate
from seektalent.prompting import LoadedPrompt


def test_snapshot_sha256_is_stable_for_key_order() -> None:
    left = {"b": 2, "a": 1}
    right = {"a": 1, "b": 2}

    assert snapshot_sha256(left) == snapshot_sha256(right)


def test_ndcg_at_10_is_one_for_ideal_ranking() -> None:
    assert ndcg_at_10([3, 2, 2, 1, 0]) == 1.0


def test_precision_at_10_counts_scores_two_and_above() -> None:
    assert precision_at_10([3, 2, 1, 0]) == 0.2


def test_judge_cache_round_trip(tmp_path: Path) -> None:
    cache = JudgeCache(tmp_path)
    try:
        result = ResumeJudgeResult(score=3, rationale="Strong direct match.")
        cache.put(
            jd_sha256_value="jd",
            snapshot_sha256_value="resume",
            model_id="openai-chat:deepseek-v3.2",
            result=result,
        )

        loaded = cache.get(
            jd_sha256_value="jd",
            snapshot_sha256_value="resume",
            model_id="openai-chat:deepseek-v3.2",
        )

        assert loaded == result
    finally:
        cache.close()


def test_evaluate_run_keeps_no_judge_artifacts_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    async def fake_judge_many(self, *, jd, candidates, cache):  # noqa: ANN001
        del self, jd, candidates, cache
        raise RuntimeError("judge failed")

    monkeypatch.setattr("seektalent.evaluation.ResumeJudge.judge_many", fake_judge_many)
    settings = AppSettings(_env_file=None, runs_dir=str(tmp_path / "runs"))
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
            )
        )

    assert not (run_dir / "evaluation").exists()
    assert not (run_dir / "raw_resumes").exists()
    assert not (tmp_path / ".seektalent" / "judge_cache.sqlite3").exists()
