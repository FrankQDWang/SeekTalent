"""Production WTSCLI verify-session composition."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from seektalent.browser_bridge_manifest import BrowserBridgeRequirement
from seektalent.opencli_browser.daemon_transport import (
    OpenCliDaemonAction,
    OpenCliDaemonResult,
)
from seektalent.source_port.authenticated_verify_session_frames import (
    PostHandshakeVerifySessionSession,
)
from seektalent.source_port.command_journal import CommandJournalSession
from seektalent.source_port.verify_session_journal_effect import (
    MonotonicClock,
    VerifySessionJournalEffectComposition,
    create_verify_session_journal_effect_composition,
)
from seektalent.wtscli_verify_session_adapter import (
    WtsCliCurrentProfileSnapshot,
    create_wtscli_verify_session_effect,
)


class _WtsCliDaemon(Protocol):
    def verify_bridge(
        self,
        *,
        timeout_seconds: float,
        validate: bool = True,
    ) -> Mapping[str, object]: ...

    def command(
        self,
        action: OpenCliDaemonAction,
        params: Mapping[str, object],
        *,
        timeout_seconds: float,
    ) -> OpenCliDaemonResult: ...


def create_wtscli_verify_session_composition(
    *,
    command_journal_session: CommandJournalSession,
    frame_session: PostHandshakeVerifySessionSession,
    daemon: _WtsCliDaemon,
    bridge_requirement: BrowserBridgeRequirement,
    current_profile_snapshot: Callable[
        [],
        WtsCliCurrentProfileSnapshot,
    ],
    control_key: str,
    monotonic_clock: MonotonicClock,
    poll_wait: Callable[[float], None],
) -> VerifySessionJournalEffectComposition:
    """Bind authenticated arrival, durable journal, and the one real WTSCLI effect."""
    effect = create_wtscli_verify_session_effect(
        daemon=daemon,
        bridge_requirement=bridge_requirement,
        current_profile_snapshot=current_profile_snapshot,
        control_key=control_key,
        monotonic_clock=monotonic_clock,
        poll_wait=poll_wait,
    )
    return create_verify_session_journal_effect_composition(
        command_journal_session=command_journal_session,
        frame_session=frame_session,
        effect=effect,
        monotonic_clock=monotonic_clock,
    )


__all__ = ["create_wtscli_verify_session_composition"]
