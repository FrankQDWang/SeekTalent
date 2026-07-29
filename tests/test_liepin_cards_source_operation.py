from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import os
from types import SimpleNamespace

import pytest

from seektalent.config import AppSettings
from seektalent.source_port.liepin_cards_contract import (
    LiepinCardsArtifactV1,
    LiepinCardsObservationV1,
    LiepinCardsOperationRequestV1,
    canonical_liepin_cards_request_hash,
    stable_liepin_cards_operation_id,
)
from seektalent.source_port.liepin_cards_artifacts import (
    read_liepin_cards_artifact,
    write_liepin_cards_artifact,
)
from seektalent.liepin_cards_source_operation import (
    LiepinCardsSourceOperationExecutor,
    _authorization_from_acceptance,
    _spawn_sidecar,
)
from seektalent.liepin_cards_sidecar import _terminal_observation_digest
from seektalent.source_port.authenticated_liepin_cards_frames import (
    LiepinCardsAcceptedAckV1,
    LiepinCardsResultV1,
    LiepinCardsSubmitV1,
    PostHandshakeLiepinCardsSession,
    ReceivedLiepinCardsAcceptedAck,
    ReceivedLiepinCardsResult,
    ReceivedLiepinCardsSubmit,
)
from seektalent.source_port.operation_dispatch import (
    DispatchAuthorizationV1,
    InitialDeliveryV1,
    OperationIdentityV1,
    RelativeMonotonicDeadlineV1,
)
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_runtime_control.checkpoint_v2 import checkpoint_projection

from tests.test_runtime_control_source_operations import (
    _ack,
    _acceptance,
    _store_with_run,
)
from tests.test_runtime_control_checkpoint_v2 import _seed_running_store
from tests.test_runtime_multi_source_round_dispatch import _run_state


NOW = datetime(2026, 7, 28, 0, 5, tzinfo=UTC)


def _request(**updates: object) -> LiepinCardsOperationRequestV1:
    payload: dict[str, object] = {
        "contract_version": "seektalent.source.liepin-cards.request/v1",
        "runtime_run_id": "run-cards-1",
        "source_lane_run_id": "run-cards-1:source:1:liepin:round:1:lane:1",
        "query_instance_id": "query-1",
        "keyword_query": "机器学习 工程师",
        "max_pages": 1,
        "max_cards": 20,
        "native_filters": {"city": ["上海"], "experience": 5},
    }
    payload.update(updates)
    return LiepinCardsOperationRequestV1.model_validate(payload, strict=True)


def test_cards_operation_identity_and_hash_are_stable_across_delivery_attempts() -> None:
    request = _request()

    assert stable_liepin_cards_operation_id(request) == stable_liepin_cards_operation_id(request)
    assert canonical_liepin_cards_request_hash(request) == canonical_liepin_cards_request_hash(request)
    assert canonical_liepin_cards_request_hash(request) != canonical_liepin_cards_request_hash(
        _request(keyword_query="推荐系统 工程师")
    )


def test_cards_safe_retry_reuses_operation_identity_and_durable_cas_epoch() -> None:
    request = _request()
    original = _identity(request)
    identity = OperationIdentityV1(
        **{
            **original.model_dump(mode="python"),
            "attempt_no": 2,
            "runtime_attempt_fence_ref": "e" * 64,
            "profile_binding_generation": 2,
            "expected_source_operation_ledger_revision": 3,
            "expected_reconciliation_revision": 1,
        }
    )
    expected = DispatchAuthorizationV1.create_safe_retry(
        identity=identity,
        dispatch_intent_id="dispatch-cards-retry-2",
        dispatch_intent_revision=2,
        dispatch_authorization_ordinal=2,
        safe_retry_commit_ref="source-history-cards-retry-1",
        source_operation_acceptance_ref="source-acceptance-cards-1",
    )
    accepted_dispatch = SimpleNamespace(
        dispatch_intent_id=expected.dispatch_intent_id,
        dispatch_intent_revision=expected.dispatch_intent_revision,
        dispatch_authorization_ordinal=2,
        safe_retry_commit_ref=expected.safe_retry_commit_ref,
        source_operation_acceptance_ref=(
            expected.source_operation_acceptance_ref
        ),
        dispatch_intent_digest=expected.dispatch_intent_digest,
    )

    rebuilt = _authorization_from_acceptance(identity, accepted_dispatch)

    assert rebuilt == expected
    assert identity.operation_id == stable_liepin_cards_operation_id(request)


