from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeCheckpoint, RuntimeCommand, RuntimeControlEventInput
from seektalent_runtime_control.normalizer import DefaultRequirementNormalizer, apply_next_round_patch
from seektalent_runtime_control.requirements import ApprovedRequirementRevision, RequirementAmendment, ReviewItem, ReviewResolutionOperation
from seektalent_runtime_control.store import RuntimeControlStore


_PENDING_COMMAND_STATUSES = {"accepted", "pending_safe_boundary"}
_TERMINAL_RUN_STATUSES = {"cancelled", "completed", "failed"}


class NextRoundRequirementNormalizer(Protocol):
    def normalize_next_round_requirement_text(
        self,
        *,
        text: str,
        target_section_hint: str | None,
        current_requirement: ApprovedRequirementRevision,
    ) -> dict[str, object]: ...


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
        requirement_normalizer: NextRoundRequirementNormalizer | None = None,
        command_id_factory: Callable[[], str] | None = None,
        amendment_id_factory: Callable[[], str] | None = None,
        approved_requirement_id_factory: Callable[[], str] | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.requirement_normalizer = requirement_normalizer or DefaultRequirementNormalizer()
        self.command_id_factory = command_id_factory or (lambda: f"rtcmd_{uuid4().hex}")
        self.amendment_id_factory = amendment_id_factory or (lambda: f"reqamend_{uuid4().hex}")
        self.approved_requirement_id_factory = approved_requirement_id_factory or (lambda: f"reqapproved_{uuid4().hex}")
        self.now = now or _now

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
            self.store.write_checkpoint(checkpoint, executor_id=executor_id)
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
                run_status=target_status,
                completed_at=applied_at if command.command_type == "cancel" else None,
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
    ) -> NextRoundRequirementResult:
        run = self.store.get_run(runtime_run_id)
        self._reject_if_terminal_cancel_pending(runtime_run_id=runtime_run_id, command_type="apply_next_round_requirement")
        existing = self.store.get_requirement_amendment_by_idempotency(
            conversation_id=run.agent_conversation_id or runtime_run_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return _amendment_result(existing, supersedes_amendment_id=None)
        current = self.store.get_approved_requirement(run.approved_requirement_revision_id)
        normalized = self.requirement_normalizer.normalize_next_round_requirement_text(
            text=text,
            target_section_hint=target_section_hint,
            current_requirement=current,
        )
        target_round_no = self._next_unlocked_round(runtime_run_id=runtime_run_id, after_round=run.current_round or 0)
        amendment_id = self.amendment_id_factory()
        review_items = _review_items(normalized)
        if review_items:
            amendment = RequirementAmendment(
                amendment_id=amendment_id,
                agent_conversation_id=run.agent_conversation_id or runtime_run_id,
                runtime_run_id=runtime_run_id,
                base_approved_requirement_revision_id=current.approved_requirement_revision_id,
                target_round_no=target_round_no,
                effective_boundary="before_round_controller",
                input_text=text,
                target_section_hint=target_section_hint,
                status="needs_review",
                normalized_patch=dict(normalized),
                rejected_fragments=_list_payload(normalized.get("rejectedFragments")),
                review_items=review_items,
                idempotency_key=idempotency_key,
                created_at=self.now(),
            )
            self.store.save_requirement_amendment(amendment)
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
                    status="needs_review",
                    summary="next-round requirement needs review",
                    payload={
                        "amendmentId": amendment.amendment_id,
                        "targetRoundNo": target_round_no,
                        "reviewItems": [item.model_dump(mode="json") for item in review_items],
                    },
                    created_at=amendment.created_at,
                )
            )
            return _amendment_result(amendment, supersedes_amendment_id=replace_amendment_id)
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
        amendment = RequirementAmendment(
            amendment_id=amendment_id,
            agent_conversation_id=run.agent_conversation_id or runtime_run_id,
            runtime_run_id=runtime_run_id,
            base_approved_requirement_revision_id=current.approved_requirement_revision_id,
            result_approved_requirement_revision_id=approved.approved_requirement_revision_id,
            target_round_no=target_round_no,
            effective_boundary="before_round_controller",
            input_text=text,
            target_section_hint=target_section_hint,
            status="pending_target_round",
            normalized_patch=dict(normalized),
            rejected_fragments=_list_payload(normalized.get("rejectedFragments")),
            review_items=[],
            idempotency_key=idempotency_key,
            created_at=approved.created_at,
        )
        self.store.save_requirement_amendment(amendment)
        if replace_amendment_id is not None:
            self.store.update_requirement_amendment_status(
                amendment_id=replace_amendment_id,
                status="superseded",
                superseded_by_amendment_id=amendment.amendment_id,
                resolved_at=approved.created_at,
            )
            self.store.append_event(
                _event(
                    runtime_run_id=runtime_run_id,
                    event_type="runtime_next_round_requirement_superseded",
                    stage=run.current_stage,
                    round_no=run.current_round,
                    status="completed",
                    summary="next-round requirement superseded",
                    payload={"amendmentId": replace_amendment_id, "supersededByAmendmentId": amendment.amendment_id},
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
        return _amendment_result(amendment, supersedes_amendment_id=replace_amendment_id)

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
        if amendment.status != "needs_review":
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
        target_round_no = self._next_unlocked_round(
            runtime_run_id=runtime_run_id,
            after_round=(amendment.target_round_no or run.current_round or 0) - 1,
        )
        resolved = self.store.resolve_runtime_requirement_amendment(
            amendment_id=amendment.amendment_id,
            status="pending_target_round",
            target_round_no=target_round_no,
            result_approved_requirement_revision_id=approved.approved_requirement_revision_id,
            resolved_patch=resolved_patch,
            resolved_at=approved.created_at,
        )
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
        round_no: int,
    ) -> list[RequirementAmendment]:
        pending = self.store.list_runtime_requirement_amendments(
            runtime_run_id=runtime_run_id,
            target_round_no=round_no,
            statuses={"pending_target_round"},
        )
        applied: list[RequirementAmendment] = []
        for amendment in pending:
            applied_at = self.now()
            event = self.store.append_executor_event(
                _event(
                    runtime_run_id=runtime_run_id,
                    event_type="runtime_next_round_requirement_applied",
                    stage="round",
                    round_no=round_no,
                    status="completed",
                    summary="next-round requirement applied",
                    payload={"amendmentId": amendment.amendment_id},
                    created_at=applied_at,
                ),
                executor_id=executor_id,
                run_status="running",
            )
            updated = self.store.update_requirement_amendment_status(
                amendment_id=amendment.amendment_id,
                status="applied",
                applied_event_id=event.event_id,
                resolved_at=applied_at,
            )
            if amendment.result_approved_requirement_revision_id is not None:
                self.store.activate_run_requirement_revision(
                    runtime_run_id=runtime_run_id,
                    approved_requirement_revision_id=amendment.result_approved_requirement_revision_id,
                    updated_at=applied_at,
                )
                self.store.append_executor_event(
                    _event(
                        runtime_run_id=runtime_run_id,
                        event_type="runtime_requirement_revision_activated",
                        stage="round",
                        round_no=round_no,
                        status="completed",
                        summary="requirement revision activated",
                        payload={
                            "amendmentId": amendment.amendment_id,
                            "approvedRequirementRevisionId": amendment.result_approved_requirement_revision_id,
                        },
                        created_at=applied_at,
                    ),
                    executor_id=executor_id,
                    run_status="running",
                )
            applied.append(updated)
        return applied

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

    def _next_unlocked_round(self, *, runtime_run_id: str, after_round: int) -> int:
        target = after_round + 1
        while self.store.has_event(
            runtime_run_id=runtime_run_id,
            event_type="runtime_round_input_locked",
            round_no=target,
        ):
            target += 1
        return target


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
        review_required=amendment.status == "needs_review",
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
        result.append(
            ReviewItem(
                review_item_id=str(item.get("reviewItemId") or ""),
                raw_text=str(item.get("rawText") or ""),
                candidate_text=str(item.get("candidateText") or ""),
                candidate_section=str(candidate_section) if candidate_section else None,
                reason_code=str(item.get("reasonCode") or "requirement_amendment_ambiguous"),
            )
        )
    return result


def _resolved_patch_from_review_items(operations: list[ReviewResolutionOperation]) -> dict[str, object]:
    additions: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    for operation in operations:
        if operation.op in {"reject_candidate", "reject_fragment"}:
            rejected.append(
                {
                    "reviewItemId": operation.review_item_id,
                    "reasonCode": operation.reason_code or "not_a_requirement",
                }
            )
            continue
        text = (operation.text or "").strip()
        target_section = operation.target_section or "must_have_capabilities"
        if not text:
            raise RuntimeControlError("requirement_amendment_unclassifiable")
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
