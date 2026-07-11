from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from typing import Callable, Protocol, TypeGuard, runtime_checkable
from typing import TypeVar
from typing import cast
from urllib.error import HTTPError
from urllib import parse
from urllib import request as urllib_request

from pydantic import ValidationError

from seektalent.config import AppSettings
from seektalent.models import ConstraintValue
from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.providers.liepin.mapper import map_liepin_worker_card
from seektalent.providers.liepin.mapper import map_liepin_worker_detail
from seektalent.providers.liepin.worker_contracts import LiepinDetailOpenRequest
from seektalent.providers.liepin.worker_contracts import LiepinDetailOpenResponse
from seektalent.providers.liepin.worker_contracts import LiepinCardSearchResponse
from seektalent.providers.liepin.worker_contracts import LiepinResumeSearchResponse
from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError
from seektalent.providers.liepin.worker_contracts import LoginRelayCompleteResult
from seektalent.providers.liepin.worker_contracts import LoginRelayInputResult
from seektalent.providers.liepin.worker_contracts import LoginRelaySnapshot
from seektalent.providers.liepin.worker_contracts import LoginHandoff
from seektalent.providers.liepin.worker_contracts import SessionStatus
from seektalent.providers.liepin.worker_contracts import decode_card_search_response
from seektalent.providers.liepin.worker_contracts import decode_detail_open_response
from seektalent.providers.liepin.worker_contracts import decode_login_handoff
from seektalent.providers.liepin.worker_contracts import decode_login_relay_complete_result
from seektalent.providers.liepin.worker_contracts import decode_login_relay_input_result
from seektalent.providers.liepin.worker_contracts import decode_login_relay_snapshot
from seektalent.providers.liepin.worker_contracts import decode_session_status
from seektalent.providers.liepin.worker_contracts import decode_worker_health

if TYPE_CHECKING:
    from seektalent.source_contracts.detail_open_claims import DetailOpenClaimLedger
    from seektalent.core.retrieval.provider_contract import ProviderFirstPageExpansionResult, ProviderSearchContinuation


EventCallback = Callable[[str, dict[str, object]], None]
DecodedWorkerPayload = TypeVar("DecodedWorkerPayload")
LIVE_LIEPIN_WORKER_MODES = frozenset({"external_http", "opencli"})


def is_live_liepin_worker_mode(worker_mode: str) -> bool:
    return worker_mode in LIVE_LIEPIN_WORKER_MODES


@runtime_checkable
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

    async def session_status(
        self,
        *,
        connection_id: str,
        tenant: str | None = None,
        workspace: str | None = None,
        provider_account_hash: str | None = None,
    ) -> SessionStatus: ...

    async def open_details(self, request: LiepinDetailOpenRequest) -> LiepinDetailOpenResponse: ...

    async def login_handoff(
        self,
        *,
        connection_id: str,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        provider_account_hash: str | None = None,
    ) -> LoginHandoff: ...

    async def login_relay_snapshot(self, *, connection_id: str) -> LoginRelaySnapshot: ...

    async def submit_login_relay_input(
        self,
        *,
        connection_id: str,
        action: str,
        x: float | None = None,
        y: float | None = None,
        text: str | None = None,
        key: str | None = None,
    ) -> LoginRelayInputResult: ...

    async def complete_login_relay(self, *, connection_id: str) -> LoginRelayCompleteResult: ...


class _DetailOpenClaimCapableLiepinWorker(Protocol):
    async def handle_first_page_continuation_with_detail_open_claim_ledger(self, *, action: str,
        continuation: ProviderSearchContinuation, detail_open_claim_ledger: DetailOpenClaimLedger,
        logical_round_no: int, query_instance_id: str) -> ProviderFirstPageExpansionResult: ...
    async def search_with_detail_open_claim_ledger(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
        detail_open_claim_ledger: DetailOpenClaimLedger,
        logical_round_no: int,
        query_instance_id: str,
    ) -> SearchResult: ...


