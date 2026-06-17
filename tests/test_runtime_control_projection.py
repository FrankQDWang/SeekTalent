from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from seektalent_ui.workbench_store import WorkbenchStore, WorkbenchUser


PROJECTION_TIME = "2026-06-17T00:00:10.000000Z"


def test_unprojected_public_runtime_events_project_into_workbench_session_events(tmp_path: Path) -> None:
    from seektalent_ui.runtime_control_projection import RuntimeControlProjectionService
    from seektalent_ui.runtime_workbench_bridge import RuntimeWorkbenchBridge

    runtime_store = _runtime_store_with_run(tmp_path, runtime_run_id="runtime_run_projection", workbench_session_id=None)
    workbench_store, user = _workbench_store_with_user(tmp_path)
    session = workbench_store.create_workbench_session(
        user=user,
        job_title="Data Engineer",
        jd_text="Own data products.",
        notes="",
        source_kinds=["cts"],
        runtime_run_id="runtime_run_projection",
    )
    runtime_store.link_workbench_session(
        runtime_run_id="runtime_run_projection",
        workbench_session_id=session.session_id,
        updated_at="2026-06-17T00:00:00.500000Z",
    )
    public_event = runtime_store.append_event(
        _runtime_event(
            event_id="rtevt_public_1",
            runtime_run_id="runtime_run_projection",
            visibility="public",
            created_at="2026-06-17T00:00:01.000000Z",
        )
    )
    runtime_store.append_event(
        _runtime_event(
            event_id="rtevt_internal_1",
            runtime_run_id="runtime_run_projection",
            visibility="internal",
            created_at="2026-06-17T00:00:02.000000Z",
        )
    )
    service = RuntimeControlProjectionService(
        runtime_store=runtime_store,
        bridge=RuntimeWorkbenchBridge(runtime_store=runtime_store, workbench_store=workbench_store),
        user=user,
        now=lambda: PROJECTION_TIME,
    )

    result = service.project_unprojected_public_events(runtime_run_id="runtime_run_projection", limit=10)

    assert result.attempted_count == 1
    assert result.projected_count == 1
    assert result.failed_count == 0
    projected = _runtime_public_events(workbench_store, user=user, session_id=session.session_id)
    assert len(projected) == 1
    assert projected[0].idempotency_key == public_event.event_id
    assert projected[0].payload["eventId"] == public_event.event_id
    stored = runtime_store.get_event(runtime_run_id="runtime_run_projection", event_id=public_event.event_id)
    assert stored.workbench_event_global_seq == projected[0].global_seq
    assert stored.projected_at == PROJECTION_TIME
    assert stored.last_projection_error_code is None


def test_projection_retry_is_idempotent_without_duplicate_workbench_event(tmp_path: Path) -> None:
    from seektalent_ui.runtime_control_projection import RuntimeControlProjectionService
    from seektalent_ui.runtime_workbench_bridge import RuntimeWorkbenchBridge

    runtime_store, workbench_store, user, session = _linked_projection_context(
        tmp_path,
        runtime_run_id="runtime_run_projection_retry",
    )
    runtime_store.append_event(
        _runtime_event(
            event_id="rtevt_public_retry",
            runtime_run_id="runtime_run_projection_retry",
            visibility="public",
            created_at="2026-06-17T00:00:01.000000Z",
        )
    )
    service = RuntimeControlProjectionService(
        runtime_store=runtime_store,
        bridge=RuntimeWorkbenchBridge(runtime_store=runtime_store, workbench_store=workbench_store),
        user=user,
        now=lambda: PROJECTION_TIME,
    )

    first = service.project_unprojected_public_events(runtime_run_id="runtime_run_projection_retry", limit=10)
    retry = service.project_unprojected_public_events(runtime_run_id="runtime_run_projection_retry", limit=10)

    assert first.projected_count == 1
    assert retry.attempted_count == 0
    assert len(_runtime_public_events(workbench_store, user=user, session_id=session.session_id)) == 1


