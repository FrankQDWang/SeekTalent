from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence

from seektalent.providers.liepin.opencli_browser_contracts import (
    OpenCliBrowserConfig,
    OpenCliBrowserError,
    OpenCliBrowserResult,
)
from seektalent.providers.liepin.opencli_runtime import (
    ALLOWED_BROWSER_COMMANDS,
    FORBIDDEN_BROWSER_COMMANDS,
    OPENCLI_ERROR_CODE_TO_REASON,
    BlankChromeWindowCloser,
    ChromeWindowCounter,
    CurrentChromeTabOpener,
    OpenCliCommandRunner,
    SubprocessBlankChromeWindowCloser,
    SubprocessChromeWindowCounter,
    SubprocessCurrentChromeTabOpener,
    SubprocessOpenCliCommandRunner,
    strip_opencli_stdout_notice,
)


_SAFE_PAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class OpenCliBrowserAutomation:
    def __init__(
        self,
        *,
        config: OpenCliBrowserConfig,
        commands: OpenCliCommandRunner | None = None,
        window_counter: ChromeWindowCounter | None = None,
        blank_window_closer: BlankChromeWindowCloser | None = None,
        current_tab_opener: CurrentChromeTabOpener | None = None,
    ) -> None:
        self.config = config
        self.commands = commands or SubprocessOpenCliCommandRunner()
        self.window_counter = window_counter or SubprocessChromeWindowCounter()
        self.blank_window_closer = blank_window_closer or SubprocessBlankChromeWindowCloser()
        self.current_tab_opener = current_tab_opener or SubprocessCurrentChromeTabOpener()

    def status(self) -> OpenCliBrowserResult:
        try:
            output = self._run(tuple(self.config.command) + ("daemon", "status"))
        except OpenCliBrowserError as exc:
            return OpenCliBrowserResult(ok=False, action="status", safe_reason_code=exc.safe_reason_code)
        reason = _opencli_status_reason(output)
        if reason is not None:
            return OpenCliBrowserResult(
                ok=False,
                action="status",
                safe_reason_code=reason,
                private_output=output,
            )
        return OpenCliBrowserResult(ok=True, action="status", private_output=output)

    def get_url(self) -> OpenCliBrowserResult:
        output = self.run_browser_command("get", ("url",))
        return OpenCliBrowserResult(ok=True, action="get_url", private_output=output)

    def find(self, *, query: str) -> OpenCliBrowserResult:
        output = self.run_browser_command("find", (query,))
        return OpenCliBrowserResult(ok=True, action="find", private_output=output)

    def fill(self, *, target_args: tuple[str, ...], text_size: int) -> OpenCliBrowserResult:
        self.pace_before_action("fill")
        output = self.run_browser_command("fill", target_args)
        return OpenCliBrowserResult(ok=True, action="fill", counts={"chars": text_size}, private_output=output)

    def click(self, *, target_args: tuple[str, ...]) -> OpenCliBrowserResult:
        self.pace_before_action("click")
        output = self.run_browser_command("click", target_args)
        return OpenCliBrowserResult(ok=True, action="click", private_output=output)

    def scroll(self, *, direction: str) -> OpenCliBrowserResult:
        if direction not in {"up", "down"}:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        self.pace_before_action("scroll")
        output = self.run_browser_command("scroll", (direction,))
        return OpenCliBrowserResult(ok=True, action="scroll", private_output=output)

    def wait_time(self, *, seconds: int) -> OpenCliBrowserResult:
        if seconds < 1 or seconds > 10:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        output = self.run_browser_command("wait", ("time", str(seconds)))
        return OpenCliBrowserResult(ok=True, action="wait_time", private_output=output)

    def click_ref(self, ref: str) -> str:
        if not _is_safe_page_id(ref):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        return self._run(tuple(self.config.command) + ("browser", self.config.session, "click", ref))

    def find_css(self, selector: str, *, limit: int, text_max: int) -> str:
        if not selector.strip() or "\x00" in selector:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        if limit < 1 or limit > 100:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        if text_max < 1 or text_max > 10_000:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        return self._run(
            tuple(self.config.command)
            + (
                "browser",
                self.config.session,
                "find",
                "--css",
                selector,
                "--limit",
                str(limit),
                "--text-max",
                str(text_max),
            )
        )

    def readonly_eval(self, script: str) -> str:
        if not script.strip() or "\x00" in script:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        return self._run(tuple(self.config.command) + ("browser", self.config.session, "eval", script))

    def launch_idle_cleanup_worker(self) -> None:
        if not self.config.cleanup_worker_enabled:
            return
        env = os.environ.copy()
        if self.config.lease_dir is not None:
            env["SEEKTALENT_LIEPIN_OPENCLI_LEASE_DIR"] = str(self.config.lease_dir)
        env["SEEKTALENT_LIEPIN_OPENCLI_IDLE_CLOSE_SECONDS"] = str(self.config.idle_close_seconds)
        env["SEEKTALENT_LIEPIN_OPENCLI_CLOSE_BLANK_WINDOW"] = "true" if self.config.close_blank_window else "false"
        try:
            subprocess.Popen(
                (sys.executable, "-m", "seektalent.providers.liepin.opencli_browser_cli", "watch_idle_lease"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
        except OSError:
            return

    def pace_before_action(self, action: str) -> None:
        if not self.config.pacing_enabled:
            return
        if action not in {"fill", "click", "scroll"}:
            return
        low = max(0, self.config.pacing_min_ms) / 1000
        high = max(self.config.pacing_max_ms, self.config.pacing_min_ms) / 1000
        if high <= 0:
            return
        time.sleep(random.uniform(low, high))

    def run_browser_command(self, command: str, args: Sequence[str]) -> str:
        if command not in ALLOWED_BROWSER_COMMANDS or command in FORBIDDEN_BROWSER_COMMANDS:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        args_tuple = tuple(args)
        _validate_command_shape(command, args_tuple)
        return self._run(tuple(self.config.command) + ("browser", self.config.session, command, *args_tuple))

    def _run(self, argv: Sequence[str]) -> str:
        try:
            return strip_opencli_stdout_notice(self.commands.run(tuple(argv), timeout=self.config.timeout_seconds))
        except FileNotFoundError as exc:
            raise OpenCliBrowserError("liepin_opencli_command_missing") from exc
        except subprocess.TimeoutExpired as exc:
            raise OpenCliBrowserError("liepin_opencli_timeout") from exc
        except subprocess.CalledProcessError as exc:
            output = f"{getattr(exc, 'stdout', None) or getattr(exc, 'output', '') or ''}\n{exc.stderr or ''}"
            if "Extension" in output and ("not connected" in output or "disconnected" in output):
                raise OpenCliBrowserError("liepin_opencli_extension_disconnected") from exc
            if "Daemon:" in output:
                raise OpenCliBrowserError(_opencli_status_reason(output) or "liepin_opencli_status_unavailable") from exc
            reason = _safe_reason_from_opencli_error_output(output)
            if reason is not None:
                raise OpenCliBrowserError(reason) from exc
            raise OpenCliBrowserError("liepin_opencli_status_unavailable") from exc


def _validate_command_shape(command: str, args: tuple[str, ...]) -> None:
    valid = {
        "state": len(args) == 0,
        "get": args == ("url",),
        "open": len(args) == 1 or (len(args) == 3 and args[0] == "--tab" and bool(args[1].strip())),
        "find": len(args) == 1,
        "click": len(args) == 1 or _is_role_button_command(args),
        "fill": len(args) == 2 or _is_role_fill_command(args),
        "scroll": args in {("up",), ("down",)},
        "wait": len(args) == 2 and args[0] in {"time", "text", "selector"},
        "bind": len(args) == 0,
        "unbind": len(args) == 0,
        "tab": args == ("list",)
        or (len(args) == 2 and args[0] in {"new", "select", "close"} and bool(args[1].strip())),
    }.get(command, False)
    if not valid:
        raise OpenCliBrowserError("liepin_opencli_forbidden_command")
    if command == "open" and len(args) == 3 and not _is_safe_page_id(args[1]):
        raise OpenCliBrowserError("liepin_opencli_forbidden_command")
    if command == "tab" and args[0] in {"new", "select", "close"} and not _is_safe_page_id(args[1]):
        raise OpenCliBrowserError("liepin_opencli_forbidden_command")


def _opencli_status_reason(output: str) -> str | None:
    if "Daemon: running" in output:
        if "Extension: connected" in output:
            return None
        if "Extension:" in output:
            return "liepin_opencli_extension_disconnected"
        return "liepin_opencli_status_unavailable"
    if "Daemon: stale" in output:
        return "liepin_opencli_daemon_stale"
    if "Daemon: not running" in output:
        return "liepin_opencli_daemon_not_running"
    if "Daemon:" in output:
        return "liepin_opencli_daemon_not_running"
    return "liepin_opencli_status_unavailable"


def _safe_reason_from_opencli_error_output(output: str) -> str | None:
    for candidate in (output.strip(), *output.splitlines()):
        text = candidate.strip()
        if not text:
            continue
        payload = _json_mapping_or_none(text)
        if payload is None:
            continue
        error = payload.get("error")
        error_payload = _string_key_mapping_or_none(error)
        if error_payload is None:
            continue
        raw_code = str(error_payload.get("code") or "").strip().lower().replace("-", "_")
        reason = OPENCLI_ERROR_CODE_TO_REASON.get(raw_code)
        if reason is not None:
            return reason
    return None


def _string_key_mapping_or_none(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


def _json_mapping_or_none(text: str) -> Mapping[str, object] | None:
    try:
        payload, _end = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        return None
    return _string_key_mapping_or_none(payload)


def _is_role_button_command(args: tuple[str, ...]) -> bool:
    return (
        len(args) == 4
        and args[0] == "--role"
        and args[1] == "button"
        and args[2] in {"--name", "--text"}
        and bool(args[3].strip())
    )


def _is_role_fill_command(args: tuple[str, ...]) -> bool:
    if len(args) != 5 or args[0] != "--role" or args[2] != "--nth":
        return False
    if args[1] not in {"textbox", "combobox"}:
        return False
    try:
        nth = int(args[3])
    except ValueError:
        return False
    return 0 <= nth <= 20 and bool(args[4].strip())


def _is_safe_page_id(value: str) -> bool:
    return bool(_SAFE_PAGE_ID_PATTERN.fullmatch(value))
