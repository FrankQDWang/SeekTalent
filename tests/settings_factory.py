from __future__ import annotations

from pathlib import Path
from tempfile import mkdtemp
from typing import Any, cast

from seektalent.config import AppSettings


def make_settings(**overrides: object) -> AppSettings:
    isolated_db_root = Path(mkdtemp(prefix="seektalent-test-settings-"))
    defaults: dict[str, object] = {
        "liepin_worker_mode": "disabled",
        "liepin_browser_action_backend": "disabled",
    }
    if "workspace_root" not in overrides and "corpus_db_path" not in overrides:
        defaults["corpus_db_path"] = str(isolated_db_root / "corpus.sqlite3")
    defaults.update(overrides)
    return cast(Any, AppSettings)(_env_file=None, **defaults)
