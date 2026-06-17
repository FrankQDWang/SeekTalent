from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class PrivacyErasureResult:
    runtime_candidate_identity_count: int = 0
    runtime_candidate_evidence_count: int = 0
    workbench_review_item_count: int = 0
    workbench_candidate_evidence_count: int = 0

    @property
    def total_count(self) -> int:
        return (
            self.runtime_candidate_identity_count
            + self.runtime_candidate_evidence_count
            + self.workbench_review_item_count
            + self.workbench_candidate_evidence_count
        )


def erase_candidate_subject(
    *,
    resume_id: str,
    erased_at: str,
    runtime_control_path: str | Path | None = None,
    workbench_path: str | Path | None = None,
) -> PrivacyErasureResult:
    subject_resume_id = resume_id.strip()
    if not subject_resume_id:
        raise ValueError("privacy_erasure_resume_id_required")
    runtime_identity_count = 0
    runtime_evidence_count = 0
    workbench_review_count = 0
    workbench_evidence_count = 0
    if runtime_control_path is not None:
        runtime_identity_count, runtime_evidence_count = _erase_runtime_control_subject(
            Path(runtime_control_path),
            resume_id=subject_resume_id,
            erased_at=erased_at,
        )
    if workbench_path is not None:
        workbench_review_count, workbench_evidence_count = _erase_workbench_subject(
            Path(workbench_path),
            resume_id=subject_resume_id,
            erased_at=erased_at,
        )
    return PrivacyErasureResult(
        runtime_candidate_identity_count=runtime_identity_count,
        runtime_candidate_evidence_count=runtime_evidence_count,
        workbench_review_item_count=workbench_review_count,
        workbench_candidate_evidence_count=workbench_evidence_count,
    )


def _erase_runtime_control_subject(path: Path, *, resume_id: str, erased_at: str) -> tuple[int, int]:
    if not path.exists():
        return (0, 0)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        has_identity_table = _table_exists(conn, "runtime_control_candidate_identities")
        has_evidence_table = _table_exists(conn, "runtime_control_candidate_evidence")
        if not has_identity_table and not has_evidence_table:
            return (0, 0)
        evidence_rows = (
            conn.execute(
                """
                SELECT runtime_run_id, evidence_id, identity_id, resume_id, provider_candidate_key_hash, payload_json
                FROM runtime_control_candidate_evidence
                WHERE resume_id = ?
                """,
                (resume_id,),
            ).fetchall()
            if has_evidence_table
            else []
        )
        evidence_identity_keys = {(row["runtime_run_id"], row["identity_id"]) for row in evidence_rows}
        identity_rows = []
        if has_identity_table:
            identity_rows = conn.execute(
                """
                SELECT
                    runtime_run_id, identity_id, canonical_resume_id, merged_resume_ids_json,
                    source_evidence_ids_json, display_name, title, company, location, summary
                FROM runtime_control_candidate_identities
                WHERE canonical_resume_id = ?
                   OR merged_resume_ids_json LIKE ? ESCAPE '\\'
                """,
                (resume_id, _merged_resume_id_like_pattern(resume_id)),
            ).fetchall()
            identity_keys = {(row["runtime_run_id"], row["identity_id"]) for row in identity_rows}
            missing_evidence_identity_keys = sorted(evidence_identity_keys - identity_keys)
            identity_rows.extend(
                conn.execute(
                    """
                    SELECT
                        runtime_run_id, identity_id, canonical_resume_id, merged_resume_ids_json,
                        source_evidence_ids_json, display_name, title, company, location, summary
                    FROM runtime_control_candidate_identities
                    WHERE runtime_run_id = ? AND identity_id = ?
                    """,
                    key,
                ).fetchone()
                for key in missing_evidence_identity_keys
            )
            identity_rows = [row for row in identity_rows if row is not None]
            subject_tokens = _runtime_subject_tokens(
                resume_id=resume_id,
                identity_rows=identity_rows,
                evidence_rows=evidence_rows,
            )
            conn.execute(
                """
                UPDATE runtime_control_candidate_identities
                SET canonical_resume_id = '',
                    merged_resume_ids_json = '[]',
                    display_name = 'Candidate erased',
                    title = '',
                    company = '',
                    location = '',
                    summary = '',
                    score = NULL,
                    fit_bucket = NULL,
                    payload_hash = ?,
                    updated_at = ?
                WHERE canonical_resume_id = ?
                   OR merged_resume_ids_json LIKE ? ESCAPE '\\'
                """,
                (f"erased:{erased_at}", erased_at, resume_id, _merged_resume_id_like_pattern(resume_id)),
            )
            for runtime_run_id, identity_id in missing_evidence_identity_keys:
                conn.execute(
                    """
                    UPDATE runtime_control_candidate_identities
                    SET canonical_resume_id = '',
                        merged_resume_ids_json = '[]',
                        display_name = 'Candidate erased',
                        title = '',
                        company = '',
                        location = '',
                        summary = '',
                        score = NULL,
                        fit_bucket = NULL,
                        payload_hash = ?,
                        updated_at = ?
                    WHERE runtime_run_id = ? AND identity_id = ?
                    """,
                    (f"erased:{erased_at}", erased_at, runtime_run_id, identity_id),
                )
            _erase_runtime_checkpoints(
                conn,
                resume_id=resume_id,
                subject_tokens=subject_tokens,
                identity_rows=identity_rows,
            )
            _erase_runtime_snapshots_and_summaries(
                conn,
                resume_id=resume_id,
                subject_tokens=subject_tokens,
                identity_rows=identity_rows,
            )
        if has_evidence_table:
            conn.execute(
                """
                UPDATE runtime_control_candidate_evidence
                SET resume_id = '',
                    provider_candidate_key_hash = '',
                    score = NULL,
                    fit_bucket = NULL,
                    payload_json = '{}',
                    payload_hash = ?,
                    updated_at = ?
                WHERE resume_id = ?
                """,
                (f"erased:{erased_at}", erased_at, resume_id),
            )
    return (len(identity_rows), len(evidence_rows))


