"""Strict shared operation identity and durable dispatch authorization."""

from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Literal, TypeAlias

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from seektalent.source_port.wire_primitives import (
    ExactIntegerOne,
    ExactIntegerZero,
    NonNegativeJsonInteger,
    Opaque96,
    Opaque128,
    Opaque256,
    OperationKind,
    PositiveJsonInteger,
    Sha256,
    StrictWireModel,
    canonical_json_bytes,
)


_FENCE_REF_DOMAIN = b"seektalent-runtime-attempt-fence-ref-v1"


class _OperationDispatchModel(StrictWireModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        strict=True,
    )


DeadlineMilliseconds = Annotated[int, Field(strict=True, ge=1, le=900_000)]


class RelativeMonotonicDeadlineV1(_OperationDispatchModel):
    value: DeadlineMilliseconds
    clock: Literal["relative_monotonic"]
    unit: Literal["milliseconds"]


class OperationIdentityV1(_OperationDispatchModel):
    run_id: Opaque96
    operation_id: Opaque96
    attempt_no: PositiveJsonInteger
    source: Literal["liepin"]
    operation_kind: OperationKind
    request_hash: Sha256
    idempotency_key: Opaque128
    correlation_id: Opaque96
    accepted_requirement_revision_id: Opaque96
    runtime_attempt_fence_ref: Sha256
    profile_binding_generation: PositiveJsonInteger
    browser_control_scope_id: Opaque96
    deadline: RelativeMonotonicDeadlineV1
    expected_source_operation_ledger_revision: PositiveJsonInteger
    expected_reconciliation_revision: NonNegativeJsonInteger


class DispatchAuthorizationV1(_OperationDispatchModel):
    """The one currently supported durable main-owned authorization epoch."""

    run_id: Opaque96
    operation_id: Opaque96
    attempt_no: PositiveJsonInteger
    request_hash: Sha256
    dispatch_intent_id: Opaque96
    dispatch_intent_revision: PositiveJsonInteger
    dispatch_authorization_ordinal: ExactIntegerOne
    source_operation_acceptance_ref: Opaque256
    expected_source_operation_ledger_revision: ExactIntegerOne
    expected_reconciliation_revision: ExactIntegerZero
    dispatch_intent_digest: Sha256

    @model_validator(mode="after")
    def validate_digest(self) -> DispatchAuthorizationV1:
        if self.dispatch_intent_digest != _dispatch_authorization_digest_for_values(self):
            raise ValueError("source_port_dispatch_authorization_digest_mismatch")
        return self

    @classmethod
    def create(
        cls,
        *,
        identity: OperationIdentityV1,
        dispatch_intent_id: str,
        dispatch_intent_revision: int,
        source_operation_acceptance_ref: str,
    ) -> DispatchAuthorizationV1:
        validated_identity = _validated_identity(identity)
        provisional = cls.model_construct(
            run_id=validated_identity.run_id,
            operation_id=validated_identity.operation_id,
            attempt_no=validated_identity.attempt_no,
            request_hash=validated_identity.request_hash,
            dispatch_intent_id=dispatch_intent_id,
            dispatch_intent_revision=dispatch_intent_revision,
            dispatch_authorization_ordinal=1,
            source_operation_acceptance_ref=source_operation_acceptance_ref,
            expected_source_operation_ledger_revision=validated_identity.expected_source_operation_ledger_revision,
            expected_reconciliation_revision=validated_identity.expected_reconciliation_revision,
            dispatch_intent_digest="0" * 64,
        )
        digest = _dispatch_authorization_digest_for_values(provisional)
        return cls.model_validate(
            {
                **provisional.model_dump(mode="python"),
                "dispatch_intent_digest": digest,
            },
            strict=True,
        )


class InitialDeliveryV1(_OperationDispatchModel):
    delivery_mode: Literal["initial"]
    authorization: DispatchAuthorizationV1


