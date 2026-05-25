from __future__ import annotations

import io
import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from seektalent.providers.pi_agent.pi_external import (
    PiExternalAgentErrorCode,
    PiRpcAgentClient,
    PiRpcCommand,
    PiRpcTaskResult,
    PiRpcTaskStatus,
    SubprocessPiRpcTransport,
    _task_contract_for_prompt,
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


class SequentialFakeRpcTransport:
    def __init__(self, *results: PiRpcTaskResult) -> None:
        self.results = list(results)
        self.commands: list[PiRpcCommand] = []
        self.prompts: list[str] = []

    def request(self, command: PiRpcCommand, *, prompt: str) -> PiRpcTaskResult:
        self.commands.append(command)
        self.prompts.append(prompt)
        if not self.results:
            raise AssertionError("unexpected extra Pi RPC request")
        return self.results.pop(0)


def _skill(tmp_path: Path) -> Path:
    path = tmp_path / "liepin_search_cards.md"
    path.write_text("---\nname: liepin-search-cards\n---\n", encoding="utf-8")
    return path


def _client(tmp_path: Path, result: PiRpcTaskResult) -> PiRpcAgentClient:
    skill_path = _skill(tmp_path)
    return PiRpcAgentClient(
        command=build_pi_rpc_argv("pi --mode rpc --no-session", skill_path=skill_path),
        skill_path=skill_path,
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=tmp_path / "artifacts" / "pi-agent",
        transport=FakeRpcTransport(result),
    )


def test_build_pi_rpc_argv_requires_rpc_no_session_and_loads_skill(tmp_path: Path) -> None:
    skill_path = _skill(tmp_path)

    argv = build_pi_rpc_argv("pi --mode rpc --no-session", skill_path=skill_path)

    assert argv == ("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", str(skill_path))


def test_build_pi_rpc_argv_preserves_required_provider_and_mcp_extensions(tmp_path: Path) -> None:
    skill_path = _skill(tmp_path)
    command = (
        "pi --mode rpc --no-session "
        "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
        "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts "
        "--provider bailian --model deepseek-v4-flash"
    )

    argv = build_pi_rpc_argv(
        command,
        skill_path=skill_path,
        required_extension_markers=("pi_extensions/bailian_deepseek.ts", "pi-mcp-adapter/index.ts"),
    )

    assert "src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts" in argv
    assert "apps/web-svelte/node_modules/pi-mcp-adapter/index.ts" in argv
    assert "--skill" in argv


def test_build_pi_rpc_argv_rejects_missing_mcp_adapter_extension(tmp_path: Path) -> None:
    skill_path = _skill(tmp_path)
    command = (
        "pi --mode rpc --no-session "
        "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
        "--provider bailian --model deepseek-v4-flash"
    )

    with pytest.raises(ValueError, match="liepin_pi_command must include required extension"):
        build_pi_rpc_argv(
            command,
            skill_path=skill_path,
            required_extension_markers=("pi_extensions/bailian_deepseek.ts", "pi-mcp-adapter/index.ts"),
        )


def test_build_pi_rpc_argv_rejects_missing_provider_extension(tmp_path: Path) -> None:
    skill_path = _skill(tmp_path)
    command = (
        "pi --mode rpc --no-session "
        "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts "
        "--provider bailian --model deepseek-v4-flash"
    )

    with pytest.raises(ValueError, match="liepin_pi_command must include required extension"):
        build_pi_rpc_argv(
            command,
            skill_path=skill_path,
            required_extension_markers=("pi_extensions/bailian_deepseek.ts", "pi-mcp-adapter/index.ts"),
        )


def test_build_pi_rpc_argv_does_not_accept_marker_outside_extension_arg(tmp_path: Path) -> None:
    skill_path = _skill(tmp_path)
    command = (
        "pi --mode rpc --no-session "
        "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
        "--model pi-mcp-adapter/index.ts"
    )

    with pytest.raises(ValueError, match="liepin_pi_command must include required extension"):
        build_pi_rpc_argv(
            command,
            skill_path=skill_path,
            required_extension_markers=("pi_extensions/bailian_deepseek.ts", "pi-mcp-adapter/index.ts"),
        )


def test_build_pi_rpc_argv_rejects_missing_required_extension_file(tmp_path: Path) -> None:
    skill_path = _skill(tmp_path)
    provider_extension = tmp_path / "src" / "seektalent" / "providers" / "pi_agent" / "pi_extensions"
    provider_extension.mkdir(parents=True)
    (provider_extension / "bailian_deepseek.ts").write_text("provider", encoding="utf-8")
    command = (
        "pi --mode rpc --no-session "
        "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
        "--extension src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts"
    )

    with pytest.raises(ValueError, match="required extension file"):
        build_pi_rpc_argv(
            command,
            skill_path=skill_path,
            required_extension_markers=(
                "pi_extensions/bailian_deepseek.ts",
                "pi_extensions/seektalent_opencli_browser.ts",
            ),
            extension_root=tmp_path,
        )


def test_build_pi_rpc_argv_does_not_require_external_adapter_file(tmp_path: Path) -> None:
    skill_path = _skill(tmp_path)
    provider_extension = tmp_path / "src" / "seektalent" / "providers" / "pi_agent" / "pi_extensions"
    provider_extension.mkdir(parents=True)
    (provider_extension / "bailian_deepseek.ts").write_text("provider", encoding="utf-8")
    command = (
        "pi --mode rpc --no-session "
        "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
        "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts"
    )

    argv = build_pi_rpc_argv(
        command,
        skill_path=skill_path,
        required_extension_markers=("pi_extensions/bailian_deepseek.ts", "pi-mcp-adapter/index.ts"),
        extension_root=tmp_path,
    )

    assert "apps/web-svelte/node_modules/pi-mcp-adapter/index.ts" in argv


@pytest.mark.parametrize("command", ["pi", "pi --mode json --no-session", "pi --mode rpc"])
def test_build_pi_rpc_argv_rejects_non_rpc_or_sessionful_commands(tmp_path: Path, command: str) -> None:
    with pytest.raises(ValueError):
        build_pi_rpc_argv(command, skill_path=_skill(tmp_path))


def test_pi_rpc_client_accepts_exact_json_object_only(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text='{"schema_version":"seektalent.pi_liepin_cards.v1","status":"succeeded","cards":[]}',
        ),
    )

    envelope = client.run_json_task("collect cards")

    assert envelope["schema_version"] == "seektalent.pi_liepin_cards.v1"
    transport = client.transport_for_test
    assert transport.commands[0].env["SEEKTALENT_PI_ARTIFACT_ROOT"].endswith("artifacts/pi-agent")
    assert "Required artifact root:" in transport.prompts[0]


