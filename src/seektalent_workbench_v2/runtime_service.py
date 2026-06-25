from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from seektalent.config import AppSettings
from seektalent.models import RequirementSheet
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_runtime_control.models import RuntimeRunRecord
from seektalent_runtime_control.requirements import (
    ApprovedRequirementRevision,
    RequirementDraft,
    draft_from_requirement_sheet,
)
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_workbench_v2.agent_loop import WorkbenchV2RuntimeInput


REQUIREMENT_DRAFT_SOURCE = "workbench_v2_agent"
RUNTIME_INPUT_REQUIRED = "workbench_v2_runtime_input_required"
REQUIREMENT_EXTRACTOR_UNAVAILABLE = "workbench_v2_requirement_extractor_unavailable"
DEFAULT_SOURCE_IDS = ["liepin"]


class RequirementExtractor(Protocol):
    def extract_requirements(
        self,
        *,
        job_title: str,
        jd: str,
        notes: str | None,
        requirement_cache_scope: str,
    ) -> RequirementSheet: ...


class WorkbenchV2RuntimeService:
    def __init__(
        self,
        *,
        store: RuntimeControlStore,
        settings: AppSettings | None = None,
        runtime_factory: Callable[[], object] | None = None,
        requirement_extractor: object | None = None,
        executor: WorkflowRuntimeExecutor | None = None,
        draft_revision_id_factory: Callable[[], str] | None = None,
        approved_requirement_revision_id_factory: Callable[[], str] | None = None,
        runtime_run_id_factory: Callable[[], str] | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.runtime_factory = runtime_factory
        self.requirement_extractor = requirement_extractor
        self._runtime_executor = executor
        self.draft_revision_id_factory = draft_revision_id_factory or (lambda: _new_id("reqdraft"))
        self.approved_requirement_revision_id_factory = approved_requirement_revision_id_factory or (
            lambda: _new_id("reqapproved")
        )
        self.runtime_run_id_factory = runtime_run_id_factory
        self.now = now or _now_iso

    def extract_requirements(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput,
    ) -> RequirementDraft:
        job_title, jd_text, notes = _runtime_input_values(runtime_input)
        sheet = self._requirement_extractor().extract_requirements(
            job_title=job_title,
            jd=jd_text,
            notes=notes,
            requirement_cache_scope=conversation_id,
        )
        return draft_from_requirement_sheet(
            conversation_id=conversation_id,
            draft_revision_id=self.draft_revision_id_factory(),
            base_revision_id=None,
            requirement_sheet=sheet,
            source=REQUIREMENT_DRAFT_SOURCE,
            created_at=self.now(),
        )

    def start_run(
        self,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput | None,
        requirement_sheet: RequirementSheet,
    ) -> RuntimeRunRecord:
        job_title, jd_text, notes = _runtime_input_values(runtime_input)
        created_at = self.now()
        draft = draft_from_requirement_sheet(
            conversation_id=conversation_id,
            draft_revision_id=self.draft_revision_id_factory(),
            base_revision_id=None,
            requirement_sheet=requirement_sheet,
            source=REQUIREMENT_DRAFT_SOURCE,
            created_at=created_at,
        )
        approved = ApprovedRequirementRevision(
            approved_requirement_revision_id=self.approved_requirement_revision_id_factory(),
            draft_revision_id=draft.draft_revision_id,
            agent_conversation_id=conversation_id,
            requirement_sheet=requirement_sheet,
            selected_item_ids=_selected_item_ids(draft),
            deselected_item_ids=_deselected_item_ids(draft),
            created_at=created_at,
        )
        saved = self.store.save_approved_requirement(
            approved,
            idempotency_key=f"workbench-v2-runtime-approved:{approved.approved_requirement_revision_id}",
        )
        return self._executor().enqueue_workflow_run(
            conversation_id=conversation_id,
            workbench_session_id=None,
            approved_requirement=saved,
            job_title=job_title,
            jd_text=jd_text,
            notes=notes,
            source_ids=DEFAULT_SOURCE_IDS,
            start_idempotency_key=f"workbench-v2-runtime-start:{saved.approved_requirement_revision_id}",
        )

    def get_status(self, runtime_run_id: str) -> dict[str, str]:
        run = self.store.get_run(runtime_run_id)
        snapshot = self.store.get_snapshot(runtime_run_id=runtime_run_id)
        stage = snapshot.current_stage if snapshot is not None else run.current_stage
        return {
            "runtimeRunId": run.runtime_run_id,
            "status": run.status,
            "stage": stage,
            "summary": _status_summary(run.status),
        }

    def _requirement_extractor(self) -> RequirementExtractor:
        extractor = self.requirement_extractor
        if extractor is None and self.runtime_factory is not None:
            extractor = self.runtime_factory()
        if not callable(getattr(extractor, "extract_requirements", None)):
            raise RuntimeError(REQUIREMENT_EXTRACTOR_UNAVAILABLE)
        return extractor

    def _executor(self) -> WorkflowRuntimeExecutor:
        if self._runtime_executor is None:
            self._runtime_executor = WorkflowRuntimeExecutor(
                store=self.store,
                settings=self.settings,
                runtime_factory=self.runtime_factory,
                runtime_run_id_factory=self.runtime_run_id_factory,
                now=self.now,
            )
        return self._runtime_executor


def _runtime_input_values(runtime_input: WorkbenchV2RuntimeInput | None) -> tuple[str, str, str | None]:
    if runtime_input is None:
        raise ValueError(RUNTIME_INPUT_REQUIRED)
    job_title = _required_input_text(getattr(runtime_input, "jobTitle", None))
    jd_text = _required_input_text(getattr(runtime_input, "jd", None))
    notes = getattr(runtime_input, "notes", None)
    if isinstance(notes, str):
        notes = notes.strip() or None
    return job_title, jd_text, notes


def _required_input_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(RUNTIME_INPUT_REQUIRED)
    text = value.strip()
    if not text:
        raise ValueError(RUNTIME_INPUT_REQUIRED)
    return text


def _selected_item_ids(draft: RequirementDraft) -> list[str]:
    return [
        item.item_id
        for section in draft.sections
        for item in section.items
        if item.selected and item.status == "resolved"
    ]


def _deselected_item_ids(draft: RequirementDraft) -> list[str]:
    return [
        item.item_id
        for section in draft.sections
        for item in section.items
        if not item.selected or item.status in {"deleted", "moved", "rejected"}
    ]


def _status_summary(status: str) -> str:
    summaries = {
        "queued": "Run is queued and waiting to start.",
        "starting": "Run is starting.",
        "running": "Run is in progress.",
        "pause_requested": "Run pause has been requested.",
        "paused": "Run is paused.",
        "resume_requested": "Run resume has been requested.",
        "cancellation_requested": "Run cancellation has been requested.",
        "cancelled": "Run was cancelled.",
        "completed": "Run completed.",
        "failed": "Run failed.",
    }
    return summaries.get(status, f"Run status: {status}.")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")
