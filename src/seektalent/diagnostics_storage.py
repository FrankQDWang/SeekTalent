"""Immutable Failure Envelope revisions in a caller-owned SQLite transaction."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import sqlite3
from typing import Literal

from seektalent.diagnostics_event_models import FailureEnvelopeV1
from seektalent.diagnostics_schema import (
    canonical_diagnostics_bytes,
    parse_failure_envelope,
)


FAILURE_ENVELOPE_TABLE = "runtime_control_failure_envelope_revisions"

_SCHEMA_STATEMENTS = (
    f"""
    CREATE TABLE {FAILURE_ENVELOPE_TABLE} (
      failure_id TEXT NOT NULL,
      revision INTEGER NOT NULL,
      canonical_bytes BLOB NOT NULL,
      canonical_sha256 TEXT NOT NULL,
      run_id TEXT NOT NULL,
      operation_id TEXT,
      attempt_no INTEGER,
      correlation_id TEXT,
      component TEXT NOT NULL,
      domain TEXT NOT NULL,
      failure_kind TEXT NOT NULL,
      reason_code TEXT NOT NULL,
      current_outcome TEXT,
      occurred_at TEXT NOT NULL,
      observed_at TEXT NOT NULL,
      PRIMARY KEY(failure_id, revision),
      CHECK (revision >= 1 AND revision <= 9007199254740991),
      CHECK (typeof(canonical_bytes) = 'blob' AND length(canonical_bytes) <= 32768),
      CHECK (
        length(canonical_sha256) = 64
        AND canonical_sha256 NOT GLOB '*[^0-9a-f]*'
      ),
      CHECK (attempt_no IS NULL OR (attempt_no >= 1 AND attempt_no <= 9007199254740991)),
      CHECK (
        current_outcome IS NULL
        OR current_outcome IN (
          'succeeded_with_results',
          'succeeded_empty',
          'degraded_with_results',
          'needs_attention',
          'failed',
          'cancelled'
        )
      )
    )
    """,
    f"""
    CREATE INDEX idx_runtime_failure_envelopes_run
      ON {FAILURE_ENVELOPE_TABLE}(run_id, failure_id, revision)
    """,
    f"""
    CREATE TRIGGER runtime_control_failure_envelopes_no_overwrite
    BEFORE INSERT ON {FAILURE_ENVELOPE_TABLE}
    WHEN EXISTS (
      SELECT 1
      FROM {FAILURE_ENVELOPE_TABLE}
      WHERE failure_id = NEW.failure_id AND revision = NEW.revision
    )
    BEGIN
      SELECT RAISE(ABORT, 'failure_envelope_immutable');
    END
    """,
    f"""
    CREATE TRIGGER runtime_control_failure_envelopes_contiguous
    BEFORE INSERT ON {FAILURE_ENVELOPE_TABLE}
    WHEN NEW.revision != COALESCE(
      (
        SELECT MAX(revision) + 1
        FROM {FAILURE_ENVELOPE_TABLE}
        WHERE failure_id = NEW.failure_id
      ),
      1
    )
    BEGIN
      SELECT RAISE(ABORT, 'failure_envelope_revision_sequence');
    END
    """,
    f"""
    CREATE TRIGGER runtime_control_failure_envelopes_no_update
    BEFORE UPDATE ON {FAILURE_ENVELOPE_TABLE}
    BEGIN
      SELECT RAISE(ABORT, 'failure_envelope_immutable');
    END
    """,
    f"""
    CREATE TRIGGER runtime_control_failure_envelopes_no_delete
    BEFORE DELETE ON {FAILURE_ENVELOPE_TABLE}
    BEGIN
      SELECT RAISE(ABORT, 'failure_envelope_immutable');
    END
    """,
)

_PROJECTION_FIELDS = (
    "failure_id",
    "revision",
    "run_id",
    "operation_id",
    "attempt_no",
    "correlation_id",
    "component",
    "domain",
    "failure_kind",
    "reason_code",
    "current_outcome",
    "occurred_at",
    "observed_at",
)
_STORAGE_COLUMNS = (
    "failure_id",
    "revision",
    "canonical_bytes",
    "canonical_sha256",
    *_PROJECTION_FIELDS[2:],
)
_STORAGE_COLUMN_LIST = ", ".join(_STORAGE_COLUMNS)
_STORAGE_COLUMN_FACTS = (
    ("failure_id", "TEXT", 1, None, 1, 0),
    ("revision", "INTEGER", 1, None, 2, 0),
    ("canonical_bytes", "BLOB", 1, None, 0, 0),
    ("canonical_sha256", "TEXT", 1, None, 0, 0),
    ("run_id", "TEXT", 1, None, 0, 0),
    ("operation_id", "TEXT", 0, None, 0, 0),
    ("attempt_no", "INTEGER", 0, None, 0, 0),
    ("correlation_id", "TEXT", 0, None, 0, 0),
    ("component", "TEXT", 1, None, 0, 0),
    ("domain", "TEXT", 1, None, 0, 0),
    ("failure_kind", "TEXT", 1, None, 0, 0),
    ("reason_code", "TEXT", 1, None, 0, 0),
    ("current_outcome", "TEXT", 0, None, 0, 0),
    ("occurred_at", "TEXT", 1, None, 0, 0),
    ("observed_at", "TEXT", 1, None, 0, 0),
)
_COLUMN_INDEX = {
    "failure_id": 0,
    "revision": 1,
    "canonical_bytes": 2,
    "canonical_sha256": 3,
    "run_id": 4,
    "operation_id": 5,
    "attempt_no": 6,
    "correlation_id": 7,
    "component": 8,
    "domain": 9,
    "failure_kind": 10,
    "reason_code": 11,
    "current_outcome": 12,
    "occurred_at": 13,
    "observed_at": 14,
}


class FailureEnvelopeStorageError(ValueError):
    """A bounded storage error that never exposes SQLite or payload details."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class FailureEnvelopeRef:
    failure_id: str
    revision: int


