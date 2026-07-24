from __future__ import annotations

import hashlib
import http.client
import json
import math
import re
import socket
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlencode

from seektalent.browser_bridge_manifest import (
    BROWSER_BRIDGE_IMPLEMENTATION as OPENCLI_BRIDGE_IMPLEMENTATION,
    BROWSER_BRIDGE_MANIFEST_SCHEMA,
    BROWSER_BRIDGE_PROTOCOL_MAJOR as OPENCLI_BRIDGE_PROTOCOL_MAJOR,
    MAX_SAFE_INTEGER,
    REQUIRED_BROWSER_BRIDGE_CAPABILITIES as REQUIRED_OPENCLI_BRIDGE_CAPABILITIES,
    BrowserBridgeManifestError,
    BrowserBridgeRequirement as OpenCliBridgeRequirement,
    load_browser_bridge_requirement,
)
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
from seektalent.strict_json import StrictJsonError, strict_json_object_loads


OPENCLI_DAEMON_MAX_RESPONSE_BYTES = 1024 * 1024
OPENCLI_BRIDGE_MANIFEST_SCHEMA = BROWSER_BRIDGE_MANIFEST_SCHEMA
WTSCLI_DAEMON_OWNERSHIP_SCHEMA = "wtscli.daemon_ownership.v1"
_OWNERSHIP_TOKEN_RE = re.compile(r"[0-9a-f]{64}\Z")
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
_RESERVED_COMMAND_FIELDS = frozenset(
    {"id", "action", "timeout", "deadlineAt", "contextId", "preferredContextId"}
)


@dataclass(frozen=True)
class OpenCliDaemonResult:
    command_id: str
    data: object | None = None
    page: str | None = None
    idle_deadline_at: int | None = None


@dataclass(frozen=True)
class _DaemonOwnership:
    token: str
    token_hash: str


ConnectionFactory = Callable[[str, int, float], http.client.HTTPConnection]


