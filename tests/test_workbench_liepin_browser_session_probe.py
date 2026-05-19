from __future__ import annotations

import sqlite3

from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError
from seektalent.providers.liepin.worker_contracts import SessionStatus

from tests.test_workbench_api import (
    FakeLiepinCardWorkerClient,
    _approve_triage,
    _bootstrap_and_login,
    _client,
    _create_session,
    _csrf_header,
    _db_path,
    _started_source,
    _workbench_user_from_bootstrap,
)


FORBIDDEN_PUBLIC_STRINGS = (
    "cookie",
    "storageState",
    "raw_provider_payload",
    "Authorization",
    "Bearer ",
    "/Users/",
    "localStorage",
    "session_secret",
    "pi command missing",
)


def assert_no_probe_leaks(text: str, *extra_forbidden: str) -> None:
    lowered = text.lower()
    for forbidden in (*FORBIDDEN_PUBLIC_STRINGS, *extra_forbidden):
        assert forbidden.lower() not in lowered


class ProbeLiepinWorker(FakeLiepinCardWorkerClient):
    def __init__(
        self,
        *,
        status: str,
        provider_account_hash: str | None = "acct_hash_ready",
        error: Exception | None = None,
    ) -> None:
        super().__init__()
        self.status = status
        self.provider_account_hash = provider_account_hash
        self.error = error
        self.probe_calls: list[dict[str, object]] = []

    async def session_status(
        self,
        *,
        connection_id: str,
        tenant: str | None = None,
        workspace: str | None = None,
        provider_account_hash: str | None = None,
    ) -> SessionStatus:
        self.probe_calls.append(
            {
                "connection_id": connection_id,
                "tenant": tenant,
                "workspace": workspace,
                "provider_account_hash": provider_account_hash,
            }
        )
        if self.error is not None:
            raise self.error
        return SessionStatus(
            connectionId=connection_id,
            status=self.status,
            providerAccountHash=self.provider_account_hash if self.status == "ready" else None,
        )


class QueueingRaceLiepinWorker(ProbeLiepinWorker):
    def __init__(self, *, store, user, session_id: str, source_run_id: str) -> None:
        super().__init__(status="login_required", provider_account_hash=None)
        self.store = store
        self.user = user
        self.session_id = session_id
        self.source_run_id = source_run_id

    async def session_status(
        self,
        *,
        connection_id: str,
        tenant: str | None = None,
        workspace: str | None = None,
        provider_account_hash: str | None = None,
    ) -> SessionStatus:
        self.probe_calls.append(
            {
                "connection_id": connection_id,
                "tenant": tenant,
                "workspace": workspace,
                "provider_account_hash": provider_account_hash,
            }
        )
        self.store.mark_liepin_connection_connected_for_source_run(
            user=self.user,
            connection_id=connection_id,
            session_id=self.session_id,
            source_run_id=self.source_run_id,
            provider_account_hash="acct_hash_race_ready",
        )
        self.store.start_source_run_job(
            user=self.user,
            session_id=self.session_id,
            source_run_id=self.source_run_id,
        )
        return SessionStatus(connectionId=connection_id, status="login_required", providerAccountHash=None)


def _install_probe_worker(client, worker: ProbeLiepinWorker) -> None:
    client.app.state.liepin_worker_client = worker
    client.app.state.workbench_job_runner.liepin_worker_client = worker


def _get_liepin_card(client, session_id: str) -> tuple[dict, dict]:
    session_response = client.get(
        f"/api/workbench/sessions/{session_id}",
        headers=_csrf_header(client),
    )
    assert session_response.status_code == 200, session_response.text
    liepin_card = next(
        card for card in session_response.json()["sourceCards"] if card["sourceKind"] == "liepin"
    )
    return session_response.json(), liepin_card


def _assert_public_probe_surfaces_do_not_leak(client, session_id: str, *extra_forbidden: str) -> None:
    session_response = client.get(
        f"/api/workbench/sessions/{session_id}",
        headers=_csrf_header(client),
    )
    session_events = client.get(
        f"/api/workbench/sessions/{session_id}/events",
        headers=_csrf_header(client),
    )
    global_events = client.get("/api/workbench/events", headers=_csrf_header(client))
    security_events = client.get("/api/workbench/security-audit-events", headers=_csrf_header(client))

    assert session_response.status_code == 200, session_response.text
    assert session_events.status_code == 200, session_events.text
    assert global_events.status_code == 200, global_events.text
    assert security_events.status_code == 200, security_events.text
    assert_no_probe_leaks(session_response.text, *extra_forbidden)
    assert_no_probe_leaks(session_events.text, *extra_forbidden)
    assert_no_probe_leaks(global_events.text, *extra_forbidden)
    assert_no_probe_leaks(security_events.text, *extra_forbidden)


