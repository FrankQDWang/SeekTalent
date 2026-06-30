from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping
from pathlib import Path

from seektalent.config import DEFAULT_LIEPIN_OPENCLI_COMMAND
from seektalent.workbench_internal_secrets import ensure_workbench_internal_liepin_env


PRODUCT_USER_ENV_VARS = frozenset(
    {
        "SEEKTALENT_TEXT_LLM_API_KEY",
    }
)

_PASSTHROUGH_ENV_VARS = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "SHELL",
        "TMPDIR",
        "USER",
    }
)


def load_product_user_env(
    env: MutableMapping[str, str],
    *,
    env_file: str | Path | None = None,
) -> None:
    path = Path(env_file).expanduser() if env_file is not None else Path.home() / ".seektalent" / ".env"
    values = _read_product_env_file(path)
    for key in PRODUCT_USER_ENV_VARS:
        value = values.get(key)
        if value and key not in env:
            env[key] = value


def build_workbench_command_env(
    base_env: Mapping[str, str] | None = None,
    *,
    env_file: str | Path | None = None,
) -> dict[str, str]:
    source_env = os.environ if base_env is None else base_env
    env = {key: value for key, value in source_env.items() if key in _PASSTHROUGH_ENV_VARS}
    for key in PRODUCT_USER_ENV_VARS:
        value = source_env.get(key)
        if value:
            env[key] = value
    env["SEEKTALENT_WORKSPACE_ROOT"] = str(Path.home())
    env["SEEKTALENT_RUNTIME_MODE"] = "prod"
    env["SEEKTALENT_PROVIDER_NAME"] = "liepin"
    env["SEEKTALENT_LIEPIN_WORKER_MODE"] = "opencli"
    env["SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND"] = "opencli"
    load_product_user_env(env, env_file=env_file)
    env["SEEKTALENT_LIEPIN_OPENCLI_COMMAND"] = DEFAULT_LIEPIN_OPENCLI_COMMAND
    ensure_workbench_internal_liepin_env(env)
    return env


def _read_product_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in PRODUCT_USER_ENV_VARS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values
