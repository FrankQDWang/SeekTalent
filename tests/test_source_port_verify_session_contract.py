from __future__ import annotations

import ast
from collections import UserDict
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError
import pytest
import rfc8785

from seektalent.source_port.operation_dispatch import (
    DispatchAuthorizationV1,
    validate_dispatch_authorization,
)
from seektalent.source_port.verify_session_contract import (
    VerifySessionRequestV1,
    VerifySessionResultV1,
    canonical_dispatch_authorization_bytes,
    canonical_request_intent_bytes,
    canonical_request_intent_hash,
    canonical_verify_session_result_bytes,
    dispatch_authorization_digest,
    runtime_attempt_fence_ref,
    validate_outbox_redelivery,
    validate_verify_session_result_echo,
    verify_session_result_hash,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = PROJECT_ROOT / "src" / "seektalent" / "source_port" / "verify_session_contract.py"
OPERATION_DISPATCH_PATH = PROJECT_ROOT / "src" / "seektalent" / "source_port" / "operation_dispatch.py"
WIRE_PRIMITIVES_PATH = PROJECT_ROOT / "src" / "seektalent" / "source_port" / "wire_primitives.py"
CANONICAL_JSON_PATH = PROJECT_ROOT / "src" / "seektalent" / "canonical_json.py"
VERIFY_SESSION_FRAMES_PATH = (
    PROJECT_ROOT / "src" / "seektalent" / "source_port" / "authenticated_verify_session_frames.py"
)
PRODUCTION_UNREACHABLE_WTSCLI_COMPOSITION_PATH = (
    PROJECT_ROOT / "src" / "seektalent" / "wtscli_verify_session_classification.py"
)
PLAN_PATH = PROJECT_ROOT / "docs" / "plans" / "external-execution-plane-v1-source-execution-port.md"
RAW_FENCE_TOKEN = "raw-fence-token-canary-" + "x" * 64
LEAK_CANARY = "RAW-FENCE-TOKEN-MUST-NOT-LEAK-" + "z" * 64
SHORT_LEAK_CANARY = "short-raw-fence-canary"
REQUEST_HASH_VECTOR = "67781fd05d58043915e2b1e6c82166ad6aafbe47cb9c2f32672d962db13493ff"
FENCE_REF_VECTOR = "4203e2774f65d2556d34cd28c35154df755106f739e8d71abed936e07693fdb4"
DISPATCH_DIGEST_VECTOR = "c8d31a012d53276db8ed65d84f8c973149f40730f85ea28e00ade60473a6affd"
SAFE_RETRY_DISPATCH_DIGEST_VECTOR = "6bfebde2f3cac1eb8bfdc2efe868979a2a858ea98bbac0fe1c886e5afffe8c2f"
RESULT_HASH_VECTOR = "734f5a4d1555adbe9350de9974d7e37099b1d08fe598ed1524208f9e54c6d9ce"


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


def _safe_retry_request(**updates: object) -> VerifySessionRequestV1:
    values: dict[str, object] = {
        "attempt_no": 2,
        "expected_source_operation_ledger_revision": 2,
        "expected_reconciliation_revision": 1,
        "dispatch_intent_revision": 2,
        "dispatch_authorization_ordinal": 2,
        "safe_retry_commit_ref": "safe-retry-commit-2",
    }
    values.update(updates)
    return _request(**values)


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


class _CustomMapping(Mapping[str, object]):
    def __init__(self, values: dict[str, object]) -> None:
        self.values = values

    def __getitem__(self, key: str) -> object:
        return self.values[key]

    def __iter__(self):
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)

    def __repr__(self) -> str:
        return repr(self.values)


class _CanaryObject:
    def __repr__(self) -> str:
        return f"<canary {RAW_FENCE_TOKEN}>"


def test_request_is_strict_closed_frozen_and_only_serializes_the_raw_token_in_the_full_submit() -> None:
    request = _request()

    assert request.identity.operation_kind == "verify_session"
    assert request.delivery.delivery_mode == "initial"
    assert request.model_dump()["runtime_attempt_fence_token"] == RAW_FENCE_TOKEN
    assert RAW_FENCE_TOKEN not in repr(request)
    with pytest.raises(ValidationError):
        request.identity.attempt_no = 2  # type: ignore[misc]

    payload = request.model_dump()
    payload["unknown"] = "escape-hatch"
    with pytest.raises(ValidationError):
        VerifySessionRequestV1.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("identity", "attempt_no"), True),
        (("identity", "attempt_no"), 1.0),
        (("identity", "request_hash"), "A" * 64),
        (("identity", "run_id"), "run\n1"),
        (("identity", "run_id"), "x" * 97),
        (("identity", "source"), "linkedin"),
        (("identity", "operation_kind"), "search"),
        (("profile_binding_ref",), "\ud800"),
        (("required_capabilities",), ["bridge"] * 17),
        (("verify_search_surface",), 1),
        (("identity", "deadline", "clock"), "wall_clock"),
        (("identity", "deadline", "value"), 900_001),
    ],
)
def test_request_rejects_wire_coercion_invalid_unicode_and_out_of_bounds_values(
    path: tuple[str, ...], value: object
) -> None:
    payload = _request().model_dump()
    target: object = payload
    for key in path[:-1]:
        assert isinstance(target, dict)
        target = target[key]
    assert isinstance(target, dict)
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        VerifySessionRequestV1.model_validate(payload)


