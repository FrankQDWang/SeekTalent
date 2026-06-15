from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
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
        "runtime_control_snapshots",
        "runtime_control_artifact_refs",
        "runtime_control_final_summaries",
    } <= tables


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
