from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from seektalent.models import CTSQuery, ResumeCandidate


class CTSFetchResult(BaseModel):
    request_payload: dict[str, object]
    candidates: list[ResumeCandidate]
    raw_candidate_count: int = 0
    adapter_notes: list[str] = Field(default_factory=list)
    latency_ms: int | None = None
    response_message: str | None = None


class CTSClientProtocol(Protocol):
    async def search(self, query: CTSQuery, *, round_no: int, trace_id: str) -> CTSFetchResult: ...
