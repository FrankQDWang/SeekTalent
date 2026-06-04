from __future__ import annotations

import hashlib

from seektalent.config import AppSettings
from seektalent.providers.liepin.compliance import ComplianceGate
from seektalent.providers.liepin.store import LiepinStore
from seektalent_ui.workbench_store import DEFAULT_TENANT_ID, WorkbenchSourceConnection, WorkbenchUser


def ensure_workbench_liepin_provider_connection(
    *,
    settings: AppSettings,
    user: WorkbenchUser,
    connection: WorkbenchSourceConnection,
) -> str:
    store = LiepinStore(settings.resolve_workspace_path(settings.liepin_connector_db_path))
    if connection.compliance_gate_ref:
        existing = store.get_connection(
            tenant_id=DEFAULT_TENANT_ID,
            workspace_id=user.workspace_id,
            actor_id=user.user_id,
            connection_id=connection.connection_id,
        )
        existing_gate = store.get_compliance_gate(
            gate_ref=connection.compliance_gate_ref,
            tenant_id=DEFAULT_TENANT_ID,
            workspace_id=user.workspace_id,
            actor_id=user.user_id,
        )
        if existing is not None and existing.compliance_gate_ref == connection.compliance_gate_ref and existing_gate:
            _restore_bound_liepin_provider_account(
                settings=settings,
                store=store,
                user=user,
                connection=connection,
                compliance_gate_ref=connection.compliance_gate_ref,
            )
            return connection.compliance_gate_ref
    gate = ComplianceGate(
        tenant_id=DEFAULT_TENANT_ID,
        workspace_id=user.workspace_id,
        actor_id=user.user_id,
        provider_account_hash=None,
        status="pending_account_binding",
        candidate_personal_info_processing_basis="operator_initiated_recruiting_search",
        personal_information_processor="local_seek_talent_workbench",
        operator_audit_owner=user.user_id,
        account_holder_authorized=True,
        human_initiated_recruiting=True,
        allowed_purposes=["search"],
        retention_policy="workspace_recruiting_record",
        deletion_sla_days=30,
        deletion_path="local_workbench_delete_flow",
        raw_payload_access_scope="run_only",
        raw_detail_retention_allowed_after_debug=False,
        fixture_export_allowed=False,
        policy_ref="workbench-runtime-source-lane-v1",
    )
    gate_ref = store.create_compliance_gate(
        tenant_id=DEFAULT_TENANT_ID,
        workspace_id=user.workspace_id,
        actor_id=user.user_id,
        gate=gate,
        purpose="search",
    )
    store.create_connection(
        tenant_id=DEFAULT_TENANT_ID,
        workspace_id=user.workspace_id,
        actor_id=user.user_id,
        compliance_gate_ref=gate_ref,
        connection_id=connection.connection_id,
    )
    _restore_bound_liepin_provider_account(
        settings=settings,
        store=store,
        user=user,
        connection=connection,
        compliance_gate_ref=gate_ref,
    )
    return gate_ref


def _restore_bound_liepin_provider_account(
    *,
    settings: AppSettings,
    store: LiepinStore,
    user: WorkbenchUser,
    connection: WorkbenchSourceConnection,
    compliance_gate_ref: str,
) -> None:
    if not connection.provider_account_hash:
        return
    state_hash = hashlib.sha256(
        f"{connection.connection_id}:{connection.provider_account_hash}".encode("utf-8")
    ).hexdigest()
    session = store.record_session_metadata(
        tenant_id=DEFAULT_TENANT_ID,
        workspace_id=user.workspace_id,
        actor_id=user.user_id,
        connection_id=connection.connection_id,
        provider_account_hash=connection.provider_account_hash,
        session_store_key_id=settings.liepin_session_store_key_id,
        encrypted_state_sha256=state_hash,
    )
    if session is not None:
        store.approve_connection_account_hash(
            gate_ref=compliance_gate_ref,
            tenant_id=DEFAULT_TENANT_ID,
            workspace_id=user.workspace_id,
            actor_id=user.user_id,
            connection_id=connection.connection_id,
            provider_account_hash=connection.provider_account_hash,
        )


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


def refresh_workbench_liepin_provider_session_safety(
    *,
    settings: AppSettings,
    user: WorkbenchUser,
    connection: WorkbenchSourceConnection,
) -> bool:
    if not connection.provider_account_hash or not connection.compliance_gate_ref:
        return False
    store = LiepinStore(settings.resolve_workspace_path(settings.liepin_connector_db_path))
    state_hash = hashlib.sha256(
        f"{connection.connection_id}:{connection.provider_account_hash}".encode("utf-8")
    ).hexdigest()
    session = store.record_session_metadata(
        tenant_id=DEFAULT_TENANT_ID,
        workspace_id=user.workspace_id,
        actor_id=user.user_id,
        connection_id=connection.connection_id,
        provider_account_hash=connection.provider_account_hash,
        session_store_key_id=settings.liepin_session_store_key_id,
        encrypted_state_sha256=state_hash,
    )
    return session is not None
