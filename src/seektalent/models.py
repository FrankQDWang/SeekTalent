from __future__ import annotations

import json
from hashlib import sha1
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ConstraintValue = str | int | list[str]
RoutingMode = Literal["explicit_domain", "inferred_domain", "generic_fallback"]
ConfidenceLevel = Literal["high", "medium", "low"]
GroundingEvidenceType = Literal[
    "title_alias",
    "query_term",
    "must_have_link",
    "preferred_link",
    "generic_requirement",
]


def stable_deduplicate(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        clean = " ".join(value.split()).strip()
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(clean)
    return output


def stable_fallback_resume_id(payload: dict[str, Any]) -> str:
    digest = sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"fallback-{digest}"


class SearchInputTruth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_description: str
    hiring_notes: str
    job_description_sha256: str
    hiring_notes_sha256: str


class RequirementPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_domains: list[str] = Field(default_factory=list)
    preferred_backgrounds: list[str] = Field(default_factory=list)


class HardConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locations: list[str] = Field(default_factory=list)
    min_years: int | None = None
    max_years: int | None = None
    company_names: list[str] = Field(default_factory=list)
    school_names: list[str] = Field(default_factory=list)
    degree_requirement: str | None = None
    school_type_requirement: list[str] = Field(default_factory=list)
    gender_requirement: str | None = None
    min_age: int | None = None
    max_age: int | None = None


class RequirementExtractionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_title_candidate: str = ""
    role_summary_candidate: str = ""
    must_have_capability_candidates: list[str] = Field(default_factory=list)
    preferred_capability_candidates: list[str] = Field(default_factory=list)
    exclusion_signal_candidates: list[str] = Field(default_factory=list)
    preference_candidates: RequirementPreferences = Field(default_factory=RequirementPreferences)
    hard_constraint_candidates: HardConstraints = Field(default_factory=HardConstraints)
    scoring_rationale_candidate: str = ""


class RequirementSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_title: str
    role_summary: str
    must_have_capabilities: list[str] = Field(default_factory=list)
    preferred_capabilities: list[str] = Field(default_factory=list)
    exclusion_signals: list[str] = Field(default_factory=list)
    hard_constraints: HardConstraints = Field(default_factory=HardConstraints)
    preferences: RequirementPreferences = Field(default_factory=RequirementPreferences)
    scoring_rationale: str


class FitGateConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locations: list[str] = Field(default_factory=list)
    min_years: int | None = None
    max_years: int | None = None
    company_names: list[str] = Field(default_factory=list)
    school_names: list[str] = Field(default_factory=list)
    degree_requirement: str | None = None
    gender_requirement: str | None = None
    min_age: int | None = None
    max_age: int | None = None


class FusionWeightPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rerank: float | None = None
    must_have: float | None = None
    preferred: float | None = None
    risk_penalty: float | None = None


class FusionWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rerank: float = Field(ge=0.0)
    must_have: float = Field(ge=0.0)
    preferred: float = Field(ge=0.0)
    risk_penalty: float = Field(ge=0.0)


class StabilityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["soft_penalty"] = "soft_penalty"
    penalty_weight: float | None = Field(default=None, ge=0.0)
    confidence_floor: float | None = Field(default=None, ge=0.0, le=1.0)
    allow_hard_gate: bool = False


class PenaltyWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_hop: float = Field(ge=0.0)
    job_hop_confidence_floor: float = Field(ge=0.0, le=1.0)


class ExplanationPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_n_for_explanation: int | None = Field(default=None, ge=1)
    emphasize_business_delivery: bool = False


class BusinessPolicyPack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_pack_ids: list[str] = Field(default_factory=list)
    fusion_weight_preferences: FusionWeightPreferences = Field(default_factory=FusionWeightPreferences)
    fit_gate_overrides: FitGateConstraints = Field(default_factory=FitGateConstraints)
    stability_policy: StabilityPolicy = Field(default_factory=StabilityPolicy)
    explanation_preferences: ExplanationPreferences = Field(default_factory=ExplanationPreferences)


class RerankerCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str
    normalization: str
    temperature: float
    offset: float
    clip_min: float
    clip_max: float
    calibration_version: str


class ScoringPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fit_gate_constraints: FitGateConstraints
    must_have_capabilities_snapshot: list[str] = Field(default_factory=list)
    preferred_capabilities_snapshot: list[str] = Field(default_factory=list)
    fusion_weights: FusionWeights
    penalty_weights: PenaltyWeights
    top_n_for_explanation: int = Field(ge=1)
    rerank_instruction: str
    rerank_query_text: str
    reranker_calibration_snapshot: RerankerCalibration
    ranking_audit_notes: str


class GroundingKnowledgeCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_id: str
    domain_id: str
    report_type: str
    card_type: str
    title: str
    summary: str
    canonical_terms: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    positive_signals: list[str] = Field(default_factory=list)
    negative_signals: list[str] = Field(default_factory=list)
    query_terms: list[str] = Field(default_factory=list)
    must_have_links: list[str] = Field(default_factory=list)
    preferred_links: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel
    source_report_ids: list[str] = Field(default_factory=list)
    source_model_votes: int = Field(ge=0)
    freshness_date: str


class GroundingKnowledgeBaseSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    domain_pack_ids: list[str] = Field(default_factory=list)
    compiled_report_ids: list[str] = Field(default_factory=list)
    card_ids: list[str] = Field(default_factory=list)
    compiled_at: str


class KnowledgeRetrievalBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_cards: int = Field(default=8, ge=1)
    max_inferred_domain_packs: int = Field(default=2, ge=1)


class GroundingEvidenceCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_card_id: str
    label: str
    rationale: str
    evidence_type: GroundingEvidenceType
    confidence: ConfidenceLevel


class FrontierSeedSpecification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator_name: str
    seed_terms: list[str] = Field(default_factory=list)
    seed_rationale: str
    source_card_ids: list[str] = Field(default_factory=list)
    expected_coverage: list[str] = Field(default_factory=list)
    negative_terms: list[str] = Field(default_factory=list)
    target_location: str | None = None


class GroundingDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grounding_evidence_cards: list[GroundingEvidenceCard] = Field(default_factory=list)
    frontier_seed_specifications: list[FrontierSeedSpecification] = Field(default_factory=list)


class KnowledgeRetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_snapshot_id: str
    routing_mode: RoutingMode
    selected_domain_pack_ids: list[str] = Field(default_factory=list)
    routing_confidence: float = Field(ge=0.0, le=1.0)
    fallback_reason: str | None = None
    retrieved_cards: list[GroundingKnowledgeCard] = Field(default_factory=list)
    negative_signal_terms: list[str] = Field(default_factory=list)


class GroundingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grounding_evidence_cards: list[GroundingEvidenceCard] = Field(default_factory=list)
    frontier_seed_specifications: list[FrontierSeedSpecification] = Field(default_factory=list)


class OperatorStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    average_reward: float
    times_selected: int = Field(ge=0)


class FrontierNode_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontier_node_id: str
    parent_frontier_node_id: str | None = None
    donor_frontier_node_id: str | None = None
    selected_operator_name: str
    node_query_term_pool: list[str] = Field(default_factory=list)
    source_card_ids: list[str] = Field(default_factory=list)
    seed_rationale: str | None = None
    negative_terms: list[str] = Field(default_factory=list)
    parent_shortlist_candidate_ids: list[str] = Field(default_factory=list)
    node_shortlist_candidate_ids: list[str] = Field(default_factory=list)
    node_shortlist_score_snapshot: dict[str, float] = Field(default_factory=dict)
    previous_branch_evaluation: dict[str, Any] | None = None
    reward_breakdown: dict[str, Any] | None = None
    status: str


class FrontierState_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontier_nodes: dict[str, FrontierNode_t] = Field(default_factory=dict)
    open_frontier_node_ids: list[str] = Field(default_factory=list)
    closed_frontier_node_ids: list[str] = Field(default_factory=list)
    run_term_catalog: list[str] = Field(default_factory=list)
    run_shortlist_candidate_ids: list[str] = Field(default_factory=list)
    semantic_hashes_seen: list[str] = Field(default_factory=list)
    operator_statistics: dict[str, OperatorStatistics] = Field(default_factory=dict)
    remaining_budget: int = Field(ge=0)


class FrontierState_t1(FrontierState_t):
    pass


class SearchControllerDecision_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["search_cts", "stop"]
    target_frontier_node_id: str
    selected_operator_name: str
    operator_args: dict[str, Any] = Field(default_factory=dict)
    expected_gain_hypothesis: str


class RuntimeSearchBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_round_budget: int = Field(default=5, ge=0)
    default_target_new_candidate_count: int = Field(default=10, ge=1)
    max_target_new_candidate_count: int = Field(default=20, ge=1)


class RuntimeTermBudgetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    high_budget_range: tuple[int, int] = (2, 6)
    medium_budget_range: tuple[int, int] = (2, 5)
    low_budget_range: tuple[int, int] = (2, 4)


class CrossoverGuardThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_shared_anchor_terms: int = Field(default=1, ge=0)
    min_reward_score: float = 1.5
    max_donor_candidates: int = Field(default=2, ge=1)


class RuntimeOnlyConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    must_have_keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)


