from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping, Sequence

from seektalent.cli import main as seektalent_main


DOMI_NODE_KEYS = ("SEEKTALENT_DOMI_NODE", "DOMI_NODE")
_DOMI_ENV_KEYS = (
    "SEEKTALENT_TEXT_LLM_PROVIDER_LABEL",
    "SEEKTALENT_OPENCLI_NODE_POLICY",
    "SEEKTALENT_OPENCLI_NODE",
    "SEEKTALENT_DOMI_LLM_CHANNEL",
)
_JWT_MISSING_MESSAGE = "未获取到 Domi 大模型授权。请在当前终端设置 SEEKTALENT_DOMI_JWT 后重试。"
_NODE_MISSING_MESSAGE = "未找到 Domi Node 运行时。请在当前终端设置 SEEKTALENT_DOMI_NODE 或 DOMI_NODE 后重试。"


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if _is_help_request(args):
        return seektalent_main(["workbench", *args])

    original_env = {key: os.environ.get(key) for key in _DOMI_ENV_KEYS}
    error = prepare_domi_env(os.environ)
    if error is not None:
        reason_code, message = error
        print(f"reason_code={reason_code} {message}", file=sys.stderr)
        return 1
    try:
        return seektalent_main(["workbench", *args])
    finally:
        _restore_env(os.environ, original_env)


def prepare_domi_env(env: MutableMapping[str, str]) -> tuple[str, str] | None:
    if _first_env(env, ("SEEKTALENT_DOMI_JWT",)) is None:
        return "seektalent_domi_jwt_missing", _JWT_MISSING_MESSAGE

    node = _first_env(env, DOMI_NODE_KEYS)
    if node is None:
        return "domi_node_missing", _NODE_MISSING_MESSAGE

    env["SEEKTALENT_TEXT_LLM_PROVIDER_LABEL"] = "domi"
    env["SEEKTALENT_OPENCLI_NODE_POLICY"] = "domi"
    env["SEEKTALENT_OPENCLI_NODE"] = node
    env.setdefault("SEEKTALENT_DOMI_LLM_CHANNEL", "seek_talent")
    return None


def _first_env(env: MutableMapping[str, str], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = env.get(key)
        if value is None:
            continue
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _is_help_request(args: Sequence[str]) -> bool:
    return any(arg in {"--help", "-h"} for arg in args)


def _restore_env(env: MutableMapping[str, str], values: dict[str, str | None]) -> None:
    for key, value in values.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value


if __name__ == "__main__":
    raise SystemExit(main())
