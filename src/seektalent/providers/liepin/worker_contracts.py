from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from seektalent.providers.liepin.models import LiepinAccessScope
from seektalent.providers.liepin.models import LiepinExtractionSource
from seektalent.providers.liepin.models import LiepinIdentityConfidence
from seektalent.providers.liepin.models import LiepinPiiClassification
from seektalent.providers.liepin.models import LiepinRedactionState
from seektalent.providers.liepin.models import LiepinRetentionPolicy

DetailOpenStatus = Literal[
    "completed",
    "blocked_by_risk_control",
    "failed_before_consumption",
    "failed_after_possible_consumption",
    "unknown",
]


class LiepinWorkerModeError(RuntimeError):
    def __init__(self, message: str, *, setup_status: str | None = None) -> None:
        super().__init__(message)
        self.setup_status = setup_status


class WorkerHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    status: Literal["ok", "starting"]
    worker_version: str | None = Field(default=None, alias="workerVersion")


class SessionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    connection_id: str = Field(alias="connectionId")
    status: Literal["missing", "login_required", "ready", "revoked"]
    provider_account_hash: str | None = Field(default=None, alias="providerAccountHash")
    fixture_only: bool = Field(default=False, alias="fixtureOnly")


class LoginHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    connection_id: str = Field(alias="connectionId")
    handoff_token: str = Field(alias="handoffToken")
    login_url: str = Field(alias="loginUrl")
    expires_at: str = Field(alias="expiresAt")


class RedactedWorkerDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    stdout: Literal["[redacted]"] | None = None
    stderr: Literal["[redacted]"] | None = None


class LiepinWorkerCandidateCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]
    normalized_text: str
    provider_subject_id: str | None = None
    provider_listing_id: str | None = None
    synthetic_candidate_fingerprint: str
    identity_confidence: LiepinIdentityConfidence
    extraction_source: LiepinExtractionSource
    extractor_version: str
    pii_classification: LiepinPiiClassification
    retention_policy: LiepinRetentionPolicy
    access_scope: LiepinAccessScope
    redaction_state: LiepinRedactionState


class LiepinCardSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    cards: list[LiepinWorkerCandidateCard]
    diagnostics: list[str] = Field(default_factory=list)
    exhausted: bool = False
    next_cursor: str | None = Field(default=None, alias="nextCursor")
    request_payload: dict[str, Any] = Field(default_factory=dict, alias="requestPayload")
    raw_candidate_count: int | None = Field(default=None, alias="rawCandidateCount")


class LiepinWorkerCandidateDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]
    normalized_text: str
    provider_subject_id: str | None = None
    provider_listing_id: str | None = None
    synthetic_candidate_fingerprint: str
    identity_confidence: LiepinIdentityConfidence
    extraction_source: LiepinExtractionSource
    extractor_version: str
    pii_classification: LiepinPiiClassification
    retention_policy: LiepinRetentionPolicy
    access_scope: LiepinAccessScope
    redaction_state: LiepinRedactionState


class LiepinDetailOpenRequestItem(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: str = Field(alias="requestId")
    attempt_id: str = Field(alias="attemptId")
    idempotency_key: str = Field(alias="idempotencyKey")
    approval_key: str = Field(alias="approvalKey")
    candidate_id: str = Field(alias="candidateId")
    detail_url: str | None = Field(default=None, alias="detailUrl")


class LiepinDetailOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tenant_id: str = Field(alias="tenantId")
    workspace_id: str = Field(alias="workspaceId")
    provider_account_hash: str = Field(alias="providerAccountHash")
    connection_id: str = Field(alias="connectionId")
    provider_day_key: str = Field(alias="providerDayKey")
    worker_command_id: str = Field(alias="workerCommandId")
    requests: list[LiepinDetailOpenRequestItem]


class LiepinDetailWorkerDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    page_loaded: bool = Field(default=False, alias="pageLoaded")
    payload_seen: bool = Field(default=False, alias="payloadSeen")
    extraction_source: Literal["network", "dom_fallback"] | None = Field(default=None, alias="extractionSource")
    messages: list[str] = Field(default_factory=list)


class LiepinDetailOpenResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: str = Field(alias="requestId")
    attempt_id: str = Field(alias="attemptId")
    idempotency_key: str = Field(alias="idempotencyKey")
    status: DetailOpenStatus
    worker_response_id: str = Field(alias="workerResponseId")
    worker_command_id: str = Field(alias="workerCommandId")
    raw_evidence_ref: str | None = Field(default=None, alias="rawEvidenceRef")
    diagnostics: LiepinDetailWorkerDiagnostics
    candidate: LiepinWorkerCandidateDetail | None = None


class LiepinDetailOpenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    worker_command_id: str = Field(alias="workerCommandId")
    results: list[LiepinDetailOpenResult]


def decode_worker_health(payload: dict[str, object]) -> WorkerHealth:
    return WorkerHealth.model_validate(payload)


def decode_session_status(payload: dict[str, object]) -> SessionStatus:
    return SessionStatus.model_validate(payload)


def decode_login_handoff(payload: dict[str, object]) -> LoginHandoff:
    return LoginHandoff.model_validate(payload)


def decode_redacted_diagnostics(payload: dict[str, object]) -> RedactedWorkerDiagnostics:
    return RedactedWorkerDiagnostics.model_validate(payload)


def decode_card_search_response(payload: dict[str, object]) -> LiepinCardSearchResponse:
    return LiepinCardSearchResponse.model_validate(payload)


def decode_detail_open_response(payload: dict[str, object]) -> LiepinDetailOpenResponse:
    return LiepinDetailOpenResponse.model_validate(payload)
