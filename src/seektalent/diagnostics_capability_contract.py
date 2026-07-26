"""Closed fact-to-capability contract for machine diagnostics receipts."""

from __future__ import annotations


CAPABILITY_FIELDS = frozenset(
    {
        "source_port",
        "browser_bridge",
        "endpoint_ownership",
        "database_integrity",
        "disk_access",
        "network_posture",
        "release_integrity",
    }
)

ENDPOINT_STATES = {
    "verified": ("supported", ()),
    "conflict": ("unsupported", ("endpoint_owner_conflict",)),
    "unknown": ("indeterminate", ("endpoint_owner_unknown",)),
}
DATABASE_STATES = {
    "ok": ("supported", ()),
    "failed": ("unsupported", ("database_integrity_failed",)),
    "unknown": ("indeterminate", ("database_integrity_unknown",)),
}


def expected_capabilities(
    *,
    bridge_implementation: str,
    bridge_capabilities: tuple[str, ...],
    endpoint_ownership: str,
    database_integrity: str,
    disk_writable: bool,
    disk_executable: bool,
    network_offline: bool,
    manifest_signature_status: str,
    artifact_signature_status: str,
) -> tuple[dict[str, str], tuple[str, ...]]:
    capabilities: dict[str, str] = {}
    gaps: list[str] = []

    source_port_supported = (
        bridge_implementation != "none" and "source_port_v1" in bridge_capabilities
    )
    capabilities["source_port"] = "supported" if source_port_supported else "unsupported"
    if not source_port_supported:
        gaps.append("source_port_unsupported")

    browser_supported = (
        bridge_implementation != "none" and "browser_control" in bridge_capabilities
    )
    capabilities["browser_bridge"] = "supported" if browser_supported else "unsupported"
    if not browser_supported:
        gaps.append("browser_bridge_unsupported")

    endpoint_state, endpoint_gaps = ENDPOINT_STATES[endpoint_ownership]
    capabilities["endpoint_ownership"] = endpoint_state
    gaps.extend(endpoint_gaps)

    database_state, database_gaps = DATABASE_STATES[database_integrity]
    capabilities["database_integrity"] = database_state
    gaps.extend(database_gaps)

    disk_gaps = []
    if not disk_writable:
        disk_gaps.append("disk_not_writable")
    if not disk_executable:
        disk_gaps.append("disk_not_executable")
    capabilities["disk_access"] = "unsupported" if disk_gaps else "supported"
    gaps.extend(disk_gaps)

    capabilities["network_posture"] = "unsupported" if network_offline else "supported"
    if network_offline:
        gaps.append("network_offline")

    signature_statuses = (manifest_signature_status, artifact_signature_status)
    if "failed" in signature_statuses:
        capabilities["release_integrity"] = "unsupported"
        if manifest_signature_status == "failed":
            gaps.append("manifest_signature_failed")
        if artifact_signature_status == "failed":
            gaps.append("artifact_signature_failed")
    elif all(status == "verified" for status in signature_statuses):
        capabilities["release_integrity"] = "supported"
    else:
        capabilities["release_integrity"] = "indeterminate"
        gaps.append("signature_verification_missing")

    return capabilities, tuple(gaps)


def aggregate_capability_result(capabilities: dict[str, str]) -> str:
    states = set(capabilities.values())
    if "unsupported" in states:
        return "unsupported"
    if "indeterminate" in states:
        return "indeterminate"
    return "supported"
