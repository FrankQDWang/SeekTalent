from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError
from seektalent.providers.liepin.worker_contracts import OPENCLI_LOCAL_BROWSER_PROFILE_SUBJECT
from seektalent.providers.liepin.worker_contracts import SessionStatus
from seektalent_ui.liepin_account_binding import (
    bind_observed_liepin_account,
    ensure_workbench_liepin_provider_connection,
)

from tests.test_workbench_api import (
    _approve_requirement_review,
    _ensure_local_actor,
    _client,
    _create_session,
    _db_path,
    _workbench_user_from_actor_payload,
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


def _started_source(payload: dict, source_kind: str) -> dict:
    return next(run for run in payload["sourceRuns"] if run["sourceKind"] == source_kind)


class ProbeLiepinWorker:
    def __init__(
        self,
        *,
        status: str,
        provider_account_hash: str | None = "acct_hash_ready",
        error: Exception | None = None,
        readiness_error: Exception | None = None,
        echo_requested_provider_account_hash: bool = True,
        safe_reason_code: str | None = None,
    ) -> None:
        self.status = status
        self.provider_account_hash = provider_account_hash
        self.error = error
        self.readiness_error = readiness_error
        self.echo_requested_provider_account_hash = echo_requested_provider_account_hash
        self.safe_reason_code = safe_reason_code
        self.readiness_calls = 0
        self.probe_calls: list[dict[str, object]] = []

    async def ensure_ready(self, *, on_event=None) -> None:
        del on_event
        self.readiness_calls += 1
        if self.readiness_error is not None:
            raise self.readiness_error

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
        returned_provider_account_hash = self.provider_account_hash
        if self.status == "ready" and provider_account_hash is not None and self.echo_requested_provider_account_hash:
            returned_provider_account_hash = provider_account_hash
        return SessionStatus(
            connectionId=connection_id,
            status=self.status,
            providerAccountHash=returned_provider_account_hash if self.status == "ready" else None,
            safeReasonCode=self.safe_reason_code,
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
        self.store.start_runtime_sourcing_job(
            user=self.user,
            session_id=self.session_id,
            idempotency_key="runtime-race-start",
        )
        return SessionStatus(connectionId=connection_id, status="login_required", providerAccountHash=None)


def _install_probe_worker(client, worker: ProbeLiepinWorker) -> None:
    client.app.state.liepin_worker_client = worker
    client.app.state.workbench_job_runner.liepin_worker_client = worker


def _reset_probe_worker(worker: ProbeLiepinWorker) -> None:
    worker.readiness_calls = 0
    worker.probe_calls.clear()


def _opencli_settings() -> dict[str, object]:
    return {
        "liepin_worker_mode": "opencli",
        "liepin_browser_action_backend": "opencli",
        "liepin_account_binding_secret": "account-binding-secret",
    }


def _get_liepin_card(client, session_id: str) -> tuple[dict, dict]:
    session_response = client.get(
        f"/api/workbench/sessions/{session_id}",
    )
    assert session_response.status_code == 200, session_response.text
    liepin_card = next(card for card in session_response.json()["sourceCards"] if card["sourceKind"] == "liepin")
    return session_response.json(), liepin_card


def _bind_workbench_liepin_account(
    client,
    *,
    user,
    connection,
    observed_subject: str = "observed-opencli-account",
) -> str:
    settings = client.app.state.settings
    gate_ref = ensure_workbench_liepin_provider_connection(
        settings=settings,
        user=user,
        connection=connection,
    )
    provider_account_hash = bind_observed_liepin_account(
        settings=settings,
        user=user,
        connection_id=connection.connection_id,
        compliance_gate_ref=gate_ref,
        observed_provider_account_subject=observed_subject,
    )
    assert provider_account_hash is not None
    updated = client.app.state.workbench_store.mark_liepin_connection_connected(
        user=user,
        connection_id=connection.connection_id,
        provider_account_hash=provider_account_hash,
        compliance_gate_ref=gate_ref,
    )
    assert updated is not None
    return provider_account_hash


def _assert_runtime_start(payload: dict, source_kinds: list[str]) -> None:
    assert "sourceRuns" not in payload
    runtime_job = payload["runtimeJob"]
    assert runtime_job is not None
    assert runtime_job["status"] in {"queued", "running"}
    assert runtime_job["sourceKinds"] == source_kinds


def test_liepin_provider_connection_binding_is_idempotent_for_same_connection(tmp_path) -> None:
    with _client(tmp_path, settings_overrides=_opencli_settings()) as client:
        actor_payload = _ensure_local_actor(client)
        user = _workbench_user_from_actor_payload(actor_payload)
        connection, _created = client.app.state.workbench_store.get_or_create_liepin_source_connection(user=user)

        first_gate_ref = ensure_workbench_liepin_provider_connection(
            settings=client.app.state.settings,
            user=user,
            connection=connection,
        )
        second_gate_ref = ensure_workbench_liepin_provider_connection(
            settings=client.app.state.settings,
            user=user,
            connection=connection,
        )

        assert second_gate_ref == first_gate_ref


def _assert_public_probe_surfaces_do_not_leak(client, session_id: str, *extra_forbidden: str) -> None:
    session_response = client.get(
        f"/api/workbench/sessions/{session_id}",
    )
    session_events = client.get(
        f"/api/workbench/sessions/{session_id}/events",
    )
    global_events = client.get("/api/workbench/events")
    security_events = client.get("/api/workbench/security-audit-events")

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
        _ensure_local_actor(client)
        worker = ProbeLiepinWorker(status="ready", provider_account_hash="acct_hash_browser_ready")
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["liepin"])
        _approve_requirement_review(client, session["sessionId"])
        _reset_probe_worker(worker)

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )

        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["blockedSources"] == []
        _assert_runtime_start(payload, ["liepin"])
        assert worker.probe_calls
        assert_no_probe_leaks(response.text, "acct_hash_browser_ready")

        _session, liepin_card = _get_liepin_card(client, session["sessionId"])
        assert liepin_card["authState"] == "not_required"
        assert liepin_card["warningCode"] is None
        _assert_public_probe_surfaces_do_not_leak(client, session["sessionId"], "acct_hash_browser_ready")


