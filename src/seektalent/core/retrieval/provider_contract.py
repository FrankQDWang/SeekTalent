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
ProviderPayloadKind = Literal["card", "detail"]
ProviderContinuationKind = Literal["first_page_detail_expansion"]


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
    keyword_query: str
    adapter_notes: list[str]
    runtime_constraints: list[RuntimeConstraint]
    fetch_mode: FetchMode
    page_size: int
    provider_filters: dict[str, ConstraintValue] = field(default_factory=dict)
    provider_context: dict[str, str] = field(default_factory=dict)
    cursor: str | None = None


@dataclass(frozen=True)
class ProviderSnapshot:
    provider_name: str
    payload_kind: ProviderPayloadKind
    raw_payload: dict[str, Any]
    normalized_text: str
    provider_subject_id: str | None
    provider_listing_id: str | None
    synthetic_candidate_fingerprint: str
    identity_confidence: str
    extraction_source: str
    extractor_version: str
    pii_classification: str
    retention_policy: str
    access_scope: str
    redaction_state: str
    score_evidence_source: str

    def privacy_metadata(self) -> dict[str, str]:
        return {
            "pii_classification": self.pii_classification,
            "retention_policy": self.retention_policy,
            "access_scope": self.access_scope,
            "redaction_state": self.redaction_state,
        }


@dataclass(frozen=True, kw_only=True)
class ProviderSearchContinuation:
    kind: ProviderContinuationKind
    continuation_id: str
    opaque_ref: str
    source_kind: str
    round_no: int
    query_instance_id: str
    visible_candidate_count: int
    eligible_candidate_count: int
    initial_opened_count: int


@dataclass(frozen=True)
class SearchResult:
    candidates: list[ResumeCandidate] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    exhausted: bool = False
    next_cursor: str | None = None
    request_payload: dict[str, Any] = field(default_factory=dict)
    provider_snapshots: list[ProviderSnapshot] = field(default_factory=list)
    raw_candidate_count: int = 0
    latency_ms: int | None = None
    private_continuations: tuple[ProviderSearchContinuation, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ProviderFirstPageExpansionResult:
    search_result: SearchResult
    first_page_visible_count: int
    first_page_eligible_count: int
    initial_opened_count: int
    expansion_opened_count: int
    expansion_skipped_seen_count: int
    expansion_terminal_failure_count: int
    status: Literal["completed", "partial", "blocked", "failed"]
    safe_reason_code: str | None = None
    continuation_deleted: bool = False


class ProviderSearchError(RuntimeError):
    def __init__(self, *, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.safe_message = message


class ProviderAdapter(Protocol):
    name: str

    def describe_capabilities(self) -> ProviderCapabilities: ...

    async def search(self, request: SearchRequest, *, round_no: int, trace_id: str) -> SearchResult: ...
