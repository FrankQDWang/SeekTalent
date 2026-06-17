from __future__ import annotations

from pathlib import Path


def test_workbench_bridge_creates_session_link_for_runtime_run(tmp_path: Path) -> None:
    from seektalent_ui.runtime_workbench_bridge import RuntimeWorkbenchBridge

    runtime_store = _runtime_store_with_run(tmp_path, runtime_run_id="runtime_run_1", workbench_session_id=None)
    workbench_store, user = _workbench_store_with_user(tmp_path)
    bridge = RuntimeWorkbenchBridge(runtime_store=runtime_store, workbench_store=workbench_store)

    link = bridge.ensure_workbench_session_for_run(
        user=user,
        runtime_run_id="runtime_run_1",
        job_title="Python Engineer",
        jd_text="Build ranking systems.",
        notes="Remote.",
    )

    assert link.runtime_run_id == "runtime_run_1"
    assert link.workbench_session_id is not None
    assert runtime_store.get_run("runtime_run_1").workbench_session_id == link.workbench_session_id
    assert workbench_store.get_workbench_session(user=user, session_id=link.workbench_session_id) is not None


def test_project_runtime_event_to_workbench_is_idempotent_and_records_global_seq(tmp_path: Path) -> None:
    from seektalent_runtime_control.models import RuntimeControlEventInput
    from seektalent_ui.runtime_workbench_bridge import RuntimeWorkbenchBridge

    workbench_store, user = _workbench_store_with_user(tmp_path)
    session = workbench_store.create_workbench_session(
        user=user,
        job_title="Python Engineer",
        jd_text="Build ranking systems.",
        notes="Remote.",
        source_kinds=["cts"],
    )
    runtime_store = _runtime_store_with_run(
        tmp_path,
        runtime_run_id="runtime_run_1",
        workbench_session_id=session.session_id,
    )
    event = runtime_store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_source_1",
            runtime_run_id="runtime_run_1",
            event_type="runtime_round_source_result",
            stage="source_result",
            round_no=2,
            source_id="cts",
            status="completed",
            summary="CTS returned candidates.",
            payload={"counts": {"roundReturned": 3}, "details": {"reflectionSummary": "CTS had useful matches."}},
            workbench_event_global_seq=None,
            created_at="2026-06-08T00:00:01.000000Z",
        )
    )
    bridge = RuntimeWorkbenchBridge(runtime_store=runtime_store, workbench_store=workbench_store)

    first = bridge.project_runtime_event(user=user, runtime_run_id="runtime_run_1", event_id=event.event_id)
    replay = bridge.project_runtime_event(user=user, runtime_run_id="runtime_run_1", event_id=event.event_id)

    assert replay.workbench_event_global_seq == first.workbench_event_global_seq
    assert runtime_store.get_event(runtime_run_id="runtime_run_1", event_id=event.event_id).workbench_event_global_seq == first.workbench_event_global_seq
    projected = [
        item
        for item in workbench_store.list_session_workbench_events(user=user, session_id=session.session_id, after_seq=0)
        if item.idempotency_key == event.event_id
    ]
    assert len(projected) == 1


def test_workbench_bridge_reconciliation_returns_stable_reason_codes(tmp_path: Path) -> None:
    from seektalent_ui.runtime_workbench_bridge import RuntimeWorkbenchBridge

    workbench_store, user = _workbench_store_with_user(tmp_path)
    runtime_store = _runtime_store_with_run(tmp_path, runtime_run_id="runtime_run_missing", workbench_session_id=None)
    _create_runtime_run(runtime_store, runtime_run_id="runtime_run_broken", workbench_session_id="session_missing")
    bridge = RuntimeWorkbenchBridge(runtime_store=runtime_store, workbench_store=workbench_store)

    missing = bridge.reconcile_run_link(user=user, runtime_run_id="runtime_run_missing")
    broken = bridge.reconcile_run_link(user=user, runtime_run_id="runtime_run_broken")

    assert missing.reason_code == "workbench_session_missing"
    assert broken.reason_code == "runtime_link_broken"


def _runtime_store_with_run(tmp_path: Path, *, runtime_run_id: str, workbench_session_id: str | None):
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    _create_runtime_run(store, runtime_run_id=runtime_run_id, workbench_session_id=workbench_session_id)
    return store


def _create_runtime_run(store, *, runtime_run_id: str, workbench_session_id: str | None) -> None:
    from seektalent_runtime_control.models import RuntimeRunRecord

    store.create_run(
        RuntimeRunRecord(
            runtime_run_id=runtime_run_id,
            agent_conversation_id="agent_conv_1",
            workbench_session_id=workbench_session_id,
            approved_requirement_revision_id=f"reqapproved_{runtime_run_id}",
            status="running",
            current_stage="round",
            current_round=2,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-06-08T00:00:00.000000Z",
            updated_at="2026-06-08T00:00:00.000000Z",
            completed_at=None,
        )
    )


def _workbench_store_with_user(tmp_path: Path):
    from seektalent_ui.workbench_store import WorkbenchStore

    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user = store.ensure_local_actor()
    return store, user
