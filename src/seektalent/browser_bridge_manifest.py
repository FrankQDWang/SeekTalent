from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast


BROWSER_BRIDGE_MANIFEST_SCHEMA = "seektalent.browser_bridge_bundle.v1"
BROWSER_BRIDGE_IMPLEMENTATION = "seektalent-opencli"
BROWSER_BRIDGE_PROTOCOL_MAJOR = 1
REQUIRED_BROWSER_BRIDGE_CAPABILITIES = frozenset(
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

BrowserBridgeManifestErrorCode = Literal[
    "integrity_failed",
    "wrong_implementation",
    "protocol_mismatch",
    "capability_missing",
]


class BrowserBridgeManifestError(RuntimeError):
    def __init__(self, code: BrowserBridgeManifestErrorCode) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BrowserBridgeRequirement:
    implementation: str
    bridge_build_id: str
    protocol_major: int
    protocol_minor: int
    capabilities: frozenset[str]


def load_browser_bridge_requirement(path: Path) -> BrowserBridgeRequirement:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrowserBridgeManifestError("integrity_failed") from exc
    if not isinstance(payload, dict) or payload.get("schemaVersion") != BROWSER_BRIDGE_MANIFEST_SCHEMA:
        raise BrowserBridgeManifestError("integrity_failed")
    implementation = payload.get("implementation")
    if implementation != BROWSER_BRIDGE_IMPLEMENTATION:
        raise BrowserBridgeManifestError("wrong_implementation")
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
        raise BrowserBridgeManifestError("integrity_failed")
    if protocol["major"] != BROWSER_BRIDGE_PROTOCOL_MAJOR:
        raise BrowserBridgeManifestError("protocol_mismatch")
    capability_set = frozenset(cast("list[str]", capabilities))
    if not REQUIRED_BROWSER_BRIDGE_CAPABILITIES.issubset(capability_set):
        raise BrowserBridgeManifestError("capability_missing")
    return BrowserBridgeRequirement(
        implementation=implementation,
        bridge_build_id=bridge_build_id,
        protocol_major=protocol["major"],
        protocol_minor=protocol["minor"],
        capabilities=capability_set,
    )
