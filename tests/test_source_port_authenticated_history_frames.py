from __future__ import annotations

import ast
from hashlib import sha256
import hmac
import json
from pathlib import Path
import warnings

import pytest
import rfc8785

import seektalent.source_port.authenticated_history_frames as frames
from seektalent.source_port.authenticated_history_frames import (
    HistoryFrameError,
    HistoryFrameReason,
    PostHandshakeHistorySession,
    ReceivedHistoryQuery,
    ReceivedHistoryResult,
    canonical_source_history_semantics_bytes,
)
from seektalent.source_port.history_contract import (
    AcceptedNoDispatchFact,
    JSON_SAFE_INTEGER,
    SourceHistoryMatched,
    SourceHistoryNotFound,
    SourceHistoryQueryV1,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "src" / "seektalent" / "source_port" / "authenticated_history_frames.py"
CORE_MODULE_PATH = PROJECT_ROOT / "src" / "seektalent" / "source_port" / "authenticated_frame_core.py"
MAIN_TO_SIDECAR_KEY = bytes(range(32))
SIDECAR_TO_MAIN_KEY = bytes(range(32, 64))
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _query(**updates: object) -> SourceHistoryQueryV1:
    values: dict[str, object] = {
        "contract_version": "seektalent.source-port.query.request/v1",
        "run_id": "run-1",
        "operation_id": "operation-1",
        "source": "liepin",
        "operation_kind": "search",
        "idempotency_key": "key-1",
        "request_hash": HASH_A,
        "attempt_no": 1,
        "authorization_selector": {"kind": "exact", "ordinal": 1},
        "accepted_generation_hint": 2,
        "searched_first_generation": 1,
        "searched_last_generation": 3,
        "expected_source_operation_ledger_revision": 4,
        "expected_reconciliation_revision": 0,
    }
    values.update(updates)
    return SourceHistoryQueryV1(**values)


def _not_found(query: SourceHistoryQueryV1, **updates: object) -> SourceHistoryNotFound:
    values: dict[str, object] = {
        **query.model_dump(exclude={"contract_version"}),
        "contract_version": "seektalent.source-port.query.result/v1",
        "outcome": "not_found",
        "oldest_retained_generation": 1,
        "newest_known_generation": 3,
        "history_complete": True,
        "history_truncated": False,
    }
    values.update(updates)
    return SourceHistoryNotFound(**values)


def _matched(query: SourceHistoryQueryV1) -> SourceHistoryMatched:
    fact = AcceptedNoDispatchFact(
        run_id=query.run_id,
        operation_id=query.operation_id,
        source="liepin",
        operation_kind=query.operation_kind,
        idempotency_key=query.idempotency_key,
        request_hash=query.request_hash,
        attempt_no=query.attempt_no,
        accepted_requirement_revision_id="requirement-1",
        runtime_attempt_fence_ref=HASH_B,
        accepted_generation=2,
        accepted_journal_revision=10,
        head_generation=2,
        head_journal_revision=10,
        dispatch_authorization_ordinal=1,
        authorized_dispatch_intent_id="intent-1",
        authorized_dispatch_intent_revision=1,
        authorized_dispatch_intent_digest=HASH_C,
        profile_binding_generation=1,
        browser_control_scope_id="browser-scope-1",
        controller_fence_ref=HASH_A,
        conclusion="accepted_no_dispatch",
    )
    return SourceHistoryMatched(
        **query.model_dump(exclude={"contract_version"}),
        contract_version="seektalent.source-port.query.result/v1",
        outcome="matched",
        oldest_retained_generation=1,
        newest_known_generation=3,
        history_complete=True,
        history_truncated=False,
        facts=(fact,),
    )


def _main(
    *,
    session_id: str = "session-1",
    protocol_minor: int = 0,
    main_to_sidecar_key: bytes = MAIN_TO_SIDECAR_KEY,
    sidecar_to_main_key: bytes = SIDECAR_TO_MAIN_KEY,
) -> PostHandshakeHistorySession:
    return PostHandshakeHistorySession.for_main(
        session_id=session_id,
        protocol_minor=protocol_minor,
        main_to_sidecar_key=main_to_sidecar_key,
        sidecar_to_main_key=sidecar_to_main_key,
    )


def _sidecar(
    *,
    session_id: str = "session-1",
    protocol_minor: int = 0,
    main_to_sidecar_key: bytes = MAIN_TO_SIDECAR_KEY,
    sidecar_to_main_key: bytes = SIDECAR_TO_MAIN_KEY,
) -> PostHandshakeHistorySession:
    return PostHandshakeHistorySession.for_sidecar(
        session_id=session_id,
        protocol_minor=protocol_minor,
        main_to_sidecar_key=main_to_sidecar_key,
        sidecar_to_main_key=sidecar_to_main_key,
    )


def _raw_frame(body: bytes) -> bytes:
    return len(body).to_bytes(4, "big") + body


def _frame_body(frame: bytes) -> bytes:
    size = int.from_bytes(frame[:4], "big")
    assert size == len(frame) - 4
    return frame[4:]


def _unsigned_query(
    query: SourceHistoryQueryV1,
    *,
    sequence: int = 1,
    session_id: str = "session-1",
    message_id: str = "query-message-1",
    correlation_id: str | None = "correlation-1",
    protocol_minor: int = 0,
) -> dict[str, object]:
    return {
        "protocol_name": frames.PROTOCOL_NAME,
        "protocol_major": frames.PROTOCOL_MAJOR,
        "protocol_minor": protocol_minor,
        "session_id": session_id,
        "direction_seq": sequence,
        "message_id": message_id,
        "reply_to": None,
        "message_type": "operation.query",
        "correlation_id": correlation_id,
        "payload": query.model_dump(mode="json"),
    }


def _unsigned_result(
    result: SourceHistoryNotFound | SourceHistoryMatched,
    *,
    sequence: int = 1,
    session_id: str = "session-1",
    message_id: str = "result-message-1",
    reply_to: str = "query-message-1",
    correlation_id: str | None = "correlation-1",
    protocol_minor: int = 0,
) -> dict[str, object]:
    return {
        "protocol_name": frames.PROTOCOL_NAME,
        "protocol_major": frames.PROTOCOL_MAJOR,
        "protocol_minor": protocol_minor,
        "session_id": session_id,
        "direction_seq": sequence,
        "message_id": message_id,
        "reply_to": reply_to,
        "message_type": "operation.query.result",
        "correlation_id": correlation_id,
        "payload": result.model_dump(mode="json"),
    }


def _lp(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big") + value


def _signed_frame(
    unsigned: dict[str, object],
    *,
    key: bytes,
    direction: bytes,
    authenticated_length_offset: int = 0,
) -> bytes:
    unsigned_body = rfc8785.dumps(unsigned)
    body_with_zero_tag = rfc8785.dumps({**unsigned, "auth_tag": "0" * 64})
    authenticated_length = len(body_with_zero_tag) + authenticated_length_offset
    auth_input = b"".join(
        (
            _lp(b"seektalent-source-port-frame-auth-v1"),
            _lp(str(unsigned["session_id"]).encode()),
            _lp(direction),
            int(unsigned["direction_seq"]).to_bytes(8, "big"),
            authenticated_length.to_bytes(4, "big"),
            len(unsigned_body).to_bytes(4, "big"),
            unsigned_body,
        )
    )
    tag = hmac.new(key, auth_input, sha256).hexdigest()
    return _raw_frame(rfc8785.dumps({**unsigned, "auth_tag": tag}))


def _assert_feed_failure(
    session: PostHandshakeHistorySession,
    frame: bytes,
    reason: HistoryFrameReason,
) -> HistoryFrameError:
    with pytest.raises(HistoryFrameError) as caught:
        session.feed(frame)
    assert caught.value.reason_code == reason.value
    assert session.closed is True
    assert session.closed_reason == reason.value
    with pytest.raises(HistoryFrameError) as closed:
        session.feed(b"")
    assert closed.value.reason_code == HistoryFrameReason.SESSION_CLOSED.value
    return caught.value


def test_query_and_matched_result_round_trip_through_one_byte_fragments() -> None:
    main = _main()
    sidecar = _sidecar()
    query = _query()
    query_frame = main.encode_query(message_id="query-message-1", correlation_id="correlation-1", payload=query)

    received_queries = []
    for byte in query_frame:
        received_queries.extend(sidecar.feed(bytes((byte,))))

    assert received_queries == [
        ReceivedHistoryQuery(message_id="query-message-1", correlation_id="correlation-1", payload=query)
    ]
    result = _matched(query)
    result_frame = sidecar.encode_result(
        message_id="result-message-1",
        reply_to="query-message-1",
        payload=result,
    )
    result_body = json.loads(_frame_body(result_frame))
    assert isinstance(result_body["payload"]["facts"], list)

    received_results = main.feed(result_frame)

    assert received_results == (
        ReceivedHistoryResult(
            message_id="result-message-1",
            reply_to="query-message-1",
            correlation_id="correlation-1",
            payload=result,
        ),
    )
    assert "auth_tag" not in repr(received_results)


def test_coalesced_frames_are_returned_only_after_the_whole_chunk_is_valid() -> None:
    main = _main()
    sidecar = _sidecar()
    first = main.encode_query(message_id="query-1", correlation_id=None, payload=_query(operation_id="operation-1"))
    second = main.encode_query(message_id="query-2", correlation_id=None, payload=_query(operation_id="operation-2"))

    received = sidecar.feed(first + second)

    assert [message.message_id for message in received] == ["query-1", "query-2"]


def test_valid_prefix_does_not_escape_when_later_coalesced_frame_is_invalid() -> None:
    main = _main()
    sidecar = _sidecar()
    valid = main.encode_query(message_id="query-1", correlation_id=None, payload=_query())

    _assert_feed_failure(sidecar, valid + b"\x00\x00\x00\x00", HistoryFrameReason.FRAME_LENGTH_INVALID)


def test_maximum_jcs_safe_integer_encodes_before_rfc8785() -> None:
    main = _main()
    sidecar = _sidecar()
    query = _query(attempt_no=JSON_SAFE_INTEGER)

    received = sidecar.feed(main.encode_query(message_id="query-1", correlation_id=None, payload=query))

    assert received[0].payload.attempt_no == 2**53 - 1


@pytest.mark.parametrize(("field", "value"), [("attempt_no", True), ("run_id", 12_345)])
def test_query_sender_strictly_revalidates_copied_model_instances(field: str, value: object) -> None:
    invalid = _query().model_copy(update={field: value})
    main = _main()

    with warnings.catch_warnings(record=True) as recorded, pytest.raises(HistoryFrameError) as caught:
        warnings.simplefilter("always")
        main.encode_query(message_id="query-1", correlation_id=None, payload=invalid)

    assert caught.value.reason_code == HistoryFrameReason.SCHEMA_VALIDATION.value
    assert caught.value.__context__ is None
    assert recorded == []
    assert main.closed is True


def test_result_sender_strictly_revalidates_copied_model_instances() -> None:
    query = _query()
    main = _main()
    sidecar = _sidecar()
    sidecar.feed(main.encode_query(message_id="query-1", correlation_id=None, payload=query))
    invalid = _not_found(query).model_copy(update={"attempt_no": True})

    with warnings.catch_warnings(record=True) as recorded, pytest.raises(HistoryFrameError) as caught:
        warnings.simplefilter("always")
        sidecar.encode_result(message_id="result-1", reply_to="query-1", payload=invalid)

    assert caught.value.reason_code == HistoryFrameReason.SCHEMA_VALIDATION.value
    assert caught.value.__context__ is None
    assert recorded == []
    assert sidecar.closed is True


def test_history_semantic_canonical_bytes_revalidate_bypassed_models() -> None:
    query = _query()
    result = _not_found(query)
    bypassed_query = query.model_copy(update={"attempt_no": True})
    bypassed_result = result.model_copy(update={"attempt_no": True})

    for invalid_query, invalid_result in ((bypassed_query, result), (query, bypassed_result)):
        with pytest.raises(HistoryFrameError) as caught:
            canonical_source_history_semantics_bytes(invalid_query, invalid_result)
        assert caught.value.reason_code == HistoryFrameReason.SCHEMA_VALIDATION.value


def test_frame_authentication_known_answer_is_byte_stable() -> None:
    unsigned = _unsigned_query(_query())
    unsigned_body = rfc8785.dumps(unsigned)
    zero_tag_body = rfc8785.dumps({**unsigned, "auth_tag": "0" * 64})
    auth_input = b"".join(
        (
            _lp(b"seektalent-source-port-frame-auth-v1"),
            _lp(b"session-1"),
            _lp(b"main-to-sidecar"),
            (1).to_bytes(8, "big"),
            len(zero_tag_body).to_bytes(4, "big"),
            len(unsigned_body).to_bytes(4, "big"),
            unsigned_body,
        )
    )
    frame = _main().encode_query(
        message_id="query-message-1",
        correlation_id="correlation-1",
        payload=_query(),
    )
    body = json.loads(_frame_body(frame))

    assert len(unsigned_body) == 757
    assert len(zero_tag_body) == 835
    assert sha256(unsigned_body).hexdigest() == "352621dc542e618d362d04aa02567b84ec9564cdb97a61eecb318558a839eed2"
    assert sha256(auth_input).hexdigest() == "a31a4b25323f27fbd454aa7aa7a1404212441c74e919914b5dc67a6fe969c587"
    assert body["auth_tag"] == "5c4ac6c941e272e58b7a6dd325168ef3ee1036fa738c67de526678619b83c278"
    assert sha256(frame).hexdigest() == "be94f30c020df62c3e5ed925eff39184102e9e8ef048c85f59c6885a57c5e091"


@pytest.mark.parametrize("header_size", [1, 2, 3])
def test_eof_rejects_partial_header(header_size: int) -> None:
    session = _sidecar()
    session.feed(b"\x00" * header_size)

    with pytest.raises(HistoryFrameError) as caught:
        session.feed_eof()

    assert caught.value.reason_code == HistoryFrameReason.TRUNCATED_FRAME.value
    assert session.closed is True


def test_eof_rejects_partial_body_and_clean_eof_closes_without_error() -> None:
    main = _main()
    frame = main.encode_query(message_id="query-1", correlation_id=None, payload=_query())
    truncated = _sidecar()
    truncated.feed(frame[:-1])

    with pytest.raises(HistoryFrameError, match=HistoryFrameReason.TRUNCATED_FRAME.value):
        truncated.feed_eof()

    clean = _sidecar()
    clean.feed_eof()
    assert clean.closed is True
    assert clean.closed_reason is None


def test_frame_length_bounds_fail_before_body_buffering() -> None:
    _assert_feed_failure(_sidecar(), b"\x00\x00\x00\x00", HistoryFrameReason.FRAME_LENGTH_INVALID)
    oversized = _sidecar()
    _assert_feed_failure(
        oversized,
        (frames.MAX_FRAME_BYTES + 1).to_bytes(4, "big"),
        HistoryFrameReason.FRAME_TOO_LARGE,
    )
    assert len(oversized._body) == 0  # type: ignore[attr-defined]

    at_cap = _sidecar()
    assert at_cap.feed(frames.MAX_FRAME_BYTES.to_bytes(4, "big")) == ()
    assert len(at_cap._body) == 0  # type: ignore[attr-defined]
    with pytest.raises(HistoryFrameError, match=HistoryFrameReason.TRUNCATED_FRAME.value):
        at_cap.feed_eof()


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"\xff", HistoryFrameReason.INVALID_UTF8),
        (b"\xef\xbb\xbf{}", HistoryFrameReason.BOM_FORBIDDEN),
        (b"{}{}", HistoryFrameReason.INVALID_JSON),
        (b'{"x":1,"x":2}', HistoryFrameReason.DUPLICATE_KEY),
        (b'{"x":{"y":1,"y":2}}', HistoryFrameReason.DUPLICATE_KEY),
        (b'{"x":NaN}', HistoryFrameReason.ILLEGAL_NUMBER),
        (b'{"x":Infinity}', HistoryFrameReason.ILLEGAL_NUMBER),
        (b'{"x":1.0}', HistoryFrameReason.ILLEGAL_NUMBER),
        (b'{"x":1e0}', HistoryFrameReason.ILLEGAL_NUMBER),
        (b'{"x":-0}', HistoryFrameReason.ILLEGAL_NUMBER),
        (b'{"x":9007199254740992}', HistoryFrameReason.ILLEGAL_NUMBER),
        (b"[]", HistoryFrameReason.ROOT_NOT_OBJECT),
        (b'{"x":"\\ud800"}', HistoryFrameReason.INVALID_UNICODE),
        (b' {"x":1}', HistoryFrameReason.NON_CANONICAL_BODY),
        (b'{"z":1,"a":2}', HistoryFrameReason.NON_CANONICAL_BODY),
        (b'{"x":"\\u0061"}', HistoryFrameReason.NON_CANONICAL_BODY),
        (rfc8785.dumps({"unknown": 1}), HistoryFrameReason.SCHEMA_VALIDATION),
    ],
)
def test_strict_json_and_exact_jcs_fail_closed(body: bytes, reason: HistoryFrameReason) -> None:
    _assert_feed_failure(_sidecar(), _raw_frame(body), reason)


