from __future__ import annotations

from seektalent.models import ResumeCandidate


def candidate_rows(candidates: list[ResumeCandidate]) -> list[dict[str, object]]:
    return [candidate.model_dump(mode="json") for candidate in candidates]
