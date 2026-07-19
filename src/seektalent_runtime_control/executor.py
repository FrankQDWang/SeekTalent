from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from inspect import Parameter, signature
from typing import Protocol, runtime_checkable
from uuid import uuid4

from seektalent.config import AppSettings
from seektalent.progress import ProgressEvent
from seektalent.runtime.orchestrator import WorkflowRuntime
from seektalent_runtime_control.commands import RuntimeCommandService
from seektalent_runtime_control.event_sink import RuntimeControlEventSink, RuntimeEventSink
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import (
    RunKind,
    RuntimeCheckpoint,
    RuntimeControlEventInput,
    RuntimeRunRecord,
    RuntimeRunSnapshot,
)
from seektalent_runtime_control.requirements import ApprovedRequirementRevision
from seektalent_runtime_control.store import RuntimeCheckpointLoadFailure, RuntimeControlStore

SourceContext = dict[str, str | int | bool | None]
SourceContextProvider = Callable[[Sequence[str], AppSettings | None], SourceContext | None]


@runtime_checkable
class RuntimeLike(Protocol):
    async def run_async(self, **kwargs: object) -> object: ...


class WorkflowRuntimeExecutor:
    def __init__(
        self,
        *,
        store: RuntimeControlStore,
        settings: AppSettings | None = None,
        runtime_factory: Callable[[], object] | None = None,
        runtime_run_id_factory: Callable[[], str] | None = None,
        executor_id_factory: Callable[[], str] | None = None,
        checkpoint_id_factory: Callable[[], str] | None = None,
        now: Callable[[], str] | None = None,
        lease_seconds: int = 60,
        event_sink: RuntimeEventSink | None = None,
        command_service: RuntimeCommandService | None = None,
        source_context_provider: SourceContextProvider | None = None,
    ) -> None:
        if runtime_factory is None and settings is None:
            raise ValueError("settings is required when runtime_factory is not provided")
        self.store = store
        self.settings = settings
        self.runtime_factory: Callable[[], object] = runtime_factory or (lambda: _build_default_runtime(settings))
        self.runtime_run_id_factory = runtime_run_id_factory or (lambda: f"rtrun_{uuid4().hex}")
        self.executor_id_factory = executor_id_factory or (lambda: f"rtexec_{uuid4().hex}")
        self.checkpoint_id_factory = checkpoint_id_factory or (lambda: f"rtcheckpoint_{uuid4().hex}")
        self.now = now or _now
        self.lease_seconds = lease_seconds
        self.event_sink = event_sink or RuntimeControlEventSink(store)
        self.command_service = command_service
        self.source_context_provider = source_context_provider

    async def start_workflow(
        self,
        *,
        conversation_id: str | None,
        workbench_session_id: str | None,
        approved_requirement: ApprovedRequirementRevision,
        job_title: str,
        jd_text: str,
        notes: str | None,
        source_ids: Sequence[str],
    ) -> RuntimeRunRecord:
        run = self.enqueue_workflow_run(
            conversation_id=conversation_id,
            workbench_session_id=workbench_session_id,
            approved_requirement=approved_requirement,
            job_title=job_title,
            jd_text=jd_text,
            notes=notes,
            source_ids=source_ids,
        )
        executor_id = self.executor_id_factory()
        claimed_at = self.now()
        claim = self.store.claim_next_runnable_run(
            executor_id=executor_id,
            claimed_at=claimed_at,
            lease_expires_at=_plus_seconds(claimed_at, self.lease_seconds),
            runtime_run_id=run.runtime_run_id,
        )
        if claim is None:
            return self.store.get_run(run.runtime_run_id)
        return await self.execute_claimed_run(
            runtime_run_id=claim.runtime_run.runtime_run_id,
            executor_id=claim.lease.executor_id,
            attempt_no=claim.lease.attempt_no,
            job_title=job_title,
            jd_text=jd_text,
            notes=notes,
            source_ids=source_ids,
            approved_requirement=approved_requirement,
        )

    def enqueue_workflow_run(
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
        run_kind: str = "primary",
    ) -> RuntimeRunRecord:
        created_at = self.now()
        runtime_run_id = self.runtime_run_id_factory()
        run_kind_value = _run_kind(run_kind)
        source_context = self._source_context(source_ids)
        intent_id = run_intent_id or _default_run_intent_id(
            conversation_id=conversation_id,
            workbench_session_id=workbench_session_id,
            approved_requirement_revision_id=approved_requirement.approved_requirement_revision_id,
            run_kind=run_kind_value,
        )
        queued_at = self.now()
        workflow_input: dict[str, object] = {
            "jobTitle": job_title,
            "jdText": jd_text,
            "notes": notes or "",
            "sourceIds": list(source_ids),
        }
        if source_context is not None:
            workflow_input["sourceContext"] = source_context
        run = RuntimeRunRecord(
            runtime_run_id=runtime_run_id,
            run_intent_id=intent_id,
            start_idempotency_key=start_idempotency_key or intent_id,
            run_kind=run_kind_value,
            agent_conversation_id=conversation_id,
            workbench_session_id=workbench_session_id,
            approved_requirement_revision_id=approved_requirement.approved_requirement_revision_id,
            status="queued",
            current_stage="queued",
            current_round=None,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=list(source_ids),
            stop_reason_code=None,
            created_at=created_at,
            updated_at=created_at,
            completed_at=None,
        )
        return self.store.accept_run(
            run,
            initial_event=_event(
                runtime_run_id=runtime_run_id,
                event_type="runtime_run_queued",
                stage="queued",
                status="queued",
                summary="workflow run queued",
                payload={
                    "runIntentId": intent_id,
                    "runKind": run_kind_value,
                    "sourceIds": list(source_ids),
                },
                created_at=queued_at,
                idempotency_key=f"runtime-run-queued:{runtime_run_id}",
            ),
            snapshot=RuntimeRunSnapshot(
                runtime_run_id=runtime_run_id,
                status="queued",
                current_stage="queued",
                current_round=None,
                latest_event_seq=0,
                snapshot={"workflowInput": workflow_input},
                updated_at=queued_at,
            ),
        )

    async def execute_claimed_run(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int,
        job_title: str | None = None,
        jd_text: str | None = None,
        notes: str | None = None,
        source_ids: Sequence[str] | None = None,
        approved_requirement: ApprovedRequirementRevision | None = None,
    ) -> RuntimeRunRecord:
        run = self.store.get_run(runtime_run_id)
        approved = approved_requirement or self.store.get_approved_requirement(run.approved_requirement_revision_id)
        snapshot = self.store.get_snapshot(runtime_run_id=runtime_run_id)
        workflow_input = _workflow_input(snapshot)
        claim_reason = _text(_snapshot_payload(snapshot).get("claimReason"))
        resolved_source_ids = list(source_ids) if source_ids is not None else list(run.source_ids)
        resolved_job_title = job_title or _text(workflow_input.get("jobTitle")) or approved.requirement_sheet.job_title
        resolved_jd_text = jd_text if jd_text is not None else _text(workflow_input.get("jdText")) or ""
        resolved_notes = notes if notes is not None else _text(workflow_input.get("notes")) or ""
        resolved_source_context = _source_context_from_workflow_input(workflow_input) or self._source_context(
            resolved_source_ids
        )

        self.store.append_executor_event(
            _event(
                runtime_run_id=runtime_run_id,
                event_type="runtime_executor_starting",
                stage="startup",
                status="pending",
                summary="executor starting",
                payload={"executorId": executor_id},
                created_at=self.now(),
            ),
            executor_id=executor_id,
            attempt_no=attempt_no,
            run_status="starting",
        )
        runtime_started = False
        runtime = self.runtime_factory()
        if not isinstance(runtime, RuntimeLike):
            raise RuntimeControlError("runtime_adapter_invalid")
        resume_checkpoint = self._load_resume_checkpoint(
            runtime_run_id=run.runtime_run_id,
            executor_id=executor_id,
            attempt_no=attempt_no,
            claim_reason=claim_reason,
        )

        def runtime_start_callback(workflow_runtime_run_id: str) -> None:
            nonlocal runtime_started
            runtime_started = True
            self.store.append_executor_event(
                _event(
                    runtime_run_id=run.runtime_run_id,
                    event_type="runtime_executor_started",
                    stage="startup",
                    status="completed",
                    summary="executor started",
                    payload={"executorId": executor_id, "workflowRuntimeRunId": workflow_runtime_run_id},
                    created_at=self.now(),
                ),
                executor_id=executor_id,
                attempt_no=attempt_no,
                run_status="running",
            )

        def progress_callback(progress: ProgressEvent) -> None:
            self.event_sink.append_progress(
                progress,
                runtime_run_id=run.runtime_run_id,
                executor_id=executor_id,
                attempt_no=attempt_no,
                now=self.now(),
            )

        def runtime_checkpoint_callback(artifacts: object) -> None:
            checkpoint = RuntimeCheckpoint(
                checkpoint_id=self.checkpoint_id_factory(),
                runtime_run_id=run.runtime_run_id,
                stage="round",
                round_no=None,
                safe_boundary="runtime_candidate_checkpoint",
                run_state=_run_state_payload(getattr(artifacts, "run_state", {})),
                source_plan={"sourceIds": resolved_source_ids},
                pending_commands=[],
                artifact_manifest_ref=None,
                schema_version="runtime-control-checkpoint/v1",
                created_at=self.now(),
            )
            self.store.write_checkpoint(checkpoint, executor_id=executor_id, attempt_no=attempt_no)
            self.store.append_executor_event(
                _event(
                    runtime_run_id=run.runtime_run_id,
                    event_type="runtime_checkpoint_written",
                    stage=checkpoint.stage,
                    status="completed",
                    summary="checkpoint written",
                    payload={"checkpointId": checkpoint.checkpoint_id},
                    created_at=checkpoint.created_at,
                    round_no=checkpoint.round_no,
                ),
                executor_id=executor_id,
                attempt_no=attempt_no,
                run_status="running",
                latest_checkpoint_id=checkpoint.checkpoint_id,
            )

        def runtime_round_boundary_callback(round_no: int) -> object | None:
            nonlocal approved
            if self.command_service is None:
                return None
            self.command_service.apply_next_round_requirements_at_boundary(
                runtime_run_id=run.runtime_run_id,
                executor_id=executor_id,
                attempt_no=attempt_no,
                round_no=round_no,
            )
            current_run = self.store.get_run(run.runtime_run_id)
            if current_run.approved_requirement_revision_id == approved.approved_requirement_revision_id:
                return None
            approved = self.store.get_approved_requirement(current_run.approved_requirement_revision_id)
            return approved.requirement_sheet

        try:
            runtime_kwargs: dict[str, object] = {
                "job_title": resolved_job_title,
                "jd": resolved_jd_text,
                "notes": resolved_notes,
                "source_kinds": resolved_source_ids,
                "progress_callback": progress_callback,
                "runtime_start_callback": runtime_start_callback,
                "runtime_checkpoint_callback": runtime_checkpoint_callback,
                "approved_requirement_sheet": approved.requirement_sheet,
            }
            if resolved_source_context is not None:
                runtime_kwargs["source_context"] = resolved_source_context
            if _runtime_accepts_round_boundary_callback(runtime):
                runtime_kwargs["runtime_round_boundary_callback"] = runtime_round_boundary_callback
            if resume_checkpoint is not None and _runtime_accepts_resume_context(runtime):
                runtime_kwargs["resume_checkpoint"] = resume_checkpoint.model_dump(mode="json")
                runtime_kwargs["resume_run_state"] = dict(resume_checkpoint.run_state)
            await runtime.run_async(**runtime_kwargs)
        except (RuntimeError, ValueError, OSError) as exc:
            reason_code = "runtime_run_failed" if runtime_started else "runtime_executor_start_failed"
            self.store.append_executor_event(
                _event(
                    runtime_run_id=run.runtime_run_id,
                    event_type=reason_code,
                    stage="runtime",
                    status="failed",
                    summary=str(exc),
                    payload={
                        "reasonCode": reason_code,
                        "exceptionType": type(exc).__name__,
                        "message": str(exc),
                    },
                    created_at=self.now(),
                ),
                executor_id=executor_id,
                attempt_no=attempt_no,
                run_status="failed",
                stop_reason_code=reason_code,
                completed_at=self.now(),
            )
            self.store.release_executor_lease(
                runtime_run_id=run.runtime_run_id,
                executor_id=executor_id,
                attempt_no=attempt_no,
                released_at=self.now(),
                status="failed",
                reason_code=reason_code,
            )
            raise

        completed_at = self.now()
        self.store.append_executor_event(
            _event(
                runtime_run_id=run.runtime_run_id,
                event_type="runtime_run_completed",
                stage="finalization",
                status="completed",
                summary="run completed",
                payload={},
                created_at=completed_at,
            ),
            executor_id=executor_id,
            attempt_no=attempt_no,
            run_status="completed",
            completed_at=completed_at,
        )
        self.store.release_executor_lease(
            runtime_run_id=run.runtime_run_id,
            executor_id=executor_id,
            attempt_no=attempt_no,
            released_at=self.now(),
        )
        return self.store.get_run(run.runtime_run_id)

    def _source_context(self, source_ids: Sequence[str]) -> SourceContext | None:
        if self.source_context_provider is not None:
            provided = self.source_context_provider(source_ids, self.settings)
            if provided is not None:
                return provided
        return _default_source_context(source_ids=source_ids, settings=self.settings)

    def _load_resume_checkpoint(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int,
        claim_reason: str | None,
    ) -> RuntimeCheckpoint | None:
        if claim_reason != "resume_requested":
            return None
        checkpoint = self.store.get_latest_recoverable_checkpoint(runtime_run_id=runtime_run_id)
        resumed_at = self.now()
        if isinstance(checkpoint, RuntimeCheckpoint):
            self.store.append_executor_event(
                _event(
                    runtime_run_id=runtime_run_id,
                    event_type="runtime_resumed",
                    stage=checkpoint.stage,
                    status="completed",
                    summary="runtime resumed from checkpoint",
                    payload={
                        "checkpointId": checkpoint.checkpoint_id,
                        "safeBoundary": checkpoint.safe_boundary,
                    },
                    created_at=resumed_at,
                    round_no=checkpoint.round_no,
                ),
                executor_id=executor_id,
                attempt_no=attempt_no,
                run_status="running",
                latest_checkpoint_id=checkpoint.checkpoint_id,
            )
            return checkpoint
        reason_code = (
            checkpoint.reason_code
            if isinstance(checkpoint, RuntimeCheckpointLoadFailure)
            else "runtime_resume_checkpoint_missing"
        )
        self.store.append_executor_event(
            _event(
                runtime_run_id=runtime_run_id,
                event_type="runtime_resume_failed",
                stage="resume",
                status="failed",
                summary="runtime resume checkpoint unavailable",
                payload={"reasonCode": reason_code},
                created_at=resumed_at,
            ),
            executor_id=executor_id,
            attempt_no=attempt_no,
            run_status="failed",
            stop_reason_code=reason_code,
            completed_at=resumed_at,
        )
        self.store.release_executor_lease(
            runtime_run_id=runtime_run_id,
            executor_id=executor_id,
            attempt_no=attempt_no,
            released_at=self.now(),
            status="failed",
            reason_code=reason_code,
        )
        raise RuntimeControlError(reason_code)


