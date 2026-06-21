from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import seektalent_ui.maintenance as maintenance
from seektalent.config import AppSettings
from seektalent_ui.workbench_paths import workbench_db_path
from seektalent_ui.workbench_store import WorkbenchStore


SchemaSignature = dict[str, object]


def main() -> None:
    with TemporaryDirectory(prefix="seektalent-schema-modes-") as temp_dir:
        root = Path(temp_dir)
        canonical_db = root / "canonical" / "workbench.sqlite3"
        dev_root = root / "dev-workspace"
        prod_home = root / "prod-home"

        canonical = _initialized_workbench_schema(canonical_db)
        dev_db = _dev_workbench_db(dev_root)
        with _temporary_home(prod_home):
            prod_db = _prod_workbench_db()

        _assert_expected_paths(dev_db=dev_db, dev_root=dev_root, prod_db=prod_db, prod_home=prod_home)

        for label, database_path in (("dev", dev_db), ("prod", prod_db)):
            maintenance._validate_workbench_schema(database_path)
            signature = _schema_signature(database_path)
            if signature != canonical:
                raise SystemExit(f"{label} workbench schema does not match canonical signature")

        if _schema_signature(dev_db) != _schema_signature(prod_db):
            raise SystemExit("dev and prod workbench schemas differ")

        tables = canonical.get("tables")
        indexes = canonical.get("indexes")
        if not isinstance(tables, list) or not isinstance(indexes, dict):
            raise SystemExit("canonical workbench schema signature is malformed")

        print(
            json.dumps(
                {
                    "status": "pass",
                    "dev_db": _display_path(dev_db),
                    "prod_db": _display_path(prod_db),
                    "tables": len(tables),
                    "indexes": len(indexes),
                },
                sort_keys=True,
            )
        )


def _initialized_workbench_schema(database_path: Path) -> SchemaSignature:
    WorkbenchStore(database_path).list_security_audit_events()
    return _schema_signature(database_path)


def _dev_workbench_db(workspace_root: Path) -> Path:
    settings = AppSettings(runtime_mode="dev", workspace_root=str(workspace_root), _env_file=None)
    database_path = workbench_db_path(settings)
    WorkbenchStore(database_path).list_security_audit_events()
    return database_path


def _prod_workbench_db() -> Path:
    settings = AppSettings(runtime_mode="prod", _env_file=None)
    database_path = workbench_db_path(settings)
    WorkbenchStore(database_path).list_security_audit_events()
    return database_path


def _assert_expected_paths(*, dev_db: Path, dev_root: Path, prod_db: Path, prod_home: Path) -> None:
    expected_dev = dev_root / ".seektalent" / "workbench.sqlite3"
    expected_prod = prod_home / ".seektalent" / "workbench.sqlite3"
    if dev_db != expected_dev:
        raise SystemExit(f"dev workbench path mismatch: expected {expected_dev}, got {dev_db}")
    if prod_db != expected_prod:
        raise SystemExit(f"prod workbench path mismatch: expected {expected_prod}, got {prod_db}")
    if dev_db == prod_db:
        raise SystemExit("dev and prod workbench paths must not point to the same database")


def _schema_signature(database_path: Path) -> SchemaSignature:
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as conn:
        table_rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        index_rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL").fetchall()
        table_names = sorted(str(row[0]) for row in table_rows)
        table_sql = {
            str(row[0]): maintenance._normalize_schema_sql(row[1])
            for row in table_rows
        }
        return {
            "checks": {
                table: maintenance._check_constraint_fragments(table_sql[table])
                for table in table_names
            },
            "columns": {
                table: maintenance._table_column_signature(conn, table)
                for table in table_names
            },
            "foreign_keys": {
                table: maintenance._table_foreign_key_signature(conn, table)
                for table in table_names
            },
            "indexes": {
                str(row[0]): maintenance._normalize_schema_sql(row[1])
                for row in index_rows
            },
            "tables": table_names,
        }


@contextmanager
def _temporary_home(home: Path) -> Iterator[None]:
    previous_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        yield
    finally:
        if previous_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous_home


def _display_path(path: Path) -> str:
    return str(path)


if __name__ == "__main__":
    main()
