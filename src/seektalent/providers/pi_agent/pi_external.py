from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
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


_OPENCLI_SAFE_TOOL_REASON_CODES = frozenset(
    {
        "liepin_opencli_backend_disabled",
        "liepin_opencli_command_missing",
        "liepin_opencli_extension_disconnected",
        "liepin_opencli_status_unavailable",
        "liepin_opencli_forbidden_command",
        "liepin_opencli_forbidden_text",
        "liepin_opencli_host_blocked",
        "liepin_opencli_start_url_blocked",
        "liepin_opencli_window_policy_blocked",
        "liepin_opencli_budget_exhausted",
        "liepin_opencli_timeout",
        "liepin_opencli_login_required",
        "liepin_opencli_identity_intercept",
        "liepin_opencli_risk_page",
        "liepin_opencli_unknown_modal",
        "liepin_opencli_source_policy_missing",
        "liepin_opencli_malformed_state",
    }
)


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
    safe_reason_code: str | None = None
    safe_message: str = ""
    observed_tool_names: tuple[str, ...] = ()
    events: tuple[dict[str, object], ...] = ()


def build_pi_rpc_argv(
    command: str,
    *,
    skill_path: Path,
    required_extension_markers: tuple[str, ...] = (),
    extension_root: Path | None = None,
) -> tuple[str, ...]:
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
    extensions = _extension_values(argv)
    for marker in required_extension_markers:
        extension = _extension_matching(extensions, marker)
        if extension is None:
            raise ValueError("liepin_pi_command must include required extension")
        if (
            extension_root is not None
            and _requires_local_extension_file(marker)
            and not _extension_file_exists(extension, root=extension_root)
        ):
            raise ValueError("liepin_pi_command required extension file does not exist")
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
        browser_backend_description: str | None = None,
        env: Mapping[str, str] | None = None,
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
        self._browser_backend_description = browser_backend_description
        self._timeout_seconds = timeout_seconds
        self._artifact_root = artifact_root
        self._artifact_root.mkdir(parents=True, exist_ok=True)
        self._env = dict(env or {})
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
        result = self._run_json_task_result_once(prompt, strict_retry=False)
        if result.ok or result.error_code != PiExternalAgentErrorCode.MALFORMED_OUTPUT:
            return result
        retry_result = self._run_json_task_result_once(prompt, strict_retry=True)
        if not retry_result.ok and retry_result.safe_reason_code is None and result.safe_reason_code is not None:
            return replace(retry_result, safe_reason_code=result.safe_reason_code)
        return retry_result

    def _run_json_task_result_once(self, prompt: str, *, strict_retry: bool) -> PiExternalTaskResult:
        task_name = _task_name_from_prompt(prompt)
        command = PiRpcCommand(
            argv=self._command,
            timeout_seconds=self._timeout_seconds,
            artifact_root=self._artifact_root,
            env={**self._env, "SEEKTALENT_PI_ARTIFACT_ROOT": str(self._artifact_root)},
        )
        rpc_result = self._transport.request(command, prompt=self._build_prompt(prompt, strict_retry=strict_retry))
        observed_tool_names = _observed_tool_names(rpc_result.events)
        safe_reason_code = _safe_tool_reason_code(rpc_result.events)
        safe_events = _safe_rpc_events(rpc_result.events)
        if task_name == "liepin.search_cards":
            tool_envelope = _strict_cards_envelope_from_tool_events(rpc_result.events)
            if tool_envelope is not None:
                return PiExternalTaskResult(
                    ok=True,
                    envelope=tool_envelope,
                    safe_reason_code=safe_reason_code,
                    observed_tool_names=observed_tool_names,
                    events=safe_events,
                )
        if rpc_result.status != PiRpcTaskStatus.SUCCEEDED:
            return PiExternalTaskResult(
                ok=False,
                error_code=_external_code_for_rpc_status(rpc_result.status),
                safe_reason_code=safe_reason_code,
                safe_message=_safe_external_message(rpc_result.safe_message),
                observed_tool_names=observed_tool_names,
                events=safe_events,
            )
        try:
            envelope = parse_strict_json_object(rpc_result.final_text)
        except ValueError:
            tool_envelope = _strict_cards_envelope_from_tool_events(rpc_result.events)
            if tool_envelope is not None:
                return PiExternalTaskResult(
                    ok=True,
                    envelope=tool_envelope,
                    safe_reason_code=safe_reason_code,
                    observed_tool_names=observed_tool_names,
                    events=safe_events,
                )
            return PiExternalTaskResult(
                ok=False,
                error_code=PiExternalAgentErrorCode.MALFORMED_OUTPUT,
                safe_reason_code=safe_reason_code,
                safe_message="pi output did not contain exactly one valid JSON envelope",
                observed_tool_names=observed_tool_names,
                events=safe_events,
            )
        return PiExternalTaskResult(
            ok=True,
            envelope=envelope,
            safe_reason_code=safe_reason_code,
            observed_tool_names=observed_tool_names,
            events=safe_events,
        )

    def _build_prompt(self, prompt: str, *, strict_retry: bool = False) -> str:
        backend_line = (
            f"Required browser backend inside Pi: {self._browser_backend_description}\n"
            if self._browser_backend_description
            else f"Required DokoBot tool inside Pi: {self._dokobot_tool_name}\n"
        )
        task_contract = _task_contract_for_prompt(prompt)
        retry_line = (
            "STRICT JSON RETRY: your previous final answer was rejected because it was not exactly one raw JSON "
            "object. Return the final answer as raw JSON only. Do not include prose, markdown fences, code blocks, "
            "or explanations.\n"
            if strict_retry
            else ""
        )
        return (
            f"Required loaded skill path: {self._skill_path}\n"
            f"{backend_line}"
            f"Required artifact root: {self._artifact_root}\n"
            "Write every artifact://protected/... and artifact://public-summary/... ref to that root before returning final JSON.\n"
            "Final answer must be exactly one raw JSON object. No prose, markdown fences, code blocks, or explanations.\n"
            f"{task_contract}"
            f"{retry_line}"
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


def _extension_values(argv: Sequence[str]) -> tuple[str, ...]:
    values: list[str] = []
    for index, part in enumerate(argv):
        if part == "--extension" and index + 1 < len(argv):
            values.append(argv[index + 1])
        elif part.startswith("--extension="):
            values.append(part.split("=", 1)[1])
    return tuple(values)


def _extension_matching(extensions: Sequence[str], marker: str) -> str | None:
    for extension in extensions:
        if marker in extension:
            return extension
    return None


def _extension_file_exists(extension: str, *, root: Path) -> bool:
    path = Path(extension)
    if not path.is_absolute():
        path = root / path
    return path.is_file()


def _requires_local_extension_file(marker: str) -> bool:
    return marker.startswith("pi_extensions/")


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


def _task_name_from_prompt(prompt: str) -> str | None:
    try:
        task = json.loads(prompt)
    except json.JSONDecodeError:
        return None
    if not isinstance(task, dict):
        return None
    task_name = task.get("task")
    return task_name if isinstance(task_name, str) else None


def _task_contract_for_prompt(prompt: str) -> str:
    task_name = _task_name_from_prompt(prompt)
    if task_name == "liepin.probe_capabilities":
        return (
            "For task liepin.probe_capabilities, call the safe browser status tool and capability manifest tool only. "
            "Do not click, type, scroll, navigate, or open a page for this probe. "
            "Return exactly this schema: "
            '{"schema_version":"seektalent.pi_capability_probe.v1","status":"ready|blocked|failed",'
            '"pi_version":null,"read_tool_name":"<capability tool name or read tool name>",'
            '"action_tool_names":["<declared safe browser tool names>"],'
            '"proof_kind":"trusted_manifest_and_observed_tool_event|none",'
            '"capability_manifest_ref":"artifact://protected/pi-capability/manifest.json",'
            '"tool_evidence_ref":"artifact://protected/pi-capability/tool-events.json",'
            '"allowed_hosts":["www.liepin.com"],"stop_reason":null}. '
            "If the status or capability tool is unavailable, use status blocked, proof_kind none, empty tool lists, "
            "empty allowed_hosts, and an allowlisted stop_reason such as liepin_opencli_status_unavailable.\n"
        )
    if task_name == "liepin.probe_session":
        return (
            "For task liepin.probe_session, return exactly this schema: "
            '{"schema_version":"seektalent.pi_liepin_session_probe.v1","status":"ready|login_required|revoked|missing|failed",'
            '"connection_id":"<input connection_id>","provider_account_material_ref":null,'
            '"page_origin":null,"stop_reason":null}. '
            "Only status ready may include provider_account_material_ref. Never include cookies, tokens, localStorage, "
            "sessionStorage, raw account identifiers, phone numbers, or email addresses.\n"
        )
    if task_name == "liepin.search_cards":
        return (
            "For task liepin.search_cards, Call seektalent_opencli_search_liepin_cards exactly once with sourceRunId, "
            "query, maxPages, and maxCards from the input task, then return that tool result exactly as the final raw "
            "JSON object. Do not call read, bash, provider APIs, cookies, storage, network, eval, download, upload, "
            "contact, chat, payment, detail pages, or low-level browser tools for this task. The tool result already "
            "matches seektalent.pi_liepin_cards.v1 exactly.\n"
        )
    return ""


def _strict_cards_envelope_from_tool_events(events: tuple[dict[str, object], ...]) -> dict[str, object] | None:
    for event in reversed(events[:100]):
        tool_name = event.get("toolName") or event.get("tool_name")
        if tool_name != "seektalent_opencli_search_liepin_cards":
            continue
        envelope = _cards_envelope_from_value(event)
        if envelope is not None:
            return envelope
    return None


def _cards_envelope_from_value(value: object, *, depth: int = 0) -> dict[str, object] | None:
    if depth > 8:
        return None
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, object], value)
        schema = mapping.get("schema_version")
        if schema == "seektalent.pi_liepin_cards.v1":
            return dict(mapping)
        for item in mapping.values():
            envelope = _cards_envelope_from_value(item, depth=depth + 1)
            if envelope is not None:
                return envelope
        return None
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for item in value:
            envelope = _cards_envelope_from_value(item, depth=depth + 1)
            if envelope is not None:
                return envelope
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped[0] not in "[{" or len(stripped) > 200000:
            return None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        return _cards_envelope_from_value(parsed, depth=depth + 1)
    return None


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