@pytest.mark.parametrize("location", ["envelope", "payload"])
def test_unknown_envelope_or_payload_field_is_rejected_before_authentication(location: str) -> None:
    main = _main()
    valid = main.encode_query(message_id="query-1", correlation_id=None, payload=_query())
    payload = json.loads(_frame_body(valid))
    if location == "envelope":
        payload["unexpected"] = "field"
    else:
        payload["payload"]["unexpected"] = "field"

    _assert_feed_failure(
        _sidecar(),
        _raw_frame(rfc8785.dumps(payload)),
        HistoryFrameReason.SCHEMA_VALIDATION,
    )


def test_pathological_integer_and_nesting_fail_closed_without_raw_exceptions() -> None:
    huge_integer = b'{"x":' + b"1" * 4_301 + b"}"
    _assert_feed_failure(_sidecar(), _raw_frame(huge_integer), HistoryFrameReason.ILLEGAL_NUMBER)

    deeply_nested = b'{"x":' + b"[" * 998 + b"0" + b"]" * 998 + b"}"
    _assert_feed_failure(_sidecar(), _raw_frame(deeply_nested), HistoryFrameReason.INVALID_JSON)


def test_protocol_major_rejects_json_boolean_even_with_a_valid_tag() -> None:
    unsigned = _unsigned_query(_query())
    unsigned["protocol_major"] = True
    frame = _signed_frame(unsigned, key=MAIN_TO_SIDECAR_KEY, direction=b"main-to-sidecar")

    _assert_feed_failure(_sidecar(), frame, HistoryFrameReason.SCHEMA_VALIDATION)


