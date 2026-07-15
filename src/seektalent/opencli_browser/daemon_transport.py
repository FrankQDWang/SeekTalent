from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlencode

from seektalent.opencli_browser.contracts import OpenCliBrowserError
from seektalent.opencli_browser.reason_codes import (
    OPENCLI_BRIDGE_BUILD_MISMATCH,
    OPENCLI_BRIDGE_CAPABILITY_MISSING,
    OPENCLI_BRIDGE_INTEGRITY_FAILED,
    OPENCLI_BRIDGE_PROTOCOL_MISMATCH,
    OPENCLI_BRIDGE_WRONG_IMPLEMENTATION,
    OPENCLI_COMMAND_RESULT_UNKNOWN,
    OPENCLI_DAEMON_NOT_RUNNING,
    OPENCLI_ERROR_CODE_TO_REASON,
    OPENCLI_EXTENSION_DISCONNECTED,
    OPENCLI_FORBIDDEN_COMMAND,
    OPENCLI_STATUS_UNAVAILABLE,
)


OPENCLI_DAEMON_HOST = "127.0.0.1"
OPENCLI_DAEMON_PORT = 19825
OPENCLI_DAEMON_MAX_RESPONSE_BYTES = 1024 * 1024
OPENCLI_BRIDGE_MANIFEST_SCHEMA = "seektalent.browser_bridge_bundle.v1"
OPENCLI_BRIDGE_IMPLEMENTATION = "seektalent-opencli"
OPENCLI_BRIDGE_PROTOCOL_MAJOR = 1
REQUIRED_OPENCLI_BRIDGE_CAPABILITIES = frozenset(
    {
        "browser.operation-deadline.v1",
        "browser.operations.v1",
        "control-fence.v1",
        "tab.close-verified.v1",
        "tab.create-in-existing-window.v1",
        "tab.find.v1",
        "tab.idle-deadline.v1",
    }
)

OpenCliDaemonAction = Literal[
    "browser-operation",
    "exec",
    "navigate",
    "tabs",
    "screenshot",
    "insert-text",
    "cdp",
    "control",
]
_ALLOWED_DAEMON_ACTIONS = frozenset(
    {"browser-operation", "exec", "navigate", "tabs", "screenshot", "insert-text", "cdp", "control"}
)
_RESERVED_COMMAND_FIELDS = frozenset({"id", "action", "timeout", "deadlineAt", "contextId", "preferredContextId"})


@dataclass(frozen=True)
class OpenCliBridgeRequirement:
    implementation: str
    bridge_build_id: str
    protocol_major: int
    protocol_minor: int
    capabilities: frozenset[str]


@dataclass(frozen=True)
class OpenCliDaemonResult:
    command_id: str
    data: object | None = None
    page: str | None = None
    idle_deadline_at: int | None = None


ConnectionFactory = Callable[[str, int, float], http.client.HTTPConnection]


