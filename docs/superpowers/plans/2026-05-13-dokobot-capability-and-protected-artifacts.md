# DokoBot Capability And Protected Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add DokoBot capability negotiation, structured read results, and protected artifact boundaries without enabling live Liepin actions unless a DokoBot-compatible action manifest is already explicitly available.

**Architecture:** Treat `dokobot read` as a read-only snapshot backend. Only an explicit DokoBot-compatible action tool manifest can set click, text-entry, navigation, and pagination capabilities true. The probe must not attempt tool installation, permission mutation, or backend downgrade. Raw provider output is stored behind protected artifact refs; ordinary UI can only receive safe summary artifacts.

**Tech Stack:** Python 3.12, Pydantic v2, DokoBot CLI, explicit DokoBot-compatible action tool manifest, pytest.

**Spec:** `docs/superpowers/specs/2026-05-13-provider-interaction-agent-dokobot-design.md`

**Depends On:** `docs/superpowers/plans/2026-05-13-pi-agent-contracts-and-skill-recipes.md`

The dependency must already provide `PiArtifactRef.protection_policy_id` and the artifact-class policy validator from Plan 1. This plan must reuse that typed artifact ref; it must not introduce string-only artifact handles for DokoBot reads or command errors.

---

## File Structure

- Add: `src/seektalent/providers/pi_agent/capabilities.py`
  - DokoBot capability probe and fail-closed action capability checks.
- Add: `src/seektalent/providers/pi_agent/dokobot_client.py`
  - DokoBot read command boundary returning structured read results and redacted command output refs.
- Modify: `src/seektalent/providers/pi_agent/contracts.py`
  - Add `DokoBotReadResult` using typed `PiArtifactRef` fields.
- Add: `src/seektalent/providers/pi_agent/artifacts.py`
  - Safe summary, redacted evidence, and protected provider snapshot refs plus UI-safe assertions.
- Test: `tests/test_dokobot_capabilities.py`
  - Read-only CLI, no-install/no-downgrade, and explicit action manifest tests.
- Test: `tests/test_pi_agent_artifacts.py`
  - UI leakage guard tests.
- Test: `tests/test_pi_agent_contracts.py`
  - DokoBot read-result schema coverage.

### Task 1: Add DokoBot Capability Probe And Structured Read Result

**Files:**
- Create: `src/seektalent/providers/pi_agent/capabilities.py`
- Create: `src/seektalent/providers/pi_agent/dokobot_client.py`
- Modify: `src/seektalent/providers/pi_agent/contracts.py`
- Test: `tests/test_dokobot_capabilities.py`
- Test: `tests/test_pi_agent_contracts.py`

- [x] **Step 1: Write failing capability and read-result tests**

Add `tests/test_dokobot_capabilities.py`:

```python
import json
import subprocess
from hashlib import sha256
from subprocess import CompletedProcess

import pytest

from seektalent.providers.pi_agent.capabilities import DokoBotActionToolManifest, DokoBotCapabilityProbe
from seektalent.providers.pi_agent.contracts import PiArtifactRef, ProtectedArtifactClass
from seektalent.providers.pi_agent.dokobot_client import DokoBotClient, DokoBotExecutionError


def _artifact_ref(content: bytes, artifact_class: ProtectedArtifactClass, policy_id: str) -> PiArtifactRef:
    return PiArtifactRef(
        artifact_class=artifact_class,
        artifact_ref=f"{artifact_class.value}:{sha256(content).hexdigest()}",
        content_sha256=sha256(content).hexdigest(),
        redaction_policy_id=(
            policy_id
            if artifact_class
            in {ProtectedArtifactClass.SAFE_SUMMARY, ProtectedArtifactClass.REDACTED_EVIDENCE}
            else None
        ),
        protection_policy_id=policy_id if artifact_class == ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT else None,
    )


def test_capability_probe_marks_public_cli_as_read_only() -> None:
    def fake_runner(command: list[str]) -> CompletedProcess[str]:
        command_text = " ".join(command)
        if command_text == "dokobot --version":
            return CompletedProcess(command, 0, "2.11.0\n", "")
        if command_text == "dokobot --help":
            return CompletedProcess(command, 0, "Commands:\n  read\n  search\n  close\n", "")
        if command_text == "dokobot read --help":
            return CompletedProcess(command, 0, "--format <type> text or chunks\n--session-id <id>\n--screens <n>\n", "")
        return CompletedProcess(command, 1, "", "unexpected command")

    capabilities = DokoBotCapabilityProbe(run_command=fake_runner).discover()

    assert capabilities.cli_version == "2.11.0"
    assert capabilities.supports_read is True
    assert capabilities.supports_chunks_format is True
    assert capabilities.supports_session_continuation is True
    assert capabilities.can_execute_liepin_actions is False


def test_probe_does_not_attempt_action_tool_install_or_downgrade() -> None:
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> CompletedProcess[str]:
        calls.append(command)
        command_text = " ".join(command)
        if command_text == "dokobot --version":
            return CompletedProcess(command, 0, "2.11.0\n", "")
        if command_text == "dokobot --help":
            return CompletedProcess(command, 0, "Commands:\n  read\n  search\n  close\n", "")
        if command_text == "dokobot read --help":
            return CompletedProcess(command, 0, "--format <type> text or chunks\n--session-id <id>\n--screens <n>\n", "")
        return CompletedProcess(command, 1, "", "unexpected command")

    capabilities = DokoBotCapabilityProbe(run_command=fake_runner).discover()

    assert capabilities.can_execute_liepin_actions is False
    command_texts = [" ".join(command) for command in calls]
    assert all("install" not in text for text in command_texts)
    assert all("legacy" not in text for text in command_texts)
    assert all("downgrade" not in text for text in command_texts)
    assert all("config" not in text for text in command_texts)
    assert all("update" not in text for text in command_texts)


def test_action_manifest_can_enable_text_input_actions() -> None:
    def fake_runner(command: list[str]) -> CompletedProcess[str]:
        command_text = " ".join(command)
        if command_text == "dokobot --version":
            return CompletedProcess(command, 0, "2.11.0\n", "")
        if command_text == "dokobot --help":
            return CompletedProcess(command, 0, "Commands:\n  read\n  search\n  close\n", "")
        if command_text == "dokobot read --help":
            return CompletedProcess(command, 0, "--format <type> text or chunks\n--session-id <id>\n--screens <n>\n", "")
        return CompletedProcess(command, 1, "", "unexpected command")

    manifest = DokoBotActionToolManifest(
        manifest_id="dokobot-mcp-browser-tools",
        manifest_version="2026-05-14",
        provider="dokobot_compatible",
        enabled_tools=("click", "fill", "type_text", "navigate", "turn_page"),
    )
    capabilities = DokoBotCapabilityProbe(run_command=fake_runner, action_tool_manifest=manifest).discover()

    assert capabilities.action_manifest_id == "dokobot-mcp-browser-tools"
    assert capabilities.action_manifest_version == "2026-05-14"
    assert capabilities.action_manifest_tools == ("click", "fill", "type_text", "navigate", "turn_page")
    assert capabilities.supports_click is True
    assert capabilities.supports_type is True
    assert capabilities.supports_navigation is True
    assert capabilities.supports_pagination_action is True
    assert capabilities.can_execute_liepin_actions is True


def test_probe_fails_closed_when_dokobot_cli_is_missing() -> None:
    def fake_runner(command: list[str]) -> CompletedProcess[str]:
        raise FileNotFoundError("dokobot")

    capabilities = DokoBotCapabilityProbe(run_command=fake_runner).discover()

    assert capabilities.cli_version == "unknown"
    assert capabilities.supports_read is False
    assert capabilities.can_execute_liepin_actions is False
    assert capabilities.capability_error_code == "dokobot_cli_unavailable"


def test_probe_fails_closed_when_help_command_fails() -> None:
    def fake_runner(command: list[str]) -> CompletedProcess[str]:
        if command == ["dokobot", "--version"]:
            return CompletedProcess(command, 0, "2.11.0\n", "")
        return CompletedProcess(command, 1, "", "Bearer secret-token")

    capabilities = DokoBotCapabilityProbe(run_command=fake_runner).discover()

    assert capabilities.cli_version == "2.11.0"
    assert capabilities.supports_read is False
    assert capabilities.can_execute_liepin_actions is False
    assert capabilities.capability_error_code == "dokobot_capability_probe_failed"


def test_probe_fails_closed_when_help_command_times_out() -> None:
    def fake_runner(command: list[str]) -> CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, timeout=10)

    capabilities = DokoBotCapabilityProbe(run_command=fake_runner).discover()

    assert capabilities.supports_read is False
    assert capabilities.can_execute_liepin_actions is False
    assert capabilities.capability_error_code == "dokobot_capability_probe_timeout"


def test_read_url_returns_structured_text_result() -> None:
    calls: list[list[str]] = []
    written: list[tuple[bytes, ProtectedArtifactClass, str]] = []

    def fake_runner(command: list[str], process_timeout_seconds: int) -> CompletedProcess[str]:
        calls.append(command)
        assert process_timeout_seconds == 40
        return CompletedProcess(command, 0, "Candidate summary text", "")

    def fake_writer(content: bytes, artifact_class: ProtectedArtifactClass, policy_id: str) -> PiArtifactRef:
        written.append((content, artifact_class, policy_id))
        return _artifact_ref(content, artifact_class, policy_id)

    client = DokoBotClient(run_command=fake_runner, artifact_writer=fake_writer)
    result = client.read_url("https://www.liepin.com/zhaopin/", screens=2)

    assert result.schema_version == "dokobot-read-result-v1"
    assert str(result.url) == "https://www.liepin.com/zhaopin/"
    assert "--reuse-tab" not in calls[0]
    assert "--screens" in calls[0]
    assert "2" in calls[0]
    assert result.text_ref is not None
    assert result.text_ref.artifact_class == ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT
    assert result.text_ref.protection_policy_id == "liepin-protected-snapshot-v1"
    assert result.session_id is None
    assert result.vertical_stop_reason == "unknown"
    assert written == [
        (
            b"Candidate summary text",
            ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT,
            "liepin-protected-snapshot-v1",
        )
    ]


def test_read_url_can_enable_reuse_tab_explicitly() -> None:
    calls: list[list[str]] = []

    def fake_runner(command: list[str], process_timeout_seconds: int) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(command, 0, "Candidate summary text", "")

    def fake_writer(content: bytes, artifact_class: ProtectedArtifactClass, policy_id: str) -> PiArtifactRef:
        return _artifact_ref(content, artifact_class, policy_id)

    client = DokoBotClient(run_command=fake_runner, artifact_writer=fake_writer)
    client.read_url("https://www.liepin.com/zhaopin/", reuse_tab=True)

    assert "--reuse-tab" in calls[0]


def test_read_url_parses_session_id_from_success_stderr() -> None:
    written: list[tuple[bytes, ProtectedArtifactClass, str]] = []

    def fake_runner(command: list[str], process_timeout_seconds: int) -> CompletedProcess[str]:
        return CompletedProcess(command, 0, "Candidate summary text", "Session: sess_abc\n")

    def fake_writer(content: bytes, artifact_class: ProtectedArtifactClass, policy_id: str) -> PiArtifactRef:
        written.append((content, artifact_class, policy_id))
        return _artifact_ref(content, artifact_class, policy_id)

    client = DokoBotClient(run_command=fake_runner, artifact_writer=fake_writer)
    result = client.read_url("https://www.liepin.com/zhaopin/", screens=5)

    assert result.session_id == "sess_abc"
    assert result.vertical_has_more is True
    assert result.screens_used == 5
    assert result.stderr_redacted_ref is not None
    assert result.stderr_redacted_ref.artifact_class == ProtectedArtifactClass.REDACTED_EVIDENCE
    assert (
        b"Session: [REDACTED_SESSION]",
        ProtectedArtifactClass.REDACTED_EVIDENCE,
        "dokobot-command-error-redaction-v1",
    ) in written


def test_read_url_does_not_parse_public_cli_stdout_as_json_without_json_surface() -> None:
    def fake_runner(command: list[str], process_timeout_seconds: int) -> CompletedProcess[str]:
        payload = {"text": "Candidate summary text", "chunks": [{"text": "Candidate summary text"}]}
        return CompletedProcess(command, 0, json.dumps(payload), "")

    def fake_writer(content: bytes, artifact_class: ProtectedArtifactClass, policy_id: str) -> PiArtifactRef:
        return _artifact_ref(content, artifact_class, policy_id)

    client = DokoBotClient(run_command=fake_runner, artifact_writer=fake_writer)
    result = client.read_url("https://www.liepin.com/zhaopin/", output_format="chunks")

    assert result.text_ref is not None
    assert result.chunks_ref is None
    assert result.session_id is None


def test_read_url_parses_json_payload_from_explicit_json_capable_surface() -> None:
    written: list[tuple[bytes, ProtectedArtifactClass, str]] = []

    def fake_runner(command: list[str], process_timeout_seconds: int) -> CompletedProcess[str]:
        payload = {
            "data": {
                "text": "Candidate summary text",
                "chunks": [{"text": "Candidate summary text", "bbox": [0, 0, 100, 20]}],
            },
            "sessionId": "doko_session_1",
            "vertical": {"hasMore": True, "stopReason": "limit_reached"},
            "screens": 5,
        }
        return CompletedProcess(command, 0, json.dumps(payload), "")

    def fake_writer(content: bytes, artifact_class: ProtectedArtifactClass, policy_id: str) -> PiArtifactRef:
        written.append((content, artifact_class, policy_id))
        return _artifact_ref(content, artifact_class, policy_id)

    client = DokoBotClient(run_command=fake_runner, artifact_writer=fake_writer)
    result = client.read_url(
        "https://www.liepin.com/zhaopin/",
        output_format="chunks",
        json_capable_surface=True,
    )

    assert result.text_ref is not None
    assert result.chunks_ref is not None
    assert result.text_ref.artifact_class == ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT
    assert result.chunks_ref.artifact_class == ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT
    assert result.session_id == "doko_session_1"
    assert result.vertical_has_more is True
    assert result.vertical_stop_reason == "limit_reached"
    assert result.screens_used == 5
    assert [artifact_class for _, artifact_class, _ in written] == [
        ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT,
        ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT,
    ]


def test_failed_read_does_not_store_stdout_as_redacted_evidence() -> None:
    written: list[tuple[bytes, ProtectedArtifactClass, str]] = []

    def fake_runner(command: list[str], process_timeout_seconds: int) -> CompletedProcess[str]:
        return CompletedProcess(command, 1, "candidate 张三 13800138000", "Bearer secret-token")

    def fake_writer(content: bytes, artifact_class: ProtectedArtifactClass, policy_id: str) -> PiArtifactRef:
        written.append((content, artifact_class, policy_id))
        return _artifact_ref(content, artifact_class, policy_id)

    client = DokoBotClient(run_command=fake_runner, artifact_writer=fake_writer)

    with pytest.raises(DokoBotExecutionError) as error:
        client.read_url("https://www.liepin.com/")

    assert "secret-token" not in str(error.value)
    assert "张三" not in str(error.value)
    assert "13800138000" not in str(error.value)
    assert error.value.error_code == "dokobot_read_failed"
    assert error.value.stderr_redacted_ref is not None
    assert error.value.stderr_redacted_ref.artifact_class == ProtectedArtifactClass.REDACTED_EVIDENCE
    assert written == [
        (
            b"Bearer [REDACTED]",
            ProtectedArtifactClass.REDACTED_EVIDENCE,
            "dokobot-command-error-redaction-v1",
        )
    ]
    assert "张三".encode("utf-8") not in written[0][0]
    assert b"13800138000" not in written[0][0]
    assert b"secret-token" not in written[0][0]


def test_read_url_fails_closed_when_cli_process_times_out() -> None:
    written: list[tuple[bytes, ProtectedArtifactClass, str]] = []

    def fake_runner(command: list[str], process_timeout_seconds: int) -> CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, timeout=process_timeout_seconds)

    def fake_writer(content: bytes, artifact_class: ProtectedArtifactClass, policy_id: str) -> PiArtifactRef:
        written.append((content, artifact_class, policy_id))
        return _artifact_ref(content, artifact_class, policy_id)

    client = DokoBotClient(run_command=fake_runner, artifact_writer=fake_writer)

    with pytest.raises(DokoBotExecutionError) as error:
        client.read_url("https://www.liepin.com/", timeout_seconds=30)

    assert error.value.error_code == "dokobot_read_timeout"
    assert error.value.stderr_redacted_ref is not None
    assert error.value.stderr_redacted_ref.artifact_class == ProtectedArtifactClass.REDACTED_EVIDENCE
    assert written == [
        (
            b"dokobot read timed out",
            ProtectedArtifactClass.REDACTED_EVIDENCE,
            "dokobot-command-error-redaction-v1",
        )
    ]


def test_default_artifact_writer_is_not_fake_persistent_storage() -> None:
    def fake_runner(command: list[str], process_timeout_seconds: int) -> CompletedProcess[str]:
        return CompletedProcess(command, 0, "Candidate summary text", "")

    client = DokoBotClient(run_command=fake_runner)

    with pytest.raises(RuntimeError, match="artifact_writer is required"):
        client.read_url("https://www.liepin.com/zhaopin/")
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_dokobot_capabilities.py tests/test_pi_agent_contracts.py -q
```

