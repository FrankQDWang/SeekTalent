"""Bounded durable workflow transitions between compression checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
import sqlite3
from typing import Literal

from seektalent_runtime_control.errors import RuntimeControlError


WORKFLOW_TRANSITION_SCHEMA_VERSION = (
    "runtime-control-workflow-transition/v1"
)
MAX_WORKFLOW_TRANSITION_BYTES = 64 * 1024
MAX_WORKFLOW_ROUND_BARRIER_LOGICAL_BYTES = 64 * 1024
WorkflowStepKind = Literal[
    "source_dispatch",
    "detail_queued",
    "detail_dispatch",
    "lane_completed",
]
WorkflowTransitionStatus = Literal[
    "active",
    "superseded",
    "checkpointed",
]

_STEP_KINDS = frozenset(
    {
        "source_dispatch",
        "detail_queued",
        "detail_dispatch",
        "lane_completed",
    }
)
_STATUSES = frozenset({"active", "superseded", "checkpointed"})
_OPAQUE = re.compile(r"[^\x00-\x1f\x7f]+")
_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "runState",
        "candidateStore",
        "normalizedStore",
        "scorecardsByResumeId",
        "candidates",
        "resume",
    }
)


WORKFLOW_TRANSITION_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS runtime_control_workflow_transitions (
      transition_id TEXT PRIMARY KEY,
      runtime_run_id TEXT NOT NULL,
      source_lane_run_id TEXT NOT NULL,
      query_instance_id TEXT NOT NULL,
      parent_transition_id TEXT,
      base_checkpoint_id TEXT NOT NULL,
      round_no INTEGER NOT NULL,
      step_kind TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      payload_hash TEXT NOT NULL,
      payload_size_bytes INTEGER NOT NULL,
      status TEXT NOT NULL,
      executor_attempt_no INTEGER NOT NULL,
      created_at TEXT NOT NULL,
      settled_at TEXT,
      CHECK (round_no >= 1),
      CHECK (
        step_kind IN (
          'source_dispatch', 'detail_queued', 'detail_dispatch',
          'lane_completed'
        )
      ),
      CHECK (length(payload_hash) = 64),
      CHECK (payload_size_bytes > 0 AND payload_size_bytes <= 65536),
      CHECK (status IN ('active', 'superseded', 'checkpointed')),
      CHECK (
        (status = 'active' AND settled_at IS NULL)
        OR (status != 'active' AND settled_at IS NOT NULL)
      )
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
      runtime_control_workflow_transitions_one_active_lane
    ON runtime_control_workflow_transitions(
      runtime_run_id, source_lane_run_id, query_instance_id
    )
    WHERE status = 'active'
    """,
    """
    CREATE INDEX IF NOT EXISTS
      runtime_control_workflow_transitions_parent
    ON runtime_control_workflow_transitions(
      runtime_run_id, source_lane_run_id, query_instance_id,
      parent_transition_id
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
      runtime_control_workflow_transitions_one_way
    BEFORE UPDATE ON runtime_control_workflow_transitions
    WHEN NOT (
      OLD.status = 'active'
      AND NEW.status IN ('superseded', 'checkpointed')
      AND NEW.transition_id = OLD.transition_id
      AND NEW.runtime_run_id = OLD.runtime_run_id
      AND NEW.source_lane_run_id = OLD.source_lane_run_id
      AND NEW.query_instance_id = OLD.query_instance_id
      AND NEW.parent_transition_id IS OLD.parent_transition_id
      AND NEW.base_checkpoint_id = OLD.base_checkpoint_id
      AND NEW.round_no = OLD.round_no
      AND NEW.step_kind = OLD.step_kind
      AND NEW.payload_json = OLD.payload_json
      AND NEW.payload_hash = OLD.payload_hash
      AND NEW.payload_size_bytes = OLD.payload_size_bytes
      AND NEW.executor_attempt_no = OLD.executor_attempt_no
      AND NEW.created_at = OLD.created_at
      AND NEW.settled_at IS NOT NULL
    )
    BEGIN
      SELECT RAISE(ABORT, 'runtime_workflow_transition_immutable');
    END
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_control_workflow_round_barriers (
      runtime_run_id TEXT NOT NULL,
      round_no INTEGER NOT NULL,
      base_checkpoint_id TEXT NOT NULL,
      work_plan_artifact_ref TEXT NOT NULL,
      work_plan_artifact_hash TEXT NOT NULL,
      expected_lane_count INTEGER NOT NULL,
      lane_set_hash TEXT NOT NULL,
      status TEXT NOT NULL,
      executor_attempt_no INTEGER NOT NULL,
      created_at TEXT NOT NULL,
      settled_at TEXT,
      PRIMARY KEY (runtime_run_id, round_no),
      CHECK (round_no >= 1),
      CHECK (expected_lane_count >= 1),
      CHECK (length(work_plan_artifact_hash) = 64),
      CHECK (length(lane_set_hash) = 64),
      CHECK (status IN ('active', 'checkpointed')),
      CHECK (
        (status = 'active' AND settled_at IS NULL)
        OR (status = 'checkpointed' AND settled_at IS NOT NULL)
      )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_control_workflow_barrier_lanes (
      runtime_run_id TEXT NOT NULL,
      round_no INTEGER NOT NULL,
      source_lane_run_id TEXT NOT NULL,
      query_instance_id TEXT NOT NULL,
      status TEXT NOT NULL,
      settled_at TEXT,
      PRIMARY KEY (
        runtime_run_id, round_no, source_lane_run_id, query_instance_id
      ),
      CHECK (status IN ('pending', 'active', 'completed', 'skipped')),
      CHECK (
        (status IN ('pending', 'active') AND settled_at IS NULL)
        OR (status IN ('completed', 'skipped') AND settled_at IS NOT NULL)
      )
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS runtime_control_workflow_barrier_lanes_status
    ON runtime_control_workflow_barrier_lanes(
      runtime_run_id, round_no, status
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS
      runtime_control_workflow_transitions_no_delete
    BEFORE DELETE ON runtime_control_workflow_transitions
    BEGIN
      SELECT RAISE(ABORT, 'runtime_workflow_transition_immutable');
    END
    """,
)


