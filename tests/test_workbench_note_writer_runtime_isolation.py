from __future__ import annotations

import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from seektalent.progress import ProgressEvent
from seektalent_ui.job_runner import WorkbenchNoteWriterRunner
from seektalent_ui.workbench_store import WorkbenchUser
from tests.test_workbench_api import (
    FakeWorkbenchRuntime,
    _approve_requirement_review,
    _client,
    _create_session,
    _db_path,
    _ensure_local_actor,
    _reset_fake_runtime,
    _start_session,
    _wait_for_source_status,
    _workbench_user_from_actor_payload,
)


class BlockingWorkbenchNoteAgent:
    started = threading.Event()
    release = threading.Event()

    def run_sync(self, prompt: str) -> SimpleNamespace:
        del prompt
        type(self).started.set()
        type(self).release.wait(timeout=5)
        return SimpleNamespace(output="正在根据已确认需求整理候选人搜索进展。")


class FailingWorkbenchNoteAgent:
    called = threading.Event()

    def run_sync(self, prompt: str) -> SimpleNamespace:
        del prompt
        type(self).called.set()
        raise TimeoutError("note writer timed out")


def test_runner_coalesces_duplicate_wakes_and_restarts_after_idle() -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    pending_drained = threading.Event()
    restarted = threading.Event()
    calls: list[str] = []

    def tick(user: WorkbenchUser, session_id: str) -> None:
        del user
        calls.append(session_id)
        if len(calls) == 1:
            first_started.set()
            release_first.wait(timeout=1)
        elif session_id == "session-a":
            raise RuntimeError("isolated note failure")
        elif session_id == "session-b":
            pending_drained.set()
        elif session_id == "session-c":
            restarted.set()

    runner = WorkbenchNoteWriterRunner(tick=tick)
    user = WorkbenchUser(
        workspace_id="workspace-test",
        user_id="user-test",
        email="test@example.com",
        display_name="Test User",
        role="admin",
    )
    runner.wake(user=user, session_id="session-a")
    assert first_started.wait(timeout=1)
    for _ in range(1_000):
        runner.wake(user=user, session_id="session-a")
    runner.wake(user=user, session_id="session-b")

    release_first.set()
    assert pending_drained.wait(timeout=1)
    assert calls == ["session-a", "session-a", "session-b"]
    deadline = time.time() + 1
    while time.time() < deadline:
        with runner._lock:
            if runner._thread is None:
                break
        time.sleep(0.01)
    with runner._lock:
        assert runner._thread is None

    runner.wake(user=user, session_id="session-c")
    assert restarted.wait(timeout=1)
    assert calls == ["session-a", "session-a", "session-b", "session-c"]


def test_slow_note_writer_does_not_delay_runtime_or_terminal_commit(tmp_path: Path) -> None:
    _reset_fake_runtime()
    BlockingWorkbenchNoteAgent.started = threading.Event()
    BlockingWorkbenchNoteAgent.release = threading.Event()
    FakeWorkbenchRuntime.progress_events = [
        ProgressEvent(
            type="source_progress",
            message="CTS progress",
            timestamp="2026-07-20T00:00:00+00:00",
            round_no=1,
            payload={},
        )
    ]
    client = _client(tmp_path, note_writer_agent_factory=BlockingWorkbenchNoteAgent)
    _ensure_local_actor(client)
    session = _create_session(client, source_kinds=["cts"])
    cts_run = next(run for run in session["sourceRuns"] if run["sourceKind"] == "cts")
    _approve_requirement_review(client, session["sessionId"])

    response = _start_session(client, session["sessionId"])

    assert response.status_code == 202, response.text
    assert FakeWorkbenchRuntime.started.wait(timeout=1)
    assert BlockingWorkbenchNoteAgent.started.wait(timeout=1)
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        running = conn.execute(
            "SELECT status, attempt_count, error_message FROM runtime_sourcing_jobs WHERE session_id = ?",
            (session["sessionId"],),
        ).fetchone()
    assert running == ("running", 1, None)

    FakeWorkbenchRuntime.release.set()
    completed = _wait_for_source_status(client, session["sessionId"], cts_run["sourceRunId"], "completed")
    assert completed["status"] == "completed"
    assert not BlockingWorkbenchNoteAgent.release.is_set()
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        terminal = conn.execute(
            "SELECT status, attempt_count, error_message FROM runtime_sourcing_jobs WHERE session_id = ?",
            (session["sessionId"],),
        ).fetchone()
    assert terminal == ("completed", 1, None)
    BlockingWorkbenchNoteAgent.release.set()


