from __future__ import annotations

import os
import secrets
from collections.abc import MutableMapping
from pathlib import Path


INTERNAL_LIEPIN_ENV_VARS = (
    "SEEKTALENT_LIEPIN_API_TOKEN",
    "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET",
    "SEEKTALENT_LIEPIN_STREAM_TOKEN_SECRET",
)
LOCAL_DEVELOPMENT_VALUES = {
    "local-development",
    "local-development-liepin-api-token",
}


def ensure_workbench_internal_liepin_env(
    env: MutableMapping[str, str] | None = None,
    *,
    secrets_file: Path | None = None,
) -> None:
    target_env = env if env is not None else os.environ
    path = secrets_file or (Path.home() / ".seektalent" / "workbench-secrets.env")
    stored = _read_env_file(path)
    changed = False
    for name in INTERNAL_LIEPIN_ENV_VARS:
        value = target_env.get(name) or stored.get(name)
        if not value or value in LOCAL_DEVELOPMENT_VALUES:
            value = secrets.token_urlsafe(32)
            changed = True
        stored[name] = value
        target_env[name] = value
    if changed or not path.exists():
        _write_env_file(path, stored)


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in INTERNAL_LIEPIN_ENV_VARS:
            values[key] = value.strip()
    return values


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{name}={values[name]}" for name in INTERNAL_LIEPIN_ENV_VARS]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