@pytest.mark.parametrize("token", ("😀" * 8, "é" * 16))
def test_runtime_fence_token_requires_at_least_32_unicode_code_points(token: str) -> None:
    with pytest.raises(ValueError, match="source_port_runtime_fence_token_invalid"):
        _request(runtime_attempt_fence_token=token)


def test_canonical_request_hash_and_fence_ref_match_manual_rfc8785_and_length_prefix_vectors() -> None:
    request = _request()
    expected_intent = {
        "accepted_requirement_revision_id": "requirement-revision-1",
        "component_receipt_refs": ["main-receipt-1"],
        "contract_version": "seektalent.source.verify-session.request/v1",
        "operation_id": "verify-session-1",
        "operation_kind": "verify_session",
        "profile_binding_ref": "profile-binding-1",
        "profile_mode": "existing_profile",
        "provider_account_ref": "provider-account-1",
        "required_capabilities": ["bridge", "extension", "profile_lock", "search_surface"],
        "run_id": "run-1",
        "source": "liepin",
        "user_interaction_policy": "observe_only",
        "verify_search_surface": True,
    }
    expected_intent_bytes = rfc8785.dumps(expected_intent)

    assert canonical_request_intent_bytes(request) == expected_intent_bytes
    assert canonical_request_intent_hash(request) == sha256(expected_intent_bytes).hexdigest()
    assert canonical_request_intent_hash(request) == REQUEST_HASH_VECTOR
    assert request.identity.request_hash == canonical_request_intent_hash(request)

    def lp(value: bytes) -> bytes:
        return len(value).to_bytes(4, "big") + value

    expected_fence_ref = sha256(
        b"".join(
            (
                lp(b"seektalent-runtime-attempt-fence-ref-v1"),
                lp(RAW_FENCE_TOKEN.encode()),
                lp(b"run-1"),
                lp(b"verify-session-1"),
                (1).to_bytes(8, "big"),
                lp(bytes.fromhex(request.identity.request_hash)),
                (1).to_bytes(8, "big"),
                (0).to_bytes(8, "big"),
            )
        )
    ).hexdigest()

    assert request.identity.runtime_attempt_fence_ref == expected_fence_ref
    assert expected_fence_ref == FENCE_REF_VECTOR
    assert (
        runtime_attempt_fence_ref(
            raw_runtime_attempt_fence_token=RAW_FENCE_TOKEN,
            run_id=request.identity.run_id,
            operation_id=request.identity.operation_id,
            attempt_no=request.identity.attempt_no,
            request_hash=request.identity.request_hash,
            expected_source_operation_ledger_revision=request.identity.expected_source_operation_ledger_revision,
            expected_reconciliation_revision=request.identity.expected_reconciliation_revision,
        )
        == expected_fence_ref
    )


def test_request_hash_has_the_frozen_inclusion_and_exclusion_matrix() -> None:
    baseline = _request()

    for changed in (
        _request(run_id="run-2"),
        _request(operation_id="verify-session-2"),
        _request(accepted_requirement_revision_id="requirement-revision-2"),
        _request(profile_binding_ref="profile-binding-2"),
        _request(required_capabilities=("bridge", "extension")),
        _request(component_receipt_refs=("main-receipt-2",)),
    ):
        assert changed.identity.request_hash != baseline.identity.request_hash

    for unchanged in (
        _request(attempt_no=2),
        _request(runtime_attempt_fence_token="raw-fence-token-rotated-" + "y" * 64),
        _request(deadline_value=60_001),
        _request(correlation_id="correlation-2"),
        _request(profile_binding_generation=2),
        _request(browser_control_scope_id="browser-scope-2"),
        _request(delivery_mode="outbox_redelivery"),
    ):
        assert unchanged.identity.request_hash == baseline.identity.request_hash


