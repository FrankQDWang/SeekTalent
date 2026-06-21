from __future__ import annotations

from pathlib import Path

from seektalent.config import AppSettings


def liepin_db_path(settings: AppSettings) -> Path:
    path = Path(settings.liepin_connector_db_path)
    if path.is_absolute():
        return path
    root = Path.home() if settings.runtime_mode == "prod" else Path(settings.workspace_root) if settings.workspace_root else None
    return root / path if root is not None else path


def workbench_db_path(settings: AppSettings) -> Path:
    root = Path.home() if settings.runtime_mode == "prod" else Path(settings.workspace_root or ".")
    return root / ".seektalent" / "workbench.sqlite3"


def agent_workbench_stream_db_path(settings: AppSettings) -> Path:
    root = Path.home() if settings.runtime_mode == "prod" else Path(settings.workspace_root or ".")
    return root / ".seektalent" / "agent_workbench_stream.sqlite3"
