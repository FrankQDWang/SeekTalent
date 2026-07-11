from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from seektalent.core.retrieval.provider_contract import ProviderSearchContinuation
from seektalent.models import ResumeCandidate
from seektalent.source_contracts import RuntimeQueryCandidateAttribution, RuntimeSourceLaneResult

ExpansionStatus = Literal["completed", "partial", "blocked", "failed"]
ExpansionAction = Literal["expand", "discard"]


class SourceFirstPageExpansionError(RuntimeError):
    def __init__(self, message: str, *, status: ExpansionStatus, safe_reason_code: str,
                 continuation_deleted: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.safe_reason_code = safe_reason_code
        self.continuation_deleted = continuation_deleted


@dataclass(frozen=True, kw_only=True)
class SourceFirstPageExpansionRequest:
    runtime_run_id: str
    round_no: int
    source_kind: str
    query_instance_id: str
    continuation_id: str
    continuation: ProviderSearchContinuation
    action: ExpansionAction


@dataclass(frozen=True, kw_only=True)
class SourceFirstPageExpansionResult:
    source_kind: str
    query_instance_id: str
    continuation_id: str
    status: ExpansionStatus
    candidates: tuple[ResumeCandidate, ...] = ()
    candidate_query_attributions: tuple[RuntimeQueryCandidateAttribution, ...] = ()
    lane_result: RuntimeSourceLaneResult | None = None
    first_page_visible_count: int = 0
    first_page_eligible_count: int = 0
    initial_opened_count: int = 0
    expansion_opened_count: int = 0
    expansion_skipped_seen_count: int = 0
    expansion_terminal_failure_count: int = 0
    safe_reason_code: str | None = None
    continuation_deleted: bool = False


SourceFirstPageExpander = Callable[[SourceFirstPageExpansionRequest], Awaitable[SourceFirstPageExpansionResult]]