def _erase_workbench_subject(path: Path, *, resume_id: str, erased_at: str) -> tuple[int, int]:
    if not path.exists():
        return (0, 0)
    del erased_at
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        has_evidence_table = _table_exists(conn, "candidate_evidence")
        has_snapshot_table = _table_exists(conn, "runtime_candidate_identity_snapshots")
        if not has_evidence_table and not has_snapshot_table:
            return (0, 0)
        evidence_rows = (
            conn.execute(
                """
                SELECT evidence_id, review_item_id
                FROM candidate_evidence
                WHERE resume_id = ?
                """,
                (resume_id,),
            ).fetchall()
            if has_evidence_table
            else []
        )
        review_item_ids = sorted({row["review_item_id"] for row in evidence_rows if row["review_item_id"]})
        if review_item_ids and _table_exists(conn, "candidate_review_items"):
            placeholders = ",".join("?" for _ in review_item_ids)
            conn.execute(
                f"""
                UPDATE candidate_review_items
                SET display_name = 'Candidate erased',
                    title = '',
                    company = '',
                    location = '',
                    summary = '',
                    aggregate_score = NULL,
                    fit_bucket = NULL,
                    why_selected = '',
                    note = ''
                WHERE review_item_id IN ({placeholders})
                """,
                review_item_ids,
            )
        evidence_ids = sorted({row["evidence_id"] for row in evidence_rows if row["evidence_id"]})
        _erase_workbench_secondary_subject_tables(
            conn,
            resume_id=resume_id,
            review_item_ids=review_item_ids,
            evidence_ids=evidence_ids,
        )
        if has_snapshot_table:
            conn.execute(
                """
                UPDATE runtime_candidate_identity_snapshots
                SET canonical_resume_id = '',
                    merged_resume_ids_json = '[]',
                    source_evidence_ids_json = '[]'
                WHERE canonical_resume_id = ?
                   OR merged_resume_ids_json LIKE ? ESCAPE '\\'
                """,
                (resume_id, _merged_resume_id_like_pattern(resume_id)),
            )
        if has_evidence_table:
            conn.execute(
                """
                UPDATE candidate_evidence
                SET resume_id = '',
                    provider_candidate_key_hash = '',
                    score = NULL,
                    fit_bucket = NULL,
                    matched_must_haves_json = '[]',
                    matched_preferences_json = '[]',
                    missing_risks_json = '[]',
                    strengths_json = '[]',
                    weaknesses_json = '[]'
                WHERE resume_id = ?
                """,
                (resume_id,),
            )
    return (len(review_item_ids), len(evidence_rows))


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _merged_resume_id_like_pattern(resume_id: str) -> str:
    token = json.dumps(resume_id, ensure_ascii=False, separators=(",", ":"))
    return f"%{_escape_sqlite_like(token)}%"


def _escape_sqlite_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _runtime_subject_tokens(
    *,
    resume_id: str,
    identity_rows: list[sqlite3.Row],
    evidence_rows: list[sqlite3.Row],
) -> set[str]:
    tokens: set[str] = {resume_id}
    for row in identity_rows:
        for column in (
            "canonical_resume_id",
            "display_name",
            "title",
            "company",
            "location",
            "summary",
        ):
            _add_subject_token(tokens, row[column])
        for item in _loads_list(row["merged_resume_ids_json"]):
            _add_subject_token(tokens, item)
        for item in _loads_list(row["source_evidence_ids_json"]):
            _add_subject_token(tokens, item)
    for row in evidence_rows:
        for column in ("resume_id", "provider_candidate_key_hash", "evidence_id"):
            _add_subject_token(tokens, row[column])
        _collect_json_string_tokens(tokens, _loads_json(row["payload_json"]))
    return {token for token in tokens if token}