@dataclass(frozen=True)
class StoredFailureEnvelopeRevision:
    ref: FailureEnvelopeRef
    canonical_sha256: str
    disposition: Literal["created", "exact_replay"]


def create_failure_envelope_schema(conn: sqlite3.Connection) -> None:
    """Create the diagnostics-owned table inside the migration owner's transaction."""

    try:
        for statement in _SCHEMA_STATEMENTS:
            conn.execute(statement)
    except sqlite3.Error:
        raise FailureEnvelopeStorageError("failure_envelope_schema_failed") from None


def migrate_failure_envelope_schema_v13_to_v14(
    conn: sqlite3.Connection,
) -> None:
    """Replace the legacy outcome constraint without accepting legacy aliases."""

    try:
        column_facts = tuple(
            (
                row[1],
                row[2].upper(),
                row[3],
                row[4],
                row[5],
                row[6],
            )
            for row in conn.execute(
                f"PRAGMA table_xinfo({FAILURE_ENVELOPE_TABLE})"
            )
        )
        if column_facts != _STORAGE_COLUMN_FACTS:
            raise FailureEnvelopeStorageError(
                "failure_envelope_schema_failed"
            )
        rows = conn.execute(
            f"""
            SELECT {_STORAGE_COLUMN_LIST}
            FROM {FAILURE_ENVELOPE_TABLE}
            ORDER BY failure_id, revision
            """
        ).fetchall()
        for row in rows:
            _verified_envelope_from_row(row)
        conn.execute("DROP TRIGGER runtime_control_failure_envelopes_no_overwrite")
        conn.execute("DROP TRIGGER runtime_control_failure_envelopes_contiguous")
        conn.execute("DROP TRIGGER runtime_control_failure_envelopes_no_update")
        conn.execute("DROP TRIGGER runtime_control_failure_envelopes_no_delete")
        conn.execute("DROP INDEX idx_runtime_failure_envelopes_run")
        conn.execute(
            f"ALTER TABLE {FAILURE_ENVELOPE_TABLE} "
            f"RENAME TO {FAILURE_ENVELOPE_TABLE}_v13"
        )
        create_failure_envelope_schema(conn)
        conn.execute(
            f"""
            INSERT INTO {FAILURE_ENVELOPE_TABLE} ({_STORAGE_COLUMN_LIST})
            SELECT {_STORAGE_COLUMN_LIST}
            FROM {FAILURE_ENVELOPE_TABLE}_v13
            ORDER BY failure_id, revision
            """
        )
        conn.execute(f"DROP TABLE {FAILURE_ENVELOPE_TABLE}_v13")
    except FailureEnvelopeStorageError:
        raise
    except sqlite3.Error:
        raise FailureEnvelopeStorageError("failure_envelope_schema_failed") from None