class OpenCliDaemonClient:
    """Small stateful client for the fork daemon's loopback HTTP protocol."""

    def __init__(
        self,
        *,
        requirement: OpenCliBridgeRequirement,
        context_id: str | None = None,
        host: str = OPENCLI_DAEMON_HOST,
        port: int = OPENCLI_DAEMON_PORT,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        _validate_requirement(requirement)
        self.requirement = requirement
        self.context_id = context_id
        self.host = host
        self.port = port
        self._connection_factory = connection_factory or _http_connection
        self._connection: http.client.HTTPConnection | None = None
        self._verified = False
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._drop_connection()

    def verify_bridge(self, *, timeout_seconds: float = 2.0) -> Mapping[str, object]:
        with self._lock:
            return self._verify_bridge(timeout_seconds=timeout_seconds)

    def command(
        self,
        action: OpenCliDaemonAction,
        params: Mapping[str, object],
        *,
        timeout_seconds: float,
    ) -> OpenCliDaemonResult:
        if action not in _ALLOWED_DAEMON_ACTIONS or _RESERVED_COMMAND_FIELDS.intersection(params):
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)
        if timeout_seconds <= 0:
            raise OpenCliBrowserError(OPENCLI_FORBIDDEN_COMMAND)

        command_id = f"seektalent_{uuid.uuid4().hex}"
        deadline_at = int(time.time() * 1000 + timeout_seconds * 1000)
        body = {
            "id": command_id,
            "action": action,
            **dict(params),
            "timeout": max(1, int(timeout_seconds)),
            "deadlineAt": deadline_at,
            **({"contextId": self.context_id} if self.context_id else {}),
        }
        with self._lock:
            if not self._verified:
                self._verify_bridge(timeout_seconds=min(timeout_seconds, 2.0))
            status, payload = self._request_json(
                "POST",
                "/command",
                body=body,
                timeout_seconds=_command_response_timeout(timeout_seconds),
            )
            if payload.get("ok") is not True:
                self._raise_command_error(status, payload)
            if status < 200 or status >= 300 or payload.get("id") != command_id:
                self._drop_connection()
                raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
            return OpenCliDaemonResult(
                command_id=command_id,
                data=payload.get("data"),
                page=_optional_string(payload.get("page")),
                idle_deadline_at=_optional_int(payload.get("idleDeadlineAt")),
            )

    def _verify_bridge(self, *, timeout_seconds: float) -> Mapping[str, object]:
        query = urlencode({"contextId": self.context_id}) if self.context_id else ""
        path = f"/status?{query}" if query else "/status"
        status, payload = self._request_json("GET", path, body=None, timeout_seconds=timeout_seconds)
        if status != 200:
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
        validate_bridge_status(payload, self.requirement)
        self._verified = True
        return payload

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, object] | None,
        timeout_seconds: float,
    ) -> tuple[int, dict[str, object]]:
        connection = self._connection
        if connection is None:
            connection = self._connection_factory(self.host, self.port, timeout_seconds)
            self._connection = connection
        connection.timeout = timeout_seconds
        if connection.sock is not None:
            connection.sock.settimeout(timeout_seconds)
        encoded_body = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers = {"X-OpenCLI": "1", "Accept": "application/json"}
        if encoded_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=encoded_body, headers=headers)
            response = connection.getresponse()
            raw = response.read(OPENCLI_DAEMON_MAX_RESPONSE_BYTES + 1)
            status = response.status
            if response.will_close:
                self._drop_connection()
        except (OSError, socket.timeout, http.client.HTTPException) as exc:
            self._drop_connection()
            raise OpenCliBrowserError(OPENCLI_DAEMON_NOT_RUNNING) from exc
        if len(raw) > OPENCLI_DAEMON_MAX_RESPONSE_BYTES:
            self._drop_connection()
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE) from exc
        if not isinstance(payload, dict):
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
        return status, {str(key): value for key, value in payload.items()}

    def _raise_command_error(self, _status: int, payload: Mapping[str, object]) -> None:
        error_code = _optional_string(payload.get("errorCode"))
        reason = {
            "bridge_wrong_implementation": OPENCLI_BRIDGE_WRONG_IMPLEMENTATION,
            "bridge_build_mismatch": OPENCLI_BRIDGE_BUILD_MISMATCH,
            "bridge_protocol_mismatch": OPENCLI_BRIDGE_PROTOCOL_MISMATCH,
            "bridge_capability_missing": OPENCLI_BRIDGE_CAPABILITY_MISSING,
            "extension_not_connected": OPENCLI_EXTENSION_DISCONNECTED,
            "profile_disconnected": OPENCLI_EXTENSION_DISCONNECTED,
            "command_result_unknown": OPENCLI_COMMAND_RESULT_UNKNOWN,
        }.get(error_code or "")
        if reason is None and error_code is not None:
            reason = OPENCLI_ERROR_CODE_TO_REASON.get(error_code.strip().lower().replace("-", "_"))
        if reason is None:
            reason = OPENCLI_STATUS_UNAVAILABLE
        if reason.startswith("opencli_bridge_"):
            self._verified = False
        raise OpenCliBrowserError(reason)

    def _drop_connection(self) -> None:
        connection = self._connection
        self._connection = None
        self._verified = False
        if connection is not None:
            try:
                connection.close()
            except OSError:
                return


def _command_response_timeout(command_timeout_seconds: float) -> float:
    grace_seconds = min(1.0, max(0.05, command_timeout_seconds * 0.1))
    return command_timeout_seconds + grace_seconds


