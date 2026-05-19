from __future__ import annotations

import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from seektalent.config import AppSettings, evaluate_local_data_root_policy, resolve_path_from_root
from seektalent.providers.pi_agent.local_setup import build_pi_agent_local_setup_status


DevModeComponentStatus = Literal["configured", "missing", "needs_setup", "invalid", "ready", "warning", "safe", "unknown"]
DevModeOverallStatus = Literal["ready", "warning", "needs_setup", "invalid"]


class DevModeComponentStatusItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    status: DevModeComponentStatus
    reasonCode: str | None = None
    authNote: str | None = None


class DevModeDataRootStatusItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    status: Literal["safe", "warning", "error", "unknown"]
    reasonCode: str


class DevModeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["settings", "raw_env_diagnostics"]
    overallStatus: DevModeOverallStatus
    components: list[DevModeComponentStatusItem] = Field(default_factory=list)
    dataRoots: list[DevModeDataRootStatusItem] = Field(default_factory=list)


def build_dev_mode_env_diagnostics(env: Mapping[str, str | None], *, workspace_root: Path) -> DevModeStatus:
    worker_mode = _env_text(env, "SEEKTALENT_LIEPIN_WORKER_MODE") or "disabled"
    components = [
        _component(
            "text_llm",
            "Text LLM",
            "configured" if _env_text(env, "SEEKTALENT_TEXT_LLM_API_KEY") else "missing",
        ),
        _component(
            "cts",
            "CTS",
            "configured"
            if _env_text(env, "SEEKTALENT_CTS_TENANT_KEY") and _env_text(env, "SEEKTALENT_CTS_TENANT_SECRET")
            else "missing",
        ),
        _component("liepin_worker_mode", "Liepin worker mode", "configured" if worker_mode == "pi_agent" else "missing"),
        _component(
            "liepin_pi_command",
            "Pi RPC command",
            _raw_pi_command_status(env, worker_mode=worker_mode),
        ),
        _component(
            "liepin_pi_skill",
            "Liepin Pi skill",
            _raw_skill_status(env, workspace_root=workspace_root, worker_mode=worker_mode),
        ),
        _component(
            "liepin_pi_dokobot_tool",
            "DokoBot tool",
            "configured" if _env_text(env, "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME") or worker_mode == "pi_agent" else "missing",
        ),
        _component(
            "liepin_account_binding_secret",
            "Liepin account binding",
            "configured"
            if _non_placeholder(_env_text(env, "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET"))
            else ("needs_setup" if worker_mode == "pi_agent" else "missing"),
        ),
    ]
    components.extend(_pi_mcp_components_from_env(env, workspace_root=workspace_root))
    data_roots = _data_roots_from_values(
        workspace_root=workspace_root,
        artifacts_dir=_env_text(env, "SEEKTALENT_ARTIFACTS_DIR") or "artifacts",
        llm_cache_dir=_env_text(env, "SEEKTALENT_LLM_CACHE_DIR") or ".seektalent/cache",
    )
    return DevModeStatus(
        mode="raw_env_diagnostics",
        overallStatus=_overall_status(components, data_roots),
        components=components,
        dataRoots=data_roots,
    )


def build_dev_mode_status(settings: AppSettings) -> DevModeStatus:
    liepin_pi_enabled = settings.liepin_worker_mode == "pi_agent"
    components = [
        _component("text_llm", "Text LLM", "configured" if settings.text_llm_api_key else "missing"),
        _component("cts", "CTS", "configured" if settings.cts_tenant_key and settings.cts_tenant_secret else "missing"),
        _component("liepin_worker_mode", "Liepin worker mode", "configured" if liepin_pi_enabled else "missing"),
        _component("liepin_pi_command", "Pi RPC command", "configured" if liepin_pi_enabled else "missing"),
        _component("liepin_pi_skill", "Liepin Pi skill", "configured" if liepin_pi_enabled else "missing"),
        _component(
            "liepin_pi_dokobot_tool",
            "DokoBot tool",
            "configured" if liepin_pi_enabled and settings.liepin_pi_dokobot_tool_name else "missing",
        ),
        _component(
            "liepin_account_binding_secret",
            "Liepin account binding",
            "configured"
            if liepin_pi_enabled and _non_placeholder(settings.liepin_account_binding_secret)
            else ("needs_setup" if liepin_pi_enabled else "missing"),
        ),
    ]
    components.extend(_pi_mcp_components_from_settings(settings))
    data_roots = _data_roots_from_values(
        workspace_root=settings.project_root,
        artifacts_dir=settings.artifacts_dir or "artifacts",
        llm_cache_dir=settings.llm_cache_dir or ".seektalent/cache",
    )
    return DevModeStatus(
        mode="settings",
        overallStatus=_overall_status(components, data_roots),
        components=components,
        dataRoots=data_roots,
    )


def _pi_mcp_components_from_env(
    env: Mapping[str, str | None],
    *,
    workspace_root: Path,
) -> list[DevModeComponentStatusItem]:
    status = build_pi_agent_local_setup_status(env, workspace_root=workspace_root)
    return _pi_mcp_components_from_reason(status.components["dokobot_mcp"].reason_code)


