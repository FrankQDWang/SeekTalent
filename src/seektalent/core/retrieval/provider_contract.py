from __future__ import annotations

from typing import Any
from dataclasses import dataclass, field
from typing import Literal, Protocol

from seektalent.models import ConstraintValue
from seektalent.models import ResumeCandidate
from seektalent.models import RuntimeConstraint


PagingMode = Literal["cursor"]
FetchMode = Literal["summary", "detail"]
QueryRole = Literal["primary", "expansion"]


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_structured_filters: bool
    supports_detail_fetch: bool
    supports_fetch_mode_summary: bool
    supports_fetch_mode_detail: bool
    paging_mode: PagingMode
    recommended_max_concurrency: int
    has_stable_external_id: bool
    has_stable_dedup_key: bool


@dataclass(frozen=True)
class SearchRequest:
    query_terms: list[str]
    query_role: QueryRole
    runtime_constraints: list[RuntimeConstraint]
    fetch_mode: FetchMode
    page_size: int
    provider_filters: dict[str, ConstraintValue] = field(default_factory=dict)
    cursor: str | None = None


@dataclass(frozen=True)
class SearchResult:
    candidates: list[ResumeCandidate] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    exhausted: bool = False
    next_cursor: str | None = None
    request_payload: dict[str, Any] = field(default_factory=dict)
    raw_candidate_count: int = 0
    latency_ms: int | None = None


class ProviderAdapter(Protocol):
    name: str

    def describe_capabilities(self) -> ProviderCapabilities: ...

    async def search(self, request: SearchRequest, *, round_no: int, trace_id: str) -> SearchResult: ...