Expected: import failure for `seektalent.providers.pi_agent.capabilities` / `dokobot_client` or missing `DokoBotReadResult`.

- [x] **Step 3: Add the structured read result to contracts**

Append to `src/seektalent/providers/pi_agent/contracts.py`. Reuse the Plan 1 contract imports and models: `AnyUrl`, `model_validator`, `PiArtifactRef`, and `ProtectedArtifactClass`.

```python
class DokoBotReadResult(PiBoundaryModel):
    schema_version: Literal["dokobot-read-result-v1"]
    url: AnyUrl
    text_ref: PiArtifactRef | None = None
    chunks_ref: PiArtifactRef | None = None
    session_id: str | None = None
    vertical_has_more: bool = False
    vertical_stop_reason: Literal["end_of_scroll", "limit_reached", "timeout", "unknown"] = "unknown"
    screens_used: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    stderr_redacted_ref: PiArtifactRef | None = None

    @model_validator(mode="after")
    def validate_read_artifact_classes(self) -> "DokoBotReadResult":
        for ref in (self.text_ref, self.chunks_ref):
            if ref is not None and ref.artifact_class != ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT:
                raise ValueError("DokoBot read text/chunks refs must be protected provider snapshots")
        if self.stderr_redacted_ref is not None:
            if self.stderr_redacted_ref.artifact_class != ProtectedArtifactClass.REDACTED_EVIDENCE:
                raise ValueError("DokoBot stderr ref must be redacted evidence")
        return self
```