def load_bridge_requirement(path: Path) -> OpenCliBridgeRequirement:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenCliBrowserError(OPENCLI_BRIDGE_INTEGRITY_FAILED) from exc
    if not isinstance(payload, dict) or payload.get("schemaVersion") != OPENCLI_BRIDGE_MANIFEST_SCHEMA:
        raise OpenCliBrowserError(OPENCLI_BRIDGE_INTEGRITY_FAILED)
    implementation = payload.get("implementation")
    if implementation != OPENCLI_BRIDGE_IMPLEMENTATION:
        raise OpenCliBrowserError(OPENCLI_BRIDGE_WRONG_IMPLEMENTATION)
    bridge_build_id = payload.get("bridgeBuildId")
    protocol = payload.get("protocolVersion")
    capabilities = payload.get("capabilities")
    if (
        not isinstance(bridge_build_id, str)
        or not bridge_build_id
        or not isinstance(protocol, dict)
        or type(protocol.get("major")) is not int
        or type(protocol.get("minor")) is not int
        or protocol["major"] < 0
        or protocol["minor"] < 0
        or not isinstance(capabilities, list)
        or not all(isinstance(value, str) and value for value in capabilities)
    ):
        raise OpenCliBrowserError(OPENCLI_BRIDGE_INTEGRITY_FAILED)
    if protocol["major"] != OPENCLI_BRIDGE_PROTOCOL_MAJOR:
        raise OpenCliBrowserError(OPENCLI_BRIDGE_PROTOCOL_MISMATCH)
    capability_set = frozenset(cast("list[str]", capabilities))
    if not REQUIRED_OPENCLI_BRIDGE_CAPABILITIES.issubset(capability_set):
        raise OpenCliBrowserError(OPENCLI_BRIDGE_CAPABILITY_MISSING)
    return OpenCliBridgeRequirement(
        implementation=implementation,
        bridge_build_id=bridge_build_id,
        protocol_major=protocol["major"],
        protocol_minor=protocol["minor"],
        capabilities=capability_set,
    )


def _validate_requirement(requirement: OpenCliBridgeRequirement) -> None:
    if requirement.implementation != OPENCLI_BRIDGE_IMPLEMENTATION:
        raise OpenCliBrowserError(OPENCLI_BRIDGE_WRONG_IMPLEMENTATION)
    if not requirement.bridge_build_id:
        raise OpenCliBrowserError(OPENCLI_BRIDGE_INTEGRITY_FAILED)
    if requirement.protocol_major != OPENCLI_BRIDGE_PROTOCOL_MAJOR or requirement.protocol_minor < 0:
        raise OpenCliBrowserError(OPENCLI_BRIDGE_PROTOCOL_MISMATCH)
    if not REQUIRED_OPENCLI_BRIDGE_CAPABILITIES.issubset(requirement.capabilities):
        raise OpenCliBrowserError(OPENCLI_BRIDGE_CAPABILITY_MISSING)


def validate_bridge_status(status: Mapping[str, object], requirement: OpenCliBridgeRequirement) -> None:
    if status.get("ok") is not True:
        raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
    if status.get("extensionConnected") is not True:
        raise OpenCliBrowserError(OPENCLI_EXTENSION_DISCONNECTED)
    daemon_implementation = status.get("implementation")
    extension_implementation = status.get("extensionImplementation")
    if daemon_implementation != requirement.implementation or extension_implementation != requirement.implementation:
        raise OpenCliBrowserError(OPENCLI_BRIDGE_WRONG_IMPLEMENTATION)
    daemon_build = status.get("bridgeBuildId")
    extension_build = status.get("extensionBridgeBuildId")
    if daemon_build != requirement.bridge_build_id or extension_build != requirement.bridge_build_id:
        raise OpenCliBrowserError(OPENCLI_BRIDGE_BUILD_MISMATCH)
    if not _compatible_protocol(status.get("protocolVersion"), requirement) or not _compatible_protocol(
        status.get("extensionProtocolVersion"), requirement
    ):
        raise OpenCliBrowserError(OPENCLI_BRIDGE_PROTOCOL_MISMATCH)
    daemon_capabilities = _string_set(status.get("capabilities"))
    extension_capabilities = _string_set(status.get("extensionCapabilities"))
    if daemon_capabilities is None or extension_capabilities is None:
        raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
    if not requirement.capabilities.issubset(daemon_capabilities) or not requirement.capabilities.issubset(
        extension_capabilities
    ):
        raise OpenCliBrowserError(OPENCLI_BRIDGE_CAPABILITY_MISSING)


def _compatible_protocol(value: object, requirement: OpenCliBridgeRequirement) -> bool:
    if not isinstance(value, Mapping):
        return False
    protocol = cast("Mapping[object, object]", value)
    major = protocol.get("major")
    minor = protocol.get("minor")
    return (
        type(major) is int
        and type(minor) is int
        and major == requirement.protocol_major
        and minor >= requirement.protocol_minor
    )


def _string_set(value: object) -> frozenset[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return frozenset(cast("list[str]", value))


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if type(value) is int else None


def _http_connection(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
    return http.client.HTTPConnection(host, port, timeout=timeout)