def test_ready_probe_does_not_unblock_liepin_runs_from_other_sessions(tmp_path) -> None:
    with _client(tmp_path) as client:
        _ensure_local_actor(client)
        worker = ProbeLiepinWorker(status="ready", provider_account_hash="acct_hash_browser_ready")
        _install_probe_worker(client, worker)

        first_session = _create_session(client, source_kinds=["liepin"])
        second_session = _create_session(client, source_kinds=["liepin"])
        _approve_requirement_review(client, first_session["sessionId"])
        _reset_probe_worker(worker)

        response = client.post(
            f"/api/workbench/sessions/{first_session['sessionId']}/start",
        )

        assert response.status_code == 202, response.text
        first_payload = response.json()
        assert first_payload["blockedSources"] == []
        _assert_runtime_start(first_payload, ["liepin"])

        _session, second_liepin = _get_liepin_card(client, second_session["sessionId"])
        assert second_liepin["status"] == "blocked"
        assert second_liepin["authState"] == "login_required"


def test_start_session_blocks_only_liepin_when_browser_login_is_required(tmp_path) -> None:
    with _client(tmp_path) as client:
        _ensure_local_actor(client)
        worker = ProbeLiepinWorker(status="login_required", provider_account_hash=None)
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
                "reason": "source_login_required",
            }
        ]

        session_payload, liepin_card = _get_liepin_card(client, session["sessionId"])
        assert liepin_card["status"] == "blocked"
        assert liepin_card["authState"] == "login_required"
        assert liepin_card["warningCode"] == "source_login_required"
        assert "本机 Chrome" in liepin_card["warningMessage"]
        liepin_runtime = next(
            source
            for source in session_payload.get("runtimeSourceState", {}).get("sources", [])
            if source["sourceKind"] == "liepin"
        )
        assert liepin_runtime["reasonCode"] == "source_login_required"
        assert_no_probe_leaks(response.text)
        _assert_public_probe_surfaces_do_not_leak(client, session["sessionId"])


