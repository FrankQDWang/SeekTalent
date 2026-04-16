from __future__ import annotations

from time import perf_counter
from typing import Any

import httpx
from pydantic import BaseModel, Field

from seektalent.clients.cts_client import BaseCTSClient
from seektalent.clients.cts_models import CandidateSearchResponse
from seektalent.config import AppSettings
from seektalent.evaluation import TOP_K
from seektalent.models import ResumeCandidate


class JDTextSearchResult(BaseModel):
    request_payload: dict[str, Any]
    response_body: dict[str, Any]
    candidates: list[ResumeCandidate]
    total: int | None = None
    raw_candidate_count: int = 0
    adapter_notes: list[str] = Field(default_factory=list)
    latency_ms: int | None = None
    response_message: str | None = None


class JDTextCTSClient(BaseCTSClient):
    def __init__(self, settings: AppSettings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(settings)
        self.transport = transport

    async def search_by_jd(self, *, jd: str, trace_id: str) -> JDTextSearchResult:
        self.settings.require_cts_credentials()
        payload: dict[str, Any] = {"jd": jd, "page": 1, "pageSize": TOP_K}
        headers = {
            "trace_id": trace_id,
            "tenant_key": self.settings.cts_tenant_key or "",
            "tenant_secret": self.settings.cts_tenant_secret or "",
        }
        started = perf_counter()
        async with httpx.AsyncClient(
            base_url=self.settings.cts_base_url,
            timeout=self.settings.cts_timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.post("/thirdCooperate/search/candidate/cts", headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()

        parsed = CandidateSearchResponse.model_validate(body)
        if parsed.data is None:
            raise ValueError("CTS JD search returned data:null.")
        candidates = [self._normalize_candidate(item, round_no=1) for item in parsed.data.candidates]
        return JDTextSearchResult(
            request_payload=payload,
            response_body=body,
            candidates=candidates,
            total=parsed.data.total,
            raw_candidate_count=len(candidates),
            adapter_notes=["CTS request used only jd/page/pageSize."],
            latency_ms=int((perf_counter() - started) * 1000),
            response_message=parsed.message,
        )
