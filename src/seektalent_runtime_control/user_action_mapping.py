"""Strict source transport to canonical main action mapping."""

from __future__ import annotations

from seektalent.source_port.verify_session_contract import (
    VerifySessionUserActionV1,
)
from seektalent.sources.liepin.reason_codes import interpret_liepin_failure
from seektalent.user_action import UserActionV1


def map_verify_session_user_action(
    source_action: VerifySessionUserActionV1,
    *,
    affected_scope_ref: str,
) -> UserActionV1:
    if type(source_action) is not VerifySessionUserActionV1:
        raise TypeError("verify_session_user_action_required")
    action = interpret_liepin_failure(
        source_action.code,
        operation="verify_session",
        affected_scope_ref=affected_scope_ref,
    ).user_action
    if action is None:
        raise ValueError("verify_session_user_action_unsupported")
    return action


__all__ = ["map_verify_session_user_action"]
