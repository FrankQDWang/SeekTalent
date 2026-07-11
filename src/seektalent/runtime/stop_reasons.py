from __future__ import annotations

PUBLIC_STOP_REASON_ALLOWLIST: tuple[str, ...] = (
    "enough_high_fit_candidates",
    "insufficient_new_candidates",
    "no_progress_repeated_results",
    "max_rounds_reached",
    "controller_stop",
    "reflection_stop",
    "target_satisfied",
    "provider_exhausted",
    "max_pages_reached",
    "max_attempts_reached",
    "source_lanes_completed",
    "source_lanes_degraded",
    "query_family_exhausted",
)


def normalize_stop_reason(stop_reason: str | None) -> str:
    if stop_reason in PUBLIC_STOP_REASON_ALLOWLIST:
        return stop_reason
    return "controller_stop"
