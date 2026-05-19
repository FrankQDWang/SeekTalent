from __future__ import annotations

import json
from pathlib import Path

from seektalent.providers.pi_agent.local_setup import (
    build_pi_agent_local_setup_status,
    init_project_pi_mcp_config,
)


def test_init_dry_run_does_not_write_project_mcp_config(tmp_path: Path) -> None:
    result = init_project_pi_mcp_config(
        workspace_root=tmp_path,
        dokobot_tool_name="dokobot",
        write=False,
    )

    assert result.status == "needs_write"
    assert result.reason_code == "liepin_pi_mcp_config_missing"
    assert not (tmp_path / ".pi" / "mcp.json").exists()
    assert str(tmp_path) not in json.dumps(result.to_public_payload())


def test_init_write_creates_project_mcp_config(tmp_path: Path) -> None:
    result = init_project_pi_mcp_config(
        workspace_root=tmp_path,
        dokobot_tool_name="dokobot",
        write=True,
    )

    config = tmp_path / ".pi" / "mcp.json"
    payload = json.loads(config.read_text(encoding="utf-8"))
    assert result.status == "written"
    assert payload == {
        "mcpServers": {
            "dokobot": {
                "command": "dokobot",
                "args": [],
            }
        }
    }


def test_init_preserves_existing_mcp_servers(tmp_path: Path) -> None:
    config = tmp_path / ".pi" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"mcpServers": {"other": {"command": "other", "args": ["--x"]}}}),
        encoding="utf-8",
    )

    init_project_pi_mcp_config(
        workspace_root=tmp_path,
        dokobot_tool_name="dokobot",
        write=True,
    )

    payload = json.loads(config.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["other"] == {"command": "other", "args": ["--x"]}
    assert payload["mcpServers"]["dokobot"] == {"command": "dokobot", "args": []}


def test_init_write_refuses_invalid_existing_mcp_config_without_overwriting(tmp_path: Path) -> None:
    config = tmp_path / ".pi" / "mcp.json"
    config.parent.mkdir(parents=True)
    original = "{not-json"
    config.write_text(original, encoding="utf-8")

    result = init_project_pi_mcp_config(
        workspace_root=tmp_path,
        dokobot_tool_name="dokobot",
        write=True,
    )

    assert result.status == "blocked"
    assert result.reason_code == "liepin_pi_mcp_config_invalid"
    assert config.read_text(encoding="utf-8") == original
    public = json.dumps(result.to_public_payload())
    assert str(tmp_path) not in public
    assert original not in public


def test_init_refuses_user_global_pi_config_path(tmp_path: Path) -> None:
    outside = Path.home() / ".pi" / "agent" / "mcp.json"

    result = init_project_pi_mcp_config(
        workspace_root=tmp_path,
        dokobot_tool_name="dokobot",
        write=True,
        mcp_config_path=outside,
    )

    assert result.status == "blocked"
    assert result.reason_code == "liepin_pi_mcp_config_not_project_local"


def test_reports_missing_pi_executable_without_running_dokobot(tmp_path: Path) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": "missing-pi --mode rpc --no-session",
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": "dokobot",
        },
        workspace_root=tmp_path,
        which=lambda _name: None,
    )

    assert status.overall_status == "needs_setup"
    assert status.reason_code == "liepin_pi_command_missing"
    assert status.components["pi_command"].status == "needs_setup"
    assert "missing-pi" not in json.dumps(status.to_public_payload())


def test_reports_explicit_repo_local_pi_dependency_path_as_configured(tmp_path: Path) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    pi_bin = tmp_path / "apps/web-svelte/node_modules/.bin/pi"
    skill.write_text("Liepin skill", encoding="utf-8")
    pi_bin.parent.mkdir(parents=True)
    pi_bin.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    pi_bin.chmod(0o755)

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": f"{pi_bin} --mode rpc --no-session",
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": "dokobot",
        },
        workspace_root=tmp_path,
        which=lambda _name: None,
    )

    assert status.components["pi_command"].status == "configured"


def test_reports_invalid_pi_mcp_config(tmp_path: Path) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")
    config = tmp_path / ".pi" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text("{not-json", encoding="utf-8")

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": "pi --mode rpc --no-session",
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": "dokobot",
            "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": str(config),
        },
        workspace_root=tmp_path,
        which=lambda name: "/usr/local/bin/pi" if name == "pi" else None,
    )

    assert status.overall_status == "invalid"
    assert status.reason_code == "liepin_pi_mcp_config_invalid"
    assert status.components["dokobot_mcp"].reason_code == "liepin_pi_mcp_config_invalid"


def test_reports_missing_dokobot_server_in_pi_mcp_config(tmp_path: Path) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")
    config = tmp_path / ".pi" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"mcpServers": {"other": {"command": "other"}}}), encoding="utf-8")

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": "pi --mode rpc --no-session",
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": "dokobot",
            "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": str(config),
        },
        workspace_root=tmp_path,
        which=lambda name: "/usr/local/bin/pi" if name == "pi" else None,
    )

    assert status.overall_status == "needs_setup"
    assert status.reason_code == "liepin_pi_dokobot_mcp_missing"
    assert status.components["dokobot_mcp"].reason_code == "liepin_pi_dokobot_mcp_missing"


def test_reports_configured_when_pi_and_dokobot_mcp_are_declared(tmp_path: Path) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")
    config = tmp_path / ".pi" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"mcpServers": {"dokobot": {"command": "dokobot", "args": []}}}),
        encoding="utf-8",
    )

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": "pi --mode rpc --no-session",
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": "dokobot",
            "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": str(config),
        },
        workspace_root=tmp_path,
        which=lambda name: f"/usr/local/bin/{name}" if name == "pi" else None,
    )

    public = status.to_public_payload()
    assert status.overall_status == "configured"
    assert public["components"]["pi_command"]["status"] == "configured"
    assert public["components"]["dokobot_mcp"]["status"] == "configured"
    assert str(tmp_path) not in json.dumps(public)
