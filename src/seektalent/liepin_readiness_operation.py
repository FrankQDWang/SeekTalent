"""Durable lane admission for mutating Liepin readiness preparation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from seektalent.config import AppSettings
from seektalent.liepin_verify_session_gate import (
    _prepare_session,
    _raise_reason,
)
from seektalent_runtime_control.browser_lane import (
    BrowserLaneBusyError,
    BrowserLaneGuard,
)
from seektalent_runtime_control.store import RuntimeControlStore


def prepare_production_liepin_readiness(
    *,
    settings: AppSettings,
    store: RuntimeControlStore,
    runtime_run_id: str,
    operation_id: str,
) -> None:
    try:
        with BrowserLaneGuard(
            store=store,
            runtime_run_id=runtime_run_id,
            operation_id=operation_id,
            operation_kind="prepare_readiness",
            now=_now,
            plus_seconds=_plus_seconds,
            wait_timeout_seconds=max(
                0.001,
                settings.liepin_opencli_timeout_seconds,
            ),
        ):
            _prepare_session(settings)
    except BrowserLaneBusyError:
        _raise_reason("liepin_opencli_status_unavailable")


def _now() -> str:
    return datetime.now(UTC).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _plus_seconds(value: str, seconds: float) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        parsed + timedelta(seconds=seconds)
    ).isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = ["prepare_production_liepin_readiness"]
