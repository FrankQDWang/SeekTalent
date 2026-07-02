from __future__ import annotations

from seektalent.models import NormalizedResume


def normalized_resume_artifact_payload(resume: NormalizedResume) -> dict[str, object]:
    payload = resume.model_dump(mode="json")
    if resume.source_provider == "liepin":
        payload.pop("raw_text_excerpt", None)
    return payload