class ChildFrontierNodeStub(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontier_node_id: str
    parent_frontier_node_id: str
    donor_frontier_node_id: str | None = None
    selected_operator_name: str


class SearchExecutionPlan_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_terms: list[str] = Field(default_factory=list)
    projected_filters: HardConstraints = Field(default_factory=HardConstraints)
    runtime_only_constraints: RuntimeOnlyConstraints = Field(default_factory=RuntimeOnlyConstraints)
    target_new_candidate_count: int
    semantic_hash: str
    source_card_ids: list[str] = Field(default_factory=list)
    child_frontier_node_stub: ChildFrontierNodeStub
    derived_position: str | None = None
    derived_work_content: str | None = None


class RetrievedCandidate_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    age: int | None = None
    gender: str | None = None
    now_location: str | None = None
    expected_location: str | None = None
    years_of_experience_raw: int | None = None
    education_summaries: list[str] = Field(default_factory=list)
    work_experience_summaries: list[str] = Field(default_factory=list)
    project_names: list[str] = Field(default_factory=list)
    work_summaries: list[str] = Field(default_factory=list)
    search_text: str
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class CareerStabilityProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_count_last_5y: int
    short_tenure_count: int
    median_tenure_months: int
    current_tenure_months: int
    parsed_experience_count: int
    confidence_score: float = Field(ge=0.0, le=1.0)

    @classmethod
    def low_confidence(cls, experience_count: int) -> "CareerStabilityProfile":
        return cls(
            job_count_last_5y=min(experience_count, 5),
            short_tenure_count=0,
            median_tenure_months=0,
            current_tenure_months=0,
            parsed_experience_count=0,
            confidence_score=0.0 if experience_count == 0 else 0.2,
        )


class ScoringCandidate_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    scoring_text: str
    capability_signals: list[str] = Field(default_factory=list)
    years_of_experience: int | None = None
    age: int | None = None
    gender: str | None = None
    location_signals: list[str] = Field(default_factory=list)
    work_experience_summaries: list[str] = Field(default_factory=list)
    education_summaries: list[str] = Field(default_factory=list)
    career_stability_profile: CareerStabilityProfile


class SearchPageStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pages_fetched: int
    duplicate_rate: float = Field(ge=0.0, le=1.0)
    latency_ms: int


class SearchObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unique_candidate_ids: list[str] = Field(default_factory=list)
    shortage_after_last_page: bool


class SearchExecutionResult_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_candidates: list[RetrievedCandidate_t] = Field(default_factory=list)
    deduplicated_candidates: list[RetrievedCandidate_t] = Field(default_factory=list)
    scoring_candidates: list[ScoringCandidate_t] = Field(default_factory=list)
    search_page_statistics: SearchPageStatistics
    search_observation: SearchObservation


class ScoredCandidate_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    fit: int
    rerank_raw: float
    rerank_normalized: float = Field(ge=0.0, le=1.0)
    must_have_match_score_raw: int = Field(ge=0, le=100)
    must_have_match_score: float = Field(ge=0.0, le=1.0)
    preferred_match_score_raw: int = Field(ge=0, le=100)
    preferred_match_score: float = Field(ge=0.0, le=1.0)
    risk_score_raw: int = Field(ge=0, le=100)
    risk_score: float = Field(ge=0.0, le=1.0)
    risk_flags: list[str] = Field(default_factory=list)
    fusion_score: float


class TopThreeStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    average_fusion_score_top_three: float


class SearchScoringResult_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scored_candidates: list[ScoredCandidate_t] = Field(default_factory=list)
    node_shortlist_candidate_ids: list[str] = Field(default_factory=list)
    explanation_candidate_ids: list[str] = Field(default_factory=list)
    top_three_statistics: TopThreeStatistics


class SearchRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_shortlist_candidate_ids: list[str] = Field(default_factory=list)
    run_summary: str
    stop_reason: str