@dataclass(frozen=True, slots=True)
class RuntimeWorkflowTransition:
    transition_id: str
    runtime_run_id: str
    source_lane_run_id: str
    query_instance_id: str
    parent_transition_id: str | None
    base_checkpoint_id: str
    round_no: int
    step_kind: WorkflowStepKind
    continuation: dict[str, object]
    artifact_refs: tuple[str, ...]
    payload_hash: str
    payload_size_bytes: int
    status: WorkflowTransitionStatus
    executor_attempt_no: int
    created_at: str
    settled_at: str | None

    def resume_payload(self) -> dict[str, object]:
        return {
            "schemaVersion": WORKFLOW_TRANSITION_SCHEMA_VERSION,
            "transitionId": self.transition_id,
            "sourceLaneRunId": self.source_lane_run_id,
            "queryInstanceId": self.query_instance_id,
            "parentTransitionId": self.parent_transition_id,
            "baseCheckpointId": self.base_checkpoint_id,
            "roundNo": self.round_no,
            "stepKind": self.step_kind,
            "continuation": self.continuation,
            "artifactRefs": list(self.artifact_refs),
            "payloadHash": self.payload_hash,
            "payloadSizeBytes": self.payload_size_bytes,
        }


@dataclass(frozen=True, slots=True)
class RuntimeWorkflowLaneResume:
    round_no: int
    base_checkpoint_id: str
    source_lane_run_id: str
    query_instance_id: str
    barrier_status: Literal["pending", "active", "completed", "skipped"]
    work_plan_artifact_ref: str
    work_plan_artifact_hash: str
    transitions: tuple[RuntimeWorkflowTransition, ...]

    def resume_payload(self) -> dict[str, object]:
        return {
            "roundNo": self.round_no,
            "baseCheckpointId": self.base_checkpoint_id,
            "sourceLaneRunId": self.source_lane_run_id,
            "queryInstanceId": self.query_instance_id,
            "barrierStatus": self.barrier_status,
            "workPlanArtifactRef": self.work_plan_artifact_ref,
            "workPlanArtifactHash": self.work_plan_artifact_hash,
            "transitions": [
                transition.resume_payload()
                for transition in self.transitions
            ],
        }


