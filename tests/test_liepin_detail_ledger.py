from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from seektalent.providers.liepin.store import LiepinStore


TENANT = "tenant-a"
WORKSPACE = "workspace-a"
ACTOR = "actor-a"
ACCOUNT = "account-hash-a"


def test_reserve_detail_attempt_is_idempotent_and_persists_day_keys(tmp_path: Path) -> None:
    store = _store(tmp_path)

    first = store.reserve_detail_attempt(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        provider_account_hash=ACCOUNT,
        candidate_provider_id="candidate-1",
        budget_date="2026-05-07",
        provider_day_key="liepin:account-hash-a:2026-05-07",
        timezone="Asia/Shanghai",
        idempotency_key="open:candidate-1",
    )
    second = store.reserve_detail_attempt(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        provider_account_hash=ACCOUNT,
        candidate_provider_id="candidate-1",
        budget_date="2026-05-07",
        provider_day_key="liepin:account-hash-a:2026-05-07",
        timezone="Asia/Shanghai",
        idempotency_key="open:candidate-1",
    )

    assert second == first
    assert first.state == "approved_not_started"
    assert first.consumption_state == "not_consumed"
    assert first.budget_date == "2026-05-07"
    assert first.provider_day_key == "liepin:account-hash-a:2026-05-07"
    assert first.timezone == "Asia/Shanghai"


@pytest.mark.parametrize(
    ("field_name", "override"),
    [
        ("candidate_provider_id", "candidate-2"),
        ("provider_day_key", "liepin:account-hash-a:other-day"),
        ("timezone", "UTC"),
    ],
)
def test_reserve_detail_attempt_rejects_idempotency_replay_input_mismatches(
    tmp_path: Path,
    field_name: str,
    override: str,
) -> None:
    store = _store(tmp_path)
    request = {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "actor_id": ACTOR,
        "provider_account_hash": ACCOUNT,
        "candidate_provider_id": "candidate-1",
        "budget_date": "2026-05-07",
        "provider_day_key": "liepin:account-hash-a:2026-05-07",
        "timezone": "Asia/Shanghai",
        "idempotency_key": "open:candidate-1",
    }
    first = store.reserve_detail_attempt(**request)

    replay = {**request, field_name: override}
    with pytest.raises(ValueError, match="idempotency replay mismatch"):
        store.reserve_detail_attempt(**replay)

    assert store.reserve_detail_attempt(**request) == first