@pytest.mark.parametrize(
    ("target", "reason"),
    [
        (_sidecar(main_to_sidecar_key=b"x" * 32), HistoryFrameReason.BAD_AUTH_TAG),
        (_sidecar(session_id="session-2"), HistoryFrameReason.SESSION_MISMATCH),
        (_sidecar(protocol_minor=1), HistoryFrameReason.PROTOCOL_MISMATCH),
    ],
)
def test_wrong_key_session_or_minor_closes_session(
    target: PostHandshakeHistorySession,
    reason: HistoryFrameReason,
) -> None:
    frame = _main().encode_query(message_id="query-1", correlation_id=None, payload=_query())

    _assert_feed_failure(target, frame, reason)


def test_bad_tag_and_authenticated_length_tamper_close_session() -> None:
    frame = _main().encode_query(message_id="query-1", correlation_id=None, payload=_query())
    payload = json.loads(_frame_body(frame))
    payload["auth_tag"] = ("0" if payload["auth_tag"][0] != "0" else "1") + payload["auth_tag"][1:]
    _assert_feed_failure(_sidecar(), _raw_frame(rfc8785.dumps(payload)), HistoryFrameReason.BAD_AUTH_TAG)

    wrong_length_auth = _signed_frame(
        _unsigned_query(_query()),
        key=MAIN_TO_SIDECAR_KEY,
        direction=b"main-to-sidecar",
        authenticated_length_offset=1,
    )
    _assert_feed_failure(_sidecar(), wrong_length_auth, HistoryFrameReason.BAD_AUTH_TAG)


