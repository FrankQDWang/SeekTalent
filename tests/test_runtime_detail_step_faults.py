from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from seektalent.config import AppSettings
from seektalent.liepin_cards_source_operation import (
    LiepinCardsSourceOperationExecutor,
)
from seektalent.source_port.authenticated_liepin_details_frames import (
    LiepinDetailsAcceptedAckV1,
    LiepinDetailsObservationV1,
    LiepinDetailsResultV1,
    ReceivedLiepinDetailsResult,
)
from seektalent.source_port.liepin_details_artifacts import (
    write_liepin_details_artifact,
)
from seektalent.source_port.liepin_details_contract import (
    LiepinDetailsArtifactV1,
    canonical_liepin_details_request_hash,
    stable_liepin_details_operation_id,
)
from seektalent_runtime_control.models import RuntimeControlEventInput
from seektalent_runtime_control.source_reconciliation import (
    SourceOperationReconciliationDecision,
)
from seektalent_runtime_control.store import RuntimeControlStore
from tests.test_liepin_cards_source_operation import (
    _details_request,
    _queue_single_detail_for_test,
)
from tests.test_runtime_control_checkpoint_v2 import _seed_running_store


class SimulatedProcessDeath(BaseException):
    pass


@pytest.mark.parametrize(
    ("fault_point", "expected_step_kind", "operation_exists"),
    (
        ("after_detail_transition_before_readiness", "detail_queued", False),
        ("before_initial_detail_accept", "detail_queued", False),
        (
            "after_initial_detail_accept_before_exchange",
            "detail_dispatch",
            True,
        ),
    ),
)
def test_initial_detail_crash_windows_resume_exact_pending_ordinal_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_point: str,
    expected_step_kind: str,
    operation_exists: bool,
) -> None:
    store = _seed_running_store(tmp_path)
    clock = {"now": "2026-07-28T00:00:05.000000Z"}
    monkeypatch.setattr(
        "seektalent.liepin_cards_source_operation._now",
        lambda: clock["now"],
    )
    executor = _executor(tmp_path, store)
    request = _details_request()
    _queue_single_detail_for_test(executor, store, request)
    monkeypatch.setattr(
        executor,
        "_ready_source_process",
        lambda: SimpleNamespace(),
    )

    def fail(point: str) -> None:
        if point == fault_point:
            raise SimulatedProcessDeath(point)

    monkeypatch.setattr(
        "seektalent.liepin_cards_source_operation._inject_detail_step_fault",
        fail,
    )
    with pytest.raises(SimulatedProcessDeath, match=fault_point):
        executor._execute_details_with_lane(request)  # noqa: SLF001

    active = _active_detail_transition(store, request)
    assert active.step_kind == expected_step_kind
    operation_id = stable_liepin_details_operation_id(request)
    if operation_exists:
        accepted = store.get_accepted_source_operation_context(
            request.runtime_run_id,
            operation_id,
        )
        assert accepted.dispatch.status == "pending"
        assert accepted.dispatch.dispatch_authorization_ordinal == 1
    else:
        with pytest.raises(Exception, match="source_operation_not_found"):
            store.get_source_operation(request.runtime_run_id, operation_id)

    restarted = _executor(tmp_path, store)
    monkeypatch.setattr(
        restarted,
        "_ready_source_process",
        lambda: SimpleNamespace(),
    )
    ordinals: list[int] = []
    monkeypatch.setattr(
        restarted,
        "_exchange_details",
        _successful_details_exchange(restarted, ordinals),
    )
    monkeypatch.setattr(
        "seektalent.liepin_cards_source_operation._inject_detail_step_fault",
        lambda _point: None,
    )

    envelope, structured = restarted.resume_detail_dispatch_transition(
        active.resume_payload()
    )

    assert envelope["status"] == "succeeded"
    assert structured["ok"] is True
    assert ordinals == [1]
    accepted = store.get_accepted_source_operation_context(
        request.runtime_run_id,
        operation_id,
    )
    assert accepted.dispatch.dispatch_authorization_ordinal == 1


