from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ComplianceGate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    org_name: str
    org_domain: str
    approved_purposes: list[str] = Field(default_factory=list)
    search_keywords: list[str] = Field(default_factory=list)
    retention_days: int
    pii_policy: str
    operator_id: str
    operator_name: str
    created_at: str
    approved_at: str | None = None
    account_binding_hash: str | None = None

    @property
    def status(self) -> str:
        if self.approved_at and self.account_binding_hash:
            return "approved"
        return "pending_account_binding"

    def allows_connection_handoff(self, *, purpose: str = "search") -> bool:
        return self._base_policy_allows(purpose=purpose)

    def allows_live_search(self, *, account_binding_hash: str | None, purpose: str = "search") -> bool:
        return (
            self.status == "approved"
            and self.account_binding_hash is not None
            and self.account_binding_hash == account_binding_hash
            and self._base_policy_allows(purpose=purpose)
        )

    def denial_reason(self, *, account_binding_hash: str | None = None, purpose: str = "search") -> str | None:
        if self.status != "approved":
            return self.status
        if self.account_binding_hash is None:
            return "pending_account_binding"
        if self.account_binding_hash != account_binding_hash:
            return "account_binding_mismatch"
        if not self._base_policy_allows(purpose=purpose):
            return "policy_requirements_not_satisfied"
        return None

    def _base_policy_allows(self, *, purpose: str) -> bool:
        return (
            purpose in self.approved_purposes
            and bool(self.org_name.strip())
            and bool(self.org_domain.strip())
            and bool(self.search_keywords)
            and self.retention_days > 0
            and bool(self.pii_policy.strip())
            and bool(self.operator_id.strip())
            and bool(self.operator_name.strip())
            and bool(self.created_at.strip())
        )
