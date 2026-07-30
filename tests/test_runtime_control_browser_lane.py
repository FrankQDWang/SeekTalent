from __future__ import annotations

import multiprocessing
from pathlib import Path

from seektalent.liepin_cards_source_operation import (
    LiepinCardsSourceOperationExecutor,
)
from seektalent.liepin_readiness_operation import (
    prepare_production_liepin_readiness,
)
from seektalent.source_port.liepin_cards_contract import (
    LiepinCardsOperationRequestV1,
)
from seektalent_runtime_control.browser_lane import (
    LIEPIN_BROWSER_LANE,
)
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


def test_expired_browser_lane_takeover_increments_fence(
    tmp_path: Path,
) -> None:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
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

    assert second is not None
    assert second.fencing_token == first.fencing_token + 1
    assert second.owner_id == "owner-second"


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


def test_prepare_readiness_holds_same_browser_lane(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import seektalent.liepin_readiness_operation as operation_module

    settings = make_settings(
        workspace_root=str(tmp_path),
    )
    store = RuntimeControlStore(settings.runtime_control_path)
    store.initialize()
    observed: list[str] = []

    def prepare(_settings) -> None:
        snapshot = store.get_browser_lane()
        assert snapshot is not None
        observed.append(snapshot.operation_kind)
        assert snapshot.status == "active"

    monkeypatch.setattr(operation_module, "_prepare_session", prepare)

    prepare_production_liepin_readiness(
        settings=settings,
        store=store,
        runtime_run_id="rtrun-readiness",
        operation_id="prepare-readiness-test",
    )

    assert observed == ["prepare_readiness"]
    snapshot = store.get_browser_lane()
    assert snapshot is not None
    assert snapshot.status == "completed"


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


def _sqlite(path: Path):
    import sqlite3

    return sqlite3.connect(path)
