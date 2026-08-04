"""Main-owned execution of the hard-cut Liepin cards Source Operation."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast

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
    LiepinCardsArtifactV1,
    LiepinCardsOperationRequestV1,
    canonical_liepin_cards_request_hash,
    stable_liepin_cards_operation_id,
)
from seektalent.source_port.liepin_details_artifacts import (
    read_liepin_details_artifact,
)
from seektalent.source_port.liepin_detail_work_plan_artifacts import (
    LiepinDetailWorkItemV1,
    LiepinDetailWorkPlanV1,
    read_liepin_detail_work_plan_artifact,
    write_liepin_detail_work_plan_artifact,
)
from seektalent.source_port.liepin_details_request_artifacts import (
    read_liepin_details_request_artifact,
    write_liepin_details_request_artifact,
)
from seektalent.source_port.liepin_round_work_plan_artifacts import (
    LiepinRoundWorkPlanV1,
    read_liepin_round_work_plan_artifact,
    write_liepin_round_work_plan_artifact,
)
from seektalent.source_port.wire_primitives import canonical_json_bytes
from seektalent.source_port.liepin_details_contract import (
    LiepinDetailsArtifactV1,
    LiepinDetailsObservationV1,
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
from seektalent.source_port.verify_session_contract import (
    VerifySessionRequestV1,
)
from seektalent.wtscli_verify_session_classification import (
    WtsCliCurrentProfileSnapshot,
)
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_runtime_control.errors import (
    RuntimeControlError,
    RuntimeControlLookupError,
)
from seektalent_runtime_control.source_operations import (
    AcceptedSourceOperation,
)
from seektalent_runtime_control.workflow_transition import (
    WorkflowTransitionWriteResult,
)
from seektalent_runtime_control.browser_lane import BrowserLaneGuard
from seektalent.wtscli_lifecycle_supervisor import WtsCliLifecycleSupervisor


_LOGGER = logging.getLogger(__name__)
_SAFE_SIDECAR_REASON = re.compile(r"^[a-z][a-z0-9_]{0,159}$")


@dataclass(frozen=True, slots=True)
class _SidecarExitDiagnostic:
    boundary: str
    operation_kind: str
    safe_reason_code: str


@dataclass(slots=True)
class _SidecarProcess:
    process: subprocess.Popen[bytes]
    transport: _ProtocolTransport
    cards_session: PostHandshakeLiepinSourceSession | None
    history_session: PostHandshakeHistorySession | None
    diagnostic_path: Path
    _exit_diagnostic: _SidecarExitDiagnostic | None = None
    _exit_diagnostic_read: bool = False
    _close_lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
    )
    _closed: bool = False

    def exit_diagnostic(self) -> _SidecarExitDiagnostic | None:
        if self._exit_diagnostic_read:
            return self._exit_diagnostic
        try:
            with self.diagnostic_path.open("rb") as stream:
                raw = stream.read(4097)
        except OSError:
            return None
        self._exit_diagnostic_read = True
        self.diagnostic_path.unlink(missing_ok=True)
        if len(raw) > 4096:
            return None
        self._exit_diagnostic = _parse_sidecar_exit_diagnostic(raw)
        return self._exit_diagnostic

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self.transport.close()
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.diagnostic_path.unlink(missing_ok=True)


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


@dataclass(frozen=True, slots=True)
class _LaneRecoveryContext:
    source_plan_id: str
    round_no: int
    query_terms: tuple[str, ...]
    keyword_query: str
    query_fingerprint: str
    query_role: Literal["exploit", "explore"]
    requested_count: int
    max_pages: int
    max_cards: int
    claim_aware: bool


def _browser_effect_deadline(
    settings: AppSettings,
) -> RelativeMonotonicDeadlineV1:
    return RelativeMonotonicDeadlineV1(
        value=min(
            900_000,
            max(1, int(settings.liepin_opencli_timeout_seconds * 1000)),
        ),
        clock="relative_monotonic",
        unit="milliseconds",
    )


def _detail_request_from_work_plan(
    plan: LiepinDetailWorkPlanV1,
    cursor: int,
) -> LiepinDetailsOperationRequestV1:
    try:
        item = plan.items[cursor]
    except IndexError:
        raise RuntimeControlError(
            "runtime_detail_work_plan_cursor_invalid"
        ) from None
    return LiepinDetailsOperationRequestV1(
        contract_version="seektalent.source.liepin-details.request/v1",
        runtime_run_id=plan.runtime_run_id,
        source_lane_run_id=plan.source_lane_run_id,
        query_instance_id=plan.query_instance_id,
        card_ref=item.card_ref,
        rank=item.rank,
        open_mode=(
            "resolve_locator"
            if plan.phase == "locators"
            else "cached_locator"
        ),
        provider_candidate_key_hash=(
            item.provider_candidate_key_hash
            if plan.phase == "captures"
            else None
        ),
        expected_provider_candidate_key_hash=(
            item.provider_candidate_key_hash
            if plan.phase == "captures" and plan.claim_aware
            else None
        ),
    )


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
        wtscli_lifecycle_supervisor: WtsCliLifecycleSupervisor | None = None,
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
        self._wtscli_lifecycle_supervisor = wtscli_lifecycle_supervisor
        root = settings.runtime_control_path.parent / "source-port"
        self._journal_path = root / "liepin-cards-journal.sqlite3"
        self._verify_session_journal_path = root / "liepin-verify-session-journal.sqlite3"
        self._artifact_root = root / "liepin-cards-results"
        self._details_artifact_root = root / "liepin-details-results"
        self._details_request_artifact_root = (
            root / "liepin-details-requests"
        )
        self._detail_work_plan_artifact_root = (
            root / "liepin-detail-work-plans"
        )
        self._round_work_plan_artifact_root = (
            root / "liepin-round-work-plans"
        )
        self._lane_queries: dict[str, str] = {}
        self._lane_recovery_contexts: dict[
            str,
            _LaneRecoveryContext,
        ] = {}
        self._pending_detail_work_plans: dict[
            str,
            tuple[LiepinDetailWorkPlanV1, str, str],
        ] = {}
        self._round_work_plan_bindings: dict[
            int,
            tuple[str, str],
        ] = {}
        self._pending_checkpoint_operation_ids: set[str] = set()
        self._step_resource_evidence: dict[str, float | int] = {
            "barrierBindAttemptCount": 0,
            "barrierCommittedWriteCount": 0,
            "barrierCommittedLaneCount": 0,
            "barrierCommittedLogicalPayloadBytes": 0,
            "barrierTransactionDurationMs": 0.0,
            "transitionWriteCount": 0,
            "transitionPayloadBytes": 0,
            "transitionTransactionDurationMs": 0.0,
            "requestArtifactWriteCount": 0,
            "requestArtifactBytes": 0,
            "requestArtifactWriteDurationMs": 0.0,
            "workPlanArtifactWriteCount": 0,
            "workPlanArtifactBytes": 0,
            "workPlanArtifactWriteDurationMs": 0.0,
            "roundPlanArtifactWriteCount": 0,
            "roundPlanArtifactPayloadBytes": 0,
            "roundPlanArtifactBytes": 0,
            "roundPlanArtifactWriteDurationMs": 0.0,
        }
        self._process: _SidecarProcess | None = None
        # The lock only protects framing on one subprocess pipe and close().
        # Durable admission, replay, and fencing remain store/journal owned.
        self._channel_lock = threading.Lock()

    def activate_requirement_revision(
        self,
        accepted_requirement_revision_id: str,
    ) -> None:
        """Bind future source operations to the activated run requirement."""
        self._accepted_requirement_revision_id = accepted_requirement_revision_id

    def bind_lane(
        self,
        source_lane_run_id: str,
        query_instance_id: str,
        *,
        source_plan_id: str | None = None,
        round_no: int | None = None,
        query_terms: tuple[str, ...] = (),
        keyword_query: str | None = None,
        query_fingerprint: str | None = None,
        query_role: Literal["exploit", "explore"] | None = None,
        requested_count: int | None = None,
        max_pages: int | None = None,
        max_cards: int | None = None,
        claim_aware: bool = False,
    ) -> None:
        existing = self._lane_queries.setdefault(
            source_lane_run_id,
            query_instance_id,
        )
        if existing != query_instance_id:
            raise RuntimeError("liepin_cards_lane_identity_conflict")
        if source_plan_id is None or round_no is None:
            return
        context = _LaneRecoveryContext(
            source_plan_id=source_plan_id,
            round_no=round_no,
            query_terms=query_terms,
            keyword_query=keyword_query or " ".join(query_terms),
            query_fingerprint=query_fingerprint or query_instance_id,
            query_role=query_role or "exploit",
            requested_count=max(1, requested_count or 1),
            max_pages=max(1, max_pages or 1),
            max_cards=max(1, max_cards or requested_count or 1),
            claim_aware=claim_aware,
        )
        previous = self._lane_recovery_contexts.setdefault(
            source_lane_run_id,
            context,
        )
        if previous != context:
            raise RuntimeError("liepin_cards_lane_context_conflict")

    def bind_round_work_plan(
        self,
        plan: LiepinRoundWorkPlanV1,
    ) -> None:
        if plan.runtime_run_id != self._runtime_run_id:
            raise RuntimeControlError(
                "runtime_workflow_round_plan_run_mismatch"
            )
        self._validate_round_work_plan_authority(plan)
        write = write_liepin_round_work_plan_artifact(
            self._round_work_plan_artifact_root,
            plan,
        )
        self._step_resource_evidence["roundPlanArtifactWriteCount"] += int(
            write.published
        )
        self._step_resource_evidence[
            "roundPlanArtifactPayloadBytes"
        ] += write.payload_size_bytes
        self._step_resource_evidence["roundPlanArtifactBytes"] += (
            write.payload_size_bytes if write.published else 0
        )
        self._step_resource_evidence[
            "roundPlanArtifactWriteDurationMs"
        ] += write.write_duration_ms
        binding = (write.artifact_ref, write.artifact_hash)
        existing = self._round_work_plan_bindings.setdefault(
            plan.round_no,
            binding,
        )
        if existing != binding:
            raise RuntimeControlError(
                "runtime_workflow_round_plan_conflict"
            )
        self.bind_round_work_barrier(
            round_no=plan.round_no,
            lanes=tuple(
                (
                    lane.source_lane_run_id,
                    lane.query_instance_id,
                )
                for lane in plan.lanes
            ),
            work_plan_artifact_ref=write.artifact_ref,
            work_plan_artifact_hash=write.artifact_hash,
        )

    def read_round_work_plan(
        self,
        *,
        artifact_ref: str,
        artifact_hash: str,
    ) -> LiepinRoundWorkPlanV1:
        return read_liepin_round_work_plan_artifact(
            self._round_work_plan_artifact_root,
            artifact_ref,
            expected_hash=artifact_hash,
        )

    def load_recovered_round_work_plan(
        self,
        *,
        artifact_ref: str,
        artifact_hash: str,
    ) -> LiepinRoundWorkPlanV1:
        plan = self.prepare_recovered_round_work_plan(
            artifact_ref=artifact_ref,
            artifact_hash=artifact_hash,
        )
        self.activate_recovered_round_work_plan(plan)
        return plan

    def prepare_recovered_round_work_plan(
        self,
        *,
        artifact_ref: str,
        artifact_hash: str,
    ) -> LiepinRoundWorkPlanV1:
        """Read and validate one recovery plan without mutating durable state."""
        plan = self.read_round_work_plan(
            artifact_ref=artifact_ref,
            artifact_hash=artifact_hash,
        )
        if plan.runtime_run_id != self._runtime_run_id:
            raise RuntimeControlError(
                "runtime_workflow_round_plan_run_mismatch"
            )
        self._validate_round_work_plan_authority(plan)
        return plan

    def activate_recovered_round_work_plan(
        self,
        plan: LiepinRoundWorkPlanV1,
    ) -> None:
        """Bind a previously validated plan immediately before execution."""
        self._validate_round_work_plan_authority(plan)
        payload = canonical_json_bytes(plan.model_dump(mode="json"))
        digest = sha256(payload).hexdigest()
        binding = (
            f"liepin-round-work-plan://sha256/{digest}",
            digest,
        )
        existing = self._round_work_plan_bindings.setdefault(
            plan.round_no,
            binding,
        )
        if existing != binding:
            raise RuntimeControlError(
                "runtime_workflow_round_plan_conflict"
            )

    def round_work_plan_authority(
        self,
        *,
        round_no: int,
    ) -> tuple[str, str]:
        checkpoint = self._current_round_base_checkpoint(round_no)
        return (
            checkpoint.checkpoint_id,
            self._accepted_requirement_revision_id,
        )

    def _validate_round_work_plan_authority(
        self,
        plan: LiepinRoundWorkPlanV1,
    ) -> None:
        checkpoint = self._current_round_base_checkpoint(plan.round_no)
        requirement_hash = sha256(
            canonical_json_bytes(plan.requirement_sheet)
        ).hexdigest()
        if (
            plan.base_checkpoint_id != checkpoint.checkpoint_id
            or plan.accepted_requirement_revision_id
            != self._accepted_requirement_revision_id
            or checkpoint.accepted_requirement_revision_id
            != plan.accepted_requirement_revision_id
            or plan.requirement_sheet_hash != requirement_hash
        ):
            raise RuntimeControlError(
                "runtime_workflow_round_plan_authority_invalid"
            )

    def _current_round_base_checkpoint(self, round_no: int):
        checkpoint = self._store.get_latest_checkpoint(
            runtime_run_id=self._runtime_run_id
        )
        run = self._store.get_run(self._runtime_run_id)
        if (
            checkpoint is None
            or run.latest_checkpoint_id != checkpoint.checkpoint_id
            or checkpoint.safe_boundary != "before_round_controller"
            or checkpoint.round_no != round_no
            or run.approved_requirement_revision_id
            != self._accepted_requirement_revision_id
        ):
            raise RuntimeControlError(
                "runtime_workflow_round_plan_authority_invalid"
            )
        return checkpoint

    def bind_round_work_barrier(
        self,
        *,
        round_no: int,
        lanes: tuple[tuple[str, str], ...],
        work_plan_artifact_ref: str,
        work_plan_artifact_hash: str,
    ) -> None:
        result = self._store.open_workflow_round_barrier(
            runtime_run_id=self._runtime_run_id,
            executor_id=self._executor_id,
            attempt_no=self._attempt_no,
            round_no=round_no,
            lanes=lanes,
            work_plan_artifact_ref=work_plan_artifact_ref,
            work_plan_artifact_hash=work_plan_artifact_hash,
            created_at=_now(),
        )
        self._step_resource_evidence["barrierBindAttemptCount"] += 1
        self._step_resource_evidence[
            "barrierTransactionDurationMs"
        ] += result.transaction_duration_ms
        if result.inserted:
            self._step_resource_evidence[
                "barrierCommittedWriteCount"
            ] += 1
            self._step_resource_evidence[
                "barrierCommittedLaneCount"
            ] += len(lanes)
            self._step_resource_evidence[
                "barrierCommittedLogicalPayloadBytes"
            ] += result.committed_logical_payload_bytes

    def complete_lane(
        self,
        *,
        source_lane_run_id: str,
        query_instance_id: str,
    ) -> None:
        active = self._store.get_active_workflow_transition(
            runtime_run_id=self._runtime_run_id,
            source_lane_run_id=source_lane_run_id,
            query_instance_id=query_instance_id,
        )
        if active is None:
            raise RuntimeControlError(
                "runtime_workflow_lane_transition_missing"
            )
        if active.step_kind == "lane_completed":
            return
        if active.step_kind not in {"source_dispatch", "detail_dispatch"}:
            raise RuntimeControlError(
                "runtime_workflow_lane_unsettled"
            )
        operation_id = active.continuation.get("operationId")
        if not isinstance(operation_id, str):
            raise RuntimeControlError(
                "runtime_workflow_lane_transition_invalid"
            )
        operation = self._store.get_source_operation(
            self._runtime_run_id,
            operation_id,
        )
        if (
            operation.operation_phase != "observed"
            or operation.conclusive_observation_ref is None
            or operation.main_commit_ref is not None
        ):
            raise RuntimeControlError(
                "runtime_workflow_lane_unsettled"
            )
        continuation: dict[str, object] = {
            "schemaVersion": "runtime-lane-completed-continuation/v1",
            "operationId": operation_id,
            "laneResultKind": "cards_only",
        }
        for key in (
            "cardsArtifactRef",
            "workPlanArtifactRef",
            "workPlanHash",
            "workPlanPhase",
        ):
            value = active.continuation.get(key)
            if value is not None:
                continuation[key] = value
        if "workPlanArtifactRef" in continuation:
            plan, _plan_ref, _plan_hash = self._work_plan_from_transition(
                active
            )
            continuation["laneResultKind"] = (
                "liepin_detail_work_plan"
            )
            continuation["detailCompletedHighWatermark"] = (
                len(plan.items) - 1
            )
        artifact_refs = set(active.artifact_refs)
        artifact_refs.add(operation.conclusive_observation_ref)
        result = self._store.write_workflow_transition(
            runtime_run_id=self._runtime_run_id,
            source_lane_run_id=source_lane_run_id,
            query_instance_id=query_instance_id,
            executor_id=self._executor_id,
            attempt_no=self._attempt_no,
            round_no=active.round_no,
            step_kind="lane_completed",
            continuation=continuation,
            artifact_refs=tuple(sorted(artifact_refs)),
            source_operation_ids=(operation_id,),
            created_at=_now(),
        )
        self._pending_checkpoint_operation_ids.discard(operation_id)
        self._record_transition_resource(result)

    def skip_lane(
        self,
        *,
        round_no: int,
        source_lane_run_id: str,
        query_instance_id: str,
    ) -> None:
        started = time.perf_counter()
        self._store.skip_workflow_barrier_lane(
            runtime_run_id=self._runtime_run_id,
            executor_id=self._executor_id,
            attempt_no=self._attempt_no,
            round_no=round_no,
            source_lane_run_id=source_lane_run_id,
            query_instance_id=query_instance_id,
            settled_at=_now(),
        )
        self._step_resource_evidence[
            "barrierTransactionDurationMs"
        ] += (time.perf_counter() - started) * 1000

    def bind_detail_work_plan(
        self,
        *,
        source_lane_run_id: str,
        phase: Literal["locators", "captures"],
        items: tuple[tuple[int, str, str | None], ...],
        target_resumes: int,
        claim_aware: bool,
    ) -> None:
        if phase not in {"locators", "captures"}:
            raise RuntimeControlError(
                "runtime_detail_work_plan_phase_invalid"
            )
        context = self._lane_recovery_contexts.get(
            source_lane_run_id
        )
        if context is None:
            raise RuntimeControlError(
                "runtime_detail_work_plan_context_missing"
            )
        query_instance_id = self._lane_queries[source_lane_run_id]
        cards_ref, cards_hash = self._cards_artifact_binding(
            source_lane_run_id=source_lane_run_id,
            query_instance_id=query_instance_id,
        )
        plan = LiepinDetailWorkPlanV1(
            contract_version=(
                "seektalent.source.liepin-detail-work-plan/v1"
            ),
            runtime_run_id=self._runtime_run_id,
            source_plan_id=context.source_plan_id,
            source_lane_run_id=source_lane_run_id,
            round_no=context.round_no,
            query_instance_id=query_instance_id,
            query_fingerprint=context.query_fingerprint,
            query_role=context.query_role,
            query_terms=context.query_terms,
            keyword_query=context.keyword_query,
            requested_count=max(1, target_resumes),
            max_pages=context.max_pages,
            max_cards=context.max_cards,
            phase=phase,
            claim_aware=claim_aware,
            cards_artifact_ref=cards_ref,
            cards_artifact_hash=cards_hash,
            items=tuple(
                LiepinDetailWorkItemV1(
                    rank=rank,
                    card_ref=card_ref,
                    provider_candidate_key_hash=(
                        provider_hash
                        if phase == "captures"
                        else None
                    ),
                )
                for rank, card_ref, provider_hash in items
            ),
        )
        write = write_liepin_detail_work_plan_artifact(
            self._detail_work_plan_artifact_root,
            plan,
        )
        if write.published:
            self._step_resource_evidence[
                "workPlanArtifactWriteCount"
            ] += 1
            self._step_resource_evidence[
                "workPlanArtifactBytes"
            ] += write.payload_size_bytes
        self._step_resource_evidence[
            "workPlanArtifactWriteDurationMs"
        ] += write.write_duration_ms
        if phase == "captures":
            self._pending_detail_work_plans[source_lane_run_id] = (
                plan,
                write.artifact_ref,
                write.artifact_hash,
            )
        elif plan.items:
            self._queue_detail_work_item(
                plan=plan,
                plan_artifact_ref=write.artifact_ref,
                plan_artifact_hash=write.artifact_hash,
                cursor=0,
            )

    def prepare_readiness(self) -> None:
        """Run readiness repair under the same durable Source authority."""
        from seektalent.liepin_verify_session_gate import (
            _prepare_session_mutating,
        )

        digest = sha256(
            (
                f"{self._runtime_run_id}:prepare-readiness:"
                f"{self._attempt_no}"
            ).encode()
        ).hexdigest()
        operation_id = f"prepare-{digest[:48]}"
        dispatch_intent_id = f"dispatch-{digest[:48]}"
        browser_control_scope_id = f"browser-scope-{digest[:48]}"
        profile_binding_ref = f"profile-binding-{digest[:48]}"
        provider_account_ref = f"provider-account-{digest[:48]}"
        raw_runtime_fence = (
            "prepare-readiness-fence-"
            + sha256(
                (
                    f"{self._runtime_run_id}:{self._executor_id}:"
                    f"{self._attempt_no}:"
                    f"{self._runtime_attempt_authority_ref}"
                ).encode()
            ).hexdigest()
        )
        request = VerifySessionRequestV1.create(
            run_id=self._runtime_run_id,
            operation_id=operation_id,
            attempt_no=self._attempt_no,
            idempotency_key=f"prepare-{digest[:48]}",
            correlation_id=f"prepare-correlation-{digest[:40]}",
            accepted_requirement_revision_id=(
                self._accepted_requirement_revision_id
            ),
            runtime_attempt_fence_token=raw_runtime_fence,
            profile_binding_generation=self._profile_binding_generation,
            browser_control_scope_id=browser_control_scope_id,
            deadline_value=min(
                900_000,
                max(
                    1,
                    int(
                        self._settings
                        .liepin_opencli_timeout_seconds
                        * 1000
                    ),
                ),
            ),
            expected_source_operation_ledger_revision=1,
            expected_reconciliation_revision=0,
            delivery_mode="initial",
            dispatch_intent_id=dispatch_intent_id,
            dispatch_intent_revision=1,
            source_operation_acceptance_ref=(
                f"source-acceptance://{operation_id}"
            ),
            profile_binding_ref=profile_binding_ref,
            provider_account_ref=provider_account_ref,
            required_capabilities=(
                "account",
                "bridge",
                "extension",
                "process",
                "profile_lock",
                "risk_state",
                "search_surface",
            ),
            user_interaction_policy="headed_user_action_allowed",
            verify_search_surface=True,
        )
        request_hash = request.identity.request_hash
        try:
            current = self._store.get_source_operation(
                self._runtime_run_id,
                operation_id,
            )
        except RuntimeControlLookupError:
            current = None
        if current is not None:
            if current.operation_phase == "main_committed":
                self._ensure_supervisor_ready_for_recovery()
                return
            if (
                current.operation_phase == "observed"
                and current.conclusive_observation_ref is not None
            ):
                self._ensure_supervisor_ready_for_recovery()
                self._pending_checkpoint_operation_ids.add(operation_id)
                return
            raise RuntimeControlError(
                "liepin_prepare_readiness_reconcile_first"
            )
        dispatch_intent_ref = f"source-dispatch://{operation_id}/1"
        dispatch_digest = (
            request.delivery.authorization.dispatch_intent_digest
        )
        accepted = self._store.accept_source_operation(
            runtime_run_id=self._runtime_run_id,
            operation_id=operation_id,
            source_id="liepin",
            operation_kind="verify_session",
            canonical_request_hash=request_hash,
            idempotency_key=f"prepare-{digest[:48]}",
            accepted_requirement_revision_id=(
                self._accepted_requirement_revision_id
            ),
            runtime_attempt_no=self._attempt_no,
            runtime_attempt_authority_ref=(
                self._runtime_attempt_authority_ref
            ),
            runtime_attempt_fence_ref=(
                request.identity.runtime_attempt_fence_ref
            ),
            profile_binding_generation=self._profile_binding_generation,
            browser_control_scope_id=browser_control_scope_id,
            controller_fence_ref=None,
            outbox_id=f"outbox-{digest[:48]}",
            dispatch_intent_id=dispatch_intent_id,
            dispatch_intent_revision=1,
            dispatch_intent_digest=dispatch_digest,
            dispatch_authorization_ordinal=1,
            source_operation_acceptance_ref=(
                f"source-acceptance://{operation_id}"
            ),
            expected_ledger_revision=1,
            expected_reconciliation_revision=0,
        )
        guard = BrowserLaneGuard(
            store=self._store,
            runtime_run_id=self._runtime_run_id,
            operation_id=operation_id,
            operation_kind="prepare_readiness",
            now=_now,
            plus_seconds=_plus_seconds,
            wait_timeout_seconds=(
                self._settings
                .liepin_browser_lane_admission_timeout_seconds
            ),
            on_lease_lost=self._fence_active_sidecar,
        )
        with guard:
            accepted_generation, accepted_journal_revision, ack_ref = (
                self._accept_verify_session_receipt(
                    operation_id=operation_id,
                    request_hash=request_hash,
                    dispatch_intent_id=dispatch_intent_id,
                )
            )
            self._store.record_source_dispatch_ack(
                runtime_run_id=self._runtime_run_id,
                operation_id=operation_id,
                outbox_id=accepted.dispatch.outbox_id,
                canonical_request_hash=request_hash,
                dispatch_intent_id=dispatch_intent_id,
                dispatch_intent_revision=1,
                dispatch_intent_digest=dispatch_digest,
                dispatch_authorization_ordinal=1,
                expected_outbox_revision=1,
                accepted_sidecar_generation=accepted_generation,
                accepted_sidecar_journal_revision=accepted_journal_revision,
                ack_ref=ack_ref,
                ack_kind="new_logical_operation",
                acknowledged_at=_now(),
            )
            effect_started = False
            effect_completed = False

            def mark_effect_started() -> None:
                nonlocal effect_started
                effect_started = True

            def mark_effect_completed() -> None:
                nonlocal effect_completed
                effect_completed = True

            try:
                _prepare_session_mutating(
                    self._settings,
                    request=request,
                    lifecycle_supervisor=self._wtscli_lifecycle_supervisor,
                    on_effect_started=mark_effect_started,
                    on_effect_completed=mark_effect_completed,
                    current_profile_snapshot=(
                        WtsCliCurrentProfileSnapshot(
                            runtime_attempt_fence_ref=(
                                request.identity
                                .runtime_attempt_fence_ref
                            ),
                            profile_binding_ref=profile_binding_ref,
                            profile_binding_generation=(
                                self._profile_binding_generation
                            ),
                            provider_account_ref=(
                                provider_account_ref
                            ),
                            provider_account_subject=(
                                "liepin-opencli-local-browser-profile"
                            ),
                            browser_control_scope_id=(
                                browser_control_scope_id
                            ),
                        )
                    ),
                )
            except Exception:
                if not effect_started or effect_completed:
                    self._store.record_owned_source_operation_observation(
                        runtime_run_id=self._runtime_run_id,
                        operation_id=operation_id,
                        executor_id=self._executor_id,
                        attempt_no=self._attempt_no,
                        expected_ledger_revision=1,
                        dispatch_intent_ref=dispatch_intent_ref,
                        conclusive_observation_ref=(
                            f"source-observation://{operation_id}/failed"
                        ),
                        source_operation_disposition="failed",
                        observed_at=_now(),
                    )
                    self._pending_checkpoint_operation_ids.add(operation_id)
                    raise
                history_digest = sha256(
                    f"{operation_id}:history-unavailable".encode()
                ).hexdigest()
                self._store.record_owned_source_reconciliation_unknown(
                    runtime_run_id=self._runtime_run_id,
                    operation_id=operation_id,
                    executor_id=self._executor_id,
                    attempt_no=self._attempt_no,
                    expected_ledger_revision=1,
                    expected_reconciliation_revision=0,
                    history_result_ref=f"sha256:{history_digest}",
                    history_result_digest=history_digest,
                    history_outcome="history_unavailable",
                    history_conclusion=None,
                    dispatch_intent_ref=dispatch_intent_ref,
                    committed_at=_now(),
                )
                guard.preserve_unresolved(
                    "liepin_prepare_reconciliation_unknown"
                )
                raise
            self._store.record_owned_source_operation_observation(
                runtime_run_id=self._runtime_run_id,
                operation_id=operation_id,
                executor_id=self._executor_id,
                attempt_no=self._attempt_no,
                expected_ledger_revision=1,
                dispatch_intent_ref=dispatch_intent_ref,
                conclusive_observation_ref=(
                    f"source-observation://{operation_id}/completed"
                ),
                source_operation_disposition="completed",
                observed_at=_now(),
            )
            self._pending_checkpoint_operation_ids.add(operation_id)

    def _accept_verify_session_receipt(
        self,
        *,
        operation_id: str,
        request_hash: str,
        dispatch_intent_id: str,
    ) -> tuple[int, int, str]:
        """Persist the verify-session acceptance before any browser effect."""
        self._verify_session_journal_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._verify_session_journal_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS verify_session_receipts (
                    generation INTEGER PRIMARY KEY,
                    journal_revision INTEGER NOT NULL,
                    operation_id TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    dispatch_intent_id TEXT NOT NULL,
                    accepted_at TEXT NOT NULL
                )
                """
            )
            existing = connection.execute(
                """
                SELECT generation, journal_revision
                FROM verify_session_receipts
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            if existing is not None:
                generation, revision = (int(existing[0]), int(existing[1]))
            else:
                generation = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(generation), 0) + 1 FROM verify_session_receipts"
                    ).fetchone()[0]
                )
                revision = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(journal_revision), 0) + 1 FROM verify_session_receipts"
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    INSERT INTO verify_session_receipts
                    (generation, journal_revision, operation_id, request_hash,
                     dispatch_intent_id, accepted_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        generation,
                        revision,
                        operation_id,
                        request_hash,
                        dispatch_intent_id,
                        _now(),
                    ),
                )
        receipt = f"verify-session-ack://{operation_id}/{generation}/{revision}"
        return generation, revision, receipt

    def _ensure_supervisor_ready_for_recovery(self) -> None:
        supervisor = self._wtscli_lifecycle_supervisor
        if supervisor is None:
            return
        supervisor.ensure_ready(
            timeout_seconds=min(
                40.0,
                max(1.0, self._settings.liepin_opencli_timeout_seconds),
            )
        )

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

    def step_resource_evidence(self) -> dict[str, float | int]:
        return dict(self._step_resource_evidence)

    def resume_detail_dispatch_transition(
        self,
        transition_payload: object,
    ) -> tuple[dict[str, object], dict[str, object]]:
        if not isinstance(transition_payload, dict):
            raise RuntimeControlError(
                "runtime_detail_dispatch_transition_mismatch"
            )
        transition_payload = cast(dict[str, object], transition_payload)
        source_lane_run_id = transition_payload.get(
            "sourceLaneRunId"
        )
        query_instance_id = transition_payload.get("queryInstanceId")
        if (
            not isinstance(source_lane_run_id, str)
            or not isinstance(query_instance_id, str)
        ):
            raise RuntimeControlError(
                "runtime_detail_dispatch_transition_mismatch"
            )
        active = self._store.get_active_workflow_transition(
            runtime_run_id=self._runtime_run_id,
            source_lane_run_id=source_lane_run_id,
            query_instance_id=query_instance_id,
        )
        if active is None or active.step_kind not in {
            "detail_queued",
            "detail_dispatch",
        }:
            raise RuntimeControlError(
                "runtime_detail_dispatch_transition_missing"
            )
        if (
            transition_payload != active.resume_payload()
        ):
            raise RuntimeControlError(
                "runtime_detail_dispatch_transition_mismatch"
            )
        continuation = active.continuation
        common_keys = {
            "schemaVersion",
            "operationId",
            "requestHash",
            "requestArtifactRef",
            "workPlanArtifactRef",
            "workPlanHash",
            "workPlanPhase",
            "detailCursor",
            "detailCompletedHighWatermark",
            "cardsArtifactRef",
        }
        epoch_keys = {
            "dispatchAuthorizationOrdinal",
            "dispatchIntentId",
            "dispatchIntentDigest",
            "runtimeAttemptNo",
            "runtimeAttemptFenceRef",
            "browserControlScopeId",
        }
        expected_keys = (
            common_keys
            if active.step_kind == "detail_queued"
            else common_keys | epoch_keys
        )
        if set(continuation) != expected_keys:
            raise RuntimeControlError(
                "runtime_detail_dispatch_continuation_invalid"
            )
        operation_id = continuation["operationId"]
        request_hash = continuation["requestHash"]
        request_artifact_ref = continuation["requestArtifactRef"]
        work_plan_ref = continuation["workPlanArtifactRef"]
        work_plan_hash = continuation["workPlanHash"]
        detail_cursor = continuation["detailCursor"]
        detail_high_watermark = continuation[
            "detailCompletedHighWatermark"
        ]
        if (
            not isinstance(operation_id, str)
            or not isinstance(request_hash, str)
            or len(request_hash) != 64
            or not isinstance(request_artifact_ref, str)
            or request_artifact_ref not in active.artifact_refs
            or not isinstance(work_plan_ref, str)
            or work_plan_ref not in active.artifact_refs
            or not isinstance(work_plan_hash, str)
            or len(work_plan_hash) != 64
            or isinstance(detail_cursor, bool)
            or not isinstance(detail_cursor, int)
            or detail_cursor < 0
            or isinstance(detail_high_watermark, bool)
            or not isinstance(detail_high_watermark, int)
            or detail_high_watermark != detail_cursor - 1
        ):
            raise RuntimeControlError(
                "runtime_detail_dispatch_continuation_invalid"
            )
        if active.step_kind == "detail_queued":
            if (
                continuation["schemaVersion"]
                != "runtime-detail-queued-continuation/v1"
            ):
                raise RuntimeControlError(
                    "runtime_detail_dispatch_continuation_invalid"
                )
        else:
            dispatch_ordinal = continuation[
                "dispatchAuthorizationOrdinal"
            ]
            dispatch_intent_id = continuation["dispatchIntentId"]
            dispatch_intent_digest = continuation[
                "dispatchIntentDigest"
            ]
            runtime_attempt_no = continuation["runtimeAttemptNo"]
            runtime_attempt_fence_ref = continuation[
                "runtimeAttemptFenceRef"
            ]
            browser_control_scope_id = continuation[
                "browserControlScopeId"
            ]
            if (
                continuation["schemaVersion"]
                != "runtime-detail-dispatch-continuation/v1"
                or isinstance(dispatch_ordinal, bool)
                or not isinstance(dispatch_ordinal, int)
                or dispatch_ordinal < 1
                or not isinstance(dispatch_intent_id, str)
                or not dispatch_intent_id
                or not isinstance(dispatch_intent_digest, str)
                or len(dispatch_intent_digest) != 64
                or isinstance(runtime_attempt_no, bool)
                or not isinstance(runtime_attempt_no, int)
                or runtime_attempt_no < 1
                or not isinstance(runtime_attempt_fence_ref, str)
                or not runtime_attempt_fence_ref
                or not isinstance(browser_control_scope_id, str)
                or not browser_control_scope_id
            ):
                raise RuntimeControlError(
                    "runtime_detail_dispatch_continuation_invalid"
                )
        request = read_liepin_details_request_artifact(
            self._details_request_artifact_root,
            request_artifact_ref,
            expected_hash=request_artifact_ref.rsplit("/", 1)[-1],
        )
        plan, _plan_ref, _plan_hash = self._work_plan_from_transition(
            active
        )
        if (
            request.runtime_run_id != self._runtime_run_id
            or stable_liepin_details_operation_id(request) != operation_id
            or canonical_liepin_details_request_hash(request)
            != request_hash
            or request != _detail_request_from_work_plan(
                plan,
                detail_cursor,
            )
        ):
            raise RuntimeControlError(
                "runtime_detail_dispatch_request_mismatch"
            )
        try:
            accepted = self._store.get_accepted_source_operation_context(
                self._runtime_run_id,
                operation_id,
            )
        except RuntimeControlLookupError:
            accepted = None
        if (
            accepted is not None
            and accepted.operation.canonical_request_hash != request_hash
        ):
            raise RuntimeControlError(
                "runtime_detail_dispatch_request_mismatch"
            )
        if active.step_kind == "detail_queued":
            if accepted is not None:
                raise RuntimeControlError(
                    "runtime_detail_dispatch_epoch_mismatch"
                )
        elif accepted is None:
            raise RuntimeControlError(
                "runtime_detail_dispatch_epoch_mismatch"
            )
        elif (
            (
                accepted.dispatch.dispatch_authorization_ordinal
                != dispatch_ordinal
                or accepted.dispatch.dispatch_intent_id
                != dispatch_intent_id
                or accepted.dispatch.dispatch_intent_digest
                != dispatch_intent_digest
                or accepted.expectation.runtime_attempt_no
                != runtime_attempt_no
                or accepted.expectation.runtime_attempt_fence_ref
                != runtime_attempt_fence_ref
                or accepted.expectation.browser_control_scope_id
                != browser_control_scope_id
            )
            and accepted.operation.retry_posture != "safe_retry"
        ):
            raise RuntimeControlError(
                "runtime_detail_dispatch_epoch_mismatch"
            )
        return self._execute_details(request)

    def resume_detail_workflow_transition(
        self,
        transition_payload: object,
        *,
        detail_open_claim_ledger,
    ):
        """Finish the active detail queue before publishing one lane delta."""
        if not isinstance(transition_payload, dict):
            raise RuntimeControlError(
                "runtime_detail_dispatch_transition_mismatch"
            )
        transition_payload = cast(dict[str, object], transition_payload)
        source_lane_run_id = transition_payload.get(
            "sourceLaneRunId"
        )
        query_instance_id = transition_payload.get("queryInstanceId")
        if (
            not isinstance(source_lane_run_id, str)
            or not isinstance(query_instance_id, str)
        ):
            raise RuntimeControlError(
                "runtime_detail_dispatch_transition_mismatch"
            )
        active = self._store.get_active_workflow_transition(
            runtime_run_id=self._runtime_run_id,
            source_lane_run_id=source_lane_run_id,
            query_instance_id=query_instance_id,
        )
        if active is None:
            raise RuntimeControlError(
                "runtime_detail_dispatch_transition_missing"
            )
        plan, _plan_ref, _plan_hash = self._work_plan_from_transition(active)
        cursor = active.continuation.get("detailCursor")
        if isinstance(cursor, bool) or not isinstance(cursor, int):
            raise RuntimeControlError(
                "runtime_detail_work_plan_cursor_invalid"
            )
        if plan.phase == "captures":
            self._prepare_claim_for_recovered_capture(
                plan,
                cursor,
                detail_open_claim_ledger,
            )
        envelope, structured = self.resume_detail_dispatch_transition(
            transition_payload
        )
        if plan.phase == "captures":
            self._settle_recovered_detail_claim(
                plan,
                cursor,
                envelope,
                structured,
                detail_open_claim_ledger,
            )
        self._require_detail_result_conclusive(envelope, structured)

        if plan.phase == "locators":
            for next_cursor in range(cursor + 1, len(plan.items)):
                request = _detail_request_from_work_plan(plan, next_cursor)
                envelope, structured = self._execute_details(request)
                self._require_detail_result_conclusive(
                    envelope,
                    structured,
                )
            plan = self._capture_plan_from_completed_locators(plan)
            if plan.items:
                write = write_liepin_detail_work_plan_artifact(
                    self._detail_work_plan_artifact_root,
                    plan,
                )
                self._record_work_plan_artifact_resource(write)
                self._queue_detail_work_item(
                    plan=plan,
                    plan_artifact_ref=write.artifact_ref,
                    plan_artifact_hash=write.artifact_hash,
                    cursor=0,
                )
                cursor = -1

        opened = len(self._completed_detail_results(plan))
        for next_cursor in range(cursor + 1, len(plan.items)):
            if opened >= plan.requested_count:
                break
            item = plan.items[next_cursor]
            provider_hash = item.provider_candidate_key_hash
            if provider_hash is None:
                raise RuntimeControlError(
                    "runtime_detail_work_plan_capture_hash_missing"
                )
            if plan.claim_aware and not self._claim_capture_item(
                detail_open_claim_ledger,
                provider_hash,
            ):
                continue
            request = _detail_request_from_work_plan(plan, next_cursor)
            envelope, structured = self._execute_details(request)
            if plan.claim_aware:
                self._settle_recovered_detail_claim(
                    plan,
                    next_cursor,
                    envelope,
                    structured,
                    detail_open_claim_ledger,
                )
            self._require_detail_result_conclusive(
                envelope,
                structured,
            )
            if structured.get("ingest_ready") is True:
                opened += 1

        from seektalent.sources.liepin.runtime_lane import (
            build_resumed_liepin_detail_lane_result,
        )

        result = build_resumed_liepin_detail_lane_result(
            plan=plan,
            structured_results=self._completed_detail_results(plan),
        )
        self.complete_lane(
            source_lane_run_id=source_lane_run_id,
            query_instance_id=query_instance_id,
        )
        return result

    def resume_completed_detail_workflow_transition(
        self,
        transition_payload: object,
    ):
        if not isinstance(transition_payload, dict):
            raise RuntimeControlError(
                "runtime_workflow_lane_transition_invalid"
            )
        transition_payload = cast(dict[str, object], transition_payload)
        source_lane_run_id = transition_payload.get("sourceLaneRunId")
        query_instance_id = transition_payload.get("queryInstanceId")
        transition_id = transition_payload.get("transitionId")
        if (
            not isinstance(source_lane_run_id, str)
            or not isinstance(query_instance_id, str)
            or not isinstance(transition_id, str)
        ):
            raise RuntimeControlError(
                "runtime_workflow_lane_transition_invalid"
            )
        active = self._store.get_active_workflow_transition(
            runtime_run_id=self._runtime_run_id,
            source_lane_run_id=source_lane_run_id,
            query_instance_id=query_instance_id,
        )
        if (
            active is None
            or active.transition_id != transition_id
            or active.step_kind != "lane_completed"
            or active.continuation.get("laneResultKind")
            != "liepin_detail_work_plan"
        ):
            raise RuntimeControlError(
                "runtime_workflow_lane_transition_invalid"
            )
        plan, _plan_ref, _plan_hash = self._work_plan_from_transition(
            active
        )
        from seektalent.sources.liepin.runtime_lane import (
            build_resumed_liepin_detail_lane_result,
        )

        return build_resumed_liepin_detail_lane_result(
            plan=plan,
            structured_results=self._completed_detail_results(plan),
        )

    def resume_completed_cards_workflow_transition(
        self,
        transition_payload: object,
        *,
        expected_request: LiepinCardsOperationRequestV1,
    ) -> LiepinCardsArtifactV1:
        """Restore a completed card lane strictly from durable observation."""
        if not isinstance(transition_payload, dict):
            raise RuntimeControlError(
                "runtime_workflow_lane_transition_invalid"
            )
        transition_payload = cast(dict[str, object], transition_payload)
        source_lane_run_id = transition_payload.get("sourceLaneRunId")
        query_instance_id = transition_payload.get("queryInstanceId")
        transition_id = transition_payload.get("transitionId")
        if (
            not isinstance(source_lane_run_id, str)
            or not isinstance(query_instance_id, str)
            or not isinstance(transition_id, str)
            or expected_request.runtime_run_id != self._runtime_run_id
            or expected_request.source_lane_run_id
            != source_lane_run_id
            or expected_request.query_instance_id != query_instance_id
        ):
            raise RuntimeControlError(
                "runtime_workflow_lane_transition_invalid"
            )
        active = self._store.get_active_workflow_transition(
            runtime_run_id=self._runtime_run_id,
            source_lane_run_id=source_lane_run_id,
            query_instance_id=query_instance_id,
        )
        if (
            active is None
            or active.resume_payload() != transition_payload
            or active.transition_id != transition_id
            or active.step_kind != "lane_completed"
            or active.continuation.get("laneResultKind") != "cards_only"
        ):
            raise RuntimeControlError(
                "runtime_workflow_lane_transition_invalid"
            )
        operation_id = stable_liepin_cards_operation_id(expected_request)
        request_hash = canonical_liepin_cards_request_hash(
            expected_request
        )
        if active.continuation.get("operationId") != operation_id:
            raise RuntimeControlError(
                "runtime_workflow_lane_transition_invalid"
            )
        operation = self._store.get_source_operation(
            self._runtime_run_id,
            operation_id,
        )
        artifact_ref = operation.conclusive_observation_ref
        if (
            operation.operation_kind != "cards"
            or operation.operation_phase != "main_committed"
            or operation.canonical_request_hash != request_hash
            or operation.accepted_requirement_revision_id
            != self._accepted_requirement_revision_id
            or artifact_ref is None
            or artifact_ref not in active.artifact_refs
            or (
                active.continuation.get("cardsArtifactRef")
                not in {None, artifact_ref}
            )
        ):
            raise RuntimeControlError(
                "runtime_workflow_lane_transition_invalid"
            )
        digest = artifact_ref.rsplit("/", 1)[-1]
        try:
            artifact = read_liepin_cards_artifact(
                self._artifact_root,
                artifact_ref,
                expected_hash=digest,
            )
        except (OSError, ValueError):
            raise RuntimeControlError(
                "runtime_workflow_cards_artifact_invalid"
            ) from None
        if (
            artifact.operation_id != operation_id
            or artifact.canonical_request_hash != request_hash
        ):
            raise RuntimeControlError(
                "runtime_workflow_cards_artifact_invalid"
            )
        return artifact

    def close(self) -> None:
        with self._channel_lock:
            process, self._process = self._process, None
            if process is not None:
                process.close()

    def _fence_active_sidecar(self) -> None:
        process = self._process
        if process is not None:
            process.close()

    def _execute(
        self,
        request: LiepinCardsOperationRequestV1,
    ) -> tuple[dict[str, object], dict[str, object]]:
        operation_id = stable_liepin_cards_operation_id(request)
        replayed = self._replay_committed_cards(request, operation_id)
        if replayed is not None:
            return replayed
        self._ensure_source_dispatch_transition(request)
        if self._operation_is_reconciliation_unknown(operation_id):
            result = self._execute_with_lane(request)
            if not _source_result_is_reconciliation_unknown(result):
                self._store.resolve_browser_lane_from_conclusive_observation(
                    runtime_run_id=self._runtime_run_id,
                    operation_id=operation_id,
                    resolved_at=_now(),
                )
            return result
        guard = BrowserLaneGuard(
            store=self._store,
            runtime_run_id=self._runtime_run_id,
            operation_id=operation_id,
            operation_kind="cards",
            now=_now,
            plus_seconds=_plus_seconds,
            wait_timeout_seconds=(
                self._settings.liepin_browser_lane_admission_timeout_seconds
            ),
            on_lease_lost=self._fence_active_sidecar,
        )
        with guard:
            try:
                result = self._execute_with_lane(request)
                if _source_result_is_reconciliation_unknown(result):
                    guard.preserve_unresolved(
                        "liepin_cards_reconciliation_unknown"
                    )
                return result
            finally:
                self.close()

    def _execute_with_lane(
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
        safe_retry_redispatch = False
        if (
            existing is not None
            and existing.operation.retry_posture == "safe_retry"
        ):
            suffix = operation_id.removeprefix("cards_")
            fence_ref = sha256(
                (
                    f"{self._runtime_run_id}:{self._executor_id}:"
                    f"{self._attempt_no}:"
                    f"{self._runtime_attempt_authority_ref}:"
                    f"cards-safe-retry:{suffix}"
                ).encode()
            ).hexdigest()
            scope_digest = sha256(
                f"{fence_ref}:cards-scope".encode()
            ).hexdigest()[:16]
            existing = self._store.mint_current_safe_retry_dispatch_epoch(
                runtime_run_id=self._runtime_run_id,
                operation_id=operation_id,
                executor_id=self._executor_id,
                attempt_no=self._attempt_no,
                observed_at=_now(),
                runtime_attempt_authority_ref=(
                    self._runtime_attempt_authority_ref
                ),
                runtime_attempt_fence_ref=fence_ref,
                profile_binding_generation=(
                    self._profile_binding_generation
                ),
                browser_control_scope_id=(
                    f"cards-retry-scope-{suffix[:16]}-{scope_digest}"
                ),
                controller_fence_ref=None,
            )
            _inject_source_step_fault(
                "after_cards_safe_retry_mint_before_exchange"
            )
            safe_retry_redispatch = True
        elif (
            existing is not None
            and existing.operation.retry_posture == "no_retry"
            and existing.dispatch.status == "pending"
        ):
            self._require_active_source_dispatch_transition(
                operation_id=operation_id,
                request_hash=request_hash,
                source_lane_run_id=request.source_lane_run_id,
                query_instance_id=request.query_instance_id,
                accepted=existing,
            )
            safe_retry_redispatch = True
        identity = self._identity(
            request,
            operation_id=operation_id,
            request_hash=request_hash,
            existing=existing,
        )
        if existing is None:
            try:
                self._ready_source_process()
            except (OSError, RuntimeError, SidecarReadinessError):
                self._report_sidecar_exit()
                return _cards_readiness_unavailable_result()
            authorization = DispatchAuthorizationV1.create_initial(
                identity=identity,
                dispatch_intent_id=f"dispatch-{operation_id}",
                dispatch_intent_revision=1,
                source_operation_acceptance_ref=(
                    f"source-acceptance://{operation_id}/1"
                ),
            )
            accept_started = time.perf_counter()
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
                advance_source_transition=True,
                transition_created_at=_now(),
            )
            active_dispatch = self._store.get_active_workflow_transition(
                runtime_run_id=self._runtime_run_id,
                source_lane_run_id=request.source_lane_run_id,
                query_instance_id=request.query_instance_id,
            )
            if (
                active_dispatch is None
                or active_dispatch.step_kind != "source_dispatch"
            ):
                raise RuntimeControlError(
                    "runtime_source_dispatch_transition_missing"
                )
            self._step_resource_evidence[
                "transitionWriteCount"
            ] += 1
            self._step_resource_evidence[
                "transitionPayloadBytes"
            ] += active_dispatch.payload_size_bytes
            self._step_resource_evidence[
                "transitionTransactionDurationMs"
            ] += (time.perf_counter() - accept_started) * 1000
            _inject_source_step_fault(
                "after_initial_cards_accept_before_exchange"
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
        if existing is not None and not safe_retry_redispatch:
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
            self._report_sidecar_exit()
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
        replayed = self._replay_committed_details(
            request,
            operation_id,
        )
        if replayed is not None:
            return replayed
        if self._operation_is_reconciliation_unknown(operation_id):
            result = self._execute_details_with_lane(request)
            if not _source_result_is_reconciliation_unknown(result):
                self._store.resolve_browser_lane_from_conclusive_observation(
                    runtime_run_id=self._runtime_run_id,
                    operation_id=operation_id,
                    resolved_at=_now(),
                )
            return result
        guard = BrowserLaneGuard(
            store=self._store,
            runtime_run_id=self._runtime_run_id,
            operation_id=operation_id,
            operation_kind="details",
            now=_now,
            plus_seconds=_plus_seconds,
            wait_timeout_seconds=(
                self._settings.liepin_browser_lane_admission_timeout_seconds
            ),
            on_lease_lost=self._fence_active_sidecar,
        )
        with guard:
            try:
                result = self._execute_details_with_lane(request)
                if _source_result_is_reconciliation_unknown(result):
                    guard.preserve_unresolved(
                        "liepin_details_reconciliation_unknown"
                    )
                return result
            finally:
                self.close()

    def _replay_committed_cards(self, request, operation_id):
        try:
            operation = self._store.get_source_operation(
                self._runtime_run_id,
                operation_id,
            )
        except RuntimeControlLookupError:
            return None
        if (
            operation.operation_phase
            not in {"observed", "main_committed"}
            or operation.conclusive_observation_ref is None
            or operation.canonical_request_hash
            != canonical_liepin_cards_request_hash(request)
        ):
            return None
        digest = operation.conclusive_observation_ref.rsplit("/", 1)[-1]
        try:
            artifact = read_liepin_cards_artifact(
                self._artifact_root,
                operation.conclusive_observation_ref,
                expected_hash=digest,
            )
        except (OSError, ValueError):
            return None
        if (
            artifact.operation_id != operation_id
            or artifact.canonical_request_hash
            != operation.canonical_request_hash
        ):
            return None
        observation = SimpleNamespace(
            disposition=operation.source_operation_disposition,
            safe_reason_code=artifact.safe_reason_code,
        )
        return _workflow_result(request, artifact, observation)

    def _operation_is_reconciliation_unknown(
        self,
        operation_id: str,
    ) -> bool:
        try:
            operation = self._store.get_source_operation(
                self._runtime_run_id,
                operation_id,
            )
        except RuntimeControlLookupError:
            return False
        return (
            operation.source_operation_disposition
            == "reconciliation_unknown"
            or operation.retry_posture == "reconcile_first"
        )

    def _replay_committed_details(self, request, operation_id):
        try:
            operation = self._store.get_source_operation(
                self._runtime_run_id,
                operation_id,
            )
        except RuntimeControlLookupError:
            return None
        request_hash = canonical_liepin_details_request_hash(request)
        if (
            operation.operation_phase
            not in {"observed", "main_committed"}
            or operation.conclusive_observation_ref is None
            or operation.canonical_request_hash != request_hash
        ):
            return None
        digest = operation.conclusive_observation_ref.rsplit("/", 1)[-1]
        try:
            artifact = read_liepin_details_artifact(
                self._details_artifact_root,
                operation.conclusive_observation_ref,
                expected_hash=digest,
            )
        except (OSError, ValueError):
            return None
        disposition = operation.source_operation_disposition
        if disposition not in {
            "completed",
            "partial",
            "failed",
            "reconciliation_unknown",
        }:
            return None
        observation = LiepinDetailsObservationV1.model_validate(
            {
                "contract_version": (
                    "seektalent.source.liepin-details.observation/v1"
                ),
                "operation_id": operation_id,
                "canonical_request_hash": request_hash,
                "disposition": disposition,
                "artifact_ref": operation.conclusive_observation_ref,
                "artifact_hash": digest,
                "open_mode": artifact.open_mode,
                "provider_candidate_key_hash": (
                    artifact.provider_candidate_key_hash
                ),
                "rank": artifact.rank,
                "action_attempted": artifact.action_attempted,
                "effect_posture": artifact.effect_posture,
                "safe_reason_code": artifact.safe_reason_code,
                "producer_generation": 1,
            },
            strict=True,
        )
        if not _details_artifact_binds_accepted_request(
            request=request,
            artifact=artifact,
            observation=observation,
            operation_id=operation_id,
            request_hash=request_hash,
        ):
            return None
        return _details_workflow_result(request, artifact, observation)

    def _execute_details_with_lane(
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
        safe_retry_redispatch = False
        if (
            existing is not None
            and existing.operation.retry_posture == "safe_retry"
        ):
            self._require_active_detail_dispatch_transition(
                operation_id=operation_id,
                request_hash=request_hash,
                source_lane_run_id=request.source_lane_run_id,
                query_instance_id=request.query_instance_id,
            )
            suffix = operation_id.removeprefix("details_")
            fence_ref = sha256(
                (
                    f"{self._runtime_run_id}:{self._executor_id}:"
                    f"{self._attempt_no}:"
                    f"{self._runtime_attempt_authority_ref}"
                ).encode()
            ).hexdigest()
            scope_digest = sha256(
                (
                    f"{self._runtime_run_id}:{operation_id}:"
                    f"{self._attempt_no}"
                ).encode()
            ).hexdigest()[:40]
            existing = self._store.mint_current_safe_retry_dispatch_epoch(
                runtime_run_id=self._runtime_run_id,
                operation_id=operation_id,
                executor_id=self._executor_id,
                attempt_no=self._attempt_no,
                observed_at=_now(),
                runtime_attempt_authority_ref=(
                    self._runtime_attempt_authority_ref
                ),
                runtime_attempt_fence_ref=fence_ref,
                profile_binding_generation=(
                    self._profile_binding_generation
                ),
                browser_control_scope_id=(
                    f"details-retry-scope-{suffix[:16]}-{scope_digest}"
                ),
                controller_fence_ref=None,
            )
            _inject_detail_step_fault(
                "after_safe_retry_mint_before_exchange"
            )
            safe_retry_redispatch = True
        elif (
            existing is not None
            and existing.operation.retry_posture == "no_retry"
            and existing.dispatch.status == "pending"
        ):
            self._require_active_detail_dispatch_transition(
                operation_id=operation_id,
                request_hash=request_hash,
                source_lane_run_id=request.source_lane_run_id,
                query_instance_id=request.query_instance_id,
                accepted=existing,
            )
            safe_retry_redispatch = True
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
            self._persist_detail_dispatch_transition(
                request,
            )
            _inject_detail_step_fault(
                "after_detail_transition_before_readiness"
            )
            try:
                self._ready_source_process()
            except (OSError, RuntimeError, SidecarReadinessError):
                self._report_sidecar_exit()
                return _details_failed_result(
                    reason="liepin_opencli_status_unavailable",
                    effect_posture="not_attempted",
                    rank=None,
                    action_attempted=None,
                )
            _inject_detail_step_fault("before_initial_detail_accept")
            accept_started = time.perf_counter()
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
                advance_detail_transition=True,
                transition_created_at=_now(),
            )
            active_dispatch = (
                self._store.get_active_workflow_transition(
                    runtime_run_id=self._runtime_run_id,
                    source_lane_run_id=request.source_lane_run_id,
                    query_instance_id=request.query_instance_id,
                )
            )
            if (
                active_dispatch is None
                or active_dispatch.step_kind != "detail_dispatch"
            ):
                raise RuntimeControlError(
                    "runtime_detail_dispatch_transition_missing"
                )
            self._step_resource_evidence[
                "transitionWriteCount"
            ] += 1
            self._step_resource_evidence[
                "transitionPayloadBytes"
            ] += active_dispatch.payload_size_bytes
            self._step_resource_evidence[
                "transitionTransactionDurationMs"
            ] += (time.perf_counter() - accept_started) * 1000
            _inject_detail_step_fault(
                "after_initial_detail_accept_before_exchange"
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
        if existing is not None and not safe_retry_redispatch:
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
            self._report_sidecar_exit()
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
            _inject_detail_step_fault(
                "after_detail_ack_before_observation"
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
        if not _details_artifact_binds_accepted_request(
            request=request,
            artifact=artifact,
            observation=observation,
            operation_id=operation_id,
            request_hash=request_hash,
        ):
            return _details_identity_mismatch_result(observation)
        if (
            self._store.get_source_operation(
                self._runtime_run_id,
                operation_id,
            ).operation_phase
            != "main_committed"
        ):
            self._pending_checkpoint_operation_ids.add(operation_id)
        return _details_workflow_result(request, artifact, observation)

    def _ensure_source_dispatch_transition(
        self,
        request: LiepinCardsOperationRequestV1,
    ) -> None:
        request_hash = canonical_liepin_cards_request_hash(request)
        operation_id = stable_liepin_cards_operation_id(request)
        active = self._store.get_active_workflow_transition(
            runtime_run_id=self._runtime_run_id,
            source_lane_run_id=request.source_lane_run_id,
            query_instance_id=request.query_instance_id,
        )
        if active is not None:
            if (
                active.step_kind != "source_dispatch"
                or active.continuation.get("operationId") != operation_id
                or active.continuation.get("requestHash") != request_hash
            ):
                raise RuntimeControlError(
                    "runtime_source_dispatch_transition_mismatch"
                )
            round_plan_ref = active.continuation.get(
                "roundWorkPlanArtifactRef"
            )
            round_plan_hash = active.continuation.get(
                "roundWorkPlanArtifactHash"
            )
            if (
                not isinstance(round_plan_ref, str)
                or not isinstance(round_plan_hash, str)
                or round_plan_ref not in active.artifact_refs
            ):
                raise RuntimeControlError(
                    "runtime_workflow_round_plan_binding_invalid"
                )
            binding = (round_plan_ref, round_plan_hash)
            current_binding = self._round_work_plan_bindings.get(
                active.round_no
            )
            if current_binding is None:
                plan = self.load_recovered_round_work_plan(
                    artifact_ref=round_plan_ref,
                    artifact_hash=round_plan_hash,
                )
                if (
                    plan.round_no != active.round_no
                    or not any(
                        lane.source_lane_run_id
                        == request.source_lane_run_id
                        and lane.query_instance_id
                        == request.query_instance_id
                        for lane in plan.lanes
                    )
                ):
                    raise RuntimeControlError(
                        "runtime_workflow_round_plan_binding_invalid"
                    )
            elif current_binding != binding:
                raise RuntimeControlError(
                    "runtime_workflow_round_plan_conflict"
                )
            return
        if active is None:
            checkpoint = self._store.get_latest_checkpoint(
                runtime_run_id=self._runtime_run_id
            )
            if (
                checkpoint is None
                or checkpoint.safe_boundary != "before_round_controller"
                or checkpoint.round_no is None
            ):
                raise RuntimeControlError(
                    "runtime_source_dispatch_checkpoint_missing"
                )
            round_no = checkpoint.round_no
        round_plan_binding = self._round_work_plan_bindings.get(round_no)
        if round_plan_binding is None:
            raise RuntimeControlError(
                "runtime_workflow_round_plan_missing"
            )
        round_plan_ref, round_plan_hash = round_plan_binding
        result = self._store.write_workflow_transition(
            runtime_run_id=self._runtime_run_id,
            source_lane_run_id=request.source_lane_run_id,
            query_instance_id=request.query_instance_id,
            executor_id=self._executor_id,
            attempt_no=self._attempt_no,
            round_no=round_no,
            step_kind="source_dispatch",
            continuation={
                "schemaVersion": (
                    "runtime-source-dispatch-continuation/v1"
                ),
                "operationId": operation_id,
                "requestHash": request_hash,
                "queryFingerprint": request_hash,
                "roundWorkPlanArtifactRef": round_plan_ref,
                "roundWorkPlanArtifactHash": round_plan_hash,
            },
            artifact_refs=(round_plan_ref,),
            source_operation_ids=(),
            created_at=_now(),
        )
        self._record_transition_resource(result)

    def _persist_detail_dispatch_transition(
        self,
        request: LiepinDetailsOperationRequestV1,
    ) -> None:
        active = self._store.get_active_workflow_transition(
            runtime_run_id=self._runtime_run_id,
            source_lane_run_id=request.source_lane_run_id,
            query_instance_id=request.query_instance_id,
        )
        if active is None:
            raise RuntimeControlError(
                "runtime_detail_dispatch_parent_missing"
            )
        if active.step_kind == "detail_queued":
            if self._detail_request_from_active_transition(active) != request:
                raise RuntimeControlError(
                    "runtime_detail_dispatch_transition_mismatch"
                )
            return
        if active.step_kind == "detail_dispatch":
            plan, plan_ref, plan_hash = self._work_plan_from_transition(active)
            current_cursor = active.continuation.get("detailCursor")
            if isinstance(current_cursor, bool) or not isinstance(current_cursor, int):
                raise RuntimeControlError(
                    "runtime_detail_work_plan_cursor_invalid"
                )
            try:
                next_cursor = self._cursor_for_detail_request(
                    plan,
                    request,
                    after=current_cursor,
                )
            except RuntimeControlError as exc:
                if (
                    exc.reason_code
                    != "runtime_detail_work_plan_request_mismatch"
                ):
                    raise
                pending = self._pending_detail_work_plans.get(
                    request.source_lane_run_id
                )
                if pending is None:
                    raise
                plan, plan_ref, plan_hash = pending
                next_cursor = self._cursor_for_detail_request(
                    plan,
                    request,
                    after=-1,
                )
            self._queue_detail_work_item(
                plan=plan,
                plan_artifact_ref=plan_ref,
                plan_artifact_hash=plan_hash,
                cursor=next_cursor,
            )
            return
        if active.step_kind != "source_dispatch":
            raise RuntimeControlError(
                "runtime_detail_dispatch_parent_missing"
            )
        raise RuntimeControlError(
            "runtime_detail_work_plan_context_missing"
        )

    def _queue_detail_work_item(
        self,
        *,
        plan: LiepinDetailWorkPlanV1,
        plan_artifact_ref: str,
        plan_artifact_hash: str,
        cursor: int,
    ) -> None:
        if cursor < 0 or cursor >= len(plan.items):
            raise RuntimeControlError("runtime_detail_work_plan_cursor_invalid")
        active = self._store.get_active_workflow_transition(
            runtime_run_id=self._runtime_run_id,
            source_lane_run_id=plan.source_lane_run_id,
            query_instance_id=plan.query_instance_id,
        )
        if active is None or active.step_kind not in {
            "source_dispatch",
            "detail_dispatch",
            "detail_queued",
        }:
            raise RuntimeControlError("runtime_detail_dispatch_parent_missing")
        request = _detail_request_from_work_plan(plan, cursor)
        request_write = write_liepin_details_request_artifact(
            self._details_request_artifact_root,
            request,
        )
        self._record_request_artifact_resource(request_write)
        continuation: dict[str, object] = {
            "schemaVersion": "runtime-detail-queued-continuation/v1",
            "operationId": stable_liepin_details_operation_id(request),
            "requestHash": canonical_liepin_details_request_hash(request),
            "requestArtifactRef": request_write.artifact_ref,
            "workPlanArtifactRef": plan_artifact_ref,
            "workPlanHash": plan_artifact_hash,
            "workPlanPhase": plan.phase,
            "detailCursor": cursor,
            "detailCompletedHighWatermark": cursor - 1,
            "cardsArtifactRef": plan.cards_artifact_ref,
        }
        operation_ids: list[str] = []
        artifact_refs = set(active.artifact_refs)
        if active.step_kind == "detail_queued":
            if active.continuation != continuation:
                raise RuntimeControlError(
                    "runtime_detail_dispatch_transition_mismatch"
                )
        else:
            previous_operation_id = active.continuation.get("operationId")
            if not isinstance(previous_operation_id, str):
                raise RuntimeControlError(
                    "runtime_detail_dispatch_transition_mismatch"
                )
            previous = self._store.get_source_operation(
                self._runtime_run_id,
                previous_operation_id,
            )
            if (
                previous.operation_phase != "observed"
                or previous.main_commit_ref is not None
                or previous.conclusive_observation_ref is None
            ):
                raise RuntimeControlError(
                    "runtime_detail_dispatch_previous_unsettled"
                )
            operation_ids.append(previous_operation_id)
            artifact_refs.add(previous.conclusive_observation_ref)
        artifact_refs.update(
            {
                request_write.artifact_ref,
                plan_artifact_ref,
                plan.cards_artifact_ref,
            }
        )
        result = self._store.write_workflow_transition(
            runtime_run_id=self._runtime_run_id,
            source_lane_run_id=plan.source_lane_run_id,
            query_instance_id=plan.query_instance_id,
            executor_id=self._executor_id,
            attempt_no=self._attempt_no,
            round_no=active.round_no,
            step_kind="detail_queued",
            continuation=continuation,
            artifact_refs=tuple(sorted(artifact_refs)),
            source_operation_ids=tuple(operation_ids),
            created_at=_now(),
        )
        self._pending_checkpoint_operation_ids.difference_update(operation_ids)
        self._record_transition_resource(result)

    def _cards_artifact_binding(
        self,
        *,
        source_lane_run_id: str,
        query_instance_id: str,
    ) -> tuple[str, str]:
        chain = self._store.get_active_workflow_transition_chain(
            runtime_run_id=self._runtime_run_id,
            source_lane_run_id=source_lane_run_id,
            query_instance_id=query_instance_id,
        )
        if not chain or chain[0].step_kind != "source_dispatch":
            raise RuntimeControlError(
                "runtime_detail_work_plan_cards_missing"
            )
        operation_id = chain[0].continuation.get("operationId")
        if not isinstance(operation_id, str):
            raise RuntimeControlError(
                "runtime_detail_work_plan_cards_missing"
            )
        operation = self._store.get_source_operation(
            self._runtime_run_id,
            operation_id,
        )
        artifact_ref = operation.conclusive_observation_ref
        if (
            operation.operation_phase not in {"observed", "main_committed"}
            or artifact_ref is None
        ):
            raise RuntimeControlError(
                "runtime_detail_work_plan_cards_missing"
            )
        artifact_hash = artifact_ref.rsplit("/", 1)[-1]
        read_liepin_cards_artifact(
            self._artifact_root,
            artifact_ref,
            expected_hash=artifact_hash,
        )
        return artifact_ref, artifact_hash

    def _work_plan_from_transition(self, active):
        plan_ref = active.continuation.get("workPlanArtifactRef")
        plan_hash = active.continuation.get("workPlanHash")
        if (
            not isinstance(plan_ref, str)
            or not isinstance(plan_hash, str)
            or plan_ref not in active.artifact_refs
        ):
            raise RuntimeControlError(
                "runtime_detail_work_plan_binding_invalid"
            )
        plan = read_liepin_detail_work_plan_artifact(
            self._detail_work_plan_artifact_root,
            plan_ref,
            expected_hash=plan_hash,
        )
        if (
            plan.runtime_run_id != self._runtime_run_id
            or plan.round_no != active.round_no
            or plan.cards_artifact_ref
            != active.continuation.get("cardsArtifactRef")
            or plan.phase != active.continuation.get("workPlanPhase")
        ):
            raise RuntimeControlError(
                "runtime_detail_work_plan_binding_invalid"
            )
        return plan, plan_ref, plan_hash

    def _detail_request_from_active_transition(self, active):
        plan, _plan_ref, _plan_hash = self._work_plan_from_transition(active)
        cursor = active.continuation.get("detailCursor")
        if isinstance(cursor, bool) or not isinstance(cursor, int):
            raise RuntimeControlError(
                "runtime_detail_work_plan_cursor_invalid"
            )
        return _detail_request_from_work_plan(plan, cursor)

    def _cursor_for_detail_request(
        self,
        plan: LiepinDetailWorkPlanV1,
        request: LiepinDetailsOperationRequestV1,
        *,
        after: int,
    ) -> int:
        for cursor in range(after + 1, len(plan.items)):
            if _detail_request_from_work_plan(plan, cursor) == request:
                return cursor
        raise RuntimeControlError(
            "runtime_detail_work_plan_request_mismatch"
        )

    def _record_request_artifact_resource(self, write) -> None:
        if write.published:
            self._step_resource_evidence[
                "requestArtifactWriteCount"
            ] += 1
            self._step_resource_evidence[
                "requestArtifactBytes"
            ] += write.payload_size_bytes
        self._step_resource_evidence[
            "requestArtifactWriteDurationMs"
        ] += write.write_duration_ms

    def _record_work_plan_artifact_resource(self, write) -> None:
        if write.published:
            self._step_resource_evidence[
                "workPlanArtifactWriteCount"
            ] += 1
            self._step_resource_evidence[
                "workPlanArtifactBytes"
            ] += write.payload_size_bytes
        self._step_resource_evidence[
            "workPlanArtifactWriteDurationMs"
        ] += write.write_duration_ms

    def _completed_detail_results(
        self,
        plan: LiepinDetailWorkPlanV1,
    ) -> tuple[dict[str, object], ...]:
        results: list[dict[str, object]] = []
        for cursor in range(len(plan.items)):
            request = _detail_request_from_work_plan(plan, cursor)
            replayed = self._replay_committed_details(
                request,
                stable_liepin_details_operation_id(request),
            )
            if replayed is not None:
                results.append(replayed[1])
        return tuple(results)

    def _capture_plan_from_completed_locators(
        self,
        locator_plan: LiepinDetailWorkPlanV1,
    ) -> LiepinDetailWorkPlanV1:
        if locator_plan.phase != "locators":
            raise RuntimeControlError(
                "runtime_detail_work_plan_locator_required"
            )
        locator_item_by_rank = {
            item.rank: item for item in locator_plan.items
        }
        capture_items: list[LiepinDetailWorkItemV1] = []
        for structured in self._completed_detail_results(locator_plan):
            counts = structured.get("counts")
            rank = (
                cast(dict[str, object], counts).get("rank")
                if isinstance(counts, dict)
                else None
            )
            provider_hash = structured.get(
                "provider_candidate_key_hash"
            )
            if (
                structured.get("ok") is not True
                or isinstance(rank, bool)
                or not isinstance(rank, int)
                or not isinstance(provider_hash, str)
                or len(provider_hash) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in provider_hash
                )
            ):
                continue
            locator_item = locator_item_by_rank.get(rank)
            if locator_item is None:
                raise RuntimeControlError(
                    "runtime_detail_work_plan_result_mismatch"
                )
            capture_items.append(
                LiepinDetailWorkItemV1(
                    rank=rank,
                    card_ref=locator_item.card_ref,
                    provider_candidate_key_hash=provider_hash,
                )
            )
        return locator_plan.model_copy(
            update={"phase": "captures", "items": tuple(capture_items)}
        )

    @staticmethod
    def _require_detail_result_conclusive(
        envelope: dict[str, object],
        structured: dict[str, object],
    ) -> None:
        if _source_result_is_reconciliation_unknown(
            (envelope, structured)
        ):
            raise RuntimeControlError(
                "liepin_details_reconciliation_unknown"
            )

    def _prepare_claim_for_recovered_capture(
        self,
        plan: LiepinDetailWorkPlanV1,
        cursor: int,
        ledger,
    ) -> None:
        if not plan.claim_aware:
            return
        provider_hash = plan.items[cursor].provider_candidate_key_hash
        if provider_hash is None:
            raise RuntimeControlError(
                "runtime_detail_work_plan_capture_hash_missing"
            )
        claim = ledger.snapshot().get(provider_hash)
        if claim is None:
            if not ledger.try_claim(provider_hash):
                raise RuntimeControlError(
                    "runtime_detail_claim_recovery_conflict"
                )
            return
        if claim.status != "terminal_failed":
            return
        if claim.last_safe_reason_code != "liepin_details_effect_unknown":
            return
        request = _detail_request_from_work_plan(plan, cursor)
        operation = self._store.get_source_operation(
            self._runtime_run_id,
            stable_liepin_details_operation_id(request),
        )
        if operation.retry_posture not in {"safe_retry", "no_retry"}:
            raise RuntimeControlError(
                "runtime_detail_claim_reconciliation_pending"
            )
        ledger.resume_after_no_effect_reconciliation(provider_hash)

    @staticmethod
    def _claim_capture_item(ledger, provider_hash: str) -> bool:
        claim = ledger.snapshot().get(provider_hash)
        if claim is None:
            return ledger.try_claim(provider_hash)
        return claim.status == "claimed"

    @staticmethod
    def _settle_recovered_detail_claim(
        plan: LiepinDetailWorkPlanV1,
        cursor: int,
        envelope: dict[str, object],
        structured: dict[str, object],
        ledger,
    ) -> None:
        if not plan.claim_aware:
            return
        provider_hash = plan.items[cursor].provider_candidate_key_hash
        if provider_hash is None:
            raise RuntimeControlError(
                "runtime_detail_work_plan_capture_hash_missing"
            )
        claim = ledger.snapshot().get(provider_hash)
        if claim is None:
            raise RuntimeControlError("runtime_detail_claim_missing")
        if claim.status != "claimed":
            return
        counts = structured.get("counts")
        action_attempted = (
            cast(dict[str, object], counts).get("action_attempted")
            if isinstance(counts, dict)
            else None
        )
        posture = envelope.get("effect_posture")
        if not isinstance(posture, str):
            posture = structured.get("effect_posture")
        if posture == "not_attempted" and action_attempted == 0:
            ledger.release_unattempted(provider_hash)
            return
        if not ledger.has_browser_open_attempt(provider_hash):
            ledger.record_browser_open_attempt(provider_hash)
        if posture == "attempted" and structured.get("ok") is True:
            ledger.mark_opened(provider_hash)
            return
        reason = structured.get("safe_reason_code")
        ledger.mark_terminal_failed(
            provider_hash,
            safe_reason_code=(
                "liepin_details_effect_unknown"
                if posture == "unknown"
                else (
                    reason
                    if isinstance(reason, str) and reason
                    else "liepin_details_terminal_failure"
                )
            ),
        )

    def _require_active_detail_dispatch_transition(
        self,
        *,
        operation_id: str,
        request_hash: str,
        source_lane_run_id: str,
        query_instance_id: str,
        accepted: AcceptedSourceOperation | None = None,
    ) -> None:
        active = self._store.get_active_workflow_transition(
            runtime_run_id=self._runtime_run_id,
            source_lane_run_id=source_lane_run_id,
            query_instance_id=query_instance_id,
        )
        if (
            active is None
            or active.step_kind != "detail_dispatch"
            or active.continuation.get("operationId") != operation_id
            or active.continuation.get("requestHash") != request_hash
            or active.continuation.get("schemaVersion")
            != "runtime-detail-dispatch-continuation/v1"
        ):
            raise RuntimeControlError(
                "runtime_detail_dispatch_transition_missing"
            )
        if accepted is None:
            return
        expectation = accepted.expectation
        dispatch = accepted.dispatch
        if (
            active.continuation.get(
                "dispatchAuthorizationOrdinal"
            )
            != dispatch.dispatch_authorization_ordinal
            or active.continuation.get("dispatchIntentId")
            != dispatch.dispatch_intent_id
            or active.continuation.get("dispatchIntentDigest")
            != dispatch.dispatch_intent_digest
            or active.continuation.get("runtimeAttemptNo")
            != expectation.runtime_attempt_no
            or active.continuation.get("runtimeAttemptFenceRef")
            != expectation.runtime_attempt_fence_ref
            or active.continuation.get("browserControlScopeId")
            != expectation.browser_control_scope_id
        ):
            raise RuntimeControlError(
                "runtime_detail_dispatch_epoch_mismatch"
            )

    def _require_active_source_dispatch_transition(
        self,
        *,
        operation_id: str,
        request_hash: str,
        source_lane_run_id: str,
        query_instance_id: str,
        accepted: AcceptedSourceOperation,
    ) -> None:
        active = self._store.get_active_workflow_transition(
            runtime_run_id=self._runtime_run_id,
            source_lane_run_id=source_lane_run_id,
            query_instance_id=query_instance_id,
        )
        expectation = accepted.expectation
        dispatch = accepted.dispatch
        if (
            active is None
            or active.step_kind != "source_dispatch"
            or active.continuation.get("operationId") != operation_id
            or active.continuation.get("requestHash") != request_hash
            or active.continuation.get("schemaVersion")
            != "runtime-source-dispatch-continuation/v1"
            or active.continuation.get(
                "dispatchAuthorizationOrdinal"
            )
            != dispatch.dispatch_authorization_ordinal
            or active.continuation.get("dispatchIntentId")
            != dispatch.dispatch_intent_id
            or active.continuation.get("dispatchIntentDigest")
            != dispatch.dispatch_intent_digest
            or active.continuation.get("runtimeAttemptNo")
            != expectation.runtime_attempt_no
            or active.continuation.get("runtimeAttemptFenceRef")
            != expectation.runtime_attempt_fence_ref
            or active.continuation.get("browserControlScopeId")
            != expectation.browser_control_scope_id
        ):
            raise RuntimeControlError(
                "runtime_source_dispatch_epoch_mismatch"
            )

    def _record_transition_resource(
        self,
        result: WorkflowTransitionWriteResult,
    ) -> None:
        self._step_resource_evidence[
            "transitionTransactionDurationMs"
        ] += result.transaction_duration_ms
        if result.inserted:
            self._step_resource_evidence[
                "transitionWriteCount"
            ] += 1
            self._step_resource_evidence[
                "transitionPayloadBytes"
            ] += result.transition.payload_size_bytes

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
            deadline=_browser_effect_deadline(self._settings),
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
            deadline=_browser_effect_deadline(self._settings),
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
            self._process = _spawn_sidecar(
                settings=self._settings,
                journal_path=self._journal_path,
                artifact_root=self._artifact_root,
                history_only=False,
                replay_observed_only=True,
            )
            ack, terminal = self._exchange(submit)
        except (OSError, RuntimeError, SidecarReadinessError):
            return None
        finally:
            process, self._process = self._process, None
            if process is not None:
                process.close()
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
            self._process = _spawn_sidecar(
                settings=self._settings,
                journal_path=self._journal_path,
                artifact_root=self._artifact_root,
                history_only=False,
                replay_observed_only=True,
            )
            ack, terminal = self._exchange_details(submit)
        except (OSError, RuntimeError, SidecarReadinessError):
            return None
        finally:
            process, self._process = self._process, None
            if process is not None:
                process.close()
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
                self._process = None
            self._process = _spawn_sidecar(
                settings=self._settings,
                journal_path=self._journal_path,
                artifact_root=self._artifact_root,
                history_only=False,
            )
        return self._process

    def _report_sidecar_exit(self) -> None:
        process = self._process
        if process is None:
            return
        diagnostic = process.exit_diagnostic()
        if diagnostic is None:
            return
        _LOGGER.warning(
            "liepin_source_sidecar_effect_failed boundary=%s "
            "operation_kind=%s safe_reason_code=%s exit_code=%s",
            diagnostic.boundary,
            diagnostic.operation_kind,
            diagnostic.safe_reason_code,
            process.process.returncode,
        )


def _spawn_sidecar(
    *,
    settings: AppSettings,
    journal_path: Path,
    artifact_root: Path,
    history_only: bool,
    replay_observed_only: bool = False,
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
    elif replay_observed_only:
        command.append("--replay-observed-only")
    environment = _sidecar_environment(environment_overrides)
    diagnostic_path = (
        journal_path.parent
        / f".liepin-sidecar-exit-{secrets.token_hex(16)}.json"
    )
    environment["SEEKTALENT_LIEPIN_SIDECAR_DIAGNOSTIC_PATH"] = str(
        diagnostic_path
    )
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
        diagnostic_path.unlink(missing_ok=True)
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
        diagnostic_path.unlink(missing_ok=True)
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
        diagnostic_path=diagnostic_path,
    )


def _parse_sidecar_exit_diagnostic(
    raw: bytes,
) -> _SidecarExitDiagnostic | None:
    if not raw or len(raw) > 4096:
        return None
    try:
        payload = json.loads(raw.decode("utf-8").strip())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {
            "schema_version",
            "boundary",
            "operation_kind",
            "safe_reason_code",
        }
        or payload.get("schema_version")
        != "seektalent.liepin-sidecar-exit.v1"
        or payload.get("boundary")
        not in {"cards_effect", "details_effect"}
        or payload.get("operation_kind") not in {"cards", "details"}
        or not isinstance(payload.get("safe_reason_code"), str)
        or _SAFE_SIDECAR_REASON.fullmatch(
            payload["safe_reason_code"]
        )
        is None
    ):
        return None
    return _SidecarExitDiagnostic(
        boundary=payload["boundary"],
        operation_kind=payload["operation_kind"],
        safe_reason_code=payload["safe_reason_code"],
    )


def _sidecar_environment(
    environment_overrides: dict[str, str] | None,
) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["SEEKTALENT_RUNTIME_ARTIFACT_OUTPUT_MODE"] = "prod"
    if environment_overrides is not None:
        environment.update(environment_overrides)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    return environment


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


def _cards_readiness_unavailable_result():
    reason = "liepin_opencli_status_unavailable"
    return (
        {
            "status": "failed",
            "cards_seen": 0,
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


def _source_result_is_reconciliation_unknown(
    result: tuple[dict[str, object], dict[str, object]],
) -> bool:
    return any(
        isinstance(value, str)
        and value.endswith("_reconciliation_unknown")
        for payload in result
        for key, value in payload.items()
        if key == "safe_reason_code"
    )


def _details_artifact_binds_accepted_request(
    *,
    request: LiepinDetailsOperationRequestV1,
    artifact: LiepinDetailsArtifactV1,
    observation: LiepinDetailsObservationV1,
    operation_id: str,
    request_hash: str,
) -> bool:
    """Reject any artifact or observation that is not bound to the accepted request."""
    if operation_id not in {artifact.operation_id, observation.operation_id}:
        return False
    if artifact.operation_id != observation.operation_id:
        return False
    if request_hash != artifact.canonical_request_hash:
        return False
    if request_hash != observation.canonical_request_hash:
        return False
    if artifact.open_mode != request.open_mode:
        return False
    if observation.open_mode != artifact.open_mode:
        return False
    if (
        request.open_mode == "cached_locator"
        and artifact.provider_candidate_key_hash != request.provider_candidate_key_hash
    ):
        return False
    if observation.provider_candidate_key_hash != artifact.provider_candidate_key_hash:
        return False
    if artifact.rank != request.rank or artifact.card_ref != request.card_ref:
        return False
    if observation.rank != artifact.rank:
        return False
    if observation.action_attempted != artifact.action_attempted:
        return False
    if observation.effect_posture != artifact.effect_posture:
        return False
    if observation.safe_reason_code != artifact.safe_reason_code:
        return False
    return observation.disposition == _details_disposition(artifact.status)


def _details_disposition(status: str) -> str:
    if status == "succeeded":
        return "completed"
    if status == "partial":
        return "partial"
    return "failed"


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
        "effect_posture": artifact.effect_posture,
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
        "effect_posture": artifact.effect_posture,
        "resume": artifact.resume,
        "ingest_ready": (
            artifact.resume is not None
            and observation.disposition in {"completed", "partial"}
        ),
    }
    return envelope, structured


def _details_artifact_unavailable_result(observation):
    return _details_failed_result(
        reason="liepin_details_artifact_unavailable",
        effect_posture=observation.effect_posture,
        rank=observation.rank,
        action_attempted=observation.action_attempted,
    )


def _details_identity_mismatch_result(observation):
    return _details_failed_result(
        reason="liepin_details_artifact_identity_mismatch",
        effect_posture="unknown",
        rank=observation.rank,
        action_attempted=observation.action_attempted,
    )


def _details_unknown_result():
    return _details_failed_result(
        reason="liepin_details_reconciliation_unknown",
        effect_posture="unknown",
        rank=None,
        action_attempted=None,
    )


def _details_failed_result(
    *,
    reason: str,
    effect_posture: str,
    rank: int | None,
    action_attempted: int | None,
):
    counts = (
        {"rank": rank, "action_attempted": action_attempted}
        if rank is not None and action_attempted is not None
        else {}
    )
    return (
        {
            "status": "failed",
            "safe_reason_code": reason,
            "effect_posture": effect_posture,
        },
        {
            "ok": False,
            "action": "capture_liepin_detail_resume",
            "safe_reason_code": reason,
            "counts": counts,
            "observation": {},
            "effect_posture": effect_posture,
            "resume": None,
            "ingest_ready": False,
        },
    )


def _inject_detail_step_fault(_point: str) -> None:
    return


def _inject_source_step_fault(_point: str) -> None:
    return


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


def _plus_seconds(value: str, seconds: float) -> str:
    from datetime import datetime, timedelta

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        parsed + timedelta(seconds=seconds)
    ).isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = ["LiepinCardsSourceOperationExecutor"]
