from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SubjectType = Literal["connection", "run"]
LiepinIdentityConfidence = Literal["provider_subject_id", "synthetic_fingerprint"]
LiepinExtractionSource = Literal["network", "dom_fallback", "fixture"]
LiepinScoreEvidenceSource = Literal["card_only", "detail_enriched"]
LiepinPiiClassification = Literal["direct_contact_possible", "direct_contact_present", "no_direct_contact", "unknown"]
LiepinRetentionPolicy = Literal["provider_snapshot_7d", "provider_snapshot_30d", "retain_local"]
LiepinAccessScope = Literal["local_run_only", "local_workspace"]
LiepinRedactionState = Literal["raw_provider_payload", "redacted"]


@dataclass(frozen=True)
class LiepinConnectionRow:
    connection_id: str
    tenant_id: str
    workspace_id: str
    actor_id: str
    compliance_gate_ref: str
    status: str
    provider_account_hash: str | None


@dataclass(frozen=True)
class LiepinRunRow:
    run_id: str
    tenant_id: str
    workspace_id: str
    actor_id: str
    connection_id: str
    compliance_gate_ref: str
    status: str


@dataclass(frozen=True)
class LiepinEventRow:
    tenant_id: str
    workspace_id: str
    actor_id: str
    subject_type: SubjectType
    subject_id: str
    sequence: int
    event_name: str
    payload: dict[str, object]
    redaction_state: str
    created_at: str
