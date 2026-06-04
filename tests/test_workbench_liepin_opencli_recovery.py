from __future__ import annotations

import sqlite3
from pathlib import Path

from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError

from tests.test_workbench_api import (
    _approve_requirement_review,
    _bootstrap_and_login,
    _client,
    _create_session,
    _csrf_header,
    _db_path,
    _workbench_user_from_bootstrap,
)
from tests.test_workbench_liepin_browser_session_probe import (
    ProbeLiepinWorker,
    _bind_workbench_liepin_account,
    _install_probe_worker,
    _opencli_settings,
    _reset_probe_worker,
    _started_source,
    assert_no_probe_leaks,
)


def test_get_session_recovers_opencli_channel_block_after_connection_ready(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        bootstrap = _bootstrap_and_login(client)
        user = _workbench_user_from_bootstrap(bootstrap)
        store = client.app.state.workbench_store
        connection, _created = store.get_or_create_liepin_source_connection(user=user)
        provider_account_hash = _bind_workbench_liepin_account(client, user=user, connection=connection)
        worker = ProbeLiepinWorker(
            status="ready",
            provider_account_hash=provider_account_hash,
            readiness_error=LiepinWorkerModeError(
                "OpenCLI extension disconnected: /secret/path",
                code="liepin_opencli_extension_disconnected",
            ),
        )
        _install_probe_worker(client, worker)
        client.app.state.settings.liepin_browser_action_backend = "opencli"

        session = _create_session(client, source_kinds=["liepin"])
        _approve_requirement_review(client, session["sessionId"])

        blocked = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )
        assert blocked.status_code == 202, blocked.text
        assert blocked.json()["blockedSources"] == [
            {
                "sourceRunId": _started_source(session, "liepin")["sourceRunId"],
                "sourceKind": "liepin",
                "reason": "source_browser_extension_disconnected",
            }
        ]

        worker.readiness_error = None
        recovered = client.get(
            f"/api/workbench/sessions/{session['sessionId']}",
            headers=_csrf_header(client),
        )

        assert recovered.status_code == 200, recovered.text
        liepin_card = next(card for card in recovered.json()["sourceCards"] if card["sourceKind"] == "liepin")
        assert liepin_card["connectionStatus"] == "connected"
        assert liepin_card["status"] == "queued"
        assert liepin_card["authState"] == "not_required"
        assert liepin_card["warningCode"] is None
        assert worker.readiness_calls == 2
        assert_no_probe_leaks(recovered.text)


def test_get_session_recovers_unbound_opencli_login_required_after_session_ready(tmp_path: Path) -> None:
    with _client(tmp_path, settings_overrides=_opencli_settings()) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(status="login_required", provider_account_hash=None)
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["liepin"])
        blocked_card = next(card for card in session["sourceCards"] if card["sourceKind"] == "liepin")
        assert blocked_card["connectionStatus"] == "login_required"
        assert blocked_card["status"] == "blocked"
        assert blocked_card["authState"] == "login_required"
        assert blocked_card["warningCode"] == "source_login_required"

        worker.status = "ready"
        worker.provider_account_hash = "acct_hash_browser_ready"
        _reset_probe_worker(worker)
        recovered = client.get(
            f"/api/workbench/sessions/{session['sessionId']}",
            headers=_csrf_header(client),
        )

        assert recovered.status_code == 200, recovered.text
        liepin_card = next(card for card in recovered.json()["sourceCards"] if card["sourceKind"] == "liepin")
        assert liepin_card["connectionStatus"] == "connected"
        assert liepin_card["status"] == "queued"
        assert liepin_card["authState"] == "not_required"
        assert liepin_card["warningCode"] is None
        assert worker.probe_calls == [
            {
                "connection_id": liepin_card["connectionId"],
                "tenant": "local",
                "workspace": "default",
                "provider_account_hash": None,
            }
        ]
        assert_no_probe_leaks(recovered.text, "acct_hash_browser_ready")


