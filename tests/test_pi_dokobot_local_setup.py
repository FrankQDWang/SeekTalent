from __future__ import annotations

import json
from pathlib import Path

from seektalent.providers.pi_agent.local_setup import (
    build_pi_agent_local_setup_status,
    init_project_pi_mcp_config,
)

VALID_PI_COMMAND = (
    "pi --mode rpc --no-session "
    "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
    "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts"
)


def _write_pi_extension_files(root: Path) -> None:
    provider_extension = root / "src" / "seektalent" / "providers" / "pi_agent" / "pi_extensions"
    provider_extension.mkdir(parents=True, exist_ok=True)
    (provider_extension / "bailian_deepseek.ts").write_text("provider", encoding="utf-8")
    adapter_extension = root / "apps" / "web-svelte" / "node_modules" / "pi-mcp-adapter"
    adapter_extension.mkdir(parents=True, exist_ok=True)
    (adapter_extension / "index.ts").write_text("adapter", encoding="utf-8")


def _write_opencli_extension_files(root: Path) -> Path:
    provider_extension = root / "src" / "seektalent" / "providers" / "pi_agent" / "pi_extensions"
    provider_extension.mkdir(parents=True, exist_ok=True)
    (provider_extension / "bailian_deepseek.ts").write_text("provider", encoding="utf-8")
    (provider_extension / "seektalent_opencli_browser.ts").write_text("opencli", encoding="utf-8")
    opencli_bin = root / "apps" / "web-svelte" / "node_modules" / ".bin" / "opencli"
    opencli_bin.parent.mkdir(parents=True, exist_ok=True)
    opencli_bin.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    opencli_bin.chmod(0o755)
    return opencli_bin


def test_init_dry_run_does_not_write_project_mcp_config(tmp_path: Path) -> None:
    result = init_project_pi_mcp_config(
        workspace_root=tmp_path,
        dokobot_tool_name="dokobot",
        write=False,
        dokobot_mcp_command="dokobot-mcp",
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
        dokobot_mcp_command="dokobot-mcp",
    )

    config = tmp_path / ".pi" / "mcp.json"
    payload = json.loads(config.read_text(encoding="utf-8"))
    assert result.status == "written"
    assert payload == {
        "mcpServers": {
            "dokobot": {
                "command": "dokobot-mcp",
                "args": [],
                "lifecycle": "lazy",
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
        dokobot_mcp_command="dokobot-mcp",
    )

    payload = json.loads(config.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["other"] == {"command": "other", "args": ["--x"]}
    assert payload["mcpServers"]["dokobot"] == {"command": "dokobot-mcp", "args": [], "lifecycle": "lazy"}


def test_init_write_refuses_invalid_existing_mcp_config_without_overwriting(tmp_path: Path) -> None:
    config = tmp_path / ".pi" / "mcp.json"
    config.parent.mkdir(parents=True)
    original = "{not-json"
    config.write_text(original, encoding="utf-8")

    result = init_project_pi_mcp_config(
        workspace_root=tmp_path,
        dokobot_tool_name="dokobot",
        write=True,
        dokobot_mcp_command="dokobot-mcp",
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
        dokobot_mcp_command="dokobot-mcp",
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
            "SEEKTALENT_LIEPIN_PI_COMMAND": (
                "missing-pi --mode rpc --no-session "
                "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
                "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts"
            ),
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
    _write_pi_extension_files(tmp_path)
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
            "SEEKTALENT_LIEPIN_PI_COMMAND": (
                f"{pi_bin} --mode rpc --no-session "
                "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
                "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts"
            ),
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": "dokobot",
        },
        workspace_root=tmp_path,
        which=lambda _name: None,
    )

    assert status.components["pi_command"].status == "configured"


def test_reports_invalid_pi_mcp_config(tmp_path: Path) -> None:
    _write_pi_extension_files(tmp_path)
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")
    config = tmp_path / ".pi" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text("{not-json", encoding="utf-8")

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": VALID_PI_COMMAND,
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": "dokobot",
            "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": str(config),
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND": "dokobot-mcp",
            "SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON": '["dokobot_read_page"]',
        },
        workspace_root=tmp_path,
        which=lambda name: "/usr/local/bin/pi" if name == "pi" else None,
    )

    assert status.overall_status == "invalid"
    assert status.reason_code == "liepin_pi_mcp_config_invalid"
    assert status.components["dokobot_mcp"].reason_code == "liepin_pi_mcp_config_invalid"


def test_reports_missing_dokobot_server_in_pi_mcp_config(tmp_path: Path) -> None:
    _write_pi_extension_files(tmp_path)
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")
    config = tmp_path / ".pi" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"mcpServers": {"other": {"command": "other"}}}), encoding="utf-8")

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": VALID_PI_COMMAND,
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": "dokobot",
            "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": str(config),
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND": "dokobot-mcp",
            "SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON": '["dokobot_read_page"]',
        },
        workspace_root=tmp_path,
        which=lambda name: "/usr/local/bin/pi" if name == "pi" else None,
    )

    assert status.overall_status == "needs_setup"
    assert status.reason_code == "liepin_pi_dokobot_mcp_missing"
    assert status.components["dokobot_mcp"].reason_code == "liepin_pi_dokobot_mcp_missing"


