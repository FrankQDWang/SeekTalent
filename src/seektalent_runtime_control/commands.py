from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from pydantic import ValidationError

from seektalent.models import RequirementSheet
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeCheckpoint, RuntimeCommand, RuntimeControlEventInput
from seektalent_runtime_control.normalizer import (
    apply_next_round_patch,
    merge_requirement_sheet_supplement,
)
from seektalent_runtime_control.requirements import ApprovedRequirementRevision, RequirementAmendment, ReviewItem, ReviewResolutionOperation
from seektalent_runtime_control.store import RuntimeControlStore


_PENDING_COMMAND_STATUSES = {"accepted", "pending_safe_boundary"}
_TERMINAL_RUN_STATUSES = {"cancelled", "completed", "failed"}
_NEEDS_REVIEW_STATUS = "needs_review"
_EXTRACTING_STATUS = "extracting"
_PENDING_TARGET_ROUND_STATUS = "pending_target_round"
_REJECT_REVIEW_OPS = {"reject_candidate", "reject_fragment"}
_ALLOWED_REVIEW_OPS = {
    "accept_candidate",
    "edit_candidate",
    "move_candidate",
    *_REJECT_REVIEW_OPS,
}
_REQUIREMENT_AMENDMENT_UNCLASSIFIABLE_REASON_CODE = "requirement_amendment_unclassifiable"
_NOT_A_REQUIREMENT_REASON_CODE = "not_a_requirement"
_INVALID_REVIEW_OPERATION_REASON_CODE = "requirement_amendment_invalid_review_operation"
_INVALID_REVIEW_ITEM_PAYLOAD_REASON_CODE = "requirement_amendment_invalid_review_item"
_PROVENANCE_STRING_MAX_CHARS = 4000


class NextRoundRequirementNormalizer(Protocol):
    def normalize_next_round_requirement_text(
        self,
        *,
        text: str,
        target_section_hint: str | None,
        current_requirement: ApprovedRequirementRevision,
    ) -> dict[str, object]: ...


class NextRoundRequirementExtractor(Protocol):
    def extract_requirements(
        self,
        *,
        job_title: str | None,
        jd_text: str,
        notes: str | None,
        requirement_cache_scope: str | None = None,
    ) -> RequirementSheet: ...


@dataclass(frozen=True)
class NextRoundRequirementResult:
    amendment_id: str
    status: str
    target_round_no: int
    effective_boundary: str
    approved_requirement_revision_id: str | None
    review_required: bool = False
    review_items: list[ReviewItem] | None = None
    supersedes_amendment_id: str | None = None