def test_direction_label_rejects_reflection_even_when_direction_keys_match() -> None:
    shared_key = b"k" * 32
    main = _main(main_to_sidecar_key=shared_key, sidecar_to_main_key=shared_key)
    reflected = main.encode_query(message_id="query-1", correlation_id=None, payload=_query())

    _assert_feed_failure(main, reflected, HistoryFrameReason.BAD_AUTH_TAG)


def test_sequence_duplicate_gap_and_reorder_close_sessions() -> None:
    main = _main()
    first = main.encode_query(message_id="query-1", correlation_id=None, payload=_query(operation_id="op-1"))
    second = main.encode_query(message_id="query-2", correlation_id=None, payload=_query(operation_id="op-2"))
    third = main.encode_query(message_id="query-3", correlation_id=None, payload=_query(operation_id="op-3"))

    duplicate = _sidecar()
    duplicate.feed(first)
    _assert_feed_failure(duplicate, first, HistoryFrameReason.SEQUENCE_MISMATCH)
    _assert_feed_failure(_sidecar(), second, HistoryFrameReason.SEQUENCE_MISMATCH)
    reordered = _sidecar()
    reordered.feed(first)
    _assert_feed_failure(reordered, third, HistoryFrameReason.SEQUENCE_MISMATCH)


def test_duplicate_message_id_is_rejected_with_a_valid_next_sequence() -> None:
    sidecar = _sidecar()
    first = _signed_frame(
        _unsigned_query(_query(), sequence=1, message_id="same-message"),
        key=MAIN_TO_SIDECAR_KEY,
        direction=b"main-to-sidecar",
    )
    duplicate = _signed_frame(
        _unsigned_query(_query(), sequence=2, message_id="same-message"),
        key=MAIN_TO_SIDECAR_KEY,
        direction=b"main-to-sidecar",
    )
    sidecar.feed(first)

    _assert_feed_failure(sidecar, duplicate, HistoryFrameReason.DUPLICATE_MESSAGE_ID)


