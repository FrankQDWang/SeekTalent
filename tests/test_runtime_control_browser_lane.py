from __future__ import annotations

import multiprocessing
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import pytest

from seektalent.liepin_cards_source_operation import (
    LiepinCardsSourceOperationExecutor,
)
from seektalent.support_bundle import create_execution_support_bundle
from seektalent.source_port.liepin_cards_contract import (
    LiepinCardsOperationRequestV1,
)
from seektalent.source_port.liepin_details_contract import (
    LiepinDetailsOperationRequestV1,
)
from seektalent_runtime_control.browser_lane import (
    BrowserLaneBusyError,
    BrowserLaneGuard,
    LIEPIN_BROWSER_LANE,
)
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.store import (
    RUNTIME_CONTROL_SCHEMA_VERSION,
    RuntimeControlStore,
)
from tests.settings_factory import make_settings


ACQUIRED_AT = "2026-07-30T10:00:00.000000Z"
EXPIRES_AT = "2099-01-01T00:00:00.000000Z"
RELEASED_AT = "2026-07-30T10:00:01.000000Z"


def test_browser_lane_allows_only_one_process_owner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime_control.sqlite3"
    RuntimeControlStore(path).initialize()
    context = multiprocessing.get_context("spawn")
    release = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_try_hold_lane,
            args=(str(path), f"owner-{index}", release, results),
        )
        for index in range(2)
    ]
    for process in processes:
        process.start()

    outcomes = [results.get(timeout=10) for _ in processes]
    release.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert sorted(outcomes) == [False, True]
    snapshot = RuntimeControlStore(path).get_browser_lane()
    assert snapshot is not None
    assert snapshot.status == "completed"
    assert snapshot.fencing_token == 1


def test_expired_unresolved_browser_lane_forbids_takeover(
    tmp_path: Path,
) -> None:
    settings = make_settings(workspace_root=str(tmp_path))
    store = RuntimeControlStore(settings.runtime_control_path)
    store.initialize()
    first = store.try_acquire_browser_lane(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id="owner-first",
        owner_process_id=100,
        process_boot_id="process-first",
        runtime_run_id="rtrun-first",
        operation_id="operation-first",
        operation_kind="cards",
        acquired_at="2026-07-30T10:00:00.000000Z",
        lease_expires_at="2026-07-30T10:00:01.000000Z",
    )
    assert first is not None

    second = store.try_acquire_browser_lane(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id="owner-second",
        owner_process_id=200,
        process_boot_id="process-second",
        runtime_run_id="rtrun-second",
        operation_id="operation-second",
        operation_kind="details",
        acquired_at="2026-07-30T10:00:02.000000Z",
        lease_expires_at="2026-07-30T10:00:32.000000Z",
    )

    assert second is None


def test_heartbeat_loss_fences_blocked_effect_before_takeover(
    tmp_path: Path,
) -> None:
    settings = make_settings(workspace_root=str(tmp_path))
    store = RuntimeControlStore(settings.runtime_control_path)
    store.initialize()
    effect_started = threading.Event()
    effect_fenced = threading.Event()
    guard_finished = threading.Event()
    heartbeat_attempted = threading.Event()
    original_heartbeat = store.heartbeat_browser_lane

    def fail_heartbeat(**kwargs):
        heartbeat_attempted.set()
        raise RuntimeError("injected_lane_loss")

    store.heartbeat_browser_lane = fail_heartbeat  # type: ignore[method-assign]

    def run_old_effect() -> None:
        try:
            try:
                with BrowserLaneGuard(
                    store=store,
                    runtime_run_id="rtrun-old",
                    operation_id="operation-old",
                    operation_kind="cards",
                    now=lambda: "2026-07-30T10:00:00.000000Z",
                    plus_seconds=lambda _value, _seconds: (
                        "2026-07-30T10:00:00.300000Z"
                    ),
                    wait_timeout_seconds=1,
                    lease_seconds=0.15,
                    poll_interval_seconds=0.01,
                    on_lease_lost=effect_fenced.set,
                ):
                    effect_started.set()
                    assert effect_fenced.wait(timeout=2)
            except RuntimeControlError as exc:
                assert getattr(exc, "reason_code", None) == (
                    "liepin_browser_lane_heartbeat_failed"
                )
        finally:
            guard_finished.set()

    thread = threading.Thread(target=run_old_effect)
    thread.start()
    assert effect_started.wait(timeout=1)
    assert heartbeat_attempted.wait(timeout=1)
    assert effect_fenced.wait(timeout=1)
    assert guard_finished.wait(timeout=1)
    thread.join(timeout=1)

    store.heartbeat_browser_lane = original_heartbeat  # type: ignore[method-assign]
    takeover = store.try_acquire_browser_lane(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id="owner-new",
        owner_process_id=200,
        process_boot_id="process-new",
        runtime_run_id="rtrun-new",
        operation_id="operation-new",
        operation_kind="details",
        acquired_at="2026-07-30T10:00:01.000000Z",
        lease_expires_at="2026-07-30T10:00:31.000000Z",
    )
    assert takeover is None
    assert effect_fenced.is_set()
    unresolved = store.get_browser_lane()
    assert unresolved is not None
    assert unresolved.status == "active"
    assert unresolved.owner_id != "owner-new"