def test_cards_frames_require_authenticated_ack_before_terminal() -> None:
    request = _request()
    identity = _identity(request)
    authorization = DispatchAuthorizationV1.create_initial(
        identity=identity,
        dispatch_intent_id="dispatch-cards-1",
        dispatch_intent_revision=1,
        source_operation_acceptance_ref="source-acceptance-cards-1",
    )
    submit = LiepinCardsSubmitV1(
        contract_version="seektalent.source.liepin-cards.submit/v1",
        identity=identity,
        delivery=InitialDeliveryV1(
            delivery_mode="initial",
            authorization=authorization,
        ),
        request=request,
    )
    main, sidecar = _frame_pair()
    received_submit = sidecar.feed(
        main.encode_submit(
            message_id="submit-cards-1",
            correlation_id="correlation-cards-1",
            payload=submit,
        )
    )
    assert isinstance(received_submit[0], ReceivedLiepinCardsSubmit)
    ack = LiepinCardsAcceptedAckV1(
        contract_version="seektalent.source.liepin-cards.ack/v1",
        identity=identity,
        sidecar_generation=1,
        accepted_journal_revision=1,
        ack_kind="new_logical_operation",
        dispatch_intent_ref="source-dispatch://cards/1",
    )
    received_ack = main.feed(
        sidecar.encode_accepted_ack(
            message_id="ack-cards-1",
            reply_to="submit-cards-1",
            correlation_id="correlation-cards-1",
            payload=ack,
        )
    )
    assert isinstance(received_ack[0], ReceivedLiepinCardsAcceptedAck)
    observation = LiepinCardsObservationV1(
        contract_version="seektalent.source.liepin-cards.observation/v1",
        operation_id=identity.operation_id,
        canonical_request_hash=identity.request_hash,
        disposition="completed",
        artifact_ref="liepin-cards://sha256/" + "d" * 64,
        artifact_hash="d" * 64,
        cards_seen=3,
        card_count=3,
        producer_generation=1,
    )
    received_result = main.feed(
        sidecar.encode_result(
            message_id="result-cards-1",
            reply_to="submit-cards-1",
            correlation_id="correlation-cards-1",
            payload=LiepinCardsResultV1(
                contract_version="seektalent.source.liepin-cards.result/v1",
                identity=identity,
                observation=observation,
            ),
        )
    )
    assert isinstance(received_result[0], ReceivedLiepinCardsResult)


def test_cards_artifact_is_content_addressed_private_and_durable(
    tmp_path,
    monkeypatch,
) -> None:
    artifact = LiepinCardsArtifactV1(
        contract_version="seektalent.source.liepin-cards.artifact/v1",
        operation_id="cards-operation-1",
        canonical_request_hash="a" * 64,
        status="partial",
        cards=({"provider_candidate_key": "candidate-1"},),
        cards_seen=1,
        safe_reason_code="liepin_opencli_card_limit",
    )
    persisted_directories = []
    real_fsync = os.fsync

    def record_fsync(descriptor: int) -> None:
        if os.path.isdir(f"/dev/fd/{descriptor}"):
            persisted_directories.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", record_fsync)
    artifact_ref, artifact_hash = write_liepin_cards_artifact(
        tmp_path / "artifacts",
        artifact,
    )

    path = tmp_path / "artifacts" / f"{artifact_hash}.json"
    assert path.stat().st_mode & 0o777 == 0o600
    if os.name == "posix":
        assert persisted_directories
    assert read_liepin_cards_artifact(
        tmp_path / "artifacts",
        artifact_ref,
        expected_hash=artifact_hash,
    ) == artifact


def test_cards_terminal_journal_digest_binds_terminal_reply_not_artifact() -> None:
    terminal_reply = b'{"disposition":"failed","artifact_hash":"artifact-digest"}'

    assert _terminal_observation_digest(terminal_reply) == sha256(
        terminal_reply
    ).hexdigest()