def _erase_runtime_checkpoints(
    conn: sqlite3.Connection,
    *,
    resume_id: str,
    subject_tokens: set[str],
    identity_rows: list[sqlite3.Row],
) -> None:
    if not subject_tokens or not _table_exists(conn, "runtime_control_checkpoints"):
        return
    runtime_run_ids = sorted({row["runtime_run_id"] for row in identity_rows if row["runtime_run_id"]})
    rows: list[sqlite3.Row] = []
    if runtime_run_ids:
        placeholders = ",".join("?" for _ in runtime_run_ids)
        rows.extend(
            conn.execute(
                f"""
                SELECT checkpoint_id, run_state_json
                FROM runtime_control_checkpoints
                WHERE runtime_run_id IN ({placeholders})
                """,
                runtime_run_ids,
            ).fetchall()
        )
    rows.extend(
        conn.execute(
            """
            SELECT checkpoint_id, run_state_json
            FROM runtime_control_checkpoints
            WHERE run_state_json LIKE ? ESCAPE '\\'
            """,
            (_contains_like_pattern(resume_id),),
        ).fetchall()
    )
    seen: set[str] = set()
    for row in rows:
        checkpoint_id = row["checkpoint_id"]
        if checkpoint_id in seen:
            continue
        seen.add(checkpoint_id)
        cleaned = _erase_tokens_from_json_text(row["run_state_json"], subject_tokens)
        if cleaned != row["run_state_json"]:
            conn.execute(
                """
                UPDATE runtime_control_checkpoints
                SET run_state_json = ?
                WHERE checkpoint_id = ?
                """,
                (cleaned, checkpoint_id),
            )


def _erase_runtime_snapshots_and_summaries(
    conn: sqlite3.Connection,
    *,
    resume_id: str,
    subject_tokens: set[str],
    identity_rows: list[sqlite3.Row],
) -> None:
    if not subject_tokens:
        return
    runtime_run_ids = sorted({row["runtime_run_id"] for row in identity_rows if row["runtime_run_id"]})
    snapshot_rows: list[sqlite3.Row] = []
    if _table_exists(conn, "runtime_control_snapshots"):
        if runtime_run_ids:
            placeholders = ",".join("?" for _ in runtime_run_ids)
            snapshot_rows.extend(
                conn.execute(
                    f"""
                    SELECT runtime_run_id, snapshot_json
                    FROM runtime_control_snapshots
                    WHERE runtime_run_id IN ({placeholders})
                    """,
                    runtime_run_ids,
                ).fetchall()
            )
        snapshot_rows.extend(
            conn.execute(
                """
                SELECT runtime_run_id, snapshot_json
                FROM runtime_control_snapshots
                WHERE snapshot_json LIKE ? ESCAPE '\\'
                """,
                (_contains_like_pattern(resume_id),),
            ).fetchall()
        )
        seen_snapshots: set[str] = set()
        for row in snapshot_rows:
            runtime_run_id = row["runtime_run_id"]
            if runtime_run_id in seen_snapshots:
                continue
            seen_snapshots.add(runtime_run_id)
            cleaned = _erase_tokens_from_json_text(row["snapshot_json"], subject_tokens)
            if cleaned != row["snapshot_json"]:
                conn.execute(
                    """
                    UPDATE runtime_control_snapshots
                    SET snapshot_json = ?
                    WHERE runtime_run_id = ?
                    """,
                    (cleaned, runtime_run_id),
                )
    if not _table_exists(conn, "runtime_control_final_summaries"):
        return
    summary_rows: list[sqlite3.Row] = []
    if runtime_run_ids:
        placeholders = ",".join("?" for _ in runtime_run_ids)
        summary_rows.extend(
            conn.execute(
                f"""
                SELECT summary_id, user_instruction, summary_json
                FROM runtime_control_final_summaries
                WHERE runtime_run_id IN ({placeholders})
                """,
                runtime_run_ids,
            ).fetchall()
        )
    summary_rows.extend(
        conn.execute(
            """
            SELECT summary_id, user_instruction, summary_json
            FROM runtime_control_final_summaries
            WHERE summary_json LIKE ? ESCAPE '\\'
               OR user_instruction LIKE ? ESCAPE '\\'
            """,
            (_contains_like_pattern(resume_id), _contains_like_pattern(resume_id)),
        ).fetchall()
    )
    seen_summaries: set[str] = set()
    for row in summary_rows:
        summary_id = row["summary_id"]
        if summary_id in seen_summaries:
            continue
        seen_summaries.add(summary_id)
        cleaned_json = _erase_tokens_from_json_text(row["summary_json"], subject_tokens)
        cleaned_instruction = (
            _erase_tokens_from_string(row["user_instruction"], subject_tokens)
            if isinstance(row["user_instruction"], str)
            else None
        )
        if cleaned_json != row["summary_json"] or cleaned_instruction != row["user_instruction"]:
            conn.execute(
                """
                UPDATE runtime_control_final_summaries
                SET summary_json = ?, user_instruction = ?
                WHERE summary_id = ?
                """,
                (cleaned_json, cleaned_instruction, summary_id),
            )


