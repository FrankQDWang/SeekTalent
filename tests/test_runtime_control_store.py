from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import sqlite3
from pathlib import Path

import pytest

from tests.settings_factory import make_settings


def test_settings_resolves_runtime_control_db_path_under_workspace_root(tmp_path: Path) -> None:
    settings = make_settings(workspace_root=str(tmp_path))

    assert settings.runtime_control_path == tmp_path / ".seektalent" / "runtime_control.sqlite3"


def test_store_initializes_empty_db_and_reopens_idempotently(tmp_path: Path) -> None:
    from seektalent_runtime_control.store import RUNTIME_CONTROL_SCHEMA_VERSION, RuntimeControlStore

    db_path = tmp_path / "nested" / "runtime_control.sqlite3"
    store = RuntimeControlStore(db_path)

    store.initialize()
    store.initialize()

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'runtime_%'"
            )
        }

    assert version == RUNTIME_CONTROL_SCHEMA_VERSION
    assert {
        "runtime_control_runs",
        "runtime_requirement_drafts",
        "runtime_requirement_amendments",
        "runtime_approved_requirements",
        "runtime_control_commands",
        "runtime_control_checkpoints",
        "runtime_control_executor_leases",
        "runtime_control_events",
        "runtime_control_source_operations",
        "runtime_control_source_dispatch_outbox",
        "runtime_control_snapshots",
        "runtime_control_artifact_refs",
        "runtime_control_final_summaries",
    } <= tables


