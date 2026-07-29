"""Main-owned execution of the hard-cut Liepin cards Source Operation."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from seektalent.config import AppSettings
from seektalent.sidecar_handshake_protocol import (
    SidecarReadinessError,
    _ProtocolTransport,
    perform_main_handshake,
)
from seektalent.source_port.authenticated_history_frames import (
    PostHandshakeHistorySession,
    ReceivedHistoryResult,
    canonical_source_history_semantics_bytes,
)
from seektalent.source_port.authenticated_liepin_cards_frames import (
    LiepinCardsAcceptedAckV1,
    LiepinCardsSubmitV1,
    ReceivedLiepinCardsAcceptedAck,
    ReceivedLiepinCardsReconcileRequired,
    ReceivedLiepinCardsResult,
)
from seektalent.source_port.authenticated_liepin_details_frames import (
    LiepinDetailsAcceptedAckV1,
    LiepinDetailsSubmitV1,
    ReceivedLiepinDetailsAcceptedAck,
    ReceivedLiepinDetailsReconcileRequired,
    ReceivedLiepinDetailsResult,
)
from seektalent.source_port.authenticated_liepin_source_frames import (
    PostHandshakeLiepinSourceSession,
)
from seektalent.source_port.history_contract import (
    AcceptedNoDispatchFact,
    DispatchNotObservedFact,
    ExactAuthorizationSelector,
    ObservedFailureFact,
    ObservedResultFact,
    SourceHistoryMatched,
    SourceHistoryNotFound,
    SourceHistoryQueryV1,
    SourceHistoryQueryResultV1,
    SourceHistoryUnavailable,
)
from seektalent.source_port.liepin_cards_artifacts import (
    read_liepin_cards_artifact,
)
from seektalent.source_port.liepin_cards_contract import (
    LiepinCardsOperationRequestV1,
    canonical_liepin_cards_request_hash,
    stable_liepin_cards_operation_id,
)
from seektalent.source_port.liepin_details_artifacts import (
    read_liepin_details_artifact,
)
from seektalent.source_port.liepin_details_contract import (
    LiepinDetailsOperationRequestV1,
    canonical_liepin_details_request_hash,
    stable_liepin_details_operation_id,
)
from seektalent.source_port.liepin_cards_sidecar_identity import (
    liepin_cards_sidecar_identity,
)
from seektalent.source_port.operation_dispatch import (
    DispatchAuthorizationV1,
    InitialDeliveryV1,
    OperationIdentityV1,
    OutboxRedeliveryV1,
    RelativeMonotonicDeadlineV1,
)
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_runtime_control.errors import RuntimeControlLookupError


@dataclass(slots=True)
class _SidecarProcess:
    process: subprocess.Popen[bytes]
    transport: _ProtocolTransport
    cards_session: PostHandshakeLiepinSourceSession | None
    history_session: PostHandshakeHistorySession | None

    def close(self) -> None:
        self.transport.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


@dataclass(frozen=True, slots=True)
class _HistoryUnknown:
    ack: LiepinCardsAcceptedAckV1 | LiepinDetailsAcceptedAckV1 | None
    query: SourceHistoryQueryV1
    result: SourceHistoryQueryResultV1
    history_conclusion: str | None
    dispatch_intent_ref: str | None


@dataclass(frozen=True, slots=True)
class _HistoryObserved:
    ack: LiepinCardsAcceptedAckV1 | LiepinDetailsAcceptedAckV1
    query: SourceHistoryQueryV1
    result: SourceHistoryQueryResultV1
    history_conclusion: str
    dispatch_intent_ref: str


class LiepinCardsSourceOperationExecutor:
    """One main authority and one supervised sidecar for cards operations."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        store: RuntimeControlStore,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int,
        accepted_requirement_revision_id: str,
        runtime_attempt_authority_ref: str,
        profile_binding_generation: int = 1,
    ) -> None:
        self._settings = settings
        self._store = store
        self._runtime_run_id = runtime_run_id
        self._executor_id = executor_id
        self._attempt_no = attempt_no
        self._accepted_requirement_revision_id = (
            accepted_requirement_revision_id
        )
        self._runtime_attempt_authority_ref = runtime_attempt_authority_ref
        self._profile_binding_generation = profile_binding_generation
        root = settings.runtime_control_path.parent / "source-port"
        self._journal_path = root / "liepin-cards-journal.sqlite3"
        self._artifact_root = root / "liepin-cards-results"
        self._details_artifact_root = root / "liepin-details-results"
        self._lane_queries: dict[str, str] = {}
        self._pending_checkpoint_operation_ids: set[str] = set()
        self._process: _SidecarProcess | None = None
        # The lock only protects framing on one subprocess pipe and close().
        # Durable admission, replay, and fencing remain store/journal owned.
        self._channel_lock = threading.Lock()

    def bind_lane(self, source_lane_run_id: str, query_instance_id: str) -> None:
        existing = self._lane_queries.setdefault(
            source_lane_run_id,
            query_instance_id,
        )
        if existing != query_instance_id:
            raise RuntimeError("liepin_cards_lane_identity_conflict")

    def __call__(
        self,
        *,
        source_run_id: str,
        query: str,
        max_pages: int,
        max_cards: int,
        native_filters,
    ) -> tuple[dict[str, object], dict[str, object]]:
        request = LiepinCardsOperationRequestV1.model_validate(
            {
                "contract_version": (
                    "seektalent.source.liepin-cards.request/v1"
                ),
                "runtime_run_id": self._runtime_run_id,
                "source_lane_run_id": source_run_id,
                "query_instance_id": self._lane_queries.get(
                    source_run_id,
                    source_run_id,
                ),
                "keyword_query": query,
                "max_pages": max_pages,
                "max_cards": max_cards,
                "native_filters": (
                    dict(native_filters) if native_filters else None
                ),
            },
            strict=True,
        )
        return self._execute(request)

    def execute_details(
        self,
        *,
        source_run_id: str,
        card_ref: str,
        rank: int,
        open_mode: str,
        provider_candidate_key_hash: str | None = None,
        expected_provider_candidate_key_hash: str | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        request = LiepinDetailsOperationRequestV1.model_validate(
            {
                "contract_version": (
                    "seektalent.source.liepin-details.request/v1"
                ),
                "runtime_run_id": self._runtime_run_id,
                "source_lane_run_id": source_run_id,
                "query_instance_id": self._lane_queries.get(
                    source_run_id,
                    source_run_id,
                ),
                "card_ref": card_ref,
                "rank": rank,
                "open_mode": open_mode,
                "provider_candidate_key_hash": provider_candidate_key_hash,
                "expected_provider_candidate_key_hash": (
                    expected_provider_candidate_key_hash
                ),
            },
            strict=True,
        )
        return self._execute_details(request)

    def checkpoint_operation_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._pending_checkpoint_operation_ids))

    def checkpoint_committed(self, operation_ids: tuple[str, ...]) -> None:
        self._pending_checkpoint_operation_ids.difference_update(operation_ids)

    def close(self) -> None:
        with self._channel_lock:
            process, self._process = self._process, None
            if process is not None:
                process.close()

    def _execute(
        self,
        request: LiepinCardsOperationRequestV1,
    ) -> tuple[dict[str, object], dict[str, object]]:
        operation_id = stable_liepin_cards_operation_id(request)
        request_hash = canonical_liepin_cards_request_hash(request)
        existing = None
        try:
            existing = self._store.get_accepted_source_operation_context(
                self._runtime_run_id,
                operation_id,
            )
        except RuntimeControlLookupError:
            existing = None
        identity = self._identity(
            request,
            operation_id=operation_id,
            request_hash=request_hash,
            existing=existing,
        )
        if existing is None:
            authorization = DispatchAuthorizationV1.create_initial(
                identity=identity,
                dispatch_intent_id=f"dispatch-{operation_id}",
                dispatch_intent_revision=1,
                source_operation_acceptance_ref=(
                    f"source-acceptance://{operation_id}/1"
                ),
            )
            accepted = self._store.accept_source_operation(
                runtime_run_id=self._runtime_run_id,
                operation_id=operation_id,
                source_id="liepin",
                operation_kind="cards",
                canonical_request_hash=request_hash,
                idempotency_key=identity.idempotency_key,
                accepted_requirement_revision_id=(
                    identity.accepted_requirement_revision_id
                ),
                runtime_attempt_no=identity.attempt_no,
                runtime_attempt_authority_ref=(
                    self._runtime_attempt_authority_ref
                ),
                runtime_attempt_fence_ref=(
                    identity.runtime_attempt_fence_ref
                ),
                profile_binding_generation=(
                    identity.profile_binding_generation
                ),
                browser_control_scope_id=(
                    identity.browser_control_scope_id
                ),
                controller_fence_ref=None,
                outbox_id=f"outbox-{operation_id}",
                dispatch_intent_id=authorization.dispatch_intent_id,
                dispatch_intent_revision=(
                    authorization.dispatch_intent_revision
                ),
                dispatch_intent_digest=(
                    authorization.dispatch_intent_digest
                ),
                dispatch_authorization_ordinal=1,
                source_operation_acceptance_ref=(
                    authorization.source_operation_acceptance_ref
                ),
                expected_ledger_revision=1,
                expected_reconciliation_revision=0,
            )
        else:
            accepted = existing
            authorization = _authorization_from_acceptance(
                identity,
                existing.dispatch,
            )
        delivery = (
            OutboxRedeliveryV1(
                delivery_mode="outbox_redelivery",
                authorization=authorization,
            )
            if existing is not None
            else InitialDeliveryV1(
                delivery_mode="initial",
                authorization=authorization,
            )
        )
        submit = LiepinCardsSubmitV1(
            contract_version="seektalent.source.liepin-cards.submit/v1",
            identity=identity,
            delivery=delivery,
            request=request,
        )
        if existing is not None:
            recovered = self._query_terminal_history_safely(accepted, identity)
            if isinstance(recovered, _HistoryObserved):
                replayed = self._replay_observed_terminal(submit)
                if replayed is None:
                    self._record_reconciliation_unknown(
                        _unknown_from_observed(recovered),
                        operation_id,
                    )
                    return _unknown_result()
                ack, terminal = replayed
            else:
                if isinstance(recovered, _HistoryUnknown):
                    self._record_reconciliation_unknown(
                        recovered,
                        operation_id,
                    )
                return _unknown_result()
        else:
            ack = None
            terminal = None
        try:
            if terminal is None:
                ack, terminal = self._exchange(submit)
        except (OSError, RuntimeError, SidecarReadinessError):
            recovered = self._query_terminal_history_safely(accepted, identity)
            if recovered is None:
                return _unknown_result()
            if isinstance(recovered, _HistoryUnknown):
                ack, terminal = recovered.ack, recovered
            elif isinstance(recovered, _HistoryObserved):
                replayed = self._replay_observed_terminal(submit)
                if replayed is None:
                    self._record_reconciliation_unknown(
                        _unknown_from_observed(recovered),
                        operation_id,
                    )
                    return _unknown_result()
                ack, terminal = replayed
            else:
                ack, terminal = recovered
        if ack is not None and accepted.dispatch.status == "pending":
            self._store.record_source_dispatch_ack(
                runtime_run_id=self._runtime_run_id,
                operation_id=operation_id,
                outbox_id=accepted.dispatch.outbox_id,
                canonical_request_hash=request_hash,
                dispatch_intent_id=authorization.dispatch_intent_id,
                dispatch_intent_revision=authorization.dispatch_intent_revision,
                dispatch_intent_digest=authorization.dispatch_intent_digest,
                dispatch_authorization_ordinal=(
                    authorization.dispatch_authorization_ordinal
                ),
                expected_outbox_revision=accepted.dispatch.outbox_revision,
                accepted_sidecar_generation=ack.sidecar_generation,
                accepted_sidecar_journal_revision=(
                    ack.accepted_journal_revision
                ),
                ack_ref=(
                    f"source-ack://{operation_id}/"
                    f"{authorization.dispatch_authorization_ordinal}"
                ),
                ack_kind=(
                    "new_logical_operation"
                    if authorization.dispatch_authorization_ordinal == 1
                    else "new_dispatch_authorization"
                ),
                acknowledged_at=_now(),
            )
        if isinstance(terminal, _HistoryUnknown):
            self._record_reconciliation_unknown(terminal, operation_id)
            return _unknown_result()
        if isinstance(terminal, ReceivedLiepinCardsReconcileRequired):
            recovered = self._query_terminal_history_safely(accepted, identity)
            if recovered is None:
                return _unknown_result()
            if isinstance(recovered, _HistoryUnknown):
                self._record_reconciliation_unknown(
                    recovered,
                    operation_id,
                )
                return _unknown_result()
            if isinstance(recovered, _HistoryObserved):
                replayed = self._replay_observed_terminal(submit)
                if replayed is None:
                    self._record_reconciliation_unknown(
                        _unknown_from_observed(recovered),
                        operation_id,
                    )
                    return _unknown_result()
                recovered_ack, terminal = replayed
                ack = ack or recovered_ack
            else:
                recovered_ack, terminal = recovered
                ack = ack or recovered_ack
        observation = terminal.payload.observation
        current = self._store.get_source_operation(
            self._runtime_run_id,
            operation_id,
        )
        if current.operation_phase in {"accepted", "reconciled"}:
            self._store.record_owned_source_operation_observation(
                runtime_run_id=self._runtime_run_id,
                operation_id=operation_id,
                executor_id=self._executor_id,
                attempt_no=self._attempt_no,
                expected_ledger_revision=current.ledger_revision,
                dispatch_intent_ref=(
                    ack.dispatch_intent_ref
                    if ack is not None
                    else (
                        f"source-dispatch://{operation_id}/"
                        f"{authorization.dispatch_authorization_ordinal}"
                    )
                ),
                conclusive_observation_ref=observation.artifact_ref or "",
                source_operation_disposition=observation.disposition,
                observed_at=_now(),
            )
        try:
            artifact = read_liepin_cards_artifact(
                self._artifact_root,
                observation.artifact_ref or "",
                expected_hash=observation.artifact_hash or "",
            )
        except (OSError, ValueError):
            return _artifact_unavailable_result(observation)
        if (
            self._store.get_source_operation(
                self._runtime_run_id,
                operation_id,
            ).operation_phase
            != "main_committed"
        ):
            self._pending_checkpoint_operation_ids.add(operation_id)
        return _workflow_result(request, artifact, observation)

    def _execute_details(
        self,
        request: LiepinDetailsOperationRequestV1,
    ) -> tuple[dict[str, object], dict[str, object]]:
        operation_id = stable_liepin_details_operation_id(request)
        request_hash = canonical_liepin_details_request_hash(request)
        existing = None
        try:
            existing = self._store.get_accepted_source_operation_context(
                self._runtime_run_id,
                operation_id,
            )
        except RuntimeControlLookupError:
            existing = None
        identity = self._details_identity(
            request,
            operation_id=operation_id,
            request_hash=request_hash,
            existing=existing,
        )
        if existing is None:
            authorization = DispatchAuthorizationV1.create_initial(
                identity=identity,
                dispatch_intent_id=f"dispatch-{operation_id}",
                dispatch_intent_revision=1,
                source_operation_acceptance_ref=(
                    f"source-acceptance://{operation_id}/1"
                ),
            )
            accepted = self._store.accept_source_operation(
                runtime_run_id=self._runtime_run_id,
                operation_id=operation_id,
                source_id="liepin",
                operation_kind="details",
                canonical_request_hash=request_hash,
                idempotency_key=identity.idempotency_key,
                accepted_requirement_revision_id=(
                    identity.accepted_requirement_revision_id
                ),
                runtime_attempt_no=identity.attempt_no,
                runtime_attempt_authority_ref=(
                    self._runtime_attempt_authority_ref
                ),
                runtime_attempt_fence_ref=identity.runtime_attempt_fence_ref,
                profile_binding_generation=identity.profile_binding_generation,
                browser_control_scope_id=identity.browser_control_scope_id,
                controller_fence_ref=None,
                outbox_id=f"outbox-{operation_id}",
                dispatch_intent_id=authorization.dispatch_intent_id,
                dispatch_intent_revision=authorization.dispatch_intent_revision,
                dispatch_intent_digest=authorization.dispatch_intent_digest,
                dispatch_authorization_ordinal=1,
                source_operation_acceptance_ref=(
                    authorization.source_operation_acceptance_ref
                ),
                expected_ledger_revision=1,
                expected_reconciliation_revision=0,
            )
        else:
            accepted = existing
            authorization = _authorization_from_acceptance(
                identity,
                existing.dispatch,
            )
        delivery = (
            OutboxRedeliveryV1(
                delivery_mode="outbox_redelivery",
                authorization=authorization,
            )
            if existing is not None
            else InitialDeliveryV1(
                delivery_mode="initial",
                authorization=authorization,
            )
        )
        submit = LiepinDetailsSubmitV1(
            contract_version="seektalent.source.liepin-details.submit/v1",
            identity=identity,
            delivery=delivery,
            request=request,
        )
        if existing is not None:
            recovered = self._query_terminal_history_safely(accepted, identity)
            if isinstance(recovered, _HistoryObserved):
                replayed = self._replay_observed_details_terminal(submit)
                if replayed is None:
                    self._record_reconciliation_unknown(
                        _unknown_from_observed(recovered),
                        operation_id,
                    )
                    return _details_unknown_result()
                ack, terminal = replayed
            else:
                if isinstance(recovered, _HistoryUnknown):
                    self._record_reconciliation_unknown(
                        recovered,
                        operation_id,
                    )
                return _details_unknown_result()
        else:
            ack = None
            terminal = None
        try:
            if terminal is None:
                ack, terminal = self._exchange_details(submit)
        except (OSError, RuntimeError, SidecarReadinessError):
            recovered = self._query_terminal_history_safely(accepted, identity)
            if recovered is None:
                return _details_unknown_result()
            if isinstance(recovered, _HistoryUnknown):
                ack, terminal = recovered.ack, recovered
            elif isinstance(recovered, _HistoryObserved):
                replayed = self._replay_observed_details_terminal(submit)
                if replayed is None:
                    self._record_reconciliation_unknown(
                        _unknown_from_observed(recovered),
                        operation_id,
                    )
                    return _details_unknown_result()
                ack, terminal = replayed
            else:
                ack, terminal = recovered
        if ack is not None and accepted.dispatch.status == "pending":
            self._store.record_source_dispatch_ack(
                runtime_run_id=self._runtime_run_id,
                operation_id=operation_id,
                outbox_id=accepted.dispatch.outbox_id,
                canonical_request_hash=request_hash,
                dispatch_intent_id=authorization.dispatch_intent_id,
                dispatch_intent_revision=authorization.dispatch_intent_revision,
                dispatch_intent_digest=authorization.dispatch_intent_digest,
                dispatch_authorization_ordinal=(
                    authorization.dispatch_authorization_ordinal
                ),
                expected_outbox_revision=accepted.dispatch.outbox_revision,
                accepted_sidecar_generation=ack.sidecar_generation,
                accepted_sidecar_journal_revision=ack.accepted_journal_revision,
                ack_ref=(
                    f"source-ack://{operation_id}/"
                    f"{authorization.dispatch_authorization_ordinal}"
                ),
                ack_kind=(
                    "new_logical_operation"
                    if authorization.dispatch_authorization_ordinal == 1
                    else "new_dispatch_authorization"
                ),
                acknowledged_at=_now(),
            )
        if isinstance(terminal, _HistoryUnknown):
            self._record_reconciliation_unknown(terminal, operation_id)
            return _details_unknown_result()
        if isinstance(terminal, ReceivedLiepinDetailsReconcileRequired):
            recovered = self._query_terminal_history_safely(accepted, identity)
            if recovered is None:
                return _details_unknown_result()
            if isinstance(recovered, _HistoryUnknown):
                self._record_reconciliation_unknown(recovered, operation_id)
                return _details_unknown_result()
            if isinstance(recovered, _HistoryObserved):
                replayed = self._replay_observed_details_terminal(submit)
                if replayed is None:
                    self._record_reconciliation_unknown(
                        _unknown_from_observed(recovered),
                        operation_id,
                    )
                    return _details_unknown_result()
                recovered_ack, terminal = replayed
                ack = ack or recovered_ack
            else:
                recovered_ack, terminal = recovered
                ack = ack or recovered_ack
        observation = terminal.payload.observation
        current = self._store.get_source_operation(
            self._runtime_run_id,
            operation_id,
        )
        if current.operation_phase in {"accepted", "reconciled"}:
            self._store.record_owned_source_operation_observation(
                runtime_run_id=self._runtime_run_id,
                operation_id=operation_id,
                executor_id=self._executor_id,
                attempt_no=self._attempt_no,
                expected_ledger_revision=current.ledger_revision,
                dispatch_intent_ref=(
                    ack.dispatch_intent_ref
                    if ack is not None
                    else (
                        f"source-dispatch://{operation_id}/"
                        f"{authorization.dispatch_authorization_ordinal}"
                    )
                ),
                conclusive_observation_ref=observation.artifact_ref or "",
                source_operation_disposition=observation.disposition,
                observed_at=_now(),
            )
        try:
            artifact = read_liepin_details_artifact(
                self._details_artifact_root,
                observation.artifact_ref or "",
                expected_hash=observation.artifact_hash or "",
            )
        except (OSError, ValueError):
            return _details_artifact_unavailable_result(observation)
        if (
            self._store.get_source_operation(
                self._runtime_run_id,
                operation_id,
            ).operation_phase
            != "main_committed"
        ):
            self._pending_checkpoint_operation_ids.add(operation_id)
        return _details_workflow_result(request, artifact, observation)

    def _identity(
        self,
        request: LiepinCardsOperationRequestV1,
        *,
        operation_id: str,
        request_hash: str,
        existing,
    ) -> OperationIdentityV1:
        fence_ref = sha256(
            (
                f"{self._runtime_run_id}:{self._executor_id}:"
                f"{self._attempt_no}:{self._runtime_attempt_authority_ref}"
            ).encode()
        ).hexdigest()
        expectation = existing.expectation if existing is not None else None
        operation = existing.operation if existing is not None else None
        return OperationIdentityV1(
            run_id=self._runtime_run_id,
            operation_id=operation_id,
            attempt_no=(
                expectation.runtime_attempt_no
                if expectation is not None
                else self._attempt_no
            ),
            source="liepin",
            operation_kind="cards",
            request_hash=request_hash,
            idempotency_key=f"cards-key-{operation_id.removeprefix('cards_')}",
            correlation_id=f"cards-correlation-{operation_id.removeprefix('cards_')}",
            accepted_requirement_revision_id=(
                operation.accepted_requirement_revision_id
                if operation is not None
                else self._accepted_requirement_revision_id
            ),
            runtime_attempt_fence_ref=(
                expectation.runtime_attempt_fence_ref
                if expectation is not None
                else fence_ref
            ),
            profile_binding_generation=(
                expectation.profile_binding_generation
                if expectation is not None
                else self._profile_binding_generation
            ),
            browser_control_scope_id=(
                expectation.browser_control_scope_id
                if expectation is not None
                and expectation.browser_control_scope_id is not None
                else f"cards-scope-{operation_id.removeprefix('cards_')}"
            ),
            deadline=RelativeMonotonicDeadlineV1(
                value=min(
                    900_000,
                    max(
                        1,
                        int(
                            self._settings.liepin_worker_timeout_seconds
                            * 1000
                        ),
                    ),
                ),
                clock="relative_monotonic",
                unit="milliseconds",
            ),
            expected_source_operation_ledger_revision=(
                existing.dispatch.expected_ledger_revision
                if existing is not None
                else 1
            ),
            expected_reconciliation_revision=(
                existing.dispatch.expected_reconciliation_revision
                if existing is not None
                else 0
            ),
        )

    def _details_identity(
        self,
        request: LiepinDetailsOperationRequestV1,
        *,
        operation_id: str,
        request_hash: str,
        existing,
    ) -> OperationIdentityV1:
        fence_ref = sha256(
            (
                f"{self._runtime_run_id}:{self._executor_id}:"
                f"{self._attempt_no}:{self._runtime_attempt_authority_ref}"
            ).encode()
        ).hexdigest()
        expectation = existing.expectation if existing is not None else None
        operation = existing.operation if existing is not None else None
        suffix = operation_id.removeprefix("details_")
        return OperationIdentityV1(
            run_id=self._runtime_run_id,
            operation_id=operation_id,
            attempt_no=(
                expectation.runtime_attempt_no
                if expectation is not None
                else self._attempt_no
            ),
            source="liepin",
            operation_kind="details",
            request_hash=request_hash,
            idempotency_key=f"details-key-{suffix}",
            correlation_id=f"details-correlation-{suffix}",
            accepted_requirement_revision_id=(
                operation.accepted_requirement_revision_id
                if operation is not None
                else self._accepted_requirement_revision_id
            ),
            runtime_attempt_fence_ref=(
                expectation.runtime_attempt_fence_ref
                if expectation is not None
                else fence_ref
            ),
            profile_binding_generation=(
                expectation.profile_binding_generation
                if expectation is not None
                else self._profile_binding_generation
            ),
            browser_control_scope_id=(
                expectation.browser_control_scope_id
                if expectation is not None
                and expectation.browser_control_scope_id is not None
                else f"details-scope-{suffix}"
            ),
            deadline=RelativeMonotonicDeadlineV1(
                value=min(
                    900_000,
                    max(
                        1,
                        int(
                            self._settings.liepin_worker_timeout_seconds
                            * 1000
                        ),
                    ),
                ),
                clock="relative_monotonic",
                unit="milliseconds",
            ),
            expected_source_operation_ledger_revision=(
                existing.dispatch.expected_ledger_revision
                if existing is not None
                else 1
            ),
            expected_reconciliation_revision=(
                existing.dispatch.expected_reconciliation_revision
                if existing is not None
                else 0
            ),
        )

    def _exchange(self, submit: LiepinCardsSubmitV1):
        with self._channel_lock:
            process = self._ready_source_process()
            assert process.cards_session is not None
            session = process.cards_session
            message_id = f"submit-{secrets.token_hex(16)}"
            deadline = time.monotonic() + (
                submit.identity.deadline.value / 1000
            )
            process.transport.write_raw(
                session.encode_cards_submit(
                    message_id=message_id,
                    correlation_id=submit.identity.correlation_id,
                    payload=submit,
                ),
                deadline,
            )
            ack = None
            while True:
                messages = session.feed(
                    process.transport.read_history_chunk(
                        deadline,
                        process.process,
                    )
                )
                for message in messages:
                    if isinstance(message, ReceivedLiepinCardsAcceptedAck):
                        ack = message.payload
                        continue
                    if isinstance(
                        message,
                        (
                            ReceivedLiepinCardsResult,
                            ReceivedLiepinCardsReconcileRequired,
                        ),
                    ):
                        if ack is None:
                            raise RuntimeError("liepin_cards_ack_missing")
                        return ack, message

    def _exchange_details(self, submit: LiepinDetailsSubmitV1):
        with self._channel_lock:
            process = self._ready_source_process()
            assert process.cards_session is not None
            session = process.cards_session
            message_id = f"submit-{secrets.token_hex(16)}"
            deadline = time.monotonic() + (
                submit.identity.deadline.value / 1000
            )
            process.transport.write_raw(
                session.encode_details_submit(
                    message_id=message_id,
                    correlation_id=submit.identity.correlation_id,
                    payload=submit,
                ),
                deadline,
            )
            ack = None
            while True:
                messages = session.feed(
                    process.transport.read_history_chunk(
                        deadline,
                        process.process,
                    )
                )
                for message in messages:
                    if isinstance(message, ReceivedLiepinDetailsAcceptedAck):
                        ack = message.payload
                        continue
                    if isinstance(
                        message,
                        (
                            ReceivedLiepinDetailsResult,
                            ReceivedLiepinDetailsReconcileRequired,
                        ),
                    ):
                        if ack is None:
                            raise RuntimeError("liepin_details_ack_missing")
                        return ack, message

    def _query_terminal_history_safely(self, accepted, identity):
        try:
            return self._query_terminal_history(accepted, identity)
        except (OSError, RuntimeError, SidecarReadinessError):
            query = _history_query(accepted, identity)
            return _HistoryUnknown(
                ack=None,
                query=query,
                result=SourceHistoryUnavailable.model_validate(
                    {
                        **query.model_dump(mode="python"),
                        "contract_version": (
                            "seektalent.source-port.query.result/v1"
                        ),
                        "outcome": "history_unavailable",
                        "reason": "unreadable",
                        "oldest_retained_generation": None,
                        "newest_known_generation": None,
                    },
                    strict=True,
                ),
                history_conclusion=None,
                dispatch_intent_ref=None,
            )

    def _query_terminal_history(self, accepted, identity):
        operation_kind = identity.operation_kind
        process = _spawn_sidecar(
            settings=self._settings,
            journal_path=self._journal_path,
            artifact_root=self._artifact_root,
            history_only=True,
        )
        try:
            assert process.history_session is not None
            session = process.history_session
            searched_last_generation = max(
                1,
                accepted.dispatch.accepted_sidecar_generation or 1,
            )
            while True:
                query = _history_query(
                    accepted,
                    identity,
                    searched_last_generation=searched_last_generation,
                )
                message_id = f"history-{secrets.token_hex(16)}"
                deadline = time.monotonic() + 30
                process.transport.write_raw(
                    session.encode_query(
                        message_id=message_id,
                        correlation_id=identity.correlation_id,
                        payload=query,
                    ),
                    deadline,
                )
                result_message = None
                while result_message is None:
                    messages = session.feed(
                        process.transport.read_history_chunk(
                            deadline,
                            process.process,
                        )
                    )
                    result_message = next(
                        (
                            message
                            for message in messages
                            if isinstance(message, ReceivedHistoryResult)
                        ),
                        None,
                    )
                result = result_message.payload
                if isinstance(
                    result,
                    (SourceHistoryNotFound, SourceHistoryUnavailable),
                ):
                    newest = result.newest_known_generation
                    if (
                        newest is not None
                        and newest > searched_last_generation
                    ):
                        searched_last_generation = newest
                        continue
                    return _HistoryUnknown(
                        ack=None,
                        query=query,
                        result=result,
                        history_conclusion=None,
                        dispatch_intent_ref=None,
                    )
                if not isinstance(result, SourceHistoryMatched):
                    return None
                for fact in result.facts:
                    if isinstance(
                        fact,
                        (AcceptedNoDispatchFact, DispatchNotObservedFact),
                    ):
                        dispatch_ref = (
                            fact.durable_dispatch_intent_ref
                            if isinstance(fact, DispatchNotObservedFact)
                            else None
                        )
                        recovered_ack = _recovered_ack(
                            identity=identity,
                            accepted=accepted,
                            operation_kind=operation_kind,
                            sidecar_generation=fact.accepted_generation,
                            accepted_journal_revision=fact.accepted_journal_revision,
                            dispatch_intent_ref=(
                                dispatch_ref
                                or f"source-dispatch://"
                                f"{identity.operation_id}/"
                                f"{accepted.dispatch.dispatch_authorization_ordinal}"
                            ),
                        )
                        return _HistoryUnknown(
                            ack=recovered_ack,
                            query=query,
                            result=result,
                            history_conclusion=fact.conclusion,
                            dispatch_intent_ref=dispatch_ref,
                        )
                    if not isinstance(
                        fact,
                        (ObservedResultFact, ObservedFailureFact),
                    ):
                        continue
                    recovered_ack = _recovered_ack(
                        identity=identity,
                        accepted=accepted,
                        operation_kind=operation_kind,
                        sidecar_generation=fact.accepted_generation,
                        accepted_journal_revision=fact.accepted_journal_revision,
                        dispatch_intent_ref=fact.durable_dispatch_intent_ref,
                    )
                    return _HistoryObserved(
                        ack=recovered_ack,
                        query=query,
                        result=result,
                        history_conclusion=fact.conclusion,
                        dispatch_intent_ref=(
                            fact.durable_dispatch_intent_ref
                        ),
                    )
                return None
        finally:
            process.close()

    def _replay_observed_terminal(
        self,
        submit: LiepinCardsSubmitV1,
    ):
        process, self._process = self._process, None
        if process is not None:
            process.close()
        try:
            ack, terminal = self._exchange(submit)
        except (OSError, RuntimeError, SidecarReadinessError):
            return None
        if not isinstance(terminal, ReceivedLiepinCardsResult):
            return None
        return ack, terminal

    def _replay_observed_details_terminal(
        self,
        submit: LiepinDetailsSubmitV1,
    ):
        process, self._process = self._process, None
        if process is not None:
            process.close()
        try:
            ack, terminal = self._exchange_details(submit)
        except (OSError, RuntimeError, SidecarReadinessError):
            return None
        if not isinstance(terminal, ReceivedLiepinDetailsResult):
            return None
        return ack, terminal

    def _record_reconciliation_unknown(
        self,
        history: _HistoryUnknown,
        operation_id: str,
    ) -> None:
        semantic_bytes = canonical_source_history_semantics_bytes(
            history.query,
            history.result,
        )
        digest = sha256(semantic_bytes).hexdigest()
        current = self._store.get_source_operation(
            self._runtime_run_id,
            operation_id,
        )
        if current.operation_phase != "accepted":
            return
        history_outcome = getattr(
            history.result,
            "outcome",
            "history_unavailable",
        )
        self._store.record_owned_source_reconciliation_unknown(
            runtime_run_id=self._runtime_run_id,
            operation_id=operation_id,
            executor_id=self._executor_id,
            attempt_no=self._attempt_no,
            expected_ledger_revision=current.ledger_revision,
            expected_reconciliation_revision=(
                current.reconciliation_revision
            ),
            history_result_ref=f"sha256:{digest}",
            history_result_digest=digest,
            history_outcome=history_outcome,
            history_conclusion=history.history_conclusion,
            dispatch_intent_ref=history.dispatch_intent_ref,
            committed_at=_now(),
        )

    def _ready_source_process(self) -> _SidecarProcess:
        if (
            self._process is None
            or self._process.process.poll() is not None
        ):
            if self._process is not None:
                self._process.close()
            self._process = _spawn_sidecar(
                settings=self._settings,
                journal_path=self._journal_path,
                artifact_root=self._artifact_root,
                history_only=False,
            )
        return self._process


