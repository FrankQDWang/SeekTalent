from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from seektalent_runtime_control.checkpoint_v2 import checkpoint_projection
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeCheckpoint
from seektalent_runtime_control.store import RuntimeControlStore
from tests.test_runtime_control_checkpoint_v2 import (
    _seed_running_store,
    _state_with_round_and_finalization,
)
from tests.test_runtime_multi_source_round_dispatch import _run_state
from tests.settings_factory import make_settings


_RUN_ID = "runtime_run_1"
_EXECUTOR_ID = "executor-1"
_ROUND_NO = 1
_ROUND_PLAN_REF = "liepin-round-work-plan://sha256/" + ("f" * 64)
_ROUND_PLAN_HASH = "f" * 64
_LANES = (
    ("runtime_run_1:source:liepin:lane:a", "query-a"),
    ("runtime_run_1:source:liepin:lane:b", "query-b"),
)


def test_duplicate_barrier_bind_counts_attempt_without_duplicate_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seektalent.liepin_cards_source_operation import (
        LiepinCardsSourceOperationExecutor,
    )

    store = _seed_running_store(tmp_path)
    monkeypatch.setattr(
        "seektalent.liepin_cards_source_operation._now",
        lambda: "2026-07-28T00:00:02.000000Z",
    )
    store.write_checkpoint_v2(
        checkpoint_id="checkpoint-before-controller",
        runtime_run_id=_RUN_ID,
        executor_id=_EXECUTOR_ID,
        attempt_no=1,
        stage="round",
        round_no=_ROUND_NO,
        safe_boundary="before_round_controller",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=checkpoint_projection(_run_state()),
        detail_claim_revision=0,
        detail_claim_hash=None,
        continuation_cursor={
            "nextPhase": "rounds",
            "completedRounds": 0,
            "stopReason": "max_rounds_reached",
        },
        created_at="2026-07-28T00:00:01.000000Z",
    )
    executor = LiepinCardsSourceOperationExecutor(
        settings=make_settings(
            workspace_root=str(tmp_path),
            runtime_control_path=str(store.path),
        ),
        store=store,
        runtime_run_id=_RUN_ID,
        executor_id=_EXECUTOR_ID,
        attempt_no=1,
        accepted_requirement_revision_id="approved-1",
        runtime_attempt_authority_ref=(
            "executor-lease://runtime_run_1/1"
        ),
    )

    executor.bind_round_work_barrier(
        round_no=_ROUND_NO,
        lanes=_LANES,
        work_plan_artifact_ref=_ROUND_PLAN_REF,
        work_plan_artifact_hash=_ROUND_PLAN_HASH,
    )
    first_evidence = executor.step_resource_evidence()
    executor.bind_round_work_barrier(
        round_no=_ROUND_NO,
        lanes=_LANES,
        work_plan_artifact_ref=_ROUND_PLAN_REF,
        work_plan_artifact_hash=_ROUND_PLAN_HASH,
    )
    second_evidence = executor.step_resource_evidence()

    assert first_evidence["barrierBindAttemptCount"] == 1
    assert first_evidence["barrierCommittedWriteCount"] == 1
    assert first_evidence["barrierCommittedLaneCount"] == 2
    assert first_evidence["barrierCommittedLogicalPayloadBytes"] > 0
    assert second_evidence["barrierBindAttemptCount"] == 2
    assert (
        second_evidence["barrierCommittedWriteCount"]
        == first_evidence["barrierCommittedWriteCount"]
    )
    assert (
        second_evidence["barrierCommittedLaneCount"]
        == first_evidence["barrierCommittedLaneCount"]
    )
    assert (
        second_evidence["barrierCommittedLogicalPayloadBytes"]
        == first_evidence["barrierCommittedLogicalPayloadBytes"]
    )