def test_note_writer_timeout_evidence_does_not_change_runtime_semantics(tmp_path: Path) -> None:
    _reset_fake_runtime()
    FailingWorkbenchNoteAgent.called = threading.Event()
    FakeWorkbenchRuntime.progress_events = [
        ProgressEvent(
            type="source_progress",
            message="CTS progress",
            timestamp="2026-07-20T00:00:00+00:00",
            round_no=1,
            payload={},
        )
    ]
    client = _client(tmp_path, note_writer_agent_factory=FailingWorkbenchNoteAgent)
    _ensure_local_actor(client)
    session = _create_session(client, source_kinds=["cts"])
    cts_run = next(run for run in session["sourceRuns"] if run["sourceKind"] == "cts")
    _approve_requirement_review(client, session["sessionId"])

    response = _start_session(client, session["sessionId"])

    assert response.status_code == 202, response.text
    assert FakeWorkbenchRuntime.started.wait(timeout=1)
    assert FailingWorkbenchNoteAgent.called.wait(timeout=1)
    FakeWorkbenchRuntime.release.set()
    completed = _wait_for_source_status(client, session["sessionId"], cts_run["sourceRunId"], "completed")
    assert completed["status"] == "completed"
    deadline = time.time() + 1
    failure_events: list[dict] = []
    while time.time() < deadline:
        events = client.get(f"/api/workbench/sessions/{session['sessionId']}/events?after_seq=0").json()["events"]
        failure_events = [event for event in events if event["eventName"] == "workbench_note_writer_failed"]
        if failure_events:
            break
        time.sleep(0.02)
    assert failure_events
    assert failure_events[-1]["payload"] == {"reasonCode": "note_writer_timeout"}
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        terminal = conn.execute(
            "SELECT status, attempt_count, error_message FROM runtime_sourcing_jobs WHERE session_id = ?",
            (session["sessionId"],),
        ).fetchone()
    assert terminal == ("completed", 1, None)
    assert len(FakeWorkbenchRuntime.calls) == 1


def test_workbench_api_ignores_ambient_llm_credentials_and_makes_no_model_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_fake_runtime()
    monkeypatch.setenv("SEEKTALENT_TEXT_LLM_API_KEY", "ambient-live-key")
    model_builds = 0

    def reject_real_model(*args, **kwargs):
        nonlocal model_builds
        model_builds += 1
        raise AssertionError("ordinary Workbench API tests must not build a real model")

    monkeypatch.setattr("seektalent_ui.workbench_note_writer.build_model", reject_real_model)
    client = _client(tmp_path)
    _ensure_local_actor(client)
    session = _create_session(client, source_kinds=["cts"])
    cts_run = next(run for run in session["sourceRuns"] if run["sourceKind"] == "cts")
    _approve_requirement_review(client, session["sessionId"])

    response = _start_session(client, session["sessionId"])

    assert response.status_code == 202, response.text
    assert FakeWorkbenchRuntime.started.wait(timeout=1)
    FakeWorkbenchRuntime.release.set()
    _wait_for_source_status(client, session["sessionId"], cts_run["sourceRunId"], "completed")
    assert client.app.state.settings.text_llm_api_key is None
    assert model_builds == 0


def test_workbench_note_writer_lease_claim_release_and_expired_claim(tmp_path: Path) -> None:
    client = _client(tmp_path)
    actor_payload = _ensure_local_actor(client)
    user = _workbench_user_from_actor_payload(actor_payload)
    session = _create_session(client)
    store = client.app.state.workbench_store

    assert store.claim_workbench_note_writer_lease(
        user=user,
        session_id=session["sessionId"],
        lease_owner="worker-a",
        lease_expires_at="2026-01-01T00:01:00+00:00",
        now="2026-01-01T00:00:00+00:00",
    )
    assert not store.claim_workbench_note_writer_lease(
        user=user,
        session_id=session["sessionId"],
        lease_owner="worker-a",
        lease_expires_at="2026-01-01T00:01:10+00:00",
        now="2026-01-01T00:00:10+00:00",
    )
    assert not store.claim_workbench_note_writer_lease(
        user=user,
        session_id=session["sessionId"],
        lease_owner="worker-b",
        lease_expires_at="2026-01-01T00:01:30+00:00",
        now="2026-01-01T00:00:30+00:00",
    )
    assert store.claim_workbench_note_writer_lease(
        user=user,
        session_id=session["sessionId"],
        lease_owner="worker-b",
        lease_expires_at="2026-01-01T00:03:00+00:00",
        now="2026-01-01T00:02:00+00:00",
    )
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        row = conn.execute(
            """
            SELECT lease_expires_at, last_tick_slot, in_flight_started_at
            FROM workbench_note_writer_leases
            WHERE session_id = ?
            """,
            (session["sessionId"],),
        ).fetchone()
    assert row == ("2026-01-01T00:03:00+00:00", None, "2026-01-01T00:02:00+00:00")
    assert not store.release_workbench_note_writer_lease(
        user=user,
        session_id=session["sessionId"],
        lease_owner="worker-a",
    )
    assert store.release_workbench_note_writer_lease(
        user=user,
        session_id=session["sessionId"],
        lease_owner="worker-b",
    )
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        released = conn.execute(
            "SELECT last_tick_slot, in_flight_started_at FROM workbench_note_writer_leases"
        ).fetchone()
    assert released == (None, None)


