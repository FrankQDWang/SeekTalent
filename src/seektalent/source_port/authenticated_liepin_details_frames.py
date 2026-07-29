"""Authenticated frames for the Liepin details Source Operation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import model_validator

from seektalent.source_port.liepin_details_contract import (
    LiepinDetailsObservationV1,
    LiepinDetailsOperationRequestV1,
    canonical_liepin_details_request_hash,
    stable_liepin_details_operation_id,
)
from seektalent.source_port.operation_dispatch import (
    DispatchDeliveryV1,
    OperationIdentityV1,
)
from seektalent.source_port.wire_primitives import (
    PositiveJsonInteger,
    StrictWireModel,
)


class LiepinDetailsSubmitV1(StrictWireModel):
    contract_version: Literal["seektalent.source.liepin-details.submit/v1"]
    identity: OperationIdentityV1
    delivery: DispatchDeliveryV1
    request: LiepinDetailsOperationRequestV1

    @model_validator(mode="after")
    def validate_binding(self) -> LiepinDetailsSubmitV1:
        authorization = self.delivery.authorization
        if (
            self.identity.operation_kind != "details"
            or self.identity.run_id != self.request.runtime_run_id
            or self.identity.operation_id != stable_liepin_details_operation_id(self.request)
            or self.identity.request_hash
            != canonical_liepin_details_request_hash(self.request)
            or authorization.run_id != self.identity.run_id
            or authorization.operation_id != self.identity.operation_id
            or authorization.request_hash != self.identity.request_hash
        ):
            raise ValueError("liepin_details_submit_identity_mismatch")
        return self


class LiepinDetailsAcceptedAckV1(StrictWireModel):
    contract_version: Literal["seektalent.source.liepin-details.ack/v1"]
    identity: OperationIdentityV1
    sidecar_generation: PositiveJsonInteger
    accepted_journal_revision: PositiveJsonInteger
    ack_kind: Literal[
        "new_logical_operation",
        "same_intent_replay",
        "new_dispatch_authorization",
    ]
    dispatch_intent_ref: str

    @model_validator(mode="after")
    def validate_ack_kind(self) -> LiepinDetailsAcceptedAckV1:
        expected = (
            "new_logical_operation"
            if self.identity.expected_reconciliation_revision == 0
            else "new_dispatch_authorization"
        )
        if self.ack_kind != expected:
            raise ValueError("liepin_details_ack_kind_mismatch")
        return self


class LiepinDetailsResultV1(StrictWireModel):
    contract_version: Literal["seektalent.source.liepin-details.result/v1"]
    identity: OperationIdentityV1
    observation: LiepinDetailsObservationV1


class LiepinDetailsReconcileRequiredV1(StrictWireModel):
    contract_version: Literal[
        "seektalent.source.liepin-details.reconcile-required/v1"
    ]
    identity: OperationIdentityV1
    history_fact: Literal["accepted_no_dispatch", "dispatch_not_observed"]


@dataclass(frozen=True, slots=True)
class ReceivedLiepinDetailsSubmit:
    message_id: str
    correlation_id: str | None
    payload: LiepinDetailsSubmitV1


@dataclass(frozen=True, slots=True)
class ReceivedLiepinDetailsAcceptedAck:
    message_id: str
    reply_to: str
    correlation_id: str | None
    payload: LiepinDetailsAcceptedAckV1


@dataclass(frozen=True, slots=True)
class ReceivedLiepinDetailsResult:
    message_id: str
    reply_to: str
    correlation_id: str | None
    payload: LiepinDetailsResultV1


@dataclass(frozen=True, slots=True)
class ReceivedLiepinDetailsReconcileRequired:
    message_id: str
    reply_to: str
    correlation_id: str | None
    payload: LiepinDetailsReconcileRequiredV1


__all__ = [
    "LiepinDetailsAcceptedAckV1",
    "LiepinDetailsReconcileRequiredV1",
    "LiepinDetailsResultV1",
    "LiepinDetailsSubmitV1",
    "ReceivedLiepinDetailsAcceptedAck",
    "ReceivedLiepinDetailsReconcileRequired",
    "ReceivedLiepinDetailsResult",
    "ReceivedLiepinDetailsSubmit",
]
