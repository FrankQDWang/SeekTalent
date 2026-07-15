from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from seektalent.opencli_browser.contracts import OpenCliBrowserError
from seektalent.opencli_browser.daemon_transport import (
    OPENCLI_BRIDGE_MANIFEST_SCHEMA,
    OPENCLI_DAEMON_MAX_RESPONSE_BYTES,
    REQUIRED_OPENCLI_BRIDGE_CAPABILITIES,
    OpenCliBridgeRequirement,
    OpenCliDaemonClient,
    load_bridge_requirement,
    validate_bridge_status,
)
from seektalent.opencli_browser.reason_codes import (
    OPENCLI_BRIDGE_BUILD_MISMATCH,
    OPENCLI_BRIDGE_CAPABILITY_MISSING,
    OPENCLI_BRIDGE_INTEGRITY_FAILED,
    OPENCLI_BRIDGE_PROTOCOL_MISMATCH,
    OPENCLI_BRIDGE_WRONG_IMPLEMENTATION,
    OPENCLI_COMMAND_RESULT_UNKNOWN,
    OPENCLI_FORBIDDEN_COMMAND,
    OPENCLI_SELECTOR_NOT_FOUND,
    OPENCLI_STALE_CONTROL_FENCE,
    OPENCLI_STATUS_UNAVAILABLE,
)


BUILD_ID = "seektalent-opencli-1.8.6+test"


def _requirement() -> OpenCliBridgeRequirement:
    return OpenCliBridgeRequirement(
        implementation="seektalent-opencli",
        bridge_build_id=BUILD_ID,
        protocol_major=1,
        protocol_minor=0,
        capabilities=REQUIRED_OPENCLI_BRIDGE_CAPABILITIES,
    )


def _status(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "implementation": "seektalent-opencli",
        "bridgeBuildId": BUILD_ID,
        "protocolVersion": {"major": 1, "minor": 0},
        "capabilities": sorted(REQUIRED_OPENCLI_BRIDGE_CAPABILITIES),
        "extensionConnected": True,
        "extensionImplementation": "seektalent-opencli",
        "extensionBridgeBuildId": BUILD_ID,
        "extensionProtocolVersion": {"major": 1, "minor": 0},
        "extensionCapabilities": sorted(REQUIRED_OPENCLI_BRIDGE_CAPABILITIES),
    }
    payload.update(updates)
    return payload


class _Response:
    def __init__(self, status: int, payload: object, *, will_close: bool = False) -> None:
        self.status = status
        self.payload = payload
        self.will_close = will_close

    def read(self, amount: int) -> bytes:
        del amount
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")


class _Connection:
    def __init__(self, *, status_payload: Mapping[str, object], command_error: tuple[int, str] | None = None) -> None:
        self.timeout = 0.0
        self.sock = None
        self.closed = False
        self.requests: list[tuple[str, str, bytes | None, Mapping[str, str]]] = []
        self.status_payload = status_payload
        self.command_error = command_error

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.requests.append((method, path, body, dict(headers or {})))

    def getresponse(self) -> _Response:
        method, path, body, _headers = self.requests[-1]
        if method == "GET" and path.startswith("/status"):
            return _Response(200, self.status_payload)
        assert body is not None
        command = json.loads(body)
        if self.command_error is not None:
            status, error_code = self.command_error
            return _Response(status, {"id": command["id"], "ok": False, "errorCode": error_code})
        return _Response(
            200,
            {
                "id": command["id"],
                "ok": True,
                "data": {"echo": command["action"]},
                "page": "page_1",
                "idleDeadlineAt": 123456,
            },
        )

    def close(self) -> None:
        self.closed = True


