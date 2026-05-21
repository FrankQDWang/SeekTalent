# Runtime-Owned Multi-Source Workbench Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Workbench start one Runtime-owned CTS+Liepin sourcing run that uses the mature Runtime round loop, dispatches the same logical query bundle to both sources in parallel, merges duplicates, and returns one identity-level Top 10.

**Architecture:** Keep Runtime as the only multi-source orchestration owner. Add a session-level Workbench runtime job, route Workbench start through `WorkflowRuntime.run(..., source_kinds=("cts", "liepin"))`, and move source fan-out into the existing `_run_rounds(...)` flow instead of the simplified source-lane shortcut. Persist Runtime finalization and source coverage back into Workbench as projections.

**Tech Stack:** Python 3.12, asyncio `TaskGroup`, SQLite Workbench store, existing Runtime retrieval/query planning, existing Liepin Pi/OpenCLI lane adapter, pytest, Svelte/Vitest.

---

## Spec Link

This plan implements:

`docs/superpowers/specs/2026-05-21-runtime-owned-multi-source-workbench-execution-design.md`

## Execution Notes

- Execute in a new worktree and branch.
- Use tests first for each behavior.
- Do not rewrite the full Runtime. This is a targeted execution-boundary refactor.
- Preserve CTS-only CLI and CTS-only Runtime behavior unless a test explicitly covers Workbench multi-source.
- Do not remove existing source-run rows. They remain UI/status projections.
- Do not use Workbench as a source orchestrator after this plan.
- Do not expose Pi/OpenCLI/DokoBot internals in Workbench public UI payloads.
- Do not implement source dispatch with broad `except Exception` coverage. Provider failures may degrade a source; Runtime invariant/programmer errors must fail the round.
- Do not disable CTS query-outcome scoring. Query-outcome scoring is an ephemeral round-control signal; final ranking still happens after merge.
- Do not derive Workbench final ranking from raw review item projection for new runtime-owned runs. Persist and read Runtime finalization order first.

## File Map

Modify:

- `src/seektalent_ui/workbench_store.py`
  - Add session-level runtime sourcing jobs.
  - Add source-run projection helpers for Runtime-run lifecycle and coverage.
  - Persist Runtime final candidates and source evidence without deriving final rank from raw source counts.

- `src/seektalent_ui/models.py`
  - Add an explicit runtime sourcing job start response shape so the start route does not fake per-source job responses.

- `src/seektalent_ui/maintenance.py`
  - Add the runtime sourcing job table and indexes to Workbench schema readiness metadata.

- `src/seektalent_ui/job_runner.py`
  - Add one runtime job worker.
  - Stop using separate CTS/Liepin source workers for primary Workbench agent runs.
  - Keep source-run workers only for non-primary future lanes such as approved detail enrichment when needed.

- `src/seektalent_ui/runtime_bridge.py`
  - Replace `run_cts_source_run(...)` / primary `run_liepin_card_source_run(...)` call path with `run_runtime_sourcing_job(...)`.
  - Call `WorkflowRuntime.run(...)` once with selected source kinds.
  - Attach the returned Runtime run id to all selected source projections.

- `src/seektalent/runtime/source_round_dispatch.py`
  - New focused module for source-round fan-out contracts and safe per-source dispatch.
  - It converts one logical query bundle into per-source results without mutating `RunState`.
  - It carries CTS retrieval metadata and Liepin source-lane deltas back to the Runtime merge point.

- `src/seektalent/runtime/logical_query_dispatch.py`
  - New immutable Runtime logical query dispatch contract.
  - Freezes `query_instance_id`, `query_fingerprint`, `requested_count`, and query text before source adapters run.

- `src/seektalent/runtime/orchestrator.py`
  - Pass `source_plan` and `liepin_context` into `_run_rounds(...)`.
  - Use source-round dispatch for multi-source runs inside the mature round loop.
  - Keep `_run_full_source_lanes(...)` for lane-level APIs and approved detail flows, not the Workbench primary run.

- `src/seektalent/runtime/retrieval_runtime.py`
  - Expose a small helper that runs one CTS logical query bundle through the existing retrieval path without changing the query split semantics.

- `src/seektalent/providers/liepin/runtime_lane.py`
  - Add a helper for executing a Runtime logical query bundle as Liepin card searches.
  - Preserve card-only behavior and detail recommendation budget.
  - Consume Runtime logical query identity instead of recomputing Liepin query ids or fingerprints.

- `src/seektalent_ui/workbench_routes.py`
  - Ensure `final-top10` uses Runtime finalization-backed ranking fields.
  - Keep source state and coverage public-safe.

- `apps/web-svelte/src/lib/workbench/runStory.ts`
  - Build graph around Runtime source plan, source branches, merge/dedupe, scoring, and final Top 10.
  - Use final-top10 count for final node, not raw candidate review count.

Add or modify tests:

- `tests/test_workbench_runtime_owned_execution.py`
- `tests/test_runtime_multi_source_round_dispatch.py`
- `tests/test_runtime_source_lanes.py`
- `tests/test_workbench_semantic_guardrails.py`
- `tests/test_workbench_api.py`
- `apps/web-svelte/src/lib/workbench/runStory.test.ts`
- `apps/web-svelte/src/lib/workbench/finalCandidateCards.test.ts`

---

## Task 1: Lock Current Bug With Workbench Runtime Ownership Tests

**Files:**
- Create: `tests/test_workbench_runtime_owned_execution.py`
- Modify: none

- [ ] **Step 1: Write failing tests for one Runtime job per dual-source session**

Create `tests/test_workbench_runtime_owned_execution.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from seektalent.config import AppSettings
from seektalent.runtime import RunArtifacts
from seektalent_ui.job_runner import WorkbenchJobRunner
from seektalent_ui.runtime_bridge import run_runtime_sourcing_job
from seektalent_ui.workbench_store import DEFAULT_TENANT_ID, WorkbenchStore, WorkbenchUser


@dataclass
class FakeRuntime:
    calls: list[dict[str, Any]]

    def run(self, **kwargs: Any) -> RunArtifacts:
        self.calls.append(kwargs)
        return RunArtifacts(
            run_id="run_dual_source_1",
            run_dir=Path("/tmp/seektalent-test-run"),
            final_markdown="final",
            final_result=None,
            candidate_store={},
            normalized_store={},
            run_state=None,
        )

    def run_source_lane(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("Workbench primary run must not call run_source_lane")


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        artifacts_path=tmp_path / "artifacts",
        cache_path=tmp_path / "cache",
        corpus_path=tmp_path / "corpus",
        flywheel_path=tmp_path / "flywheel.sqlite3",
        workbench_db_path=tmp_path / "workbench.sqlite3",
        mock_cts=True,
        text_llm_provider="deepseek",
        provider_api_key="test-key",
    )


def _user() -> WorkbenchUser:
    return WorkbenchUser(
        user_id="qa-user",
        email="qa@example.com",
        display_name="QA",
        workspace_id="workspace-1",
    )


def test_runtime_bridge_calls_runtime_once_for_dual_source_session(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user = _user()
    session = store.create_workbench_session(
        user=user,
        job_title="数据开发专家",
        jd_text="负责数据平台建设",
        notes="必备条件：Python",
        source_kinds=["cts", "liepin"],
    )
    triage = store.update_requirement_triage(
        user=user,
        session_id=session.session_id,
        must_haves=["Python"],
        nice_to_haves=[],
        synonyms=[],
        seniority_filters=[],
        exclusions=[],
        generated_query_hints=["数据开发"],
    )
    assert triage is not None
    store.approve_requirement_triage(user=user, session_id=session.session_id)
    job = store.start_runtime_sourcing_job(
        user=user,
        session_id=session.session_id,
        idempotency_key="start-agent",
    )
    assert job is not None
    context = store.claim_next_runtime_sourcing_job(
        owner_id="test-owner",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    assert context is not None
    fake_runtime = FakeRuntime(calls=[])

    run_runtime_sourcing_job(
        context=context,
        store=store,
        settings=_settings(tmp_path),
        runtime_factory=lambda settings: fake_runtime,
        progress_callback=None,
    )

    assert len(fake_runtime.calls) == 1
    call = fake_runtime.calls[0]
    assert call["source_kinds"] == ("cts", "liepin")
    assert "Approved requirement triage:" in str(call["notes"])
    assert call["requirement_cache_scope"] == session.session_id


def test_starting_dual_source_session_does_not_enqueue_primary_source_run_jobs(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user = _user()
    session = store.create_workbench_session(
        user=user,
        job_title="数据开发专家",
        jd_text="负责数据平台建设",
        notes="必备条件：Python",
        source_kinds=["cts", "liepin"],
    )
    store.update_requirement_triage(
        user=user,
        session_id=session.session_id,
        must_haves=["Python"],
        nice_to_haves=[],
        synonyms=[],
        seniority_filters=[],
        exclusions=[],
        generated_query_hints=["数据开发"],
    )
    store.approve_requirement_triage(user=user, session_id=session.session_id)

    created = store.start_runtime_sourcing_job(
        user=user,
        session_id=session.session_id,
        idempotency_key="start-agent",
    )

    assert created is not None
    with store._connect() as conn:
        source_job_count = conn.execute(
            "SELECT COUNT(*) FROM source_run_jobs WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()[0]
        runtime_job_count = conn.execute(
            "SELECT COUNT(*) FROM runtime_sourcing_jobs WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()[0]
    assert source_job_count == 0
    assert runtime_job_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_workbench_runtime_owned_execution.py -q
```

Expected: fail because `run_runtime_sourcing_job`, `start_runtime_sourcing_job`, `claim_next_runtime_sourcing_job`, and `runtime_sourcing_jobs` do not exist yet.

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/test_workbench_runtime_owned_execution.py
git commit -m "test: lock runtime-owned workbench execution"
```

---

## Task 2: Add Session-Level Runtime Sourcing Jobs

**Files:**
- Modify: `src/seektalent_ui/workbench_store.py`
- Modify: `src/seektalent_ui/maintenance.py`
- Test: `tests/test_workbench_runtime_owned_execution.py`
- Test: `tests/test_workbench_maintenance.py`

- [ ] **Step 1: Add runtime job data structures**

In `src/seektalent_ui/workbench_store.py`, add a dataclass near existing Workbench job dataclasses:

```python
@dataclass(frozen=True)
class WorkbenchRuntimeSourcingJob:
    job_id: str
    tenant_id: str
    workspace_id: str
    user_id: str
    session_id: str
    status: Literal["queued", "running", "completed", "failed"]
    lease_owner: str | None
    lease_expires_at: str | None
    idempotency_key: str | None
    attempt_count: int
    runtime_run_id: str | None
    error_message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WorkbenchRuntimeSourcingJobContext:
    job: WorkbenchRuntimeSourcingJob
    session: WorkbenchSession
    triage: WorkbenchRequirementTriage
    source_runs: tuple[WorkbenchSourceRun, ...]