def test_observed_history_replay_closes_stale_process_before_exact_redelivery(
    monkeypatch,
) -> None:
    executor = object.__new__(LiepinCardsSourceOperationExecutor)
    closed = []
    executor._process = SimpleNamespace(close=lambda: closed.append(True))
    request = _request()
    identity = _identity(request)
    ack = LiepinCardsAcceptedAckV1(
        contract_version="seektalent.source.liepin-cards.ack/v1",
        identity=identity,
        sidecar_generation=2,
        accepted_journal_revision=3,
        ack_kind="new_logical_operation",
        dispatch_intent_ref="source-dispatch://cards/1",
    )
    observation = LiepinCardsObservationV1(
        contract_version="seektalent.source.liepin-cards.observation/v1",
        operation_id=identity.operation_id,
        canonical_request_hash=identity.request_hash,
        disposition="completed",
        artifact_ref="liepin-cards://sha256/" + "d" * 64,
        artifact_hash="d" * 64,
        cards_seen=1,
        card_count=1,
        producer_generation=2,
    )
    terminal = ReceivedLiepinCardsResult(
        message_id="result-cards-1",
        reply_to="submit-cards-1",
        correlation_id=identity.correlation_id,
        payload=LiepinCardsResultV1(
            contract_version="seektalent.source.liepin-cards.result/v1",
            identity=identity,
            observation=observation,
        ),
    )
    monkeypatch.setattr(
        executor,
        "_exchange",
        lambda _submit: (ack, terminal),
    )

    replayed = executor._replay_observed_terminal(SimpleNamespace())

    assert replayed == (ack, terminal)
    assert closed == [True]
    assert executor._process is None


def test_cards_history_sidecar_is_authenticated_supervised_child(
    tmp_path,
    monkeypatch,
) -> None:
    settings = AppSettings(
        _env_file=None,
        workspace_root=str(tmp_path),
    )
    monkeypatch.setenv("PYTHONPATH", "/not/a/package/path")

    process = _spawn_sidecar(
        settings=settings,
        journal_path=tmp_path / "journal.sqlite3",
        artifact_root=tmp_path / "artifacts",
        history_only=True,
    )
    try:
        assert process.process.pid != os.getpid()
        assert process.process.poll() is None
        assert process.history_session is not None
        assert process.cards_session is None
    finally:
        process.close()

    assert process.process.poll() is not None
    assert (tmp_path / "journal.sqlite3").is_file()


def test_acceptance_and_outbox_rollback_together_on_crash(tmp_path) -> None:
    store = _store_with_run(tmp_path)
    kwargs = _acceptance(operation_kind="cards")

    def crash(point: str) -> None:
        if point == "after_operation_insert":
            raise RuntimeControlError("injected_cards_accept_crash")

    with pytest.raises(RuntimeControlError, match="injected_cards_accept_crash"):
        store.accept_source_operation(**kwargs, fault_injector=crash)

    with pytest.raises(Exception, match="source_operation_not_found"):
        store.get_source_operation(kwargs["runtime_run_id"], kwargs["operation_id"])
    assert store.list_pending_source_dispatches() == []


def test_checkpoint_commit_atomically_binds_cards_operation(tmp_path) -> None:
    store: RuntimeControlStore = _seed_running_store(tmp_path)
    kwargs = _acceptance(
        runtime_run_id="runtime_run_1",
        operation_kind="cards",
        accepted_requirement_revision_id="approved-1",
    )
    accepted = store.accept_source_operation(**kwargs)

    with pytest.raises(
        RuntimeControlError,
        match="source_operation_not_conclusive",
    ):
        store.write_checkpoint_v2(
            checkpoint_id="checkpoint-before-observation",
            runtime_run_id=kwargs["runtime_run_id"],
            executor_id="executor-1",
            attempt_no=1,
            stage="round",
            round_no=1,
            safe_boundary="after_round_controller",
            accepted_requirement_revision_id="approved-1",
            source_ids=["liepin"],
            projection=checkpoint_projection(_run_state()),
            detail_claim_revision=0,
            detail_claim_hash=None,
            created_at=NOW.isoformat().replace("+00:00", "Z"),
            source_operation_ids=(accepted.operation.operation_id,),
        )