def test_main_style_safe_retry_rotates_epoch_authority_without_changing_logical_hash() -> None:
    initial = _request()
    safe_retry = _safe_retry_request(
        runtime_attempt_fence_token="safe-retry-runtime-fence-" + "y" * 64,
        profile_binding_generation=2,
        browser_control_scope_id="browser-scope-2",
        deadline_value=45_000,
        correlation_id="safe-retry-correlation-2",
        dispatch_intent_id="dispatch-intent-2",
        source_operation_acceptance_ref="source-acceptance-1",
    )

    assert safe_retry.delivery.authorization.dispatch_authorization_ordinal == 2
    assert safe_retry.identity.attempt_no == 2
    assert safe_retry.identity.profile_binding_generation == 2
    assert safe_retry.identity.runtime_attempt_fence_ref != initial.identity.runtime_attempt_fence_ref
    assert safe_retry.identity.request_hash == initial.identity.request_hash

    for changed_logical_binding in (
        _safe_retry_request(profile_binding_ref="profile-binding-2"),
        _safe_retry_request(provider_account_ref="provider-account-2"),
        _safe_retry_request(required_capabilities=("bridge", "extension")),
    ):
        assert changed_logical_binding.identity.request_hash != initial.identity.request_hash


def test_dispatch_authorization_digest_is_durable_only_and_redelivery_reuses_it_exactly() -> None:
    initial = _request()
    redelivery = _request(delivery_mode="outbox_redelivery")

    assert initial.delivery.authorization == redelivery.delivery.authorization
    assert dispatch_authorization_digest(initial.delivery.authorization) == DISPATCH_DIGEST_VECTOR
    assert dispatch_authorization_digest(initial.delivery.authorization) == dispatch_authorization_digest(
        redelivery.delivery.authorization
    )
    assert canonical_dispatch_authorization_bytes(initial.delivery.authorization) == rfc8785.dumps(
        {
            "attempt_no": 1,
            "dispatch_authorization_ordinal": 1,
            "dispatch_intent_id": "dispatch-intent-1",
            "dispatch_intent_revision": 1,
            "expected_reconciliation_revision": 0,
            "expected_source_operation_ledger_revision": 1,
            "operation_id": "verify-session-1",
            "request_hash": initial.identity.request_hash,
            "run_id": "run-1",
            "safe_retry_commit_ref": None,
            "source_operation_acceptance_ref": "source-acceptance-1",
        }
    )
    validate_outbox_redelivery(initial, redelivery)

    changed_authorization = _request(
        delivery_mode="outbox_redelivery",
        dispatch_intent_id="dispatch-intent-2",
    )
    assert changed_authorization.delivery.authorization.dispatch_intent_digest != (
        initial.delivery.authorization.dispatch_intent_digest
    )
    with pytest.raises(ValueError, match="outbox_redelivery"):
        validate_outbox_redelivery(initial, changed_authorization)

    renewed_deadline = _request(delivery_mode="outbox_redelivery", deadline_value=60_001)
    with pytest.raises(ValueError, match="outbox_redelivery"):
        validate_outbox_redelivery(initial, renewed_deadline)

    shorter_deadline = _request(delivery_mode="outbox_redelivery", deadline_value=59_999)
    validate_outbox_redelivery(initial, shorter_deadline)

    current_fence_token = _request(
        delivery_mode="outbox_redelivery",
        runtime_attempt_fence_token="raw-fence-token-rotated-" + "y" * 64,
    )
    assert current_fence_token.identity.runtime_attempt_fence_ref != initial.identity.runtime_attempt_fence_ref
    assert current_fence_token.delivery.authorization == initial.delivery.authorization
    assert dispatch_authorization_digest(current_fence_token.delivery.authorization) == dispatch_authorization_digest(
        initial.delivery.authorization
    )
    assert current_fence_token.delivery.authorization.dispatch_authorization_ordinal == 1
    validate_outbox_redelivery(initial, current_fence_token)

    changed_correlation_scope = _request(
        delivery_mode="outbox_redelivery",
        correlation_id="correlation-2",
        browser_control_scope_id="browser-scope-2",
    )
    validate_outbox_redelivery(initial, changed_correlation_scope)

    for changed_logical_body in (
        _request(delivery_mode="outbox_redelivery", profile_binding_ref="profile-binding-2"),
        _request(delivery_mode="outbox_redelivery", required_capabilities=("bridge", "extension")),
    ):
        with pytest.raises(ValueError, match="outbox_redelivery"):
            validate_outbox_redelivery(initial, changed_logical_body)

    for field, value in (
        ("dispatch_authorization_ordinal", 2),
        ("expected_source_operation_ledger_revision", 2),
        ("expected_reconciliation_revision", 1),
    ):
        invalid_authorization = initial.delivery.authorization.model_dump()
        invalid_authorization[field] = value
        with pytest.raises(ValidationError):
            type(initial.delivery.authorization).model_validate(invalid_authorization)

    payload = redelivery.model_dump()
    payload["delivery"]["delivery_mode"] = "safe_retry"
    with pytest.raises(ValidationError):
        VerifySessionRequestV1.model_validate(payload)


