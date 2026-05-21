from __future__ import annotations

import io
import json
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