def test_observed_cards_result_and_candidate_checkpoint_commit_share_boundary(
    tmp_path,
) -> None:
    store: RuntimeControlStore = _seed_running_store(tmp_path)
    kwargs = _acceptance(
        runtime_run_id="runtime_run_1",
        operation_kind="cards",
        accepted_requirement_revision_id="approved-1",
    )
    accepted = store.accept_source_operation(**kwargs)
    store.record_source_dispatch_ack(**_ack())
    observed = store.record_owned_source_operation_observation(
        runtime_run_id="runtime_run_1",
        operation_id=accepted.operation.operation_id,
        executor_id="executor-1",
        attempt_no=1,
        expected_ledger_revision=1,
        dispatch_intent_ref="sidecar-dispatch-intent://source_operation_1/1",
        conclusive_observation_ref="artifact://liepin-cards/result-sha",
        source_operation_disposition="completed",
        observed_at="2026-07-28T00:04:00.000000Z",
    )
    assert observed.operation_phase == "observed"

    checkpoint = store.write_checkpoint_v2(
        checkpoint_id="checkpoint-with-cards",
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        stage="round",
        round_no=1,
        safe_boundary="after_round_controller",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=checkpoint_projection(_run_state()),
        detail_claim_revision=0,
        detail_claim_hash=None,
        created_at=NOW.isoformat().replace("+00:00", "Z"),
        source_operation_ids=(accepted.operation.operation_id,),
    )

    committed = store.get_source_operation("runtime_run_1", accepted.operation.operation_id)
    assert committed.operation_phase == "main_committed"
    assert committed.main_commit_ref == checkpoint.checkpoint_id
    assert store.get_run("runtime_run_1").latest_checkpoint_id == checkpoint.checkpoint_id


def test_reconciliation_unknown_can_later_accept_terminal_without_retry(
    tmp_path,
) -> None:
    store: RuntimeControlStore = _seed_running_store(tmp_path)
    kwargs = _acceptance(
        runtime_run_id="runtime_run_1",
        operation_kind="cards",
        accepted_requirement_revision_id="approved-1",
    )
    accepted = store.accept_source_operation(**kwargs)
    store.record_source_dispatch_ack(**_ack())
    unknown = store.record_owned_source_reconciliation_unknown(
        runtime_run_id="runtime_run_1",
        operation_id=accepted.operation.operation_id,
        executor_id="executor-1",
        attempt_no=1,
        expected_ledger_revision=1,
        expected_reconciliation_revision=0,
        history_result_ref="sha256:" + "e" * 64,
        history_result_digest="e" * 64,
        history_outcome="matched",
        history_conclusion="dispatch_not_observed",
        dispatch_intent_ref="source-dispatch://source_operation_1/1",
        committed_at="2026-07-28T00:04:00.000000Z",
    )
    assert unknown.source_operation_disposition == "reconciliation_unknown"
    assert unknown.retry_posture == "reconcile_first"

    observed = store.record_owned_source_operation_observation(
        runtime_run_id="runtime_run_1",
        operation_id=accepted.operation.operation_id,
        executor_id="executor-1",
        attempt_no=1,
        expected_ledger_revision=unknown.ledger_revision,
        dispatch_intent_ref="source-dispatch://source_operation_1/1",
        conclusive_observation_ref="artifact://liepin-cards/late-result",
        source_operation_disposition="partial",
        observed_at="2026-07-28T00:04:30.000000Z",
    )
    assert observed.operation_phase == "observed"
    assert observed.source_operation_disposition == "partial"
    assert observed.retry_posture == "no_retry"


