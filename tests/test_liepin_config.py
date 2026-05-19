from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from seektalent.config import AppSettings


def test_pi_agent_requires_rpc_command_skill_and_dokobot_tool(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("skill", encoding="utf-8")

    settings = AppSettings(
        _env_file=None,
        liepin_worker_mode="pi_agent",
        liepin_pi_command="pi --mode rpc --no-session",
        liepin_pi_skill_path=str(skill_path),
        liepin_pi_dokobot_tool_name="dokobot",
        liepin_account_binding_secret="runtime-secret",
    )

    assert settings.liepin_worker_mode == "pi_agent"
    assert settings.liepin_pi_command_argv[-2:] == ("--skill", str(skill_path))


def test_pi_agent_rejects_missing_rpc_no_session_or_skill(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("skill", encoding="utf-8")

    with pytest.raises(ValueError):
        AppSettings(
            _env_file=None,
            liepin_worker_mode="pi_agent",
            liepin_pi_command="pi --mode json --no-session",
            liepin_pi_skill_path=str(skill_path),
            liepin_pi_dokobot_tool_name="dokobot",
            liepin_account_binding_secret="runtime-secret",
        )


def test_pi_agent_resolves_relative_skill_path_from_workspace_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skill_path = workspace / "src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("skill", encoding="utf-8")
    old_cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        settings = AppSettings(
            _env_file=None,
            workspace_root=str(workspace),
            liepin_worker_mode="pi_agent",
            liepin_pi_command="pi --mode rpc --no-session",
            liepin_pi_skill_path="src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md",
            liepin_pi_dokobot_tool_name="dokobot",
            liepin_account_binding_secret="runtime-secret",
        )
    finally:
        os.chdir(old_cwd)

    assert settings.liepin_pi_skill_file_path == skill_path
    assert settings.liepin_pi_command_argv[-1] == str(skill_path)


def test_pi_agent_uses_explicit_repo_local_pi_dependency_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skill_path = workspace / "src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md"
    pi_bin = workspace / "apps/web-svelte/node_modules/.bin/pi"
    skill_path.parent.mkdir(parents=True)
    pi_bin.parent.mkdir(parents=True)
    skill_path.write_text("skill", encoding="utf-8")
    pi_bin.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    pi_bin.chmod(0o755)

    settings = AppSettings(
        _env_file=None,
        workspace_root=str(workspace),
        liepin_worker_mode="pi_agent",
        liepin_pi_command=f"{pi_bin} --mode rpc --no-session",
        liepin_pi_skill_path="src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md",
        liepin_pi_dokobot_tool_name="dokobot",
        liepin_account_binding_secret="runtime-secret",
    )

    assert settings.liepin_pi_command_argv[0] == str(pi_bin)


def test_dokobot_action_is_not_a_live_worker_mode() -> None:
    with pytest.raises(ValidationError, match="dokobot_action"):
        AppSettings(_env_file=None, liepin_worker_mode="dokobot_action")


def test_pi_agent_rejects_placeholder_account_binding_secret(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("skill", encoding="utf-8")

    with pytest.raises(ValueError, match="liepin_account_binding_secret"):
        AppSettings(
            _env_file=None,
            liepin_worker_mode="pi_agent",
            liepin_pi_command="pi --mode rpc --no-session",
            liepin_pi_skill_path=str(skill_path),
            liepin_pi_dokobot_tool_name="dokobot",
            liepin_account_binding_secret="local-development",
        )


def test_pi_agent_accepts_optional_mcp_config_path(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    mcp_path = tmp_path / ".pi" / "mcp.json"
    skill_path.write_text("Liepin skill", encoding="utf-8")
    settings = AppSettings(
        _env_file=None,
        workspace_root=str(tmp_path),
        liepin_worker_mode="pi_agent",
        liepin_pi_command="pi --mode rpc --no-session",
        liepin_pi_skill_path=str(skill_path),
        liepin_pi_mcp_config_path=str(mcp_path),
        liepin_pi_dokobot_tool_name="dokobot",
        liepin_account_binding_secret="non-placeholder-secret",
    )

    assert settings.liepin_pi_mcp_config_file_path == mcp_path


def test_empty_pi_mcp_config_path_normalizes_to_none(tmp_path: Path) -> None:
    settings = AppSettings(
        _env_file=None,
        workspace_root=str(tmp_path),
        liepin_pi_mcp_config_path="",
    )

    assert settings.liepin_pi_mcp_config_path is None


def test_pi_agent_model_id_is_explicit_root_env_setting(tmp_path: Path) -> None:
    settings = AppSettings(
        _env_file=None,
        workspace_root=str(tmp_path),
        liepin_pi_model_id="deepseek-v4-flash",
    )

    assert settings.liepin_pi_model_id == "deepseek-v4-flash"
