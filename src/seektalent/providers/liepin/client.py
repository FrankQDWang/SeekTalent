from __future__ import annotations

import json
from typing import Any
from typing import Callable, Protocol
from typing import TypeVar
from urllib.error import HTTPError
from urllib import parse
from urllib import request as urllib_request

from pydantic import ValidationError

from seektalent.config import AppSettings
from seektalent.models import ConstraintValue
from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.providers.liepin.mapper import map_liepin_worker_card
from seektalent.providers.liepin.worker_contracts import LiepinDetailOpenRequest
from seektalent.providers.liepin.worker_contracts import LiepinDetailOpenResponse
from seektalent.providers.liepin.worker_contracts import LiepinCardSearchResponse
from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError
from seektalent.providers.liepin.worker_contracts import LoginHandoff
from seektalent.providers.liepin.worker_contracts import SessionStatus
from seektalent.providers.liepin.worker_contracts import decode_card_search_response
from seektalent.providers.liepin.worker_contracts import decode_detail_open_response
from seektalent.providers.liepin.worker_contracts import decode_login_handoff
from seektalent.providers.liepin.worker_contracts import decode_session_status
from seektalent.providers.liepin.worker_contracts import decode_worker_health
from seektalent.providers.liepin.worker_runtime import ManagedLiepinWorkerRuntime


EventCallback = Callable[[str, dict[str, object]], None]
DecodedWorkerPayload = TypeVar("DecodedWorkerPayload")


class LiepinWorkerClient(Protocol):
    async def ensure_ready(self, *, on_event: EventCallback | None = None) -> None: ...

    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult: ...

    async def open_details(self, request: LiepinDetailOpenRequest) -> LiepinDetailOpenResponse: ...


class FakeLiepinWorkerClient:
    def __init__(self, settings: AppSettings) -> None:
        if settings.liepin_worker_mode != "fake_fixture" or not settings.liepin_allow_fake_fixture_worker:
            raise LiepinWorkerModeError(
                "Fake Liepin fixture worker requires liepin_worker_mode=fake_fixture "
                "and liepin_allow_fake_fixture_worker=True.",
                setup_status="fake_fixture_not_allowed",
            )
        if settings.liepin_live_enabled:
            raise LiepinWorkerModeError(
                "Fake Liepin fixture worker is not allowed when liepin_live_enabled=True.",
                setup_status="fake_fixture_live_rejected",
            )
        self.settings = settings

    async def ensure_ready(self, *, on_event: EventCallback | None = None) -> None:
        return None

    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult:
        del provider_account_hash
        return SearchResult(
            candidates=[],
            diagnostics=["liepin fake fixture worker"],
            exhausted=True,
            request_payload={
                "fixture_only": True,
                "keyword_query": request.keyword_query,
                "round_no": round_no,
                "trace_id": trace_id,
            },
            raw_candidate_count=0,
        )

    async def open_details(self, request: LiepinDetailOpenRequest) -> LiepinDetailOpenResponse:
        raise LiepinWorkerModeError("Fake Liepin fixture worker does not open live detail pages.")


class ManagedLocalLiepinWorkerClient:
    def __init__(
        self,
        settings: AppSettings,
        *,
        runtime: ManagedLiepinWorkerRuntime | None = None,
        http_json: Callable[..., dict[str, object]] | None = None,
    ) -> None:
        if settings.liepin_worker_mode != "managed_local":
            raise LiepinWorkerModeError("Managed local Liepin worker requires liepin_worker_mode=managed_local.")
        self.settings = settings
        self.runtime = runtime or ManagedLiepinWorkerRuntime.shared(settings)
        self.http_json = http_json or _default_http_json

    async def ensure_ready(self, *, on_event: EventCallback | None = None) -> None:
        self.runtime.ensure_started(on_event=on_event)

    async def session_status(
        self,
        *,
        connection_id: str,
        tenant: str | None = None,
        workspace: str | None = None,
        provider_account_hash: str | None = None,
    ) -> SessionStatus:
        base_url = self._internal_base_url()
        return _decode_worker_response(
            decode_session_status,
            self._request_json(
                "GET",
                _session_status_url(
                    base_url,
                    connection_id=connection_id,
                    tenant=tenant,
                    workspace=workspace,
                    provider_account_hash=provider_account_hash,
                ),
            )
        )

    async def login_handoff(self, *, connection_id: str) -> LoginHandoff:
        base_url = self._internal_base_url()
        return _decode_worker_response(
            decode_login_handoff,
            self._request_json(
                "POST",
                f"{base_url}/internal/session/login-handoff",
                json_body={"connectionId": connection_id},
            )
        )

    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult:
        await self.ensure_ready()
        base_url = self._internal_base_url()
        return _search_result_from_worker_response(
            _decode_worker_response(
                decode_card_search_response,
                self._request_json(
                    "POST",
                    f"{base_url}/internal/search/cards",
                    json_body=_search_request_body(
                        request,
                        round_no=round_no,
                        trace_id=trace_id,
                        provider_account_hash=provider_account_hash,
                    ),
                ),
            )
        )

    async def open_details(self, request: LiepinDetailOpenRequest) -> LiepinDetailOpenResponse:
        await self.ensure_ready()
        base_url = self._internal_base_url()
        return _decode_worker_response(
            decode_detail_open_response,
            self._request_json(
                "POST",
                f"{base_url}/internal/details/open",
                json_body=request.model_dump(mode="json", by_alias=True),
            ),
        )

    def _internal_base_url(self) -> str:
        handle = self.runtime.ensure_started()
        return handle.internal_base_url.rstrip("/")

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return self.http_json(
            method,
            url,
            headers={"Authorization": f"Bearer {self.settings.liepin_api_token}"},
            json_body=json_body,
            timeout=self.settings.liepin_worker_timeout_seconds,
        )