Also extend the existing `tests/test_pi_agent_contracts.py` contract import with `DokoBotReadResult`, then append:

```python
def test_dokobot_read_result_requires_schema_version() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(DokoBotReadResult).validate_python(
            {
                "url": "https://www.liepin.com/zhaopin/",
                "text_ref": _protected_snapshot_ref().model_dump(mode="python"),
            }
        )


def test_dokobot_read_result_rejects_safe_summary_as_text_ref() -> None:
    with pytest.raises(ValidationError):
        DokoBotReadResult(
            schema_version="dokobot-read-result-v1",
            url="https://www.liepin.com/zhaopin/",
            text_ref=_safe_summary_ref(),
        )
```

- [x] **Step 4: Implement the capability probe**

Add `src/seektalent/providers/pi_agent/capabilities.py`:

```python
from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict


RunCommand = Callable[[list[str]], subprocess.CompletedProcess[str]]


class DokoBotActionToolManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    manifest_id: str
    manifest_version: str
    provider: Literal["dokobot_compatible"]
    enabled_tools: tuple[str, ...] = ()

    @property
    def supports_text_entry(self) -> bool:
        return bool({"fill", "fill_form", "type_text"}.intersection(self.enabled_tools))


class DokoBotCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    cli_version: str
    extension_version: str | None = None
    core_skill_version: str | None = None
    supports_read: bool
    supports_chunks_format: bool
    supports_session_continuation: bool
    supports_click: bool = False
    supports_type: bool = False
    supports_navigation: bool = False
    supports_pagination_action: bool = False
    action_manifest_id: str | None = None
    action_manifest_version: str | None = None
    action_manifest_tools: tuple[str, ...] = ()
    local_mode_available: bool = False
    remote_mode_available: bool = False
    capability_error_code: Literal[
        "dokobot_cli_unavailable",
        "dokobot_capability_probe_failed",
        "dokobot_capability_probe_timeout",
    ] | None = None

    @property
    def can_execute_liepin_actions(self) -> bool:
        return (
            self.supports_read
            and self.supports_click
            and self.supports_type
            and self.supports_navigation
            and self.supports_pagination_action
        )


class DokoBotCapabilityProbe:
    def __init__(
        self,
        *,
        run_command: RunCommand | None = None,
        action_tool_manifest: DokoBotActionToolManifest | None = None,
    ) -> None:
        self._run_command = run_command or _run_command
        self._action_tool_manifest = action_tool_manifest

    def discover(self) -> DokoBotCapabilities:
        # Discovery is intentionally read-only: no install, permission mutation, or backend fallback.
        try:
            version_result = self._run_command(["dokobot", "--version"])
            help_result = self._run_command(["dokobot", "--help"])
            read_help_result = self._run_command(["dokobot", "read", "--help"])
        except FileNotFoundError:
            return _failed_capabilities("unknown", "dokobot_cli_unavailable")
        except subprocess.TimeoutExpired:
            return _failed_capabilities("unknown", "dokobot_capability_probe_timeout")
        if version_result.returncode != 0 or help_result.returncode != 0 or read_help_result.returncode != 0:
            return _failed_capabilities(version_result.stdout.strip() or "unknown", "dokobot_capability_probe_failed")
        help_text = f"{help_result.stdout}\n{read_help_result.stdout}"
        tools = self._action_tool_manifest.enabled_tools if self._action_tool_manifest is not None else ()
        tool_set = set(tools)
        supports_click = "click" in tool_set
        return DokoBotCapabilities(
            cli_version=version_result.stdout.strip() or "unknown",
            supports_read=_help_has_command(help_result.stdout, "read"),
            supports_chunks_format="chunks" in read_help_result.stdout,
            supports_session_continuation="session-id" in read_help_result.stdout,
            supports_click=supports_click,
            supports_type=self._action_tool_manifest.supports_text_entry if self._action_tool_manifest else False,
            supports_navigation=bool({"navigate", "navigation"}.intersection(tool_set)),
            supports_pagination_action=supports_click
            and bool({"turn_page", "paginate", "pagination"}.intersection(tool_set)),
            action_manifest_id=self._action_tool_manifest.manifest_id if self._action_tool_manifest else None,
            action_manifest_version=self._action_tool_manifest.manifest_version if self._action_tool_manifest else None,
            action_manifest_tools=tools,
            local_mode_available="--local" in read_help_result.stdout,
            remote_mode_available="--api-key" in help_result.stdout or "--server" in help_result.stdout,
        )


def _failed_capabilities(
    cli_version: str,
    capability_error_code: Literal[
        "dokobot_cli_unavailable",
        "dokobot_capability_probe_failed",
        "dokobot_capability_probe_timeout",
    ],
) -> DokoBotCapabilities:
    return DokoBotCapabilities(
        cli_version=cli_version,
        supports_read=False,
        supports_chunks_format=False,
        supports_session_continuation=False,
        capability_error_code=capability_error_code,
    )


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, capture_output=True, timeout=10)


def _help_has_command(help_text: str, command: str) -> bool:
    return re.search(rf"(?m)^\s*{re.escape(command)}\b", help_text) is not None
```