def _spawn_sidecar(
    *,
    settings: AppSettings,
    journal_path: Path,
    artifact_root: Path,
    history_only: bool,
    module: str = "seektalent.liepin_cards_sidecar",
    environment_overrides: dict[str, str] | None = None,
) -> _SidecarProcess:
    if not history_only:
        journal_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    command = [
        sys.executable,
        "-m",
        module,
        "--journal",
        str(journal_path),
        "--artifacts",
        str(artifact_root),
    ]
    if history_only:
        command.append("--history-only")
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["SEEKTALENT_RUNTIME_ARTIFACT_OUTPUT_MODE"] = "prod"
    if environment_overrides is not None:
        environment.update(environment_overrides)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=settings.project_root,
        env=environment,
    )
    if process.stdin is None or process.stdout is None:
        process.kill()
        process.wait()
        raise RuntimeError("liepin_cards_sidecar_pipe_missing")
    transport = _ProtocolTransport(process.stdout, process.stdin)
    identity = liepin_cards_sidecar_identity()
    try:
        material = perform_main_handshake(
            transport,
            identity,
            product_build_id=identity.product_build_id,
            main_application_build_id=(
                identity.expected_main_application_build_id
            ),
            deadline=time.monotonic() + 30,
            process=process,
        )
    except BaseException:
        transport.close()
        process.kill()
        process.wait()
        raise
    cards_session = (
        None
        if history_only
        else PostHandshakeLiepinSourceSession(
            role="main",
            session_id=material.session_id,
            protocol_minor=material.protocol_minor,
            main_to_sidecar_key=material.main_to_sidecar_key,
            sidecar_to_main_key=material.sidecar_to_main_key,
        )
    )
    history_session = (
        PostHandshakeHistorySession.for_main(
            session_id=material.session_id,
            protocol_minor=material.protocol_minor,
            main_to_sidecar_key=material.main_to_sidecar_key,
            sidecar_to_main_key=material.sidecar_to_main_key,
        )
        if history_only
        else None
    )
    return _SidecarProcess(
        process=process,
        transport=transport,
        cards_session=cards_session,
        history_session=history_session,
    )


