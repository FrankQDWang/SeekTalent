from __future__ import annotations

import ast
from hashlib import sha256
import json
import logging
from pathlib import Path

from pydantic import ValidationError
import pytest

import seektalent.source_port.authenticated_verify_session_frames as frames
from seektalent.source_port.authenticated_verify_session_frames import (
    PostHandshakeVerifySessionSession,
    ReceivedVerifySessionAcceptedAck,
    ReceivedVerifySessionFailure,
    ReceivedVerifySessionReconcileRequired,
    ReceivedVerifySessionRejected,
    ReceivedVerifySessionResult,
    ReceivedVerifySessionSubmit,
    VerifySessionAcceptedAckV1,
    VerifySessionFailureV1,
    VerifySessionFrameError,
    VerifySessionFrameReason,
    VerifySessionReconcileRequiredV1,
    VerifySessionRejectedV1,
)
from seektalent.source_port.verify_session_contract import (
    VerifySessionRequestEchoV1,
    VerifySessionRequestV1,
    VerifySessionResultV1,
    validate_outbox_redelivery,
    verify_session_request_echo,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRAME_MODULE_PATH = PROJECT_ROOT / "src" / "seektalent" / "source_port" / "authenticated_verify_session_frames.py"
CORE_MODULE_PATH = PROJECT_ROOT / "src" / "seektalent" / "source_port" / "authenticated_frame_core.py"
RAW_FENCE_TOKEN = "verify-session-frame-fence-canary-" + "x" * 64
MAIN_TO_SIDECAR_KEY = bytes(range(32))
SIDECAR_TO_MAIN_KEY = bytes(range(32, 64))
VERIFY_SESSION_FRAME_VECTORS = {
    "submit": {
        "length": 2068,
        "auth_tag": "cfd0a05e9b247838a4ebc6b78da5b1436323c11218571c62dabe25921d49c211",
        "sha256": "dbf41b0a7f70930737479082d3cdd7518944cc5dd5016f5ef9a92dce36e13c58",
    },
    "accepted_ack": {
        "length": 1646,
        "auth_tag": "ccef8340eb6a8863bdb0b319fcc66e7ef45916cecd51050aca7757851d905b53",
        "sha256": "f1074e6da77a340e76e92e8be456c59c65d41f65ccd01b227b99d6d4e56bba67",
    },
    "result": {
        "length": 1543,
        "auth_tag": "1d5469b50b34fc879b02625343f6267b613bb8113daf78d55fdba99abe13b83e",
        "sha256": "6418e044c7d1c8ec4e43026a2821116e563821a4992f48f513493966fa5a99f3",
    },
}


def _request(**updates: object) -> VerifySessionRequestV1:
    values: dict[str, object] = {
        "run_id": "run-1",
        "operation_id": "verify-session-1",
        "attempt_no": 1,
        "idempotency_key": "verify-session-key-1",
        "correlation_id": "correlation-1",
        "accepted_requirement_revision_id": "requirement-revision-1",
        "runtime_attempt_fence_token": RAW_FENCE_TOKEN,
        "profile_binding_generation": 1,
        "browser_control_scope_id": "browser-scope-1",
        "deadline_value": 60_000,
        "expected_source_operation_ledger_revision": 1,
        "expected_reconciliation_revision": 0,
        "delivery_mode": "initial",
        "dispatch_intent_id": "dispatch-intent-1",
        "dispatch_intent_revision": 1,
        "source_operation_acceptance_ref": "source-acceptance-1",
        "profile_binding_ref": "profile-binding-1",
        "provider_account_ref": "provider-account-1",
        "required_capabilities": ("bridge", "extension", "profile_lock", "search_surface"),
        "user_interaction_policy": "observe_only",
        "verify_search_surface": True,
        "component_receipt_refs": ("main-receipt-1",),
    }
    values.update(updates)
    return VerifySessionRequestV1.create(**values)


def _result(request: VerifySessionRequestV1, **updates: object) -> VerifySessionResultV1:
    values: dict[str, object] = {
        "contract_version": "seektalent.source.verify-session.result/v1",
        "identity": request.identity,
        "process_readiness": "ready",
        "bridge_readiness": "ready",
        "extension_readiness": "ready",
        "profile_lock_readiness": "ready",
        "account_readiness": "ready",
        "search_surface_readiness": "ready",
        "risk_state": "clear",
        "session_readiness": "ready",
        "actual_profile_binding_ref": "profile-binding-1",
        "actual_provider_account_ref": "provider-account-1",
        "actual_profile_binding_generation": 1,
        "safe_reason_code": None,
        "user_action": None,
        "component_receipt_refs": ("main-receipt-1",),
    }
    values.update(updates)
    return VerifySessionResultV1.model_validate(values)


def _accepted_ack(request: VerifySessionRequestV1, **updates: object) -> VerifySessionAcceptedAckV1:
    values: dict[str, object] = {
        "contract_version": "seektalent.source.verify-session.accepted-ack/v1",
        "identity": request.identity,
        "dispatch_authorization": request.delivery.authorization,
        "accepted_fact": "dispatch_authorized",
    }
    values.update(updates)
    return VerifySessionAcceptedAckV1.model_validate(values)


def _rejected(request: VerifySessionRequestV1, **updates: object) -> VerifySessionRejectedV1:
    values: dict[str, object] = {
        "contract_version": "seektalent.source.verify-session.rejected/v1",
        "identity": request.identity,
        "rejection_reason": "deadline_expired",
    }
    values.update(updates)
    return VerifySessionRejectedV1.model_validate(values)


def _failure(request: VerifySessionRequestV1, **updates: object) -> VerifySessionFailureV1:
    values: dict[str, object] = {
        "contract_version": "seektalent.source.verify-session.failure/v1",
        "identity": request.identity,
        "failure_fact": "no_effect_performed",
        "failure_reason": "sidecar_not_ready",
    }
    values.update(updates)
    return VerifySessionFailureV1.model_validate(values)


def _reconcile_required(
    request: VerifySessionRequestV1,
    **updates: object,
) -> VerifySessionReconcileRequiredV1:
    values: dict[str, object] = {
        "contract_version": "seektalent.source.verify-session.reconcile-required/v1",
        "identity": request.identity,
        "reconciliation_fact": "dispatch_not_observed",
    }
    values.update(updates)
    return VerifySessionReconcileRequiredV1.model_validate(values)


def _main() -> PostHandshakeVerifySessionSession:
    return PostHandshakeVerifySessionSession.for_main(
        session_id="session-1",
        protocol_minor=0,
        main_to_sidecar_key=MAIN_TO_SIDECAR_KEY,
        sidecar_to_main_key=SIDECAR_TO_MAIN_KEY,
    )


def _sidecar() -> PostHandshakeVerifySessionSession:
    return PostHandshakeVerifySessionSession.for_sidecar(
        session_id="session-1",
        protocol_minor=0,
        main_to_sidecar_key=MAIN_TO_SIDECAR_KEY,
        sidecar_to_main_key=SIDECAR_TO_MAIN_KEY,
    )


def _frame_body(frame: bytes) -> bytes:
    assert int.from_bytes(frame[:4], "big") == len(frame) - 4
    return frame[4:]


def _assert_validation_error_has_no_fence_leak(error: ValidationError, caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("seektalent.source_port.verify_session.validation")
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger=logger.name):
        logger.error("verify_session_validation_failed", exc_info=error)
    surfaces = "\n".join(
        (
            str(error),
            repr(error),
            repr(error.args),
            repr(error.errors()),
            repr(error.__context__),
            caplog.text,
        )
    )
    assert RAW_FENCE_TOKEN not in surfaces


def test_submit_ack_and_terminal_result_share_one_authenticated_session_core() -> None:
    request = _request()
    main = _main()
    sidecar = _sidecar()

    submit_frame = main.encode_submit(
        message_id="submit-1",
        correlation_id="correlation-1",
        payload=request,
    )
    assert RAW_FENCE_TOKEN.encode() in submit_frame
    assert sidecar.feed(submit_frame) == (
        ReceivedVerifySessionSubmit(
            message_id="submit-1",
            correlation_id="correlation-1",
            payload=request,
        ),
    )

    ack = _accepted_ack(request)
    ack_frame = sidecar.encode_accepted_ack(
        message_id="ack-1",
        reply_to="submit-1",
        payload=ack,
    )
    assert main.feed(ack_frame) == (
        ReceivedVerifySessionAcceptedAck(
            message_id="ack-1",
            reply_to="submit-1",
            correlation_id="correlation-1",
            payload=ack,
        ),
    )

    result = _result(request)
    result_frame = sidecar.encode_result(
        message_id="result-1",
        reply_to="submit-1",
        payload=result,
    )
    received = main.feed(result_frame)

    assert received == (
        ReceivedVerifySessionResult(
            message_id="result-1",
            reply_to="submit-1",
            correlation_id="correlation-1",
            payload=result,
        ),
    )
    assert RAW_FENCE_TOKEN not in repr(received)


def test_verify_session_authenticated_exchange_known_answer_is_byte_stable() -> None:
    request = _request()
    main = _main()
    sidecar = _sidecar()

    submit = main.encode_submit(message_id="submit-1", correlation_id="correlation-1", payload=request)
    sidecar.feed(submit)
    accepted_ack = sidecar.encode_accepted_ack(
        message_id="ack-1",
        reply_to="submit-1",
        payload=_accepted_ack(request),
    )
    main.feed(accepted_ack)
    result = sidecar.encode_result(
        message_id="result-1",
        reply_to="submit-1",
        payload=_result(request),
    )
    assert main.feed(result) == (
        ReceivedVerifySessionResult(
            message_id="result-1",
            reply_to="submit-1",
            correlation_id="correlation-1",
            payload=_result(request),
        ),
    )

    for name, frame in (("submit", submit), ("accepted_ack", accepted_ack), ("result", result)):
        vector = VERIFY_SESSION_FRAME_VECTORS[name]
        body = json.loads(_frame_body(frame))
        assert len(frame) == vector["length"]
        assert body["auth_tag"] == vector["auth_tag"]
        assert sha256(frame).hexdigest() == vector["sha256"]

    assert RAW_FENCE_TOKEN.encode() in submit
    assert RAW_FENCE_TOKEN.encode() not in accepted_ack
    assert RAW_FENCE_TOKEN.encode() not in result


def test_rejected_is_terminal_while_failure_requires_the_accepted_ack() -> None:
    request = _request()
    main = _main()
    sidecar = _sidecar()
    submit = main.encode_submit(message_id="submit-1", correlation_id=None, payload=request)
    sidecar.feed(submit)

    with pytest.raises(VerifySessionFrameError) as result_before_ack:
        sidecar.encode_result(message_id="result-1", reply_to="submit-1", payload=_result(request))
    assert result_before_ack.value.reason_code == VerifySessionFrameReason.RESPONSE_STATE_MISMATCH.value
    assert sidecar.closed is True

    main = _main()
    sidecar = _sidecar()
    sidecar.feed(main.encode_submit(message_id="submit-1", correlation_id=None, payload=request))
    with pytest.raises(VerifySessionFrameError) as reconcile_before_ack:
        sidecar.encode_reconcile_required(
            message_id="reconcile-1",
            reply_to="submit-1",
            payload=_reconcile_required(request),
        )
    assert reconcile_before_ack.value.reason_code == VerifySessionFrameReason.RESPONSE_STATE_MISMATCH.value

    main = _main()
    sidecar = _sidecar()
    sidecar.feed(main.encode_submit(message_id="submit-1", correlation_id=None, payload=request))
    rejected = _rejected(request)
    assert main.feed(sidecar.encode_rejected(message_id="rejected-1", reply_to="submit-1", payload=rejected)) == (
        ReceivedVerifySessionRejected(
            message_id="rejected-1",
            reply_to="submit-1",
            correlation_id=None,
            payload=rejected,
        ),
    )

    main = _main()
    sidecar = _sidecar()
    sidecar.feed(main.encode_submit(message_id="submit-1", correlation_id=None, payload=request))
    main.feed(
        sidecar.encode_accepted_ack(
            message_id="ack-1",
            reply_to="submit-1",
            payload=_accepted_ack(request),
        )
    )
    failure = _failure(request)
    assert main.feed(sidecar.encode_failure(message_id="failure-1", reply_to="submit-1", payload=failure)) == (
        ReceivedVerifySessionFailure(
            message_id="failure-1",
            reply_to="submit-1",
            correlation_id=None,
            payload=failure,
        ),
    )


def test_reconcile_required_is_authenticated_and_retires_an_accepted_submit() -> None:
    request = _request()
    main = _main()
    sidecar = _sidecar()
    sidecar.feed(main.encode_submit(message_id="submit-1", correlation_id="correlation-1", payload=request))
    main.feed(
        sidecar.encode_accepted_ack(
            message_id="ack-1",
            reply_to="submit-1",
            payload=_accepted_ack(request),
        )
    )
    reconciliation = _reconcile_required(request)

    assert main.feed(
        sidecar.encode_reconcile_required(
            message_id="reconcile-1",
            reply_to="submit-1",
            payload=reconciliation,
        )
    ) == (
        ReceivedVerifySessionReconcileRequired(
            message_id="reconcile-1",
            reply_to="submit-1",
            correlation_id="correlation-1",
            payload=reconciliation,
        ),
    )
    assert len(main._pending_requests) == 0  # type: ignore[attr-defined]
    assert len(sidecar._pending_requests) == 0  # type: ignore[attr-defined]


def test_response_identity_authorization_and_direction_fail_closed() -> None:
    request = _request()
    main = _main()
    sidecar = _sidecar()
    sidecar.feed(main.encode_submit(message_id="submit-1", correlation_id=None, payload=request))

    wrong_identity = _accepted_ack(_request(operation_id="verify-session-2"))
    with pytest.raises(VerifySessionFrameError) as mismatch:
        sidecar.encode_accepted_ack(message_id="ack-1", reply_to="submit-1", payload=wrong_identity)
    assert mismatch.value.reason_code == VerifySessionFrameReason.REPLY_MISMATCH.value
    assert sidecar.closed is True

    with pytest.raises(VerifySessionFrameError) as wrong_direction:
        _sidecar().encode_submit(message_id="submit-1", correlation_id=None, payload=request)
    assert wrong_direction.value.reason_code == VerifySessionFrameReason.UNEXPECTED_DIRECTION.value


def test_pending_submit_limit_has_verify_session_taxonomy(monkeypatch: pytest.MonkeyPatch) -> None:
    assert VerifySessionFrameReason.PENDING_SUBMIT_LIMIT.value == "source_port_pending_submit_limit"
    monkeypatch.setattr(frames, "MAX_PENDING_SUBMITS", 1)
    main = _main()
    request = _request()
    main.encode_submit(message_id="submit-1", correlation_id=None, payload=request)

    with pytest.raises(VerifySessionFrameError) as limited:
        main.encode_submit(message_id="submit-2", correlation_id=None, payload=request)

    assert limited.value.reason_code == VerifySessionFrameReason.PENDING_SUBMIT_LIMIT.value
    assert main.closed is True


def test_outbox_redelivery_accepts_durable_ack_and_failure_with_the_original_identity() -> None:
    initial = _request()
    redelivery = _request(
        delivery_mode="outbox_redelivery",
        correlation_id="correlation-2",
        browser_control_scope_id="browser-scope-2",
        deadline_value=59_999,
        runtime_attempt_fence_token="verify-session-redelivery-fence-canary-" + "y" * 64,
    )
    assert redelivery.identity != initial.identity
    validate_outbox_redelivery(initial, redelivery)

    main = _main()
    sidecar = _sidecar()
    sidecar.feed(main.encode_submit(message_id="submit-1", correlation_id="correlation-2", payload=redelivery))

    accepted_ack = _accepted_ack(initial)
    assert main.feed(sidecar.encode_accepted_ack(message_id="ack-1", reply_to="submit-1", payload=accepted_ack)) == (
        ReceivedVerifySessionAcceptedAck(
            message_id="ack-1",
            reply_to="submit-1",
            correlation_id="correlation-2",
            payload=accepted_ack,
        ),
    )

    failure = _failure(initial)
    assert main.feed(sidecar.encode_failure(message_id="failure-1", reply_to="submit-1", payload=failure)) == (
        ReceivedVerifySessionFailure(
            message_id="failure-1",
            reply_to="submit-1",
            correlation_id="correlation-2",
            payload=failure,
        ),
    )

    main = _main()
    sidecar = _sidecar()
    sidecar.feed(main.encode_submit(message_id="submit-1", correlation_id="correlation-2", payload=redelivery))
    main.feed(sidecar.encode_accepted_ack(message_id="ack-1", reply_to="submit-1", payload=accepted_ack))
    reconciliation = _reconcile_required(initial)
    assert main.feed(
        sidecar.encode_reconcile_required(
            message_id="reconcile-1",
            reply_to="submit-1",
            payload=reconciliation,
        )
    ) == (
        ReceivedVerifySessionReconcileRequired(
            message_id="reconcile-1",
            reply_to="submit-1",
            correlation_id="correlation-2",
            payload=reconciliation,
        ),
    )


def test_raw_fence_bearer_is_submit_only_and_bypasses_revalidate_without_leaking() -> None:
    request = _request()
    ack = _accepted_ack(request)
    rejected = _rejected(request)
    result = _result(request)
    failure = _failure(request)
    reconciliation = _reconcile_required(request)

    for value in (ack, rejected, result, failure, reconciliation):
        surfaces = "\n".join((repr(value), repr(value.model_dump()), repr(value.model_dump(mode="json"))))
        assert RAW_FENCE_TOKEN not in surfaces

    for model, value in (
        (VerifySessionAcceptedAckV1, ack),
        (VerifySessionRejectedV1, rejected),
        (VerifySessionFailureV1, failure),
        (VerifySessionReconcileRequiredV1, reconciliation),
    ):
        payload = value.model_dump()
        payload["runtime_attempt_fence_token"] = RAW_FENCE_TOKEN
        with pytest.raises(ValidationError) as invalid:
            model.model_validate(payload)
        assert RAW_FENCE_TOKEN not in str(invalid.value)
        assert RAW_FENCE_TOKEN not in repr(invalid.value.errors())

    bypassed = request.model_copy(update={"identity": request.identity.model_copy(update={"operation_kind": "search"})})
    main = _main()
    with pytest.raises(VerifySessionFrameError) as invalid_submit:
        main.encode_submit(message_id="submit-1", correlation_id=None, payload=bypassed)
    assert invalid_submit.value.reason_code == VerifySessionFrameReason.SCHEMA_VALIDATION.value
    surfaces = "\n".join((str(invalid_submit.value), repr(invalid_submit.value), repr(invalid_submit.value.args)))
    assert RAW_FENCE_TOKEN not in surfaces
    assert main.closed is True


@pytest.mark.parametrize(
    "details",
    (
        {"runtime_attempt_fence_token": RAW_FENCE_TOKEN},
        {"nested": {"runtime_attempt_fence_token": RAW_FENCE_TOKEN}},
        {"nested": [{"runtime_attempt_fence_token": RAW_FENCE_TOKEN}]},
    ),
    ids=("unknown_mapping", "nested_mapping", "nested_list"),
)
def test_all_verify_dtos_recursively_redact_fence_bearers_from_validation_surfaces(
    details: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = _request()
    values = (
        (VerifySessionRequestV1, request),
        (VerifySessionAcceptedAckV1, _accepted_ack(request)),
        (VerifySessionRejectedV1, _rejected(request)),
        (VerifySessionResultV1, _result(request)),
        (VerifySessionFailureV1, _failure(request)),
        (VerifySessionReconcileRequiredV1, _reconcile_required(request)),
    )

    for model, value in values:
        payload = value.model_dump()
        payload["details"] = details
        with pytest.raises(ValidationError) as invalid:
            model.model_validate(payload)
        _assert_validation_error_has_no_fence_leak(invalid.value, caplog)


def test_non_submit_verify_dtos_redact_unknown_top_level_fence_bearers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = _request()
    values = (
        (VerifySessionAcceptedAckV1, _accepted_ack(request)),
        (VerifySessionRejectedV1, _rejected(request)),
        (VerifySessionResultV1, _result(request)),
        (VerifySessionFailureV1, _failure(request)),
        (VerifySessionReconcileRequiredV1, _reconcile_required(request)),
    )

    for model, value in values:
        payload = value.model_dump()
        payload["runtime_attempt_fence_token"] = RAW_FENCE_TOKEN
        with pytest.raises(ValidationError) as invalid:
            model.model_validate(payload)
        _assert_validation_error_has_no_fence_leak(invalid.value, caplog)


def test_recursive_fence_redaction_fails_closed_for_a_cyclic_unknown_input(
    caplog: pytest.LogCaptureFixture,
) -> None:
    details: dict[str, object] = {"runtime_attempt_fence_token": RAW_FENCE_TOKEN}
    details["cycle"] = details
    payload = _result(_request()).model_dump()
    payload["details"] = details

    with pytest.raises(ValidationError) as invalid:
        VerifySessionResultV1.model_validate(payload)

    _assert_validation_error_has_no_fence_leak(invalid.value, caplog)


def test_pending_frame_state_keeps_only_a_non_bearer_request_echo() -> None:
    request = _request()
    main = _main()
    sidecar = _sidecar()
    submit = main.encode_submit(message_id="submit-1", correlation_id=None, payload=request)

    assert isinstance(verify_session_request_echo(request), VerifySessionRequestEchoV1)
    for session in (main, sidecar):
        if session is sidecar:
            sidecar.feed(submit)
        pending = next(iter(session._pending_requests.values()))  # type: ignore[attr-defined]
        retained_request = pending.request.request
        surfaces = "\n".join((repr(retained_request), repr(retained_request.model_dump(mode="json"))))
        assert RAW_FENCE_TOKEN not in surfaces


def test_reply_contracts_are_closed_and_revalidate_constructed_bypasses() -> None:
    request = _request()
    rejected = _rejected(request)
    failure = _failure(request)
    reconciliation = _reconcile_required(request)

    for model, payload, field in (
        (VerifySessionRejectedV1, rejected.model_dump(), "rejection_reason"),
        (VerifySessionFailureV1, failure.model_dump(), "failure_reason"),
        (VerifySessionReconcileRequiredV1, reconciliation.model_dump(), "reconciliation_fact"),
    ):
        payload[field] = "https://private.example.invalid/path"
        with pytest.raises(ValidationError):
            model.model_validate(payload)
        with pytest.raises(ValidationError):
            model.model_validate({**payload, "details": {"cookie": "forbidden"}})

    bypassed = VerifySessionAcceptedAckV1.model_construct(
        **{
            **_accepted_ack(request).model_dump(),
            "identity": request.identity.model_copy(update={"operation_kind": "search"}),
        }
    )
    main = _main()
    sidecar = _sidecar()
    sidecar.feed(main.encode_submit(message_id="submit-1", correlation_id=None, payload=request))

    with pytest.raises(VerifySessionFrameError) as invalid_bypass:
        sidecar.encode_accepted_ack(message_id="ack-1", reply_to="submit-1", payload=bypassed)
    assert invalid_bypass.value.reason_code == VerifySessionFrameReason.SCHEMA_VALIDATION.value
    assert sidecar.closed is True


def test_frame_modules_keep_one_source_port_core_and_no_production_caller() -> None:
    frame_source = FRAME_MODULE_PATH.read_text(encoding="utf-8")
    core_source = CORE_MODULE_PATH.read_text(encoding="utf-8")
    imported_modules = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(ast.parse(frame_source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert imported_modules <= {
        "__future__",
        "collections.abc",
        "dataclasses",
        "enum",
        "pydantic",
        "threading",
        "typing",
        "seektalent.source_port.authenticated_frame_core",
        "seektalent.source_port.operation_dispatch",
        "seektalent.source_port.verify_session_contract",
        "seektalent.source_port.wire_primitives",
    }
    assert "json.loads" in core_source
    assert "rfc8785.dumps" not in core_source
    assert "command_journal" not in frame_source
    assert "sqlite" not in frame_source.lower()

    callers = []
    for path in (PROJECT_ROOT / "src").rglob("*.py"):
        if path in {FRAME_MODULE_PATH, CORE_MODULE_PATH}:
            continue
        if "authenticated_verify_session_frames" in path.read_text(encoding="utf-8"):
            callers.append(path.relative_to(PROJECT_ROOT).as_posix())
    assert set(callers) == {
        "src/seektalent/source_port/authenticated_source_port_session.py",
        "src/seektalent/source_port/sidecar_transport.py",
        "src/seektalent/source_port/verify_session_journal_effect.py",
        "src/seektalent/source_port/verify_session_journal_effect_durable.py",
    }
