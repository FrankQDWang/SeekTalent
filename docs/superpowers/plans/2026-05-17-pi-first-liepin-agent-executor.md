# Pi-First Liepin Agent Executor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Replace the incomplete repo-local `dokobot_action` Liepin execution route with a Pi-first external agent executor that uses DokoBot only inside Pi, validates strict JSON output, preserves Runtime budgets/card policy/evidence, and fails closed without fallback.

**Architecture:** Runtime remains the source-lane, budget, detail recommendation, merge, scoring, and finalization owner. Pi is a bounded one-task provider executor reached through a hardened RPC/JSONL boundary. SeekTalent validates capability proof, strict JSON, public payload safety, Runtime-owned hashes, artifact refs, and card-mode traces before mapping cards into the existing Runtime lane contract.

**Tech Stack:** Python 3.12, standard library `subprocess`, `threading`, `queue`, `time`, `json`, `hmac`, `hashlib`, Pydantic v2, pytest, ruff, existing `seektalent.providers.liepin`, `seektalent.providers.pi_agent`, and `seektalent.runtime.source_lanes`.

---

## Spec Link

This plan implements:

`docs/superpowers/specs/2026-05-17-pi-first-liepin-agent-executor-design.md`

It supersedes the executor premise in:

`docs/superpowers/specs/2026-05-16-liepin-live-browser-action-card-policy-design.md`

Keep from the 05-16 work:

- card policy and safe card summary direction
- Runtime source-lane budgets
- detail recommendation fields
- protected artifact refs
- safe reason-code mapping
- partial card preservation
- typed Pi agent boundary contracts that remain true
- trusted DokoBot manifest ideas when they are used only as Pi-internal capability proof

Replace from the 05-16 work:

- `liepin_worker_mode=dokobot_action` as a live path
- repo-owned `DokoBotActionSurface`
- repo-owned `DokoBotActionTransportSession`
- `PiBackendMode.DOKOBOT_ACTION` dispatch as the live executor
- automatic backend compatibility dispatch

## File Map

Create:

- `src/seektalent/providers/pi_agent/pi_external.py`
  - Pi RPC/JSONL one-task process boundary
  - command validation and skill loading
  - strict final JSON parsing
  - prompt response, timeout, UI-request, process, and malformed-output result mapping

- `src/seektalent/providers/pi_agent/payload_firewall.py`
  - SafePayloadFirewall for external executor output
  - artifact-ref allowlist validation
  - forbidden free-text scanner for cookies, tokens, contacts, raw HTML, storage, and exception-like text

- `src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md`
  - repo-owned Pi instruction asset for Liepin card search
  - DokoBot use described as Pi-internal tooling

- `src/seektalent/providers/liepin/pi_executor.py`
  - strict Pi Liepin card/session/capability models
  - Runtime-owned HMAC mapping seam
  - card-mode action-trace validation
  - mapping into `LiepinPiCardSearchResult` / `LiepinCardSearchResponse`

- `tests/test_pi_external_agent.py`
  - Pi command, RPC protocol, strict JSON, timeout, UI-request, stderr redaction, and skill-loading tests

- `tests/test_pi_payload_firewall.py`
  - forbidden text, artifact-ref, and public payload safety tests

- `tests/test_liepin_pi_executor.py`
  - valid cards, business invariant rejection, Runtime-owned hash mapping, partial cards, blocked DokoBot/login/risk, forbidden detail data, and card-mode trace validation

Modify:

- `src/seektalent/config.py`
  - add `pi_agent` worker mode
  - add minimal Pi settings
  - reject stale `dokobot_action` live mode
  - validate RPC mode, no-session, skill path, and DokoBot tool name

- `.env.example`
  - document `pi_agent`

- `src/seektalent/default.env`
  - keep default `disabled`
  - document `pi_agent` keys as commented examples if this file uses comments

- `src/seektalent/providers/pi_agent/contracts.py`
  - reuse or tighten `PiArtifactRef`
  - remove live-path backend modes that encode the old executor premise if no remaining production code uses them

- `src/seektalent/providers/pi_agent/capabilities.py`
  - preserve trusted manifest semantics as Pi capability proof
  - remove any product factory path that makes SeekTalent call DokoBot actions directly

- `src/seektalent/providers/liepin/pi_worker_client.py`
  - depend on `PiLiepinExecutor`
  - keep async APIs from blocking the event loop with `asyncio.to_thread()` when the Pi adapter is synchronous

- `src/seektalent/providers/liepin/client.py`
  - build `PiLiepinExecutor` for `liepin_worker_mode=pi_agent`
  - remove construction of `DokoBotActionTransportSession(action_surface=None)`
  - do not fallback to other worker modes

- `src/seektalent/providers/liepin/adapter.py`
  - treat `pi_agent` as a live mode requiring compliance/session/provider-account safety

- `src/seektalent/providers/liepin/runtime_lane.py`
  - map Pi stop reasons into Runtime safe reason codes
  - keep card budgets and detail recommendation policy unchanged

- `src/seektalent/runtime/source_lanes.py`
  - report `pi_agent` backend posture instead of `dokobot_action` or `legacy_worker_compat`

- `src/seektalent/cli.py`
  - let Liepin smoke use `pi_agent` when explicitly configured
  - do not coerce `pi_agent` to `managed_local`

- `src/seektalent/providers/registry.py`
  - treat `pi_agent` as a live Liepin worker mode requiring `LiepinStore`

- `src/seektalent/providers/liepin/worker_contracts.py`
  - keep or tighten `safe_card_summary`
  - reject detail-only fields in card mode if the model already has a validation seam

- `src/seektalent/providers/liepin/mapper.py`
  - keep allowlisted safe card summary mapping

- `tests/test_liepin_pi_worker_client.py`
  - switch from old runner dispatch to `PiLiepinExecutor`

- `tests/test_liepin_worker_client.py`
  - update factory expectations for `pi_agent`

- `tests/test_provider_registry.py`
  - verify `pi_agent` gets live store/safety handling

- `tests/test_liepin_provider_adapter.py`
  - verify live safety applies to `pi_agent`

- `tests/test_liepin_runtime_source_lane.py`
  - verify Pi blocked/partial reason-code mapping
  - verify source-lane posture reports `backend_mode="pi_agent"`

- `tests/test_liepin_cli.py`
  - verify Liepin smoke settings preserve explicit `pi_agent`

- `tests/test_liepin_config.py`
  - add Liepin worker-mode settings coverage

Delete after imports are migrated to the Pi-first path:

- `src/seektalent/providers/liepin/pi_runner.py`
- `src/seektalent/providers/liepin/dokobot_actions.py`
- `src/seektalent/providers/pi_agent/dokobot_action_transport.py`
- `tests/test_liepin_pi_runner.py`
- `tests/test_liepin_dokobot_actions.py`

## Execution Notes

- Do not install Pi or DokoBot from this plan.
- Do not use Codex-side browser tools as product execution.
- Do not introduce Claude Code, Skyvern, browser-use, or A2A.
- Do not change final Top 10 scoring rules except to preserve Pi-backed Liepin evidence through existing Runtime merge.
- Use fake process runners and fake transports in unit tests so the test suite does not require a local Pi install.
- Product live execution must fail closed if Pi is absent.
- Keep code small and literal. Add abstractions only at the process boundary, safe payload boundary, and provider protocol boundary.
- One Pi RPC process handles one provider task. Do not multiplex prompts through a shared Pi process in this slice.

## Tasks

### Task 1: Harden Pi RPC Boundary

**Files:**
- Create: `tests/test_pi_external_agent.py`
- Create: `src/seektalent/providers/pi_agent/pi_external.py`

- [x] **Step 1: Add failing tests for command validation, skill loading, strict JSON, and safe error mapping**

Create `tests/test_pi_external_agent.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from seektalent.providers.pi_agent.pi_external import (
    PiExternalAgentErrorCode,
    PiRpcAgentClient,
    PiRpcCommand,
    PiRpcTaskResult,
    PiRpcTaskStatus,
    SubprocessPiRpcTransport,
    build_pi_rpc_argv,
)


class FakeRpcTransport:
    def __init__(self, result: PiRpcTaskResult) -> None:
        self.result = result
        self.commands: list[PiRpcCommand] = []
        self.prompts: list[str] = []

    def request(self, command: PiRpcCommand, *, prompt: str) -> PiRpcTaskResult:
        self.commands.append(command)
        self.prompts.append(prompt)
        return self.result


def test_build_pi_rpc_argv_requires_rpc_no_session_and_loads_skill(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("---\nname: liepin-search-cards\n---\n", encoding="utf-8")

    argv = build_pi_rpc_argv("pi --mode rpc --no-session", skill_path=skill_path)

    assert argv == ("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", str(skill_path))


@pytest.mark.parametrize(
    "command",
    [
        "pi",
        "pi --mode json --no-session",
        "pi --mode rpc",
    ],
)
def test_build_pi_rpc_argv_rejects_non_rpc_or_sessionful_commands(tmp_path: Path, command: str) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("skill", encoding="utf-8")

    with pytest.raises(ValueError):
        build_pi_rpc_argv(command, skill_path=skill_path)


def test_pi_rpc_client_accepts_exact_json_object_only(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("skill", encoding="utf-8")
    artifact_root = tmp_path / "artifacts" / "pi-agent"
    transport = FakeRpcTransport(
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text='{"schema_version":"seektalent.pi_liepin_cards.v1","status":"succeeded","cards":[]}',
        )
    )
    client = PiRpcAgentClient(
        command=("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", str(skill_path)),
        skill_path=skill_path,
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=artifact_root,
        transport=transport,
    )

    envelope = client.run_json_task("collect cards")

    assert envelope["schema_version"] == "seektalent.pi_liepin_cards.v1"
    assert transport.commands[0].argv[-2:] == ("--skill", str(skill_path))
    assert transport.commands[0].artifact_root == artifact_root
    assert transport.commands[0].env["SEEKTALENT_PI_ARTIFACT_ROOT"] == str(artifact_root)
    assert "Required artifact root:" in transport.prompts[0]


def test_pi_rpc_client_rejects_notes_before_json(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("skill", encoding="utf-8")
    artifact_root = tmp_path / "artifacts" / "pi-agent"
    client = PiRpcAgentClient(
        command=("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", str(skill_path)),
        skill_path=skill_path,
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=artifact_root,
        transport=FakeRpcTransport(
            PiRpcTaskResult(
                status=PiRpcTaskStatus.SUCCEEDED,
                final_text='notes\n{"schema_version":"seektalent.pi_liepin_cards.v1","status":"succeeded"}',
            )
        ),
    )

    result = client.run_json_task_result("collect cards")

    assert result.ok is False
    assert result.error_code == PiExternalAgentErrorCode.MALFORMED_OUTPUT


def test_pi_rpc_client_exposes_only_observed_tool_names_from_rpc_events(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("skill", encoding="utf-8")
    artifact_root = tmp_path / "artifacts" / "pi-agent"
    client = PiRpcAgentClient(
        command=("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", str(skill_path)),
        skill_path=skill_path,
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=artifact_root,
        transport=FakeRpcTransport(
            PiRpcTaskResult(
                status=PiRpcTaskStatus.SUCCEEDED,
                final_text='{"ok":true}',
                events=(
                    {"type": "tool_execution_start", "toolName": "dokobot.read", "raw": "secret-token"},
                    {"type": "tool_execution_start", "tool_name": "dokobot.click", "input": {"cookie": "session"}},
                ),
            )
        ),
    )

    result = client.run_json_task_result("probe tools")

    assert result.observed_tool_names == ("dokobot.read", "dokobot.click")
    assert result.events == (
        {"type": "tool_execution_start", "tool_name": "dokobot.read"},
        {"type": "tool_execution_start", "tool_name": "dokobot.click"},
    )


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        (PiRpcTaskStatus.UNAVAILABLE, PiExternalAgentErrorCode.PI_UNAVAILABLE),
        (PiRpcTaskStatus.PROMPT_REJECTED, PiExternalAgentErrorCode.PROMPT_REJECTED),
        (PiRpcTaskStatus.TIMEOUT, PiExternalAgentErrorCode.TIMEOUT),
        (PiRpcTaskStatus.UI_REQUESTED, PiExternalAgentErrorCode.UI_REQUEST_DENIED),
        (PiRpcTaskStatus.FAILED, PiExternalAgentErrorCode.PROCESS_FAILED),
    ],
)
def test_pi_rpc_client_maps_external_failures_without_leaking_private_diagnostics(
    tmp_path: Path,
    status: PiRpcTaskStatus,
    expected_code: PiExternalAgentErrorCode,
) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("skill", encoding="utf-8")
    artifact_root = tmp_path / "artifacts" / "pi-agent"
    client = PiRpcAgentClient(
        command=("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", str(skill_path)),
        skill_path=skill_path,
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=artifact_root,
        transport=FakeRpcTransport(
            PiRpcTaskResult(
                status=status,
                safe_message="pi rpc failed",
                private_diagnostic="Bearer secret-token\ncookie=session",
            )
        ),
    )

    result = client.run_json_task_result("collect cards")

    assert result.ok is False
    assert result.error_code == expected_code
    assert "secret-token" not in result.safe_message
    assert "cookie" not in result.safe_message.lower()


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (FileNotFoundError("pi"), PiRpcTaskStatus.UNAVAILABLE),
        (PermissionError("pi"), PiRpcTaskStatus.UNAVAILABLE),
        (OSError("bad executable"), PiRpcTaskStatus.FAILED),
    ],
)
def test_subprocess_transport_maps_process_start_errors_without_throwing(
    tmp_path: Path,
    error: OSError,
    expected_status: PiRpcTaskStatus,
) -> None:
    def broken_process_factory(*args: object, **kwargs: object) -> object:
        raise error

    transport = SubprocessPiRpcTransport(process_factory=broken_process_factory)

    result = transport.request(
        PiRpcCommand(
            argv=("pi", "--mode", "rpc"),
            timeout_seconds=1,
            artifact_root=tmp_path,
        ),
        prompt="probe",
    )

    assert result.status == expected_status
    assert "pi" in result.safe_message
```