- [x] **Step 5: Implement the DokoBot read client**

Add `src/seektalent/providers/pi_agent/dokobot_client.py`:

The public DokoBot CLI boundary is text-first. It may expose `--format chunks`, but this client must not assume public CLI stdout is JSON. Parse JSON/chunks only when the caller explicitly marks the read surface as JSON-capable; otherwise persist stdout as a protected text snapshot. Command failures must write only redacted stderr to redacted evidence. Raw stdout from failed commands must not enter redacted evidence.

```python
from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable
from typing import Any, Literal

from seektalent.providers.pi_agent.contracts import DokoBotReadResult, PiArtifactRef, ProtectedArtifactClass


RunCommand = Callable[[list[str], int], subprocess.CompletedProcess[str]]
ArtifactWriter = Callable[[bytes, ProtectedArtifactClass, str], PiArtifactRef]
ReadOutputFormat = Literal["text", "chunks"]

PROTECTED_PROVIDER_POLICY_ID = "liepin-protected-snapshot-v1"
DOKOBOT_COMMAND_ERROR_REDACTION_POLICY_ID = "dokobot-command-error-redaction-v1"


class DokoBotExecutionError(RuntimeError):
    def __init__(self, error_code: str, *, stderr_redacted_ref: PiArtifactRef | None = None) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.stderr_redacted_ref = stderr_redacted_ref


class DokoBotClient:
    def __init__(
        self,
        *,
        run_command: RunCommand | None = None,
        artifact_writer: ArtifactWriter | None = None,
    ) -> None:
        self._run_command = run_command or _run_command
        self._artifact_writer = artifact_writer or _missing_artifact_writer

    def read_url(
        self,
        url: str,
        *,
        timeout_seconds: int = 30,
        output_format: ReadOutputFormat = "text",
        screens: int = 1,
        session_id: str | None = None,
        reuse_tab: bool = False,
        json_capable_surface: bool = False,
    ) -> DokoBotReadResult:
        if screens < 1 or screens > 100:
            raise ValueError("screens must be between 1 and 100")
        command = [
            "dokobot",
            "read",
            "--local",
            "--timeout",
            str(timeout_seconds),
            "--screens",
            str(screens),
            "--format",
            output_format,
        ]
        if reuse_tab:
            command.append("--reuse-tab")
        if session_id is not None:
            command.extend(["--session-id", session_id])
        command.append(url)
        started = time.monotonic()
        process_timeout_seconds = timeout_seconds + 10
        try:
            result = self._run_command(command, process_timeout_seconds)
        except subprocess.TimeoutExpired:
            stderr_ref = self._artifact_writer(
                b"dokobot read timed out",
                ProtectedArtifactClass.REDACTED_EVIDENCE,
                DOKOBOT_COMMAND_ERROR_REDACTION_POLICY_ID,
            )
            raise DokoBotExecutionError("dokobot_read_timeout", stderr_redacted_ref=stderr_ref) from None
        duration_ms = int((time.monotonic() - started) * 1000)
        if result.returncode != 0:
            stderr_ref = _write_redacted_stderr_ref(result.stderr, self._artifact_writer)
            raise DokoBotExecutionError("dokobot_read_failed", stderr_redacted_ref=stderr_ref)
        return _read_result_from_process(
            url=url,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
            artifact_writer=self._artifact_writer,
            parse_json=output_format == "chunks" and json_capable_surface,
            requested_screens=screens,
        )


def _run_command(command: list[str], process_timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, capture_output=True, timeout=process_timeout_seconds)


def _missing_artifact_writer(
    _content: bytes,
    _artifact_class: ProtectedArtifactClass,
    _policy_id: str,
) -> PiArtifactRef:
    raise RuntimeError("artifact_writer is required")


def _read_result_from_process(
    *,
    url: str,
    stdout: str,
    stderr: str,
    duration_ms: int,
    artifact_writer: ArtifactWriter,
    parse_json: bool,
    requested_screens: int,
) -> DokoBotReadResult:
    session_id = _session_id_from_stderr(stderr)
    stderr_ref = _write_redacted_stderr_ref(stderr, artifact_writer)
    payload = _maybe_json_object(stdout) if parse_json else None
    if payload is None:
        return DokoBotReadResult(
            schema_version="dokobot-read-result-v1",
            url=url,
            text_ref=artifact_writer(
                stdout.encode("utf-8"),
                ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT,
                PROTECTED_PROVIDER_POLICY_ID,
            )
            if stdout
            else None,
            session_id=session_id,
            vertical_has_more=session_id is not None,
            screens_used=requested_screens,
            duration_ms=duration_ms,
            stderr_redacted_ref=stderr_ref,
        )

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    text = str(data.get("text") or payload.get("text") or "")
    chunks = payload.get("chunks")
    if chunks is None:
        chunks = data.get("chunks")
    text_ref = None
    chunks_ref = (
        artifact_writer(
            json.dumps(chunks, ensure_ascii=False).encode("utf-8"),
            ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT,
            PROTECTED_PROVIDER_POLICY_ID,
        )
        if chunks is not None
        else None
    )
    if text:
        text_ref = artifact_writer(
            text.encode("utf-8"),
            ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT,
            PROTECTED_PROVIDER_POLICY_ID,
        )
    payload_session_id = str(payload.get("sessionId") or data.get("sessionId") or "") or None
    session_id = payload_session_id or session_id
    vertical = payload.get("vertical") if isinstance(payload.get("vertical"), dict) else {}
    explicit_has_more = _optional_bool(
        payload.get("canContinue"),
        payload.get("hasMore"),
        vertical.get("hasMore"),
    )
    return DokoBotReadResult(
        schema_version="dokobot-read-result-v1",
        url=url,
        text_ref=text_ref,
        chunks_ref=chunks_ref,
        session_id=session_id,
        vertical_has_more=explicit_has_more if explicit_has_more is not None else session_id is not None,
        vertical_stop_reason=_normalize_stop_reason(payload.get("stopReason") or vertical.get("stopReason")),
        screens_used=_non_negative_int(payload.get("screens"), default=requested_screens),
        duration_ms=duration_ms,
        stderr_redacted_ref=stderr_ref,
    )


def _maybe_json_object(stdout: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_stop_reason(value: object) -> Literal["end_of_scroll", "limit_reached", "timeout", "unknown"]:
    if value == "end_of_scroll":
        return "end_of_scroll"
    if value == "limit_reached":
        return "limit_reached"
    if value == "timeout":
        return "timeout"
    return "unknown"


def _non_negative_int(value: object, *, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, 0)


def _optional_bool(*values: object) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
    return None


def _session_id_from_stderr(stderr: str) -> str | None:
    match = re.search(r"(?m)^Session:\s+(\S+)\s*$", stderr)
    return match.group(1) if match else None


def _write_redacted_stderr_ref(stderr: str, artifact_writer: ArtifactWriter) -> PiArtifactRef | None:
    redacted = _redact_command_stderr(stderr)
    if not redacted:
        return None
    return artifact_writer(
        redacted.encode("utf-8"),
        ProtectedArtifactClass.REDACTED_EVIDENCE,
        DOKOBOT_COMMAND_ERROR_REDACTION_POLICY_ID,
    )


def _redact_command_stderr(text: str) -> str:
    text = re.sub(r"(?m)^(Session:\s+)\S+", r"\1[REDACTED_SESSION]", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    text = re.sub(r"secret[-_A-Za-z0-9]*", "[REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"1[3-9]\d{9}", "[REDACTED_PHONE]", text)
    return text.strip()
```

