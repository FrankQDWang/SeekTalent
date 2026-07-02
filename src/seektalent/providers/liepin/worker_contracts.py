from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.providers.liepin.detail_payload_text import find_liepin_whole_page_text_alias_paths
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
    def __init__(
        self,
        message: str,
        *,
        setup_status: str | None = None,
        code: str | None = None,
        partial_search_result: SearchResult | None = None,
        cards_collected: int = 0,
    ) -> None:
        super().__init__(message)
        self.setup_status = setup_status
        self.code = code or setup_status
        self.partial_search_result = partial_search_result
        self.cards_collected = cards_collected


class LiepinWorkerPartialSearchError(LiepinWorkerModeError):
    partial_search_result: SearchResult
    cards_collected: int

    def __init__(
        self,
        message: str,
        *,
        code: str,
        partial_search_result: SearchResult,
        cards_collected: int,
    ) -> None:
        super().__init__(message, code=code)
        self.partial_search_result = partial_search_result
        self.cards_collected = cards_collected


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


class LoginRelaySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    connection_id: str = Field(alias="connectionId")
    status: Literal["login_in_progress", "ready", "expired", "failed"]
    page_title: str = Field(alias="pageTitle")
    page_origin: str = Field(alias="pageOrigin")
    image_mime_type: Literal["image/jpeg"] = Field(alias="imageMimeType")
    image_base64: str = Field(alias="imageBase64")
    updated_at: str = Field(alias="updatedAt")


class LoginRelayInputResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    connection_id: str = Field(alias="connectionId")
    accepted: Literal[True]
    updated_at: str = Field(alias="updatedAt")


class LoginRelayCompleteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    connection_id: str = Field(alias="connectionId")
    status: Literal["ready"]
    provider_account_hash: str | None = Field(default=None, alias="providerAccountHash")
    fixture_only: bool = Field(default=False, alias="fixtureOnly")


class RedactedWorkerDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    stdout: Literal["[redacted]"] | None = None
    stderr: Literal["[redacted]"] | None = None


class LiepinSafeCardSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    display_title: str | None = None
    current_or_recent_company: str | None = None
    current_or_recent_title: str | None = None
    work_years: int | None = None
    age: int | None = None
    city: str | None = None
    expected_city: str | None = None
    education_level: str | None = None
    school_names: tuple[str, ...] = ()
    major_names: tuple[str, ...] = ()
    skill_tags: tuple[str, ...] = ()
    job_intention: str | None = None
    recent_experience_text: str | None = None
    masked_name: bool = False


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
    safe_card_summary: LiepinSafeCardSummary | None = Field(default=None, alias="safeCardSummary")


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

    @model_validator(mode="after")
    def reject_whole_page_text_payload_aliases(self) -> LiepinWorkerCandidateDetail:
        prohibited_paths = find_liepin_whole_page_text_alias_paths(self.payload)
        if prohibited_paths:
            paths = ", ".join(prohibited_paths)
            raise ValueError(f"Liepin detail payload includes prohibited whole-page text field(s): {paths}")
        return self


class LiepinResumeSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    resumes: list[LiepinWorkerCandidateDetail]
    diagnostics: list[str] = Field(default_factory=list)
    exhausted: bool = False
    next_cursor: str | None = Field(default=None, alias="nextCursor")
    request_payload: dict[str, Any] = Field(default_factory=dict, alias="requestPayload")
    raw_candidate_count: int | None = Field(default=None, alias="rawCandidateCount")


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


def decode_login_relay_snapshot(payload: dict[str, object]) -> LoginRelaySnapshot:
    return LoginRelaySnapshot.model_validate(payload)


def decode_login_relay_input_result(payload: dict[str, object]) -> LoginRelayInputResult:
    return LoginRelayInputResult.model_validate(payload)


def decode_login_relay_complete_result(payload: dict[str, object]) -> LoginRelayCompleteResult:
    return LoginRelayCompleteResult.model_validate(payload)


def decode_redacted_diagnostics(payload: dict[str, object]) -> RedactedWorkerDiagnostics:
    return RedactedWorkerDiagnostics.model_validate(payload)


def decode_card_search_response(payload: dict[str, object]) -> LiepinCardSearchResponse:
    return LiepinCardSearchResponse.model_validate(payload)


def decode_resume_search_response(payload: dict[str, object]) -> LiepinResumeSearchResponse:
    return LiepinResumeSearchResponse.model_validate(payload)


def decode_detail_open_response(payload: dict[str, object]) -> LiepinDetailOpenResponse:
    return LiepinDetailOpenResponse.model_validate(payload)
