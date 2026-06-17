from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from seektalent.runtime.public_events import make_runtime_public_event
from seektalent_runtime_control.models import RuntimeRunRecord
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_ui.artifact_repair_import import (
    import_legacy_runtime_checkpoint_for_repair,
    import_legacy_runtime_completion_for_repair,
    import_legacy_runtime_public_events_for_repair,
)
from seektalent_ui.workbench_store import WorkbenchStore, WorkbenchUser
from tests.conversation_agent_test_support import sample_requirement_sheet


def test_old_artifact_import_is_explicit_repair_only_not_prod_reconciliation(tmp_path: Path) -> None:
    store, context = _claimed_runtime_context(tmp_path)
    artifacts = SimpleNamespace(run_id="runtime_run_repair", run_dir=tmp_path / "missing")

    assert not hasattr(store, "reconcile_runtime_public_events_from_artifacts")
    assert not hasattr(store, "complete_runtime_sourcing_job_with_artifacts")
    assert not hasattr(store, "refresh_runtime_candidate_index_with_artifacts")
    with pytest.raises(PermissionError, match="operator_confirmation"):
        import_legacy_runtime_public_events_for_repair(store=store, context=context, artifacts=artifacts)


@pytest.mark.parametrize(
    "entrypoint",
    [
        import_legacy_runtime_completion_for_repair,
        import_legacy_runtime_checkpoint_for_repair,
        import_legacy_runtime_public_events_for_repair,
    ],
)
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "operator_confirmation"),
        ({"repair_confirmed": True, "runtime_mode": "prod"}, "rejected_in_prod"),
    ],
)
def test_repair_import_guards_all_entrypoints_before_workbench_mutation(
    entrypoint,
    kwargs: dict[str, object],
    message: str,
) -> None:
    store = _MutationTrapStore()

    with pytest.raises(PermissionError, match=message):
        entrypoint(
            store=store,
            context=SimpleNamespace(job=SimpleNamespace(job_id="job_1"), session=SimpleNamespace(session_id="s_1")),
            artifacts=SimpleNamespace(run_id="runtime_run_repair", run_dir=Path("/tmp/missing")),
            **kwargs,
        )

    assert store.calls == []


def test_startup_does_not_import_runtime_public_events_jsonl() -> None:
    startup_source = Path("src/seektalent_ui/server.py").read_text(encoding="utf-8")
    runtime_bridge_source = Path("src/seektalent_ui/runtime_bridge.py").read_text(encoding="utf-8")

    assert "artifact_repair_import" not in startup_source
    assert "public_events.jsonl" not in startup_source
    assert "public_events.jsonl" not in runtime_bridge_source


def test_job_runner_does_not_complete_from_artifact_manifest() -> None:
    runner_source = Path("src/seektalent_ui/job_runner.py").read_text(encoding="utf-8")
    bridge_source = Path("src/seektalent_ui/runtime_bridge.py").read_text(encoding="utf-8")

    assert "complete_runtime_sourcing_job_with_artifacts" not in runner_source
    assert "complete_runtime_sourcing_job_with_artifacts" not in bridge_source
    assert "runtime_checkpoint_callback" not in bridge_source