def _authorization_from_acceptance(identity, dispatch):
    values = {
        "identity": identity,
        "dispatch_intent_id": dispatch.dispatch_intent_id,
        "dispatch_intent_revision": dispatch.dispatch_intent_revision,
        "source_operation_acceptance_ref": (
            dispatch.source_operation_acceptance_ref
        ),
    }
    if dispatch.dispatch_authorization_ordinal == 1:
        authorization = DispatchAuthorizationV1.create_initial(**values)
    else:
        if dispatch.safe_retry_commit_ref is None:
            raise RuntimeError("liepin_cards_safe_retry_authority_missing")
        authorization = DispatchAuthorizationV1.create_safe_retry(
            **values,
            dispatch_authorization_ordinal=(
                dispatch.dispatch_authorization_ordinal
            ),
            safe_retry_commit_ref=dispatch.safe_retry_commit_ref,
        )
    if authorization.dispatch_intent_digest != dispatch.dispatch_intent_digest:
        raise RuntimeError("liepin_cards_dispatch_authority_conflict")
    return authorization


def _history_query(
    accepted,
    identity,
    *,
    searched_last_generation: int | None = None,
) -> SourceHistoryQueryV1:
    last_generation = searched_last_generation or max(
        1,
        accepted.dispatch.accepted_sidecar_generation or 1,
    )
    return SourceHistoryQueryV1(
        contract_version="seektalent.source-port.query.request/v1",
        run_id=identity.run_id,
        operation_id=identity.operation_id,
        source="liepin",
        operation_kind=identity.operation_kind,
        idempotency_key=identity.idempotency_key,
        request_hash=identity.request_hash,
        attempt_no=identity.attempt_no,
        authorization_selector=ExactAuthorizationSelector(
            kind="exact",
            ordinal=accepted.dispatch.dispatch_authorization_ordinal,
        ),
        accepted_generation_hint=(
            accepted.dispatch.accepted_sidecar_generation
        ),
        searched_first_generation=1,
        searched_last_generation=last_generation,
        expected_source_operation_ledger_revision=(
            accepted.operation.ledger_revision
        ),
        expected_reconciliation_revision=(
            accepted.operation.reconciliation_revision
        ),
    )


