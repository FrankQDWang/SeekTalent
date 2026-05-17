import json
import subprocess
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from subprocess import CompletedProcess

import pytest
from pydantic import ValidationError

from seektalent.providers.pi_agent.capabilities import (
    DokoBotActionToolManifest,
    DokoBotCapabilities,
    DokoBotCapabilityProbe,
)
from seektalent.providers.pi_agent.contracts import PiArtifactRef, ProtectedArtifactClass
from seektalent.providers.pi_agent.dokobot_client import DokoBotClient, DokoBotExecutionError


def _manifest(**overrides: object) -> DokoBotActionToolManifest:
    payload = {
        "schema_version": "dokobot-action-manifest-v1",
        "manifest_id": "manifest_1",
        "manifest_version": "2026.05.1",
        "provider": "dokobot_compatible",
        "transport": "local_only",
        "declared_operations": {
            "navigate": True,
            "click": True,
            "type_text": True,
            "pagination": True,
            "read_snapshot": True,
            "network_inspection": False,
            "script_evaluation": False,
            "direct_api_replay": False,
            "cookie_header_injection": False,
            "cdp_access": False,
            "stealth_or_proxy_evasion": False,
            "auto_install": False,
            "update_or_config_mutation": False,
            "permission_mutation": False,
            "fallback_mode_mutation": False,
        },
        "forbidden_operations_ack": (
            "network_inspection",
            "script_evaluation",
            "direct_api_replay",
            "cookie_header_injection",
            "cdp_access",
            "stealth_or_proxy_evasion",
            "arbitrary_script_eval",
            "auto_install",
            "update_or_config_mutation",
            "permission_mutation",
            "fallback_mode_mutation",
        ),
        "trust_source": "preconfigured_admin",
        "signature_required": True,
        "manifest_signature": "signature_1",
        "expires_at": datetime.now(UTC) + timedelta(days=30),
        "auto_install_allowed": False,
    }
    payload.update(overrides)
    return DokoBotActionToolManifest(**payload)


def _artifact_ref(content: bytes, artifact_class: ProtectedArtifactClass, policy_id: str) -> PiArtifactRef:
    content_hash = sha256(content).hexdigest()
    return PiArtifactRef(
        artifact_class=artifact_class,
        artifact_ref=f"{artifact_class.value}:{content_hash}",
        content_sha256=content_hash,
        redaction_policy_id=(
            policy_id
            if artifact_class
            in {ProtectedArtifactClass.SAFE_SUMMARY, ProtectedArtifactClass.REDACTED_EVIDENCE}
            else None
        ),
        protection_policy_id=policy_id if artifact_class == ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT else None,
    )


def fake_successful_help(command: list[str]) -> CompletedProcess[str]:
    command_text = " ".join(command)
    if command_text == "dokobot --version":
        return CompletedProcess(command, 0, "2.11.0\n", "")
    if command_text == "dokobot --help":
        return CompletedProcess(command, 0, "Commands:\n  read\n  search\n  close\n", "")
    if command_text == "dokobot read --help":
        return CompletedProcess(
            command,
            0,
            "--format <type> text or chunks\n--session-id <id>\n--screens <n>\n--local\n",
            "",
        )
    return CompletedProcess(command, 1, "", "unexpected command")


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


