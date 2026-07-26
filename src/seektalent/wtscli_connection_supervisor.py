"""Main-owned pre-dispatch readiness for the exact installed WTSCLI bridge."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
import time

from seektalent.opencli_browser.contracts import OpenCliBrowserError
from seektalent.opencli_browser.daemon_process import (
    OPENCLI_DAEMON_VERIFY_TIMEOUT_SECONDS,
    connect_installed_opencli_daemon,
)
from seektalent.opencli_launcher import OpenCliRuntime


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
WTSCLI_CONNECTION_READINESS_TIMEOUT_SECONDS = OPENCLI_DAEMON_VERIFY_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class WtsCliConnectionReceipt:
    """Privacy-safe transient proof that exact daemon and extension were paired."""

    daemon_build_id: str
    extension_build_id: str
    endpoint: str
    ownership_ref: str
    last_connected_at: int | None
    elapsed_milliseconds: int


class WtsCliConnectionError(RuntimeError):
    def __init__(self, safe_reason_code: str) -> None:
        super().__init__(safe_reason_code)
        self.safe_reason_code = safe_reason_code


class InstalledWtsCliConnectionSupervisor:
    """Start only the admitted WTSCLI daemon and wait for its exact extension."""

    def __init__(
        self,
        runtime: OpenCliRuntime,
        *,
        context_id: str | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runtime = runtime
        self._context_id = context_id
        self._monotonic_clock = monotonic_clock

    def await_ready(self, *, timeout_seconds: float) -> WtsCliConnectionReceipt:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        started = self._monotonic_clock()
        try:
            client = connect_installed_opencli_daemon(
                self._runtime,
                context_id=self._context_id,
                verify_timeout_seconds=timeout_seconds,
            )
        except OpenCliBrowserError as exc:
            raise WtsCliConnectionError(exc.safe_reason_code) from None
        try:
            elapsed = max(0.0, self._monotonic_clock() - started)
            remaining = timeout_seconds - elapsed
            if remaining <= 0:
                raise WtsCliConnectionError("wtscli_readiness_deadline_exceeded")
            try:
                status = client.verify_bridge(
                    timeout_seconds=remaining,
                )
            except OpenCliBrowserError as exc:
                raise WtsCliConnectionError(exc.safe_reason_code) from None
            requirement = client.requirement
        finally:
            client.close()
        daemon_build = status.get("bridgeBuildId")
        extension_build = status.get("extensionBridgeBuildId")
        ownership_hash = status.get("ownerTokenHash")
        last_connected = status.get("lastSeenAt")
        if (
            type(daemon_build) is not str
            or type(extension_build) is not str
            or type(ownership_hash) is not str
            or _SHA256.fullmatch(ownership_hash) is None
            or (last_connected is not None and (type(last_connected) is not int or last_connected < 0))
        ):
            raise WtsCliConnectionError("wtscli_connection_receipt_invalid")
        endpoint = requirement.runtime_identity.endpoint
        return WtsCliConnectionReceipt(
            daemon_build_id=daemon_build,
            extension_build_id=extension_build,
            endpoint=f"{endpoint.host}:{endpoint.port}",
            ownership_ref=f"sha256:{ownership_hash}",
            last_connected_at=last_connected,
            elapsed_milliseconds=round(max(0.0, self._monotonic_clock() - started) * 1000),
        )


__all__ = [
    "InstalledWtsCliConnectionSupervisor",
    "WTSCLI_CONNECTION_READINESS_TIMEOUT_SECONDS",
    "WtsCliConnectionError",
    "WtsCliConnectionReceipt",
]
