# Liepin Automatic Browser Session Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Starting a Workbench session automatically verifies the user's local Chrome Liepin session through the Pi/DokoBot worker path, starts Liepin when ready, and blocks only Liepin with safe recruiter-facing status when not ready.

**Architecture:** Keep Workbench as the session-start coordinator, keep Runtime as the source-lane executor, and keep Pi/DokoBot inside the Liepin worker client. Add a small source-start probe before `WorkbenchStore.start_source_run_job(...)` for Liepin only; the probe binds the existing Workbench connection when ready and otherwise stores a safe blocked state.

**Tech Stack:** Python 3.12, FastAPI, SQLite-backed `WorkbenchStore`, existing Liepin worker contract `SessionStatus`, existing Pi-backed `LiepinPiWorkerClient`, Svelte 5, Vitest, pytest.

---

## Spec

- Spec: `docs/superpowers/specs/2026-05-19-liepin-automatic-browser-session-probe-design.md`

## File Structure

- Modify `src/seektalent_ui/workbench_routes.py`
  - Make the session start route async.
  - Add a private Liepin source-start probe helper.
  - Map worker probe outcomes to safe blocked reasons.
  - Reuse `_ensure_workbench_liepin_provider_connection(...)` and `_record_workbench_liepin_provider_session(...)`.
  - Keep repeated start calls idempotent for already queued or running Liepin source runs.
  - Disable legacy managed-browser login relay routes by default or guard them behind an explicit settings flag.
- Modify `src/seektalent_ui/workbench_store.py`
  - Add narrow methods to mark a Liepin connection as login-required/backend-unavailable, mark the current Liepin source run ready after a successful probe, and block one source run with a safe message.
  - Keep `start_source_run_job(...)` as the job creation boundary.
- Modify `src/seektalent/config.py`
  - Add a default-off flag for legacy Liepin managed-browser login relay routes if those routes remain in the backend.
- Modify `src/seektalent/providers/liepin/client.py`
  - Add `session_status(...)` to the shared worker protocol and fake fixture worker.
- Modify `src/seektalent/providers/liepin/pi_worker_client.py`
  - Preserve ready browser-session account hash output so Workbench can distinguish account mismatch.
- Modify `tests/test_liepin_pi_worker_client.py`
  - Update the Pi worker client mismatch test to prove mismatch is visible to Workbench instead of folded into login-required.
- Modify `tests/test_workbench_api.py`
  - Add settings override support to the shared `_client(...)` helper.
  - Update existing legacy login relay tests to opt in with `workbench_legacy_liepin_login_relay_enabled=True`.
- No planned changes to `src/seektalent_ui/models.py`: `WorkbenchSessionStartBlockedSourceResponse.reason` should remain a string that accepts the new safe reason codes.
- Create `tests/test_workbench_liepin_browser_session_probe.py`
  - Focused backend tests for ready, login-required, backend-unavailable, and non-leak behavior.
- Modify `apps/web-svelte/src/lib/workbench/sourceDisplay.ts`
  - Keep passive user-facing labels for local Chrome login state and probe failure.
- Modify `apps/web-svelte/src/lib/components/SourceCard.svelte`
  - Keep the card passive in session detail: no connect/probe action.
- Create or update `apps/web-svelte/src/lib/components/SourceCard.test.ts`
  - Assert the Liepin source card shows local Chrome status and no action link.

## Task 0: Worker Session Probe Contract

**Files:**
- Modify: `src/seektalent/providers/liepin/client.py`
- Modify: `src/seektalent/providers/liepin/pi_worker_client.py`
- Modify: `tests/test_liepin_pi_worker_client.py`

- [ ] **Step 1: Add `session_status(...)` to the worker protocol**

In `src/seektalent/providers/liepin/client.py`, add the method to `LiepinWorkerClient` after `search(...)`:

```python
    async def session_status(
        self,
        *,
        connection_id: str,
        tenant: str | None = None,
        workspace: str | None = None,
        provider_account_hash: str | None = None,
    ) -> SessionStatus: ...
```

If `SessionStatus` is not already imported in that file, import it from `seektalent.providers.liepin.worker_contracts`.

- [ ] **Step 2: Add a safe default probe to `FakeLiepinWorkerClient`**

In `src/seektalent/providers/liepin/client.py`, add this method to `FakeLiepinWorkerClient` after `search(...)`:

```python
    async def session_status(
        self,
        *,
        connection_id: str,
        tenant: str | None = None,
        workspace: str | None = None,
        provider_account_hash: str | None = None,
    ) -> SessionStatus:
        del tenant, workspace, provider_account_hash
        return SessionStatus(connectionId=connection_id, status="login_required", provider_account_hash=None)
```

The default fake fixture worker must not silently report a real browser account. Focused Workbench tests can use a test-specific fake worker that returns `ready`.

- [ ] **Step 3: Preserve ready account hash in the real Pi worker client**

In `src/seektalent/providers/liepin/pi_worker_client.py`, replace the current mismatch folding block inside `LiepinPiWorkerClient.session_status(...)` with this logic:

```python
        del tenant, workspace, provider_account_hash
        if status.status == "ready" and status.provider_account_hash:
            return SessionStatus(
                connectionId=connection_id,
                status="ready",
                provider_account_hash=status.provider_account_hash,
            )
        return SessionStatus(connectionId=connection_id, status="login_required", provider_account_hash=None)
```

Workbench owns the comparison with any existing bound account hash. The worker must not turn a ready-but-different browser account into ordinary `login_required`.

- [ ] **Step 4: Update the Pi worker client mismatch test**

In `tests/test_liepin_pi_worker_client.py`, replace `test_session_status_rejects_mismatched_provider_account_hash` with:

```python
def test_session_status_exposes_ready_provider_hash_for_workbench_account_comparison() -> None:
    client = _client(
        FakeExecutor(
            session_result=PiLiepinSessionProbeResult(
                status="ready",
                connection_id="connection-1",
                provider_account_hash="other-acct",
            )
        )
    )

    status = asyncio.run(
        client.session_status(connection_id="connection-1", provider_account_hash="expected-acct")
    )

    assert status.status == "ready"
    assert status.provider_account_hash == "other-acct"
```