class OutboxRedeliveryV1(_OperationDispatchModel):
    delivery_mode: Literal["outbox_redelivery"]
    authorization: DispatchAuthorizationV1


DispatchDeliveryV1: TypeAlias = Annotated[
    InitialDeliveryV1 | OutboxRedeliveryV1,
    Field(discriminator="delivery_mode"),
]


_OPAQUE256_ADAPTER = TypeAdapter(Opaque256)
_OPAQUE96_ADAPTER = TypeAdapter(Opaque96)
_POSITIVE_INTEGER_ADAPTER = TypeAdapter(PositiveJsonInteger)
_NON_NEGATIVE_INTEGER_ADAPTER = TypeAdapter(NonNegativeJsonInteger)
_SHA256_ADAPTER = TypeAdapter(Sha256)


def validate_runtime_attempt_fence_token(value: object) -> str:
    """Validate a bearer only while deriving its non-bearer fence reference."""
    try:
        token = _OPAQUE256_ADAPTER.validate_python(value, strict=True)
        byte_length = len(token.encode("utf-8"))
    except (UnicodeEncodeError, ValidationError):
        raise ValueError("source_port_runtime_fence_token_invalid") from None
    if byte_length < 32:
        raise ValueError("source_port_runtime_fence_token_invalid")
    return token


def runtime_attempt_fence_ref(
    *,
    raw_runtime_attempt_fence_token: str,
    run_id: str,
    operation_id: str,
    attempt_no: int,
    request_hash: str,
    expected_source_operation_ledger_revision: int,
    expected_reconciliation_revision: int,
) -> str:
    """Return the §5.3 length-prefixed, non-bearer runtime fence reference."""
    try:
        token = validate_runtime_attempt_fence_token(raw_runtime_attempt_fence_token)
        validated_run_id = _OPAQUE96_ADAPTER.validate_python(run_id, strict=True)
        validated_operation_id = _OPAQUE96_ADAPTER.validate_python(operation_id, strict=True)
        validated_attempt_no = _POSITIVE_INTEGER_ADAPTER.validate_python(attempt_no, strict=True)
        validated_request_hash = _SHA256_ADAPTER.validate_python(request_hash, strict=True)
        validated_ledger_revision = _POSITIVE_INTEGER_ADAPTER.validate_python(
            expected_source_operation_ledger_revision,
            strict=True,
        )
        validated_reconciliation_revision = _NON_NEGATIVE_INTEGER_ADAPTER.validate_python(
            expected_reconciliation_revision,
            strict=True,
        )
    except ValidationError:
        raise ValueError("source_port_runtime_fence_ref_input_invalid") from None
    return _runtime_attempt_fence_ref_for_values(
        raw_runtime_attempt_fence_token=token,
        run_id=validated_run_id,
        operation_id=validated_operation_id,
        attempt_no=validated_attempt_no,
        request_hash=validated_request_hash,
        expected_source_operation_ledger_revision=validated_ledger_revision,
        expected_reconciliation_revision=validated_reconciliation_revision,
    )


def canonical_dispatch_authorization_bytes(authorization: DispatchAuthorizationV1) -> bytes:
    """Return durable authorization bytes, excluding transport delivery and its digest."""
    validated = _validated_authorization(authorization)
    return canonical_json_bytes(_dispatch_authorization_payload(validated))


def dispatch_authorization_digest(authorization: DispatchAuthorizationV1) -> str:
    return sha256(canonical_dispatch_authorization_bytes(authorization)).hexdigest()


def validate_dispatch_authorization(
    identity: OperationIdentityV1,
    authorization: DispatchAuthorizationV1,
) -> None:
    """Require one durable ordinal-one authorization to match an operation identity."""
    validated_identity = _validated_identity(identity)
    validated_authorization = _validated_authorization(authorization)
    if not _authorization_matches_identity(validated_authorization, validated_identity):
        raise ValueError("source_port_dispatch_authorization_identity_mismatch")