class OpenCliDaemonClient:
    """Stateful client bound to the exact endpoint and identity in the WTS manifest."""

    def __init__(
        self,
        *,
        requirement: OpenCliBridgeRequirement,
        context_id: str | None = None,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        _validate_requirement(requirement)
        self.requirement = requirement
        self.context_id = context_id
        self.host = requirement.runtime_identity.endpoint.host
        self.port = requirement.runtime_identity.endpoint.port
        self._connection_factory = connection_factory or _http_connection
        self._connection: http.client.HTTPConnection | None = None
        self._verified = False
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._drop_connection()

    def new_connection(self) -> OpenCliDaemonClient:
        return OpenCliDaemonClient(
            requirement=self.requirement,
            context_id=self.context_id,
            connection_factory=self._connection_factory,
        )

    def verify_bridge(
        self,
        *,
        timeout_seconds: float = 2.0,
        validate: bool = True,
    ) -> Mapping[str, object]:
        """Read bridge status, validating transport and bundle identity by default."""
        with self._lock:
            return self._verify_bridge(timeout_seconds=timeout_seconds, validate=validate)

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
            status, payload, _owner_hash = self._request_json(
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

    def _verify_bridge(
        self,
        *,
        timeout_seconds: float,
        validate: bool = True,
    ) -> Mapping[str, object]:
        query = urlencode({"contextId": self.context_id}) if self.context_id else ""
        path = f"/status?{query}" if query else "/status"
        status, payload, owner_hash = self._request_json(
            "GET",
            path,
            body=None,
            timeout_seconds=timeout_seconds,
        )
        if status != 200:
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
        if validate:
            validate_bridge_status(
                payload,
                self.requirement,
                expected_owner_hash=owner_hash,
            )
            self._verified = True
        return payload

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, object] | None,
        timeout_seconds: float,
    ) -> tuple[int, dict[str, object], str]:
        ownership = _load_daemon_ownership(self.requirement)
        connection = self._connection
        if connection is None:
            connection = self._connection_factory(self.host, self.port, timeout_seconds)
            self._connection = connection
        connection.timeout = timeout_seconds
        if connection.sock is not None:
            connection.sock.settimeout(timeout_seconds)
        encoded_body = (
            None
            if body is None
            else json.dumps(body, separators=(",", ":")).encode("utf-8")
        )
        transport = self.requirement.runtime_identity.transport
        headers = {
            transport.request_header[0]: transport.request_header[1],
            "Accept": "application/json",
        }
        if ownership is not None:
            headers[transport.ownership_header] = ownership.token
        if encoded_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=encoded_body, headers=headers)
            response = connection.getresponse()
            expected_owner_hash = ownership.token_hash if ownership is not None else None
            if (
                response.getheader(transport.response_header[0])
                != transport.response_header[1]
                or expected_owner_hash is None
                or response.getheader(transport.owner_proof_header) != expected_owner_hash
            ):
                self._drop_connection()
                raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
            raw = response.read(OPENCLI_DAEMON_MAX_RESPONSE_BYTES + 1)
            status = response.status
            if response.will_close:
                self._drop_connection()
        except OpenCliBrowserError:
            raise
        except (OSError, socket.timeout, http.client.HTTPException) as exc:
            self._drop_connection()
            raise OpenCliBrowserError(OPENCLI_DAEMON_NOT_RUNNING) from exc
        if len(raw) > OPENCLI_DAEMON_MAX_RESPONSE_BYTES:
            self._drop_connection()
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE)
        try:
            payload = _json_object_loads_with_unique_keys(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._drop_connection()
            raise OpenCliBrowserError(OPENCLI_STATUS_UNAVAILABLE) from exc
        return (
            status,
            {str(key): value for key, value in payload.items()},
            expected_owner_hash,
        )

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
            reason = OPENCLI_ERROR_CODE_TO_REASON.get(
                error_code.strip().lower().replace("-", "_")
            )
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


def _load_daemon_ownership(
    requirement: OpenCliBridgeRequirement,
) -> _DaemonOwnership | None:
    path = requirement.runtime_identity.state.ownership_path()
    try:
        payload = strict_json_object_loads(path.read_bytes())
    except (OSError, StrictJsonError):
        return None
    allowed = {"schemaVersion", "endpoint", "token", "tokenHash", "createdAt"}
    if "pid" in payload:
        allowed.add("pid")
    if set(payload) != allowed:
        return None
    raw_endpoint = payload.get("endpoint")
    if type(raw_endpoint) is not dict or set(raw_endpoint) != {"host", "port"}:
        return None
    endpoint = {str(key): value for key, value in raw_endpoint.items()}
    token = payload.get("token")
    token_hash = payload.get("tokenHash")
    pid = payload.get("pid")
    if (
        payload.get("schemaVersion") != WTSCLI_DAEMON_OWNERSHIP_SCHEMA
        or endpoint.get("host") != requirement.runtime_identity.endpoint.host
        or endpoint.get("port") != requirement.runtime_identity.endpoint.port
        or type(token) is not str
        or _OWNERSHIP_TOKEN_RE.fullmatch(token) is None
        or type(token_hash) is not str
        or token_hash != hashlib.sha256(token.encode()).hexdigest()
        or type(payload.get("createdAt")) is not str
        or not payload["createdAt"]
        or (
            "pid" in payload
            and (type(pid) is not int or pid <= 0 or pid > MAX_SAFE_INTEGER)
        )
    ):
        return None
    return _DaemonOwnership(token=token, token_hash=token_hash)


def _command_response_timeout(command_timeout_seconds: float) -> float:
    grace_seconds = min(1.0, max(0.05, command_timeout_seconds * 0.1))
    return command_timeout_seconds + grace_seconds


def _json_object_loads_with_unique_keys(raw: bytes) -> dict[str, object]:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        payload: dict[str, object] = {}
        for key, value in pairs:
            if key in payload:
                raise ValueError(f"duplicate JSON key: {key}")
            payload[key] = value
        return payload

    def reject_nonfinite(value: str) -> object:
        raise ValueError(f"non-finite JSON number: {value}")

    payload = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_nonfinite,
    )
    if not isinstance(payload, dict):
        raise ValueError("JSON response must be an object")
    result: dict[str, object] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise ValueError("JSON object keys must be strings")
        result[key] = value
    return result


def load_bridge_requirement(path: Path) -> OpenCliBridgeRequirement:
    try:
        return load_browser_bridge_requirement(path)
    except BrowserBridgeManifestError as exc:
        reason = {
            "integrity_failed": OPENCLI_BRIDGE_INTEGRITY_FAILED,
            "wrong_implementation": OPENCLI_BRIDGE_WRONG_IMPLEMENTATION,
            "protocol_mismatch": OPENCLI_BRIDGE_PROTOCOL_MISMATCH,
            "capability_missing": OPENCLI_BRIDGE_CAPABILITY_MISSING,
        }[exc.code]
        raise OpenCliBrowserError(reason) from exc


def _validate_requirement(requirement: OpenCliBridgeRequirement) -> None:
    if requirement.implementation != OPENCLI_BRIDGE_IMPLEMENTATION:
        raise OpenCliBrowserError(OPENCLI_BRIDGE_WRONG_IMPLEMENTATION)
    if not requirement.bridge_build_id:
        raise OpenCliBrowserError(OPENCLI_BRIDGE_INTEGRITY_FAILED)
    if (
        requirement.protocol_major != OPENCLI_BRIDGE_PROTOCOL_MAJOR
        or requirement.protocol_minor < 0
    ):
        raise OpenCliBrowserError(OPENCLI_BRIDGE_PROTOCOL_MISMATCH)
    if not REQUIRED_OPENCLI_BRIDGE_CAPABILITIES.issubset(requirement.capabilities):
        raise OpenCliBrowserError(OPENCLI_BRIDGE_CAPABILITY_MISSING)