def test_pi_rpc_client_passes_runtime_provider_env_without_putting_secrets_in_command(tmp_path: Path) -> None:
    skill_path = _skill(tmp_path)
    transport = FakeRpcTransport(
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text='{"schema_version":"seektalent.pi_liepin_cards.v1","status":"succeeded","cards":[]}',
        )
    )
    client = PiRpcAgentClient(
        command=build_pi_rpc_argv(
            "pi --mode rpc --no-session --extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts --provider bailian --model deepseek-v4-flash",
            skill_path=skill_path,
        ),
        skill_path=skill_path,
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        resume_capture_idle_timeout_seconds=12.5,
        artifact_root=tmp_path / "artifacts" / "pi-agent",
        env={
            "SEEKTALENT_PI_BAILIAN_API_KEY": "runtime-secret-key",
            "SEEKTALENT_PI_BAILIAN_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "SEEKTALENT_PI_BAILIAN_MODEL_ID": "deepseek-v4-flash",
        },
        transport=transport,
    )

    client.run_json_task("collect cards")

    command = transport.commands[0]
    assert command.env["SEEKTALENT_PI_BAILIAN_API_KEY"] == "runtime-secret-key"
    assert command.env["SEEKTALENT_PI_BAILIAN_MODEL_ID"] == "deepseek-v4-flash"
    assert command.resume_capture_idle_timeout_seconds == 12.5
    assert "runtime-secret-key" not in " ".join(command.argv)


