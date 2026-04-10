from __future__ import annotations

import json
from hashlib import sha1
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ConstraintValue = str | int | list[str]
RoutingMode = Literal[
    "explicit_pack",
    "inferred_single_pack",
    "inferred_multi_pack",
    "generic_fallback",
]
SeedIntentType = Literal[
    "core_precision",
    "must_have_alias",
    "relaxed_floor",
    "pack_expansion",
    "cross_pack_bridge",
    "generic_expansion",
]
Round0OperatorName = SeedIntentType
OperatorName = Literal[
    "core_precision",
    "must_have_alias",
    "relaxed_floor",
    "pack_expansion",
    "cross_pack_bridge",
    "generic_expansion",
    "crossover_compose",
]
SearchControllerAction = Literal["search_cts", "stop"]
SearchPhase = Literal["explore", "balance", "harvest"]


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


class PromptSurfaceSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    body_text: str
    source_paths: list[str] = Field(default_factory=list)
    is_dynamic: bool = False


class PromptSurfaceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface_id: str
    instructions_text: str
    input_text: str
    instructions_sha1: str
    input_sha1: str
    sections: list[PromptSurfaceSection] = Field(default_factory=list)


class LLMCallAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_mode: str
    retries: int = Field(ge=0)
    output_retries: int = Field(ge=0)
    validator_retry_count: int = Field(ge=0)
    model_name: str
    model_settings_snapshot: dict[str, Any] = Field(default_factory=dict)
    prompt_surface: PromptSurfaceSnapshot


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

    knowledge_pack_id_override: str | None = None
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


class DomainKnowledgePack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_pack_id: str
    label: str
    routing_text: str
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)


class FrontierSeedSpecification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator_name: Round0OperatorName
    seed_terms: list[str] = Field(default_factory=list)
    seed_rationale: str
    knowledge_pack_ids: list[str] = Field(default_factory=list)
    expected_coverage: list[str] = Field(default_factory=list)
    negative_terms: list[str] = Field(default_factory=list)
    target_location: str | None = None


class SeedIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_type: SeedIntentType
    keywords: list[str] = Field(default_factory=list)
    source_knowledge_pack_ids: list[str] = Field(default_factory=list)
    reasoning: str


class BootstrapKeywordDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_seeds: list[SeedIntent] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)


class BootstrapRoutingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routing_mode: RoutingMode
    selected_knowledge_pack_ids: list[str] = Field(default_factory=list)
    routing_confidence: float = Field(ge=0.0, le=1.0)
    fallback_reason: str | None = None
    pack_scores: dict[str, float] = Field(default_factory=dict)


class BootstrapOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontier_seed_specifications: list[FrontierSeedSpecification] = Field(default_factory=list)


class OperatorStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    average_reward: float
    times_selected: int = Field(ge=0)


class BranchEvaluation_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novelty_score: float = Field(ge=0.0, le=1.0)
    usefulness_score: float = Field(ge=0.0, le=1.0)
    branch_exhausted: bool
    repair_operator_hint: OperatorName | None = None
    evaluation_notes: str


class BranchEvaluationDraft_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novelty_score: float
    usefulness_score: float
    branch_exhausted: bool
    repair_operator_hint: str | None = None
    evaluation_notes: str


class NodeRewardBreakdown_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delta_top_three: float
    must_have_gain: float = Field(ge=0.0)
    new_fit_yield: float = Field(ge=0.0)
    novelty: float = Field(ge=0.0, le=1.0)
    usefulness: float = Field(ge=0.0, le=1.0)
    diversity: float = Field(ge=0.0, le=1.0)
    stability_risk_penalty: float = Field(ge=0.0, le=1.0)
    hard_constraint_violation: float = Field(ge=0.0, le=1.0)
    duplicate_penalty: float = Field(ge=0.0, le=1.0)
    cost_penalty: float = Field(ge=0.0, le=1.0)
    reward_score: float


class FrontierNode_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontier_node_id: str
    parent_frontier_node_id: str | None = None
    donor_frontier_node_id: str | None = None
    selected_operator_name: OperatorName
    node_query_term_pool: list[str] = Field(default_factory=list)
    knowledge_pack_ids: list[str] = Field(default_factory=list)
    seed_rationale: str | None = None
    negative_terms: list[str] = Field(default_factory=list)
    parent_shortlist_candidate_ids: list[str] = Field(default_factory=list)
    node_shortlist_candidate_ids: list[str] = Field(default_factory=list)
    node_shortlist_score_snapshot: dict[str, float] = Field(default_factory=dict)
    previous_branch_evaluation: BranchEvaluation_t | None = None
    reward_breakdown: NodeRewardBreakdown_t | None = None
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


class ActiveFrontierNodeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontier_node_id: str
    selected_operator_name: OperatorName
    node_query_term_pool: list[str] = Field(default_factory=list)
    node_shortlist_candidate_ids: list[str] = Field(default_factory=list)


class DonorCandidateNodeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontier_node_id: str
    shared_anchor_terms: list[str] = Field(default_factory=list)
    expected_incremental_coverage: list[str] = Field(default_factory=list)
    reward_score: float


class FrontierHeadSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_node_count: int = Field(ge=0)
    remaining_budget: int = Field(ge=0)
    highest_selection_score: float


class FrontierSelectionBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_phase: SearchPhase
    operator_exploitation_score: float = Field(ge=0.0)
    operator_exploration_bonus: float = Field(ge=0.0)
    coverage_opportunity_score: float = Field(ge=0.0)
    incremental_value_score: float = Field(ge=0.0)
    fresh_node_bonus: float = Field(ge=0.0)
    redundancy_penalty: float = Field(ge=0.0)
    final_selection_score: float


class FrontierSelectionCandidateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontier_node_id: str
    selected_operator_name: OperatorName
    breakdown: FrontierSelectionBreakdown


class RuntimeBudgetState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_round_budget: int = Field(ge=0)
    runtime_round_index: int = Field(ge=0)
    remaining_budget: int = Field(ge=0)
    used_ratio: float = Field(ge=0.0, le=1.0)
    remaining_ratio: float = Field(ge=0.0, le=1.0)
    phase_progress: float = Field(ge=0.0, le=1.0)
    search_phase: SearchPhase
    near_budget_end: bool


class UnmetRequirementWeight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str
    weight: float = Field(ge=0.0)


class SearchControllerContext_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_title: str
    role_summary: str
    active_frontier_node_summary: ActiveFrontierNodeSummary
    donor_candidate_node_summaries: list[DonorCandidateNodeSummary] = Field(default_factory=list)
    frontier_head_summary: FrontierHeadSummary
    active_selection_breakdown: FrontierSelectionBreakdown
    selection_ranking: list[FrontierSelectionCandidateSummary] = Field(default_factory=list)
    unmet_requirement_weights: list[UnmetRequirementWeight] = Field(default_factory=list)
    operator_statistics_summary: dict[str, OperatorStatistics] = Field(default_factory=dict)
    allowed_operator_names: list[OperatorName] = Field(default_factory=list)
    term_budget_range: tuple[int, int]
    fit_gate_constraints: FitGateConstraints
    runtime_budget_state: RuntimeBudgetState


class SearchControllerDecisionDraft_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    selected_operator_name: str
    operator_args: dict[str, Any] = Field(default_factory=dict)
    expected_gain_hypothesis: str


class SearchControllerDecision_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: SearchControllerAction
    target_frontier_node_id: str
    selected_operator_name: OperatorName
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


class RuntimeRoundState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_round_index: int = Field(default=0, ge=0)


class CrossoverGuardThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_shared_anchor_terms: int = Field(default=1, ge=0)
    min_reward_score: float = 1.5
    max_donor_candidates: int = Field(default=2, ge=1)


class StopGuardThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novelty_floor: float = Field(default=0.25, ge=0.0, le=1.0)
    usefulness_floor: float = Field(default=0.25, ge=0.0, le=1.0)
    reward_floor: float = 1.5
    min_round_index: int = Field(default=2, ge=0)


class RuntimeOnlyConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    must_have_keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)


class ChildFrontierNodeStub(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontier_node_id: str
    parent_frontier_node_id: str
    donor_frontier_node_id: str | None = None
    selected_operator_name: OperatorName


class SearchExecutionPlan_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_terms: list[str] = Field(default_factory=list)
    projected_filters: HardConstraints = Field(default_factory=HardConstraints)
    runtime_only_constraints: RuntimeOnlyConstraints = Field(default_factory=RuntimeOnlyConstraints)
    target_new_candidate_count: int
    semantic_hash: str
    knowledge_pack_ids: list[str] = Field(default_factory=list)
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


class SearchRunSummaryDraft_t(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_summary: str


class RuntimeActiveManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["phase6_offline_artifacts_active"]
    knowledge_pack_ids: list[str] = Field(default_factory=list)
    policy_id: str
    calibration_id: str


class BusinessPolicySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str
    policy_pack: BusinessPolicyPack


class SearchRunBootstrapArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_truth: SearchInputTruth
    requirement_extraction_audit: LLMCallAudit
    requirement_sheet: RequirementSheet
    business_policy_snapshot: BusinessPolicySnapshot
    runtime_search_budget: RuntimeSearchBudget
    routing_result: BootstrapRoutingResult
    scoring_policy: ScoringPolicy
    bootstrap_keyword_generation_audit: LLMCallAudit
    bootstrap_output: BootstrapOutput
    frontier_state: FrontierState_t


class SearchRoundArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_round_index: int = Field(ge=0)
    frontier_state_before: FrontierState_t
    controller_context: SearchControllerContext_t
    controller_draft: SearchControllerDecisionDraft_t
    controller_audit: LLMCallAudit
    controller_decision: SearchControllerDecision_t
    execution_plan: SearchExecutionPlan_t | None = None
    execution_result: SearchExecutionResult_t | None = None
    runtime_audit_tags: dict[str, list[str]] = Field(default_factory=dict)
    scoring_result: SearchScoringResult_t | None = None
    branch_evaluation_draft: BranchEvaluationDraft_t | None = None
    branch_evaluation_audit: LLMCallAudit | None = None
    branch_evaluation: BranchEvaluation_t | None = None
    reward_breakdown: NodeRewardBreakdown_t | None = None
    frontier_state_after: FrontierState_t1
    stop_reason: str | None = None
    continue_flag: bool


class SearchRunEvalMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: bool | int | float | str | list[str]


class SearchRunEval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    run_id: str
    metrics: list[SearchRunEvalMetric] = Field(default_factory=list)


class SearchRunBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["phase6_offline_artifacts_active"]
    run_id: str
    run_dir: str
    created_at_utc: str
    bootstrap: SearchRunBootstrapArtifact
    rounds: list[SearchRoundArtifact] = Field(default_factory=list)
    finalization_audit: LLMCallAudit
    final_result: SearchRunResult
    eval: SearchRunEval | None = None