def test_cross_direction_message_id_reuse_closes_sidecar() -> None:
    main = _main()
    sidecar = _sidecar()
    query = _query()
    sidecar.feed(main.encode_query(message_id="same-message", correlation_id=None, payload=query))

    with pytest.raises(HistoryFrameError) as caught:
        sidecar.encode_result(
            message_id="same-message",
            reply_to="same-message",
            payload=_not_found(query),
        )

    assert caught.value.reason_code == HistoryFrameReason.DUPLICATE_MESSAGE_ID.value
    assert sidecar.closed is True


def test_wrong_consumed_and_mismatched_replies_close_main() -> None:
    query = _query()
    main = _main()
    main.encode_query(message_id="query-message-1", correlation_id="correlation-1", payload=query)
    unknown = _signed_frame(
        _unsigned_result(_not_found(query), reply_to="unknown"),
        key=SIDECAR_TO_MAIN_KEY,
        direction=b"sidecar-to-main",
    )
    _assert_feed_failure(main, unknown, HistoryFrameReason.WRONG_REPLY)

    main = _main()
    sidecar = _sidecar()
    query_frame = main.encode_query(message_id="query-message-1", correlation_id="correlation-1", payload=query)
    sidecar.feed(query_frame)
    main.feed(
        sidecar.encode_result(
            message_id="result-1",
            reply_to="query-message-1",
            payload=_not_found(query),
        )
    )
    consumed = _signed_frame(
        _unsigned_result(_not_found(query), sequence=2, message_id="result-2"),
        key=SIDECAR_TO_MAIN_KEY,
        direction=b"sidecar-to-main",
    )
    _assert_feed_failure(main, consumed, HistoryFrameReason.WRONG_REPLY)