def test_pi_prompt_can_describe_opencli_backend_without_dokobot_wording(tmp_path: Path) -> None:
    skill_path = _skill(tmp_path)
    client = PiRpcAgentClient(
        command=build_pi_rpc_argv("pi --mode rpc --no-session", skill_path=skill_path),
        skill_path=skill_path,
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=tmp_path / "artifacts" / "pi-agent",
        browser_backend_description="SeekTalent OpenCLI browser tools: seektalent_opencli_status",
        transport=FakeRpcTransport(PiRpcTaskResult(status=PiRpcTaskStatus.SUCCEEDED, final_text="{}")),
    )

    prompt = client._build_prompt("{}")

    assert "SeekTalent OpenCLI browser tools" in prompt
    assert "Required DokoBot tool inside Pi" not in prompt


def test_pi_prompt_adds_strict_capability_probe_contract(tmp_path: Path) -> None:
    skill_path = _skill(tmp_path)
    client = PiRpcAgentClient(
        command=build_pi_rpc_argv("pi --mode rpc --no-session", skill_path=skill_path),
        skill_path=skill_path,
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=tmp_path / "artifacts" / "pi-agent",
        browser_backend_description="SeekTalent OpenCLI browser tools: seektalent_opencli_status",
        transport=FakeRpcTransport(PiRpcTaskResult(status=PiRpcTaskStatus.SUCCEEDED, final_text="{}")),
    )

    prompt = client._build_prompt('{"task":"liepin.probe_capabilities"}')

    assert "seektalent.pi_capability_probe.v1" in prompt
    assert "Do not click, type, scroll, navigate, or open a page for this probe" in prompt
    assert "liepin_opencli_status_unavailable" in prompt


def test_pi_prompt_adds_strict_session_probe_contract(tmp_path: Path) -> None:
    skill_path = _skill(tmp_path)
    client = PiRpcAgentClient(
        command=build_pi_rpc_argv("pi --mode rpc --no-session", skill_path=skill_path),
        skill_path=skill_path,
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=tmp_path / "artifacts" / "pi-agent",
        transport=FakeRpcTransport(PiRpcTaskResult(status=PiRpcTaskStatus.SUCCEEDED, final_text="{}")),
    )

    prompt = client._build_prompt('{"task":"liepin.probe_session","connection_id":"conn-1"}')

    assert "seektalent.pi_liepin_session_probe.v1" in prompt
    assert "Only status ready may include provider_account_material_ref" in prompt
    assert "Never include cookies" in prompt


def test_pi_prompt_routes_liepin_search_to_single_opencli_tool(tmp_path: Path) -> None:
    skill_path = _skill(tmp_path)
    client = PiRpcAgentClient(
        command=build_pi_rpc_argv("pi --mode rpc --no-session", skill_path=skill_path),
        skill_path=skill_path,
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=tmp_path / "artifacts" / "pi-agent",
        browser_backend_description="SeekTalent OpenCLI browser tools: seektalent_opencli_search_liepin_cards",
        transport=FakeRpcTransport(PiRpcTaskResult(status=PiRpcTaskStatus.SUCCEEDED, final_text="{}")),
    )

    prompt = client._build_prompt('{"task":"liepin.search_cards","source_run_id":"run-1","query":"数据开发专家"}')

    assert "Call seektalent_opencli_search_liepin_cards exactly once" in prompt
    assert "return that tool result exactly as the final raw JSON object" in prompt
    assert "Do not call read, bash" in prompt
    assert "bounded loop" not in prompt


def test_pi_rpc_client_accepts_search_cards_envelope_from_high_level_tool_event(tmp_path: Path) -> None:
    envelope = {
        "schema_version": "seektalent.pi_liepin_cards.v1",
        "status": "blocked",
        "stop_reason": "blocked_backend_unavailable",
        "safe_reason_code": "liepin_opencli_timeout",
        "source_run_id": "run-1",
        "query": "数据开发专家",
        "cards_seen": 0,
        "cards_returned": 0,
        "pages_visited": 1,
        "action_trace_ref": "artifact://protected/pi-trace/run-1/action-trace.json",
        "safe_summary_refs": [],
        "protected_snapshot_refs": [],
        "cards": [],
    }
    client = _client(
        tmp_path,
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text="not json",
            events=(
                {
                    "type": "tool_execution_result",
                    "toolName": "seektalent_opencli_search_liepin_cards",
                    "result": {"content": [{"type": "text", "text": json.dumps(envelope, ensure_ascii=False)}]},
                },
            ),
        ),
    )

    result = client.run_json_task_result('{"task":"liepin.search_cards"}')

    assert result.ok is True
    assert result.envelope == envelope
    assert result.events == (
        {"type": "tool_execution_result", "tool_name": "seektalent_opencli_search_liepin_cards"},
    )


