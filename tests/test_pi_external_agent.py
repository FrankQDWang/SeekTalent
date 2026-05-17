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

    assert "Use DokoBot only through the Pi runtime" in skill
    assert "Do not ask for cookies" in skill
    assert "Do not open candidate detail pages in card mode" in skill
    assert "Return exactly one JSON object" in skill
    assert "SEEKTALENT_PI_ARTIFACT_ROOT" in skill
    assert "provider_candidate_key_material_ref" in skill
