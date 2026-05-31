from __future__ import annotations

import hashlib

from seektalent.config import AppSettings
from seektalent.corpus.store import DEFAULT_TENANT_ID
from seektalent.providers.liepin.store import LiepinStore
from seektalent_ui.workbench_store import WorkbenchUser


def bind_observed_liepin_account(
    *,
    settings: AppSettings,
    user: WorkbenchUser,
    connection_id: str,
    compliance_gate_ref: str,
    observed_provider_account_subject: str,
) -> str | None:
    if not settings.liepin_account_binding_secret:
        return None
    store = LiepinStore(settings.resolve_workspace_path(settings.liepin_connector_db_path))
    store.record_connection_account_subject(
        tenant_id=DEFAULT_TENANT_ID,
        workspace_id=user.workspace_id,
        actor_id=user.user_id,
        connection_id=connection_id,
        observed_provider_account_subject=observed_provider_account_subject,
    )
    provider_account_hash = store.bind_connection_account(
        gate_ref=compliance_gate_ref,
        tenant_id=DEFAULT_TENANT_ID,
        workspace_id=user.workspace_id,
        actor_id=user.user_id,
        connection_id=connection_id,
        secret=settings.liepin_account_binding_secret,
    )
    if provider_account_hash is None:
        return None
    state_hash = hashlib.sha256(f"{connection_id}:{provider_account_hash}".encode("utf-8")).hexdigest()
    session = store.record_session_metadata(
        tenant_id=DEFAULT_TENANT_ID,
        workspace_id=user.workspace_id,
        actor_id=user.user_id,
        connection_id=connection_id,
        provider_account_hash=provider_account_hash,
        session_store_key_id=settings.liepin_session_store_key_id,
        encrypted_state_sha256=state_hash,
    )
    return provider_account_hash if session is not None else None
