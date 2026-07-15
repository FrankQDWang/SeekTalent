from __future__ import annotations

from functools import cache
from importlib.resources import files


CONTROLLED_TAB_HELPER_TIMEOUT_SECONDS = 0.25

_LOCK_NAME = "__seektalentControlledTabLockV1"
_DEADLINE_NAME = "__seektalentControlledTabLockDeadlineAt"


@cache
def _source() -> str:
    return files("seektalent.opencli_browser").joinpath("controlled_tab_lock.js").read_text(encoding="utf-8")


def install_script(deadline_at: int | None) -> str:
    deadline = "null" if deadline_at is None else str(deadline_at)
    return f"window[{_DEADLINE_NAME!r}] = {deadline};\n{_source()}"


def unlock_script() -> str:
    return f"""(() => {{
  const lock = window[{_LOCK_NAME!r}];
  if (!lock || typeof lock.setAutomationActive !== "function") return {{ installed: false }};
  return lock.setAutomationActive(true);
}})()"""


def relock_script() -> str:
    return f"""(() => {{
  const lock = window[{_LOCK_NAME!r}];
  if (!lock || typeof lock.setAutomationActive !== "function") return {{ installed: false }};
  lock.updateDeadline(Date.now() + 60_000);
  return lock.setAutomationActive(false);
}})()"""
