from __future__ import annotations

from pathlib import Path

from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError

from tests.test_workbench_api import _approve_requirement_review, _ensure_local_actor, _client, _create_session, _workbench_user_from_actor_payload
from tests.test_workbench_liepin_browser_session_probe import (
    ProbeLiepinWorker,
    _assert_runtime_start,
    _get_liepin_card,
    _install_probe_worker,
    _opencli_settings,
    _reset_probe_worker,
    _started_source,
)


def test_recovered_opencli_connection_does_not_requeue_liepin_after_runtime_job_started(tmp_path: Path) -> None:
    with _client(tmp_path, settings_overrides=_opencli_settings()) as client:
        actor_payload = _ensure_local_actor(client)
        user = _workbench_user_from_actor_payload(actor_payload)
        store = client.app.state.workbench_store
        connection, _created = store.get_or_create_liepin_source_connection(user=user)
        connected = store.mark_liepin_connection_connected(
            user=user,
            connection_id=connection.connection_id,
            provider_account_hash="acct_hash_bound",
        )
        assert connected is not None
        worker = ProbeLiepinWorker(
            status="ready",
            provider_account_hash="acct_hash_bound",
            readiness_error=LiepinWorkerModeError(
                "OpenCLI extension disconnected: /secret/path",
                code="liepin_opencli_extension_disconnected",
            ),
        )
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_requirement_review(client, session["sessionId"])
        _reset_probe_worker(worker)

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )
        assert response.status_code == 202, response.text
        payload = response.json()
        _assert_runtime_start(payload, ["cts"])
        assert payload["blockedSources"] == [
            {
                "sourceRunId": _started_source(session, "liepin")["sourceRunId"],
                "sourceKind": "liepin",
                "reason": "source_browser_extension_disconnected",
            }
        ]

        worker.readiness_error = None
        _session, liepin_card = _get_liepin_card(client, session["sessionId"])
        assert liepin_card["connectionStatus"] == "connected"
        assert liepin_card["status"] == "blocked"
        assert liepin_card["warningCode"] == "source_browser_extension_disconnected"
