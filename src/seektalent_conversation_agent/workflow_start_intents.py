from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


WorkflowStartIntentStatus = Literal["pending", "started", "failed", "cancelled"]


@dataclass(frozen=True)
class WorkflowStartIntent:
    workflow_start_intent_id: str
    workspace_id: str
    owner_user_id: str
    conversation_id: str
    draft_revision_id: str
    approved_requirement_revision_id: str
    job_request_revision_id: str
    idempotency_key: str
    request_hash: str
    deterministic_run_key: str
    status: WorkflowStartIntentStatus
    runtime_run_id: str | None
    reason_code: str | None
    created_at: str
    updated_at: str