- [ ] **Step 5: Run worker contract tests**

Run:

```bash
uv run pytest tests/test_liepin_pi_worker_client.py -q
```

Expected: pass after the protocol and Pi worker client changes.

## Task 1: Backend Tests For Automatic Probe

**Files:**
- Create: `tests/test_workbench_liepin_browser_session_probe.py`

- [ ] **Step 1: Write fake probe clients**

Add this test support code to `tests/test_workbench_liepin_browser_session_probe.py`:

```python
from __future__ import annotations

from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError, SessionStatus

from tests.test_workbench_api import (
    FakeLiepinCardWorkerClient,
    _approve_triage,
    _bootstrap_and_login,
    _client,
    _create_session,
    _csrf_header,
    _started_source,
    _workbench_user_from_bootstrap,
)


class ProbeLiepinWorker(FakeLiepinCardWorkerClient):
    def __init__(
        self,
        *,
        status: str,
        provider_account_hash: str | None = "acct_hash_ready",
        error: LiepinWorkerModeError | None = None,
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
```

- [ ] **Step 2: Write the ready probe test**

Add this test:

```python
def test_start_session_auto_probes_liepin_browser_session_and_starts_liepin(
    tmp_path,
) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(status="ready", provider_account_hash="acct_hash_browser_ready")
        client.app.state.liepin_worker_client = worker
        client.app.state.workbench_job_runner.liepin_worker_client = worker

        session = _create_session(client, source_kinds=["liepin"])
        _approve_triage(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert response.status_code == 202
        payload = response.json()
        assert payload["blockedSources"] == []
        assert len(payload["sourceRuns"]) == 1
        assert payload["sourceRuns"][0]["sourceKind"] == "liepin"
        assert worker.probe_calls

        session_response = client.get(
            f"/api/workbench/sessions/{session['sessionId']}",
            headers=_csrf_header(client),
        )
        assert session_response.status_code == 200
        liepin_card = next(
            card for card in session_response.json()["sourceCards"] if card["sourceKind"] == "liepin"
        )
        assert liepin_card["authState"] == "not_required"
        assert liepin_card["warningCode"] is None
        assert "acct_hash_browser_ready" not in response.text
```

- [ ] **Step 3: Write the scoped ready probe regression test**

Add this test:

```python
def test_ready_probe_does_not_unblock_liepin_runs_from_other_sessions(tmp_path) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(status="ready", provider_account_hash="acct_hash_browser_ready")
        client.app.state.liepin_worker_client = worker
        client.app.state.workbench_job_runner.liepin_worker_client = worker

        first_session = _create_session(client, source_kinds=["liepin"])
        second_session = _create_session(client, source_kinds=["liepin"])
        _approve_triage(client, first_session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{first_session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert response.status_code == 202
        first_payload = response.json()
        assert first_payload["blockedSources"] == []
        assert first_payload["sourceRuns"][0]["sourceKind"] == "liepin"

        second_response = client.get(
            f"/api/workbench/sessions/{second_session['sessionId']}",
            headers=_csrf_header(client),
        )
        assert second_response.status_code == 200
        second_liepin = next(
            card for card in second_response.json()["sourceCards"] if card["sourceKind"] == "liepin"
        )
        assert second_liepin["status"] == "blocked"
        assert second_liepin["authState"] == "login_required"
```

- [ ] **Step 4: Write the CTS continues when Liepin login is required test**

Add this test:

```python
def test_start_session_blocks_only_liepin_when_browser_login_is_required(tmp_path) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(status="login_required", provider_account_hash=None)
        client.app.state.liepin_worker_client = worker
        client.app.state.workbench_job_runner.liepin_worker_client = worker

        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_triage(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert response.status_code == 202
        payload = response.json()
        assert [run["sourceKind"] for run in payload["sourceRuns"]] == ["cts"]
        assert payload["blockedSources"] == [
            {
                "sourceRunId": _started_source(session, "liepin")["sourceRunId"],
                "sourceKind": "liepin",
                "reason": "liepin_browser_login_required",
            }
        ]

        session_response = client.get(
            f"/api/workbench/sessions/{session['sessionId']}",
            headers=_csrf_header(client),
        )
        liepin_card = next(
            card for card in session_response.json()["sourceCards"] if card["sourceKind"] == "liepin"
        )
        assert liepin_card["status"] == "blocked"
        assert liepin_card["authState"] == "login_required"
        assert liepin_card["warningCode"] == "liepin_browser_login_required"
        assert "本机 Chrome" in liepin_card["warningMessage"]
```

- [ ] **Step 5: Write the probe unavailable test**

Add this test:

```python
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
        client.app.state.liepin_worker_client = worker
        client.app.state.workbench_job_runner.liepin_worker_client = worker

        session = _create_session(client, source_kinds=["cts", "liepin"])
        _approve_triage(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert response.status_code == 202
        body = response.text
        assert "pi command missing" not in body
        assert "/secret/path" not in body
        payload = response.json()
        assert [run["sourceKind"] for run in payload["sourceRuns"]] == ["cts"]
        assert payload["blockedSources"][0]["sourceKind"] == "liepin"
        assert payload["blockedSources"][0]["reason"] == "liepin_browser_probe_unavailable"
```

- [ ] **Step 6: Write the account mismatch test**

Add this test:

```python
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
        client.app.state.liepin_worker_client = worker
        client.app.state.workbench_job_runner.liepin_worker_client = worker

        session = _create_session(client, source_kinds=["liepin"])
        _approve_triage(client, session["sessionId"])

        response = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )

        assert response.status_code == 202
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
        assert "acct_hash_other" not in response.text
```

