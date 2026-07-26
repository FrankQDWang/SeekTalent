from __future__ import annotations

import ast
import json
from pathlib import Path
import sqlite3

import pytest

from seektalent.diagnostics_schema import (
    FailureEnvelopeV1,
    canonical_diagnostics_bytes,
    canonical_diagnostics_hash,
    parse_failure_envelope,
)
from tests.test_diagnostics_schema import _failure


def _envelope(*, revision: int = 1, failure_id: str | None = None) -> FailureEnvelopeV1:
    payload = _failure()
    payload["revision"] = revision
    if failure_id is not None:
        payload["failure_id"] = failure_id
    return parse_failure_envelope(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )


def _initialized_path(tmp_path: Path) -> Path:
    from seektalent_runtime_control.store import RuntimeControlStore

    path = tmp_path / "runtime_control.sqlite3"
    RuntimeControlStore(path).initialize()
    return path


def test_fresh_runtime_control_schema_v13_owns_failure_envelope_table(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.store import (
        RUNTIME_CONTROL_SCHEMA_VERSION,
        RuntimeControlStore,
    )

    path = tmp_path / "runtime_control.sqlite3"
    RuntimeControlStore(path).initialize()

    with sqlite3.connect(path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(runtime_control_failure_envelope_revisions)"
            )
        }
        indexes = {
            row[1]
            for row in conn.execute(
                "PRAGMA index_list(runtime_control_failure_envelope_revisions)"
            )
        }

    assert version == RUNTIME_CONTROL_SCHEMA_VERSION == 13
    assert {
        "failure_id",
        "revision",
        "canonical_bytes",
        "canonical_sha256",
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
    } <= columns
    assert "idx_runtime_failure_envelopes_run" in indexes


def test_real_v12_to_v13_migration_creates_backup_and_reopens(
    tmp_path: Path,
) -> None:
    from seektalent_runtime_control.store import RuntimeControlStore

    path = _initialized_path(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            DROP TRIGGER runtime_control_failure_envelopes_no_update;
            DROP TRIGGER runtime_control_failure_envelopes_no_delete;
            DROP TABLE runtime_control_failure_envelope_revisions;
            PRAGMA user_version = 12;
            """
        )

    RuntimeControlStore(path).initialize()
    RuntimeControlStore(path).initialize()

    backups = list((tmp_path / "migration_backups").glob("runtime-control-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM runtime_control_failure_envelope_revisions"
            ).fetchone()[0]
            == 0
        )
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with sqlite3.connect(f"file:{backups[0]}?mode=ro", uri=True) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 12
        assert (
            backup.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'runtime_control_failure_envelope_revisions'
                """
            ).fetchone()
            is None
        )


def test_v12_to_v13_failure_rolls_back_partial_schema_and_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import seektalent_runtime_control.store as store_module
    from seektalent.diagnostics_storage import create_failure_envelope_schema
    from seektalent_runtime_control.store import RuntimeControlStore

    path = _initialized_path(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            DROP TABLE runtime_control_failure_envelope_revisions;
            PRAGMA user_version = 12;
            """
        )

    def fail_after_schema(conn: sqlite3.Connection) -> None:
        create_failure_envelope_schema(conn)
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(
        store_module,
        "create_failure_envelope_schema",
        fail_after_schema,
    )
    with pytest.raises(RuntimeError, match="injected migration failure"):
        RuntimeControlStore(path).initialize()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 12
        assert (
            conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'runtime_control_failure_envelope_revisions'
                """
            ).fetchone()
            is None
        )
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_caller_transaction_controls_rollback_commit_and_restart_readback(
    tmp_path: Path,
) -> None:
    from seektalent.diagnostics_storage import (
        load_failure_envelope_revision,
        store_failure_envelope_revision,
    )

    path = _initialized_path(tmp_path)
    envelope = _envelope()

    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        created = store_failure_envelope_revision(conn, envelope)
        assert created.disposition == "created"
        conn.rollback()
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM runtime_control_failure_envelope_revisions"
            ).fetchone()[0]
            == 0
        )

        conn.execute("BEGIN IMMEDIATE")
        store_failure_envelope_revision(conn, envelope)
        conn.commit()

    with sqlite3.connect(path) as restarted:
        loaded = load_failure_envelope_revision(
            restarted,
            failure_id=envelope.failure_id,
            revision=envelope.revision,
        )

    assert loaded == envelope