def test_projection_retry_backfills_after_success_mark_failure(tmp_path: Path) -> None:
    from seektalent_ui.runtime_control_projection import RuntimeControlProjectionService
    from seektalent_ui.runtime_workbench_bridge import RuntimeWorkbenchBridge

    runtime_store, workbench_store, user, session = _linked_projection_context(
        tmp_path,
        runtime_run_id="runtime_run_projection_backfill",
    )
    runtime_store.append_event(
        _runtime_event(
            event_id="rtevt_public_backfill",
            runtime_run_id="runtime_run_projection_backfill",
            visibility="public",
            created_at="2026-06-17T00:00:01.000000Z",
        )
    )
    flaky_store = _FailingSuccessMarkRuntimeStore(runtime_store)
    service = RuntimeControlProjectionService(
        runtime_store=flaky_store,
        bridge=RuntimeWorkbenchBridge(runtime_store=flaky_store, workbench_store=workbench_store),
        user=user,
        now=lambda: PROJECTION_TIME,
    )

    first = service.project_unprojected_public_events(runtime_run_id="runtime_run_projection_backfill", limit=10)
    retry = service.project_unprojected_public_events(runtime_run_id="runtime_run_projection_backfill", limit=10)

    projected = _runtime_public_events(workbench_store, user=user, session_id=session.session_id)
    stored = runtime_store.get_event(runtime_run_id="runtime_run_projection_backfill", event_id="rtevt_public_backfill")
    assert first.failed_count == 1
    assert retry.projected_count == 1
    assert len(projected) == 1
    assert stored.workbench_event_global_seq == projected[0].global_seq
    assert stored.projected_at == PROJECTION_TIME
    assert stored.last_projection_error_code is None


def test_projection_failure_increments_attempt_metadata_and_leaves_event_unprojected(tmp_path: Path) -> None:
    from seektalent_ui.runtime_control_projection import RuntimeControlProjectionService

    runtime_store, _workbench_store, user, _session = _linked_projection_context(
        tmp_path,
        runtime_run_id="runtime_run_projection_failure",
    )
    runtime_store.append_event(
        _runtime_event(
            event_id="rtevt_public_failure",
            runtime_run_id="runtime_run_projection_failure",
            visibility="public",
            created_at="2026-06-17T00:00:01.000000Z",
        )
    )
    service = RuntimeControlProjectionService(
        runtime_store=runtime_store,
        bridge=_FailingProjectionBridge(),
        user=user,
        now=lambda: PROJECTION_TIME,
    )

    result = service.project_unprojected_public_events(runtime_run_id="runtime_run_projection_failure", limit=10)

    stored = runtime_store.get_event(runtime_run_id="runtime_run_projection_failure", event_id="rtevt_public_failure")
    assert result.attempted_count == 1
    assert result.projected_count == 0
    assert result.failed_count == 1
    assert stored.projection_attempt_count == 1
    assert stored.last_projection_error_code == "runtime_projection_failed"
    assert stored.projected_at is None
    assert stored.workbench_event_global_seq is None
    assert runtime_store.list_unprojected_public_events(runtime_run_id="runtime_run_projection_failure", limit=10) == [
        stored
    ]


