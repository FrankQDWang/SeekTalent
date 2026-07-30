"""Create a local, allowlisted execution support bundle."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib import metadata as importlib_metadata
import json
import os
from pathlib import Path
import sqlite3
from typing import Callable
from uuid import uuid4

from seektalent.config import AppSettings
from seektalent.version import __version__


SUPPORT_BUNDLE_SCHEMA = "seektalent.execution-support-bundle.v1"


class SupportBundleError(RuntimeError):
    pass


def create_execution_support_bundle(
    *,
    settings: AppSettings,
    runtime_run_id: str | None = None,
    output_dir: Path | None = None,
    now: Callable[[], datetime] | None = None,
) -> Path:
    created_at = (now or (lambda: datetime.now(UTC)))().astimezone(UTC)
    root = output_dir or settings.artifacts_path / "support-bundles"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    database_path = settings.runtime_control_path
    payload: dict[str, object] = {
        "schemaVersion": SUPPORT_BUNDLE_SCHEMA,
        "createdAt": created_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "package": {
            "name": "seektalent",
            "version": _installed_version(),
        },
        "runtimeControl": {
            "databaseAvailable": database_path.is_file(),
            "schemaVersion": None,
            "runs": [],
            "checkpoints": [],
            "sourceOperations": [],
            "browserLane": None,
            "failureEnvelopes": [],
        },
        "privacy": {
            "allowlistOnly": True,
            "includesRawStderr": False,
            "includesDom": False,
            "includesBusinessText": False,
            "automaticUpload": False,
        },
    }
    if database_path.is_file():
        try:
            payload["runtimeControl"] = _runtime_control_payload(
                database_path,
                runtime_run_id=runtime_run_id,
            )
        except sqlite3.Error as exc:
            raise SupportBundleError("support_bundle_runtime_control_unavailable") from exc
    destination = root / (
        f"execution-support-{created_at.strftime('%Y%m%dT%H%M%SZ')}-"
        f"{uuid4().hex[:12]}.json"
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode() + b"\n"
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
    return destination


def _runtime_control_payload(
    database_path: Path,
    *,
    runtime_run_id: str | None,
) -> dict[str, object]:
    connection = sqlite3.connect(
        f"file:{database_path}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        selected_run_ids = _selected_run_ids(
            connection,
            runtime_run_id=runtime_run_id,
        )
        return {
            "databaseAvailable": True,
            "schemaVersion": schema_version,
            "runs": _runs(connection, selected_run_ids),
            "checkpoints": _checkpoints(connection, selected_run_ids),
            "sourceOperations": _source_operations(connection, selected_run_ids),
            "browserLane": _browser_lane(connection),
            "failureEnvelopes": _failure_envelopes(connection, selected_run_ids),
        }
    finally:
        connection.close()


def _selected_run_ids(
    connection: sqlite3.Connection,
    *,
    runtime_run_id: str | None,
) -> tuple[str, ...]:
    if runtime_run_id is not None:
        row = connection.execute(
            "SELECT runtime_run_id FROM runtime_control_runs WHERE runtime_run_id = ?",
            (runtime_run_id,),
        ).fetchone()
        if row is None:
            raise SupportBundleError("support_bundle_runtime_run_not_found")
        return (str(row["runtime_run_id"]),)
    rows = connection.execute(
        """
        SELECT runtime_run_id
        FROM runtime_control_runs
        ORDER BY updated_at DESC
        LIMIT 20
        """
    ).fetchall()
    return tuple(str(row["runtime_run_id"]) for row in rows)


def _runs(
    connection: sqlite3.Connection,
    runtime_run_ids: tuple[str, ...],
) -> list[dict[str, object]]:
    return _query_for_runs(
        connection,
        """
        SELECT runtime_run_id, status, current_stage, current_round,
               latest_checkpoint_id, stop_reason_code, created_at, updated_at,
               completed_at
        FROM runtime_control_runs
        WHERE runtime_run_id IN ({placeholders})
        ORDER BY updated_at DESC
        """,
        runtime_run_ids,
    )


def _checkpoints(
    connection: sqlite3.Connection,
    runtime_run_ids: tuple[str, ...],
) -> list[dict[str, object]]:
    return _query_for_runs(
        connection,
        """
        SELECT checkpoint_id, runtime_run_id, stage, round_no, safe_boundary,
               schema_version, created_at
        FROM runtime_control_checkpoints
        WHERE runtime_run_id IN ({placeholders})
        ORDER BY created_at DESC
        """,
        runtime_run_ids,
    )


def _source_operations(
    connection: sqlite3.Connection,
    runtime_run_ids: tuple[str, ...],
) -> list[dict[str, object]]:
    rows = _query_for_runs(
        connection,
        """
        SELECT runtime_run_id, operation_id, source_id, operation_kind,
               runtime_attempt_no, operation_phase,
               source_operation_disposition, retry_posture,
               reconciliation_revision, main_commit_ref, ledger_revision
        FROM runtime_control_source_operations
        WHERE runtime_run_id IN ({placeholders})
        ORDER BY runtime_run_id, ledger_revision
        """,
        runtime_run_ids,
    )
    for row in rows:
        row["mainCommitted"] = row.pop("main_commit_ref") is not None
    return rows


def _browser_lane(
    connection: sqlite3.Connection,
) -> dict[str, object] | None:
    if not _table_exists(connection, "runtime_control_browser_lanes"):
        return None
    row = connection.execute(
        """
        SELECT lane_key, fencing_token, status, runtime_run_id,
               operation_id, operation_kind, heartbeat_at,
               lease_expires_at, released_at, last_failure_code, updated_at
        FROM runtime_control_browser_lanes
        WHERE lane_key = 'liepin_browser'
        """
    ).fetchone()
    return None if row is None else dict(row)


def _failure_envelopes(
    connection: sqlite3.Connection,
    runtime_run_ids: tuple[str, ...],
) -> list[dict[str, object]]:
    return _query_for_runs(
        connection,
        """
        SELECT failure_id, revision, run_id AS runtime_run_id,
               operation_id, attempt_no, component, domain,
               failure_kind, reason_code, current_outcome,
               occurred_at, observed_at
        FROM runtime_control_failure_envelope_revisions
        WHERE run_id IN ({placeholders})
        ORDER BY observed_at DESC
        """,
        runtime_run_ids,
    )


def _query_for_runs(
    connection: sqlite3.Connection,
    sql: str,
    runtime_run_ids: tuple[str, ...],
) -> list[dict[str, object]]:
    if not runtime_run_ids:
        return []
    statement = sql.format(
        placeholders=", ".join("?" for _ in runtime_run_ids)
    )
    return [
        dict(row)
        for row in connection.execute(statement, runtime_run_ids).fetchall()
    ]


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (name,),
    ).fetchone() is not None


def _installed_version() -> str:
    try:
        return importlib_metadata.version("seektalent")
    except importlib_metadata.PackageNotFoundError:
        return __version__