class _TrackingConnection(sqlite3.Connection):
    transaction_calls: list[str]

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.transaction_calls = []

    def execute(
        self,
        sql: str,
        parameters: object = (),
        /,
    ) -> sqlite3.Cursor:
        operation = sql.lstrip().split(maxsplit=1)[0].upper()
        if operation in {"BEGIN", "COMMIT", "ROLLBACK"}:
            self.transaction_calls.append(operation)
        return super().execute(sql, parameters)

    def commit(self) -> None:
        self.transaction_calls.append("COMMIT")
        super().commit()

    def rollback(self) -> None:
        self.transaction_calls.append("ROLLBACK")
        super().rollback()

    def close(self) -> None:
        self.transaction_calls.append("CLOSE")
        super().close()


def test_writer_uses_only_active_caller_connection_and_never_controls_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seektalent.diagnostics_storage import (
        FailureEnvelopeStorageError,
        store_failure_envelope_revision,
    )

    path = _initialized_path(tmp_path)
    conn = sqlite3.connect(path, factory=_TrackingConnection)
    try:
        with pytest.raises(FailureEnvelopeStorageError) as exc_info:
            store_failure_envelope_revision(conn, _envelope())
        assert exc_info.value.reason == "failure_envelope_transaction_required"

        conn.execute("BEGIN IMMEDIATE")
        conn.transaction_calls.clear()

        def forbidden_connect(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("writer opened a second connection")

        monkeypatch.setattr(sqlite3, "connect", forbidden_connect)
        store_failure_envelope_revision(conn, _envelope())

        assert conn.in_transaction
        assert conn.transaction_calls == []
    finally:
        sqlite3.Connection.rollback(conn)
        sqlite3.Connection.close(conn)


def test_exact_replay_is_idempotent_and_keeps_exact_canonical_identity(
    tmp_path: Path,
) -> None:
    from seektalent.diagnostics_storage import store_failure_envelope_revision

    path = _initialized_path(tmp_path)
    envelope = _envelope()

    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        created = store_failure_envelope_revision(conn, envelope)
        replayed = store_failure_envelope_revision(
            conn,
            canonical_diagnostics_bytes(envelope),
        )
        row = conn.execute(
            """
            SELECT canonical_bytes, canonical_sha256
            FROM runtime_control_failure_envelope_revisions
            """
        ).fetchone()
        conn.commit()

    assert created.disposition == "created"
    assert replayed.disposition == "exact_replay"
    assert created.ref == replayed.ref
    assert row == (
        canonical_diagnostics_bytes(envelope),
        canonical_diagnostics_hash(envelope),
    )
    assert len(row[0]) <= 32 * 1024


@pytest.mark.parametrize(
    ("first_revision", "second_revision", "reason"),
    (
        (2, None, "failure_envelope_revision_sequence"),
        (1, 3, "failure_envelope_revision_sequence"),
        (1, 1, "failure_envelope_revision_conflict"),
    ),
)
def test_revision_sequence_and_conflicts_fail_without_mutation(
    tmp_path: Path,
    first_revision: int,
    second_revision: int | None,
    reason: str,
) -> None:
    from seektalent.diagnostics_storage import (
        FailureEnvelopeStorageError,
        store_failure_envelope_revision,
    )

    path = _initialized_path(tmp_path)
    failure_id = "7" * 32

    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if first_revision == 1:
            store_failure_envelope_revision(conn, _envelope(failure_id=failure_id))
            candidate = _envelope(
                revision=second_revision or 1,
                failure_id=failure_id,
            )
            if second_revision == 1:
                payload = _failure()
                payload["failure_id"] = failure_id
                payload["detail"] = {
                    "operation_kind": "search",
                    "source_id": "liepin",
                }
                candidate = parse_failure_envelope(
                    json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
                )
        else:
            candidate = _envelope(
                revision=first_revision,
                failure_id=failure_id,
            )

        with pytest.raises(FailureEnvelopeStorageError) as exc_info:
            store_failure_envelope_revision(conn, candidate)
        assert exc_info.value.reason == reason
        count = conn.execute(
            "SELECT COUNT(*) FROM runtime_control_failure_envelope_revisions"
        ).fetchone()[0]
        conn.rollback()

    assert count == (1 if first_revision == 1 else 0)


def test_exact_replay_of_older_revision_remains_idempotent(
    tmp_path: Path,
) -> None:
    from seektalent.diagnostics_storage import store_failure_envelope_revision

    path = _initialized_path(tmp_path)
    failure_id = "7" * 32
    first = _envelope(failure_id=failure_id)
    second = _envelope(revision=2, failure_id=failure_id)

    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        store_failure_envelope_revision(conn, first)
        store_failure_envelope_revision(conn, second)
        replay = store_failure_envelope_revision(conn, first)
        assert replay.disposition == "exact_replay"
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM runtime_control_failure_envelope_revisions"
            ).fetchone()[0]
            == 2
        )


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE runtime_control_failure_envelope_revisions SET reason_code = 'changed'",
        "DELETE FROM runtime_control_failure_envelope_revisions",
        """
        INSERT OR REPLACE INTO runtime_control_failure_envelope_revisions
        SELECT * FROM runtime_control_failure_envelope_revisions
        """,
    ),
)
def test_sql_cannot_update_delete_or_replace_accepted_revision(
    tmp_path: Path,
    statement: str,
) -> None:
    from seektalent.diagnostics_storage import store_failure_envelope_revision

    path = _initialized_path(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        store_failure_envelope_revision(conn, _envelope())
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement)
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM runtime_control_failure_envelope_revisions"
            ).fetchone()[0]
            == 1
        )
        conn.rollback()


