from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from seektalent.config import AppSettings
from seektalent.progress import ProgressEvent
from seektalent.runtime.orchestrator import WorkflowRuntime
from seektalent_runtime_control.models import RuntimeCheckpoint, RuntimeControlEventInput, RuntimeRunRecord
from seektalent_runtime_control.requirements import ApprovedRequirementRevision
from seektalent_runtime_control.store import RuntimeControlStore


class RuntimeLike(Protocol):
    async def run_async(self, **kwargs: object) -> object: ...


class WorkflowRuntimeExecutor:
    def __init__(
        self,
        *,
        store: RuntimeControlStore,
        settings: AppSettings | None = None,
        runtime_factory: Callable[[], RuntimeLike] | None = None,
        runtime_run_id_factory: Callable[[], str] | None = None,
        executor_id_factory: Callable[[], str] | None = None,
        checkpoint_id_factory: Callable[[], str] | None = None,
        now: Callable[[], str] | None = None,
        lease_seconds: int = 60,
    ) -> None:
        if runtime_factory is None and settings is None:
            raise ValueError("settings is required when runtime_factory is not provided")
        self.store = store
        self.settings = settings
        self.runtime_factory = runtime_factory or (lambda: _build_default_runtime(settings))
        self.runtime_run_id_factory = runtime_run_id_factory or (lambda: f"rtrun_{uuid4().hex}")
        self.executor_id_factory = executor_id_factory or (lambda: f"rtexec_{uuid4().hex}")
        self.checkpoint_id_factory = checkpoint_id_factory or (lambda: f"rtcheckpoint_{uuid4().hex}")
        self.now = now or _now
        self.lease_seconds = lease_seconds

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
        existing = self.store.get_run_by_approved_requirement_revision(
            approved_requirement.approved_requirement_revision_id
        )
        if existing is not None:
            return existing

        created_at = self.now()
        runtime_run_id = self.runtime_run_id_factory()
        run = self.store.create_run(
            RuntimeRunRecord(
                runtime_run_id=runtime_run_id,
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
        )
        starting_at = self.now()
        self.store.update_run_status(
            runtime_run_id=runtime_run_id,
            status="starting",
            current_stage="startup",
            updated_at=starting_at,
        )
        executor_id = self.executor_id_factory()
        self.store.acquire_executor_lease(
            runtime_run_id=runtime_run_id,
            executor_id=executor_id,
            acquired_at=starting_at,
            lease_expires_at=_plus_seconds(starting_at, self.lease_seconds),
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
            run_status="starting",
        )

        runtime_started = False
        runtime = self.runtime_factory()

        def runtime_start_callback(workflow_runtime_run_id: str) -> None:
            nonlocal runtime_started
            runtime_started = True
            self.store.append_executor_event(
                _event(
                    runtime_run_id=runtime_run_id,
                    event_type="runtime_executor_started",
                    stage="startup",
                    status="completed",
                    summary="executor started",
                    payload={"executorId": executor_id, "workflowRuntimeRunId": workflow_runtime_run_id},
                    created_at=self.now(),
                ),
                executor_id=executor_id,
                run_status="running",
            )

        def progress_callback(progress: ProgressEvent) -> None:
            self.store.append_executor_event(
                _event(
                    runtime_run_id=runtime_run_id,
                    event_type=_progress_event_type(progress.type),
                    stage=str(progress.payload.get("stage") or "runtime"),
                    status="completed",
                    summary=progress.message,
                    payload=dict(progress.payload),
                    created_at=self.now(),
                    round_no=progress.round_no,
                ),
                executor_id=executor_id,
                run_status="running",
            )

        def runtime_checkpoint_callback(artifacts: object) -> None:
            checkpoint = RuntimeCheckpoint(
                checkpoint_id=self.checkpoint_id_factory(),
                runtime_run_id=runtime_run_id,
                stage="round",
                round_no=None,
                safe_boundary="runtime_candidate_checkpoint",
                run_state=_run_state_payload(getattr(artifacts, "run_state", {})),
                source_plan={"sourceIds": list(source_ids)},
                pending_commands=[],
                artifact_manifest_ref=None,
                schema_version="runtime-control-checkpoint/v1",
                created_at=self.now(),
            )
            self.store.write_checkpoint(checkpoint, executor_id=executor_id)
            self.store.append_executor_event(
                _event(
                    runtime_run_id=runtime_run_id,
                    event_type="runtime_checkpoint_written",
                    stage=checkpoint.stage,
                    status="completed",
                    summary="checkpoint written",
                    payload={"checkpointId": checkpoint.checkpoint_id},
                    created_at=checkpoint.created_at,
                    round_no=checkpoint.round_no,
                ),
                executor_id=executor_id,
                run_status="running",
                latest_checkpoint_id=checkpoint.checkpoint_id,
            )

        try:
            await runtime.run_async(
                job_title=job_title,
                jd=jd_text,
                notes=notes or "",
                source_kinds=list(source_ids),
                progress_callback=progress_callback,
                runtime_start_callback=runtime_start_callback,
                runtime_checkpoint_callback=runtime_checkpoint_callback,
                approved_requirement_sheet=approved_requirement.requirement_sheet,
            )
        except (RuntimeError, ValueError, OSError) as exc:
            reason_code = "runtime_run_failed" if runtime_started else "runtime_executor_start_failed"
            self.store.append_executor_event(
                _event(
                    runtime_run_id=runtime_run_id,
                    event_type=reason_code,
                    stage="runtime",
                    status="failed",
                    summary=str(exc),
                    payload={"reasonCode": reason_code, "message": str(exc)},
                    created_at=self.now(),
                ),
                executor_id=executor_id,
                run_status="failed",
                stop_reason_code=reason_code,
                completed_at=self.now(),
            )
            self.store.release_executor_lease(
                runtime_run_id=runtime_run_id,
                executor_id=executor_id,
                released_at=self.now(),
                status="failed",
                reason_code=reason_code,
            )
            raise

        completed_at = self.now()
        self.store.append_executor_event(
            _event(
                runtime_run_id=runtime_run_id,
                event_type="runtime_run_completed",
                stage="finalization",
                status="completed",
                summary="run completed",
                payload={},
                created_at=completed_at,
            ),
            executor_id=executor_id,
            run_status="completed",
            completed_at=completed_at,
        )
        self.store.release_executor_lease(
            runtime_run_id=runtime_run_id,
            executor_id=executor_id,
            released_at=self.now(),
        )
        return self.store.get_run(run.runtime_run_id)


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


def _progress_event_type(event_type: str) -> str:
    if event_type.startswith("runtime_"):
        return event_type
    return f"runtime_{event_type}"


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