def _build_default_runtime(settings: AppSettings | None) -> WorkflowRuntime:
    if settings is None:
        raise ValueError("settings is required")
    from seektalent.source_adapters import build_source_enabled_runtime

    return build_source_enabled_runtime(settings)


def _event(
    *,
    runtime_run_id: str,
    event_type: str,
    stage: str,
    status: str,
    summary: str,
    payload: dict[str, object],
    created_at: str,
    round_no: int | None = None,
    idempotency_key: str | None = None,
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
        idempotency_key=idempotency_key,
        workbench_event_global_seq=None,
        created_at=created_at,
    )


def _run_kind(value: str) -> RunKind:
    if value == "primary":
        return "primary"
    if value == "rerun":
        return "rerun"
    if value == "fork":
        return "fork"
    raise RuntimeControlError("runtime_run_kind_invalid")


def _default_run_intent_id(
    *,
    conversation_id: str | None,
    workbench_session_id: str | None,
    approved_requirement_revision_id: str,
    run_kind: RunKind,
) -> str:
    owner = conversation_id or workbench_session_id or "standalone"
    return f"workflow:{owner}:{approved_requirement_revision_id}:{run_kind}"


def _workflow_input(snapshot: object) -> dict[str, object]:
    value = _snapshot_payload(snapshot).get("workflowInput")
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _source_context_from_workflow_input(workflow_input: dict[str, object]) -> SourceContext | None:
    value = workflow_input.get("sourceContext")
    if not isinstance(value, dict):
        return None
    context = {
        key: item
        for key, item in value.items()
        if isinstance(key, str) and (item is None or isinstance(item, (str, int, bool)))
    }
    return context or None


