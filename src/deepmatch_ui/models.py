from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RunStatus = Literal["queued", "running", "completed", "failed"]


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jdText: str = Field(min_length=1)
    sourcingPreferenceText: str = ""


class RunCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runId: str
    status: Literal["queued", "running"]


class AgentShortlistCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidateId: str
    externalIdentityId: str
    name: str
    title: str
    company: str
    location: str
    summary: str
    reason: str
    score: float
    sourceRound: int


class ResumeWorkExperienceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str
    title: str
    duration: str | None = None
    startTime: str | None = None
    endTime: str | None = None
    summary: str | None = None


class ResumeEducationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    school: str
    degree: str
    major: str
    startTime: str | None = None
    endTime: str | None = None


class ResumeProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workYear: int | None = None
    currentLocation: str | None = None
    expectedLocation: str | None = None
    jobState: str | None = None
    expectedSalary: str | None = None
    age: int | None = None
    education: list[ResumeEducationItem] = Field(default_factory=list)
    workExperience: list[ResumeWorkExperienceItem] = Field(default_factory=list)
    workSummaries: list[str] = Field(default_factory=list)
    projectNames: list[str] = Field(default_factory=list)


class CandidateCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidateId: str
    externalIdentityId: str
    name: str
    title: str
    company: str
    location: str
    summary: str = ""


class CandidateResumeView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshotId: str
    projection: ResumeProjection


class ResumeAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    summary: str
    evidenceSpans: list[str] = Field(default_factory=list)
    riskFlags: list[str] = Field(default_factory=list)


class VerdictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: str
    reasons: list[str] = Field(default_factory=list)
    notes: str | None = None
    actorId: str
    createdAt: str


class CandidateDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: CandidateCard
    resumeView: CandidateResumeView
    aiAnalysis: ResumeAnalysis
    verdictHistory: list[VerdictRecord] = Field(default_factory=list)


class RunStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runId: str
    status: RunStatus
    errorMessage: str | None = None
    finalShortlist: list[AgentShortlistCandidate] = Field(default_factory=list)
