from __future__ import annotations

import json
import os
import shlex
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from seektalent.config import resolve_path_from_root


PiLocalComponentStatus = Literal["configured", "needs_setup", "invalid", "disabled"]
PiLocalOverallStatus = Literal["configured", "needs_setup", "invalid", "disabled"]
PiMcpInitStatus = Literal["current", "needs_write", "written", "blocked"]

DEFAULT_LIEPIN_PI_COMMAND = "pi --mode rpc --no-session"
DEFAULT_LIEPIN_PI_SKILL_PATH = "src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md"
DEFAULT_DOKOBOT_TOOL_NAME = "dokobot"


@dataclass(frozen=True)
class PiAgentLocalSetupComponent:
    status: PiLocalComponentStatus
    reason_code: str

    def to_public_payload(self) -> dict[str, str]:
        return {"status": self.status, "reasonCode": self.reason_code}


@dataclass(frozen=True)
class PiAgentLocalSetupStatus:
    overall_status: PiLocalOverallStatus
    reason_code: str
    components: dict[str, PiAgentLocalSetupComponent]

    def to_public_payload(self) -> dict[str, object]:
        return {
            "overallStatus": self.overall_status,
            "reasonCode": self.reason_code,
            "components": {name: component.to_public_payload() for name, component in self.components.items()},
        }


@dataclass(frozen=True)
class PiMcpInitResult:
    status: PiMcpInitStatus
    reason_code: str
    changed: bool
    operations: tuple[str, ...] = ()

    def to_public_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reasonCode": self.reason_code,
            "changed": self.changed,
            "target": "project",
            "operations": list(self.operations),
        }


def build_pi_agent_local_setup_status(
    env: Mapping[str, str | None],
    *,
    workspace_root: Path,
    which: Callable[[str], str | None] = shutil.which,
) -> PiAgentLocalSetupStatus:
    workspace = workspace_root.resolve()
    worker_mode = _env_value(env, "SEEKTALENT_LIEPIN_WORKER_MODE") or "disabled"
    if worker_mode != "pi_agent":
        return PiAgentLocalSetupStatus(
            overall_status="disabled",
            reason_code="liepin_pi_disabled",
            components={
                "worker_mode": PiAgentLocalSetupComponent("disabled", "liepin_pi_disabled"),
                "account_binding_secret": PiAgentLocalSetupComponent("disabled", "liepin_pi_disabled"),
                "pi_command": PiAgentLocalSetupComponent("disabled", "liepin_pi_disabled"),
                "pi_skill": PiAgentLocalSetupComponent("disabled", "liepin_pi_disabled"),
                "dokobot_mcp": PiAgentLocalSetupComponent("disabled", "liepin_pi_disabled"),
            },
        )

    dokobot_tool_name = _env_value(env, "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME") or DEFAULT_DOKOBOT_TOOL_NAME
    components = {
        "worker_mode": PiAgentLocalSetupComponent("configured", "configured"),
        "account_binding_secret": _account_secret_component(env),
        "pi_command": _pi_command_component(env, which=which),
        "pi_skill": _pi_skill_component(env, workspace_root=workspace),
        "dokobot_mcp": _dokobot_mcp_component(env, workspace_root=workspace, dokobot_tool_name=dokobot_tool_name),
    }
    return _summarize(components)