def test_start_session_auto_probes_liepin_browser_session_and_starts_liepin(tmp_path) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(status="ready", provider_account_hash="acct_hash_browser_ready")
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["liepin"])
        _approve_triage(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["blockedSources"] == []
        assert len(payload["sourceRuns"]) == 1
        assert payload["sourceRuns"][0]["sourceKind"] == "liepin"
        assert worker.probe_calls
        assert_no_probe_leaks(response.text, "acct_hash_browser_ready")

        _session, liepin_card = _get_liepin_card(client, session["sessionId"])
        assert liepin_card["authState"] == "not_required"
        assert liepin_card["warningCode"] is None
        _assert_public_probe_surfaces_do_not_leak(client, session["sessionId"], "acct_hash_browser_ready")


def test_ready_probe_does_not_unblock_liepin_runs_from_other_sessions(tmp_path) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(status="ready", provider_account_hash="acct_hash_browser_ready")
        _install_probe_worker(client, worker)

        first_session = _create_session(client, source_kinds=["liepin"])
        second_session = _create_session(client, source_kinds=["liepin"])
        _approve_triage(client, first_session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{first_session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert response.status_code == 202, response.text
        first_payload = response.json()
        assert first_payload["blockedSources"] == []
        assert first_payload["sourceRuns"][0]["sourceKind"] == "liepin"

        _session, second_liepin = _get_liepin_card(client, second_session["sessionId"])
        assert second_liepin["status"] == "blocked"
        assert second_liepin["authState"] == "login_required"


def test_start_session_blocks_only_liepin_when_browser_login_is_required(tmp_path) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(status="login_required", provider_account_hash=None)
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_triage(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert response.status_code == 202, response.text
        payload = response.json()
        assert [run["sourceKind"] for run in payload["sourceRuns"]] == ["cts"]
        assert payload["blockedSources"] == [
            {
                "sourceRunId": _started_source(session, "liepin")["sourceRunId"],
                "sourceKind": "liepin",
                "reason": "liepin_browser_login_required",
            }
        ]

        session_payload, liepin_card = _get_liepin_card(client, session["sessionId"])
        assert liepin_card["status"] == "blocked"
        assert liepin_card["authState"] == "login_required"
        assert liepin_card["warningCode"] == "liepin_browser_login_required"
        assert "本机 Chrome" in liepin_card["warningMessage"]
        liepin_runtime = next(
            source
            for source in session_payload.get("runtimeSourceState", {}).get("sources", [])
            if source["sourceKind"] == "liepin"
        )
        assert liepin_runtime["reasonCode"] == "liepin_browser_login_required"
        assert_no_probe_leaks(response.text)
        _assert_public_probe_surfaces_do_not_leak(client, session["sessionId"])


def test_start_session_blocks_liepin_when_probe_backend_is_unavailable(tmp_path) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(
            status="login_required",
            error=LiepinWorkerModeError(
                "pi command missing: /secret/path",
                setup_status="disabled",
                code="blocked_backend_unavailable",
            ),
        )
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_triage(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert response.status_code == 202, response.text
        assert_no_probe_leaks(response.text)
        payload = response.json()
        assert [run["sourceKind"] for run in payload["sourceRuns"]] == ["cts"]
        assert payload["blockedSources"][0]["sourceKind"] == "liepin"
        assert payload["blockedSources"][0]["reason"] == "liepin_browser_probe_unavailable"

        session_payload, _liepin_card = _get_liepin_card(client, session["sessionId"])
        liepin_runtime = next(
            source
            for source in session_payload.get("runtimeSourceState", {}).get("sources", [])
            if source["sourceKind"] == "liepin"
        )
        assert liepin_runtime["reasonCode"] == "liepin_browser_probe_unavailable"
        _assert_public_probe_surfaces_do_not_leak(client, session["sessionId"])


def test_unexpected_probe_error_blocks_liepin_without_blocking_cts_or_leaking(tmp_path) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(
            status="login_required",
            error=ValueError("raw provider cookie secret"),
        )
        _install_probe_worker(client, worker)
        wake_calls: list[str] = []
        client.app.state.workbench_job_runner.wake = lambda: wake_calls.append("wake")

        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_triage(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert response.status_code == 202, response.text
        assert_no_probe_leaks(response.text, "raw provider cookie secret")
        payload = response.json()
        assert [run["sourceKind"] for run in payload["sourceRuns"]] == ["cts"]
        assert payload["blockedSources"] == [
            {
                "sourceRunId": _started_source(session, "liepin")["sourceRunId"],
                "sourceKind": "liepin",
                "reason": "liepin_browser_probe_unavailable",
            }
        ]
        assert wake_calls == ["wake"]

        session_payload, _liepin_card = _get_liepin_card(client, session["sessionId"])
        liepin_runtime = next(
            source
            for source in session_payload.get("runtimeSourceState", {}).get("sources", [])
            if source["sourceKind"] == "liepin"
        )
        assert liepin_runtime["reasonCode"] == "liepin_browser_probe_unavailable"
        _assert_public_probe_surfaces_do_not_leak(
            client,
            session["sessionId"],
            "raw provider cookie secret",
        )


def test_start_session_blocks_liepin_when_browser_account_does_not_match_bound_account(tmp_path) -> None:
    with _client(tmp_path) as client:
        bootstrap = _bootstrap_and_login(client)
        user = _workbench_user_from_bootstrap(bootstrap)
        store = client.app.state.workbench_store
        connection, _created = store.get_or_create_liepin_source_connection(user=user)
        store.mark_liepin_connection_connected(
            user=user,
            connection_id=connection.connection_id,
            provider_account_hash="acct_hash_bound",
        )
        worker = ProbeLiepinWorker(status="ready", provider_account_hash="acct_hash_other")
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["liepin"])
        _approve_triage(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["sourceRuns"] == []
        assert payload["blockedSources"] == [
            {
                "sourceRunId": _started_source(session, "liepin")["sourceRunId"],
                "sourceKind": "liepin",
                "reason": "liepin_browser_account_mismatch",
            }
        ]
        assert worker.probe_calls[0]["provider_account_hash"] == "acct_hash_bound"
        assert_no_probe_leaks(response.text, "acct_hash_bound", "acct_hash_other")

        session_payload, _liepin_card = _get_liepin_card(client, session["sessionId"])
        liepin_runtime = next(
            source
            for source in session_payload.get("runtimeSourceState", {}).get("sources", [])
            if source["sourceKind"] == "liepin"
        )
        assert liepin_runtime["reasonCode"] == "liepin_browser_account_mismatch"
        _assert_public_probe_surfaces_do_not_leak(
            client,
            session["sessionId"],
            "acct_hash_bound",
            "acct_hash_other",
        )


def test_repeated_start_does_not_reprobe_or_block_queued_liepin_run(tmp_path) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(status="ready", provider_account_hash="acct_hash_browser_ready")
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["liepin"])
        _approve_triage(client, session["sessionId"])

        first = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )
        assert first.status_code == 202, first.text
        assert first.json()["blockedSources"] == []
        assert len(worker.probe_calls) == 1

        worker.status = "login_required"
        worker.provider_account_hash = None
        second = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )
        assert second.status_code == 202, second.text
        assert second.json()["blockedSources"] == []
        assert len(worker.probe_calls) == 1


def test_probe_race_does_not_downgrade_already_queued_liepin_run_or_connection(tmp_path) -> None:
    with _client(tmp_path) as client:
        bootstrap = _bootstrap_and_login(client)
        user = _workbench_user_from_bootstrap(bootstrap)
        session = _create_session(client, source_kinds=["liepin"])
        _approve_triage(client, session["sessionId"])
        source_run_id = _started_source(session, "liepin")["sourceRunId"]
        worker = QueueingRaceLiepinWorker(
            store=client.app.state.workbench_store,
            user=user,
            session_id=session["sessionId"],
            source_run_id=source_run_id,
        )
        _install_probe_worker(client, worker)
        client.app.state.workbench_job_runner.wake = lambda: None

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["blockedSources"] == []
        assert len(payload["sourceRuns"]) == 1
        assert payload["sourceRuns"][0]["sourceRunId"] == source_run_id
        assert len(worker.probe_calls) == 1

        _session_payload, liepin_card = _get_liepin_card(client, session["sessionId"])
        assert liepin_card["status"] == "queued"
        assert liepin_card["authState"] == "not_required"
        assert liepin_card["warningCode"] is None
        assert liepin_card["connectionStatus"] == "connected"


def test_repeated_start_wakes_runner_for_existing_queued_job(tmp_path) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        session = _create_session(client, source_kinds=["cts"])
        _approve_triage(client, session["sessionId"])
        wake_calls: list[str] = []
        client.app.state.workbench_job_runner.wake = lambda: wake_calls.append("wake")

        first = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )
        second = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text
        assert len(first.json()["sourceRuns"]) == 1
        assert second.json()["sourceRuns"][0]["job"]["jobId"] == first.json()["sourceRuns"][0]["job"]["jobId"]
        assert wake_calls == ["wake", "wake"]


def test_repeated_start_ignores_liepin_run_that_reached_terminal_between_clicks(tmp_path) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(status="ready", provider_account_hash="acct_hash_browser_ready")
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["liepin"])
        _approve_triage(client, session["sessionId"])
        source_run_id = _started_source(session, "liepin")["sourceRunId"]

        first = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )
        assert first.status_code == 202, first.text
        assert first.json()["blockedSources"] == []
        assert len(worker.probe_calls) == 1

        with sqlite3.connect(_db_path(tmp_path)) as conn:
            conn.execute(
                "UPDATE source_runs SET status = 'completed' WHERE source_run_id = ?",
                (source_run_id,),
            )

        worker.status = "login_required"
        worker.provider_account_hash = None
        second = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )
        assert second.status_code == 202, second.text
        assert second.json()["blockedSources"] == []
        assert len(worker.probe_calls) == 1


