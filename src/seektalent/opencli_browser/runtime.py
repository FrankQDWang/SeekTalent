from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


ALLOWED_BROWSER_COMMANDS = frozenset(
    {"open", "state", "get", "find", "click", "fill", "scroll", "wait", "tab", "bind", "unbind"}
)
FORBIDDEN_BROWSER_COMMANDS = frozenset({"eval", "network", "upload", "console", "dialog", "drag", "select"})


class OpenCliCommandRunner(Protocol):
    def run(self, argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None) -> str: ...


@dataclass(frozen=True)
class SubprocessOpenCliCommandRunner:
    def run(self, argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None) -> str:
        process_env = None
        if env:
            process_env = os.environ.copy()
            process_env.update(env)
        completed = subprocess.run(
            list(argv),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=process_env,
        )
        return completed.stdout


def strip_opencli_stdout_notice(output: str) -> str:
    return re.sub(
        r"\n\s*Update available:[^\n]*\n\s*Run: npm install -g @jackwener/opencli\s*$",
        "",
        output,
    ).strip()
