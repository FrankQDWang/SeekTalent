from __future__ import annotations

import ast
from pathlib import Path

from pydantic import ValidationError
import pytest

from seektalent.source_port.authenticated_verify_session_frames import (
    PostHandshakeVerifySessionSession,
    ReceivedVerifySessionAcceptedAck,
    ReceivedVerifySessionFailure,
    ReceivedVerifySessionRejected,
    ReceivedVerifySessionResult,
    ReceivedVerifySessionSubmit,
    VerifySessionAcceptedAckV1,
    VerifySessionFailureV1,
    VerifySessionFrameError,
    VerifySessionFrameReason,
    VerifySessionRejectedV1,
)
from seektalent.source_port.verify_session_contract import (
    VerifySessionRequestEchoV1,
    VerifySessionRequestV1,
    VerifySessionResultV1,
    verify_session_request_echo,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRAME_MODULE_PATH = (
    PROJECT_ROOT / "src" / "seektalent" / "source_port" / "authenticated_verify_session_frames.py"
)
CORE_MODULE_PATH = PROJECT_ROOT / "src" / "seektalent" / "source_port" / "authenticated_frame_core.py"
RAW_FENCE_TOKEN = "verify-session-frame-fence-canary-" + "x" * 64
MAIN_TO_SIDECAR_KEY = bytes(range(32))
SIDECAR_TO_MAIN_KEY = bytes(range(32, 64))


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


def test_raw_fence_bearer_is_submit_only_and_bypasses_revalidate_without_leaking() -> None:
    request = _request()
    ack = _accepted_ack(request)
    rejected = _rejected(request)
    result = _result(request)
    failure = _failure(request)

    for value in (ack, rejected, result, failure):
        surfaces = "\n".join((repr(value), repr(value.model_dump()), repr(value.model_dump(mode="json"))))
        assert RAW_FENCE_TOKEN not in surfaces

    for model in (VerifySessionAcceptedAckV1, VerifySessionRejectedV1, VerifySessionFailureV1):
        payload = ack.model_dump() if model is VerifySessionAcceptedAckV1 else (
            rejected.model_dump() if model is VerifySessionRejectedV1 else failure.model_dump()
        )
        payload["runtime_attempt_fence_token"] = RAW_FENCE_TOKEN
        with pytest.raises(ValidationError) as invalid:
            model.model_validate(payload)
        assert RAW_FENCE_TOKEN not in str(invalid.value)
        assert RAW_FENCE_TOKEN not in repr(invalid.value.errors())

    bypassed = request.model_copy(
        update={"identity": request.identity.model_copy(update={"operation_kind": "search"})}
    )
    main = _main()
    with pytest.raises(VerifySessionFrameError) as invalid_submit:
        main.encode_submit(message_id="submit-1", correlation_id=None, payload=bypassed)
    assert invalid_submit.value.reason_code == VerifySessionFrameReason.SCHEMA_VALIDATION.value
    surfaces = "\n".join((str(invalid_submit.value), repr(invalid_submit.value), repr(invalid_submit.value.args)))
    assert RAW_FENCE_TOKEN not in surfaces
    assert main.closed is True


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

    for model, payload, field in (
        (VerifySessionRejectedV1, rejected.model_dump(), "rejection_reason"),
        (VerifySessionFailureV1, failure.model_dump(), "failure_reason"),
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
    assert callers == []
