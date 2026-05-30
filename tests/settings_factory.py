from __future__ import annotations

from typing import Any, cast

from seektalent.config import AppSettings


def make_settings(**overrides: object) -> AppSettings:
    return cast(Any, AppSettings)(_env_file=None, **overrides)