def test_manifest_with_required_actions_enables_liepin_action_capability() -> None:
    def fake_runner(command: list[str]) -> CompletedProcess[str]:
        command_text = " ".join(command)
        if command_text == "dokobot --version":
            return CompletedProcess(command, 0, "2.11.0\n", "")
        if command_text == "dokobot --help":
            return CompletedProcess(command, 0, "Commands:\n  read\n  search\n  close\n", "")
        if command_text == "dokobot read --help":
            return CompletedProcess(command, 0, "--format <type> text or chunks\n--session-id <id>\n--screens <n>\n", "")
        return CompletedProcess(command, 1, "", "unexpected command")

    manifest = _manifest(manifest_id="dokobot-mcp-browser-tools", manifest_version="2026-05-14")
    capabilities = DokoBotCapabilityProbe(
        run_command=fake_runner,
        action_tool_manifest=manifest,
        trusted_action_manifest_ids={"dokobot-mcp-browser-tools"},
    ).discover()

    assert capabilities.action_manifest_id == "dokobot-mcp-browser-tools"
    assert capabilities.action_manifest_version == "2026-05-14"
    assert capabilities.action_manifest_transport == "local_only"
    assert capabilities.action_manifest_trust_source == "preconfigured_admin"
    assert capabilities.supports_click is True
    assert capabilities.supports_type is True
    assert capabilities.supports_navigation is True
    assert capabilities.supports_pagination_action is True
    assert capabilities.can_execute_liepin_actions is True


def test_read_only_or_partial_manifest_fails_closed() -> None:
    valid = _manifest()
    manifest = _manifest(declared_operations={**valid.declared_operations.model_dump(), "type_text": False})

    capabilities = DokoBotCapabilityProbe(
        run_command=fake_successful_help,
        action_tool_manifest=manifest,
        trusted_action_manifest_ids={"manifest_1"},
    ).discover()

    assert capabilities.can_execute_liepin_actions is False


@pytest.mark.parametrize(
    "operation",
    [
        "network_inspection",
        "script_evaluation",
        "direct_api_replay",
        "cookie_header_injection",
        "cdp_access",
        "stealth_or_proxy_evasion",
        "auto_install",
        "update_or_config_mutation",
        "permission_mutation",
        "fallback_mode_mutation",
    ],
)
def test_manifest_rejects_forbidden_enabled_operations(operation: str) -> None:
    manifest = _manifest()
    operations = {**manifest.declared_operations.model_dump(), operation: True}

    with pytest.raises(ValidationError):
        _manifest(declared_operations=operations)


def test_manifest_rejects_untrusted_expired_or_unsigned_in_production() -> None:
    with pytest.raises(ValidationError):
        _manifest(trust_source="untrusted")

    with pytest.raises(ValidationError):
        _manifest(expires_at=datetime.now(UTC) - timedelta(seconds=1))

    with pytest.raises(ValidationError):
        _manifest(signature_required=True, manifest_signature="")


def test_manifest_rejects_missing_or_extra_declared_operations() -> None:
    valid = _manifest()

    with pytest.raises(ValidationError):
        _manifest(declared_operations={**valid.declared_operations.model_dump(), "unexpected": False})

    incomplete = valid.declared_operations.model_dump()
    del incomplete["pagination"]
    with pytest.raises(ValidationError):
        _manifest(declared_operations=incomplete)


def test_manifest_not_in_trusted_allowlist_fails_closed() -> None:
    manifest = _manifest(manifest_id="not_trusted")

    capabilities = DokoBotCapabilityProbe(
        run_command=fake_successful_help,
        action_tool_manifest=manifest,
        trusted_action_manifest_ids={"manifest_1"},
    ).discover()

    assert capabilities.can_execute_liepin_actions is False
    assert capabilities.capability_error_code == "dokobot_tool_manifest_untrusted"


def test_manifest_without_trusted_allowlist_fails_closed() -> None:
    manifest = _manifest()

    capabilities = DokoBotCapabilityProbe(
        run_command=fake_successful_help,
        action_tool_manifest=manifest,
    ).discover()

    assert capabilities.can_execute_liepin_actions is False
    assert capabilities.capability_error_code == "dokobot_tool_manifest_untrusted"


def test_remote_manifest_does_not_enable_default_local_liepin_actions() -> None:
    manifest = _manifest(transport="remote_e2e_allowed")

    capabilities = DokoBotCapabilityProbe(
        run_command=fake_successful_help,
        action_tool_manifest=manifest,
        trusted_action_manifest_ids={"manifest_1"},
    ).discover()

    assert capabilities.action_manifest_transport == "remote_e2e_allowed"
    assert capabilities.can_execute_liepin_actions is False
    assert capabilities.can_execute_liepin_actions_for_transport("remote_e2e_allowed") is True


