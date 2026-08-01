from __future__ import annotations

import os
from pathlib import Path

from seektalent.config import AppSettings


def liepin_db_path(settings: AppSettings) -> Path:
    path = Path(settings.liepin_connector_db_path)
    if path.is_absolute():
        return path
    root = _production_home() if settings.runtime_mode == "prod" else Path(settings.workspace_root) if settings.workspace_root else None
    return root / path if root is not None else path


def _production_home() -> Path:
    return Path(
        os.environ.get("SEEKTALENT_INSTALL_HOME", str(Path.home()))
    )
