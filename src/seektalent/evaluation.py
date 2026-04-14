from __future__ import annotations

import asyncio
import json
import math
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, build_output_spec
from seektalent.models import ResumeCandidate
from seektalent.prompting import LoadedPrompt, json_block

TOP_K = 10
PRECISION_RELEVANCE_THRESHOLD = 2


JudgeScore = Literal[0, 1, 2, 3]


class ResumeJudgeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: JudgeScore
    rationale: str = Field(min_length=1)


class EvaluatedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int
    resume_id: str
    source_resume_id: str | None = None
    snapshot_sha256: str
    raw_resume_path: str
    expected_job_category: str | None = None
    now_location: str | None = None
    work_year: int | None = None
    judge_score: JudgeScore
    judge_rationale: str
    cache_hit: bool = False


class EvaluationStageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Literal["round_01", "final"]
    ndcg_at_10: float
    precision_at_10: float
    total_score: float
    candidates: list[EvaluatedCandidate] = Field(default_factory=list)


class EvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    judge_model: str
    jd_sha256: str
    round_01: EvaluationStageResult
    final: EvaluationStageResult


class JudgeTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Literal["round_01", "final"]
    rank: int
    jd_sha256: str
    resume_id: str
    source_resume_id: str | None = None
    snapshot_sha256: str
    raw_resume_path: str


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def snapshot_sha256(payload: dict[str, Any]) -> str:
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _cache_path(project_root: Path) -> Path:
    return project_root / ".seektalent" / "judge_cache.sqlite3"


class JudgeCache:
    def __init__(self, project_root: Path) -> None:
        self.path = _cache_path(project_root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: sqlite3.Connection | None = None
        if self.path.exists():
            self.conn = sqlite3.connect(self.path)
            self.conn.row_factory = sqlite3.Row

    def _ensure_conn(self) -> sqlite3.Connection:
        if self.conn is None:
            self.conn = sqlite3.connect(self.path)
            self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS judge_cache (
                jd_sha256 TEXT NOT NULL,
                snapshot_sha256 TEXT NOT NULL,
                model_id TEXT NOT NULL,
                score INTEGER NOT NULL,
                rationale TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (jd_sha256, snapshot_sha256, model_id)
            )
            """
        )
        self.conn.commit()
        return self.conn

    def get(self, *, jd_sha256_value: str, snapshot_sha256_value: str, model_id: str) -> ResumeJudgeResult | None:
        if self.conn is None and not self.path.exists():
            return None
        conn = self._ensure_conn()
        row = conn.execute(
            """
            SELECT score, rationale
            FROM judge_cache
            WHERE jd_sha256 = ? AND snapshot_sha256 = ? AND model_id = ?
            """,
            (jd_sha256_value, snapshot_sha256_value, model_id),
        ).fetchone()
        if row is None:
            return None
        return ResumeJudgeResult(score=row["score"], rationale=row["rationale"])

    def put(
        self,
        *,
        jd_sha256_value: str,
        snapshot_sha256_value: str,
        model_id: str,
        result: ResumeJudgeResult,
    ) -> None:
        conn = self._ensure_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO judge_cache (
                jd_sha256, snapshot_sha256, model_id, score, rationale, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                jd_sha256_value,
                snapshot_sha256_value,
                model_id,
                result.score,
                result.rationale,
                datetime.now().astimezone().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()

    def put_many(
        self,
        entries: list[tuple[str, str, str, ResumeJudgeResult]],
    ) -> None:
        if not entries:
            return
        conn = self._ensure_conn()
        try:
            conn.executemany(
                """
                INSERT OR REPLACE INTO judge_cache (
                    jd_sha256, snapshot_sha256, model_id, score, rationale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        jd_sha256_value,
                        snapshot_sha256_value,
                        model_id,
                        result.score,
                        result.rationale,
                        datetime.now().astimezone().isoformat(timespec="seconds"),
                    )
                    for jd_sha256_value, snapshot_sha256_value, model_id, result in entries
                ],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()


