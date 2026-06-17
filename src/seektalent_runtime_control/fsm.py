from __future__ import annotations

from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RunStatus


TERMINAL_RUN_STATUSES: frozenset[str] = frozenset({"cancelled", "completed", "failed"})

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"queued", "starting", "cancellation_requested", "cancelled", "failed"}),
    "starting": frozenset({"starting", "running", "cancellation_requested", "cancelled", "failed"}),
    "running": frozenset(
        {
            "running",
            "pause_requested",
            "paused",
            "resume_requested",
            "cancellation_requested",
            "cancelled",
            "completed",
            "failed",
        }
    ),
    "pause_requested": frozenset({"pause_requested", "paused", "cancellation_requested", "cancelled", "failed"}),
    "paused": frozenset({"paused", "resume_requested", "cancellation_requested", "cancelled", "failed"}),
    "resume_requested": frozenset(
        {"resume_requested", "starting", "running", "pause_requested", "cancellation_requested", "cancelled", "failed"}
    ),
    "cancellation_requested": frozenset({"cancellation_requested", "cancelled", "failed"}),
    "cancelled": frozenset({"cancelled"}),
    "completed": frozenset({"completed"}),
    "failed": frozenset({"failed"}),
}


def validate_run_transition(current_status: str, target_status: str) -> None:
    if current_status not in _ALLOWED_TRANSITIONS:
        raise RuntimeControlError("runtime_run_unknown_status")
    if target_status not in _ALLOWED_TRANSITIONS:
        raise RuntimeControlError("runtime_run_unknown_status")
    if target_status not in _ALLOWED_TRANSITIONS[current_status]:
        raise RuntimeControlError(
            "runtime_run_invalid_transition",
            payload={"currentStatus": current_status, "targetStatus": target_status},
        )


def require_run_transition(current_status: str, target_status: str) -> RunStatus:
    validate_run_transition(current_status, target_status)
    if target_status == "queued":
        return "queued"
    if target_status == "starting":
        return "starting"
    if target_status == "running":
        return "running"
    if target_status == "pause_requested":
        return "pause_requested"
    if target_status == "paused":
        return "paused"
    if target_status == "resume_requested":
        return "resume_requested"
    if target_status == "cancellation_requested":
        return "cancellation_requested"
    if target_status == "cancelled":
        return "cancelled"
    if target_status == "completed":
        return "completed"
    if target_status == "failed":
        return "failed"
    raise RuntimeControlError("runtime_run_unknown_status")