- [ ] **Step 7: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_workbench_liepin_browser_session_probe.py -q
```

Expected before implementation: at least the ready test fails because the start route does not call `session_status(...)` before `start_source_run_job(...)`.

## Task 2: Store Methods For Safe Probe State

**Files:**
- Modify: `src/seektalent_ui/workbench_store.py`
- Test: `tests/test_workbench_liepin_browser_session_probe.py`

- [ ] **Step 1: Update the existing Liepin warning constant and add safe reason constants**

Near the existing `LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE`, replace that message value and add the missing reason constants:

```python
LIEPIN_BROWSER_LOGIN_REQUIRED_CODE = "liepin_browser_login_required"
LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE = "liepin_browser_probe_unavailable"
LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE = "liepin_browser_account_mismatch"
LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE = "请在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。"
LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE = "浏览器检索通道暂不可用，请确认本机应用和浏览器助手正常后重试。"
LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE = "当前 Chrome 中的猎聘账号与此工作台绑定不一致，请切换账号后重试。"
```

Keep the existing English "Liepin login has not been connected yet." only in legacy connection creation. Source cards should receive the Chinese recruiter-facing message through the source-run warning. Do not create a second `LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE` definition.

- [ ] **Step 2: Add `mark_liepin_connection_login_required`**

Add this method to `WorkbenchStore` after `mark_liepin_connection_connected(...)`:

```python
    def mark_liepin_connection_login_required(
        self,
        *,
        user: WorkbenchUser,
        connection_id: str,
        warning_code: str,
        warning_message: str,
        session_id: str | None = None,
        source_run_id: str | None = None,
    ) -> WorkbenchSourceConnection | None:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT *
                FROM source_connections
                WHERE tenant_id = ? AND workspace_id = ? AND user_id = ? AND connection_id = ? AND source_kind = 'liepin'
                """,
                (DEFAULT_TENANT_ID, user.workspace_id, user.user_id, connection_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE source_connections
                SET status = 'login_required',
                    warning_code = ?,
                    warning_message = ?,
                    connected_at = NULL,
                    updated_at = ?
                WHERE connection_id = ?
                """,
                (warning_code, warning_message, now, connection_id),
            )
            _append_connection_status_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                connection_id=connection_id,
                source_kind="liepin",
                status="login_required",
                event_name="source_connection_status_changed",
                payload={
                    "connectionId": connection_id,
                    "sourceKind": "liepin",
                    "status": "login_required",
                    "warningCode": warning_code,
                },
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=source_run_id,
                source_kind="liepin",
                event_name="source_connection_status_changed",
                payload={
                    "connectionId": connection_id,
                    "sourceKind": "liepin",
                    "status": "login_required",
                    "warningCode": warning_code,
                },
            )
            updated = conn.execute("SELECT * FROM source_connections WHERE connection_id = ?", (connection_id,)).fetchone()
        return _source_connection_from_row(updated)
```

Pass `session_id` and `source_run_id` from the automatic source-start probe path. This keeps connection-state changes visible to session-scoped SSE consumers and global workbench event consumers when the browser probe returns login-required, account-mismatch, or probe-unavailable.

- [ ] **Step 3: Add `mark_liepin_connection_connected_for_source_run`**

Add this scoped method to `WorkbenchStore` after `mark_liepin_connection_connected(...)`. This method is for automatic source-start probing; it must not reuse the existing broad source-run update inside `mark_liepin_connection_connected(...)`.

```python
    def mark_liepin_connection_connected_for_source_run(
        self,
        *,
        user: WorkbenchUser,
        connection_id: str,
        session_id: str,
        source_run_id: str,
        provider_account_hash: str,
        compliance_gate_ref: str | None = None,
    ) -> WorkbenchSourceConnection | None:
        self._initialize()
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            connection_row = conn.execute(
                """
                SELECT *
                FROM source_connections
                WHERE tenant_id = ? AND workspace_id = ? AND user_id = ? AND connection_id = ? AND source_kind = 'liepin'
                """,
                (DEFAULT_TENANT_ID, user.workspace_id, user.user_id, connection_id),
            ).fetchone()
            if connection_row is None:
                return None
            source_run_row = conn.execute(
                """
                SELECT sr.*
                FROM source_runs AS sr
                JOIN sessions AS s ON s.session_id = sr.session_id
                WHERE sr.source_run_id = ?
                  AND sr.session_id = ?
                  AND sr.source_kind = 'liepin'
                  AND sr.workspace_id = ?
                  AND sr.user_id = ?
                  AND s.user_id = ?
                """,
                (source_run_id, session_id, user.workspace_id, user.user_id, user.user_id),
            ).fetchone()
            if source_run_row is None:
                return None
            conn.execute(
                """
                UPDATE source_connections
                SET status = 'connected',
                    warning_code = NULL,
                    warning_message = NULL,
                    provider_account_hash = ?,
                    compliance_gate_ref = COALESCE(?, compliance_gate_ref),
                    connected_at = ?,
                    updated_at = ?
                WHERE connection_id = ?
                """,
                (provider_account_hash, compliance_gate_ref, now, now, connection_id),
            )
            conn.execute(
                """
                UPDATE source_runs
                SET status = 'queued',
                    auth_state = 'not_required',
                    warning_code = NULL,
                    warning_message = NULL
                WHERE source_run_id = ?
                  AND session_id = ?
                  AND source_kind = 'liepin'
                  AND status = 'blocked'
                """,
                (source_run_id, session_id),
            )
            _append_connection_status_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                connection_id=connection_id,
                source_kind="liepin",
                status="connected",
                event_name="source_connection_login_completed",
                payload={"connectionId": connection_id, "sourceKind": "liepin", "status": "connected"},
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=source_run_id,
                source_kind="liepin",
                event_name="source_connection_status_changed",
                payload={"connectionId": connection_id, "sourceKind": "liepin", "status": "connected"},
            )
            _append_security_audit_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                actor_user_id=user.user_id,
                actor_role=user.role,
                target_type="source_connection",
                target_id=connection_id,
                action="liepin_login_completed",
                result="success",
                reason_code="verified",
                metadata={"sourceKind": "liepin", "status": "connected"},
                created_at=now,
            )
            updated = conn.execute("SELECT * FROM source_connections WHERE connection_id = ?", (connection_id,)).fetchone()
        return _source_connection_from_row(updated)
