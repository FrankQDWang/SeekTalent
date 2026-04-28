from __future__ import annotations

import asyncio
import json
import math
import shutil
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from seektalent.artifacts import ArtifactResolver, ArtifactSession
from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, build_output_spec
from seektalent.models import ReplaySnapshot, ResumeCandidate, StopGuidance
from seektalent.prompting import LoadedPrompt, json_block
from seektalent.resources import package_prompt_dir

TOP_K = 10
PRECISION_RELEVANCE_THRESHOLD = 2
WANDB_REPORT_TITLE = "SeekTalent Version Metrics"
REQUIRED_WANDB_SUMMARY_KEYS = (
    "final_total_score",
    "final_precision_at_10",
    "final_ndcg_at_10",
    "round_01_total_score",
    "round_01_precision_at_10",
    "round_01_ndcg_at_10",
)


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


def task_sha256(jd: str, notes: str = "") -> str:
    if not notes.strip():
        return sha256(jd.encode("utf-8")).hexdigest()
    return sha256(canonical_json({"jd": jd, "notes": notes}).encode("utf-8")).hexdigest()


def build_replay_rows(snapshots: Sequence[ReplaySnapshot]) -> list[dict[str, object]]:
    return [
        {
            "run_id": snapshot.run_id,
            "round_no": snapshot.round_no,
            "retrieval_snapshot_id": snapshot.retrieval_snapshot_id,
            "second_lane_query_fingerprint": snapshot.second_lane_query_fingerprint,
            "provider_request": snapshot.provider_request,
            "provider_response_resume_ids": snapshot.provider_response_resume_ids,
            "provider_response_raw_rank": snapshot.provider_response_raw_rank,
            "dedupe_version": snapshot.dedupe_version,
            "scoring_model_version": snapshot.scoring_model_version,
            "query_plan_version": snapshot.query_plan_version,
            "prf_gate_version": snapshot.prf_gate_version,
            "generic_explore_version": snapshot.generic_explore_version,
            "prf_span_model_name": snapshot.prf_span_model_name,
            "prf_span_model_revision": snapshot.prf_span_model_revision,
            "prf_embedding_model_name": snapshot.prf_embedding_model_name,
            "prf_candidate_span_artifact_ref": snapshot.prf_candidate_span_artifact_ref,
            "prf_expression_family_artifact_ref": snapshot.prf_expression_family_artifact_ref,
            "prf_policy_decision_artifact_ref": snapshot.prf_policy_decision_artifact_ref,
        }
        for snapshot in snapshots
    ]


def export_replay_rows(*, run_dir: Path, output_dir: Path | None = None) -> Path | None:
    try:
        resolver = ArtifactResolver.for_root(run_dir)
    except ValueError:
        return None
    snapshots = [
        ReplaySnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        for path in resolver.resolve_many("round.*.retrieval.replay_snapshot")
        if path.exists()
    ]
    if not snapshots:
        return None
    if output_dir is None:
        replay_rows_path = resolver.resolve_for_write("evaluation.replay_rows")
    else:
        replay_rows_path = output_dir / "replay_rows.jsonl"
    replay_rows_path.parent.mkdir(parents=True, exist_ok=True)
    rows = build_replay_rows(snapshots)
    replay_rows_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return replay_rows_path


def render_judge_prompt(
    *,
    jd: str,
    notes: str,
    candidate: ResumeCandidate,
    snapshot_hash: str,
) -> str:
    exact_data = {
        "resume_id": candidate.resume_id,
        "source_resume_id": candidate.source_resume_id,
        "snapshot_sha256": snapshot_hash,
    }
    return "\n\n".join(
        [
            "TASK\nJudge whether this resume matches the job. Return one ResumeJudgeResult.",
            f"JOB DESCRIPTION\n{jd}",
            f"NOTES\n{notes.strip() or '(none)'}",
            (
                "RESUME SNAPSHOT\n"
                f"- Summary: {candidate.compact_summary()}\n"
                f"- Current/expected role: {candidate.expected_job_category or '(unknown)'}\n"
                f"- Location: {candidate.now_location or '(unknown)'}\n"
                f"- Work years: {candidate.work_year if candidate.work_year is not None else '(unknown)'}\n"
                f"- Education: {', '.join(candidate.education_summaries[:3]) or '(none)'}\n"
                f"- Work experience: {'; '.join(candidate.work_experience_summaries[:5]) or '(none)'}\n"
                f"- Projects: {', '.join(candidate.project_names[:5]) or '(none)'}\n"
                f"- Work summaries: {'; '.join(candidate.work_summaries[:5]) or '(none)'}\n"
                f"- Search text: {candidate.search_text}"
            ),
            json_block("EXACT DATA", exact_data),
        ]
    )


def _cache_path(project_root: Path) -> Path:
    return project_root / ".seektalent" / "judge_cache.sqlite3"


def _app_version() -> str:
    try:
        return package_version("seektalent")
    except PackageNotFoundError:
        return "0.6.1"


@dataclass(frozen=True)
class JudgeLabelWrite:
    task_sha256_value: str
    snapshot_sha256_value: str
    judge_model: str
    result: ResumeJudgeResult
    judge_prompt_text: str | None = None


