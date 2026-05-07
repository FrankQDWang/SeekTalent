from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RunStatus = Literal["queued", "running", "completed", "failed"]


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobTitle: str = Field(min_length=1)
    jdText: str = Field(min_length=1)
    sourcingPreferenceText: str = ""
    provider: Literal["cts", "liepin"] = "cts"
    connectionId: str | None = None
    complianceGateRef: str | None = None


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

    projection: ResumeProjection


class ResumeAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    summary: str
    evidenceSpans: list[str] = Field(default_factory=list)
    riskFlags: list[str] = Field(default_factory=list)


class CandidateDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: CandidateCard
    resumeView: CandidateResumeView
    aiAnalysis: ResumeAnalysis


class RunStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runId: str
    status: RunStatus
    errorMessage: str | None = None
    finalShortlist: list[AgentShortlistCandidate] = Field(default_factory=list)


class LiepinComplianceGateCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidatePersonalInfoProcessingBasis: str
    personalInformationProcessor: str
    operatorAuditOwner: str
    accountHolderAuthorized: bool
    humanInitiatedRecruiting: bool
    allowedPurposes: list[str]
    retentionPolicy: Literal["run_debug_short", "workspace_recruiting_record", "forbidden_persist"]
    deletionSlaDays: int
    deletionPath: str
    rawPayloadAccessScope: Literal["run_only", "workspace", "admin_only"]
    rawDetailRetentionAllowedAfterDebug: bool
    fixtureExportAllowed: bool
    policyRef: str


class LiepinComplianceGateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gateRef: str
    tenantId: str
    workspaceId: str
    actorId: str
    status: Literal["pending_account_binding", "approved", "denied", "expired"]
    allowedPurposes: list[str]
    retentionPolicy: Literal["run_debug_short", "workspace_recruiting_record", "forbidden_persist"]
    policyRef: str


class LiepinConnectionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    complianceGateRef: str


class LiepinComplianceGateConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connectionId: str


class LiepinComplianceGateActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gateRef: str
    status: Literal["pending_account_binding", "approved", "denied", "expired"]


class LiepinConnectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connectionId: str
    tenantId: str
    workspaceId: str
    actorId: str
    complianceGateRef: str
    status: str


class LiepinLoginUrlResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connectionId: str
    loginUrl: str
    handoffState: Literal["ready_for_browser_login"]


class LiepinRunResultsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runId: str
    results: list[dict[str, object]] = Field(default_factory=list)