def test_projection_success_rejects_conflicting_workbench_event_sequence(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    runtime_store, _workbench_store, _user, _session = _linked_projection_context(
        tmp_path,
        runtime_run_id="runtime_run_projection_conflict",
    )
    runtime_store.append_event(
        _runtime_event(
            event_id="rtevt_public_conflict",
            runtime_run_id="runtime_run_projection_conflict",
            visibility="public",
            created_at="2026-06-17T00:00:01.000000Z",
        )
    )
    runtime_store.mark_event_projection_success(
        runtime_run_id="runtime_run_projection_conflict",
        event_id="rtevt_public_conflict",
        workbench_event_global_seq=7,
        projected_at=PROJECTION_TIME,
    )

    with pytest.raises(RuntimeControlError) as exc_info:
        runtime_store.mark_event_projection_success(
            runtime_run_id="runtime_run_projection_conflict",
            event_id="rtevt_public_conflict",
            workbench_event_global_seq=8,
            projected_at=PROJECTION_TIME,
        )

    assert exc_info.value.reason_code == "runtime_event_projection_conflict"
    stored = runtime_store.get_event(
        runtime_run_id="runtime_run_projection_conflict",
        event_id="rtevt_public_conflict",
    )
    assert stored.workbench_event_global_seq == 7
    assert stored.projected_at == PROJECTION_TIME


def test_workbench_bridge_repairs_missing_and_broken_links_by_runtime_run_id(tmp_path: Path) -> None:
    from seektalent_ui.runtime_workbench_bridge import RuntimeWorkbenchBridge

    runtime_store = _runtime_store_with_run(tmp_path, runtime_run_id="runtime_run_missing_link", workbench_session_id=None)
    workbench_store, user = _workbench_store_with_user(tmp_path)
    missing_session = workbench_store.create_workbench_session(
        user=user,
        job_title="Existing missing link session",
        jd_text="Existing JD",
        notes="",
        source_kinds=["cts"],
        runtime_run_id="runtime_run_missing_link",
    )
    _create_runtime_run(runtime_store, runtime_run_id="runtime_run_broken_link", workbench_session_id="session_deleted")
    broken_session = workbench_store.create_workbench_session(
        user=user,
        job_title="Existing broken link session",
        jd_text="Existing JD",
        notes="",
        source_kinds=["cts"],
        runtime_run_id="runtime_run_broken_link",
    )
    bridge = RuntimeWorkbenchBridge(runtime_store=runtime_store, workbench_store=workbench_store)

    missing = bridge.ensure_workbench_session_for_run(
        user=user,
        runtime_run_id="runtime_run_missing_link",
        job_title="Must not create",
        jd_text="Must not create",
        notes="",
    )
    broken = bridge.ensure_workbench_session_for_run(
        user=user,
        runtime_run_id="runtime_run_broken_link",
        job_title="Must not create",
        jd_text="Must not create",
        notes="",
    )

    assert missing.workbench_session_id == missing_session.session_id
    assert broken.workbench_session_id == broken_session.session_id
    assert runtime_store.get_run("runtime_run_missing_link").workbench_session_id == missing_session.session_id
    assert runtime_store.get_run("runtime_run_broken_link").workbench_session_id == broken_session.session_id


def test_projection_path_does_not_call_artifact_reconciliation(tmp_path: Path) -> None:
    from seektalent_ui.runtime_control_projection import RuntimeControlProjectionService
    from seektalent_ui.runtime_workbench_bridge import RuntimeWorkbenchBridge

    runtime_store, workbench_store, user, _session = _linked_projection_context(
        tmp_path,
        runtime_run_id="runtime_run_projection_no_artifacts",
    )
    runtime_store.append_event(
        _runtime_event(
            event_id="rtevt_public_no_artifacts",
            runtime_run_id="runtime_run_projection_no_artifacts",
            visibility="public",
            created_at="2026-06-17T00:00:01.000000Z",
        )
    )

    assert not hasattr(workbench_store, "reconcile_runtime_public_events_from_artifacts")
    service = RuntimeControlProjectionService(
        runtime_store=runtime_store,
        bridge=RuntimeWorkbenchBridge(runtime_store=runtime_store, workbench_store=workbench_store),
        user=user,
        now=lambda: PROJECTION_TIME,
    )

    result = service.project_unprojected_public_events(runtime_run_id="runtime_run_projection_no_artifacts", limit=10)

    assert result.projected_count == 1


def test_projected_public_runtime_events_feed_workbench_source_count_projection(tmp_path: Path) -> None:
    from seektalent_ui.runtime_control_projection import RuntimeControlProjectionService
    from seektalent_ui.runtime_workbench_bridge import RuntimeWorkbenchBridge

    runtime_store, workbench_store, user, session = _linked_projection_context(
        tmp_path,
        runtime_run_id="runtime_run_projection_source_counts",
    )
    runtime_store.append_event(
        _runtime_event(
            event_id="rtevt_public_source_counts",
            runtime_run_id="runtime_run_projection_source_counts",
            visibility="public",
            created_at="2026-06-17T00:00:01.000000Z",
            payload={
                "counts": {
                    "roundReturned": 9,
                    "roundIdentities": 7,
                    "sourceCumulativeReturned": 12,
                    "sourceCumulativeIdentities": 8,
                },
                "details": {},
                "safeReasonCode": None,
            },
        )
    )
    service = RuntimeControlProjectionService(
        runtime_store=runtime_store,
        bridge=RuntimeWorkbenchBridge(runtime_store=runtime_store, workbench_store=workbench_store),
        user=user,
        now=lambda: PROJECTION_TIME,
    )

    result = service.project_unprojected_public_events(
        runtime_run_id="runtime_run_projection_source_counts",
        limit=10,
    )

    source_counts = workbench_store.latest_runtime_source_count_projection(
        user=user,
        session_id=session.session_id,
    )
    assert result.projected_count == 1
    assert source_counts["cts"].status == "completed"
    assert source_counts["cts"].cards_scanned_count == 12
    assert source_counts["cts"].unique_candidates_count == 8


def test_candidate_truth_projection_is_idempotent_by_runtime_revision(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeCheckpoint
    from seektalent_ui.runtime_control_projection import RuntimeControlProjectionService
    from seektalent_ui.runtime_workbench_bridge import RuntimeWorkbenchBridge
    from tests.test_runtime_control_candidate_truth import _run_state_payload

    runtime_store, workbench_store, user, session = _linked_projection_context(
        tmp_path,
        runtime_run_id="runtime_run_candidate_projection",
    )
    lease = runtime_store.acquire_executor_lease(
        runtime_run_id="runtime_run_candidate_projection",
        executor_id="executor_candidates",
        acquired_at="2026-06-17T00:00:00.000000Z",
        lease_expires_at="2026-06-17T00:01:00.000000Z",
    )
    runtime_store.write_checkpoint(
        RuntimeCheckpoint(
            checkpoint_id="rtcheckpoint_candidate_projection",
            runtime_run_id="runtime_run_candidate_projection",
            stage="finalization",
            round_no=None,
            safe_boundary="runtime_candidate_checkpoint",
            run_state={
                **_run_state_payload(),
                "finalization_revisions": [
                    {
                        "revision": 1,
                        "runtime_run_id": "runtime_run_candidate_projection",
                        "reason_code": "runtime_finalized",
                        "candidate_identity_ids": ["identity_1"],
                        "coverage_summary": {"status": "complete"},
                        "created_at": "2026-06-17T00:00:09.000000Z",
                    }
                ],
            },
            source_plan={"sourceIds": ["cts"]},
            pending_commands=[],
            artifact_manifest_ref=None,
            schema_version="runtime-control-checkpoint/v1",
            created_at="2026-06-17T00:00:10.000000Z",
        ),
        executor_id="executor_candidates",
        attempt_no=lease.attempt_no,
    )
    counting_runtime_store = _CountingCandidateTruthRuntimeStore(runtime_store)
    service = RuntimeControlProjectionService(
        runtime_store=counting_runtime_store,
        bridge=RuntimeWorkbenchBridge(runtime_store=counting_runtime_store, workbench_store=workbench_store),
        user=user,
        now=lambda: PROJECTION_TIME,
    )

    first = service.project_unprojected_candidate_truth(
        runtime_run_id="runtime_run_candidate_projection",
        limit=10,
    )
    retry = service.project_unprojected_candidate_truth(
        runtime_run_id="runtime_run_candidate_projection",
        limit=10,
    )

    snapshots = workbench_store.list_runtime_candidate_identity_snapshots(
        user=user,
        session_id=session.session_id,
        runtime_run_id="runtime_run_candidate_projection",
    )
    assert first.projected_count == 1
    assert retry.attempted_count == 0
    assert counting_runtime_store.list_candidate_finalization_revision_calls == 0
    assert counting_runtime_store.list_candidate_identity_calls == 1
    assert counting_runtime_store.list_candidate_evidence_calls == 1
    assert snapshots is not None
    assert [(item.identity_id, item.canonical_resume_id) for item in snapshots] == [("identity_1", "resume_1")]
    with sqlite3.connect(workbench_store.db_path) as conn:
        revision_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM runtime_finalization_revisions
            WHERE session_id = ? AND runtime_run_id = ?
            """,
            (session.session_id, "runtime_run_candidate_projection"),
        ).fetchone()[0]
        event_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM session_events
            WHERE session_id = ? AND event_name = 'candidate_review_item_upserted'
            """,
            (session.session_id,),
        ).fetchone()[0]
    assert revision_count == 1
    assert event_count == 1


def test_workbench_sessions_schema_has_runtime_run_id_and_partial_unique_index(tmp_path: Path) -> None:
    db_path = tmp_path / "workbench.sqlite3"
    store = WorkbenchStore(db_path)
    store.ensure_local_actor()

    with sqlite3.connect(db_path) as conn:
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        indexes = {row[1]: row for row in conn.execute("PRAGMA index_list(sessions)").fetchall()}
        index_columns = [
            row[2] for row in conn.execute("PRAGMA index_info(idx_sessions_runtime_run_id)").fetchall()
        ]
        index_sql = conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'index' AND name = 'idx_sessions_runtime_run_id'
            """
        ).fetchone()[0]

    assert columns["runtime_run_id"][2] == "TEXT"
    assert columns["runtime_run_id"][3] == 0
    assert indexes["idx_sessions_runtime_run_id"][2] == 1
    assert indexes["idx_sessions_runtime_run_id"][4] == 1
    assert index_columns == ["runtime_run_id"]
    assert "WHERE runtime_run_id IS NOT NULL" in index_sql


def test_create_workbench_session_with_same_runtime_run_id_is_idempotent(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user = store.ensure_local_actor()

    first = store.create_workbench_session(
        user=user,
        job_title="Data Engineer",
        jd_text="Own data products.",
        notes="First attempt.",
        source_kinds=["cts"],
        runtime_run_id="runtime-run-idempotent",
    )
    second = store.create_workbench_session(
        user=user,
        job_title="Changed title must not replace the existing session",
        jd_text="Changed JD",
        notes="Duplicate attempt.",
        source_kinds=["cts"],
        runtime_run_id="runtime-run-idempotent",
    )
    random_first = store.create_workbench_session(
        user=user,
        job_title="Random A",
        jd_text="No runtime run id.",
        notes="",
        source_kinds=["cts"],
    )
    random_second = store.create_workbench_session(
        user=user,
        job_title="Random B",
        jd_text="No runtime run id.",
        notes="",
        source_kinds=["cts"],
    )

    assert second.session_id == first.session_id
    assert second.runtime_run_id == "runtime-run-idempotent"
    assert second.job_title == first.job_title
    assert random_first.session_id != random_second.session_id
    assert random_first.runtime_run_id is None
    assert random_second.runtime_run_id is None
    with sqlite3.connect(store.db_path) as conn:
        session_count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE runtime_run_id = 'runtime-run-idempotent'"
        ).fetchone()[0]
        source_run_count = conn.execute(
            "SELECT COUNT(*) FROM source_runs WHERE session_id = ?",
            (first.session_id,),
        ).fetchone()[0]
    assert session_count == 1
    assert source_run_count == 1


def test_concurrent_workbench_session_creation_with_same_runtime_run_id_returns_one_session(
    tmp_path: Path,
) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user = store.ensure_local_actor()
    worker_count = 8
    barrier = Barrier(worker_count)

    def create(index: int) -> str:
        barrier.wait()
        session = store.create_workbench_session(
            user=user,
            job_title=f"Concurrent {index}",
            jd_text="Create from runtime control.",
            notes="",
            source_kinds=["cts"],
            runtime_run_id="runtime-run-concurrent",
        )
        return session.session_id

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        session_ids = list(executor.map(create, range(worker_count)))

    assert len(set(session_ids)) == 1
    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute(
            """
            SELECT session_id, runtime_run_id
            FROM sessions
            WHERE runtime_run_id = 'runtime-run-concurrent'
            """
        ).fetchall()
        source_run_count = conn.execute(
            "SELECT COUNT(*) FROM source_runs WHERE session_id = ?",
            (session_ids[0],),
        ).fetchone()[0]
    assert rows == [(session_ids[0], "runtime-run-concurrent")]
    assert source_run_count == 1


def test_get_workbench_session_by_runtime_run_id_is_scoped_by_user_and_workspace(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    local_user = store.ensure_local_actor()
    local_session = store.create_workbench_session(
        user=local_user,
        job_title="Local",
        jd_text="Local JD",
        notes="",
        source_kinds=["cts"],
        runtime_run_id="runtime-run-local",
    )
    foreign_user = _insert_user(
        store.db_path,
        user_id="user_foreign",
        email="foreign@example.com",
        workspace_id="default",
    )
    foreign_session = store.create_workbench_session(
        user=foreign_user,
        job_title="Foreign",
        jd_text="Foreign JD",
        notes="",
        source_kinds=["cts"],
        runtime_run_id="runtime-run-foreign",
    )
    other_workspace_user = _insert_user(
        store.db_path,
        user_id="user_other_workspace",
        email="other-workspace@example.com",
        workspace_id="other-workspace",
    )
    other_workspace_session = store.create_workbench_session(
        user=other_workspace_user,
        job_title="Other workspace",
        jd_text="Other workspace JD",
        notes="",
        source_kinds=["cts"],
        runtime_run_id="runtime-run-other-workspace",
    )
    wrong_workspace_local_user = WorkbenchUser(
        user_id=local_user.user_id,
        email=local_user.email,
        display_name=local_user.display_name,
        role=local_user.role,
        workspace_id="other-workspace",
    )

    found_local = store.get_workbench_session_by_runtime_run_id(
        user=local_user,
        runtime_run_id="runtime-run-local",
    )
    found_foreign = store.get_workbench_session_by_runtime_run_id(
        user=foreign_user,
        runtime_run_id="runtime-run-foreign",
    )
    found_other_workspace = store.get_workbench_session_by_runtime_run_id(
        user=other_workspace_user,
        runtime_run_id="runtime-run-other-workspace",
    )
    assert found_local is not None
    assert found_foreign is not None
    assert found_other_workspace is not None
    assert found_local.session_id == local_session.session_id
    assert found_foreign.session_id == foreign_session.session_id
    assert found_other_workspace.session_id == other_workspace_session.session_id
    assert store.get_workbench_session_by_runtime_run_id(
        user=local_user,
        runtime_run_id="runtime-run-foreign",
    ) is None
    assert store.get_workbench_session_by_runtime_run_id(
        user=foreign_user,
        runtime_run_id="runtime-run-local",
    ) is None
    assert store.get_workbench_session_by_runtime_run_id(
        user=wrong_workspace_local_user,
        runtime_run_id="runtime-run-local",
    ) is None


def _insert_user(
    db_path: Path,
    *,
    user_id: str,
    email: str,
    workspace_id: str,
) -> WorkbenchUser:
    now = "2026-06-17T00:00:00+00:00"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO workspaces (workspace_id, tenant_id, name, created_at)
            VALUES (?, 'local', ?, ?)
            """,
            (workspace_id, workspace_id, now),
        )
        conn.execute(
            """
            INSERT INTO users (user_id, email, display_name, password_hash, disabled_at, created_at)
            VALUES (?, ?, ?, 'test-password', NULL, ?)
            """,
            (user_id, email, user_id, now),
        )
        conn.execute(
            """
            INSERT INTO workspace_memberships (workspace_id, user_id, role, created_at)
            VALUES (?, ?, 'member', ?)
            """,
            (workspace_id, user_id, now),
        )
    return WorkbenchUser(
        user_id=user_id,
        email=email,
        display_name=user_id,
        role="member",
        workspace_id=workspace_id,
    )


