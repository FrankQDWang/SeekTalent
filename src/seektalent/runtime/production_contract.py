from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from seektalent.models import FinalCandidate

SCHEMA_VERSION = "seektalent.production_match_result.v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PublicRuntimeWarningV1(StrictModel):
    code: str
    message: str
    source: str | None = None
    retryable: bool = False
    operator_action: str | None = None


class PublicRuntimeErrorV1(StrictModel):
    code: str
    message: str
    http_status: int
    cli_exit_code: int
    retryable: bool = False


class PublicArtifactRefV1(StrictModel):
    artifact_id: str
    artifact_uri: str | None = None
    retention_policy: str


class SourceSelectionV1(StrictModel):
    required: tuple[str, ...] = Field(default_factory=tuple)
    optional: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def source_kinds(self) -> tuple[str, ...]:
        output: list[str] = []
        for source in [*self.required, *self.optional]:
            if source not in output:
                output.append(source)
        return tuple(output)


SourceCoverageStatusV1 = Literal[
    "succeeded",
    "empty",
    "partial",
    "failed",
    "blocked",
    "invalid",
    "rate_limited",
    "timeout",
    "canceled",
    "retry_exhausted",
]


class SourceCoverageV1(StrictModel):
    source_kind: str
    status: SourceCoverageStatusV1
    usable_candidate_count: int = 0
    query_outcomes: tuple[str, ...] = Field(default_factory=tuple)
    retryable: bool = False
    operator_action: str | None = None


class SourceCoverageSummaryV1(StrictModel):
    required: tuple[SourceCoverageV1, ...] = Field(default_factory=tuple)
    optional: tuple[SourceCoverageV1, ...] = Field(default_factory=tuple)


class ConstraintDecisionV1(StrictModel):
    rule_id: str
    decision: Literal["passed", "failed", "unknown", "not_applicable"]
    reason_code: str
    provenance: str


class ProductionCandidateV1(StrictModel):
    candidate_id: str
    rank: int
    score: float | None = None
    source_provider: str
    evidence_level: str
    detail_open_status: str
    score_evidence_source: str | None = None
    card_scorecard_ref: str | None = None
    detail_scorecard_ref: str | None = None
    detail_open_reason: str | None = None
    detail_open_policy_version: str | None = None
    constraint_decisions: tuple[ConstraintDecisionV1, ...] = Field(default_factory=tuple)
    public_fit_summary: str | None = None

    @classmethod
    def from_final_candidate(cls, candidate: FinalCandidate) -> "ProductionCandidateV1":
        return cls(
            candidate_id=candidate.resume_id,
            rank=candidate.rank,
            score=float(candidate.final_score),
            source_provider=_required_candidate_text(candidate, "source_provider"),
            evidence_level=_required_candidate_text(candidate, "evidence_level"),
            detail_open_status=_required_candidate_text(candidate, "detail_open_status"),
            score_evidence_source=candidate.score_evidence_source,
            card_scorecard_ref=candidate.card_scorecard_ref,
            detail_scorecard_ref=candidate.detail_scorecard_ref,
            detail_open_reason=candidate.detail_open_reason,
            detail_open_policy_version=candidate.detail_open_policy_version,
            public_fit_summary=candidate.match_summary,
        )


def _required_candidate_text(candidate: FinalCandidate, field_name: str) -> str:
    value = getattr(candidate, field_name)
    if isinstance(value, str) and value:
        return value
    raise ValueError(f"Production candidate {candidate.resume_id!r} is missing {field_name}.")


class PrfSummaryV1(StrictModel):
    selected: bool
    status: Literal["not_selected", "succeeded", "unavailable", "degraded"]
    reason_code: str


class StopReasonV1(StrictModel):
    code: str
    message: str


class CoreCommitReceiptV1(StrictModel):
    commit_id: str
    idempotency_key: str


