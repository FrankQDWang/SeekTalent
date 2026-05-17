from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


RunCommand = Callable[[list[str]], subprocess.CompletedProcess[str]]
TransportMode = Literal["local_only", "remote_e2e_allowed"]


class DokoBotDeclaredOperations(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    navigate: bool
    click: bool
    type_text: bool
    pagination: bool
    read_snapshot: bool
    network_inspection: bool
    script_evaluation: bool
    direct_api_replay: bool
    cookie_header_injection: bool
    cdp_access: bool
    stealth_or_proxy_evasion: bool
    auto_install: bool
    update_or_config_mutation: bool
    permission_mutation: bool
    fallback_mode_mutation: bool

    @model_validator(mode="after")
    def reject_forbidden_operations(self) -> DokoBotDeclaredOperations:
        forbidden = {
            "network_inspection": self.network_inspection,
            "script_evaluation": self.script_evaluation,
            "direct_api_replay": self.direct_api_replay,
            "cookie_header_injection": self.cookie_header_injection,
            "cdp_access": self.cdp_access,
            "stealth_or_proxy_evasion": self.stealth_or_proxy_evasion,
            "auto_install": self.auto_install,
            "update_or_config_mutation": self.update_or_config_mutation,
            "permission_mutation": self.permission_mutation,
            "fallback_mode_mutation": self.fallback_mode_mutation,
        }
        enabled = [name for name, is_enabled in forbidden.items() if is_enabled]
        if enabled:
            raise ValueError(f"forbidden DokoBot operation enabled: {enabled[0]}")
        return self


class DokoBotActionToolManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    schema_version: Literal["dokobot-action-manifest-v1"]
    manifest_id: str = Field(min_length=1)
    manifest_version: str = Field(min_length=1)
    provider: Literal["dokobot_compatible"]
    transport: TransportMode
    declared_operations: DokoBotDeclaredOperations
    forbidden_operations_ack: tuple[str, ...]
    trust_source: Literal["preconfigured_admin"]
    signature_required: bool = True
    manifest_signature: str | None = Field(default=None, repr=False)
    expires_at: datetime
    auto_install_allowed: bool = False

    @property
    def supports_click(self) -> bool:
        return self.declared_operations.click

    @property
    def supports_type(self) -> bool:
        return self.declared_operations.type_text

    @property
    def supports_navigation(self) -> bool:
        return self.declared_operations.navigate

    @property
    def supports_pagination_action(self) -> bool:
        return self.supports_click and self.declared_operations.pagination

    @field_validator("expires_at")
    @classmethod
    def expires_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_manifest_policy(self) -> DokoBotActionToolManifest:
        if self.expires_at <= datetime.now(UTC):
            raise ValueError("action manifest is expired")
        if self.auto_install_allowed:
            raise ValueError("action manifest must not allow auto install")
        if self.signature_required and not self.manifest_signature:
            raise ValueError("action manifest signature is required")
        required_ack = {
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
        }
        if not required_ack.issubset(set(self.forbidden_operations_ack)):
            raise ValueError("action manifest missing forbidden operation acknowledgement")
        return self


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
    action_manifest_transport: TransportMode | None = None
    action_manifest_trust_source: Literal["preconfigured_admin"] | None = None
    action_manifest_tools: tuple[str, ...] = ()
    capability_error_code: Literal[
        "dokobot_cli_unavailable",
        "dokobot_capability_probe_failed",
        "dokobot_capability_probe_timeout",
        "dokobot_tool_manifest_untrusted",
    ] | None = None

    @property
    def can_execute_liepin_actions(self) -> bool:
        return self.can_execute_liepin_actions_for_transport("local_only")

    def can_execute_liepin_actions_for_transport(self, requested_transport: TransportMode) -> bool:
        return (
            self.supports_read
            and self.capability_error_code is None
            and bool(self.action_manifest_id)
            and bool(self.action_manifest_version)
            and self.action_manifest_transport == requested_transport
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
        trusted_action_manifest_ids: set[str] | None = None,
    ) -> None:
        self._run_command = run_command or _run_subprocess_command
        self._action_tool_manifest = action_tool_manifest
        self._trusted_action_manifest_ids = trusted_action_manifest_ids

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

        manifest = self._trusted_manifest()
        capability_error_code = None if manifest is self._action_tool_manifest else "dokobot_tool_manifest_untrusted"
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
            action_manifest_transport=manifest.transport if manifest is not None else None,
            action_manifest_trust_source=manifest.trust_source if manifest is not None else None,
            action_manifest_tools=_manifest_tools(manifest),
            capability_error_code=capability_error_code,
        )

    def _trusted_manifest(self) -> DokoBotActionToolManifest | None:
        manifest = self._action_tool_manifest
        if manifest is None:
            return None
        if self._trusted_action_manifest_ids is None:
            return None
        if manifest.manifest_id not in self._trusted_action_manifest_ids:
            return None
        return manifest


def _run_subprocess_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, capture_output=True, timeout=10)


def _failed_capabilities(
    cli_version: str,
    error_code: Literal[
        "dokobot_cli_unavailable",
        "dokobot_capability_probe_failed",
        "dokobot_capability_probe_timeout",
        "dokobot_tool_manifest_untrusted",
    ],
) -> DokoBotCapabilities:
    return DokoBotCapabilities(cli_version=cli_version, capability_error_code=error_code)


def _help_has_command(help_text: str, command: str) -> bool:
    return re.search(rf"(?m)^\s*{re.escape(command)}\b", help_text) is not None


def _manifest_tools(manifest: DokoBotActionToolManifest | None) -> tuple[str, ...]:
    if manifest is None:
        return ()
    tools: list[str] = []
    if manifest.supports_click:
        tools.append("click")
    if manifest.supports_type:
        tools.append("type_text")
    if manifest.supports_navigation:
        tools.append("navigate")
    if manifest.supports_pagination_action:
        tools.append("pagination")
    if manifest.declared_operations.read_snapshot:
        tools.append("read_snapshot")
    return tuple(tools)