Also add subprocess-transport tests with an injected fake process factory. These tests must cover:

- process factory raises `FileNotFoundError`, `PermissionError`, or `OSError` before launch -> typed fail-closed result, not an uncaught exception
- process exits nonzero before a prompt response -> `PiRpcTaskStatus.FAILED`, not timeout
- process closes stdin before accepting the prompt -> `PiRpcTaskStatus.FAILED`, not an uncaught `BrokenPipeError`
- prompt response `success=false` -> `PiRpcTaskStatus.PROMPT_REJECTED`
- no `agent_end` before deadline -> `PiRpcTaskStatus.TIMEOUT` or `MISSING_AGENT_END` according to whether the process is still running or exited cleanly
- `extension_ui_request` -> `PiRpcTaskStatus.UI_REQUESTED`
- stderr is drained without exposing private text in public `safe_message`

- [x] **Step 2: Run the new test file to confirm failure**

Run:

```bash
uv run pytest tests/test_pi_external_agent.py -q
```

Expected: failure because `seektalent.providers.pi_agent.pi_external` does not exist.

- [x] **Step 3: Implement the Pi RPC boundary types, argv builder, and strict parser**

Create `src/seektalent/providers/pi_agent/pi_external.py` with these public names and behavior:

```python
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
from typing import Protocol


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
    def request(self, command: PiRpcCommand, *, prompt: str) -> PiRpcTaskResult:
        ...


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
    result = [part for part in argv if part not in {"--no-skills"}]
    if "--skill" in result:
        raise ValueError("liepin_pi_command must not inline --skill; use liepin_pi_skill_path")
    result.extend(["--no-skills", "--skill", str(skill_path)])
    return tuple(result)


def _arg_value(argv: tuple[str, ...], flag: str) -> str | None:
    try:
        index = argv.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(argv):
        return None
    return argv[index + 1]


def parse_strict_json_object(text: str) -> dict[str, object]:
    try:
        loaded = json.loads(text.strip())
    except json.JSONDecodeError as exc:
        raise ValueError("pi final output must be exactly one JSON object") from exc
    if not isinstance(loaded, dict):
        raise ValueError("pi final output must be a JSON object")
    return loaded
```

- [x] **Step 4: Implement subprocess transport with full deadline and UI-request denial**

Append to `src/seektalent/providers/pi_agent/pi_external.py`:

```python
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

        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None

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
        final_text = ""
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
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
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
                final_text = _assistant_text_from_agent_end(event)
                _stop_process(process)
                return PiRpcTaskResult(status=PiRpcTaskStatus.SUCCEEDED, final_text=final_text, events=tuple(events))

        if process.poll() is None:
            _stop_process(process)
            return PiRpcTaskResult(status=PiRpcTaskStatus.TIMEOUT, safe_message="pi rpc timed out")
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
```

Also append helper functions:

```python
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
```

- [x] **Step 5: Implement `PiRpcAgentClient`**

Append:

```python
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
    if any(marker in lowered for marker in ["bearer ", "cookie", "session=", "token", "secret"]):
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
```

- [x] **Step 6: Verify boundary tests pass**

Run:

```bash
uv run pytest tests/test_pi_external_agent.py -q
```

Expected: all tests in `tests/test_pi_external_agent.py` pass.

- [x] **Step 7: Run ruff for the new module and tests**

Run:

```bash
uv run ruff check src/seektalent/providers/pi_agent/pi_external.py tests/test_pi_external_agent.py
```

Expected: no violations.

### Task 2: Add Safe Payload Firewall

**Files:**
- Create: `tests/test_pi_payload_firewall.py`
- Create: `src/seektalent/providers/pi_agent/payload_firewall.py`

- [x] **Step 1: Add failing firewall tests**

Create `tests/test_pi_payload_firewall.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from seektalent.providers.pi_agent.payload_firewall import (
    LocalPiArtifactRegistry,
    SafePayloadFirewall,
    SafePayloadViolation,
    validate_public_artifact_ref,
)


class FakeArtifactRefRegistry:
    def __init__(self, refs: set[str]) -> None:
        self._refs = refs

    def contains_public_artifact_ref(self, ref: str) -> bool:
        return ref in self._refs

    def resolve_material(self, ref: str) -> bytes:
        if ref not in self._refs:
            raise SafePayloadViolation("artifact ref is not registered")
        return ref.encode("utf-8")


@pytest.mark.parametrize(
    "text",
    [
        "email me at candidate@example.com",
        "phone 13800138000",
        "wechat wx_candidate",
        "Bearer secret-token",
        "document.cookie",
        "<div>raw html</div>",
        "localStorage.getItem('token')",
    ],
)
def test_firewall_rejects_forbidden_free_text(text: str) -> None:
    firewall = SafePayloadFirewall()

    with pytest.raises(SafePayloadViolation):
        firewall.assert_safe_text(text)


@pytest.mark.parametrize(
    "ref",
    [
        "file:///etc/passwd",
        "https://attacker.example/x",
        "artifact://protected/../../secret",
        "/tmp/local-file",
        "artifact://unknown/run-1",
    ],
)
def test_artifact_ref_validator_rejects_unsafe_refs(ref: str) -> None:
    with pytest.raises(SafePayloadViolation):
        validate_public_artifact_ref(ref)


def test_artifact_ref_validator_rejects_missing_registry_record() -> None:
    registry = FakeArtifactRefRegistry(set())

    with pytest.raises(SafePayloadViolation):
        validate_public_artifact_ref("artifact://protected/pi-trace/run-1", registry=registry)


def test_artifact_ref_validator_accepts_registered_schemes() -> None:
    registry = FakeArtifactRefRegistry(
        {
            "artifact://protected/pi-trace/run-1",
            "artifact://public-summary/pi-card/run-1/1",
        }
    )

    assert (
        validate_public_artifact_ref("artifact://protected/pi-trace/run-1", registry=registry)
        == "artifact://protected/pi-trace/run-1"
    )
    assert (
        validate_public_artifact_ref("artifact://public-summary/pi-card/run-1/1", registry=registry)
        == "artifact://public-summary/pi-card/run-1/1"
    )


def test_local_pi_artifact_registry_resolves_materialized_refs(tmp_path: Path) -> None:
    registry = LocalPiArtifactRegistry(tmp_path)
    materialized = tmp_path / "pi-agent" / "protected" / "pi-provider-key" / "run-1" / "1"
    materialized.parent.mkdir(parents=True)
    materialized.write_bytes(b"provider-visible-key")

    assert registry.artifact_root_for_pi == tmp_path / "pi-agent"
    assert registry.resolve_material("artifact://protected/pi-provider-key/run-1/1") == b"provider-visible-key"


def test_local_pi_artifact_registry_rejects_string_only_refs(tmp_path: Path) -> None:
    registry = LocalPiArtifactRegistry(tmp_path)

    with pytest.raises(SafePayloadViolation):
        registry.resolve_material("artifact://protected/pi-provider-key/run-1/missing")
```

- [x] **Step 2: Run firewall tests to confirm failure**

Run:

```bash
uv run pytest tests/test_pi_payload_firewall.py -q
```

Expected: failure because `seektalent.providers.pi_agent.payload_firewall` does not exist.

- [x] **Step 3: Implement the firewall**

Create `src/seektalent/providers/pi_agent/payload_firewall.py`:

```python
from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol


class SafePayloadViolation(ValueError):
    pass


class ArtifactRefRegistry(Protocol):
    def contains_public_artifact_ref(self, ref: str) -> bool:
        ...

    def resolve_material(self, ref: str) -> bytes:
        ...


FORBIDDEN_TEXT_PATTERNS = (
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?:\+?86[-\s]?)?1[3-9]\d{9}\b"),
    re.compile(r"\b(?:wechat|weixin|wx)[-_:\s]?[A-Za-z0-9_]{4,}\b", re.IGNORECASE),
    re.compile(r"\bbearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"\bcookie\b|\bsession=", re.IGNORECASE),
    re.compile(r"\blocalStorage\b|\bsessionStorage\b", re.IGNORECASE),
    re.compile(r"<[^>]+>"),
)

ALLOWED_ARTIFACT_PREFIXES = (
    "artifact://protected/",
    "artifact://public-summary/",
)


class LocalPiArtifactRegistry:
    """Minimal v1 local registry for Pi-produced artifact refs.

    It deliberately resolves only refs already materialized under the local
    artifacts root. Missing files fail closed instead of being treated as
    public payload evidence.
    """

    def __init__(self, artifacts_root: Path) -> None:
        self._root = artifacts_root

    @property
    def artifact_root_for_pi(self) -> Path:
        return self._root / "pi-agent"

    def contains_public_artifact_ref(self, ref: str) -> bool:
        try:
            return self._path_for(ref).is_file()
        except SafePayloadViolation:
            return False

    def resolve_material(self, ref: str) -> bytes:
        path = self._path_for(ref)
        if not path.is_file():
            raise SafePayloadViolation("artifact ref is not registered")
        return path.read_bytes()

    def _path_for(self, ref: str) -> Path:
        validate_public_artifact_ref(ref)
        scope, _, relative = ref.removeprefix("artifact://").partition("/")
        if scope not in {"protected", "public-summary"} or not relative:
            raise SafePayloadViolation("unsupported artifact ref scope")
        path = (self.artifact_root_for_pi / scope / relative).resolve()
        root = (self.artifact_root_for_pi / scope).resolve()
        if root not in path.parents and path != root:
            raise SafePayloadViolation("artifact ref escapes artifact root")
        return path


class SafePayloadFirewall:
    def assert_safe_text(self, text: str | None) -> None:
        if text is None:
            return
        for pattern in FORBIDDEN_TEXT_PATTERNS:
            if pattern.search(text):
                raise SafePayloadViolation("forbidden external executor text")
        if len(text) > 2000:
            raise SafePayloadViolation("external executor text exceeds public safety limit")

    def assert_safe_mapping(self, payload: object) -> None:
        if isinstance(payload, str):
            self.assert_safe_text(payload)
            return
        if isinstance(payload, dict):
            for value in payload.values():
                self.assert_safe_mapping(value)
            return
        if isinstance(payload, list | tuple):
            for value in payload:
                self.assert_safe_mapping(value)


def validate_public_artifact_ref(
    ref: str | None,
    *,
    registry: ArtifactRefRegistry | None = None,
) -> str | None:
    if ref is None:
        return None
    if not ref.startswith(ALLOWED_ARTIFACT_PREFIXES):
        raise SafePayloadViolation("unsupported artifact ref scheme")
    if ".." in ref.split("/"):
        raise SafePayloadViolation("artifact ref contains parent path")
    if not re.fullmatch(r"artifact://[A-Za-z0-9._/-]+", ref):
        raise SafePayloadViolation("artifact ref contains invalid characters")
    if registry is not None and not registry.contains_public_artifact_ref(ref):
        raise SafePayloadViolation("artifact ref is not registered")
    return ref
```

- [x] **Step 4: Verify firewall tests pass**

Run:

```bash
uv run pytest tests/test_pi_payload_firewall.py -q
```

Expected: all tests pass.

### Task 3: Add Pi Liepin Skill Asset

**Files:**
- Modify: `tests/test_pi_external_agent.py`
- Create: `src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md`

- [x] **Step 1: Add a failing test for required skill boundaries**

Append to `tests/test_pi_external_agent.py`:

```python
def test_liepin_pi_skill_contains_required_browser_boundaries() -> None:
    skill = Path("src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md").read_text(encoding="utf-8")

    assert "Use DokoBot only through the Pi runtime" in skill
    assert "Do not ask for cookies" in skill
    assert "Do not open candidate detail pages in card mode" in skill
    assert "Return exactly one JSON object" in skill
    assert "SEEKTALENT_PI_ARTIFACT_ROOT" in skill
    assert "Do not return an artifact ref before the file is written" in skill
    assert "provider_candidate_key_material_ref" in skill
    assert "seektalent.pi_liepin_cards.v1" in skill
```

- [x] **Step 2: Run the skill test to confirm failure**

Run:

```bash
uv run pytest tests/test_pi_external_agent.py::test_liepin_pi_skill_contains_required_browser_boundaries -q
```

Expected: failure because the skill file does not exist.

- [x] **Step 3: Create the Pi Liepin skill asset**

Create `src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md`:

````markdown
---
name: liepin-search-cards
description: Search Liepin cards through DokoBot inside Pi and return one strict SeekTalent JSON envelope.
---

# SeekTalent Liepin Card Search

You are running inside Pi as SeekTalent's Liepin provider executor.

Use DokoBot only through the Pi runtime. Do not use Codex tools, local shell browser scripts, provider APIs, or network interception.

Inputs:

- source_run_id
- keyword_query
- query_terms
- card_page_size
- max_cards
- max_pages
- allowed_hosts
- dokobot_tool_name
- artifact_root from `SEEKTALENT_PI_ARTIFACT_ROOT`

Browser rules:

- Use the already logged-in browser profile exposed by DokoBot.
- Do not ask for cookies, passwords, SMS codes, QR codes, phone numbers, or session tokens.
- Do not export cookies, local storage, session storage, or raw browser state.
- Do not execute page JavaScript to extract hidden data.
- Do not open candidate detail pages in card mode.
- Do not solve verification challenges or risk-control challenges.
- Stop when login, verification, risk control, unsupported route, timeout, or budget exhaustion is detected.

Collection rules:

- Type the provided keyword exactly.
- Preserve Liepin provider order.
- Read at most max_pages pages.
- Return at most max_cards cards.
- Include visible card fields only.
- Write each protected and public-summary artifact file under `SEEKTALENT_PI_ARTIFACT_ROOT` using the relative path implied by its artifact ref.
- Do not return an artifact ref before the file is written.
- Use protected artifact refs for snapshots, traces, and provider-key material.
- For provider_candidate_key_material_ref and provider_account_material_ref, write only provider-visible stable key material needed for SeekTalent HMAC. Do not write cookies, tokens, contact data, or raw browser state.
- Do not include raw HTML, raw provider responses, cookies, tokens, direct contact data, or natural-language explanations in the JSON.

Return exactly one JSON object as the final assistant message. Surrounding whitespace is allowed. Markdown fences, notes, and multiple top-level payloads are not allowed.

Card envelope shape:

```json
{
  "schema_version": "seektalent.pi_liepin_cards.v1",
  "status": "succeeded",
  "stop_reason": "completed",
  "source_run_id": "run-id",
  "query": "keyword",
  "cards_seen": 0,
  "cards_returned": 0,
  "pages_visited": 0,
  "action_trace_ref": "artifact://protected/pi-trace/run-id",
  "safe_summary_refs": [],
  "protected_snapshot_refs": [],
  "cards": [
    {
      "provider_rank": 1,
      "provider_candidate_key_material_ref": "artifact://protected/pi-provider-key/run-id/1",
      "candidate_resume_id": "liepin-card-1",
      "display_name_masked": true,
      "safe_card_summary": {
        "display_title": "Senior Backend Engineer",
        "current_or_recent_company": "Example Inc",
        "current_or_recent_title": "Senior Backend Engineer",
        "work_years": 8,
        "age": 33,
        "city": "Shanghai",
        "expected_city": "Shanghai",
        "education_level": "master",
        "school_names": ["Shanghai Jiao Tong University"],
        "major_names": ["Computer Science"],
        "skill_tags": ["Python", "Search"],
        "job_intention": "Backend Engineer",
        "recent_experience_text": "Built ranking services",
        "normalized_card_text": "Senior backend engineer Python search"
      },
      "safe_card_summary_ref": "artifact://public-summary/pi-card/run-id/1",
      "protected_snapshot_ref": "artifact://protected/pi-card-snapshot/run-id/1"
    }
  ]
}
```

Allowed status values: succeeded, partial, blocked, failed.

Allowed stop_reason values: completed, partial_timeout, blocked_pi_unavailable, blocked_dokobot_unavailable, blocked_dokobot_tool_unavailable, blocked_permission_required, blocked_login_required, blocked_risk_control, blocked_unsupported_route, blocked_budget_exhausted, failed_malformed_output, failed_provider_error, failed_internal_error.
````

- [x] **Step 4: Verify the skill boundary test passes**

Run:

```bash
uv run pytest tests/test_pi_external_agent.py::test_liepin_pi_skill_contains_required_browser_boundaries -q
```

Expected: `1 passed`.

### Task 4: Add Strict Pi Liepin Output Models And Business Invariants

**Files:**
- Create: `tests/test_liepin_pi_executor.py`
- Create: `src/seektalent/providers/liepin/pi_executor.py`

- [x] **Step 1: Add failing tests for valid cards and invariant rejection**

Create `tests/test_liepin_pi_executor.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from seektalent.providers.liepin.pi_executor import HmacProviderKeyHasher, PiLiepinExecutor
from seektalent.providers.pi_agent.pi_external import PiRpcAgentClient, PiRpcTaskResult, PiRpcTaskStatus
from tests.test_pi_external_agent import FakeRpcTransport


@dataclass(frozen=True)
class FakeProviderKeyHasher:
    def provider_candidate_hash(self, *, provider: str, material_ref: str) -> str:
        return f"hmac:{provider}:{material_ref.rsplit('/', 1)[-1]}"

    def provider_account_hash(self, *, provider: str, material_ref: str) -> str:
        return f"acct:{provider}:{material_ref.rsplit('/', 1)[-1]}"


@dataclass(frozen=True)
class FakeArtifactRefRegistry:
    refs: frozenset[str]

    def contains_public_artifact_ref(self, ref: str) -> bool:
        return ref in self.refs

    def resolve_material(self, ref: str) -> bytes:
        if ref not in self.refs:
            raise ValueError("artifact ref is not registered")
        return ref.encode("utf-8")


def _registry(*refs: str) -> FakeArtifactRefRegistry:
    return FakeArtifactRefRegistry(frozenset(refs))


def _client(
    final_text: str = "",
    *,
    observed_tool_names: tuple[str, ...] = (),
    rpc_status: PiRpcTaskStatus = PiRpcTaskStatus.SUCCEEDED,
) -> PiRpcAgentClient:
    skill_path = Path("src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md")
    artifact_root = Path("artifacts/pi-agent")
    events = tuple(
        {"type": "tool_execution_start", "toolName": tool_name}
        for tool_name in observed_tool_names
    )
    return PiRpcAgentClient(
        command=("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", str(skill_path)),
        skill_path=skill_path,
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=artifact_root,
        transport=FakeRpcTransport(PiRpcTaskResult(status=rpc_status, final_text=final_text, events=events)),
    )


def test_hmac_provider_key_hasher_resolves_protected_material_inside_runtime() -> None:
    registry = _registry("artifact://protected/pi-provider-key/run-1/1")
    hasher = HmacProviderKeyHasher("runtime-secret", material_resolver=registry)

    candidate_hash = hasher.provider_candidate_hash(
        provider="liepin",
        material_ref="artifact://protected/pi-provider-key/run-1/1",
    )
    account_hash = hasher.provider_account_hash(
        provider="liepin",
        material_ref="artifact://protected/pi-provider-key/run-1/1",
    )

    assert candidate_hash != "artifact://protected/pi-provider-key/run-1/1"
    assert candidate_hash != account_hash
    assert hasher.provider_candidate_hash(
        provider="liepin",
        material_ref="artifact://protected/pi-provider-key/run-1/1",
    ) == candidate_hash


def test_pi_liepin_executor_maps_valid_cards_with_runtime_owned_hash() -> None:
    client = _client(
        """
{"schema_version":"seektalent.pi_liepin_cards.v1","status":"succeeded","stop_reason":"completed","source_run_id":"run-1","query":"python ranking","cards_seen":1,"cards_returned":1,"pages_visited":1,"action_trace_ref":"artifact://protected/pi-trace/run-1","safe_summary_refs":[],"protected_snapshot_refs":["artifact://protected/pi-page/run-1"],"cards":[{"provider_rank":1,"provider_candidate_key_material_ref":"artifact://protected/pi-provider-key/run-1/1","candidate_resume_id":"liepin-1","display_name_masked":true,"safe_card_summary":{"display_title":"Senior Backend Engineer","current_or_recent_company":"Example","current_or_recent_title":"Senior Backend Engineer","work_years":8,"age":33,"city":"Shanghai","expected_city":"Shanghai","education_level":"master","school_names":["SJTU"],"major_names":["CS"],"skill_tags":["Python","Ranking"],"job_intention":"Backend Engineer","recent_experience_text":"Built ranking systems","normalized_card_text":"senior backend python ranking"},"safe_card_summary_ref":"artifact://public-summary/pi-card/run-1/1","protected_snapshot_ref":"artifact://protected/pi-card/run-1/1"}]}
""".strip()
    )
    executor = PiLiepinExecutor(
        client=client,
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-trace/run-1",
            "artifact://protected/pi-page/run-1",
            "artifact://protected/pi-provider-key/run-1/1",
            "artifact://public-summary/pi-card/run-1/1",
            "artifact://protected/pi-card/run-1/1",
        ),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python ranking",
        query_terms=("python", "ranking"),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "succeeded"
    assert result.card_search is not None
    assert len(result.card_search.cards) == 1
    assert result.card_search.cards[0].payload["providerCandidateKeyHash"] == "hmac:liepin:1"
    assert result.card_search.cards[0].extractor_version == "pi_liepin_cards_v1"


@pytest.mark.parametrize(
    "payload_patch",
    [
        '"source_run_id":"wrong-run"',
        '"query":"wrong query"',
        '"cards_seen":1,"cards_returned":2',
        '"pages_visited":2',
    ],
)
def test_pi_liepin_executor_rejects_business_invariant_violations(payload_patch: str) -> None:
    base = '{"schema_version":"seektalent.pi_liepin_cards.v1","status":"succeeded","stop_reason":"completed","source_run_id":"run-1","query":"python","cards_seen":0,"cards_returned":0,"pages_visited":1,"action_trace_ref":"artifact://protected/pi-trace/run-1","safe_summary_refs":[],"protected_snapshot_refs":[],"cards":[]}'
    bad = base
    for fragment in payload_patch.split(","):
        key = fragment.split(":", 1)[0]
        bad = bad.replace(next(part for part in base.split(",") if part.startswith(key)), fragment)
    executor = PiLiepinExecutor(
        client=_client(bad),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry("artifact://protected/pi-trace/run-1"),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python",
        query_terms=("python",),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "failed"
    assert result.stop_reason == "failed_malformed_output"
    assert result.safe_reason_code == "failed_provider_error"


def test_pi_liepin_executor_treats_rpc_timeout_without_final_cards_as_failed_provider_error() -> None:
    executor = PiLiepinExecutor(
        client=_client(rpc_status=PiRpcTaskStatus.TIMEOUT),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python",
        query_terms=("python",),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "failed"
    assert result.stop_reason == "failed_provider_error"
    assert result.safe_reason_code == "failed_provider_error"
    assert result.card_search is None
```

