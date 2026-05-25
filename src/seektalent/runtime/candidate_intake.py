from __future__ import annotations

from collections.abc import Iterable

from seektalent.models import NormalizedResume, ResumeCandidate, RunState
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