def test_safe_retry_mint_crash_reuses_pending_ordinal_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _seed_running_store(tmp_path)
    clock = {"now": "2026-07-28T00:00:05.000000Z"}
    monkeypatch.setattr(
        "seektalent.liepin_cards_source_operation._now",
        lambda: clock["now"],
    )
    request = _details_request()
    first = _executor(tmp_path, store)
    _queue_single_detail_for_test(first, store, request)
    monkeypatch.setattr(
        first,
        "_ready_source_process",
        lambda: SimpleNamespace(),
    )

    def die_after_accept(point: str) -> None:
        if point == "after_initial_detail_accept_before_exchange":
            raise SimulatedProcessDeath(point)

    monkeypatch.setattr(
        "seektalent.liepin_cards_source_operation._inject_detail_step_fault",
        die_after_accept,
    )
    with pytest.raises(SimulatedProcessDeath):
        first._execute_details_with_lane(request)  # noqa: SLF001

    operation_id = stable_liepin_details_operation_id(request)
    request_hash = canonical_liepin_details_request_hash(request)
    unknown = store.record_owned_source_reconciliation_unknown(
        runtime_run_id=request.runtime_run_id,
        operation_id=operation_id,
        executor_id="executor-1",
        attempt_no=1,
        expected_ledger_revision=1,
        expected_reconciliation_revision=0,
        history_result_ref="sha256:" + ("d" * 64),
        history_result_digest="d" * 64,
        history_outcome="history_unavailable",
        history_conclusion=None,
        dispatch_intent_ref=None,
        committed_at="2026-07-28T00:00:05.500000Z",
    )
    store.yield_executor_for_automatic_source_reconciliation(
        event=RuntimeControlEventInput(
            event_id="event-detail-reconcile",
            runtime_run_id=request.runtime_run_id,
            event_type="runtime_source_reconciliation_required",
            stage="source_reconciliation",
            round_no=1,
            source_id="liepin",
            status="resume_requested",
            summary="Detail operation requires reconciliation.",
            payload={"operationId": operation_id},
            created_at="2026-07-28T00:00:06.000000Z",
        ),
        executor_id="executor-1",
        attempt_no=1,
    )
    safe_retry = store.commit_no_owner_source_reconciliation(
        SourceOperationReconciliationDecision(
            reconciliation_id="reconciliation-detail-no-effect",
            runtime_run_id=request.runtime_run_id,
            operation_id=operation_id,
            source_id="liepin",
            operation_kind="details",
            canonical_request_hash=request_hash,
            idempotency_key=(
                store.get_source_operation(
                    request.runtime_run_id,
                    operation_id,
                ).idempotency_key
            ),
            accepted_requirement_revision_id="approved-1",
            runtime_attempt_no=1,
            runtime_attempt_authority_ref=(
                first._runtime_attempt_authority_ref  # noqa: SLF001
            ),
            history_result_ref="sha256:" + ("e" * 64),
            history_result_digest="e" * 64,
            decision_kind="no_dispatch_proved",
            history_outcome="not_found",
            history_conclusion=None,
            dispatch_intent_ref=None,
            conclusive_observation_ref=None,
            source_operation_disposition=None,
            retry_posture="safe_retry",
            expected_ledger_revision=unknown.ledger_revision,
            expected_reconciliation_revision=(
                unknown.reconciliation_revision
            ),
            committed_at="2026-07-28T00:00:07.000000Z",
        )
    )
    assert safe_retry.retry_posture == "safe_retry"
    lease = store.acquire_executor_lease(
        runtime_run_id=request.runtime_run_id,
        executor_id="executor-2",
        acquired_at="2026-07-28T00:00:08.000000Z",
        lease_expires_at="2026-07-28T00:10:00.000000Z",
    )
    clock["now"] = "2026-07-28T00:00:09.000000Z"
    second = _executor(
        tmp_path,
        store,
        executor_id="executor-2",
        attempt_no=lease.attempt_no,
        authority_ref="executor-lease://runtime_run_1/2",
        profile_binding_generation=2,
    )

    def die_after_mint(point: str) -> None:
        if point == "after_safe_retry_mint_before_exchange":
            raise SimulatedProcessDeath(point)

    monkeypatch.setattr(
        "seektalent.liepin_cards_source_operation._inject_detail_step_fault",
        die_after_mint,
    )
    with pytest.raises(SimulatedProcessDeath):
        second._execute_details_with_lane(request)  # noqa: SLF001

    active = _active_detail_transition(store, request)
    assert active.step_kind == "detail_dispatch"
    assert active.continuation["dispatchAuthorizationOrdinal"] == 2
    accepted = store.get_accepted_source_operation_context(
        request.runtime_run_id,
        operation_id,
    )
    assert accepted.operation.retry_posture == "no_retry"
    assert accepted.dispatch.status == "pending"
    assert accepted.dispatch.dispatch_authorization_ordinal == 2

    restarted = _executor(
        tmp_path,
        store,
        executor_id="executor-2",
        attempt_no=lease.attempt_no,
        authority_ref="executor-lease://runtime_run_1/2",
        profile_binding_generation=2,
    )
    ordinals: list[int] = []
    monkeypatch.setattr(
        restarted,
        "_exchange_details",
        _successful_details_exchange(restarted, ordinals),
    )
    monkeypatch.setattr(
        "seektalent.liepin_cards_source_operation._inject_detail_step_fault",
        lambda _point: None,
    )

    envelope, structured = restarted.resume_detail_dispatch_transition(
        active.resume_payload()
    )

    assert envelope["status"] == "succeeded"
    assert structured["ok"] is True
    assert ordinals == [2]
    with store._connect() as connection:
        ordinals_in_store = connection.execute(
            """
            SELECT dispatch_authorization_ordinal
            FROM runtime_control_source_dispatch_outbox
            WHERE runtime_run_id = ? AND operation_id = ?
            ORDER BY dispatch_authorization_ordinal
            """,
            (request.runtime_run_id, operation_id),
        ).fetchall()
    assert [row[0] for row in ordinals_in_store] == [1, 2]


