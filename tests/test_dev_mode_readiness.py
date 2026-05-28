from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydantic import ValidationError
from seektalent.config import AppSettings
from seektalent.dev_mode import build_dev_mode_env_diagnostics, build_dev_mode_status
from seektalent_ui.server import _can_recover_with_dev_mode_env_diagnostics, create_app
from tests.settings_factory import make_settings


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


def test_raw_env_diagnostics_do_not_expose_secret_values(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin.md"
    env = {
        "SEEKTALENT_TEXT_LLM_API_KEY": "sk-secret-value",
        "SEEKTALENT_CTS_TENANT_KEY": "tenant-key-secret",
        "SEEKTALENT_CTS_TENANT_SECRET": "tenant-secret-value",
        "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
        "SEEKTALENT_LIEPIN_PI_COMMAND": "pi --mode rpc --no-session --token secret",
        "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill_path),
        "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": "dokobot",
        "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-binding-secret",
    }

    payload = build_dev_mode_env_diagnostics(env, workspace_root=tmp_path).model_dump(mode="json")
    raw = json.dumps(payload, sort_keys=True)

    assert "sk-secret-value" not in raw
    assert "tenant-key-secret" not in raw
    assert "tenant-secret-value" not in raw
    assert "account-binding-secret" not in raw
    assert "--token secret" not in raw
    assert str(skill_path) not in raw
    assert payload["overallStatus"] == "needs_setup"


def test_raw_env_diagnostics_reports_local_data_root_posture(tmp_path: Path) -> None:
    env = {
        "SEEKTALENT_ARTIFACTS_DIR": str(tmp_path / ".seektalent" / "artifacts"),
        "SEEKTALENT_LLM_CACHE_DIR": str(tmp_path / "repo-cache"),
    }
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    payload = build_dev_mode_env_diagnostics(env, workspace_root=tmp_path)
    roots = {root.name: root for root in payload.dataRoots}

    assert roots["artifacts"].status in {"safe", "unknown"}
    assert roots["llm_cache"].status == "warning"
    assert roots["llm_cache"].reasonCode == "inside_repo"


def test_raw_env_diagnostics_reports_pi_agent_missing_config_without_appsettings(tmp_path: Path) -> None:
    payload = build_dev_mode_env_diagnostics(
        {"SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent"},
        workspace_root=tmp_path,
    )
    components = {component.name: component for component in payload.components}

    assert payload.mode == "raw_env_diagnostics"
    assert payload.overallStatus == "needs_setup"
    assert components["liepin_pi_command"].status == "needs_setup"
    assert components["liepin_pi_command"].reasonCode == "liepin_pi_mcp_adapter_missing"
    assert components["liepin_pi_skill"].status == "needs_setup"
    assert components["liepin_pi_skill"].reasonCode == "liepin_pi_skill_missing"
    assert components["liepin_pi_mcp_config"].status == "needs_setup"
    assert components["liepin_pi_mcp_config"].reasonCode == "liepin_pi_dokobot_mcp_command_missing"
    assert components["liepin_pi_dokobot_mcp"].reasonCode == "liepin_pi_dokobot_mcp_command_missing"
    assert components["liepin_account_binding_secret"].status == "needs_setup"


def test_raw_env_diagnostics_preserves_missing_pi_mcp_adapter_reason(tmp_path: Path) -> None:
    pi_bin = tmp_path / "bin" / "pi"
    pi_bin.parent.mkdir(parents=True)
    pi_bin.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    pi_bin.chmod(0o755)
    provider_extension = tmp_path / "src" / "seektalent" / "providers" / "pi_agent" / "pi_extensions"
    provider_extension.mkdir(parents=True)
    (provider_extension / "bailian_deepseek.ts").write_text("provider", encoding="utf-8")
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("Liepin skill", encoding="utf-8")
    mcp_path = tmp_path / ".pi" / "mcp.json"
    mcp_path.parent.mkdir(parents=True)
    mcp_path.write_text('{"mcpServers":{"dokobot":{"command":"dokobot-mcp","args":[]}}}', encoding="utf-8")

    payload = build_dev_mode_env_diagnostics(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-binding-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": (
                f"{pi_bin} --mode rpc --no-session "
                "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
                "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts"
            ),
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill_path),
            "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": str(mcp_path),
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND": "dokobot-mcp",
            "SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON": '["dokobot_read_page"]',
        },
        workspace_root=tmp_path,
    )
    components = {component.name: component for component in payload.components}
    raw = json.dumps(payload.model_dump(mode="json"), sort_keys=True)

    assert payload.overallStatus == "needs_setup"
    assert components["liepin_pi_command"].status == "needs_setup"
    assert components["liepin_pi_command"].reasonCode == "liepin_pi_mcp_adapter_missing"
    assert components["liepin_pi_skill"].status == "configured"
    assert components["liepin_pi_mcp_config"].status == "configured"
    assert components["liepin_pi_dokobot_mcp"].status == "configured"
    assert str(tmp_path) not in raw


