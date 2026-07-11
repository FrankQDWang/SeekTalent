from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any, Literal

from seektalent.models import (
    NormalizedResume,
    PoolDecision,
    ResumeCandidate,
    RunState,
    RuntimeConstraint,
    RuntimeCanonicalIntakeSummary,
    ScoringFailure,
    ScoredCandidate,
)
from seektalent.normalization import normalize_resume
from seektalent.runtime.candidate_intake import build_canonical_scoring_intake, select_identity_top_candidates
from seektalent.runtime.normalized_artifacts import normalized_resume_artifact_payload
from seektalent.runtime.runtime_diagnostics import slim_top_pool_snapshot
from seektalent.runtime.scoring_context import build_scoring_context
from seektalent.tracing import RunTracer, json_char_count, json_sha256


@dataclass(frozen=True)
class ScoringRoundResult:
    top_candidates: list[ScoredCandidate]
    pool_decisions: list[PoolDecision]
    dropped_candidates: list[ScoredCandidate]
    scoring_failures: list[ScoringFailure]

    @classmethod
    def empty(cls) -> "ScoringRoundResult":
        return cls([], [], [], [])


def combine_round_intake_summaries(
    *, baseline: RuntimeCanonicalIntakeSummary | None, expansion: RuntimeCanonicalIntakeSummary | None
) -> RuntimeCanonicalIntakeSummary | None:
    if baseline is None:
        return expansion
    if expansion is None:
        return baseline
    if baseline.round_no != expansion.round_no:
        raise ValueError("canonical_intake_summary_round_mismatch")
    def add_counts(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
        return {key: left.get(key, 0) + right.get(key, 0) for key in dict.fromkeys((*left, *right))}
    return baseline.model_copy(update={
        "raw_candidate_count": baseline.raw_candidate_count + expansion.raw_candidate_count,
        "normalized_candidate_count": baseline.normalized_candidate_count + expansion.normalized_candidate_count,
        "identity_count": baseline.identity_count + expansion.identity_count,
        "auto_merged_duplicate_count": baseline.auto_merged_duplicate_count + expansion.auto_merged_duplicate_count,
        "uncertain_conflict_count": baseline.uncertain_conflict_count + expansion.uncertain_conflict_count,
        "skipped_already_scored_identity_count": baseline.skipped_already_scored_identity_count + expansion.skipped_already_scored_identity_count,
        "scoring_candidate_count": baseline.scoring_candidate_count + expansion.scoring_candidate_count,
        "canonical_resume_ids": tuple(dict.fromkeys((*baseline.canonical_resume_ids, *expansion.canonical_resume_ids))),
        "per_source_raw_counts": add_counts(baseline.per_source_raw_counts, expansion.per_source_raw_counts),
        "per_source_normalized_counts": add_counts(baseline.per_source_normalized_counts, expansion.per_source_normalized_counts),
    })


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
    selected_source_kinds: tuple[str, ...] = (),
    source_raw_targets: dict[str, int] | None = None,
    batch_kind: Literal["baseline", "first_page_expansion"] = "baseline",
    fail_on_scoring_error: bool = True,
    finalize_pool: bool = True,
) -> ScoringRoundResult:
    canonical_intake = build_canonical_scoring_intake(
        run_state=run_state,
        round_no=round_no,
        new_candidates=new_candidates,
        selected_source_kinds=selected_source_kinds,
        source_raw_targets=source_raw_targets,
    )
    scoring_pool = build_scoring_pool(
        new_candidates=canonical_intake.scoring_candidates,
        scorecards_by_resume_id=run_state.scorecards_by_resume_id,
    )
    normalized_scoring_pool = normalize_scoring_pool(
        round_no=round_no,
        scoring_pool=scoring_pool,
        tracer=tracer,
        normalized_store=run_state.normalized_store,
    )
    if batch_kind == "baseline":
        tracer.write_jsonl(f"round.{round_no:02d}.scoring.scoring_input_refs", [])
        tracer.write_jsonl(f"round.{round_no:02d}.scoring.scorecards", [])
    for item in normalized_scoring_pool:
        tracer.append_jsonl(
            f"round.{round_no:02d}.scoring.scoring_input_refs",
            {**scoring_input_ref(item), "batch_kind": batch_kind},
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
    scoring_failures: list[ScoringFailure] = []
    if scoring_contexts:
        scored_candidates, scoring_failures = await resume_scorer.score_candidates_parallel(
            contexts=scoring_contexts,
            tracer=tracer,
        )
        for candidate in scored_candidates:
            if candidate.resume_id not in run_state.scorecards_by_resume_id:
                run_state.scorecards_by_resume_id[candidate.resume_id] = candidate
        if scoring_failures and fail_on_scoring_error:
            raise run_stage_error("scoring", format_scoring_failure_message(scoring_failures))
    else:
        scored_candidates = []
    for item in scored_candidates:
        tracer.append_jsonl(
            f"round.{round_no:02d}.scoring.scorecards",
            {**item.model_dump(mode="json"), "batch_kind": batch_kind},
        )
    if not finalize_pool:
        return ScoringRoundResult(select_identity_top_candidates(run_state), [], [], scoring_failures)
    current_top_candidates, pool_decisions, dropped_candidates = finalize_round_pool(
        round_no=round_no,
        run_state=run_state,
        tracer=tracer,
        previous_top_ids=previous_top_ids,
    )
    return ScoringRoundResult(current_top_candidates, pool_decisions, dropped_candidates, scoring_failures)


def finalize_round_pool(
    *, round_no: int, run_state: RunState, tracer: RunTracer, previous_top_ids: set[str]
) -> tuple[list[ScoredCandidate], list[PoolDecision], list[ScoredCandidate]]:
    current_top_candidates = select_identity_top_candidates(run_state)
    pool_decisions = build_pool_decisions(
        round_no=round_no,
        top_candidates=current_top_candidates,
        previous_top_ids=previous_top_ids,
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
        existing = normalized_store.get(candidate.resume_id)
        if existing is not None:
            normalized_pool.append(existing)
            continue
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
            normalized_resume_artifact_payload(normalized),
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
    payload = normalized_resume_artifact_payload(resume)
    return {
        "resume_id": resume.resume_id,
        "source_round": resume.source_round,
        "normalized_resume_ref": f"resumes/{resume.resume_id}.json",
        "normalized_resume_sha256": json_sha256(payload),
        "normalized_resume_chars": json_char_count(payload),
        "summary": resume.compact_summary(),
    }