def test_repair_import_requires_operator_flag_and_writes_runtime_control_source_metadata(tmp_path: Path) -> None:
    store, context = _claimed_runtime_context(tmp_path)
    runtime_store = _runtime_store_with_run(tmp_path, runtime_run_id="runtime_run_repair")
    artifacts = _legacy_public_event_artifacts(tmp_path, runtime_run_id="runtime_run_repair")

    with pytest.raises(PermissionError, match="operator_confirmation"):
        import_legacy_runtime_public_events_for_repair(
            store=store,
            context=context,
            artifacts=artifacts,
            runtime_mode="dev",
            runtime_control_store=runtime_store,
        )

    imported = import_legacy_runtime_public_events_for_repair(
        store=store,
        context=context,
        artifacts=artifacts,
        repair_confirmed=True,
        runtime_mode="dev",
        runtime_control_store=runtime_store,
        created_at="2026-06-17T00:00:02.000000Z",
    )

    assert imported == 1
    events = store.list_session_workbench_events(
        user=_user(),
        session_id=context.session.session_id,
        after_seq=0,
        limit=50,
    )
    assert [event.idempotency_key for event in events if event.schema_version == "runtime_public_event_v1"] == [
        "runtime_run_repair:1:source_result:cts"
    ]
    with sqlite3.connect(runtime_store.path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM runtime_control_artifact_refs").fetchone()
    assert row["runtime_run_id"] == "runtime_run_repair"
    assert row["artifact_kind"] == "legacy_runtime_public_events"
    assert row["visibility"] == "debug_repair"
    metadata = json.loads(row["metadata_json"])
    assert metadata["source"] == "artifact_repair_import"
    assert metadata["imported_count"] == 1


def test_repair_import_is_rejected_in_prod_mode_without_debug_or_repair_flag(tmp_path: Path) -> None:
    store, context = _claimed_runtime_context(tmp_path)
    artifacts = _legacy_public_event_artifacts(tmp_path, runtime_run_id="runtime_run_repair")

    with pytest.raises(PermissionError, match="rejected_in_prod"):
        import_legacy_runtime_public_events_for_repair(
            store=store,
            context=context,
            artifacts=artifacts,
            repair_confirmed=True,
            runtime_mode="prod",
        )

    imported = import_legacy_runtime_public_events_for_repair(
        store=store,
        context=context,
        artifacts=artifacts,
        repair_confirmed=True,
        runtime_mode="prod",
        allow_prod_repair=True,
    )
    assert imported == 1


def _claimed_runtime_context(tmp_path: Path):
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user = store.ensure_local_actor()
    session = store.create_workbench_session(
        user=user,
        job_title="Data Platform Engineer",
        jd_text="Own data platforms.",
        notes="",
        source_kinds=["cts"],
    )
    review = store.update_requirement_review(
        user=user,
        session_id=session.session_id,
        requirement_sheet=sample_requirement_sheet(job_title=session.job_title),
    )
    assert review is not None
    store.approve_requirement_review(user=user, session_id=session.session_id)
    started = store.start_runtime_sourcing_job(user=user, session_id=session.session_id, idempotency_key="repair")
    assert started is not None
    context = store.claim_next_runtime_sourcing_job(
        owner_id="repair-test",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    assert context is not None
    return store, context


def _runtime_store_with_run(tmp_path: Path, *, runtime_run_id: str) -> RuntimeControlStore:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id=runtime_run_id,
            agent_conversation_id=None,
            workbench_session_id=None,
            approved_requirement_revision_id="reqapproved_repair",
            status="running",
            current_stage="runtime",
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
    return store


def _legacy_public_event_artifacts(tmp_path: Path, *, runtime_run_id: str) -> SimpleNamespace:
    run_dir = tmp_path / "legacy-run"
    event_dir = run_dir / "runtime"
    event_dir.mkdir(parents=True)
    payload = make_runtime_public_event(
        runtime_run_id=runtime_run_id,
        stage="source_result",
        event_seq=1,
        round_no=1,
        source_kind="cts",
        status="completed",
        created_at="2026-06-17T00:00:01.000000Z",
        counts={
            "roundReturned": 1,
            "roundIdentities": 1,
            "sourceCumulativeReturned": 1,
            "sourceCumulativeIdentities": 1,
        },
    )
    (event_dir / "public_events.jsonl").write_text(json.dumps(payload), encoding="utf-8")
    return SimpleNamespace(run_id=runtime_run_id, run_dir=run_dir)


class _MutationTrapJobs:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def complete_runtime_sourcing_job_with_artifacts(self, **kwargs: object) -> None:
        del kwargs
        self.calls.append("complete_runtime_sourcing_job_with_artifacts")

    def refresh_runtime_candidate_index_with_artifacts(self, **kwargs: object) -> None:
        del kwargs
        self.calls.append("refresh_runtime_candidate_index_with_artifacts")


class _MutationTrapEvents:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def reconcile_runtime_public_events_from_artifacts(self, **kwargs: object) -> int:
        del kwargs
        self.calls.append("reconcile_runtime_public_events_from_artifacts")
        return 0


class _MutationTrapStore:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._jobs = _MutationTrapJobs(self.calls)
        self._events = _MutationTrapEvents(self.calls)


def _user():
    return WorkbenchUser(
        user_id="user_local",
        email="local@seektalent.local",
        display_name="Local Workbench",
        role="admin",
        workspace_id="default",
    )