class ResumeJudge:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt

    def _build_agent(self) -> Agent[None, ResumeJudgeResult]:
        model_id = self.settings.effective_judge_model
        model = build_model(model_id, openai_base_url=self.settings.judge_openai_base_url)
        return Agent(
            model=model,
            output_type=build_output_spec(model_id, model, ResumeJudgeResult),
            system_prompt=self.prompt.content,
            model_settings=build_model_settings(
                self.settings,
                model_id,
                reasoning_effort=self.settings.effective_judge_reasoning_effort,
            ),
            retries=0,
            output_retries=1,
        )

    async def judge_many(
        self,
        *,
        jd: str,
        candidates: list[ResumeCandidate],
        cache: JudgeCache,
    ) -> tuple[dict[str, tuple[ResumeJudgeResult, bool, int]], list[tuple[str, str, str, ResumeJudgeResult]]]:
        agent = self._build_agent()
        semaphore = asyncio.Semaphore(self.settings.scoring_max_concurrency)
        jd_hash = sha256(jd.encode("utf-8")).hexdigest()
        results: dict[str, tuple[ResumeJudgeResult, bool, int]] = {}
        pending_cache_writes: list[tuple[str, str, str, ResumeJudgeResult]] = []

        async def worker(candidate: ResumeCandidate) -> None:
            snapshot_hash = candidate.snapshot_sha256 or snapshot_sha256(candidate.raw)
            cached = cache.get(
                jd_sha256_value=jd_hash,
                snapshot_sha256_value=snapshot_hash,
                model_id=self.settings.effective_judge_model,
            )
            if cached is not None:
                results[candidate.resume_id] = (cached, True, 0)
                return
            prompt = "\n\n".join(
                [
                    json_block("JOB_DESCRIPTION", {"jd": jd}),
                    json_block(
                        "RESUME_SNAPSHOT",
                        {
                            "resume_id": candidate.resume_id,
                            "source_resume_id": candidate.source_resume_id,
                            "snapshot_sha256": snapshot_hash,
                            "candidate": candidate.raw,
                        },
                    ),
                ]
            )
            started = perf_counter()
            async with semaphore:
                judged = await agent.run(prompt)
            result = judged.output
            latency_ms = max(1, int((perf_counter() - started) * 1000))
            results[candidate.resume_id] = (result, False, latency_ms)
            pending_cache_writes.append((jd_hash, snapshot_hash, self.settings.effective_judge_model, result))

        await asyncio.gather(*(worker(candidate) for candidate in candidates))
        return results, pending_cache_writes


def persist_raw_resume_snapshot(*, run_dir: Path, candidate: ResumeCandidate) -> Path:
    snapshot_hash = candidate.snapshot_sha256 or snapshot_sha256(candidate.raw)
    path = run_dir / "raw_resumes" / f"{snapshot_hash}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "resume_id": candidate.resume_id,
        "source_resume_id": candidate.source_resume_id,
        "snapshot_sha256": snapshot_hash,
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "candidate": candidate.raw,
    }
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def export_judge_tasks(
    *,
    run_dir: Path,
    stage: Literal["round_01", "final"],
    jd_sha256_value: str,
    candidates: list[ResumeCandidate],
) -> Path:
    path = run_dir / "evaluation" / f"{stage}_judge_tasks.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        JudgeTask(
            stage=stage,
            rank=index,
            jd_sha256=jd_sha256_value,
            resume_id=candidate.resume_id,
            source_resume_id=candidate.source_resume_id,
            snapshot_sha256=candidate.snapshot_sha256 or snapshot_sha256(candidate.raw),
            raw_resume_path=f"raw_resumes/{candidate.snapshot_sha256 or snapshot_sha256(candidate.raw)}.json",
        ).model_dump(mode="json")
        for index, candidate in enumerate(candidates[:TOP_K], start=1)
    ]
    content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    path.write_text(content + ("\n" if content else ""), encoding="utf-8")
    return path