def init_project_pi_mcp_config(
    *,
    workspace_root: Path,
    dokobot_tool_name: str,
    write: bool,
    mcp_config_path: Path | None = None,
) -> PiMcpInitResult:
    workspace = workspace_root.resolve()
    project_pi_dir = workspace / ".pi"
    target = _resolve_optional_path(mcp_config_path, workspace_root=workspace) or project_pi_dir / "mcp.json"
    target_resolved = target.resolve(strict=False)
    project_pi_resolved = project_pi_dir.resolve(strict=False)
    if target_resolved != project_pi_resolved and project_pi_resolved not in target_resolved.parents:
        return PiMcpInitResult(
            status="blocked",
            reason_code="liepin_pi_mcp_config_not_project_local",
            changed=False,
        )

    server_name = dokobot_tool_name.strip() or DEFAULT_DOKOBOT_TOOL_NAME
    expected_server = {"command": "dokobot", "args": []}
    if not target.exists():
        payload: dict[str, Any] = {"mcpServers": {}}
        operations = ("create_config", "add_dokobot_server")
    else:
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return PiMcpInitResult(status="blocked", reason_code="liepin_pi_mcp_config_invalid", changed=False)
        if not isinstance(payload, dict) or not isinstance(payload.get("mcpServers"), dict):
            return PiMcpInitResult(status="blocked", reason_code="liepin_pi_mcp_config_invalid", changed=False)
        mcp_servers = payload["mcpServers"]
        assert isinstance(mcp_servers, dict)
        if mcp_servers.get(server_name) == expected_server:
            return PiMcpInitResult(
                status="current",
                reason_code="configured",
                changed=False,
                operations=("no_change",),
            )
        operations = ("add_dokobot_server",) if server_name not in mcp_servers else ("update_dokobot_server",)

    mcp_servers = payload.setdefault("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        return PiMcpInitResult(status="blocked", reason_code="liepin_pi_mcp_config_invalid", changed=False)
    mcp_servers[server_name] = expected_server

    if not write:
        return PiMcpInitResult(
            status="needs_write",
            reason_code="liepin_pi_mcp_config_missing",
            changed=True,
            operations=operations,
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return PiMcpInitResult(
        status="written",
        reason_code="configured",
        changed=True,
        operations=operations,
    )


def _env_value(env: Mapping[str, str | None], key: str) -> str | None:
    value = env.get(key)
    if value is None:
        return None
    text = value.strip()
    return text or None


def _resolve_optional_path(value: str | Path | None, *, workspace_root: Path) -> Path | None:
    if value is None:
        return None
    if isinstance(value, Path):
        path = value
    else:
        text = value.strip()
        if not text:
            return None
        path = Path(text)
    if path.is_absolute():
        return path
    return resolve_path_from_root(str(path), root=workspace_root)


def _account_secret_component(env: Mapping[str, str | None]) -> PiAgentLocalSetupComponent:
    value = _env_value(env, "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET")
    if value and value != "local-development":
        return PiAgentLocalSetupComponent("configured", "configured")
    return PiAgentLocalSetupComponent("needs_setup", "liepin_pi_account_secret_missing")


def _pi_command_component(
    env: Mapping[str, str | None],
    *,
    which: Callable[[str], str | None],
) -> PiAgentLocalSetupComponent:
    command = _env_value(env, "SEEKTALENT_LIEPIN_PI_COMMAND") or DEFAULT_LIEPIN_PI_COMMAND
    try:
        argv = shlex.split(command)
    except ValueError:
        return PiAgentLocalSetupComponent("invalid", "liepin_pi_command_invalid")
    if not argv or _arg_value(argv, "--mode") != "rpc" or "--no-session" not in argv or "--skill" in argv:
        return PiAgentLocalSetupComponent("invalid", "liepin_pi_command_invalid")
    executable = argv[0]
    if _executable_resolves(executable, which=which):
        return PiAgentLocalSetupComponent("configured", "configured")
    return PiAgentLocalSetupComponent("needs_setup", "liepin_pi_command_missing")


def _executable_resolves(executable: str, *, which: Callable[[str], str | None]) -> bool:
    path = Path(executable)
    if path.is_absolute() or os.sep in executable:
        return path.exists() and os.access(path, os.X_OK)
    return which(executable) is not None


def _pi_skill_component(env: Mapping[str, str | None], *, workspace_root: Path) -> PiAgentLocalSetupComponent:
    value = _env_value(env, "SEEKTALENT_LIEPIN_PI_SKILL_PATH") or DEFAULT_LIEPIN_PI_SKILL_PATH
    path = _resolve_optional_path(value, workspace_root=workspace_root)
    if path is not None and path.is_file():
        return PiAgentLocalSetupComponent("configured", "configured")
    return PiAgentLocalSetupComponent("needs_setup", "liepin_pi_skill_missing")


def _dokobot_mcp_component(
    env: Mapping[str, str | None],
    *,
    workspace_root: Path,
    dokobot_tool_name: str,
) -> PiAgentLocalSetupComponent:
    config_path = _resolve_optional_path(
        _env_value(env, "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH"),
        workspace_root=workspace_root,
    ) or workspace_root / ".pi" / "mcp.json"
    if not config_path.exists():
        return PiAgentLocalSetupComponent("needs_setup", "liepin_pi_mcp_config_missing")
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PiAgentLocalSetupComponent("invalid", "liepin_pi_mcp_config_invalid")
    if not isinstance(payload, dict):
        return PiAgentLocalSetupComponent("invalid", "liepin_pi_mcp_config_invalid")
    mcp_servers = payload.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        return PiAgentLocalSetupComponent("invalid", "liepin_pi_mcp_config_invalid")
    server = mcp_servers.get(dokobot_tool_name)
    if not isinstance(server, dict) or not str(server.get("command") or "").strip():
        return PiAgentLocalSetupComponent("needs_setup", "liepin_pi_dokobot_mcp_missing")
    return PiAgentLocalSetupComponent("configured", "configured")


def _arg_value(argv: list[str], flag: str) -> str | None:
    try:
        index = argv.index(flag)
    except ValueError:
        return None
    next_index = index + 1
    if next_index >= len(argv):
        return None
    return argv[next_index]


def _summarize(components: dict[str, PiAgentLocalSetupComponent]) -> PiAgentLocalSetupStatus:
    for component in components.values():
        if component.status == "invalid":
            return PiAgentLocalSetupStatus("invalid", component.reason_code, components)
    for component in components.values():
        if component.status == "needs_setup":
            return PiAgentLocalSetupStatus("needs_setup", component.reason_code, components)
    return PiAgentLocalSetupStatus("configured", "configured", components)