def test_action_booleans_without_manifest_do_not_enable_liepin_actions() -> None:
    capabilities = DokoBotCapabilities(
        cli_version="2.11.0",
        supports_read=True,
        supports_click=True,
        supports_type=True,
        supports_navigation=True,
        supports_pagination_action=True,
    )

    assert capabilities.can_execute_liepin_actions is False


def test_probe_fails_closed_when_dokobot_cli_is_missing() -> None:
    def fake_runner(command: list[str]) -> CompletedProcess[str]:
        raise FileNotFoundError("dokobot")

    capabilities = DokoBotCapabilityProbe(run_command=fake_runner).discover()

    assert capabilities.cli_version == "unknown"
    assert capabilities.supports_read is False
    assert capabilities.can_execute_liepin_actions is False
    assert capabilities.capability_error_code == "dokobot_cli_unavailable"


def test_probe_fails_closed_when_dokobot_cli_is_not_executable() -> None:
    def fake_runner(command: list[str]) -> CompletedProcess[str]:
        raise PermissionError("dokobot")

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
    assert "--local" in calls[0]
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


def test_read_url_rejects_invalid_url_before_dokobot_command() -> None:
    calls: list[list[str]] = []

    def fake_runner(command: list[str], process_timeout_seconds: int) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(command, 0, "Candidate summary text", "")

    def fake_writer(content: bytes, artifact_class: ProtectedArtifactClass, policy_id: str) -> PiArtifactRef:
        return _artifact_ref(content, artifact_class, policy_id)

    client = DokoBotClient(run_command=fake_runner, artifact_writer=fake_writer)

    with pytest.raises(ValidationError):
        client.read_url("not a url")

    assert calls == []


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


def test_read_url_remote_mode_must_be_selected_explicitly() -> None:
    calls: list[list[str]] = []

    def fake_runner(command: list[str], process_timeout_seconds: int) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(command, 0, "Candidate summary text", "")

    def fake_writer(content: bytes, artifact_class: ProtectedArtifactClass, policy_id: str) -> PiArtifactRef:
        return _artifact_ref(content, artifact_class, policy_id)

    client = DokoBotClient(
        run_command=fake_runner,
        artifact_writer=fake_writer,
        transport_mode="remote_e2e_allowed",
    )
    client.read_url("https://www.liepin.com/zhaopin/")

    assert "--local" not in calls[0]


def test_local_bridge_failure_does_not_retry_remote() -> None:
    calls: list[list[str]] = []

    def fake_runner(command: list[str], process_timeout_seconds: int) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(command, 1, "", "local bridge unavailable")

    def fake_writer(content: bytes, artifact_class: ProtectedArtifactClass, policy_id: str) -> PiArtifactRef:
        return _artifact_ref(content, artifact_class, policy_id)

    client = DokoBotClient(run_command=fake_runner, artifact_writer=fake_writer)

    with pytest.raises(DokoBotExecutionError) as error:
        client.read_url("https://www.liepin.com/zhaopin/")

    assert error.value.error_code == "dokobot_local_transport_failed"
    assert len(calls) == 1
    assert "--local" in calls[0]


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


def test_read_url_uses_session_id_and_does_not_reuse_tab_by_default() -> None:
    calls: list[list[str]] = []

    def fake_runner(command: list[str], process_timeout_seconds: int) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(command, 0, "Candidate summary text", "")

    def fake_writer(content: bytes, artifact_class: ProtectedArtifactClass, policy_id: str) -> PiArtifactRef:
        return _artifact_ref(content, artifact_class, policy_id)

    client = DokoBotClient(run_command=fake_runner, artifact_writer=fake_writer)
    client.read_url("https://www.liepin.com/zhaopin/", session_id="sess_abc")

    assert "--session-id" in calls[0]
    assert "sess_abc" in calls[0]
    assert "--reuse-tab" not in calls[0]


