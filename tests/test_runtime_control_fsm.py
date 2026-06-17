from __future__ import annotations

from pathlib import Path

import pytest


def test_runtime_run_fsm_rejects_terminal_to_runnable(tmp_path: Path) -> None:
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store(tmp_path)
    _create_run(store, status="completed")

    with pytest.raises(RuntimeControlError) as exc_info:
        store.update_run_status(
            runtime_run_id="runtime_run_1",
            status="running",
            updated_at="2026-06-17T00:00:01.000000Z",
        )

    assert exc_info.value.reason_code == "runtime_run_invalid_transition"
    run = store.get_run("runtime_run_1")
    assert run.status == "completed"
    assert run.completed_at == "2026-06-17T00:00:00.000000Z"


def test_runtime_run_fsm_rejects_pause_without_running(tmp_path: Path) -> None:
    from seektalent_runtime_control.commands import RuntimeCommandService
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store(tmp_path)
    _create_run(store, status="queued")
    service = RuntimeCommandService(store=store, now=lambda: "2026-06-17T00:00:01.000000Z")

    with pytest.raises(RuntimeControlError) as exc_info:
        service.request_pause(
            runtime_run_id="runtime_run_1",
            requested_by="agent",
            idempotency_key="pause-queued",
        )

    assert exc_info.value.reason_code == "runtime_run_not_running"
    assert store.get_run("runtime_run_1").status == "queued"


def _store(tmp_path: Path):
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    return store


def _create_run(store, *, status: str) -> None:
    from seektalent_runtime_control.models import RuntimeRunRecord

    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_1",
            run_intent_id="intent_1",
            start_idempotency_key="start_1",
            run_kind="primary",
            agent_conversation_id="agent_conv_1",
            workbench_session_id=None,
            approved_requirement_revision_id="reqapproved_1",
            status=status,
            current_stage=status,
            current_round=None,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-06-17T00:00:00.000000Z",
            updated_at="2026-06-17T00:00:00.000000Z",
            completed_at="2026-06-17T00:00:00.000000Z" if status in {"cancelled", "completed", "failed"} else None,
        )
    )