def test_raw_env_diagnostics_reports_configured_project_pi_mcp(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("Liepin skill", encoding="utf-8")
    mcp_path = tmp_path / ".pi" / "mcp.json"
    mcp_path.parent.mkdir(parents=True)
    mcp_path.write_text('{"mcpServers":{"dokobot":{"command":"dokobot-mcp","args":[]}}}', encoding="utf-8")

    payload = build_dev_mode_env_diagnostics(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-binding-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": (
                "pi --mode rpc --no-session "
                "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
                "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts"
            ),
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill_path),
            "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": str(mcp_path),
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND": "dokobot-mcp",
            "SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON": '["dokobot_read_page"]',
        },
        workspace_root=tmp_path,
    )
    components = {component.name: component for component in payload.components}
    raw = json.dumps(payload.model_dump(mode="json"), sort_keys=True)

    assert components["liepin_pi_mcp_config"].status == "configured"
    assert components["liepin_pi_dokobot_mcp"].status == "configured"
    assert str(tmp_path) not in raw


def test_raw_env_diagnostics_reports_configured_opencli_without_pi_mcp(tmp_path: Path) -> None:
    opencli_bin = _write_opencli_extension_files(tmp_path)
    pi_bin = tmp_path / "apps" / "web-svelte" / "node_modules" / ".bin" / "pi"
    pi_bin.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    pi_bin.chmod(0o755)
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("Liepin skill", encoding="utf-8")

    payload = build_dev_mode_env_diagnostics(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND": "opencli",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-binding-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": (
                f"{pi_bin} --mode rpc --no-session "
                "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
                "--extension src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts"
            ),
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill_path),
            "SEEKTALENT_LIEPIN_OPENCLI_COMMAND": str(opencli_bin),
        },
        workspace_root=tmp_path,
    )
    components = {component.name: component for component in payload.components}
    raw = json.dumps(payload.model_dump(mode="json"), sort_keys=True)

    assert components["liepin_pi_command"].status == "configured"
    assert components["liepin_opencli_browser"].status == "configured"
    assert "liepin_pi_mcp_config" not in components
    assert "liepin_pi_dokobot_mcp" not in components
    assert str(tmp_path) not in raw


def test_raw_env_diagnostics_reports_missing_opencli_command_without_pi_mcp_noise(tmp_path: Path) -> None:
    _write_opencli_extension_files(tmp_path)
    pi_bin = tmp_path / "apps" / "web-svelte" / "node_modules" / ".bin" / "pi"
    pi_bin.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    pi_bin.chmod(0o755)
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("Liepin skill", encoding="utf-8")

    payload = build_dev_mode_env_diagnostics(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND": "opencli",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-binding-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": (
                f"{pi_bin} --mode rpc --no-session "
                "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
                "--extension src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts"
            ),
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill_path),
            "SEEKTALENT_LIEPIN_OPENCLI_COMMAND": str(tmp_path / "missing-opencli"),
        },
        workspace_root=tmp_path,
    )
    components = {component.name: component for component in payload.components}

    assert payload.overallStatus == "needs_setup"
    assert components["liepin_opencli_browser"].status == "needs_setup"
    assert components["liepin_opencli_browser"].reasonCode == "liepin_opencli_command_missing"
    assert "liepin_pi_dokobot_mcp" not in components


def test_raw_env_diagnostics_reports_invalid_pi_command(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("Liepin skill", encoding="utf-8")

    payload = build_dev_mode_env_diagnostics(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-binding-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": "pi --no-session",
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill_path),
        },
        workspace_root=tmp_path,
    )
    components = {component.name: component for component in payload.components}

    assert payload.overallStatus == "invalid"
    assert components["liepin_pi_command"].status == "invalid"


