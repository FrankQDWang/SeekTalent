from __future__ import annotations

import re


ALLOWED_BROWSER_COMMANDS = frozenset(
    {"open", "state", "get", "find", "click", "fill", "scroll", "wait", "tab", "bind", "unbind"}
)
FORBIDDEN_BROWSER_COMMANDS = frozenset({"eval", "network", "upload", "console", "dialog", "drag", "select"})


def strip_opencli_stdout_notice(output: str) -> str:
    return re.sub(
        r"\n\s*Update available:[^\n]*\n\s*Run: npm install -g @jackwener/opencli\s*$",
        "",
        output,
    ).strip()
