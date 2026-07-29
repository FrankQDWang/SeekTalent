"""Liepin details Source Port hard-cut coverage."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import threading

import pytest

from seektalent.config import AppSettings
from seektalent.liepin_cards_sidecar import _serve
from seektalent.liepin_cards_source_operation import (
    LiepinCardsSourceOperationExecutor,
    _spawn_sidecar,
)
from seektalent.sidecar_handshake_protocol import SidecarReadinessError
from seektalent.source_port.authenticated_liepin_cards_frames import (
    LiepinCardsAcceptedAckV1,
    LiepinCardsResultV1,
    LiepinCardsSubmitV1,
    ReceivedLiepinCardsResult,
    ReceivedLiepinCardsSubmit,
)
from seektalent.source_port.authenticated_liepin_details_frames import (
    LiepinDetailsAcceptedAckV1,
    LiepinDetailsObservationV1,
    LiepinDetailsResultV1,
    LiepinDetailsSubmitV1,
    ReceivedLiepinDetailsAcceptedAck,
    ReceivedLiepinDetailsResult,
    ReceivedLiepinDetailsSubmit,
)
from seektalent.source_port.authenticated_liepin_source_frames import (
    LiepinSourceFrameError,
    PostHandshakeLiepinSourceSession,
)
from seektalent.source_port.liepin_cards_contract import (
    LiepinCardsObservationV1,
    LiepinCardsOperationRequestV1,
    canonical_liepin_cards_request_hash,
    stable_liepin_cards_operation_id,
)
from seektalent.source_port.liepin_details_artifacts import (
    read_liepin_details_artifact,
    write_liepin_details_artifact,
)
from seektalent.source_port.liepin_details_contract import (
    LiepinDetailsArtifactV1,
    LiepinDetailsOperationRequestV1,
    canonical_liepin_details_request_hash,
    stable_liepin_details_operation_id,
)
from seektalent.providers.liepin.liepin_details_locator_store import (
    load_liepin_detail_locator,
    remember_liepin_detail_locator,
)
from seektalent.source_port.operation_dispatch import (
    DispatchAuthorizationV1,
    InitialDeliveryV1,
    OperationIdentityV1,
    RelativeMonotonicDeadlineV1,
)
from seektalent_runtime_control.errors import RuntimeControlError
from tests.test_runtime_control_source_operations import _acceptance


_DETAIL_URL = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=70"


def _detail_hash(url: str = _DETAIL_URL) -> str:
    from seektalent.providers.liepin.liepin_site_parsing import (
        stable_liepin_detail_candidate_key_hash,
    )

    key = stable_liepin_detail_candidate_key_hash(url)
    assert key is not None
    return key


_HASH = _detail_hash()


def _request(**updates: object) -> LiepinDetailsOperationRequestV1:
    payload: dict[str, object] = {
        "contract_version": "seektalent.source.liepin-details.request/v1",
        "runtime_run_id": "run-details-1",
        "source_lane_run_id": "run-details-1:source:1:liepin:round:1:lane:1",
        "query_instance_id": "query-1",
        "card_ref": "70",
        "rank": 1,
        "open_mode": "cached_locator",
        "provider_candidate_key_hash": _HASH,
        "expected_provider_candidate_key_hash": _HASH,
    }
    payload.update(updates)
    return LiepinDetailsOperationRequestV1.model_validate(payload, strict=True)


def _resolve_request(**updates: object) -> LiepinDetailsOperationRequestV1:
    payload: dict[str, object] = {
        "contract_version": "seektalent.source.liepin-details.request/v1",
        "runtime_run_id": "run-details-1",
        "source_lane_run_id": "run-details-1:source:1:liepin:round:1:lane:1",
        "query_instance_id": "query-1",
        "card_ref": "70",
        "rank": 1,
        "open_mode": "resolve_locator",
    }
    payload.update(updates)
    return LiepinDetailsOperationRequestV1.model_validate(payload, strict=True)


def _cards_request(**updates: object) -> LiepinCardsOperationRequestV1:
    payload: dict[str, object] = {
        "contract_version": "seektalent.source.liepin-cards.request/v1",
        "runtime_run_id": "run-details-1",
        "source_lane_run_id": "run-details-1:source:1:liepin:round:1:lane:1",
        "query_instance_id": "query-1",
        "keyword_query": "机器学习 工程师",
        "max_pages": 1,
        "max_cards": 20,
        "native_filters": {"city": ["上海"]},
    }
    payload.update(updates)
    return LiepinCardsOperationRequestV1.model_validate(payload, strict=True)


def _artifact(**updates: object) -> LiepinDetailsArtifactV1:
    payload: dict[str, object] = {
        "contract_version": "seektalent.source.liepin-details.artifact/v1",
        "operation_id": "details-operation-1",
        "canonical_request_hash": "a" * 64,
        "status": "succeeded",
        "open_mode": "cached_locator",
        "provider_candidate_key_hash": _HASH,
        "rank": 1,
        "card_ref": "70",
        "detail_url": _DETAIL_URL,
        "resume": {"name": "candidate"},
        "action_attempted": 1,
        "effect_posture": "attempted",
    }
    payload.update(updates)
    return LiepinDetailsArtifactV1.model_validate(payload, strict=True)


def test_details_operation_identity_and_hash_are_stable() -> None:
    request = _request()
    assert stable_liepin_details_operation_id(request) == stable_liepin_details_operation_id(
        request
    )
    assert canonical_liepin_details_request_hash(request) == (
        canonical_liepin_details_request_hash(request)
    )
    other = _request(card_ref="71", provider_candidate_key_hash="b" * 64)
    assert stable_liepin_details_operation_id(request) != stable_liepin_details_operation_id(
        other
    )
    resolve = _resolve_request()
    assert stable_liepin_details_operation_id(resolve).startswith("details_")
    assert stable_liepin_details_operation_id(resolve) != (
        stable_liepin_details_operation_id(request)
    )


def test_details_frames_require_authenticated_ack_before_terminal() -> None:
    request = _request()
    identity = _identity(request)
    authorization = DispatchAuthorizationV1.create_initial(
        identity=identity,
        dispatch_intent_id="dispatch-details-1",
        dispatch_intent_revision=1,
        source_operation_acceptance_ref="source-acceptance-details-1",
    )
    submit = LiepinDetailsSubmitV1(
        contract_version="seektalent.source.liepin-details.submit/v1",
        identity=identity,
        delivery=InitialDeliveryV1(
            delivery_mode="initial",
            authorization=authorization,
        ),
        request=request,
    )
    main, sidecar = _frame_pair()
    received_submit = sidecar.feed(
        main.encode_details_submit(
            message_id="submit-details-1",
            correlation_id="correlation-details-1",
            payload=submit,
        )
    )
    assert isinstance(received_submit[0], ReceivedLiepinDetailsSubmit)
    ack = LiepinDetailsAcceptedAckV1(
        contract_version="seektalent.source.liepin-details.ack/v1",
        identity=identity,
        sidecar_generation=1,
        accepted_journal_revision=1,
        ack_kind="new_logical_operation",
        dispatch_intent_ref="source-dispatch://details/1",
    )
    received_ack = main.feed(
        sidecar.encode_details_accepted_ack(
            message_id="ack-details-1",
            reply_to="submit-details-1",
            correlation_id="correlation-details-1",
            payload=ack,
        )
    )
    assert isinstance(received_ack[0], ReceivedLiepinDetailsAcceptedAck)
    observation = LiepinDetailsObservationV1(
        contract_version="seektalent.source.liepin-details.observation/v1",
        operation_id=identity.operation_id,
        canonical_request_hash=identity.request_hash,
        disposition="completed",
        artifact_ref="liepin-details://sha256/" + "d" * 64,
        artifact_hash="d" * 64,
        open_mode="cached_locator",
        provider_candidate_key_hash=_HASH,
        rank=1,
        action_attempted=1,
        effect_posture="attempted",
        producer_generation=1,
    )
    received_result = main.feed(
        sidecar.encode_details_result(
            message_id="result-details-1",
            reply_to="submit-details-1",
            correlation_id="correlation-details-1",
            payload=LiepinDetailsResultV1(
                contract_version="seektalent.source.liepin-details.result/v1",
                identity=identity,
                observation=observation,
            ),
        )
    )
    assert isinstance(received_result[0], ReceivedLiepinDetailsResult)


def test_unified_session_supports_cards_then_details() -> None:
    cards_request = _cards_request()
    cards_identity = _cards_identity(cards_request)
    cards_authorization = DispatchAuthorizationV1.create_initial(
        identity=cards_identity,
        dispatch_intent_id="dispatch-cards-1",
        dispatch_intent_revision=1,
        source_operation_acceptance_ref="source-acceptance-cards-1",
    )
    cards_submit = LiepinCardsSubmitV1(
        contract_version="seektalent.source.liepin-cards.submit/v1",
        identity=cards_identity,
        delivery=InitialDeliveryV1(
            delivery_mode="initial",
            authorization=cards_authorization,
        ),
        request=cards_request,
    )
    details_request = _request()
    details_identity = _identity(details_request)
    details_authorization = DispatchAuthorizationV1.create_initial(
        identity=details_identity,
        dispatch_intent_id="dispatch-details-1",
        dispatch_intent_revision=1,
        source_operation_acceptance_ref="source-acceptance-details-1",
    )
    details_submit = LiepinDetailsSubmitV1(
        contract_version="seektalent.source.liepin-details.submit/v1",
        identity=details_identity,
        delivery=InitialDeliveryV1(
            delivery_mode="initial",
            authorization=details_authorization,
        ),
        request=details_request,
    )
    main, sidecar = _frame_pair()

    received_cards = sidecar.feed(
        main.encode_cards_submit(
            message_id="submit-cards-1",
            correlation_id="correlation-cards-1",
            payload=cards_submit,
        )
    )
    assert isinstance(received_cards[0], ReceivedLiepinCardsSubmit)
    main.feed(
        sidecar.encode_cards_accepted_ack(
            message_id="ack-cards-1",
            reply_to="submit-cards-1",
            correlation_id="correlation-cards-1",
            payload=LiepinCardsAcceptedAckV1(
                contract_version="seektalent.source.liepin-cards.ack/v1",
                identity=cards_identity,
                sidecar_generation=1,
                accepted_journal_revision=1,
                ack_kind="new_logical_operation",
                dispatch_intent_ref="source-dispatch://cards/1",
            ),
        )
    )
    cards_result = main.feed(
        sidecar.encode_cards_result(
            message_id="result-cards-1",
            reply_to="submit-cards-1",
            correlation_id="correlation-cards-1",
            payload=LiepinCardsResultV1(
                contract_version="seektalent.source.liepin-cards.result/v1",
                identity=cards_identity,
                observation=LiepinCardsObservationV1(
                    contract_version="seektalent.source.liepin-cards.observation/v1",
                    operation_id=cards_identity.operation_id,
                    canonical_request_hash=cards_identity.request_hash,
                    disposition="completed",
                    artifact_ref="liepin-cards://sha256/" + "c" * 64,
                    artifact_hash="c" * 64,
                    cards_seen=1,
                    card_count=1,
                    producer_generation=1,
                ),
            ),
        )
    )
    assert isinstance(cards_result[0], ReceivedLiepinCardsResult)

    received_details = sidecar.feed(
        main.encode_details_submit(
            message_id="submit-details-1",
            correlation_id="correlation-details-1",
            payload=details_submit,
        )
    )
    assert isinstance(received_details[0], ReceivedLiepinDetailsSubmit)
    main.feed(
        sidecar.encode_details_accepted_ack(
            message_id="ack-details-1",
            reply_to="submit-details-1",
            correlation_id="correlation-details-1",
            payload=LiepinDetailsAcceptedAckV1(
                contract_version="seektalent.source.liepin-details.ack/v1",
                identity=details_identity,
                sidecar_generation=1,
                accepted_journal_revision=1,
                ack_kind="new_logical_operation",
                dispatch_intent_ref="source-dispatch://details/1",
            ),
        )
    )
    details_result = main.feed(
        sidecar.encode_details_result(
            message_id="result-details-1",
            reply_to="submit-details-1",
            correlation_id="correlation-details-1",
            payload=LiepinDetailsResultV1(
                contract_version="seektalent.source.liepin-details.result/v1",
                identity=details_identity,
                observation=LiepinDetailsObservationV1(
                    contract_version="seektalent.source.liepin-details.observation/v1",
                    operation_id=details_identity.operation_id,
                    canonical_request_hash=details_identity.request_hash,
                    disposition="completed",
                    artifact_ref="liepin-details://sha256/" + "d" * 64,
                    artifact_hash="d" * 64,
                    open_mode="cached_locator",
                    provider_candidate_key_hash=_HASH,
                    rank=1,
                    action_attempted=1,
                    effect_posture="attempted",
                    producer_generation=1,
                ),
            ),
        )
    )
    assert isinstance(details_result[0], ReceivedLiepinDetailsResult)


def test_unified_session_rejects_wrong_kind_reply() -> None:
    request = _request()
    identity = _identity(request)
    authorization = DispatchAuthorizationV1.create_initial(
        identity=identity,
        dispatch_intent_id="dispatch-details-1",
        dispatch_intent_revision=1,
        source_operation_acceptance_ref="source-acceptance-details-1",
    )
    submit = LiepinDetailsSubmitV1(
        contract_version="seektalent.source.liepin-details.submit/v1",
        identity=identity,
        delivery=InitialDeliveryV1(
            delivery_mode="initial",
            authorization=authorization,
        ),
        request=request,
    )
    main, sidecar = _frame_pair()
    sidecar.feed(
        main.encode_details_submit(
            message_id="submit-details-1",
            correlation_id="correlation-details-1",
            payload=submit,
        )
    )
    with pytest.raises(LiepinSourceFrameError, match="liepin_source_reply_mismatch"):
        main.feed(
            sidecar.encode_cards_accepted_ack(
                message_id="ack-wrong-kind",
                reply_to="submit-details-1",
                correlation_id="correlation-details-1",
                payload=LiepinCardsAcceptedAckV1(
                    contract_version="seektalent.source.liepin-cards.ack/v1",
                    identity=identity,
                    sidecar_generation=1,
                    accepted_journal_revision=1,
                    ack_kind="new_logical_operation",
                    dispatch_intent_ref="source-dispatch://details/1",
                ),
            )
        )


def test_unified_session_rejects_result_before_ack() -> None:
    request = _request()
    identity = _identity(request)
    authorization = DispatchAuthorizationV1.create_initial(
        identity=identity,
        dispatch_intent_id="dispatch-details-1",
        dispatch_intent_revision=1,
        source_operation_acceptance_ref="source-acceptance-details-1",
    )
    submit = LiepinDetailsSubmitV1(
        contract_version="seektalent.source.liepin-details.submit/v1",
        identity=identity,
        delivery=InitialDeliveryV1(
            delivery_mode="initial",
            authorization=authorization,
        ),
        request=request,
    )
    main, sidecar = _frame_pair()
    sidecar.feed(
        main.encode_details_submit(
            message_id="submit-details-1",
            correlation_id="correlation-details-1",
            payload=submit,
        )
    )
    with pytest.raises(LiepinSourceFrameError, match="liepin_details_ack_missing"):
        main.feed(
            sidecar.encode_details_result(
                message_id="result-details-1",
                reply_to="submit-details-1",
                correlation_id="correlation-details-1",
                payload=LiepinDetailsResultV1(
                    contract_version="seektalent.source.liepin-details.result/v1",
                    identity=identity,
                    observation=LiepinDetailsObservationV1(
                        contract_version="seektalent.source.liepin-details.observation/v1",
                        operation_id=identity.operation_id,
                        canonical_request_hash=identity.request_hash,
                        disposition="completed",
                        artifact_ref="liepin-details://sha256/" + "d" * 64,
                        artifact_hash="d" * 64,
                        open_mode="cached_locator",
                        provider_candidate_key_hash=_HASH,
                        rank=1,
                        action_attempted=1,
                        effect_posture="attempted",
                        producer_generation=1,
                    ),
                ),
            )
        )


def test_unified_session_rejects_identity_mismatch_on_ack() -> None:
    request = _request()
    identity = _identity(request)
    other = _identity(_request(card_ref="71", provider_candidate_key_hash="b" * 64))
    authorization = DispatchAuthorizationV1.create_initial(
        identity=identity,
        dispatch_intent_id="dispatch-details-1",
        dispatch_intent_revision=1,
        source_operation_acceptance_ref="source-acceptance-details-1",
    )
    submit = LiepinDetailsSubmitV1(
        contract_version="seektalent.source.liepin-details.submit/v1",
        identity=identity,
        delivery=InitialDeliveryV1(
            delivery_mode="initial",
            authorization=authorization,
        ),
        request=request,
    )
    main, sidecar = _frame_pair()
    sidecar.feed(
        main.encode_details_submit(
            message_id="submit-details-1",
            correlation_id="correlation-details-1",
            payload=submit,
        )
    )
    with pytest.raises(LiepinSourceFrameError, match="liepin_source_reply_identity_mismatch"):
        main.feed(
            sidecar.encode_details_accepted_ack(
                message_id="ack-details-1",
                reply_to="submit-details-1",
                correlation_id="correlation-details-1",
                payload=LiepinDetailsAcceptedAckV1(
                    contract_version="seektalent.source.liepin-details.ack/v1",
                    identity=other,
                    sidecar_generation=1,
                    accepted_journal_revision=1,
                    ack_kind="new_logical_operation",
                    dispatch_intent_ref="source-dispatch://details/1",
                ),
            )
        )


def test_details_artifact_is_content_addressed(tmp_path: Path) -> None:
    artifact = _artifact()
    artifact_ref, artifact_hash = write_liepin_details_artifact(
        tmp_path / "artifacts",
        artifact,
    )
    loaded = read_liepin_details_artifact(
        tmp_path / "artifacts",
        artifact_ref,
        expected_hash=artifact_hash,
    )
    assert loaded == artifact


def test_sidecar_details_effect_owns_browser_control_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seektalent.providers.liepin.liepin_site_adapter import LiepinSiteAdapter
    from seektalent.providers.liepin.liepin_details_locator_store import LiepinDetailLocator

    events: list[str] = []
    site = object.__new__(LiepinSiteAdapter)
    remember_liepin_detail_locator(
        tmp_path,
        provider_candidate_key_hash=_HASH,
        detail_url=_DETAIL_URL,
        card_ref="70",
        rank=1,
    )

    monkeypatch.setattr(
        LiepinSiteAdapter,
        "_begin_browser_control_scope",
        lambda _self: events.append("begin"),
    )
    monkeypatch.setattr(
        LiepinSiteAdapter,
        "_finish_browser_control_scope",
        lambda _self: events.append("finish"),
    )
    monkeypatch.setattr(
        "seektalent.providers.liepin.liepin_details_locator_store.load_liepin_detail_locator",
        lambda *_args, **_kwargs: LiepinDetailLocator(
            provider_candidate_key_hash=_HASH,
            detail_url=_DETAIL_URL,
            card_ref="70",
            rank=1,
        ),
    )

    def open_once(_self, **_kwargs):
        events.append("open")
        assert events[:2] == ["begin", "open"]
        from seektalent.opencli_browser.contracts import OpenCliBrowserResult

        return OpenCliBrowserResult(ok=False, action="open", safe_reason_code="liepin_opencli_timeout")

    monkeypatch.setattr(LiepinSiteAdapter, "_open_liepin_detail_cached_url", open_once)

    result = LiepinSiteAdapter._execute_liepin_details_sidecar_effect(
        site,
        source_run_id="lane-1",
        card_ref="70",
        rank=1,
        open_mode="cached_locator",
        provider_candidate_key_hash=_HASH,
        expected_provider_candidate_key_hash=_HASH,
        locator_root=tmp_path,
    )
    assert events == ["begin", "open", "finish"]
    assert result["effect_posture"] == "attempted"
    assert result["action_attempted"] == 1


def test_effect_crash_before_terminal_leaves_no_candidate_for_main_finalization(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        _env_file=None,
        workspace_root=str(Path(__file__).parents[1]),
    )
    journal_path = tmp_path / "journal.sqlite3"
    artifact_root = tmp_path / "artifacts"
    counter_path = tmp_path / "effect-count"
    request = _request()
    identity = _identity(request)
    authorization = DispatchAuthorizationV1.create_initial(
        identity=identity,
        dispatch_intent_id="dispatch-details-1",
        dispatch_intent_revision=1,
        source_operation_acceptance_ref="source-acceptance-details-1",
    )
    submit = LiepinDetailsSubmitV1(
        contract_version="seektalent.source.liepin-details.submit/v1",
        identity=identity,
        delivery=InitialDeliveryV1(
            delivery_mode="initial",
            authorization=authorization,
        ),
        request=request,
    )
    environment = {
        "SEEKTALENT_TEST_EFFECT_COUNTER": str(counter_path),
        "SEEKTALENT_TEST_EFFECT_STATUS": "succeeded",
        "SEEKTALENT_TEST_FAULT_POINT": "after_effect",
        "SEEKTALENT_TEST_FAULT_MARKER": str(tmp_path / "fault-marker"),
    }
    executor = object.__new__(LiepinCardsSourceOperationExecutor)
    executor._settings = settings
    executor._channel_lock = threading.Lock()
    executor._process = _spawn_sidecar(
        settings=settings,
        journal_path=journal_path,
        artifact_root=artifact_root,
        history_only=False,
        module="tests.test_liepin_details_source_operation",
        environment_overrides=environment,
    )
    try:
        with pytest.raises((OSError, RuntimeError, SidecarReadinessError)):
            executor._exchange_details(submit)
    finally:
        executor._process.close()

    assert counter_path.read_text(encoding="utf-8") == "1"
    details_dir = artifact_root / "details"
    assert not details_dir.exists() or not any(details_dir.glob("*.json"))
    with sqlite3.connect(journal_path) as connection:
        row = connection.execute("SELECT phase FROM source_history_heads").fetchone()
    assert row is not None and row[0] == "dispatch_intent"

    executor._process = _spawn_sidecar(
        settings=settings,
        journal_path=journal_path,
        artifact_root=artifact_root,
        history_only=False,
        module="tests.test_liepin_details_source_operation",
        environment_overrides={
            "SEEKTALENT_TEST_EFFECT_COUNTER": str(counter_path),
            "SEEKTALENT_TEST_EFFECT_STATUS": "succeeded",
        },
    )
    try:
        _ack, terminal = executor._exchange_details(submit)
    finally:
        executor._process.close()
    assert terminal.__class__.__name__.endswith("ReconcileRequired")
    assert counter_path.read_text(encoding="utf-8") == "1"


@pytest.mark.parametrize(
    ("effect_status", "expected_disposition"),
    [
        ("succeeded", "completed"),
        ("failed", "failed"),
    ],
)
def test_supervised_details_sidecar_replays_observed_terminal_without_second_effect(
    tmp_path: Path,
    effect_status: str,
    expected_disposition: str,
) -> None:
    settings = AppSettings(
        _env_file=None,
        workspace_root=str(Path(__file__).parents[1]),
    )
    journal_path = tmp_path / "journal.sqlite3"
    artifact_root = tmp_path / "artifacts"
    counter_path = tmp_path / "effect-count"
    request = _request()
    identity = _identity(request)
    authorization = DispatchAuthorizationV1.create_initial(
        identity=identity,
        dispatch_intent_id="dispatch-details-1",
        dispatch_intent_revision=1,
        source_operation_acceptance_ref="source-acceptance-details-1",
    )
    submit = LiepinDetailsSubmitV1(
        contract_version="seektalent.source.liepin-details.submit/v1",
        identity=identity,
        delivery=InitialDeliveryV1(
            delivery_mode="initial",
            authorization=authorization,
        ),
        request=request,
    )
    environment = {
        "SEEKTALENT_TEST_EFFECT_COUNTER": str(counter_path),
        "SEEKTALENT_TEST_EFFECT_STATUS": effect_status,
        "SEEKTALENT_TEST_FAULT_POINT": "after_terminal",
        "SEEKTALENT_TEST_FAULT_MARKER": str(tmp_path / "fault-marker"),
    }
    executor = object.__new__(LiepinCardsSourceOperationExecutor)
    executor._settings = settings
    executor._channel_lock = threading.Lock()
    executor._process = _spawn_sidecar(
        settings=settings,
        journal_path=journal_path,
        artifact_root=artifact_root,
        history_only=False,
        module="tests.test_liepin_details_source_operation",
        environment_overrides=environment,
    )

    with pytest.raises((OSError, RuntimeError, SidecarReadinessError)):
        executor._exchange_details(submit)
    assert counter_path.read_text(encoding="utf-8") == "1"
    executor._process.close()

    replay_payloads = []
    for _restart in range(2):
        executor._process = _spawn_sidecar(
            settings=settings,
            journal_path=journal_path,
            artifact_root=artifact_root,
            history_only=False,
            module="tests.test_liepin_details_source_operation",
            environment_overrides=environment,
        )
        try:
            replayed_ack, replayed = executor._exchange_details(submit)
        finally:
            executor._process.close()
        assert replayed_ack.identity == identity
        replay_payloads.append(replayed.payload)

    assert replay_payloads[0] == replay_payloads[1]
    assert replay_payloads[0].observation.disposition == expected_disposition
    assert replay_payloads[0].observation.effect_posture in {
        "attempted",
        "not_attempted",
    }
    assert counter_path.read_text(encoding="utf-8") == "1"


def test_terminal_reply_loss_replays_without_second_effect_and_main_ingests_once(
    tmp_path: Path,
) -> None:
    """Reply loss after durable terminal: effect stays 1; main ingest is separate authority."""
    settings = AppSettings(
        _env_file=None,
        workspace_root=str(Path(__file__).parents[1]),
    )
    journal_path = tmp_path / "journal.sqlite3"
    artifact_root = tmp_path / "artifacts"
    counter_path = tmp_path / "effect-count"
    request = _request()
    identity = _identity(request)
    authorization = DispatchAuthorizationV1.create_initial(
        identity=identity,
        dispatch_intent_id="dispatch-details-1",
        dispatch_intent_revision=1,
        source_operation_acceptance_ref="source-acceptance-details-1",
    )
    submit = LiepinDetailsSubmitV1(
        contract_version="seektalent.source.liepin-details.submit/v1",
        identity=identity,
        delivery=InitialDeliveryV1(
            delivery_mode="initial",
            authorization=authorization,
        ),
        request=request,
    )
    environment = {
        "SEEKTALENT_TEST_EFFECT_COUNTER": str(counter_path),
        "SEEKTALENT_TEST_EFFECT_STATUS": "succeeded",
        "SEEKTALENT_TEST_FAULT_POINT": "after_terminal",
        "SEEKTALENT_TEST_FAULT_MARKER": str(tmp_path / "fault-marker"),
    }
    executor = object.__new__(LiepinCardsSourceOperationExecutor)
    executor._settings = settings
    executor._channel_lock = threading.Lock()
    executor._process = _spawn_sidecar(
        settings=settings,
        journal_path=journal_path,
        artifact_root=artifact_root,
        history_only=False,
        module="tests.test_liepin_details_source_operation",
        environment_overrides=environment,
    )
    with pytest.raises((OSError, RuntimeError, SidecarReadinessError)):
        executor._exchange_details(submit)
    assert counter_path.read_text(encoding="utf-8") == "1"
    executor._process.close()

    executor._process = _spawn_sidecar(
        settings=settings,
        journal_path=journal_path,
        artifact_root=artifact_root,
        history_only=False,
        module="tests.test_liepin_details_source_operation",
        environment_overrides=environment,
    )
    try:
        _ack, replayed = executor._exchange_details(submit)
    finally:
        executor._process.close()
    assert replayed.payload.observation.disposition == "completed"
    assert replayed.payload.observation.effect_posture == "attempted"
    assert counter_path.read_text(encoding="utf-8") == "1"
    assert any((artifact_root.parent / "liepin-details-results").glob("*.json"))

    ingest_calls: list[dict[str, object]] = []
    from seektalent.opencli_browser.contracts import OpenCliBrowserResult
    from seektalent.providers.liepin.liepin_site_adapter import LiepinSiteAdapter

    class _Executor:
        def execute_details(self, **_kwargs):
            return (
                {
                    "status": "succeeded",
                    "effect_posture": "attempted",
                    "action_attempted": 1,
                },
                {
                    "ok": True,
                    "action": "capture_liepin_detail_resume",
                    "safe_reason_code": None,
                    "counts": {"rank": 1, "action_attempted": 1},
                    "observation": {},
                    "resume": {
                        "provider_rank": 1,
                        "detail_payload": {"ok": True},
                        "normalized_text": "candidate",
                        "page_url_hash": "e" * 64,
                        "claim_aware": True,
                        "provider_candidate_key_hash": _HASH,
                    },
                    "ingest_ready": True,
                    "effect_posture": "attempted",
                },
            )

    site = object.__new__(LiepinSiteAdapter)
    site._cards_operation_executor = _Executor()
    site.ingest_liepin_detail_resume_from_source_artifact = (  # type: ignore[method-assign]
        lambda **kwargs: ingest_calls.append(dict(kwargs))
    )
    envelope, result = LiepinSiteAdapter.run_liepin_details_operation(
        site,
        source_run_id="lane-1",
        card_ref="70",
        rank=1,
        open_mode="cached_locator",
        provider_candidate_key_hash=_HASH,
        expected_provider_candidate_key_hash=_HASH,
    )
    assert envelope["status"] == "succeeded"
    assert isinstance(result, OpenCliBrowserResult) and result.ok
    assert len(ingest_calls) == 1


@pytest.mark.parametrize(
    ("fault_point", "expected_phase", "expected_effects"),
    [
        ("before_accept", None, 0),
        ("after_accept", "accepted", 0),
        ("after_dispatch_intent", "dispatch_intent", 0),
        ("after_effect", "dispatch_intent", 1),
    ],
)
def test_supervised_details_fault_matrix_persists_phase_before_effect(
    tmp_path: Path,
    fault_point: str,
    expected_phase: str | None,
    expected_effects: int,
) -> None:
    settings = AppSettings(
        _env_file=None,
        workspace_root=str(Path(__file__).parents[1]),
    )
    journal_path = tmp_path / "journal.sqlite3"
    artifact_root = tmp_path / "artifacts"
    counter_path = tmp_path / "effect-count"
    request = _request()
    identity = _identity(request)
    authorization = DispatchAuthorizationV1.create_initial(
        identity=identity,
        dispatch_intent_id="dispatch-details-1",
        dispatch_intent_revision=1,
        source_operation_acceptance_ref="source-acceptance-details-1",
    )
    submit = LiepinDetailsSubmitV1(
        contract_version="seektalent.source.liepin-details.submit/v1",
        identity=identity,
        delivery=InitialDeliveryV1(
            delivery_mode="initial",
            authorization=authorization,
        ),
        request=request,
    )
    executor = object.__new__(LiepinCardsSourceOperationExecutor)
    executor._settings = settings
    executor._channel_lock = threading.Lock()
    executor._process = _spawn_sidecar(
        settings=settings,
        journal_path=journal_path,
        artifact_root=artifact_root,
        history_only=False,
        module="tests.test_liepin_details_source_operation",
        environment_overrides={
            "SEEKTALENT_TEST_EFFECT_COUNTER": str(counter_path),
            "SEEKTALENT_TEST_FAULT_POINT": fault_point,
            "SEEKTALENT_TEST_FAULT_MARKER": str(tmp_path / "fault-marker"),
        },
    )
    try:
        with pytest.raises((OSError, RuntimeError, SidecarReadinessError)):
            executor._exchange_details(submit)
    finally:
        executor._process.close()

    with sqlite3.connect(journal_path) as connection:
        row = connection.execute(
            "SELECT phase FROM source_history_heads"
        ).fetchone()
    assert (row[0] if row is not None else None) == expected_phase
    effect_count = (
        int(counter_path.read_text(encoding="utf-8"))
        if counter_path.exists()
        else 0
    )
    assert effect_count == expected_effects


def test_same_operation_key_different_request_hash_conflicts(tmp_path: Path) -> None:
    from tests.test_runtime_control_source_operations import _store_with_run

    store = _store_with_run(tmp_path)
    first = _acceptance(
        operation_id="details_same_key",
        operation_kind="details",
        idempotency_key="details-key-same",
        canonical_request_hash="a" * 64,
    )
    store.accept_source_operation(**first)
    with pytest.raises(RuntimeControlError) as exc_info:
        store.accept_source_operation(
            **{
                **first,
                "canonical_request_hash": "b" * 64,
            }
        )
    assert exc_info.value.reason_code == "idempotency_conflict"


def test_artifact_tamper_and_identity_mismatch_fail_closed(tmp_path: Path) -> None:
    artifact = _artifact(operation_id="details_op", canonical_request_hash="a" * 64)
    ref, digest = write_liepin_details_artifact(tmp_path, artifact)
    with pytest.raises(ValueError):
        read_liepin_details_artifact(tmp_path, ref, expected_hash="f" * 64)

    request = _request()
    identity = _identity(request)
    observation = LiepinDetailsObservationV1(
        contract_version="seektalent.source.liepin-details.observation/v1",
        operation_id=identity.operation_id,
        canonical_request_hash=identity.request_hash,
        disposition="completed",
        artifact_ref=ref,
        artifact_hash=digest,
        open_mode="cached_locator",
        provider_candidate_key_hash=_HASH,
        rank=1,
        action_attempted=1,
        effect_posture="attempted",
        producer_generation=1,
    )
    mismatched = _artifact(
        operation_id=identity.operation_id,
        canonical_request_hash=identity.request_hash,
        provider_candidate_key_hash="b" * 64,
        effect_posture="attempted",
    )
    from seektalent.liepin_cards_source_operation import (
        _details_artifact_binds_accepted_request,
    )

    assert not _details_artifact_binds_accepted_request(
        request=request,
        artifact=mismatched,
        observation=observation,
        operation_id=identity.operation_id,
        request_hash=identity.request_hash,
    )


def test_locator_corrupt_fields_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="liepin_details_locator_hash_invalid"):
        remember_liepin_detail_locator(
            tmp_path,
            provider_candidate_key_hash="not-a-hash",
            detail_url=_DETAIL_URL,
            card_ref="70",
            rank=1,
        )
    remember_liepin_detail_locator(
        tmp_path,
        provider_candidate_key_hash=_HASH,
        detail_url=_DETAIL_URL,
        card_ref="70",
        rank=1,
    )
    path = tmp_path / f"{_HASH}.json"
    path.write_text(
        '{"provider_candidate_key_hash":"'
        + _HASH
        + '","detail_url":"https://evil.example/x","card_ref":"70","rank":1}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="liepin_details_locator_detail_url_mismatch"):
        load_liepin_detail_locator(tmp_path, _HASH)
    path.write_text(
        '{"provider_candidate_key_hash":"'
        + _HASH
        + '","detail_url":"'
        + _DETAIL_URL
        + '","card_ref":"70","rank":"1"}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="liepin_details_locator_rank_invalid"):
        load_liepin_detail_locator(tmp_path, _HASH)
    path.write_text(
        '{"provider_candidate_key_hash":"'
        + _HASH
        + '","detail_url":"'
        + _DETAIL_URL
        + '","card_ref":"","rank":1}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="liepin_details_locator_card_ref_invalid"):
        load_liepin_detail_locator(tmp_path, _HASH)


def test_details_source_port_missing_fails_closed() -> None:
    from seektalent.opencli_browser.automation import OpenCliBrowserAutomation
    from seektalent.opencli_browser.contracts import OpenCliBrowserConfig
    from seektalent.providers.liepin.liepin_site_adapter import (
        LiepinOpenCliSiteConfig,
        LiepinSiteAdapter,
    )
    from seektalent.providers.liepin.liepin_opencli_policy import LIEPIN_RECRUITER_SEARCH_URL

    site = LiepinSiteAdapter(
        browser_config=OpenCliBrowserConfig(
            command=("opencli",),
            session="seektalent-test",
            timeout_seconds=10,
            pacing_enabled=False,
        ),
        site_config=LiepinOpenCliSiteConfig(
            allowed_hosts=("h.liepin.com",),
            allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
        ),
        automation=OpenCliBrowserAutomation(
            config=OpenCliBrowserConfig(
                command=("opencli",),
                session="seektalent-test",
                timeout_seconds=10,
                pacing_enabled=False,
            )
        ),
        cards_operation_executor=None,
    )
    with pytest.raises(RuntimeError, match="liepin_details_source_port_missing"):
        site.run_liepin_details_operation(
            source_run_id="run-1",
            card_ref="70",
            rank=1,
            open_mode="resolve_locator",
        )


def test_production_caller_scan_details_effect_owners() -> None:
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    effect_definition_owners = {
        "src/seektalent/providers/liepin/liepin_site_adapter.py",
    }
    effect_call_owners = {
        "src/seektalent/liepin_cards_sidecar.py",
        "src/seektalent/providers/liepin/liepin_site_adapter.py",
    }
    effect_callers: list[str] = []
    effect_definitions: list[str] = []
    for path in src.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        if "def _execute_liepin_details_sidecar_effect" in text:
            effect_definitions.append(relative)
        if "_execute_liepin_details_sidecar_effect" in text and relative not in effect_call_owners:
            if "def _execute_liepin_details_sidecar_effect" in text:
                continue
            effect_callers.append(relative)
    assert effect_definitions == sorted(effect_definition_owners)
    assert effect_callers == []

    workflow = (
        src / "seektalent/providers/liepin/liepin_search_workflow.py"
    ).read_text(encoding="utf-8")
    for pattern in (
        "_open_detail_with_retry",
        "_open_detail_transition",
        "_wait_detail_ready_transition",
        "_capture_detail_transition",
        "._open_liepin_detail(",
        "._open_liepin_detail_cached_url(",
        "._capture_liepin_detail_resume(",
        "._safe_liepin_detail_url_for_ref(",
        "_open_liepin_detail_with_local_retry",
        'open_mode="visible_card"',
        "visible_card",
    ):
        if pattern == "visible_card":
            assert "visible_cards" in workflow or "visible_card_count" in workflow
            assert 'open_mode="visible_card"' not in workflow
            continue
        assert pattern not in workflow

    workflow_site = (
        src / "seektalent/providers/liepin/liepin_workflow_site.py"
    ).read_text(encoding="utf-8")
    assert "run_liepin_details_operation" in workflow_site
    for dead in (
        "def open_liepin_detail(",
        "def open_liepin_detail_cached_url(",
        "def wait_liepin_detail_ready(",
        "def capture_liepin_detail_resume(",
        "def _capture_liepin_detail_resume_claim_aware(",
        "_open_detail_with_retry",
        "_open_detail_transition",
        "_wait_detail_ready_transition",
        "_capture_detail_transition",
    ):
        assert dead not in workflow_site

    adapter = (
        src / "seektalent/providers/liepin/liepin_site_adapter.py"
    ).read_text(encoding="utf-8")
    assert "ingest_liepin_detail_resume_from_source_artifact" in adapter
    assert "_observe_liepin_detail_resume_for_sidecar" in adapter
    sidecar = (src / "seektalent/liepin_cards_sidecar.py").read_text(encoding="utf-8")
    assert "collected-resumes" not in sidecar
    assert "ingest_liepin_detail_resume_from_source_artifact" not in sidecar


def test_continuation_expand_uses_details_port_only() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (
        root / "src/seektalent/providers/liepin/liepin_search_workflow.py"
    ).read_text(encoding="utf-8")
    assert "run_liepin_details_operation" in workflow
    assert 'open_mode="cached_locator"' in workflow
    expand = workflow.split("def expand_first_page_continuation")[1].split(
        "def _search_detail_backed_resumes"
    )[0]
    assert "_open_detail_with_retry(" not in expand
    assert "record_browser_open_attempt" not in expand
    assert "effect_posture" in workflow or "apply_detail_claim_from_result" in workflow


def test_same_key_replay_keeps_stable_details_operation_id() -> None:
    first = _request()
    second = _request()
    assert stable_liepin_details_operation_id(first) == (
        stable_liepin_details_operation_id(second)
    )
    assert canonical_liepin_details_request_hash(first) == (
        canonical_liepin_details_request_hash(second)
    )


def test_partial_details_artifact_round_trip(tmp_path: Path) -> None:
    artifact = _artifact(
        operation_id="details-partial-1",
        canonical_request_hash="c" * 64,
        status="partial",
        rank=2,
        card_ref="71",
        resume={"partial": True},
        action_attempted=1,
        effect_posture="attempted",
        safe_reason_code="liepin_opencli_detail_open_retry_exhausted",
        detail_url=None,
    )
    ref, digest = write_liepin_details_artifact(tmp_path, artifact)
    loaded = read_liepin_details_artifact(tmp_path, ref, expected_hash=digest)
    assert loaded.status == "partial"
    assert loaded.effect_posture == "attempted"
    assert loaded.safe_reason_code == "liepin_opencli_detail_open_retry_exhausted"


def _identity(request: LiepinDetailsOperationRequestV1) -> OperationIdentityV1:
    operation_id = stable_liepin_details_operation_id(request)
    request_hash = canonical_liepin_details_request_hash(request)
    return OperationIdentityV1(
        run_id=request.runtime_run_id,
        operation_id=operation_id,
        attempt_no=1,
        source="liepin",
        operation_kind="details",
        request_hash=request_hash,
        idempotency_key=f"details-key-{operation_id.removeprefix('details_')}",
        correlation_id=f"details-correlation-{operation_id.removeprefix('details_')}",
        accepted_requirement_revision_id="approved-1",
        runtime_attempt_fence_ref="f" * 64,
        profile_binding_generation=1,
        browser_control_scope_id=f"details-scope-{operation_id.removeprefix('details_')}",
        deadline=RelativeMonotonicDeadlineV1(
            value=60_000,
            clock="relative_monotonic",
            unit="milliseconds",
        ),
        expected_source_operation_ledger_revision=1,
        expected_reconciliation_revision=0,
    )


def _cards_identity(request: LiepinCardsOperationRequestV1) -> OperationIdentityV1:
    operation_id = stable_liepin_cards_operation_id(request)
    request_hash = canonical_liepin_cards_request_hash(request)
    return OperationIdentityV1(
        run_id=request.runtime_run_id,
        operation_id=operation_id,
        attempt_no=1,
        source="liepin",
        operation_kind="cards",
        request_hash=request_hash,
        idempotency_key=f"cards-key-{operation_id.removeprefix('cards_')}",
        correlation_id=f"cards-correlation-{operation_id.removeprefix('cards_')}",
        accepted_requirement_revision_id="approved-1",
        runtime_attempt_fence_ref="f" * 64,
        profile_binding_generation=1,
        browser_control_scope_id=f"cards-scope-{operation_id.removeprefix('cards_')}",
        deadline=RelativeMonotonicDeadlineV1(
            value=60_000,
            clock="relative_monotonic",
            unit="milliseconds",
        ),
        expected_source_operation_ledger_revision=1,
        expected_reconciliation_revision=0,
    )


def _frame_pair():
    values = {
        "session_id": "details-session-1",
        "protocol_minor": 0,
        "main_to_sidecar_key": b"m" * 32,
        "sidecar_to_main_key": b"s" * 32,
    }
    return (
        PostHandshakeLiepinSourceSession(role="main", **values),
        PostHandshakeLiepinSourceSession(role="sidecar", **values),
    )


class _SidecarHarnessSite:
    def __init__(self, counter_path: Path, status: str) -> None:
        self._counter_path = counter_path
        self._status = status

    def _execute_liepin_details_sidecar_effect(self, **_kwargs):
        count = (
            int(self._counter_path.read_text(encoding="utf-8"))
            if self._counter_path.exists()
            else 0
        )
        self._counter_path.write_text(str(count + 1), encoding="utf-8")
        succeeded = self._status == "succeeded"
        return {
            "status": self._status,
            "provider_candidate_key_hash": _HASH,
            "detail_url": _DETAIL_URL,
            "resume": (
                {
                    "provider_rank": 1,
                    "detail_payload": {"ok": True},
                    "normalized_text": "candidate",
                    "page_url_hash": "e" * 64,
                    "claim_aware": True,
                    "provider_candidate_key_hash": _HASH,
                }
                if succeeded
                else None
            ),
            "action_attempted": 1,
            "effect_posture": "attempted",
            "safe_reason_code": (
                None if succeeded else "liepin_test_observed_failure"
            ),
        }


def _run_sidecar_harness() -> int:
    counter_path = Path(os.environ["SEEKTALENT_TEST_EFFECT_COUNTER"])
    status = os.environ.get("SEEKTALENT_TEST_EFFECT_STATUS", "succeeded")
    fault_point = os.environ.get("SEEKTALENT_TEST_FAULT_POINT")
    fault_marker = Path(
        os.environ.get(
            "SEEKTALENT_TEST_FAULT_MARKER",
            str(counter_path.with_suffix(".fault")),
        )
    )

    def fault(point: str) -> None:
        if point == fault_point and not fault_marker.exists():
            fault_marker.write_text(point, encoding="utf-8")
            os._exit(86)

    return _serve(
        site_factory=lambda: _SidecarHarnessSite(counter_path, status),
        fault_hook=fault,
    )


if __name__ == "__main__":
    raise SystemExit(_run_sidecar_harness())