def test_start_session_blocks_liepin_when_readiness_missing_observed_tools(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _ensure_local_actor(client)
        worker = ProbeLiepinWorker(
            status="ready",
            readiness_error=LiepinWorkerModeError(
                "observed tool names missing: /secret/path",
                code="liepin_pi_dokobot_mcp_tool_names_missing",
            ),
        )
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_requirement_review(client, session["sessionId"])
        _reset_probe_worker(worker)

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )
        payload = response.json()

        assert response.status_code == 202, response.text
        assert worker.readiness_calls == 1
        assert worker.probe_calls == []
        _assert_runtime_start(payload, ["cts"])
        assert payload["blockedSources"] == [
            {
                "sourceRunId": _started_source(session, "liepin")["sourceRunId"],
                "sourceKind": "liepin",
                "reason": "source_browser_backend_unavailable",
            }
        ]
        _session, liepin_card = _get_liepin_card(client, session["sessionId"])
        assert liepin_card["warningCode"] == "source_browser_backend_unavailable"
        assert_no_probe_leaks(response.text)


def test_start_session_blocks_liepin_when_browser_backend_is_unavailable(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _ensure_local_actor(client)
        worker = ProbeLiepinWorker(
            status="ready",
            readiness_error=LiepinWorkerModeError(
                "browser backend unavailable: /secret/path",
                code="liepin_browser_backend_unavailable",
            ),
        )
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_requirement_review(client, session["sessionId"])
        _reset_probe_worker(worker)

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )
        payload = response.json()

        assert response.status_code == 202, response.text
        assert worker.readiness_calls == 1
        assert worker.probe_calls == []
        _assert_runtime_start(payload, ["cts"])
        assert payload["blockedSources"] == [
            {
                "sourceRunId": _started_source(session, "liepin")["sourceRunId"],
                "sourceKind": "liepin",
                "reason": "source_browser_backend_unavailable",
            }
        ]
        _session, liepin_card = _get_liepin_card(client, session["sessionId"])
        assert liepin_card["warningCode"] == "source_browser_backend_unavailable"


def test_start_session_opencli_mode_maps_readiness_error_to_safe_reason(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _ensure_local_actor(client)
        worker = ProbeLiepinWorker(
            status="ready",
            readiness_error=LiepinWorkerModeError(
                "OpenCLI extension disconnected: /secret/path",
                code="liepin_opencli_extension_disconnected",
            ),
        )
        _install_probe_worker(client, worker)
        client.app.state.settings.liepin_browser_action_backend = "opencli"

        session = _create_session(client, source_kinds=["cts", "liepin"])
        liepin_card = next(card for card in session["sourceCards"] if card["sourceKind"] == "liepin")
        connection_id = liepin_card["connectionId"]
        assert connection_id is not None
        _approve_requirement_review(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )
        payload = response.json()

        assert response.status_code == 202, response.text
        assert worker.readiness_calls == 3
        assert worker.probe_calls == []
        _assert_runtime_start(payload, ["cts"])
        assert payload["blockedSources"] == [
            {
                "sourceRunId": _started_source(session, "liepin")["sourceRunId"],
                "sourceKind": "liepin",
                "reason": "source_browser_extension_disconnected",
            }
        ]
        _session, liepin_card = _get_liepin_card(client, session["sessionId"])
        assert liepin_card["warningCode"] == "source_browser_extension_disconnected"
        assert_no_probe_leaks(response.text)


def test_start_session_opencli_mode_blocks_liepin_without_bound_account(
    tmp_path: Path,
) -> None:
    with _client(tmp_path, settings_overrides=_opencli_settings()) as client:
        actor_payload = _ensure_local_actor(client)
        user = _workbench_user_from_actor_payload(actor_payload)
        worker = ProbeLiepinWorker(status="login_required", provider_account_hash=None)
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["cts", "liepin"])
        liepin_card = next(card for card in session["sourceCards"] if card["sourceKind"] == "liepin")
        connection_id = liepin_card["connectionId"]
        assert connection_id is not None
        _approve_requirement_review(client, session["sessionId"])

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
                "reason": "source_login_required",
            }
        ]
        assert worker.readiness_calls == 3
        assert len(worker.probe_calls) == 3
        assert all(
            call
            == {
                "connection_id": connection_id,
                "tenant": "local",
                "workspace": user.workspace_id,
                "provider_account_hash": None,
            }
            for call in worker.probe_calls
        )

        _session_payload, liepin_card = _get_liepin_card(client, session["sessionId"])
        assert liepin_card["status"] == "blocked"
        assert liepin_card["authState"] == "login_required"
        assert liepin_card["warningCode"] == "source_login_required"


