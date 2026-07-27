"""Checkpoint/candidate writes that participate in a caller-owned transaction."""

from __future__ import annotations

import json
import sqlite3

from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeCheckpoint


def write_checkpoint_participant(
    conn: sqlite3.Connection,
    checkpoint: RuntimeCheckpoint,
) -> None:
    """Store exact checkpoint/candidate truth without owning transaction timing."""
    if not isinstance(conn, sqlite3.Connection) or not conn.in_transaction:
        raise RuntimeControlError("runtime_checkpoint_transaction_required")
    existing = conn.execute(
        "SELECT * FROM runtime_control_checkpoints WHERE checkpoint_id = ?",
        (checkpoint.checkpoint_id,),
    ).fetchone()
    values = (
        checkpoint.checkpoint_id,
        checkpoint.runtime_run_id,
        checkpoint.stage,
        checkpoint.round_no,
        checkpoint.safe_boundary,
        _json(checkpoint.run_state),
        _json(checkpoint.source_plan),
        _json(checkpoint.pending_commands),
        checkpoint.artifact_manifest_ref,
        checkpoint.schema_version,
        checkpoint.created_at,
    )
    if existing is None:
        conn.execute(
            """
            INSERT INTO runtime_control_checkpoints (
                checkpoint_id, runtime_run_id, stage, round_no, safe_boundary,
                run_state_json, source_plan_json, pending_commands_json,
                artifact_manifest_ref, schema_version, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
    elif tuple(existing[column] for column in (
        "checkpoint_id",
        "runtime_run_id",
        "stage",
        "round_no",
        "safe_boundary",
        "run_state_json",
        "source_plan_json",
        "pending_commands_json",
        "artifact_manifest_ref",
        "schema_version",
        "created_at",
    )) != values:
        raise RuntimeControlError("runtime_checkpoint_replay_conflict")
    from seektalent_runtime_control.store import _sync_candidate_truth_from_checkpoint

    _sync_candidate_truth_from_checkpoint(conn, checkpoint)


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = ["write_checkpoint_participant"]
