from __future__ import annotations

from dataclasses import dataclass

from seektalent.providers.liepin.security import hmac_provider_account_hash
from seektalent.providers.liepin.store import LiepinStore


@dataclass(frozen=True)
class LiepinSessionMetadata:
    connection_id: str
    tenant_id: str
    workspace_id: str
    actor_id: str
    status: str
    provider_account_hash: str | None
    session_store_key_id: str | None
    encrypted_state_sha256: str | None
    session_updated_at: str | None
    revoked_at: str | None


class ProtectedLiepinSessionStore:
    def __init__(self, store: LiepinStore) -> None:
        self.store = store

    def record_ready_session(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        connection_id: str,
        provider_account_subject: str,
        hmac_secret: str,
        session_store_key_id: str,
        encrypted_state_sha256: str,
    ) -> LiepinSessionMetadata | None:
        if not provider_account_subject.strip():
            return None
        row = self.store.record_session_metadata(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            connection_id=connection_id,
            provider_account_hash=hmac_provider_account_hash(hmac_secret, provider_account_subject),
            session_store_key_id=session_store_key_id,
            encrypted_state_sha256=encrypted_state_sha256,
        )
        return _metadata_from_row(row)

    def get_session_metadata(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        connection_id: str,
    ) -> LiepinSessionMetadata | None:
        row = self.store.get_session_metadata(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            connection_id=connection_id,
        )
        return _metadata_from_row(row)

    def revoke_session(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        connection_id: str,
        reason: str,
    ) -> bool:
        return self.store.revoke_session(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            connection_id=connection_id,
            reason=reason,
        )


def _metadata_from_row(row: dict[str, object] | None) -> LiepinSessionMetadata | None:
    if row is None:
        return None
    return LiepinSessionMetadata(
        connection_id=str(row["connection_id"]),
        tenant_id=str(row["tenant_id"]),
        workspace_id=str(row["workspace_id"]),
        actor_id=str(row["actor_id"]),
        status=str(row["status"]),
        provider_account_hash=_optional_str(row["provider_account_hash"]),
        session_store_key_id=_optional_str(row["session_store_key_id"]),
        encrypted_state_sha256=_optional_str(row["encrypted_state_sha256"]),
        session_updated_at=_optional_str(row["session_updated_at"]),
        revoked_at=_optional_str(row["revoked_at"]),
    )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)
