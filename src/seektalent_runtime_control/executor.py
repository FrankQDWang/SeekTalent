from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from inspect import Parameter, signature
import logging
from typing import Protocol, runtime_checkable
from uuid import uuid4

from seektalent.config import AppSettings
from seektalent.progress import ProgressEvent
from seektalent_runtime_control.checkpoint_v2 import (
    RUNTIME_CHECKPOINT_SCHEMA_V1,
    checkpoint_projection,
    legacy_checkpoint_projection,
)
from seektalent_runtime_control.browser_lane import BrowserLaneBusyError
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
from seektalent_runtime_control.recovery_state import RecoveryStateAssembler
from seektalent_runtime_control.store import RuntimeCheckpointLoadFailure, RuntimeControlStore

SourceContext = dict[str, str | int | bool | None]
SourceContextProvider = Callable[[Sequence[str], AppSettings | None], SourceContext | None]
logger = logging.getLogger(__name__)


class RuntimeFactory(Protocol):
    def __call__(
        self,
        *,
        source_operation_executor: object | None,
    ) -> object: ...


@runtime_checkable
class RuntimeLike(Protocol):
    async def run_async(self, **kwargs: object) -> object: ...


class WorkflowRuntimeExecutor:
    def __init__(
        self,
        *,
        store: RuntimeControlStore,
        settings: AppSettings | None = None,
        runtime_factory: RuntimeFactory | None = None,
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
        self.runtime_factory: RuntimeFactory = runtime_factory or (
            lambda *, source_operation_executor: _build_default_runtime(
                settings,
                source_operation_executor=source_operation_executor,
            )
        )
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
        cards_operation_executor = None
        if (
            self.settings is not None
            and "liepin" in resolved_source_ids
            and self.settings.liepin_worker_mode == "opencli"
        ):
            from seektalent.liepin_cards_source_operation import (
                LiepinCardsSourceOperationExecutor,
            )

            cards_operation_executor = LiepinCardsSourceOperationExecutor(
                settings=self.settings,
                store=self.store,
                runtime_run_id=run.runtime_run_id,
                executor_id=executor_id,
                attempt_no=attempt_no,
                accepted_requirement_revision_id=(
                    approved.approved_requirement_revision_id
                ),
                runtime_attempt_authority_ref=(
                    f"executor-lease://{run.runtime_run_id}/{attempt_no}"
                ),
            )
        resume_checkpoint = self._load_resume_checkpoint(
            runtime_run_id=run.runtime_run_id,
            executor_id=executor_id,
            attempt_no=attempt_no,
            claim_reason=claim_reason,
        )
        if (
            resume_checkpoint is not None
            and resume_checkpoint.safe_boundary
            == "after_finalization_commit"
        ):
            if cards_operation_executor is not None:
                cards_operation_executor.close()
            return self._settle_completed_run(
                runtime_run_id=run.runtime_run_id,
                executor_id=executor_id,
                attempt_no=attempt_no,
            )
        runtime = self._build_runtime(
            source_operation_executor=cards_operation_executor,
        )
        if not isinstance(runtime, RuntimeLike):
            if cards_operation_executor is not None:
                cards_operation_executor.close()
            raise RuntimeControlError("runtime_adapter_invalid")
        detail_claim_revision, detail_claim_payload_hash = (
            self.store.get_detail_claim_revision(runtime_run_id=run.runtime_run_id)
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
            projection = checkpoint_projection(
                getattr(artifacts, "run_state", {})
            )
            source_operation_ids = (
                cards_operation_executor.checkpoint_operation_ids()
                if cards_operation_executor is not None
                else ()
            )
            checkpoint = self.store.write_checkpoint_v2(
                checkpoint_id=self.checkpoint_id_factory(),
                runtime_run_id=run.runtime_run_id,
                executor_id=executor_id,
                attempt_no=attempt_no,
                stage=str(getattr(artifacts, "stage", "round")),
                round_no=getattr(artifacts, "round_no", None),
                safe_boundary=str(
                    getattr(
                        artifacts,
                        "safe_boundary",
                        "runtime_candidate_checkpoint",
                    )
                ),
                accepted_requirement_revision_id=(
                    approved.approved_requirement_revision_id
                ),
                source_ids=resolved_source_ids,
                projection=projection,
                detail_claim_revision=detail_claim_revision,
                detail_claim_hash=detail_claim_payload_hash,
                created_at=self.now(),
                continuation_cursor=_string_key_dict(
                    getattr(artifacts, "continuation_cursor", None)
                ),
                source_operation_ids=source_operation_ids,
            )
            if cards_operation_executor is not None:
                cards_operation_executor.checkpoint_committed(
                    source_operation_ids
                )
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

        def runtime_detail_claim_callback(claims: object) -> None:
            nonlocal detail_claim_revision, detail_claim_payload_hash
            payload = _detail_claim_payload(claims)
            detail_claim_revision, detail_claim_payload_hash = (
                self.store.write_detail_claim_snapshot(
                    runtime_run_id=run.runtime_run_id,
                    claims=payload,
                    expected_revision=detail_claim_revision,
                    updated_at=self.now(),
                )
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
            if _runtime_accepts_detail_claim_callback(runtime):
                runtime_kwargs[
                    "runtime_detail_claim_callback"
                ] = runtime_detail_claim_callback
            if resume_checkpoint is not None and _runtime_accepts_resume_context(runtime):
                if resume_checkpoint.schema_version == RUNTIME_CHECKPOINT_SCHEMA_V1:
                    legacy_projection = legacy_checkpoint_projection(
                        resume_checkpoint.run_state
                    )
                    if legacy_projection.detail_claims:
                        (
                            detail_claim_revision,
                            detail_claim_payload_hash,
                        ) = self.store.write_detail_claim_snapshot(
                            runtime_run_id=run.runtime_run_id,
                            claims=legacy_projection.detail_claims,
                            expected_revision=detail_claim_revision,
                            updated_at=self.now(),
                        )
                    resume_checkpoint = self.store.write_checkpoint_v2(
                        checkpoint_id=self.checkpoint_id_factory(),
                        runtime_run_id=run.runtime_run_id,
                        executor_id=executor_id,
                        attempt_no=attempt_no,
                        stage=resume_checkpoint.stage,
                        round_no=resume_checkpoint.round_no,
                        safe_boundary=resume_checkpoint.safe_boundary,
                        accepted_requirement_revision_id=(
                            approved.approved_requirement_revision_id
                        ),
                        source_ids=resolved_source_ids,
                        projection=legacy_projection,
                        detail_claim_revision=detail_claim_revision,
                        detail_claim_hash=detail_claim_payload_hash,
                        created_at=self.now(),
                    )
                runtime_kwargs["resume_checkpoint"] = (
                    resume_checkpoint.model_dump(mode="json")
                )
                runtime_kwargs["resume_run_state"] = (
                    RecoveryStateAssembler(self.store)
                    .assemble(resume_checkpoint)
                    .model_dump(mode="json")
                )
            await runtime.run_async(**runtime_kwargs)
        except BrowserLaneBusyError as exc:
            if cards_operation_executor is not None:
                self._close_source_executor_after_failure(
                    cards_operation_executor,
                    runtime_run_id=run.runtime_run_id,
                    primary_error=exc,
                )
            return self._yield_for_browser_lane(
                runtime_run_id=run.runtime_run_id,
                executor_id=executor_id,
                attempt_no=attempt_no,
            )
        except (RuntimeError, ValueError, OSError) as exc:
            if cards_operation_executor is not None:
                self._close_source_executor_after_failure(
                    cards_operation_executor,
                    runtime_run_id=run.runtime_run_id,
                    primary_error=exc,
                )
            reason_code = "runtime_run_failed" if runtime_started else "runtime_executor_start_failed"
            self._record_execution_failure_safely(
                runtime_run_id=run.runtime_run_id,
                component="runtime_executor",
                boundary=(
                    "runtime_effect"
                    if runtime_started
                    else "runtime_start"
                ),
                safe_reason_code=reason_code,
                error=exc,
                failure_role="primary",
                occurred_at=self.now(),
            )
            self.store.append_executor_event(
                _event(
                    runtime_run_id=run.runtime_run_id,
                    event_type=reason_code,
                    stage="runtime",
                    status="failed",
                    summary=reason_code,
                    payload={
                        "reasonCode": reason_code,
                        "exceptionType": type(exc).__name__,
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
            if self.store.get_run(run.runtime_run_id).latest_checkpoint_id:
                self.store.compact_terminal_checkpoints(
                    runtime_run_id=run.runtime_run_id
                )
            raise
        except BaseException as exc:
            if cards_operation_executor is not None:
                self._close_source_executor_after_failure(
                    cards_operation_executor,
                    runtime_run_id=run.runtime_run_id,
                    primary_error=exc,
                )
            raise

        if cards_operation_executor is not None:
            cards_operation_executor.close()
        return self._settle_completed_run(
            runtime_run_id=run.runtime_run_id,
            executor_id=executor_id,
            attempt_no=attempt_no,
        )

    def _close_source_executor_after_failure(
        self,
        source_executor: object,
        *,
        runtime_run_id: str,
        primary_error: BaseException,
    ) -> None:
        del primary_error
        try:
            source_executor.close()  # type: ignore[attr-defined]
        except Exception as cleanup_error:  # noqa: BLE001
            self._record_execution_failure_safely(
                runtime_run_id=runtime_run_id,
                component="runtime_executor",
                boundary="source_executor_close",
                safe_reason_code="source_executor_close_failed",
                error=cleanup_error,
                failure_role="secondary",
                occurred_at=self.now(),
            )

    def _record_execution_failure_safely(
        self,
        **values: object,
    ) -> None:
        try:
            self.store.record_execution_failure(**values)
        except Exception as persistence_error:  # noqa: BLE001
            logger.debug(
                "execution failure persistence failed: %s",
                type(persistence_error).__name__,
            )
            return

    def _yield_for_browser_lane(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int,
    ) -> RuntimeRunRecord:
        yielded_at = self.now()
        self.store.append_executor_event(
            _event(
                runtime_run_id=runtime_run_id,
                event_type="runtime_resource_waiting",
                stage="source",
                status="pending",
                summary="waiting for Liepin browser lane",
                payload={
                    "reasonCode": "liepin_browser_lane_busy",
                    "resource": "liepin_browser",
                },
                created_at=yielded_at,
            ),
            executor_id=executor_id,
            attempt_no=attempt_no,
            run_status="resume_requested",
        )
        self.store.release_executor_lease(
            runtime_run_id=runtime_run_id,
            executor_id=executor_id,
            attempt_no=attempt_no,
            released_at=yielded_at,
            reason_code="liepin_browser_lane_busy",
        )
        return self.store.get_run(runtime_run_id)

    def _settle_completed_run(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        attempt_no: int,
    ) -> RuntimeRunRecord:
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
            attempt_no=attempt_no,
            run_status="completed",
            completed_at=completed_at,
        )
        self.store.release_executor_lease(
            runtime_run_id=runtime_run_id,
            executor_id=executor_id,
            attempt_no=attempt_no,
            released_at=self.now(),
        )
        if self.store.get_run(runtime_run_id).latest_checkpoint_id:
            self.store.compact_terminal_checkpoints(
                runtime_run_id=runtime_run_id
            )
        return self.store.get_run(runtime_run_id)

    def _source_context(self, source_ids: Sequence[str]) -> SourceContext | None:
        if self.source_context_provider is not None:
            provided = self.source_context_provider(source_ids, self.settings)
            if provided is not None:
                return provided
        return _default_source_context(source_ids=source_ids, settings=self.settings)

    def _build_runtime(
        self,
        *,
        source_operation_executor: object | None,
    ) -> object:
        if self.settings is None:
            legacy_factory = self.runtime_factory
            return legacy_factory()  # type: ignore[call-arg]
        return self.runtime_factory(
            source_operation_executor=source_operation_executor,
        )

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


def _build_default_runtime(
    settings: AppSettings | None,
    *,
    source_operation_executor: object | None = None,
) -> object:
    if settings is None:
        raise ValueError("settings is required")
    from seektalent.source_adapters import build_source_enabled_runtime

    return build_source_enabled_runtime(
        settings,
        source_operation_executor=source_operation_executor,
    )


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


def _runtime_accepts_detail_claim_callback(runtime: object) -> bool:
    run_async = getattr(runtime, "run_async", None)
    if not callable(run_async):
        return False
    parameters = signature(run_async).parameters
    if "runtime_detail_claim_callback" in parameters:
        return True
    return any(
        parameter.kind == Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def _run_state_payload(run_state: object) -> dict[str, object]:
    if isinstance(run_state, dict):
        return _string_key_dict(run_state)
    model_dump = getattr(run_state, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json")
        return _string_key_dict(payload)
    values = vars(run_state) if hasattr(run_state, "__dict__") else {}
    return _string_key_dict(values)


def _detail_claim_payload(claims: object) -> dict[str, object]:
    if not isinstance(claims, dict):
        raise RuntimeControlError("runtime_detail_claim_snapshot_invalid")
    payload: dict[str, object] = {}
    for key, claim in claims.items():
        if not isinstance(key, str):
            continue
        model_dump = getattr(claim, "model_dump", None)
        value = model_dump(mode="json") if callable(model_dump) else claim
        payload[key] = _string_key_dict(value)
    return payload


def _plus_seconds(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=seconds)).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _string_key_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}
