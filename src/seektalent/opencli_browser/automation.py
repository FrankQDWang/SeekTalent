from __future__ import annotations

import json
import random
import re
import subprocess
import time
import uuid
from collections.abc import Mapping, Sequence
from urllib.parse import urlparse

from seektalent.opencli_browser.contracts import (
    BrowserControlScope,
    BrowserHostTab,
    OpenCliBrowserConfig,
    OpenCliBrowserError,
    OpenCliBrowserResult,
    OpenCliBrowserTiming,
    OpenCliBrowserTimingRecorder,
    OpenCliOwnedTab,
    OpenCliTabKind,
)
from seektalent.opencli_browser.daemon_transport import (
    OpenCliDaemonAction,
    OpenCliDaemonClient,
    OpenCliDaemonResult,
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
from seektalent.opencli_browser.lifecycle import OPENCLI_OWNED_TAB_IDLE_SECONDS
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
        daemon: OpenCliDaemonClient | None = None,
        timing_recorder: OpenCliBrowserTimingRecorder | None = None,
    ) -> None:
        self.config = config
        self.commands = commands or SubprocessOpenCliCommandRunner()
        self._daemon = daemon
        self._daemon_page: str | None = None
        self._control_scope: BrowserControlScope | None = None
        self._owned_tabs: dict[str, OpenCliOwnedTab] = {}
        self._timing_recorder = timing_recorder

    @property
    def daemon_enabled(self) -> bool:
        return self._daemon is not None

    def activate_control_scope(self, control_key: str) -> BrowserControlScope:
        if self._daemon is None or not control_key.strip() or len(control_key) > 256:
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
        result = self._daemon_command(
            "control",
            {"op": "activate", "controlKey": control_key},
            label="control.activate",
        )
        payload = _string_key_mapping_or_none(result.data)
        fence_token = payload.get("fenceToken") if payload is not None else None
        returned_key = payload.get("controlKey") if payload is not None else None
        if returned_key != control_key or type(fence_token) is not int or fence_token < 1:
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
        scope = BrowserControlScope(
            scope_id=uuid.uuid4().hex,
            control_key=control_key,
            fence_token=fence_token,
        )
        self._control_scope = scope
        self._daemon_page = None
        self._owned_tabs = {}
        return scope

    def find_host_tabs(self, url_prefix: str) -> tuple[BrowserHostTab, ...]:
        parsed = urlparse(url_prefix)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
        params = self._daemon_session_params(f"st_probe_{uuid.uuid4().hex}")
        params.update({"op": "find", "urlPrefix": url_prefix})
        result = self._daemon_command("tabs", params, label="tab.find")
        if not isinstance(result.data, list):
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
        candidates: list[BrowserHostTab] = []
        for item in result.data:
            payload = _string_key_mapping_or_none(item)
            if payload is None:
                raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
            page_id = payload.get("page")
            url = payload.get("url")
            window_id = payload.get("windowId")
            active = payload.get("active")
            window_focused = payload.get("windowFocused")
            if (
                not isinstance(page_id, str)
                or not _is_safe_page_id(page_id)
                or not isinstance(url, str)
                or type(window_id) is not int
                or not isinstance(active, bool)
                or not isinstance(window_focused, bool)
            ):
                raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
            candidates.append(
                BrowserHostTab(
                    page_id=page_id,
                    url=url,
                    window_id=window_id,
                    active=active,
                    window_focused=window_focused,
                )
            )
        return tuple(candidates)

    def open_owned_tab(
        self,
        *,
        host_page: str,
        url: str,
        tab_kind: OpenCliTabKind,
    ) -> OpenCliOwnedTab:
        self._require_control_scope()
        parsed = urlparse(url)
        if (
            not _is_safe_page_id(host_page)
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or tab_kind not in {"search", "detail"}
        ):
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
        tab_token = uuid.uuid4().hex
        session = f"st_tab_{uuid.uuid4().hex}"
        params = self._daemon_session_params(session)
        params.update(
            {
                "op": "new",
                "hostPage": host_page,
                "url": url,
                "active": False,
                "idleTimeout": OPENCLI_OWNED_TAB_IDLE_SECONDS,
            }
        )
        result = self._daemon_command("tabs", params, label="tab.new-in-host")
        payload = _string_key_mapping_or_none(result.data)
        if (
            payload is None
            or payload.get("active") is not False
            or payload.get("placement") != "borrowed-host-window"
            or not isinstance(result.page, str)
            or not _is_safe_page_id(result.page)
        ):
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
        owned_tab = OpenCliOwnedTab(
            tab_token=tab_token,
            session=session,
            page_id=result.page,
            tab_kind=tab_kind,
            idle_deadline_at=result.idle_deadline_at,
        )
        self._owned_tabs[result.page] = owned_tab
        self._daemon_page = result.page
        return owned_tab

    def status(self) -> OpenCliBrowserResult:
        if self._daemon is not None:
            try:
                output = json.dumps(self._daemon.verify_bridge(), ensure_ascii=False, sort_keys=True)
            except OpenCliBrowserError as exc:
                return OpenCliBrowserResult(ok=False, action="status", safe_reason_code=exc.safe_reason_code)
            return OpenCliBrowserResult(ok=True, action="status", private_output=output)
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
        if self._daemon is not None:
            self._daemon.close()
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
        if self._daemon is not None:
            return self._daemon_operation("click", {"target": ref}, label="click")
        return self._run(tuple(self.config.command) + ("browser", self.config.session, "click", ref))

    def find_css(self, selector: str, *, limit: int, text_max: int) -> str:
        if not selector.strip() or "\x00" in selector:
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
        if limit < 1 or limit > 100:
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
        if text_max < 1 or text_max > 10_000:
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
        if self._daemon is not None:
            return self._daemon_operation(
                "find-css",
                {"selector": selector, "limit": limit, "textMax": text_max},
                label="find",
            )
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
        if self._daemon is not None:
            return self._daemon_operation("evaluate", {"code": script}, label="eval")
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
        if self._daemon is not None:
            return self._run_daemon_browser_command(command, args_tuple)
        return self._run(tuple(self.config.command) + ("browser", self.config.session, command, *args_tuple))

    def _run_daemon_browser_command(self, command: str, args: tuple[str, ...]) -> str:
        if command == "state":
            return self._daemon_operation("state", {}, label=command)
        if command == "get":
            return self._daemon_operation("get-url", {}, label=command)
        if command == "find":
            return self._daemon_semantic_find({"text": args[0]}, label=command)
        if command == "click":
            target = args[0] if len(args) == 1 else self._daemon_semantic_ref(args)
            return self._daemon_operation("click", {"target": target}, label=command)
        if command == "fill":
            if len(args) == 2:
                target, text = args
            else:
                target = self._daemon_semantic_ref(args)
                text = args[-1]
            return self._daemon_operation("fill", {"target": target, "text": text}, label=command)
        if command == "scroll":
            return self._daemon_operation(
                "scroll",
                {"direction": args[0], "amount": 500},
                label=command,
            )
        if command == "wait":
            wait_kind, value = args
            wait_value: str | int = int(value) if wait_kind == "time" else value
            return self._daemon_operation(
                "wait",
                {"waitKind": wait_kind, "waitValue": wait_value, "waitTimeout": 10},
                label=command,
            )
        if command == "open":
            page = args[1] if len(args) == 3 else self._daemon_page
            if self._control_scope is not None and page not in self._owned_tabs:
                raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
            url = args[-1]
            params = self._daemon_page_params(page)
            params["url"] = url
            result = self._daemon_command("navigate", params, label=command)
            if result.page:
                if self._control_scope is not None and result.page not in self._owned_tabs:
                    raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
                self._daemon_page = result.page
            return _daemon_output(result)
        if command == "tab":
            return self._run_daemon_tab_command(args)
        raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)

    def _run_daemon_tab_command(self, args: tuple[str, ...]) -> str:
        operation = args[0]
        if operation == "new":
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
        if self._control_scope is not None:
            if operation == "list":
                return self._list_owned_tabs()
            page = args[1]
            owned_tab = self._owned_tabs.get(page)
            if owned_tab is None:
                raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
            if operation == "select":
                self._daemon_page = page
                return json.dumps({"page": page, "selected": True}, sort_keys=True)
            params = self._daemon_page_params(page)
            params["op"] = "close"
            result = self._daemon_command("tabs", params, label="tab.close")
            outcome = _string_key_mapping_or_none(result.data)
            if outcome is not None and outcome.get("outcome") in {"closed", "already_missing"}:
                self._owned_tabs.pop(page, None)
                if page == self._daemon_page:
                    self._daemon_page = None
            return _daemon_output(result)
        params = self._daemon_page_params(None)
        params["op"] = operation
        if operation in {"select", "close"}:
            params["page"] = args[1]
        result = self._daemon_command("tabs", params, label="tab")
        if operation == "select" and result.page:
            self._daemon_page = result.page
        elif operation == "close" and args[1] == self._daemon_page:
            self._daemon_page = None
        return _daemon_output(result)

    def _list_owned_tabs(self) -> str:
        tabs: list[object] = []
        for owned_tab in tuple(self._owned_tabs.values()):
            params = self._daemon_page_params(owned_tab.page_id)
            params["op"] = "list"
            result = self._daemon_command("tabs", params, label="tab.list")
            if not isinstance(result.data, list):
                raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
            tabs.extend(result.data)
        return json.dumps(tabs, ensure_ascii=False, sort_keys=True)

    def _daemon_semantic_ref(self, args: tuple[str, ...]) -> str:
        if args[0] != "--role":
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
        if args[2] == "--nth":
            semantic = {"role": args[1]}
            nth = int(args[3])
        else:
            semantic = {"role": args[1], args[2].removeprefix("--"): args[3]}
            nth = 0
        output = self._daemon_semantic_find(semantic, label="find")
        try:
            payload = json.loads(output)
            entries = payload["entries"]
            ref = entries[nth]["ref"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE) from exc
        if not isinstance(ref, int) or ref < 1:
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
        return str(ref)

    def _daemon_semantic_find(self, semantic: Mapping[str, str], *, label: str) -> str:
        return self._daemon_operation(
            "find-semantic",
            {"semantic": dict(semantic), "limit": 50, "textMax": 120},
            label=label,
        )

    def _daemon_operation(self, operation: str, params: Mapping[str, object], *, label: str) -> str:
        if self._control_scope is not None and self._daemon_page not in self._owned_tabs:
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
        command_params = self._daemon_page_params(self._daemon_page)
        command_params.update({"operation": operation, **params})
        result = self._daemon_command("browser-operation", command_params, label=label)
        if result.page:
            if self._control_scope is not None and result.page not in self._owned_tabs:
                raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
            self._daemon_page = result.page
        return _daemon_output(result)

    def _daemon_page_params(self, page: str | None) -> dict[str, object]:
        owned_tab = self._owned_tabs.get(page or "")
        params = self._daemon_session_params(owned_tab.session if owned_tab is not None else self.config.session)
        if page:
            params["page"] = page
        if owned_tab is not None:
            params["idleTimeout"] = OPENCLI_OWNED_TAB_IDLE_SECONDS
        return params

    def _daemon_session_params(self, session: str) -> dict[str, object]:
        params: dict[str, object] = {
            "session": session,
            "surface": "browser",
            "windowMode": self.config.window_mode,
        }
        scope = self._control_scope
        if scope is not None:
            params.update({"controlKey": scope.control_key, "fenceToken": scope.fence_token})
        return params

    def _require_control_scope(self) -> BrowserControlScope:
        scope = self._control_scope
        if scope is None:
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
        return scope

    def _daemon_command(
        self,
        action: OpenCliDaemonAction,
        params: Mapping[str, object],
        *,
        label: str,
    ) -> OpenCliDaemonResult:
        daemon = self._daemon
        if daemon is None:
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
        argv = tuple(self.config.command) + ("browser", self.config.session, label)
        started = time.perf_counter()
        ok = False
        safe_reason_code: str | None = None
        try:
            result = daemon.command(action, params, timeout_seconds=self.config.timeout_seconds)
            ok = True
            return result
        except OpenCliBrowserError as exc:
            safe_reason_code = exc.safe_reason_code
            raise
        finally:
            self._record_timing(
                argv=argv,
                duration_ms=(time.perf_counter() - started) * 1000,
                ok=ok,
                safe_reason_code=safe_reason_code,
            )

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


def _daemon_output(result: OpenCliDaemonResult) -> str:
    if isinstance(result.data, str):
        return result.data
    return json.dumps(result.data, ensure_ascii=False, sort_keys=True)


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
