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
    if checkpoint.schema_version == "runtime-control-checkpoint/v1":
        if (
            existing is not None
            and existing["schema_version"] == "runtime-control-checkpoint/v2"
        ):
            _adopt_existing_v2_for_legacy_replay(checkpoint, existing)
        else:
            from seektalent_runtime_control.store import (
                _upgrade_legacy_checkpoint_in_transaction,
            )

            _upgrade_legacy_checkpoint_in_transaction(conn, checkpoint)
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
        checkpoint.state_revision,
        checkpoint.accepted_requirement_revision_id,
        checkpoint.control_state_hash,
        checkpoint.candidate_truth_revision,
        checkpoint.candidate_truth_hash,
        checkpoint.detail_claim_revision,
        checkpoint.detail_claim_hash,
        _json(checkpoint.durable_refs),
        _json(checkpoint.field_bytes),
        checkpoint.serialization_latency_ms,
        checkpoint.projection_latency_ms,
        checkpoint.payload_size_bytes,
        int(checkpoint.is_final_manifest),
    )
    if existing is None:
        conn.execute(
            """
            INSERT INTO runtime_control_checkpoints (
                checkpoint_id, runtime_run_id, stage, round_no, safe_boundary,
                run_state_json, source_plan_json, pending_commands_json,
                artifact_manifest_ref, schema_version, created_at,
                state_revision, accepted_requirement_revision_id, control_state_hash,
                candidate_truth_revision, candidate_truth_hash,
                detail_claim_revision, detail_claim_hash, durable_refs_json,
                field_bytes_json, serialization_latency_ms, projection_latency_ms,
                payload_size_bytes, is_final_manifest
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        "state_revision",
        "accepted_requirement_revision_id",
        "control_state_hash",
        "candidate_truth_revision",
        "candidate_truth_hash",
        "detail_claim_revision",
        "detail_claim_hash",
        "durable_refs_json",
        "field_bytes_json",
        "serialization_latency_ms",
        "projection_latency_ms",
        "payload_size_bytes",
        "is_final_manifest",
    )) != values:
        raise RuntimeControlError("runtime_checkpoint_replay_conflict")


def _adopt_existing_v2_for_legacy_replay(
    checkpoint: RuntimeCheckpoint,
    existing: sqlite3.Row,
) -> None:
    from seektalent_runtime_control.checkpoint_v2 import (
        candidate_truth_hash,
        legacy_checkpoint_projection,
    )
    from seektalent_runtime_control.store import _checkpoint_from_row

    projection = legacy_checkpoint_projection(checkpoint.run_state)
    stored = _checkpoint_from_row(existing)
    if (
        checkpoint.runtime_run_id != stored.runtime_run_id
        or checkpoint.stage != stored.stage
        or checkpoint.round_no != stored.round_no
        or checkpoint.safe_boundary != stored.safe_boundary
        or checkpoint.source_plan != stored.source_plan
        or checkpoint.pending_commands != stored.pending_commands
        or checkpoint.artifact_manifest_ref != stored.artifact_manifest_ref
        or checkpoint.created_at != stored.created_at
        or projection.control_state_hash != stored.control_state_hash
        or candidate_truth_hash(projection.candidate_state)
        != stored.candidate_truth_hash
    ):
        raise RuntimeControlError("runtime_checkpoint_replay_conflict")
    for field_name in RuntimeCheckpoint.model_fields:
        setattr(checkpoint, field_name, getattr(stored, field_name))


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = ["write_checkpoint_participant"]