- [x] **Step 2: Run executor tests to confirm failure**

Run:

```bash
uv run pytest tests/test_liepin_pi_executor.py -q
```

Expected: failure because `seektalent.providers.liepin.pi_executor` does not exist.

- [x] **Step 3: Implement strict models with `strict=True`, hidden inputs, and validators**

Create `src/seektalent/providers/liepin/pi_executor.py` with these boundary types:

```python
from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from seektalent.providers.liepin.models import LiepinAccessScope, LiepinExtractionSource
from seektalent.providers.liepin.worker_contracts import (
    LiepinCardSearchResponse,
    LiepinIdentityConfidence,
    LiepinPiiClassification,
    LiepinRedactionState,
    LiepinRetentionPolicy,
    LiepinSafeCardSummary,
    LiepinWorkerCandidateCard,
)
from seektalent.providers.pi_agent.payload_firewall import (
    ArtifactRefRegistry,
    SafePayloadFirewall,
    SafePayloadViolation,
    validate_public_artifact_ref,
)
from seektalent.providers.pi_agent.pi_external import PiExternalAgentErrorCode, PiRpcAgentClient


PI_MODEL_CONFIG = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class ProviderKeyHasher(Protocol):
    def provider_candidate_hash(self, *, provider: str, material_ref: str) -> str:
        ...

    def provider_account_hash(self, *, provider: str, material_ref: str) -> str:
        ...


class ProviderKeyMaterialResolver(Protocol):
    def resolve_material(self, material_ref: str) -> bytes:
        ...


class HmacProviderKeyHasher:
    def __init__(self, secret: str, *, material_resolver: ProviderKeyMaterialResolver) -> None:
        if not secret:
            raise ValueError("provider key HMAC secret is required")
        self._secret = secret.encode("utf-8")
        self._material_resolver = material_resolver

    def provider_candidate_hash(self, *, provider: str, material_ref: str) -> str:
        return self._hash(namespace="candidate", provider=provider, material_ref=material_ref)

    def provider_account_hash(self, *, provider: str, material_ref: str) -> str:
        return self._hash(namespace="account", provider=provider, material_ref=material_ref)

    def _hash(self, *, namespace: str, provider: str, material_ref: str) -> str:
        material = self._material_resolver.resolve_material(material_ref)
        payload = b"\0".join([namespace.encode("utf-8"), provider.encode("utf-8"), material])
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()


class PiLiepinStopReason(str):
    pass


class PiLiepinSafeCardSummary(BaseModel):
    model_config = PI_MODEL_CONFIG

    display_title: str | None = Field(default=None, max_length=200)
    current_or_recent_company: str | None = Field(default=None, max_length=200)
    current_or_recent_title: str | None = Field(default=None, max_length=200)
    work_years: int | None = Field(default=None, ge=0, le=80)
    age: int | None = Field(default=None, ge=16, le=80)
    city: str | None = Field(default=None, max_length=80)
    expected_city: str | None = Field(default=None, max_length=80)
    education_level: str | None = Field(default=None, max_length=80)
    school_names: list[str] = Field(default_factory=list)
    major_names: list[str] = Field(default_factory=list)
    skill_tags: list[str] = Field(default_factory=list)
    job_intention: str | None = Field(default=None, max_length=200)
    recent_experience_text: str | None = Field(default=None, max_length=1000)
    normalized_card_text: str = Field(max_length=2000)
```

Append card and envelope models:

```python
class PiLiepinCardEnvelope(BaseModel):
    model_config = PI_MODEL_CONFIG

    provider_rank: int = Field(ge=1)
    provider_candidate_key_material_ref: str
    candidate_resume_id: str = Field(min_length=1, max_length=200)
    display_name_masked: bool
    safe_card_summary: PiLiepinSafeCardSummary
    safe_card_summary_ref: str | None = None
    protected_snapshot_ref: str | None = None

    @model_validator(mode="after")
    def validate_refs(self) -> PiLiepinCardEnvelope:
        validate_public_artifact_ref(self.provider_candidate_key_material_ref)
        validate_public_artifact_ref(self.safe_card_summary_ref)
        validate_public_artifact_ref(self.protected_snapshot_ref)
        return self


class PiLiepinCardsEnvelope(BaseModel):
    model_config = PI_MODEL_CONFIG

    schema_version: Literal["seektalent.pi_liepin_cards.v1"]
    status: Literal["succeeded", "partial", "blocked", "failed"]
    stop_reason: Literal[
        "completed",
        "partial_timeout",
        "blocked_pi_unavailable",
        "blocked_dokobot_unavailable",
        "blocked_dokobot_tool_unavailable",
        "blocked_permission_required",
        "blocked_login_required",
        "blocked_risk_control",
        "blocked_unsupported_route",
        "blocked_budget_exhausted",
        "failed_malformed_output",
        "failed_provider_error",
        "failed_internal_error",
    ] | None
    source_run_id: str
    query: str
    cards_seen: int = Field(ge=0)
    cards_returned: int = Field(ge=0)
    pages_visited: int = Field(ge=0)
    action_trace_ref: str
    safe_summary_refs: list[str] = Field(default_factory=list)
    protected_snapshot_refs: list[str] = Field(default_factory=list)
    cards: list[PiLiepinCardEnvelope] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_envelope(self) -> PiLiepinCardsEnvelope:
        validate_public_artifact_ref(self.action_trace_ref)
        for ref in self.safe_summary_refs:
            validate_public_artifact_ref(ref)
        for ref in self.protected_snapshot_refs:
            validate_public_artifact_ref(ref)
        if self.cards_returned != len(self.cards):
            raise ValueError("cards_returned must equal len(cards)")
        if self.cards_seen < self.cards_returned:
            raise ValueError("cards_seen must be >= cards_returned")
        ranks = [card.provider_rank for card in self.cards]
        if len(set(ranks)) != len(ranks):
            raise ValueError("provider_rank must be unique")
        if self.status == "succeeded" and self.stop_reason not in {"completed", None}:
            raise ValueError("succeeded status requires completed stop_reason")
        if self.status in {"blocked", "failed"} and self.cards:
            raise ValueError("blocked/failed envelopes must not carry cards")
        return self
```

- [x] **Step 4: Implement result dataclass and executor invariant checks**

Append:

```python
@dataclass(frozen=True, kw_only=True)
class LiepinPiCardSearchResult:
    status: Literal["succeeded", "partial", "blocked", "failed"]
    stop_reason: str | None
    safe_reason_code: str | None
    action_trace_ref: str | None
    card_search: LiepinCardSearchResponse | None = None


class PiLiepinExecutor:
    def __init__(
        self,
        *,
        client: PiRpcAgentClient,
        key_hasher: ProviderKeyHasher,
        artifact_registry: ArtifactRefRegistry,
        firewall: SafePayloadFirewall | None = None,
    ) -> None:
        self._client = client
        self._key_hasher = key_hasher
        self._artifact_registry = artifact_registry
        self._firewall = firewall or SafePayloadFirewall()

    def search_cards(
        self,
        *,
        source_run_id: str,
        keyword_query: str,
        query_terms: Sequence[str],
        page_size: int,
        max_pages: int,
        max_cards: int,
    ) -> LiepinPiCardSearchResult:
        prompt = _build_card_search_prompt(
            source_run_id=source_run_id,
            keyword_query=keyword_query,
            query_terms=tuple(query_terms),
            page_size=page_size,
            max_pages=max_pages,
            max_cards=max_cards,
        )
        task_result = self._client.run_json_task_result(prompt)
        if not task_result.ok or task_result.envelope is None:
            return _failed_external_result(task_result.error_code)
        try:
            self._firewall.assert_safe_mapping(task_result.envelope)
            envelope = PiLiepinCardsEnvelope.model_validate(task_result.envelope)
            _validate_against_request(
                envelope,
                source_run_id=source_run_id,
                keyword_query=keyword_query,
                max_pages=max_pages,
                max_cards=max_cards,
            )
            _validate_card_envelope_artifact_refs(envelope, self._artifact_registry)
        except CardModeTraceViolation:
            return LiepinPiCardSearchResult(
                status="failed",
                stop_reason="failed_provider_error",
                safe_reason_code="failed_provider_error",
                action_trace_ref=None,
            )
        except (ValidationError, ValueError, SafePayloadViolation):
            return LiepinPiCardSearchResult(
                status="failed",
                stop_reason="failed_malformed_output",
                safe_reason_code="failed_provider_error",
                action_trace_ref=None,
            )
        if envelope.status in {"blocked", "failed"}:
            return LiepinPiCardSearchResult(
                status=envelope.status,
                stop_reason=envelope.stop_reason,
                safe_reason_code=_safe_reason_for_stop(envelope.stop_reason),
                action_trace_ref=envelope.action_trace_ref,
            )
        card_search = LiepinCardSearchResponse(
            cards=[self._map_card(card) for card in envelope.cards],
            exhausted=envelope.stop_reason == "completed",
            rawCandidateCount=envelope.cards_seen,
            requestPayload={"source": "liepin", "query": envelope.query, "pages_visited": envelope.pages_visited},
        )
        return LiepinPiCardSearchResult(
            status=envelope.status,
            stop_reason=envelope.stop_reason,
            safe_reason_code=_safe_reason_for_stop(envelope.stop_reason),
            action_trace_ref=envelope.action_trace_ref,
            card_search=card_search,
        )

    def _map_card(self, card: PiLiepinCardEnvelope) -> LiepinWorkerCandidateCard:
        provider_hash = self._key_hasher.provider_candidate_hash(
            provider="liepin",
            material_ref=card.provider_candidate_key_material_ref,
        )
        safe_summary = LiepinSafeCardSummary(
            **card.safe_card_summary.model_dump(exclude={"normalized_card_text"}),
            masked_name=card.display_name_masked,
        )
        return LiepinWorkerCandidateCard(
            payload={
                "providerRank": card.provider_rank,
                "providerCandidateKeyHash": provider_hash,
                "candidateResumeId": card.candidate_resume_id,
                "safeCardSummaryRef": card.safe_card_summary_ref,
                "protectedSnapshotRef": card.protected_snapshot_ref,
            },
            normalized_text=card.safe_card_summary.normalized_card_text,
            provider_subject_id=None,
            provider_listing_id=provider_hash,
            synthetic_candidate_fingerprint=f"liepin:{provider_hash}",
            identity_confidence=cast(LiepinIdentityConfidence, "synthetic_fingerprint"),
            extraction_source=cast(LiepinExtractionSource, "dom_fallback"),
            pii_classification=cast(LiepinPiiClassification, "no_direct_contact"),
            retention_policy=cast(LiepinRetentionPolicy, "provider_snapshot_7d"),
            access_scope=cast(LiepinAccessScope, "local_run_only"),
            redaction_state=cast(LiepinRedactionState, "redacted"),
            extractor_version="pi_liepin_cards_v1",
            safe_card_summary=safe_summary,
        )
```

