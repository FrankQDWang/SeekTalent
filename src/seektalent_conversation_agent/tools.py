from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.commands import RuntimeCommandService
from seektalent_runtime_control.detail import RuntimeDetailService
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_runtime_control.models import (
    RuntimeCommand,
    RuntimeControlEventPage,
    RuntimeDetailResponse,
    RuntimeFinalSummary,
    RuntimeRunRecord,
    RuntimeRunSnapshot,
)
from seektalent_runtime_control.requirements import (
    ApprovedRequirementRevision,
    DraftOperation,
    RequirementDraft,
    ReviewResolutionOperation,
)
from seektalent_runtime_control.service import RuntimeControlService
from seektalent_runtime_control.store import RuntimeControlStore


AGENT_RUNTIME_TOOL_NAMES = (
    "extract_requirements",
    "get_requirement_draft",
    "update_requirement_draft",
    "amend_requirement_draft_from_text",
    "resolve_requirement_review",
    "confirm_requirements",
    "start_workflow",
    "get_workflow_snapshot",
    "list_workflow_events",
    "request_pause",
    "request_cancel",
    "resume_workflow",
    "submit_next_round_requirement",
    "get_runtime_detail",
    "prepare_final_summary",
)


@dataclass(frozen=True)
class AgentToolAdapter:
    runtime_store: RuntimeControlStore | None = None
    requirement_service: RuntimeControlService | None = None
    command_service: RuntimeCommandService | None = None
    workflow_executor: WorkflowRuntimeExecutor | None = None
    detail_service: RuntimeDetailService | None = None

    def extract_requirements(
        self,
        *,
        conversation_id: str,
        job_title: str | None,
        jd_text: str,
        notes: str | None,
        source_ids: list[str],
        idempotency_key: str,
    ) -> RequirementDraft:
        return self._require_requirement_service().extract_requirements(
            conversation_id=conversation_id,
            job_title=job_title,
            jd_text=jd_text,
            notes=notes,
            source_ids=source_ids,
            idempotency_key=idempotency_key,
        )

    def get_requirement_draft(
        self,
        *,
        conversation_id: str,
        draft_revision_id: str | None = None,
    ) -> RequirementDraft:
        return self._require_requirement_service().get_requirement_draft(
            conversation_id=conversation_id,
            draft_revision_id=draft_revision_id,
        )

    def update_requirement_draft(
        self,
        *,
        draft_revision_id: str,
        base_revision_id: str,
        operations: list[DraftOperation],
        idempotency_key: str,
    ) -> RequirementDraft:
        return self._require_requirement_service().update_requirement_draft(
            draft_revision_id=draft_revision_id,
            base_revision_id=base_revision_id,
            operations=operations,
            idempotency_key=idempotency_key,
        )

    def amend_requirement_draft_from_text(
        self,
        *,
        draft_revision_id: str,
        base_revision_id: str,
        text: str,
        target_section_hint: str | None,
        idempotency_key: str,
    ) -> RequirementDraft:
        return self._require_requirement_service().amend_requirement_draft_from_text(
            draft_revision_id=draft_revision_id,
            base_revision_id=base_revision_id,
            text=text,
            target_section_hint=target_section_hint,
            idempotency_key=idempotency_key,
        )

    def resolve_requirement_review(
        self,
        *,
        draft_revision_id: str,
        base_revision_id: str,
        amendment_id: str,
        operations: list[ReviewResolutionOperation],
        idempotency_key: str,
    ) -> RequirementDraft:
        return self._require_requirement_service().resolve_requirement_review(
            draft_revision_id=draft_revision_id,
            base_revision_id=base_revision_id,
            amendment_id=amendment_id,
            operations=operations,
            idempotency_key=idempotency_key,
        )

    def confirm_requirements(
        self,
        *,
        draft_revision_id: str,
        base_revision_id: str,
        idempotency_key: str,
    ) -> ApprovedRequirementRevision:
        return self._require_requirement_service().confirm_requirements(
            draft_revision_id=draft_revision_id,
            base_revision_id=base_revision_id,
            idempotency_key=idempotency_key,
        )

    def start_workflow(
        self,
        *,
        conversation_id: str | None,
        workbench_session_id: str | None,
        approved_requirement: ApprovedRequirementRevision,
        job_title: str,
        jd_text: str,
        notes: str | None,
        source_ids: Sequence[str],
        run_intent_id: str | None = None,
        start_idempotency_key: str | None = None,
    ) -> RuntimeRunRecord:
        executor = self.workflow_executor
        if executor is None:
            raise RuntimeControlError("runtime_workflow_executor_required")
        return executor.enqueue_workflow_run(
            conversation_id=conversation_id,
            workbench_session_id=workbench_session_id,
            approved_requirement=approved_requirement,
            job_title=job_title,
            jd_text=jd_text,
            notes=notes,
            source_ids=source_ids,
            run_intent_id=run_intent_id,
            start_idempotency_key=start_idempotency_key,
        )

    def get_workflow_snapshot(self, *, runtime_run_id: str) -> RuntimeRunSnapshot:
        if self.runtime_store is None:
            raise RuntimeControlError("runtime_control_store_required")
        snapshot = self.runtime_store.get_snapshot(runtime_run_id=runtime_run_id)
        if snapshot is None:
            raise RuntimeControlError("runtime_snapshot_not_found")
        return snapshot

    def list_workflow_events(self, *, runtime_run_id: str, after_seq: int, limit: int) -> RuntimeControlEventPage:
        if self.runtime_store is None:
            raise RuntimeControlError("runtime_control_store_required")
        return self.runtime_store.list_events(runtime_run_id=runtime_run_id, after_seq=after_seq, limit=limit)

    def request_pause(self, *, runtime_run_id: str, requested_by: str | None, idempotency_key: str) -> RuntimeCommand:
        return self._require_command_service().request_pause(
            runtime_run_id=runtime_run_id,
            requested_by=requested_by,
            idempotency_key=idempotency_key,
        )

    def request_cancel(self, *, runtime_run_id: str, requested_by: str | None, idempotency_key: str) -> RuntimeCommand:
        return self._require_command_service().request_cancel(
            runtime_run_id=runtime_run_id,
            requested_by=requested_by,
            idempotency_key=idempotency_key,
        )

    def resume_workflow(self, *, runtime_run_id: str, requested_by: str | None, idempotency_key: str) -> RuntimeCommand:
        return self._require_command_service().resume_workflow(
            runtime_run_id=runtime_run_id,
            requested_by=requested_by,
            idempotency_key=idempotency_key,
        )

    def submit_next_round_requirement(
        self,
        *,
        runtime_run_id: str,
        text: str,
        target_section_hint: str | None,
        idempotency_key: str,
        replace_amendment_id: str | None = None,
    ) -> object:
        return self._require_command_service().submit_next_round_requirement(
            runtime_run_id=runtime_run_id,
            text=text,
            target_section_hint=target_section_hint,
            idempotency_key=idempotency_key,
            replace_amendment_id=replace_amendment_id,
        )

    def get_runtime_detail(
        self,
        *,
        runtime_run_id: str,
        kind: str,
        round_no: int | None = None,
        event_id: str | None = None,
        command_id: str | None = None,
        checkpoint_id: str | None = None,
        include_artifacts: bool = False,
    ) -> RuntimeDetailResponse:
        detail = self.detail_service
        if detail is None:
            raise RuntimeControlError("runtime_detail_service_required")
        return detail.get_runtime_detail(
            runtime_run_id=runtime_run_id,
            kind=kind,
            round_no=round_no,
            event_id=event_id,
            command_id=command_id,
            checkpoint_id=checkpoint_id,
            include_artifacts=include_artifacts,
        )

    def prepare_final_summary(
        self,
        *,
        runtime_run_id: str,
        user_instruction: str | None,
        source_snapshot_event_seq: int,
        idempotency_key: str,
    ) -> RuntimeFinalSummary:
        detail = self.detail_service
        if detail is None:
            raise RuntimeControlError("runtime_detail_service_required")
        return detail.prepare_final_summary(
            runtime_run_id=runtime_run_id,
            user_instruction=user_instruction,
            source_snapshot_event_seq=source_snapshot_event_seq,
            idempotency_key=idempotency_key,
        )

    def _require_requirement_service(self) -> RuntimeControlService:
        if self.requirement_service is None:
            raise RuntimeControlError("runtime_requirement_service_required")
        return self.requirement_service

    def _require_command_service(self) -> RuntimeCommandService:
        if self.command_service is None:
            raise RuntimeControlError("runtime_command_service_required")
        return self.command_service
