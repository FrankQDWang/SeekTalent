from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PiResumeValidationGap(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    resume_count: int = Field(ge=0)
    protected_snapshot_refs: list[str] = Field(default_factory=list)
    detail_payloads: list[str] = Field(default_factory=list)

    @property
    def needs_repair(self) -> bool:
        return bool(self.resume_count or self.protected_snapshot_refs or self.detail_payloads)


class PiResumeRepairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task: Literal["liepin.repair_resume_output"] = "liepin.repair_resume_output"
    schema_version: Literal["seektalent.pi_liepin_resume_repair.v1"] = "seektalent.pi_liepin_resume_repair.v1"
    source_run_id: str
    query: str
    missing: PiResumeValidationGap
    instruction: str = (
        "Continue from the current search context. Do not restart the full search. "
        "Open additional ranked cards or repair missing detail payloads until the lane contract is satisfied."
    )


def _resume_label(resume: object, *, index: int) -> str:
    if isinstance(resume, Mapping):
        candidate_id = resume.get("candidate_resume_id")
        if isinstance(candidate_id, str) and candidate_id:
            return candidate_id
    return f"resume index {index}"


def validation_gap_for_resume_payload(payload: Mapping[str, object], *, target: int) -> PiResumeValidationGap:
    raw_resumes = payload.get("resumes")
    resumes = raw_resumes if isinstance(raw_resumes, list) else []
    returned = payload.get("resumes_returned")
    returned_count = returned if isinstance(returned, int) else len(resumes)
    observed_count = min(returned_count, len(resumes))
    missing_count = max(0, target - observed_count)
    protected_snapshot_refs: list[str] = []
    detail_payloads: list[str] = []

    for index, resume in enumerate(resumes, start=1):
        label = _resume_label(resume, index=index)
        if not isinstance(resume, Mapping):
            detail_payloads.append(label)
            protected_snapshot_refs.append(label)
            continue
        if not isinstance(resume.get("protected_snapshot_ref"), str):
            protected_snapshot_refs.append(label)
        detail_payload = resume.get("detail_payload")
        if not isinstance(detail_payload, Mapping) or not detail_payload:
            detail_payloads.append(label)

    return PiResumeValidationGap(
        resume_count=missing_count,
        protected_snapshot_refs=protected_snapshot_refs,
        detail_payloads=detail_payloads,
    )
