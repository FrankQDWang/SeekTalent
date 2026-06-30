from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from seektalent_runtime_control.models import RuntimeControlEventInput
from seektalent_runtime_control.store import RuntimeCheckpointLoadFailure, RuntimeControlStore


@dataclass(frozen=True)
class RuntimeRecoveryDecision:
    runtime_run_id: str
    reason_code: str


class RuntimeRecoveryService:
    def __init__(self, *, store: RuntimeControlStore, now: Callable[[], str] | None = None) -> None:
        self.store = store
        self.now = now or _now

    def recover_start_timeouts(self, *, resume_recoverable: bool = True) -> list[RuntimeRecoveryDecision]:
        now = self.now()
        decisions: list[RuntimeRecoveryDecision] = []
        for lease in self.store.expire_executor_leases(now=now):
            run = self.store.get_run(lease.runtime_run_id)
            if run.status in {"cancelled", "completed", "failed"}:
                continue
            self.store.append_event(
                _event(
                    runtime_run_id=run.runtime_run_id,
                    event_type="runtime_executor_lease_expired",
                    stage=run.current_stage,
                    round_no=run.current_round,
                    status="failed",
                    summary="executor lease expired",
                    payload={"executorId": lease.executor_id, "attemptNo": lease.attempt_no},
                    created_at=now,
                )
            )
            if run.status == "cancellation_requested":
                self.store.append_event(
                    _event(
                        runtime_run_id=run.runtime_run_id,
                        event_type="runtime_run_cancelled",
                        stage=run.current_stage,
                        round_no=run.current_round,
                        status="completed",
                        summary="run cancelled after executor lease expired",
                        payload={
                            "reasonCode": "runtime_cancel_after_executor_lost",
                            "executorId": lease.executor_id,
                        },
                        created_at=now,
                    )
                )
                self.store.update_run_status(
                    runtime_run_id=run.runtime_run_id,
                    status="cancelled",
                    stop_reason_code="runtime_cancel_after_executor_lost",
                    completed_at=now,
                    updated_at=now,
                )
                decisions.append(
                    RuntimeRecoveryDecision(
                        runtime_run_id=run.runtime_run_id,
                        reason_code="runtime_cancel_after_executor_lost",
                    )
                )
                continue
            checkpoint = self.store.get_latest_recoverable_checkpoint(runtime_run_id=run.runtime_run_id)
            if isinstance(checkpoint, RuntimeCheckpointLoadFailure):
                self.store.append_event(
                    _event(
                        runtime_run_id=run.runtime_run_id,
                        event_type="runtime_checkpoint_restore_failed",
                        stage=run.current_stage,
                        round_no=run.current_round,
                        status="failed",
                        summary="checkpoint restore failed",
                        payload={
                            "checkpointId": checkpoint.checkpoint_id,
                            "reasonCode": checkpoint.reason_code,
                        },
                        created_at=now,
                    )
                )
                self.store.update_run_status(
                    runtime_run_id=run.runtime_run_id,
                    status="failed",
                    stop_reason_code=checkpoint.reason_code,
                    completed_at=now,
                    updated_at=now,
                )
                decisions.append(
                    RuntimeRecoveryDecision(
                        runtime_run_id=run.runtime_run_id,
                        reason_code=checkpoint.reason_code,
                    )
                )
                continue
            if checkpoint is not None and resume_recoverable:
                self.store.append_event(
                    _event(
                        runtime_run_id=run.runtime_run_id,
                        event_type="runtime_checkpoint_restored",
                        stage=checkpoint.stage,
                        round_no=checkpoint.round_no,
                        status="completed",
                        summary="checkpoint restored",
                        payload={"checkpointId": checkpoint.checkpoint_id},
                        created_at=now,
                    )
                )
                self.store.update_run_status(
                    runtime_run_id=run.runtime_run_id,
                    status="resume_requested",
                    current_stage=checkpoint.stage,
                    current_round=checkpoint.round_no,
                    latest_checkpoint_id=checkpoint.checkpoint_id,
                    updated_at=now,
                )
                decisions.append(
                    RuntimeRecoveryDecision(
                        runtime_run_id=run.runtime_run_id,
                        reason_code="runtime_checkpoint_restored",
                    )
                )
                continue

            reason_code = _no_checkpoint_failure_reason(run.status)
            payload: dict[str, object] = {"reasonCode": reason_code, "executorId": lease.executor_id}
            summary = (
                "executor lease expired without a recoverable checkpoint"
                if checkpoint is None
                else "executor lease expired; recoverable checkpoint was not resumed by recovery policy"
            )
            if checkpoint is not None:
                payload["checkpointId"] = checkpoint.checkpoint_id
            self.store.append_event(
                _event(
                    runtime_run_id=run.runtime_run_id,
                    event_type="runtime_executor_start_failed"
                    if reason_code == "runtime_executor_start_timeout"
                    else "runtime_executor_crashed",
                    stage=run.current_stage,
                    round_no=run.current_round,
                    status="failed",
                    summary="executor did not acknowledge start before lease timeout"
                    if reason_code == "runtime_executor_start_timeout"
                    else summary,
                    payload=payload,
                    created_at=now,
                )
            )
            self.store.update_run_status(
                runtime_run_id=run.runtime_run_id,
                status="failed",
                stop_reason_code=reason_code,
                completed_at=now,
                updated_at=now,
            )
            decisions.append(
                RuntimeRecoveryDecision(
                    runtime_run_id=run.runtime_run_id,
                    reason_code=reason_code,
                )
            )
        return decisions


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


def _no_checkpoint_failure_reason(run_status: str) -> str:
    if run_status == "starting":
        return "runtime_executor_start_timeout"
    return "runtime_executor_crash_timeout"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