Append helpers:

```python
def _build_card_search_prompt(
    *,
    source_run_id: str,
    keyword_query: str,
    query_terms: tuple[str, ...],
    page_size: int,
    max_pages: int,
    max_cards: int,
) -> str:
    return (
        "Run Liepin card search. "
        f"source_run_id={source_run_id!r}; "
        f"keyword_query={keyword_query!r}; "
        f"query_terms={list(query_terms)!r}; "
        f"card_page_size={page_size}; max_pages={max_pages}; max_cards={max_cards}."
    )


def _validate_against_request(
    envelope: PiLiepinCardsEnvelope,
    *,
    source_run_id: str,
    keyword_query: str,
    max_pages: int,
    max_cards: int,
) -> None:
    if envelope.source_run_id != source_run_id:
        raise ValueError("source_run_id mismatch")
    if envelope.query != keyword_query:
        raise ValueError("query mismatch")
    if envelope.pages_visited > max_pages:
        raise ValueError("pages_visited exceeds max_pages")
    if envelope.cards_returned > max_cards:
        raise ValueError("cards_returned exceeds max_cards")


def _validate_registered_refs(refs: Sequence[str | None], registry: ArtifactRefRegistry) -> None:
    for ref in refs:
        validate_public_artifact_ref(ref, registry=registry)


def _validate_card_envelope_artifact_refs(envelope: PiLiepinCardsEnvelope, registry: ArtifactRefRegistry) -> None:
    refs = [
        envelope.action_trace_ref,
        *envelope.safe_summary_refs,
        *envelope.protected_snapshot_refs,
    ]
    for card in envelope.cards:
        refs.extend(
            [
                card.provider_candidate_key_material_ref,
                card.safe_card_summary_ref,
                card.protected_snapshot_ref,
            ]
        )
    _validate_registered_refs(refs, registry)


def _failed_external_result(error_code: PiExternalAgentErrorCode | None) -> LiepinPiCardSearchResult:
    stop_reason = {
        PiExternalAgentErrorCode.PI_UNAVAILABLE: "blocked_pi_unavailable",
        PiExternalAgentErrorCode.PROMPT_REJECTED: "failed_provider_error",
        PiExternalAgentErrorCode.TIMEOUT: "failed_provider_error",
        PiExternalAgentErrorCode.UI_REQUEST_DENIED: "blocked_permission_required",
        PiExternalAgentErrorCode.PROCESS_FAILED: "failed_provider_error",
        PiExternalAgentErrorCode.MISSING_AGENT_END: "failed_provider_error",
        PiExternalAgentErrorCode.MALFORMED_OUTPUT: "failed_malformed_output",
        None: "failed_provider_error",
    }[error_code]
    safe_reason = _runtime_safe_reason_for_stop(stop_reason)
    status = "blocked" if safe_reason.startswith("blocked_") else "failed"
    return LiepinPiCardSearchResult(status=status, stop_reason=stop_reason, safe_reason_code=safe_reason, action_trace_ref=None)


def _safe_reason_for_stop(stop_reason: str | None) -> str | None:
    if stop_reason in {None, "completed"}:
        return None
    return _runtime_safe_reason_for_stop(stop_reason)


def _runtime_safe_reason_for_stop(stop_reason: str | None) -> str:
    if stop_reason in {None, "completed"}:
        return "failed_provider_error"
    if stop_reason == "blocked_pi_unavailable":
        return "blocked_backend_unavailable"
    if stop_reason in {"blocked_dokobot_unavailable", "blocked_dokobot_tool_unavailable"}:
        return "blocked_backend_unavailable"
    if stop_reason == "blocked_login_required":
        return "blocked_login_required"
    if stop_reason in {"blocked_dokobot_permission_missing", "blocked_unsupported_host_policy"}:
        return "blocked_compliance"
    if stop_reason in {"blocked_permission_required", "blocked_risk_control"}:
        return "blocked_compliance"
    if stop_reason == "blocked_budget_exhausted":
        return "blocked_budget_exhausted"
    if stop_reason == "partial_timeout":
        return "partial_timeout"
    return "failed_provider_error"
```

- [x] **Step 5: Verify strict executor tests pass**

Run:

```bash
uv run pytest tests/test_liepin_pi_executor.py -q
```

Expected: all tests in `tests/test_liepin_pi_executor.py` pass.

### Task 5: Add Capability Proof And Session Probe Contracts

**Files:**
- Modify: `tests/test_liepin_pi_executor.py`
- Modify: `src/seektalent/providers/liepin/pi_executor.py`

- [x] **Step 1: Add failing tests for capability proof and self-report rejection**

Append to `tests/test_liepin_pi_executor.py`:

```python
def test_capability_probe_requires_manifest_or_observed_tool_evidence() -> None:
    client = _client(
        """
{"schema_version":"seektalent.pi_capability_probe.v1","status":"ready","pi_version":"0.1.0","read_tool_name":"dokobot.read","action_tool_names":["dokobot.click","dokobot.type_text"],"proof_kind":"self_report_only","capability_manifest_ref":null,"tool_evidence_ref":null,"allowed_hosts":["liepin.com"],"stop_reason":null}
""".strip()
    )
    executor = PiLiepinExecutor(client=client, key_hasher=FakeProviderKeyHasher(), artifact_registry=_registry())

    result = executor.probe_capabilities(expected_dokobot_tool_name="dokobot")

    assert result.ready is False
    assert result.safe_reason_code == "blocked_backend_unavailable"


def test_capability_probe_accepts_trusted_manifest_and_tool_evidence() -> None:
    client = _client(
        """
{"schema_version":"seektalent.pi_capability_probe.v1","status":"ready","pi_version":"0.1.0","read_tool_name":"dokobot.read","action_tool_names":["dokobot.navigate","dokobot.click","dokobot.type_text"],"proof_kind":"trusted_manifest_and_observed_tool_event","capability_manifest_ref":"artifact://protected/pi-capability/run-1/manifest","tool_evidence_ref":"artifact://protected/pi-capability/run-1/tool-events","allowed_hosts":["liepin.com"],"stop_reason":null}
""".strip(),
        observed_tool_names=("dokobot.read", "dokobot.navigate", "dokobot.click", "dokobot.type_text"),
    )
    executor = PiLiepinExecutor(
        client=client,
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-capability/run-1/manifest",
            "artifact://protected/pi-capability/run-1/tool-events",
        ),
    )

    result = executor.probe_capabilities(expected_dokobot_tool_name="dokobot")

    assert result.ready is True
    assert result.safe_reason_code is None


def test_capability_probe_rejects_non_dokobot_tool_actions() -> None:
    client = _client(
        """
{"schema_version":"seektalent.pi_capability_probe.v1","status":"ready","pi_version":"0.1.0","read_tool_name":"dokobot.read","action_tool_names":["browser.navigate","browser.click","browser.type_text"],"proof_kind":"trusted_manifest_and_observed_tool_event","capability_manifest_ref":"artifact://protected/pi-capability/run-1/manifest","tool_evidence_ref":"artifact://protected/pi-capability/run-1/tool-events","allowed_hosts":["liepin.com"],"stop_reason":null}
""".strip(),
        observed_tool_names=("browser.navigate", "browser.click", "browser.type_text"),
    )
    executor = PiLiepinExecutor(
        client=client,
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-capability/run-1/manifest",
            "artifact://protected/pi-capability/run-1/tool-events",
        ),
    )

    result = executor.probe_capabilities(expected_dokobot_tool_name="dokobot")

    assert result.ready is False
    assert result.safe_reason_code == "blocked_backend_unavailable"


def test_capability_probe_rejects_envelope_claims_without_observed_tool_events() -> None:
    client = _client(
        """
{"schema_version":"seektalent.pi_capability_probe.v1","status":"ready","pi_version":"0.1.0","read_tool_name":"dokobot.read","action_tool_names":["dokobot.navigate","dokobot.click","dokobot.type_text"],"proof_kind":"trusted_manifest_and_observed_tool_event","capability_manifest_ref":"artifact://protected/pi-capability/run-1/manifest","tool_evidence_ref":"artifact://protected/pi-capability/run-1/tool-events","allowed_hosts":["liepin.com"],"stop_reason":null}
""".strip()
    )
    executor = PiLiepinExecutor(
        client=client,
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-capability/run-1/manifest",
            "artifact://protected/pi-capability/run-1/tool-events",
        ),
    )

    result = executor.probe_capabilities(expected_dokobot_tool_name="dokobot")

    assert result.ready is False
    assert result.safe_reason_code == "blocked_backend_unavailable"


def test_capability_probe_rejects_forbidden_free_text() -> None:
    client = _client(
        """
{"schema_version":"seektalent.pi_capability_probe.v1","status":"ready","pi_version":"Bearer secret-token","read_tool_name":"dokobot.read","action_tool_names":["dokobot.navigate","dokobot.click","dokobot.type_text"],"proof_kind":"trusted_manifest_and_observed_tool_event","capability_manifest_ref":"artifact://protected/pi-capability/run-1/manifest","tool_evidence_ref":"artifact://protected/pi-capability/run-1/tool-events","allowed_hosts":["liepin.com"],"stop_reason":null}
""".strip(),
        observed_tool_names=("dokobot.read", "dokobot.navigate", "dokobot.click", "dokobot.type_text"),
    )
    executor = PiLiepinExecutor(
        client=client,
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-capability/run-1/manifest",
            "artifact://protected/pi-capability/run-1/tool-events",
        ),
    )

    result = executor.probe_capabilities(expected_dokobot_tool_name="dokobot")

    assert result.ready is False
    assert result.safe_reason_code == "blocked_backend_unavailable"
```

- [x] **Step 2: Add failing tests for session privacy**

Append:

