from __future__ import annotations

from fastapi import Request

from seektalent_ui.workbench_source_connection_routes import (
    RECOVERABLE_LIEPIN_BROWSER_CHANNEL_CODES,
    refresh_liepin_opencli_connection_if_ready,
)
from seektalent_ui.workbench_store import WorkbenchSession, WorkbenchStore, WorkbenchUser


async def recover_liepin_session(
    *,
    request: Request,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session: WorkbenchSession,
) -> WorkbenchSession:
    connection = await refresh_liepin_opencli_connection_if_ready(request=request, store=store, user=user)
    if connection is None or connection.status != "connected" or connection.provider_account_hash is None:
        return session
    session = store.get_workbench_session(user=user, session_id=session.session_id) or session

    recovered = False
    for source_run in session.source_runs:
        if (
            source_run.source_kind != "liepin"
            or source_run.warning_code not in RECOVERABLE_LIEPIN_BROWSER_CHANNEL_CODES
        ):
            continue
        updated = store.mark_liepin_connection_connected_for_source_run(
            user=user,
            connection_id=connection.connection_id,
            session_id=session.session_id,
            source_run_id=source_run.source_run_id,
            provider_account_hash=connection.provider_account_hash,
            compliance_gate_ref=connection.compliance_gate_ref,
        )
        recovered = recovered or updated is not None

    if not recovered:
        return session
    return store.get_workbench_session(user=user, session_id=session.session_id) or session
