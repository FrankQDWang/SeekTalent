from __future__ import annotations

import logging
from collections.abc import Mapping
from uuid import uuid4


logger = logging.getLogger(__name__)


def correlation_id_from_request(request: object) -> str:
    headers = getattr(request, "headers", None)
    if headers is not None:
        request_id = headers.get("x-correlation-id") or headers.get("x-request-id")
        if request_id:
            return request_id
    return f"awb_{uuid4().hex}"


def record_workbench_metric(
    name: str,
    value: int | float,
    *,
    correlation_id: str | None = None,
    extra: Mapping[str, object] | None = None,
) -> None:
    logger.info(
        "Agent workbench metric recorded.",
        extra={
            "metric_name": name,
            "metric_value": value,
            "correlation_id": correlation_id,
            **_redacted_extra(extra),
        },
    )


def record_workbench_audit_event(
    name: str,
    *,
    reason_code: str | None = None,
    correlation_id: str | None = None,
    extra: Mapping[str, object] | None = None,
) -> None:
    logger.info(
        "Agent workbench audit event recorded.",
        extra={
            "event_name": name,
            "reason_code": reason_code,
            "correlation_id": correlation_id,
            **_redacted_extra(extra),
        },
    )


def record_projection_lag_ms(value: int | float, *, correlation_id: str | None = None) -> None:
    record_workbench_metric("projection_lag_ms", value, correlation_id=correlation_id)


def record_outbox_lag_ms(value: int | float, *, correlation_id: str | None = None) -> None:
    record_workbench_metric("outbox_lag_ms", value, correlation_id=correlation_id)


def record_sse_replay_gap(*, correlation_id: str | None = None) -> None:
    record_workbench_audit_event("sse_replay_gap", reason_code="stream_replay_gap", correlation_id=correlation_id)


def record_idempotency_conflict(*, correlation_id: str | None = None) -> None:
    record_workbench_audit_event(
        "idempotency_conflict",
        reason_code="idempotency_key_conflict",
        correlation_id=correlation_id,
    )


def record_duplicate_run_prevented(*, correlation_id: str | None = None) -> None:
    record_workbench_audit_event(
        "duplicate_run_prevented",
        reason_code="agent_request_in_progress",
        correlation_id=correlation_id,
    )


def record_candidate_detail_denied(*, correlation_id: str | None = None) -> None:
    record_workbench_audit_event(
        "candidate_detail_denied",
        reason_code="candidate_detail_denied",
        correlation_id=correlation_id,
    )


def record_workbench_payload_bytes(value: int, *, correlation_id: str | None = None) -> None:
    record_workbench_metric("workbench_payload_bytes", value, correlation_id=correlation_id)


def record_requirement_snapshot_invalid(*, error_count: int, correlation_id: str | None = None) -> None:
    record_workbench_audit_event(
        "requirement_snapshot_invalid",
        reason_code="requirement_snapshot_invalid",
        correlation_id=correlation_id,
        extra={"error_count": error_count},
    )


def record_workflow_start_kill_switch_blocked(*, correlation_id: str | None = None) -> None:
    record_workbench_audit_event(
        "workflow_start_kill_switch_blocked",
        reason_code="workflow_start_kill_switch_blocked",
        correlation_id=correlation_id,
    )


def _redacted_extra(extra: Mapping[str, object] | None) -> dict[str, object]:
    if extra is None:
        return {}
    return {f"workbench_{key}": "redacted" if _looks_sensitive(key) else value for key, value in extra.items()}


def _looks_sensitive(key: str) -> bool:
    lowered = key.casefold()
    return any(fragment in lowered for fragment in ("id", "token", "secret", "email", "name", "payload", "text"))
