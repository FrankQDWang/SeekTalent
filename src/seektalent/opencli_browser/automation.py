from __future__ import annotations

import json
import random
import re
import subprocess
import time
from collections.abc import Mapping, Sequence

from seektalent.opencli_browser.contracts import (
    OpenCliBrowserConfig,
    OpenCliBrowserError,
    OpenCliBrowserResult,
    OpenCliBrowserTiming,
    OpenCliBrowserTimingRecorder,
)
from seektalent.opencli_browser.reason_codes import (
    OPENCLI_BOOTSTRAP_FAILED,
    OPENCLI_COMMAND_MISSING,
    OPENCLI_DAEMON_NOT_RUNNING,
    OPENCLI_DAEMON_STALE,
    OPENCLI_ERROR_CODE_TO_REASON,
    OPENCLI_EXTENSION_DISCONNECTED,
    OPENCLI_FORBIDDEN_COMMAND,
    OPENCLI_STATUS_UNAVAILABLE,
    OPENCLI_TIMEOUT,
)
from seektalent.opencli_browser.runtime import (
    ALLOWED_BROWSER_COMMANDS,
    FORBIDDEN_BROWSER_COMMANDS,
    OpenCliCommandRunner,
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
        timing_recorder: OpenCliBrowserTimingRecorder | None = None,
    ) -> None:
        self.config = config
        self.commands = commands or SubprocessOpenCliCommandRunner()
        self._timing_recorder = timing_recorder

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

    def restart_daemon(self) -> OpenCliBrowserResult:
        try:
            output = self._run(tuple(self.config.command) + ("daemon", "restart"))
        except OpenCliBrowserError as exc:
            return OpenCliBrowserResult(ok=False, action="restart_daemon", safe_reason_code=exc.safe_reason_code)
        return OpenCliBrowserResult(ok=True, action="restart_daemon", private_output=output)

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
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
        self.pace_before_action("scroll")
        output = self.run_browser_command("scroll", (direction,))
        return OpenCliBrowserResult(ok=True, action="scroll", private_output=output)

    def wait_time(self, *, seconds: int) -> OpenCliBrowserResult:
        if seconds < 1 or seconds > 10:
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
        output = self.run_browser_command("wait", ("time", str(seconds)))
        return OpenCliBrowserResult(ok=True, action="wait_time", private_output=output)

    def click_ref(self, ref: str) -> str:
        if not _is_safe_page_id(ref):
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
        return self._run(tuple(self.config.command) + ("browser", self.config.session, "click", ref))

    def find_css(self, selector: str, *, limit: int, text_max: int) -> str:
        if not selector.strip() or "\x00" in selector:
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
        if limit < 1 or limit > 100:
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
        if text_max < 1 or text_max > 10_000:
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
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
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
        return self._run(tuple(self.config.command) + ("browser", self.config.session, "eval", script))

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
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
        args_tuple = tuple(args)
        _validate_command_shape(command, args_tuple)
        return self._run(tuple(self.config.command) + ("browser", self.config.session, command, *args_tuple))

    def _run(self, argv: Sequence[str]) -> str:
        started = time.perf_counter()
        ok = False
        safe_reason_code: str | None = None
        try:
            output = strip_opencli_stdout_notice(
                self.commands.run(
                    tuple(argv),
                    timeout=self.config.timeout_seconds,
                    env={"OPENCLI_WINDOW": self.config.window_mode},
                )
            )
            ok = True
            return output
        except FileNotFoundError as exc:
            safe_reason_code = OPENCLI_COMMAND_MISSING
            raise OpenCliBrowserError(OPENCLI_COMMAND_MISSING) from exc
        except subprocess.TimeoutExpired as exc:
            safe_reason_code = OPENCLI_TIMEOUT
            raise OpenCliBrowserError(OPENCLI_TIMEOUT) from exc
        except subprocess.CalledProcessError as exc:
            output = f"{getattr(exc, 'stdout', None) or getattr(exc, 'output', '') or ''}\n{exc.stderr or ''}"
            if exc.returncode == 127 and "SeekTalent OpenCLI bootstrap failed:" in output:
                safe_reason_code = OPENCLI_BOOTSTRAP_FAILED
                raise OpenCliBrowserError(OPENCLI_BOOTSTRAP_FAILED) from exc
            if "Extension" in output and ("not connected" in output or "disconnected" in output):
                safe_reason_code = OPENCLI_EXTENSION_DISCONNECTED
                raise OpenCliBrowserError(OPENCLI_EXTENSION_DISCONNECTED) from exc
            if "Daemon:" in output:
                safe_reason_code = _opencli_status_reason(output) or OPENCLI_STATUS_UNAVAILABLE
                raise OpenCliBrowserError(safe_reason_code) from exc
            reason = _safe_reason_from_opencli_error_output(output)
            if reason is not None:
                safe_reason_code = reason
                raise OpenCliBrowserError(reason) from exc
            safe_reason_code = OPENCLI_STATUS_UNAVAILABLE
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE) from exc
        finally:
            self._record_timing(
                argv=tuple(argv),
                duration_ms=(time.perf_counter() - started) * 1000,
                ok=ok,
                safe_reason_code=safe_reason_code,
            )

    def _record_timing(
        self,
        *,
        argv: tuple[str, ...],
        duration_ms: float,
        ok: bool,
        safe_reason_code: str | None,
    ) -> None:
        if self._timing_recorder is None:
            return
        try:
            self._timing_recorder.record(
                OpenCliBrowserTiming(
                    command=self._safe_command_label(argv),
                    session=self._safe_command_session(argv),
                    argv_len=len(argv),
                    duration_ms=round(duration_ms, 3),
                    ok=ok,
                    safe_reason_code=safe_reason_code,
                )
            )
        except Exception:  # noqa: BLE001 - timing metadata is best-effort and must not affect actions.
            return

    def _safe_command_label(self, argv: tuple[str, ...]) -> str:
        action = self._opencli_action(argv)
        if len(action) >= 2 and action[0] == "daemon":
            return f"daemon.{action[1]}"
        if len(action) >= 3 and action[0] == "browser":
            return f"browser.{action[2]}"
        return "unknown"

    def _safe_command_session(self, argv: tuple[str, ...]) -> str | None:
        action = self._opencli_action(argv)
        if len(action) >= 2 and action[0] == "browser":
            session = action[1]
            return session if _is_safe_page_id(session) else None
        return None

    def _opencli_action(self, argv: tuple[str, ...]) -> tuple[str, ...]:
        command_len = len(self.config.command)
        if len(argv) <= command_len:
            return ()
        return argv[command_len:]


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
        raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
    if command == "open" and len(args) == 3 and not _is_safe_page_id(args[1]):
        raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
    if command == "tab" and args[0] == "new" and ("\x00" in args[1] or not args[1].strip()):
        raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
    if command == "tab" and args[0] in {"select", "close"} and not _is_safe_page_id(args[1]):
        raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)


def _opencli_status_reason(output: str) -> str | None:
    if "Daemon: running" in output:
        if "Extension: connected" in output:
            return None
        if "Extension:" in output:
            return OPENCLI_EXTENSION_DISCONNECTED
        return OPENCLI_STATUS_UNAVAILABLE
    if "Daemon: stale" in output:
        return OPENCLI_DAEMON_STALE
    if "Daemon: not running" in output:
        return OPENCLI_DAEMON_NOT_RUNNING
    if "Daemon:" in output:
        return OPENCLI_DAEMON_NOT_RUNNING
    return OPENCLI_STATUS_UNAVAILABLE


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