def _recovered_ack(
    *,
    identity,
    accepted,
    operation_kind: str,
    sidecar_generation: int,
    accepted_journal_revision: int,
    dispatch_intent_ref: str,
):
    ack_kind = (
        "new_logical_operation"
        if accepted.dispatch.dispatch_authorization_ordinal == 1
        else "new_dispatch_authorization"
    )
    if operation_kind == "details":
        return LiepinDetailsAcceptedAckV1(
            contract_version="seektalent.source.liepin-details.ack/v1",
            identity=identity,
            sidecar_generation=sidecar_generation,
            accepted_journal_revision=accepted_journal_revision,
            ack_kind=ack_kind,
            dispatch_intent_ref=dispatch_intent_ref,
        )
    return LiepinCardsAcceptedAckV1(
        contract_version="seektalent.source.liepin-cards.ack/v1",
        identity=identity,
        sidecar_generation=sidecar_generation,
        accepted_journal_revision=accepted_journal_revision,
        ack_kind=ack_kind,
        dispatch_intent_ref=dispatch_intent_ref,
    )


def _unknown_from_observed(
    observed: _HistoryObserved,
) -> _HistoryUnknown:
    return _HistoryUnknown(
        ack=observed.ack,
        query=observed.query,
        result=observed.result,
        history_conclusion=observed.history_conclusion,
        dispatch_intent_ref=observed.dispatch_intent_ref,
    )