def _dcg(gains: list[int]) -> float:
    total = 0.0
    for index, gain in enumerate(gains[:TOP_K], start=1):
        total += gain / math.log2(index + 1)
    return total


def ndcg_at_10(scores: list[int]) -> float:
    padded = scores[:TOP_K] + [0] * max(0, TOP_K - len(scores))
    ideal = sorted(padded, reverse=True)
    ideal_dcg = _dcg(ideal)
    if ideal_dcg == 0:
        return 0.0
    return _dcg(padded) / ideal_dcg


def precision_at_10(scores: list[int]) -> float:
    padded = scores[:TOP_K] + [0] * max(0, TOP_K - len(scores))
    hits = sum(1 for score in padded if score >= PRECISION_RELEVANCE_THRESHOLD)
    return hits / TOP_K


def _total_score(*, ndcg: float, precision: float) -> float:
    return ndcg * 0.3 + precision * 0.7


def _stage_result(
    *,
    stage: Literal["round_01", "final"],
    candidates: list[ResumeCandidate],
    judged: dict[str, tuple[ResumeJudgeResult, bool, int]],
) -> EvaluationStageResult:
    rows: list[EvaluatedCandidate] = []
    for rank, candidate in enumerate(candidates[:TOP_K], start=1):
        result, cache_hit, _ = judged[candidate.resume_id]
        snapshot_hash = candidate.snapshot_sha256 or snapshot_sha256(candidate.raw)
        rows.append(
            EvaluatedCandidate(
                rank=rank,
                resume_id=candidate.resume_id,
                source_resume_id=candidate.source_resume_id,
                snapshot_sha256=snapshot_hash,
                raw_resume_path=f"raw_resumes/{snapshot_hash}.json",
                expected_job_category=candidate.expected_job_category,
                now_location=candidate.now_location,
                work_year=candidate.work_year,
                judge_score=result.score,
                judge_rationale=result.rationale,
                cache_hit=cache_hit,
            )
        )
    scores = [row.judge_score for row in rows]
    ndcg = ndcg_at_10(scores)
    precision = precision_at_10(scores)
    return EvaluationStageResult(
        stage=stage,
        ndcg_at_10=ndcg,
        precision_at_10=precision,
        total_score=_total_score(ndcg=ndcg, precision=precision),
        candidates=rows,
    )


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        path.unlink(missing_ok=True)


def _log_to_wandb(
    *,
    settings: AppSettings,
    artifact_root: Path,
    evaluation: EvaluationResult,
) -> None:
    if not settings.wandb_project:
        return
    import wandb

    run = wandb.init(
        project=settings.wandb_project,
        entity=settings.wandb_entity or None,
        job_type="resume-eval",
        config={
            "judge_model": evaluation.judge_model,
            "jd_sha256": evaluation.jd_sha256,
            "round_01_ndcg_at_10": evaluation.round_01.ndcg_at_10,
            "round_01_precision_at_10": evaluation.round_01.precision_at_10,
            "round_01_total_score": evaluation.round_01.total_score,
            "final_ndcg_at_10": evaluation.final.ndcg_at_10,
            "final_precision_at_10": evaluation.final.precision_at_10,
            "final_total_score": evaluation.final.total_score,
        },
        name=evaluation.run_id,
    )
    try:
        run.log(
            {
                "round_01_ndcg_at_10": evaluation.round_01.ndcg_at_10,
                "round_01_precision_at_10": evaluation.round_01.precision_at_10,
                "round_01_total_score": evaluation.round_01.total_score,
                "final_ndcg_at_10": evaluation.final.ndcg_at_10,
                "final_precision_at_10": evaluation.final.precision_at_10,
                "final_total_score": evaluation.final.total_score,
            }
        )
        for stage_name, stage in (("round_01", evaluation.round_01), ("final", evaluation.final)):
            table = wandb.Table(
                columns=[
                    "rank",
                    "resume_id",
                    "source_resume_id",
                    "snapshot_sha256",
                    "raw_resume_path",
                    "expected_job_category",
                    "now_location",
                    "work_year",
                    "judge_score",
                    "judge_rationale",
                    "cache_hit",
                ]
            )
            for candidate in stage.candidates:
                table.add_data(
                    candidate.rank,
                    candidate.resume_id,
                    candidate.source_resume_id,
                    candidate.snapshot_sha256,
                    candidate.raw_resume_path,
                    candidate.expected_job_category,
                    candidate.now_location,
                    candidate.work_year,
                    candidate.judge_score,
                    candidate.judge_rationale,
                    candidate.cache_hit,
                )
            run.log({f"{stage_name}_top10": table})

        artifact = wandb.Artifact(f"seektalent-eval-{evaluation.run_id}", type="evaluation")
        artifact.add_file(str(artifact_root / "evaluation" / "evaluation.json"))
        artifact.add_dir(str(artifact_root / "raw_resumes"), name="raw_resumes")
        run.log_artifact(artifact)
    finally:
        run.finish()