class ExternalHttpLiepinWorkerClient:
    def __init__(
        self,
        settings: AppSettings,
        *,
        http_json: Callable[..., dict[str, object]] | None = None,
    ) -> None:
        if settings.liepin_worker_mode != "external_http":
            raise LiepinWorkerModeError("External Liepin worker requires liepin_worker_mode=external_http.")
        if settings.liepin_worker_base_url is None:
            raise LiepinWorkerModeError(
                "liepin_worker_base_url is required when liepin_worker_mode=external_http.",
                setup_status="missing_external_worker_url",
            )
        self.settings = settings
        self.base_url = settings.liepin_worker_base_url.rstrip("/")
        self.http_json = http_json or _default_http_json

    async def ensure_ready(self, *, on_event: EventCallback | None = None) -> None:
        health = _decode_worker_response(
            decode_worker_health,
            self._request_json("GET", f"{self.base_url}/internal/health"),
        )
        if health.status != "ok":
            raise LiepinWorkerModeError("Liepin external worker is not ready.", setup_status=health.status)

    async def session_status(
        self,
        *,
        connection_id: str,
        tenant: str | None = None,
        workspace: str | None = None,
        provider_account_hash: str | None = None,
    ) -> SessionStatus:
        return _decode_worker_response(
            decode_session_status,
            self._request_json(
                "GET",
                _session_status_url(
                    self.base_url,
                    connection_id=connection_id,
                    tenant=tenant,
                    workspace=workspace,
                    provider_account_hash=provider_account_hash,
                ),
            )
        )

    async def login_handoff(self, *, connection_id: str) -> LoginHandoff:
        return _decode_worker_response(
            decode_login_handoff,
            self._request_json(
                "POST",
                f"{self.base_url}/internal/session/login-handoff",
                json_body={"connectionId": connection_id},
            )
        )

    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult:
        return _search_result_from_worker_response(
            _decode_worker_response(
                decode_card_search_response,
                self._request_json(
                    "POST",
                    f"{self.base_url}/internal/search/cards",
                    json_body=_search_request_body(
                        request,
                        round_no=round_no,
                        trace_id=trace_id,
                        provider_account_hash=provider_account_hash,
                    ),
                ),
            )
        )

    async def open_details(self, request: LiepinDetailOpenRequest) -> LiepinDetailOpenResponse:
        return _decode_worker_response(
            decode_detail_open_response,
            self._request_json(
                "POST",
                f"{self.base_url}/internal/details/open",
                json_body=request.model_dump(mode="json", by_alias=True),
            ),
        )

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return self.http_json(
            method,
            url,
            headers={"Authorization": f"Bearer {self.settings.liepin_api_token}"},
            json_body=json_body,
            timeout=self.settings.liepin_worker_timeout_seconds,
        )


def build_liepin_worker_client(settings: AppSettings) -> LiepinWorkerClient:
    if settings.liepin_worker_mode == "fake_fixture":
        return FakeLiepinWorkerClient(settings)
    if settings.liepin_worker_mode == "managed_local":
        return ManagedLocalLiepinWorkerClient(settings)
    if settings.liepin_worker_mode == "external_http":
        return ExternalHttpLiepinWorkerClient(settings)
    raise LiepinWorkerModeError(
        "Liepin worker mode is disabled; no worker client can be built.",
        setup_status="disabled",
    )