@dataclass(frozen=True, slots=True)
class WorkflowTransitionWriteResult:
    transition: RuntimeWorkflowTransition
    transaction_duration_ms: float
    inserted: bool


@dataclass(frozen=True, slots=True)
class WorkflowRoundBarrierWriteResult:
    lane_set_hash: str
    logical_payload_hash: str
    logical_payload_size_bytes: int
    transaction_duration_ms: float
    inserted: bool

    @property
    def committed_logical_payload_bytes(self) -> int:
        return self.logical_payload_size_bytes if self.inserted else 0


def create_workflow_transition_schema(
    connection: sqlite3.Connection,
) -> None:
    for statement in WORKFLOW_TRANSITION_SCHEMA_STATEMENTS:
        connection.execute(statement)


def workflow_transition_payload(
    *,
    continuation: dict[str, object],
    artifact_refs: tuple[str, ...],
) -> tuple[str, str, int]:
    if not isinstance(continuation, dict) or not continuation:
        raise RuntimeControlError(
            "runtime_workflow_transition_continuation_invalid"
        )
    _reject_large_state_keys(continuation)
    if (
        not isinstance(artifact_refs, tuple)
        or len(artifact_refs) != len(set(artifact_refs))
    ):
        raise RuntimeControlError(
            "runtime_workflow_transition_artifact_refs_invalid"
        )
    for artifact_ref in artifact_refs:
        _require_opaque(
            artifact_ref,
            "runtime_workflow_transition_artifact_ref_invalid",
            max_bytes=256,
        )
    payload = {
        "artifactRefs": list(artifact_refs),
        "continuation": continuation,
    }
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise RuntimeControlError(
            "runtime_workflow_transition_continuation_invalid"
        ) from None
    if len(encoded) > MAX_WORKFLOW_TRANSITION_BYTES:
        raise RuntimeControlError(
            "runtime_workflow_transition_payload_too_large"
        )
    return encoded.decode("utf-8"), sha256(encoded).hexdigest(), len(
        encoded
    )