def bridge_status_failure(
    status: Mapping[str, object],
    requirement: OpenCliBridgeRequirement,
    *,
    expected_owner_hash: str | None = None,
) -> tuple[Literal["bridge", "extension", "process"], str] | None:
    """Return the first causal status failure using strict WTS bundle identity."""
    if status.get("ok") is not True:
        return "process", OPENCLI_STATUS_UNAVAILABLE
    if status.get("daemonVersion") != requirement.cli.version:
        return "bridge", OPENCLI_BRIDGE_BUILD_MISMATCH
    if status.get("implementation") != requirement.implementation:
        return "bridge", OPENCLI_BRIDGE_WRONG_IMPLEMENTATION
    if status.get("bridgeBuildId") != requirement.bridge_build_id:
        return "bridge", OPENCLI_BRIDGE_BUILD_MISMATCH
    if not _exact_protocol(status.get("protocolVersion"), requirement):
        return "bridge", OPENCLI_BRIDGE_PROTOCOL_MISMATCH
    raw_transport_protocol = status.get("transportProtocol")
    expected_transport = requirement.runtime_identity.transport.protocol
    if (
        not isinstance(raw_transport_protocol, Mapping)
        or set(raw_transport_protocol) != {"name", "version"}
    ):
        return "bridge", OPENCLI_BRIDGE_PROTOCOL_MISMATCH
    transport_protocol = {
        str(key): value for key, value in raw_transport_protocol.items()
    }
    if (
        transport_protocol.get("name") != expected_transport.name
        or not _exact_protocol(
            transport_protocol.get("version"),
            requirement,
        )
    ):
        return "bridge", OPENCLI_BRIDGE_PROTOCOL_MISMATCH
    if status.get("port") != requirement.runtime_identity.endpoint.port:
        return "bridge", OPENCLI_STATUS_UNAVAILABLE
    owner_hash = status.get("ownerTokenHash")
    if (
        type(owner_hash) is not str
        or _OWNERSHIP_TOKEN_RE.fullmatch(owner_hash) is None
        or (expected_owner_hash is not None and owner_hash != expected_owner_hash)
    ):
        return "bridge", OPENCLI_STATUS_UNAVAILABLE
    daemon_capabilities = _string_set(status.get("capabilities"))
    if daemon_capabilities is None:
        return "bridge", OPENCLI_STATUS_UNAVAILABLE
    if daemon_capabilities != requirement.capabilities:
        return "bridge", OPENCLI_BRIDGE_CAPABILITY_MISSING
    if status.get("extensionConnected") is not True:
        return "extension", OPENCLI_EXTENSION_DISCONNECTED
    if status.get("extensionVersion") != requirement.extension.version:
        return "extension", OPENCLI_BRIDGE_BUILD_MISMATCH
    if status.get("extensionImplementation") != requirement.implementation:
        return "extension", OPENCLI_BRIDGE_WRONG_IMPLEMENTATION
    if status.get("extensionBridgeBuildId") != requirement.bridge_build_id:
        return "extension", OPENCLI_BRIDGE_BUILD_MISMATCH
    if not _exact_protocol(status.get("extensionProtocolVersion"), requirement):
        return "extension", OPENCLI_BRIDGE_PROTOCOL_MISMATCH
    extension_capabilities = _string_set(status.get("extensionCapabilities"))
    if extension_capabilities is None:
        return "extension", OPENCLI_STATUS_UNAVAILABLE
    if extension_capabilities != requirement.capabilities:
        return "extension", OPENCLI_BRIDGE_CAPABILITY_MISSING
    return None


def validate_bridge_status(
    status: Mapping[str, object],
    requirement: OpenCliBridgeRequirement,
    *,
    expected_owner_hash: str | None = None,
) -> None:
    failure = bridge_status_failure(
        status,
        requirement,
        expected_owner_hash=expected_owner_hash,
    )
    if failure is not None:
        raise OpenCliBrowserError(failure[1])


def _exact_protocol(
    value: object,
    requirement: OpenCliBridgeRequirement,
) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"major", "minor"}:
        return False
    protocol = cast("Mapping[object, object]", value)
    major = protocol.get("major")
    minor = protocol.get("minor")
    return (
        type(major) is int
        and type(minor) is int
        and major == requirement.protocol_major
        and minor == requirement.protocol_minor
    )


def _string_set(value: object) -> frozenset[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return frozenset(cast("list[str]", value))


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    if type(value) is int:
        return value
    if type(value) is float and math.isfinite(value):
        return math.floor(value)
    return None


def _http_connection(
    host: str,
    port: int,
    timeout: float,
) -> http.client.HTTPConnection:
    return http.client.HTTPConnection(host, port, timeout=timeout)
