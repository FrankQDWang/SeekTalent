from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from seektalent_ui.auth import DUMMY_PASSWORD_HASH, verify_password
from seektalent_ui.workbench_store import UserSessionTokens, WorkbenchStore, WorkbenchUser


LoginStatus = Literal["success", "invalid_credentials"]


@dataclass(frozen=True)
class WorkbenchLoginInput:
    email: str
    password: str
    ip_address: str | None
    user_agent: str | None


@dataclass(frozen=True)
class WorkbenchLoginResult:
    status: LoginStatus
    user: WorkbenchUser | None
    session_tokens: UserSessionTokens | None


class WorkbenchAuthService:
    def __init__(self, *, store: WorkbenchStore) -> None:
        self._store = store

    def login(self, request: WorkbenchLoginInput) -> WorkbenchLoginResult:
        login_row = self._store.get_user_for_login(email=request.email)
        if self._store.is_login_locked(email=request.email, ip_address=request.ip_address):
            password_hash = login_row[1] if login_row is not None else DUMMY_PASSWORD_HASH
            verify_password(request.password, password_hash)
            self._record_failure(request, reason="locked_out", user=login_row[0] if login_row is not None else None)
            return WorkbenchLoginResult(status="invalid_credentials", user=None, session_tokens=None)
        if login_row is None:
            verify_password(request.password, DUMMY_PASSWORD_HASH)
            self._record_failure(request, reason="invalid_credentials", user=None)
            return WorkbenchLoginResult(status="invalid_credentials", user=None, session_tokens=None)
        user, password_hash, disabled = login_row
        if disabled:
            verify_password(request.password, password_hash)
            self._record_failure(request, reason="disabled_user", user=user)
            return WorkbenchLoginResult(status="invalid_credentials", user=None, session_tokens=None)
        if not verify_password(request.password, password_hash):
            self._record_failure(request, reason="invalid_credentials", user=user)
            return WorkbenchLoginResult(status="invalid_credentials", user=None, session_tokens=None)

        session_tokens = self._store.create_user_session(user_id=user.user_id, workspace_id=user.workspace_id)
        self._store.record_login_attempt(
            email=request.email,
            success=True,
            reason="success",
            user_id=user.user_id,
            ip_address=request.ip_address,
            user_agent=request.user_agent,
        )
        return WorkbenchLoginResult(status="success", user=user, session_tokens=session_tokens)

    def _record_failure(
        self,
        request: WorkbenchLoginInput,
        *,
        reason: str,
        user: WorkbenchUser | None,
    ) -> None:
        self._store.record_login_attempt(
            email=request.email,
            success=False,
            reason=reason,
            user_id=user.user_id if user is not None else None,
            ip_address=request.ip_address,
            user_agent=request.user_agent,
        )
