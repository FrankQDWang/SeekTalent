from __future__ import annotations

from seektalent.opencli_browser.contracts import OpenCliBrowserError
from seektalent.opencli_browser.daemon_transport import OpenCliDaemonClient, load_bridge_requirement
from seektalent.opencli_browser.reason_codes import OPENCLI_BRIDGE_INTEGRITY_FAILED
from seektalent.wtscli_runtime import WtsCliRuntime


def connect_existing_opencli_daemon_read_only(
    runtime: WtsCliRuntime,
    *,
    context_id: str | None = None,
    verify_timeout_seconds: float = 0.3,
) -> OpenCliDaemonClient:
    """Connect to an existing daemon without starting, restarting, or repair."""
    manifest = runtime.bridge_manifest
    if manifest is None or not manifest.is_file():
        raise OpenCliBrowserError(OPENCLI_BRIDGE_INTEGRITY_FAILED)
    requirement = runtime.requirement or load_bridge_requirement(manifest)
    client = OpenCliDaemonClient(
        requirement=requirement,
        context_id=context_id,
    )
    try:
        client.verify_bridge(
            timeout_seconds=max(0.001, verify_timeout_seconds)
        )
    except Exception:
        client.close()
        raise
    return client