@pytest.mark.parametrize(
    ("status", "safe_reason_code", "public_reason_code"),
    [
        ("missing", "liepin_opencli_filter_unapplied", "source_filter_unavailable"),
        ("missing", "liepin_opencli_search_not_ready", "source_browser_backend_unavailable"),
        ("login_required", "liepin_opencli_identity_intercept", "source_risk_or_verification_required"),
    ],
)
def test_start_session_opencli_mode_preserves_raw_status_reason_when_not_ready(
    tmp_path: Path,
    status: str,
    safe_reason_code: str,
    public_reason_code: str,
) -> None:
    with _client(tmp_path, settings_overrides=_opencli_settings()) as client:
        actor_payload = _ensure_local_actor(client)
        user = _workbench_user_from_actor_payload(actor_payload)
        store = client.app.state.workbench_store
        connection, _created = store.get_or_create_liepin_source_connection(user=user)
        provider_account_hash = _bind_workbench_liepin_account(
            client,
            user=user,
            connection=connection,
        )
        worker = ProbeLiepinWorker(
            status=status,
            provider_account_hash=None,
            safe_reason_code=safe_reason_code,
        )
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_requirement_review(client, session["sessionId"])

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
                "reason": public_reason_code,
            }
        ]
        assert worker.probe_calls[0]["provider_account_hash"] == provider_account_hash

        session_payload, liepin_card = _get_liepin_card(client, session["sessionId"])
        assert liepin_card["warningCode"] == public_reason_code
        with sqlite3.connect(_db_path(tmp_path)) as db:
            raw_warning_code = db.execute(
                "SELECT warning_code FROM source_runs WHERE source_run_id = ?",
                (_started_source(session, "liepin")["sourceRunId"],),
            ).fetchone()[0]
        assert raw_warning_code == safe_reason_code
        liepin_runtime = next(
            source
            for source in session_payload.get("runtimeSourceState", {}).get("sources", [])
            if source["sourceKind"] == "liepin"
        )
        assert liepin_runtime["reasonCode"] == public_reason_code


def test_create_session_opencli_mode_auto_binds_ready_local_browser_from_clean_db(
    tmp_path: Path,
) -> None:
    with _client(tmp_path, settings_overrides=_opencli_settings()) as client:
        actor_payload = _ensure_local_actor(client)
        user = _workbench_user_from_actor_payload(actor_payload)
        worker = ProbeLiepinWorker(status="ready", provider_account_hash="observed-opencli-account")
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["liepin"])

        liepin_card = next(card for card in session["sourceCards"] if card["sourceKind"] == "liepin")
        assert liepin_card["status"] == "queued"
        assert liepin_card["authState"] == "not_required"
        assert liepin_card["warningCode"] is None
        assert liepin_card["connectionStatus"] == "connected"
        assert worker.readiness_calls == 1
        assert worker.probe_calls == [
            {
                "connection_id": liepin_card["connectionId"],
                "tenant": "local",
                "workspace": user.workspace_id,
                "provider_account_hash": None,
            }
        ]

        connection = client.app.state.workbench_store.get_source_connection(
            user=user,
            connection_id=liepin_card["connectionId"],
        )
        assert connection is not None
        assert connection.provider_account_hash is not None
        assert connection.provider_account_hash != "observed-opencli-account"
        assert connection.compliance_gate_ref is not None
        assert_no_probe_leaks(repr(session), "observed-opencli-account", connection.provider_account_hash)
        _assert_public_probe_surfaces_do_not_leak(
            client,
            session["sessionId"],
            "observed-opencli-account",
            connection.provider_account_hash,
        )


