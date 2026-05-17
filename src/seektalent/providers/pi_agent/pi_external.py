from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast


class PiExternalAgentErrorCode(StrEnum):
    PI_UNAVAILABLE = "pi_unavailable"
    PROMPT_REJECTED = "prompt_rejected"
    TIMEOUT = "timeout"
    UI_REQUEST_DENIED = "ui_request_denied"
    PROCESS_FAILED = "process_failed"
    MISSING_AGENT_END = "missing_agent_end"
    MALFORMED_OUTPUT = "malformed_output"


class PiRpcTaskStatus(StrEnum):
    SUCCEEDED = "succeeded"
    UNAVAILABLE = "unavailable"
    PROMPT_REJECTED = "prompt_rejected"
    TIMEOUT = "timeout"
    UI_REQUESTED = "ui_requested"
    FAILED = "failed"
    MISSING_AGENT_END = "missing_agent_end"


@dataclass(frozen=True, kw_only=True)
class PiRpcCommand:
    argv: tuple[str, ...]
    timeout_seconds: int
    artifact_root: Path
    cwd: Path | None = None
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class PiRpcTaskResult:
    status: PiRpcTaskStatus
    final_text: str = ""
    safe_message: str = ""
    private_diagnostic: str = ""
    events: tuple[dict[str, object], ...] = ()


class PiRpcTransport(Protocol):
    def request(self, command: PiRpcCommand, *, prompt: str) -> PiRpcTaskResult: ...


@dataclass(frozen=True, kw_only=True)
class PiExternalTaskResult:
    ok: bool
    envelope: dict[str, object] | None = None
    error_code: PiExternalAgentErrorCode | None = None
    safe_message: str = ""
    observed_tool_names: tuple[str, ...] = ()
    events: tuple[dict[str, object], ...] = ()


def build_pi_rpc_argv(command: str, *, skill_path: Path) -> tuple[str, ...]:
    if not skill_path.is_file():
        raise ValueError("liepin_pi_skill_path must point to a readable file")
    argv = tuple(shlex.split(command))
    if not argv:
        raise ValueError("liepin_pi_command is required")
    if "--mode" not in argv or _arg_value(argv, "--mode") != "rpc":
        raise ValueError("liepin_pi_command must include --mode rpc")
    if "--no-session" not in argv:
        raise ValueError("liepin_pi_command must include --no-session")
    if "--skill" in argv:
        raise ValueError("liepin_pi_command must not inline --skill; use liepin_pi_skill_path")
    result = [part for part in argv if part != "--no-skills"]
    result.extend(["--no-skills", "--skill", str(skill_path)])
    return tuple(result)


def parse_strict_json_object(text: str) -> dict[str, object]:
    try:
        loaded = json.loads(text.strip())
    except json.JSONDecodeError as exc:
        raise ValueError("pi final output must be exactly one JSON object") from exc
    if not isinstance(loaded, dict):
        raise ValueError("pi final output must be a JSON object")
    return loaded