```

Add a row mapper near `_job_from_row(...)`:

```python
def _runtime_sourcing_job_from_row(row: sqlite3.Row) -> WorkbenchRuntimeSourcingJob:
    return WorkbenchRuntimeSourcingJob(
        job_id=row["job_id"],
        tenant_id=row["tenant_id"],
        workspace_id=row["workspace_id"],
        user_id=row["user_id"],
        session_id=row["session_id"],
        status=row["status"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        idempotency_key=row["idempotency_key"],
        attempt_count=int(row["attempt_count"] or 0),
        runtime_run_id=row["runtime_run_id"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
```

- [ ] **Step 2: Add the table**

Inside `_initialize(...)`, add:

```python
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS runtime_sourcing_jobs (
        job_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        workspace_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        status TEXT NOT NULL,
        lease_owner TEXT,
        lease_expires_at TEXT,
        idempotency_key TEXT,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        runtime_run_id TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (session_id) REFERENCES sessions(session_id)
    )
    """
)
conn.execute(
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_sourcing_jobs_idempotency
    ON runtime_sourcing_jobs(tenant_id, workspace_id, user_id, session_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL
    """
)
conn.execute(
    """
    CREATE INDEX IF NOT EXISTS idx_runtime_sourcing_jobs_claim
    ON runtime_sourcing_jobs(status, lease_expires_at, created_at)
    """
)
```

- [ ] **Step 3: Add job start method**

Add this method to `WorkbenchStore`:

```python
def start_runtime_sourcing_job(
    self,
    *,
    user: WorkbenchUser,
    session_id: str,
    idempotency_key: str | None,
) -> WorkbenchRuntimeSourcingJob | None:
    self._initialize()
    now = _now_iso()
    with self._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        session_row = conn.execute(
            """
            SELECT *
            FROM sessions
            WHERE session_id = ? AND workspace_id = ? AND user_id = ?
            """,
            (session_id, user.workspace_id, user.user_id),
        ).fetchone()
        if session_row is None:
            return None
        triage = _triage_by_session(conn, [session_id])[session_id]
        if triage.status != "approved":
            raise PermissionError("requirement_triage_not_approved")
        existing = conn.execute(
            """
            SELECT *
            FROM runtime_sourcing_jobs
            WHERE session_id = ?
              AND workspace_id = ?
              AND user_id = ?
              AND (status IN ('queued', 'running') OR (? IS NOT NULL AND idempotency_key = ?))
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (session_id, user.workspace_id, user.user_id, idempotency_key, idempotency_key),
        ).fetchone()
        if existing is not None:
            return _runtime_sourcing_job_from_row(existing)
        job_id = f"runtime_job_{uuid.uuid4().hex[:16]}"
        conn.execute(
            """
            INSERT INTO runtime_sourcing_jobs (
                job_id, tenant_id, workspace_id, user_id, session_id, status,
                lease_owner, lease_expires_at, idempotency_key, attempt_count,
                runtime_run_id, error_message, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'queued', NULL, NULL, ?, 0, NULL, NULL, ?, ?)
            """,
            (
                job_id,
                DEFAULT_TENANT_ID,
                user.workspace_id,
                user.user_id,
                session_id,
                _bounded_text(idempotency_key, 128),
                now,
                now,
            ),
        )
	        conn.execute(
	            """
	            UPDATE source_runs
	            SET status = 'queued',
	                warning_code = NULL,
	                warning_message = NULL
	            WHERE session_id = ?
	              AND workspace_id = ?
	              AND user_id = ?
	              AND status NOT IN ('blocked', 'completed', 'failed')
	            """,
	            (session_id, user.workspace_id, user.user_id),
	        )
        _append_workbench_event_conn(
            conn,
            tenant_id=DEFAULT_TENANT_ID,
            workspace_id=user.workspace_id,
            user_id=user.user_id,
            session_id=session_id,
            source_run_id=None,
            source_kind=None,
            event_name="runtime_sourcing_job_queued",
            payload={"jobId": job_id, "sessionId": session_id},
        )
        job = _runtime_sourcing_job_from_row(
            conn.execute("SELECT * FROM runtime_sourcing_jobs WHERE job_id = ?", (job_id,)).fetchone()
        )
    return job
```

- [ ] **Step 4: Add claim method**

Add this method:

```python
def claim_next_runtime_sourcing_job(
    self,
    *,
    owner_id: str,
    lease_expires_at: str,
) -> WorkbenchRuntimeSourcingJobContext | None:
    self._initialize()
    self.reconcile_expired_runtime_sourcing_jobs()
    now = _now_iso()
    with self._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT *
            FROM runtime_sourcing_jobs
            WHERE status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        job = _runtime_sourcing_job_from_row(row)
        conn.execute(
            """
            UPDATE runtime_sourcing_jobs
            SET status = 'running',
                lease_owner = ?,
                lease_expires_at = ?,
                attempt_count = attempt_count + 1,
                updated_at = ?
            WHERE job_id = ?
            """,
            (owner_id, lease_expires_at, now, job.job_id),
        )
        conn.execute(
            """
            UPDATE source_runs
            SET status = 'running'
            WHERE session_id = ?
              AND workspace_id = ?
              AND user_id = ?
              AND status = 'queued'
            """,
            (job.session_id, job.workspace_id, job.user_id),
        )
        session = _session_from_row(conn.execute("SELECT * FROM sessions WHERE session_id = ?", (job.session_id,)).fetchone())
        triage = _triage_by_session(conn, [job.session_id])[job.session_id]
	        source_runs = tuple(
            _source_run_from_row(source_row)
            for source_row in conn.execute(
                """
                SELECT *
                FROM source_runs
                WHERE session_id = ?
                ORDER BY source_kind ASC
                """,
                (job.session_id,),
            ).fetchall()
        )
        claimed = _runtime_sourcing_job_from_row(
            conn.execute("SELECT * FROM runtime_sourcing_jobs WHERE job_id = ?", (job.job_id,)).fetchone()
        )
	    return WorkbenchRuntimeSourcingJobContext(job=claimed, session=session, triage=triage, source_runs=source_runs)
```

Do not reset preflight-blocked source projections when starting the runtime job. A blocked Liepin projection must stay blocked while CTS runs; the Runtime adapter will also receive the blocked posture and return source-scoped blocked coverage. Add a route/store regression that starts a CTS+Liepin session with Liepin preflight blocked and asserts:

- one `runtime_sourcing_jobs` row exists;
- no `source_run_jobs` row exists;
- CTS source run is queued/running;
- Liepin source run remains `blocked` with its mapped business-safe warning code.

- [ ] **Step 5: Add lease heartbeat, expiry, and completion helpers**

Add:

```python
def extend_runtime_sourcing_job_lease(
    self,
    *,
    job_id: str,
    owner_id: str,
    lease_expires_at: str,
) -> bool:
    self._initialize()
    with self._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE runtime_sourcing_jobs
            SET lease_expires_at = ?,
                updated_at = ?
            WHERE job_id = ?
              AND lease_owner = ?
              AND status = 'running'
            """,
            (lease_expires_at, _now_iso(), job_id, owner_id),
        )
    return cursor.rowcount == 1


def reconcile_expired_runtime_sourcing_jobs(self) -> None:
    self._initialize()
    now = _now_iso()
    with self._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT *
            FROM runtime_sourcing_jobs
            WHERE status = 'running'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < ?
            ORDER BY lease_expires_at ASC, job_id ASC
            """,
            (now,),
        ).fetchall()
        for row in rows:
            if int(row["attempt_count"] or 0) >= 3 or row["runtime_run_id"]:
                conn.execute(
                    """
                    UPDATE runtime_sourcing_jobs
                    SET status = 'failed',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        error_message = ?,
                        updated_at = ?
                    WHERE job_id = ? AND status = 'running'
                    """,
                    ("Runtime sourcing lease expired with uncertain in-flight state.", now, row["job_id"]),
	                )
	                conn.execute(
	                    """
	                    UPDATE source_runs
	                    SET status = 'failed',
	                        warning_code = 'failed_internal_error',
	                        warning_message = 'Runtime sourcing lease expired before completion.'
	                    WHERE session_id = ?
	                      AND workspace_id = ?
	                      AND user_id = ?
	                      AND status = 'running'
	                    """,
	                    (row["session_id"], row["workspace_id"], row["user_id"]),
	                )
	                continue
            conn.execute(
                """
                UPDATE runtime_sourcing_jobs
                SET status = 'queued',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = ?
                WHERE job_id = ? AND status = 'running'
                """,
                (now, row["job_id"]),
            )


def mark_runtime_sourcing_job_failed(
    self,
    *,
    job: WorkbenchRuntimeSourcingJob,
    error_message: str,
) -> None:
    self._initialize()
    now = _now_iso()
    safe_message = _bounded_text(error_message, 500)
    with self._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE runtime_sourcing_jobs
            SET status = 'failed',
                error_message = ?,
                lease_owner = NULL,
                lease_expires_at = NULL,
                updated_at = ?
            WHERE job_id = ?
            """,
            (safe_message, now, job.job_id),
        )
        conn.execute(
            """
            UPDATE source_runs
            SET status = 'failed',
                warning_code = 'failed_provider_error',
                warning_message = 'Runtime sourcing failed.'
            WHERE session_id = ?
              AND workspace_id = ?
              AND user_id = ?
              AND status = 'running'
            """,
            (job.session_id, job.workspace_id, job.user_id),
        )
```

Add a heartbeat regression to `tests/test_workbench_runtime_owned_execution.py`:

```python
def test_runtime_sourcing_job_lease_heartbeat_extends_only_current_owner(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user = _user()
    session = _approved_dual_source_session(store, user)
    job = store.start_runtime_sourcing_job(
        user=user,
        session_id=session.session_id,
        idempotency_key="heartbeat",
    )
    assert job is not None
    context = store.claim_next_runtime_sourcing_job(
        owner_id="owner-a",
        lease_expires_at="2026-05-21T00:10:00+00:00",
    )
    assert context is not None

    assert store.extend_runtime_sourcing_job_lease(
        job_id=context.job.job_id,
        owner_id="owner-a",
        lease_expires_at="2026-05-21T00:20:00+00:00",
    )
    assert not store.extend_runtime_sourcing_job_lease(
        job_id=context.job.job_id,
        owner_id="owner-b",
        lease_expires_at="2026-05-21T00:30:00+00:00",
    )
```

Define `_approved_dual_source_session(...)` in the test file if it is not already present. It should create a dual-source Workbench session, populate requirement triage, and approve it.

- [ ] **Step 6: Run focused tests**

Update Workbench maintenance metadata before running tests:

- Add `runtime_sourcing_jobs` to `WORKBENCH_REQUIRED_TABLES`.
- Add required columns for `runtime_sourcing_jobs`: `job_id`, `tenant_id`, `workspace_id`, `user_id`, `session_id`, `status`, `lease_owner`, `lease_expires_at`, `idempotency_key`, `attempt_count`, `runtime_run_id`, `error_message`, `created_at`, `updated_at`.
- Add `idx_runtime_sourcing_jobs_idempotency` and `idx_runtime_sourcing_jobs_claim` to `WORKBENCH_REQUIRED_INDEXES`.
- Extend `tests/test_workbench_maintenance.py` schema/readiness assertions so the new table and indexes are part of the canonical schema.

```bash
uv run pytest tests/test_workbench_runtime_owned_execution.py tests/test_workbench_maintenance.py -q
```

Expected: tests still fail because the runtime bridge function is not implemented yet, but table and job methods no longer fail by name.

- [ ] **Step 7: Commit**

```bash
git add src/seektalent_ui/workbench_store.py src/seektalent_ui/maintenance.py tests/test_workbench_runtime_owned_execution.py tests/test_workbench_maintenance.py
git commit -m "feat: add workbench runtime sourcing jobs"
```

---

## Task 3: Add Runtime Bridge For One Primary Sourcing Run

**Files:**
- Modify: `src/seektalent_ui/runtime_bridge.py`
- Modify: `src/seektalent_ui/workbench_store.py`
- Test: `tests/test_workbench_runtime_owned_execution.py`

- [ ] **Step 1: Add `run_runtime_sourcing_job(...)`**

In `src/seektalent_ui/runtime_bridge.py`, add imports:

```python
from seektalent_ui.workbench_store import WorkbenchRuntimeSourcingJobContext
```

Add this function:

```python
def run_runtime_sourcing_job(
    *,
    context: WorkbenchRuntimeSourcingJobContext,
    store: WorkbenchStore,
    settings: AppSettings,
    runtime_factory: RuntimeFactory,
    progress_callback: ProgressCallback | None = None,
) -> None:
    runtime = runtime_factory(settings)
    run_method = getattr(runtime, "run", None)
    if not callable(run_method):
        raise RuntimeError("Runtime does not support Workbench sourcing runs.")
    source_kinds = tuple(source_run.source_kind for source_run in context.source_runs)
    liepin_context = _liepin_context_for_runtime_job(context=context, store=store)
    run_kwargs: dict[str, object] = {
        "job_title": context.session.job_title,
        "jd": context.session.jd_text,
        "notes": _notes_with_runtime_triage(context),
        "source_kinds": source_kinds,
        "liepin_context": liepin_context,
        "progress_callback": progress_callback,
    }
    if _runtime_run_accepts_start_callback(run_method):
        run_kwargs["runtime_start_callback"] = lambda run_id: store.attach_runtime_sourcing_job_runtime_run_id(
            job=context.job,
            runtime_run_id=run_id,
        )
    if _callable_accepts_keyword(run_method, "requirement_cache_scope"):
        _seed_approved_requirement_cache_for_runtime_job(context=context, settings=settings, notes=str(run_kwargs["notes"]))
        run_kwargs["requirement_cache_scope"] = context.session.session_id
    artifacts = run_method(**run_kwargs)
    store.complete_runtime_sourcing_job_with_artifacts(context=context, artifacts=artifacts)
```

Add helpers:

```python
def _notes_with_runtime_triage(context: WorkbenchRuntimeSourcingJobContext) -> str:
    triage = context.triage
    sections = [
        context.session.notes.strip(),
        "Approved requirement triage:",
        f"must_haves: {_bounded_join(triage.must_haves)}",
        f"nice_to_haves: {_bounded_join(triage.nice_to_haves)}",
        f"synonyms: {_bounded_join(triage.synonyms)}",
        f"seniority_filters: {_bounded_join(triage.seniority_filters)}",
        f"exclusions: {_bounded_join(triage.exclusions)}",
        f"generated_query_hints: {_bounded_join(triage.generated_query_hints)}",
    ]
    return "\n".join(section for section in sections if section)


def _seed_approved_requirement_cache_for_runtime_job(
    *,
    context: WorkbenchRuntimeSourcingJobContext,
    settings: AppSettings,
    notes: str,
) -> None:
    input_truth = build_input_truth(job_title=context.session.job_title, jd=context.session.jd_text, notes=notes)
    prompt = PromptRegistry(settings.prompt_dir).load("requirements")
    key = requirement_cache_key(
        settings,
        prompt=prompt,
        input_truth=input_truth,
        cache_scope=context.session.session_id,
    )
    draft = RequirementExtractionDraft(
        role_title=context.session.job_title,
        title_anchor_terms=_title_anchor_terms(context.session.job_title),
        title_anchor_rationale="Workbench requirement triage was approved by the user.",
        jd_query_terms=list(context.triage.generated_query_hints),
        notes_query_terms=[],
        role_summary=context.session.job_title,
        must_have_capabilities=list(context.triage.must_haves),
        preferred_capabilities=list(context.triage.nice_to_haves),
        exclusion_signals=list(context.triage.exclusions),
    )
    put_cached_json(settings, namespace="requirements", key=key, payload=draft.model_dump(mode="json"))
```

Add Liepin context helper:

```python
def _liepin_context_for_runtime_job(
    *,
    context: WorkbenchRuntimeSourcingJobContext,
    store: WorkbenchStore,
) -> dict[str, str | int | bool | None] | None:
    liepin_source_run = next((source_run for source_run in context.source_runs if source_run.source_kind == "liepin"), None)
    if liepin_source_run is None:
        return None
    if liepin_source_run.status == "blocked":
        return {
            "status": "blocked",
            "safe_reason_code": _public_source_reason_code(liepin_source_run.warning_code),
        }
    connection = store.get_liepin_source_connection_for_runtime_job(
        workspace_id=context.session.workspace_id,
        user_id=context.session.owner_user_id,
    )
    if connection is None or connection.provider_account_hash is None:
        return {"status": "blocked", "safe_reason_code": "source_login_required"}
    return {
        "status": "ready",
        "tenant_id": DEFAULT_TENANT_ID,
        "workspace_id": context.session.workspace_id,
        "actor_id": context.session.owner_user_id,
        "connection_id": connection.connection_id,
        "compliance_gate_ref": connection.compliance_gate_ref,
        "provider_account_hash": connection.provider_account_hash,
    }
```

Add `_public_source_reason_code(...)` in `runtime_bridge.py` or reuse the public reason mapper from `source_lanes.py` if it is already import-safe. It must map internal preflight/provider codes such as `liepin_opencli_timeout` to business-safe codes before they enter Workbench public events or Runtime source-plan public payloads.

- [ ] **Step 2: Add store method to attach runtime run id**

In `WorkbenchStore`, add the Liepin connection lookup used by the runtime bridge:

```python
def get_liepin_source_connection_for_runtime_job(
    self,
    *,
    workspace_id: str,
    user_id: str,
) -> WorkbenchSourceConnection | None:
    self._initialize()
    with self._connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM source_connections
            WHERE tenant_id = ?
              AND workspace_id = ?
              AND user_id = ?
              AND source_kind = 'liepin'
              AND status = 'connected'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (DEFAULT_TENANT_ID, workspace_id, user_id),
        ).fetchone()
    return _source_connection_from_row(row) if row is not None else None
```

Then add:

```python
def attach_runtime_sourcing_job_runtime_run_id(
    self,
    *,
    job: WorkbenchRuntimeSourcingJob,
    runtime_run_id: str,
) -> None:
    self._initialize()
    now = _now_iso()
    with self._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE runtime_sourcing_jobs
            SET runtime_run_id = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (runtime_run_id, now, job.job_id),
        )
        conn.execute(
            """
            UPDATE source_runs
            SET runtime_run_id = ?
            WHERE session_id = ?
              AND workspace_id = ?
              AND user_id = ?
            """,
            (runtime_run_id, job.session_id, job.workspace_id, job.user_id),
        )