def test_start_session_opencli_mode_auto_binds_ready_local_browser_from_clean_db(
    tmp_path: Path,
) -> None:
    with _client(tmp_path, settings_overrides=_opencli_settings()) as client:
        actor_payload = _ensure_local_actor(client)
        user = _workbench_user_from_actor_payload(actor_payload)
        worker = ProbeLiepinWorker(status="login_required", provider_account_hash=None)
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["liepin"])
        initial_liepin_card = next(card for card in session["sourceCards"] if card["sourceKind"] == "liepin")
        connection_id = initial_liepin_card["connectionId"]
        assert connection_id is not None
        _approve_requirement_review(client, session["sessionId"])
        worker.status = "ready"
        worker.provider_account_hash = "observed-opencli-account"
        worker.readiness_calls = 0
        worker.probe_calls.clear()

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )

        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["blockedSources"] == []
        _assert_runtime_start(payload, ["liepin"])
        assert worker.readiness_calls == 1
        assert worker.probe_calls == [
            {
                "connection_id": connection_id,
                "tenant": "local",
                "workspace": user.workspace_id,
                "provider_account_hash": None,
            }
        ]

        _session_payload, liepin_card = _get_liepin_card(client, session["sessionId"])
        assert liepin_card["status"] in {"queued", "running", "completed"}
        assert liepin_card["authState"] == "not_required"
        assert liepin_card["warningCode"] is None
        assert liepin_card["connectionStatus"] == "connected"
        connection = client.app.state.workbench_store.get_source_connection(
            user=user,
            connection_id=liepin_card["connectionId"],
        )
        assert connection is not None
        assert connection.provider_account_hash is not None
        assert connection.provider_account_hash != "observed-opencli-account"
        assert connection.compliance_gate_ref is not None
        assert_no_probe_leaks(response.text, "observed-opencli-account", connection.provider_account_hash)
        _assert_public_probe_surfaces_do_not_leak(
            client,
            session["sessionId"],
            "observed-opencli-account",
            connection.provider_account_hash,
        )