@dataclass(frozen=True)
class EvaluationArtifacts:
    result: EvaluationResult
    path: Path


async def evaluate_run(
    *,
    settings: AppSettings,
    prompt: LoadedPrompt,
    run_id: str,
    run_dir: Path,
    jd: str,
    round_01_candidates: list[ResumeCandidate],
    final_candidates: list[ResumeCandidate],
) -> EvaluationArtifacts:
    cache = JudgeCache(settings.project_root)
    temp_root = run_dir / "._evaluation_tmp"
    final_evaluation_dir = run_dir / "evaluation"
    final_raw_dir = run_dir / "raw_resumes"
    try:
        jd_hash = sha256(jd.encode("utf-8")).hexdigest()
        unique_candidates: dict[str, ResumeCandidate] = {}
        for candidate in [*round_01_candidates[:TOP_K], *final_candidates[:TOP_K]]:
            unique_candidates[candidate.resume_id] = candidate

        judged, pending_cache_writes = await ResumeJudge(settings, prompt).judge_many(
            jd=jd,
            candidates=list(unique_candidates.values()),
            cache=cache,
        )
        evaluation = EvaluationResult(
            run_id=run_id,
            judge_model=settings.effective_judge_model,
            jd_sha256=jd_hash,
            round_01=_stage_result(stage="round_01", candidates=round_01_candidates, judged=judged),
            final=_stage_result(stage="final", candidates=final_candidates, judged=judged),
        )
        _remove_path(temp_root)
        export_judge_tasks(run_dir=temp_root, stage="round_01", jd_sha256_value=jd_hash, candidates=round_01_candidates)
        export_judge_tasks(run_dir=temp_root, stage="final", jd_sha256_value=jd_hash, candidates=final_candidates)
        (temp_root / "raw_resumes").mkdir(parents=True, exist_ok=True)
        for candidate in unique_candidates.values():
            persist_raw_resume_snapshot(run_dir=temp_root, candidate=candidate)

        path = temp_root / "evaluation" / "evaluation.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evaluation.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        _log_to_wandb(settings=settings, artifact_root=temp_root, evaluation=evaluation)

        _remove_path(final_evaluation_dir)
        _remove_path(final_raw_dir)
        shutil.move(str(temp_root / "evaluation"), str(final_evaluation_dir))
        shutil.move(str(temp_root / "raw_resumes"), str(final_raw_dir))
        cache.put_many(pending_cache_writes)
        _remove_path(temp_root)
        return EvaluationArtifacts(result=evaluation, path=final_evaluation_dir / "evaluation.json")
    except Exception:
        _remove_path(temp_root)
        _remove_path(final_evaluation_dir)
        _remove_path(final_raw_dir)
        raise
    finally:
        cache.close()