def test_dispatch_authorization_exposes_only_explicit_epoch_constructors() -> None:
    initial_request = _request()
    initial = DispatchAuthorizationV1.create_initial(
        identity=initial_request.identity,
        dispatch_intent_id="dispatch-intent-1",
        dispatch_intent_revision=1,
        source_operation_acceptance_ref="source-acceptance-1",
    )
    safe_retry_request = _safe_retry_request()
    safe_retry = DispatchAuthorizationV1.create_safe_retry(
        identity=safe_retry_request.identity,
        dispatch_intent_id="dispatch-intent-1",
        dispatch_intent_revision=2,
        dispatch_authorization_ordinal=2,
        safe_retry_commit_ref="safe-retry-commit-2",
        source_operation_acceptance_ref="source-acceptance-1",
    )

    assert not hasattr(DispatchAuthorizationV1, "create")
    assert initial == initial_request.delivery.authorization
    assert safe_retry == safe_retry_request.delivery.authorization


@pytest.mark.parametrize(
    "updates",
    (
        {"dispatch_authorization_ordinal": 2, "safe_retry_commit_ref": None},
        {"dispatch_authorization_ordinal": 1, "safe_retry_commit_ref": "safe-retry-commit-1"},
        {"expected_source_operation_ledger_revision": 2},
        {"expected_reconciliation_revision": 1},
    ),
)
def test_initial_and_safe_retry_authorization_matrix_fails_closed(updates: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError, ValidationError)):
        _request(**updates)


@pytest.mark.parametrize("ordinal", (True, 2.0, 0, -1, 2**53))
def test_safe_retry_authorization_rejects_non_json_safe_ordinals(ordinal: object) -> None:
    with pytest.raises((TypeError, ValueError, ValidationError)):
        _safe_retry_request(dispatch_authorization_ordinal=ordinal)


@pytest.mark.parametrize(
    "updates",
    (
        {"safe_retry_commit_ref": ""},
        {"safe_retry_commit_ref": "x" * 257},
        {"safe_retry_commit_ref": "retry\ncommit"},
        {"expected_source_operation_ledger_revision": True},
        {"expected_source_operation_ledger_revision": 2.0},
        {"expected_source_operation_ledger_revision": 0},
        {"expected_source_operation_ledger_revision": 2**53},
        {"expected_reconciliation_revision": True},
        {"expected_reconciliation_revision": 1.0},
        {"expected_reconciliation_revision": 0},
        {"expected_reconciliation_revision": 2**53},
    ),
)
def test_safe_retry_authorization_rejects_invalid_refs_and_revisions(updates: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError, ValidationError)):
        _safe_retry_request(**updates)


def test_safe_retry_authorization_round_trips_with_fixed_canonical_vector() -> None:
    request = _safe_retry_request()
    authorization = request.delivery.authorization
    expected_payload = {
        "attempt_no": 2,
        "dispatch_authorization_ordinal": 2,
        "dispatch_intent_id": "dispatch-intent-1",
        "dispatch_intent_revision": 2,
        "expected_reconciliation_revision": 1,
        "expected_source_operation_ledger_revision": 2,
        "operation_id": "verify-session-1",
        "request_hash": request.identity.request_hash,
        "run_id": "run-1",
        "safe_retry_commit_ref": "safe-retry-commit-2",
        "source_operation_acceptance_ref": "source-acceptance-1",
    }

    assert VerifySessionRequestV1.model_validate_json(request.model_dump_json()) == request
    assert canonical_dispatch_authorization_bytes(authorization) == rfc8785.dumps(expected_payload)
    assert dispatch_authorization_digest(authorization) == SAFE_RETRY_DISPATCH_DIGEST_VECTOR
    assert authorization.dispatch_intent_digest == SAFE_RETRY_DISPATCH_DIGEST_VECTOR


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("safe_retry_commit_ref", "safe-retry-commit-3"),
        ("dispatch_authorization_ordinal", 3),
        ("expected_source_operation_ledger_revision", 3),
        ("expected_reconciliation_revision", 2),
    ),
)
def test_safe_retry_authorization_tampering_changes_digest_and_cannot_match_exact_epoch(
    field: str,
    value: object,
) -> None:
    request = _safe_retry_request()
    payload = request.delivery.authorization.model_dump()
    payload[field] = value

    with pytest.raises(ValidationError, match="digest"):
        DispatchAuthorizationV1.model_validate(payload)

    changed = _safe_retry_request(**{field: value})
    assert changed.delivery.authorization.dispatch_intent_digest != (
        request.delivery.authorization.dispatch_intent_digest
    )
    with pytest.raises(ValueError, match="outbox_redelivery"):
        validate_outbox_redelivery(
            request,
            _safe_retry_request(delivery_mode="outbox_redelivery", **{field: value}),
        )