class SubprocessPiRpcTransport:
    def __init__(self, *, process_factory=subprocess.Popen) -> None:
        self._process_factory = process_factory

    def request(self, command: PiRpcCommand, *, prompt: str) -> PiRpcTaskResult:
        deadline = time.monotonic() + command.timeout_seconds
        stdout_lines: queue.Queue[str | None] = queue.Queue()
        stderr_chunks: list[str] = []
        try:
            process = self._process_factory(
                command.argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=command.cwd,
                env={**os.environ, **command.env} if command.env else None,
                bufsize=1,
            )
        except FileNotFoundError:
            return PiRpcTaskResult(status=PiRpcTaskStatus.UNAVAILABLE, safe_message="pi command not found")
        except PermissionError:
            return PiRpcTaskResult(status=PiRpcTaskStatus.UNAVAILABLE, safe_message="pi command is not executable")
        except OSError:
            return PiRpcTaskResult(status=PiRpcTaskStatus.FAILED, safe_message="pi process could not start")

        if process.stdin is None or process.stdout is None or process.stderr is None:
            _stop_process(process)
            return PiRpcTaskResult(status=PiRpcTaskStatus.FAILED, safe_message="pi rpc pipes unavailable")

        stdout_thread = threading.Thread(target=_drain_stdout, args=(process.stdout, stdout_lines), daemon=True)
        stderr_thread = threading.Thread(target=_drain_stderr, args=(process.stderr, stderr_chunks), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        request_id = "seektalent-1"
        command_line = json.dumps({"id": request_id, "type": "prompt", "message": prompt}) + "\n"
        try:
            process.stdin.write(command_line)
            process.stdin.flush()
        except OSError:
            _stop_process(process)
            return PiRpcTaskResult(
                status=PiRpcTaskStatus.FAILED,
                safe_message="pi rpc stdin closed before prompt was accepted",
                private_diagnostic=_safe_join(stderr_chunks),
            )

        prompt_accepted = False
        events: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                line = stdout_lines.get(timeout=min(0.1, remaining))
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            if line is None:
                break
            event = _json_object_from_line(line)
            if event is None:
                continue
            events.append(event)
            if event.get("type") == "response" and event.get("command") == "prompt":
                if event.get("success") is not True:
                    _stop_process(process)
                    return PiRpcTaskResult(
                        status=PiRpcTaskStatus.PROMPT_REJECTED,
                        safe_message="pi rejected prompt command",
                        private_diagnostic=_safe_join(stderr_chunks),
                        events=tuple(events),
                    )
                prompt_accepted = True
                continue
            if event.get("type") == "extension_ui_request":
                _stop_process(process)
                return PiRpcTaskResult(
                    status=PiRpcTaskStatus.UI_REQUESTED,
                    safe_message="pi requested user interaction during provider task",
                    private_diagnostic=_safe_join(stderr_chunks),
                    events=tuple(events),
                )
            if event.get("type") == "agent_end":
                if not prompt_accepted:
                    _stop_process(process)
                    return PiRpcTaskResult(
                        status=PiRpcTaskStatus.MISSING_AGENT_END,
                        safe_message="pi rpc ended before prompt acknowledgement",
                        private_diagnostic=_safe_join(stderr_chunks),
                        events=tuple(events),
                    )
                final_text = _assistant_text_from_agent_end(event)
                _stop_process(process)
                return PiRpcTaskResult(status=PiRpcTaskStatus.SUCCEEDED, final_text=final_text, events=tuple(events))

        if process.poll() is None:
            _stop_process(process)
            return PiRpcTaskResult(status=PiRpcTaskStatus.TIMEOUT, safe_message="pi rpc timed out", events=tuple(events))
        if process.returncode not in {0, None}:
            return PiRpcTaskResult(
                status=PiRpcTaskStatus.FAILED,
                safe_message=f"pi rpc exited with code {process.returncode}",
                private_diagnostic=_safe_join(stderr_chunks),
                events=tuple(events),
            )
        if not prompt_accepted:
            return PiRpcTaskResult(status=PiRpcTaskStatus.TIMEOUT, safe_message="pi prompt was not acknowledged")
        return PiRpcTaskResult(status=PiRpcTaskStatus.MISSING_AGENT_END, safe_message="pi rpc ended without agent_end")


class PiRpcAgentClient:
    def __init__(
        self,
        *,
        command: tuple[str, ...],
        skill_path: Path,
        dokobot_tool_name: str,
        timeout_seconds: int,
        artifact_root: Path,
        transport: PiRpcTransport | None = None,
    ) -> None:
        if "--mode" not in command or _arg_value(command, "--mode") != "rpc":
            raise ValueError("PiRpcAgentClient requires --mode rpc")
        if "--no-session" not in command:
            raise ValueError("PiRpcAgentClient requires --no-session")
        if "--skill" not in command or str(skill_path) not in command:
            raise ValueError("PiRpcAgentClient requires the configured Liepin skill")
        self._command = command
        self._skill_path = skill_path
        self._dokobot_tool_name = dokobot_tool_name
        self._timeout_seconds = timeout_seconds
        self._artifact_root = artifact_root
        self._artifact_root.mkdir(parents=True, exist_ok=True)
        self._transport = transport or SubprocessPiRpcTransport()

    @property
    def transport_for_test(self) -> PiRpcTransport:
        return self._transport

    def run_json_task(self, prompt: str) -> dict[str, object]:
        result = self.run_json_task_result(prompt)
        if not result.ok or result.envelope is None:
            raise ValueError(result.error_code or PiExternalAgentErrorCode.MALFORMED_OUTPUT)
        return result.envelope

    def run_json_task_result(self, prompt: str) -> PiExternalTaskResult:
        command = PiRpcCommand(
            argv=self._command,
            timeout_seconds=self._timeout_seconds,
            artifact_root=self._artifact_root,
            env={"SEEKTALENT_PI_ARTIFACT_ROOT": str(self._artifact_root)},
        )
        rpc_result = self._transport.request(command, prompt=self._build_prompt(prompt))
        observed_tool_names = _observed_tool_names(rpc_result.events)
        safe_events = _safe_rpc_events(rpc_result.events)
        if rpc_result.status != PiRpcTaskStatus.SUCCEEDED:
            return PiExternalTaskResult(
                ok=False,
                error_code=_external_code_for_rpc_status(rpc_result.status),
                safe_message=_safe_external_message(rpc_result.safe_message),
                observed_tool_names=observed_tool_names,
                events=safe_events,
            )
        try:
            envelope = parse_strict_json_object(rpc_result.final_text)
        except ValueError:
            return PiExternalTaskResult(
                ok=False,
                error_code=PiExternalAgentErrorCode.MALFORMED_OUTPUT,
                safe_message="pi output did not contain exactly one valid JSON envelope",
                observed_tool_names=observed_tool_names,
                events=safe_events,
            )
        return PiExternalTaskResult(ok=True, envelope=envelope, observed_tool_names=observed_tool_names, events=safe_events)

    def _build_prompt(self, prompt: str) -> str:
        return (
            f"Required loaded skill path: {self._skill_path}\n"
            f"Required DokoBot tool inside Pi: {self._dokobot_tool_name}\n"
            f"Required artifact root: {self._artifact_root}\n"
            "Write every artifact://protected/... and artifact://public-summary/... ref to that root before returning final JSON.\n"
            f"{prompt}"
        )


def _arg_value(argv: tuple[str, ...], flag: str) -> str | None:
    try:
        index = argv.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(argv):
        return None
    return argv[index + 1]


def _external_code_for_rpc_status(status: PiRpcTaskStatus) -> PiExternalAgentErrorCode:
    return {
        PiRpcTaskStatus.UNAVAILABLE: PiExternalAgentErrorCode.PI_UNAVAILABLE,
        PiRpcTaskStatus.PROMPT_REJECTED: PiExternalAgentErrorCode.PROMPT_REJECTED,
        PiRpcTaskStatus.TIMEOUT: PiExternalAgentErrorCode.TIMEOUT,
        PiRpcTaskStatus.UI_REQUESTED: PiExternalAgentErrorCode.UI_REQUEST_DENIED,
        PiRpcTaskStatus.FAILED: PiExternalAgentErrorCode.PROCESS_FAILED,
        PiRpcTaskStatus.MISSING_AGENT_END: PiExternalAgentErrorCode.MISSING_AGENT_END,
        PiRpcTaskStatus.SUCCEEDED: PiExternalAgentErrorCode.MALFORMED_OUTPUT,
    }[status]


def _safe_external_message(message: str) -> str:
    lowered = message.lower()
    if any(marker in lowered for marker in ("bearer ", "cookie", "session=", "token", "secret")):
        return "pi rpc failed"
    return message or "pi rpc failed"


def _observed_tool_names(events: tuple[dict[str, object], ...]) -> tuple[str, ...]:
    names: list[str] = []
    for event in events:
        event_type = str(event.get("type") or "")
        if not event_type.startswith("tool_execution_"):
            continue
        tool_name = event.get("toolName") or event.get("tool_name")
        if isinstance(tool_name, str) and tool_name and tool_name not in names:
            names.append(tool_name)
    return tuple(names)


def _safe_rpc_events(events: tuple[dict[str, object], ...]) -> tuple[dict[str, object], ...]:
    safe: list[dict[str, object]] = []
    for event in events[:100]:
        item: dict[str, object] = {}
        event_type = event.get("type")
        tool_name = event.get("toolName") or event.get("tool_name")
        if isinstance(event_type, str):
            item["type"] = event_type
        if isinstance(tool_name, str):
            item["tool_name"] = tool_name
        if item:
            safe.append(item)
    return tuple(safe)


def _drain_stdout(stream, output: queue.Queue[str | None]) -> None:
    try:
        for line in stream:
            output.put(line)
    finally:
        output.put(None)


def _drain_stderr(stream, chunks: list[str]) -> None:
    for line in stream:
        if len(chunks) < 50:
            chunks.append(line)


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)


def _safe_join(chunks: list[str]) -> str:
    return "".join(chunks)[-4000:]


def _json_object_from_line(line: str) -> dict[str, object] | None:
    try:
        event = json.loads(line.rstrip("\r\n"))
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    return cast(dict[str, object], event)


def _assistant_text_from_agent_end(event: dict[str, object]) -> str:
    messages = event.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [
                    block.get("text")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
                ]
                if parts:
                    return "".join(parts)
    text = event.get("text")
    return text if isinstance(text, str) else ""