def _default_source_context(
    *,
    source_ids: Sequence[str],
    settings: AppSettings | None,
) -> SourceContext | None:
    if "liepin" not in {str(source_id) for source_id in source_ids}:
        return None
    worker_mode = str(getattr(settings, "liepin_worker_mode", "") or "")
    context: dict[str, str | int | bool | None] = {
        "actor_id": "local",
        "connection_id": "liepin-opencli",
        "provider_account_hash": "liepin-opencli-local",
        "tenant_id": "local",
        "workspace_id": "default",
    }
    if worker_mode:
        context["backend_mode"] = worker_mode
    return context


def _snapshot_payload(snapshot: object) -> dict[str, object]:
    if not isinstance(snapshot, RuntimeRunSnapshot):
        return {}
    return dict(snapshot.snapshot)


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _runtime_accepts_resume_context(runtime: object) -> bool:
    if getattr(runtime, "supports_resume_context", False) is True:
        return True
    run_async = getattr(runtime, "run_async", None)
    if not callable(run_async):
        return False
    parameters = signature(run_async).parameters
    if "resume_checkpoint" in parameters and "resume_run_state" in parameters:
        return True
    if getattr(runtime, "supports_resume_context", None) is False:
        return False
    return any(parameter.kind == Parameter.VAR_KEYWORD for parameter in parameters.values())


def _runtime_accepts_round_boundary_callback(runtime: object) -> bool:
    run_async = getattr(runtime, "run_async", None)
    if not callable(run_async):
        return False
    parameters = signature(run_async).parameters
    if "runtime_round_boundary_callback" in parameters:
        return True
    return any(parameter.kind == Parameter.VAR_KEYWORD for parameter in parameters.values())


def _run_state_payload(run_state: object) -> dict[str, object]:
    if isinstance(run_state, dict):
        return _string_key_dict(run_state)
    model_dump = getattr(run_state, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json")
        return _string_key_dict(payload)
    values = vars(run_state) if hasattr(run_state, "__dict__") else {}
    return _string_key_dict(values)


def _plus_seconds(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=seconds)).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _string_key_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}
