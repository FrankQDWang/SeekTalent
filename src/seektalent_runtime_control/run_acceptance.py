from __future__ import annotations

import json
import sqlite3

from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeControlEventInput, RuntimeRunRecord, RuntimeRunSnapshot


RUN_ACCEPTANCE_JOINS = """
JOIN runtime_control_snapshots AS acceptance_snapshot ON acceptance_snapshot.runtime_run_id = run.runtime_run_id
  AND acceptance_snapshot.latest_event_seq > 0
JOIN runtime_control_events AS initial_event ON initial_event.runtime_run_id = run.runtime_run_id
  AND initial_event.event_seq = 1
  AND initial_event.event_type = 'runtime_run_queued'
  AND initial_event.stage = 'queued'
  AND initial_event.status = 'queued'
  AND initial_event.idempotency_key = 'runtime-run-queued:' || run.runtime_run_id
"""


def normalize_run_record(run: RuntimeRunRecord) -> RuntimeRunRecord:
    return run.model_copy(
        update={
            "run_intent_id": run.run_intent_id or run.runtime_run_id,
            "start_idempotency_key": run.start_idempotency_key or run.run_intent_id or run.runtime_run_id,
            "run_kind": run.run_kind or "primary",
        }
    )


def validate_run_acceptance(
    run: RuntimeRunRecord,
    *,
    initial_event: RuntimeControlEventInput,
    snapshot: RuntimeRunSnapshot,
) -> None:
    if (
        run.status != "queued"
        or run.current_stage != "queued"
        or run.latest_event_seq != 0
        or initial_event.runtime_run_id != run.runtime_run_id
        or initial_event.event_type != "runtime_run_queued"
        or initial_event.stage != "queued"
        or initial_event.status != "queued"
        or initial_event.idempotency_key != f"runtime-run-queued:{run.runtime_run_id}"
        or snapshot.runtime_run_id != run.runtime_run_id
        or snapshot.status != "queued"
        or snapshot.current_stage != "queued"
        or snapshot.latest_event_seq != 0
    ):
        raise RuntimeControlError("runtime_run_acceptance_invalid")


def existing_run_for_start(conn: sqlite3.Connection, run: RuntimeRunRecord) -> sqlite3.Row | None:
    existing_by_intent = conn.execute(
        "SELECT * FROM runtime_control_runs WHERE run_intent_id = ?",
        (run.run_intent_id,),
    ).fetchone()
    existing_by_start_key = conn.execute(
        "SELECT * FROM runtime_control_runs WHERE start_idempotency_key = ?",
        (run.start_idempotency_key,),
    ).fetchone()
    if existing_by_intent is not None and existing_by_start_key is not None:
        if existing_by_intent["runtime_run_id"] != existing_by_start_key["runtime_run_id"]:
            raise RuntimeControlError("runtime_run_start_idempotency_conflict")
        return existing_by_intent
    return existing_by_intent or existing_by_start_key


def insert_run(conn: sqlite3.Connection, run: RuntimeRunRecord) -> None:
    conn.execute(
        """
        INSERT INTO runtime_control_runs (
            runtime_run_id, run_intent_id, start_idempotency_key, run_kind,
            agent_conversation_id, workbench_session_id,
            approved_requirement_revision_id, status, current_stage, current_round,
            latest_checkpoint_id, latest_event_seq, source_ids_json, stop_reason_code,
            created_at, updated_at, completed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.runtime_run_id,
            run.run_intent_id,
            run.start_idempotency_key,
            run.run_kind,
            run.agent_conversation_id,
            run.workbench_session_id,
            run.approved_requirement_revision_id,
            run.status,
            run.current_stage,
            run.current_round,
            run.latest_checkpoint_id,
            run.latest_event_seq,
            _json(run.source_ids),
            run.stop_reason_code,
            run.created_at,
            run.updated_at,
            run.completed_at,
        ),
    )


def accepted_run_row(conn: sqlite3.Connection, runtime_run_id: str) -> sqlite3.Row | None:
    return conn.execute(
        f"""
        SELECT run.*
        FROM runtime_control_runs AS run
        {RUN_ACCEPTANCE_JOINS}
        WHERE run.runtime_run_id = ? AND run.latest_event_seq > 0
        """,
        (runtime_run_id,),
    ).fetchone()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