def test_server_startup_can_fallback_to_readiness_for_invalid_pi_agent_config(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("Liepin skill", encoding="utf-8")
    env = {
        "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
        "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-binding-secret",
        "SEEKTALENT_LIEPIN_PI_COMMAND": "pi --no-session",
        "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill_path),
    }

    with pytest.raises(ValidationError) as exc_info:
        AppSettings(_env_file=None, workspace_root=str(tmp_path), **{key.removeprefix("SEEKTALENT_").lower(): value for key, value in env.items()})

    assert _can_recover_with_dev_mode_env_diagnostics(exc_info.value, env)


def test_valid_settings_status_reports_configured_components(tmp_path: Path) -> None:
    _write_pi_extension_files(tmp_path)
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("Liepin skill", encoding="utf-8")
    mcp_path = tmp_path / ".pi" / "mcp.json"
    mcp_path.parent.mkdir(parents=True)
    mcp_path.write_text('{"mcpServers":{"dokobot":{"command":"dokobot-mcp","args":[]}}}', encoding="utf-8")
    settings = make_settings(
        workspace_root=str(tmp_path),
        text_llm_api_key="sk-live",
        cts_tenant_key="tenant-key",
        cts_tenant_secret="tenant-secret",
        liepin_worker_mode="pi_agent",
        liepin_pi_command=(
            "pi --mode rpc --no-session "
            "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
            "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts"
        ),
        liepin_pi_skill_path=str(skill_path),
        liepin_pi_mcp_config_path=str(mcp_path),
        liepin_account_binding_secret="non-placeholder-secret",
        liepin_dokobot_mcp_command="dokobot-mcp",
        liepin_dokobot_observed_tools_json='["dokobot_read_page"]',
    )

    payload = build_dev_mode_status(settings)
    components = {component.name: component for component in payload.components}

    assert payload.mode == "settings"
    assert payload.overallStatus in {"ready", "warning"}
    assert components["text_llm"].status == "configured"
    assert components["cts"].status == "configured"
    assert components["liepin_account_binding_secret"].status == "configured"
    assert components["liepin_pi_mcp_config"].status == "configured"
    assert components["liepin_pi_dokobot_mcp"].status == "configured"


def test_dev_mode_status_uses_configured_dokobot_mcp_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
                        "args": ["--stdio"],
                        "lifecycle": "lazy",
                        "directTools": ["read_page", "click", "type_text"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SEEKTALENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("SEEKTALENT_LIEPIN_WORKER_MODE", "pi_agent")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET", "secret")
    monkeypatch.setenv(
        "SEEKTALENT_LIEPIN_PI_COMMAND",
        "pi --mode rpc --no-session "
        "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
        "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts",
    )
    monkeypatch.setenv("SEEKTALENT_LIEPIN_PI_SKILL_PATH", str(skill))
    monkeypatch.setenv("SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH", str(mcp_config))
    monkeypatch.setenv("SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND", "dokobot-mcp")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_DOKOBOT_MCP_ARGS_JSON", '["--stdio"]')
    monkeypatch.setenv("SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON", '["read_page","click","type_text"]')
    monkeypatch.setenv(
        "SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON",
        '["dokobot_read_page","dokobot_click","dokobot_type_text"]',
    )

    status = build_dev_mode_status(AppSettings(_env_file=None))

    components = {item.name: item for item in status.components}
    assert components["liepin_pi_dokobot_mcp"].status == "configured"
    assert components["liepin_pi_dokobot_mcp"].reasonCode == "configured"


def test_dev_server_startup_does_not_bootstrap_project_pi_mcp_config(tmp_path: Path) -> None:
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)

    create_app(settings=settings)

    assert not (tmp_path / ".pi" / "mcp.json").exists()


def test_dev_server_startup_keeps_disabled_liepin_mode_explicit(tmp_path: Path) -> None:
    pi_bin = tmp_path / "apps/web-svelte/node_modules/.bin/pi"
    skill_path = tmp_path / "src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md"
    pi_bin.parent.mkdir(parents=True)
    skill_path.parent.mkdir(parents=True)
    pi_bin.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    skill_path.write_text("Liepin skill", encoding="utf-8")
    pi_bin.chmod(0o755)
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True, liepin_worker_mode="disabled")

    app = create_app(settings=settings)

    assert app.state.settings.liepin_worker_mode == "disabled"
    assert not (tmp_path / ".seektalent" / "liepin_account_binding_secret").exists()
