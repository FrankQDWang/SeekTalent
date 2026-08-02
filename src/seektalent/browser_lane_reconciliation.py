"""Conservative production reconciliation for an orphaned Liepin browser lane."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Literal

from seektalent.source_port.authenticated_history_frames import (
    canonical_source_history_semantics_bytes,
)
from seektalent.source_port.history_contract import (
    AcceptedNoDispatchFact,
    DispatchNotObservedFact,
    ExactAuthorizationSelector,
    JSON_SAFE_INTEGER,
    ObservedFailureFact,
    ObservedResultFact,
    SourceHistoryIdentityConflict,
    SourceHistoryMatched,
    SourceHistoryNotFound,
    SourceHistoryQueryV1,
    SourceHistoryUnavailable,
)
from seektalent.source_port.history_sqlite_reader import (
    SourceHistorySQLiteReader,
)
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.source_operations import (
    SourceDispatchMetadata,
)
from seektalent_runtime_control.source_reconciliation import (
    SourceOperationReconciliationDecision,
)
from seektalent_runtime_control.store import RuntimeControlStore


class BrowserLaneReconciliationCoordinator:
    def __init__(
        self,
        *,
        store: RuntimeControlStore,
        journal_path: Path | None = None,
        prepare_readiness_probe: Callable[[], None] | None = None,
    ) -> None:
        self.store = store
        self.prepare_readiness_probe = prepare_readiness_probe
        self.journal_path = (
            journal_path
            or store.path.parent
            / "source-port"
            / "liepin-cards-journal.sqlite3"
        )

    def run_once(
        self,
    ) -> Literal["not_applicable", "released", "needs_attention"]:
        observed_at = _now()
        projected = (
            self.store
            .reconcile_expired_browser_lane_from_durable_evidence(
                observed_at=observed_at,
            )
        )
        if projected != "needs_attention":
            return projected

        lane = self.store.get_browser_lane()
        if (
            lane is None
            or lane.status != "active"
            or lane.lease_expires_at is None
            or lane.lease_expires_at > observed_at
            or lane.runtime_run_id is None
        ):
            return "not_applicable"
        try:
            accepted = self.store.get_accepted_source_operation_context(
                lane.runtime_run_id,
                lane.operation_id,
            )
        except RuntimeControlError:
            return "needs_attention"

        query, result = self._read_history(accepted)
        semantics = canonical_source_history_semantics_bytes(query, result)
        history_digest = sha256(semantics).hexdigest()
        decision, dispatch_ack = _decision_from_history(
            accepted=accepted,
            query=query,
            result=result,
            history_digest=history_digest,
            observed_at=observed_at,
        )
        if (
            (
                decision.decision_kind == "unresolved"
                or (
                    decision.decision_kind == "no_dispatch_proved"
                    and accepted.operation.source_operation_disposition
                    == "reconciliation_unknown"
                    and accepted.operation.retry_posture
                    == "reconcile_first"
                )
            )
            and lane.operation_kind == "prepare_readiness"
            and accepted.operation.operation_kind == "verify_session"
            and self.prepare_readiness_probe is not None
        ):
            try:
                self.prepare_readiness_probe()
            except (OSError, RuntimeError, ValueError):
                return "needs_attention"
            decision = _decision_from_current_readiness(
                accepted=accepted,
                observed_at=observed_at,
            )
        if (
            decision.decision_kind == "unresolved"
            and accepted.operation.source_operation_disposition
            == "reconciliation_unknown"
            and accepted.operation.retry_posture == "reconcile_first"
        ):
            return "needs_attention"
        try:
            self.store.commit_no_owner_source_reconciliation(
                decision,
                dispatch_precondition=accepted.dispatch,
                dispatch_ack=dispatch_ack,
                expired_browser_lane_fencing_token=lane.fencing_token,
            )
        except RuntimeControlError:
            return "needs_attention"

        outcome = (
            self.store
            .reconcile_expired_browser_lane_from_durable_evidence(
                observed_at=observed_at,
            )
        )
        if outcome == "released":
            _resume_reconciled_recovery_attention(
                self.store,
                runtime_run_id=lane.runtime_run_id,
                resolved_at=observed_at,
            )
        return outcome

    def _read_history(self, accepted):
        query = _history_query(
            accepted,
            searched_last_generation=(
                accepted.dispatch.accepted_sidecar_generation
                or JSON_SAFE_INTEGER
            ),
        )
        reader = SourceHistorySQLiteReader(self.journal_path)
        result = reader.query(
            query,
            deadline=time.monotonic() + 1.0,
        )
        if (
            isinstance(result, SourceHistoryUnavailable)
            and result.reason == "unknown_generation"
            and result.newest_known_generation is not None
        ):
            query = _history_query(
                accepted,
                searched_last_generation=result.newest_known_generation,
            )
            result = reader.query(
                query,
                deadline=time.monotonic() + 1.0,
            )
        return query, result


def _decision_from_current_readiness(
    *,
    accepted,
    observed_at: str,
) -> SourceOperationReconciliationDecision:
    evidence = json.dumps(
        {
            "schemaVersion": "seektalent.prepare-readiness-observation/v1",
            "runtimeRunId": accepted.operation.runtime_run_id,
            "operationId": accepted.operation.operation_id,
            "observation": "current_bridge_ready",
            "observedAt": observed_at,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = sha256(evidence).hexdigest()
    observation_ref = f"prepare-readiness-observation://{digest}"
    return SourceOperationReconciliationDecision(
        reconciliation_id=f"prepare-ready-{digest}",
        runtime_run_id=accepted.operation.runtime_run_id,
        operation_id=accepted.operation.operation_id,
        source_id="liepin",
        operation_kind=accepted.operation.operation_kind,
        canonical_request_hash=accepted.operation.canonical_request_hash,
        idempotency_key=accepted.operation.idempotency_key,
        accepted_requirement_revision_id=(
            accepted.operation.accepted_requirement_revision_id
        ),
        runtime_attempt_no=accepted.operation.runtime_attempt_no,
        runtime_attempt_authority_ref=(
            accepted.operation.runtime_attempt_authority_ref
        ),
        history_result_ref=observation_ref,
        history_result_digest=digest,
        decision_kind="conclusive_observation",
        history_outcome="matched",
        history_conclusion="observed_result",
        dispatch_intent_ref=accepted.operation.dispatch_intent_ref,
        conclusive_observation_ref=observation_ref,
        source_operation_disposition="partial",
        retry_posture="no_retry",
        expected_ledger_revision=accepted.operation.ledger_revision,
        expected_reconciliation_revision=(
            accepted.operation.reconciliation_revision
        ),
        committed_at=observed_at,
    )


def _history_query(
    accepted,
    *,
    searched_last_generation: int,
) -> SourceHistoryQueryV1:
    return SourceHistoryQueryV1(
        contract_version="seektalent.source-port.query.request/v1",
        run_id=accepted.operation.runtime_run_id,
        operation_id=accepted.operation.operation_id,
        source="liepin",
        operation_kind=accepted.operation.operation_kind,
        idempotency_key=accepted.operation.idempotency_key,
        request_hash=accepted.operation.canonical_request_hash,
        attempt_no=accepted.expectation.runtime_attempt_no,
        authorization_selector=ExactAuthorizationSelector(
            kind="exact",
            ordinal=accepted.dispatch.dispatch_authorization_ordinal,
        ),
        accepted_generation_hint=(
            accepted.dispatch.accepted_sidecar_generation
        ),
        searched_first_generation=1,
        searched_last_generation=searched_last_generation,
        expected_source_operation_ledger_revision=(
            accepted.operation.ledger_revision
        ),
        expected_reconciliation_revision=(
            accepted.operation.reconciliation_revision
        ),
    )


def _decision_from_history(
    *,
    accepted,
    query: SourceHistoryQueryV1,
    result,
    history_digest: str,
    observed_at: str,
) -> tuple[
    SourceOperationReconciliationDecision,
    SourceDispatchMetadata | None,
]:
    fact = result.facts[0] if isinstance(
        result,
        SourceHistoryMatched,
    ) else None
    values: dict[str, object] = {
        "decision_kind": "unresolved",
        "history_outcome": "history_unavailable",
        "history_conclusion": None,
        "dispatch_intent_ref": accepted.operation.dispatch_intent_ref,
        "conclusive_observation_ref": None,
        "source_operation_disposition": "reconciliation_unknown",
        "retry_posture": "reconcile_first",
    }
    dispatch_ack = None
    if isinstance(result, SourceHistoryNotFound):
        values.update(
            decision_kind="no_dispatch_proved",
            history_outcome="not_found",
            source_operation_disposition=None,
            dispatch_intent_ref=None,
            retry_posture="safe_retry",
        )
    elif isinstance(fact, AcceptedNoDispatchFact):
        values.update(
            decision_kind="no_dispatch_proved",
            history_outcome="matched",
            history_conclusion=fact.conclusion,
            source_operation_disposition=None,
            dispatch_intent_ref=None,
            retry_posture="safe_retry",
        )
    elif isinstance(fact, DispatchNotObservedFact):
        values.update(
            history_outcome="matched",
            history_conclusion=fact.conclusion,
            dispatch_intent_ref=fact.durable_dispatch_intent_ref,
        )
        dispatch_ack = _recovered_dispatch_ack(
            accepted.dispatch,
            fact=fact,
            history_digest=history_digest,
            acknowledged_at=observed_at,
        )
    elif isinstance(fact, (ObservedResultFact, ObservedFailureFact)):
        observation_ref = (
            fact.result_ref
            if isinstance(fact, ObservedResultFact)
            else fact.failure_ref
        )
        values.update(
            decision_kind="conclusive_observation",
            history_outcome="matched",
            history_conclusion=fact.conclusion,
            dispatch_intent_ref=fact.durable_dispatch_intent_ref,
            conclusive_observation_ref=observation_ref,
            source_operation_disposition=(
                "completed"
                if isinstance(fact, ObservedResultFact)
                else "failed"
            ),
            retry_posture="no_retry",
        )
        dispatch_ack = _recovered_dispatch_ack(
            accepted.dispatch,
            fact=fact,
            history_digest=history_digest,
            acknowledged_at=observed_at,
        )
    elif isinstance(result, SourceHistoryIdentityConflict):
        values["dispatch_intent_ref"] = (
            accepted.operation.dispatch_intent_ref
        )

    decision = SourceOperationReconciliationDecision(
        reconciliation_id=(
            "browser-lane-reconciliation-"
            + sha256(
                (
                    f"{accepted.operation.runtime_run_id}:"
                    f"{accepted.operation.operation_id}:"
                    f"{accepted.operation.reconciliation_revision}:"
                    f"{history_digest}"
                ).encode()
            ).hexdigest()[:40]
        ),
        runtime_run_id=accepted.operation.runtime_run_id,
        operation_id=accepted.operation.operation_id,
        source_id="liepin",
        operation_kind=accepted.operation.operation_kind,
        canonical_request_hash=(
            accepted.operation.canonical_request_hash
        ),
        idempotency_key=accepted.operation.idempotency_key,
        accepted_requirement_revision_id=(
            accepted.operation.accepted_requirement_revision_id
        ),
        runtime_attempt_no=accepted.operation.runtime_attempt_no,
        runtime_attempt_authority_ref=(
            accepted.operation.runtime_attempt_authority_ref
        ),
        history_result_ref=f"sha256:{history_digest}",
        history_result_digest=history_digest,
        expected_ledger_revision=accepted.operation.ledger_revision,
        expected_reconciliation_revision=(
            accepted.operation.reconciliation_revision
        ),
        committed_at=observed_at,
        **values,
    )
    return decision, dispatch_ack


def _recovered_dispatch_ack(
    dispatch: SourceDispatchMetadata,
    *,
    fact: DispatchNotObservedFact
    | ObservedResultFact
    | ObservedFailureFact,
    history_digest: str,
    acknowledged_at: str,
) -> SourceDispatchMetadata:
    if dispatch.status == "acknowledged":
        return dispatch
    return replace(
        dispatch,
        status="acknowledged",
        outbox_revision=2,
        accepted_sidecar_generation=fact.accepted_generation,
        accepted_sidecar_journal_revision=(
            fact.accepted_journal_revision
        ),
        ack_ref=f"sha256:{history_digest}",
        ack_kind=(
            "new_logical_operation"
            if dispatch.dispatch_authorization_ordinal == 1
            else "new_dispatch_authorization"
        ),
        acknowledged_at=acknowledged_at,
    )


def _resume_reconciled_recovery_attention(
    store: RuntimeControlStore,
    *,
    runtime_run_id: str,
    resolved_at: str,
) -> None:
    try:
        run = store.get_run(runtime_run_id)
        if (
            run.status == "needs_attention"
            and run.current_action_id is None
        ):
            store.resolve_source_operation_recovery_attention(
                runtime_run_id=runtime_run_id,
                resolved_at=resolved_at,
            )
    except RuntimeControlError:
        return


def _now() -> str:
    return datetime.now(UTC).isoformat(
        timespec="microseconds",
    ).replace("+00:00", "Z")