def test_start_ignores_terminal_race_reported_by_job_start(tmp_path, monkeypatch) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(status="ready", provider_account_hash="acct_hash_browser_ready")
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["liepin"])
        _approve_triage(client, session["sessionId"])

        def raise_terminal_race(**_kwargs):
            raise RuntimeError("source_run_already_terminal")

        monkeypatch.setattr(client.app.state.workbench_store, "start_source_run_job", raise_terminal_race)

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert response.status_code == 202, response.text
        assert response.json()["sourceRuns"] == []
        assert response.json()["blockedSources"] == []
        assert len(worker.probe_calls) == 1


def test_start_does_not_expose_unexpected_job_start_runtime_error(tmp_path, monkeypatch) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(status="ready", provider_account_hash="acct_hash_browser_ready")
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["liepin"])
        _approve_triage(client, session["sessionId"])

        def raise_unexpected_error(**_kwargs):
            raise RuntimeError("raw provider cookie secret")

        monkeypatch.setattr(client.app.state.workbench_store, "start_source_run_job", raise_unexpected_error)

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert response.status_code == 500, response.text
        assert response.json() == {"detail": "source_run_start_failed"}
        assert "raw provider cookie secret" not in response.text
        assert len(worker.probe_calls) == 1


def test_legacy_liepin_login_relay_routes_are_disabled_by_default(tmp_path) -> None:
    with _client(tmp_path) as client:
        bootstrap = _bootstrap_and_login(client)
        user = _workbench_user_from_bootstrap(bootstrap)
        connection, _created = client.app.state.workbench_store.get_or_create_liepin_source_connection(user=user)
        connection_id = connection.connection_id

        start = client.post(
            f"/api/workbench/source-connections/{connection_id}/login",
            headers=_csrf_header(client),
        )
        frame = client.get(f"/api/workbench/source-connections/{connection_id}/login/frame")
        snapshot = client.get(f"/api/workbench/source-connections/{connection_id}/login/snapshot")
        relay_input = client.post(
            f"/api/workbench/source-connections/{connection_id}/login/input",
            headers=_csrf_header(client),
            json={"action": "click", "x": 0, "y": 0},
        )
        complete = client.post(
            f"/api/workbench/source-connections/{connection_id}/login/complete",
            headers=_csrf_header(client),
        )

        assert start.status_code == 410
        assert frame.status_code == 410
        assert snapshot.status_code == 410
        assert relay_input.status_code == 410
        assert complete.status_code == 410