def test_reports_configured_when_pi_and_dokobot_mcp_are_declared(tmp_path: Path) -> None:
    _write_pi_extension_files(tmp_path)
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")
    config = tmp_path / ".pi" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"mcpServers": {"dokobot": {"command": "dokobot-mcp", "args": []}}}),
        encoding="utf-8",
    )

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": VALID_PI_COMMAND,
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": "dokobot",
            "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": str(config),
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND": "dokobot-mcp",
            "SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON": '["dokobot_read_page"]',
        },
        workspace_root=tmp_path,
        which=lambda name: f"/usr/local/bin/{name}" if name == "pi" else None,
    )

    public = status.to_public_payload()
    assert status.overall_status == "configured"
    assert public["components"]["pi_command"]["status"] == "configured"
    assert public["components"]["dokobot_mcp"]["status"] == "configured"
    assert str(tmp_path) not in json.dumps(public)


def test_static_setup_reports_configured_opencli_without_dokobot_mcp(tmp_path: Path) -> None:
    opencli_bin = _write_opencli_extension_files(tmp_path)
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND": "opencli",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": (
                "pi --mode rpc --no-session "
                "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
                "--extension src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts"
            ),
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_OPENCLI_COMMAND": str(opencli_bin),
        },
        workspace_root=tmp_path,
        which=lambda name: f"/usr/local/bin/{name}" if name == "pi" else None,
    )

    public = status.to_public_payload()
    assert status.overall_status == "configured"
    assert public["components"]["pi_command"]["status"] == "configured"
    assert public["components"]["opencli_browser"]["status"] == "configured"
    assert "dokobot_mcp" not in public["components"]
    assert str(tmp_path) not in json.dumps(public)


def test_static_setup_reports_missing_opencli_extension_in_opencli_mode(tmp_path: Path) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND": "opencli",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": (
                "pi --mode rpc --no-session "
                "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts"
            ),
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_OPENCLI_COMMAND": str(tmp_path / "apps/web-svelte/node_modules/.bin/opencli"),
        },
        workspace_root=tmp_path,
        which=lambda name: f"/usr/local/bin/{name}" if name == "pi" else None,
    )

    assert status.overall_status == "needs_setup"
    assert status.components["pi_command"].reason_code == "liepin_opencli_source_policy_missing"


def test_init_reports_missing_dokobot_mcp_command_without_writing(tmp_path: Path) -> None:
    result = init_project_pi_mcp_config(
        workspace_root=tmp_path,
        dokobot_tool_name="dokobot",
        write=True,
        dokobot_mcp_command=None,
        dokobot_mcp_args=(),
        dokobot_direct_tools=(),
    )

    assert result.status == "blocked"
    assert result.reason_code == "liepin_pi_dokobot_mcp_command_missing"
    assert not (tmp_path / ".pi" / "mcp.json").exists()


def test_init_writes_explicit_dokobot_mcp_command_and_direct_tools(tmp_path: Path) -> None:
    result = init_project_pi_mcp_config(
        workspace_root=tmp_path,
        dokobot_tool_name="dokobot",
        write=True,
        dokobot_mcp_command="dokobot-mcp",
        dokobot_mcp_args=("--stdio",),
        dokobot_direct_tools=("read_page", "click", "type_text"),
    )

    payload = json.loads((tmp_path / ".pi" / "mcp.json").read_text(encoding="utf-8"))
    assert result.status == "written"
    assert payload["mcpServers"]["dokobot"] == {
        "command": "dokobot-mcp",
        "args": ["--stdio"],
        "lifecycle": "lazy",
        "directTools": ["read_page", "click", "type_text"],
    }


def test_static_setup_reports_missing_dokobot_mcp_command(tmp_path: Path) -> None:
    _write_pi_extension_files(tmp_path)
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": (
                "pi --mode rpc --no-session "
                "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
                "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts"
            ),
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": "dokobot",
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME": "dokobot",
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND": "",
        },
        workspace_root=tmp_path,
        which=lambda name: "/usr/local/bin/pi" if name == "pi" else None,
    )

    assert status.overall_status == "needs_setup"
    assert status.reason_code == "liepin_pi_dokobot_mcp_command_missing"
    assert status.components["dokobot_mcp"].reason_code == "liepin_pi_dokobot_mcp_command_missing"
    assert str(tmp_path) not in json.dumps(status.to_public_payload())