def test_effect_failure_remains_primary_when_lane_release_also_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = make_settings(workspace_root=str(tmp_path))
    store = RuntimeControlStore(settings.runtime_control_path)
    store.initialize()

    def release_fails(**_kwargs) -> None:
        raise OSError("PRIVATE_RELEASE_FAILURE")

    monkeypatch.setattr(store, "release_browser_lane", release_fails)

    with pytest.raises(ValueError, match="PRIMARY_EFFECT_FAILURE"):
        with BrowserLaneGuard(
            store=store,
            runtime_run_id="rtrun-primary",
            operation_id="operation-primary",
            operation_kind="cards",
            now=lambda: "2026-07-30T10:00:00.000000Z",
            plus_seconds=lambda _value, _seconds: EXPIRES_AT,
            wait_timeout_seconds=1,
            lease_seconds=30,
        ):
            raise ValueError("PRIMARY_EFFECT_FAILURE")

    failures = store.list_execution_failures()
    assert len(failures) == 1
    assert failures[0].failure_role == "secondary"
    assert failures[0].boundary == "release"
    assert failures[0].exception_type == "OSError"
    bundle = json.loads(
        create_execution_support_bundle(settings=settings).read_text(
            encoding="utf-8"
        )
    )
    assert bundle["runtimeControl"]["executionFailures"][0][
        "failure_role"
    ] == "secondary"
    assert bundle["runtimeControl"]["executionFailures"][0][
        "exception_type"
    ] == "OSError"