def _workflow_result(request, artifact, observation):
    status = (
        "succeeded"
        if observation.disposition == "completed"
        else (
            "partial"
            if observation.disposition == "partial"
            else "failed"
        )
    )
    envelope = {
        "status": status,
        "cards_seen": artifact.cards_seen,
        "safe_reason_code": observation.safe_reason_code,
    }
    structured = {
        "ok": status in {"succeeded", "partial"},
        "action": "extract_structured_liepin_cards",
        "safe_reason_code": observation.safe_reason_code,
        "counts": {"cards": len(artifact.cards)},
        "observation": {
            "schema_version": (
                "seektalent.opencli_liepin_structured_cards.v1"
            ),
            "source_run_id": request.source_lane_run_id,
            "cards": list(artifact.cards),
            "card_count": len(artifact.cards),
        },
    }
    return envelope, structured


def _artifact_unavailable_result(observation):
    reason = "liepin_cards_artifact_unavailable"
    return (
        {
            "status": "failed",
            "cards_seen": observation.cards_seen,
            "safe_reason_code": reason,
        },
        {
            "ok": False,
            "action": "extract_structured_liepin_cards",
            "safe_reason_code": reason,
            "counts": {},
            "observation": {},
        },
    )


def _unknown_result():
    return (
        {
            "status": "failed",
            "cards_seen": 0,
            "safe_reason_code": "liepin_cards_reconciliation_unknown",
        },
        {
            "ok": False,
            "action": "extract_structured_liepin_cards",
            "safe_reason_code": "liepin_cards_reconciliation_unknown",
            "counts": {},
            "observation": {},
        },
    )


