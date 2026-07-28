from __future__ import annotations

from hashlib import sha256
import json
from typing import TYPE_CHECKING

from seektalent.models import InputTruth, RunState, ScoringPolicy
from seektalent_runtime_control.checkpoint_v2 import (
    RUNTIME_CHECKPOINT_SCHEMA_V2,
    candidate_truth_hash,
)
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeCheckpoint

if TYPE_CHECKING:
    from seektalent_runtime_control.store import RuntimeControlStore


class RecoveryStateAssembler:
    """Rebuild a runtime view from each field's single durable owner."""

    def __init__(self, store: RuntimeControlStore) -> None:
        self.store = store

    def assemble(self, checkpoint: RuntimeCheckpoint) -> RunState:
        from seektalent_runtime_control.store import (
            _checkpoint_durable_owners_match,
        )

        if checkpoint.schema_version != RUNTIME_CHECKPOINT_SCHEMA_V2:
            raise RuntimeControlError("runtime_checkpoint_v1_requires_migration")
        run = self.store.get_run(checkpoint.runtime_run_id)
        if run.latest_checkpoint_id != checkpoint.checkpoint_id:
            raise RuntimeControlError("runtime_checkpoint_not_latest")
        approved = self.store.get_approved_requirement(
            checkpoint.accepted_requirement_revision_id or ""
        )
        snapshot = self.store.get_snapshot(runtime_run_id=checkpoint.runtime_run_id)
        raw_workflow_input = (
            snapshot.snapshot.get("workflowInput")
            if snapshot is not None
            else None
        )
        if not isinstance(raw_workflow_input, dict):
            raise RuntimeControlError("runtime_checkpoint_input_truth_missing")
        workflow_input: dict[str, object] = {
            key: value
            for key, value in raw_workflow_input.items()
            if isinstance(key, str)
        }
        job_title = " ".join(
            (_text(workflow_input.get("jobTitle")) or "").split()
        )
        jd = _text(workflow_input.get("jdText")) or ""
        notes = _text(workflow_input.get("notes")) or ""
        input_truth = InputTruth(
            job_title=job_title,
            jd=jd,
            notes=notes,
            job_title_sha256=sha256(job_title.encode("utf-8")).hexdigest(),
            jd_sha256=sha256(jd.encode("utf-8")).hexdigest(),
            notes_sha256=sha256(notes.encode("utf-8")).hexdigest(),
        )

        with self.store._connect() as conn:
            truth_row = conn.execute(
                """
                SELECT *
                FROM runtime_control_candidate_truth_state
                WHERE runtime_run_id = ?
                """,
                (checkpoint.runtime_run_id,),
            ).fetchone()
            if (
                truth_row is None
                or int(truth_row["revision"]) != checkpoint.candidate_truth_revision
                or truth_row["payload_hash"] != checkpoint.candidate_truth_hash
            ):
                raise RuntimeControlError("runtime_checkpoint_candidate_truth_mismatch")
            if not _checkpoint_durable_owners_match(conn, checkpoint):
                raise RuntimeControlError(
                    "runtime_checkpoint_durable_owner_mismatch"
                )
            records = conn.execute(
                """
                SELECT *
                FROM runtime_control_candidate_records
                WHERE runtime_run_id = ?
                ORDER BY resume_id
                """,
                (checkpoint.runtime_run_id,),
            ).fetchall()
            claim_rows = conn.execute(
                """
                SELECT *
                FROM runtime_control_detail_claims
                WHERE runtime_run_id = ?
                ORDER BY provider_candidate_key_hash
                """,
                (checkpoint.runtime_run_id,),
            ).fetchall()
            round_rows = conn.execute(
                """
                SELECT *
                FROM runtime_control_round_states
                WHERE runtime_run_id = ? AND round_no <= ?
                ORDER BY round_no
                """,
                (
                    checkpoint.runtime_run_id,
                    checkpoint.durable_refs["roundLedgerHighWatermark"],
                ),
            ).fetchall()
            finalization_rows = conn.execute(
                """
                SELECT *
                FROM runtime_control_candidate_finalization_revisions
                WHERE runtime_run_id = ? AND revision <= ?
                ORDER BY revision
                """,
                (
                    checkpoint.runtime_run_id,
                    checkpoint.durable_refs["finalizationRevision"],
                ),
            ).fetchall()

        candidate_store = {
            row["resume_id"]: _json_object(row["candidate_json"])
            for row in records
        }
        normalized_store = {
            row["resume_id"]: _json_object(row["normalized_json"])
            for row in records
            if row["normalized_json"] is not None
        }
        scorecards = {
            row["resume_id"]: _json_object(row["scorecard_json"])
            for row in records
            if row["scorecard_json"] is not None
        }
        candidate_state = {
            "candidate_store": candidate_store,
            "normalized_store": normalized_store,
            "source_evidence_by_resume_id": _json_object(
                truth_row["source_evidence_by_resume_json"]
            ),
            "source_evidence_by_identity_id": _json_object(
                truth_row["source_evidence_by_identity_json"]
            ),
            "candidate_identity_by_resume_id": _json_object(
                truth_row["identity_by_resume_id_json"]
            ),
            "candidate_identities": _json_object(
                truth_row["identity_payloads_json"]
            ),
            "identity_aliases_by_canonical_id": _json_object(
                truth_row["aliases_json"]
            ),
            "identity_conflicts": _json_list(truth_row["conflicts_json"]),
            "canonical_resume_by_identity_id": _json_object(
                truth_row["canonical_selections_json"]
            ),
            "scorecards_by_resume_id": scorecards,
        }
        if candidate_truth_hash(candidate_state) != checkpoint.candidate_truth_hash:
            raise RuntimeControlError("runtime_checkpoint_candidate_truth_mismatch")

        detail_claims = {
            row["provider_candidate_key_hash"]: {
                "status": row["status"],
                "browser_open_attempt_count": int(
                    row["browser_open_attempt_count"]
                ),
                "last_safe_reason_code": row["last_safe_reason_code"],
            }
            for row in claim_rows
        }
        rounds = []
        for row in round_rows:
            round_state = _json_object(row["state_json"])
            round_state["top_candidates"] = [
                scorecards[resume_id]
                for resume_id in _string_list(
                    round_state.get("top_pool_ids")
                )
                if resume_id in scorecards
            ]
            round_state["dropped_candidates"] = [
                scorecards[resume_id]
                for resume_id in _string_list(
                    round_state.get("dropped_candidate_ids")
                )
                if resume_id in scorecards
            ]
            rounds.append(round_state)
        finalization_revisions = [
            {
                "revision": int(row["revision"]),
                "runtime_run_id": checkpoint.runtime_run_id,
                "reason_code": row["reason_code"],
                "selected_source_kinds": _json_object(
                    row["coverage_summary_json"]
                ).get("selected_source_kinds", []),
                "candidate_identity_ids": _json_list(
                    row["candidate_identity_ids_json"]
                ),
                "created_at": row["created_at"],
                "coverage_summary": _json_object(row["coverage_summary_json"])
                or None,
            }
            for row in finalization_rows
        ]

        payload = {
            "input_truth": input_truth.model_dump(mode="json"),
            "requirement_sheet": approved.requirement_sheet.model_dump(mode="json"),
            "scoring_policy": ScoringPolicy.model_validate(
                {
                    "job_title": approved.requirement_sheet.job_title,
                    "role_summary": approved.requirement_sheet.role_summary,
                    "must_have_capabilities": (
                        approved.requirement_sheet.must_have_capabilities
                    ),
                    "preferred_capabilities": (
                        approved.requirement_sheet.preferred_capabilities
                    ),
                    "exclusion_signals": (
                        approved.requirement_sheet.exclusion_signals
                    ),
                    "hard_constraints": (
                        approved.requirement_sheet.hard_constraints.model_dump(
                            mode="json"
                        )
                    ),
                    "preferences": (
                        approved.requirement_sheet.preferences.model_dump(
                            mode="json"
                        )
                    ),
                    "scoring_rationale": (
                        approved.requirement_sheet.scoring_rationale
                    ),
                }
            ).model_dump(mode="json"),
            **checkpoint.run_state,
            **candidate_state,
            "runtime_source_lane_results": _json_list(
                truth_row["source_lane_results_json"]
            ),
            "detail_open_claims_by_provider_key": detail_claims,
            "round_history": rounds,
            "finalization_revisions": finalization_revisions,
        }
        return RunState.model_validate(payload)


def _json_object(value: str | None) -> dict[str, object]:
    payload = json.loads(value or "{}")
    if not isinstance(payload, dict):
        raise RuntimeControlError("runtime_checkpoint_json_object_required")
    return {key: item for key, item in payload.items() if isinstance(key, str)}


def _json_list(value: str | None) -> list[object]:
    payload = json.loads(value or "[]")
    if not isinstance(payload, list):
        raise RuntimeControlError("runtime_checkpoint_json_list_required")
    return payload


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


__all__ = ["RecoveryStateAssembler"]
