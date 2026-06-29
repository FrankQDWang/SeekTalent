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


class ChromeWindowCounter(Protocol):
    def count(self) -> int | None: ...


class BlankChromeWindowCloser(Protocol):
    def close_blank(self) -> bool: ...


class CurrentChromeTabOpener(Protocol):
    def open_tab(self, url: str) -> bool: ...


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
    def close_blank(self) -> bool:
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
    reuse_url_fragments: tuple[str, ...] = ()

    def open_tab(self, url: str) -> bool:
        script = '''
on run argv
  set targetUrl to item 1 of argv
  set reuseFragments to {}
  if (count of argv) > 1 then
    repeat with i from 2 to count of argv
      set end of reuseFragments to item i of argv
    end repeat
  end if
  tell application "Google Chrome"
    if (count of windows) = 0 then return "no-window"
    repeat with i from 1 to count of tabs of front window
      set tabUrl to URL of tab i of front window
      set shouldReuse to false
      repeat with fragment in reuseFragments
        if targetUrl contains fragment and tabUrl contains fragment then set shouldReuse to true
      end repeat
      if tabUrl is targetUrl or shouldReuse then
        set active tab index of front window to i
        set URL of active tab of front window to targetUrl
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
                ("osascript", "-e", script, url, *self.reuse_url_fragments),
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
