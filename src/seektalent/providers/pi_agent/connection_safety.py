from __future__ import annotations

from datetime import datetime
from typing import Literal, NoReturn

from pydantic import field_validator

from seektalent.providers.pi_agent.contracts import NonEmptyStr, PiBoundaryModel, _require_timezone_aware


DEFAULT_SENSITIVE_MATERIAL_POLICY_ID = "liepin-sensitive-material-protection-v1"
TransportMode = Literal["local_only", "remote_e2e_allowed"]


class ProviderConnectionSafetyValidationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProviderConnectionSafetyRecord(PiBoundaryModel):
    schema_version: Literal["provider-connection-safety-v1"]
    provider: Literal["liepin"]
    connection_id: NonEmptyStr
    workspace_id: NonEmptyStr
    user_id: NonEmptyStr
    provider_account_hash: NonEmptyStr
    login_state: Literal["verified", "expired", "verification_required"]
    connection_owner_verified: bool
    sensitive_material_policy_id: NonEmptyStr
    transport_policy: Literal["local_only", "remote_e2e_allowed", "remote_forbidden"]
    verified_at: datetime
    expires_at: datetime
    issued_by: Literal["workflow_runtime"]
    policy_version: NonEmptyStr

    @field_validator("verified_at", "expires_at")
    @classmethod
    def datetimes_must_be_timezone_aware(cls, value: datetime, info: object) -> datetime:
        field_name = getattr(info, "field_name", "datetime")
        return _require_timezone_aware(value, field_name)


def validate_provider_connection_safety(
    record: ProviderConnectionSafetyRecord | None,
    *,
    provider: Literal["liepin"],
    connection_id: str,
    workspace_id: str,
    user_id: str,
    provider_account_hash: str,
    transport: TransportMode,
    now: datetime,
    sensitive_material_policy_id: str = DEFAULT_SENSITIVE_MATERIAL_POLICY_ID,
) -> None:
    _require_timezone_aware(now, "now")
    if record is None:
        _raise("connection_safety_missing")
    assert record is not None
    if record.provider != provider:
        _raise("connection_safety_provider_mismatch")
    if record.connection_id != connection_id:
        _raise("connection_safety_connection_mismatch")
    if record.workspace_id != workspace_id:
        _raise("connection_safety_workspace_mismatch")
    if record.user_id != user_id:
        _raise("connection_safety_user_mismatch")
    if not record.connection_owner_verified:
        _raise("connection_safety_owner_unverified")
    if record.expires_at <= now:
        _raise("connection_safety_expired")
    if record.login_state != "verified":
        _raise("connection_safety_login_unverified")
    if record.provider_account_hash != provider_account_hash:
        _raise("connection_safety_provider_account_mismatch")
    if record.sensitive_material_policy_id != sensitive_material_policy_id:
        _raise("connection_safety_material_policy_mismatch")
    if not _transport_allowed(record.transport_policy, transport):
        _raise("connection_safety_transport_denied")


def _transport_allowed(record_policy: str, requested_transport: TransportMode) -> bool:
    if requested_transport == "local_only":
        return record_policy in {"local_only", "remote_e2e_allowed", "remote_forbidden"}
    return record_policy == "remote_e2e_allowed"


def _raise(code: str) -> NoReturn:
    raise ProviderConnectionSafetyValidationError(code)