- [x] **Step 6: Run DokoBot tests**

```bash
uv run pytest tests/test_dokobot_capabilities.py tests/test_pi_agent_contracts.py -q
```

Expected: pass.

- [ ] **Step 7: Commit DokoBot capability boundary**

```bash
git add src/seektalent/providers/pi_agent/capabilities.py src/seektalent/providers/pi_agent/dokobot_client.py src/seektalent/providers/pi_agent/contracts.py tests/test_dokobot_capabilities.py tests/test_pi_agent_contracts.py
git commit -m "feat: add dokobot capability boundary"
```

### Task 2: Add Protected Artifact And UI-Leakage Guard

**Files:**
- Create: `src/seektalent/providers/pi_agent/artifacts.py`
- Test: `tests/test_pi_agent_artifacts.py`

- [x] **Step 1: Write failing artifact tests**

Add `tests/test_pi_agent_artifacts.py`:

```python
import pytest

from seektalent.providers.pi_agent.artifacts import assert_ui_safe_artifact, make_artifact_ref


def test_safe_summary_artifact_is_ui_readable() -> None:
    ref = make_artifact_ref(
        artifact_class="safe_summary_artifact",
        artifact_ref="artifact_safe_1",
        content=b"safe candidate summary",
        redaction_policy_id="liepin-summary-redaction-v1",
    )

    assert_ui_safe_artifact(ref)


def test_protected_provider_snapshot_is_not_ui_readable() -> None:
    ref = make_artifact_ref(
        artifact_class="protected_provider_snapshot",
        artifact_ref="artifact_protected_1",
        content=b"raw provider resume",
        redaction_policy_id=None,
        protection_policy_id="liepin-protected-snapshot-v1",
    )

    with pytest.raises(PermissionError):
        assert_ui_safe_artifact(ref)


def test_redacted_evidence_artifact_is_not_ordinary_ui_readable() -> None:
    ref = make_artifact_ref(
        artifact_class="redacted_evidence_artifact",
        artifact_ref="artifact_redacted_1",
        content=b"redacted audit evidence",
        redaction_policy_id="liepin-evidence-redaction-v1",
    )

    with pytest.raises(PermissionError):
        assert_ui_safe_artifact(ref)


def test_protected_snapshot_requires_protection_policy_id() -> None:
    with pytest.raises(ValueError):
        make_artifact_ref(
            artifact_class="protected_provider_snapshot",
            artifact_ref="artifact_protected_missing_policy",
            content=b"raw provider resume",
            redaction_policy_id=None,
        )
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_pi_agent_artifacts.py -q
```