def workflow_round_barrier_logical_payload(
    *,
    runtime_run_id: str,
    round_no: int,
    base_checkpoint_id: str,
    work_plan_artifact_ref: str,
    work_plan_artifact_hash: str,
    lane_set_hash: str,
    lanes: tuple[tuple[str, str], ...],
    executor_attempt_no: int,
    created_at: str,
) -> tuple[str, int]:
    """Hash the exact logical barrier and keyed lane rows committed by SQLite."""
    payload = {
        "barrier": {
            "baseCheckpointId": base_checkpoint_id,
            "createdAt": created_at,
            "executorAttemptNo": executor_attempt_no,
            "expectedLaneCount": len(lanes),
            "laneSetHash": lane_set_hash,
            "roundNo": round_no,
            "runtimeRunId": runtime_run_id,
            "settledAt": None,
            "status": "active",
            "workPlanArtifactHash": work_plan_artifact_hash,
            "workPlanArtifactRef": work_plan_artifact_ref,
        },
        "lanes": [
            {
                "queryInstanceId": query_instance_id,
                "roundNo": round_no,
                "runtimeRunId": runtime_run_id,
                "settledAt": None,
                "sourceLaneRunId": source_lane_run_id,
                "status": "pending",
            }
            for source_lane_run_id, query_instance_id in lanes
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > MAX_WORKFLOW_ROUND_BARRIER_LOGICAL_BYTES:
        raise RuntimeControlError(
            "runtime_workflow_barrier_payload_too_large"
        )
    return sha256(encoded).hexdigest(), len(encoded)


def workflow_transition_from_row(
    row: sqlite3.Row,
) -> RuntimeWorkflowTransition:
    try:
        transition_id = str(row["transition_id"])
        runtime_run_id = str(row["runtime_run_id"])
        source_lane_run_id = str(row["source_lane_run_id"])
        query_instance_id = str(row["query_instance_id"])
        parent_transition_id = row["parent_transition_id"]
        base_checkpoint_id = str(row["base_checkpoint_id"])
        round_no = int(row["round_no"])
        step_kind = row["step_kind"]
        status = row["status"]
        executor_attempt_no = int(row["executor_attempt_no"])
        payload_json = str(row["payload_json"])
        decoded = json.loads(payload_json)
        continuation = decoded["continuation"]
        artifact_refs = tuple(decoded["artifactRefs"])
        canonical, payload_hash, payload_size = (
            workflow_transition_payload(
                continuation=continuation,
                artifact_refs=artifact_refs,
            )
        )
        if (
            canonical != payload_json
            or payload_hash != row["payload_hash"]
            or payload_size != int(row["payload_size_bytes"])
        ):
            raise ValueError
        _require_opaque(
            transition_id,
            "runtime_workflow_transition_invalid",
            max_bytes=96,
        )
        _require_opaque(
            runtime_run_id,
            "runtime_workflow_transition_invalid",
            max_bytes=96,
        )
        _require_opaque(
            source_lane_run_id,
            "runtime_workflow_transition_invalid",
            max_bytes=256,
        )
        _require_opaque(
            query_instance_id,
            "runtime_workflow_transition_invalid",
            max_bytes=96,
        )
        _require_opaque(
            base_checkpoint_id,
            "runtime_workflow_transition_invalid",
            max_bytes=96,
        )
        if parent_transition_id is not None:
            _require_opaque(
                parent_transition_id,
                "runtime_workflow_transition_invalid",
                max_bytes=96,
            )
        if (
            round_no < 1
            or step_kind not in _STEP_KINDS
            or status not in _STATUSES
            or executor_attempt_no < 1
        ):
            raise ValueError
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        RuntimeControlError,
    ):
        raise RuntimeControlError(
            "runtime_workflow_transition_corrupt"
        ) from None
    return RuntimeWorkflowTransition(
        transition_id=transition_id,
        runtime_run_id=runtime_run_id,
        source_lane_run_id=source_lane_run_id,
        query_instance_id=query_instance_id,
        parent_transition_id=parent_transition_id,
        base_checkpoint_id=base_checkpoint_id,
        round_no=round_no,
        step_kind=step_kind,
        continuation=continuation,
        artifact_refs=artifact_refs,
        payload_hash=payload_hash,
        payload_size_bytes=payload_size,
        status=status,
        executor_attempt_no=executor_attempt_no,
        created_at=str(row["created_at"]),
        settled_at=row["settled_at"],
    )


def validate_transition_identity(
    *,
    runtime_run_id: str,
    source_lane_run_id: str,
    query_instance_id: str,
    round_no: int,
    step_kind: str,
) -> None:
    _require_opaque(
        runtime_run_id,
        "runtime_workflow_transition_run_id_invalid",
        max_bytes=96,
    )
    _require_opaque(
        source_lane_run_id,
        "runtime_workflow_transition_lane_id_invalid",
        max_bytes=256,
    )
    _require_opaque(
        query_instance_id,
        "runtime_workflow_transition_query_id_invalid",
        max_bytes=96,
    )
    if isinstance(round_no, bool) or not isinstance(round_no, int) or round_no < 1:
        raise RuntimeControlError(
            "runtime_workflow_transition_round_invalid"
        )
    if step_kind not in _STEP_KINDS:
        raise RuntimeControlError(
            "runtime_workflow_transition_step_invalid"
        )


def _reject_large_state_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or key in _FORBIDDEN_PAYLOAD_KEYS:
                raise RuntimeControlError(
                    "runtime_workflow_transition_large_state_forbidden"
                )
            _reject_large_state_keys(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _reject_large_state_keys(item)


def _require_opaque(
    value: object,
    reason_code: str,
    *,
    max_bytes: int,
) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > max_bytes
        or _OPAQUE.fullmatch(value) is None
    ):
        raise RuntimeControlError(reason_code)


__all__ = [
    "MAX_WORKFLOW_ROUND_BARRIER_LOGICAL_BYTES",
    "MAX_WORKFLOW_TRANSITION_BYTES",
    "RuntimeWorkflowLaneResume",
    "RuntimeWorkflowTransition",
    "WorkflowTransitionWriteResult",
    "WorkflowRoundBarrierWriteResult",
    "create_workflow_transition_schema",
    "validate_transition_identity",
    "workflow_transition_from_row",
    "workflow_transition_payload",
    "workflow_round_barrier_logical_payload",
]