def test_safe_retry_outbox_redelivery_requires_the_exact_authorization_and_cannot_extend_deadline() -> None:
    initial = _safe_retry_request()
    redelivery = _safe_retry_request(
        delivery_mode="outbox_redelivery",
        runtime_attempt_fence_token="current-safe-retry-fence-token-" + "y" * 64,
        deadline_value=59_999,
        correlation_id="correlation-2",
        browser_control_scope_id="browser-scope-2",
    )

    validate_outbox_redelivery(initial, redelivery)
    assert redelivery.delivery.authorization == initial.delivery.authorization
    assert redelivery.identity.runtime_attempt_fence_ref != initial.identity.runtime_attempt_fence_ref

    with pytest.raises(ValueError, match="outbox_redelivery"):
        validate_outbox_redelivery(
            initial,
            _safe_retry_request(delivery_mode="outbox_redelivery", deadline_value=60_001),
        )

    for transport_only in (
        _request(runtime_attempt_fence_token="new-fence-token-" + "z" * 64),
        _request(attempt_no=2),
        _request(deadline_value=59_999),
    ):
        assert transport_only.delivery.authorization.dispatch_authorization_ordinal == 1
        with pytest.raises(ValueError, match="identity_mismatch"):
            validate_dispatch_authorization(
                transport_only.identity,
                initial.delivery.authorization,
            )


def test_raw_fence_token_never_leaks_from_repr_errors_or_canonical_projections_and_bypasses_revalidate() -> None:
    request = _request()
    result = _result(request)

    assert VerifySessionRequestV1.model_validate(request) == request
    assert RAW_FENCE_TOKEN not in canonical_request_intent_bytes(request).decode()
    assert RAW_FENCE_TOKEN not in canonical_dispatch_authorization_bytes(request.delivery.authorization).decode()
    assert RAW_FENCE_TOKEN not in canonical_verify_session_result_bytes(result).decode()
    assert RAW_FENCE_TOKEN not in request.identity.runtime_attempt_fence_ref

    invalid_payload = request.model_dump()
    invalid_payload["runtime_attempt_fence_token"] = LEAK_CANARY[:12]
    with pytest.raises(ValidationError) as invalid:
        VerifySessionRequestV1.model_validate(invalid_payload)
    assert LEAK_CANARY not in str(invalid.value)
    assert LEAK_CANARY not in repr(invalid.value.errors())

    invalid_identity = request.model_dump()
    invalid_identity["identity"]["request_hash"] = "a" * 64
    with pytest.raises(ValidationError) as invalid_semantics:
        VerifySessionRequestV1.model_validate(invalid_identity)
    assert RAW_FENCE_TOKEN not in str(invalid_semantics.value.errors())
    assert RAW_FENCE_TOKEN not in repr(invalid_semantics.value.errors())

    bypassed_identity = request.identity.model_copy(update={"request_hash": "A" * 64})
    bypassed = VerifySessionRequestV1.model_construct(
        **{
            **request.model_dump(),
            "identity": bypassed_identity,
            "runtime_attempt_fence_token": LEAK_CANARY,
        }
    )
    with pytest.raises(ValidationError) as invalid_submit_bypass:
        VerifySessionRequestV1.model_validate(bypassed)
    assert LEAK_CANARY not in str(invalid_submit_bypass.value)
    assert LEAK_CANARY not in repr(invalid_submit_bypass.value.errors())
    with pytest.raises(ValueError) as invalid_bypass:
        canonical_request_intent_bytes(bypassed)
    assert LEAK_CANARY not in str(invalid_bypass.value)


