from __future__ import annotations

import shlex
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from seektalent.config import DEFAULT_LIEPIN_OPENCLI_COMMAND, AppSettings, evaluate_local_data_root_policy, resolve_path_from_root


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
    provider_name = _env_text(env, "SEEKTALENT_PROVIDER_NAME") or "liepin"
    worker_mode = _env_text(env, "SEEKTALENT_LIEPIN_WORKER_MODE") or "disabled"
    browser_backend = _browser_action_backend(env)
    opencli_enabled = worker_mode == "opencli" and browser_backend == "opencli"
    components = [
        _component(
            "text_llm",
            "Text LLM",
            "configured" if _env_text(env, "SEEKTALENT_TEXT_LLM_API_KEY") else "missing",
        ),
        _component(
            "liepin_worker_mode",
            "Liepin worker mode",
            "configured" if worker_mode == "opencli" else "missing",
        ),
        _opencli_browser_component(
            opencli_enabled=opencli_enabled,
            command_status=_command_status(
                _env_text(env, "SEEKTALENT_LIEPIN_OPENCLI_COMMAND") or DEFAULT_LIEPIN_OPENCLI_COMMAND,
                root=workspace_root,
            ),
        ),
        _component(
            "liepin_account_binding_secret",
            "Liepin account binding",
            "configured"
            if _non_placeholder(_env_text(env, "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET"))
            else ("needs_setup" if worker_mode == "opencli" else "missing"),
        ),
    ]
    if provider_name == "cts":
        components.insert(
            1,
            _component(
                "cts",
                "CTS",
                "configured"
                if _env_text(env, "SEEKTALENT_CTS_TENANT_KEY") and _env_text(env, "SEEKTALENT_CTS_TENANT_SECRET")
                else "missing",
            ),
        )
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
    liepin_opencli_enabled = settings.liepin_worker_mode == "opencli" and settings.liepin_browser_action_backend == "opencli"
    components = [
        _component("text_llm", "Text LLM", "configured" if settings.text_llm_api_key else "missing"),
        _component("liepin_worker_mode", "Liepin worker mode", "configured" if settings.liepin_worker_mode == "opencli" else "missing"),
        _opencli_browser_component(
            opencli_enabled=liepin_opencli_enabled,
            command_status=_command_status(settings.liepin_opencli_command, root=settings.code_base_root),
        ),
        _component(
            "liepin_account_binding_secret",
            "Liepin account binding",
            "configured"
            if liepin_opencli_enabled and _non_placeholder(settings.liepin_account_binding_secret)
            else ("needs_setup" if liepin_opencli_enabled else "missing"),
        ),
    ]
    if settings.provider_name == "cts":
        components.insert(
            1,
            _component("cts", "CTS", "configured" if settings.cts_tenant_key and settings.cts_tenant_secret else "missing"),
        )
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


def _opencli_browser_component(
    *,
    opencli_enabled: bool,
    command_status: tuple[Literal["configured", "missing", "invalid"], str],
) -> DevModeComponentStatusItem:
    if not opencli_enabled:
        return _component(
            "liepin_opencli_browser",
            "Liepin browser channel",
            "missing",
            reason_code="liepin_opencli_backend_disabled",
        )
    status, reason_code = command_status
    if status != "configured":
        return _component(
            "liepin_opencli_browser",
            "Liepin browser channel",
            "needs_setup" if status == "missing" else "invalid",
            reason_code=reason_code,
        )
    return _component(
        "liepin_opencli_browser",
        "Liepin browser channel",
        "configured",
        reason_code="liepin_opencli_preflight_required",
    )


def _command_status(command_value: str, *, root: Path) -> tuple[Literal["configured", "missing", "invalid"], str]:
    try:
        argv = shlex.split(command_value)
    except ValueError:
        return "invalid", "liepin_opencli_command_invalid"
    if not argv:
        return "missing", "liepin_opencli_command_missing"
    command = Path(argv[0])
    if command.is_absolute() or len(command.parts) > 1:
        path = command if command.is_absolute() else resolve_path_from_root(str(command), root=root)
        if path.exists():
            return "configured", "configured"
        return "missing", "liepin_opencli_command_missing"
    if shutil.which(argv[0]):
        return "configured", "configured"
    return "missing", "liepin_opencli_command_missing"


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


def _browser_action_backend(env: Mapping[str, str | None]) -> str:
    return (_env_text(env, "SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND") or "disabled").lower()


def _non_placeholder(value: str | None) -> bool:
    return bool(value and value != "local-development")