def test_start_session_opencli_mode_queues_liepin_with_existing_bound_account(
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
        worker = ProbeLiepinWorker(
            status="ready",
            provider_account_hash=OPENCLI_LOCAL_BROWSER_PROFILE_SUBJECT,
            echo_requested_provider_account_hash=False,
        )
        _install_probe_worker(client, worker)
        client.app.state.settings.liepin_browser_action_backend = "opencli"

        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_requirement_review(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )

        assert response.status_code == 202, response.text
        payload = response.json()
        _assert_runtime_start(payload, ["cts", "liepin"])
        assert payload["blockedSources"] == []
        assert worker.readiness_calls == 1
        assert worker.probe_calls == [
            {
                "connection_id": connection.connection_id,
                "tenant": "local",
                "workspace": user.workspace_id,
                "provider_account_hash": provider_account_hash,
            }
        ]

        updated_connection = store.get_source_connection(user=user, connection_id=connection.connection_id)
        assert updated_connection is not None
        assert updated_connection.provider_account_hash == provider_account_hash


def test_start_session_opencli_mode_recovers_bound_account_after_provider_connector_db_reset(
    tmp_path: Path,
) -> None:
    with _client(tmp_path, settings_overrides=_opencli_settings()) as client:
        actor_payload = _ensure_local_actor(client)
        user = _workbench_user_from_actor_payload(actor_payload)
        store = client.app.state.workbench_store
        connection, _created = store.get_or_create_liepin_source_connection(user=user)
        provider_account_hash = _bind_workbench_liepin_account(
            client,
            user=user,
            connection=connection,
        )
        bound_connection = store.get_source_connection(user=user, connection_id=connection.connection_id)
        assert bound_connection is not None
        assert bound_connection.compliance_gate_ref is not None

        settings = client.app.state.settings
        provider_db_path = settings.resolve_workspace_path(settings.liepin_connector_db_path)
        with sqlite3.connect(provider_db_path) as provider_db:
            provider_db.execute("DELETE FROM liepin_connections")
            provider_db.execute("DELETE FROM liepin_compliance_gates")

        worker = ProbeLiepinWorker(
            status="ready",
            provider_account_hash=OPENCLI_LOCAL_BROWSER_PROFILE_SUBJECT,
            echo_requested_provider_account_hash=False,
        )
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_requirement_review(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )

        assert response.status_code == 202, response.text
        payload = response.json()
        _assert_runtime_start(payload, ["cts", "liepin"])
        assert payload["blockedSources"] == []

        updated_connection = store.get_source_connection(user=user, connection_id=connection.connection_id)
        assert updated_connection is not None
        assert updated_connection.provider_account_hash == provider_account_hash
        assert updated_connection.compliance_gate_ref is not None
        with sqlite3.connect(provider_db_path) as provider_db:
            provider_db.row_factory = sqlite3.Row
            provider_connection = provider_db.execute(
                "SELECT * FROM liepin_connections WHERE connection_id = ?",
                (connection.connection_id,),
            ).fetchone()
            provider_gate = provider_db.execute(
                "SELECT * FROM liepin_compliance_gates WHERE gate_ref = ?",
                (updated_connection.compliance_gate_ref,),
            ).fetchone()
        assert provider_connection is not None
        assert provider_connection["provider_account_hash"] == provider_account_hash
        assert provider_connection["compliance_gate_ref"] == updated_connection.compliance_gate_ref
        assert provider_gate is not None
        assert provider_gate["status"] == "approved"
        assert provider_gate["provider_account_hash"] == provider_account_hash


def test_create_session_opencli_mode_keeps_liepin_blocked_when_unbound_probe_requires_login(
    tmp_path: Path,
) -> None:
    with _client(tmp_path, settings_overrides=_opencli_settings()) as client:
        actor_payload = _ensure_local_actor(client)
        user = _workbench_user_from_actor_payload(actor_payload)
        worker = ProbeLiepinWorker(status="login_required", provider_account_hash=None)
        _install_probe_worker(client, worker)
        store = client.app.state.workbench_store
        connection, _created = store.get_or_create_liepin_source_connection(user=user)
        store.mark_liepin_connection_login_required(
            user=user,
            connection_id=connection.connection_id,
            warning_code="liepin_browser_probe_unavailable",
            warning_message="stale unavailable",
        )

        session = _create_session(client, source_kinds=["liepin"])

        liepin_card = next(card for card in session["sourceCards"] if card["sourceKind"] == "liepin")
        assert worker.readiness_calls == 1
        assert worker.probe_calls == [
            {
                "connection_id": connection.connection_id,
                "tenant": "local",
                "workspace": user.workspace_id,
                "provider_account_hash": None,
            }
        ]
        assert liepin_card["connectionStatus"] == "login_required"
        assert liepin_card["status"] == "blocked"
        assert liepin_card["authState"] == "login_required"
        assert liepin_card["warningCode"] == "source_login_required"


def test_start_session_blocks_liepin_when_probe_backend_is_unavailable(tmp_path) -> None:
    with _client(tmp_path) as client:
        _ensure_local_actor(client)
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
        _approve_requirement_review(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )

        assert response.status_code == 202, response.text
        assert_no_probe_leaks(response.text)
        payload = response.json()
        _assert_runtime_start(payload, ["cts"])
        assert payload["blockedSources"][0]["sourceKind"] == "liepin"
        assert payload["blockedSources"][0]["reason"] == "source_browser_backend_unavailable"

        session_payload, _liepin_card = _get_liepin_card(client, session["sessionId"])
        liepin_runtime = next(
            source
            for source in session_payload.get("runtimeSourceState", {}).get("sources", [])
            if source["sourceKind"] == "liepin"
        )
        assert liepin_runtime["reasonCode"] == "source_browser_backend_unavailable"
        _assert_public_probe_surfaces_do_not_leak(client, session["sessionId"])


def test_start_session_preserves_pi_setup_reason_without_blocking_cts(tmp_path) -> None:
    with _client(tmp_path) as client:
        _ensure_local_actor(client)
        worker = ProbeLiepinWorker(
            status="login_required",
            error=LiepinWorkerModeError(
                "pi command missing: /secret/path",
                code="liepin_pi_command_missing",
            ),
        )
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_requirement_review(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )

        assert response.status_code == 202, response.text
        assert_no_probe_leaks(response.text)
        payload = response.json()
        _assert_runtime_start(payload, ["cts"])
        assert payload["blockedSources"] == [
            {
                "sourceRunId": _started_source(session, "liepin")["sourceRunId"],
                "sourceKind": "liepin",
                "reason": "source_browser_backend_unavailable",
            }
        ]

        session_payload, liepin_card = _get_liepin_card(client, session["sessionId"])
        assert liepin_card["warningCode"] == "source_browser_backend_unavailable"
        assert "Pi" not in liepin_card["warningMessage"]
        liepin_runtime = next(
            source
            for source in session_payload.get("runtimeSourceState", {}).get("sources", [])
            if source["sourceKind"] == "liepin"
        )
        assert liepin_runtime["reasonCode"] == "source_browser_backend_unavailable"
        _assert_public_probe_surfaces_do_not_leak(client, session["sessionId"])


def test_unexpected_probe_error_blocks_liepin_without_blocking_cts_or_leaking(tmp_path) -> None:
    with _client(tmp_path) as client:
        _ensure_local_actor(client)
        worker = ProbeLiepinWorker(
            status="login_required",
            error=ValueError("raw provider cookie secret"),
        )
        _install_probe_worker(client, worker)
        wake_calls: list[str] = []
        client.app.state.workbench_job_runner.wake = lambda: wake_calls.append("wake")

        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_requirement_review(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )

        assert response.status_code == 202, response.text
        assert_no_probe_leaks(response.text, "raw provider cookie secret")
        payload = response.json()
        _assert_runtime_start(payload, ["cts"])
        assert payload["blockedSources"] == [
            {
                "sourceRunId": _started_source(session, "liepin")["sourceRunId"],
                "sourceKind": "liepin",
                "reason": "source_browser_backend_unavailable",
            }
        ]
        assert wake_calls == ["wake"]

        session_payload, _liepin_card = _get_liepin_card(client, session["sessionId"])
        liepin_runtime = next(
            source
            for source in session_payload.get("runtimeSourceState", {}).get("sources", [])
            if source["sourceKind"] == "liepin"
        )
        assert liepin_runtime["reasonCode"] == "source_browser_backend_unavailable"
        _assert_public_probe_surfaces_do_not_leak(
            client,
            session["sessionId"],
            "raw provider cookie secret",
        )


def test_start_session_blocks_liepin_when_browser_account_does_not_match_bound_account(tmp_path) -> None:
    with _client(tmp_path) as client:
        actor_payload = _ensure_local_actor(client)
        user = _workbench_user_from_actor_payload(actor_payload)
        store = client.app.state.workbench_store
        connection, _created = store.get_or_create_liepin_source_connection(user=user)
        store.mark_liepin_connection_connected(
            user=user,
            connection_id=connection.connection_id,
            provider_account_hash="acct_hash_bound",
        )
        worker = ProbeLiepinWorker(
            status="ready",
            provider_account_hash="acct_hash_other",
            echo_requested_provider_account_hash=False,
        )
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["liepin"])
        _approve_requirement_review(client, session["sessionId"])
        _reset_probe_worker(worker)

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )

        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["blockedSources"] == [
            {
                "sourceRunId": _started_source(session, "liepin")["sourceRunId"],
                "sourceKind": "liepin",
                "reason": "source_account_mismatch",
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
        assert liepin_runtime["reasonCode"] == "source_account_mismatch"
        _assert_public_probe_surfaces_do_not_leak(
            client,
            session["sessionId"],
            "acct_hash_bound",
            "acct_hash_other",
        )


def test_repeated_start_does_not_reprobe_or_block_queued_liepin_run(tmp_path) -> None:
    with _client(tmp_path) as client:
        _ensure_local_actor(client)
        worker = ProbeLiepinWorker(status="ready", provider_account_hash="acct_hash_browser_ready")
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["liepin"])
        _approve_requirement_review(client, session["sessionId"])
        _reset_probe_worker(worker)

        first = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )
        assert first.status_code == 202, first.text
        assert first.json()["blockedSources"] == []
        assert len(worker.probe_calls) == 1

        worker.status = "login_required"
        worker.provider_account_hash = None
        second = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )
        assert second.status_code == 202, second.text
        assert second.json()["blockedSources"] == []
        assert len(worker.probe_calls) == 1