def test_two_lanes_advance_exact_detail_transition_across_cold_restart(
    tmp_path: Path,
) -> None:
    store = _seed_two_lane_round(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = tuple(
            pool.map(
                lambda lane: _accept_detail_operation(store, lane),
                _LANES,
            )
        )

    assert {item.operation.operation_id for item in accepted} == {
        _detail_operation_id(lane) for lane in _LANES
    }

    reopened = RuntimeControlStore(store.path)
    reopened.initialize()
    recoverable = reopened.get_latest_recoverable_checkpoint(
        runtime_run_id=_RUN_ID
    )
    chains = reopened.get_active_workflow_transition_chains(
        runtime_run_id=_RUN_ID
    )

    assert isinstance(recoverable, RuntimeCheckpoint)
    assert recoverable.checkpoint_id == "checkpoint-before-controller"
    assert len(chains) == 2
    assert {
        (
            chain[-1].source_lane_run_id,
            chain[-1].query_instance_id,
            chain[-1].continuation["operationId"],
            chain[-1].continuation["dispatchAuthorizationOrdinal"],
        )
        for chain in chains
    } == {
        (source_lane_run_id, query_instance_id, _detail_operation_id(lane), 1)
        for lane in _LANES
        for source_lane_run_id, query_instance_id in (lane,)
    }


def test_barrier_with_no_started_lanes_is_recoverable_as_exact_pending_work(
    tmp_path: Path,
) -> None:
    store = _seed_pending_two_lane_round(tmp_path)

    reopened = RuntimeControlStore(store.path)
    reopened.initialize()
    recoverable = reopened.get_latest_recoverable_checkpoint(
        runtime_run_id=_RUN_ID
    )
    resumes = reopened.get_workflow_round_resume_lanes(
        runtime_run_id=_RUN_ID,
        base_checkpoint_id="checkpoint-before-controller",
    )

    assert recoverable.checkpoint_id == "checkpoint-before-controller"
    assert tuple(
        (
            item.source_lane_run_id,
            item.query_instance_id,
            item.barrier_status,
            item.transitions,
        )
        for item in resumes
    ) == tuple((*lane, "pending", ()) for lane in _LANES)


def test_barrier_with_one_queued_lane_preserves_it_and_returns_other_pending(
    tmp_path: Path,
) -> None:
    store = _seed_pending_two_lane_round(tmp_path)
    _write_source_dispatch_transition(store, _LANES[0])

    reopened = RuntimeControlStore(store.path)
    reopened.initialize()
    recoverable = reopened.get_latest_recoverable_checkpoint(
        runtime_run_id=_RUN_ID
    )
    resumes = reopened.get_workflow_round_resume_lanes(
        runtime_run_id=_RUN_ID,
        base_checkpoint_id="checkpoint-before-controller",
    )

    assert recoverable.checkpoint_id == "checkpoint-before-controller"
    assert resumes[0].barrier_status == "active"
    assert [item.step_kind for item in resumes[0].transitions] == [
        "source_dispatch"
    ]
    assert resumes[1].barrier_status == "pending"
    assert resumes[1].transitions == ()


def test_checkpoint_waits_for_every_barrier_lane_and_settles_atomically(
    tmp_path: Path,
) -> None:
    store = _seed_two_lane_round(tmp_path)
    for lane in _LANES:
        _accept_detail_operation(store, lane)
        _observe_detail_operation(store, lane)

    _complete_lane_transition(store, _LANES[0])

    with pytest.raises(
        RuntimeControlError,
        match="runtime_workflow_round_barrier_unsettled",
    ):
        _write_after_round_checkpoint(
            store,
            checkpoint_id="checkpoint-before-last-lane",
        )

    assert store.get_latest_checkpoint(
        runtime_run_id=_RUN_ID
    ).checkpoint_id == "checkpoint-before-controller"
    assert store.get_workflow_round_barrier_lanes(
        runtime_run_id=_RUN_ID,
        round_no=_ROUND_NO,
    ) == (
        (*_LANES[0], "completed"),
        (*_LANES[1], "active"),
    )
    assert store.get_active_workflow_transition(
        runtime_run_id=_RUN_ID,
        source_lane_run_id=_LANES[0][0],
        query_instance_id=_LANES[0][1],
    ).step_kind == "lane_completed"

    _complete_lane_transition(store, _LANES[1])
    checkpoint = _write_after_round_checkpoint(
        store,
        checkpoint_id="checkpoint-after-all-lanes",
    )

    assert checkpoint.checkpoint_id == "checkpoint-after-all-lanes"
    assert store.get_active_workflow_transition_chains(
        runtime_run_id=_RUN_ID
    ) == ()
    with store._connect() as connection:
        barrier_status = connection.execute(
            """
            SELECT status
            FROM runtime_control_workflow_round_barriers
            WHERE runtime_run_id = ? AND round_no = ?
            """,
            (_RUN_ID, _ROUND_NO),
        ).fetchone()["status"]
        transition_statuses = connection.execute(
            """
            SELECT source_lane_run_id, query_instance_id, status
            FROM runtime_control_workflow_transitions
            WHERE runtime_run_id = ? AND step_kind = 'lane_completed'
            ORDER BY source_lane_run_id, query_instance_id
            """,
            (_RUN_ID,),
        ).fetchall()
    assert barrier_status == "checkpointed"
    assert [row["status"] for row in transition_statuses] == [
        "checkpointed",
        "checkpointed",
    ]


def _seed_two_lane_round(tmp_path: Path) -> RuntimeControlStore:
    store = _seed_pending_two_lane_round(tmp_path)
    for lane in _LANES:
        _write_source_dispatch_transition(store, lane)
        source_lane_run_id, query_instance_id = lane
        store.write_workflow_transition(
            runtime_run_id=_RUN_ID,
            source_lane_run_id=source_lane_run_id,
            query_instance_id=query_instance_id,
            executor_id=_EXECUTOR_ID,
            attempt_no=1,
            round_no=_ROUND_NO,
            step_kind="detail_queued",
            continuation={
                "schemaVersion": "runtime-detail-queued-continuation/v1",
                "operationId": _detail_operation_id(lane),
                "requestHash": _request_hash(lane, prefix="2"),
                "requestArtifactRef": (
                    f"artifact://detail-request/{query_instance_id}"
                ),
                "workPlanArtifactRef": (
                    f"artifact://detail-work-plan/{query_instance_id}"
                ),
                "workPlanHash": _request_hash(lane, prefix="3"),
                "workPlanPhase": "captures",
                "detailCursor": 0,
                "detailCompletedHighWatermark": -1,
                "cardsArtifactRef": (
                    f"artifact://cards-result/{query_instance_id}"
                ),
            },
            artifact_refs=(
                _ROUND_PLAN_REF,
                f"artifact://cards-result/{query_instance_id}",
                f"artifact://detail-request/{query_instance_id}",
                f"artifact://detail-work-plan/{query_instance_id}",
            ),
            source_operation_ids=(),
            created_at="2026-07-28T00:00:04.000000Z",
        )
    return store


def _seed_pending_two_lane_round(tmp_path: Path) -> RuntimeControlStore:
    store = _seed_running_store(tmp_path)
    store.write_checkpoint_v2(
        checkpoint_id="checkpoint-before-controller",
        runtime_run_id=_RUN_ID,
        executor_id=_EXECUTOR_ID,
        attempt_no=1,
        stage="round",
        round_no=_ROUND_NO,
        safe_boundary="before_round_controller",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=checkpoint_projection(_run_state()),
        detail_claim_revision=0,
        detail_claim_hash=None,
        continuation_cursor={
            "nextPhase": "rounds",
            "completedRounds": 0,
            "stopReason": "max_rounds_reached",
        },
        created_at="2026-07-28T00:00:01.000000Z",
    )
    store.open_workflow_round_barrier(
        runtime_run_id=_RUN_ID,
        executor_id=_EXECUTOR_ID,
        attempt_no=1,
        round_no=_ROUND_NO,
        lanes=_LANES,
        work_plan_artifact_ref=_ROUND_PLAN_REF,
        work_plan_artifact_hash=_ROUND_PLAN_HASH,
        created_at="2026-07-28T00:00:02.000000Z",
    )
    return store


def _write_source_dispatch_transition(
    store: RuntimeControlStore,
    lane: tuple[str, str],
) -> None:
    source_lane_run_id, query_instance_id = lane
    store.write_workflow_transition(
        runtime_run_id=_RUN_ID,
        source_lane_run_id=source_lane_run_id,
        query_instance_id=query_instance_id,
        executor_id=_EXECUTOR_ID,
        attempt_no=1,
        round_no=_ROUND_NO,
        step_kind="source_dispatch",
        continuation={
            "schemaVersion": "runtime-source-dispatch-continuation/v1",
            "operationId": f"cards-{query_instance_id}",
            "requestHash": _request_hash(lane, prefix="1"),
            "queryFingerprint": _request_hash(lane, prefix="1"),
            "roundWorkPlanArtifactRef": _ROUND_PLAN_REF,
            "roundWorkPlanArtifactHash": _ROUND_PLAN_HASH,
        },
        artifact_refs=(_ROUND_PLAN_REF,),
        source_operation_ids=(),
        created_at="2026-07-28T00:00:03.000000Z",
    )


def _accept_detail_operation(
    store: RuntimeControlStore,
    lane: tuple[str, str],
):
    source_lane_run_id, query_instance_id = lane
    operation_id = _detail_operation_id(lane)
    request_hash = _request_hash(lane, prefix="2")
    suffix = query_instance_id[-1]
    return store.accept_source_operation(
        runtime_run_id=_RUN_ID,
        operation_id=operation_id,
        source_id="liepin",
        operation_kind="details",
        canonical_request_hash=request_hash,
        idempotency_key=f"detail-idempotency-{suffix}",
        accepted_requirement_revision_id="approved-1",
        runtime_attempt_no=1,
        runtime_attempt_authority_ref=f"executor-lease://{_RUN_ID}/1",
        runtime_attempt_fence_ref=(suffix * 64),
        profile_binding_generation=1,
        browser_control_scope_id=f"browser-scope-{suffix}",
        controller_fence_ref=None,
        outbox_id=f"outbox-{operation_id}",
        dispatch_intent_id=f"dispatch-{operation_id}",
        dispatch_intent_revision=1,
        dispatch_intent_digest=(("a" if suffix == "a" else "b") * 64),
        dispatch_authorization_ordinal=1,
        source_operation_acceptance_ref=f"source-acceptance://{operation_id}/1",
        expected_ledger_revision=1,
        expected_reconciliation_revision=0,
        advance_detail_transition=True,
        transition_created_at="2026-07-28T00:00:05.000000Z",
    )


def _observe_detail_operation(
    store: RuntimeControlStore,
    lane: tuple[str, str],
) -> None:
    operation_id = _detail_operation_id(lane)
    accepted = store.get_accepted_source_operation_context(
        _RUN_ID,
        operation_id,
    )
    store.record_source_dispatch_ack(
        runtime_run_id=_RUN_ID,
        operation_id=operation_id,
        outbox_id=accepted.dispatch.outbox_id,
        canonical_request_hash=accepted.operation.canonical_request_hash,
        dispatch_intent_id=accepted.dispatch.dispatch_intent_id,
        dispatch_intent_revision=accepted.dispatch.dispatch_intent_revision,
        dispatch_intent_digest=accepted.dispatch.dispatch_intent_digest,
        dispatch_authorization_ordinal=(
            accepted.dispatch.dispatch_authorization_ordinal
        ),
        expected_outbox_revision=accepted.dispatch.outbox_revision,
        accepted_sidecar_generation=1,
        accepted_sidecar_journal_revision=1,
        ack_ref=f"source-ack://{operation_id}/1",
        ack_kind="new_logical_operation",
        acknowledged_at="2026-07-28T00:00:06.000000Z",
    )
    current = store.get_source_operation(_RUN_ID, operation_id)
    store.record_owned_source_operation_observation(
        runtime_run_id=_RUN_ID,
        operation_id=operation_id,
        executor_id=_EXECUTOR_ID,
        attempt_no=1,
        expected_ledger_revision=current.ledger_revision,
        dispatch_intent_ref=f"source-dispatch://{operation_id}/1",
        conclusive_observation_ref=f"artifact://details/{operation_id}",
        source_operation_disposition="completed",
        observed_at="2026-07-28T00:00:07.000000Z",
    )


def _complete_lane_transition(
    store: RuntimeControlStore,
    lane: tuple[str, str],
) -> None:
    source_lane_run_id, query_instance_id = lane
    operation_id = _detail_operation_id(lane)
    store.write_workflow_transition(
        runtime_run_id=_RUN_ID,
        source_lane_run_id=source_lane_run_id,
        query_instance_id=query_instance_id,
        executor_id=_EXECUTOR_ID,
        attempt_no=1,
        round_no=_ROUND_NO,
        step_kind="lane_completed",
        continuation={
            "schemaVersion": "runtime-lane-completed-continuation/v1",
            "operationId": operation_id,
            "laneResultKind": "liepin_detail_work_plan",
        },
        artifact_refs=(f"artifact://details/{operation_id}",),
        source_operation_ids=(operation_id,),
        created_at="2026-07-28T00:00:08.000000Z",
    )


def _write_after_round_checkpoint(
    store: RuntimeControlStore,
    *,
    checkpoint_id: str,
):
    return store.write_checkpoint_v2(
        checkpoint_id=checkpoint_id,
        runtime_run_id=_RUN_ID,
        executor_id=_EXECUTOR_ID,
        attempt_no=1,
        stage="round",
        round_no=_ROUND_NO,
        safe_boundary="after_round_controller",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=checkpoint_projection(_state_with_round_and_finalization()),
        detail_claim_revision=0,
        detail_claim_hash=None,
        created_at="2026-07-28T00:00:09.000000Z",
    )


def _detail_operation_id(lane: tuple[str, str]) -> str:
    return f"details-{lane[1]}"


def _request_hash(lane: tuple[str, str], *, prefix: str) -> str:
    suffix = "a" if lane[1].endswith("a") else "b"
    return (prefix + suffix) * 32
