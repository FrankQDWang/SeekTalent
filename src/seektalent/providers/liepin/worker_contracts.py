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


class LiepinWorkerModeError(RuntimeError):
    def __init__(self, message: str, *, setup_status: str | None = None) -> None:
        super().__init__(message)
        self.setup_status = setup_status


class WorkerHealth(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["ok", "starting"]
    worker_version: str | None = Field(default=None, alias="workerVersion")


class SessionStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connection_id: str = Field(alias="connectionId")
    status: Literal["missing", "login_required", "ready", "revoked"]
    provider_account_hash: str | None = Field(default=None, alias="providerAccountHash")
    fixture_only: bool = Field(default=False, alias="fixtureOnly")


class LoginHandoff(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connection_id: str = Field(alias="connectionId")
    login_url: str = Field(alias="loginUrl")
    expires_at: str = Field(alias="expiresAt")


class RedactedWorkerDiagnostics(BaseModel):
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


def decode_worker_health(payload: dict[str, object]) -> WorkerHealth:
    return WorkerHealth.model_validate(payload)


def decode_session_status(payload: dict[str, object]) -> SessionStatus:
    return SessionStatus.model_validate(payload)


def decode_login_handoff(payload: dict[str, object]) -> LoginHandoff:
    return LoginHandoff.model_validate(payload)


def decode_redacted_diagnostics(payload: dict[str, object]) -> RedactedWorkerDiagnostics:
    return RedactedWorkerDiagnostics.model_validate(payload)
