from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any

from seektalent.evaluation import TOP_K
from seektalent.models import (
    NormalizedResume,
    PoolDecision,
    ResumeCandidate,
    RunState,
    RuntimeConstraint,
    ScoredCandidate,
    scored_candidate_sort_key,
)
from seektalent.normalization import normalize_resume
from seektalent.runtime.runtime_diagnostics import slim_top_pool_snapshot
from seektalent.runtime.scoring_context import build_scoring_context
from seektalent.tracing import RunTracer, json_char_count, json_sha256


async def score_round(
    *,
    round_no: int,
    new_candidates: list[ResumeCandidate],
    run_state: RunState,
    tracer: RunTracer,
    runtime_only_constraints: list[RuntimeConstraint],
    resume_scorer: Any,
    format_scoring_failure_message: Callable[[Collection[object]], str],
    run_stage_error: Callable[[str, str], Exception],
) -> tuple[list[ScoredCandidate], list[PoolDecision], list[ScoredCandidate]]:
    scoring_pool = build_scoring_pool(
        new_candidates=new_candidates,
        scorecards_by_resume_id=run_state.scorecards_by_resume_id,
    )
    normalized_scoring_pool = normalize_scoring_pool(
        round_no=round_no,
        scoring_pool=scoring_pool,
        tracer=tracer,
        normalized_store=run_state.normalized_store,
    )
    tracer.write_jsonl(
        f"round.{round_no:02d}.scoring.scoring_input_refs",
        [scoring_input_ref(item) for item in normalized_scoring_pool],
    )
    scoring_contexts = [
        build_scoring_context(
            run_state=run_state,
            round_no=round_no,
            normalized_resume=item,
            runtime_only_constraints=runtime_only_constraints,
        )
        for item in normalized_scoring_pool
    ]
    previous_top_ids = set(run_state.top_pool_ids)
    if scoring_contexts:
        scored_candidates, scoring_failures = await resume_scorer.score_candidates_parallel(
            contexts=scoring_contexts,
            tracer=tracer,
        )
        if scoring_failures:
            raise run_stage_error("scoring", format_scoring_failure_message(scoring_failures))
        for candidate in scored_candidates:
            if candidate.resume_id not in run_state.scorecards_by_resume_id:
                run_state.scorecards_by_resume_id[candidate.resume_id] = candidate
    else:
        scored_candidates = []
    global_ranked_candidates = sorted(run_state.scorecards_by_resume_id.values(), key=scored_candidate_sort_key)
    current_top_candidates = global_ranked_candidates[:TOP_K]
    run_state.top_pool_ids = [item.resume_id for item in current_top_candidates]
    pool_decisions = build_pool_decisions(
        round_no=round_no,
        top_candidates=current_top_candidates,
        previous_top_ids=previous_top_ids,
    )
    tracer.write_jsonl(
        f"round.{round_no:02d}.scoring.scorecards",
        [item.model_dump(mode="json") for item in scored_candidates],
    )
    tracer.session.register_path(
        f"round.{round_no:02d}.scoring.top_pool_snapshot",
        f"rounds/{round_no:02d}/scoring/top_pool_snapshot.json",
        content_type="application/json",
        schema_version="v1",
    )
    tracer.write_json(
        f"round.{round_no:02d}.scoring.top_pool_snapshot",
        slim_top_pool_snapshot(current_top_candidates),
    )
    dropped_candidates = [
        run_state.scorecards_by_resume_id[resume_id]
        for resume_id in previous_top_ids
        if resume_id not in run_state.top_pool_ids and resume_id in run_state.scorecards_by_resume_id
    ]
    return current_top_candidates, pool_decisions, dropped_candidates


def build_scoring_pool(
    *,
    new_candidates: list[ResumeCandidate],
    scorecards_by_resume_id: dict[str, ScoredCandidate],
) -> list[ResumeCandidate]:
    pool: list[ResumeCandidate] = []
    seen_ids: set[str] = set()
    for candidate in new_candidates:
        if candidate.resume_id in seen_ids or candidate.resume_id in scorecards_by_resume_id:
            continue
        seen_ids.add(candidate.resume_id)
        pool.append(candidate)
    return pool


def normalize_scoring_pool(
    *,
    round_no: int,
    scoring_pool: list[ResumeCandidate],
    tracer: RunTracer,
    normalized_store: dict[str, NormalizedResume],
) -> list[NormalizedResume]:
    normalized_pool: list[NormalizedResume] = []
    for candidate in scoring_pool:
        tracer.emit(
            "resume_normalization_started",
            round_no=round_no,
            resume_id=candidate.resume_id,
            summary=candidate.compact_summary(),
        )
        normalized = normalize_resume(candidate)
        normalized_store[normalized.resume_id] = normalized
        tracer.write_json(
            f"resumes/{normalized.resume_id}.json",
            normalized.model_dump(mode="json"),
        )
        normalized_pool.append(normalized)
    return normalized_pool


def build_pool_decisions(
    *,
    round_no: int,
    top_candidates: list[ScoredCandidate],
    previous_top_ids: set[str],
) -> list[PoolDecision]:
    top_ids = {candidate.resume_id for candidate in top_candidates}
    decisions: list[PoolDecision] = []
    for rank, candidate in enumerate(top_candidates, start=1):
        decision_type = "retained" if candidate.resume_id in previous_top_ids else "selected"
        decisions.append(
            PoolDecision(
                resume_id=candidate.resume_id,
                round_no=round_no,
                decision=decision_type,
                rank_in_round=rank,
                reasons_for_selection=(
                    candidate.strengths[:3]
                    or [f"Ranked into current global top pool with score {candidate.overall_score}."]
                ),
                reasons_for_rejection=candidate.weaknesses[:2],
                compared_against_pool_summary=f"Deterministically ranked #{rank} in the global scored set.",
            )
        )
    for rank, resume_id in enumerate(
        sorted(previous_top_ids - top_ids),
        start=len(top_candidates) + 1,
    ):
        decisions.append(
            PoolDecision(
                resume_id=resume_id,
                round_no=round_no,
                decision="dropped",
                rank_in_round=rank,
                reasons_for_selection=[],
                reasons_for_rejection=["Replaced by higher-ranked resumes in the global scored set."],
                compared_against_pool_summary="Dropped from the global top pool after this round's new scores landed.",
            )
        )
    return decisions


def scoring_input_ref(resume: NormalizedResume) -> dict[str, object]:
    payload = resume.model_dump(mode="json")
    return {
        "resume_id": resume.resume_id,
        "source_round": resume.source_round,
        "normalized_resume_ref": f"resumes/{resume.resume_id}.json",
        "normalized_resume_sha256": json_sha256(payload),
        "normalized_resume_chars": json_char_count(payload),
        "summary": resume.compact_summary(),
    }