def test_load_bridge_requirement_validates_manifest_identity_and_minimum_capabilities(tmp_path: Path) -> None:
    manifest = tmp_path / "bridge-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": OPENCLI_BRIDGE_MANIFEST_SCHEMA,
                "implementation": "seektalent-opencli",
                "bridgeBuildId": BUILD_ID,
                "protocolVersion": {"major": 1, "minor": 0},
                "capabilities": sorted(REQUIRED_OPENCLI_BRIDGE_CAPABILITIES | {"future.v1"}),
            }
        ),
        encoding="utf-8",
    )

    requirement = load_bridge_requirement(manifest)

    assert requirement.bridge_build_id == BUILD_ID
    assert requirement.capabilities == REQUIRED_OPENCLI_BRIDGE_CAPABILITIES | {"future.v1"}


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({}, OPENCLI_BRIDGE_INTEGRITY_FAILED),
        (
            {
                "schemaVersion": OPENCLI_BRIDGE_MANIFEST_SCHEMA,
                "implementation": "upstream-opencli",
                "bridgeBuildId": BUILD_ID,
                "protocolVersion": {"major": 1, "minor": 0},
                "capabilities": sorted(REQUIRED_OPENCLI_BRIDGE_CAPABILITIES),
            },
            OPENCLI_BRIDGE_WRONG_IMPLEMENTATION,
        ),
        (
            {
                "schemaVersion": OPENCLI_BRIDGE_MANIFEST_SCHEMA,
                "implementation": "seektalent-opencli",
                "bridgeBuildId": BUILD_ID,
                "protocolVersion": {"major": 2, "minor": 0},
                "capabilities": sorted(REQUIRED_OPENCLI_BRIDGE_CAPABILITIES),
            },
            OPENCLI_BRIDGE_PROTOCOL_MISMATCH,
        ),
        (
            {
                "schemaVersion": OPENCLI_BRIDGE_MANIFEST_SCHEMA,
                "implementation": "seektalent-opencli",
                "bridgeBuildId": BUILD_ID,
                "protocolVersion": {"major": 1, "minor": 0},
                "capabilities": [],
            },
            OPENCLI_BRIDGE_CAPABILITY_MISSING,
        ),
    ],
)
def test_load_bridge_requirement_rejects_invalid_manifest(
    tmp_path: Path, payload: Mapping[str, object], reason: str
) -> None:
    manifest = tmp_path / "bridge-manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(OpenCliBrowserError) as captured:
        load_bridge_requirement(manifest)

    assert captured.value.safe_reason_code == reason


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"extensionImplementation": "upstream-opencli"}, OPENCLI_BRIDGE_WRONG_IMPLEMENTATION),
        ({"extensionBridgeBuildId": "stale-build"}, OPENCLI_BRIDGE_BUILD_MISMATCH),
        ({"extensionProtocolVersion": {"major": 2, "minor": 0}}, OPENCLI_BRIDGE_PROTOCOL_MISMATCH),
        ({"extensionCapabilities": []}, OPENCLI_BRIDGE_CAPABILITY_MISSING),
    ],
)
def test_validate_bridge_status_rejects_unpaired_daemon_or_extension(
    updates: Mapping[str, object], reason: str
) -> None:
    with pytest.raises(OpenCliBrowserError) as captured:
        validate_bridge_status(_status(**updates), _requirement())

    assert captured.value.safe_reason_code == reason


def test_daemon_client_reuses_connection_and_sends_unique_deadlined_commands() -> None:
    connection = _Connection(status_payload=_status())
    factory_calls: list[tuple[str, int, float]] = []

    def factory(host: str, port: int, timeout: float) -> _Connection:
        factory_calls.append((host, port, timeout))
        return connection

    client = OpenCliDaemonClient(requirement=_requirement(), connection_factory=factory)

    first = client.command("tabs", {"op": "list", "session": "scope_a"}, timeout_seconds=3)
    second = client.command("tabs", {"op": "list", "session": "scope_a"}, timeout_seconds=3)

    assert len(factory_calls) == 1
    assert [request[0:2] for request in connection.requests] == [
        ("GET", "/status"),
        ("POST", "/command"),
        ("POST", "/command"),
    ]
    command_bodies = [json.loads(request[2] or b"{}") for request in connection.requests[1:]]
    assert command_bodies[0]["id"] != command_bodies[1]["id"]
    assert all(body["deadlineAt"] > 0 and body["timeout"] == 3 for body in command_bodies)
    assert first.data == {"echo": "tabs"}
    assert first.page == "page_1"
    assert first.idle_deadline_at == 123456
    assert second.command_id != first.command_id


def test_daemon_client_can_create_an_independent_connection_for_background_cleanup() -> None:
    connection = _Connection(status_payload=_status())

    def factory(*_args: object) -> _Connection:
        return connection

    client = OpenCliDaemonClient(
        requirement=_requirement(),
        context_id="profile-1",
        host="127.0.0.1",
        port=19826,
        connection_factory=factory,
    )

    background = client.new_connection()

    assert background is not client
    assert background.requirement == client.requirement
    assert background.context_id == "profile-1"
    assert background.host == "127.0.0.1"
    assert background.port == 19826


