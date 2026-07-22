"""Production-unreachable strict wire contract for ``verify_session``."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    AfterValidator,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from seektalent.source_port.operation_dispatch import (
    DispatchAuthorizationV1,
    DispatchDeliveryV1,
    InitialDeliveryV1,
    OperationIdentityV1,
    OutboxRedeliveryV1,
    RelativeMonotonicDeadlineV1,
    canonical_dispatch_authorization_bytes,
    dispatch_authorization_digest,
    runtime_attempt_fence_ref,
    validate_dispatch_authorization,
    validate_runtime_attempt_fence_token,
)

from seektalent.source_port.wire_primitives import (
    ExactTrue,
    Opaque96,
    Opaque256,
    PositiveJsonInteger,
    StrictWireModel,
    canonical_json_bytes,
)


VERIFY_SESSION_REQUEST_CONTRACT = "seektalent.source.verify-session.request/v1"
VERIFY_SESSION_RESULT_CONTRACT = "seektalent.source.verify-session.result/v1"


class _VerifySessionModel(StrictWireModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        strict=True,
    )


RuntimeAttemptFenceToken = Annotated[
    Opaque256,
    Field(strict=True),
    AfterValidator(validate_runtime_attempt_fence_token),
]
VerifySessionCapability: TypeAlias = Literal[
    "account",
    "bridge",
    "extension",
    "process",
    "profile_lock",
    "risk_state",
    "search_surface",
]
VerifySessionSafeReasonCode: TypeAlias = Literal[
    "configured",
    "liepin_host_tab_missing",
    "liepin_host_window_ambiguous",
    "liepin_opencli_bootstrap_failed",
    "liepin_opencli_command_missing",
    "liepin_opencli_daemon_not_running",
    "liepin_opencli_daemon_stale",
    "liepin_opencli_extension_disconnected",
    "liepin_opencli_forbidden_command",
    "liepin_opencli_host_blocked",
    "liepin_opencli_identity_intercept",
    "liepin_opencli_login_required",
    "liepin_opencli_malformed_state",
    "liepin_opencli_risk_page",
    "liepin_opencli_search_not_ready",
    "liepin_opencli_selector_ambiguous",
    "liepin_opencli_selector_not_found",
    "liepin_opencli_stale_control_fence",
    "liepin_opencli_stale_ref",
    "liepin_opencli_status_unavailable",
    "liepin_opencli_tab_response_malformed",
    "liepin_opencli_target_not_found",
    "liepin_opencli_terminal_state",
    "liepin_opencli_timeout",
    "liepin_opencli_unknown_modal",
    "liepin_opencli_window_policy_blocked",
    "liepin_owned_tab_missing",
]


class _VerifySessionBodyV1(_VerifySessionModel):
    profile_mode: Literal["existing_profile"]
    profile_binding_ref: Opaque96
    provider_account_ref: Opaque96 | None
    required_capabilities: tuple[VerifySessionCapability, ...]
    user_interaction_policy: Literal["observe_only", "headed_user_action_allowed"]
    verify_search_surface: ExactTrue
    component_receipt_refs: tuple[Opaque96, ...] = ()

    @field_validator("required_capabilities")
    @classmethod
    def validate_registered_capabilities(
        cls,
        required_capabilities: tuple[VerifySessionCapability, ...],
    ) -> tuple[VerifySessionCapability, ...]:
        if len(required_capabilities) > 16:
            raise ValueError("verify_session_capabilities_too_many")
        if tuple(sorted(set(required_capabilities))) != required_capabilities:
            raise ValueError("verify_session_capabilities_not_unique_sorted")
        return required_capabilities

    @field_validator("component_receipt_refs")
    @classmethod
    def validate_component_receipt_refs(
        cls,
        component_receipt_refs: tuple[Opaque96, ...],
    ) -> tuple[Opaque96, ...]:
        if len(component_receipt_refs) > 16:
            raise ValueError("verify_session_receipt_refs_too_many")
        if len(set(component_receipt_refs)) != len(component_receipt_refs):
            raise ValueError("verify_session_duplicate_receipt_ref")
        return component_receipt_refs


class VerifySessionRequestV1(_VerifySessionBodyV1):
    contract_version: Literal["seektalent.source.verify-session.request/v1"]
    runtime_attempt_fence_token: RuntimeAttemptFenceToken = Field(repr=False)
    identity: OperationIdentityV1
    delivery: DispatchDeliveryV1

    @model_validator(mode="before")
    @classmethod
    def redact_invalid_fence_token_input(cls, value: object) -> object:
        if type(value) is cls:
            value = value.model_dump(mode="python")
        if not isinstance(value, Mapping):
            return {"invalid_submit_input": "[redacted]"}
        wire_value: dict[object, object] = {}
        for key, item in value.items():
            wire_value[key] = item
        if "runtime_attempt_fence_token" not in wire_value:
            return wire_value
        token = wire_value["runtime_attempt_fence_token"]
        if _raw_fence_token_is_valid(token):
            return wire_value
        redacted = dict(wire_value)
        redacted["runtime_attempt_fence_token"] = "[redacted]"
        return redacted

    @field_validator("identity")
    @classmethod
    def validate_identity_field(
        cls,
        identity: OperationIdentityV1,
        info: ValidationInfo,
    ) -> OperationIdentityV1:
        raw_runtime_attempt_fence_token = info.data.get("runtime_attempt_fence_token")
        if type(raw_runtime_attempt_fence_token) is not str:
            return identity
        body = _body_from_validated_data(info.data)
        if body is None:
            return identity
        if identity.operation_kind != "verify_session":
            raise ValueError("verify_session_operation_kind_invalid")
        expected_request_hash = _request_intent_hash_from_parts(identity, body)
        if identity.request_hash != expected_request_hash:
            raise ValueError("verify_session_request_hash_mismatch")
        expected_fence_ref = runtime_attempt_fence_ref(
            raw_runtime_attempt_fence_token=raw_runtime_attempt_fence_token,
            run_id=identity.run_id,
            operation_id=identity.operation_id,
            attempt_no=identity.attempt_no,
            request_hash=identity.request_hash,
            expected_source_operation_ledger_revision=identity.expected_source_operation_ledger_revision,
            expected_reconciliation_revision=identity.expected_reconciliation_revision,
        )
        if identity.runtime_attempt_fence_ref != expected_fence_ref:
            raise ValueError("verify_session_runtime_fence_ref_mismatch")
        return identity

    @field_validator("delivery")
    @classmethod
    def validate_delivery_field(
        cls,
        delivery: DispatchDeliveryV1,
        info: ValidationInfo,
    ) -> DispatchDeliveryV1:
        identity = info.data.get("identity")
        if type(identity) is not OperationIdentityV1:
            return delivery
        try:
            validate_dispatch_authorization(identity, delivery.authorization)
        except ValueError:
            raise ValueError("verify_session_dispatch_authorization_invalid") from None
        return delivery

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        operation_id: str,
        attempt_no: int,
        idempotency_key: str,
        correlation_id: str,
        accepted_requirement_revision_id: str,
        runtime_attempt_fence_token: str,
        profile_binding_generation: int,
        browser_control_scope_id: str,
        deadline_value: int,
        expected_source_operation_ledger_revision: int,
        expected_reconciliation_revision: int,
        delivery_mode: Literal["initial", "outbox_redelivery"],
        dispatch_intent_id: str,
        dispatch_intent_revision: int,
        source_operation_acceptance_ref: str,
        profile_binding_ref: str,
        provider_account_ref: str | None,
        required_capabilities: tuple[VerifySessionCapability, ...],
        user_interaction_policy: Literal["observe_only", "headed_user_action_allowed"],
        verify_search_surface: bool,
        component_receipt_refs: tuple[str, ...] = (),
    ) -> VerifySessionRequestV1:
        body = _VerifySessionBodyV1.model_validate(
            {
                "profile_mode": "existing_profile",
                "profile_binding_ref": profile_binding_ref,
                "provider_account_ref": provider_account_ref,
                "required_capabilities": required_capabilities,
                "user_interaction_policy": user_interaction_policy,
                "verify_search_surface": verify_search_surface,
                "component_receipt_refs": component_receipt_refs,
            }
        )
        provisional_identity = OperationIdentityV1.model_validate(
            {
                "run_id": run_id,
                "operation_id": operation_id,
                "attempt_no": attempt_no,
                "source": "liepin",
                "operation_kind": "verify_session",
                "request_hash": "0" * 64,
                "idempotency_key": idempotency_key,
                "correlation_id": correlation_id,
                "accepted_requirement_revision_id": accepted_requirement_revision_id,
                "runtime_attempt_fence_ref": "0" * 64,
                "profile_binding_generation": profile_binding_generation,
                "browser_control_scope_id": browser_control_scope_id,
                "deadline": {
                    "value": deadline_value,
                    "clock": "relative_monotonic",
                    "unit": "milliseconds",
                },
                "expected_source_operation_ledger_revision": expected_source_operation_ledger_revision,
                "expected_reconciliation_revision": expected_reconciliation_revision,
            }
        )
        request_hash = _request_intent_hash_from_parts(provisional_identity, body)
        fence_ref = runtime_attempt_fence_ref(
            raw_runtime_attempt_fence_token=runtime_attempt_fence_token,
            run_id=provisional_identity.run_id,
            operation_id=provisional_identity.operation_id,
            attempt_no=provisional_identity.attempt_no,
            request_hash=request_hash,
            expected_source_operation_ledger_revision=provisional_identity.expected_source_operation_ledger_revision,
            expected_reconciliation_revision=provisional_identity.expected_reconciliation_revision,
        )
        identity = provisional_identity.model_copy(
            update={"request_hash": request_hash, "runtime_attempt_fence_ref": fence_ref}
        )
        authorization = DispatchAuthorizationV1.create(
            identity=identity,
            dispatch_intent_id=dispatch_intent_id,
            dispatch_intent_revision=dispatch_intent_revision,
            source_operation_acceptance_ref=source_operation_acceptance_ref,
        )
        return cls.model_validate(
            {
                "contract_version": VERIFY_SESSION_REQUEST_CONTRACT,
                **body.model_dump(),
                "identity": identity,
                "runtime_attempt_fence_token": runtime_attempt_fence_token,
                "delivery": {
                    "delivery_mode": delivery_mode,
                    "authorization": authorization,
                },
            }
        )


ComponentReadiness: TypeAlias = Literal["ready", "not_ready", "not_observed"]
AccountReadiness: TypeAlias = Literal[
    "ready",
    "not_ready",
    "not_observed",
    "missing",
    "login_required",
    "revoked",
]
RiskState: TypeAlias = Literal["clear", "risk_page", "not_observed"]


class VerifySessionUserActionV1(_VerifySessionModel):
    code: Literal[
        "liepin_host_tab_missing",
        "liepin_opencli_identity_intercept",
        "liepin_opencli_login_required",
        "liepin_opencli_risk_page",
        "liepin_opencli_unknown_modal",
    ]
    instruction_key: Literal[
        "verify_session.open_liepin_host",
        "verify_session.complete_identity_check",
        "verify_session.log_in",
        "verify_session.complete_risk_check",
        "verify_session.dismiss_or_resolve_modal",
    ]

    @model_validator(mode="after")
    def validate_instruction_key(self) -> VerifySessionUserActionV1:
        expected = {
            "liepin_host_tab_missing": "verify_session.open_liepin_host",
            "liepin_opencli_identity_intercept": "verify_session.complete_identity_check",
            "liepin_opencli_login_required": "verify_session.log_in",
            "liepin_opencli_risk_page": "verify_session.complete_risk_check",
            "liepin_opencli_unknown_modal": "verify_session.dismiss_or_resolve_modal",
        }[self.code]
        if self.instruction_key != expected:
            raise ValueError("verify_session_user_action_instruction_mismatch")
        return self


class VerifySessionResultV1(_VerifySessionModel):
    contract_version: Literal["seektalent.source.verify-session.result/v1"]
    identity: OperationIdentityV1
    process_readiness: ComponentReadiness
    bridge_readiness: ComponentReadiness
    extension_readiness: ComponentReadiness
    profile_lock_readiness: ComponentReadiness
    account_readiness: AccountReadiness
    search_surface_readiness: ComponentReadiness
    risk_state: RiskState
    session_readiness: Literal["ready", "not_ready"]
    actual_profile_binding_ref: Opaque96 | None
    actual_provider_account_ref: Opaque96 | None
    actual_profile_binding_generation: PositiveJsonInteger
    safe_reason_code: VerifySessionSafeReasonCode | None
    user_action: VerifySessionUserActionV1 | None
    component_receipt_refs: tuple[Opaque96, ...] = ()

    @field_validator("identity")
    @classmethod
    def validate_verify_session_identity(
        cls,
        identity: OperationIdentityV1,
    ) -> OperationIdentityV1:
        if identity.operation_kind != "verify_session":
            raise ValueError("verify_session_operation_kind_invalid")
        return identity

    @model_validator(mode="after")
    def validate_closed_readiness(self) -> VerifySessionResultV1:
        if len(self.component_receipt_refs) > 16:
            raise ValueError("verify_session_receipt_refs_too_many")
        if len(set(self.component_receipt_refs)) != len(self.component_receipt_refs):
            raise ValueError("verify_session_duplicate_receipt_ref")
        if self.user_action is not None and self.safe_reason_code != self.user_action.code:
            raise ValueError("verify_session_user_action_reason_mismatch")
        if self.session_readiness == "ready":
            if (
                self.process_readiness != "ready"
                or self.bridge_readiness != "ready"
                or self.extension_readiness != "ready"
                or self.profile_lock_readiness != "ready"
                or self.account_readiness != "ready"
                or self.search_surface_readiness != "ready"
                or self.risk_state != "clear"
                or self.actual_profile_binding_ref is None
                or self.actual_provider_account_ref is None
                or self.safe_reason_code is not None
                or self.user_action is not None
            ):
                raise ValueError("verify_session_ready_facts_incomplete")
        elif self.safe_reason_code == "configured":
            raise ValueError("verify_session_not_ready_configured")
        return self


def canonical_request_intent_bytes(request: VerifySessionRequestV1) -> bytes:
    """Return strict RFC 8785 request-intent bytes without transport authority."""
    validated = _validated_request(request)
    return _request_intent_bytes_for_validated(validated)


def canonical_request_intent_hash(request: VerifySessionRequestV1) -> str:
    return sha256(canonical_request_intent_bytes(request)).hexdigest()


def canonical_verify_session_result_bytes(result: VerifySessionResultV1) -> bytes:
    """Return strict RFC 8785 bytes for closed verify-session facts."""
    validated = _validated_result(result)
    return canonical_json_bytes(validated.model_dump(mode="json"))


def verify_session_result_hash(result: VerifySessionResultV1) -> str:
    return sha256(canonical_verify_session_result_bytes(result)).hexdigest()


def validate_outbox_redelivery(
    initial: VerifySessionRequestV1,
    redelivery: VerifySessionRequestV1,
) -> None:
    """Require one logical request to replay exact authorization without increasing its budget.

    The sidecar's accepted monotonic deadline belongs to a later durable record;
    this pure DTO check only prevents a replay from proposing a larger value.
    """
    validated_initial = _validated_request(initial)
    validated_redelivery = _validated_request(redelivery)
    if (
        validated_initial.delivery.delivery_mode != "initial"
        or validated_redelivery.delivery.delivery_mode != "outbox_redelivery"
    ):
        raise ValueError("verify_session_outbox_redelivery_mode_invalid")
    if (
        validated_initial.delivery.authorization != validated_redelivery.delivery.authorization
        or validated_initial.identity.run_id != validated_redelivery.identity.run_id
        or validated_initial.identity.operation_id != validated_redelivery.identity.operation_id
        or validated_initial.identity.idempotency_key != validated_redelivery.identity.idempotency_key
        or validated_initial.identity.request_hash != validated_redelivery.identity.request_hash
        or validated_redelivery.identity.deadline.value > validated_initial.identity.deadline.value
    ):
        raise ValueError("verify_session_outbox_redelivery_not_exact")


def validate_verify_session_result_echo(
    request: VerifySessionRequestV1,
    result: VerifySessionResultV1,
) -> None:
    """Require an exact initial echo or a stable-fact redelivery echo."""
    validated_request = _validated_request(request)
    validated_result = _validated_result(result)
    if validated_request.delivery.delivery_mode == "initial":
        identity_matches = validated_result.identity == validated_request.identity
    else:
        identity_matches = _redelivery_result_identity_matches_request(
            validated_request.identity,
            validated_result.identity,
        )
    if not identity_matches:
        raise ValueError("verify_session_result_identity_mismatch")
    if validated_result.component_receipt_refs != validated_request.component_receipt_refs:
        raise ValueError("verify_session_result_receipt_mismatch")
    if validated_result.actual_profile_binding_ref != validated_request.profile_binding_ref:
        raise ValueError("verify_session_result_profile_binding_mismatch")
    if (
        validated_request.provider_account_ref is not None
        and validated_result.actual_provider_account_ref != validated_request.provider_account_ref
    ):
        raise ValueError("verify_session_result_account_binding_mismatch")
    if validated_result.actual_profile_binding_generation != validated_request.identity.profile_binding_generation:
        raise ValueError("verify_session_result_profile_generation_mismatch")


def _raw_fence_token_is_valid(value: object) -> bool:
    try:
        validate_runtime_attempt_fence_token(value)
    except ValueError:
        return False
    return True


_BODY_FIELDS = (
    "profile_mode",
    "profile_binding_ref",
    "provider_account_ref",
    "required_capabilities",
    "user_interaction_policy",
    "verify_search_surface",
    "component_receipt_refs",
)


def _redelivery_result_identity_matches_request(
    request_identity: OperationIdentityV1,
    result_identity: OperationIdentityV1,
) -> bool:
    return (
        result_identity.run_id == request_identity.run_id
        and result_identity.operation_id == request_identity.operation_id
        and result_identity.attempt_no == request_identity.attempt_no
        and result_identity.source == request_identity.source
        and result_identity.operation_kind == request_identity.operation_kind
        and result_identity.request_hash == request_identity.request_hash
        and result_identity.idempotency_key == request_identity.idempotency_key
        and result_identity.accepted_requirement_revision_id == request_identity.accepted_requirement_revision_id
        and result_identity.profile_binding_generation == request_identity.profile_binding_generation
        and result_identity.expected_source_operation_ledger_revision
        == request_identity.expected_source_operation_ledger_revision
        and result_identity.expected_reconciliation_revision == request_identity.expected_reconciliation_revision
    )


def _body_from_validated_data(data: dict[str, object]) -> _VerifySessionBodyV1 | None:
    if any(name not in data for name in _BODY_FIELDS):
        return None
    try:
        return _VerifySessionBodyV1.model_validate(
            {name: data[name] for name in _BODY_FIELDS},
            strict=True,
        )
    except (TypeError, ValueError, ValidationError):
        return None


def _request_intent_bytes_for_validated(request: VerifySessionRequestV1) -> bytes:
    return canonical_json_bytes(_request_intent_payload(request.identity, request))


def _request_intent_hash_for_validated(request: VerifySessionRequestV1) -> str:
    return sha256(_request_intent_bytes_for_validated(request)).hexdigest()


def _request_intent_hash_from_parts(identity: OperationIdentityV1, body: _VerifySessionBodyV1) -> str:
    return sha256(canonical_json_bytes(_request_intent_payload(identity, body))).hexdigest()


def _request_intent_payload(
    identity: OperationIdentityV1,
    body: _VerifySessionBodyV1,
) -> dict[str, object]:
    return {
        "contract_version": VERIFY_SESSION_REQUEST_CONTRACT,
        "source": identity.source,
        "operation_kind": identity.operation_kind,
        "run_id": identity.run_id,
        "operation_id": identity.operation_id,
        "accepted_requirement_revision_id": identity.accepted_requirement_revision_id,
        "profile_binding_generation": identity.profile_binding_generation,
        "profile_mode": body.profile_mode,
        "profile_binding_ref": body.profile_binding_ref,
        "provider_account_ref": body.provider_account_ref,
        "required_capabilities": body.required_capabilities,
        "user_interaction_policy": body.user_interaction_policy,
        "verify_search_surface": body.verify_search_surface,
        "component_receipt_refs": body.component_receipt_refs,
    }


def _validated_request(value: VerifySessionRequestV1) -> VerifySessionRequestV1:
    if type(value) is not VerifySessionRequestV1:
        raise TypeError("strict VerifySessionRequestV1 required")
    try:
        return VerifySessionRequestV1.model_validate(value.model_dump(mode="python", warnings="error"), strict=True)
    except (TypeError, ValueError, ValidationError):
        raise ValueError("verify_session_contract_invalid") from None


def _validated_result(value: VerifySessionResultV1) -> VerifySessionResultV1:
    if type(value) is not VerifySessionResultV1:
        raise TypeError("strict VerifySessionResultV1 required")
    try:
        return VerifySessionResultV1.model_validate(value.model_dump(mode="python", warnings="error"), strict=True)
    except (TypeError, ValueError, ValidationError):
        raise ValueError("verify_session_contract_invalid") from None


__all__ = [
    "DispatchAuthorizationV1",
    "DispatchDeliveryV1",
    "InitialDeliveryV1",
    "OperationIdentityV1",
    "OutboxRedeliveryV1",
    "RelativeMonotonicDeadlineV1",
    "VerifySessionRequestV1",
    "VerifySessionResultV1",
    "VerifySessionUserActionV1",
    "canonical_dispatch_authorization_bytes",
    "canonical_request_intent_bytes",
    "canonical_request_intent_hash",
    "canonical_verify_session_result_bytes",
    "dispatch_authorization_digest",
    "runtime_attempt_fence_ref",
    "validate_outbox_redelivery",
    "validate_verify_session_result_echo",
    "verify_session_result_hash",
]
