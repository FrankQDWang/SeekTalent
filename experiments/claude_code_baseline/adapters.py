from __future__ import annotations

from seektalent.evaluation import TOP_K
from seektalent.models import ResumeCandidate


def candidate_brief(candidate: ResumeCandidate) -> dict[str, object]:
    return {
        "resume_id": candidate.resume_id,
        "source_round": candidate.source_round,
        "expected_job_category": candidate.expected_job_category,
        "now_location": candidate.now_location,
        "work_year": candidate.work_year,
        "education_summaries": candidate.education_summaries[:1],
        "work_experience_summaries": candidate.work_experience_summaries[:2],
        "project_names": candidate.project_names[:3],
        "work_summaries": candidate.work_summaries[:5],
        "snapshot_sha256": candidate.snapshot_sha256,
    }


def candidate_rows(candidates: list[ResumeCandidate]) -> list[dict[str, object]]:
    return [candidate.model_dump(mode="json") for candidate in candidates]


def ranked_candidates_from_ids(
    ranked_resume_ids: list[str],
    candidate_store: dict[str, ResumeCandidate],
    *,
    limit: int = TOP_K,
) -> list[ResumeCandidate]:
    seen: set[str] = set()
    selected: list[ResumeCandidate] = []
    for resume_id in ranked_resume_ids:
        if resume_id in seen or resume_id not in candidate_store:
            continue
        seen.add(resume_id)
        selected.append(candidate_store[resume_id])
        if len(selected) >= limit:
            break
    if not selected:
        raise ValueError("Claude Code returned no known resume ids in the shortlist snapshot.")
    return selected
