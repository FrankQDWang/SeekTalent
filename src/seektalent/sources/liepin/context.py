from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


RuntimeLiepinContextPayload = Mapping[str, str | int | bool | None]


@dataclass(frozen=True, kw_only=True)
class RuntimeLiepinContext:
    tenant_id: str = "local"
    workspace_id: str = "default"
    actor_id: str = "local"
    connection_id: str | None = None
    compliance_gate_ref: str | None = None
    provider_account_hash: str | None = None
    backend_mode: str | None = None

    @classmethod
    def from_mapping(cls, values: Mapping[str, str | int | bool | None] | None) -> RuntimeLiepinContext:
        values = values or {}
        return cls(
            tenant_id=_context_text(values.get("tenant_id"), default="local") or "local",
            workspace_id=_context_text(values.get("workspace_id"), default="default") or "default",
            actor_id=_context_text(values.get("actor_id"), default="local") or "local",
            connection_id=_context_text(values.get("connection_id")),
            compliance_gate_ref=_context_text(values.get("compliance_gate_ref")),
            provider_account_hash=_context_text(values.get("provider_account_hash")),
            backend_mode=_context_text(values.get("backend_mode")),
        )

    def to_provider_context(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "liepin_tenant_id": self.tenant_id,
                "liepin_workspace_id": self.workspace_id,
                "liepin_actor_id": self.actor_id,
                "liepin_connection_id": self.connection_id,
                "liepin_compliance_gate_ref": self.compliance_gate_ref,
                "liepin_provider_account_hash": self.provider_account_hash,
            }.items()
            if value is not None
        }

    def to_provider_actor_context(self) -> dict[str, str]:
        return {
            "liepin_tenant_id": self.tenant_id,
            "liepin_workspace_id": self.workspace_id,
            "liepin_actor_id": self.actor_id,
        }

    def to_runtime_payload(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "tenant_id": self.tenant_id,
                "workspace_id": self.workspace_id,
                "actor_id": self.actor_id,
                "connection_id": self.connection_id,
                "compliance_gate_ref": self.compliance_gate_ref,
                "provider_account_hash": self.provider_account_hash,
                "backend_mode": self.backend_mode,
            }.items()
            if value is not None
        }

    def to_safe_posture(self) -> dict[str, str | bool]:
        posture: dict[str, str | bool] = {
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
        }
        if self.connection_id is not None:
            posture["connection_id"] = self.connection_id
        if self.backend_mode is not None:
            posture["backend_mode"] = self.backend_mode
        if self.compliance_gate_ref is not None:
            posture["compliance_gate_bound"] = True
        if self.provider_account_hash is not None:
            posture["provider_account_bound"] = True
        return posture


type RuntimeLiepinContextInput = RuntimeLiepinContext | RuntimeLiepinContextPayload


def normalize_runtime_liepin_context(
    value: object | None,
) -> RuntimeLiepinContext:
    if value is None:
        return RuntimeLiepinContext.from_mapping(None)
    if isinstance(value, RuntimeLiepinContext):
        return value
    if isinstance(value, Mapping):
        payload: dict[str, str | int | bool | None] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            if item is None or isinstance(item, (str, int, bool)):
                payload[key] = item
        return RuntimeLiepinContext.from_mapping(payload)
    raise TypeError("liepin_context_invalid")


def _context_text(value: object, *, default: str | None = None) -> str | None:
    if value is None:
        return default
    text = str(value).strip()
    return text or default