def test_expired_lane_requires_conclusive_reconciliation_before_release(
    tmp_path: Path,
) -> None:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    owner = context.Process(
        target=_acquire_lane_and_crash,
        args=(str(store.path), send),
    )
    owner.start()
    fencing_token = receive.recv()
    owner.join(timeout=10)
    assert owner.exitcode == 0
    lease = store.get_browser_lane()
    assert lease is not None
    assert lease.fencing_token == fencing_token
    assert lease.status == "active"
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            INSERT INTO runtime_control_source_operations (
              runtime_run_id, operation_id, source_id, operation_kind,
              canonical_request_hash, idempotency_key,
              accepted_requirement_revision_id, runtime_attempt_no,
              runtime_attempt_authority_ref, operation_phase,
              dispatch_intent_ref, conclusive_observation_ref,
              source_operation_disposition, retry_posture,
              reconciliation_revision, main_commit_ref, ledger_revision
            )
            VALUES (?, ?, 'liepin', 'cards', ?, ?, ?, 1, ?,
                    'reconciled', ?, NULL, 'reconciliation_unknown',
                    'reconcile_first', 1, NULL, 2)
            """,
            (
                "rtrun-killed",
                "operation-killed",
                "a" * 64,
                "idempotency-killed",
                "approved-killed",
                "executor-lease://rtrun-killed/1",
                "dispatch://operation-killed",
            ),
        )
        connection.execute(
            """
            INSERT INTO runtime_control_source_reconciliations (
              reconciliation_id, runtime_run_id, operation_id, source_id,
              operation_kind, canonical_request_hash, idempotency_key,
              accepted_requirement_revision_id, runtime_attempt_no,
              runtime_attempt_authority_ref, history_result_ref,
              history_result_digest, history_outcome, history_conclusion,
              decision_kind, dispatch_intent_ref,
              conclusive_observation_ref, source_operation_disposition,
              retry_posture, expected_ledger_revision,
              expected_reconciliation_revision, committed_at,
              committed_operation_phase, committed_ledger_revision,
              committed_reconciliation_revision
            )
            VALUES (
              'reconciliation-unknown', 'rtrun-killed',
              'operation-killed', 'liepin', 'cards', ?, ?,
              'approved-killed', 1, 'executor-lease://rtrun-killed/1',
              ?, ?, 'matched', 'dispatch_not_observed', 'unresolved',
              'dispatch://operation-killed', NULL,
              'reconciliation_unknown', 'reconcile_first',
              1, 0, '2026-07-30T10:00:02.000000Z',
              'reconciled', 2, 1
            )
            """,
            (
                "a" * 64,
                "idempotency-killed",
                "sha256:" + "b" * 64,
                "b" * 64,
            ),
        )

    assert store.resolve_expired_browser_lane_after_reconciliation(
        fencing_token=lease.fencing_token,
        runtime_run_id="rtrun-killed",
        operation_id="operation-killed",
        outcome="unknown",
        history_conclusion="dispatch_not_observed",
        evidence_ref="sha256:" + "b" * 64,
        evidence_digest="b" * 64,
        resolved_at="2026-07-30T10:00:02.000000Z",
    ) is False
    assert store.try_acquire_browser_lane(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id="owner-blocked",
        owner_process_id=1000,
        process_boot_id="process-blocked",
        runtime_run_id="rtrun-blocked",
        operation_id="operation-blocked",
        operation_kind="details",
        acquired_at="2026-07-30T10:00:03.000000Z",
        lease_expires_at="2026-07-30T10:00:33.000000Z",
    ) is None

    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            UPDATE runtime_control_source_operations
            SET dispatch_intent_ref = NULL,
                source_operation_disposition = NULL,
                retry_posture = 'safe_retry',
                reconciliation_revision = 2,
                ledger_revision = 3
            WHERE runtime_run_id = 'rtrun-killed'
              AND operation_id = 'operation-killed'
            """
        )
        connection.execute(
            """
            INSERT INTO runtime_control_source_reconciliations (
              reconciliation_id, runtime_run_id, operation_id, source_id,
              operation_kind, canonical_request_hash, idempotency_key,
              accepted_requirement_revision_id, runtime_attempt_no,
              runtime_attempt_authority_ref, history_result_ref,
              history_result_digest, history_outcome, history_conclusion,
              decision_kind, dispatch_intent_ref,
              conclusive_observation_ref, source_operation_disposition,
              retry_posture, expected_ledger_revision,
              expected_reconciliation_revision, committed_at,
              committed_operation_phase, committed_ledger_revision,
              committed_reconciliation_revision
            )
            VALUES (
              'reconciliation-no-effect', 'rtrun-killed',
              'operation-killed', 'liepin', 'cards', ?, ?,
              'approved-killed', 1, 'executor-lease://rtrun-killed/1',
              ?, ?, 'matched', 'accepted_no_dispatch',
              'no_dispatch_proved', NULL, NULL, NULL, 'safe_retry',
              2, 1, '2026-07-30T10:00:04.000000Z',
              'reconciled', 3, 2
            )
            """,
            (
                "a" * 64,
                "idempotency-killed",
                "sha256:" + "c" * 64,
                "c" * 64,
            ),
        )
    assert store.resolve_expired_browser_lane_after_reconciliation(
        fencing_token=lease.fencing_token,
        runtime_run_id="rtrun-killed",
        operation_id="operation-killed",
        outcome="no_effect",
        history_conclusion="accepted_no_dispatch",
        evidence_ref="sha256:" + "c" * 64,
        evidence_digest="c" * 64,
        resolved_at="2026-07-30T10:00:04.000000Z",
    ) is True
    next_lease = store.try_acquire_browser_lane(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id="owner-next",
        owner_process_id=1001,
        process_boot_id="process-next",
        runtime_run_id="rtrun-next",
        operation_id="operation-next",
        operation_kind="details",
        acquired_at="2026-07-30T10:00:05.000000Z",
        lease_expires_at="2026-07-30T10:00:35.000000Z",
    )
    assert next_lease is not None
    assert next_lease.fencing_token == lease.fencing_token + 1