```

- [ ] **Step 3: Add store completion method**

Add a first completion method that reuses existing CTS persistence for final candidates and updates source projections from coverage:

```python
def complete_runtime_sourcing_job_with_artifacts(
    self,
    *,
    context: WorkbenchRuntimeSourcingJobContext,
    artifacts: object,
) -> list[WorkbenchCandidateReviewItem]:
    self._initialize()
    now = _now_iso()
    with self._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        persisted = self._persist_runtime_final_candidate_results_conn(
            conn,
            context=context,
            artifacts=artifacts,
            now=now,
        )
        self._update_source_runs_from_runtime_coverage_conn(
            conn,
            context=context,
            artifacts=artifacts,
            now=now,
        )
        conn.execute(
            """
            UPDATE runtime_sourcing_jobs
            SET status = 'completed',
                lease_owner = NULL,
                lease_expires_at = NULL,
                updated_at = ?
            WHERE job_id = ?
            """,
            (now, context.job.job_id),
        )
        _append_workbench_event_conn(
            conn,
            tenant_id=DEFAULT_TENANT_ID,
            workspace_id=context.session.workspace_id,
            user_id=context.session.owner_user_id,
            session_id=context.session.session_id,
            source_run_id=None,
            source_kind=None,
            event_name="runtime_sourcing_job_completed",
            payload={"jobId": context.job.job_id, "sessionId": context.session.session_id},
        )
    return persisted
