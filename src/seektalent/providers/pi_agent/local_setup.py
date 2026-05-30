from __future__ import annotations

import json
import os
import shlex
import shutil
from collections.abc import Callable, Mapping, Sequence
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
                "opencli_browser": PiAgentLocalSetupComponent("disabled", "liepin_pi_disabled"),
            },
        )

    components = {
        "worker_mode": PiAgentLocalSetupComponent("configured", "configured"),
        "account_binding_secret": _account_secret_component(env),
        "pi_command": _pi_command_component(env, workspace_root=workspace, which=which),
        "pi_skill": _pi_skill_component(env, workspace_root=workspace),
    }
    dokobot_tool_name = _env_value(env, "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME") or DEFAULT_DOKOBOT_TOOL_NAME
    components["dokobot_mcp"] = _dokobot_mcp_component(
        env,
        workspace_root=workspace,
        dokobot_tool_name=dokobot_tool_name,
    )
    return _summarize(components)


def init_project_pi_mcp_config(
    *,
    workspace_root: Path,
    dokobot_tool_name: str,
    write: bool,
    mcp_config_path: Path | None = None,
    dokobot_mcp_command: str | None = None,
    dokobot_mcp_args: tuple[str, ...] = (),
    dokobot_direct_tools: tuple[str, ...] = (),
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
    command = (dokobot_mcp_command or "").strip()
    if not command:
        return PiMcpInitResult(
            status="blocked",
            reason_code="liepin_pi_dokobot_mcp_command_missing",
            changed=False,
        )
    expected_server: dict[str, object] = {
        "command": command,
        "args": list(dokobot_mcp_args),
        "lifecycle": "lazy",
    }
    if dokobot_direct_tools:
        expected_server["directTools"] = list(dokobot_direct_tools)
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
    workspace_root: Path,
    which: Callable[[str], str | None],
) -> PiAgentLocalSetupComponent:
    command = _env_value(env, "SEEKTALENT_LIEPIN_PI_COMMAND") or DEFAULT_LIEPIN_PI_COMMAND
    try:
        argv = shlex.split(command)
    except ValueError:
        return PiAgentLocalSetupComponent("invalid", "liepin_pi_command_invalid")
    if not argv or _arg_value(argv, "--mode") != "rpc" or "--no-session" not in argv or "--skill" in argv:
        return PiAgentLocalSetupComponent("invalid", "liepin_pi_command_invalid")
    extensions = _extension_values(argv)
    adapter_extension = _extension_matching(extensions, "pi-mcp-adapter/index.ts")
    if adapter_extension is None:
        return PiAgentLocalSetupComponent("needs_setup", "liepin_pi_mcp_adapter_missing")
    if not any("pi_extensions/bailian_deepseek.ts" in extension for extension in extensions):
        return PiAgentLocalSetupComponent("invalid", "liepin_pi_command_invalid")
    executable = argv[0]
    if not _executable_resolves(executable, which=which):
        return PiAgentLocalSetupComponent("needs_setup", "liepin_pi_command_missing")
    if not _extension_file_exists(adapter_extension, workspace_root=workspace_root):
        return PiAgentLocalSetupComponent("needs_setup", "liepin_pi_mcp_adapter_missing")
    return PiAgentLocalSetupComponent("configured", "configured")


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
    configured_server_name = _env_value(env, "SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME") or dokobot_tool_name
    configured_command = _env_value(env, "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND")
    if not configured_command:
        return PiAgentLocalSetupComponent("needs_setup", "liepin_pi_dokobot_mcp_command_missing")
    try:
        expected_args = _json_string_tuple_env(env, "SEEKTALENT_LIEPIN_DOKOBOT_MCP_ARGS_JSON")
        expected_direct_tools = _json_string_tuple_env(env, "SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON")
        expected_observed_tools = _json_string_tuple_env(env, "SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON")
    except ValueError:
        return PiAgentLocalSetupComponent("invalid", "liepin_pi_mcp_config_invalid")
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
    server = mcp_servers.get(configured_server_name)
    if not isinstance(server, dict) or not str(server.get("command") or "").strip():
        return PiAgentLocalSetupComponent("needs_setup", "liepin_pi_dokobot_mcp_missing")
    if str(server.get("command") or "").strip() != configured_command:
        return PiAgentLocalSetupComponent("needs_setup", "liepin_pi_dokobot_mcp_missing")
    server_args = tuple(str(item).strip() for item in server.get("args") or ())
    server_direct_tools = tuple(str(item).strip() for item in server.get("directTools") or ())
    if server_args != expected_args or server_direct_tools != expected_direct_tools:
        return PiAgentLocalSetupComponent("needs_setup", "liepin_pi_dokobot_mcp_config_mismatch")
    if not expected_observed_tools:
        return PiAgentLocalSetupComponent("needs_setup", "liepin_pi_dokobot_mcp_tool_names_missing")
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


def _extension_values(argv: Sequence[str]) -> tuple[str, ...]:
    values: list[str] = []
    for index, part in enumerate(argv):
        if part == "--extension" and index + 1 < len(argv):
            values.append(argv[index + 1])
        elif part.startswith("--extension="):
            values.append(part.split("=", 1)[1])
    return tuple(values)


def _extension_matching(extensions: Sequence[str], marker: str) -> str | None:
    for extension in extensions:
        if marker in extension:
            return extension
    return None


def _extension_file_exists(extension: str, *, workspace_root: Path) -> bool:
    path = Path(extension)
    if not path.is_absolute():
        path = resolve_path_from_root(extension, root=workspace_root)
    return path.is_file()


def _json_string_tuple_env(env: Mapping[str, str | None], key: str) -> tuple[str, ...]:
    text = _env_value(env, key)
    if text is None:
        return ()
    return _parse_json_string_tuple(text, key=key)


def _parse_json_string_tuple(text: str, *, key: str) -> tuple[str, ...]:
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{key} must be a JSON array of strings") from exc
    if not isinstance(loaded, list) or not all(isinstance(item, str) and item.strip() for item in loaded):
        raise ValueError(f"{key} must be a JSON array of non-empty strings")
    return tuple(item.strip() for item in loaded)


def _summarize(components: dict[str, PiAgentLocalSetupComponent]) -> PiAgentLocalSetupStatus:
    for component in components.values():
        if component.status == "invalid":
            return PiAgentLocalSetupStatus("invalid", component.reason_code, components)
    for component in components.values():
        if component.status == "needs_setup":
            return PiAgentLocalSetupStatus("needs_setup", component.reason_code, components)
    return PiAgentLocalSetupStatus("configured", "configured", components)
