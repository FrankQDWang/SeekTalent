from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


ALLOWED_BROWSER_COMMANDS = frozenset(
    {"open", "state", "get", "find", "click", "fill", "scroll", "wait", "tab", "bind", "unbind"}
)
FORBIDDEN_BROWSER_COMMANDS = frozenset({"eval", "network", "upload", "console", "dialog", "drag", "select"})
OPENCLI_ERROR_CODE_TO_REASON = {
    "bound_tab_mutation_blocked": "liepin_opencli_window_policy_blocked",
    "stale_ref": "liepin_opencli_stale_ref",
    "selector_not_found": "liepin_opencli_selector_not_found",
    "selector_ambiguous": "liepin_opencli_selector_ambiguous",
    "target_not_found": "liepin_opencli_target_not_found",
    "not_found": "liepin_opencli_target_not_found",
}
class OpenCliCommandRunner(Protocol):
    def run(self, argv: Sequence[str], *, timeout: int) -> str: ...


class ChromeWindowCounter(Protocol):
    def count(self) -> int | None: ...


class BlankChromeWindowCloser(Protocol):
    def close_blank_window(self) -> bool: ...


class CurrentChromeTabOpener(Protocol):
    def open_tab(self, url: str) -> bool: ...


@dataclass(frozen=True)
class SubprocessOpenCliCommandRunner:
    def run(self, argv: Sequence[str], *, timeout: int) -> str:
        completed = subprocess.run(
            list(argv),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return completed.stdout


@dataclass(frozen=True)
class SubprocessChromeWindowCounter:
    def count(self) -> int | None:
        try:
            completed = subprocess.run(
                ("osascript", "-e", 'tell application "Google Chrome" to get count of windows'),
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        try:
            return int(completed.stdout.strip())
        except ValueError:
            return None


@dataclass(frozen=True)
class SubprocessBlankChromeWindowCloser:
    def close_blank_window(self) -> bool:
        script = '''
tell application "Google Chrome"
  repeat with w in windows
    if (count of tabs of w) = 1 and (URL of active tab of w) is "about:blank" then
      close w
      return "closed"
    end if
  end repeat
  return "none"
end tell
'''
        try:
            completed = subprocess.run(
                ("osascript", "-e", script),
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False
        return completed.stdout.strip() == "closed"


@dataclass(frozen=True)
class SubprocessCurrentChromeTabOpener:
    def open_tab(self, url: str) -> bool:
        script = '''
on run argv
  set targetUrl to item 1 of argv
  set shouldReuseSearch to targetUrl contains "h.liepin.com/search/getConditionItem"
  tell application "Google Chrome"
    if (count of windows) = 0 then return "no-window"
    repeat with i from 1 to count of tabs of front window
      set tabUrl to URL of tab i of front window
      if tabUrl is targetUrl or (shouldReuseSearch and tabUrl contains "h.liepin.com/search/getConditionItem") then
        set active tab index of front window to i
        return URL of active tab of front window
      end if
    end repeat
    make new tab at end of tabs of front window with properties {URL:targetUrl}
    set active tab index of front window to (count of tabs of front window)
    return URL of active tab of front window
  end tell
end run
'''
        try:
            completed = subprocess.run(
                ("osascript", "-e", script, url),
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False
        opened_url = completed.stdout.strip()
        return bool(opened_url) and opened_url != "no-window"


def strip_opencli_stdout_notice(output: str) -> str:
    return re.sub(
        r"\n\s*Update available:[^\n]*\n\s*Run: npm install -g @jackwener/opencli\s*$",
        "",
        output,
    ).strip()