def test_read_url_parses_nested_vertical_status_from_json_capable_surface() -> None:
    stdout = json.dumps(
        {
            "text": "Candidate summary text",
            "vertical": {"hasMore": True, "stopReason": "limit_reached"},
            "sessionId": "sess_json",
            "screens": 3,
        }
    )

    def fake_runner(command: list[str], process_timeout_seconds: int) -> CompletedProcess[str]:
        return CompletedProcess(command, 0, stdout, "")

    def fake_writer(content: bytes, artifact_class: ProtectedArtifactClass, policy_id: str) -> PiArtifactRef:
        return _artifact_ref(content, artifact_class, policy_id)

    client = DokoBotClient(
        run_command=fake_runner,
        artifact_writer=fake_writer,
        json_capable_surface=True,
    )
    result = client.read_url("https://www.liepin.com/zhaopin/", output_format="chunks")

    assert result.session_id == "sess_json"
    assert result.vertical_has_more is True
    assert result.vertical_stop_reason == "limit_reached"
    assert result.screens_used == 3
    assert result.text_ref is not None
    assert result.chunks_ref is None


def test_public_cli_text_surface_does_not_assume_chunks_json() -> None:
    stdout = json.dumps({"text": "Candidate summary text", "chunks": [{"text": "Candidate summary text"}]})

    def fake_runner(command: list[str], process_timeout_seconds: int) -> CompletedProcess[str]:
        return CompletedProcess(command, 0, stdout, "")

    def fake_writer(content: bytes, artifact_class: ProtectedArtifactClass, policy_id: str) -> PiArtifactRef:
        return _artifact_ref(content, artifact_class, policy_id)

    client = DokoBotClient(run_command=fake_runner, artifact_writer=fake_writer)
    result = client.read_url("https://www.liepin.com/zhaopin/", output_format="chunks")

    assert result.text_ref is not None
    assert result.chunks_ref is None
    assert result.vertical_stop_reason == "unknown"


def test_failed_read_does_not_store_stdout_as_redacted_evidence() -> None:
    written: list[tuple[bytes, ProtectedArtifactClass, str]] = []

    def fake_runner(command: list[str], process_timeout_seconds: int) -> CompletedProcess[str]:
        return CompletedProcess(command, 1, "张三 13800138000 raw resume", "Bearer secret-token")

    def fake_writer(content: bytes, artifact_class: ProtectedArtifactClass, policy_id: str) -> PiArtifactRef:
        written.append((content, artifact_class, policy_id))
        return _artifact_ref(content, artifact_class, policy_id)

    client = DokoBotClient(run_command=fake_runner, artifact_writer=fake_writer)

    with pytest.raises(DokoBotExecutionError) as error:
        client.read_url("https://www.liepin.com/zhaopin/")

    assert "张三" not in str(error.value)
    assert "13800138000" not in str(error.value)
    assert "secret-token" not in str(error.value)
    assert len(written) == 1
    content, artifact_class, policy_id = written[0]
    assert artifact_class == ProtectedArtifactClass.REDACTED_EVIDENCE
    assert policy_id == "dokobot-command-error-redaction-v1"
    assert "张三".encode() not in content
    assert b"13800138000" not in content
    assert b"secret-token" not in content


def test_default_artifact_writer_is_not_fake_persistent_storage() -> None:
    def fake_runner(command: list[str], process_timeout_seconds: int) -> CompletedProcess[str]:
        return CompletedProcess(command, 0, "Candidate summary text", "")

    client = DokoBotClient(run_command=fake_runner)

    with pytest.raises(RuntimeError, match="artifact_writer is required"):
        client.read_url("https://www.liepin.com/zhaopin/")
