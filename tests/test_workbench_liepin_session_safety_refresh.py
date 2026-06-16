from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from seektalent.providers.liepin.adapter import LiepinStoreConnectionSafetyResolver
from seektalent.providers.liepin.connection_safety import (
    ProviderConnectionSafetyValidationError,
    validate_provider_connection_safety,
)
from seektalent.providers.liepin.store import LiepinStore
from tests.test_workbench_api import (
    _approve_requirement_review,
    _ensure_local_actor,
    _client,
    _create_session,
        _workbench_user_from_actor_payload,
)
from tests.test_workbench_liepin_browser_session_probe import (
    ProbeLiepinWorker,
    _assert_runtime_start,
    _bind_workbench_liepin_account,
    _install_probe_worker,
)


def test_start_session_opencli_mode_refreshes_provider_session_safety_metadata(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        actor_payload = _ensure_local_actor(client)
        user = _workbench_user_from_actor_payload(actor_payload)
        store = client.app.state.workbench_store
        connection, _created = store.get_or_create_liepin_source_connection(user=user)
        provider_account_hash = _bind_workbench_liepin_account(
            client,
            user=user,
            connection=connection,
        )
        connection = store.get_source_connection(user=user, connection_id=connection.connection_id)
        assert connection is not None
        assert connection.compliance_gate_ref is not None
        provider_store = LiepinStore(
            client.app.state.settings.resolve_workspace_path(
                client.app.state.settings.liepin_connector_db_path
            )
        )
        expired_at = (datetime.now(UTC) - timedelta(days=2)).isoformat(timespec="seconds")
        with sqlite3.connect(provider_store.db_path) as conn:
            conn.execute(
                """
                UPDATE liepin_connections
                SET session_updated_at = ?
                WHERE tenant_id = ? AND workspace_id = ? AND actor_id = ? AND connection_id = ?
                """,
                (
                    expired_at,
                    "local",
                    user.workspace_id,
                    user.user_id,
                    connection.connection_id,
                ),
            )
        expired_record = LiepinStoreConnectionSafetyResolver(provider_store).resolve_liepin_connection_safety(
            tenant_id="local",
            workspace_id=user.workspace_id,
            actor_id=user.user_id,
            connection_id=connection.connection_id,
            compliance_gate_ref=connection.compliance_gate_ref,
            provider_account_hash=provider_account_hash,
            requested_transport="local_only",
            now=datetime.now(UTC),
        )
        with pytest.raises(ProviderConnectionSafetyValidationError) as expired_error:
            validate_provider_connection_safety(
                expired_record,
                provider="liepin",
                connection_id=connection.connection_id,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                provider_account_hash=provider_account_hash,
                transport="local_only",
                now=datetime.now(UTC),
            )
        assert expired_error.value.code == "connection_safety_expired"

        worker = ProbeLiepinWorker(status="ready", provider_account_hash=provider_account_hash)
        _install_probe_worker(client, worker)
        client.app.state.settings.liepin_browser_action_backend = "opencli"
        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_requirement_review(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )

        assert response.status_code == 202, response.text
        _assert_runtime_start(response.json(), ["cts", "liepin"])
        refreshed_record = LiepinStoreConnectionSafetyResolver(provider_store).resolve_liepin_connection_safety(
            tenant_id="local",
            workspace_id=user.workspace_id,
            actor_id=user.user_id,
            connection_id=connection.connection_id,
            compliance_gate_ref=connection.compliance_gate_ref,
            provider_account_hash=provider_account_hash,
            requested_transport="local_only",
            now=datetime.now(UTC),
        )
        validate_provider_connection_safety(
            refreshed_record,
            provider="liepin",
            connection_id=connection.connection_id,
            workspace_id=user.workspace_id,
            user_id=user.user_id,
            provider_account_hash=provider_account_hash,
            transport="local_only",
            now=datetime.now(UTC),
        )