def test_consumed_count_resets_by_provider_day_and_counts_uncertain_consumption(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _complete(store, "candidate-1", "2026-05-07", "liepin:account-hash-a:2026-05-07", "consumed")
    _complete(store, "candidate-2", "2026-05-07", "liepin:account-hash-a:2026-05-07", "possibly_consumed")
    _complete(store, "candidate-3", "2026-05-07", "liepin:account-hash-a:2026-05-07", "unknown")
    _complete(store, "candidate-4", "2026-05-08", "liepin:account-hash-a:2026-05-08", "consumed")

    assert (
        store.count_detail_budget_consumed(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            provider_account_hash=ACCOUNT,
            provider_day_key="liepin:account-hash-a:2026-05-07",
        )
        == 3
    )
    assert (
        store.count_detail_budget_consumed(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            provider_account_hash=ACCOUNT,
            provider_day_key="liepin:account-hash-a:2026-05-08",
        )
        == 1
    )


def test_duplicate_worker_response_is_applied_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    attempt = _reserve(store, "candidate-1")
    store.transition_detail_attempt(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        attempt_id=attempt.attempt_id,
        state="started",
        consumption_state="not_consumed",
        worker_command_id="cmd-1",
    )

    completed = store.apply_detail_worker_response(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        attempt_id=attempt.attempt_id,
        worker_response_id="worker-response-1",
        state="completed",
        consumption_state="consumed",
        worker_command_id="cmd-1",
        raw_evidence_ref="artifact:detail-1",
    )
    duplicate = store.apply_detail_worker_response(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        attempt_id=attempt.attempt_id,
        worker_response_id="worker-response-1",
        state="failed_after_possible_consumption",
        consumption_state="possibly_consumed",
        worker_command_id="cmd-1",
        raw_evidence_ref="artifact:conflicting-duplicate",
    )

    assert duplicate == completed
    assert duplicate.state == "completed"
    assert duplicate.started_at is not None
    assert duplicate.completed_at is not None
    assert duplicate.worker_command_id == "cmd-1"
    assert duplicate.raw_evidence_ref == "artifact:detail-1"
    assert (
        store.count_detail_budget_consumed(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            provider_account_hash=ACCOUNT,
            provider_day_key="liepin:account-hash-a:2026-05-07",
        )
        == 1
    )


def test_duplicate_worker_response_id_rejects_different_attempt(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _reserve(store, "candidate-1")
    second = _reserve(store, "candidate-2")
    for attempt, worker_command_id in [(first, "cmd-1"), (second, "cmd-2")]:
        store.transition_detail_attempt(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            attempt_id=attempt.attempt_id,
            state="started",
            consumption_state="not_consumed",
            worker_command_id=worker_command_id,
        )

    completed = store.apply_detail_worker_response(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        attempt_id=first.attempt_id,
        worker_response_id="worker-response-reused",
        state="completed",
        consumption_state="consumed",
        worker_command_id="cmd-1",
        raw_evidence_ref="artifact:detail-1",
    )

    with pytest.raises(ValueError, match="worker response attempt mismatch"):
        store.apply_detail_worker_response(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            attempt_id=second.attempt_id,
            worker_response_id="worker-response-reused",
            state="completed",
            consumption_state="consumed",
            worker_command_id="cmd-2",
            raw_evidence_ref="artifact:detail-2",
        )

    assert (
        store.apply_detail_worker_response(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            attempt_id=first.attempt_id,
            worker_response_id="worker-response-reused",
            state="failed_after_possible_consumption",
            consumption_state="possibly_consumed",
            worker_command_id="cmd-1",
            raw_evidence_ref="artifact:conflicting-duplicate",
        )
        == completed
    )
    assert (
        store.count_detail_budget_consumed(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            provider_account_hash=ACCOUNT,
            provider_day_key="liepin:account-hash-a:2026-05-07",
        )
        == 1
    )


def test_blocked_risk_control_records_evidence_without_completion(tmp_path: Path) -> None:
    store = _store(tmp_path)
    attempt = _reserve(store, "candidate-risk")
    store.transition_detail_attempt(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        attempt_id=attempt.attempt_id,
        state="started",
        consumption_state="not_consumed",
        worker_command_id="cmd-risk",
    )

    blocked = store.transition_detail_attempt(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        attempt_id=attempt.attempt_id,
        state="blocked_by_risk_control",
        consumption_state="not_consumed",
        raw_evidence_ref="artifact:risk-control",
    )

    assert blocked.state == "blocked_by_risk_control"
    assert blocked.worker_command_id == "cmd-risk"
    assert blocked.completed_at is None
    assert blocked.raw_evidence_ref == "artifact:risk-control"


def test_failed_before_consumption_does_not_consume_budget(tmp_path: Path) -> None:
    store = _store(tmp_path)
    attempt = _reserve(store, "candidate-before")

    failed = store.transition_detail_attempt(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        attempt_id=attempt.attempt_id,
        state="failed_before_consumption",
        consumption_state="not_consumed",
        raw_evidence_ref="artifact:navigation-failed",
    )

    assert failed.state == "failed_before_consumption"
    assert failed.consumption_state == "not_consumed"
    assert (
        store.count_detail_budget_consumed(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            provider_account_hash=ACCOUNT,
            provider_day_key="liepin:account-hash-a:2026-05-07",
        )
        == 0
    )


def test_failed_after_possible_consumption_consumes_budget_conservatively(tmp_path: Path) -> None:
    store = _store(tmp_path)
    attempt = _reserve(store, "candidate-after")
    store.transition_detail_attempt(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        attempt_id=attempt.attempt_id,
        state="started",
        consumption_state="not_consumed",
        worker_command_id="cmd-after",
    )

    failed = store.transition_detail_attempt(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        attempt_id=attempt.attempt_id,
        state="failed_after_possible_consumption",
        consumption_state="possibly_consumed",
        raw_evidence_ref="artifact:browser-crash-after-dispatch",
    )

    assert failed.consumption_state == "possibly_consumed"
    assert (
        store.count_detail_budget_consumed(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            provider_account_hash=ACCOUNT,
            provider_day_key="liepin:account-hash-a:2026-05-07",
        )
        == 1
    )


def test_invalid_transition_rejects_completed_directly_from_approved_not_started(tmp_path: Path) -> None:
    store = _store(tmp_path)
    attempt = _reserve(store, "candidate-invalid")

    with pytest.raises(ValueError, match="invalid Liepin detail attempt transition"):
        store.transition_detail_attempt(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            attempt_id=attempt.attempt_id,
            state="completed",
            consumption_state="consumed",
            raw_evidence_ref="artifact:detail",
        )


@pytest.mark.parametrize("state", ["started", "provider_page_loaded", "detail_payload_seen"])
@pytest.mark.parametrize("consumption_state", ["consumed", "possibly_consumed", "unknown"])
def test_in_progress_state_rejects_budget_consuming_consumption_states(
    tmp_path: Path,
    state: str,
    consumption_state: str,
) -> None:
    store = _store(tmp_path)
    attempt = _reserve(store, f"candidate-{state}-{consumption_state}")
    if state in {"provider_page_loaded", "detail_payload_seen"}:
        store.transition_detail_attempt(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            attempt_id=attempt.attempt_id,
            state="started",
            consumption_state="not_consumed",
            worker_command_id=f"cmd-{state}",
        )
    if state == "detail_payload_seen":
        store.transition_detail_attempt(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            attempt_id=attempt.attempt_id,
            state="provider_page_loaded",
            consumption_state="not_consumed",
        )

    with pytest.raises(ValueError, match="invalid Liepin detail state/consumption pair"):
        store.transition_detail_attempt(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            attempt_id=attempt.attempt_id,
            state=state,
            consumption_state=consumption_state,
            worker_command_id=f"cmd-{state}",
        )

    assert (
        store.count_detail_budget_consumed(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            provider_account_hash=ACCOUNT,
            provider_day_key="liepin:account-hash-a:2026-05-07",
        )
        == 0
    )


def test_valid_terminal_and_conservative_consumption_pairs_still_work(tmp_path: Path) -> None:
    store = _store(tmp_path)
    valid_pairs = [
        ("candidate-completed", "completed", "consumed"),
        ("candidate-after", "failed_after_possible_consumption", "possibly_consumed"),
        ("candidate-unknown", "unknown", "unknown"),
        ("candidate-before", "failed_before_consumption", "not_consumed"),
        ("candidate-blocked", "blocked_by_risk_control", "not_consumed"),
    ]

    for candidate_provider_id, state, consumption_state in valid_pairs:
        attempt = _reserve(store, candidate_provider_id)
        store.transition_detail_attempt(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            attempt_id=attempt.attempt_id,
            state="started",
            consumption_state="not_consumed",
            worker_command_id=f"cmd-{candidate_provider_id}",
        )
        updated = store.transition_detail_attempt(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            attempt_id=attempt.attempt_id,
            state=state,
            consumption_state=consumption_state,
            raw_evidence_ref=f"artifact:{candidate_provider_id}",
        )
        assert updated.state == state
        assert updated.consumption_state == consumption_state


def test_delete_terminal_detail_attempts_preserves_active_and_current_budget_rows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    old_terminal = _reserve(store, "candidate-old-terminal")
    active = _reserve(store, "candidate-active")
    current_budget = store.reserve_detail_attempt(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        provider_account_hash=ACCOUNT,
        candidate_provider_id="candidate-current",
        budget_date="2026-06-17",
        provider_day_key="liepin:account-hash-a:2026-06-17",
        timezone="Asia/Shanghai",
        idempotency_key="open:candidate-current",
    )
    for attempt in [old_terminal, current_budget]:
        store.transition_detail_attempt(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            attempt_id=attempt.attempt_id,
            state="started",
            consumption_state="not_consumed",
            worker_command_id=f"cmd-{attempt.candidate_provider_id}",
        )
        store.apply_detail_worker_response(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            attempt_id=attempt.attempt_id,
            worker_response_id=f"worker-{attempt.candidate_provider_id}",
            state="completed",
            consumption_state="consumed",
            raw_evidence_ref=f"artifact:{attempt.candidate_provider_id}",
        )
    store.transition_detail_attempt(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        attempt_id=active.attempt_id,
        state="started",
        consumption_state="not_consumed",
        worker_command_id="cmd-active",
    )
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE liepin_detail_attempts
            SET updated_at = '2026-05-01T00:00:00Z'
            WHERE attempt_id IN (?, ?, ?)
            """,
            (old_terminal.attempt_id, active.attempt_id, current_budget.attempt_id),
        )
        conn.commit()

    dry_run = store.delete_terminal_detail_attempts(
        updated_before="2026-06-01T00:00:00Z",
        budget_date_before="2026-06-01",
        dry_run=True,
    )
    applied = store.delete_terminal_detail_attempts(
        updated_before="2026-06-01T00:00:00Z",
        budget_date_before="2026-06-01",
    )

    with sqlite3.connect(store.db_path) as conn:
        remaining_attempt_ids = {
            row[0] for row in conn.execute("SELECT attempt_id FROM liepin_detail_attempts").fetchall()
        }
        remaining_response_attempt_ids = {
            row[0] for row in conn.execute("SELECT attempt_id FROM liepin_detail_worker_responses").fetchall()
        }
    assert dry_run == 1
    assert applied == 1
    assert old_terminal.attempt_id not in remaining_attempt_ids
    assert old_terminal.attempt_id not in remaining_response_attempt_ids
    assert active.attempt_id in remaining_attempt_ids
    assert current_budget.attempt_id in remaining_attempt_ids
    assert (
        store.count_detail_budget_consumed(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            actor_id=ACTOR,
            provider_account_hash=ACCOUNT,
            provider_day_key="liepin:account-hash-a:2026-06-17",
        )
        == 1
    )


def _store(tmp_path: Path) -> LiepinStore:
    return LiepinStore(tmp_path / "liepin.sqlite3")


def _reserve(store: LiepinStore, candidate_provider_id: str):
    return store.reserve_detail_attempt(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        provider_account_hash=ACCOUNT,
        candidate_provider_id=candidate_provider_id,
        budget_date="2026-05-07",
        provider_day_key="liepin:account-hash-a:2026-05-07",
        timezone="Asia/Shanghai",
        idempotency_key=f"open:{candidate_provider_id}",
    )


def _complete(
    store: LiepinStore,
    candidate_provider_id: str,
    budget_date: str,
    provider_day_key: str,
    consumption_state: str,
) -> None:
    attempt = store.reserve_detail_attempt(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        provider_account_hash=ACCOUNT,
        candidate_provider_id=candidate_provider_id,
        budget_date=budget_date,
        provider_day_key=provider_day_key,
        timezone="Asia/Shanghai",
        idempotency_key=f"open:{candidate_provider_id}",
    )
    store.transition_detail_attempt(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        attempt_id=attempt.attempt_id,
        state="started",
        consumption_state="not_consumed",
        worker_command_id=f"cmd-{candidate_provider_id}",
    )
    if consumption_state == "consumed":
        state = "completed"
    elif consumption_state == "possibly_consumed":
        state = "failed_after_possible_consumption"
    else:
        state = "unknown"
    store.transition_detail_attempt(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        actor_id=ACTOR,
        attempt_id=attempt.attempt_id,
        state=state,
        consumption_state=consumption_state,
        raw_evidence_ref=f"artifact:{candidate_provider_id}",
    )