def test_probe_race_does_not_downgrade_already_queued_liepin_run_or_connection(tmp_path) -> None:
    with _client(tmp_path) as client:
        actor_payload = _ensure_local_actor(client)
        user = _workbench_user_from_actor_payload(actor_payload)
        session = _create_session(client, source_kinds=["liepin"])
        _approve_requirement_review(client, session["sessionId"])
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
        )

        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["blockedSources"] == []
        _assert_runtime_start(payload, ["liepin"])
        assert len(worker.probe_calls) == 1

        _session_payload, liepin_card = _get_liepin_card(client, session["sessionId"])
        assert liepin_card["status"] == "queued"
        assert liepin_card["authState"] == "not_required"
        assert liepin_card["warningCode"] is None
        assert liepin_card["connectionStatus"] == "connected"


def test_repeated_start_wakes_runner_for_existing_queued_job(tmp_path) -> None:
    with _client(tmp_path) as client:
        _ensure_local_actor(client)
        session = _create_session(client, source_kinds=["cts"])
        _approve_requirement_review(client, session["sessionId"])
        wake_calls: list[str] = []
        client.app.state.workbench_job_runner.wake = lambda: wake_calls.append("wake")

        first = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )
        second = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )

        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text
        _assert_runtime_start(first.json(), ["cts"])
        _assert_runtime_start(second.json(), ["cts"])
        assert second.json()["runtimeJob"]["jobId"] == first.json()["runtimeJob"]["jobId"]
        assert wake_calls == ["wake", "wake"]