def is_detail_open_claim_capable_liepin_worker(
    worker: object,
) -> TypeGuard[_DetailOpenClaimCapableLiepinWorker]:
    from seektalent.providers.liepin.opencli_worker_client import LiepinOpenCliWorkerClient

    return isinstance(worker, LiepinOpenCliWorkerClient)


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

    async def session_status(
        self,
        *,
        connection_id: str,
        tenant: str | None = None,
        workspace: str | None = None,
        provider_account_hash: str | None = None,
    ) -> SessionStatus:
        del tenant, workspace, provider_account_hash
        return SessionStatus(connectionId=connection_id, status="login_required", provider_account_hash=None)

    async def login_handoff(
        self,
        *,
        connection_id: str,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        provider_account_hash: str | None = None,
    ) -> LoginHandoff:
        raise LiepinWorkerModeError("Fake Liepin fixture worker does not provide live login handoff.")

    async def login_relay_snapshot(self, *, connection_id: str) -> LoginRelaySnapshot:
        raise LiepinWorkerModeError("Fake Liepin fixture worker does not provide login relay snapshots.")

    async def submit_login_relay_input(
        self,
        *,
        connection_id: str,
        action: str,
        x: float | None = None,
        y: float | None = None,
        text: str | None = None,
        key: str | None = None,
    ) -> LoginRelayInputResult:
        raise LiepinWorkerModeError("Fake Liepin fixture worker does not accept login relay input.")

    async def complete_login_relay(self, *, connection_id: str) -> LoginRelayCompleteResult:
        raise LiepinWorkerModeError("Fake Liepin fixture worker does not complete live login relay.")


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
            await self._request_json_async("GET", f"{self.base_url}/internal/health"),
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
            await self._request_json_async(
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

    async def login_handoff(
        self,
        *,
        connection_id: str,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        provider_account_hash: str | None = None,
    ) -> LoginHandoff:
        return _decode_worker_response(
            decode_login_handoff,
            await self._request_json_async(
                "POST",
                f"{self.base_url}/internal/session/login-handoff",
                json_body=_login_handoff_body(
                    connection_id=connection_id,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    provider_account_hash=provider_account_hash,
                ),
            )
        )

    async def login_relay_snapshot(self, *, connection_id: str) -> LoginRelaySnapshot:
        return _decode_worker_response(
            decode_login_relay_snapshot,
            await self._request_json_async(
                "GET",
                f"{self.base_url}/internal/session/login-relay/snapshot?{parse.urlencode({'connectionId': connection_id})}",
            ),
        )

    async def submit_login_relay_input(
        self,
        *,
        connection_id: str,
        action: str,
        x: float | None = None,
        y: float | None = None,
        text: str | None = None,
        key: str | None = None,
    ) -> LoginRelayInputResult:
        return _decode_worker_response(
            decode_login_relay_input_result,
            await self._request_json_async(
                "POST",
                f"{self.base_url}/internal/session/login-relay/input",
                json_body=_login_relay_input_body(
                    connection_id=connection_id,
                    action=action,
                    x=x,
                    y=y,
                    text=text,
                    key=key,
                ),
            ),
        )

    async def complete_login_relay(self, *, connection_id: str) -> LoginRelayCompleteResult:
        return _decode_worker_response(
            decode_login_relay_complete_result,
            await self._request_json_async(
                "POST",
                f"{self.base_url}/internal/session/login-relay/complete",
                json_body={"connectionId": connection_id},
            ),
        )

    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult:
        return liepin_card_search_response_to_search_result(
            _decode_worker_response(
                decode_card_search_response,
                await self._request_json_async(
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
            await self._request_json_async(
                "POST",
                f"{self.base_url}/internal/details/open",
                json_body=request.model_dump(mode="json", by_alias=True),
            ),
        )

    async def _request_json_async(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return await asyncio.to_thread(self._request_json, method, url, json_body=json_body)

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
    if settings.liepin_worker_mode == "external_http":
        return ExternalHttpLiepinWorkerClient(settings)
    if settings.liepin_worker_mode == "opencli":
        return build_liepin_opencli_worker_client(settings)
    raise LiepinWorkerModeError(
        "Liepin worker mode is disabled; no worker client can be built.",
        setup_status="disabled",
    )


def build_liepin_opencli_worker_client(settings: AppSettings) -> LiepinWorkerClient:
    from seektalent.opencli_browser.automation import OpenCliBrowserAutomation
    from seektalent.opencli_browser.contracts import OpenCliBrowserConfig
    from seektalent.providers.liepin.opencli_retriever import LiepinOpenCliResumeRetriever
    from seektalent.providers.liepin.opencli_worker_client import LiepinOpenCliWorkerClient
    from seektalent.providers.liepin.liepin_site_adapter import (
        LiepinOpenCliSiteConfig,
        LiepinOpenCliTimingRecorder,
        LiepinSiteAdapter,
    )

    browser_config = OpenCliBrowserConfig(
        command=settings.liepin_opencli_command_argv,
        session=settings.liepin_opencli_session,
        timeout_seconds=settings.liepin_opencli_timeout_seconds,
        window_mode=settings.liepin_opencli_window_mode,
        pacing_enabled=settings.liepin_opencli_pacing_enabled,
        pacing_min_ms=settings.liepin_opencli_pacing_min_ms,
        pacing_max_ms=settings.liepin_opencli_pacing_max_ms,
    )
    site_config = LiepinOpenCliSiteConfig(
        allowed_hosts=settings.liepin_opencli_allowed_hosts,
        allowed_start_urls=settings.liepin_opencli_allowed_start_urls,
        detail_open_timeout_seconds=settings.liepin_opencli_detail_open_timeout_seconds,
        lease_dir=settings.project_root / ".seektalent" / "opencli_leases",
        artifact_root=settings.artifacts_path,
    )

    return LiepinOpenCliWorkerClient(
        retriever=LiepinOpenCliResumeRetriever(
            runner=LiepinSiteAdapter(
                browser_config=browser_config,
                site_config=site_config,
                automation=OpenCliBrowserAutomation(
                    config=browser_config,
                    timing_recorder=LiepinOpenCliTimingRecorder(
                        artifact_root=site_config.artifact_root,
                        writes_local_debug_artifacts=(
                            settings.runtime_artifact_output_mode != "prod"
                        ),
                    ),
                ),
            )
        ),
        connection_id="liepin-opencli",
        provider_account_hash="liepin-opencli-local",
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


def _login_handoff_body(
    *,
    connection_id: str,
    tenant_id: str | None,
    workspace_id: str | None,
    provider_account_hash: str | None,
) -> dict[str, object]:
    body: dict[str, object] = {"connectionId": connection_id}
    if tenant_id is not None:
        body["tenantId"] = tenant_id
    if workspace_id is not None:
        body["workspaceId"] = workspace_id
    if provider_account_hash is not None:
        body["providerAccountHash"] = provider_account_hash
    return body


def _login_relay_input_body(
    *,
    connection_id: str,
    action: str,
    x: float | None,
    y: float | None,
    text: str | None,
    key: str | None,
) -> dict[str, object]:
    body: dict[str, object] = {"connectionId": connection_id, "action": action}
    if x is not None:
        body["x"] = x
    if y is not None:
        body["y"] = y
    if text is not None:
        body["text"] = text
    if key is not None:
        body["key"] = key
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


def liepin_card_search_response_to_search_result(response: LiepinCardSearchResponse) -> SearchResult:
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


def liepin_resume_search_response_to_search_result(response: LiepinResumeSearchResponse) -> SearchResult:
    mapped = [map_liepin_worker_detail(detail) for detail in response.resumes]
    return SearchResult(
        candidates=[item.candidate for item in mapped],
        diagnostics=response.diagnostics,
        exhausted=response.exhausted,
        next_cursor=response.next_cursor,
        request_payload=_safe_search_request_payload(response.request_payload),
        provider_snapshots=[item.provider_snapshot for item in mapped],
        raw_candidate_count=response.raw_candidate_count
        if response.raw_candidate_count is not None
        else len(response.resumes),
        private_continuations=response._private_first_page_continuations,
    )


def _safe_search_request_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "keyword",
        "pageSize",
        "cursor",
        "round",
        "traceId",
        "providerFilters",
        "backend",
        "opencliStatus",
        "safeReasonCode",
        "actionTraceRef",
        "workflowSteps",
    }
    safe_payload: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in allowed_keys:
            continue
        if key == "workflowSteps":
            workflow_steps = _safe_workflow_steps(value)
            if workflow_steps:
                safe_payload[key] = workflow_steps
            continue
        if key == "actionTraceRef":
            artifact_ref = _safe_artifact_ref(value)
            if artifact_ref is not None:
                safe_payload[key] = artifact_ref
            continue
        safe_payload[key] = value
    return safe_payload


_SAFE_WORKFLOW_EVENT_TYPES = {
    "source_workflow_step_started",
    "source_workflow_step_completed",
    "source_workflow_step_failed",
}
_SAFE_WORKFLOW_STEP_NAMES = {
    "prepare_search",
    "apply_filters",
    "submit_search",
    "observe_cards",
    "cache_detail_urls",
    "open_detail",
    "capture_detail",
    "finalize",
}
_SAFE_WORKFLOW_STATUSES = {"running", "completed", "partial", "blocked", "failed", "cancelled"}
_SAFE_WORKFLOW_COUNT_KEYS = {
    "cards_seen",
    "cached_detail_urls",
    "detail_claim_granted_count",
    "detail_open_terminal_failure_count",
    "detail_open_skipped_seen_count",
    "detail_opened_count",
    "details_opened",
    "resumes_returned",
    "target_resumes",
    "visible_cards",
}
_SAFE_WORKFLOW_METADATA_KEYS = {"rank", "open_mode"}


def _safe_workflow_steps(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    steps: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        item = cast(Mapping[str, object], item)
        event_type = item.get("event_type")
        step_name = item.get("step_name")
        status = item.get("status")
        if event_type not in _SAFE_WORKFLOW_EVENT_TYPES or step_name not in _SAFE_WORKFLOW_STEP_NAMES:
            continue
        safe_step: dict[str, object] = {
            "event_type": event_type,
            "step_name": step_name,
            "safe_counts": _safe_workflow_counts(item.get("safe_counts")),
            "safe_metadata": _safe_workflow_metadata(item.get("safe_metadata")),
            "artifact_refs": _safe_artifact_refs(item.get("artifact_refs")),
        }
        if status in _SAFE_WORKFLOW_STATUSES:
            safe_step["status"] = status
        reason_code = _safe_reason_token(item.get("safe_reason_code"))
        if reason_code is not None:
            safe_step["safe_reason_code"] = reason_code
        steps.append(safe_step)
    return steps


def _safe_workflow_counts(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if key in _SAFE_WORKFLOW_COUNT_KEYS and isinstance(item, int) and not isinstance(item, bool) and item >= 0
    }


def _safe_workflow_metadata(value: object) -> dict[str, str | int]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str | int] = {}
    for key, item in value.items():
        if key not in _SAFE_WORKFLOW_METADATA_KEYS or isinstance(item, bool):
            continue
        if isinstance(item, int) and item >= 0:
            result[str(key)] = item
            continue
        if isinstance(item, str):
            clean = item.strip()
            if clean and len(clean) <= 80 and "://" not in clean and _safe_reason_token(clean) is not None:
                result[str(key)] = clean
    return result


def _safe_artifact_refs(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [ref for item in value if (ref := _safe_artifact_ref(item)) is not None]


def _safe_artifact_ref(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    if not clean.startswith("artifact://protected/") or ".." in clean or any(character.isspace() for character in clean):
        return None
    return clean


def _safe_reason_token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    if not clean or len(clean) > 128:
        return None
    return clean if all(part.replace("_", "").isalnum() for part in clean.split("_")) else None


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
        "login_relay_not_configured": "Liepin worker login relay is not configured.",
        "login_not_verified": "Liepin login has not been verified.",
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
