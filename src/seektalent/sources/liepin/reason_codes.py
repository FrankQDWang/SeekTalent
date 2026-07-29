"""Compatibility exports for the product failure-interpretation contract."""

from seektalent.failure_interpretation import (
    LIEPIN_FAILURE_POLICIES,
    LIEPIN_PRODUCTION_FAILURE_REASON_CODES,
    LIEPIN_RECOVERY_GUIDANCE,
    PUBLIC_SOURCE_PROBLEMS,
    PUBLIC_SOURCE_REASON_CODES,
    LiepinRecoveryGuidance,
    legacy_lane_retryable_metadata,
    liepin_recovery_guidance,
    public_liepin_failure_cause_code,
    public_source_failure_cause_code,
    public_source_problem_code,
    public_source_problem_message,
    user_action_for_liepin_failure,
)

LIEPIN_BACKEND_MODE_BY_WORKER_MODE = {
    "disabled": "blocked",
    "opencli": "opencli",
    "fake_fixture": "fake_fixture",
}

__all__ = [
    "LIEPIN_BACKEND_MODE_BY_WORKER_MODE",
    "LIEPIN_FAILURE_POLICIES",
    "LIEPIN_PRODUCTION_FAILURE_REASON_CODES",
    "LIEPIN_RECOVERY_GUIDANCE",
    "LiepinRecoveryGuidance",
    "PUBLIC_SOURCE_PROBLEMS",
    "PUBLIC_SOURCE_REASON_CODES",
    "legacy_lane_retryable_metadata",
    "liepin_recovery_guidance",
    "public_liepin_failure_cause_code",
    "public_source_failure_cause_code",
    "public_source_problem_code",
    "public_source_problem_message",
    "user_action_for_liepin_failure",
]