def _pi_mcp_components_from_settings(settings: AppSettings) -> list[DevModeComponentStatusItem]:
    env = {
        "SEEKTALENT_LIEPIN_WORKER_MODE": settings.liepin_worker_mode,
        "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": settings.liepin_account_binding_secret,
        "SEEKTALENT_LIEPIN_PI_COMMAND": settings.liepin_pi_command,
        "SEEKTALENT_LIEPIN_PI_SKILL_PATH": settings.liepin_pi_skill_path,
        "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": settings.liepin_pi_mcp_config_path,
        "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": settings.liepin_pi_dokobot_tool_name,
    }
    return _pi_mcp_components_from_env(env, workspace_root=settings.project_root)


def _pi_mcp_components_from_reason(reason_code: str) -> list[DevModeComponentStatusItem]:
    if reason_code == "liepin_pi_disabled":
        config_status: DevModeComponentStatus = "missing"
        tool_status: DevModeComponentStatus = "missing"
    elif reason_code == "liepin_pi_mcp_config_invalid":
        config_status = "invalid"
        tool_status = "invalid"
    elif reason_code == "liepin_pi_mcp_config_missing":
        config_status = "needs_setup"
        tool_status = "needs_setup"
    elif reason_code == "liepin_pi_dokobot_mcp_missing":
        config_status = "configured"
        tool_status = "needs_setup"
    else:
        config_status = "configured"
        tool_status = "configured"
    return [
        _component("liepin_pi_mcp_config", "Pi MCP config", config_status, reason_code=reason_code),
        _component("liepin_pi_dokobot_mcp", "DokoBot MCP", tool_status, reason_code=reason_code),
    ]


def _component(
    name: str,
    label: str,
    status: DevModeComponentStatus,
    *,
    reason_code: str | None = None,
    auth_note: str | None = None,
) -> DevModeComponentStatusItem:
    return DevModeComponentStatusItem(name=name, label=label, status=status, reasonCode=reason_code, authNote=auth_note)


def _data_roots_from_values(
    *,
    workspace_root: Path,
    artifacts_dir: str,
    llm_cache_dir: str,
) -> list[DevModeDataRootStatusItem]:
    return [
        _data_root("artifacts", "Artifacts", artifacts_dir, workspace_root=workspace_root),
        _data_root("llm_cache", "LLM cache", llm_cache_dir, workspace_root=workspace_root),
    ]


def _data_root(name: str, label: str, value: str, *, workspace_root: Path) -> DevModeDataRootStatusItem:
    path = resolve_path_from_root(value, root=workspace_root)
    if ".seektalent" in path.parts:
        return DevModeDataRootStatusItem(name=name, label=label, status="safe", reasonCode="local_data_root")
    policy = evaluate_local_data_root_policy(path, runtime_mode="dev")
    return DevModeDataRootStatusItem(name=name, label=label, status=policy.status, reasonCode=policy.reason_code)


def _overall_status(
    components: list[DevModeComponentStatusItem],
    data_roots: list[DevModeDataRootStatusItem],
) -> DevModeOverallStatus:
    component_statuses = {item.status for item in components}
    root_statuses = {item.status for item in data_roots}
    if "invalid" in component_statuses or "error" in root_statuses:
        return "invalid"
    if component_statuses.intersection({"needs_setup", "missing"}):
        return "needs_setup"
    if "warning" in root_statuses:
        return "warning"
    return "ready"


def _env_text(env: Mapping[str, str | None], key: str) -> str | None:
    value = env.get(key)
    if value is None:
        return None
    text = value.strip()
    return text or None


def _raw_skill_status(
    env: Mapping[str, str | None],
    *,
    workspace_root: Path,
    worker_mode: str,
) -> DevModeComponentStatus:
    value = _env_text(env, "SEEKTALENT_LIEPIN_PI_SKILL_PATH")
    if value:
        path = resolve_path_from_root(value, root=workspace_root)
        return "configured" if path.exists() else "needs_setup"
    return "configured" if worker_mode == "pi_agent" else "missing"


def _raw_pi_command_status(env: Mapping[str, str | None], *, worker_mode: str) -> DevModeComponentStatus:
    value = _env_text(env, "SEEKTALENT_LIEPIN_PI_COMMAND")
    if not value and worker_mode != "pi_agent":
        return "missing"
    command = value or "pi --mode rpc --no-session"
    try:
        argv = shlex.split(command)
    except ValueError:
        return "invalid"
    if not argv:
        return "missing"
    if _arg_value(argv, "--mode") != "rpc":
        return "invalid"
    if "--no-session" not in argv:
        return "invalid"
    if "--skill" in argv:
        return "invalid"
    return "configured"


def _arg_value(argv: list[str], flag: str) -> str | None:
    try:
        index = argv.index(flag)
    except ValueError:
        return None
    next_index = index + 1
    if next_index >= len(argv):
        return None
    return argv[next_index]


def _non_placeholder(value: str | None) -> bool:
    return bool(value and value != "local-development")