class ProductionMatchResultV1(StrictModel):
    schema_version: Literal["seektalent.production_match_result.v1"] = SCHEMA_VERSION
    run_id: str
    runtime_profile: Literal["prod_core", "development", "workbench"]
    completion_status: Literal["succeeded", "degraded", "failed"]
    input_digest: str
    approved_requirement_sheet_digest: str | None = None
    requirement_profile_version: str
    policy_version: str
    model_policy_version: str
    source_selection: SourceSelectionV1
    source_coverage: SourceCoverageSummaryV1
    final_candidates: tuple[ProductionCandidateV1, ...]
    stop_reason: StopReasonV1
    rounds_executed: int
    terminal_stop_guidance: str | None = None
    prf_summary: PrfSummaryV1
    warnings: tuple[PublicRuntimeWarningV1, ...] = Field(default_factory=tuple)
    artifact_ref: PublicArtifactRefV1 | None = None
    core_commit: CoreCommitReceiptV1 | None = None
    public_error: PublicRuntimeErrorV1 | None = None

    @classmethod
    def from_debug_result(
        cls,
        debug_result,
        *,
        input_digest: str,
        source_selection: SourceSelectionV1,
        approved_requirement_sheet_digest: str | None = None,
        runtime_profile: Literal["prod_core", "development", "workbench"] = "prod_core",
    ) -> "ProductionMatchResultV1":
        final_result = debug_result.final_result
        return cls(
            run_id=debug_result.run_id,
            runtime_profile=runtime_profile,
            completion_status="succeeded",
            input_digest=input_digest,
            approved_requirement_sheet_digest=approved_requirement_sheet_digest,
            requirement_profile_version="requirements.v1",
            policy_version="production-contract.v1",
            model_policy_version="model-policy.v1",
            source_selection=source_selection,
            source_coverage=SourceCoverageSummaryV1(),
            final_candidates=tuple(
                ProductionCandidateV1.from_final_candidate(candidate)
                for candidate in final_result.candidates
            ),
            stop_reason=StopReasonV1(code=final_result.stop_reason, message=final_result.stop_reason),
            rounds_executed=final_result.rounds_executed,
            terminal_stop_guidance=(
                debug_result.terminal_stop_guidance.reason
                if debug_result.terminal_stop_guidance is not None
                else None
            ),
            prf_summary=_prf_summary_from_debug_result(debug_result),
            artifact_ref=PublicArtifactRefV1(
                artifact_id=debug_result.run_id,
                artifact_uri=None,
                retention_policy="local_runtime_artifact",
            ),
        )


def _prf_summary_from_debug_result(debug_result) -> PrfSummaryV1:
    direct_summary = getattr(debug_result, "prf_summary", None)
    if isinstance(direct_summary, PrfSummaryV1):
        return direct_summary

    run_state = getattr(debug_result, "run_state", None)
    retrieval_state = getattr(run_state, "retrieval_state", None)
    decisions = tuple(getattr(retrieval_state, "second_lane_decision_history", ()) or ())
    if not decisions:
        return PrfSummaryV1(
            selected=False,
            status="unavailable" if run_state is None else "not_selected",
            reason_code="prf_runtime_summary_unavailable" if run_state is None else "prf_not_attempted",
        )

    attempted = tuple(decision for decision in decisions if getattr(decision, "attempted_prf", False))
    if not attempted:
        return PrfSummaryV1(selected=False, status="not_selected", reason_code="prf_not_eligible")

    latest = attempted[-1]
    if latest.prf_gate_passed and latest.selected_lane_type == "prf_probe":
        return PrfSummaryV1(selected=True, status="succeeded", reason_code="prf_probe_selected")

    reason_code = _public_prf_reason_code(latest)
    status: Literal["not_selected", "succeeded", "unavailable", "degraded"] = (
        "unavailable"
        if reason_code
        in {
            "insufficient_prf_seed_support",
            "insufficient_high_quality_seeds",
            "llm_prf_unsupported_capability",
            "prf_policy_not_available",
            "no_generic_explore_query",
        }
        else "degraded"
    )
    return PrfSummaryV1(selected=False, status=status, reason_code=reason_code)


def _public_prf_reason_code(decision) -> str:
    if decision.llm_prf_failure_kind:
        return decision.llm_prf_failure_kind
    if decision.reject_reasons:
        return decision.reject_reasons[0]
    if decision.no_fetch_reason:
        return decision.no_fetch_reason
    if decision.fallback_lane_type:
        return f"fallback_{decision.fallback_lane_type}"
    return "prf_not_selected"


def digest_text_parts(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def digest_model_payload(payload: object | None) -> str | None:
    if payload is None:
        return None
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
