"""Privacy-safe persistent first-cause records for execution components."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal
from uuid import uuid4


EXECUTION_FAILURE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS runtime_control_execution_failures (
      failure_id TEXT PRIMARY KEY,
      runtime_run_id TEXT,
      component TEXT NOT NULL,
      boundary TEXT NOT NULL,
      safe_reason_code TEXT NOT NULL,
      exception_type TEXT NOT NULL,
      exception_fingerprint TEXT NOT NULL,
      failure_role TEXT NOT NULL,
      occurred_at TEXT NOT NULL,
      CHECK (failure_role IN ('primary', 'secondary'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_runtime_execution_failures_recent
    ON runtime_control_execution_failures(occurred_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_control_component_health (
      component TEXT PRIMARY KEY,
      alive INTEGER NOT NULL,
      last_heartbeat_at TEXT,
      last_success_at TEXT,
      first_failure_at TEXT,
      first_failure_type TEXT,
      failure_count INTEGER NOT NULL,
      restart_count INTEGER NOT NULL,
      observed_at TEXT NOT NULL,
      CHECK (alive IN (0, 1)),
      CHECK (failure_count >= 0),
      CHECK (restart_count >= 0)
    )
    """,
)
_SAFE = re.compile(r"^[A-Za-z0-9_.:/-]{1,160}$")


@dataclass(frozen=True, slots=True)
class ExecutionFailureRecord:
    failure_id: str
    runtime_run_id: str | None
    component: str
    boundary: str
    safe_reason_code: str
    exception_type: str
    exception_fingerprint: str
    failure_role: Literal["primary", "secondary"]
    occurred_at: str


class ExecutionFailureStoreMixin:
    def record_component_health(
        self,
        *,
        component: str,
        alive: bool,
        last_heartbeat_at: str | None,
        last_success_at: str | None,
        first_failure_at: str | None,
        first_failure_type: str | None,
        failure_count: int,
        restart_count: int,
        observed_at: str,
    ) -> None:
        if _SAFE.fullmatch(component) is None:
            raise ValueError("component_health_name_invalid")
        if (
            first_failure_type is not None
            and _SAFE.fullmatch(first_failure_type) is None
        ):
            raise ValueError("component_health_failure_type_invalid")
        with self._connect() as connection, connection:  # type: ignore[attr-defined]
            connection.execute(
                """
                INSERT INTO runtime_control_component_health (
                  component, alive, last_heartbeat_at, last_success_at,
                  first_failure_at, first_failure_type, failure_count,
                  restart_count, observed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(component) DO UPDATE SET
                  alive = excluded.alive,
                  last_heartbeat_at = excluded.last_heartbeat_at,
                  last_success_at = excluded.last_success_at,
                  first_failure_at = excluded.first_failure_at,
                  first_failure_type = excluded.first_failure_type,
                  failure_count = excluded.failure_count,
                  restart_count = excluded.restart_count,
                  observed_at = excluded.observed_at
                """,
                (
                    component,
                    int(alive),
                    last_heartbeat_at,
                    last_success_at,
                    first_failure_at,
                    first_failure_type,
                    failure_count,
                    restart_count,
                    observed_at,
                ),
            )

    def record_execution_failure(
        self,
        *,
        runtime_run_id: str | None,
        component: str,
        boundary: str,
        safe_reason_code: str,
        error: BaseException,
        failure_role: Literal["primary", "secondary"],
        occurred_at: str,
    ) -> ExecutionFailureRecord:
        values = (
            component,
            boundary,
            safe_reason_code,
            type(error).__name__,
        )
        if any(_SAFE.fullmatch(value) is None for value in values):
            raise ValueError("execution_failure_field_invalid")
        fingerprint = sha256(":".join(values).encode()).hexdigest()
        record = ExecutionFailureRecord(
            failure_id=f"execution-failure-{uuid4().hex}",
            runtime_run_id=runtime_run_id,
            component=component,
            boundary=boundary,
            safe_reason_code=safe_reason_code,
            exception_type=type(error).__name__,
            exception_fingerprint=fingerprint,
            failure_role=failure_role,
            occurred_at=occurred_at,
        )
        with self._connect() as connection, connection:  # type: ignore[attr-defined]
            connection.execute(
                """
                INSERT INTO runtime_control_execution_failures (
                  failure_id, runtime_run_id, component, boundary,
                  safe_reason_code, exception_type,
                  exception_fingerprint, failure_role, occurred_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.failure_id,
                    record.runtime_run_id,
                    record.component,
                    record.boundary,
                    record.safe_reason_code,
                    record.exception_type,
                    record.exception_fingerprint,
                    record.failure_role,
                    record.occurred_at,
                ),
            )
        return record

    def list_execution_failures(
        self,
        *,
        limit: int = 100,
    ) -> list[ExecutionFailureRecord]:
        with self._connect() as connection:  # type: ignore[attr-defined]
            rows = connection.execute(
                """
                SELECT * FROM runtime_control_execution_failures
                ORDER BY occurred_at DESC, failure_id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 1000)),),
            ).fetchall()
        return [ExecutionFailureRecord(**dict(row)) for row in rows]


def create_execution_failure_schema(connection) -> None:
    for statement in EXECUTION_FAILURE_SCHEMA_STATEMENTS:
        connection.execute(statement)