@pytest.mark.parametrize("mapping_type", (UserDict, _CustomMapping))
def test_mapping_submit_input_redacts_an_invalid_raw_fence_token(mapping_type: type[Mapping[str, object]]) -> None:
    payload = _request().model_dump()
    payload["runtime_attempt_fence_token"] = SHORT_LEAK_CANARY

    with pytest.raises(ValidationError) as invalid:
        VerifySessionRequestV1.model_validate(mapping_type(payload))

    assert SHORT_LEAK_CANARY not in str(invalid.value)
    assert SHORT_LEAK_CANARY not in repr(invalid.value.errors())


@pytest.mark.parametrize("mapping_type", (UserDict, _CustomMapping))
def test_mapping_submit_input_hides_a_valid_raw_fence_token_on_semantic_error(
    mapping_type: type[Mapping[str, object]],
) -> None:
    payload = _request().model_dump()
    payload["identity"]["request_hash"] = "a" * 64

    with pytest.raises(ValidationError) as invalid:
        VerifySessionRequestV1.model_validate(mapping_type(payload))

    assert RAW_FENCE_TOKEN not in str(invalid.value)
    assert RAW_FENCE_TOKEN not in repr(invalid.value.errors())


@pytest.mark.parametrize(
    "input_value",
    ([RAW_FENCE_TOKEN], (RAW_FENCE_TOKEN,), _CanaryObject()),
    ids=("list", "tuple", "repr_object"),
)
def test_non_mapping_submit_input_never_exposes_a_raw_fence_token(input_value: object) -> None:
    with pytest.raises(ValidationError) as invalid:
        VerifySessionRequestV1.model_validate(input_value)

    assert RAW_FENCE_TOKEN not in str(invalid.value)
    assert RAW_FENCE_TOKEN not in repr(invalid.value.errors())


def test_operation_and_idempotency_identity_facts_stay_unambiguous_without_a_runtime_kernel() -> None:
    baseline = _request()
    same = _request()
    same_key_different_hash = _request(required_capabilities=("bridge", "extension"))
    same_operation_different_key = _request(idempotency_key="verify-session-key-2")

    assert same.identity == baseline.identity
    assert same_key_different_hash.identity.operation_id == baseline.identity.operation_id
    assert same_key_different_hash.identity.idempotency_key == baseline.identity.idempotency_key
    assert same_key_different_hash.identity.request_hash != baseline.identity.request_hash
    assert same_operation_different_key.identity.operation_id == baseline.identity.operation_id
    assert same_operation_different_key.identity.idempotency_key != baseline.identity.idempotency_key


def test_verify_session_result_has_only_closed_safe_facts_and_echoes_main_receipts() -> None:
    request = _request()
    result = _result(request)

    assert canonical_verify_session_result_bytes(result) == rfc8785.dumps(result.model_dump(mode="json"))
    assert verify_session_result_hash(result) == sha256(canonical_verify_session_result_bytes(result)).hexdigest()
    assert verify_session_result_hash(result) == RESULT_HASH_VECTOR
    validate_verify_session_result_echo(request, result)

    for forbidden in (
        "current_url",
        "page_ref",
        "window_id",
        "profile_path",
        "cookie",
        "token",
        "screenshot",
        "raw_error",
        "payload",
        "receipt",
    ):
        with pytest.raises(ValidationError):
            VerifySessionResultV1.model_validate({**result.model_dump(), forbidden: "escape"})

    with pytest.raises(ValidationError):
        VerifySessionResultV1.model_validate({**result.model_dump(), "safe_reason_code": "arbitrary_machine_semantics"})

    mismatched = _result(request, component_receipt_refs=("sidecar-self-signed-receipt",))
    with pytest.raises(ValueError, match="receipt"):
        validate_verify_session_result_echo(request, mismatched)


@pytest.mark.parametrize(
    "safe_reason_code",
    (
        "liepin_opencli_bridge_integrity_failed",
        "liepin_opencli_bridge_wrong_implementation",
        "liepin_opencli_bridge_build_mismatch",
        "liepin_opencli_bridge_protocol_mismatch",
        "liepin_opencli_bridge_capability_missing",
    ),
)
def test_verify_session_result_accepts_only_the_named_bridge_identity_failure_taxonomy(
    safe_reason_code: str,
) -> None:
    request = _request()

    result = _result(
        request,
        bridge_readiness="not_ready",
        session_readiness="not_ready",
        safe_reason_code=safe_reason_code,
    )

    assert result.safe_reason_code == safe_reason_code
    with pytest.raises(ValidationError):
        _result(
            request,
            bridge_readiness="not_ready",
            session_readiness="not_ready",
            safe_reason_code=f"{safe_reason_code}_unknown",
        )