```python
def test_session_probe_hashes_account_material_only_when_ready() -> None:
    client = _client(
        """
{"schema_version":"seektalent.pi_liepin_session_probe.v1","status":"ready","connection_id":"liepin-pi-agent","provider_account_material_ref":"artifact://protected/pi-account/run-1/current","page_origin":"https://www.liepin.com","stop_reason":null}
""".strip()
    )
    executor = PiLiepinExecutor(
        client=client,
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry("artifact://protected/pi-account/run-1/current"),
    )

    result = executor.probe_session(connection_id="liepin-pi-agent")

    assert result.status == "ready"
    assert result.provider_account_hash == "acct:liepin:current"


def test_session_probe_rejects_non_ready_account_material() -> None:
    client = _client(
        """
{"schema_version":"seektalent.pi_liepin_session_probe.v1","status":"login_required","connection_id":"liepin-pi-agent","provider_account_material_ref":"artifact://protected/pi-account/run-1/current","page_origin":"https://www.liepin.com","stop_reason":"blocked_login_required"}
""".strip()
    )
    executor = PiLiepinExecutor(
        client=client,
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry("artifact://protected/pi-account/run-1/current"),
    )

    result = executor.probe_session(connection_id="liepin-pi-agent")

    assert result.status == "failed"
    assert result.safe_reason_code == "failed_provider_error"
    assert result.provider_account_hash is None


def test_session_probe_rejects_unknown_stop_reason() -> None:
    client = _client(
        """
{"schema_version":"seektalent.pi_liepin_session_probe.v1","status":"login_required","connection_id":"liepin-pi-agent","provider_account_material_ref":null,"page_origin":"https://www.liepin.com","stop_reason":"not_allowed"}
""".strip()
    )
    executor = PiLiepinExecutor(client=client, key_hasher=FakeProviderKeyHasher(), artifact_registry=_registry())

    result = executor.probe_session(connection_id="liepin-pi-agent")

    assert result.status == "failed"
    assert result.safe_reason_code == "failed_provider_error"
```

- [x] **Step 3: Implement probe models and result dataclasses**

Append to `src/seektalent/providers/liepin/pi_executor.py`:

```python
@dataclass(frozen=True, kw_only=True)
class PiLiepinCapabilityProbeResult:
    ready: bool
    safe_reason_code: str | None = None


@dataclass(frozen=True, kw_only=True)
class PiLiepinSessionProbeResult:
    status: Literal["ready", "login_required", "risk_control", "blocked", "failed"]
    provider_account_hash: str | None = None
    safe_reason_code: str | None = None


class PiCapabilityProbeEnvelope(BaseModel):
    model_config = PI_MODEL_CONFIG

    schema_version: Literal["seektalent.pi_capability_probe.v1"]
    status: Literal["ready", "blocked", "failed"]
    pi_version: str
    read_tool_name: str | None = None
    action_tool_names: list[str] = Field(default_factory=list)
    proof_kind: Literal["self_report_only", "trusted_manifest_and_observed_tool_event", "observed_tool_event"]
    capability_manifest_ref: str | None = None
    tool_evidence_ref: str | None = None
    allowed_hosts: list[str] = Field(default_factory=list)
    stop_reason: Literal[
        "blocked_pi_unavailable",
        "blocked_dokobot_unavailable",
        "blocked_dokobot_tool_unavailable",
        "blocked_dokobot_permission_missing",
        "blocked_unsupported_host_policy",
        "failed_malformed_output",
        "failed_internal_error",
    ] | None = None

    @model_validator(mode="after")
    def validate_probe(self) -> PiCapabilityProbeEnvelope:
        validate_public_artifact_ref(self.capability_manifest_ref)
        validate_public_artifact_ref(self.tool_evidence_ref)
        if self.status == "ready":
            if "liepin.com" not in self.allowed_hosts:
                raise ValueError("ready capability probe requires liepin.com")
            if self.proof_kind == "self_report_only":
                raise ValueError("ready capability probe requires observed proof")
            if not self.tool_evidence_ref:
                raise ValueError("ready capability probe requires tool_evidence_ref")
        return self


class PiSessionProbeEnvelope(BaseModel):
    model_config = PI_MODEL_CONFIG

    schema_version: Literal["seektalent.pi_liepin_session_probe.v1"]
    status: Literal["ready", "login_required", "risk_control", "blocked", "failed"]
    connection_id: str
    provider_account_material_ref: str | None = None
    page_origin: str
    stop_reason: Literal[
        "blocked_pi_unavailable",
        "blocked_dokobot_unavailable",
        "blocked_dokobot_tool_unavailable",
        "blocked_permission_required",
        "blocked_login_required",
        "blocked_risk_control",
        "failed_malformed_output",
        "failed_provider_error",
        "failed_internal_error",
    ] | None = None

    @model_validator(mode="after")
    def validate_session_probe(self) -> PiSessionProbeEnvelope:
        validate_public_artifact_ref(self.provider_account_material_ref)
        if self.status == "ready" and not self.provider_account_material_ref:
            raise ValueError("ready session requires account material ref")
        if self.status != "ready" and self.provider_account_material_ref:
            raise ValueError("non-ready session must not include account material")
        if not self.page_origin.startswith("https://www.liepin.com") and not self.page_origin.startswith("https://liepin.com"):
            raise ValueError("session page_origin must be liepin.com")
        return self
```

- [x] **Step 4: Add `probe_capabilities()` and `probe_session()` methods**

Add these methods to `PiLiepinExecutor`:

```python
def probe_capabilities(self, *, expected_dokobot_tool_name: str) -> PiLiepinCapabilityProbeResult:
    task_result = self._client.run_json_task_result(
        f"Probe Pi Liepin browser capability for DokoBot tool prefix {expected_dokobot_tool_name!r}."
    )
    if not task_result.ok or task_result.envelope is None:
        return PiLiepinCapabilityProbeResult(ready=False, safe_reason_code="blocked_backend_unavailable")
    try:
        self._firewall.assert_safe_mapping(task_result.envelope)
        envelope = PiCapabilityProbeEnvelope.model_validate(task_result.envelope)
        _validate_registered_refs(
            [envelope.capability_manifest_ref, envelope.tool_evidence_ref],
            self._artifact_registry,
        )
    except (ValidationError, ValueError, SafePayloadViolation):
        return PiLiepinCapabilityProbeResult(ready=False, safe_reason_code="blocked_backend_unavailable")
    if envelope.status != "ready":
        return PiLiepinCapabilityProbeResult(ready=False, safe_reason_code=_safe_reason_for_stop(envelope.stop_reason))
    expected_prefix = f"{expected_dokobot_tool_name}."
    if envelope.read_tool_name is None or not envelope.read_tool_name.startswith(expected_prefix):
        return PiLiepinCapabilityProbeResult(ready=False, safe_reason_code="blocked_backend_unavailable")
    required_actions = {"navigate", "click", "type_text"}
    action_suffixes: set[str] = set()
    for tool_name in envelope.action_tool_names:
        if not tool_name.startswith(expected_prefix):
            return PiLiepinCapabilityProbeResult(ready=False, safe_reason_code="blocked_backend_unavailable")
        action_suffixes.add(tool_name.rsplit(".", 1)[-1])
    if not required_actions.issubset(action_suffixes):
        return PiLiepinCapabilityProbeResult(ready=False, safe_reason_code="blocked_backend_unavailable")
    observed_tool_names = set(task_result.observed_tool_names)
    required_observed_tools = {envelope.read_tool_name, *(f"{expected_prefix}{action}" for action in required_actions)}
    if not required_observed_tools.issubset(observed_tool_names):
        return PiLiepinCapabilityProbeResult(ready=False, safe_reason_code="blocked_backend_unavailable")
    return PiLiepinCapabilityProbeResult(ready=True)


def probe_session(self, *, connection_id: str) -> PiLiepinSessionProbeResult:
    task_result = self._client.run_json_task_result(f"Probe Liepin session for connection_id={connection_id!r}.")
    if not task_result.ok or task_result.envelope is None:
        return PiLiepinSessionProbeResult(status="blocked", safe_reason_code="blocked_backend_unavailable")
    try:
        self._firewall.assert_safe_mapping(task_result.envelope)
        envelope = PiSessionProbeEnvelope.model_validate(task_result.envelope)
        _validate_registered_refs([envelope.provider_account_material_ref], self._artifact_registry)
    except (ValidationError, ValueError, SafePayloadViolation):
        return PiLiepinSessionProbeResult(status="failed", safe_reason_code="failed_provider_error")
    if envelope.connection_id != connection_id:
        return PiLiepinSessionProbeResult(status="failed", safe_reason_code="failed_provider_error")
    if envelope.status != "ready":
        return PiLiepinSessionProbeResult(status=envelope.status, safe_reason_code=_safe_reason_for_stop(envelope.stop_reason))
    assert envelope.provider_account_material_ref is not None
    return PiLiepinSessionProbeResult(
        status="ready",
        provider_account_hash=self._key_hasher.provider_account_hash(
            provider="liepin",
            material_ref=envelope.provider_account_material_ref,
        ),
    )
```

- [x] **Step 5: Verify capability and session probe tests pass**

Run:

```bash
uv run pytest tests/test_liepin_pi_executor.py -q
```

Expected: all tests pass.

### Task 6: Enforce Card-Mode Trace And Detail Boundary

**Files:**
- Modify: `tests/test_liepin_pi_executor.py`
- Modify: `src/seektalent/providers/liepin/pi_executor.py`

- [x] **Step 1: Add failing tests for card-mode detail-route trace rejection**

Append to `tests/test_liepin_pi_executor.py`:

```python
def test_card_mode_rejects_valid_cards_when_trace_shows_detail_route() -> None:
    client = _client(
        """
{"schema_version":"seektalent.pi_liepin_cards.v1","status":"succeeded","stop_reason":"completed","source_run_id":"run-1","query":"python","cards_seen":0,"cards_returned":0,"pages_visited":1,"action_trace_ref":"artifact://protected/pi-trace/run-1/detail-route","safe_summary_refs":[],"protected_snapshot_refs":[],"cards":[]}
""".strip()
    )
    executor = PiLiepinExecutor(
        client=client,
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry("artifact://protected/pi-trace/run-1/detail-route"),
    )

    result = executor.search_cards(
        source_run_id="run-1",
        keyword_query="python",
        query_terms=("python",),
        page_size=10,
        max_pages=1,
        max_cards=10,
    )

    assert result.status == "failed"
    assert result.safe_reason_code == "failed_provider_error"
```

- [x] **Step 2: Add trace validator**

Append helper to `src/seektalent/providers/liepin/pi_executor.py`:

```python
class CardModeTraceViolation(ValueError):
    pass


def _validate_card_mode_trace_ref(action_trace_ref: str, registry: ArtifactRefRegistry) -> None:
    trace_text = registry.resolve_material(action_trace_ref).decode("utf-8", errors="replace").lower()
    forbidden_markers = (
        '"route_kind": "detail"',
        '"routeKind": "detail"',
        '"action_kind": "contact_button"',
        '"actionKind": "contact_button"',
        "contact-button",
        "resume-detail",
        "detail-tab",
        "/detail",
    )
    if any(marker in trace_text for marker in forbidden_markers):
        raise CardModeTraceViolation("card-mode trace shows detail navigation")
```

Call `_validate_card_mode_trace_ref(envelope.action_trace_ref, self._artifact_registry)` after `_validate_card_envelope_artifact_refs(...)`.

Also add tests proving:

- a harmless-looking ref whose materialized trace content includes `route_kind=detail` fails closed
- `display_name_masked=true` maps to `LiepinSafeCardSummary.masked_name is True`

- [x] **Step 3: Verify trace boundary test passes**

Run:

```bash
uv run pytest tests/test_liepin_pi_executor.py::test_card_mode_rejects_valid_cards_when_trace_shows_detail_route -q
```

