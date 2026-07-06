from __future__ import annotations

import hashlib
from collections.abc import Sequence

from seektalent.config import AppSettings
from seektalent.providers.liepin.compliance import ComplianceGate
from seektalent.providers.liepin.store import LiepinStore


def local_opencli_liepin_source_context(
    source_ids: Sequence[str],
    settings: AppSettings | None,
) -> dict[str, str | int | bool | None] | None:
    if "liepin" not in {str(source_id) for source_id in source_ids}:
        return None
    worker_mode = str(getattr(settings, "liepin_worker_mode", "") or "")
    context: dict[str, str | int | bool | None] = {
        "actor_id": "local",
        "connection_id": "liepin-opencli",
        "provider_account_hash": "liepin-opencli-local",
        "tenant_id": "local",
        "workspace_id": "default",
    }
    if worker_mode:
        context["backend_mode"] = worker_mode
    if worker_mode == "opencli":
        compliance_gate_ref = _ensure_local_opencli_liepin_context(settings=settings, context=context)
        if compliance_gate_ref:
            context["compliance_gate_ref"] = compliance_gate_ref
    return context


def _ensure_local_opencli_liepin_context(
    *,
    settings: AppSettings | None,
    context: dict[str, str | int | bool | None],
) -> str | None:
    if settings is None:
        return None
    try:
        connector_db_path = getattr(settings, "liepin_connector_db_path")
        session_store_key_id = str(getattr(settings, "liepin_session_store_key_id", "") or "local-opencli-session")
        db_path = settings.resolve_workspace_path(connector_db_path)
    except (AttributeError, TypeError, ValueError):
        return None

    tenant_id = str(context["tenant_id"] or "local")
    workspace_id = str(context["workspace_id"] or "default")
    actor_id = str(context["actor_id"] or "local")
    connection_id = str(context["connection_id"] or "liepin-opencli")
    provider_account_hash = str(context["provider_account_hash"] or "liepin-opencli-local")

    store = LiepinStore(db_path)
    connection = store.get_connection(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        connection_id=connection_id,
    )
    if connection is None:
        gate = ComplianceGate(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            provider_account_hash=None,
            status="pending_account_binding",
            candidate_personal_info_processing_basis="human initiated recruiting search",
            personal_information_processor="SeekTalent local workbench",
            operator_audit_owner=actor_id,
            account_holder_authorized=True,
            human_initiated_recruiting=True,
            allowed_purposes=["search"],
            retention_policy="run_debug_short",
            deletion_sla_days=7,
            deletion_path="local workspace retention cleanup",
            raw_payload_access_scope="run_only",
            raw_detail_retention_allowed_after_debug=False,
            fixture_export_allowed=False,
            policy_ref="workbench-v2-local-opencli-v1",
        )
        gate_ref = store.create_compliance_gate(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            gate=gate,
            purpose="search",
        )
        store.create_connection(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            compliance_gate_ref=gate_ref,
            connection_id=connection_id,
        )
    else:
        gate_ref = connection.compliance_gate_ref

    state_hash = hashlib.sha256(f"{connection_id}:{provider_account_hash}".encode("utf-8")).hexdigest()
    session = store.record_session_metadata(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        connection_id=connection_id,
        provider_account_hash=provider_account_hash,
        session_store_key_id=session_store_key_id,
        encrypted_state_sha256=state_hash,
    )
    if session is None:
        return gate_ref
    store.approve_connection_account_hash(
        gate_ref=gate_ref,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        connection_id=connection_id,
        provider_account_hash=provider_account_hash,
    )
    return gate_ref
