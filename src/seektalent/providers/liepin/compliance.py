from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


GateStatus = Literal["pending_account_binding", "approved", "denied", "expired"]
RetentionPolicy = Literal["run_debug_short", "workspace_recruiting_record", "forbidden_persist"]
RawPayloadAccessScope = Literal["run_only", "workspace", "admin_only"]


class ComplianceGate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    workspace_id: str
    actor_id: str
    provider_account_hash: str | None
    status: GateStatus
    candidate_personal_info_processing_basis: str
    personal_information_processor: str
    operator_audit_owner: str
    account_holder_authorized: bool
    human_initiated_recruiting: bool
    allowed_purposes: list[str] = Field(default_factory=list)
    retention_policy: RetentionPolicy
    deletion_sla_days: int
    deletion_path: str
    raw_payload_access_scope: RawPayloadAccessScope
    raw_detail_retention_allowed_after_debug: bool
    fixture_export_allowed: bool
    policy_ref: str

    def allows_connection_handoff(self, *, purpose: str = "search") -> bool:
        return self.status in {"pending_account_binding", "approved"} and self._base_policy_allows(purpose=purpose)

    def allows_live_search(self, *, provider_account_hash: str | None, purpose: str = "search") -> bool:
        return (
            self.status == "approved"
            and self.provider_account_hash is not None
            and self.provider_account_hash == provider_account_hash
            and self._base_policy_allows(purpose=purpose)
        )

    def denial_reason(self, *, provider_account_hash: str | None = None, purpose: str = "search") -> str | None:
        if self.status != "approved":
            return self.status
        if self.provider_account_hash is None:
            return "pending_account_binding"
        if self.provider_account_hash != provider_account_hash:
            return "provider_account_mismatch"
        if not self._base_policy_allows(purpose=purpose):
            return "policy_requirements_not_satisfied"
        return None

    def _base_policy_allows(self, *, purpose: str) -> bool:
        return (
            bool(self.tenant_id.strip())
            and bool(self.workspace_id.strip())
            and bool(self.actor_id.strip())
            and bool(self.candidate_personal_info_processing_basis.strip())
            and bool(self.personal_information_processor.strip())
            and bool(self.operator_audit_owner.strip())
            and self.account_holder_authorized
            and self.human_initiated_recruiting
            and purpose in self.allowed_purposes
            and self.deletion_sla_days > 0
            and bool(self.deletion_path.strip())
            and not self.raw_detail_retention_allowed_after_debug
            and not self.fixture_export_allowed
            and bool(self.policy_ref.strip())
        )