class JudgeCache:
    def __init__(self, project_root: Path, *, path: Path | None = None) -> None:
        self.path = path or _cache_path(project_root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: sqlite3.Connection | None = None
        if self.path.exists():
            self.conn = sqlite3.connect(self.path)
            self.conn.row_factory = sqlite3.Row

    def _ensure_conn(self) -> sqlite3.Connection:
        if self.conn is None:
            self.conn = sqlite3.connect(self.path)
            self.conn.row_factory = sqlite3.Row
        self._ensure_asset_tables(self.conn)
        self.conn.commit()
        return self.conn

    def _ensure_asset_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jd_assets (
                task_sha256 TEXT PRIMARY KEY,
                jd_sha256 TEXT NOT NULL,
                notes_sha256 TEXT NOT NULL,
                job_title TEXT,
                jd_text TEXT NOT NULL,
                notes_text TEXT NOT NULL,
                captured_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resume_assets (
                snapshot_sha256 TEXT PRIMARY KEY,
                raw_json TEXT NOT NULL,
                captured_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS judge_labels (
                task_sha256 TEXT NOT NULL,
                snapshot_sha256 TEXT NOT NULL,
                score INTEGER NOT NULL,
                rationale TEXT NOT NULL,
                judge_model TEXT NOT NULL,
                judge_prompt_text TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (task_sha256, snapshot_sha256)
            )
            """
        )

    def get(self, *, jd_sha256_value: str, snapshot_sha256_value: str, model_id: str) -> ResumeJudgeResult | None:
        del model_id
        return self.get_label(
            task_sha256_value=jd_sha256_value,
            snapshot_sha256_value=snapshot_sha256_value,
        )

    def get_label(self, *, task_sha256_value: str, snapshot_sha256_value: str) -> ResumeJudgeResult | None:
        if self.conn is None and not self.path.exists():
            return None
        row = self.get_label_row(
            task_sha256_value=task_sha256_value,
            snapshot_sha256_value=snapshot_sha256_value,
        )
        if row is None:
            return None
        return ResumeJudgeResult(score=row["score"], rationale=row["rationale"])

    def get_label_row(self, *, task_sha256_value: str, snapshot_sha256_value: str) -> sqlite3.Row | None:
        conn = self._ensure_conn()
        return conn.execute(
            """
            SELECT *
            FROM judge_labels
            WHERE task_sha256 = ? AND snapshot_sha256 = ?
            """,
            (task_sha256_value, snapshot_sha256_value),
        ).fetchone()

    def put(
        self,
        *,
        jd_sha256_value: str,
        snapshot_sha256_value: str,
        model_id: str,
        result: ResumeJudgeResult,
    ) -> None:
        self.put_label(
            task_sha256_value=jd_sha256_value,
            snapshot_sha256_value=snapshot_sha256_value,
            judge_model=model_id,
            result=result,
        )

    def put_label(
        self,
        *,
        task_sha256_value: str,
        snapshot_sha256_value: str,
        judge_model: str,
        result: ResumeJudgeResult,
        judge_prompt_text: str | None = None,
    ) -> None:
        conn = self._ensure_conn()
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        conn.execute(
            """
            INSERT INTO judge_labels (
                task_sha256, snapshot_sha256, score, rationale, judge_model,
                judge_prompt_text, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_sha256, snapshot_sha256) DO UPDATE SET
                score = excluded.score,
                rationale = excluded.rationale,
                judge_model = excluded.judge_model,
                judge_prompt_text = excluded.judge_prompt_text,
                updated_at = excluded.updated_at
            """,
            (
                task_sha256_value,
                snapshot_sha256_value,
                result.score,
                result.rationale,
                judge_model,
                judge_prompt_text,
                now,
                now,
            ),
        )
        conn.commit()

    def put_many(
        self,
        entries: Sequence[JudgeLabelWrite | tuple[str, str, str, ResumeJudgeResult]],
    ) -> None:
        if not entries:
            return
        conn = self._ensure_conn()
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        label_rows = []
        for entry in entries:
            if isinstance(entry, JudgeLabelWrite):
                write = entry
            else:
                task_sha256_value, snapshot_sha256_value, model_id, result = entry
                write = JudgeLabelWrite(
                    task_sha256_value=task_sha256_value,
                    snapshot_sha256_value=snapshot_sha256_value,
                    judge_model=model_id,
                    result=result,
                )
            label_rows.append(
                (
                    write.task_sha256_value,
                    write.snapshot_sha256_value,
                    write.result.score,
                    write.result.rationale,
                    write.judge_model,
                    write.judge_prompt_text,
                    now,
                    now,
                )
            )
        try:
            conn.executemany(
                """
                INSERT INTO judge_labels (
                    task_sha256, snapshot_sha256, score, rationale, judge_model,
                    judge_prompt_text, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_sha256, snapshot_sha256) DO UPDATE SET
                    score = excluded.score,
                    rationale = excluded.rationale,
                    judge_model = excluded.judge_model,
                    judge_prompt_text = excluded.judge_prompt_text,
                    updated_at = excluded.updated_at
                """,
                label_rows,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def upsert_jd_asset(
        self,
        *,
        job_title: str | None,
        jd: str,
        notes: str,
        captured_at: str | None = None,
    ) -> str:
        conn = self._ensure_conn()
        task_hash = task_sha256(jd, notes)
        now = captured_at or datetime.now().astimezone().isoformat(timespec="seconds")
        conn.execute(
            """
            INSERT OR REPLACE INTO jd_assets (
                task_sha256, jd_sha256, notes_sha256, job_title, jd_text, notes_text,
                captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_hash,
                sha256(jd.encode("utf-8")).hexdigest(),
                sha256(notes.encode("utf-8")).hexdigest(),
                job_title,
                jd,
                notes,
                now,
            ),
        )
        conn.commit()
        return task_hash

    def upsert_resume_asset(
        self,
        *,
        snapshot_sha256_value: str,
        raw_payload: dict[str, Any],
        captured_at: str | None = None,
    ) -> None:
        conn = self._ensure_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO resume_assets (
                snapshot_sha256, raw_json, captured_at
            ) VALUES (?, ?, ?)
            """,
            (
                snapshot_sha256_value,
                canonical_json(raw_payload),
                captured_at or datetime.now().astimezone().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None


class AsyncJudgeLimiter:
    def __init__(self, max_concurrency: int) -> None:
        self._semaphore = threading.BoundedSemaphore(max_concurrency)

    async def __aenter__(self) -> "AsyncJudgeLimiter":
        while not self._semaphore.acquire(blocking=False):
            await asyncio.sleep(0.001)
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._semaphore.release()


class ResumeJudge:
    def __init__(self, settings: AppSettings, prompt: LoadedPrompt) -> None:
        self.settings = settings
        self.prompt = prompt

    def _build_agent(self) -> Agent[None, ResumeJudgeResult]:
        model_id = self.settings.effective_judge_model
        model = build_model(
            model_id,
            openai_base_url=self.settings.judge_openai_base_url,
            openai_api_key=self.settings.judge_openai_api_key,
        )
        return cast(Agent[None, ResumeJudgeResult], Agent(
            model=model,
            output_type=build_output_spec(model_id, model, ResumeJudgeResult),
            system_prompt=self.prompt.content,
            model_settings=build_model_settings(
                self.settings,
                model_id,
                reasoning_effort=self.settings.effective_judge_reasoning_effort,
            ),
            retries=0,
            output_retries=2,
        ))

    async def judge_many(
        self,
        *,
        jd: str,
        notes: str = "",
        candidates: list[ResumeCandidate],
        cache: JudgeCache,
        judge_limiter: AsyncJudgeLimiter | None = None,
    ) -> tuple[dict[str, tuple[ResumeJudgeResult, bool, int]], list[JudgeLabelWrite]]:
        limiter = judge_limiter or AsyncJudgeLimiter(self.settings.judge_max_concurrency)
        task_hash = task_sha256(jd, notes)
        results: dict[str, tuple[ResumeJudgeResult, bool, int]] = {}
        pending_cache_writes: list[JudgeLabelWrite] = []
        pending_candidates_by_snapshot: dict[str, list[ResumeCandidate]] = {}

        for candidate in candidates:
            snapshot_hash = candidate.snapshot_sha256 or snapshot_sha256(candidate.raw)
            cached = cache.get_label(
                task_sha256_value=task_hash,
                snapshot_sha256_value=snapshot_hash,
            )
            if cached is not None:
                results[candidate.resume_id] = (cached, True, 0)
            else:
                pending_candidates_by_snapshot.setdefault(snapshot_hash, []).append(candidate)

        if not pending_candidates_by_snapshot:
            return results, pending_cache_writes

        agent = self._build_agent()

        async def worker(candidate: ResumeCandidate, snapshot_hash: str, aliases: list[ResumeCandidate]) -> None:
            prompt = render_judge_prompt(
                jd=jd,
                notes=notes,
                candidate=candidate,
                snapshot_hash=snapshot_hash,
            )
            started = perf_counter()
            async with limiter:
                judged = await agent.run(prompt)
            result = judged.output
            latency_ms = max(1, int((perf_counter() - started) * 1000))
            for alias in aliases:
                results[alias.resume_id] = (result, False, latency_ms)
            pending_cache_writes.append(
                JudgeLabelWrite(
                    task_sha256_value=task_hash,
                    snapshot_sha256_value=snapshot_hash,
                    judge_model=self.settings.effective_judge_model,
                    result=result,
                    judge_prompt_text=self.prompt.content,
                )
            )

        await asyncio.gather(
            *(
                worker(candidates_with_snapshot[0], snapshot_hash, candidates_with_snapshot)
                for snapshot_hash, candidates_with_snapshot in pending_candidates_by_snapshot.items()
            )
        )
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


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _artifact_session_for_run(run_dir: Path) -> ArtifactSession | None:
    try:
        resolver = ArtifactResolver.for_root(run_dir)
    except ValueError:
        return None
    return ArtifactSession(root=run_dir, manifest=resolver.manifest)


def _register_evaluation_outputs(run_dir: Path, evaluation: EvaluationResult) -> None:
    session = _artifact_session_for_run(run_dir)
    if session is None:
        return

    session.write_json("evaluation.evaluation", evaluation.model_dump(mode="json"))

    replay_rows_path = run_dir / "evaluation" / "replay_rows.jsonl"
    if replay_rows_path.exists():
        session.write_jsonl("evaluation.replay_rows", _jsonl_rows(replay_rows_path))

    for stage in ("round_01", "final"):
        logical_name = f"evaluation.{stage}_judge_tasks"
        relative_path = f"evaluation/{stage}_judge_tasks.jsonl"
        judge_tasks_path = run_dir / relative_path
        if not judge_tasks_path.exists():
            continue
        session.register_path(
            logical_name,
            relative_path,
            content_type="application/x-ndjson",
            schema_version="v1",
        )
        session.write_jsonl(logical_name, _jsonl_rows(judge_tasks_path))

    raw_resumes_dir = run_dir / "raw_resumes"
    if not raw_resumes_dir.exists():
        return

    session.register_path(
        "evaluation.raw_resumes",
        "raw_resumes",
        content_type="application/x-directory",
        collection=True,
    )
    for raw_resume_path in sorted(raw_resumes_dir.glob("*.json")):
        logical_name = f"evaluation.raw_resumes.{raw_resume_path.stem}"
        relative_path = str(raw_resume_path.relative_to(run_dir))
        session.register_path(
            logical_name,
            relative_path,
            content_type="application/json",
            schema_version="v1",
        )
        session.write_json(logical_name, json.loads(raw_resume_path.read_text(encoding="utf-8")))


def _dcg(gains: list[int]) -> float:
    total = 0.0
    for index, gain in enumerate(gains[:TOP_K], start=1):
        total += gain / math.log2(index + 1)
    return total


def ndcg_at_10(scores: Sequence[int]) -> float:
    padded = list(scores[:TOP_K]) + [0] * max(0, TOP_K - len(scores))
    ideal = [3] * TOP_K
    ideal_dcg = _dcg(ideal)
    if ideal_dcg == 0:
        return 0.0
    return _dcg(padded) / ideal_dcg


def precision_at_10(scores: Sequence[int]) -> float:
    padded = list(scores[:TOP_K]) + [0] * max(0, TOP_K - len(scores))
    hits = sum(1 for score in padded if score >= PRECISION_RELEVANCE_THRESHOLD)
    return hits / TOP_K


def _total_score(*, ndcg: float, precision: float) -> float:
    return ndcg * 0.3 + precision * 0.7


def _judge_cache_summary(evaluation: EvaluationResult) -> dict[str, int | float]:
    cache_hits_by_snapshot: dict[str, bool] = {}
    for candidate in [*evaluation.round_01.candidates, *evaluation.final.candidates]:
        cache_hits_by_snapshot[candidate.snapshot_sha256] = (
            cache_hits_by_snapshot.get(candidate.snapshot_sha256, False) or candidate.cache_hit
        )
    candidate_count = len(cache_hits_by_snapshot)
    hit_count = sum(1 for cache_hit in cache_hits_by_snapshot.values() if cache_hit)
    return {
        "judge_candidate_count": candidate_count,
        "judge_cache_hit_count": hit_count,
        "judge_cache_hit_rate_pct": (hit_count / candidate_count * 100) if candidate_count else 0.0,
    }


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


def _weave_project_name(settings: AppSettings) -> str | None:
    if not settings.weave_project:
        return None
    if settings.effective_weave_entity:
        return f"{settings.effective_weave_entity}/{settings.weave_project}"
    return settings.weave_project


def _weave_summary_markdown(
    *,
    evaluation: EvaluationResult,
    stage: EvaluationStageResult,
    seektalent_version: str,
) -> str:
    return "\n".join(
        [
            "# SeekTalent Eval Summary",
            "",
            f"- Run ID: `{evaluation.run_id}`",
            f"- Stage: `{stage.stage}`",
            f"- SeekTalent version: `{seektalent_version}`",
            f"- Judge model: `{evaluation.judge_model}`",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| ndcg@10 | {stage.ndcg_at_10:.4f} |",
            f"| precision@10 | {stage.precision_at_10:.4f} |",
            f"| total_score | {stage.total_score:.4f} |",
        ]
    )


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _run_version(run: dict[str, Any]) -> str:
    return str(run.get("version") or run.get("seektalent_version") or "")


def _run_rounds(run: dict[str, Any]) -> int | None:
    rounds = run.get("rounds_executed")
    return int(rounds) if rounds is not None else None


def _successful_eval_run_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run in runs:
        if run.get("state") != "finished" or not run.get("eval_enabled"):
            continue
        version = _run_version(run)
        if not version:
            continue
        if any(run.get(key) is None for key in REQUIRED_WANDB_SUMMARY_KEYS):
            continue
        rows.append(run)
    return rows


def _latest_runs_by_version_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_by_version: dict[str, dict[str, Any]] = {}
    for run in _successful_eval_run_rows(runs):
        version = _run_version(run)
        current = latest_by_version.get(version)
        if current is None or _parse_timestamp(str(run["created_at"])) > _parse_timestamp(str(current["created_at"])):
            latest_by_version[version] = run
    return [latest_by_version[version] for version in sorted(latest_by_version.keys(), reverse=True)]


def _best_runs_by_version_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_version: dict[str, dict[str, Any]] = {}
    for run in _successful_eval_run_rows(runs):
        version = _run_version(run)
        current = best_by_version.get(version)
        if current is None:
            best_by_version[version] = run
            continue
        current_score = float(current["final_total_score"] or 0)
        candidate_score = float(run["final_total_score"] or 0)
        if candidate_score > current_score:
            best_by_version[version] = run
            continue
        if candidate_score == current_score and _parse_timestamp(str(run["created_at"])) > _parse_timestamp(
            str(current["created_at"])
        ):
            best_by_version[version] = run
    return [best_by_version[version] for version in sorted(best_by_version.keys(), reverse=True)]


def _worst_runs_by_version_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    worst_by_version: dict[str, dict[str, Any]] = {}
    for run in _successful_eval_run_rows(runs):
        version = _run_version(run)
        current = worst_by_version.get(version)
        if current is None:
            worst_by_version[version] = run
            continue
        current_score = float(current["final_total_score"] or 0)
        candidate_score = float(run["final_total_score"] or 0)
        if candidate_score < current_score:
            worst_by_version[version] = run
            continue
        if candidate_score == current_score and _parse_timestamp(str(run["created_at"])) > _parse_timestamp(
            str(current["created_at"])
        ):
            worst_by_version[version] = run
    return [worst_by_version[version] for version in sorted(worst_by_version.keys(), reverse=True)]


def _version_means_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for run in _successful_eval_run_rows(runs):
        grouped.setdefault(_run_version(run), []).append(run)

    rows: list[dict[str, Any]] = []
    for version in sorted(grouped.keys(), reverse=True):
        bucket = grouped[version]
        rounds = [_run_rounds(run) for run in bucket]
        known_rounds = [value for value in rounds if value is not None]
        judge_candidate_count = sum(int(run.get("judge_candidate_count") or 0) for run in bucket)
        judge_cache_hit_count = sum(int(run.get("judge_cache_hit_count") or 0) for run in bucket)
        rows.append(
            {
                "version": version,
                "run_count": len(bucket),
                "avg_rounds": (sum(known_rounds) / len(known_rounds)) if known_rounds else None,
                "final_total_mean": sum(float(run["final_total_score"]) for run in bucket) / len(bucket),
                "final_precision_mean": sum(float(run["final_precision_at_10"]) for run in bucket) / len(bucket),
                "final_ndcg_mean": sum(float(run["final_ndcg_at_10"]) for run in bucket) / len(bucket),
                "round1_total_mean": sum(float(run["round_01_total_score"]) for run in bucket) / len(bucket),
                "round1_precision_mean": sum(float(run["round_01_precision_at_10"]) for run in bucket) / len(bucket),
                "round1_ndcg_mean": sum(float(run["round_01_ndcg_at_10"]) for run in bucket) / len(bucket),
                "judge_cache_reuse_pct": (
                    judge_cache_hit_count / judge_candidate_count * 100 if judge_candidate_count else 0.0
                ),
            }
        )
    return rows


def _report_run_rows(*, entity: str, project: str) -> list[dict[str, Any]]:
    import wandb

    api = wandb.Api()
    return [
        {
            "run_name": run.name,
            "run_url": run.url,
            "created_at": run.created_at,
            "state": run.state,
            "eval_enabled": bool(run.config.get("eval_enabled")),
            "version": run.config.get("version"),
            "seektalent_version": run.config.get("seektalent_version"),
            "judge_model": run.config.get("judge_model"),
            "rounds_executed": run.summary.get("rounds_executed"),
            "final_total_score": run.summary.get("final_total_score"),
            "final_precision_at_10": run.summary.get("final_precision_at_10"),
            "final_ndcg_at_10": run.summary.get("final_ndcg_at_10"),
            "round_01_total_score": run.summary.get("round_01_total_score"),
            "round_01_precision_at_10": run.summary.get("round_01_precision_at_10"),
            "round_01_ndcg_at_10": run.summary.get("round_01_ndcg_at_10"),
            "judge_candidate_count": run.summary.get("judge_candidate_count"),
            "judge_cache_hit_count": run.summary.get("judge_cache_hit_count"),
            "judge_cache_hit_rate_pct": run.summary.get("judge_cache_hit_rate_pct"),
        }
        for run in api.runs(f"{entity}/{project}")
    ]


def _merge_report_rows(
    rows: list[dict[str, Any]], extra_rows: Sequence[dict[str, Any]] = ()
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in [*extra_rows, *rows]:
        key = str(row.get("run_name") or row.get("run_url") or len(merged))
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
    return merged


def _rounds_cell(run: dict[str, Any]) -> str:
    rounds = _run_rounds(run)
    return "" if rounds is None else str(rounds)


def _version_runs_markdown(
    *, entity: str, project: str, heading: str, extra_rows: Sequence[dict[str, Any]] = ()
) -> str:
    runs = _merge_report_rows(_report_run_rows(entity=entity, project=project), extra_rows)
    if heading == "latest":
        rows = _latest_runs_by_version_rows(runs)
        run_label = "Latest run"
    elif heading == "best":
        rows = _best_runs_by_version_rows(runs)
        run_label = "Best run"
    else:
        rows = _worst_runs_by_version_rows(runs)
        run_label = "Worst run"
    if not rows:
        return "No successful eval-enabled runs yet."

    lines = [
        f"| Version | {run_label} | Created | Rounds | Judge model | Final total | Final p@10 | Final ndcg@10 | Round1 total | Round1 p@10 | Round1 ndcg@10 |",
        "| --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _run_version(row),
                    f"[{row['run_name']}]({row['run_url']})",
                    str(row["created_at"]),
                    _rounds_cell(row),
                    str(row["judge_model"]),
                    f"{float(row['final_total_score'] or 0):.4f}",
                    f"{float(row['final_precision_at_10'] or 0):.4f}",
                    f"{float(row['final_ndcg_at_10'] or 0):.4f}",
                    f"{float(row['round_01_total_score'] or 0):.4f}",
                    f"{float(row['round_01_precision_at_10'] or 0):.4f}",
                    f"{float(row['round_01_ndcg_at_10'] or 0):.4f}",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _version_means_summary_markdown(
    *, entity: str, project: str, extra_rows: Sequence[dict[str, Any]] = ()
) -> str:
    rows = _version_means_rows(
        _merge_report_rows(_report_run_rows(entity=entity, project=project), extra_rows)
    )
    if not rows:
        return "No successful eval-enabled runs yet."
    lines = [
        "| Version | Run count | Avg rounds | Final total mean | Final p@10 mean | Final ndcg@10 mean | Round1 total mean | Round1 p@10 mean | Round1 ndcg@10 mean | Judge cache reuse % |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["version"]),
                    str(row["run_count"]),
                    "" if row["avg_rounds"] is None else f"{row['avg_rounds']:.2f}",
                    f"{row['final_total_mean']:.4f}",
                    f"{row['final_precision_mean']:.4f}",
                    f"{row['final_ndcg_mean']:.4f}",
                    f"{row['round1_total_mean']:.4f}",
                    f"{row['round1_precision_mean']:.4f}",
                    f"{row['round1_ndcg_mean']:.4f}",
                    f"{row['judge_cache_reuse_pct']:.2f}%",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _log_to_weave(
    *,
    settings: AppSettings,
    evaluation: EvaluationResult,
) -> None:
    project_name = _weave_project_name(settings)
    if project_name is None:
        return

    import weave

    weave.init(project_name)
    seektalent_version = _app_version()
    model = {
        "name": "seektalent",
        "version": seektalent_version,
        "judge_model": evaluation.judge_model,
    }
    for stage in (evaluation.round_01, evaluation.final):
        if not stage.candidates:
            continue
        logger = weave.EvaluationLogger(
            name=f"seektalent-{stage.stage}",
            model=model,
            dataset=[
                {
                    "rank": candidate.rank,
                    "resume_id": candidate.resume_id,
                    "source_resume_id": candidate.source_resume_id,
                    "snapshot_sha256": candidate.snapshot_sha256,
                }
                for candidate in stage.candidates
            ],
            eval_attributes={
                "run_id": evaluation.run_id,
                "stage": stage.stage,
                "jd_sha256": evaluation.jd_sha256,
                "seektalent_version": seektalent_version,
                "judge_model": evaluation.judge_model,
            },
            scorers=["judge_score", "is_relevant"],
        )
        for candidate in stage.candidates:
            prediction = logger.log_prediction(
                inputs={
                    "rank": candidate.rank,
                    "resume_id": candidate.resume_id,
                    "source_resume_id": candidate.source_resume_id,
                    "snapshot_sha256": candidate.snapshot_sha256,
                    "raw_resume_path": candidate.raw_resume_path,
                },
                output={
                    "judge_score": candidate.judge_score,
                    "judge_rationale": candidate.judge_rationale,
                },
            )
            prediction.log_score("judge_score", candidate.judge_score)
            prediction.log_score("is_relevant", int(candidate.judge_score >= PRECISION_RELEVANCE_THRESHOLD))
            prediction.finish()
        logger.set_view(
            "summary",
            _weave_summary_markdown(
                evaluation=evaluation,
                stage=stage,
                seektalent_version=seektalent_version,
            ),
            extension="md",
        )
        logger.log_summary(
            {
                "ndcg_at_10": stage.ndcg_at_10,
                "precision_at_10": stage.precision_at_10,
                "total_score": stage.total_score,
            },
            auto_summarize=False,
        )

def _wandb_report_blocks(*, entity: str, project: str, extra_rows: Sequence[dict[str, Any]] = ()) -> list[Any]:
    from wandb_workspaces.reports.v2 import BarPlot, H1, H2, MarkdownBlock, P, PanelGrid, Runset
    from wandb_workspaces.reports.v2.interface import expr

    runset_filters = [
        expr.Metric("State") == "finished",
        expr.Config("eval_enabled") == True,  # noqa: E712
        *(expr.Summary(key) >= 0 for key in REQUIRED_WANDB_SUMMARY_KEYS),
    ]
    runsets = [
        Runset(
            entity=entity,
            project=project,
            name="All versions",
            filters=runset_filters,
        )
    ]

    def metric_panel(stage: str, metric_key: str, title: str) -> BarPlot:
        return BarPlot(
            title=title,
            metrics=[f"{stage}_{metric_key}"],
            groupby="config.version",
            groupby_aggfunc="mean",
            orientation="v",
            title_x="SeekTalent version",
            title_y="Mean",
        )

    return [
        H1(WANDB_REPORT_TITLE),
        P(
            "This report compares successful eval-enabled SeekTalent runs by version. "
            "Eval-off smoke tests are excluded. Each bar uses the same finished-run filter as the summary tables."
        ),
        H2("Latest Runs By Version"),
        MarkdownBlock(text=_version_runs_markdown(entity=entity, project=project, heading="latest", extra_rows=extra_rows)),
        H2("Best Runs By Version"),
        MarkdownBlock(text=_version_runs_markdown(entity=entity, project=project, heading="best", extra_rows=extra_rows)),
        H2("Worst Runs By Version"),
        MarkdownBlock(text=_version_runs_markdown(entity=entity, project=project, heading="worst", extra_rows=extra_rows)),
        H2("Version Means"),
        MarkdownBlock(text=_version_means_summary_markdown(entity=entity, project=project, extra_rows=extra_rows)),
        H2("Final Metrics"),
        PanelGrid(
            runsets=runsets,
            panels=[
                metric_panel("final", "total_score", "Final total_score"),
                metric_panel("final", "precision_at_10", "Final precision@10"),
                metric_panel("final", "ndcg_at_10", "Final ndcg@10"),
            ],
        ),
        H2("Round 1 Metrics"),
        PanelGrid(
            runsets=runsets,
            panels=[
                metric_panel("round_01", "total_score", "Round 1 total_score"),
                metric_panel("round_01", "precision_at_10", "Round 1 precision@10"),
                metric_panel("round_01", "ndcg_at_10", "Round 1 ndcg@10"),
            ],
        ),
    ]


def _upsert_wandb_report(settings: AppSettings, extra_rows: Sequence[dict[str, Any]] = ()) -> None:
    if not settings.wandb_entity or not settings.wandb_project:
        return
    import wandb
    from wandb_workspaces.reports.v2 import Report

    blocks = _wandb_report_blocks(entity=settings.wandb_entity, project=settings.wandb_project, extra_rows=extra_rows)
    api = wandb.Api()
    reports = list(api.reports(f"{settings.wandb_entity}/{settings.wandb_project}", per_page=100))
    matches = [
        report
        for report in reports
        if getattr(report, "display_name", None) == WANDB_REPORT_TITLE
        or getattr(report, "title", None) == WANDB_REPORT_TITLE
    ]
    existing = matches[0] if matches else None
    existing_url = getattr(existing, "url", None) if existing is not None else None
    if existing_url is None:
        report = Report(
            project=settings.wandb_project,
            entity=settings.wandb_entity,
            title=WANDB_REPORT_TITLE,
            description="Version-level SeekTalent eval metrics.",
            blocks=blocks,
            width="fluid",
        )
    else:
        report = Report.from_url(existing_url)
        report.title = WANDB_REPORT_TITLE
        report.description = "Version-level SeekTalent eval metrics."
        report.blocks = blocks
        report.width = "fluid"
    report.save()
    saved_url = getattr(report, "url", None)
    for duplicate in matches[1:]:
        duplicate_url = getattr(duplicate, "url", None)
        if duplicate_url is None or duplicate_url == saved_url:
            continue
        Report.from_url(duplicate_url).delete()


def _terminal_stop_guidance_summary(terminal_stop_guidance: StopGuidance | None) -> dict[str, object]:
    return {
        "terminal_quality_gate_status": (
            terminal_stop_guidance.quality_gate_status if terminal_stop_guidance is not None else None
        ),
        "terminal_top_pool_strength": (
            terminal_stop_guidance.top_pool_strength if terminal_stop_guidance is not None else None
        ),
        "terminal_strong_fit_count": (
            terminal_stop_guidance.strong_fit_count if terminal_stop_guidance is not None else None
        ),
        "terminal_broadening_attempted": (
            terminal_stop_guidance.broadening_attempted if terminal_stop_guidance is not None else None
        ),
    }


def _log_to_wandb(
    *,
    settings: AppSettings,
    artifact_root: Path,
    evaluation: EvaluationResult,
    rounds_executed: int,
    terminal_stop_guidance: StopGuidance | None = None,
    update_report: bool = True,
) -> dict[str, Any] | None:
    if not settings.wandb_project:
        return None
    import wandb

    run = wandb.init(
        project=settings.wandb_project,
        entity=settings.wandb_entity or None,
        job_type="resume-eval",
        config={
            "version": _app_version(),
            "seektalent_version": _app_version(),
            "eval_enabled": True,
            "judge_model": evaluation.judge_model,
            "jd_sha256": evaluation.jd_sha256,
        },
        name=evaluation.run_id,
    )
    seektalent_version = _app_version()
    cache_summary = _judge_cache_summary(evaluation)
    terminal_summary = _terminal_stop_guidance_summary(terminal_stop_guidance)
    report_row = {
        "run_name": evaluation.run_id,
        "run_url": run.url,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "state": "finished",
        "eval_enabled": True,
        "version": seektalent_version,
        "seektalent_version": seektalent_version,
        "judge_model": evaluation.judge_model,
        "rounds_executed": rounds_executed,
        "final_total_score": evaluation.final.total_score,
        "final_precision_at_10": evaluation.final.precision_at_10,
        "final_ndcg_at_10": evaluation.final.ndcg_at_10,
        "round_01_total_score": evaluation.round_01.total_score,
        "round_01_precision_at_10": evaluation.round_01.precision_at_10,
        "round_01_ndcg_at_10": evaluation.round_01.ndcg_at_10,
        **cache_summary,
        **terminal_summary,
    }
    try:
        run.log(
            {
                "round_01_ndcg_at_10": evaluation.round_01.ndcg_at_10,
                "round_01_precision_at_10": evaluation.round_01.precision_at_10,
                "round_01_total_score": evaluation.round_01.total_score,
                "final_ndcg_at_10": evaluation.final.ndcg_at_10,
                "final_precision_at_10": evaluation.final.precision_at_10,
                "final_total_score": evaluation.final.total_score,
                "rounds_executed": rounds_executed,
                **cache_summary,
                **terminal_summary,
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
    if update_report:
        _upsert_wandb_report(settings, extra_rows=[report_row])
    return report_row


def log_evaluation_remotely(
    *,
    settings: AppSettings,
    artifact_root: Path,
    evaluation: EvaluationResult,
    rounds_executed: int,
    terminal_stop_guidance: StopGuidance | None = None,
    update_report: bool = True,
) -> dict[str, Any] | None:
    _log_to_weave(settings=settings, evaluation=evaluation)
    return _log_to_wandb(
        settings=settings,
        artifact_root=artifact_root,
        evaluation=evaluation,
        rounds_executed=rounds_executed,
        terminal_stop_guidance=terminal_stop_guidance,
        update_report=update_report,
    )


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
    notes: str = "",
    round_01_candidates: list[ResumeCandidate],
    final_candidates: list[ResumeCandidate],
    rounds_executed: int,
    terminal_stop_guidance: StopGuidance | None = None,
    judge_limiter: AsyncJudgeLimiter | None = None,
    log_remote: bool = True,
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
        input_truth = _load_input_truth_for_run(run_dir)
        job_title = input_truth.get("job_title") if input_truth else None
        cache.upsert_jd_asset(
            job_title=job_title,
            jd=jd,
            notes=notes,
        )
        for candidate in unique_candidates.values():
            snapshot_hash = candidate.snapshot_sha256 or snapshot_sha256(candidate.raw)
            cache.upsert_resume_asset(
                snapshot_sha256_value=snapshot_hash,
                raw_payload=candidate.raw,
            )

        judged, pending_cache_writes = await ResumeJudge(settings, prompt).judge_many(
            jd=jd,
            notes=notes,
            candidates=list(unique_candidates.values()),
            cache=cache,
            judge_limiter=judge_limiter,
        )
        cache.put_many(pending_cache_writes)
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
        export_replay_rows(run_dir=run_dir, output_dir=temp_root / "evaluation")
        if log_remote:
            log_evaluation_remotely(
                settings=settings,
                artifact_root=temp_root,
                evaluation=evaluation,
                rounds_executed=rounds_executed,
                terminal_stop_guidance=terminal_stop_guidance,
            )

        _remove_path(final_evaluation_dir)
        _remove_path(final_raw_dir)
        shutil.move(str(temp_root / "evaluation"), str(final_evaluation_dir))
        shutil.move(str(temp_root / "raw_resumes"), str(final_raw_dir))
        _register_evaluation_outputs(run_dir, evaluation)
        _remove_path(temp_root)
        return EvaluationArtifacts(result=evaluation, path=final_evaluation_dir / "evaluation.json")
    except Exception:
        _remove_path(temp_root)
        _remove_path(final_evaluation_dir)
        _remove_path(final_raw_dir)
        raise
    finally:
        cache.close()


def _judge_prompt_text_for_run(run_dir: Path) -> str:
    prompt_snapshot = run_dir / "prompt_snapshots" / "judge.md"
    if prompt_snapshot.exists():
        return prompt_snapshot.read_text(encoding="utf-8")
    return (package_prompt_dir() / "judge.md").read_text(encoding="utf-8")


def _input_truth_path_for_run(run_dir: Path) -> Path | None:
    current_path = run_dir / "input" / "input_truth.json"
    if current_path.exists():
        return current_path
    legacy_path = run_dir / "input_truth.json"
    if legacy_path.exists():
        return legacy_path
    return None


def _load_input_truth_for_run(run_dir: Path) -> dict[str, Any] | None:
    path = _input_truth_path_for_run(run_dir)
    if path is None:
        return None
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _validate_clean_judge_asset_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        expected_tables = {"jd_assets", "resume_assets", "judge_labels"}
        if tables != expected_tables:
            raise RuntimeError(f"Unexpected judge asset tables: {sorted(tables)}")
        expected_columns = {
            "jd_assets": {
                "task_sha256",
                "jd_sha256",
                "notes_sha256",
                "job_title",
                "jd_text",
                "notes_text",
                "captured_at",
            },
            "resume_assets": {"snapshot_sha256", "raw_json", "captured_at"},
            "judge_labels": {
                "task_sha256",
                "snapshot_sha256",
                "score",
                "rationale",
                "judge_model",
                "judge_prompt_text",
                "created_at",
                "updated_at",
            },
        }
        for table, columns in expected_columns.items():
            actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if actual != columns:
                raise RuntimeError(f"Unexpected columns for {table}: {sorted(actual)}")
    finally:
        conn.close()


def migrate_judge_assets(*, project_root: Path, runs_dir: Path) -> dict[str, object]:
    db_path = _cache_path(project_root)
    temp_path = db_path.with_name(f".{db_path.name}.tmp")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.unlink(missing_ok=True)
    cache = JudgeCache(project_root, path=temp_path)
    report: dict[str, object] = {
        "runs_scanned": 0,
        "jd_assets_upserted": 0,
        "resume_assets_upserted": 0,
        "judge_labels_upserted": 0,
        "conflicts": [],
        "missing_raw_resumes": [],
    }
    seen_labels: dict[tuple[str, str], dict[str, object]] = {}
    try:
        run_dirs = sorted(
            {
                path.parent.parent
                for path in runs_dir.rglob("evaluation/evaluation.json")
            },
            key=lambda path: str(path),
        )
        for run_dir in run_dirs:
            input_truth = _load_input_truth_for_run(run_dir)
            if input_truth is None:
                continue
            evaluation = json.loads((run_dir / "evaluation" / "evaluation.json").read_text(encoding="utf-8"))
            job_title = input_truth.get("job_title")
            jd = input_truth.get("jd") or ""
            notes = input_truth.get("notes") or ""
            run_id = evaluation.get("run_id") or run_dir.name
            judge_model = evaluation.get("judge_model") or ""
            judge_prompt_text = _judge_prompt_text_for_run(run_dir)
            task_hash = cache.upsert_jd_asset(
                job_title=job_title,
                jd=jd,
                notes=notes,
            )
            report["runs_scanned"] = int(report["runs_scanned"]) + 1
            report["jd_assets_upserted"] = int(report["jd_assets_upserted"]) + 1

            raw_paths: dict[str, Path] = {}
            for raw_path in (run_dir / "raw_resumes").glob("*.json"):
                envelope = json.loads(raw_path.read_text(encoding="utf-8"))
                snapshot_hash = envelope.get("snapshot_sha256") or raw_path.stem
                raw_paths[snapshot_hash] = raw_path
                cache.upsert_resume_asset(
                    snapshot_sha256_value=snapshot_hash,
                    raw_payload=envelope.get("candidate", envelope),
                    captured_at=envelope.get("captured_at"),
                )
                report["resume_assets_upserted"] = int(report["resume_assets_upserted"]) + 1

            for stage in ("round_01", "final"):
                for candidate in evaluation.get(stage, {}).get("candidates", []):
                    snapshot_hash = candidate["snapshot_sha256"]
                    if snapshot_hash not in raw_paths:
                        cast(list[dict[str, object]], report["missing_raw_resumes"]).append(
                            {"run_dir": str(run_dir), "stage": stage, "snapshot_sha256": snapshot_hash}
                        )
                        continue
                    key = (task_hash, snapshot_hash)
                    existing = seen_labels.get(key)
                    if existing is not None and (
                        existing["score"] != candidate["judge_score"]
                        or existing["rationale"] != candidate["judge_rationale"]
                    ):
                        cast(list[dict[str, object]], report["conflicts"]).append(
                            {
                                "task_sha256": task_hash,
                                "snapshot_sha256": snapshot_hash,
                                "previous_run_id": existing["run_id"],
                                "selected_run_id": run_id,
                            }
                        )
                    cache.put_label(
                        task_sha256_value=task_hash,
                        snapshot_sha256_value=snapshot_hash,
                        judge_model=judge_model,
                        result=ResumeJudgeResult(
                            score=candidate["judge_score"],
                            rationale=candidate["judge_rationale"],
                        ),
                        judge_prompt_text=judge_prompt_text,
                    )
                    seen_labels[key] = {
                        "score": candidate["judge_score"],
                        "rationale": candidate["judge_rationale"],
                        "run_id": run_id,
                    }
                    report["judge_labels_upserted"] = int(report["judge_labels_upserted"]) + 1

        cache.close()
        _validate_clean_judge_asset_db(temp_path)
        temp_path.replace(db_path)
        return report
    except Exception:
        cache.close()
        temp_path.unlink(missing_ok=True)
        raise