class _FailingSuccessMarkRuntimeStore:
    def __init__(self, delegate):
        self._delegate = delegate
        self._fail_success_mark_once = True

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def mark_event_projection_success(self, **kwargs):
        if self._fail_success_mark_once:
            self._fail_success_mark_once = False
            raise RuntimeError("simulated_runtime_success_mark_failure")
        return self._delegate.mark_event_projection_success(**kwargs)


class _CountingCandidateTruthRuntimeStore:
    def __init__(self, delegate):
        self._delegate = delegate
        self.list_candidate_finalization_revision_calls = 0
        self.list_candidate_identity_calls = 0
        self.list_candidate_evidence_calls = 0

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)

    def list_candidate_finalization_revisions(self, **kwargs):
        self.list_candidate_finalization_revision_calls += 1
        return self._delegate.list_candidate_finalization_revisions(**kwargs)

    def list_candidate_identities(self, **kwargs):
        self.list_candidate_identity_calls += 1
        return self._delegate.list_candidate_identities(**kwargs)

    def list_candidate_evidence(self, **kwargs):
        self.list_candidate_evidence_calls += 1
        return self._delegate.list_candidate_evidence(**kwargs)


class _FailingProjectionBridge:
    def project_runtime_event(self, **_kwargs):
        raise RuntimeError("simulated_workbench_append_failure")


