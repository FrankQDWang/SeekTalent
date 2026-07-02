from __future__ import annotations

from typing import Any, cast

from seektalent.config import AppSettings


def make_settings(**overrides: object) -> AppSettings:
    defaults: dict[str, object] = {
        "liepin_worker_mode": "disabled",
        "liepin_browser_action_backend": "disabled",
    }
    if overrides.get("mock_cts") is True and "provider_name" not in overrides:
        defaults["provider_name"] = "cts"
    defaults.update(overrides)
    return cast(Any, AppSettings)(_env_file=None, **defaults)