def _details_workflow_result(request, artifact, observation):
    status = (
        "succeeded"
        if observation.disposition == "completed"
        else (
            "partial"
            if observation.disposition == "partial"
            else "failed"
        )
    )
    action = (
        "resolve_liepin_detail_locator"
        if artifact.open_mode == "resolve_locator"
        else "capture_liepin_detail_resume"
    )
    envelope = {
        "status": status,
        "safe_reason_code": observation.safe_reason_code,
        "provider_candidate_key_hash": artifact.provider_candidate_key_hash,
        "detail_url": artifact.detail_url,
        "rank": artifact.rank,
        "card_ref": artifact.card_ref,
        "open_mode": artifact.open_mode,
        "action_attempted": artifact.action_attempted,
    }
    structured = {
        "ok": status in {"succeeded", "partial"},
        "action": action,
        "safe_reason_code": observation.safe_reason_code,
        "counts": {
            "rank": artifact.rank,
            "action_attempted": artifact.action_attempted,
        },
        "observation": artifact.resume or {},
        "provider_candidate_key_hash": artifact.provider_candidate_key_hash,
        "detail_url": artifact.detail_url,
    }
    return envelope, structured


def _details_artifact_unavailable_result(observation):
    reason = "liepin_details_artifact_unavailable"
    return (
        {
            "status": "failed",
            "safe_reason_code": reason,
        },
        {
            "ok": False,
            "action": "capture_liepin_detail_resume",
            "safe_reason_code": reason,
            "counts": {
                "rank": observation.rank,
                "action_attempted": observation.action_attempted,
            },
            "observation": {},
        },
    )


def _details_unknown_result():
    return (
        {
            "status": "failed",
            "safe_reason_code": "liepin_details_reconciliation_unknown",
        },
        {
            "ok": False,
            "action": "capture_liepin_detail_resume",
            "safe_reason_code": "liepin_details_reconciliation_unknown",
            "counts": {},
            "observation": {},
        },
    )


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


__all__ = ["LiepinCardsSourceOperationExecutor"]
