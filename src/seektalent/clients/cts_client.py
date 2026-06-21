from __future__ import annotations

from time import perf_counter

import httpx

import seektalent.clients.cts_contracts as _cts_contracts
from seektalent.clients.cts_request import build_cts_request_payload
from seektalent.clients.cts_response import CTSResponseError, normalize_cts_response_candidates
from seektalent.clients.cts_response import parse_cts_search_response_body
from seektalent.config import AppSettings
from seektalent.models import CTSQuery


class CTSClient:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    async def search(self, query: CTSQuery, *, round_no: int, trace_id: str) -> _cts_contracts.CTSFetchResult:
        self.settings.require_cts_credentials()
        payload, notes = build_cts_request_payload(query)
        headers = {
            "trace_id": trace_id,
            "tenant_key": self.settings.cts_tenant_key or "",
            "tenant_secret": self.settings.cts_tenant_secret or "",
        }
        start = perf_counter()
        async with httpx.AsyncClient(
            base_url=self.settings.cts_base_url,
            timeout=self.settings.cts_timeout_seconds,
        ) as client:
            response = await client.post("/thirdCooperate/search/candidate/cts", headers=headers, json=payload)
            response.raise_for_status()
            try:
                body = response.json()
            except ValueError as exc:
                raise CTSResponseError(
                    reason_code="cts_response_json_invalid",
                    message="CTS search response body was not valid JSON.",
                ) from exc
        parsed = parse_cts_search_response_body(body)
        candidates = normalize_cts_response_candidates(parsed, round_no=round_no)
        return _cts_contracts.CTSFetchResult(
            request_payload=payload,
            candidates=candidates,
            raw_candidate_count=len(candidates),
            adapter_notes=notes,
            latency_ms=int((perf_counter() - start) * 1000),
            response_message=parsed.message,
        )


__all__ = ["CTSClient"]
