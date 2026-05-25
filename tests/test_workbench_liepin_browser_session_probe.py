from __future__ import annotations

import sqlite3
from pathlib import Path

from seektalent.dev_mode import build_dev_mode_env_diagnostics
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
        readiness_error: Exception | None = None,
    ) -> None:
        super().__init__()
        self.status = status
        self.provider_account_hash = provider_account_hash
        self.error = error
        self.readiness_error = readiness_error
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


def _assert_runtime_start(payload: dict, source_kinds: list[str]) -> None:
    assert payload["sourceRuns"] == []
    runtime_job = payload["runtimeJob"]
    assert runtime_job is not None
    assert runtime_job["status"] in {"queued", "running"}
    assert runtime_job["sourceKinds"] == source_kinds


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
        _assert_runtime_start(payload, ["liepin"])
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
        _assert_runtime_start(first_payload, ["liepin"])

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
        _assert_runtime_start(payload, ["cts", "liepin"])
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


def test_start_session_preserves_recovered_dev_mode_pi_setup_reason(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        pi_bin = tmp_path / "bin" / "pi"
        pi_bin.parent.mkdir(parents=True)
        pi_bin.write_text("#!/usr/bin/env node\n", encoding="utf-8")
        pi_bin.chmod(0o755)
        provider_extension = tmp_path / "src" / "seektalent" / "providers" / "pi_agent" / "pi_extensions"
        provider_extension.mkdir(parents=True)
        (provider_extension / "bailian_deepseek.ts").write_text("provider", encoding="utf-8")
        skill_path = tmp_path / "liepin_search_cards.md"
        skill_path.write_text("Liepin skill", encoding="utf-8")
        mcp_path = tmp_path / ".pi" / "mcp.json"
        mcp_path.parent.mkdir(parents=True)
        mcp_path.write_text('{"mcpServers":{"dokobot":{"command":"dokobot-mcp","args":[]}}}', encoding="utf-8")
        client.app.state.dev_mode_env_diagnostics = build_dev_mode_env_diagnostics(
            {
                "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
                "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-binding-secret",
                "SEEKTALENT_LIEPIN_PI_COMMAND": (
                    f"{pi_bin} --mode rpc --no-session "
                    "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
                    "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts"
                ),
                "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill_path),
                "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": str(mcp_path),
                "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND": "dokobot-mcp",
                "SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON": '["dokobot_read_page"]',
            },
            workspace_root=tmp_path,
        )
        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_triage(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )
        payload = response.json()

        assert response.status_code == 202, response.text
        _assert_runtime_start(payload, ["cts", "liepin"])
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


def test_start_session_blocks_liepin_when_readiness_missing_observed_tools(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(
            status="ready",
            readiness_error=LiepinWorkerModeError(
                "observed tool names missing: /secret/path",
                code="liepin_pi_dokobot_mcp_tool_names_missing",
            ),
        )
        _install_probe_worker(client, worker)

        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_triage(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )
        payload = response.json()

        assert response.status_code == 202, response.text
        assert worker.readiness_calls == 1
        assert worker.probe_calls == []
        _assert_runtime_start(payload, ["cts", "liepin"])
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


def test_start_session_maps_bad_observed_tools_json_to_safe_reason(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(status="ready")
        _install_probe_worker(client, worker)
        client.app.state.settings.liepin_dokobot_observed_tools_json = "not-json"

        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_triage(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )
        payload = response.json()

        assert response.status_code == 202, response.text
        assert worker.readiness_calls == 0
        assert worker.probe_calls == []
        _assert_runtime_start(payload, ["cts", "liepin"])
        assert payload["blockedSources"] == [
            {
                "sourceRunId": _started_source(session, "liepin")["sourceRunId"],
                "sourceKind": "liepin",
                "reason": "source_browser_backend_unavailable",
            }
        ]
        _session, liepin_card = _get_liepin_card(client, session["sessionId"])
        assert liepin_card["warningCode"] == "source_browser_backend_unavailable"
        assert "not-json" not in response.text


def test_start_session_opencli_mode_does_not_validate_dokobot_observed_tools(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(
            status="ready",
            readiness_error=LiepinWorkerModeError(
                "OpenCLI extension disconnected: /secret/path",
                code="liepin_opencli_extension_disconnected",
            ),
        )
        _install_probe_worker(client, worker)
        client.app.state.settings.liepin_browser_action_backend = "opencli"
        client.app.state.settings.liepin_dokobot_observed_tools_json = "not-json"

        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_triage(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )
        payload = response.json()

        assert response.status_code == 202, response.text
        assert worker.readiness_calls == 1
        assert worker.probe_calls == []
        _assert_runtime_start(payload, ["cts", "liepin"])
        assert payload["blockedSources"] == [
            {
                "sourceRunId": _started_source(session, "liepin")["sourceRunId"],
                "sourceKind": "liepin",
                "reason": "source_browser_extension_disconnected",
            }
        ]
        _session, liepin_card = _get_liepin_card(client, session["sessionId"])
        assert liepin_card["warningCode"] == "source_browser_extension_disconnected"
        assert "not-json" not in response.text
        assert_no_probe_leaks(response.text)


def test_start_session_opencli_mode_queues_liepin_after_channel_readiness_without_session_probe(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(status="login_required", provider_account_hash=None)
        _install_probe_worker(client, worker)
        client.app.state.settings.liepin_browser_action_backend = "opencli"

        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_triage(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert response.status_code == 202, response.text
        payload = response.json()
        _assert_runtime_start(payload, ["cts", "liepin"])
        assert payload["blockedSources"] == []
        assert worker.readiness_calls == 1
        assert worker.probe_calls == []

        _session_payload, liepin_card = _get_liepin_card(client, session["sessionId"])
        assert liepin_card["status"] in {"queued", "running"}
        assert liepin_card["authState"] == "not_required"
        assert liepin_card["warningCode"] is None


def test_create_session_opencli_mode_refreshes_stale_liepin_connection_before_source_runs(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        bootstrap = _bootstrap_and_login(client)
        user = _workbench_user_from_bootstrap(bootstrap)
        worker = ProbeLiepinWorker(status="login_required", provider_account_hash=None)
        _install_probe_worker(client, worker)
        client.app.state.settings.liepin_browser_action_backend = "opencli"
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
        assert worker.probe_calls == []
        assert liepin_card["connectionStatus"] == "connected"
        assert liepin_card["status"] == "queued"
        assert liepin_card["authState"] == "not_required"
        assert liepin_card["warningCode"] is None


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
        _assert_runtime_start(payload, ["cts", "liepin"])
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
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(
            status="login_required",
            error=LiepinWorkerModeError(
                "pi command missing: /secret/path",
                code="liepin_pi_command_missing",
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
        _assert_runtime_start(payload, ["cts", "liepin"])
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
        _assert_runtime_start(payload, ["cts", "liepin"])
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
        _assert_runtime_start(payload, ["liepin"])
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
        _assert_runtime_start(first.json(), ["cts"])
        _assert_runtime_start(second.json(), ["cts"])
        assert second.json()["runtimeJob"]["jobId"] == first.json()["runtimeJob"]["jobId"]
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
            raise RuntimeError("runtime_sourcing_already_terminal")

        monkeypatch.setattr(client.app.state.workbench_store, "start_runtime_sourcing_job", raise_terminal_race)

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert response.status_code == 202, response.text
        assert response.json()["sourceRuns"] == []
        assert response.json()["runtimeJob"] is None
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

        monkeypatch.setattr(client.app.state.workbench_store, "start_runtime_sourcing_job", raise_unexpected_error)

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert response.status_code == 500, response.text
        assert response.json() == {"detail": "runtime_sourcing_start_failed"}
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