def test_get_session_recovers_only_current_opencli_channel_block(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        bootstrap = _bootstrap_and_login(client)
        user = _workbench_user_from_bootstrap(bootstrap)
        store = client.app.state.workbench_store
        connection, _created = store.get_or_create_liepin_source_connection(user=user)
        provider_account_hash = _bind_workbench_liepin_account(client, user=user, connection=connection)
        worker = ProbeLiepinWorker(
            status="ready",
            provider_account_hash=provider_account_hash,
            readiness_error=LiepinWorkerModeError(
                "OpenCLI extension disconnected: /secret/path",
                code="liepin_opencli_extension_disconnected",
            ),
        )
        _install_probe_worker(client, worker)
        client.app.state.settings.liepin_browser_action_backend = "opencli"

        first = _create_session(client, source_kinds=["liepin"])
        _approve_requirement_review(client, first["sessionId"])
        second = _create_session(client, source_kinds=["liepin"])
        _approve_requirement_review(client, second["sessionId"])

        for session in (first, second):
            blocked = client.post(
                f"/api/workbench/sessions/{session['sessionId']}/start",
                headers=_csrf_header(client),
            )
            assert blocked.status_code == 202, blocked.text
            assert blocked.json()["blockedSources"] == [
                {
                    "sourceRunId": _started_source(session, "liepin")["sourceRunId"],
                    "sourceKind": "liepin",
                    "reason": "source_browser_extension_disconnected",
                }
            ]

        worker.readiness_error = None
        recovered = client.get(
            f"/api/workbench/sessions/{first['sessionId']}",
            headers=_csrf_header(client),
        )

        assert recovered.status_code == 200, recovered.text
        recovered_card = next(card for card in recovered.json()["sourceCards"] if card["sourceKind"] == "liepin")
        assert recovered_card["status"] == "queued"
        assert recovered_card["authState"] == "not_required"
        assert recovered_card["warningCode"] is None
        with sqlite3.connect(_db_path(tmp_path)) as conn:
            untouched_status = conn.execute(
                """
                SELECT status, auth_state, warning_code
                FROM source_runs
                WHERE source_run_id = ?
                """,
                (_started_source(second, "liepin")["sourceRunId"],),
            ).fetchone()
        assert untouched_status == (
            "blocked",
            "login_required",
            "liepin_opencli_extension_disconnected",
        )


def test_get_session_does_not_recover_liepin_account_mismatch_block(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        bootstrap = _bootstrap_and_login(client)
        user = _workbench_user_from_bootstrap(bootstrap)
        store = client.app.state.workbench_store
        connection, _created = store.get_or_create_liepin_source_connection(user=user)
        provider_account_hash = _bind_workbench_liepin_account(client, user=user, connection=connection)
        worker = ProbeLiepinWorker(status="ready", provider_account_hash=provider_account_hash)
        _install_probe_worker(client, worker)
        client.app.state.settings.liepin_browser_action_backend = "opencli"

        session = _create_session(client, source_kinds=["liepin"])
        source_run_id = _started_source(session, "liepin")["sourceRunId"]
        with sqlite3.connect(_db_path(tmp_path)) as conn:
            conn.execute(
                """
                UPDATE source_runs
                SET status = 'blocked',
                    auth_state = 'login_required',
                    warning_code = 'liepin_browser_account_mismatch',
                    warning_message = 'account mismatch'
                WHERE source_run_id = ?
                """,
                (source_run_id,),
            )

        response = client.get(
            f"/api/workbench/sessions/{session['sessionId']}",
            headers=_csrf_header(client),
        )

        assert response.status_code == 200, response.text
        liepin_card = next(card for card in response.json()["sourceCards"] if card["sourceKind"] == "liepin")
        assert liepin_card["connectionStatus"] == "connected"
        assert liepin_card["status"] == "blocked"
        assert liepin_card["authState"] == "login_required"
        assert liepin_card["warningCode"] == "source_account_mismatch"