Expected: `1 passed`.

### Task 7: Wire `pi_agent` Configuration And Remove The Old Live Mode

**Files:**
- Modify: `tests/test_liepin_config.py`
- Modify: `src/seektalent/config.py`
- Modify: `.env.example`
- Modify: `src/seektalent/default.env`

- [x] **Step 1: Add failing config tests**

Create or append to `tests/test_liepin_config.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from seektalent.config import AppSettings


def test_pi_agent_requires_rpc_command_skill_and_dokobot_tool(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("skill", encoding="utf-8")

    settings = AppSettings(
        liepin_worker_mode="pi_agent",
        liepin_pi_command="pi --mode rpc --no-session",
        liepin_pi_skill_path=str(skill_path),
        liepin_pi_dokobot_tool_name="dokobot",
    )

    assert settings.liepin_worker_mode == "pi_agent"
    assert settings.liepin_pi_command_argv[-2:] == ("--skill", str(skill_path))


def test_pi_agent_rejects_missing_rpc_no_session_or_skill(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("skill", encoding="utf-8")

    with pytest.raises(ValueError):
        AppSettings(
            liepin_worker_mode="pi_agent",
            liepin_pi_command="pi --mode json --no-session",
            liepin_pi_skill_path=str(skill_path),
            liepin_pi_dokobot_tool_name="dokobot",
        )


def test_dokobot_action_is_not_a_live_worker_mode() -> None:
    with pytest.raises(ValueError, match="dokobot_action"):
        AppSettings(liepin_worker_mode="dokobot_action")
```

- [x] **Step 2: Run config tests to confirm failure**

Run:

```bash
uv run pytest tests/test_liepin_config.py -q
```

Expected: failure before config is updated.

- [x] **Step 3: Update settings fields and validation**

In `src/seektalent/config.py`:

```python
LiepinWorkerMode = Literal["disabled", "fake_fixture", "managed_local", "external_http", "pi_agent"]

liepin_pi_command: str | None = None
liepin_pi_timeout_seconds: int = 120
liepin_pi_skill_path: str | None = None
liepin_pi_dokobot_tool_name: str | None = None

@property
def liepin_pi_command_argv(self) -> tuple[str, ...]:
    from pathlib import Path

    from seektalent.providers.pi_agent.pi_external import build_pi_rpc_argv

    if self.liepin_worker_mode != "pi_agent":
        return ()
    if not self.liepin_pi_command:
        raise ValueError("liepin_pi_command is required when liepin_worker_mode=pi_agent")
    if not self.liepin_pi_skill_path:
        raise ValueError("liepin_pi_skill_path is required when liepin_worker_mode=pi_agent")
    if not self.liepin_pi_dokobot_tool_name:
        raise ValueError("liepin_pi_dokobot_tool_name is required when liepin_worker_mode=pi_agent")
    return build_pi_rpc_argv(self.liepin_pi_command, skill_path=self.liepin_pi_skill_file_path)
```

Add a resolved property so CLI/app cwd changes do not break relative skill paths:

```python
@property
def liepin_pi_skill_file_path(self) -> Path:
    if not self.liepin_pi_skill_path:
        raise ValueError("liepin_pi_skill_path is required when liepin_worker_mode=pi_agent")
    path = Path(self.liepin_pi_skill_path).expanduser()
    return path if path.is_absolute() else self.resolve_workspace_path(self.liepin_pi_skill_path)
```

Inside existing `validate_liepin_worker_config()`, validate `pi_agent` at model construction time by forcing the argv property to parse:

```python
if self.liepin_worker_mode == "pi_agent":
    _ = self.liepin_pi_command_argv
```

Do not add a production source branch comparing `self.liepin_worker_mode` to the removed old value. Removing it from `LiepinWorkerMode` already makes Pydantic reject that value, and the final static guard requires no old-mode token in `src/seektalent`.

Remove obsolete settings that existed only for the old live route once no retained code needs them:

- `liepin_dokobot_action_manifest_path`
- `liepin_dokobot_trusted_manifest_ids`
- their empty-string and tuple normalizers
- their `validate_liepin_worker_config()` branches

- [x] **Step 4: Update env docs**

In `.env.example` and `src/seektalent/default.env`, document:

```text
# Liepin worker mode: disabled | fake_fixture | managed_local | external_http | pi_agent
# SEEKTALENT_LIEPIN_WORKER_MODE=pi_agent
# SEEKTALENT_LIEPIN_PI_COMMAND=pi --mode rpc --no-session
# SEEKTALENT_LIEPIN_PI_SKILL_PATH=src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md
# SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME=dokobot
# SEEKTALENT_LIEPIN_PI_TIMEOUT_SECONDS=120
```

- [x] **Step 5: Verify config tests pass**

Run:

```bash
uv run pytest tests/test_liepin_config.py -q
```

Expected: all config tests pass.

### Task 8: Replace Live Worker Factory With Pi Executor

**Files:**
- Modify: `tests/test_liepin_worker_client.py`
- Modify: `tests/test_liepin_pi_worker_client.py`
- Modify: `src/seektalent/providers/liepin/client.py`
- Modify: `src/seektalent/providers/liepin/pi_worker_client.py`

- [x] **Step 1: Add failing factory tests**

In `tests/test_liepin_worker_client.py`, add:

```python
def test_build_liepin_worker_client_uses_pi_agent_executor(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("skill", encoding="utf-8")
    settings = make_settings(
        liepin_worker_mode="pi_agent",
        liepin_pi_command="pi --mode rpc --no-session",
        liepin_pi_skill_path=str(skill_path),
        liepin_pi_dokobot_tool_name="dokobot",
    )

    client = build_liepin_worker_client(settings)

    assert client.__class__.__name__ == "LiepinPiWorkerClient"
```

In `tests/test_liepin_pi_worker_client.py`, add:

```python
class FakePiLiepinExecutor:
    def __init__(self, *, search_result=None, capability_ready: bool = True) -> None:
        self.search_result = search_result
        self.capability_ready = capability_ready

    def probe_capabilities(self, *, expected_dokobot_tool_name: str):
        del expected_dokobot_tool_name
        return PiLiepinCapabilityProbeResult(
            ready=self.capability_ready,
            safe_reason_code=None if self.capability_ready else "blocked_backend_unavailable",
        )


async def test_pi_worker_client_maps_blocked_capability_to_worker_error() -> None:
    executor = FakePiLiepinExecutor(search_result=None, capability_ready=False)
    client = LiepinPiWorkerClient(
        executor=executor,
        session_id="session-1",
        connection_id="conn-1",
        provider_account_lock_key="acct-1",
    )

    with pytest.raises(LiepinWorkerModeError, match="not ready"):
        await client.ensure_ready()
```

- [x] **Step 2: Run worker tests to confirm failure**

Run:

```bash
uv run pytest tests/test_liepin_worker_client.py tests/test_liepin_pi_worker_client.py -q
```

Expected: failure before factory and client are updated.

- [x] **Step 3: Update worker client to depend on `PiLiepinExecutor`**

In `src/seektalent/providers/liepin/pi_worker_client.py`, make the constructor explicit:

```python
class LiepinPiWorkerClient:
    def __init__(
        self,
        *,
        executor: PiLiepinExecutor,
        session_id: str,
        connection_id: str,
        provider_account_lock_key: str,
        dokobot_tool_name: str = "dokobot",
    ) -> None:
        self._executor = executor
        self._session_id = session_id
        self._connection_id = connection_id
        self._provider_account_lock_key = provider_account_lock_key
        self._dokobot_tool_name = dokobot_tool_name
```

`ensure_ready()` must check the Pi/DokoBot capability gate before live search:

```python
async def ensure_ready(self, *, on_event=None) -> None:
    del on_event
    capability = await asyncio.to_thread(
        self._executor.probe_capabilities,
        expected_dokobot_tool_name=self._dokobot_tool_name,
    )
    if not capability.ready:
        raise LiepinWorkerModeError(
            "Liepin PI worker is not ready.",
            code=capability.safe_reason_code or "blocked_backend_unavailable",
        )
```

Keep async search boundaries by running synchronous executor calls in `asyncio.to_thread()`:

```python
result = await asyncio.to_thread(
    self._executor.search_cards,
    source_run_id=trace_id,
    keyword_query=request.keyword_query or " ".join(request.query_terms),
    query_terms=tuple(request.query_terms),
    page_size=request.page_size,
    max_pages=_positive_int(request.provider_context.get("liepin_max_pages"), default=1),
    max_cards=_positive_int(request.provider_context.get("liepin_max_cards"), default=request.page_size),
)
```

- [x] **Step 4: Update factory to build Pi RPC client and executor**

In `src/seektalent/providers/liepin/client.py`, replace the `dokobot_action` construction path with:

```python
def build_liepin_worker_client(settings: AppSettings) -> LiepinWorkerClient:
    if settings.liepin_worker_mode == "fake_fixture":
        return FakeLiepinWorkerClient(settings)
    if settings.liepin_worker_mode == "managed_local":
        return ManagedLocalLiepinWorkerClient(settings)
    if settings.liepin_worker_mode == "external_http":
        return ExternalHttpLiepinWorkerClient(settings)
    if settings.liepin_worker_mode == "pi_agent":
        return build_liepin_pi_worker_client(settings)
    raise LiepinWorkerModeError(
        "Liepin worker mode is disabled; no worker client can be built.",
        setup_status="disabled",
    )


def build_liepin_pi_worker_client(settings: AppSettings) -> LiepinPiWorkerClient:
    from pathlib import Path

    from seektalent.providers.liepin.pi_executor import HmacProviderKeyHasher, PiLiepinExecutor
    from seektalent.providers.liepin.pi_worker_client import LiepinPiWorkerClient
    from seektalent.providers.pi_agent.payload_firewall import LocalPiArtifactRegistry
    from seektalent.providers.pi_agent.pi_external import PiRpcAgentClient

    skill_path = settings.liepin_pi_skill_file_path
    artifact_registry = LocalPiArtifactRegistry(settings.artifacts_path)
    connection_id = "liepin-pi-agent"
    dokobot_tool_name = settings.liepin_pi_dokobot_tool_name or "dokobot"
    pi_client = PiRpcAgentClient(
        command=settings.liepin_pi_command_argv,
        skill_path=skill_path,
        dokobot_tool_name=dokobot_tool_name,
        timeout_seconds=settings.liepin_pi_timeout_seconds,
        artifact_root=artifact_registry.artifact_root_for_pi,
    )
    executor = PiLiepinExecutor(
        client=pi_client,
        key_hasher=HmacProviderKeyHasher(
            settings.liepin_account_binding_secret or "local-development",
            material_resolver=artifact_registry,
        ),
        artifact_registry=artifact_registry,
    )
    return LiepinPiWorkerClient(
        executor=executor,
        session_id="local-pi-agent",
        connection_id=connection_id,
        provider_account_lock_key=connection_id,
        dokobot_tool_name=dokobot_tool_name,
    )
```

The repository already uses `liepin_account_binding_secret` for Liepin account binding hashes. Reuse that setting for provider candidate/account HMACs in this slice. Do not pass the secret to Pi or include it in prompts.

- [x] **Step 5: Verify worker factory tests pass**

Run:

```bash
uv run pytest tests/test_liepin_worker_client.py tests/test_liepin_pi_worker_client.py -q
```

Expected: all updated worker tests pass.

### Task 9: Wire Runtime, Registry, Adapter, CLI, And Source-Lane Posture