def test_pi_rpc_client_prefers_search_cards_tool_event_over_final_text(tmp_path: Path) -> None:
    tool_envelope = {
        "schema_version": "seektalent.pi_liepin_cards.v1",
        "status": "succeeded",
        "stop_reason": "completed",
        "source_run_id": "run-1",
        "query": "数据开发专家",
        "cards_seen": 1,
        "cards_returned": 0,
        "pages_visited": 1,
        "action_trace_ref": "artifact://protected/pi-trace/run-1/action-trace.json",
        "safe_summary_refs": [],
        "protected_snapshot_refs": [],
        "cards": [],
    }
    final_text = {
        "schema_version": "seektalent.pi_liepin_cards.v1",
        "status": "blocked",
        "stop_reason": "blocked_backend_unavailable",
        "safe_reason_code": "liepin_opencli_status_unavailable",
        "source_run_id": "run-1",
        "query": "数据开发专家",
        "cards_seen": 0,
        "cards_returned": 0,
        "pages_visited": 1,
        "action_trace_ref": "artifact://protected/pi-trace/run-1/fabricated.json",
        "safe_summary_refs": [],
        "protected_snapshot_refs": [],
        "cards": [],
    }
    client = _client(
        tmp_path,
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text=json.dumps(final_text, ensure_ascii=False),
            events=(
                {
                    "type": "tool_execution_result",
                    "toolName": "seektalent_opencli_search_liepin_cards",
                    "result": {"content": [{"type": "text", "text": json.dumps(tool_envelope, ensure_ascii=False)}]},
                },
            ),
        ),
    )

    result = client.run_json_task_result('{"task":"liepin.search_cards"}')

    assert result.ok is True
    assert result.envelope == tool_envelope


def test_pi_rpc_client_accepts_search_cards_tool_event_when_agent_end_times_out(tmp_path: Path) -> None:
    tool_envelope = {
        "schema_version": "seektalent.pi_liepin_cards.v1",
        "status": "succeeded",
        "stop_reason": "completed",
        "source_run_id": "run-1",
        "query": "数据开发专家",
        "cards_seen": 10,
        "cards_returned": 0,
        "pages_visited": 1,
        "action_trace_ref": "artifact://protected/pi-trace/run-1/action-trace.json",
        "safe_summary_refs": [],
        "protected_snapshot_refs": [],
        "cards": [],
    }
    client = _client(
        tmp_path,
        PiRpcTaskResult(
            status=PiRpcTaskStatus.TIMEOUT,
            safe_message="pi rpc timed out",
            events=(
                {
                    "type": "tool_execution_result",
                    "toolName": "seektalent_opencli_search_liepin_cards",
                    "result": {"content": [{"type": "text", "text": json.dumps(tool_envelope, ensure_ascii=False)}]},
                },
            ),
        ),
    )

    result = client.run_json_task_result('{"task":"liepin.search_cards"}')

    assert result.ok is True
    assert result.envelope == tool_envelope


def test_pi_rpc_client_rejects_notes_before_json(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text='notes\n{"schema_version":"seektalent.pi_liepin_cards.v1","status":"succeeded"}',
        ),
    )

    result = client.run_json_task_result("collect cards")

    assert result.ok is False
    assert result.error_code == PiExternalAgentErrorCode.MALFORMED_OUTPUT