def test_ack_crash_reconciles_history_before_any_redispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _seed_running_store(tmp_path)
    monkeypatch.setattr(
        "seektalent.liepin_cards_source_operation._now",
        lambda: "2026-07-28T00:00:05.000000Z",
    )
    request = _details_request()
    first = _executor(tmp_path, store)
    _queue_single_detail_for_test(first, store, request)
    monkeypatch.setattr(
        first,
        "_ready_source_process",
        lambda: SimpleNamespace(),
    )
    dispatched: list[int] = []
    monkeypatch.setattr(
        first,
        "_exchange_details",
        _successful_details_exchange(first, dispatched),
    )

    def die_after_ack(point: str) -> None:
        if point == "after_detail_ack_before_observation":
            raise SimulatedProcessDeath(point)

    monkeypatch.setattr(
        "seektalent.liepin_cards_source_operation._inject_detail_step_fault",
        die_after_ack,
    )
    with pytest.raises(SimulatedProcessDeath):
        first._execute_details_with_lane(request)  # noqa: SLF001
    assert dispatched == [1]

    active = _active_detail_transition(store, request)
    operation_id = stable_liepin_details_operation_id(request)
    accepted = store.get_accepted_source_operation_context(
        request.runtime_run_id,
        operation_id,
    )
    assert accepted.dispatch.status == "acknowledged"
    restarted = _executor(tmp_path, store)
    blind_dispatches: list[int] = []
    monkeypatch.setattr(
        restarted,
        "_exchange_details",
        _successful_details_exchange(restarted, blind_dispatches),
    )
    monkeypatch.setattr(
        restarted,
        "_query_terminal_history",
        lambda *_args: (_ for _ in ()).throw(
            RuntimeError("history unavailable")
        ),
    )
    monkeypatch.setattr(
        "seektalent.liepin_cards_source_operation._inject_detail_step_fault",
        lambda _point: None,
    )

    envelope, structured = restarted.resume_detail_dispatch_transition(
        active.resume_payload()
    )

    assert envelope["safe_reason_code"] == (
        "liepin_details_reconciliation_unknown"
    )
    assert structured["ok"] is False
    assert blind_dispatches == []
    operation = store.get_source_operation(
        request.runtime_run_id,
        operation_id,
    )
    assert operation.operation_phase == "reconciled"
    assert operation.retry_posture == "reconcile_first"


