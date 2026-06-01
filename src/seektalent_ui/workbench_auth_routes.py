from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from seektalent_ui.auth import (
    DUMMY_PASSWORD_HASH,
    clear_session_cookie,
    get_session_cookie,
    get_workbench_store,
    hash_password,
    is_loopback_client,
    require_csrf_user,
    require_current_user,
    session_token_digest,
    set_csrf_cookie,
    set_session_cookie,
    verify_password,
)
from seektalent_ui.models import (
    WorkbenchBootstrapRequest,
    WorkbenchBootstrapResponse,
    WorkbenchLoginRequest,
    WorkbenchMeResponse,
)
from seektalent_ui.workbench_response import user_response, workspace_response
from seektalent_ui.workbench_store import BootstrapAlreadyCompleteError, WorkbenchUser


router = APIRouter()


@router.post("/api/auth/bootstrap", response_model=WorkbenchBootstrapResponse, status_code=201)
def bootstrap_admin(request: WorkbenchBootstrapRequest, http_request: Request) -> WorkbenchBootstrapResponse:
    if not is_loopback_client(http_request):
        raise HTTPException(status_code=403, detail="Bootstrap is only available from loopback clients.")
    store = get_workbench_store(http_request)
    try:
        user, workspace = store.bootstrap_admin(
            email=request.email,
            display_name=request.displayName,
            password_hash=hash_password(request.password),
        )
    except BootstrapAlreadyCompleteError as exc:
        raise HTTPException(status_code=409, detail="Bootstrap admin already exists.") from exc
    return WorkbenchBootstrapResponse(
        user=user_response(user),
        workspace=workspace_response(workspace),
    )


@router.post("/api/auth/login", status_code=204)
def login(request: WorkbenchLoginRequest, http_request: Request, response: Response) -> Response:
    store = get_workbench_store(http_request)
    ip_address = http_request.client.host if http_request.client else None
    user_agent = http_request.headers.get("user-agent")
    login_row = store.get_user_for_login(email=request.email)
    if store.is_login_locked(email=request.email, ip_address=ip_address):
        password_hash = login_row[1] if login_row is not None else DUMMY_PASSWORD_HASH
        verify_password(request.password, password_hash)
        store.record_login_attempt(
            email=request.email,
            success=False,
            reason="locked_out",
            user_id=login_row[0].user_id if login_row is not None else None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if login_row is None:
        verify_password(request.password, DUMMY_PASSWORD_HASH)
        store.record_login_attempt(
            email=request.email,
            success=False,
            reason="invalid_credentials",
            user_id=None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    user, password_hash, disabled = login_row
    if disabled:
        verify_password(request.password, password_hash)
        store.record_login_attempt(
            email=request.email,
            success=False,
            reason="disabled_user",
            user_id=user.user_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if not verify_password(request.password, password_hash):
        store.record_login_attempt(
            email=request.email,
            success=False,
            reason="invalid_credentials",
            user_id=user.user_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    session_tokens = store.create_user_session(user_id=user.user_id, workspace_id=user.workspace_id)
    store.record_login_attempt(
        email=request.email,
        success=True,
        reason="success",
        user_id=user.user_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    set_session_cookie(response, request=http_request, session_id=session_tokens.session_token)
    set_csrf_cookie(response, request=http_request, csrf_token=session_tokens.csrf_token)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/api/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    user: WorkbenchUser = Depends(require_csrf_user),
    session_id: str | None = Depends(get_session_cookie),
) -> Response:
    store = get_workbench_store(request)
    store.revoke_user_session(
        session_digest=session_token_digest(session_id) if session_id is not None else None,
        user=user,
    )
    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/api/auth/me", response_model=WorkbenchMeResponse)
def me(
    http_request: Request,
    response: Response,
    user: WorkbenchUser = Depends(require_current_user),
    session_id: str | None = Depends(get_session_cookie),
) -> WorkbenchMeResponse:
    if session_id is not None:
        store = get_workbench_store(http_request)
        csrf_token = store.rotate_session_csrf(session_digest=session_token_digest(session_id))
        set_csrf_cookie(response, request=http_request, csrf_token=csrf_token)
    return WorkbenchMeResponse(user=user_response(user))