def test_pi_rpc_client_retries_once_when_final_answer_is_markdown_json(tmp_path: Path) -> None:
    skill_path = _skill(tmp_path)
    transport = SequentialFakeRpcTransport(
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text='Here is the JSON.\n```json\n{"ok":true}\n```',
            events=({"type": "tool_execution_start", "toolName": "seektalent_opencli_status"},),
        ),
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text='{"ok":true}',
            events=({"type": "tool_execution_start", "toolName": "seektalent_opencli_status"},),
        ),
    )
    client = PiRpcAgentClient(
        command=build_pi_rpc_argv("pi --mode rpc --no-session", skill_path=skill_path),
        skill_path=skill_path,
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=tmp_path / "artifacts" / "pi-agent",
        transport=transport,
    )

    result = client.run_json_task_result("collect cards")

    assert result.ok is True
    assert result.envelope == {"ok": True}
    assert len(transport.prompts) == 2
    assert "STRICT JSON RETRY" not in transport.prompts[0]
    assert "STRICT JSON RETRY" in transport.prompts[1]
    assert "No prose, markdown fences, code blocks" in transport.prompts[1]


def test_pi_rpc_client_exposes_only_observed_tool_names_from_rpc_events(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text='{"ok":true}',
            events=(
                {"type": "tool_execution_start", "toolName": "dokobot.read", "raw": "secret-token"},
                {"type": "tool_execution_start", "tool_name": "dokobot.click", "input": {"cookie": "session"}},
            ),
        ),
    )

    result = client.run_json_task_result("probe tools")

    assert result.observed_tool_names == ("dokobot.read", "dokobot.click")
    assert result.events == (
        {"type": "tool_execution_start", "tool_name": "dokobot.read"},
        {"type": "tool_execution_start", "tool_name": "dokobot.click"},
    )
    assert "secret-token" not in str(result.events)
    assert "cookie" not in str(result.events).lower()


def test_pi_rpc_client_extracts_only_allowlisted_opencli_safe_reason_from_tool_output(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text="not json",
            events=(
                {
                    "type": "tool_execution_result",
                    "toolName": "seektalent_opencli_open_liepin_tab",
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    '{"ok":false,"safeReasonCode":"liepin_opencli_window_policy_blocked",'
                                    '"secret":"Bearer should-not-leak"}'
                                ),
                            }
                        ]
                    },
                },
            ),
        ),
    )

    result = client.run_json_task_result("collect cards")

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_window_policy_blocked"
    assert result.events == (
        {"type": "tool_execution_result", "tool_name": "seektalent_opencli_open_liepin_tab"},
    )
    assert "Bearer" not in str(result.events)