def _erase_workbench_secondary_subject_tables(
    conn: sqlite3.Connection,
    *,
    resume_id: str,
    review_item_ids: list[str],
    evidence_ids: list[str],
) -> None:
    if review_item_ids and _table_exists(conn, "candidate_actions"):
        placeholders = ",".join("?" for _ in review_item_ids)
        conn.execute(
            f"""
            UPDATE candidate_actions
            SET note = ''
            WHERE review_item_id IN ({placeholders})
            """,
            review_item_ids,
        )
    if _table_exists(conn, "detail_open_requests"):
        clauses: list[str] = []
        params: list[object] = []
        if evidence_ids:
            placeholders = ",".join("?" for _ in evidence_ids)
            clauses.append(f"candidate_evidence_id IN ({placeholders})")
            params.extend(evidence_ids)
        if review_item_ids:
            placeholders = ",".join("?" for _ in review_item_ids)
            clauses.append(f"review_item_id IN ({placeholders})")
            params.extend(review_item_ids)
        clauses.append("detail_candidates_json LIKE ? ESCAPE '\\'")
        params.append(_contains_like_pattern(resume_id))
        conn.execute(
            f"""
            UPDATE detail_open_requests
            SET provider_candidate_key_hash = '',
                detail_candidates_json = '[]',
                decision_note = NULL,
                blocked_reason = NULL
            WHERE {' OR '.join(clauses)}
            """,
            params,
        )
    if not _table_exists(conn, "detail_open_ledger"):
        return
    clauses = []
    params = []
    if evidence_ids:
        placeholders = ",".join("?" for _ in evidence_ids)
        clauses.append(f"candidate_evidence_id IN ({placeholders})")
        params.extend(evidence_ids)
    if _table_exists(conn, "detail_open_requests"):
        clauses.append(
            """
            request_id IN (
                SELECT request_id
                FROM detail_open_requests
                WHERE detail_candidates_json = '[]'
                  AND provider_candidate_key_hash = ''
            )
            """
        )
    clauses.append("provider_candidate_key_hash LIKE ? ESCAPE '\\'")
    params.append(_contains_like_pattern(resume_id))
    conn.execute(
        f"""
        UPDATE detail_open_ledger
        SET provider_candidate_key_hash = ''
        WHERE {' OR '.join(clauses)}
        """,
        params,
    )


def _erase_tokens_from_json_text(raw_json: str, subject_tokens: set[str]) -> str:
    value = _loads_json(raw_json)
    if value is None:
        cleaned = raw_json
        for token in subject_tokens:
            cleaned = cleaned.replace(token, "[erased]")
        return cleaned
    cleaned_value = _erase_tokens_from_json_value(value, subject_tokens)
    return json.dumps(cleaned_value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _erase_tokens_from_json_value(value: object, subject_tokens: set[str]) -> object:
    if isinstance(value, dict):
        return {
            _erase_tokens_from_string(key, subject_tokens) if isinstance(key, str) else key: _erase_tokens_from_json_value(
                item,
                subject_tokens,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_erase_tokens_from_json_value(item, subject_tokens) for item in value]
    if isinstance(value, str):
        return _erase_tokens_from_string(value, subject_tokens)
    return value


def _erase_tokens_from_string(value: str, subject_tokens: set[str]) -> str:
    cleaned = value
    ordered_tokens = list(subject_tokens)
    ordered_tokens.sort(key=lambda token: len(token), reverse=True)
    for token in ordered_tokens:
        if not token:
            continue
        if token in cleaned:
            cleaned = cleaned.replace(token, "[erased]")
    return cleaned


def _collect_json_string_tokens(tokens: set[str], value: object) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _collect_json_string_tokens(tokens, item)
    elif isinstance(value, list):
        for item in value:
            _collect_json_string_tokens(tokens, item)
    elif isinstance(value, str):
        _add_subject_token(tokens, value)


def _add_subject_token(tokens: set[str], value: object) -> None:
    if isinstance(value, str):
        text = value.strip()
        if text:
            tokens.add(text)


def _loads_list(value: str) -> list[object]:
    parsed = _loads_json(value)
    return list(parsed) if isinstance(parsed, list) else []


def _loads_json(value: str) -> object:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def _contains_like_pattern(value: str) -> str:
    return f"%{_escape_sqlite_like(value)}%"