def test_initial_result_replays_to_legal_redelivery_but_rejects_stable_identity_tampering() -> None:
    initial = _request()
    result = _result(initial)
    redelivery = _request(
        delivery_mode="outbox_redelivery",
        runtime_attempt_fence_token="current-fence-token-" + "y" * 64,
        deadline_value=59_999,
        correlation_id="correlation-2",
        browser_control_scope_id="browser-scope-2",
    )

    validate_outbox_redelivery(initial, redelivery)
    assert redelivery.identity.runtime_attempt_fence_ref != initial.identity.runtime_attempt_fence_ref
    assert redelivery.delivery.authorization == initial.delivery.authorization
    validate_verify_session_result_echo(redelivery, result)
    assert verify_session_result_hash(result) == RESULT_HASH_VECTOR

    for field, value in (
        ("run_id", "run-2"),
        ("operation_id", "verify-session-2"),
        ("idempotency_key", "verify-session-key-2"),
        ("request_hash", "a" * 64),
        ("attempt_no", 2),
        ("accepted_requirement_revision_id", "requirement-revision-2"),
        ("profile_binding_generation", 2),
        ("expected_source_operation_ledger_revision", 2),
        ("expected_reconciliation_revision", 1),
    ):
        tampered = result.model_copy(update={"identity": result.identity.model_copy(update={field: value})})
        with pytest.raises(ValueError, match="identity"):
            validate_verify_session_result_echo(redelivery, tampered)


def test_initial_result_requires_an_exact_identity_echo() -> None:
    request = _request()
    result = _result(request)

    for field, value in (
        ("runtime_attempt_fence_ref", "a" * 64),
        ("deadline", request.identity.deadline.model_copy(update={"value": 59_999})),
        ("correlation_id", "correlation-2"),
        ("browser_control_scope_id", "browser-scope-2"),
    ):
        tampered = result.model_copy(update={"identity": result.identity.model_copy(update={field: value})})
        with pytest.raises(ValueError, match="identity"):
            validate_verify_session_result_echo(request, tampered)


def test_verify_session_result_rejects_another_operation_kind() -> None:
    result = _result(_request())
    payload = result.model_dump()
    payload["identity"]["operation_kind"] = "search"

    with pytest.raises(ValidationError, match="operation_kind"):
        VerifySessionResultV1.model_validate(payload)

    bypassed = VerifySessionResultV1.model_construct(
        **{
            **result.model_dump(),
            "identity": result.identity.model_copy(update={"operation_kind": "search"}),
        }
    )
    with pytest.raises(ValueError, match="verify_session_contract_invalid"):
        canonical_verify_session_result_bytes(bypassed)


