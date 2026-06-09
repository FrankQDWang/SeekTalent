from __future__ import annotations

from pathlib import Path

import pytest


def test_prepare_final_summary_rejects_active_run(tmp_path: Path) -> None:
    from seektalent_runtime_control.detail import RuntimeDetailService
    from seektalent_runtime_control.errors import RuntimeControlError

    store = _store_with_run(tmp_path, status="running")

    with pytest.raises(RuntimeControlError) as exc_info:
        RuntimeDetailService(store=store).prepare_final_summary(
            runtime_run_id="runtime_run_1",
            user_instruction=None,
            source_snapshot_event_seq=0,
            idempotency_key="summary-1",
        )

    assert exc_info.value.reason_code == "runtime_run_not_completed"


def test_prepare_final_summary_is_idempotent_and_grounded_in_terminal_snapshot(tmp_path: Path) -> None:
    from seektalent_runtime_control.detail import RuntimeDetailService
    from seektalent_runtime_control.models import RuntimeControlEventInput, RuntimeRunSnapshot

    store = _store_with_run(tmp_path, status="completed")
    event = store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_completed",
            runtime_run_id="runtime_run_1",
            event_type="runtime_run_completed",
            stage="finalization",
            round_no=None,
            source_id=None,
            status="completed",
            summary="Run completed with two candidates.",
            payload={"candidateIds": ["cand_1", "cand_2"]},
            workbench_event_global_seq=None,
            created_at="2026-06-08T00:00:01.000000Z",
        ),
        snapshot=RuntimeRunSnapshot(
            runtime_run_id="runtime_run_1",
            status="completed",
            current_stage="finalization",
            current_round=None,
            latest_event_seq=1,
            snapshot={
                "finalCandidates": [
                    {"candidateId": "cand_1", "displayName": "Alice", "rationale": "Python search experience"},
                    {"candidateId": "cand_2", "displayName": "Bob", "rationale": "Distributed systems"},
                ]
            },
            updated_at="2026-06-08T00:00:01.000000Z",
        ),
    )
    service = RuntimeDetailService(store=store, summary_id_factory=lambda: "rtfinalsummary_1")

    summary = service.prepare_final_summary(
        runtime_run_id="runtime_run_1",
        user_instruction="Focus on top candidates.",
        source_snapshot_event_seq=event.event_seq,
        idempotency_key="summary-1",
    )
    replay = service.prepare_final_summary(
        runtime_run_id="runtime_run_1",
        user_instruction="Different wording should not create a new record.",
        source_snapshot_event_seq=event.event_seq,
        idempotency_key="summary-1",
    )

    assert replay.summary_id == summary.summary_id
    assert summary.reason_code is None
    assert summary.source_event_ids == ["rtevt_completed"]
    assert summary.facts == [
        {"label": "Candidate", "value": "Alice: Python search experience"},
        {"label": "Candidate", "value": "Bob: Distributed systems"},
    ]
    assert "Focus on top candidates." in summary.summary


def test_prepare_final_summary_falls_back_to_run_status_without_final_candidates(tmp_path: Path) -> None:
    from seektalent_runtime_control.detail import RuntimeDetailService
    from seektalent_runtime_control.models import RuntimeControlEventInput, RuntimeRunSnapshot

    store = _store_with_run(tmp_path, status="completed")
    event = store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_completed_without_candidates",
            runtime_run_id="runtime_run_1",
            event_type="runtime_run_completed",
            stage="finalization",
            round_no=None,
            source_id=None,
            status="completed",
            summary="Run completed without final candidates.",
            payload={},
            workbench_event_global_seq=None,
            created_at="2026-06-08T00:00:01.000000Z",
        ),
        snapshot=RuntimeRunSnapshot(
            runtime_run_id="runtime_run_1",
            status="completed",
            current_stage="finalization",
            current_round=None,
            latest_event_seq=1,
            snapshot={},
            updated_at="2026-06-08T00:00:01.000000Z",
        ),
    )

    summary = RuntimeDetailService(store=store).prepare_final_summary(
        runtime_run_id="runtime_run_1",
        user_instruction=None,
        source_snapshot_event_seq=event.event_seq,
        idempotency_key="summary-no-candidates",
    )

    assert summary.facts == [{"label": "Run status", "value": "completed"}]
    assert "completed" in summary.summary.lower()
    assert "candidate" not in summary.summary.lower()


def test_prepare_final_summary_rejects_stale_snapshot_cursor_with_latest_cursor(tmp_path: Path) -> None:
    from seektalent_runtime_control.detail import RuntimeDetailService
    from seektalent_runtime_control.models import RuntimeControlEventInput, RuntimeRunSnapshot

    store = _store_with_run(tmp_path, status="completed")
    store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_completed",
            runtime_run_id="runtime_run_1",
            event_type="runtime_run_completed",
            stage="finalization",
            round_no=None,
            source_id=None,
            status="completed",
            summary="Run completed.",
            payload={},
            workbench_event_global_seq=None,
            created_at="2026-06-08T00:00:01.000000Z",
        ),
        snapshot=RuntimeRunSnapshot(
            runtime_run_id="runtime_run_1",
            status="completed",
            current_stage="finalization",
            current_round=None,
            latest_event_seq=1,
            snapshot={},
            updated_at="2026-06-08T00:00:01.000000Z",
        ),
    )

    summary = RuntimeDetailService(store=store).prepare_final_summary(
        runtime_run_id="runtime_run_1",
        user_instruction=None,
        source_snapshot_event_seq=0,
        idempotency_key="summary-1",
    )

    assert summary.reason_code == "runtime_snapshot_stale"
    assert summary.latest_snapshot_event_seq == 1


def _store_with_run(tmp_path: Path, *, status: str):
    from seektalent_runtime_control.models import RuntimeRunRecord
    from seektalent_runtime_control.store import RuntimeControlStore

    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_1",
            agent_conversation_id="agent_conv_1",
            workbench_session_id="workbench_session_1",
            approved_requirement_revision_id="reqapproved_1",
            status=status,
            current_stage="finalization" if status == "completed" else "round",
            current_round=None,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-06-08T00:00:00.000000Z",
            updated_at="2026-06-08T00:00:00.000000Z",
            completed_at="2026-06-08T00:00:01.000000Z" if status == "completed" else None,
        )
    )
    return store