Expected: import failure for `seektalent.providers.pi_agent.artifacts`.

- [x] **Step 3: Implement artifact helpers**

Add `src/seektalent/providers/pi_agent/artifacts.py`:

```python
from hashlib import sha256

from seektalent.providers.pi_agent.contracts import PiArtifactRef, ProtectedArtifactClass


def make_artifact_ref(
    *,
    artifact_class: str,
    artifact_ref: str,
    content: bytes,
    redaction_policy_id: str | None,
    protection_policy_id: str | None = None,
) -> PiArtifactRef:
    return PiArtifactRef(
        artifact_class=ProtectedArtifactClass(artifact_class),
        artifact_ref=artifact_ref,
        content_sha256=sha256(content).hexdigest(),
        redaction_policy_id=redaction_policy_id,
        protection_policy_id=protection_policy_id,
    )


def assert_ui_safe_artifact(ref: PiArtifactRef) -> None:
    if ref.artifact_class != ProtectedArtifactClass.SAFE_SUMMARY:
        raise PermissionError(f"artifact class {ref.artifact_class.value} is not UI-safe")
```

- [x] **Step 4: Run artifact tests**

```bash
uv run pytest tests/test_pi_agent_artifacts.py -q
```

Expected: pass.

- [ ] **Step 5: Commit artifact boundary**

```bash
git add src/seektalent/providers/pi_agent/artifacts.py tests/test_pi_agent_artifacts.py
git commit -m "feat: protect pi agent artifacts"
```

## Self-Review

- Spec coverage: DokoBot read-only CLI behavior, explicit action manifest discovery, no-install/no-downgrade behavior, fail-closed missing/failed/timed-out CLI probing, DokoBot read subprocess timeout, typed `PiArtifactRef` read refs, structured read result schema versioning, stderr session parsing plus redacted stderr refs, nested `vertical.hasMore`/`vertical.stopReason` parsing, JSON parsing only for explicitly JSON-capable read surfaces, no failed stdout leakage into redacted evidence, `reuse_tab=False` by default, protected provider snapshots, redacted evidence, and UI-safe artifact checks are covered.
- Placeholder scan: every step names concrete files, tests, commands, and expected outcomes.
- Type consistency: this plan extends `contracts.py` from the contract plan and produces imports used by the backend-dispatch plan.