def test_cards_effect_holds_and_releases_browser_lane(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
    )
    store = RuntimeControlStore(settings.runtime_control_path)
    store.initialize()
    executor = LiepinCardsSourceOperationExecutor(
        settings=settings,
        store=store,
        runtime_run_id="rtrun-cards",
        executor_id="executor-cards",
        attempt_no=1,
        accepted_requirement_revision_id="approved-cards",
        runtime_attempt_authority_ref="executor-lease://rtrun-cards/1",
    )
    observed: list[str] = []

    def execute_with_lane(
        _self: LiepinCardsSourceOperationExecutor,
        _request: LiepinCardsOperationRequestV1,
    ) -> tuple[dict[str, object], dict[str, object]]:
        snapshot = store.get_browser_lane()
        assert snapshot is not None
        observed.append(snapshot.status)
        assert snapshot.runtime_run_id == "rtrun-cards"
        assert snapshot.operation_kind == "cards"
        return {"status": "succeeded"}, {"ok": True}

    monkeypatch.setattr(
        LiepinCardsSourceOperationExecutor,
        "_execute_with_lane",
        execute_with_lane,
    )
    request = LiepinCardsOperationRequestV1(
        contract_version="seektalent.source.liepin-cards.request/v1",
        runtime_run_id="rtrun-cards",
        source_lane_run_id="lane-cards",
        query_instance_id="query-cards",
        keyword_query="Python",
        max_pages=1,
        max_cards=10,
        native_filters=None,
    )

    executor._execute(request)  # noqa: SLF001

    assert observed == ["active"]
    snapshot = store.get_browser_lane()
    assert snapshot is not None
    assert snapshot.status == "completed"
    assert snapshot.lease_expires_at is None


