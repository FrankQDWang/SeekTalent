"""Canonical main-owned user actions."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

UserActionCode = Literal[
    "open_liepin_host",
    "complete_identity_check",
    "log_in_to_liepin",
    "complete_liepin_risk_check",
    "resolve_liepin_modal",
]
UserActionInstruction = Literal[
    "user_action.open_liepin_host",
    "user_action.complete_identity_check",
    "user_action.log_in_to_liepin",
    "user_action.complete_liepin_risk_check",
    "user_action.resolve_liepin_modal",
]
UserActionScope = Literal[
    "liepin_browser_session",
    "liepin_identity",
    "liepin_account",
    "liepin_risk_control",
    "liepin_browser_modal",
]

USER_ACTION_INSTRUCTIONS: dict[UserActionCode, UserActionInstruction] = {
    "open_liepin_host": "user_action.open_liepin_host",
    "complete_identity_check": "user_action.complete_identity_check",
    "log_in_to_liepin": "user_action.log_in_to_liepin",
    "complete_liepin_risk_check": "user_action.complete_liepin_risk_check",
    "resolve_liepin_modal": "user_action.resolve_liepin_modal",
}
USER_ACTION_SCOPES: dict[UserActionCode, UserActionScope] = {
    "open_liepin_host": "liepin_browser_session",
    "complete_identity_check": "liepin_identity",
    "log_in_to_liepin": "liepin_account",
    "complete_liepin_risk_check": "liepin_risk_control",
    "resolve_liepin_modal": "liepin_browser_modal",
}
class UserActionV1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )

    code: UserActionCode
    instruction_key: UserActionInstruction
    scope: UserActionScope
    affected_scope_ref: str

    @model_validator(mode="after")
    def validate_closed_mapping(self) -> Self:
        if (
            USER_ACTION_INSTRUCTIONS[self.code] != self.instruction_key
            or USER_ACTION_SCOPES[self.code] != self.scope
            or not 1 <= len(self.affected_scope_ref.encode("utf-8")) <= 96
            or self.affected_scope_ref.strip() != self.affected_scope_ref
            or "\x00" in self.affected_scope_ref
        ):
            raise ValueError("user_action_mapping_invalid")
        return self
__all__ = [
    "USER_ACTION_INSTRUCTIONS",
    "USER_ACTION_SCOPES",
    "UserActionCode",
    "UserActionInstruction",
    "UserActionScope",
    "UserActionV1",
]
