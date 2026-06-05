from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from seektalent.evaluation import TOP_K
from seektalent.models import (
    NormalizedResume,
    ResumeCandidate,
    RunState,
    RuntimeCanonicalIntakeSummary,
    RuntimeSourceKind,
    ScoredCandidate,
    scored_candidate_sort_key,
)
from seektalent.normalization import normalize_resume
from seektalent.tracing import RunTracer


def normalize_runtime_candidates(
    *,
    run_state: RunState,
    candidates: Iterable[ResumeCandidate],
    round_no: int,
    tracer: RunTracer | None = None,
) -> dict[str, NormalizedResume]:
    normalized_updates: dict[str, NormalizedResume] = {}
    for candidate in candidates:
        existing = run_state.normalized_store.get(candidate.resume_id)
        if existing is not None:
            normalized_updates[candidate.resume_id] = existing
            continue
        if tracer is not None:
            tracer.emit(
                "resume_normalization_started",
                round_no=round_no,
                resume_id=candidate.resume_id,
                summary=candidate.compact_summary(),
            )
        normalized = normalize_resume(candidate)
        run_state.normalized_store[normalized.resume_id] = normalized
        normalized_updates[normalized.resume_id] = normalized
        if tracer is not None:
            tracer.write_json(
                f"resumes/{normalized.resume_id}.json",
                normalized.model_dump(mode="json"),
            )
    return normalized_updates


@dataclass(frozen=True, kw_only=True)
class CanonicalScoringIntake:
    scoring_candidates: list[ResumeCandidate]
    summary: RuntimeCanonicalIntakeSummary


def build_canonical_scoring_intake(
    *,
    run_state: RunState,
    round_no: int,
    new_candidates: list[ResumeCandidate],
    selected_source_kinds: tuple[str, ...] = (),
    source_raw_targets: dict[str, int] | None = None,
) -> CanonicalScoringIntake:
    scored_identity_ids = {
        run_state.candidate_identity_by_resume_id.get(resume_id, resume_id)
        for resume_id in run_state.scorecards_by_resume_id
    }
    candidate_by_resume_id = {candidate.resume_id: candidate for candidate in new_candidates}
    first_resume_by_identity: dict[str, str] = {}
    per_source_counts: Counter[str] = Counter()
    per_source_normalized_counts: Counter[str] = Counter()
    for candidate in new_candidates:
        normalized = run_state.normalized_store.get(candidate.resume_id)
        provider = normalized.source_provider if normalized is not None else None
        per_source_counts[provider or "unknown"] += 1
        if normalized is not None:
            per_source_normalized_counts[provider or "unknown"] += 1
        identity_id = run_state.candidate_identity_by_resume_id.get(candidate.resume_id, candidate.resume_id)
        first_resume_by_identity.setdefault(identity_id, candidate.resume_id)

    scoring_candidates: list[ResumeCandidate] = []
    skipped_already_scored = 0
    for identity_id, first_resume_id in first_resume_by_identity.items():
        canonical = run_state.canonical_resume_by_identity_id.get(identity_id)
        canonical_resume_id = canonical.canonical_resume_id if canonical is not None else first_resume_id
        if identity_id in scored_identity_ids and canonical_resume_id in run_state.scorecards_by_resume_id:
            skipped_already_scored += 1
            continue
        candidate = candidate_by_resume_id.get(canonical_resume_id) or run_state.candidate_store.get(canonical_resume_id)
        if candidate is None:
            continue
        scoring_candidates.append(candidate)

    duplicate_count = max(0, len(new_candidates) - len(first_resume_by_identity))
    new_resume_ids = set(candidate_by_resume_id)
    round_conflicts = [
        conflict
        for conflict in run_state.identity_conflicts
        if new_resume_ids & set(conflict.resume_ids)
    ]
    if not selected_source_kinds and run_state.source_coverage_summary is not None:
        selected_source_kinds = tuple(run_state.source_coverage_summary.selected_source_kinds)
    summary = RuntimeCanonicalIntakeSummary(
        round_no=round_no,
        selected_source_kinds=_runtime_source_kinds(selected_source_kinds),
        source_raw_targets=dict(sorted((source_raw_targets or {}).items())),
        raw_candidate_count=len(new_candidates),
        normalized_candidate_count=sum(1 for candidate in new_candidates if candidate.resume_id in run_state.normalized_store),
        identity_count=len(first_resume_by_identity),
        auto_merged_duplicate_count=duplicate_count,
        uncertain_conflict_count=len(round_conflicts),
        skipped_already_scored_identity_count=skipped_already_scored,
        scoring_candidate_count=len(scoring_candidates),
        canonical_resume_ids=tuple(candidate.resume_id for candidate in scoring_candidates),
        per_source_raw_counts=dict(sorted(per_source_counts.items())),
        per_source_normalized_counts=dict(sorted(per_source_normalized_counts.items())),
    )
    run_state.latest_canonical_intake_summary = summary
    return CanonicalScoringIntake(scoring_candidates=scoring_candidates, summary=summary)


def _runtime_source_kinds(values: tuple[str, ...]) -> tuple[RuntimeSourceKind, ...]:
    normalized: list[RuntimeSourceKind] = []
    for value in values:
        source_kind = str(value).strip()
        if not source_kind:
            raise ValueError("runtime_source_kind_required")
        normalized.append(source_kind)
    return tuple(normalized)


def select_identity_top_candidates(run_state: RunState) -> list[ScoredCandidate]:
    selected: list[ScoredCandidate] = []
    seen_identity_ids: set[str] = set()
    for scored in sorted(run_state.scorecards_by_resume_id.values(), key=scored_candidate_sort_key):
        identity_id = run_state.candidate_identity_by_resume_id.get(scored.resume_id, scored.resume_id)
        if identity_id in seen_identity_ids:
            continue
        canonical = run_state.canonical_resume_by_identity_id.get(identity_id)
        selected_resume_id = canonical.canonical_resume_id if canonical is not None else scored.resume_id
        selected_score = run_state.scorecards_by_resume_id.get(selected_resume_id, scored)
        selected.append(selected_score)
        seen_identity_ids.add(identity_id)
        if len(selected) >= TOP_K:
            break
    run_state.top_pool_ids = [candidate.resume_id for candidate in selected]
    return selected
