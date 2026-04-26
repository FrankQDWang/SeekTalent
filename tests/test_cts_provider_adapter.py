from seektalent.models import ResumeCandidate
from seektalent.providers.cts.mapper import build_provider_candidate


def test_cts_candidate_mapper_builds_resume_candidate() -> None:
    candidate = ResumeCandidate(
        resume_id="resume-1",
        source_resume_id="source-1",
        snapshot_sha256="snap",
        dedup_key="resume-1",
        search_text="python engineer",
        raw={"resumeId": "resume-1"},
    )

    mapped = build_provider_candidate(candidate)

    assert mapped.resume_id == "resume-1"
    assert mapped.dedup_key == "resume-1"
