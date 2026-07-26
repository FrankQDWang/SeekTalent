from __future__ import annotations

import subprocess
import time

from seektalent.opencli_browser.contracts import OpenCliBrowserError
from seektalent.opencli_browser.daemon_transport import OpenCliDaemonClient, load_bridge_requirement
from seektalent.opencli_browser.reason_codes import (
    OPENCLI_BRIDGE_BUILD_MISMATCH,
    OPENCLI_BRIDGE_CAPABILITY_MISSING,
    OPENCLI_BRIDGE_INTEGRITY_FAILED,
    OPENCLI_BRIDGE_PROTOCOL_MISMATCH,
    OPENCLI_BRIDGE_WRONG_IMPLEMENTATION,
    OPENCLI_DAEMON_NOT_RUNNING,
    OPENCLI_EXTENSION_DISCONNECTED,
)
from seektalent.opencli_launcher import (
    BootstrapError,
    OpenCliRuntime,
    opencli_subprocess_env,
    runtime_requirement,
)


OPENCLI_DAEMON_RESTART_TIMEOUT_SECONDS = 10
OPENCLI_DAEMON_VERIFY_TIMEOUT_SECONDS = 40.0

_RESTARTABLE_REASONS = frozenset(
    {
        OPENCLI_BRIDGE_BUILD_MISMATCH,
        OPENCLI_BRIDGE_CAPABILITY_MISSING,
        OPENCLI_BRIDGE_PROTOCOL_MISMATCH,
        OPENCLI_BRIDGE_WRONG_IMPLEMENTATION,
        OPENCLI_DAEMON_NOT_RUNNING,
    }
)


def connect_installed_opencli_daemon(
    runtime: OpenCliRuntime,
    *,
    context_id: str | None = None,
    verify_timeout_seconds: float = OPENCLI_DAEMON_VERIFY_TIMEOUT_SECONDS,
) -> OpenCliDaemonClient:
    manifest = runtime.bridge_manifest
    if manifest is None:
        raise OpenCliBrowserError(OPENCLI_BRIDGE_INTEGRITY_FAILED)
    requirement = runtime.requirement or load_bridge_requirement(manifest)
    client = OpenCliDaemonClient(
        requirement=requirement,
        context_id=context_id,
    )
    deadline = time.monotonic() + verify_timeout_seconds
    last_error = OpenCliBrowserError(OPENCLI_DAEMON_NOT_RUNNING)
    restart_required = False
    try:
        client.verify_bridge(timeout_seconds=min(0.3, verify_timeout_seconds))
        return client
    except OpenCliBrowserError as exc:
        last_error = exc
        if exc.safe_reason_code not in _RESTARTABLE_REASONS:
            if exc.safe_reason_code != OPENCLI_EXTENSION_DISCONNECTED:
                client.close()
                raise
        else:
            restart_required = True

    if restart_required:
        client.close()
        restart_timeout = min(
            OPENCLI_DAEMON_RESTART_TIMEOUT_SECONDS,
            deadline - time.monotonic(),
        )
        if restart_timeout <= 0:
            raise last_error
        try:
            _restart_installed_daemon(
                runtime,
                timeout_seconds=restart_timeout,
            )
        except OpenCliBrowserError:
            client.close()
            raise
    while time.monotonic() < deadline:
        try:
            client.verify_bridge(timeout_seconds=min(0.3, max(0.05, deadline - time.monotonic())))
            return client
        except OpenCliBrowserError as exc:
            last_error = exc
        time.sleep(0.1)
    client.close()
    raise last_error


def _restart_installed_daemon(
    runtime: OpenCliRuntime,
    *,
    timeout_seconds: float = OPENCLI_DAEMON_RESTART_TIMEOUT_SECONDS,
) -> None:
    try:
        requirement = runtime_requirement(runtime)
    except BootstrapError as exc:
        raise OpenCliBrowserError(OPENCLI_BRIDGE_INTEGRITY_FAILED) from exc
    try:
        completed = subprocess.run(
            (str(runtime.node), str(runtime.opencli_main), "daemon", "restart"),
            env=opencli_subprocess_env(
                node_bin_dir=runtime.node_bin_dir,
                requirement=requirement,
            ),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OpenCliBrowserError(OPENCLI_DAEMON_NOT_RUNNING) from exc
    if completed.returncode != 0:
        raise OpenCliBrowserError(OPENCLI_DAEMON_NOT_RUNNING)
