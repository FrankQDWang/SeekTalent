from __future__ import annotations

from seektalent.models import NormalizedResume


def normalized_resume_artifact_payload(resume: NormalizedResume) -> dict[str, object]:
    return resume.model_dump(mode="json")