```

- [ ] **Step 4: Add `block_source_run_for_start_probe`**

Add this method to `WorkbenchStore` near `start_source_run_job(...)`:

```python
    def block_source_run_for_start_probe(
        self,
        *,
        user: WorkbenchUser,
        session_id: str,
        source_run_id: str,
        warning_code: str,
        warning_message: str,
    ) -> WorkbenchSourceRun | None:
        self._initialize()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT sr.*
                FROM source_runs AS sr
                JOIN sessions AS s ON s.session_id = sr.session_id
                WHERE sr.source_run_id = ?
                  AND sr.session_id = ?
                  AND sr.workspace_id = ?
                  AND sr.user_id = ?
                  AND s.user_id = ?
                """,
                (source_run_id, session_id, user.workspace_id, user.user_id, user.user_id),
            ).fetchone()
            if row is None:
                return None
            if (
                row["source_kind"] != "liepin"
                or row["status"] in {"queued", "running", "completed", "failed"}
                or not (row["status"] == "blocked" or row["auth_state"] == "login_required")
            ):
                return _source_run_from_row(row)
            conn.execute(
                """
                UPDATE source_runs
                SET status = 'blocked',
                    auth_state = 'login_required',
                    warning_code = ?,
                    warning_message = ?
                WHERE source_run_id = ?
                  AND session_id = ?
                  AND source_kind = 'liepin'
                  AND status NOT IN ('queued', 'running', 'completed', 'failed')
                  AND (status = 'blocked' OR auth_state = 'login_required')
                """,
                (warning_code, warning_message, source_run_id, session_id),
            )
            _append_workbench_event_conn(
                conn,
                tenant_id=DEFAULT_TENANT_ID,
                workspace_id=user.workspace_id,
                user_id=user.user_id,
                session_id=session_id,
                source_run_id=source_run_id,
                source_kind="liepin",
                event_name="source_run_blocked",
                payload={
                    "sessionId": session_id,
                    "sourceRunId": source_run_id,
                    "sourceKind": "liepin",
                    "warningCode": warning_code,
                },
            )
            updated = conn.execute("SELECT * FROM source_runs WHERE source_run_id = ?", (source_run_id,)).fetchone()
        return _source_run_from_row(updated)
```

- [ ] **Step 5: Reuse constants in existing default and no-connection branches**

In `_new_source_run("liepin")`, replace hard-coded `warning_code="login_required"` with:

```python
warning_code=LIEPIN_BROWSER_LOGIN_REQUIRED_CODE
```

In `start_source_run_job(...)`, replace hard-coded `warning_code = 'login_required'` and the old warning message for Liepin with:

```python
warning_code = LIEPIN_BROWSER_LOGIN_REQUIRED_CODE
warning_message = LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE
```

The raised `PermissionError` can stay as `"liepin_connection_not_connected"` because the new start route probe handles the public blocked reason before this branch in the normal Pi path.

- [ ] **Step 6: Run the backend tests**

Run:

```bash
uv run pytest tests/test_workbench_liepin_browser_session_probe.py -q
```

Expected: still failing because the route has not called the probe helper yet; store-method import or typing errors should be fixed before continuing.

## Task 3: Automatic Probe In Session Start Route

**Files:**
- Modify: `src/seektalent_ui/workbench_routes.py`
- Test: `tests/test_workbench_liepin_browser_session_probe.py`

- [ ] **Step 1: Add imports**

Add imports near the existing Liepin imports:

```python
from dataclasses import dataclass

from seektalent.providers.liepin.worker_contracts import SessionStatus
from seektalent_ui.workbench_store import (
    LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE,
    LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE,
    LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
    LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE,
    LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
    LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
)
```

If `workbench_routes.py` already imports several names from `workbench_store`, merge these names into that existing import instead of adding a duplicate import block.

- [ ] **Step 2: Add new reason codes to the runtime reason allowlist**

In `src/seektalent_ui/workbench_routes.py`, extend `RUNTIME_SOURCE_REASON_CODES`:

```python
RUNTIME_SOURCE_REASON_CODES = {
    "blocked_backend_unavailable",
    "failed_provider_error",
    "login_required",
    "partial_timeout",
    "cancelled_by_user",
    "liepin_connection_not_connected",
    "liepin_browser_login_required",
    "liepin_browser_probe_unavailable",
    "liepin_browser_account_mismatch",
    "runtime_failed",
}
```

- [ ] **Step 3: Add a small private probe result dataclass**

Add this helper model near `_liepin_worker_client(...)`:

```python
@dataclass(frozen=True)
class _LiepinStartProbeResult:
    ready: bool
    reason_code: str | None = None
    warning_message: str | None = None
```

- [ ] **Step 4: Add status mapping helpers**

Add:

```python
def _liepin_probe_unavailable_result() -> _LiepinStartProbeResult:
    return _LiepinStartProbeResult(
        ready=False,
        reason_code=LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
        warning_message=LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
    )


def _liepin_probe_login_required_result() -> _LiepinStartProbeResult:
    return _LiepinStartProbeResult(
        ready=False,
        reason_code=LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
        warning_message=LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE,
    )


def _liepin_probe_account_mismatch_result() -> _LiepinStartProbeResult:
    return _LiepinStartProbeResult(
        ready=False,
        reason_code=LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE,
        warning_message=LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE,
    )
```

- [ ] **Step 5: Add `_ensure_liepin_browser_session_ready_for_start`**

Add this async helper near the session-start route helpers:

```python
async def _ensure_liepin_browser_session_ready_for_start(
    *,
    request: Request,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
    source_run_id: str,
) -> _LiepinStartProbeResult:
    connection, _created = store.get_or_create_liepin_source_connection(user=user)
    try:
        worker_client = _liepin_worker_client(request)
        status: SessionStatus = await worker_client.session_status(
            connection_id=connection.connection_id,
            tenant=DEFAULT_TENANT_ID,
            workspace=user.workspace_id,
            provider_account_hash=connection.provider_account_hash,
        )
    except LiepinWorkerModeError:
        store.mark_liepin_connection_login_required(
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
            warning_message=LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        )
        return _liepin_probe_unavailable_result()

    if status.status != "ready":
        store.mark_liepin_connection_login_required(
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_LOGIN_REQUIRED_CODE,
            warning_message=LIEPIN_BROWSER_LOGIN_REQUIRED_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        )
        return _liepin_probe_login_required_result()
    if not status.provider_account_hash:
        store.mark_liepin_connection_login_required(
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
            warning_message=LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        )
        return _liepin_probe_unavailable_result()
    if connection.provider_account_hash and connection.provider_account_hash != status.provider_account_hash:
        store.mark_liepin_connection_login_required(
            user=user,
            connection_id=connection.connection_id,
            warning_code=LIEPIN_BROWSER_ACCOUNT_MISMATCH_CODE,
            warning_message=LIEPIN_BROWSER_ACCOUNT_MISMATCH_MESSAGE,
            session_id=session_id,
            source_run_id=source_run_id,
        )
        return _liepin_probe_account_mismatch_result()

    app_settings = _workbench_app_settings(request)
    compliance_gate_ref = _ensure_workbench_liepin_provider_connection(
        settings=app_settings,
        user=user,
        connection=connection,
        provider_account_hash=status.provider_account_hash,
    )
    _record_workbench_liepin_provider_session(
        settings=app_settings,
        user=user,
        connection_id=connection.connection_id,
        compliance_gate_ref=compliance_gate_ref,
        provider_account_hash=status.provider_account_hash,
    )
    updated_connection = store.mark_liepin_connection_connected_for_source_run(
        user=user,
        connection_id=connection.connection_id,
        session_id=session_id,
        source_run_id=source_run_id,
        provider_account_hash=status.provider_account_hash,
        compliance_gate_ref=compliance_gate_ref,
    )
    if updated_connection is None:
        store.block_source_run_for_start_probe(
            user=user,
            session_id=session_id,
            source_run_id=source_run_id,
            warning_code=LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
            warning_message=LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
        )
        return _liepin_probe_unavailable_result()
    return _LiepinStartProbeResult(ready=True)
```

- [ ] **Step 6: Make the start route async and call the probe only for blocked/login-required Liepin runs**

Change:

```python
def start_session_source_runs(
```

to:

```python
async def start_session_source_runs(
```

Then add this block inside the source-run loop before `store.start_source_run_job(...)`:

```python
        if (
            source_run.source_kind == "liepin"
            and source_run.status not in {"queued", "running"}
            and (source_run.status == "blocked" or source_run.auth_state == "login_required")
        ):
            probe = await _ensure_liepin_browser_session_ready_for_start(
                request=request,
                store=store,
                user=user,
                session_id=session_id,
                source_run_id=source_run.source_run_id,
            )
            if not probe.ready:
                store.block_source_run_for_start_probe(
                    user=user,
                    session_id=session_id,
                    source_run_id=source_run.source_run_id,
                    warning_code=probe.reason_code or LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
                    warning_message=probe.warning_message or LIEPIN_BROWSER_PROBE_UNAVAILABLE_MESSAGE,
                )
                blocked.append(
                    WorkbenchSessionStartBlockedSourceResponse(
                        sourceRunId=source_run.source_run_id,
                        sourceKind=source_run.source_kind,
                        reason=probe.reason_code or LIEPIN_BROWSER_PROBE_UNAVAILABLE_CODE,
                    )
                )
                continue
```

Do not run this probe for already `queued` or `running` Liepin source runs. Repeated `启动 Agent` clicks must not turn an active lane into a blocked lane because a later probe failed. Store-side connection/status writes must also re-check the current source run inside the same transaction before downgrading a Liepin connection, because the route's initial session snapshot can be stale during concurrent repeated-start requests.

When `store.start_source_run_job(...)` returns an existing queued/running job with `was_created=False`, the route must still wake the runner. The running note is idempotent, but a queued job can otherwise remain idle after a process restart or missed wake.

- [ ] **Step 7: Map legacy PermissionError to safe public reason**

In the `except PermissionError as exc:` branch inside `start_session_source_runs(...)`, replace direct `reason=str(exc)` with:

```python
            reason = str(exc)
            if source_run.source_kind == "liepin" and reason == "liepin_connection_not_connected":
                reason = LIEPIN_BROWSER_LOGIN_REQUIRED_CODE
            blocked.append(
                WorkbenchSessionStartBlockedSourceResponse(
                    sourceRunId=source_run.source_run_id,
                    sourceKind=source_run.source_kind,
                    reason=reason,
                )
            )
```

In the `except RuntimeError as exc:` branch, keep only `source_run_already_terminal` as an idempotent no-op. Any other unexpected `RuntimeError` should fail the request with a generic safe detail instead of entering `blockedSources` with the raw exception string:

```python
            if str(exc) == "source_run_already_terminal":
                continue
            raise HTTPException(status_code=500, detail="source_run_start_failed") from exc
```

- [ ] **Step 8: Add idempotency and runtime reason projection assertions**

Add these assertions to `tests/test_workbench_liepin_browser_session_probe.py`:

```python
def test_repeated_start_does_not_reprobe_or_block_queued_liepin_run(tmp_path) -> None:
    with _client(tmp_path) as client:
        _bootstrap_and_login(client)
        worker = ProbeLiepinWorker(status="ready", provider_account_hash="acct_hash_browser_ready")
        client.app.state.liepin_worker_client = worker
        client.app.state.workbench_job_runner.liepin_worker_client = worker

        session = _create_session(client, source_kinds=["liepin"])
        _approve_triage(client, session["sessionId"])

        first = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )
        assert first.status_code == 202
        assert first.json()["blockedSources"] == []
        assert len(worker.probe_calls) == 1

        worker.status = "login_required"
        worker.provider_account_hash = None
        second = client.post(
            f"/api/workbench/sessions/{session['sessionId']}/start",
            headers=_csrf_header(client),
        )
        assert second.status_code == 202
        assert second.json()["blockedSources"] == []
        assert len(worker.probe_calls) == 1
```

Also add tests for:

- a probe-time race where another request has already connected and queued the Liepin source run; the later failed probe must not downgrade the connection or append `blockedSources`.
- repeated start returning an existing queued job; the route must still call `runner.wake()`.

In the login-required and account-mismatch tests, after fetching the session response, assert the runtime reason projection preserves the safe reason code when present:

```python
runtime_sources = session_response.json().get("runtimeSourceState", {}).get("sources", [])
liepin_runtime = next((source for source in runtime_sources if source["sourceKind"] == "liepin"), None)
if liepin_runtime is not None:
    assert liepin_runtime["reasonCode"] in {
        "liepin_browser_login_required",
        "liepin_browser_probe_unavailable",
        "liepin_browser_account_mismatch",
    }
```

- [ ] **Step 9: Run backend tests**

Run:

```bash
uv run pytest tests/test_workbench_liepin_browser_session_probe.py tests/test_workbench_api.py -q
```

Expected: pass. If existing tests expected `warningCode == "login_required"` for Liepin source cards, update them to the safe product code `liepin_browser_login_required`.

## Task 4: Legacy Managed-Browser Relay Default-Off Boundary

**Files:**
- Modify: `src/seektalent/config.py`
- Modify: `src/seektalent_ui/workbench_routes.py`
- Modify: `tests/test_workbench_liepin_browser_session_probe.py`

- [ ] **Step 1: Add a default-off settings flag**

In `src/seektalent/config.py`, add this field near `workbench_enabled`:

```python
    workbench_legacy_liepin_login_relay_enabled: bool = False
```

- [ ] **Step 2: Add a route guard helper**

In `src/seektalent_ui/workbench_routes.py`, add this helper near `_workbench_app_settings(...)` or other route helpers:

```python
def _require_legacy_liepin_login_relay_enabled(request: Request) -> None:
    settings = _workbench_app_settings(request)
    if not settings.workbench_legacy_liepin_login_relay_enabled:
        raise HTTPException(status_code=410, detail="liepin_legacy_login_relay_disabled")
```

- [ ] **Step 3: Guard all legacy relay endpoints**

At the start of these route handlers, call `_require_legacy_liepin_login_relay_enabled(request)` before any worker call or store mutation:

```python
start_liepin_connection_login(...)
liepin_connection_login_frame(...)
liepin_connection_login_snapshot(...)
liepin_connection_login_input(...)
complete_liepin_connection_login(...)
```

The Svelte primary flow must not call these endpoints. The default product behavior is source-start probe through `LiepinWorkerClient.session_status(...)`.

- [ ] **Step 4: Prevent broad source-run unlock from legacy completion**

If the legacy complete route remains available behind the explicit flag, it must not call the broad `mark_liepin_connection_connected(...)` method in a way that unblocks all sessions. Replace the source-run side effect with this connection-only call:

```python
updated = store.mark_liepin_connection_connected_without_source_runs(
    user=user,
    connection_id=connection_id,
    provider_account_hash=provider_account_hash,
    compliance_gate_ref=connection.compliance_gate_ref,
)
```

Add `mark_liepin_connection_connected_without_source_runs(...)` as a connection-only store method. It should update `source_connections` to `connected`, clear warning fields, persist `provider_account_hash` and `compliance_gate_ref`, append the connection status event and security audit event, and must not update the `source_runs` table. Do not update `source_runs` from the legacy completion route.

Also append a global workbench event from the connection-only method so event consumers still refresh connection state even though no source run is unlocked:

```python
_append_workbench_event_conn(
    conn,
    tenant_id=DEFAULT_TENANT_ID,
    workspace_id=user.workspace_id,
    user_id=user.user_id,
    session_id=None,
    source_run_id=None,
    source_kind="liepin",
    event_name="source_connection_status_changed",
    payload={"connectionId": connection_id, "sourceKind": "liepin", "status": "connected"},
)
```

- [ ] **Step 5: Update existing legacy relay tests to opt in explicitly**

In `tests/test_workbench_api.py`, update the shared `_client(...)` helper to accept settings overrides:

```python
def _client(
    tmp_path: Path,
    *,
    runtime_factory=FakeWorkbenchRuntime,
    settings_overrides: dict[str, object] | None = None,
) -> TestClient:
    settings = make_settings(
        workspace_root=str(tmp_path),
        mock_cts=True,
        **(settings_overrides or {}),
    )
    return TestClient(
        create_app(RunRegistry(settings, runtime_factory=runtime_factory), settings=settings),
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    )
```

If `_client(...)` already has more parameters, preserve them and add only the `settings_overrides` behavior. Then update legacy relay tests that intentionally exercise `/login`, `/login/frame`, `/login/snapshot`, `/login/input`, or `/login/complete` to pass:

```python
with _client(
    tmp_path,
    settings_overrides={"workbench_legacy_liepin_login_relay_enabled": True},
) as client:
```

At minimum, update these existing tests if they are still present:

- `test_liepin_login_handoff_is_safe_and_updates_source_card_state`
- `test_liepin_login_handoff_rejects_unknown_connection_before_worker_call`
- `test_liepin_login_relay_exposes_safe_frame_and_marks_connection_connected`
- `test_liepin_login_relay_complete_keeps_connection_unconnected_when_worker_cannot_verify_login`

Do not loosen the new default-off test. Product-default tests should keep the relay disabled.

- [ ] **Step 6: Add tests for default-off legacy relay**

Add this test to `tests/test_workbench_liepin_browser_session_probe.py`:

```python
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
```

- [ ] **Step 7: Run backend tests**

Run:

```bash
uv run pytest tests/test_workbench_liepin_browser_session_probe.py tests/test_workbench_api.py -q
```

Expected: pass. If older tests explicitly exercise the legacy relay, update those tests to set `workbench_legacy_liepin_login_relay_enabled=True` in settings; do not let default product tests rely on legacy relay.

## Task 5: Public Payload And Event Non-Leak Coverage

**Files:**
- Modify: `tests/test_workbench_liepin_browser_session_probe.py`

- [ ] **Step 1: Add forbidden leak assertions**

Add this helper:

```python
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
```

- [ ] **Step 2: Use helper in public responses and event responses**

In each test from Task 1, call:

```python
assert_no_probe_leaks(response.text)
assert_no_probe_leaks(session_response.text)
```

where `session_response` exists. In tests that do not already fetch the session, add:

```python
session_response = client.get(
    f"/api/workbench/sessions/{session['sessionId']}",
    headers=_csrf_header(client),
)
assert session_response.status_code == 200
assert_no_probe_leaks(session_response.text)
```

For every test, also fetch session events, global events, and security audit events:

```python
session_events = client.get(
    f"/api/workbench/sessions/{session['sessionId']}/events",
    headers=_csrf_header(client),
)
global_events = client.get("/api/workbench/events", headers=_csrf_header(client))
security_events = client.get("/api/workbench/security-audit-events", headers=_csrf_header(client))

assert session_events.status_code == 200
assert global_events.status_code == 200
assert security_events.status_code == 200
assert_no_probe_leaks(session_events.text)
assert_no_probe_leaks(global_events.text)
assert_no_probe_leaks(security_events.text)
```

For tests that use fake account hashes, assert those exact fake hashes are not exposed in public responses or events. Call the helper with the fake values used by the test, such as:

```python
assert_no_probe_leaks(response.text, "acct_hash_browser_ready", "acct_hash_bound", "acct_hash_other")
assert_no_probe_leaks(session_response.text, "acct_hash_browser_ready", "acct_hash_bound", "acct_hash_other")
```

This scan must be case-insensitive. It should catch both `cookie` and `Cookie`, and it should verify dynamic fake account hashes instead of only static forbidden strings.

- [ ] **Step 3: Run focused tests**

Run:

```bash
uv run pytest tests/test_workbench_liepin_browser_session_probe.py -q
```

Expected: pass.

## Task 6: Svelte Source Card Passive Status

**Files:**
- Modify: `apps/web-svelte/src/lib/workbench/sourceDisplay.ts`
- Modify: `apps/web-svelte/src/lib/components/SourceCard.svelte`
- Test: `apps/web-svelte/src/lib/components/SourceCard.test.ts`

- [ ] **Step 1: Add display copy for new reason codes**

In `apps/web-svelte/src/lib/workbench/sourceDisplay.ts`, ensure the reason map contains:

```ts
const SOURCE_REASON_COPY: Record<string, string> = {
  liepin_browser_login_required: '请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。',
  liepin_browser_probe_unavailable: '浏览器检索通道暂不可用，请确认本机应用和浏览器助手正常后重试。',
  liepin_browser_account_mismatch: '当前 Chrome 中的猎聘账号与此工作台绑定不一致，请切换账号后重试。',
  login_required: '请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。',
  liepin_connection_not_connected: '本机 Chrome 的猎聘登录态尚未就绪。',
};
```

If the file already has a reason map, merge these entries into it instead of creating a second map.

- [ ] **Step 2: Keep SourceCard passive**

In `apps/web-svelte/src/lib/components/SourceCard.svelte`, make sure the Liepin blocked/login state renders text and no action link. The visible Liepin labels should be:

Reuse the existing source identity subtitle slot, for example the current `<span>{sourceSubtitle(card)}</span>` next to the source title. Do not add a second subtitle paragraph or a new source-card layout node; this card must keep the compact reference layout. The Liepin subtitle text should be `使用本机 Chrome 登录态`.

The blocked status label should read:

```ts
function sourceStatusText(status: string, sourceCard: WorkbenchSourceCard) {
  const liepinLoginReasonCodes = new Set([
    'login_required',
    'liepin_browser_login_required',
    'liepin_connection_not_connected'
  ]);
  if (
    sourceCard.sourceKind === 'liepin' &&
    (liepinLoginReasonCodes.has(sourceCard.connectionWarningCode ?? '') ||
      liepinLoginReasonCodes.has(sourceCard.warningCode ?? '') ||
      String(sourceCard.connectionStatus ?? '') === 'needs_login' ||
      String(sourceCard.connectionStatus ?? '') === 'login_required')
  ) {
    return '需登录猎聘';
  }
  return runtimeStatusLabel(status);
}
```

The visible warning copy should prefer safe Liepin reason-code mapping over older stored warning strings. Add or update the warning helper so `liepin_browser_*` codes cannot be hidden behind stale English `warningMessage` or `connectionWarningMessage` values:

```ts
function sourceWarningMessage(sourceCard: WorkbenchSourceCard, runtimeReasonCode: string | null) {
  const reasonCode = runtimeReasonCode ?? sourceCard.warningCode ?? sourceCard.connectionWarningCode;
  const mappedReason = sourceReasonLabel(reasonCode);
  if (sourceCard.sourceKind === 'liepin' && mappedReason) {
    return mappedReason;
  }
  if (sourceCard.warningMessage) {
    return sourceCard.warningMessage;
  }
  if (sourceCard.connectionWarningMessage) {
    return sourceCard.connectionWarningMessage;
  }
  return mappedReason;
}
```

Do not render links or buttons with text containing `连接猎聘`, `probe`, `login/frame`, or `snapshot`.

The strategy graph source-queue detail payload must reuse the same safe reason mapping, for example through `sourceReasonLabel(...)`, so `NodeDetailPanel` does not show generic "源状态异常，请查看设置。" for `liepin_browser_login_required`, `liepin_browser_probe_unavailable`, or `liepin_browser_account_mismatch`.

- [ ] **Step 3: Add component test**

Add or update this test in `apps/web-svelte/src/lib/components/SourceCard.test.ts`:

```ts
import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { WorkbenchSession } from '$lib/workbench/types';

import SourceCard from './SourceCard.svelte';

const liepinLoginRequiredCard = {
  sourceRunId: 'src-liepin',
  sourceKind: 'liepin',
  label: 'Liepin',
  status: 'blocked',
  authState: 'login_required',
  warningCode: 'liepin_browser_login_required',
  warningMessage: '请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。',
  cardsScannedCount: 0,
  uniqueCandidatesCount: 0,
  detailOpenUsedCount: 0,
  detailOpenBlockedCount: 0,
  connectionId: null,
  connectionStatus: null,
  connectionWarningCode: null,
  connectionWarningMessage: null
} as WorkbenchSession['sourceCards'][number];

const session = {
  runtimeSourceState: {
    sources: []
  }
} as unknown as WorkbenchSession;

describe('SourceCard', () => {
  it('shows passive local Chrome Liepin login guidance without a connect action', () => {
    render(SourceCard, {
      props: {
        card: liepinLoginRequiredCard,
        session,
        triageApproved: false
      },
    });

    expect(screen.getByText('使用本机 Chrome 登录态')).toBeInTheDocument();
    expect(screen.getByText('需登录猎聘')).toBeInTheDocument();
    expect(screen.getByText('请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /连接猎聘|继续登录|probe/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /连接猎聘|继续登录|probe/i })).not.toBeInTheDocument();
  });

  it('prefers safe Liepin reason copy over stale stored warning text', () => {
    render(SourceCard, {
      props: {
        card: {
          ...liepinLoginRequiredCard,
          warningMessage: 'Liepin login is not connected yet.',
          connectionStatus: 'login_required',
          connectionWarningCode: 'login_required',
          connectionWarningMessage: 'connection not connected'
        },
        session,
        triageApproved: false
      },
    });

    expect(screen.getByText('请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。')).toBeInTheDocument();
    expect(screen.queryByText('Liepin login is not connected yet.')).not.toBeInTheDocument();
    expect(screen.queryByText('connection not connected')).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 4: Run Svelte component test**

Run:

```bash
cd apps/web-svelte && npm run test -- SourceCard.test.ts
```

Expected: pass.

## Task 7: Integration Regression Checks

**Files:**
- Modify only files touched by prior tasks if these checks expose a concrete failure.

- [ ] **Step 1: Python verification**

Run:

```bash
uv run pytest tests/test_workbench_liepin_browser_session_probe.py tests/test_workbench_api.py tests/test_liepin_pi_executor.py tests/test_liepin_runtime_source_lane.py -q
uv run pytest tests/test_liepin_pi_worker_client.py -q
```

Expected: pass.

- [ ] **Step 2: Ruff verification**

Run:

```bash
uv run ruff check src/seektalent/config.py src/seektalent/providers/liepin/client.py src/seektalent/providers/liepin/pi_worker_client.py src/seektalent_ui/workbench_routes.py src/seektalent_ui/workbench_store.py tests/test_liepin_pi_worker_client.py tests/test_workbench_api.py tests/test_workbench_liepin_browser_session_probe.py
```

Expected: pass.

- [ ] **Step 3: Svelte verification**

Run:

```bash
cd apps/web-svelte && npm run check && npm run test -- SourceCard.test.ts
```

Expected: pass.

- [ ] **Step 4: Static no-legacy-login check for the Svelte primary flow**

Run:

```bash
rg -n "login/frame|login/snapshot|login/input|login/complete|server_managed_browser|safeFrame|handoff" apps/web-svelte/src/routes apps/web-svelte/src/lib/components apps/web-svelte/src/lib/workbench apps/web-svelte/src/lib/api/workbench.ts
```

Expected: no matches in the Svelte primary flow.

- [ ] **Step 5: Static default-off legacy relay check for backend**

Run:

```bash
rg -n "source-connections/\\{connection_id\\}/login|login/frame|login/snapshot|login/input|login/complete|server_managed_browser|safeFrameUrl" src/seektalent_ui/workbench_routes.py
```

Expected: matches are allowed only inside route handlers guarded by `_require_legacy_liepin_login_relay_enabled(request)` or response models that are retained for compatibility. No Svelte route or source-start path should call those endpoints.

- [ ] **Step 6: Diff whitespace check**

Run:

```bash
git diff --check
```

Expected: no whitespace errors.

## Task 8: Manual Local Smoke

**Files:**
- No code files unless the smoke exposes a concrete defect.

- [ ] **Step 1: Start backend with Pi mode disabled**

Run:

```bash
uv run seektalent-ui-api --host 127.0.0.1 --port 8012
```

Expected: backend starts with the current local settings. If local settings fail earlier because `liepin_worker_mode=pi_agent` is partially configured, record the exact config error in the implementation notes and fix only if it is caused by this change.

- [ ] **Step 2: Start Svelte frontend**

Run:

```bash
cd apps/web-svelte && npm run dev -- --host 127.0.0.1 --port 5178
```

Expected: frontend starts at `http://127.0.0.1:5178`.

- [ ] **Step 3: Browser check for disabled Pi path**

In the Workbench:

1. Log in.
2. Create or open a session with CTS + Liepin.
3. Approve search criteria.
4. Click `启动 Agent`.

Expected: CTS starts; Liepin shows a passive blocked status with local Chrome/browser-helper wording; no `连接猎聘` button appears.

- [ ] **Step 4: Browser check for fake ready worker path**

Use the backend test from Task 1 as the authoritative ready-path smoke because it injects `app.state.liepin_worker_client` through the existing test client. Do not add a hidden UI button or alternate endpoint for this smoke.

## Self-Review Checklist

- Spec coverage:
  - Worker contract parity and real Pi mismatch behavior: Task 0.
  - Automatic probe on source start: Task 3.
  - No extra button / passive Svelte card: Task 6.
  - CTS continues when Liepin blocks: Task 1.
  - Successful probe is scoped to the current source run: Task 1 and Task 2.
  - Provider account mismatch blocks safely: Task 1 and Task 3.
  - Repeated start does not re-probe queued/running Liepin lanes: Task 3.
  - Legacy managed-browser relay is default-off, existing legacy tests opt in explicitly, and legacy completion cannot broadly unlock source runs: Task 4.
  - Pi/DokoBot stays inside worker boundary: Task 3 uses `LiepinWorkerClient.session_status(...)`; no direct Chrome/DokoBot access is added.
  - Safe reason codes and non-leakage: Tasks 2, 3, and 5.
  - Public event non-leakage: Task 5.
  - Svelte card uses existing compact layout, treats `login_required` connection status as a login-required state, and prefers safe reason-code copy over stale stored warning text: Task 6.
  - Verification: Task 7 and Task 8.
- Placeholder scan:
  - The plan avoids deferred-work markers, vague error-handling instructions, and cross-task shorthand.
- Type consistency:
  - `SessionStatus` comes from `seektalent.providers.liepin.worker_contracts`.
  - Worker errors use `LiepinWorkerModeError`.
  - The new route helper returns `_LiepinStartProbeResult`.
  - Store methods return existing `WorkbenchSourceConnection | None` and `WorkbenchSourceRun | None`.