def _runtime_attempt_fence_ref_for_values(
    *,
    raw_runtime_attempt_fence_token: str,
    run_id: str,
    operation_id: str,
    attempt_no: int,
    request_hash: str,
    expected_source_operation_ledger_revision: int,
    expected_reconciliation_revision: int,
) -> str:
    return sha256(
        b"".join(
            (
                _length_prefix(_FENCE_REF_DOMAIN),
                _length_prefix(raw_runtime_attempt_fence_token.encode("utf-8")),
                _length_prefix(run_id.encode("utf-8")),
                _length_prefix(operation_id.encode("utf-8")),
                attempt_no.to_bytes(8, "big"),
                _length_prefix(bytes.fromhex(request_hash)),
                expected_source_operation_ledger_revision.to_bytes(8, "big"),
                expected_reconciliation_revision.to_bytes(8, "big"),
            )
        )
    ).hexdigest()


def _authorization_matches_identity(
    authorization: DispatchAuthorizationV1,
    identity: OperationIdentityV1,
) -> bool:
    return (
        authorization.run_id == identity.run_id
        and authorization.operation_id == identity.operation_id
        and authorization.attempt_no == identity.attempt_no
        and authorization.request_hash == identity.request_hash
        and authorization.expected_source_operation_ledger_revision
        == identity.expected_source_operation_ledger_revision
        and authorization.expected_reconciliation_revision == identity.expected_reconciliation_revision
    )


def _dispatch_authorization_payload(authorization: DispatchAuthorizationV1) -> dict[str, object]:
    return {
        "run_id": authorization.run_id,
        "operation_id": authorization.operation_id,
        "attempt_no": authorization.attempt_no,
        "request_hash": authorization.request_hash,
        "dispatch_intent_id": authorization.dispatch_intent_id,
        "dispatch_intent_revision": authorization.dispatch_intent_revision,
        "dispatch_authorization_ordinal": authorization.dispatch_authorization_ordinal,
        "source_operation_acceptance_ref": authorization.source_operation_acceptance_ref,
        "expected_source_operation_ledger_revision": authorization.expected_source_operation_ledger_revision,
        "expected_reconciliation_revision": authorization.expected_reconciliation_revision,
    }


def _dispatch_authorization_digest_for_values(authorization: DispatchAuthorizationV1) -> str:
    return sha256(canonical_json_bytes(_dispatch_authorization_payload(authorization))).hexdigest()


def _validated_identity(value: OperationIdentityV1) -> OperationIdentityV1:
    if type(value) is not OperationIdentityV1:
        raise TypeError("strict OperationIdentityV1 required")
    try:
        return OperationIdentityV1.model_validate(value.model_dump(mode="python", warnings="error"), strict=True)
    except (TypeError, ValueError, ValidationError):
        raise ValueError("source_port_operation_identity_invalid") from None


def _validated_authorization(value: DispatchAuthorizationV1) -> DispatchAuthorizationV1:
    if type(value) is not DispatchAuthorizationV1:
        raise TypeError("strict DispatchAuthorizationV1 required")
    try:
        return DispatchAuthorizationV1.model_validate(value.model_dump(mode="python", warnings="error"), strict=True)
    except (TypeError, ValueError, ValidationError):
        raise ValueError("source_port_dispatch_authorization_invalid") from None


def _length_prefix(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big") + value


__all__ = [
    "DispatchAuthorizationV1",
    "DispatchDeliveryV1",
    "InitialDeliveryV1",
    "OperationIdentityV1",
    "OutboxRedeliveryV1",
    "RelativeMonotonicDeadlineV1",
    "canonical_dispatch_authorization_bytes",
    "dispatch_authorization_digest",
    "runtime_attempt_fence_ref",
    "validate_dispatch_authorization",
    "validate_runtime_attempt_fence_token",
]