@pytest.mark.parametrize(
    "unsigned",
    [
        _unsigned_result(_not_found(_query()), correlation_id="wrong-correlation"),
        _unsigned_result(_not_found(_query(attempt_no=2))),
    ],
)
def test_result_correlation_and_identity_must_exactly_echo_query(unsigned: dict[str, object]) -> None:
    main = _main()
    main.encode_query(message_id="query-message-1", correlation_id="correlation-1", payload=_query())
    frame = _signed_frame(unsigned, key=SIDECAR_TO_MAIN_KEY, direction=b"sidecar-to-main")

    _assert_feed_failure(main, frame, HistoryFrameReason.RESULT_ECHO_MISMATCH)


def test_query_signed_for_receive_direction_is_still_rejected_by_role() -> None:
    reversed_query = _signed_frame(
        _unsigned_query(_query()),
        key=SIDECAR_TO_MAIN_KEY,
        direction=b"sidecar-to-main",
    )

    _assert_feed_failure(_main(), reversed_query, HistoryFrameReason.UNEXPECTED_DIRECTION)


def test_session_message_pending_and_sequence_state_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(frames, "MAX_SESSION_MESSAGES", 1)
    main = _main()
    main.encode_query(message_id="query-1", correlation_id=None, payload=_query(operation_id="op-1"))
    with pytest.raises(HistoryFrameError, match=HistoryFrameReason.MESSAGE_LIMIT.value):
        main.encode_query(message_id="query-2", correlation_id=None, payload=_query(operation_id="op-2"))
    assert main.closed is True

    monkeypatch.setattr(frames, "MAX_SESSION_MESSAGES", 100)
    monkeypatch.setattr(frames, "MAX_PENDING_QUERIES", 1)
    main = _main()
    main.encode_query(message_id="query-1", correlation_id=None, payload=_query(operation_id="op-1"))
    with pytest.raises(HistoryFrameError, match=HistoryFrameReason.PENDING_QUERY_LIMIT.value):
        main.encode_query(message_id="query-2", correlation_id=None, payload=_query(operation_id="op-2"))
    assert main.closed is True

    main = _main()
    main._next_send_sequence = JSON_SAFE_INTEGER  # type: ignore[attr-defined]
    with pytest.raises(HistoryFrameError, match=HistoryFrameReason.SEQUENCE_EXHAUSTED.value):
        main.encode_query(message_id="query-1", correlation_id=None, payload=_query())
    assert main.closed is True


