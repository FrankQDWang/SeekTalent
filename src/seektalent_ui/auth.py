from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
from typing import Annotated

from fastapi import Cookie, Header, HTTPException, Request, Response

from seektalent_ui.workbench_store import WorkbenchStore, WorkbenchUser


SESSION_COOKIE_NAME = "seektalent_workbench_session"
CSRF_COOKIE_NAME = "seektalent_workbench_csrf"
PASSWORD_HASH_ITERATIONS = 310_000
DUMMY_PASSWORD_HASH = (
    "pbkdf2_sha256$310000$c2Vla3RhbGVudC1kdW1teS1zYWx0"
    "$6VeHlfY5RCCUDwlMvd4E95qKnsHmSP5fTM7FtMh0BI0="
)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )
    return "$".join(
        [
            "pbkdf2_sha256",
            str(PASSWORD_HASH_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = base64.urlsafe_b64decode(salt_raw.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_raw.encode("ascii"))
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def session_token_digest(session_token: str) -> str:
    digest = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
    return f"sha256${digest}"


def is_loopback_client(request: Request) -> bool:
    if request.client is None:
        return False
    host = request.client.host
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost", "testclient"}


def require_current_user(
    request: Request,
    cookie_session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> WorkbenchUser:
    store = get_workbench_store(request)
    user = store.get_user_by_session(session_digest=_digest_or_none(cookie_session_id))
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


def require_current_user_readonly(
    request: Request,
    cookie_session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> WorkbenchUser:
    store = get_workbench_store(request)
    user = store.get_user_by_session_readonly(session_digest=_digest_or_none(cookie_session_id))
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


def require_csrf_user(
    request: Request,
    cookie_session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> WorkbenchUser:
    store = get_workbench_store(request)
    session_digest = _digest_or_none(cookie_session_id)
    user = store.get_user_by_session(session_digest=session_digest)
    if user is None or session_digest is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if not store.verify_session_csrf(session_digest=session_digest, csrf_token=x_csrf_token):
        raise HTTPException(status_code=403, detail="CSRF token is invalid.")
    return user


def get_session_cookie(
    cookie_session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> str | None:
    return cookie_session_id


def set_session_cookie(response: Response, *, request: Request, session_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        httponly=True,
        samesite="lax",
        secure=should_secure_cookie(request),
        path="/api",
        max_age=12 * 60 * 60,
    )


def set_csrf_cookie(response: Response, *, request: Request, csrf_token: str) -> None:
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        httponly=False,
        samesite="lax",
        secure=should_secure_cookie(request),
        path="/api",
        max_age=12 * 60 * 60,
    )
    response.headers["X-CSRF-Token"] = csrf_token


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/api", samesite="lax")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/api", samesite="lax")


def get_workbench_store(request: Request) -> WorkbenchStore:
    store = getattr(request.app.state, "workbench_store", None)
    if not isinstance(store, WorkbenchStore):
        raise HTTPException(status_code=500, detail="Workbench store is not configured.")
    return store


def should_secure_cookie(request: Request) -> bool:
    return request.url.scheme == "https"


def _digest_or_none(session_token: str | None) -> str | None:
    if session_token is None:
        return None
    return session_token_digest(session_token)