class RuntimeCommandService:
    def __init__(
        self,
        *,
        store: RuntimeControlStore,
        requirement_extractor: NextRoundRequirementExtractor | None = None,
        requirement_normalizer: NextRoundRequirementNormalizer | None = None,
        command_id_factory: Callable[[], str] | None = None,
        amendment_id_factory: Callable[[], str] | None = None,
        approved_requirement_id_factory: Callable[[], str] | None = None,
        now: Callable[[], str] | None = None,
        boundary_wait_timeout_seconds: float = 30.0,
        boundary_wait_poll_seconds: float = 0.1,
    ) -> None:
        self.store = store
        self.requirement_extractor = requirement_extractor
        self.requirement_normalizer = requirement_normalizer
        self.command_id_factory = command_id_factory or (lambda: f"rtcmd_{uuid4().hex}")
        self.amendment_id_factory = amendment_id_factory or (lambda: f"reqamend_{uuid4().hex}")
        self.approved_requirement_id_factory = approved_requirement_id_factory or (lambda: f"reqapproved_{uuid4().hex}")
        self.now = now or _now
        self.boundary_wait_timeout_seconds = boundary_wait_timeout_seconds
        self.boundary_wait_poll_seconds = boundary_wait_poll_seconds

    def request_pause(self, *, runtime_run_id: str, requested_by: str | None, idempotency_key: str) -> RuntimeCommand:
        return self._request_lifecycle_command(
            runtime_run_id=runtime_run_id,
            command_type="pause",
            requested_by=requested_by,
            idempotency_key=idempotency_key,
            allowed_run_statuses={"running", "resume_requested"},
            requested_run_status="pause_requested",
        )

    def request_cancel(self, *, runtime_run_id: str, requested_by: str | None, idempotency_key: str) -> RuntimeCommand:
        existing = self.store.get_command_by_idempotency(runtime_run_id=runtime_run_id, idempotency_key=idempotency_key)
        if existing is not None:
            return existing
        run = self.store.get_run(runtime_run_id)
        if run.status in _TERMINAL_RUN_STATUSES:
            raise RuntimeControlError("runtime_command_conflict")
        self._reject_if_terminal_cancel_pending(runtime_run_id=runtime_run_id, command_type="cancel")
        for command in self.store.list_commands(
            runtime_run_id=runtime_run_id,
            conflict_group="lifecycle",
            statuses=_PENDING_COMMAND_STATUSES,
        ):
            if command.command_type in {"pause", "resume"}:
                self.store.update_command_status(
                    command_id=command.command_id,
                    status="superseded",
                    superseded_by_command_id=None,
                )
                self._append_command_event(
                    run=run,
                    event_type="runtime_command_superseded",
                    command=command,
                    created_at=self.now(),
                    payload={"supersededByCommandType": "cancel"},
                )
        command = self._save_lifecycle_command(
            run=run,
            command_type="cancel",
            requested_by=requested_by,
            idempotency_key=idempotency_key,
            requested_at=self.now(),
        )
        if run.status in {"queued", "paused"}:
            self.store.update_command_status(
                command_id=command.command_id,
                status="applied",
                applied_at=command.requested_at,
            )
            self.store.update_run_status(
                runtime_run_id=runtime_run_id,
                status="cancelled",
                updated_at=command.requested_at,
                completed_at=command.requested_at,
            )
            applied_command = self.store.get_command(command.command_id)
            self._append_command_event(
                run=self.store.get_run(runtime_run_id),
                event_type="runtime_command_accepted",
                command=applied_command,
                created_at=command.requested_at,
            )
            self._append_command_event(
                run=self.store.get_run(runtime_run_id),
                event_type="runtime_run_cancelled",
                command=applied_command,
                created_at=command.requested_at,
            )
            return applied_command
        self.store.update_run_status(
            runtime_run_id=runtime_run_id,
            status="cancellation_requested",
            updated_at=command.requested_at,
        )
        self._append_command_event(
            run=self.store.get_run(runtime_run_id),
            event_type="runtime_command_accepted",
            command=command,
            created_at=command.requested_at,
        )
        return command

    def resume_workflow(self, *, runtime_run_id: str, requested_by: str | None, idempotency_key: str) -> RuntimeCommand:
        return self._request_lifecycle_command(
            runtime_run_id=runtime_run_id,
            command_type="resume",
            requested_by=requested_by,
            idempotency_key=idempotency_key,
            allowed_run_statuses={"paused"},
            requested_run_status="resume_requested",
            invalid_reason_code="runtime_run_not_paused",
        )

    def apply_lifecycle_command_at_safe_boundary(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int | None = None,
        safe_boundary: str,
        checkpoint: RuntimeCheckpoint | None = None,
    ) -> RuntimeCommand | None:
        pending = self.store.list_commands(
            runtime_run_id=runtime_run_id,
            conflict_group="lifecycle",
            statuses=_PENDING_COMMAND_STATUSES,
        )
        command = _next_lifecycle_command(pending)
        if command is None:
            return None
        applied_at = self.now()
        if command.command_type in {"pause", "cancel"} and checkpoint is not None:
            self.store.write_checkpoint(checkpoint, executor_id=executor_id, attempt_no=attempt_no)
        self.store.update_command_status(command_id=command.command_id, status="applied", applied_at=applied_at)
        target_status = _applied_run_status(command.command_type)
        event = self.store.append_executor_event(
            _event(
                runtime_run_id=runtime_run_id,
                event_type="runtime_command_applied",
                stage="command",
                round_no=checkpoint.round_no if checkpoint is not None else None,
                status="completed",
                summary=f"{command.command_type} command applied",
                payload={"commandId": command.command_id, "safeBoundary": safe_boundary},
                created_at=applied_at,
            ),
            executor_id=executor_id,
            attempt_no=attempt_no,
            run_status=target_status,
            latest_checkpoint_id=checkpoint.checkpoint_id if checkpoint is not None else None,
        )
        if command.command_type in {"pause", "cancel"}:
            self.store.append_executor_event(
                _event(
                    runtime_run_id=runtime_run_id,
                    event_type="runtime_run_paused" if command.command_type == "pause" else "runtime_run_cancelled",
                    stage="command",
                    round_no=checkpoint.round_no if checkpoint is not None else None,
                    status="completed",
                    summary="run paused" if command.command_type == "pause" else "run cancelled",
                    payload={"commandId": command.command_id, "appliedEventId": event.event_id},
                    created_at=applied_at,
                ),
                executor_id=executor_id,
                attempt_no=attempt_no,
                run_status=target_status,
                completed_at=applied_at if command.command_type == "cancel" else None,
            )
            self.store.release_executor_lease(
                runtime_run_id=runtime_run_id,
                executor_id=executor_id,
                attempt_no=attempt_no,
                released_at=applied_at,
                reason_code=f"runtime_run_{target_status}",
            )
        return self.store.get_command(command.command_id)

    def submit_next_round_requirement(
        self,
        *,
        runtime_run_id: str,
        text: str,
        target_section_hint: str | None,
        idempotency_key: str,
        replace_amendment_id: str | None = None,
        provenance: dict[str, object] | None = None,
    ) -> NextRoundRequirementResult:
        run = self.store.get_run(runtime_run_id)
        self._reject_if_terminal_cancel_pending(runtime_run_id=runtime_run_id, command_type="apply_next_round_requirement")
        existing = self.store.get_requirement_amendment_by_idempotency(
            conversation_id=run.agent_conversation_id or runtime_run_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return _amendment_result(existing, supersedes_amendment_id=None)
        amendment_id = self.amendment_id_factory()
        safe_provenance = _sanitize_requirement_provenance(provenance)
        reserved, chain_predecessor_id = self.store.reserve_next_round_requirement(
            RequirementAmendment(
                amendment_id=amendment_id,
                agent_conversation_id=run.agent_conversation_id or runtime_run_id,
                runtime_run_id=runtime_run_id,
                base_approved_requirement_revision_id=run.approved_requirement_revision_id,
                target_round_no=(run.current_round or 0) + 1,
                effective_boundary="before_round_controller",
                input_text=text,
                target_section_hint=target_section_hint,
                status=_EXTRACTING_STATUS,
                normalized_patch={},
                rejected_fragments=[],
                review_items=[],
                provenance=safe_provenance,
                idempotency_key=idempotency_key,
                created_at=self.now(),
            ),
            after_round=run.current_round or 0,
            replace_amendment_id=replace_amendment_id,
        )
        if reserved.amendment_id != amendment_id:
            return _amendment_result(reserved, supersedes_amendment_id=None)
        if reserved.target_round_no is None:
            raise RuntimeControlError("requirement_amendment_stale")
        target_round_no = reserved.target_round_no
        base_revision_id = reserved.base_approved_requirement_revision_id
        if base_revision_id is None:
            raise RuntimeControlError("requirement_not_confirmed")
        current = self.store.get_approved_requirement(base_revision_id)
        try:
            normalized = self._extract_next_round_requirement_patch(
                runtime_run_id=runtime_run_id,
                text=text,
                target_section_hint=target_section_hint,
                current_requirement=current,
                effective_round_no=target_round_no,
            )
        except (RuntimeControlError, TypeError, ValueError, ValidationError):
            self.store.update_requirement_amendment_status(
                amendment_id=amendment_id,
                status="failed",
                resolved_at=self.now(),
            )
            raise
        review_items = _review_items(normalized)
        if review_items:
            amendment = self.store.complete_runtime_requirement_amendment_extraction(
                amendment_id=amendment_id,
                status=_NEEDS_REVIEW_STATUS,
                result_approved_requirement_revision_id=None,
                normalized_patch=dict(normalized),
                rejected_fragments=_list_payload(normalized.get("rejectedFragments")),
                review_items=review_items,
                resolved_at=self.now(),
            )
            self.store.append_event(
                _event(
                    runtime_run_id=runtime_run_id,
                    event_type="runtime_next_round_requirement_submitted",
                    stage=run.current_stage,
                    round_no=run.current_round,
                    status="pending",
                    summary="next-round requirement submitted",
                    payload={"amendmentId": amendment.amendment_id, "targetRoundNo": target_round_no},
                    created_at=amendment.created_at,
                )
            )
            self.store.append_event(
                _event(
                    runtime_run_id=runtime_run_id,
                    event_type="runtime_next_round_requirement_needs_review",
                    stage=run.current_stage,
                    round_no=run.current_round,
                    status=_NEEDS_REVIEW_STATUS,
                    summary="next-round requirement needs review",
                    payload={
                        "amendmentId": amendment.amendment_id,
                        "targetRoundNo": target_round_no,
                        "reviewItems": [_review_item_payload(item) for item in review_items],
                    },
                    created_at=amendment.created_at,
                )
            )
            return _amendment_result(amendment, supersedes_amendment_id=None)
        approved = ApprovedRequirementRevision(
            approved_requirement_revision_id=self.approved_requirement_id_factory(),
            draft_revision_id=None,
            base_approved_requirement_revision_id=current.approved_requirement_revision_id,
            source_amendment_id=amendment_id,
            agent_conversation_id=current.agent_conversation_id,
            requirement_sheet=apply_next_round_patch(current.requirement_sheet, normalized),
            selected_item_ids=list(current.selected_item_ids),
            deselected_item_ids=list(current.deselected_item_ids),
            created_at=self.now(),
        )
        self.store.save_approved_requirement(approved, idempotency_key=f"{idempotency_key}:approved")
        amendment = self.store.complete_runtime_requirement_amendment_extraction(
            amendment_id=amendment_id,
            status=_PENDING_TARGET_ROUND_STATUS,
            result_approved_requirement_revision_id=approved.approved_requirement_revision_id,
            normalized_patch=dict(normalized),
            rejected_fragments=_list_payload(normalized.get("rejectedFragments")),
            review_items=[],
            resolved_at=approved.created_at,
        )
        if chain_predecessor_id is not None:
            self.store.append_event(
                _event(
                    runtime_run_id=runtime_run_id,
                    event_type="runtime_next_round_requirement_superseded",
                    stage=run.current_stage,
                    round_no=run.current_round,
                    status="completed",
                    summary="next-round requirement superseded",
                    payload={
                        "amendmentId": chain_predecessor_id,
                        "supersededByAmendmentId": amendment.amendment_id,
                    },
                    created_at=approved.created_at,
                )
            )
        self.store.append_event(
            _event(
                runtime_run_id=runtime_run_id,
                event_type="runtime_next_round_requirement_submitted",
                stage=run.current_stage,
                round_no=run.current_round,
                status="pending",
                summary="next-round requirement submitted",
                payload={"amendmentId": amendment.amendment_id, "targetRoundNo": target_round_no},
                created_at=approved.created_at,
            )
        )
        return _amendment_result(amendment, supersedes_amendment_id=chain_predecessor_id)

    def _extract_next_round_requirement_patch(
        self,
        *,
        runtime_run_id: str,
        text: str,
        target_section_hint: str | None,
        current_requirement: ApprovedRequirementRevision,
        effective_round_no: int,
    ) -> dict[str, object]:
        if self.requirement_extractor is None:
            if self.requirement_normalizer is None:
                raise RuntimeControlError("requirement_extractor_required")
            return self.requirement_normalizer.normalize_next_round_requirement_text(
                text=text,
                target_section_hint=target_section_hint,
                current_requirement=current_requirement,
            )
        del target_section_hint
        supplement = self.requirement_extractor.extract_requirements(
            job_title=current_requirement.requirement_sheet.job_title,
            jd_text=text,
            notes=None,
            requirement_cache_scope=runtime_run_id,
        )
        merged = merge_requirement_sheet_supplement(
            current_requirement.requirement_sheet,
            supplement,
            effective_round_no=effective_round_no,
        )
        return {
            "requirementSheet": merged.model_dump(mode="json"),
            "extractedSupplement": supplement.model_dump(mode="json"),
            "reviewItems": [],
            "rejectedFragments": [],
        }

    def resolve_next_round_requirement_review(
        self,
        *,
        runtime_run_id: str,
        amendment_id: str,
        base_approved_requirement_revision_id: str,
        operations: list[ReviewResolutionOperation],
        idempotency_key: str,
    ) -> NextRoundRequirementResult:
        run = self.store.get_run(runtime_run_id)
        if run.status in _TERMINAL_RUN_STATUSES or run.status == "cancellation_requested":
            raise RuntimeControlError("runtime_no_future_round_available")
        existing = self.store.get_requirement_amendment_by_idempotency(
            conversation_id=run.agent_conversation_id or runtime_run_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return _amendment_result(existing, supersedes_amendment_id=None)
        amendment = self.store.get_requirement_amendment(amendment_id)
        if amendment is None or amendment.runtime_run_id != runtime_run_id:
            raise RuntimeControlError("requirement_draft_not_found")
        if amendment.status != _NEEDS_REVIEW_STATUS:
            return _amendment_result(amendment, supersedes_amendment_id=None)
        if amendment.base_approved_requirement_revision_id != base_approved_requirement_revision_id:
            raise RuntimeControlError("requirement_amendment_stale")
        current = self.store.get_approved_requirement(base_approved_requirement_revision_id)
        resolved_patch = _resolved_patch_from_review_items(operations)
        approved = ApprovedRequirementRevision(
            approved_requirement_revision_id=self.approved_requirement_id_factory(),
            draft_revision_id=None,
            base_approved_requirement_revision_id=current.approved_requirement_revision_id,
            source_amendment_id=amendment.amendment_id,
            agent_conversation_id=current.agent_conversation_id,
            requirement_sheet=apply_next_round_patch(current.requirement_sheet, resolved_patch),
            selected_item_ids=list(current.selected_item_ids),
            deselected_item_ids=list(current.deselected_item_ids),
            created_at=self.now(),
        )
        self.store.save_approved_requirement(approved, idempotency_key=f"{idempotency_key}:approved")
        resolved = self.store.resolve_runtime_requirement_amendment(
            amendment_id=amendment.amendment_id,
            runtime_run_id=runtime_run_id,
            status=_PENDING_TARGET_ROUND_STATUS,
            after_round=(amendment.target_round_no or run.current_round or 0) - 1,
            result_approved_requirement_revision_id=approved.approved_requirement_revision_id,
            resolved_patch=resolved_patch,
            resolved_at=approved.created_at,
        )
        if resolved.target_round_no is None:
            raise RuntimeControlError("requirement_amendment_stale")
        target_round_no = resolved.target_round_no
        self.store.append_event(
            _event(
                runtime_run_id=runtime_run_id,
                event_type="runtime_next_round_requirement_normalized",
                stage=run.current_stage,
                round_no=run.current_round,
                status="pending",
                summary="next-round requirement review resolved",
                payload={
                    "amendmentId": amendment.amendment_id,
                    "approvedRequirementRevisionId": approved.approved_requirement_revision_id,
                    "targetRoundNo": target_round_no,
                },
                created_at=approved.created_at,
            )
        )
        return _amendment_result(resolved, supersedes_amendment_id=None)

    def apply_next_round_requirements_at_boundary(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int | None = None,
        round_no: int,
    ) -> list[RequirementAmendment]:
        pending = self.prepare_next_round_requirements_at_boundary(
            runtime_run_id=runtime_run_id,
            executor_id=executor_id,
            attempt_no=attempt_no,
            round_no=round_no,
        )
        return self.commit_next_round_requirements_at_boundary(
            runtime_run_id=runtime_run_id,
            executor_id=executor_id,
            attempt_no=attempt_no,
            round_no=round_no,
            amendments=pending,
        )

    def prepare_next_round_requirements_at_boundary(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int | None = None,
        round_no: int,
    ) -> list[RequirementAmendment]:
        lock_event = _event(
            runtime_run_id=runtime_run_id,
            event_type="runtime_round_input_locked",
            stage="round",
            round_no=round_no,
            status="completed",
            summary=f"round {round_no} input locked",
            payload={"targetRoundNo": round_no},
            created_at=self.now(),
        ).model_copy(
            update={"idempotency_key": f"runtime-round-input-locked:{round_no}"}
        )
        self.store.lock_round_input(
            lock_event,
            executor_id=executor_id,
            attempt_no=attempt_no,
        )
        self._wait_for_blocking_next_round_requirements(
            runtime_run_id=runtime_run_id,
            executor_id=executor_id,
            attempt_no=attempt_no,
            round_no=round_no,
        )
        pending = self.store.list_runtime_requirement_amendments(
            runtime_run_id=runtime_run_id,
            target_round_no=round_no,
            statuses={"pending_target_round"},
        )
        if len(pending) > 1:
            raise RuntimeControlError(
                "runtime_requirement_amendment_chain_conflict",
                payload={
                    "targetRoundNo": round_no,
                    "amendmentIds": [item.amendment_id for item in pending],
                },
            )
        return pending

    def commit_next_round_requirements_at_boundary(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int | None = None,
        round_no: int,
        amendments: list[RequirementAmendment],
    ) -> list[RequirementAmendment]:
        applied: list[RequirementAmendment] = []
        for amendment in amendments:
            current = self.store.get_requirement_amendment(amendment.amendment_id)
            if current is None:
                raise RuntimeControlError("requirement_draft_not_found")
            if current.status == "applied":
                applied.append(current)
                continue
            if current.status != _PENDING_TARGET_ROUND_STATUS or current.target_round_no != round_no:
                raise RuntimeControlError("requirement_amendment_stale")
            applied_at = self.now()
            applied_event = _event(
                runtime_run_id=runtime_run_id,
                event_type="runtime_next_round_requirement_applied",
                stage="round",
                round_no=round_no,
                status="completed",
                summary="next-round requirement applied",
                payload={"amendmentId": amendment.amendment_id},
                created_at=applied_at,
            ).model_copy(
                update={
                    "idempotency_key": (
                        f"runtime-next-round-requirement-applied:{amendment.amendment_id}"
                    )
                }
            )
            target_revision_id = current.result_approved_requirement_revision_id
            if target_revision_id is None:
                raise RuntimeControlError("requirement_amendment_stale")
            activated_event = _event(
                runtime_run_id=runtime_run_id,
                event_type="runtime_requirement_revision_activated",
                stage="round",
                round_no=round_no,
                status="completed",
                summary="requirement revision activated",
                payload={
                    "amendmentId": amendment.amendment_id,
                    "approvedRequirementRevisionId": target_revision_id,
                },
                created_at=applied_at,
            ).model_copy(
                update={
                    "idempotency_key": (
                        f"runtime-requirement-revision-activated:{amendment.amendment_id}"
                    )
                }
            )
            updated = self.store.activate_requirement_amendment_at_boundary(
                runtime_run_id=runtime_run_id,
                amendment_id=amendment.amendment_id,
                round_no=round_no,
                applied_event=applied_event,
                activated_event=activated_event,
                executor_id=executor_id,
                attempt_no=attempt_no,
            )
            applied.append(updated)
        return applied

    def _wait_for_blocking_next_round_requirements(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int | None,
        round_no: int,
    ) -> None:
        deadline = time.monotonic() + self.boundary_wait_timeout_seconds
        while True:
            blocking = self.store.list_runtime_requirement_amendments(
                runtime_run_id=runtime_run_id,
                target_round_no=round_no,
                statuses={_EXTRACTING_STATUS, _NEEDS_REVIEW_STATUS},
            )
            if not blocking:
                return
            if time.monotonic() >= deadline:
                status_counts: dict[str, int] = {}
                for amendment in blocking:
                    status_counts[amendment.status] = status_counts.get(amendment.status, 0) + 1
                self.store.append_executor_event(
                    _event(
                        runtime_run_id=runtime_run_id,
                        event_type="runtime_requirement_amendment_wait_timeout",
                        stage="round",
                        round_no=round_no,
                        status="failed",
                        summary="next-round requirement amendment wait timed out",
                        payload={
                            "targetRoundNo": round_no,
                            "blockingAmendmentIds": [amendment.amendment_id for amendment in blocking],
                            "blockingStatuses": status_counts,
                        },
                        created_at=self.now(),
                    ),
                    executor_id=executor_id,
                    attempt_no=attempt_no,
                    run_status="running",
                )
                raise RuntimeControlError(
                    "runtime_requirement_amendment_extraction_timeout",
                    payload={
                        "targetRoundNo": round_no,
                        "blockingAmendmentIds": [amendment.amendment_id for amendment in blocking],
                        "blockingStatuses": status_counts,
                    },
                )
            time.sleep(self.boundary_wait_poll_seconds)

    def _request_lifecycle_command(
        self,
        *,
        runtime_run_id: str,
        command_type: str,
        requested_by: str | None,
        idempotency_key: str,
        allowed_run_statuses: set[str],
        requested_run_status: str,
        invalid_reason_code: str = "runtime_run_not_running",
    ) -> RuntimeCommand:
        existing = self.store.get_command_by_idempotency(runtime_run_id=runtime_run_id, idempotency_key=idempotency_key)
        if existing is not None:
            return existing
        run = self.store.get_run(runtime_run_id)
        self._reject_if_terminal_cancel_pending(runtime_run_id=runtime_run_id, command_type=command_type)
        duplicate = self._pending_lifecycle_command(runtime_run_id=runtime_run_id, command_type=command_type)
        if duplicate is not None:
            return duplicate
        if run.status not in allowed_run_statuses:
            raise RuntimeControlError(invalid_reason_code)
        conflict = self._pending_lifecycle_conflict(runtime_run_id=runtime_run_id, command_type=command_type)
        if conflict is not None:
            raise _command_conflict(conflict)
        requested_at = self.now()
        command = self._save_lifecycle_command(
            run=run,
            command_type=command_type,
            requested_by=requested_by,
            idempotency_key=idempotency_key,
            requested_at=requested_at,
        )
        self.store.update_run_status(
            runtime_run_id=runtime_run_id,
            status=requested_run_status,
            updated_at=requested_at,
        )
        self._append_command_event(
            run=self.store.get_run(runtime_run_id),
            event_type="runtime_command_accepted",
            command=command,
            created_at=requested_at,
        )
        return command

    def _save_lifecycle_command(
        self,
        *,
        run,
        command_type: str,
        requested_by: str | None,
        idempotency_key: str,
        requested_at: str,
    ) -> RuntimeCommand:
        return self.store.save_command(
            RuntimeCommand(
                command_id=self.command_id_factory(),
                runtime_run_id=run.runtime_run_id,
                command_type=command_type,
                payload={"effectiveAt": "next_safe_boundary"},
                status="accepted",
                conflict_group="lifecycle",
                target_round_no=run.current_round,
                idempotency_key=idempotency_key,
                requested_by=requested_by,
                requested_at=requested_at,
            )
        )

    def _append_command_event(
        self,
        *,
        run,
        event_type: str,
        command: RuntimeCommand,
        created_at: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        event_payload: dict[str, object] = {
            "commandId": command.command_id,
            "commandType": command.command_type,
        }
        if payload:
            event_payload.update(payload)
        self.store.append_event(
            _event(
                runtime_run_id=run.runtime_run_id,
                event_type=event_type,
                stage=run.current_stage,
                round_no=run.current_round,
                status="completed",
                summary=event_type.replace("_", " "),
                payload=event_payload,
                created_at=created_at,
            )
        )

    def _pending_lifecycle_command(self, *, runtime_run_id: str, command_type: str) -> RuntimeCommand | None:
        for command in self.store.list_commands(
            runtime_run_id=runtime_run_id,
            conflict_group="lifecycle",
            statuses=_PENDING_COMMAND_STATUSES,
        ):
            if command.command_type == command_type:
                return command
        return None

    def _pending_lifecycle_conflict(self, *, runtime_run_id: str, command_type: str) -> RuntimeCommand | None:
        for command in self.store.list_commands(
            runtime_run_id=runtime_run_id,
            conflict_group="lifecycle",
            statuses=_PENDING_COMMAND_STATUSES,
        ):
            if command.command_type != command_type:
                return command
        return None

    def _reject_if_terminal_cancel_pending(self, *, runtime_run_id: str, command_type: str) -> None:
        if command_type == "cancel":
            return
        for command in self.store.list_commands(
            runtime_run_id=runtime_run_id,
            conflict_group="lifecycle",
            statuses=_PENDING_COMMAND_STATUSES,
        ):
            if command.command_type == "cancel":
                raise _command_conflict(command)
        run = self.store.get_run(runtime_run_id)
        if run.status in _TERMINAL_RUN_STATUSES or run.status == "cancellation_requested":
            raise RuntimeControlError("runtime_command_conflict")

def _next_lifecycle_command(commands: list[RuntimeCommand]) -> RuntimeCommand | None:
    for command_type in ("cancel", "pause", "resume"):
        for command in commands:
            if command.command_type == command_type:
                return command
    return None


def _applied_run_status(command_type: str) -> str:
    if command_type == "pause":
        return "paused"
    if command_type == "cancel":
        return "cancelled"
    if command_type == "resume":
        return "running"
    return "running"


def _command_conflict(command: RuntimeCommand) -> RuntimeControlError:
    return RuntimeControlError(
        "runtime_command_conflict",
        payload={
            "conflictingCommandId": command.command_id,
            "conflictingCommandType": command.command_type,
            "conflictingCommandStatus": command.status,
        },
    )


def _sanitize_requirement_provenance(provenance: dict[str, object] | None) -> dict[str, object]:
    if not provenance:
        return {}
    safe: dict[str, object] = {}
    for key in ("originalUserText", "normalizedRequirementText", "sourceMessageId", "runtimeRunId"):
        value = provenance.get(key)
        if isinstance(value, str):
            safe[key] = _truncate_provenance_text(value)
    intent_decision = provenance.get("intentDecision")
    if isinstance(intent_decision, dict):
        for key, value in intent_decision.items():
            if str(key) == "intent" and isinstance(value, str):
                safe["intentDecision"] = {"intent": _truncate_provenance_text(value)}
                break
    return safe


def _truncate_provenance_text(value: str) -> str:
    if len(value) <= _PROVENANCE_STRING_MAX_CHARS:
        return value
    return value[:_PROVENANCE_STRING_MAX_CHARS]


def _amendment_result(
    amendment: RequirementAmendment,
    *,
    supersedes_amendment_id: str | None,
) -> NextRoundRequirementResult:
    if amendment.target_round_no is None or amendment.effective_boundary is None:
        raise RuntimeControlError("requirement_amendment_stale")
    return NextRoundRequirementResult(
        amendment_id=amendment.amendment_id,
        status=amendment.status,
        target_round_no=amendment.target_round_no,
        effective_boundary=amendment.effective_boundary,
        approved_requirement_revision_id=amendment.result_approved_requirement_revision_id,
        review_required=amendment.status == _NEEDS_REVIEW_STATUS,
        review_items=amendment.review_items or None,
        supersedes_amendment_id=supersedes_amendment_id,
    )


def _review_items(normalized: dict[str, object]) -> list[ReviewItem]:
    result: list[ReviewItem] = []
    for raw_item in _list_payload(normalized.get("reviewItems")):
        item = _string_key_dict(raw_item)
        if not item:
            continue
        candidate_section = item.get("candidateSection")
        review_item_id = item.get("reviewItemId")
        raw_text = item.get("rawText")
        candidate_text = item.get("candidateText")
        if not isinstance(review_item_id, str) or not review_item_id:
            raise RuntimeControlError(
                _INVALID_REVIEW_ITEM_PAYLOAD_REASON_CODE,
                payload={"field": "reviewItemId"},
            )
        if not isinstance(raw_text, str) or not raw_text:
            raise RuntimeControlError(
                _INVALID_REVIEW_ITEM_PAYLOAD_REASON_CODE,
                payload={"field": "rawText", "reviewItemId": review_item_id},
            )
        if not isinstance(candidate_text, str) or not candidate_text:
            raise RuntimeControlError(
                _INVALID_REVIEW_ITEM_PAYLOAD_REASON_CODE,
                payload={"field": "candidateText", "reviewItemId": review_item_id},
            )
        result.append(
            ReviewItem(
                review_item_id=review_item_id,
                raw_text=raw_text,
                candidate_text=candidate_text,
                candidate_section=str(candidate_section) if candidate_section else None,
                reason_code=str(item.get("reasonCode") or "requirement_amendment_ambiguous"),
            )
        )
    return result


def _review_item_payload(item: ReviewItem) -> dict[str, object]:
    payload: dict[str, object] = {
        "reviewItemId": item.review_item_id,
        "rawText": item.raw_text,
        "candidateText": item.candidate_text,
        "reasonCode": item.reason_code,
    }
    if item.candidate_section is not None:
        payload["candidateSection"] = item.candidate_section
    return payload


def _resolved_patch_from_review_items(operations: list[ReviewResolutionOperation]) -> dict[str, object]:
    additions: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    for operation in operations:
        if operation.op in _REJECT_REVIEW_OPS:
            rejected.append(
                {
                    "reviewItemId": operation.review_item_id,
                    "reasonCode": operation.reason_code or _NOT_A_REQUIREMENT_REASON_CODE,
                }
            )
            continue
        if operation.op not in _ALLOWED_REVIEW_OPS:
            raise RuntimeControlError(
                _INVALID_REVIEW_OPERATION_REASON_CODE,
                payload={"operation": operation.op},
            )
        text = (operation.text or "").strip()
        target_section = operation.target_section or "must_have_capabilities"
        if not text:
            raise RuntimeControlError(_REQUIREMENT_AMENDMENT_UNCLASSIFIABLE_REASON_CODE)
        additions.append(
            {
                "sectionId": target_section,
                "text": text,
                "source": "user_review_resolution",
                "reviewItemId": operation.review_item_id,
            }
        )
    return {"additions": additions, "reviewItems": [], "rejectedFragments": rejected}


def _event(
    *,
    runtime_run_id: str,
    event_type: str,
    stage: str,
    round_no: int | None,
    status: str,
    summary: str,
    payload: dict[str, object],
    created_at: str,
) -> RuntimeControlEventInput:
    return RuntimeControlEventInput(
        event_id=f"rtevt_{uuid4().hex}",
        runtime_run_id=runtime_run_id,
        event_type=event_type,
        stage=stage,
        round_no=round_no,
        source_id=None,
        status=status,
        summary=summary,
        payload=payload,
        workbench_event_global_seq=None,
        created_at=created_at,
    )


def _list_payload(value: object) -> list[object]:
    return list(value) if isinstance(value, list | tuple) else []


def _string_key_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