def _default_http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, object] | None,
    timeout: float,
) -> dict[str, object]:
    data: bytes | None = None
    request_headers = dict(headers)
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    req = urllib_request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib_request.urlopen(req, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as error:
        raise _worker_mode_error_from_http_error(error) from error
    decoded: Any = json.loads(payload)
    if not isinstance(decoded, dict):
        raise ValueError("Liepin worker response must be a JSON object")
    return decoded


def _decode_worker_response(
    decoder: Callable[[dict[str, object]], DecodedWorkerPayload],
    payload: dict[str, object],
) -> DecodedWorkerPayload:
    try:
        return decoder(payload)
    except ValidationError:
        raise LiepinWorkerModeError(
            "Liepin worker returned an invalid response.",
            setup_status="invalid_worker_response",
        ) from None


def _search_request_body(
    request: SearchRequest,
    *,
    round_no: int,
    trace_id: str,
    provider_account_hash: str | None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "keyword": request.keyword_query,
        "pageSize": request.page_size,
        "round": round_no,
        "traceId": trace_id,
    }
    context_fields = {
        "tenantId": "liepin_tenant_id",
        "workspaceId": "liepin_workspace_id",
        "connectionId": "liepin_connection_id",
    }
    for body_key, context_key in context_fields.items():
        value = request.provider_context.get(context_key)
        if value:
            body[body_key] = value
    if provider_account_hash is not None:
        body["providerAccountHash"] = provider_account_hash
    if request.cursor is not None:
        body["cursor"] = request.cursor
    if request.provider_filters:
        body["providerFilters"] = _safe_provider_filters(request.provider_filters)
    return body


def _safe_provider_filters(filters: dict[str, ConstraintValue]) -> dict[str, object]:
    safe_filters: dict[str, object] = {}
    for key, value in filters.items():
        if not key:
            continue
        safe_value = _safe_provider_filter_value(value)
        if safe_value is not None:
            safe_filters[key] = safe_value
    return safe_filters


def _safe_provider_filter_value(value: ConstraintValue) -> object | None:
    if isinstance(value, str | int | float) and not isinstance(value, bool):
        return value
    if isinstance(value, list):
        safe_items = [item for item in value if isinstance(item, str)]
        return safe_items if safe_items else None
    return None


def _search_result_from_worker_response(response: LiepinCardSearchResponse) -> SearchResult:
    mapped = [map_liepin_worker_card(card) for card in response.cards]
    return SearchResult(
        candidates=[item.candidate for item in mapped],
        diagnostics=response.diagnostics,
        exhausted=response.exhausted,
        next_cursor=response.next_cursor,
        request_payload=_safe_search_request_payload(response.request_payload),
        provider_snapshots=[item.provider_snapshot for item in mapped],
        raw_candidate_count=response.raw_candidate_count
        if response.raw_candidate_count is not None
        else len(response.cards),
    )


def _safe_search_request_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {"keyword", "pageSize", "cursor", "round", "traceId", "providerFilters"}
    return {key: value for key, value in payload.items() if key in allowed_keys}


def _session_status_url(
    base_url: str,
    *,
    connection_id: str,
    tenant: str | None,
    workspace: str | None,
    provider_account_hash: str | None,
) -> str:
    query = {"connectionId": connection_id}
    if tenant is not None:
        query["tenantId"] = tenant
    if workspace is not None:
        query["workspaceId"] = workspace
    if provider_account_hash is not None:
        query["providerAccountHash"] = provider_account_hash
    return f"{base_url}/internal/session/status?{parse.urlencode(query)}"


def _worker_mode_error_from_http_error(error: HTTPError) -> LiepinWorkerModeError:
    safe_worker_errors = {
        "session_not_ready": "Liepin worker session is not ready.",
        "card_search_not_configured": "Liepin worker card search is not configured.",
        "invalid_worker_request": "Liepin worker rejected the request.",
        "not_found": "Liepin worker endpoint was not found.",
        "worker_auth_required": "Liepin worker authentication is required.",
        "worker_auth_forbidden": "Liepin worker authentication was rejected.",
        "missing_preapproved_idempotency_key": "Liepin worker requires a preapproved idempotency key.",
        "unapproved_idempotency_key": "Liepin worker rejected the idempotency key.",
        "budget_decision_not_allowed_in_worker": "Liepin worker rejected an unsupported budget field.",
        "detail_open_approval_not_configured": "Liepin worker detail-open approval is not configured.",
        "detail_open_not_configured": "Liepin worker detail open is not configured.",
    }
    code = "worker_request_failed"
    try:
        decoded = json.loads(error.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        decoded = None
    if isinstance(decoded, dict) and isinstance(decoded.get("error"), dict):
        error_payload = decoded["error"]
        raw_code = error_payload.get("code")
        if isinstance(raw_code, str) and raw_code in safe_worker_errors:
            code = raw_code
    message = safe_worker_errors.get(code, "Liepin worker request failed.")
    return LiepinWorkerModeError(f"{code}: {message}", setup_status=code)