def test_errors_and_repr_do_not_leak_key_tag_body_or_internal_exception(caplog: pytest.LogCaptureFixture) -> None:
    key_sentinel = b"K" * 32
    main = _main(main_to_sidecar_key=key_sentinel)
    valid = main.encode_query(message_id="query-1", correlation_id=None, payload=_query())
    payload = json.loads(_frame_body(valid))
    auth_tag = payload["auth_tag"]
    payload["leak-sentinel"] = "private-body-sentinel"
    session = _sidecar(main_to_sidecar_key=key_sentinel)

    error = _assert_feed_failure(
        session,
        _raw_frame(rfc8785.dumps(payload)),
        HistoryFrameReason.SCHEMA_VALIDATION,
    )

    surfaces = "\n".join(
        (
            str(error),
            repr(error),
            repr(error.args),
            repr(error.__dict__),
            repr(error.__cause__),
            repr(error.__context__),
            repr(session),
            caplog.text,
        )
    )
    assert key_sentinel.decode() not in surfaces
    assert auth_tag not in surfaces
    assert "private-body-sentinel" not in surfaces
    assert error.__cause__ is None
    assert error.__context__ is None


def test_source_port_frame_kernel_has_no_project_side_effect_dependency_or_business_caller() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    core_tree = ast.parse(CORE_MODULE_PATH.read_text(encoding="utf-8"))
    imported_modules = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported_modules <= {
        "__future__",
        "dataclasses",
        "enum",
        "pydantic",
        "seektalent.source_port.authenticated_frame_core",
        "seektalent.source_port.history_contract",
        "seektalent.source_port.wire_primitives",
        "typing",
    }
    assert not any(isinstance(node, ast.Name) and node.id == "Any" for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Name) and node.id == "Any" for node in ast.walk(core_tree))

    production_callers = []
    for path in (PROJECT_ROOT / "src").rglob("*.py"):
        if path == MODULE_PATH:
            continue
        source = path.read_text(encoding="utf-8")
        if "authenticated_history_frames" in source:
            production_callers.append(path.relative_to(PROJECT_ROOT).as_posix())
    # Reconciliation imports only canonical semantics; the mixed session owns framing.
    assert production_callers == [
        "src/seektalent/source_history_reconciliation.py",
        "src/seektalent/source_port/authenticated_source_port_session.py",
        "src/seektalent/source_port/sidecar_transport.py",
    ]

    runner = (PROJECT_ROOT / "src" / "seektalent_workbench_v2" / "runtime_runner.py").read_text(encoding="utf-8")
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "resume_recoverable=False" in runner
    assert "source-port" not in pyproject