def store_failure_envelope_revision(
    conn: sqlite3.Connection,
    envelope: FailureEnvelopeV1 | bytes,
) -> StoredFailureEnvelopeRevision:
    """Persist one immutable revision without taking transaction ownership."""

    if not isinstance(conn, sqlite3.Connection):
        raise FailureEnvelopeStorageError("failure_envelope_transaction_required")
    try:
        in_transaction = conn.in_transaction
    except sqlite3.Error:
        raise FailureEnvelopeStorageError(
            "failure_envelope_transaction_required"
        ) from None
    if not in_transaction:
        raise FailureEnvelopeStorageError("failure_envelope_transaction_required")

    admitted, canonical_bytes = _admit_envelope(envelope)
    canonical_sha256 = sha256(canonical_bytes).hexdigest()
    ref = FailureEnvelopeRef(
        failure_id=admitted.failure_id,
        revision=admitted.revision,
    )

    try:
        existing = conn.execute(
            f"""
            SELECT *
            FROM {FAILURE_ENVELOPE_TABLE}
            WHERE failure_id = ? AND revision = ?
            """,
            (ref.failure_id, ref.revision),
        ).fetchone()
        predecessor = conn.execute(
            f"""
            SELECT *
            FROM {FAILURE_ENVELOPE_TABLE}
            WHERE failure_id = ?
            ORDER BY revision DESC
            LIMIT 1
            """,
            (ref.failure_id,),
        ).fetchone()
    except sqlite3.Error:
        raise FailureEnvelopeStorageError("failure_envelope_storage_failed") from None

    if existing is not None:
        existing_envelope = _verified_envelope_from_row(existing)
        existing_bytes, existing_hash = _stored_identity(existing)
        if (
            existing_envelope == admitted
            and existing_bytes == canonical_bytes
            and existing_hash == canonical_sha256
        ):
            return StoredFailureEnvelopeRevision(
                ref=ref,
                canonical_sha256=canonical_sha256,
                disposition="exact_replay",
            )
        raise FailureEnvelopeStorageError("failure_envelope_revision_conflict")

    expected_revision = 1
    if predecessor is not None:
        predecessor_envelope = _verified_envelope_from_row(predecessor)
        expected_revision = predecessor_envelope.revision + 1
        frozen_identity_fields = (
            "correlation_id",
            "run_id",
            "operation_id",
            "attempt_no",
        )
        if any(
            getattr(admitted, field) != getattr(predecessor_envelope, field)
            for field in frozen_identity_fields
        ):
            raise FailureEnvelopeStorageError("failure_envelope_identity_conflict")
    if ref.revision != expected_revision:
        raise FailureEnvelopeStorageError("failure_envelope_revision_sequence")

    try:
        conn.execute(
            f"""
            INSERT INTO {FAILURE_ENVELOPE_TABLE} (
              failure_id, revision, canonical_bytes, canonical_sha256,
              run_id, operation_id, attempt_no, correlation_id,
              component, domain, failure_kind, reason_code, current_outcome,
              occurred_at, observed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                admitted.failure_id,
                admitted.revision,
                canonical_bytes,
                canonical_sha256,
                admitted.run_id,
                admitted.operation_id,
                admitted.attempt_no,
                admitted.correlation_id,
                admitted.component,
                admitted.domain,
                admitted.failure_kind,
                admitted.reason_code,
                admitted.current_outcome,
                admitted.occurred_at,
                admitted.observed_at,
            ),
        )
    except sqlite3.Error:
        raise FailureEnvelopeStorageError("failure_envelope_storage_failed") from None

    return StoredFailureEnvelopeRevision(
        ref=ref,
        canonical_sha256=canonical_sha256,
        disposition="created",
    )


def load_failure_envelope_revision(
    conn: sqlite3.Connection,
    *,
    failure_id: str,
    revision: int,
) -> FailureEnvelopeV1:
    """Load one exact ref only after canonical bytes, hash and projections verify."""

    if not isinstance(conn, sqlite3.Connection):
        raise FailureEnvelopeStorageError("failure_envelope_storage_failed")
    try:
        row = conn.execute(
            f"""
            SELECT *
            FROM {FAILURE_ENVELOPE_TABLE}
            WHERE failure_id = ? AND revision = ?
            """,
            (failure_id, revision),
        ).fetchone()
    except sqlite3.Error:
        raise FailureEnvelopeStorageError("failure_envelope_storage_failed") from None
    if row is None:
        raise FailureEnvelopeStorageError("failure_envelope_not_found")
    envelope = _verified_envelope_from_row(row)
    if envelope.failure_id != failure_id or envelope.revision != revision:
        raise FailureEnvelopeStorageError("failure_envelope_integrity_failed")
    return envelope


def _admit_envelope(
    envelope: FailureEnvelopeV1 | bytes,
) -> tuple[FailureEnvelopeV1, bytes]:
    if type(envelope) is bytes:
        admitted = parse_failure_envelope(envelope)
    elif type(envelope) is FailureEnvelopeV1:
        admitted = parse_failure_envelope(canonical_diagnostics_bytes(envelope))
    else:
        raise FailureEnvelopeStorageError("failure_envelope_admission_failed")
    canonical_bytes = canonical_diagnostics_bytes(admitted)
    return admitted, canonical_bytes


def _stored_identity(row: sqlite3.Row | tuple[object, ...]) -> tuple[bytes, str]:
    canonical_bytes = _row_value(row, "canonical_bytes")
    canonical_sha256 = _row_value(row, "canonical_sha256")
    if type(canonical_bytes) is not bytes or type(canonical_sha256) is not str:
        raise FailureEnvelopeStorageError("failure_envelope_integrity_failed")
    return canonical_bytes, canonical_sha256


def _verified_envelope_from_row(
    row: sqlite3.Row | tuple[object, ...],
) -> FailureEnvelopeV1:
    canonical_bytes, canonical_sha256 = _stored_identity(row)
    if sha256(canonical_bytes).hexdigest() != canonical_sha256:
        raise FailureEnvelopeStorageError("failure_envelope_integrity_failed")
    try:
        envelope = parse_failure_envelope(canonical_bytes)
        if canonical_diagnostics_bytes(envelope) != canonical_bytes:
            raise FailureEnvelopeStorageError("failure_envelope_integrity_failed")
        for field in _PROJECTION_FIELDS:
            stored = _row_value(row, field)
            if stored != getattr(envelope, field):
                raise FailureEnvelopeStorageError(
                    "failure_envelope_integrity_failed"
                )
    except FailureEnvelopeStorageError:
        raise
    except (ValueError, TypeError):
        raise FailureEnvelopeStorageError("failure_envelope_integrity_failed") from None
    return envelope


def _row_value(
    row: sqlite3.Row | tuple[object, ...],
    field: str,
) -> object:
    try:
        if isinstance(row, sqlite3.Row):
            return row[field]
        return row[_COLUMN_INDEX[field]]
    except (IndexError, KeyError, TypeError):
        raise FailureEnvelopeStorageError("failure_envelope_integrity_failed") from None


__all__ = [
    "FailureEnvelopeRef",
    "FailureEnvelopeStorageError",
    "StoredFailureEnvelopeRevision",
    "create_failure_envelope_schema",
    "load_failure_envelope_revision",
    "store_failure_envelope_revision",
]
