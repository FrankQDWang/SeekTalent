from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SubjectType = Literal["connection", "run"]


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