@pytest.mark.parametrize("corruption", ("bytes", "hash", "projection"))
def test_readback_corruption_fails_closed_without_payload_leakage(
    tmp_path: Path,
    corruption: str,
) -> None:
    from seektalent.diagnostics_storage import (
        FailureEnvelopeStorageError,
        load_failure_envelope_revision,
        store_failure_envelope_revision,
    )

    path = _initialized_path(tmp_path)
    envelope = _envelope()
    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        store_failure_envelope_revision(conn, envelope)
        conn.commit()
        conn.execute("DROP TRIGGER runtime_control_failure_envelopes_no_update")
        if corruption == "bytes":
            conn.execute(
                """
                UPDATE runtime_control_failure_envelope_revisions
                SET canonical_bytes = ?
                """,
                (b'{"secret":"raw payload"}',),
            )
        elif corruption == "hash":
            conn.execute(
                """
                UPDATE runtime_control_failure_envelope_revisions
                SET canonical_sha256 = ?
                """,
                ("0" * 64,),
            )
        else:
            conn.execute(
                """
                UPDATE runtime_control_failure_envelope_revisions
                SET run_id = ?
                """,
                ("other_run",),
            )
        conn.commit()

        with pytest.raises(FailureEnvelopeStorageError) as exc_info:
            load_failure_envelope_revision(
                conn,
                failure_id=envelope.failure_id,
                revision=1,
            )

    assert exc_info.value.reason == "failure_envelope_integrity_failed"
    assert str(exc_info.value) == "failure_envelope_integrity_failed"
    assert "secret" not in repr(exc_info.value)
    assert str(path) not in repr(exc_info.value)


def test_readback_unknown_ref_fails_closed() -> None:
    from seektalent.diagnostics_storage import (
        FailureEnvelopeStorageError,
        create_failure_envelope_schema,
        load_failure_envelope_revision,
    )

    with sqlite3.connect(":memory:") as conn:
        create_failure_envelope_schema(conn)
        with pytest.raises(FailureEnvelopeStorageError) as exc_info:
            load_failure_envelope_revision(
                conn,
                failure_id="missing_failure",
                revision=1,
            )

    assert exc_info.value.reason == "failure_envelope_not_found"


def test_storage_abort_has_no_partial_row_and_database_integrity_is_ok(
    tmp_path: Path,
) -> None:
    from seektalent.diagnostics_storage import (
        FailureEnvelopeStorageError,
        store_failure_envelope_revision,
    )

    path = _initialized_path(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TRIGGER injected_failure_envelope_abort
            AFTER INSERT ON runtime_control_failure_envelope_revisions
            BEGIN
              SELECT RAISE(ABORT, 'injected raw sqlite detail');
            END
            """
        )
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")

        with pytest.raises(FailureEnvelopeStorageError) as exc_info:
            store_failure_envelope_revision(conn, _envelope())

        assert exc_info.value.reason == "failure_envelope_storage_failed"
        assert "injected" not in repr(exc_info.value)
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM runtime_control_failure_envelope_revisions"
            ).fetchone()[0]
            == 0
        )
        conn.rollback()
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_sidecar_browser_source_and_wtscli_have_zero_writer_calls_or_table_access() -> None:
    root = Path(__file__).parents[1] / "src"
    forbidden_roots = (
        root / "seektalent_sidecar",
        root / "seektalent" / "browser",
        root / "seektalent" / "source_port",
        root / "seektalent" / "providers",
        root / "seektalent" / "wtscli",
    )
    violations: list[str] = []
    for package_root in forbidden_roots:
        if not package_root.exists():
            continue
        for path in package_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if (
                "store_failure_envelope_revision" in source
                or "runtime_control_failure_envelope_revisions" in source
            ):
                violations.append(str(path.relative_to(root)))
    assert violations == []


def test_production_failure_envelope_writer_callers_remain_zero() -> None:
    root = Path(__file__).parents[1] / "src"
    callers: list[str] = []
    for path in root.rglob("*.py"):
        if path.name == "diagnostics_storage.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "store_failure_envelope_revision"
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "store_failure_envelope_revision"
                )
            ):
                callers.append(f"{path.relative_to(root)}:{node.lineno}")
    assert callers == []