def test_static_setup_reports_missing_pi_mcp_adapter_extension(tmp_path: Path) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": (
                "pi --mode rpc --no-session "
                "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts"
            ),
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND": "dokobot-mcp",
            "SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON": '["dokobot_read_page"]',
        },
        workspace_root=tmp_path,
        which=lambda name: "/usr/local/bin/pi" if name == "pi" else None,
    )

    assert status.overall_status == "needs_setup"
    assert status.components["pi_command"].reason_code == "liepin_pi_mcp_adapter_missing"


def test_static_setup_reports_missing_pi_mcp_adapter_file(tmp_path: Path) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")
    provider_extension = tmp_path / "src" / "seektalent" / "providers" / "pi_agent" / "pi_extensions"
    provider_extension.mkdir(parents=True)
    (provider_extension / "bailian_deepseek.ts").write_text("provider", encoding="utf-8")

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": (
                "pi --mode rpc --no-session "
                "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
                "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts"
            ),
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND": "dokobot-mcp",
            "SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON": '["dokobot_read_page"]',
        },
        workspace_root=tmp_path,
        which=lambda name: "/usr/local/bin/pi" if name == "pi" else None,
    )

    assert status.overall_status == "needs_setup"
    assert status.components["pi_command"].reason_code == "liepin_pi_mcp_adapter_missing"


def test_static_setup_reports_missing_dokobot_tool_names(tmp_path: Path) -> None:
    _write_pi_extension_files(tmp_path)
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")
    mcp_config = tmp_path / ".pi" / "mcp.json"
    mcp_config.parent.mkdir()
    mcp_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "dokobot": {
                        "command": "dokobot-mcp",
                        "args": [],
                        "lifecycle": "lazy",
                        "directTools": ["read_page"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": (
                "pi --mode rpc --no-session "
                "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
                "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts"
            ),
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": str(mcp_config),
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME": "dokobot",
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND": "dokobot-mcp",
            "SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON": '["read_page"]',
            "SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON": "[]",
        },
        workspace_root=tmp_path,
        which=lambda name: "/usr/local/bin/pi" if name == "pi" else None,
    )

    assert status.overall_status == "needs_setup"
    assert status.reason_code == "liepin_pi_dokobot_mcp_tool_names_missing"


def test_static_setup_reports_dokobot_mcp_config_mismatch(tmp_path: Path) -> None:
    _write_pi_extension_files(tmp_path)
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")
    mcp_config = tmp_path / ".pi" / "mcp.json"
    mcp_config.parent.mkdir()
    mcp_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "dokobot": {
                        "command": "dokobot-mcp",
                        "args": ["--old"],
                        "lifecycle": "lazy",
                        "directTools": ["old_read"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": (
                "pi --mode rpc --no-session "
                "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
                "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts"
            ),
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": str(mcp_config),
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME": "dokobot",
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND": "dokobot-mcp",
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_ARGS_JSON": '["--stdio"]',
            "SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON": '["read_page"]',
            "SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON": '["dokobot_read_page"]',
        },
        workspace_root=tmp_path,
        which=lambda name: "/usr/local/bin/pi" if name == "pi" else None,
    )

    assert status.overall_status == "needs_setup"
    assert status.components["dokobot_mcp"].reason_code == "liepin_pi_dokobot_mcp_config_mismatch"


def test_dev_launcher_mentions_pinned_pi_mcp_adapter() -> None:
    script = Path("scripts/start-dev-workbench.sh").read_text(encoding="utf-8")

    assert "node_modules/pi-mcp-adapter/index.ts" in script
    assert "node_modules/.bin/opencli" in script
    assert "seektalent_opencli_browser.ts" in script
    assert "--extension $PI_MCP_ADAPTER_EXTENSION" in script
    assert "--extension $PI_OPENCLI_EXTENSION" in script
    assert "PI_MCP_ADAPTER_EXTENSION_ARG" in script
    assert 'NODE_PATH="$WEB_DIR/node_modules"' in script
    assert "SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND" in script
    assert "SEEKTALENT_LIEPIN_OPENCLI_COMMAND" in script
    assert "SEEKTALENT_LIEPIN_OPENCLI_LEASE_DIR" in script
    assert '"$OPENCLI_BIN" daemon restart' in script
    assert "opencli_browser_cli cleanup_orphaned_tabs" in script
    assert '{"force":true}' in script
    assert "OpenCLI browser bridge daemon did not start; Liepin OpenCLI source will fail closed." in script
    assert "SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME" in script
    assert "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND" in script
    assert "SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON" in script
    assert "SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON" in script
    assert "seektalent pi-agent init" not in script
    assert "--write" not in script
    assert 'if [[ "$BROWSER_ACTION_BACKEND" != "opencli" && -z "$DOKOBOT_MCP_COMMAND" ]]; then' in script
    assert "Pi MCP adapter is missing; starting Workbench with Liepin browser channel blocked." in script
    assert 'Repo-local Pi MCP adapter is missing: apps/web-svelte/node_modules/pi-mcp-adapter/index.ts" >&2\n  exit 1' not in script
