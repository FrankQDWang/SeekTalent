"""Strict source transport to canonical main action mapping."""

from __future__ import annotations

from seektalent.source_port.verify_session_contract import (
    VerifySessionUserActionV1,
)
from seektalent.user_action import (
    USER_ACTION_INSTRUCTIONS,
    USER_ACTION_SCOPES,
    UserActionCode,
    UserActionV1,
)


_VERIFY_SESSION_ACTION_CODES: dict[str, UserActionCode] = {
    "liepin_host_tab_missing": "open_liepin_host",
    "liepin_opencli_identity_intercept": "complete_identity_check",
    "liepin_opencli_login_required": "log_in_to_liepin",
    "liepin_opencli_risk_page": "complete_liepin_risk_check",
    "liepin_opencli_unknown_modal": "resolve_liepin_modal",
}


def map_verify_session_user_action(
    source_action: VerifySessionUserActionV1,
    *,
    affected_scope_ref: str,
) -> UserActionV1:
    if type(source_action) is not VerifySessionUserActionV1:
        raise TypeError("verify_session_user_action_required")
    code = _VERIFY_SESSION_ACTION_CODES.get(source_action.code)
    if code is None:
        raise ValueError("verify_session_user_action_unsupported")
    return UserActionV1(
        code=code,
        instruction_key=USER_ACTION_INSTRUCTIONS[code],
        scope=USER_ACTION_SCOPES[code],
        affected_scope_ref=affected_scope_ref,
    )


__all__ = ["map_verify_session_user_action"]