**Files:**
- Modify: `tests/test_provider_registry.py`
- Modify: `tests/test_liepin_provider_adapter.py`
- Modify: `tests/test_liepin_runtime_source_lane.py`
- Modify: `tests/test_liepin_cli.py`
- Modify: `src/seektalent/providers/registry.py`
- Modify: `src/seektalent/providers/liepin/adapter.py`
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Modify: `src/seektalent/runtime/source_lanes.py`
- Modify: `src/seektalent/cli.py`

- [x] **Step 1: Add or update failing integration tests for `pi_agent` live posture**

In `tests/test_liepin_runtime_source_lane.py`, replace old `dokobot_action` posture assertions with:

```python
def test_liepin_backend_posture_records_pi_agent_as_live_mode(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("skill", encoding="utf-8")
    settings = make_settings(
        liepin_worker_mode="pi_agent",
        liepin_pi_command="pi --mode rpc --no-session",
        liepin_pi_skill_path=str(skill_path),
        liepin_pi_dokobot_tool_name="dokobot",
    )

    assert liepin_backend_posture(settings) == {"backend_mode": "pi_agent", "reason": "pi_agent"}
```

In `tests/test_provider_registry.py`, add:

```python
def test_provider_registry_treats_pi_agent_as_live_liepin_mode(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("skill", encoding="utf-8")
    worker = object()
    settings = make_settings(
        provider_name="cts",
        liepin_worker_mode="pi_agent",
        liepin_connector_db_path=str(tmp_path / "liepin.sqlite3"),
        liepin_pi_command="pi --mode rpc --no-session",
        liepin_pi_skill_path=str(skill_path),
        liepin_pi_dokobot_tool_name="dokobot",
    )

    adapter = get_provider_adapter_for_source(settings, "liepin", liepin_worker_client=worker)

    assert adapter.name == "liepin"
    assert adapter.worker_client is worker
```

In `tests/test_liepin_cli.py`, add:

```python
def test_liepin_smoke_preserves_explicit_pi_agent_mode(monkeypatch, tmp_path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("skill", encoding="utf-8")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_WORKER_MODE", "pi_agent")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_PI_COMMAND", "pi --mode rpc --no-session")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_PI_SKILL_PATH", str(skill_path))
    monkeypatch.setenv("SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME", "dokobot")

    settings = make_settings()

    assert settings.liepin_worker_mode == "pi_agent"
```

- [x] **Step 2: Run integration tests to confirm failure**

Run:

```bash
uv run pytest tests/test_provider_registry.py tests/test_liepin_provider_adapter.py tests/test_liepin_runtime_source_lane.py tests/test_liepin_cli.py -q
```

Expected: failures where code still expects `dokobot_action` or does not know `pi_agent`.

- [x] **Step 3: Update production mapping**

Make these concrete changes:

- In `src/seektalent/providers/registry.py`, include `pi_agent` in live Liepin source handling and require `LiepinStore`.
- In `src/seektalent/providers/liepin/adapter.py`, route `pi_agent` through the same compliance/session safety gate as existing live modes.
- In `src/seektalent/providers/liepin/runtime_lane.py`, map Pi stop reasons to Runtime safe reason codes and report `backend_mode="pi_agent"`.
- In `src/seektalent/runtime/source_lanes.py`, remove `dokobot_action` posture branches and add `pi_agent`.
- In `src/seektalent/cli.py`, preserve explicit `pi_agent`; do not coerce it to `managed_local`.

- [x] **Step 4: Verify integration tests pass**

Run:

```bash
uv run pytest tests/test_provider_registry.py tests/test_liepin_provider_adapter.py tests/test_liepin_runtime_source_lane.py tests/test_liepin_cli.py -q
```

Expected: all updated integration tests pass.

### Task 10: Remove Old Live DokoBot Action Surface

**Files:**
- Delete: `src/seektalent/providers/liepin/pi_runner.py`
- Delete: `src/seektalent/providers/liepin/dokobot_actions.py`
- Delete: `src/seektalent/providers/pi_agent/dokobot_action_transport.py`
- Delete: `tests/test_liepin_pi_runner.py`
- Delete: `tests/test_liepin_dokobot_actions.py`
- Delete or rewrite: `tests/test_liepin_pi_card_search_result.py`
- Modify: tests that imported deleted modules
- Modify: `src/seektalent/providers/pi_agent/contracts.py`
- Modify: `src/seektalent/providers/pi_agent/capabilities.py`
- Modify: `tests/test_dokobot_capabilities.py`
- Modify: `src/seektalent/config.py`

- [x] **Step 1: Delete obsolete files after imports are migrated**

Run:

```bash
git rm src/seektalent/providers/liepin/pi_runner.py \
  src/seektalent/providers/liepin/dokobot_actions.py \
  src/seektalent/providers/pi_agent/dokobot_action_transport.py \
  tests/test_liepin_pi_runner.py \
  tests/test_liepin_dokobot_actions.py \
  tests/test_liepin_pi_card_search_result.py
```

Expected: files are staged for deletion. If `git rm` reports a file is untracked, use `rm <path>` for that file and continue.

Then remove or rename old-route symbols that would keep production code tied to the deleted premise:

- In `src/seektalent/providers/pi_agent/contracts.py`, remove `PiBackendMode` values for the old route and replace `DOKOBOT_ACTION_CAPABILITY_UNAVAILABLE` with a Pi-neutral failure name such as `DOKOBOT_TOOL_CAPABILITY_UNAVAILABLE`.
- In `src/seektalent/providers/pi_agent/capabilities.py`, keep trusted capability-manifest semantics only if still useful for Pi-internal proof, but rename public error strings away from old-route names, for example `dokobot_tool_manifest_untrusted`.
- In `tests/test_dokobot_capabilities.py`, remove imports and assertions tied to deleted action-transport classes. Keep or rewrite tests only for Pi-internal capability-manifest parsing/proof semantics, and update stale error-string assertions to the new Pi-neutral names.
- In `src/seektalent/config.py`, remove old-route settings and validation branches so the final source guard has no old-route token to match.
- Update any remaining tests to use `tests/test_liepin_pi_executor.py` and `tests/test_liepin_pi_worker_client.py` as the Pi-first coverage surface.

- [x] **Step 2: Add a static guard test**

Create or append to `tests/test_liepin_pi_executor.py`:

```python
def test_runtime_and_workbench_do_not_import_old_dokobot_action_surface() -> None:
    import subprocess

    result = subprocess.run(
        [
            "rg",
            "-n",
            "DokoBotActionSurface|DokoBotActionTransportSession|DokoBotLiepinSearchCardsExecutor|DOKOBOT_ACTION|LEGACY_WORKER_COMPAT|dokobot_action",
            "src/seektalent",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1, result.stdout
```

- [x] **Step 3: Run static guard**

Run:

```bash
uv run pytest tests/test_liepin_pi_executor.py::test_runtime_and_workbench_do_not_import_old_dokobot_action_surface -q
```

Expected: `1 passed`.

### Task 11: Update Deferred Platform Follow-Ups

**Files:**
- Modify: `TODOS.md`

- [x] **Step 1: Add deferred Pi provider executor platform items**

Under `Runtime Multi-Source Platform Follow-Ups`, add any missing deferred items from this list. Preserve existing entries and do not duplicate them:

```markdown
- Generic ProviderAgentExecutor protocol: extract a reusable provider-agent executor interface after the Liepin Pi path proves stable across real runs.
- Full Pi/DokoBot trace replay harness: store replayable protected golden traces for browser-agent regression testing beyond the first card-mode trace validator.
- Multi-action backend support: evaluate browser_mcp, pi extension browser tools, and human-assisted mode only after the Pi+DokoBot path is stable; do not add fallback selection in the first implementation.
- Artifact registry expansion for external agents: add retention, protected-open audit, and UI access policy for Pi/DokoBot protected artifacts beyond the first minimal local registry and protected material resolver.
- Provider-agent capability descriptor: define a small source capability descriptor once at least one more browser source is planned; avoid building a plugin marketplace.
```

- [x] **Step 2: Verify the follow-up section has one Pi executor entry**

Run:

```bash
rg -n "Generic ProviderAgentExecutor protocol|Full Pi/DokoBot trace replay harness|Multi-action backend support|Artifact registry expansion for external agents|Provider-agent capability descriptor" TODOS.md
```

Expected: each item appears once.

### Task 12: Full Verification

**Files:**
- All modified files

- [x] **Step 1: Run focused tests**

Run:

```bash
uv run pytest tests/test_pi_external_agent.py tests/test_pi_payload_firewall.py tests/test_liepin_pi_executor.py tests/test_liepin_pi_worker_client.py tests/test_liepin_worker_client.py tests/test_dokobot_capabilities.py tests/test_provider_registry.py tests/test_liepin_provider_adapter.py tests/test_liepin_runtime_source_lane.py tests/test_liepin_cli.py tests/test_liepin_config.py -q
```

Expected: all focused tests pass.

- [x] **Step 2: Run ruff**

Run:

```bash
uv run ruff check src/seektalent/providers/pi_agent/pi_external.py src/seektalent/providers/pi_agent/payload_firewall.py src/seektalent/providers/pi_agent/contracts.py src/seektalent/providers/pi_agent/capabilities.py src/seektalent/providers/liepin/pi_executor.py src/seektalent/providers/liepin/pi_worker_client.py src/seektalent/providers/liepin/client.py src/seektalent/config.py tests/test_pi_external_agent.py tests/test_pi_payload_firewall.py tests/test_liepin_pi_executor.py tests/test_liepin_pi_worker_client.py tests/test_liepin_worker_client.py tests/test_dokobot_capabilities.py tests/test_liepin_config.py
```

Expected: no violations.

- [x] **Step 3: Run static stale-path scan**

Run:

```bash
rg -n "DokoBotActionSurface|DokoBotActionTransportSession|DokoBotLiepinSearchCardsExecutor|DOKOBOT_ACTION|LEGACY_WORKER_COMPAT|dokobot_action" src/seektalent
```

Expected: no output.

- [x] **Step 4: Run plan/doc hygiene checks**

Run:

```bash
rg -n "[T]BD|implement [l]ater|fill in [d]etails|Similar to [T]ask|Write tests for the [a]bove|Use the real local [h]elper names|add [a]ppropriate|handle edge [c]ases" docs/superpowers/specs/2026-05-17-pi-first-liepin-agent-executor-design.md docs/superpowers/plans/2026-05-17-pi-first-liepin-agent-executor.md | rg -v "rg -n"
git diff --check -- docs/superpowers/specs/2026-05-17-pi-first-liepin-agent-executor-design.md docs/superpowers/plans/2026-05-17-pi-first-liepin-agent-executor.md TODOS.md
```

Expected: `rg` prints no output and `git diff --check` prints no output.

## Plan Self-Review

- Spec coverage: Pi RPC, skill loading, DokoBot capability proof, strict JSON, SafePayloadFirewall, Runtime-owned hashes, session privacy, card-mode trace validation, no fallback, old skeleton cleanup, and Runtime merge preservation are each covered by a task.
- Type consistency: `PiRpcAgentClient`, `PiRpcTaskResult`, `PiLiepinExecutor`, `LiepinPiCardSearchResult`, `safe_reason_code`, `ProviderKeyHasher`, `HmacProviderKeyHasher`, `ArtifactRefRegistry`, and probe result names are introduced before later tasks use them.
- Placeholder scan: the plan avoids the blocked phrases from the writing-plans checklist and includes concrete tests, paths, commands, and expected results.
- Execution recommendation: use Subagent-Driven implementation for this plan because the write sets are separable: RPC boundary, payload firewall, executor models, config/factory, Runtime wiring, and cleanup can be reviewed task by task.