def test_pi_rpc_client_does_not_accept_unallowlisted_tool_reason(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text="not json",
            events=(
                {
                    "type": "tool_execution_result",
                    "toolName": "seektalent_opencli_open_liepin_tab",
                    "result": {"safeReasonCode": "Bearer secret-token"},
                },
            ),
        ),
    )

    result = client.run_json_task_result("collect cards")

    assert result.ok is False
    assert result.safe_reason_code is None


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [
        (PiRpcTaskStatus.UNAVAILABLE, PiExternalAgentErrorCode.PI_UNAVAILABLE),
        (PiRpcTaskStatus.PROMPT_REJECTED, PiExternalAgentErrorCode.PROMPT_REJECTED),
        (PiRpcTaskStatus.TIMEOUT, PiExternalAgentErrorCode.TIMEOUT),
        (PiRpcTaskStatus.UI_REQUESTED, PiExternalAgentErrorCode.UI_REQUEST_DENIED),
        (PiRpcTaskStatus.FAILED, PiExternalAgentErrorCode.PROCESS_FAILED),
        (PiRpcTaskStatus.MISSING_AGENT_END, PiExternalAgentErrorCode.MISSING_AGENT_END),
    ],
)
def test_pi_rpc_client_maps_external_failures_without_leaking_private_diagnostics(
    tmp_path: Path,
    status: PiRpcTaskStatus,
    expected_code: PiExternalAgentErrorCode,
) -> None:
    client = _client(
        tmp_path,
        PiRpcTaskResult(
            status=status,
            safe_message="Bearer secret-token cookie=session",
            private_diagnostic="secret",
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
        PiRpcCommand(argv=("pi", "--mode", "rpc"), timeout_seconds=1, artifact_root=tmp_path),
        prompt="probe",
    )

    assert result.status == expected_status


class _WritablePipe:
    def write(self, data: str) -> int:
        return len(data)

    def flush(self) -> None:
        return None


class _FakeRpcProcess:
    def __init__(self, stdout_text: str) -> None:
        self.stdin = _WritablePipe()
        self.stdout = io.StringIO(stdout_text)
        self.stderr = io.StringIO("")
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


class _BlockingRpcStdout:
    def __init__(self, lines: tuple[str, ...]) -> None:
        self._lines = list(lines)
        self._stopped = threading.Event()

    def __iter__(self) -> "_BlockingRpcStdout":
        return self

    def __next__(self) -> str:
        if self._lines:
            return self._lines.pop(0)
        while not self._stopped.is_set():
            time.sleep(0.01)
        raise StopIteration

    def stop(self) -> None:
        self._stopped.set()


class _LongRunningRpcProcess:
    def __init__(self, stdout_lines: tuple[str, ...]) -> None:
        self.stdin = _WritablePipe()
        self.stdout = _BlockingRpcStdout(stdout_lines)
        self.stderr = io.StringIO("")
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15
        self.stdout.stop()

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            self.returncode = 0
        self.stdout.stop()
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9
        self.stdout.stop()


def test_subprocess_transport_rejects_agent_end_before_prompt_ack(tmp_path: Path) -> None:
    agent_end = json.dumps(
        {
            "type": "agent_end",
            "messages": [{"role": "assistant", "content": '{"ok":true}'}],
        }
    )

    transport = SubprocessPiRpcTransport(process_factory=lambda *args, **kwargs: _FakeRpcProcess(agent_end + "\n"))

    result = transport.request(
        PiRpcCommand(argv=("pi", "--mode", "rpc"), timeout_seconds=1, artifact_root=tmp_path),
        prompt="probe",
    )

    assert result.status == PiRpcTaskStatus.MISSING_AGENT_END


def test_subprocess_transport_times_out_quickly_after_resume_capture_idle(tmp_path: Path) -> None:
    capture_event = {
        "type": "tool_execution_result",
        "toolName": "seektalent_opencli_capture_liepin_detail_resume",
        "result": {"counts": {"resumes": 1}},
    }
    stdout_lines = (
        json.dumps({"type": "response", "command": "prompt", "success": True}) + "\n",
        json.dumps(capture_event) + "\n",
    )
    process = _LongRunningRpcProcess(stdout_lines)
    transport = SubprocessPiRpcTransport(process_factory=lambda *args, **kwargs: process)

    started = time.monotonic()
    result = transport.request(
        PiRpcCommand(
            argv=("pi", "--mode", "rpc"),
            timeout_seconds=30,
            artifact_root=tmp_path,
            resume_capture_idle_timeout_seconds=0.05,
        ),
        prompt="search resumes",
    )

    assert result.status == PiRpcTaskStatus.TIMEOUT
    assert result.safe_message == "pi rpc idle after resume capture"
    assert time.monotonic() - started < 2
    assert process.returncode in {-15, -9}
    assert [event.get("toolName") for event in result.events][-1] == "seektalent_opencli_capture_liepin_detail_resume"


def test_subprocess_transport_finishes_search_cards_from_opencli_tool_result(tmp_path: Path) -> None:
    envelope = {
        "schema_version": "seektalent.pi_liepin_cards.v1",
        "status": "succeeded",
        "stop_reason": "completed",
        "source_run_id": "run-1",
        "query": "数据开发专家",
        "cards_seen": 1,
        "cards_returned": 0,
        "pages_visited": 1,
        "action_trace_ref": "artifact://protected/pi-trace/run-1/action-trace.json",
        "safe_summary_refs": [],
        "protected_snapshot_refs": [],
        "cards": [],
    }
    stdout = "\n".join(
        (
            json.dumps({"type": "response", "command": "prompt", "success": True}),
            json.dumps(
                {
                    "type": "tool_execution_result",
                    "toolName": "seektalent_opencli_search_liepin_cards",
                    "result": {"content": [{"type": "text", "text": json.dumps(envelope, ensure_ascii=False)}]},
                },
                ensure_ascii=False,
            ),
        )
    )
    transport = SubprocessPiRpcTransport(process_factory=lambda *args, **kwargs: _FakeRpcProcess(stdout + "\n"))

    result = transport.request(
        PiRpcCommand(argv=("pi", "--mode", "rpc"), timeout_seconds=30, artifact_root=tmp_path),
        prompt="search cards",
    )

    assert result.status == PiRpcTaskStatus.SUCCEEDED
    assert json.loads(result.final_text or "{}") == envelope


def test_liepin_pi_skill_contains_required_browser_boundaries() -> None:
    skill = Path("src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md").read_text(encoding="utf-8")

    assert "Use only SeekTalent Pi-owned browser tools" in skill
    assert "Use DokoBot only" not in skill
    assert "Do not ask for cookies" in skill
    assert "Do not open candidate detail pages in card mode" in skill
    assert "Return exactly one JSON object" in skill
    assert "SEEKTALENT_PI_ARTIFACT_ROOT" in skill
    assert "provider_candidate_key_material_ref" in skill


def test_opencli_pi_extension_exposes_only_restricted_tools() -> None:
    text = Path("src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts").read_text(
        encoding="utf-8"
    )

    assert "seektalent_opencli_status" in text
    assert "seektalent_opencli_search_liepin_cards" in text
    assert "Never use this tool for liepin.search_resumes" in text
    assert "seektalent_opencli_capabilities" in text
    assert "seektalent_opencli_state" in text
    assert "seektalent_opencli_open_liepin_tab" in text
    assert "seektalent_opencli_get_url" in text
    assert "seektalent_opencli_find" in text
    assert "seektalent_opencli_fill" in text
    assert "seektalent_opencli_click" in text
    assert "seektalent_opencli_scroll" in text
    assert "seektalent_opencli_wait_time" in text
    assert "browser eval" not in text
    assert "browser network" not in text
    assert "document.cookie" not in text
    assert "child.stderr.on" in text
    assert "MAX_OUTPUT_CHARS" in text
    assert "terminalReason" in text
    assert 'import type { ExtensionAPI } from "@earendil-works/pi-coding-agent"' in text
    assert ("type " + "ExtensionAPI = {") not in text
    assert "async execute(_toolCallId: string, params: ToolParams" in text
    assert "stateReady" in text
    assert "requires a fresh non-terminal state" in text
    assert "details: {}" in text
    assert "SEEKTALENT_LIEPIN_OPENCLI_TIMEOUT_SECONDS" in text
    assert 'process.env.SEEKTALENT_LIEPIN_OPENCLI_TASK === "liepin.search_resumes"' in text
    assert 'action === "search_cards"' in text


def test_liepin_search_resumes_prompt_contract_is_complete_resume_only() -> None:
    prompt = json.dumps(
        {
            "task": "liepin.search_resumes",
            "query": "数据开发",
            "target_resumes": 7,
            "must_haves": ["数据治理"],
            "nice_to_haves": ["Python"],
        },
        ensure_ascii=False,
    )

    contract = _task_contract_for_prompt(prompt)

    assert "complete resumes only" in contract
    assert "must-have" in contract
    assert "nice-to-have" in contract
    assert "target_resumes" in contract
    assert "max_cards" in contract
    assert "targetResumes=input target_resumes" in contract
    assert "native_filters" in contract
    assert "detailTargets" in contract
    assert "After every successful capture" in contract
    assert "finalize immediately" in contract
    assert "sourceRunId=input source_run_id" in contract
    assert "card summaries are internal screening evidence" in contract
    assert "Do not call seektalent_opencli_search_liepin_cards" in contract
    assert "seektalent_opencli_search_liepin_resumes" not in contract
    assert "seektalent_opencli_open_liepin_tab" in contract
    assert "seektalent_opencli_open_liepin_detail" in contract
    assert "seektalent_opencli_capture_liepin_detail_resume" in contract
    assert "seektalent_opencli_finalize_liepin_resumes" in contract


def test_liepin_search_resumes_uses_tool_event_envelope_when_agent_final_text_is_missing(tmp_path: Path) -> None:
    tool_payload = {
        "schema_version": "seektalent.pi_liepin_resumes.v1",
        "status": "succeeded",
        "stop_reason": "completed",
        "source_run_id": "st-run-1",
        "query": "数据开发",
        "cards_seen": 1,
        "resumes_returned": 1,
        "pages_visited": 1,
        "action_trace_ref": "artifact://protected/pi-trace/st-run-1/action-trace.json",
        "protected_snapshot_refs": ["artifact://protected/pi-detail/st-run-1/1.json"],
        "resumes": [
            {
                "provider_rank": 1,
                "provider_candidate_key_material_ref": "artifact://protected/pi-key/st-run-1/1.txt",
                "candidate_resume_id": "liepin-detail-1",
                "protected_snapshot_ref": "artifact://protected/pi-detail/st-run-1/1.json",
                "detail_payload": {"fullText": "数据开发专家 数据治理 Python"},
                "normalized_text": "数据开发专家 数据治理 Python",
            }
        ],
    }
    client = PiRpcAgentClient(
        command=("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", "skill.md"),
        skill_path=Path("skill.md"),
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=tmp_path / "artifacts" / "pi-agent",
        transport=FakeRpcTransport(
            PiRpcTaskResult(
                status=PiRpcTaskStatus.SUCCEEDED,
                final_text="not json",
                events=(
                    {
                        "type": "tool_execution_end",
                        "toolName": "seektalent_opencli_finalize_liepin_resumes",
                        "result": json.dumps(tool_payload, ensure_ascii=False),
                    },
                ),
            )
        ),
    )

    result = client.run_json_task_result(json.dumps({"task": "liepin.search_resumes"}, ensure_ascii=False))

    assert result.ok is True
    assert result.envelope == tool_payload


def test_liepin_opencli_task_uses_source_run_scoped_browser_session(tmp_path: Path) -> None:
    client = PiRpcAgentClient(
        command=("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", "skill.md"),
        skill_path=Path("skill.md"),
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=tmp_path / "artifacts" / "pi-agent",
        env={"SEEKTALENT_LIEPIN_OPENCLI_SESSION": "seektalent-liepin"},
        transport=FakeRpcTransport(
            PiRpcTaskResult(
                status=PiRpcTaskStatus.SUCCEEDED,
                final_text='{"schema_version":"test.v1"}',
            )
        ),
    )

    client.run_json_task_result(
        json.dumps(
            {
                "task": "liepin.search_resumes",
                "source_run_id": "run:source:liepin:lane:1",
                "query": "数据开发",
            },
            ensure_ascii=False,
        )
    )

    transport = client.transport_for_test
    assert isinstance(transport, FakeRpcTransport)
    session = transport.commands[0].env["SEEKTALENT_LIEPIN_OPENCLI_SESSION"]
    assert session.startswith("seektalent-liepin-")
    assert ":" not in session
    assert len(session) <= 80


def test_liepin_search_resumes_cleans_owned_detail_tabs_after_rpc_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_run(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        cleanup_calls.append((argv, dict(kwargs)))
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr("seektalent.providers.pi_agent.pi_external.subprocess.run", fake_run)
    client = PiRpcAgentClient(
        command=("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", "skill.md"),
        skill_path=Path("skill.md"),
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=tmp_path / "artifacts" / "pi-agent",
        env={
            "SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND": "opencli",
            "SEEKTALENT_PYTHON": "/usr/bin/python3",
            "SEEKTALENT_LIEPIN_OPENCLI_SESSION": "seektalent-liepin",
        },
        transport=FakeRpcTransport(PiRpcTaskResult(status=PiRpcTaskStatus.TIMEOUT, safe_message="timeout")),
    )

    result = client.run_json_task_result(
        json.dumps({"task": "liepin.search_resumes", "source_run_id": "run-1"}, ensure_ascii=False)
    )

    assert result.ok is False
    assert len(cleanup_calls) == 1
    argv, kwargs = cleanup_calls[0]
    assert argv == (
        "/usr/bin/python3",
        "-m",
        "seektalent.providers.pi_agent.opencli_browser_cli",
        "cleanup_liepin_detail_tabs",
    )
    assert json.loads(str(kwargs["input"])) == {"sourceRunId": "run-1"}
    cleanup_env = kwargs["env"]
    assert isinstance(cleanup_env, dict)
    assert cleanup_env["SEEKTALENT_LIEPIN_OPENCLI_SESSION"].startswith("seektalent-liepin-run-1")
    assert cleanup_env["SEEKTALENT_LIEPIN_OPENCLI_TASK"] == "liepin.search_resumes"
