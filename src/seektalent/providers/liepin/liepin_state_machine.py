from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal


RetryPolicy = Literal["none", "no_repeat_toggle"]


@dataclass(frozen=True, kw_only=True)
class LiepinStateSnapshot:
    ok: bool
    text: str
    url: str | None = None
    safe_reason_code: str | None = None
    observation: dict[str, object] | None = None


@dataclass(frozen=True, kw_only=True)
class TransitionResult:
    ok: bool
    safe_reason_code: str | None = None
    debug_reason: str | None = None
    event: dict[str, object] | None = None


@dataclass(frozen=True, kw_only=True)
class LiepinTransition:
    name: str
    phase: str
    observe_pre_state: Callable[[], LiepinStateSnapshot]
    precondition: Callable[[LiepinStateSnapshot], bool]
    action: Callable[[], TransitionResult]
    observe_post_state: Callable[[], LiepinStateSnapshot]
    postcondition: Callable[[LiepinStateSnapshot], bool]
    safe_reason_code: str
    trace_event: str
    retry_policy: RetryPolicy = "none"


class LiepinTransitionRunner:
    def run(self, transition: LiepinTransition) -> TransitionResult:
        pre_state = transition.observe_pre_state()
        if not pre_state.ok:
            return TransitionResult(
                ok=False,
                safe_reason_code=pre_state.safe_reason_code
                or transition.safe_reason_code,
                debug_reason="pre_state_failed",
            )

        if not transition.precondition(pre_state):
            return TransitionResult(
                ok=False,
                safe_reason_code=transition.safe_reason_code,
                debug_reason="precondition_failed",
            )

        result = transition.action()
        if not result.ok:
            return result

        post_state = transition.observe_post_state()
        if not post_state.ok:
            return TransitionResult(
                ok=False,
                safe_reason_code=post_state.safe_reason_code
                or transition.safe_reason_code,
                debug_reason="post_state_failed",
                event=result.event,
            )

        if transition.postcondition(post_state):
            return result

        return TransitionResult(
            ok=False,
            safe_reason_code=transition.safe_reason_code,
            debug_reason="postcondition_failed",
            event=result.event,
        )