def test_daemon_client_keeps_short_best_effort_commands_strictly_bounded() -> None:
    connection = _Connection(status_payload=_status())
    client = OpenCliDaemonClient(requirement=_requirement(), connection_factory=lambda *_args: connection)
    client.verify_bridge()

    client.command("browser-operation", {"operation": "evaluate"}, timeout_seconds=0.25)

    assert connection.timeout == pytest.approx(0.3)
    command = json.loads(connection.requests[-1][2] or b"{}")
    assert command["timeout"] == 1


def test_daemon_client_never_retries_unknown_command_result() -> None:
    connection = _Connection(status_payload=_status(), command_error=(503, "command_result_unknown"))
    client = OpenCliDaemonClient(requirement=_requirement(), connection_factory=lambda *_args: connection)

    with pytest.raises(OpenCliBrowserError) as captured:
        client.command("navigate", {"url": "https://h.liepin.com/"}, timeout_seconds=3)

    assert captured.value.safe_reason_code == OPENCLI_COMMAND_RESULT_UNKNOWN
    assert [request[0] for request in connection.requests] == ["GET", "POST"]


def test_daemon_client_preserves_selector_error_semantics() -> None:
    connection = _Connection(status_payload=_status(), command_error=(404, "selector_not_found"))
    client = OpenCliDaemonClient(requirement=_requirement(), connection_factory=lambda *_args: connection)

    with pytest.raises(OpenCliBrowserError) as captured:
        client.command("browser-operation", {"operation": "find-css"}, timeout_seconds=3)

    assert captured.value.safe_reason_code == OPENCLI_SELECTOR_NOT_FOUND


def test_daemon_client_preserves_stale_control_fence_semantics() -> None:
    connection = _Connection(status_payload=_status(), command_error=(409, "stale_control_fence"))
    client = OpenCliDaemonClient(requirement=_requirement(), connection_factory=lambda *_args: connection)

    with pytest.raises(OpenCliBrowserError) as captured:
        client.command("browser-operation", {"operation": "get-url"}, timeout_seconds=3)

    assert captured.value.safe_reason_code == OPENCLI_STALE_CONTROL_FENCE


def test_daemon_client_rejects_reserved_fields_before_transport() -> None:
    connection = _Connection(status_payload=_status())
    client = OpenCliDaemonClient(requirement=_requirement(), connection_factory=lambda *_args: connection)

    with pytest.raises(OpenCliBrowserError) as captured:
        client.command("tabs", {"id": "caller-controlled", "op": "list"}, timeout_seconds=3)

    assert captured.value.safe_reason_code == OPENCLI_FORBIDDEN_COMMAND
    assert connection.requests == []


def test_daemon_client_does_not_expose_window_close_or_caller_selected_profile() -> None:
    connection = _Connection(status_payload=_status())
    client = OpenCliDaemonClient(requirement=_requirement(), connection_factory=lambda *_args: connection)

    with pytest.raises(OpenCliBrowserError) as close_error:
        client.command("close-window", {}, timeout_seconds=3)  # type: ignore[arg-type]
    with pytest.raises(OpenCliBrowserError) as profile_error:
        client.command("tabs", {"op": "list", "contextId": "other-profile"}, timeout_seconds=3)

    assert close_error.value.safe_reason_code == OPENCLI_FORBIDDEN_COMMAND
    assert profile_error.value.safe_reason_code == OPENCLI_FORBIDDEN_COMMAND
    assert connection.requests == []


def test_daemon_client_rejects_oversized_response() -> None:
    connection = _Connection(status_payload=_status())

    def oversized_response() -> _Response:
        return _Response(200, b"{" + b"x" * OPENCLI_DAEMON_MAX_RESPONSE_BYTES + b"}")

    connection.getresponse = oversized_response  # type: ignore[method-assign]
    client = OpenCliDaemonClient(requirement=_requirement(), connection_factory=lambda *_args: connection)

    with pytest.raises(OpenCliBrowserError) as captured:
        client.verify_bridge()

    assert captured.value.safe_reason_code == OPENCLI_STATUS_UNAVAILABLE
    assert connection.closed is True