def test_cards_reconciliation_unknown_keeps_browser_lane_fenced(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = make_settings(workspace_root=str(tmp_path))
    store = RuntimeControlStore(settings.runtime_control_path)
    store.initialize()
    executor = LiepinCardsSourceOperationExecutor(
        settings=settings,
        store=store,
        runtime_run_id="rtrun-cards-unknown",
        executor_id="executor-cards-unknown",
        attempt_no=1,
        accepted_requirement_revision_id="approved-cards-unknown",
        runtime_attempt_authority_ref=(
            "executor-lease://rtrun-cards-unknown/1"
        ),
    )
    monkeypatch.setattr(
        LiepinCardsSourceOperationExecutor,
        "_execute_with_lane",
        lambda *_args: (
            {
                "status": "failed",
                "safe_reason_code": (
                    "liepin_cards_reconciliation_unknown"
                ),
            },
            {
                "ok": False,
                "safe_reason_code": (
                    "liepin_cards_reconciliation_unknown"
                ),
            },
        ),
    )

    executor._execute(  # noqa: SLF001
        LiepinCardsOperationRequestV1(
            contract_version=(
                "seektalent.source.liepin-cards.request/v1"
            ),
            runtime_run_id="rtrun-cards-unknown",
            source_lane_run_id="lane-cards-unknown",
            query_instance_id="query-cards-unknown",
            keyword_query="Python",
            max_pages=1,
            max_cards=10,
            native_filters=None,
        )
    )

    lane = store.get_browser_lane()
    assert lane is not None
    assert lane.status == "active"
    assert lane.last_failure_code == (
        "liepin_cards_reconciliation_unknown"
    )
    assert store.try_acquire_browser_lane(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id="owner-after-unknown",
        owner_process_id=200,
        process_boot_id="process-after-unknown",
        runtime_run_id="rtrun-after-unknown",
        operation_id="operation-after-unknown",
        operation_kind="cards",
        acquired_at="2099-01-01T00:00:01.000000Z",
        lease_expires_at="2099-01-01T00:00:31.000000Z",
    ) is None


def test_details_reconciliation_unknown_keeps_browser_lane_fenced(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = make_settings(workspace_root=str(tmp_path))
    store = RuntimeControlStore(settings.runtime_control_path)
    store.initialize()
    executor = LiepinCardsSourceOperationExecutor(
        settings=settings,
        store=store,
        runtime_run_id="rtrun-details-unknown",
        executor_id="executor-details-unknown",
        attempt_no=1,
        accepted_requirement_revision_id="approved-details-unknown",
        runtime_attempt_authority_ref=(
            "executor-lease://rtrun-details-unknown/1"
        ),
    )
    monkeypatch.setattr(
        LiepinCardsSourceOperationExecutor,
        "_execute_details_with_lane",
        lambda *_args: (
            {
                "status": "failed",
                "safe_reason_code": (
                    "liepin_details_reconciliation_unknown"
                ),
            },
            {
                "ok": False,
                "safe_reason_code": (
                    "liepin_details_reconciliation_unknown"
                ),
            },
        ),
    )

    executor._execute_details(  # noqa: SLF001
        LiepinDetailsOperationRequestV1(
            contract_version=(
                "seektalent.source.liepin-details.request/v1"
            ),
            runtime_run_id="rtrun-details-unknown",
            source_lane_run_id="lane-details-unknown",
            query_instance_id="query-details-unknown",
            card_ref="candidate-details-unknown",
            rank=1,
            open_mode="resolve_locator",
            expected_provider_candidate_key_hash="a" * 64,
        )
    )

    lane = store.get_browser_lane()
    assert lane is not None
    assert lane.status == "active"
    assert lane.last_failure_code == (
        "liepin_details_reconciliation_unknown"
    )


def test_default_production_lane_contention_yields_promptly(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_mode="prod",
    )
    store = RuntimeControlStore(settings.runtime_control_path)
    store.initialize()
    assert store.try_acquire_browser_lane(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id="owner-contention",
        owner_process_id=100,
        process_boot_id="process-contention",
        runtime_run_id="rtrun-contention",
        operation_id="operation-contention",
        operation_kind="cards",
        acquired_at="2026-07-30T00:00:00Z",
        lease_expires_at="2099-01-01T00:00:00Z",
    ) is not None
    executor = LiepinCardsSourceOperationExecutor(
        settings=settings,
        store=store,
        runtime_run_id="rtrun-waiting",
        executor_id="executor-waiting",
        attempt_no=1,
        accepted_requirement_revision_id="approved-waiting",
        runtime_attempt_authority_ref="authority-waiting",
    )
    request = LiepinCardsOperationRequestV1(
        contract_version=(
            "seektalent.source.liepin-cards.request/v1"
        ),
        runtime_run_id="rtrun-waiting",
        source_lane_run_id="lane-waiting",
        query_instance_id="query-waiting",
        keyword_query="Python",
        max_pages=1,
        max_cards=10,
        native_filters=None,
    )

    started = time.monotonic()
    with pytest.raises(BrowserLaneBusyError):
        executor._execute(request)  # noqa: SLF001

    assert time.monotonic() - started < 1


def test_runtime_control_schema_includes_browser_lane(
    tmp_path: Path,
) -> None:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    with _sqlite(store.path) as connection:
        version = int(
            connection.execute("PRAGMA user_version").fetchone()[0]
        )
        table = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
              AND name = 'runtime_control_browser_lanes'
            """
        ).fetchone()
    assert version == RUNTIME_CONTROL_SCHEMA_VERSION
    assert table is not None


def _try_hold_lane(
    path: str,
    owner_id: str,
    release,
    results,
) -> None:
    store = RuntimeControlStore(path)
    lease = store.try_acquire_browser_lane(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id=owner_id,
        owner_process_id=multiprocessing.current_process().pid or 1,
        process_boot_id=f"process-{owner_id}",
        runtime_run_id=f"rtrun-{owner_id}",
        operation_id=f"operation-{owner_id}",
        operation_kind="cards",
        acquired_at=ACQUIRED_AT,
        lease_expires_at=EXPIRES_AT,
    )
    results.put(lease is not None)
    if lease is None:
        return
    release.wait(timeout=10)
    store.release_browser_lane(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id=lease.owner_id,
        fencing_token=lease.fencing_token,
        released_at=RELEASED_AT,
        status="completed",
    )


def _acquire_lane_and_crash(path: str, send) -> None:
    store = RuntimeControlStore(path)
    lease = store.try_acquire_browser_lane(
        lane_key=LIEPIN_BROWSER_LANE,
        owner_id="owner-killed",
        owner_process_id=multiprocessing.current_process().pid or 1,
        process_boot_id="process-killed",
        runtime_run_id="rtrun-killed",
        operation_id="operation-killed",
        operation_kind="cards",
        acquired_at="2026-07-30T10:00:00.000000Z",
        lease_expires_at="2026-07-30T10:00:01.000000Z",
    )
    if lease is None:
        raise AssertionError("child failed to acquire browser lane")
    send.send(lease.fencing_token)
    send.close()
    os._exit(0)


def _sqlite(path: Path):
    import sqlite3

    return sqlite3.connect(path)
