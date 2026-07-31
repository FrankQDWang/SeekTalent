"""SQLite-backed single-writer lease for Liepin browser effects."""

from __future__ import annotations

import os
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from collections.abc import Callable
from hashlib import sha256
from typing import Literal, Protocol
from uuid import uuid4

from seektalent_runtime_control.errors import RuntimeControlError


logger = logging.getLogger(__name__)
LIEPIN_BROWSER_LANE = "liepin_browser"
BROWSER_LANE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS runtime_control_browser_lanes (
      lane_key TEXT PRIMARY KEY,
      fencing_token INTEGER NOT NULL,
      status TEXT NOT NULL,
      owner_id TEXT NOT NULL,
      owner_process_id INTEGER NOT NULL,
      process_boot_id TEXT NOT NULL,
      runtime_run_id TEXT,
      operation_id TEXT NOT NULL,
      operation_kind TEXT NOT NULL,
      acquired_at TEXT NOT NULL,
      heartbeat_at TEXT NOT NULL,
      lease_expires_at TEXT,
      released_at TEXT,
      last_failure_code TEXT,
      updated_at TEXT NOT NULL,
      CHECK (lane_key = 'liepin_browser'),
      CHECK (fencing_token >= 1),
      CHECK (status IN ('active', 'completed', 'failed')),
      CHECK (operation_kind IN (
        'cards', 'details', 'continuation', 'recheck',
        'prepare_readiness'
      ))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_control_browser_lane_resolutions (
      resolution_id TEXT PRIMARY KEY,
      lane_key TEXT NOT NULL,
      fencing_token INTEGER NOT NULL,
      runtime_run_id TEXT NOT NULL,
      operation_id TEXT NOT NULL,
      outcome TEXT NOT NULL,
      history_conclusion TEXT,
      evidence_ref TEXT NOT NULL,
      evidence_digest TEXT NOT NULL,
      resolved_at TEXT NOT NULL,
      CHECK (lane_key = 'liepin_browser'),
      CHECK (outcome IN ('no_effect', 'terminal_observed', 'unknown')),
      CHECK (length(evidence_digest) = 64)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_runtime_control_browser_lanes_active
    ON runtime_control_browser_lanes(status, lease_expires_at)
    """,
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:/-]{1,256}$")
_SAFE_REASON = re.compile(r"^[a-z][a-z0-9_]{0,159}$")
_PROCESS_BOOT_ID = f"process-{os.getpid()}-{uuid4().hex}"


BrowserLaneOperationKind = Literal[
    "cards",
    "details",
    "continuation",
    "recheck",
    "prepare_readiness",
]


@dataclass(frozen=True, slots=True)
class BrowserLaneLease:
    lane_key: str
    fencing_token: int
    owner_id: str
    owner_process_id: int
    process_boot_id: str
    runtime_run_id: str | None
    operation_id: str
    operation_kind: BrowserLaneOperationKind
    acquired_at: str
    heartbeat_at: str
    lease_expires_at: str


@dataclass(frozen=True, slots=True)
class BrowserLaneSnapshot:
    lane_key: str
    fencing_token: int
    status: str
    owner_id: str
    owner_process_id: int
    process_boot_id: str
    runtime_run_id: str | None
    operation_id: str
    operation_kind: str
    acquired_at: str
    heartbeat_at: str
    lease_expires_at: str | None
    released_at: str | None
    last_failure_code: str | None
    updated_at: str


class BrowserLaneBusyError(RuntimeError):
    pass


class BrowserLaneStore(Protocol):
    def try_acquire_browser_lane(
        self,
        *,
        lane_key: str,
        owner_id: str,
        owner_process_id: int,
        process_boot_id: str,
        runtime_run_id: str | None,
        operation_id: str,
        operation_kind: BrowserLaneOperationKind,
        acquired_at: str,
        lease_expires_at: str,
    ) -> BrowserLaneLease | None: ...

    def heartbeat_browser_lane(
        self,
        *,
        lane_key: str,
        owner_id: str,
        fencing_token: int,
        heartbeat_at: str,
        lease_expires_at: str,
    ) -> BrowserLaneLease: ...

    def release_browser_lane(
        self,
        *,
        lane_key: str,
        owner_id: str,
        fencing_token: int,
        released_at: str,
        status: Literal["completed", "failed"],
        failure_code: str | None = None,
    ) -> BrowserLaneSnapshot: ...

    def mark_browser_lane_unresolved(
        self,
        *,
        lane_key: str,
        owner_id: str,
        fencing_token: int,
        failure_code: str,
        observed_at: str,
    ) -> BrowserLaneSnapshot: ...


class BrowserLaneGuard:
    """Hold one durable browser lane while a synchronous effect executes."""

    def __init__(
        self,
        *,
        store: BrowserLaneStore,
        runtime_run_id: str | None,
        operation_id: str,
        operation_kind: BrowserLaneOperationKind,
        now,
        plus_seconds,
        wait_timeout_seconds: float,
        lease_seconds: float = 30.0,
        poll_interval_seconds: float = 0.1,
        monotonic=time.monotonic,
        on_lease_lost: Callable[[], None] | None = None,
    ) -> None:
        if wait_timeout_seconds <= 0 or lease_seconds <= 0:
            raise ValueError("browser lane timeouts must be positive")
        _require_safe_id(operation_id, "operation_id")
        if runtime_run_id is not None:
            _require_safe_id(runtime_run_id, "runtime_run_id")
        self._store = store
        self._runtime_run_id = runtime_run_id
        self._operation_id = operation_id
        self._operation_kind = operation_kind
        self._now = now
        self._plus_seconds = plus_seconds
        self._wait_timeout_seconds = wait_timeout_seconds
        self._lease_seconds = lease_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._monotonic = monotonic
        self._on_lease_lost = on_lease_lost
        self._owner_id = f"browser-owner-{uuid4().hex}"
        self._lease: BrowserLaneLease | None = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_error: Exception | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._unresolved_failure_code: str | None = None

    @property
    def lease(self) -> BrowserLaneLease:
        if self._lease is None:
            raise RuntimeError("browser_lane_not_acquired")
        return self._lease

    def __enter__(self) -> BrowserLaneLease:
        deadline = self._monotonic() + self._wait_timeout_seconds
        while True:
            now = self._now()
            lease = self._store.try_acquire_browser_lane(
                lane_key=LIEPIN_BROWSER_LANE,
                owner_id=self._owner_id,
                owner_process_id=os.getpid(),
                process_boot_id=_PROCESS_BOOT_ID,
                runtime_run_id=self._runtime_run_id,
                operation_id=self._operation_id,
                operation_kind=self._operation_kind,
                acquired_at=now,
                lease_expires_at=self._plus_seconds(
                    now,
                    self._lease_seconds,
                ),
            )
            if lease is not None:
                self._lease = lease
                self._start_heartbeat()
                return lease
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise BrowserLaneBusyError("liepin_browser_lane_busy")
            time.sleep(min(self._poll_interval_seconds, remaining))

    def preserve_unresolved(self, failure_code: str) -> None:
        if _SAFE_REASON.fullmatch(failure_code) is None:
            raise ValueError("browser_lane_failure_code_invalid")
        self._unresolved_failure_code = failure_code

    def __exit__(self, exc_type, exc, _traceback) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=max(1.0, self._lease_seconds))
        lease = self.lease
        failure = exc is not None or self._heartbeat_error is not None
        failure_code = (
            "liepin_browser_lane_heartbeat_failed"
            if self._heartbeat_error is not None
            else _safe_failure_code(exc)
        )
        # A failed heartbeat leaves the lane durably unresolved. Killing the
        # owned sidecar cannot prove that a command already accepted by the
        # long-lived browser daemon stopped. A later owner may proceed only
        # after explicit reconciliation resolves this fence.
        if self._heartbeat_error is not None:
            recorder = getattr(
                self._store,
                "record_execution_failure",
                None,
            )
            if callable(recorder):
                try:
                    recorder(
                        runtime_run_id=self._runtime_run_id,
                        component="browser_lane",
                        boundary="heartbeat",
                        safe_reason_code=(
                            "browser_lane_heartbeat_failed"
                        ),
                        error=self._heartbeat_error,
                        failure_role=(
                            "secondary" if exc is not None else "primary"
                        ),
                        occurred_at=self._now(),
                    )
                except Exception:
                    if exc is None:
                        raise
        if (
            self._heartbeat_error is None
            and self._unresolved_failure_code is not None
        ):
            self._store.mark_browser_lane_unresolved(
                lane_key=lease.lane_key,
                owner_id=lease.owner_id,
                fencing_token=lease.fencing_token,
                failure_code=self._unresolved_failure_code,
                observed_at=self._now(),
            )
        elif self._heartbeat_error is None:
            try:
                self._store.release_browser_lane(
                    lane_key=lease.lane_key,
                    owner_id=lease.owner_id,
                    fencing_token=lease.fencing_token,
                    released_at=self._now(),
                    status="failed" if failure else "completed",
                    failure_code=failure_code,
                )
            except Exception as cleanup_error:
                recorder = getattr(
                    self._store,
                    "record_execution_failure",
                    None,
                )
                if callable(recorder):
                    try:
                        recorder(
                            runtime_run_id=self._runtime_run_id,
                            component="browser_lane",
                            boundary="release",
                            safe_reason_code=(
                                "browser_lane_release_failed"
                            ),
                            error=cleanup_error,
                            failure_role="secondary" if exc is not None else "primary",
                            occurred_at=self._now(),
                        )
                    except Exception:
                        if exc is None:
                            raise
                if exc is None:
                    raise
        if exc is None and self._heartbeat_error is not None:
            raise RuntimeControlError(
                "liepin_browser_lane_heartbeat_failed"
            ) from self._heartbeat_error

    def _start_heartbeat(self) -> None:
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="seektalent-liepin-browser-lane-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        interval = max(0.1, self._lease_seconds / 3)
        while not self._heartbeat_stop.wait(interval):
            lease = self.lease
            now = self._now()
            try:
                self._lease = self._store.heartbeat_browser_lane(
                    lane_key=lease.lane_key,
                    owner_id=lease.owner_id,
                    fencing_token=lease.fencing_token,
                    heartbeat_at=now,
                    lease_expires_at=self._plus_seconds(
                        now,
                        self._lease_seconds,
                    ),
                )
            except Exception as error:
                self._heartbeat_error = error
                if self._on_lease_lost is not None:
                    try:
                        self._on_lease_lost()
                    except Exception as callback_error:
                        logger.debug(
                            "browser lane fence callback failed: %s",
                            type(callback_error).__name__,
                        )
                self._heartbeat_stop.set()
                return


class BrowserLaneStoreMixin:
    def _connect(self) -> sqlite3.Connection:
        raise NotImplementedError

    def try_acquire_browser_lane(
        self,
        *,
        lane_key: str,
        owner_id: str,
        owner_process_id: int,
        process_boot_id: str,
        runtime_run_id: str | None,
        operation_id: str,
        operation_kind: BrowserLaneOperationKind,
        acquired_at: str,
        lease_expires_at: str,
    ) -> BrowserLaneLease | None:
        _validate_lane_write(
            lane_key=lane_key,
            owner_id=owner_id,
            owner_process_id=owner_process_id,
            process_boot_id=process_boot_id,
            runtime_run_id=runtime_run_id,
            operation_id=operation_id,
            operation_kind=operation_kind,
            now=acquired_at,
            lease_expires_at=lease_expires_at,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT *
                FROM runtime_control_browser_lanes
                WHERE lane_key = ?
                """,
                (lane_key,),
            ).fetchone()
            if row is not None and row["status"] == "active":
                connection.commit()
                return None
            fencing_token = (
                1 if row is None else int(row["fencing_token"]) + 1
            )
            connection.execute(
                """
                INSERT INTO runtime_control_browser_lanes (
                  lane_key, fencing_token, status, owner_id,
                  owner_process_id, process_boot_id, runtime_run_id,
                  operation_id, operation_kind, acquired_at,
                  heartbeat_at, lease_expires_at, released_at,
                  last_failure_code, updated_at
                )
                VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                ON CONFLICT(lane_key) DO UPDATE SET
                  fencing_token = excluded.fencing_token,
                  status = 'active',
                  owner_id = excluded.owner_id,
                  owner_process_id = excluded.owner_process_id,
                  process_boot_id = excluded.process_boot_id,
                  runtime_run_id = excluded.runtime_run_id,
                  operation_id = excluded.operation_id,
                  operation_kind = excluded.operation_kind,
                  acquired_at = excluded.acquired_at,
                  heartbeat_at = excluded.heartbeat_at,
                  lease_expires_at = excluded.lease_expires_at,
                  released_at = NULL,
                  last_failure_code = NULL,
                  updated_at = excluded.updated_at
                """,
                (
                    lane_key,
                    fencing_token,
                    owner_id,
                    owner_process_id,
                    process_boot_id,
                    runtime_run_id,
                    operation_id,
                    operation_kind,
                    acquired_at,
                    acquired_at,
                    lease_expires_at,
                    acquired_at,
                ),
            )
            acquired = connection.execute(
                """
                SELECT *
                FROM runtime_control_browser_lanes
                WHERE lane_key = ?
                """,
                (lane_key,),
            ).fetchone()
            connection.commit()
        return _lease_from_row(acquired)

    def heartbeat_browser_lane(
        self,
        *,
        lane_key: str,
        owner_id: str,
        fencing_token: int,
        heartbeat_at: str,
        lease_expires_at: str,
    ) -> BrowserLaneLease:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE runtime_control_browser_lanes
                SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE lane_key = ? AND owner_id = ?
                  AND fencing_token = ? AND status = 'active'
                  AND lease_expires_at > ?
                """,
                (
                    heartbeat_at,
                    lease_expires_at,
                    heartbeat_at,
                    lane_key,
                    owner_id,
                    fencing_token,
                    heartbeat_at,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeControlError("liepin_browser_lane_lost")
            row = connection.execute(
                """
                SELECT *
                FROM runtime_control_browser_lanes
                WHERE lane_key = ?
                """,
                (lane_key,),
            ).fetchone()
        return _lease_from_row(row)

    def release_browser_lane(
        self,
        *,
        lane_key: str,
        owner_id: str,
        fencing_token: int,
        released_at: str,
        status: Literal["completed", "failed"],
        failure_code: str | None = None,
    ) -> BrowserLaneSnapshot:
        if status == "completed" and failure_code is not None:
            raise ValueError("completed browser lane cannot retain a failure")
        if failure_code is not None and _SAFE_REASON.fullmatch(failure_code) is None:
            failure_code = "liepin_browser_lane_failed"
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE runtime_control_browser_lanes
                SET status = ?, lease_expires_at = NULL,
                    released_at = ?, last_failure_code = ?, updated_at = ?
                WHERE lane_key = ? AND owner_id = ?
                  AND fencing_token = ? AND status = 'active'
                """,
                (
                    status,
                    released_at,
                    failure_code,
                    released_at,
                    lane_key,
                    owner_id,
                    fencing_token,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeControlError("liepin_browser_lane_lost")
            row = connection.execute(
                """
                SELECT *
                FROM runtime_control_browser_lanes
                WHERE lane_key = ?
                """,
                (lane_key,),
            ).fetchone()
        return _snapshot_from_row(row)

    def mark_browser_lane_unresolved(
        self,
        *,
        lane_key: str,
        owner_id: str,
        fencing_token: int,
        failure_code: str,
        observed_at: str,
    ) -> BrowserLaneSnapshot:
        if _SAFE_REASON.fullmatch(failure_code) is None:
            raise ValueError("browser_lane_failure_code_invalid")
        with self._connect() as connection, connection:
            updated = connection.execute(
                """
                UPDATE runtime_control_browser_lanes
                SET last_failure_code = ?, updated_at = ?
                WHERE lane_key = ? AND owner_id = ?
                  AND fencing_token = ? AND status = 'active'
                """,
                (
                    failure_code,
                    observed_at,
                    lane_key,
                    owner_id,
                    fencing_token,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeControlError("liepin_browser_lane_lost")
            row = connection.execute(
                """
                SELECT * FROM runtime_control_browser_lanes
                WHERE lane_key = ?
                """,
                (lane_key,),
            ).fetchone()
        return _snapshot_from_row(row)

    def resolve_expired_browser_lane_after_reconciliation(
        self,
        *,
        fencing_token: int,
        runtime_run_id: str,
        operation_id: str,
        outcome: Literal[
            "no_effect",
            "terminal_observed",
            "unknown",
        ],
        history_conclusion: str | None,
        evidence_ref: str,
        evidence_digest: str,
        resolved_at: str,
    ) -> bool:
        """Terminalize an orphaned fence only from durable reconciliation."""
        if (
            _SAFE_ID.fullmatch(evidence_ref) is None
            or re.fullmatch(r"[0-9a-f]{64}", evidence_digest) is None
        ):
            raise ValueError("browser_lane_resolution_evidence_invalid")
        if outcome == "no_effect" and history_conclusion not in {
            None,
            "accepted_no_dispatch",
        }:
            raise ValueError("browser_lane_no_effect_evidence_invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            lane = connection.execute(
                """
                SELECT * FROM runtime_control_browser_lanes
                WHERE lane_key = 'liepin_browser'
                """
            ).fetchone()
            if (
                lane is None
                or lane["status"] != "active"
                or int(lane["fencing_token"]) != fencing_token
                or lane["runtime_run_id"] != runtime_run_id
                or lane["operation_id"] != operation_id
                or lane["lease_expires_at"] > resolved_at
            ):
                connection.rollback()
                raise RuntimeControlError(
                    "liepin_browser_lane_resolution_conflict"
                )
            operation = connection.execute(
                """
                SELECT operation_phase, conclusive_observation_ref,
                       source_operation_disposition
                FROM runtime_control_source_operations
                WHERE runtime_run_id = ? AND operation_id = ?
                """,
                (runtime_run_id, operation_id),
            ).fetchone()
            if operation is None:
                connection.rollback()
                raise RuntimeControlError(
                    "liepin_browser_lane_source_operation_missing"
                )
            reconciliation = connection.execute(
                """
                SELECT *
                FROM runtime_control_source_reconciliations
                WHERE runtime_run_id = ? AND operation_id = ?
                  AND history_result_ref = ?
                  AND history_result_digest = ?
                  AND (
                    history_conclusion = ?
                    OR (
                      history_conclusion IS NULL
                      AND ? IS NULL
                    )
                  )
                ORDER BY committed_reconciliation_revision DESC
                LIMIT 1
                """,
                (
                    runtime_run_id,
                    operation_id,
                    evidence_ref,
                    evidence_digest,
                    history_conclusion,
                    history_conclusion,
                ),
            ).fetchone()
            expected_decision = {
                "unknown": "unresolved",
                "no_effect": "no_dispatch_proved",
                "terminal_observed": "conclusive_observation",
            }[outcome]
            if (
                reconciliation is None
                or reconciliation["decision_kind"]
                != expected_decision
            ):
                connection.rollback()
                raise RuntimeControlError(
                    "liepin_browser_lane_reconciliation_evidence_missing"
                )
            if outcome == "no_effect" and (
                reconciliation["retry_posture"] != "safe_retry"
                or reconciliation["dispatch_intent_ref"] is not None
            ):
                connection.rollback()
                raise RuntimeControlError(
                    "liepin_browser_lane_no_effect_evidence_missing"
                )
            if outcome == "terminal_observed" and (
                operation["operation_phase"]
                not in {"reconciled", "main_committed"}
                or operation["conclusive_observation_ref"] is None
                or operation["source_operation_disposition"]
                == "reconciliation_unknown"
                or reconciliation["conclusive_observation_ref"]
                != operation["conclusive_observation_ref"]
            ):
                connection.rollback()
                raise RuntimeControlError(
                    "liepin_browser_lane_terminal_evidence_missing"
                )
            connection.execute(
                """
                INSERT INTO runtime_control_browser_lane_resolutions (
                  resolution_id, lane_key, fencing_token, runtime_run_id,
                  operation_id, outcome, history_conclusion, evidence_ref,
                  evidence_digest, resolved_at
                )
                VALUES (?, 'liepin_browser', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"lane-resolution-{uuid4().hex}",
                    fencing_token,
                    runtime_run_id,
                    operation_id,
                    outcome,
                    history_conclusion,
                    evidence_ref,
                    evidence_digest,
                    resolved_at,
                ),
            )
            if outcome == "unknown":
                connection.commit()
                return False
            connection.execute(
                """
                UPDATE runtime_control_browser_lanes
                SET status = 'failed', lease_expires_at = NULL,
                    released_at = ?, last_failure_code = ?,
                    updated_at = ?
                WHERE lane_key = 'liepin_browser'
                  AND fencing_token = ? AND status = 'active'
                """,
                (
                    resolved_at,
                    "liepin_browser_lane_reconciled",
                    resolved_at,
                    fencing_token,
                ),
            )
            connection.commit()
        return True

    def get_browser_lane(
        self,
        lane_key: str = LIEPIN_BROWSER_LANE,
    ) -> BrowserLaneSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM runtime_control_browser_lanes
                WHERE lane_key = ?
                """,
                (lane_key,),
            ).fetchone()
        return None if row is None else _snapshot_from_row(row)

    def reconcile_expired_browser_lane_from_durable_evidence(
        self,
        *,
        observed_at: str,
    ) -> Literal[
        "not_applicable",
        "released",
        "needs_attention",
    ]:
        lane = self.get_browser_lane()
        if (
            lane is None
            or lane.status != "active"
            or lane.lease_expires_at is None
            or lane.lease_expires_at > observed_at
            or lane.runtime_run_id is None
        ):
            return "not_applicable"
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM runtime_control_source_reconciliations
                WHERE runtime_run_id = ? AND operation_id = ?
                ORDER BY committed_reconciliation_revision DESC
                LIMIT 1
                """,
                (lane.runtime_run_id, lane.operation_id),
            ).fetchone()
            if row is None or row["decision_kind"] == "unresolved":
                connection.execute(
                    """
                    UPDATE runtime_control_browser_lanes
                    SET last_failure_code = ?, updated_at = ?
                    WHERE lane_key = ? AND fencing_token = ?
                      AND status = 'active'
                    """,
                    (
                        "liepin_browser_lane_reconciliation_required",
                        observed_at,
                        lane.lane_key,
                        lane.fencing_token,
                    ),
                )
                connection.commit()
                return "needs_attention"
            outcome: Literal["no_effect", "terminal_observed"]
            if row["decision_kind"] == "no_dispatch_proved":
                outcome = "no_effect"
            elif row["decision_kind"] == "conclusive_observation":
                outcome = "terminal_observed"
            else:
                return "needs_attention"
            history_conclusion = row["history_conclusion"]
            evidence_ref = str(row["history_result_ref"])
            evidence_digest = str(row["history_result_digest"])
        released = self.resolve_expired_browser_lane_after_reconciliation(
            fencing_token=lane.fencing_token,
            runtime_run_id=lane.runtime_run_id,
            operation_id=lane.operation_id,
            outcome=outcome,
            history_conclusion=history_conclusion,
            evidence_ref=evidence_ref,
            evidence_digest=evidence_digest,
            resolved_at=observed_at,
        )
        return "released" if released else "needs_attention"

    def resolve_browser_lane_from_conclusive_observation(
        self,
        *,
        runtime_run_id: str,
        operation_id: str,
        resolved_at: str,
    ) -> bool:
        """Release the exact active fence after its operation is observed."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            lane = connection.execute(
                """
                SELECT * FROM runtime_control_browser_lanes
                WHERE lane_key = 'liepin_browser'
                """
            ).fetchone()
            operation = connection.execute(
                """
                SELECT * FROM runtime_control_source_operations
                WHERE runtime_run_id = ? AND operation_id = ?
                """,
                (runtime_run_id, operation_id),
            ).fetchone()
            if (
                lane is None
                or lane["status"] != "active"
                or lane["runtime_run_id"] != runtime_run_id
                or lane["operation_id"] != operation_id
                or operation is None
                or operation["operation_phase"]
                not in {"observed", "main_committed"}
                or operation["conclusive_observation_ref"] is None
                or operation["source_operation_disposition"]
                == "reconciliation_unknown"
            ):
                connection.rollback()
                return False
            evidence_ref = str(
                operation["conclusive_observation_ref"]
            )
            evidence_digest = sha256(
                evidence_ref.encode()
            ).hexdigest()
            connection.execute(
                """
                INSERT INTO runtime_control_browser_lane_resolutions (
                  resolution_id, lane_key, fencing_token, runtime_run_id,
                  operation_id, outcome, history_conclusion, evidence_ref,
                  evidence_digest, resolved_at
                )
                VALUES (?, 'liepin_browser', ?, ?, ?,
                        'terminal_observed', 'observed_result',
                        ?, ?, ?)
                """,
                (
                    f"lane-resolution-{uuid4().hex}",
                    int(lane["fencing_token"]),
                    runtime_run_id,
                    operation_id,
                    evidence_ref,
                    evidence_digest,
                    resolved_at,
                ),
            )
            connection.execute(
                """
                UPDATE runtime_control_browser_lanes
                SET status = 'failed', lease_expires_at = NULL,
                    released_at = ?,
                    last_failure_code = 'liepin_browser_lane_reconciled',
                    updated_at = ?
                WHERE lane_key = 'liepin_browser'
                  AND fencing_token = ? AND status = 'active'
                """,
                (
                    resolved_at,
                    resolved_at,
                    int(lane["fencing_token"]),
                ),
            )
            connection.commit()
        return True


def create_browser_lane_schema(connection) -> None:
    for statement in BROWSER_LANE_SCHEMA_STATEMENTS:
        connection.execute(statement)


def _validate_lane_write(
    *,
    lane_key: str,
    owner_id: str,
    owner_process_id: int,
    process_boot_id: str,
    runtime_run_id: str | None,
    operation_id: str,
    operation_kind: str,
    now: str,
    lease_expires_at: str,
) -> None:
    if lane_key != LIEPIN_BROWSER_LANE:
        raise ValueError("browser lane key is invalid")
    for value, label in (
        (owner_id, "owner_id"),
        (process_boot_id, "process_boot_id"),
        (operation_id, "operation_id"),
    ):
        _require_safe_id(value, label)
    if runtime_run_id is not None:
        _require_safe_id(runtime_run_id, "runtime_run_id")
    if owner_process_id < 1:
        raise ValueError("owner_process_id is invalid")
    if operation_kind not in {
        "cards",
        "details",
        "continuation",
        "recheck",
        "prepare_readiness",
    }:
        raise ValueError("operation_kind is invalid")
    if lease_expires_at <= now:
        raise ValueError("browser lane expiry is invalid")


def _require_safe_id(value: str, label: str) -> None:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")


def _lease_from_row(row) -> BrowserLaneLease:
    if row is None or row["status"] != "active":
        raise RuntimeControlError("liepin_browser_lane_state_invalid")
    return BrowserLaneLease(
        lane_key=str(row["lane_key"]),
        fencing_token=int(row["fencing_token"]),
        owner_id=str(row["owner_id"]),
        owner_process_id=int(row["owner_process_id"]),
        process_boot_id=str(row["process_boot_id"]),
        runtime_run_id=row["runtime_run_id"],
        operation_id=str(row["operation_id"]),
        operation_kind=row["operation_kind"],
        acquired_at=str(row["acquired_at"]),
        heartbeat_at=str(row["heartbeat_at"]),
        lease_expires_at=str(row["lease_expires_at"]),
    )


def _snapshot_from_row(row) -> BrowserLaneSnapshot:
    if row is None:
        raise RuntimeControlError("liepin_browser_lane_state_invalid")
    return BrowserLaneSnapshot(
        lane_key=str(row["lane_key"]),
        fencing_token=int(row["fencing_token"]),
        status=str(row["status"]),
        owner_id=str(row["owner_id"]),
        owner_process_id=int(row["owner_process_id"]),
        process_boot_id=str(row["process_boot_id"]),
        runtime_run_id=row["runtime_run_id"],
        operation_id=str(row["operation_id"]),
        operation_kind=str(row["operation_kind"]),
        acquired_at=str(row["acquired_at"]),
        heartbeat_at=str(row["heartbeat_at"]),
        lease_expires_at=row["lease_expires_at"],
        released_at=row["released_at"],
        last_failure_code=row["last_failure_code"],
        updated_at=str(row["updated_at"]),
    )


def _safe_failure_code(error: object) -> str | None:
    if error is None:
        return None
    reason = getattr(error, "reason_code", None)
    if isinstance(reason, str) and _SAFE_REASON.fullmatch(reason):
        return reason
    text = str(error)
    if _SAFE_REASON.fullmatch(text):
        return text
    return "liepin_browser_lane_effect_failed"


__all__ = [
    "BROWSER_LANE_SCHEMA_STATEMENTS",
    "BrowserLaneBusyError",
    "BrowserLaneGuard",
    "BrowserLaneLease",
    "BrowserLaneSnapshot",
    "BrowserLaneStoreMixin",
    "LIEPIN_BROWSER_LANE",
    "create_browser_lane_schema",
]