def test_workbench_note_writer_lease_compares_iso_offsets_as_datetimes(tmp_path: Path) -> None:
    client = _client(tmp_path)
    actor_payload = _ensure_local_actor(client)
    user = _workbench_user_from_actor_payload(actor_payload)
    session = _create_session(client)
    store = client.app.state.workbench_store

    assert store.claim_workbench_note_writer_lease(
        user=user,
        session_id=session["sessionId"],
        lease_owner="worker-a",
        lease_expires_at="2026-01-01T08:01:00+08:00",
        last_tick_slot=123,
        in_flight_started_at="2026-01-01T08:00:00+08:00",
        now="2026-01-01T00:00:00Z",
    )
    assert not store.claim_workbench_note_writer_lease(
        user=user,
        session_id=session["sessionId"],
        lease_owner="worker-b",
        lease_expires_at="2026-01-01T00:01:30Z",
        now="2026-01-01T00:00:30Z",
    )
    assert store.claim_workbench_note_writer_lease(
        user=user,
        session_id=session["sessionId"],
        lease_owner="worker-b",
        lease_expires_at="2026-01-01T08:03:00+08:00",
        last_tick_slot=124,
        in_flight_started_at="2026-01-01T08:02:00+08:00",
        now="2026-01-01T00:02:00Z",
    )
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        row = conn.execute(
            """
            SELECT lease_owner, lease_expires_at, last_tick_slot, in_flight_started_at
            FROM workbench_note_writer_leases
            WHERE session_id = ?
            """,
            (session["sessionId"],),
        ).fetchone()
    assert row == ("worker-b", "2026-01-01T00:03:00+00:00", 124, "2026-01-01T00:02:00+00:00")


def test_workbench_note_writer_lease_preserves_tick_watermark_after_release(tmp_path: Path) -> None:
    client = _client(tmp_path)
    actor_payload = _ensure_local_actor(client)
    user = _workbench_user_from_actor_payload(actor_payload)
    session = _create_session(client)
    store = client.app.state.workbench_store

    assert store.claim_workbench_note_writer_lease(
        user=user,
        session_id=session["sessionId"],
        lease_owner="worker-a",
        lease_expires_at="2099-01-01T00:10:00+00:00",
        last_tick_slot=123,
        now="2099-01-01T00:00:00+00:00",
    )
    assert store.release_workbench_note_writer_lease(
        user=user,
        session_id=session["sessionId"],
        lease_owner="worker-a",
    )
    reclaim_now = datetime.now(UTC) + timedelta(seconds=1)
    reclaim_expires_at = reclaim_now + timedelta(minutes=1)
    with sqlite3.connect(_db_path(tmp_path)) as conn:
        released_expires_at = conn.execute(
            "SELECT lease_expires_at FROM workbench_note_writer_leases WHERE session_id = ?",
            (session["sessionId"],),
        ).fetchone()[0]
    assert datetime.fromisoformat(released_expires_at) <= reclaim_now

    for tick_slot in (122, 123):
        assert not store.claim_workbench_note_writer_lease(
            user=user,
            session_id=session["sessionId"],
            lease_owner="worker-b",
            lease_expires_at=reclaim_expires_at.isoformat(),
            last_tick_slot=tick_slot,
            now=reclaim_now.isoformat(),
        )
    assert store.claim_workbench_note_writer_lease(
        user=user,
        session_id=session["sessionId"],
        lease_owner="worker-b",
        lease_expires_at=reclaim_expires_at.isoformat(),
        last_tick_slot=124,
        now=reclaim_now.isoformat(),
    )