def _linked_projection_context(tmp_path: Path, *, runtime_run_id: str):
    runtime_store = _runtime_store_with_run(tmp_path, runtime_run_id=runtime_run_id, workbench_session_id=None)
    workbench_store, user = _workbench_store_with_user(tmp_path)
    session = workbench_store.create_workbench_session(
        user=user,
        job_title="Data Engineer",
        jd_text="Own data products.",
        notes="",
        source_kinds=["cts"],
        runtime_run_id=runtime_run_id,
    )
    runtime_store.link_workbench_session(
        runtime_run_id=runtime_run_id,
        workbench_session_id=session.session_id,
        updated_at="2026-06-17T00:00:00.500000Z",
    )
    return runtime_store, workbench_store, user, session


def _runtime_store_with_run(tmp_path: Path, *, runtime_run_id: str, workbench_session_id: str | None):
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / f"{runtime_run_id}.runtime.sqlite3")
    store.initialize()
    _create_runtime_run(store, runtime_run_id=runtime_run_id, workbench_session_id=workbench_session_id)
    return store


def _create_runtime_run(store, *, runtime_run_id: str, workbench_session_id: str | None) -> None:
    from seektalent_runtime_control.models import RuntimeRunRecord

    store.create_run(
        RuntimeRunRecord(
            runtime_run_id=runtime_run_id,
            run_intent_id=f"intent_{runtime_run_id}",
            start_idempotency_key=f"start_{runtime_run_id}",
            agent_conversation_id=f"agent_conv_{runtime_run_id}",
            workbench_session_id=workbench_session_id,
            approved_requirement_revision_id=f"reqapproved_{runtime_run_id}",
            status="running",
            current_stage="round",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-06-17T00:00:00.000000Z",
            updated_at="2026-06-17T00:00:00.000000Z",
            completed_at=None,
        )
    )


def _runtime_event(
    *,
    event_id: str,
    runtime_run_id: str,
    visibility: str,
    created_at: str,
    payload: dict[str, object] | None = None,
):
    from seektalent_runtime_control.models import RuntimeControlEventInput

    return RuntimeControlEventInput(
        event_id=event_id,
        runtime_run_id=runtime_run_id,
        event_type="runtime_round_source_result",
        stage="source_result",
        round_no=1,
        source_id="cts",
        status="completed",
        summary="CTS returned candidates.",
        payload=payload
        or {"counts": {"roundReturned": 3}, "details": {"reflectionSummary": "CTS had useful matches."}},
        schema_version="runtime-control-event/v1",
        visibility=visibility,
        idempotency_key=f"idempotency_{event_id}",
        payload_kind="compact",
        workbench_event_global_seq=None,
        created_at=created_at,
    )


def _workbench_store_with_user(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user = store.ensure_local_actor()
    return store, user


def _runtime_public_events(
    store: WorkbenchStore,
    *,
    user: WorkbenchUser,
    session_id: str,
):
    return [
        event
        for event in store.list_session_workbench_events(user=user, session_id=session_id, after_seq=0, limit=100)
        if event.schema_version == "runtime_public_event_v1"
    ]
