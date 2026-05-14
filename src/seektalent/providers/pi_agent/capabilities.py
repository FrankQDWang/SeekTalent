from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict


RunCommand = Callable[[list[str]], subprocess.CompletedProcess[str]]


class DokoBotActionToolManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    manifest_id: str
    manifest_version: str
    provider: Literal["dokobot_compatible"]
    enabled_tools: tuple[str, ...] = ()

    @property
    def supports_click(self) -> bool:
        return "click" in self.enabled_tools

    @property
    def supports_type(self) -> bool:
        return bool({"fill", "type_text", "type", "text_entry"}.intersection(self.enabled_tools))

    @property
    def supports_navigation(self) -> bool:
        return bool({"navigate", "navigation"}.intersection(self.enabled_tools))

    @property
    def supports_pagination_action(self) -> bool:
        return self.supports_click and bool({"turn_page", "paginate", "pagination"}.intersection(self.enabled_tools))


class DokoBotCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    cli_version: str
    extension_version: str | None = None
    core_skill_version: str | None = None
    supports_read: bool = False
    supports_chunks_format: bool = False
    supports_session_continuation: bool = False
    supports_click: bool = False
    supports_type: bool = False
    supports_navigation: bool = False
    supports_pagination_action: bool = False
    local_mode_available: bool = False
    remote_mode_available: bool = False
    action_manifest_id: str | None = None
    action_manifest_version: str | None = None
    action_manifest_tools: tuple[str, ...] = ()
    capability_error_code: Literal[
        "dokobot_cli_unavailable",
        "dokobot_capability_probe_failed",
        "dokobot_capability_probe_timeout",
    ] | None = None

    @property
    def can_execute_liepin_actions(self) -> bool:
        return (
            self.supports_read
            and bool(self.action_manifest_id)
            and bool(self.action_manifest_version)
            and bool(self.action_manifest_tools)
            and self.supports_click
            and self.supports_type
            and self.supports_navigation
            and self.supports_pagination_action
        )


class DokoBotCapabilityProbe:
    def __init__(
        self,
        *,
        run_command: RunCommand | None = None,
        action_tool_manifest: DokoBotActionToolManifest | None = None,
    ) -> None:
        self._run_command = run_command or _run_subprocess_command
        self._action_tool_manifest = action_tool_manifest

    def discover(self) -> DokoBotCapabilities:
        try:
            version_result = self._run_command(["dokobot", "--version"])
            if version_result.returncode != 0:
                return _failed_capabilities("unknown", "dokobot_capability_probe_failed")
            help_result = self._run_command(["dokobot", "--help"])
            read_help_result = self._run_command(["dokobot", "read", "--help"])
        except FileNotFoundError:
            return _failed_capabilities("unknown", "dokobot_cli_unavailable")
        except PermissionError:
            return _failed_capabilities("unknown", "dokobot_cli_unavailable")
        except subprocess.TimeoutExpired:
            return _failed_capabilities("unknown", "dokobot_capability_probe_timeout")

        cli_version = version_result.stdout.strip() or "unknown"
        if help_result.returncode != 0 or read_help_result.returncode != 0:
            return _failed_capabilities(cli_version, "dokobot_capability_probe_failed")

        manifest = self._action_tool_manifest
        return DokoBotCapabilities(
            cli_version=cli_version,
            supports_read=_help_has_command(help_result.stdout, "read"),
            supports_chunks_format="chunks" in read_help_result.stdout,
            supports_session_continuation="--session-id" in read_help_result.stdout,
            supports_click=manifest.supports_click if manifest is not None else False,
            supports_type=manifest.supports_type if manifest is not None else False,
            supports_navigation=manifest.supports_navigation if manifest is not None else False,
            supports_pagination_action=manifest.supports_pagination_action if manifest is not None else False,
            local_mode_available="--local" in read_help_result.stdout,
            remote_mode_available="--api-key" in help_result.stdout or "--server" in help_result.stdout,
            action_manifest_id=manifest.manifest_id if manifest is not None else None,
            action_manifest_version=manifest.manifest_version if manifest is not None else None,
            action_manifest_tools=manifest.enabled_tools if manifest is not None else (),
        )


def _run_subprocess_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, capture_output=True, timeout=10)


def _failed_capabilities(
    cli_version: str,
    error_code: Literal[
        "dokobot_cli_unavailable",
        "dokobot_capability_probe_failed",
        "dokobot_capability_probe_timeout",
    ],
) -> DokoBotCapabilities:
    return DokoBotCapabilities(cli_version=cli_version, capability_error_code=error_code)


def _help_has_command(help_text: str, command: str) -> bool:
    return re.search(rf"(?m)^\s*{re.escape(command)}\b", help_text) is not None