def test_contract_stays_source_port_only_with_no_production_caller_or_json_parser() -> None:
    source = CONTRACT_PATH.read_text(encoding="utf-8")
    operation_dispatch = OPERATION_DISPATCH_PATH.read_text(encoding="utf-8")
    wire_primitives = WIRE_PRIMITIVES_PATH.read_text(encoding="utf-8")
    canonical_json = CANONICAL_JSON_PATH.read_text(encoding="utf-8")

    assert "rfc8785.dumps" in canonical_json
    assert "seektalent.canonical_json" in wire_primitives
    assert "json.loads" not in source
    assert "json.dumps" not in source
    imported_modules = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported_modules <= {
        "__future__",
        "collections.abc",
        "hashlib",
        "pydantic",
        "typing",
        "seektalent.source_port.operation_dispatch",
        "seektalent.source_port.wire_primitives",
    }
    kernel_imports = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(ast.parse(operation_dispatch))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert kernel_imports <= {
        "__future__",
        "hashlib",
        "pydantic",
        "typing",
        "seektalent.source_port.wire_primitives",
    }
    assert "VerifySession" not in operation_dispatch

    callers = []
    for path in (PROJECT_ROOT / "src").rglob("*.py"):
        if path in {
            CONTRACT_PATH,
            OPERATION_DISPATCH_PATH,
            WIRE_PRIMITIVES_PATH,
            VERIFY_SESSION_FRAMES_PATH,
            PRODUCTION_UNREACHABLE_WTSCLI_COMPOSITION_PATH,
        }:
            continue
        content = path.read_text(encoding="utf-8")
        imported_modules = {
            node.module if isinstance(node, ast.ImportFrom) else alias.name
            for node in ast.walk(ast.parse(content))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        if any(
            module is not None
            and (
                module.endswith("verify_session_contract")
                or module.endswith("operation_dispatch")
            )
            for module in imported_modules
        ):
            callers.append(path.relative_to(PROJECT_ROOT).as_posix())
    assert set(callers) == {
        "src/seektalent/source_history_reconciliation.py",
        "src/seektalent/sidecar_bootstrap.py",
        "src/seektalent/source_port/_safe_retry_continuity_store.py",
        "src/seektalent/source_port/sidecar_transport.py",
        "src/seektalent/source_port/verify_session_journal_effect.py",
        "src/seektalent/source_port/verify_session_journal_effect_durable.py",
        "src/seektalent/verify_session_closed_loop.py",
        "src/seektalent_runtime_control/safe_retry_turnover.py",
    }


def test_sidecar_history_writer_supports_only_main_authorized_ordinals() -> None:
    ordinal_one_sources = {
        "runtime_control": (PROJECT_ROOT / "src" / "seektalent_runtime_control" / "source_epoch_schema.py"),
        "runtime_control_validation": (PROJECT_ROOT / "src" / "seektalent_runtime_control" / "source_operations.py"),
        "journal_types": PROJECT_ROOT / "src" / "seektalent" / "source_port" / "_command_journal_types.py",
        "journal_engine": PROJECT_ROOT / "src" / "seektalent" / "source_port" / "_command_journal_engine.py",
        "history_contract": PROJECT_ROOT / "src" / "seektalent" / "source_port" / "history_contract.py",
        "history_reader": PROJECT_ROOT / "src" / "seektalent" / "source_port" / "history_sqlite_reader.py",
        "reconciliation": PROJECT_ROOT / "src" / "seektalent" / "source_history_reconciliation.py",
    }
    source = {name: path.read_text(encoding="utf-8") for name, path in ordinal_one_sources.items()}

    assert "dispatch_authorization_ordinal BETWEEN 2 AND 9007199254740991" in source["runtime_control"]
    assert "dispatch.safe_retry_commit_ref is not None" in source["runtime_control_validation"]
    assert "dispatch_authorization_ordinal: Literal[1] = 1" in source["journal_types"]
    assert "dispatch_authorization_ordinal = ?" in source["journal_engine"]
    assert "dispatch_authorization_ordinal: PositiveJsonInteger" in source["history_contract"]
    assert "dispatch_authorization_ordinal BETWEEN 1 AND" in source["history_reader"]
    assert "query.authorization_selector.ordinal == dispatch.dispatch_authorization_ordinal" in source["reconciliation"]
    assert "dispatch.dispatch_authorization_ordinal == 1" in source["reconciliation"]

    production_callers = [
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "src").rglob("*.py")
        if path != CONTRACT_PATH and "VerifySessionRequestV1.create(" in path.read_text(encoding="utf-8")
    ]
    assert production_callers == [
        "src/seektalent/verify_session_closed_loop.py",
    ]
    safe_retry_callers = []
    for path in (PROJECT_ROOT / "src").rglob("*.py"):
        if path in {
            CONTRACT_PATH,
            OPERATION_DISPATCH_PATH,
            ordinal_one_sources["history_contract"],
            ordinal_one_sources["history_reader"],
            ordinal_one_sources["journal_engine"],
        }:
            continue
        content = path.read_text(encoding="utf-8")
        if "create_safe_retry(" in content or "safe_retry_commit_ref" in content:
            safe_retry_callers.append(path.relative_to(PROJECT_ROOT).as_posix())
    assert set(safe_retry_callers) == {
        "src/seektalent/source_port/_safe_retry_continuity_store.py",
        "src/seektalent/verify_session_closed_loop.py",
        "src/seektalent_runtime_control/safe_retry_turnover.py",
        "src/seektalent_runtime_control/source_epoch_schema.py",
        "src/seektalent_runtime_control/source_operations.py",
        "src/seektalent_runtime_control/source_reconciliation.py",
        "src/seektalent_runtime_control/store.py",
    }
    production_turnover_callers = [
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "src").rglob("*.py")
        if path != PROJECT_ROOT / "src" / "seektalent_runtime_control" / "store.py"
        and ".mint_safe_retry_dispatch_epoch(" in path.read_text(encoding="utf-8")
    ]
    assert production_turnover_callers == []


def test_dispatch_plan_keeps_delivery_out_of_the_durable_digest_allowlist() -> None:
    section = PLAN_PATH.read_text(encoding="utf-8").split("### 5.5", 1)[1].split("### 5.6", 1)[0]
    digest_formula = next(line for line in section.splitlines() if line.startswith("`dispatch_intent_digest ="))
    digest_allowlist = digest_formula.split("}))`", 1)[0]

    assert "transport `delivery_mode`" in section
    assert "不是新的 durable authorization，不续 deadline，也不允许第二次 side effect" in section
    assert "safe_retry" in section
    assert "kind" not in digest_allowlist
    assert "delivery_mode" not in digest_allowlist