def _executor(
    tmp_path: Path,
    store: RuntimeControlStore,
    *,
    executor_id: str = "executor-1",
    attempt_no: int = 1,
    authority_ref: str = "runtime_attempt_authority_ref_1",
    profile_binding_generation: int = 1,
) -> LiepinCardsSourceOperationExecutor:
    return LiepinCardsSourceOperationExecutor(
        settings=AppSettings(
            _env_file=None,
            workspace_root=str(tmp_path),
            runtime_control_path=str(store.path),
            liepin_browser_lane_admission_timeout_seconds=0.01,
        ),
        store=store,
        runtime_run_id="runtime_run_1",
        executor_id=executor_id,
        attempt_no=attempt_no,
        accepted_requirement_revision_id="approved-1",
        runtime_attempt_authority_ref=authority_ref,
        profile_binding_generation=profile_binding_generation,
    )


def _active_detail_transition(store, request):  # type: ignore[no-untyped-def]
    active = store.get_active_workflow_transition(
        runtime_run_id=request.runtime_run_id,
        source_lane_run_id=request.source_lane_run_id,
        query_instance_id=request.query_instance_id,
    )
    assert active is not None
    return active


def _successful_details_exchange(executor, ordinals: list[int]):  # type: ignore[no-untyped-def]
    def exchange(submit):  # type: ignore[no-untyped-def]
        request = submit.request
        identity = submit.identity
        ordinal = (
            submit.delivery.authorization.dispatch_authorization_ordinal
        )
        ordinals.append(ordinal)
        detail_url = (
            "https://h.liepin.com/resume/showresumedetail/"
            f"?res_id_encode={sha256(request.card_ref.encode()).hexdigest()[:24]}"
        )
        artifact_ref, artifact_hash = write_liepin_details_artifact(
            executor._details_artifact_root,  # noqa: SLF001
            LiepinDetailsArtifactV1(
                contract_version=(
                    "seektalent.source.liepin-details.artifact/v1"
                ),
                operation_id=identity.operation_id,
                canonical_request_hash=identity.request_hash,
                status="succeeded",
                open_mode=request.open_mode,
                provider_candidate_key_hash=(
                    request.provider_candidate_key_hash
                ),
                rank=request.rank,
                card_ref=request.card_ref,
                detail_url=detail_url,
                resume={
                    "provider_rank": request.rank,
                    "detail_payload": {
                        "candidate_name": "Synthetic Candidate",
                        "currentTitle": "Python Engineer",
                        "sourceUrl": detail_url,
                    },
                    "normalized_text": "Python engineer",
                    "page_url_hash": sha256(
                        detail_url.encode()
                    ).hexdigest(),
                    "claim_aware": True,
                    "provider_candidate_key_hash": (
                        request.provider_candidate_key_hash
                    ),
                },
                action_attempted=1,
                effect_posture="attempted",
            ),
        )
        ack = LiepinDetailsAcceptedAckV1(
            contract_version="seektalent.source.liepin-details.ack/v1",
            identity=identity,
            sidecar_generation=1,
            accepted_journal_revision=ordinal,
            ack_kind=(
                "new_logical_operation"
                if ordinal == 1
                else "new_dispatch_authorization"
            ),
            dispatch_intent_ref=(
                f"source-dispatch://{identity.operation_id}/{ordinal}"
            ),
        )
        observation = LiepinDetailsObservationV1(
            contract_version=(
                "seektalent.source.liepin-details.observation/v1"
            ),
            operation_id=identity.operation_id,
            canonical_request_hash=identity.request_hash,
            disposition="completed",
            artifact_ref=artifact_ref,
            artifact_hash=artifact_hash,
            open_mode=request.open_mode,
            provider_candidate_key_hash=(
                request.provider_candidate_key_hash
            ),
            rank=request.rank,
            action_attempted=1,
            effect_posture="attempted",
            producer_generation=1,
        )
        return ack, ReceivedLiepinDetailsResult(
            message_id=f"details-result-{identity.operation_id}-{ordinal}",
            reply_to="details-submit",
            correlation_id=identity.correlation_id,
            payload=LiepinDetailsResultV1(
                contract_version=(
                    "seektalent.source.liepin-details.result/v1"
                ),
                identity=identity,
                observation=observation,
            ),
        )

    return exchange
