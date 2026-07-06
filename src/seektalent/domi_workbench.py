from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping, Sequence

from seektalent.cli import main as seektalent_main


DOMI_NODE_KEYS = ("SEEKTALENT_DOMI_NODE", "DOMI_NODE")
_JWT_MISSING_MESSAGE = "未获取到 Domi 大模型授权。请在当前终端设置 SEEKTALENT_DOMI_JWT 后重试。"
_NODE_MISSING_MESSAGE = "未找到 Domi Node 运行时。请在当前终端设置 SEEKTALENT_DOMI_NODE 或 DOMI_NODE 后重试。"


def main(argv: Sequence[str] | None = None) -> int:
    error = prepare_domi_env(os.environ)
    if error is not None:
        reason_code, message = error
        print(f"reason_code={reason_code} {message}", file=sys.stderr)
        return 1
    return seektalent_main(["workbench", *list(sys.argv[1:] if argv is None else argv)])


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


if __name__ == "__main__":
    raise SystemExit(main())