def _safe_tool_reason_code(events: tuple[dict[str, object], ...]) -> str | None:
    for event in reversed(events[:100]):
        tool_name = event.get("toolName") or event.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name.startswith("seektalent_opencli_"):
            continue
        reason = _safe_reason_code_from_value(event)
        if reason is not None:
            return reason
    return None


def _safe_reason_code_from_value(value: object, *, depth: int = 0) -> str | None:
    if depth > 6:
        return None
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, object], value)
        for key in ("safeReasonCode", "safe_reason_code"):
            reason = mapping.get(key)
            if isinstance(reason, str) and reason in _OPENCLI_SAFE_TOOL_REASON_CODES:
                return reason
        for item in mapping.values():
            reason = _safe_reason_code_from_value(item, depth=depth + 1)
            if reason is not None:
                return reason
        return None
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for item in value:
            reason = _safe_reason_code_from_value(item, depth=depth + 1)
            if reason is not None:
                return reason
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped[0] not in "[{" or len(stripped) > 20000:
            return None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        return _safe_reason_code_from_value(parsed, depth=depth + 1)
    return None


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
            if not isinstance(message, dict):
                continue
            typed_message = cast(dict[str, object], message)
            if typed_message.get("role") != "assistant":
                continue
            content = typed_message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    typed_block = cast(dict[str, object], block)
                    text = typed_block.get("text")
                    if typed_block.get("type") == "text" and isinstance(text, str):
                        parts.append(text)
                if parts:
                    return "".join(parts)
    text = event.get("text")
    return text if isinstance(text, str) else ""
