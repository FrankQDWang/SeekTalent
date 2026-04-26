from __future__ import annotations

from seektalent.models import ResumeCandidate


def build_provider_candidate(candidate: ResumeCandidate) -> ResumeCandidate:
    return candidate.model_copy()
