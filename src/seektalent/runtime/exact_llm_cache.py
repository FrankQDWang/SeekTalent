from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from seektalent.config import AppSettings
from seektalent.resources import resolve_user_path
from seektalent.tracing import jsonable, json_sha256


def stable_cache_key(parts: Any) -> str:
    return json_sha256(parts)


def _cache_path(settings: AppSettings) -> Path:
    path = resolve_user_path(settings.llm_cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path / "exact_llm_cache.sqlite3"


def _ensure_conn(settings: AppSettings) -> sqlite3.Connection:
    conn = sqlite3.connect(_cache_path(settings))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exact_llm_cache (
            namespace TEXT NOT NULL,
            key TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(namespace, key)
        )
        """
    )
    return conn


def get_cached_json(settings: AppSettings, *, namespace: str, key: str) -> dict[str, Any] | None:
    conn = _ensure_conn(settings)
    try:
        row = conn.execute(
            """
            SELECT payload
            FROM exact_llm_cache
            WHERE namespace = ? AND key = ?
            """,
            (namespace, key),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    return json.loads(row[0])


def put_cached_json(
    settings: AppSettings,
    *,
    namespace: str,
    key: str,
    payload: dict[str, Any],
) -> None:
    conn = _ensure_conn(settings)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    payload_text = json.dumps(
        jsonable(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        conn.execute(
            """
            INSERT INTO exact_llm_cache (namespace, key, payload, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(namespace, key) DO UPDATE SET
                payload = excluded.payload,
                created_at = excluded.created_at
            """,
            (namespace, key, payload_text, now),
        )
        conn.commit()
    finally:
        conn.close()