def test_populated_v7_migrates_to_v8_with_readable_backup_and_reopens(tmp_path: Path) -> None:
    from seektalent_runtime_control.store import RUNTIME_CONTROL_SCHEMA_VERSION, RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    store = RuntimeControlStore(db_path)
    store.initialize()
    _accept_run(store, _queued_run("runtime_run_v7"))
    _downgrade_fixture_to_v7(db_path)

    store.initialize()
    store.initialize()

    backups = list((tmp_path / "migration_backups").glob("runtime-control-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == RUNTIME_CONTROL_SCHEMA_VERSION == 8
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_events").fetchone()[0] == 1
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        pending_index = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'idx_runtime_source_dispatch_pending'"
        ).fetchone()
    assert "runtime_control_source_operations" in tables
    assert "runtime_control_source_dispatch_outbox" in tables
    assert pending_index == (1,)

    with sqlite3.connect(f"file:{backups[0]}?mode=ro", uri=True) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 7
        assert backup.execute("SELECT COUNT(*) FROM runtime_control_runs").fetchone()[0] == 1
        assert backup.execute("SELECT COUNT(*) FROM runtime_control_events").fetchone()[0] == 1
        backup_tables = {row[0] for row in backup.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "runtime_control_source_operations" not in backup_tables
    assert "runtime_control_source_dispatch_outbox" not in backup_tables


@pytest.mark.parametrize("completed_statements", [1, 2, 3])
def test_v7_to_v8_statement_failure_rolls_back_ddl_and_user_version(
    tmp_path: Path,
    monkeypatch,
    completed_statements: int,
) -> None:
    import seektalent_runtime_control.store as store_module
    from seektalent_runtime_control.store import RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    store = RuntimeControlStore(db_path)
    store.initialize()
    _accept_run(store, _queued_run("runtime_run_v7_failure"))
    _downgrade_fixture_to_v7(db_path)
    statements = store_module._SOURCE_OPERATION_SCHEMA_STATEMENTS
    monkeypatch.setattr(
        store_module,
        "_SOURCE_OPERATION_SCHEMA_STATEMENTS",
        (*statements[:completed_statements], "CREATE TABL injected_invalid_statement"),
    )

    with pytest.raises(sqlite3.OperationalError):
        store.initialize()

    _assert_v7_without_source_schema(db_path)
    monkeypatch.setattr(store_module, "_SOURCE_OPERATION_SCHEMA_STATEMENTS", statements)
    store.initialize()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8


@pytest.mark.parametrize("completed_statements", [1, 2, 3])
def test_real_v1_to_v8_source_schema_failure_stops_at_clean_v7(
    tmp_path: Path,
    monkeypatch,
    completed_statements: int,
) -> None:
    import seektalent_runtime_control.store as store_module
    from seektalent_runtime_control.store import RuntimeControlStore
    from tests.test_runtime_control_event_contract import _create_v1_runtime_db

    db_path = tmp_path / "runtime_control_v1.sqlite3"
    _create_v1_runtime_db(db_path)
    statements = store_module._SOURCE_OPERATION_SCHEMA_STATEMENTS
    monkeypatch.setattr(
        store_module,
        "_SOURCE_OPERATION_SCHEMA_STATEMENTS",
        (*statements[:completed_statements], "CREATE TABL injected_invalid_statement"),
    )

    with pytest.raises(sqlite3.OperationalError):
        RuntimeControlStore(db_path).initialize()

    _assert_v7_without_source_schema(db_path)
    assert len(list((tmp_path / "migration_backups").glob("runtime-control-*.sqlite3"))) == 1
    monkeypatch.setattr(store_module, "_SOURCE_OPERATION_SCHEMA_STATEMENTS", statements)
    RuntimeControlStore(db_path).initialize()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_events").fetchone()[0] == 1


def test_approved_requirement_can_record_amendment_lineage_without_draft(tmp_path: Path) -> None:
    from seektalent.models import RequirementSheet
    from seektalent_runtime_control.requirements import ApprovedRequirementRevision
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    approved = ApprovedRequirementRevision(
        approved_requirement_revision_id="reqapproved_2",
        draft_revision_id=None,
        base_approved_requirement_revision_id="reqapproved_1",
        source_amendment_id="reqamend_1",
        agent_conversation_id="agent_conv_1",
        requirement_sheet=RequirementSheet(
            job_title="Python Engineer",
            title_anchor_terms=["Python"],
            title_anchor_rationale="title",
            role_summary="Build backend systems.",
            must_have_capabilities=["Python"],
            scoring_rationale="Score Python backend experience.",
        ),
        selected_item_ids=[],
        deselected_item_ids=[],
        created_at="2026-06-08T00:00:00Z",
    )

    store.save_approved_requirement(approved, idempotency_key="approved-2")

    loaded = store.get_approved_requirement("reqapproved_2")
    assert loaded.draft_revision_id is None
    assert loaded.base_approved_requirement_revision_id == "reqapproved_1"
    assert loaded.source_amendment_id == "reqamend_1"


def test_store_rejects_future_schema_version(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.store import RUNTIME_CONTROL_SCHEMA_VERSION, RuntimeControlStore

    db_path = tmp_path / "runtime_control.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"PRAGMA user_version = {RUNTIME_CONTROL_SCHEMA_VERSION + 1}")

    with pytest.raises(RuntimeControlError) as exc_info:
        RuntimeControlStore(db_path).initialize()

    assert exc_info.value.reason_code == "runtime_control_schema_unsupported"


def test_missing_runtime_run_is_lookup_error(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError, RuntimeControlLookupError
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()

    with pytest.raises(RuntimeControlLookupError) as exc_info:
        store.get_run("runtime_run_missing")

    assert isinstance(exc_info.value, RuntimeControlError)
    assert isinstance(exc_info.value, LookupError)
    assert exc_info.value.reason_code == "runtime_run_not_found"


def test_accept_run_commits_acceptance_once_and_preserves_typed_start_conflict(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    first = _queued_run("runtime_run_first", run_intent_id="intent_first", start_key="start_first")

    accepted = _accept_run(store, first)
    replayed = _accept_run(
        store,
        _queued_run("runtime_run_replay", run_intent_id="intent_first", start_key="start_first"),
    )

    assert accepted.runtime_run_id == replayed.runtime_run_id == "runtime_run_first"
    assert accepted.latest_event_seq == replayed.latest_event_seq == 1
    assert store.get_snapshot(runtime_run_id=accepted.runtime_run_id) is not None
    assert len(store.list_events(runtime_run_id=accepted.runtime_run_id, after_seq=0, limit=10).events) == 1

    claim = store.claim_next_runnable_run(
        executor_id="executor_first",
        claimed_at="2026-06-08T00:00:02Z",
        lease_expires_at="2026-06-08T00:01:02Z",
        runtime_run_id=accepted.runtime_run_id,
    )
    assert claim is not None
    replayed_starting = _accept_run(
        store,
        _queued_run("runtime_run_replay_starting", run_intent_id="intent_first", start_key="start_first"),
    )
    assert replayed_starting.status == "starting"
    assert len(store.list_events(runtime_run_id=accepted.runtime_run_id, after_seq=0, limit=10).events) == 2
    store.update_run_status(
        runtime_run_id=accepted.runtime_run_id,
        status="failed",
        updated_at="2026-06-08T00:00:03Z",
        completed_at="2026-06-08T00:00:03Z",
    )
    replayed_terminal = _accept_run(
        store,
        _queued_run("runtime_run_replay_terminal", run_intent_id="intent_first", start_key="start_first"),
    )
    assert replayed_terminal.status == "failed"
    assert len(store.list_events(runtime_run_id=accepted.runtime_run_id, after_seq=0, limit=10).events) == 2

    _accept_run(
        store,
        _queued_run("runtime_run_second", run_intent_id="intent_second", start_key="start_second"),
    )
    with pytest.raises(RuntimeControlError) as exc_info:
        _accept_run(
            store,
            _queued_run("runtime_run_conflict", run_intent_id="intent_first", start_key="start_second"),
        )

    assert exc_info.value.reason_code == "runtime_run_start_idempotency_conflict"
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_runs").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_events").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_snapshots").fetchone()[0] == 2


def test_accept_run_rejects_incomplete_initial_evidence(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError
    from seektalent_runtime_control.models import RuntimeRunSnapshot
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    run = _queued_run("runtime_run_invalid_acceptance")
    event = _queued_event(run)
    snapshot = RuntimeRunSnapshot(
        runtime_run_id=run.runtime_run_id,
        status="queued",
        current_stage="queued",
        current_round=None,
        latest_event_seq=0,
        snapshot={"workflowInput": {}},
        updated_at="2026-06-08T00:00:01Z",
    )
    invalid_evidence = (
        (event.model_copy(update={"status": "pending"}), snapshot),
        (event.model_copy(update={"idempotency_key": "wrong-key"}), snapshot),
        (event, snapshot.model_copy(update={"latest_event_seq": 1})),
    )

    for initial_event, initial_snapshot in invalid_evidence:
        with pytest.raises(RuntimeControlError) as exc_info:
            store.accept_run(run, initial_event=initial_event, snapshot=initial_snapshot)
        assert exc_info.value.reason_code == "runtime_run_acceptance_invalid"

    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_runs").fetchone()[0] == 0


def test_accept_run_rolls_back_run_event_and_snapshot_on_snapshot_failure(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlLookupError
    from seektalent_runtime_control.models import RuntimeRunSnapshot
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    run = _queued_run("runtime_run_rollback")

    with pytest.raises(TypeError):
        store.accept_run(
            run,
            initial_event=_queued_event(run),
            snapshot=RuntimeRunSnapshot(
                runtime_run_id=run.runtime_run_id,
                status="queued",
                current_stage="queued",
                current_round=None,
                latest_event_seq=0,
                snapshot={"workflowInput": {"notJsonSerializable": object()}},
                updated_at="2026-06-08T00:00:01Z",
            ),
        )

    with pytest.raises(RuntimeControlLookupError):
        store.get_run(run.runtime_run_id)
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_snapshots").fetchone()[0] == 0


def test_claim_skips_half_accepted_rows_and_claims_accepted_runs_fifo(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeRunSnapshot
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    latest_seq_zero = _queued_run("runtime_run_zero", created_at="2026-06-08T00:00:00Z")
    store.create_run(latest_seq_zero)

    missing_snapshot = _queued_run("runtime_run_no_snapshot", created_at="2026-06-08T00:00:01Z")
    store.create_run(missing_snapshot)
    store.append_event(_queued_event(missing_snapshot))

    missing_initial_event = _queued_run("runtime_run_no_initial", created_at="2026-06-08T00:00:02Z")
    store.create_run(missing_initial_event)
    store.append_event(
        _queued_event(missing_initial_event).model_copy(update={"event_type": "runtime_progress"}),
        snapshot=RuntimeRunSnapshot(
            runtime_run_id=missing_initial_event.runtime_run_id,
            status="queued",
            current_stage="queued",
            current_round=None,
            latest_event_seq=0,
            snapshot={"workflowInput": {"jobTitle": "incomplete"}},
            updated_at="2026-06-08T00:00:02Z",
        ),
    )

    first = _queued_run("runtime_run_accepted_first", created_at="2026-06-08T00:00:03Z")
    second = _queued_run("runtime_run_accepted_second", created_at="2026-06-08T00:00:04Z")
    _accept_run(store, first)
    _accept_run(store, second)

    first_claim = store.claim_next_runnable_run(
        executor_id="executor_first",
        claimed_at="2026-06-08T00:01:00Z",
        lease_expires_at="2026-06-08T00:02:00Z",
    )
    second_claim = store.claim_next_runnable_run(
        executor_id="executor_second",
        claimed_at="2026-06-08T00:01:01Z",
        lease_expires_at="2026-06-08T00:02:01Z",
    )

    assert first_claim is not None
    assert second_claim is not None
    assert first_claim.runtime_run.runtime_run_id == first.runtime_run_id
    assert second_claim.runtime_run.runtime_run_id == second.runtime_run_id
    assert (
        store.claim_next_runnable_run(
            executor_id="executor_none",
            claimed_at="2026-06-08T00:01:02Z",
            lease_expires_at="2026-06-08T00:02:02Z",
        )
        is None
    )
    assert store.get_run(latest_seq_zero.runtime_run_id).status == "queued"
    assert store.get_run(missing_snapshot.runtime_run_id).status == "queued"
    assert store.get_run(missing_initial_event.runtime_run_id).status == "queued"


def test_uncommitted_acceptance_is_invisible_then_claimable_without_wake(tmp_path: Path, monkeypatch) -> None:
    import seektalent_runtime_control.store as store_module
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    run = _queued_run("runtime_run_barrier")
    snapshot_entered = Event()
    allow_snapshot = Event()
    replace_snapshot = store_module._replace_snapshot

    def wait_before_snapshot(*args, **kwargs) -> None:
        snapshot_entered.set()
        if not allow_snapshot.wait(timeout=5):
            raise TimeoutError("snapshot barrier was not released")
        replace_snapshot(*args, **kwargs)

    monkeypatch.setattr(store_module, "_replace_snapshot", wait_before_snapshot)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_accept_run, store, run)
        assert snapshot_entered.wait(timeout=5)
        try:
            with sqlite3.connect(store.path) as reader:
                assert reader.execute("SELECT COUNT(*) FROM runtime_control_runs").fetchone()[0] == 0
                assert reader.execute("SELECT COUNT(*) FROM runtime_control_events").fetchone()[0] == 0
                assert reader.execute("SELECT COUNT(*) FROM runtime_control_snapshots").fetchone()[0] == 0
            locked_store = RuntimeControlStore(store.path, busy_timeout_ms=0)
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                locked_store.claim_next_runnable_run(
                    executor_id="executor_before_commit",
                    claimed_at="2026-06-08T00:00:02Z",
                    lease_expires_at="2026-06-08T00:01:02Z",
                )
        finally:
            allow_snapshot.set()
        accepted = future.result(timeout=5)

    claim = store.claim_next_runnable_run(
        executor_id="executor_after_commit",
        claimed_at="2026-06-08T00:00:03Z",
        lease_expires_at="2026-06-08T00:01:03Z",
    )
    assert accepted.latest_event_seq == 1
    assert claim is not None
    assert claim.runtime_run.runtime_run_id == run.runtime_run_id


def test_event_writes_are_ordered_and_gap_detected(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeControlEventInput, RuntimeRunRecord, RuntimeRunSnapshot
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    run = RuntimeRunRecord(
        runtime_run_id="runtime_run_test",
        agent_conversation_id="agent_conv_test",
        workbench_session_id=None,
        approved_requirement_revision_id="reqapproved_test",
        status="running",
        current_stage="runtime",
        current_round=1,
        latest_checkpoint_id=None,
        latest_event_seq=0,
        source_ids=["source_a"],
        stop_reason_code=None,
        created_at="2026-06-08T00:00:00Z",
        updated_at="2026-06-08T00:00:00Z",
        completed_at=None,
    )
    store.create_run(run)

    first = store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_first",
            runtime_run_id=run.runtime_run_id,
            event_type="runtime_run_started",
            stage="runtime",
            round_no=1,
            source_id=None,
            status="completed",
            summary="run started",
            payload={},
            workbench_event_global_seq=None,
            created_at="2026-06-08T00:00:01Z",
        ),
        snapshot=RuntimeRunSnapshot(
            runtime_run_id=run.runtime_run_id,
            status="running",
            current_stage="runtime",
            current_round=1,
            latest_event_seq=1,
            snapshot={"progressSummary": "run started"},
            updated_at="2026-06-08T00:00:01Z",
        ),
    )
    second = store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_second",
            runtime_run_id=run.runtime_run_id,
            event_type="runtime_checkpoint_written",
            stage="runtime",
            round_no=1,
            source_id=None,
            status="completed",
            summary="checkpoint written",
            payload={"checkpointId": "checkpoint_1"},
            workbench_event_global_seq=12,
            created_at="2026-06-08T00:00:02Z",
        )
    )

    assert first.event_seq == 1
    assert second.event_seq == 2

    page = store.list_events(runtime_run_id=run.runtime_run_id, after_seq=0, limit=100)
    assert [event.event_id for event in page.events] == ["rtevt_first", "rtevt_second"]
    assert page.next_cursor == 2
    assert page.reason_code is None

    with sqlite3.connect(tmp_path / "runtime_control.sqlite3") as conn:
        conn.execute(
            """
            INSERT INTO runtime_control_events (
                event_id, runtime_run_id, event_seq, event_type, stage, round_no,
                source_id, status, summary, payload_json, workbench_event_global_seq, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "rtevt_gap",
                run.runtime_run_id,
                4,
                "runtime_run_completed",
                "runtime",
                1,
                None,
                "completed",
                "completed after gap",
                "{}",
                None,
                "2026-06-08T00:00:04Z",
            ),
        )
        conn.execute(
            "UPDATE runtime_control_runs SET latest_event_seq = 4 WHERE runtime_run_id = ?",
            (run.runtime_run_id,),
        )

    gap_page = store.list_events(runtime_run_id=run.runtime_run_id, after_seq=2, limit=100)
    assert gap_page.events == []
    assert gap_page.next_cursor == 2
    assert gap_page.reason_code == "runtime_event_gap_detected"


def test_concurrent_event_writes_serialize_without_duplicate_event_seq(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeControlEventInput, RuntimeRunRecord
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_concurrent",
            agent_conversation_id="agent_conv_test",
            workbench_session_id=None,
            approved_requirement_revision_id="reqapproved_test",
            status="running",
            current_stage="runtime",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["source_a"],
            stop_reason_code=None,
            created_at="2026-06-08T00:00:00Z",
            updated_at="2026-06-08T00:00:00Z",
            completed_at=None,
        )
    )
    worker_count = 12
    barrier = Barrier(worker_count)

    def append(index: int) -> int:
        barrier.wait()
        event = RuntimeControlStore(tmp_path / "runtime_control.sqlite3").append_event(
            RuntimeControlEventInput(
                event_id=f"rtevt_concurrent_{index}",
                runtime_run_id="runtime_run_concurrent",
                event_type="runtime_progress",
                stage="runtime",
                round_no=1,
                source_id=None,
                status="completed",
                summary=f"progress {index}",
                payload={"index": index},
                workbench_event_global_seq=None,
                created_at=f"2026-06-08T00:00:{index:02d}Z",
            )
        )
        return event.event_seq

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        event_seqs = list(executor.map(append, range(worker_count)))

    assert sorted(event_seqs) == list(range(1, worker_count + 1))
    assert store.get_run("runtime_run_concurrent").latest_event_seq == worker_count


def test_event_write_rolls_back_insert_when_snapshot_json_fails(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeControlEventInput, RuntimeRunRecord, RuntimeRunSnapshot
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_rollback",
            agent_conversation_id="agent_conv_test",
            workbench_session_id=None,
            approved_requirement_revision_id="reqapproved_test",
            status="running",
            current_stage="runtime",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["source_a"],
            stop_reason_code=None,
            created_at="2026-06-08T00:00:00Z",
            updated_at="2026-06-08T00:00:00Z",
            completed_at=None,
        )
    )

    with pytest.raises(TypeError):
        store.append_event(
            RuntimeControlEventInput(
                event_id="rtevt_rollback",
                runtime_run_id="runtime_run_rollback",
                event_type="runtime_progress",
                stage="runtime",
                round_no=1,
                source_id=None,
                status="completed",
                summary="progress",
                payload={},
                workbench_event_global_seq=None,
                created_at="2026-06-08T00:00:01Z",
            ),
            snapshot=RuntimeRunSnapshot(
                runtime_run_id="runtime_run_rollback",
                status="running",
                current_stage="runtime",
                current_round=1,
                latest_event_seq=1,
                snapshot={"notJsonSerializable": object()},
                updated_at="2026-06-08T00:00:01Z",
            ),
        )

    assert store.get_run("runtime_run_rollback").latest_event_seq == 0
    assert store.list_events(runtime_run_id="runtime_run_rollback", after_seq=0, limit=10).events == []


def _queued_run(
    runtime_run_id: str,
    *,
    run_intent_id: str | None = None,
    start_key: str | None = None,
    created_at: str = "2026-06-08T00:00:00Z",
):
    from seektalent_runtime_control.models import RuntimeRunRecord

    return RuntimeRunRecord(
        runtime_run_id=runtime_run_id,
        run_intent_id=run_intent_id or f"intent_{runtime_run_id}",
        start_idempotency_key=start_key or f"start_{runtime_run_id}",
        run_kind="primary",
        agent_conversation_id="agent_conv_test",
        workbench_session_id=None,
        approved_requirement_revision_id="reqapproved_test",
        status="queued",
        current_stage="queued",
        current_round=None,
        latest_checkpoint_id=None,
        latest_event_seq=0,
        source_ids=["source_a"],
        stop_reason_code=None,
        created_at=created_at,
        updated_at=created_at,
        completed_at=None,
    )


def _queued_event(run):
    from seektalent_runtime_control.models import RuntimeControlEventInput

    return RuntimeControlEventInput(
        event_id=f"event_{run.runtime_run_id}",
        runtime_run_id=run.runtime_run_id,
        event_type="runtime_run_queued",
        stage="queued",
        round_no=None,
        source_id=None,
        status="queued",
        summary="workflow run queued",
        payload={"runIntentId": run.run_intent_id},
        idempotency_key=f"runtime-run-queued:{run.runtime_run_id}",
        workbench_event_global_seq=None,
        created_at="2026-06-08T00:00:01Z",
    )


def _accept_run(store, run):
    from seektalent_runtime_control.models import RuntimeRunSnapshot

    return store.accept_run(
        run,
        initial_event=_queued_event(run),
        snapshot=RuntimeRunSnapshot(
            runtime_run_id=run.runtime_run_id,
            status="queued",
            current_stage="queued",
            current_round=None,
            latest_event_seq=0,
            snapshot={"workflowInput": {"jobTitle": "Backend Engineer", "sourceIds": ["source_a"]}},
            updated_at="2026-06-08T00:00:01Z",
        ),
    )


def _downgrade_fixture_to_v7(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE runtime_control_source_dispatch_outbox")
        conn.execute("DROP TABLE runtime_control_source_operations")
        conn.execute("PRAGMA user_version = 7")


def _assert_v7_without_source_schema(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_runs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM runtime_control_events").fetchone()[0] == 1
    assert "runtime_control_source_operations" not in tables
    assert "runtime_control_source_dispatch_outbox" not in tables
    assert "idx_runtime_source_dispatch_pending" not in indexes