def test_repeated_start_ignores_liepin_run_that_reached_terminal_between_clicks(tmp_path) -> None:
    with _client(tmp_path) as client:
        _ensure_local_actor(client)
        worker = ProbeLiepinWorker(status="ready", provider_account_hash="acct_hash_browser_ready")
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["liepin"])
        _approve_requirement_review(client, session["sessionId"])
        _reset_probe_worker(worker)
        source_run_id = _started_source(session, "liepin")["sourceRunId"]

        first = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
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
        )
        assert second.status_code == 202, second.text
        assert second.json()["blockedSources"] == []
        assert len(worker.probe_calls) == 1


def test_start_ignores_terminal_race_reported_by_job_start(tmp_path, monkeypatch) -> None:
    with _client(tmp_path) as client:
        _ensure_local_actor(client)
        worker = ProbeLiepinWorker(status="ready", provider_account_hash="acct_hash_browser_ready")
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["liepin"])
        _approve_requirement_review(client, session["sessionId"])
        _reset_probe_worker(worker)

        def raise_terminal_race(**_kwargs):
            raise RuntimeError("runtime_sourcing_already_terminal")

        monkeypatch.setattr(client.app.state.workbench_store, "start_runtime_sourcing_job", raise_terminal_race)

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )

        assert response.status_code == 202, response.text
        assert response.json()["runtimeJob"] is None
        assert response.json()["blockedSources"] == []
        assert len(worker.probe_calls) == 1


def test_start_does_not_expose_unexpected_job_start_runtime_error(tmp_path, monkeypatch) -> None:
    with _client(tmp_path) as client:
        _ensure_local_actor(client)
        worker = ProbeLiepinWorker(status="ready", provider_account_hash="acct_hash_browser_ready")
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["liepin"])
        _approve_requirement_review(client, session["sessionId"])
        _reset_probe_worker(worker)

        def raise_unexpected_error(**_kwargs):
            raise RuntimeError("raw provider cookie secret")

        monkeypatch.setattr(client.app.state.workbench_store, "start_runtime_sourcing_job", raise_unexpected_error)

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
        )

        assert response.status_code == 500, response.text
        assert response.json() == {"detail": "runtime_sourcing_start_failed"}
        assert "raw provider cookie secret" not in response.text
        assert len(worker.probe_calls) == 1


def test_legacy_liepin_login_relay_routes_are_disabled_by_default(tmp_path) -> None:
    with _client(tmp_path) as client:
        actor_payload = _ensure_local_actor(client)
        user = _workbench_user_from_actor_payload(actor_payload)
        connection, _created = client.app.state.workbench_store.get_or_create_liepin_source_connection(user=user)
        connection_id = connection.connection_id

        start = client.post(
            f"/api/workbench/source-connections/{connection_id}/login",
        )
        frame = client.get(f"/api/workbench/source-connections/{connection_id}/login/frame")
        snapshot = client.get(f"/api/workbench/source-connections/{connection_id}/login/snapshot")
        relay_input = client.post(
            f"/api/workbench/source-connections/{connection_id}/login/input",
            json={"action": "click", "x": 0, "y": 0},
        )
        complete = client.post(
            f"/api/workbench/source-connections/{connection_id}/login/complete",
        )

        assert start.status_code == 410
        assert frame.status_code == 410
        assert snapshot.status_code == 410
        assert relay_input.status_code == 410
        assert complete.status_code == 410
