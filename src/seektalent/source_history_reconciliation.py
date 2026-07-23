"""Main-owned admitted history to no-owner reconciliation composition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from seektalent.source_port.sidecar_transport import (
    AdmittedSourceHistoryResult,
    require_live_admitted_source_history_result,
)
from seektalent.source_port.authenticated_history_frames import canonical_source_history_semantics_bytes
from seektalent.source_port.history_contract import (
    AcceptedNoDispatchFact,
    DispatchNotObservedFact,
    ExactAuthorizationSelector,
    MatchedHistoryFact,
    ObservedFailureFact,
    ObservedResultFact,
    SourceHistoryIdentityConflict,
    SourceHistoryMatched,
    SourceHistoryNotFound,
    SourceHistoryQueryResultV1,
    SourceHistoryQueryV1,
    SourceHistoryUnavailable,
)
from seektalent_runtime_control.source_operations import (
    AcceptedSourceOperation,
    RetryPosture,
    SourceDispatchMetadata,
    SourceOperationDisposition,
    validate_source_dispatch_ack,
    validate_source_operation_acceptance,
)
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.source_reconciliation import (
    SourceOperationHistoryConclusion,
    SourceOperationHistoryOutcome,
    SourceOperationReconciliationDecision,
    SourceOperationReconciliationDecisionKind,
    SourceOperationReconciliationRecord,
)
from seektalent_runtime_control.store import RuntimeControlStore


class SourceHistoryReconciliationReason(StrEnum):
    AUTHORIZATION_SELECTOR_INVALID = "source_history_reconciliation_authorization_selector_invalid"
    CONTEXT_MISMATCH = "source_history_reconciliation_context_mismatch"
    IDENTITY_CONFLICT = "source_history_reconciliation_identity_conflict"
    FACT_COUNT_INVALID = "source_history_reconciliation_fact_count_invalid"
    OPERATION_INTERPRETATION_REQUIRED = "source_history_reconciliation_operation_interpretation_required"


class SourceHistoryReconciliationError(ValueError):
    """A closed typed rejection before runtime-control mutation."""

    def __init__(self, reason: SourceHistoryReconciliationReason) -> None:
        self.reason = reason
        self.reason_code = reason.value
        super().__init__(reason.value)

    def __repr__(self) -> str:
        return f"SourceHistoryReconciliationError(reason={self.reason.value!r})"


@dataclass(frozen=True, slots=True)
class _ClosedInterpretation:
    decision_kind: SourceOperationReconciliationDecisionKind
    history_outcome: SourceOperationHistoryOutcome
    history_conclusion: SourceOperationHistoryConclusion | None
    dispatch_intent_ref: str | None
    conclusive_observation_ref: str | None
    source_operation_disposition: SourceOperationDisposition | None
    retry_posture: RetryPosture


def commit_admitted_source_history_reconciliation(
    admitted: AdmittedSourceHistoryResult,
    store: RuntimeControlStore,
    *,
    committed_at: str,
    fault_injector: Callable[[str], None] | None = None,
) -> SourceOperationReconciliationRecord:
    """Interpret admitted history from current main facts and commit through the existing CAS."""
    admitted = require_live_admitted_source_history_result(admitted)
    if type(store) is not RuntimeControlStore:
        raise TypeError("store must be a real RuntimeControlStore")

    query = admitted.query
    result = admitted.payload
    if not isinstance(query.authorization_selector, ExactAuthorizationSelector):
        raise SourceHistoryReconciliationError(
            SourceHistoryReconciliationReason.AUTHORIZATION_SELECTOR_INVALID
        )

    context = store.get_accepted_source_operation_context(query.run_id, query.operation_id)
    if not _context_is_valid(context) or not _query_and_result_match_context(query, result, context):
        raise SourceHistoryReconciliationError(SourceHistoryReconciliationReason.CONTEXT_MISMATCH)
    if isinstance(result, SourceHistoryIdentityConflict):
        raise SourceHistoryReconciliationError(SourceHistoryReconciliationReason.IDENTITY_CONFLICT)

    fact = _single_fact(result)
    if fact is not None and not _fact_matches_context(fact, context):
        raise SourceHistoryReconciliationError(SourceHistoryReconciliationReason.CONTEXT_MISMATCH)

    semantic_bytes = canonical_source_history_semantics_bytes(query, result)
    history_digest = sha256(semantic_bytes).hexdigest()
    interpretation = _closed_interpretation(result, fact, context)
    decision = SourceOperationReconciliationDecision(
        reconciliation_id=f"source-history-{history_digest}",
        runtime_run_id=context.operation.runtime_run_id,
        operation_id=context.operation.operation_id,
        source_id=context.operation.source_id,
        operation_kind=context.operation.operation_kind,
        canonical_request_hash=context.operation.canonical_request_hash,
        idempotency_key=context.operation.idempotency_key,
        accepted_requirement_revision_id=context.operation.accepted_requirement_revision_id,
        runtime_attempt_no=context.operation.runtime_attempt_no,
        runtime_attempt_authority_ref=context.operation.runtime_attempt_authority_ref,
        history_result_ref=f"sha256:{history_digest}",
        history_result_digest=history_digest,
        expected_ledger_revision=context.dispatch.expected_ledger_revision,
        expected_reconciliation_revision=context.dispatch.expected_reconciliation_revision,
        committed_at=committed_at,
        decision_kind=interpretation.decision_kind,
        history_outcome=interpretation.history_outcome,
        history_conclusion=interpretation.history_conclusion,
        dispatch_intent_ref=interpretation.dispatch_intent_ref,
        conclusive_observation_ref=interpretation.conclusive_observation_ref,
        source_operation_disposition=interpretation.source_operation_disposition,
        retry_posture=interpretation.retry_posture,
    )
    return store.commit_no_owner_source_reconciliation(
        decision,
        fault_injector,
        dispatch_precondition=context.dispatch,
    )


def _context_is_valid(context: AcceptedSourceOperation) -> bool:
    operation = context.operation
    expectation = context.expectation
    dispatch = context.dispatch
    try:
        validate_source_operation_acceptance(
            runtime_run_id=operation.runtime_run_id,
            operation_id=operation.operation_id,
            source_id=operation.source_id,
            operation_kind=operation.operation_kind,
            canonical_request_hash=operation.canonical_request_hash,
            idempotency_key=operation.idempotency_key,
            accepted_requirement_revision_id=operation.accepted_requirement_revision_id,
            runtime_attempt_no=operation.runtime_attempt_no,
            runtime_attempt_authority_ref=operation.runtime_attempt_authority_ref,
            runtime_attempt_fence_ref=expectation.runtime_attempt_fence_ref,
            profile_binding_generation=expectation.profile_binding_generation,
            browser_control_scope_id=expectation.browser_control_scope_id,
            controller_fence_ref=expectation.controller_fence_ref,
            outbox_id=dispatch.outbox_id,
            dispatch_intent_id=dispatch.dispatch_intent_id,
            dispatch_intent_revision=dispatch.dispatch_intent_revision,
            dispatch_intent_digest=dispatch.dispatch_intent_digest,
            dispatch_authorization_ordinal=dispatch.dispatch_authorization_ordinal,
            source_operation_acceptance_ref=dispatch.source_operation_acceptance_ref,
            expected_ledger_revision=dispatch.expected_ledger_revision,
            expected_reconciliation_revision=dispatch.expected_reconciliation_revision,
        )
        if dispatch.status == "acknowledged":
            accepted_generation = dispatch.accepted_sidecar_generation
            accepted_journal_revision = dispatch.accepted_sidecar_journal_revision
            ack_ref = dispatch.ack_ref
            ack_kind = dispatch.ack_kind
            acknowledged_at = dispatch.acknowledged_at
            if (
                accepted_generation is None
                or accepted_journal_revision is None
                or ack_ref is None
                or ack_kind is None
                or acknowledged_at is None
            ):
                return False
            validate_source_dispatch_ack(
                runtime_run_id=dispatch.runtime_run_id,
                operation_id=dispatch.operation_id,
                outbox_id=dispatch.outbox_id,
                canonical_request_hash=dispatch.canonical_request_hash,
                dispatch_intent_id=dispatch.dispatch_intent_id,
                dispatch_intent_revision=dispatch.dispatch_intent_revision,
                dispatch_intent_digest=dispatch.dispatch_intent_digest,
                dispatch_authorization_ordinal=dispatch.dispatch_authorization_ordinal,
                expected_outbox_revision=1,
                accepted_sidecar_generation=accepted_generation,
                accepted_sidecar_journal_revision=accepted_journal_revision,
                ack_ref=ack_ref,
                ack_kind=ack_kind,
                acknowledged_at=acknowledged_at,
            )
    except RuntimeControlError:
        return False
    return _dispatch_state_is_complete(dispatch)


def _query_and_result_match_context(
    query: SourceHistoryQueryV1,
    result: SourceHistoryQueryResultV1,
    context: AcceptedSourceOperation,
) -> bool:
    operation = context.operation
    dispatch = context.dispatch
    echoed = result.model_dump(mode="json", include=set(SourceHistoryQueryV1.model_fields) - {"contract_version"})
    requested = query.model_dump(mode="json", exclude={"contract_version"})
    return (
        echoed == requested
        and isinstance(query.authorization_selector, ExactAuthorizationSelector)
        and query.authorization_selector.ordinal == 1
        and query.run_id == operation.runtime_run_id
        and query.operation_id == operation.operation_id
        and query.source == operation.source_id
        and query.operation_kind == operation.operation_kind
        and query.idempotency_key == operation.idempotency_key
        and query.request_hash == operation.canonical_request_hash
        and query.attempt_no == operation.runtime_attempt_no
        and query.expected_source_operation_ledger_revision == dispatch.expected_ledger_revision
        and query.expected_reconciliation_revision == dispatch.expected_reconciliation_revision
        and _accepted_generation_hint_matches_context(query, result, dispatch)
        and dispatch.runtime_run_id == operation.runtime_run_id
        and dispatch.operation_id == operation.operation_id
        and dispatch.canonical_request_hash == operation.canonical_request_hash
        and dispatch.dispatch_authorization_ordinal == 1
        and _dispatch_state_is_complete(dispatch)
    )


def _accepted_generation_hint_matches_context(
    query: SourceHistoryQueryV1,
    result: SourceHistoryQueryResultV1,
    dispatch: SourceDispatchMetadata,
) -> bool:
    if dispatch.status == "acknowledged":
        return query.accepted_generation_hint == dispatch.accepted_sidecar_generation
    if dispatch.status == "pending":
        if not isinstance(result, SourceHistoryMatched):
            return query.accepted_generation_hint is None
        return any(query.accepted_generation_hint == fact.accepted_generation for fact in result.facts)
    return False


def _dispatch_state_is_complete(dispatch: SourceDispatchMetadata) -> bool:
    acceptance_values = (
        dispatch.accepted_sidecar_generation,
        dispatch.accepted_sidecar_journal_revision,
        dispatch.ack_ref,
        dispatch.ack_kind,
        dispatch.acknowledged_at,
    )
    if dispatch.status == "pending":
        return dispatch.outbox_revision == 1 and all(value is None for value in acceptance_values)
    if dispatch.status == "acknowledged":
        return dispatch.outbox_revision == 2 and all(value is not None for value in acceptance_values)
    return False


def _single_fact(result: SourceHistoryQueryResultV1) -> MatchedHistoryFact | None:
    if not isinstance(result, SourceHistoryMatched):
        return None
    if len(result.facts) != 1:
        raise SourceHistoryReconciliationError(SourceHistoryReconciliationReason.FACT_COUNT_INVALID)
    return result.facts[0]


def _fact_matches_context(fact: MatchedHistoryFact, context: AcceptedSourceOperation) -> bool:
    operation = context.operation
    expectation = context.expectation
    dispatch = context.dispatch
    identities_match = (
        fact.run_id == operation.runtime_run_id
        and fact.operation_id == operation.operation_id
        and fact.source == operation.source_id
        and fact.operation_kind == operation.operation_kind
        and fact.idempotency_key == operation.idempotency_key
        and fact.request_hash == operation.canonical_request_hash
        and fact.attempt_no == operation.runtime_attempt_no
        and fact.accepted_requirement_revision_id == operation.accepted_requirement_revision_id
        and fact.runtime_attempt_fence_ref == expectation.runtime_attempt_fence_ref
        and fact.profile_binding_generation == expectation.profile_binding_generation
        and fact.browser_control_scope_id == expectation.browser_control_scope_id
        and fact.controller_fence_ref == expectation.controller_fence_ref
        and fact.dispatch_authorization_ordinal == 1
        and fact.authorized_dispatch_intent_id == dispatch.dispatch_intent_id
        and fact.authorized_dispatch_intent_revision == dispatch.dispatch_intent_revision
        and fact.authorized_dispatch_intent_digest == dispatch.dispatch_intent_digest
    )
    if not identities_match or not _dispatch_state_is_complete(dispatch):
        return False
    if dispatch.status == "pending":
        return True
    if dispatch.status == "acknowledged":
        return (
            fact.accepted_generation == dispatch.accepted_sidecar_generation
            and fact.accepted_journal_revision == dispatch.accepted_sidecar_journal_revision
        )
    return False


def _closed_interpretation(
    result: SourceHistoryQueryResultV1,
    fact: MatchedHistoryFact | None,
    context: AcceptedSourceOperation,
) -> _ClosedInterpretation:
    if isinstance(result, SourceHistoryNotFound):
        if context.dispatch.status != "pending":
            raise SourceHistoryReconciliationError(SourceHistoryReconciliationReason.CONTEXT_MISMATCH)
        return _ClosedInterpretation(
            decision_kind="no_dispatch_proved",
            history_outcome="not_found",
            history_conclusion=None,
            dispatch_intent_ref=None,
            conclusive_observation_ref=None,
            source_operation_disposition=None,
            retry_posture="safe_retry",
        )
    if isinstance(result, SourceHistoryUnavailable):
        return _ClosedInterpretation(
            decision_kind="unresolved",
            history_outcome="history_unavailable",
            history_conclusion=None,
            dispatch_intent_ref=context.operation.dispatch_intent_ref,
            conclusive_observation_ref=None,
            source_operation_disposition="reconciliation_unknown",
            retry_posture="reconcile_first",
        )
    if isinstance(fact, AcceptedNoDispatchFact):
        return _ClosedInterpretation(
            decision_kind="no_dispatch_proved",
            history_outcome="matched",
            history_conclusion="accepted_no_dispatch",
            dispatch_intent_ref=None,
            conclusive_observation_ref=None,
            source_operation_disposition=None,
            retry_posture="safe_retry",
        )
    if isinstance(fact, DispatchNotObservedFact):
        return _ClosedInterpretation(
            decision_kind="unresolved",
            history_outcome="matched",
            history_conclusion="dispatch_not_observed",
            dispatch_intent_ref=fact.durable_dispatch_intent_ref,
            conclusive_observation_ref=None,
            source_operation_disposition="reconciliation_unknown",
            retry_posture="reconcile_first",
        )
    if isinstance(fact, (ObservedResultFact, ObservedFailureFact)):
        raise SourceHistoryReconciliationError(
            SourceHistoryReconciliationReason.OPERATION_INTERPRETATION_REQUIRED
        )
    raise SourceHistoryReconciliationError(SourceHistoryReconciliationReason.CONTEXT_MISMATCH)
