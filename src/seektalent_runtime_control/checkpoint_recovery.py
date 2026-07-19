from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from seektalent_runtime_control.models import RuntimeCheckpoint


RUNTIME_CHECKPOINT_MISSING = "runtime_checkpoint_missing"
RUNTIME_CHECKPOINT_RUN_MISMATCH = "runtime_checkpoint_run_mismatch"
RUNTIME_CHECKPOINT_SCHEMA_UNSUPPORTED = "runtime_checkpoint_schema_unsupported"
RUNTIME_CHECKPOINT_CORRUPT = "runtime_checkpoint_corrupt"
RUNTIME_CHECKPOINT_SAFE_BOUNDARY_UNREGISTERED = "runtime_checkpoint_safe_boundary_unregistered"
RUNTIME_CHECKPOINT_SAFE_BOUNDARY_INVALID = "runtime_checkpoint_safe_boundary_invalid"


@dataclass(frozen=True)
class RuntimeCheckpointLoadFailure:
    checkpoint_id: str
    reason_code: str


@dataclass(frozen=True)
class RuntimeCheckpointValidationContext:
    run_status: str
    run_stage: str
    run_round_no: int | None
    run_source_ids: tuple[str, ...]
    candidate_truth_valid: bool


@dataclass(frozen=True)
class RuntimeRecoveryDecision:
    runtime_run_id: str
    reason_code: str


@dataclass(frozen=True)
class RuntimeRecoveryPlan:
    reason_code: str
    target_status: str
    event_type: str
    event_status: str
    summary: str
    checkpoint_id: str | None = None


SafeBoundaryValidator = Callable[[RuntimeCheckpoint, RuntimeCheckpointValidationContext], bool]


def _before_source_dispatch_is_valid(
    checkpoint: RuntimeCheckpoint,
    context: RuntimeCheckpointValidationContext,
) -> bool:
    return (
        _checkpoint_matches_run(checkpoint, context)
        and _round_marker_matches(checkpoint)
        and checkpoint.pending_commands == []
    )


def _runtime_candidate_checkpoint_is_valid(
    checkpoint: RuntimeCheckpoint,
    context: RuntimeCheckpointValidationContext,
) -> bool:
    return _checkpoint_matches_run(checkpoint, context) and context.candidate_truth_valid


def _after_round_controller_is_valid(
    checkpoint: RuntimeCheckpoint,
    context: RuntimeCheckpointValidationContext,
) -> bool:
    return (
        _checkpoint_matches_run(checkpoint, context)
        and checkpoint.round_no is not None
        and _round_marker_matches(checkpoint)
    )


SAFE_BOUNDARY_REGISTRY: dict[str, SafeBoundaryValidator] = {
    "before_source_dispatch": _before_source_dispatch_is_valid,
    "runtime_candidate_checkpoint": _runtime_candidate_checkpoint_is_valid,
    "after_round_controller": _after_round_controller_is_valid,
}


def validate_recoverable_checkpoint(
    checkpoint: RuntimeCheckpoint,
    context: RuntimeCheckpointValidationContext,
) -> str | None:
    validator = SAFE_BOUNDARY_REGISTRY.get(checkpoint.safe_boundary)
    if validator is None:
        return RUNTIME_CHECKPOINT_SAFE_BOUNDARY_UNREGISTERED
    if not validator(checkpoint, context):
        return RUNTIME_CHECKPOINT_SAFE_BOUNDARY_INVALID
    return None


def decide_expired_lease_recovery(
    *,
    run_status: str,
    checkpoint: RuntimeCheckpoint | RuntimeCheckpointLoadFailure | None,
    resume_recoverable: bool,
) -> RuntimeRecoveryPlan:
    if run_status == "cancellation_requested":
        return RuntimeRecoveryPlan(
            reason_code="runtime_cancel_after_executor_lost",
            target_status="cancelled",
            event_type="runtime_run_cancelled",
            event_status="completed",
            summary="run cancelled after executor lease expired",
        )
    if isinstance(checkpoint, RuntimeCheckpointLoadFailure):
        return RuntimeRecoveryPlan(
            reason_code=checkpoint.reason_code,
            target_status="failed",
            event_type="runtime_checkpoint_restore_failed",
            event_status="failed",
            summary="checkpoint restore failed",
            checkpoint_id=checkpoint.checkpoint_id,
        )
    if checkpoint is not None and resume_recoverable:
        return RuntimeRecoveryPlan(
            reason_code="runtime_checkpoint_restored",
            target_status="resume_requested",
            event_type="runtime_checkpoint_restored",
            event_status="completed",
            summary="checkpoint restored",
            checkpoint_id=checkpoint.checkpoint_id,
        )
    reason_code = (
        "runtime_executor_start_timeout"
        if run_status == "starting"
        else "runtime_executor_crash_timeout"
    )
    return RuntimeRecoveryPlan(
        reason_code=reason_code,
        target_status="failed",
        event_type=(
            "runtime_executor_start_failed"
            if reason_code == "runtime_executor_start_timeout"
            else "runtime_executor_crashed"
        ),
        event_status="failed",
        summary=(
            "executor did not acknowledge start before lease timeout"
            if reason_code == "runtime_executor_start_timeout"
            else (
                "executor lease expired without a recoverable checkpoint"
                if checkpoint is None
                else "executor lease expired; recoverable checkpoint was not resumed by recovery policy"
            )
        ),
        checkpoint_id=checkpoint.checkpoint_id if checkpoint is not None else None,
    )


def _checkpoint_matches_run(
    checkpoint: RuntimeCheckpoint,
    context: RuntimeCheckpointValidationContext,
) -> bool:
    source_ids = checkpoint.source_plan.get("sourceIds")
    return (
        (
            context.run_status == "starting"
            or (
                checkpoint.stage == context.run_stage
                and checkpoint.round_no == context.run_round_no
            )
        )
        and isinstance(source_ids, list)
        and all(isinstance(source_id, str) and source_id for source_id in source_ids)
        and tuple(source_ids) == context.run_source_ids
    )


def _round_marker_matches(checkpoint: RuntimeCheckpoint) -> bool:
    round_marker = checkpoint.run_state.get("round")
    return (
        round_marker is None
        if checkpoint.round_no is None
        else isinstance(round_marker, int)
        and not isinstance(round_marker, bool)
        and round_marker == checkpoint.round_no
    )
