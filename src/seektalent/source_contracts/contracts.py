from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
from typing import TYPE_CHECKING, Literal, Protocol

from seektalent.models import NormalizedResume, RequirementSheet, ResumeCandidate, RuntimeSourceEvidence
from seektalent.progress import ProgressCallback
if TYPE_CHECKING:
    from seektalent.core.retrieval.provider_contract import ProviderSearchContinuation


SourceId = str
SourceLaneMode = Literal["card", "detail"]
SourceLaneStatus = Literal["running", "completed", "blocked", "partial", "failed", "cancelled"]


@dataclass(frozen=True, kw_only=True)
class SourceCapabilities:
    supports_card_search: bool
    supports_detail_fetch: bool
    supports_native_filters: bool
    supports_incremental_detail: bool
    requires_human_login: bool
    max_safe_concurrency: int
    stable_external_id: bool
    stable_dedup_key: bool

    def __post_init__(self) -> None:
        if self.max_safe_concurrency < 1:
            raise ValueError("source_capabilities_invalid_concurrency")


@dataclass(frozen=True, kw_only=True)
class SourceBudget:
    card_target: int
    detail_target: int
    scan_limit: int

    def __post_init__(self) -> None:
        if self.card_target < 0:
            raise ValueError("source_budget_negative_card_target")
        if self.detail_target < 0:
            raise ValueError("source_budget_negative_detail_target")
        if self.scan_limit < 0:
            raise ValueError("source_budget_negative_scan_limit")

    def to_public_payload(self) -> dict[str, int]:
        return {
            "card_target": self.card_target,
            "detail_target": self.detail_target,
            "scan_limit": self.scan_limit,
        }


@dataclass(frozen=True, kw_only=True)
class SourcePlan:
    source_id: SourceId
    source_plan_id: str
    runtime_run_id: str
    label: str
    budget: SourceBudget
    enabled: bool = True
    lane_mode: SourceLaneMode = "card"
    safe_posture: Mapping[str, str | int | bool | None] = field(default_factory=dict)
    query_intents: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("source_plan_missing_source_id")
        if not self.source_plan_id:
            raise ValueError("source_plan_missing_source_plan_id")
        if not self.runtime_run_id:
            raise ValueError("source_plan_missing_runtime_run_id")


@dataclass(frozen=True, kw_only=True)
class SourceLaneRequest:
    source_id: SourceId
    lane_mode: SourceLaneMode
    runtime_run_id: str
    source_plan_id: str
    source_lane_run_id: str
    job_title: str
    jd: str
    notes: str | None
    requirement_sheet: RequirementSheet
    source_query_terms: tuple[str, ...]
    budget: SourceBudget
    attempt: int = 1
    progress_callback: ProgressCallback | None = None


@dataclass(frozen=True, kw_only=True)
class SourceLaneResult:
    runtime_run_id: str
    source_plan_id: str
    source_lane_run_id: str
    source_id: SourceId
    lane_mode: SourceLaneMode
    attempt: int
    status: SourceLaneStatus
    candidate_store_updates: dict[str, ResumeCandidate] = field(default_factory=dict)
    normalized_store_updates: dict[str, NormalizedResume] = field(default_factory=dict)
    source_evidence_updates: tuple[RuntimeSourceEvidence, ...] = ()
    raw_candidate_count: int | None = None
    private_first_page_continuations: tuple[ProviderSearchContinuation, ...] = ()
    provider_snapshot_refs: tuple[str, ...] = ()
    safe_summary_refs: tuple[str, ...] = ()
    blocked_reason_code: str | None = None
    stop_reason_code: str | None = None
    retryable: bool = False
    safe_error_summary: str | None = None
    error_ref: str | None = None

    @classmethod
    def from_candidates(
        cls,
        *,
        request: SourceLaneRequest,
        status: SourceLaneStatus,
        candidates: Sequence[ResumeCandidate],
        collected_at: str,
        raw_candidate_count: int | None = None,
        safe_reason_code: str | None = None,
    ) -> SourceLaneResult:
        candidate_updates = {candidate.resume_id: candidate for candidate in candidates}
        source_evidence = tuple(
            _source_evidence_from_candidate(
                request=request,
                candidate=candidate,
                collected_at=collected_at,
                safe_reason_code=safe_reason_code,
            )
            for candidate in candidates
        )
        return cls(
            runtime_run_id=request.runtime_run_id,
            source_plan_id=request.source_plan_id,
            source_lane_run_id=request.source_lane_run_id,
            source_id=request.source_id,
            lane_mode=request.lane_mode,
            attempt=request.attempt,
            status=status,
            candidate_store_updates=candidate_updates,
            source_evidence_updates=source_evidence,
            raw_candidate_count=raw_candidate_count,
            stop_reason_code=safe_reason_code if status == "completed" else None,
            blocked_reason_code=safe_reason_code if status == "blocked" else None,
        )


@dataclass(frozen=True)
class UnsupportedSourceFilter:
    source_kind: str
    field: str
    query_instance_id: str | None
    safe_reason_code: str
    detail: str = ""


SourceLaneRunner = Callable[[SourceLaneRequest], Coroutine[object, object, SourceLaneResult]]


class SourcePlanBuilder(Protocol):
    def __call__(
        self,
        *,
        runtime_run_id: str,
        source_index: int,
        budget_overrides: Mapping[str, int] | None,
    ) -> SourcePlan: ...


@dataclass(frozen=True, kw_only=True)
class RegisteredSource:
    source_id: SourceId
    label: str
    capabilities: SourceCapabilities
    default_budget: SourceBudget
    plan: SourcePlanBuilder
    run_card_lane: SourceLaneRunner
    run_detail_lane: SourceLaneRunner | None = None

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("registered_source_missing_source_id")
        if not self.label:
            raise ValueError("registered_source_missing_label")
        if self.capabilities.supports_detail_fetch and self.run_detail_lane is None:
            raise ValueError("registered_source_missing_detail_runner")


def _source_evidence_from_candidate(
    *,
    request: SourceLaneRequest,
    candidate: ResumeCandidate,
    collected_at: str,
    safe_reason_code: str | None,
) -> RuntimeSourceEvidence:
    provider_key = candidate.source_resume_id or candidate.dedup_key or candidate.resume_id
    provider_candidate_key_hash = hashlib.sha256(
        f"{request.source_id}:{provider_key}".encode("utf-8")
    ).hexdigest()
    return RuntimeSourceEvidence(
        evidence_id=f"{request.source_lane_run_id}:{candidate.resume_id}",
        source=request.source_id,
        provider=request.source_id,
        source_plan_id=request.source_plan_id,
        source_lane_run_id=request.source_lane_run_id,
        evidence_level=request.lane_mode,
        candidate_resume_id=candidate.resume_id,
        provider_candidate_key_hash=provider_candidate_key_hash,
        safe_summary_ref=_safe_summary_ref(candidate),
        collected_at=collected_at,
        safe_reason_codes=(safe_reason_code,) if safe_reason_code else (),
    )


def _safe_summary_ref(candidate: ResumeCandidate) -> str | None:
    value = candidate.raw.get("safe_summary_ref") if isinstance(candidate.raw, Mapping) else None
    return value if isinstance(value, str) and value else None