```

Add `_persist_runtime_final_candidate_results_conn(...)` with this shape. The helper must persist candidates from Runtime identity finalization, not raw source result counts and not an unconstrained LLM finalizer list. Use `artifacts.run_state.top_pool_ids[:10]` as the primary ordered source. If that is unavailable, map `artifacts.finalization_revision.candidate_identity_ids` through `run_state.canonical_resume_by_identity_id`. Use `artifacts.final_result.candidates` only as optional display/rationale enrichment for the selected resume ids.

```python
def _persist_runtime_final_candidate_results_conn(
    self,
    conn: sqlite3.Connection,
    *,
    context: WorkbenchRuntimeSourcingJobContext,
    artifacts: object,
    now: str,
) -> list[WorkbenchCandidateReviewItem]:
    final_resume_ids = _runtime_final_resume_ids_from_artifacts(artifacts)
    if not final_resume_ids:
        return []
    candidate_store = getattr(artifacts, "candidate_store", {}) or {}
    normalized_store = getattr(artifacts, "normalized_store", {}) or {}
    runtime_identity_by_resume_id = _runtime_identity_by_resume_id_from_artifacts(artifacts)
    final_candidate_by_resume_id = _finalizer_candidate_by_resume_id(artifacts)
    source_run_by_kind = {source_run.source_kind: source_run.source_run_id for source_run in context.source_runs}
    review_item_ids: list[str] = []
    for provider_resume_id in final_resume_ids[:10]:
        candidate = final_candidate_by_resume_id.get(provider_resume_id)
        raw_candidate = _mapping_get(candidate_store, provider_resume_id)
        if not provider_resume_id:
            continue
        normalized = _mapping_get(normalized_store, provider_resume_id)
        source_kind = _safe_candidate_text(_attr(raw_candidate, "source"), 32) or "cts"
        if source_kind not in source_run_by_kind:
            source_kind = "cts" if "cts" in source_run_by_kind else next(iter(source_run_by_kind))
        source_run_id = source_run_by_kind[source_kind]
        review_item_id = _stable_id("review", context.session.session_id, provider_resume_id)
        evidence_id = _stable_id("evidence", source_run_id, provider_resume_id, "final")
        display_name = _safe_candidate_text(_attr(normalized, "candidate_name"), 160) or f"Candidate {review_item_id[-8:]}"
        title = _safe_candidate_text(_attr(normalized, "current_title"), 240) or _safe_candidate_text(_attr(normalized, "headline"), 240) or ""
        company = _safe_candidate_text(_attr(normalized, "current_company"), 240) or ""
        location = _safe_candidate_text(_first(_attr(normalized, "locations")), 160) or ""
        score = _int_or_none(_attr(candidate, "final_score")) if candidate is not None else None
        fit_bucket = _safe_candidate_text(_attr(candidate, "fit_bucket"), 64) if candidate is not None else None
        summary = (
            _safe_candidate_text(_attr(candidate, "match_summary"), 1000)
            or _safe_candidate_text(_attr(candidate, "why_selected"), 1000)
            or _safe_candidate_text(_attr(raw_candidate, "headline"), 1000)
            or ""
        )
        conn.execute(
            """
            INSERT INTO candidate_review_items (
                review_item_id, tenant_id, workspace_id, user_id, session_id,
                primary_evidence_id, display_name, title, company, location, summary,
                aggregate_score, fit_bucket, review_status, note, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', '', ?, ?)
            ON CONFLICT(review_item_id) DO UPDATE SET
                primary_evidence_id = excluded.primary_evidence_id,
                display_name = excluded.display_name,
                title = excluded.title,
                company = excluded.company,
                location = excluded.location,
                summary = excluded.summary,
                aggregate_score = excluded.aggregate_score,
                fit_bucket = excluded.fit_bucket,
                updated_at = excluded.updated_at
            """,
            (
                review_item_id,
                DEFAULT_TENANT_ID,
                context.session.workspace_id,
                context.session.owner_user_id,
                context.session.session_id,
                evidence_id,
                display_name,
                title,
                company,
                location,
                summary,
                score,
                fit_bucket,
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO candidate_evidence (
                evidence_id, review_item_id, tenant_id, workspace_id, user_id, session_id,
                source_run_id, source_kind, evidence_level, provider_candidate_key_hash,
                runtime_identity_id, resume_id, score, fit_bucket, matched_must_haves_json,
                matched_preferences_json, missing_risks_json, strengths_json, weaknesses_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'final', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(evidence_id) DO UPDATE SET
                review_item_id = excluded.review_item_id,
                runtime_identity_id = excluded.runtime_identity_id,
                score = excluded.score,
                fit_bucket = excluded.fit_bucket
            """,
            (
                evidence_id,
                review_item_id,
                DEFAULT_TENANT_ID,
                context.session.workspace_id,
                context.session.owner_user_id,
                context.session.session_id,
                source_run_id,
                source_kind,
                _sha256_text(provider_resume_id),
                runtime_identity_by_resume_id.get(provider_resume_id),
                provider_resume_id,
                score,
                fit_bucket,
                _json_list(_safe_list(_attr(candidate, "matched_must_haves"), 20, 240)) if candidate is not None else "[]",
                _json_list(_safe_list(_attr(candidate, "matched_preferences"), 20, 240)) if candidate is not None else "[]",
                _json_list(_safe_list(_attr(candidate, "risk_flags"), 12, 300)) if candidate is not None else "[]",
                _json_list(_safe_list(_attr(candidate, "strengths"), 12, 300)) if candidate is not None else "[]",
                _json_list(_safe_list(_attr(candidate, "weaknesses"), 12, 300)) if candidate is not None else "[]",
                now,
            ),
        )
        review_item_ids.append(review_item_id)
    if not review_item_ids:
        return []
    placeholders = ",".join("?" for _ in review_item_ids)
	    rows = conn.execute(
	        f"""
	        SELECT *
	        FROM candidate_review_items
	        WHERE workspace_id = ?
	          AND user_id = ?
	          AND session_id = ?
	          AND review_item_id IN ({placeholders})
	        """,
	        (context.session.workspace_id, context.session.owner_user_id, context.session.session_id, *review_item_ids),
	    ).fetchall()
	    rows_by_id = {row["review_item_id"]: row for row in rows}
	    ordered_rows = [rows_by_id[review_item_id] for review_item_id in review_item_ids if review_item_id in rows_by_id]
	    evidence_by_review = _evidence_by_review_item(conn, [row["review_item_id"] for row in ordered_rows])
	    return [_review_item_from_row(row, evidence_by_review.get(row["review_item_id"], [])) for row in ordered_rows]
```

Add these helper contracts near the persistence helper:

```python
def _runtime_final_resume_ids_from_artifacts(artifacts: object) -> list[str]:
    run_state = getattr(artifacts, "run_state", None)
    if run_state is not None:
        top_pool_ids = [_safe_candidate_text(item, 128) for item in getattr(run_state, "top_pool_ids", [])]
        top_pool_ids = [item for item in top_pool_ids if item]
        if top_pool_ids:
            return top_pool_ids[:10]
    revision = getattr(artifacts, "finalization_revision", None)
    identity_ids = list(getattr(revision, "candidate_identity_ids", []) or [])
    if run_state is None or not identity_ids:
        return []
    resume_ids: list[str] = []
    canonical_by_identity = getattr(run_state, "canonical_resume_by_identity_id", {}) or {}
    for identity_id in identity_ids:
        canonical = canonical_by_identity.get(identity_id)
        resume_id = _safe_candidate_text(_attr(canonical, "canonical_resume_id"), 128)
        if resume_id and resume_id not in resume_ids:
            resume_ids.append(resume_id)
    return resume_ids[:10]


def _finalizer_candidate_by_resume_id(artifacts: object) -> dict[str, object]:
    final_result = getattr(artifacts, "final_result", None)
    result: dict[str, object] = {}
    for candidate in list(getattr(final_result, "candidates", []) or []):
        resume_id = _safe_candidate_text(_attr(candidate, "resume_id"), 128)
        if resume_id:
            result[resume_id] = candidate
    return result
```

- [ ] **Step 4: Run focused tests**

```bash
uv run pytest tests/test_workbench_runtime_owned_execution.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent_ui/runtime_bridge.py src/seektalent_ui/workbench_store.py tests/test_workbench_runtime_owned_execution.py
git commit -m "feat: route workbench primary run through runtime"
```

---

## Task 4: Switch Workbench Job Runner To Runtime Jobs

**Files:**
- Modify: `src/seektalent_ui/models.py`
- Modify: `src/seektalent_ui/job_runner.py`
- Modify: `src/seektalent_ui/workbench_routes.py`
- Test: `tests/test_workbench_runtime_owned_execution.py`
- Test: `tests/test_workbench_api.py`

- [ ] **Step 1: Add route response model for one runtime job**

In `src/seektalent_ui/models.py`, add a response shape for session-level runtime sourcing jobs and extend the existing session start response:

```python
class WorkbenchRuntimeSourcingJobStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobId: str
    status: WorkbenchJobStatus
    sourceRunIds: list[str]


class WorkbenchSessionStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str
    sourceRuns: list[WorkbenchSourceRunStartResponse] = Field(default_factory=list)
    runtimeJob: WorkbenchRuntimeSourcingJobStartResponse | None = None
    blockedSources: list[WorkbenchSessionStartBlockedSourceResponse] = Field(default_factory=list)
```

Keep `WorkbenchSourceRunStartResponse` unchanged for future lane/detail-specific APIs. The primary Start Agent route must return `runtimeJob` and an empty `sourceRuns` list when it creates no per-source jobs.

- [ ] **Step 2: Add route/store start wiring test**

Add to `tests/test_workbench_api.py`:

```python
def test_start_agent_enqueues_one_runtime_sourcing_job_for_dual_source(client, workbench_store):
    session = _create_session(client, source_kinds=["cts", "liepin"])
    _approve_requirement_triage(client, session["sessionId"])

    response = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/start",
        headers=_csrf_headers(client),
        json={"idempotencyKey": "start-dual-source"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["sourceRuns"] == []
    assert payload["runtimeJob"]["sourceRunIds"]
    with workbench_store._connect() as conn:
        runtime_jobs = conn.execute(
            "SELECT COUNT(*) FROM runtime_sourcing_jobs WHERE session_id = ?",
            (session["sessionId"],),
        ).fetchone()[0]
        source_jobs = conn.execute(
            "SELECT COUNT(*) FROM source_run_jobs WHERE session_id = ?",
            (session["sessionId"],),
        ).fetchone()[0]
    assert runtime_jobs == 1
    assert source_jobs == 0
```

Add a paired preflight regression in the same file:

```python
def test_start_agent_keeps_liepin_blocked_projection_public_safe(client, workbench_store, monkeypatch):
    session = _create_session(client, source_kinds=["cts", "liepin"])
    _approve_requirement_triage(client, session["sessionId"])
    _force_liepin_preflight_block(monkeypatch, reason_code="liepin_opencli_timeout")

    response = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/start",
        headers=_csrf_headers(client),
        json={"idempotencyKey": "start-with-blocked-liepin"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["sourceRuns"] == []
    assert payload["runtimeJob"]["sourceRunIds"]
    assert payload["blockedSources"][0]["sourceKind"] == "liepin"
    assert payload["blockedSources"][0]["reason"] == "source_browser_timeout"
    session_payload = client.get(f"/api/workbench/sessions/{session['sessionId']}").json()
    source_cards = {card["sourceKind"]: card for card in session_payload["sourceCards"]}
    assert source_cards["cts"]["status"] in {"queued", "running"}
    assert source_cards["liepin"]["status"] == "blocked"
    assert source_cards["liepin"]["warningCode"] == "source_browser_timeout"
```

Define `_force_liepin_preflight_block(...)` locally against the route's existing probe hook or monkeypatch the lightweight preflight helper introduced in this task.

- [ ] **Step 3: Replace primary start route behavior**

In `src/seektalent_ui/workbench_routes.py`, find the session source start route that currently iterates source runs and calls `start_source_run_job(...)`. Replace the primary path with:

```python
job = store.start_runtime_sourcing_job(
    user=user,
    session_id=session_id,
    idempotency_key=request.idempotencyKey,
)
if job is None:
    raise HTTPException(status_code=404, detail="Not found.")
job_runner = getattr(request.app.state, "workbench_job_runner", None)
if job_runner is not None:
    job_runner.wake()
return WorkbenchSessionStartResponse(
    sessionId=session_id,
    sourceRuns=[],
    runtimeJob=WorkbenchRuntimeSourcingJobStartResponse(
        jobId=job.job_id,
        status=job.status,
        sourceRunIds=[source_run.source_run_id for source_run in session.source_runs],
    ),
    blockedSources=blocked,
)
```

Build `blocked` from lightweight Liepin preflight only. Preflight failure must update the Liepin source projection to blocked and include that source in `blockedSources`, but it must not prevent the runtime job from being queued and it must not create a Liepin source-run job. Keep detail-open and future lane-specific routes separate. The main "Start Agent" route should not enqueue per-source primary jobs.

Before returning `blockedSources`, `sourceRuns`, `sourceCards`, `runtimeSourceState`, or any Workbench session/event payload, map stored/internal warning codes through the same business-safe reason mapper used by Runtime source payloads. Internal codes such as `liepin_opencli_timeout`, `liepin_pi_mcp_config_invalid`, browser command names, local paths, and raw provider labels may remain in internal audit rows, but `WorkbenchSourceRunResponse.warningCode`, source-card `warningCode`, blocked-source response `reason`, and graph input payloads must expose only public codes such as `source_browser_timeout`, `source_browser_backend_unavailable`, or `source_login_required`.

- [ ] **Step 4: Update job runner imports and threads**

In `src/seektalent_ui/job_runner.py`, replace imports:

```python
from seektalent_ui.runtime_bridge import RuntimeFactory, extract_requirement_triage, run_runtime_sourcing_job
```

Change thread state:

```python
RUNTIME_WORKER_COUNT = 1

def __init__(self, *, store: WorkbenchStore, settings: AppSettings, runtime_factory: RuntimeFactory, liepin_worker_client: LiepinWorkerClient | None = None) -> None:
    # Keep existing assignments and add this field after existing thread collections are initialized.
    self._runtime_threads: list[threading.Thread] = []
```

Update `wake()`:

```python
def wake(self) -> None:
    with self._lock:
        self._start_runtime_workers(worker_count=RUNTIME_WORKER_COUNT)
```

Add:

```python
def _start_runtime_workers(self, *, worker_count: int) -> None:
    live_threads = [thread for thread in self._runtime_threads if thread.is_alive()]
    self._runtime_threads = live_threads
    while len(self._runtime_threads) < worker_count:
        worker_number = len(self._runtime_threads) + 1
        thread = threading.Thread(
            target=self._run_runtime_until_idle,
            name=f"seektalent-workbench-runtime-job-runner-{worker_number}",
            daemon=True,
        )
        self._runtime_threads.append(thread)
        thread.start()


def _run_runtime_until_idle(self) -> None:
    while True:
        context = self.store.claim_next_runtime_sourcing_job(
            owner_id=self.owner_id,
            lease_expires_at=self._lease_expires_at(),
        )
        if context is None:
            return
        self._execute_runtime(context)
```

Add:

```python
def _execute_runtime(self, context: WorkbenchRuntimeSourcingJobContext) -> None:
    stop_heartbeat = threading.Event()
    runtime_lease_heartbeat = self._start_runtime_lease_heartbeat(context=context, stop_event=stop_heartbeat)
    heartbeat_thread = self._start_note_writer_heartbeat(
        user=WorkbenchUser(
            user_id=context.session.owner_user_id,
            email="",
            display_name="",
            workspace_id=context.session.workspace_id,
        ),
        session_id=context.session.session_id,
        stop_event=stop_heartbeat,
    )
    try:
        run_runtime_sourcing_job(
            context=context,
            store=self.store,
            settings=self.settings,
            runtime_factory=self.runtime_factory,
            progress_callback=lambda event: self._record_runtime_progress_for_runtime_job(context, event),
        )
    except Exception as exc:
        self.store.mark_runtime_sourcing_job_failed(
            job=context.job,
            error_message=str(exc) or "Runtime sourcing failed.",
        )
        return
    finally:
        stop_heartbeat.set()
        runtime_lease_heartbeat.join(timeout=1)
        heartbeat_thread.join(timeout=1)
```

This broad catch is only the outer job boundary that marks the runtime job failed after Runtime has already raised. Do not reuse this pattern inside source dispatch, where invariant errors must continue to propagate.

Add runtime lease heartbeat helpers:

```python
def _start_runtime_lease_heartbeat(
    self,
    *,
    context: WorkbenchRuntimeSourcingJobContext,
    stop_event: threading.Event,
) -> threading.Thread:
    thread = threading.Thread(
        target=self._runtime_lease_heartbeat_loop,
        args=(context, stop_event),
        name=f"seektalent-workbench-runtime-job-heartbeat-{context.job.job_id}",
        daemon=True,
    )
    thread.start()
    return thread


def _runtime_lease_heartbeat_loop(
    self,
    context: WorkbenchRuntimeSourcingJobContext,
    stop_event: threading.Event,
) -> None:
    while not stop_event.wait(self.heartbeat_interval_seconds):
        renewed = self.store.extend_runtime_sourcing_job_lease(
            job_id=context.job.job_id,
            owner_id=self.owner_id,
            lease_expires_at=self._lease_expires_at(),
        )
        if not renewed:
            return
```

Add a progress recorder that stores `source_kind=None` for shared Runtime events:

```python
def _record_runtime_progress_for_runtime_job(
    self,
    context: WorkbenchRuntimeSourcingJobContext,
    event: ProgressEvent,
) -> None:
    self.store.append_workbench_event(
        tenant_id=DEFAULT_TENANT_ID,
        workspace_id=context.session.workspace_id,
        user_id=context.session.owner_user_id,
        session_id=context.session.session_id,
        source_run_id=None,
        source_kind=None,
        event_name=f"runtime_{_safe_event_suffix(event.type)}",
        schema_version="runtime_progress_v1",
        idempotency_key=f"{context.job.job_id}:{event.type}:{event.round_no}:{event.timestamp}",
        occurred_at=event.timestamp,
        payload={
            "message": event.message,
            "roundNo": event.round_no,
            "stage": event.payload.get("stage") if isinstance(event.payload, dict) else None,
            **(event.payload if isinstance(event.payload, dict) else {}),
        },
    )
```

- [ ] **Step 5: Run focused tests**

```bash
uv run pytest tests/test_workbench_runtime_owned_execution.py tests/test_workbench_api.py -q
```

Expected: focused tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent_ui/models.py src/seektalent_ui/job_runner.py src/seektalent_ui/workbench_routes.py tests/test_workbench_api.py
git commit -m "feat: make workbench start runtime sourcing jobs"
```

---

## Task 5: Define Runtime Logical Query Dispatch Contract

**Files:**
- Create: `src/seektalent/runtime/logical_query_dispatch.py`
- Modify: `src/seektalent/runtime/retrieval_runtime.py`
- Modify: `src/seektalent/runtime/source_lanes.py`
- Test: `tests/test_runtime_multi_source_round_dispatch.py`

- [ ] **Step 1: Write failing dispatch metadata tests**

Create the first tests in `tests/test_runtime_multi_source_round_dispatch.py` before implementing source fan-out:

```python
from __future__ import annotations

from seektalent.runtime.logical_query_dispatch import build_logical_query_dispatches
from seektalent.runtime.retrieval_runtime import LogicalQueryState


def _query_state(lane_type: str) -> LogicalQueryState:
    return LogicalQueryState(
        query_role="exploit" if lane_type == "exploit" else "explore",
        lane_type=lane_type,
        query_terms=["数据开发", lane_type],
        keyword_query=f"数据开发 {lane_type}",
        query_instance_id=f"query-{lane_type}",
        query_fingerprint=f"fingerprint-{lane_type}",
    )


def test_logical_query_dispatch_freezes_requested_count_and_identity() -> None:
    dispatches = build_logical_query_dispatches(
        query_states=(_query_state("exploit"), _query_state("generic_explore")),
        lane_requested_counts={"exploit": 7, "generic_explore": 3},
        source_plan_version="2",
    )

    assert [(item.lane_type, item.requested_count) for item in dispatches] == [
        ("exploit", 7),
        ("generic_explore", 3),
    ]
    assert [item.query_instance_id for item in dispatches] == ["query-exploit", "query-generic_explore"]
    assert [item.query_fingerprint for item in dispatches] == [
        "fingerprint-exploit",
        "fingerprint-generic_explore",
    ]


def test_logical_query_dispatch_rejects_missing_requested_count() -> None:
    try:
        build_logical_query_dispatches(
            query_states=(_query_state("exploit"),),
            lane_requested_counts={},
            source_plan_version="2",
        )
    except ValueError as exc:
        assert str(exc) == "logical_query_dispatch_missing_requested_count"
    else:
        raise AssertionError("expected missing requested_count to fail")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_runtime_multi_source_round_dispatch.py -q
```

Expected: fail because `seektalent.runtime.logical_query_dispatch` does not exist.

- [ ] **Step 3: Implement immutable dispatch dataclass and builder**

Create `src/seektalent/runtime/logical_query_dispatch.py`:

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from seektalent.models import LaneType, QueryRole
from seektalent.runtime.retrieval_runtime import LogicalQueryState


@dataclass(frozen=True)
class LogicalQueryDispatch:
    round_no: int
    query_role: QueryRole
    lane_type: LaneType
    query_instance_id: str
    query_fingerprint: str
    query_terms: tuple[str, ...]
    keyword_query: str
    requested_count: int
    source_plan_version: str


def build_logical_query_dispatches(
    *,
    query_states: Sequence[LogicalQueryState],
    lane_requested_counts: Mapping[LaneType, int],
    source_plan_version: str,
) -> tuple[LogicalQueryDispatch, ...]:
    dispatches: list[LogicalQueryDispatch] = []
    for query in query_states:
        if query.lane_type not in lane_requested_counts:
            raise ValueError("logical_query_dispatch_missing_requested_count")
        requested_count = int(lane_requested_counts[query.lane_type])
        if requested_count < 0:
            raise ValueError("logical_query_dispatch_negative_requested_count")
        dispatches.append(
            LogicalQueryDispatch(
                round_no=int(getattr(query, "round_no", 0) or 0),
                query_role=query.query_role,
                lane_type=query.lane_type,
                query_instance_id=query.query_instance_id,
                query_fingerprint=query.query_fingerprint,
                query_terms=tuple(query.query_terms),
                keyword_query=query.keyword_query,
                requested_count=requested_count,
                source_plan_version=source_plan_version,
            )
        )
    return tuple(dispatches)
```

- [ ] **Step 4: Extend Liepin runtime lane request to carry Runtime query identity**

In `src/seektalent/runtime/source_lanes.py`, add optional fields to `RuntimeSourceLaneRequest`:

```python
logical_query_instance_id: str | None = None
logical_query_fingerprint: str | None = None
logical_keyword_query: str | None = None
logical_requested_count: int | None = None
```

Update `RuntimeSourceLaneRequest.to_public_payload()` with counts only, not raw query text or internal query identity:

```python
"logical_query_count": 1 if self.logical_query_instance_id else 0,
"logical_requested_count": self.logical_requested_count,
```

`logical_query_instance_id` and `logical_query_fingerprint` are dispatch/audit fields, not Workbench public UI fields. They may be written to internal Runtime artifacts and source evidence, but `to_public_payload()` must not expose them to session/event/final-top10 APIs or DOM.

In `src/seektalent/providers/liepin/runtime_lane.py`, use these fields when building provider context:

```python
provider_context = {
    # existing Liepin context fields...
    "query_instance_id": request.logical_query_instance_id or source_lane_run_id,
    "query_fingerprint": request.logical_query_fingerprint
    or hashlib.sha256(" ".join(query_terms).encode("utf-8")).hexdigest(),
}
keyword_query = request.logical_keyword_query or " ".join(query_terms)
page_size = request.logical_requested_count or budget.liepin_card_page_size
```

Pass `keyword_query=keyword_query` and `page_size=page_size` into `SearchRequest`. This preserves current lane behavior for legacy callers while allowing Runtime multi-source dispatch to freeze query identity.

- [ ] **Step 5: Add public source reason mapping**

In `src/seektalent/runtime/source_lanes.py`, stop exposing provider implementation reason codes from `to_public_payload()`:

```python
_PUBLIC_SOURCE_REASON_CODE_MAP = {
    "liepin_opencli_timeout": "source_browser_timeout",
    "liepin_opencli_backend_disabled": "source_browser_backend_unavailable",
    "liepin_opencli_status_unavailable": "source_browser_backend_unavailable",
    "liepin_opencli_login_required": "source_login_required",
    "liepin_opencli_risk_page": "source_risk_challenge",
}

_PUBLIC_SOURCE_REASON_CODES = {
    "blocked_approval_missing",
    "blocked_backend_unavailable",
    "failed_provider_error",
    "partial_timeout",
    "source_browser_backend_unavailable",
    "source_browser_timeout",
    "source_login_required",
    "source_risk_challenge",
}


def _sanitize_reason_code(value: str | None) -> str | None:
    if value is None:
        return None
    public_code = _PUBLIC_SOURCE_REASON_CODE_MAP.get(value, value)
    return public_code if public_code in _PUBLIC_SOURCE_REASON_CODES else "unknown_reason"
```

Keep internal provider reason codes in private audit artifacts only. Workbench session/event/final-top10 payloads and Svelte DOM must see the mapped business-safe code.

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/test_runtime_multi_source_round_dispatch.py tests/test_runtime_source_lanes.py::test_opencli_safe_reason_code_survives_runtime_public_payload -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/seektalent/runtime/logical_query_dispatch.py src/seektalent/runtime/source_lanes.py src/seektalent/providers/liepin/runtime_lane.py tests/test_runtime_multi_source_round_dispatch.py
git commit -m "feat: freeze runtime logical query dispatch contract"
```

---

## Task 6: Add Source Round Dispatch Contracts

**Files:**
- Create: `src/seektalent/runtime/source_round_dispatch.py`
- Test: `tests/test_runtime_multi_source_round_dispatch.py`

- [ ] **Step 1: Write dispatch contract tests**

Create `tests/test_runtime_multi_source_round_dispatch.py`:

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from seektalent.models import ResumeCandidate
from seektalent.runtime.logical_query_dispatch import LogicalQueryDispatch
from seektalent.runtime.source_round_dispatch import (
    RuntimeSourceInvariantError,
    SourceProviderFailed,
    SourceRoundAdapterResult,
    SourceRoundDispatchRequest,
    SourceRoundDispatchStatus,
    dispatch_source_rounds,
)


def _candidate(resume_id: str, source: str) -> ResumeCandidate:
    return ResumeCandidate(
        resume_id=resume_id,
        source_resume_id=resume_id,
        source=source,
        headline="数据开发专家",
        raw={"safe_summary_ref": f"artifact://public-summary/{resume_id}"},
    )


def _dispatch(lane_type: str, requested_count: int) -> LogicalQueryDispatch:
    return LogicalQueryDispatch(
        round_no=1,
        query_role="exploit" if lane_type == "exploit" else "explore",
        lane_type=lane_type,
        query_terms=("数据开发", lane_type),
        keyword_query=f"数据开发 {lane_type}",
        query_instance_id=f"query-{lane_type}",
        query_fingerprint=f"fingerprint-{lane_type}",
        requested_count=requested_count,
        source_plan_version="2",
    )


@pytest.mark.asyncio
async def test_dispatch_sends_same_query_bundle_to_cts_and_liepin() -> None:
    seen: dict[str, list[str]] = {}
    requested_counts: dict[str, list[int]] = {}

    async def cts_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        seen["cts"] = [query.query_fingerprint for query in request.logical_queries]
        requested_counts["cts"] = [query.requested_count for query in request.logical_queries]
        return SourceRoundAdapterResult(
            source="cts",
            status="completed",
            candidates=(_candidate("cts-1", "cts"),),
            raw_candidate_count=1,
        )

    async def liepin_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        seen["liepin"] = [query.query_fingerprint for query in request.logical_queries]
        requested_counts["liepin"] = [query.requested_count for query in request.logical_queries]
        return SourceRoundAdapterResult(
            source="liepin",
            status="completed",
            candidates=(_candidate("liepin-1", "liepin"),),
            raw_candidate_count=1,
        )

    result = await dispatch_source_rounds(
        request=SourceRoundDispatchRequest(
            runtime_run_id="run-1",
            round_no=1,
            logical_queries=(_dispatch("exploit", 7), _dispatch("generic_explore", 3)),
            selected_sources=("cts", "liepin"),
            seen_resume_ids=frozenset(),
            seen_dedup_keys=frozenset(),
        ),
        cts_adapter=cts_adapter,
        liepin_adapter=liepin_adapter,
    )

    assert seen["cts"] == ["fingerprint-exploit", "fingerprint-generic_explore"]
    assert seen["liepin"] == ["fingerprint-exploit", "fingerprint-generic_explore"]
    assert requested_counts["cts"] == [7, 3]
    assert requested_counts["liepin"] == [7, 3]
    assert [item.source for item in result.source_results] == ["cts", "liepin"]
    assert [candidate.resume_id for candidate in result.candidates] == ["cts-1", "liepin-1"]


@pytest.mark.asyncio
async def test_dispatch_starts_sources_concurrently() -> None:
    started: set[str] = set()
    release = asyncio.Event()

    async def adapter(source: str, request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        started.add(source)
        if len(started) == 2:
            release.set()
        await asyncio.wait_for(release.wait(), timeout=1)
        return SourceRoundAdapterResult(
            source=source,
            status="completed",
            candidates=(_candidate(f"{source}-1", source),),
            raw_candidate_count=1,
        )

    result = await dispatch_source_rounds(
        request=SourceRoundDispatchRequest(
            runtime_run_id="run-1",
            round_no=1,
            logical_queries=(_dispatch("exploit", 7),),
            selected_sources=("cts", "liepin"),
            seen_resume_ids=frozenset(),
            seen_dedup_keys=frozenset(),
        ),
        cts_adapter=lambda request: adapter("cts", request),
        liepin_adapter=lambda request: adapter("liepin", request),
    )

    assert started == {"cts", "liepin"}
    assert {item.source for item in result.source_results} == {"cts", "liepin"}


@pytest.mark.asyncio
async def test_dispatch_converts_liepin_provider_failure_to_source_result() -> None:
    async def cts_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        return SourceRoundAdapterResult(
            source="cts",
            status="completed",
            candidates=(_candidate("cts-1", "cts"),),
            raw_candidate_count=1,
        )

    async def liepin_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        raise SourceProviderFailed("browser closed")

    result = await dispatch_source_rounds(
        request=SourceRoundDispatchRequest(
            runtime_run_id="run-1",
            round_no=1,
            logical_queries=(_dispatch("exploit", 7),),
            selected_sources=("cts", "liepin"),
            seen_resume_ids=frozenset(),
            seen_dedup_keys=frozenset(),
        ),
        cts_adapter=cts_adapter,
        liepin_adapter=liepin_adapter,
    )

    assert [candidate.resume_id for candidate in result.candidates] == ["cts-1"]
    liepin = next(item for item in result.source_results if item.source == "liepin")
    assert liepin.status == "failed"
    assert liepin.safe_reason_code == "failed_provider_error"


@pytest.mark.asyncio
async def test_dispatch_propagates_runtime_invariant_errors() -> None:
    async def cts_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        raise RuntimeSourceInvariantError("bad logical query contract")

    async def liepin_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        return SourceRoundAdapterResult(source="liepin", status="completed")

    with pytest.raises(RuntimeSourceInvariantError):
        await dispatch_source_rounds(
            request=SourceRoundDispatchRequest(
                runtime_run_id="run-1",
                round_no=1,
                logical_queries=(_dispatch("exploit", 7),),
                selected_sources=("cts", "liepin"),
                seen_resume_ids=frozenset(),
                seen_dedup_keys=frozenset(),
            ),
            cts_adapter=cts_adapter,
            liepin_adapter=liepin_adapter,
        )


@pytest.mark.asyncio
async def test_dispatch_propagates_programmer_type_errors() -> None:
    async def cts_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        raise TypeError("adapter called with an invalid contract")

    async def liepin_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        return SourceRoundAdapterResult(source="liepin", status="completed")

    with pytest.raises(TypeError):
        await dispatch_source_rounds(
            request=SourceRoundDispatchRequest(
                runtime_run_id="run-1",
                round_no=1,
                logical_queries=(_dispatch("exploit", 7),),
                selected_sources=("cts", "liepin"),
                seen_resume_ids=frozenset(),
                seen_dedup_keys=frozenset(),
            ),
            cts_adapter=cts_adapter,
            liepin_adapter=liepin_adapter,
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_runtime_multi_source_round_dispatch.py -q
```

Expected: fail because `source_round_dispatch.py` does not exist.

- [ ] **Step 3: Implement source round dispatch module**

Create `src/seektalent/runtime/source_round_dispatch.py`:

```python
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from seektalent.models import ResumeCandidate
from seektalent.runtime.logical_query_dispatch import LogicalQueryDispatch

if TYPE_CHECKING:
    from seektalent.runtime.retrieval_runtime import RetrievalExecutionResult
    from seektalent.runtime.source_lanes import RuntimeSourceLaneResult

SourceKind = Literal["cts", "liepin"]
SourceRoundDispatchStatus = Literal["completed", "partial", "blocked", "failed"]
SourceRoundAdapter = Callable[["SourceRoundDispatchRequest"], Awaitable["SourceRoundAdapterResult"]]


class SourceProviderBlocked(Exception): ...
class SourceProviderFailed(Exception): ...
class SourceProviderPartial(Exception): ...
class RuntimeSourceInvariantError(RuntimeError): ...


@dataclass(frozen=True)
class SourceRoundDispatchRequest:
    runtime_run_id: str
    round_no: int
    logical_queries: tuple[LogicalQueryDispatch, ...]
    selected_sources: tuple[SourceKind, ...]
    seen_resume_ids: frozenset[str]
    seen_dedup_keys: frozenset[str]


@dataclass(frozen=True)
class SourceRoundAdapterResult:
    source: SourceKind
    status: SourceRoundDispatchStatus
    candidates: tuple[ResumeCandidate, ...] = ()
    raw_candidate_count: int = 0
    safe_reason_code: str | None = None
    diagnostics: tuple[str, ...] = ()
    retrieval_result: "RetrievalExecutionResult | None" = None
    lane_result: "RuntimeSourceLaneResult | None" = None


@dataclass(frozen=True)
class SourceRoundDispatchResult:
    source_results: tuple[SourceRoundAdapterResult, ...]
    candidates: tuple[ResumeCandidate, ...]
    raw_candidate_count: int


async def dispatch_source_rounds(
    *,
    request: SourceRoundDispatchRequest,
    cts_adapter: SourceRoundAdapter,
    liepin_adapter: SourceRoundAdapter,
) -> SourceRoundDispatchResult:
    adapters: dict[SourceKind, SourceRoundAdapter] = {
        "cts": cts_adapter,
        "liepin": liepin_adapter,
    }
    tasks: dict[SourceKind, asyncio.Task[SourceRoundAdapterResult]] = {}
    try:
        async with asyncio.TaskGroup() as task_group:
            for source in request.selected_sources:
                tasks[source] = task_group.create_task(_run_adapter_safely(source, adapters[source], request))
    except* RuntimeSourceInvariantError as group:
        raise group.exceptions[0]
    except* AssertionError as group:
        raise group.exceptions[0]
    except* TypeError as group:
        raise group.exceptions[0]
    except* Exception as group:
        # Provider taxonomy is handled inside _run_adapter_safely. Anything
        # still unhandled here is a Runtime/programmer error and must fail
        # the round instead of becoming degraded source coverage.
        raise group.exceptions[0]
    source_results = tuple(tasks[source].result() for source in request.selected_sources)
    candidates: list[ResumeCandidate] = []
    raw_candidate_count = 0
    for result in source_results:
        candidates.extend(result.candidates)
        raw_candidate_count += result.raw_candidate_count
    return SourceRoundDispatchResult(
        source_results=source_results,
        candidates=tuple(candidates),
        raw_candidate_count=raw_candidate_count,
    )


async def _run_adapter_safely(
    source: SourceKind,
    adapter: SourceRoundAdapter,
    request: SourceRoundDispatchRequest,
) -> SourceRoundAdapterResult:
    try:
        return await adapter(request)
    except asyncio.CancelledError:
        raise
    except RuntimeSourceInvariantError:
        raise
    except (AssertionError, TypeError):
        raise
    except SourceProviderBlocked:
        return SourceRoundAdapterResult(
            source=source,
            status="blocked",
            candidates=(),
            raw_candidate_count=0,
            safe_reason_code="blocked_backend_unavailable",
            diagnostics=(f"{source} source was blocked before completion.",),
        )
    except SourceProviderPartial:
        return SourceRoundAdapterResult(
            source=source,
            status="partial",
            candidates=(),
            raw_candidate_count=0,
            safe_reason_code="partial_timeout",
            diagnostics=(f"{source} source returned partial coverage.",),
        )
    except SourceProviderFailed:
        return SourceRoundAdapterResult(
            source=source,
            status="failed",
            candidates=(),
            raw_candidate_count=0,
            safe_reason_code="failed_provider_error",
            diagnostics=(f"{source} source failed before completion.",),
        )
```

`retrieval_result` carries CTS metadata so the mature `_run_rounds(...)` path keeps `cts_queries`, `sent_query_records`, `search_attempts`, `query_resume_hits`, and provider-returned diagnostics. `lane_result` carries Liepin source evidence and detail recommendations. Source adapters must not mutate `RunState`; Runtime merges all source adapter outputs after fan-out joins.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_runtime_multi_source_round_dispatch.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/runtime/source_round_dispatch.py tests/test_runtime_multi_source_round_dispatch.py
git commit -m "feat: add runtime source round dispatch contracts"
```

---

## Task 7: Execute Multi-Source Dispatch Inside The Mature Round Loop

**Files:**
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `src/seektalent/runtime/retrieval_runtime.py`
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Test: `tests/test_runtime_multi_source_round_dispatch.py`
- Test: `tests/test_runtime_state_flow.py`

- [ ] **Step 1: Add failing test that multi-source run keeps 70/30 logical lanes**

Add to `tests/test_runtime_multi_source_round_dispatch.py`:

```python
def test_multisource_uses_existing_70_30_query_allocation() -> None:
    from seektalent.runtime.retrieval_runtime import allocate_initial_lane_targets

    exploit = _query_state("exploit", 0)
    generic = _query_state("generic_explore", 0)

    assert allocate_initial_lane_targets(query_states=[exploit, generic], target_new=10) == {
        "exploit": 7,
        "generic_explore": 3,
    }
```

Add a test for candidate feedback rescue staying intact by asserting the existing rescue router behavior:

```python
def test_candidate_feedback_remains_before_generic_fallback() -> None:
    from seektalent.models import StopGuidance
    from seektalent.runtime.rescue_router import RescueInputs, choose_rescue_lane

    decision = choose_rescue_lane(
        RescueInputs(
            stop_guidance=StopGuidance(quality_gate_status="low_quality_exhausted", can_stop=False),
            has_untried_reserve_family=False,
            has_feedback_seed_resumes=True,
            candidate_feedback_enabled=True,
            candidate_feedback_attempted=False,
            anchor_only_broaden_attempted=False,
        )
    )

    assert decision.selected_lane == "candidate_feedback"
```

Add two merge-boundary regressions:

- A fake CTS adapter returns a `RetrievalExecutionResult` with one `cts_query`, one `sent_query_record`, one `search_attempt`, and one `query_resume_hit`; `_round_search_result_from_source_dispatch(...)` must preserve those fields instead of replacing them with empty arrays.
- A fake Liepin adapter returns a `RuntimeSourceLaneResult`; `run_state` must remain unchanged until `_merge_source_round_dispatch_result(...)` is called, and after that helper runs, both CTS candidates and Liepin evidence are present before scoring.

- [ ] **Step 2: Make `_run_rounds(...)` source-aware**

Change signature in `src/seektalent/runtime/orchestrator.py`:

```python
async def _run_rounds(
    self,
    *,
    run_state: RunState,
    tracer: RunTracer,
    source_plan: tuple[RuntimeSourceLanePlan, ...],
    liepin_context: Mapping[str, str | int | bool | None] | None,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[ScoredCandidate], str, int, TerminalControllerRound | None]:
```

Change the caller in `run_async(...)` so both CTS-only and multi-source go through `_run_rounds(...)`:

```python
top_scored, stop_reason, rounds_executed, terminal_controller_round = await self._run_rounds(
    run_state=run_state,
    tracer=tracer,
    source_plan=source_plan,
    liepin_context=liepin_context,
    progress_callback=progress_callback,
)
```

Keep `_run_full_source_lanes(...)` defined for lane API compatibility, but remove it from the Workbench primary `run_async(...)` branch.

- [ ] **Step 3: Add source-aware search branch**

Inside `_run_rounds(...)`, replace direct `self.retrieval_runtime.execute_round_search(...)` call with:

```python
if tuple(lane.source for lane in source_plan) == ("cts",):
    retrieval_result = await self.retrieval_runtime.execute_round_search(
        round_no=round_no,
        retrieval_plan=retrieval_plan,
        query_states=query_states,
        base_adapter_notes=projection_result.adapter_notes,
        target_new=target_new,
        seen_resume_ids=set(run_state.seen_resume_ids),
        seen_dedup_keys=seen_dedup_keys,
        tracer=tracer,
        score_for_query_outcome=lambda candidates: self._score_candidates_for_query_outcome(
            round_no=round_no,
            candidates=candidates,
            run_state=run_state,
            runtime_only_constraints=retrieval_plan.runtime_only_constraints,
        ),
        query_outcome_thresholds=QueryOutcomeThresholds(),
        record_provider_return_batch=lambda batch: self._record_corpus_provider_results(
            tracer=tracer,
            returned_candidates=batch,
        ),
    )
else:
    retrieval_result = await self._execute_multi_source_round_search(
        round_no=round_no,
        retrieval_plan=retrieval_plan,
        query_states=tuple(query_states),
        projection_adapter_notes=projection_result.adapter_notes,
        target_new=target_new,
        seen_resume_ids=set(run_state.seen_resume_ids),
        seen_dedup_keys=seen_dedup_keys,
        run_state=run_state,
        source_plan=source_plan,
        liepin_context=liepin_context,
        tracer=tracer,
    )
```

- [ ] **Step 4: Implement `_execute_multi_source_round_search(...)`**

Add method in `WorkflowRuntime`:

```python
async def _execute_multi_source_round_search(
    self,
    *,
    round_no: int,
    retrieval_plan: RoundRetrievalPlan,
    query_states: tuple[LogicalQueryState, ...],
    projection_adapter_notes: list[str],
    target_new: int,
    seen_resume_ids: set[str],
    seen_dedup_keys: set[str],
    run_state: RunState,
    source_plan: tuple[RuntimeSourceLanePlan, ...],
    liepin_context: Mapping[str, str | int | bool | None] | None,
    tracer: RunTracer,
) -> RetrievalExecutionResult:
    from seektalent.runtime.logical_query_dispatch import build_logical_query_dispatches
    from seektalent.runtime.retrieval_runtime import allocate_initial_lane_targets
    from seektalent.runtime.source_round_dispatch import SourceRoundDispatchRequest, SourceRoundDispatchResult, dispatch_source_rounds

    selected_sources = tuple(lane.source for lane in source_plan)
    lane_requested_counts = allocate_initial_lane_targets(query_states=list(query_states), target_new=target_new)
    logical_queries = build_logical_query_dispatches(
        query_states=query_states,
        lane_requested_counts=lane_requested_counts,
        source_plan_version=str(retrieval_plan.plan_version),
    )
    dispatch_request = SourceRoundDispatchRequest(
        runtime_run_id=tracer.run_id,
        round_no=round_no,
        logical_queries=logical_queries,
        selected_sources=selected_sources,
        seen_resume_ids=frozenset(seen_resume_ids),
        seen_dedup_keys=frozenset(seen_dedup_keys),
    )
    dispatch_result = await dispatch_source_rounds(
        request=dispatch_request,
        cts_adapter=lambda request: self._execute_cts_source_round_adapter(
            request=request,
            round_no=round_no,
            retrieval_plan=retrieval_plan,
            projection_adapter_notes=projection_adapter_notes,
            target_new=target_new,
            seen_resume_ids=seen_resume_ids,
            seen_dedup_keys=seen_dedup_keys,
            run_state=run_state,
            runtime_only_constraints=retrieval_plan.runtime_only_constraints,
            tracer=tracer,
        ),
        liepin_adapter=lambda request: self._execute_liepin_source_round_adapter(
            request=request,
            source_plan=source_plan,
            liepin_context=liepin_context,
            tracer=tracer,
            input_truth=run_state.input_truth,
        ),
    )
    self._merge_source_round_dispatch_result(
        run_state=run_state,
        dispatch_result=dispatch_result,
        source_plan=source_plan,
    )
    return self._round_search_result_from_source_dispatch(
        round_no=round_no,
        retrieval_plan=retrieval_plan,
        query_states=query_states,
        dispatch_result=dispatch_result,
        tracer=tracer,
    )
```

Import these existing types near the top of `src/seektalent/runtime/orchestrator.py`:

```python
from seektalent.models import InputTruth, RoundRetrievalPlan, RuntimeConstraint, SearchObservation
from seektalent.runtime.logical_query_dispatch import LogicalQueryDispatch
from seektalent.runtime.retrieval_runtime import LogicalQueryState, RetrievalExecutionResult
from seektalent.runtime.source_round_dispatch import (
    RuntimeSourceInvariantError,
    SourceRoundAdapterResult,
    SourceRoundDispatchRequest,
    SourceRoundDispatchResult,
)
```

Add a single merge helper and call it exactly once after `dispatch_source_rounds(...)` joins:

```python
def _merge_source_round_dispatch_result(
    self,
    *,
    run_state: RunState,
    dispatch_result: SourceRoundDispatchResult,
    source_plan: tuple[RuntimeSourceLanePlan, ...],
) -> None:
    source_order = {lane.source: index for index, lane in enumerate(source_plan)}
    for result in dispatch_result.source_results:
        if result.retrieval_result is not None:
            for candidate in result.retrieval_result.new_candidates:
                run_state.candidate_store[candidate.resume_id] = candidate
                if candidate.resume_id not in run_state.seen_resume_ids:
                    run_state.seen_resume_ids.append(candidate.resume_id)
        if result.lane_result is not None:
            merge_source_lane_result_updates(
                run_state=run_state,
                result=result.lane_result,
                source_order=source_order,
                rebuild_identity=False,
            )
    rebuild_candidate_identities(run_state, source_order=source_order)
```

Use existing identity helpers where possible. If the current `apply_source_lane_result(...)` always rebuilds identities, refactor it into a small internal `merge_source_lane_result_updates(..., rebuild_identity=True)` helper in `source_lanes.py` and keep `apply_source_lane_result(...)` as the public wrapper. Do not duplicate the identity merge implementation.

Add `_round_search_result_from_source_dispatch(...)`:

```python
def _round_search_result_from_source_dispatch(
    self,
    *,
    round_no: int,
    retrieval_plan: RoundRetrievalPlan,
    query_states: tuple[LogicalQueryState, ...],
    dispatch_result: SourceRoundDispatchResult,
    tracer: RunTracer,
) -> RetrievalExecutionResult:
    cts_results = [
        result.retrieval_result
        for result in dispatch_result.source_results
        if result.source == "cts" and result.retrieval_result is not None
    ]
    cts_queries = [query for result in cts_results for query in result.cts_queries]
    sent_query_records = [record for result in cts_results for record in result.sent_query_records]
    search_attempts = [attempt for result in cts_results for attempt in result.search_attempts]
    query_resume_hits = [hit for result in cts_results for hit in result.query_resume_hits]
    provider_returned_candidates = [
        item for result in cts_results for item in result.provider_returned_candidates
    ]
    candidates = list(dispatch_result.candidates)
    observation = SearchObservation(
        round_no=round_no,
        requested_count=retrieval_plan.target_new,
        raw_candidate_count=dispatch_result.raw_candidate_count,
        unique_new_count=len(candidates),
        shortage_count=max(0, retrieval_plan.target_new - len(candidates)),
        fetch_attempt_count=len(dispatch_result.source_results),
        exhausted_reason="target_satisfied" if len(candidates) >= retrieval_plan.target_new else "source_lanes_exhausted",
        new_resume_ids=[candidate.resume_id for candidate in candidates],
        new_candidate_summaries=[candidate.compact_summary() for candidate in candidates],
        adapter_notes=[
            note
            for result in dispatch_result.source_results
            for note in result.diagnostics
        ],
    )
    tracer.write_json(
        f"round.{round_no:02d}.retrieval.source_dispatch",
        {
            "round_no": round_no,
            "source_statuses": {result.source: result.status for result in dispatch_result.source_results},
            "raw_candidate_count": dispatch_result.raw_candidate_count,
            "unique_new_count": len(candidates),
        },
    )
    return RetrievalExecutionResult(
        cts_queries=cts_queries,
        sent_query_records=sent_query_records,
        new_candidates=candidates,
        search_observation=observation,
        search_attempts=search_attempts,
        query_resume_hits=query_resume_hits,
        provider_returned_candidates=provider_returned_candidates,
    )
```

Do not fabricate empty CTS metadata for multi-source rounds. If CTS is selected and completes, its `RetrievalExecutionResult` must supply query records, attempts, hits, and provider-returned diagnostics exactly as the CTS-only `_run_rounds(...)` path does. If CTS is blocked or failed, only then may these arrays be empty for that source.

- [ ] **Step 5: Implement CTS adapter by reusing existing retrieval runtime**

Add method:

```python
async def _execute_cts_source_round_adapter(
    self,
    *,
    request: SourceRoundDispatchRequest,
    round_no: int,
    retrieval_plan: RoundRetrievalPlan,
    projection_adapter_notes: list[str],
    target_new: int,
    seen_resume_ids: set[str],
    seen_dedup_keys: set[str],
    run_state: RunState,
    runtime_only_constraints: list[RuntimeConstraint],
    tracer: RunTracer,
) -> SourceRoundAdapterResult:
    result = await self.retrieval_runtime.execute_logical_dispatch_search(
        round_no=round_no,
        retrieval_plan=retrieval_plan,
        logical_queries=request.logical_queries,
        base_adapter_notes=projection_adapter_notes,
        target_new=target_new,
        seen_resume_ids=set(seen_resume_ids),
        seen_dedup_keys=set(seen_dedup_keys),
        tracer=tracer,
        score_for_query_outcome=lambda candidates: self._score_candidates_for_query_outcome(
            round_no=round_no,
            candidates=candidates,
            run_state=run_state,
            runtime_only_constraints=runtime_only_constraints,
        ),
        query_outcome_thresholds=QueryOutcomeThresholds(),
        record_provider_return_batch=lambda batch: self._record_corpus_provider_results(
            tracer=tracer,
            returned_candidates=batch,
        ),
    )
    return SourceRoundAdapterResult(
        source="cts",
        status="completed",
        candidates=tuple(result.new_candidates),
        raw_candidate_count=result.search_observation.raw_candidate_count,
        diagnostics=tuple(result.search_observation.adapter_notes),
        retrieval_result=result,
    )
```

This scoring is ephemeral query-outcome scoring only. It must update lane refill/broad-noise/zero-recall decisions and must not replace final ranking. Multi-source final ranking still happens after merge in the main round.

Add `RetrievalRuntime.execute_logical_dispatch_search(...)` as a narrow wrapper around the existing `execute_round_search(...)`. It must preserve CTS metadata and use the already-frozen `LogicalQueryDispatch.requested_count` values instead of recalculating the allocation from scratch. Keep `execute_round_search(...)` as the CTS-only and legacy entrypoint.

Do this by adding an explicit override to `execute_round_search(...)`:

```python
async def execute_round_search(
    ...,
    initial_lane_targets_override: Mapping[LaneType, int] | None = None,
) -> RetrievalExecutionResult:
    ...
    initial_targets = (
        dict(initial_lane_targets_override)
        if initial_lane_targets_override is not None
        else allocate_initial_lane_targets(query_states=query_states, target_new=target_new)
    )
```

Then `execute_logical_dispatch_search(...)` should convert `LogicalQueryDispatch` back to the matching `LogicalQueryState` inputs and pass `{dispatch.lane_type: dispatch.requested_count}` as `initial_lane_targets_override`. Add a regression where `target_new=10` but dispatch requested counts are `{exploit: 6, generic_explore: 4}` and assert the sent CTS query records use `6` and `4`, not the default `7` and `3`.

- [ ] **Step 6: Implement Liepin adapter using card lane requests per logical query**

Add helper in `src/seektalent/providers/liepin/runtime_lane.py`:

```python
from seektalent.runtime.logical_query_dispatch import LogicalQueryDispatch


async def run_liepin_logical_query_bundle(
    *,
    settings: AppSettings,
    runtime_run_id: str,
    source_plan_id: str,
    job_title: str,
    jd: str,
    notes: str,
    logical_queries: tuple[LogicalQueryDispatch, ...],
    source_budget_policy: RuntimeSourceBudgetPolicy,
    liepin_context: Mapping[str, str | int | bool | None] | None,
    worker_client: LiepinWorkerClient | None = None,
) -> RuntimeSourceLaneResult:
    merged_result: RuntimeSourceLaneResult | None = None
    for index, logical_query in enumerate(logical_queries, start=1):
        request = RuntimeSourceLaneRequest(
            source="liepin",
            lane_mode="card",
            job_title=job_title,
            jd=jd,
            notes=notes,
            runtime_run_id=runtime_run_id,
            source_plan_id=source_plan_id,
            source_lane_run_id=f"{source_plan_id}:lane:{index}",
            source_query_terms=logical_query.query_terms,
            logical_query_instance_id=logical_query.query_instance_id,
            logical_query_fingerprint=logical_query.query_fingerprint,
            logical_keyword_query=logical_query.keyword_query,
            logical_requested_count=logical_query.requested_count,
            source_budget_policy=source_budget_policy,
            liepin_context=liepin_context or {},
        )
        result = await run_liepin_source_lane(
            settings=settings,
            request=request,
            worker_client=worker_client,
        )
        merged_result = result if merged_result is None else merge_liepin_card_lane_results(merged_result, result)
    if merged_result is None:
        raise ValueError("Liepin logical query bundle requires at least one logical query.")
    return merged_result
```

Add `merge_liepin_card_lane_results(...)` in the same file:

```python
def merge_liepin_card_lane_results(
    first: RuntimeSourceLaneResult,
    second: RuntimeSourceLaneResult,
) -> RuntimeSourceLaneResult:
    candidate_updates = dict(first.candidate_store_updates)
    candidate_updates.update(second.candidate_store_updates)
    normalized_updates = dict(first.normalized_store_updates)
    normalized_updates.update(second.normalized_store_updates)
    status = "completed" if candidate_updates else second.status
    stop_reason_code = None if candidate_updates else (second.stop_reason_code or first.stop_reason_code)
    blocked_reason_code = None if candidate_updates else (second.blocked_reason_code or first.blocked_reason_code)
    return RuntimeSourceLaneResult(
        runtime_run_id=first.runtime_run_id,
        source_plan_id=first.source_plan_id,
        source_lane_run_id=first.source_lane_run_id,
        source=first.source,
        lane_mode=first.lane_mode,
        attempt=first.attempt,
        status=status,
        candidate_store_updates=candidate_updates,
        normalized_store_updates=normalized_updates,
        raw_candidate_count=int(first.raw_candidate_count or 0) + int(second.raw_candidate_count or 0),
        source_evidence_updates=first.source_evidence_updates + second.source_evidence_updates,
        detail_recommendations=first.detail_recommendations + second.detail_recommendations,
        events=first.events + second.events,
        blocked_reason_code=blocked_reason_code,
        stop_reason_code=stop_reason_code,
        retryable=first.retryable or second.retryable,
        safe_error_summary=first.safe_error_summary or second.safe_error_summary,
    )
```

- [ ] **Step 7: Implement Runtime Liepin adapter method**

In `WorkflowRuntime`, add:

```python
async def _execute_liepin_source_round_adapter(
    self,
    *,
    request: SourceRoundDispatchRequest,
    source_plan: tuple[RuntimeSourceLanePlan, ...],
    liepin_context: Mapping[str, str | int | bool | None] | None,
    tracer: RunTracer,
    input_truth: InputTruth,
	) -> SourceRoundAdapterResult:
	    liepin_plan = next((lane for lane in source_plan if lane.source == "liepin"), None)
	    if liepin_plan is None:
	        raise RuntimeSourceInvariantError("missing_liepin_source_plan")
	    if liepin_context is None or liepin_plan.safe_posture.get("status") == "blocked":
	        return SourceRoundAdapterResult(
	            source="liepin",
	            status="blocked",
	            safe_reason_code=str(
	                liepin_plan.safe_posture.get("safe_reason_code") or "source_browser_backend_unavailable"
	            ),
	        )
	    result = await run_liepin_logical_query_bundle(
	        settings=self.settings,
	        runtime_run_id=tracer.run_id,
	        source_plan_id=liepin_plan.source_plan_id,
	        job_title=input_truth.job_title,
	        jd=input_truth.jd,
	        notes=input_truth.notes or "",
	        logical_queries=request.logical_queries,
	        source_budget_policy=liepin_plan.source_budget_policy,
	        liepin_context=liepin_context,
	    )
    return SourceRoundAdapterResult(
	        source="liepin",
	        status=result.status if result.status in {"completed", "partial", "blocked", "failed"} else "failed",
	        candidates=tuple(result.candidate_store_updates.values()),
	        raw_candidate_count=int(result.raw_candidate_count or 0),
	        safe_reason_code=result.stop_reason_code or result.blocked_reason_code,
	        lane_result=result,
	    )
```

This adapter intentionally does not call `apply_source_lane_result(...)`. The only place that may mutate `RunState` for a multi-source round is the Runtime merge helper added below after `dispatch_source_rounds(...)` returns.

- [ ] **Step 8: Run tests**

```bash
uv run pytest tests/test_runtime_multi_source_round_dispatch.py tests/test_runtime_state_flow.py::test_initial_lane_targets_split_exploit_and_generic_explore -q
```

Expected: pass.

- [ ] **Step 9: Commit**

```bash
git add src/seektalent/runtime/orchestrator.py src/seektalent/runtime/retrieval_runtime.py src/seektalent/providers/liepin/runtime_lane.py tests/test_runtime_multi_source_round_dispatch.py
git commit -m "feat: dispatch multi-source rounds inside runtime loop"
```

---

## Task 8: Merge Multi-Source Results Before Scoring And Cap Final Top 10

**Files:**
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `src/seektalent/runtime/source_lanes.py`
- Modify: `src/seektalent_ui/workbench_store.py`
- Test: `tests/test_runtime_source_lanes.py`
- Test: `tests/test_workbench_semantic_guardrails.py`

- [ ] **Step 1: Add duplicate merge regression test**

Add to `tests/test_runtime_source_lanes.py`:

```python
def test_cross_source_same_identity_keeps_one_final_identity_with_fresh_canonical_resume() -> None:
    run_state = make_run_state_for_source_lane_tests()
    cts_candidate = make_candidate(
        resume_id="cts-old",
        source="cts",
        name="王明",
        company="旧公司",
        title="数据开发专家",
        updated_at="2023-01-01",
    )
    liepin_candidate = make_candidate(
        resume_id="liepin-new",
        source="liepin",
        name="王明",
        company="新公司",
        title="高级数据开发专家",
        updated_at="2025-01-01",
    )
    cts_result = make_lane_result(source="cts", candidates=[cts_candidate], provider_key_hash="same-person")
    liepin_result = make_lane_result(source="liepin", candidates=[liepin_candidate], provider_key_hash="same-person")

    apply_source_lane_result(run_state=run_state, result=cts_result, source_order={"cts": 0, "liepin": 1})
    apply_source_lane_result(run_state=run_state, result=liepin_result, source_order={"cts": 0, "liepin": 1})

    identity_ids = {
        run_state.candidate_identity_by_resume_id["cts-old"],
        run_state.candidate_identity_by_resume_id["liepin-new"],
    }
    assert len(identity_ids) == 1
    identity_id = next(iter(identity_ids))
    assert run_state.canonical_resume_by_identity_id[identity_id].canonical_resume_id == "liepin-new"
    assert len(run_state.source_evidence_by_resume_id["cts-old"]) == 1
    assert len(run_state.source_evidence_by_resume_id["liepin-new"]) == 1
```

Add to `tests/test_workbench_semantic_guardrails.py`:

```python
def test_final_top10_never_exceeds_ten_after_runtime_completion(client, workbench_store):
    session = _create_session(client, source_kinds=["cts", "liepin"])
    _persist_twelve_runtime_final_candidates(workbench_store, session["sessionId"])

    response = client.get(f"/api/workbench/sessions/{session['sessionId']}/final-top10")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 10
    assert [item["rank"] for item in payload["items"]] == list(range(1, 11))


def test_final_top10_uses_runtime_finalization_order_and_all_source_evidence(client, workbench_store):
    session = _create_session(client, source_kinds=["cts", "liepin"])
    _persist_runtime_finalization_fixture(
        workbench_store,
        session["sessionId"],
        ordered_identity_ids=["identity-b", "identity-a"],
        evidence_by_identity={
            "identity-a": [("cts", "evidence-cts-a"), ("liepin", "evidence-liepin-a")],
            "identity-b": [("cts", "evidence-cts-b")],
        },
    )

    response = client.get(f"/api/workbench/sessions/{session['sessionId']}/final-top10")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["runtimeIdentityId"] for item in items[:2]] == ["identity-b", "identity-a"]
    identity_a = next(item for item in items if item["runtimeIdentityId"] == "identity-a")
    assert {evidence["sourceKind"] for evidence in identity_a["sourceEvidence"]} == {"cts", "liepin"}
```

Define `_persist_twelve_runtime_final_candidates(...)` and `_persist_runtime_finalization_fixture(...)` in this test file. They must insert the new `runtime_finalization_revisions`, `runtime_candidate_identity_snapshots`, `candidate_review_items`, and `candidate_evidence` rows directly with deterministic ids so the test validates persistence behavior instead of depending on a live Runtime run.

Also define `make_run_state_for_source_lane_tests(...)`, `make_candidate(...)`, and `make_lane_result(...)` locally in `tests/test_runtime_source_lanes.py` if the repository does not already expose real helpers with these exact names. Keep them thin wrappers around the real Runtime dataclasses; do not import placeholder helper names from unrelated test modules.

- [ ] **Step 2: Keep scoring after the single round merge**

In `_run_rounds(...)`, multi-source dispatch must call `_merge_source_round_dispatch_result(...)` before `_round_search_result_from_source_dispatch(...)` returns and before `_score_round(...)` runs. The normal `_run_rounds(...)` loop may still idempotently write `retrieval_result.new_candidates` into `candidate_store`, but it must not be the first place Liepin evidence is merged.

```python
dispatch_result = await dispatch_source_rounds(...)
self._merge_source_round_dispatch_result(
    run_state=run_state,
    dispatch_result=dispatch_result,
    source_plan=source_plan,
)
retrieval_result = self._round_search_result_from_source_dispatch(...)
new_candidates = retrieval_result.new_candidates
```

Use the existing identity merge helper name if it already exists in `source_lanes.py`. Do not create a parallel identity implementation and do not call `apply_source_lane_result(...)` inside source adapters.

- [ ] **Step 3: Ensure final Top 10 uses identity pool**

Before finalizer context is built in `run_async(...)`, ensure `run_state.top_pool_ids` contains identity-deduped resume ids:

```python
identity_top_candidates = self._apply_identity_top_pool(run_state)
run_state.top_pool_ids = [candidate.resume_id for candidate in identity_top_candidates[:TOP_K]]
```

If `_apply_identity_top_pool(...)` already ran in the round, keep this as an idempotent guard.

- [ ] **Step 4: Persist only Runtime identity-finalized candidates as final rows**

In `workbench_store.py`, add Runtime finalization persistence tables to `_initialize(...)`:

```sql
CREATE TABLE IF NOT EXISTS runtime_finalization_revisions (
    finalization_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    runtime_run_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    reason_code TEXT NOT NULL,
    ordered_candidate_identity_ids_json TEXT NOT NULL,
    coverage_summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, workspace_id, user_id, session_id, runtime_run_id, revision)
);

CREATE INDEX IF NOT EXISTS idx_runtime_finalization_revisions_latest
ON runtime_finalization_revisions(tenant_id, workspace_id, user_id, session_id, revision DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS runtime_candidate_identity_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    runtime_run_id TEXT NOT NULL,
    identity_id TEXT NOT NULL,
    canonical_resume_id TEXT NOT NULL,
    merged_resume_ids_json TEXT NOT NULL,
    source_evidence_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, workspace_id, user_id, session_id, runtime_run_id, identity_id)
);
```

Add these tables and indexes to `src/seektalent_ui/maintenance.py` and `tests/test_workbench_maintenance.py`.

In `complete_runtime_sourcing_job_with_artifacts(...)`, persist three things in one transaction:

1. `runtime_finalization_revisions`: ordered identity ids from `artifacts.finalization_revision.candidate_identity_ids`, or identity ids derived from `artifacts.run_state.top_pool_ids[:10]`.
2. `runtime_candidate_identity_snapshots`: canonical resume id, merged resume ids, and all source evidence ids for every final identity.
3. review item/evidence projection rows for display, preserving the Runtime identity order.

`_persist_runtime_final_candidate_results_conn(...)` must read final resume ids from Runtime identity state: `artifacts.run_state.top_pool_ids[:10]` first, then `artifacts.finalization_revision.candidate_identity_ids` mapped through canonical resume selection. It may use `artifacts.final_result.candidates` to enrich summaries and reasons for already-selected ids, but `/final-top10` must not rank directly from an unconstrained finalizer candidate array or from `project_final_top_candidates(...)`.

Add an explicit final evidence level:

```python
evidence_level = "final"
```

Set `runtime_identity_id` from `artifacts.run_state.candidate_identity_by_resume_id` when present. For merged identities, persist source evidence for every resume in `run_state.source_evidence_by_identity_id[identity_id]`, not only the canonical resume's source. When returning persisted review rows from this helper, preserve the runtime order with an explicit review-item order map or a `CASE review_item_id ... END` SQL sort; do not `ORDER BY aggregate_score`.

In `workbench_routes.py`, change `/api/workbench/sessions/{session_id}/final-top10` to:

1. Read the latest `runtime_finalization_revisions` row for the session.
2. If present, read matching `runtime_candidate_identity_snapshots`.
3. Map each identity snapshot to the canonical review item and all `candidate_evidence` rows listed by `source_evidence_ids_json`.
4. Assign ranks from the stored identity order.
5. Use `project_final_top_candidates(...)` only when no runtime finalization revision exists.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_runtime_source_lanes.py tests/test_workbench_semantic_guardrails.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/runtime/orchestrator.py src/seektalent/runtime/source_lanes.py src/seektalent_ui/workbench_store.py tests/test_runtime_source_lanes.py tests/test_workbench_semantic_guardrails.py
git commit -m "fix: merge multi-source candidates before final ranking"
```

---

## Task 9: Project Runtime Source Plan And Final Top 10 Into Workbench Graph

**Files:**
- Modify: `apps/web-svelte/src/lib/workbench/runStory.ts`
- Modify: `apps/web-svelte/src/lib/workbench/runStory.test.ts`
- Modify: `apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte`
- Test: `apps/web-svelte/src/lib/workbench/runStory.test.ts`

- [ ] **Step 1: Add graph regression tests**

Add to `apps/web-svelte/src/lib/workbench/runStory.test.ts`:

```ts
it('renders one runtime source plan with CTS and Liepin branches', () => {
	const story = buildRunStory({
		session: sessionFixture({
			sourceCards: [
				sourceCardFixture({ sourceKind: 'cts', status: 'completed' }),
				sourceCardFixture({ sourceKind: 'liepin', status: 'completed' })
			],
			runtimeSourceState: runtimeSourceStateFixture({
				coverageStatus: 'complete',
				finalizationRevision: 1,
				sources: [
					runtimeSourceLaneStateFixture({ sourceKind: 'cts', status: 'completed' }),
					runtimeSourceLaneStateFixture({ sourceKind: 'liepin', status: 'completed' })
				]
			})
		}),
		events: [
			workbenchEventFixture({ eventName: 'runtime_source_plan_created', sourceKind: null }),
			workbenchEventFixture({ eventName: 'runtime_run_completed', sourceKind: null })
		],
		finalTopCandidates: [
			finalTopCandidateFixture({ rank: 1 }),
			finalTopCandidateFixture({ rank: 2 })
		]
	});

	expect(story.graphNodes.some((node) => node.id === 'runtime-source-plan')).toBe(true);
	expect(story.graphNodes.some((node) => node.id === 'source-cts')).toBe(true);
	expect(story.graphNodes.some((node) => node.id === 'source-liepin')).toBe(true);
	expect(story.graphNodes.some((node) => node.id === 'merge-dedupe')).toBe(true);
	const finalNode = story.graphNodes.find((node) => node.id === 'final-top10');
	expect(finalNode?.detail).toContain('2');
});

it('uses final top candidates instead of raw review item count for final node', () => {
	const story = buildRunStory({
		session: sessionFixture(),
		events: [workbenchEventFixture({ eventName: 'runtime_run_completed', sourceKind: null })],
		candidateReviewItems: Array.from({ length: 24 }, (_, index) =>
			candidateReviewItemFixture({ reviewItemId: `review-${index}` })
		),
		finalTopCandidates: Array.from({ length: 10 }, (_, index) =>
			finalTopCandidateFixture({ rank: index + 1 })
		)
	});

	const finalNode = story.graphNodes.find((node) => node.id === 'final-top10');
	expect(finalNode?.detail).toContain('10');
	expect(finalNode?.detail).not.toContain('24');
});

it('does not show zero final candidates while final top10 is still loading', () => {
	const story = buildRunStory({
		session: sessionFixture(),
		events: [workbenchEventFixture({ eventName: 'runtime_run_completed', sourceKind: null })],
		candidateReviewItems: Array.from({ length: 24 }, (_, index) =>
			candidateReviewItemFixture({ reviewItemId: `review-${index}` })
		),
		finalTopCandidates: [],
		finalTopStatus: 'loading'
	});

	const finalNode = story.graphNodes.find((node) => node.id === 'final-top10');
	expect(finalNode?.detail).toBe('最终短名单生成中');
	expect(finalNode?.detail).not.toContain('0 位候选人');
});

it('shows final top10 unavailable when the final ranking request fails', () => {
	const story = buildRunStory({
		session: sessionFixture(),
		events: [workbenchEventFixture({ eventName: 'runtime_run_completed', sourceKind: null })],
		finalTopCandidates: [],
		finalTopStatus: 'error'
	});

	const finalNode = story.graphNodes.find((node) => node.id === 'final-top10');
	expect(finalNode?.detail).toBe('最终短名单暂不可用');
	expect(finalNode?.tone).toBe('amber');
});
```

- [ ] **Step 2: Extend `BuildRunStoryInput`**

In `runStory.ts`, add:

```ts
type WorkbenchFinalTopCandidate = components['schemas']['WorkbenchFinalTopCandidateResponse'];
type FinalTopStatus = 'loading' | 'error' | 'ready';

export type BuildRunStoryInput = {
	session: WorkbenchSession;
	events: WorkbenchEvent[];
	candidateReviewItems?: WorkbenchCandidateReviewItem[];
	detailOpenRequests?: WorkbenchDetailOpenRequest[];
	finalTopCandidates?: WorkbenchFinalTopCandidate[];
	finalTopStatus?: FinalTopStatus;
	sourceFilter?: SourceFilter;
};
```

- [ ] **Step 3: Add Runtime source plan nodes**

In `buildRunStory(...)`, after requirements node creation, insert:

```ts
if (sourceKinds.length > 0) {
	graphNodes.push({
		id: 'runtime-source-plan',
		at: 2,
		kind: '计划',
			label: '检索计划',
			detail: `已选择 ${sourceKinds.length} 个来源`,
		x: 38,
		y: 50,
		tone: 'neutral',
		sourceKind: 'all',
			sourceLabel: '全部来源',
		lane: 'shared',
		detailKind: 'source-plan',
		detailPayload: { kind: 'source-plan', sourceKinds },
		eventIds: runtimeSourcePlanEventIds(scopedEvents),
		sourceRunId: null,
		candidateReviewItemIds: [],
		candidateEvidenceRefs: [],
		detailOpenRequestIds: []
	});
	graphEdges.push({ from: requirementsStarted || requirements || triageHasInput ? 'requirements' : 'job', to: 'runtime-source-plan', tone: 'neutral', label: '规划来源' });
}
```

Add source branch nodes from `runtimeSourceState.sources`:

```ts
for (const source of runtimeSourceState?.sources ?? []) {
	const nodeId = `source-${source.sourceKind}`;
	graphNodes.push({
		id: nodeId,
		at: 3,
		kind: '来源',
		label: sourceLabels[source.sourceKind],
		detail: sourceReasonLabel(source.reasonCode ?? source.status),
		x: source.sourceKind === 'cts' ? 52 : 52,
		y: source.sourceKind === 'cts' ? 30 : 70,
		tone: source.status === 'completed' ? 'green' : source.status === 'blocked' ? 'amber' : 'neutral',
		sourceKind: source.sourceKind,
		sourceLabel: sourceLabels[source.sourceKind],
		lane: source.sourceKind,
		detailKind: 'source',
		detailPayload: { kind: 'source', source },
		eventIds: scopedEvents.filter((event) => event.sourceKind === source.sourceKind).map(eventId),
		sourceRunId: source.sourceRunId,
		candidateReviewItemIds: [],
		candidateEvidenceRefs: [],
		detailOpenRequestIds: []
	});
	graphEdges.push({ from: 'runtime-source-plan', to: nodeId, tone: 'neutral', label: '执行' });
}
```

- [ ] **Step 4: Add merge and final nodes based on final-top10**

Use `finalTopCandidates` and `finalTopStatus` from input:

```ts
const finalTopCandidates = input.finalTopCandidates ?? [];
const finalTopStatus = input.finalTopStatus ?? 'ready';
const finalDetail =
	finalTopStatus === 'loading'
		? '最终短名单生成中'
		: finalTopStatus === 'error'
			? '最终短名单暂不可用'
			: `${finalTopCandidates.length} 位候选人`;
const finalTone =
	finalTopStatus === 'error' ? 'amber' : finalTopCandidates.length > 0 ? 'green' : 'neutral';
if (sourceKinds.length > 1) {
	graphNodes.push({
		id: 'merge-dedupe',
		at: 4,
		kind: '合并',
			label: '跨源合并',
			detail: `${finalTopCandidates.length} 位候选人进入最终排序`,
		x: 66,
		y: 50,
		tone: 'blue',
		sourceKind: 'all',
			sourceLabel: '全部来源',
		lane: 'shared',
		detailKind: 'merge',
		detailPayload: { kind: 'merge', finalTopCount: finalTopCandidates.length },
		eventIds: [],
		sourceRunId: null,
		candidateReviewItemIds: finalTopCandidates.map((item) => item.canonicalReviewItemId),
		candidateEvidenceRefs: [],
		detailOpenRequestIds: []
	});
	for (const source of runtimeSourceState?.sources ?? []) {
		graphEdges.push({ from: `source-${source.sourceKind}`, to: 'merge-dedupe', tone: 'blue', label: '证据合并' });
	}
}
graphNodes.push({
		id: 'final-top10',
		at: 5,
		kind: '短名单',
		label: '最终短名单',
		detail: finalDetail,
		x: 82,
		y: 50,
		tone: finalTone,
	sourceKind: 'all',
	sourceLabel: '全部来源',
	lane: 'shared',
	detailKind: 'final',
	detailPayload: { kind: 'final', finalTopCount: finalTopCandidates.length },
	eventIds: allRuntimeEvents.filter((item) => item.event.eventName === 'runtime_run_completed').map((item) => eventId(item.event)),
	sourceRunId: null,
	candidateReviewItemIds: finalTopCandidates.map((item) => item.canonicalReviewItemId),
	candidateEvidenceRefs: [],
	detailOpenRequestIds: []
});
graphEdges.push({ from: sourceKinds.length > 1 ? 'merge-dedupe' : `source-${sourceKinds[0]}`, to: 'final-top10', tone: 'green', label: '最终排序' });
```

- [ ] **Step 5: Pass finalTop10 into `buildRunStory(...)`**

In `apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte`, pass the query result:

```ts
const story = $derived(
	buildRunStory({
		session: sessionQuery.data,
		events: eventsQuery.data?.items ?? [],
		candidateReviewItems: candidatesQuery.data?.items ?? [],
			detailOpenRequests: detailRequestsQuery.data?.items ?? [],
			finalTopCandidates: finalTopQuery.data?.items ?? [],
			finalTopStatus: finalTopQuery.isLoading ? 'loading' : finalTopQuery.isError ? 'error' : 'ready'
		})
	);
```

- [ ] **Step 6: Run frontend focused tests**

```bash
bun --cwd apps/web-svelte test src/lib/workbench/runStory.test.ts
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add apps/web-svelte/src/lib/workbench/runStory.ts apps/web-svelte/src/lib/workbench/runStory.test.ts 'apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte'
git commit -m "fix: graph runtime-owned multi-source flow"
```

---

## Task 10: End-To-End Regression And Safety Verification

**Files:**
- Modify: `tests/test_workbench_api.py`
- Modify: `apps/web-svelte/tests/e2e/dev-mode-dual-source.spec.ts`

- [ ] **Step 1: Add backend degraded coverage regression**

Add to `tests/test_workbench_api.py`:

```python
async def _blocked_liepin_logical_query_bundle(**kwargs):
    from seektalent.runtime.source_lanes import RuntimeSourceLaneResult

    runtime_run_id = str(kwargs["runtime_run_id"])
    source_plan_id = str(kwargs["source_plan_id"])
    return RuntimeSourceLaneResult(
        runtime_run_id=runtime_run_id,
        source_plan_id=source_plan_id,
        source_lane_run_id=f"{source_plan_id}:liepin:blocked",
        source="liepin",
        lane_mode="card",
        attempt=1,
        status="blocked",
        raw_candidate_count=0,
        blocked_reason_code="source_browser_backend_unavailable",
    )


def test_dual_source_runtime_job_keeps_cts_when_liepin_blocks(client, workbench_store, monkeypatch):
    session = _create_session(client, source_kinds=["cts", "liepin"])
    _approve_requirement_triage(client, session["sessionId"])
    monkeypatch.setattr(
        "seektalent.providers.liepin.runtime_lane.run_liepin_logical_query_bundle",
        _blocked_liepin_logical_query_bundle,
    )

    response = client.post(
        f"/api/workbench/sessions/{session['sessionId']}/start",
        headers=_csrf_headers(client),
        json={"idempotencyKey": "degraded-run"},
    )

    assert response.status_code == 202
    _drain_workbench_jobs(workbench_store)
    final_top = client.get(f"/api/workbench/sessions/{session['sessionId']}/final-top10")
    assert final_top.status_code == 200
    assert final_top.json()["coverageStatus"] in {"degraded", "complete"}
    assert len(final_top.json()["items"]) <= 10
```

If the production adapter function keeps a different name after Task 7, monkeypatch the public Liepin logical-bundle entrypoint created there. Do not patch a lower browser/session function for this regression; the test must validate source-scoped degraded coverage at the Runtime dispatch boundary.

- [ ] **Step 2: Add Svelte e2e graph count regression**

In `apps/web-svelte/tests/e2e/dev-mode-dual-source.spec.ts`, add a mocked final-top10 response with 10 items and candidate review response with 24 items. Include realistic source evidence for at least one merged identity. Assert the final node displays 10 and does not display 24:

```ts
		await expect(page.getByText('最终短名单')).toBeVisible();
		await expect(page.getByText(/10 位候选人/)).toBeVisible();
		await expect(page.getByText(/24 位候选人/)).not.toBeVisible();
```

- [ ] **Step 3: Add public payload no-leak regressions**

Add backend API payload tests in `tests/test_workbench_api.py`:

```python
import json

PUBLIC_PAYLOAD_DENY_TERMS = (
    "OpenCLI",
    "DokoBot",
    "mcp",
    "pi_agent",
    "cookie",
    "authorization",
    "raw_provider_payload",
    "raw_resume",
    "/Users/",
)


def _assert_public_payload_safe(payload: object) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    for term in PUBLIC_PAYLOAD_DENY_TERMS:
        assert term not in text


def test_workbench_public_payloads_do_not_expose_provider_internals(client, workbench_store):
    session = _create_session(client, source_kinds=["cts", "liepin"])
    _persist_public_payload_safety_fixture(workbench_store, session["sessionId"])

    for suffix in ("", "/events", "/final-top10"):
        response = client.get(f"/api/workbench/sessions/{session['sessionId']}{suffix}")
        assert response.status_code == 200
        _assert_public_payload_safe(response.json())
```

Define `_persist_public_payload_safety_fixture(...)` in the test file. It should insert a blocked Liepin projection using an internal stored reason such as `liepin_opencli_timeout`, then assert the API returns the mapped business-safe reason such as `source_browser_timeout`.

Add a DOM no-leak assertion to the Svelte e2e test after the mocked dual-source page renders:

```ts
const html = await page.locator('body').innerHTML();
for (const term of ['OpenCLI', 'DokoBot', 'mcp', 'pi_agent', 'cookie', 'authorization', 'raw_provider_payload', 'raw_resume']) {
	expect(html).not.toContain(term);
}
```

- [ ] **Step 4: Run full focused verification**

```bash
uv run pytest tests/test_workbench_runtime_owned_execution.py tests/test_runtime_multi_source_round_dispatch.py tests/test_runtime_source_lanes.py tests/test_workbench_semantic_guardrails.py tests/test_workbench_api.py tests/test_workbench_maintenance.py -q
bun --cwd apps/web-svelte test src/lib/workbench/runStory.test.ts src/lib/workbench/finalCandidateCards.test.ts
uv run ruff check src tests
uv run --group dev ty check src tests
git diff --check
```

Expected:

- pytest passes
- Svelte tests pass
- ruff passes
- ty passes
- diff check passes

- [ ] **Step 5: Run auxiliary public-surface source scan**

```bash
rg -n "cookie|authorization|storageState|raw_provider_payload|OpenCLI|DokoBot|mcp|localStorage|session secret|Bearer" src/seektalent_ui apps/web-svelte/src apps/web-svelte/tests/e2e tests/test_workbench_api.py
```

Expected: investigate any matches in public route serializers, public event builders, Svelte render paths, or fixture payloads. Matches are allowed only for deny lists, internal-to-public mapping tests, or comments asserting absence. Internal provider modules and Python source files may retain internal reason names; the hard pass/fail condition is the API/DOM payload tests above.

- [ ] **Step 6: Commit verification updates**

```bash
git add tests/test_workbench_api.py apps/web-svelte/tests/e2e/dev-mode-dual-source.spec.ts
git commit -m "test: cover runtime-owned dual-source workbench flow"
```

---

## Deferred Follow-Ups

Record these only if they are not already present in the repository follow-up document:

- Manual Liepin card-review UI with explicit human approval.
- Manual merge/unmerge controls for ambiguous duplicates.
- Source quality/cost metrics and automatic source strategy optimization.
- A stable internal source capability descriptor for future Boss/Zhaopin/ATS sources.
- Browser action trace replay fixtures for live Liepin debugging.

## Final Verification

Run before review:

```bash
uv run pytest tests/test_workbench_runtime_owned_execution.py tests/test_runtime_multi_source_round_dispatch.py tests/test_runtime_source_lanes.py tests/test_workbench_semantic_guardrails.py tests/test_workbench_api.py tests/test_workbench_maintenance.py -q
uv run pytest tests/test_runtime_state_flow.py tests/test_rescue_router.py -q
bun --cwd apps/web-svelte test src/lib/workbench/runStory.test.ts src/lib/workbench/finalCandidateCards.test.ts
uv run ruff check src tests
uv run --group dev ty check src tests
git diff --check
```

Expected: all commands pass.

## Self-Review

- Spec coverage: the tasks cover one runtime job per session, shared Runtime round loop, 70/30 query allocation, candidate-feedback priority, parallel source dispatch, source-scoped failure, identity merge, final Top 10 cap, and graph projection.
- Placeholder scan: task-local helpers and contracts are explicitly defined before use. Remaining ellipses are limited to conceptual Python stubs or shortened context in explanatory snippets, not missing implementation steps.
- Type consistency: `WorkbenchRuntimeSourcingJob`, `WorkbenchRuntimeSourcingJobContext`, `WorkbenchRuntimeSourcingJobStartResponse`, `SourceRoundDispatchRequest`, `SourceRoundAdapterResult`, `SourceRoundDispatchResult`, and the single merge helper contract are introduced before use in later tasks.