def test_late_stale_executor_cannot_commit_cards_observation(tmp_path) -> None:
    store: RuntimeControlStore = _seed_running_store(tmp_path)
    kwargs = _acceptance(
        runtime_run_id="runtime_run_1",
        operation_kind="cards",
        accepted_requirement_revision_id="approved-1",
    )
    accepted = store.accept_source_operation(**kwargs)
    store.record_source_dispatch_ack(**_ack())

    with pytest.raises(RuntimeControlError, match="runtime_executor_stale"):
        store.record_owned_source_operation_observation(
            runtime_run_id="runtime_run_1",
            operation_id=accepted.operation.operation_id,
            executor_id="stale-executor",
            attempt_no=1,
            expected_ledger_revision=1,
            dispatch_intent_ref="source-dispatch://source_operation_1/1",
            conclusive_observation_ref="artifact://liepin-cards/result",
            source_operation_disposition="completed",
            observed_at="2026-07-28T00:04:00.000000Z",
        )


def test_checkpoint_insert_failure_rolls_back_operation_main_commit(tmp_path) -> None:
    store: RuntimeControlStore = _seed_running_store(tmp_path)
    projection = checkpoint_projection(_run_state())
    store.write_checkpoint_v2(
        checkpoint_id="duplicate-checkpoint",
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        stage="round",
        round_no=1,
        safe_boundary="after_round_controller",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=projection,
        detail_claim_revision=0,
        detail_claim_hash=None,
        created_at="2026-07-28T00:02:00.000000Z",
    )
    kwargs = _acceptance(
        runtime_run_id="runtime_run_1",
        operation_kind="cards",
        accepted_requirement_revision_id="approved-1",
    )
    accepted = store.accept_source_operation(**kwargs)
    store.record_source_dispatch_ack(**_ack())
    observed = store.record_owned_source_operation_observation(
        runtime_run_id="runtime_run_1",
        operation_id=accepted.operation.operation_id,
        executor_id="executor-1",
        attempt_no=1,
        expected_ledger_revision=1,
        dispatch_intent_ref="source-dispatch://source_operation_1/1",
        conclusive_observation_ref="artifact://liepin-cards/result",
        source_operation_disposition="completed",
        observed_at="2026-07-28T00:04:00.000000Z",
    )

    with pytest.raises(
        RuntimeControlError,
        match="runtime_checkpoint_replay_conflict",
    ):
        store.write_checkpoint_v2(
            checkpoint_id="duplicate-checkpoint",
            runtime_run_id="runtime_run_1",
            executor_id="executor-1",
            attempt_no=1,
            stage="round",
            round_no=1,
            safe_boundary="after_round_controller",
            accepted_requirement_revision_id="approved-1",
            source_ids=["liepin"],
            projection=projection,
            detail_claim_revision=0,
            detail_claim_hash=None,
            created_at=NOW.isoformat().replace("+00:00", "Z"),
            source_operation_ids=(accepted.operation.operation_id,),
        )

    rolled_back = store.get_source_operation(
        "runtime_run_1",
        accepted.operation.operation_id,
    )
    assert rolled_back.operation_phase == "observed"
    assert rolled_back.ledger_revision == observed.ledger_revision
    assert rolled_back.main_commit_ref is None


def _identity(request: LiepinCardsOperationRequestV1) -> OperationIdentityV1:
    operation_id = stable_liepin_cards_operation_id(request)
    return OperationIdentityV1(
        run_id=request.runtime_run_id,
        operation_id=operation_id,
        attempt_no=1,
        source="liepin",
        operation_kind="cards",
        request_hash=canonical_liepin_cards_request_hash(request),
        idempotency_key=f"key-{operation_id}",
        correlation_id="correlation-cards-1",
        accepted_requirement_revision_id="reqapproved_1",
        runtime_attempt_fence_ref="c" * 64,
        profile_binding_generation=1,
        browser_control_scope_id="cards-scope-1",
        deadline=RelativeMonotonicDeadlineV1(
            value=30_000,
            clock="relative_monotonic",
            unit="milliseconds",
        ),
        expected_source_operation_ledger_revision=1,
        expected_reconciliation_revision=0,
    )


def _frame_pair():
    values = {
        "session_id": "cards-session-1",
        "protocol_minor": 0,
        "main_to_sidecar_key": b"m" * 32,
        "sidecar_to_main_key": b"s" * 32,
    }
    return (
        PostHandshakeLiepinCardsSession(role="main", **values),
        PostHandshakeLiepinCardsSession(role="sidecar", **values),
    )
