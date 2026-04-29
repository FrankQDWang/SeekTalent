from __future__ import annotations

import json
from collections.abc import Iterable
from hashlib import sha1
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FitBucket = Literal["fit", "not_fit"]
DecisionType = Literal["continue", "stop"]
ControllerAction = Literal["search_cts", "stop"]
PoolDecisionType = Literal["selected", "retained", "dropped"]
ConditionSource = Literal["jd", "notes", "inferred"]
ScoringConfidence = Literal["high", "medium", "low"]
ConstraintValue = str | int | list[str]
QueryTermSource = Literal[
    "job_title",
    "jd",
    "notes",
    "reflection",
    "candidate_feedback",
]
QueryTermCategory = Literal["role_anchor", "domain", "tooling", "expansion", "company"]
QueryRetrievalRole = Literal[
    "role_anchor",
    "core_skill",
    "primary_role_anchor",
    "secondary_title_anchor",
    "domain_context",
    "framework_tool",
    "filter_only",
    "score_only",
]
Queryability = Literal["admitted", "score_only", "filter_only", "blocked"]
QueryRole = Literal["exploit", "explore"]
LaneType = Literal["exploit", "generic_explore", "prf_probe"]
TopPoolStrength = Literal["empty", "weak", "usable", "strong"]
StopQualityGateStatus = Literal[
    "pass",
    "continue_low_quality",
    "broaden_required",
    "low_quality_exhausted",
    "budget_stop_allowed",
]
LocationExecutionMode = Literal["none", "single", "priority_then_fallback", "balanced_all"]
LocationExecutionPhase = Literal["priority", "balanced"]
FilterField = Literal[
    "company_names",
    "school_names",
    "degree_requirement",
    "school_type_requirement",
    "experience_requirement",
    "gender_requirement",
    "age_requirement",
    "position",
    "work_content",
]

THOUGHT_SUMMARY_MAX_CHARS = 500
DECISION_RATIONALE_MAX_CHARS = 2000
RESPONSE_TO_REFLECTION_MAX_CHARS = 2000
REFLECTION_RATIONALE_MAX_CHARS = 2000


def unique_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        clean = value.strip()
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(clean)
    return output


class InputTruth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_title: str
    jd: str
    notes: str
    job_title_sha256: str
    jd_sha256: str
    notes_sha256: str


class RequirementExtractionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_title: str = Field(min_length=1, description="Short normalized role title from the JD and notes.")
    title_anchor_terms: list[str] = Field(
        min_length=1,
        max_length=2,
        description="One or two stable searchable title anchors extracted from the job title.",
    )
    title_anchor_rationale: str = Field(
        min_length=1,
        description="Short explanation for why these title anchors best capture the searchable role title.",
    )
    jd_query_terms: list[str] = Field(
        default_factory=list,
        description="High-signal searchable terms extracted from the JD only, excluding all title anchors.",
    )
    notes_query_terms: list[str] = Field(
        default_factory=list,
        description="High-signal searchable terms extracted from the notes only, excluding all title anchors.",
    )
    role_summary: str = Field(min_length=1, description="Concise business summary of the role scope.")
    must_have_capabilities: list[str] = Field(default_factory=list, description="Critical capabilities required for fit.")
    preferred_capabilities: list[str] = Field(default_factory=list, description="Nice-to-have capabilities that strengthen fit.")
    exclusion_signals: list[str] = Field(default_factory=list, description="Signals that make the candidate unsuitable.")
    locations: list[str] = Field(default_factory=list, description="All allowed work locations mentioned by the input.")
    school_names: list[str] = Field(default_factory=list, description="Explicit school-name constraints mentioned by the input.")
    degree_requirement: str | None = Field(default=None, description="Human-readable degree requirement phrase.")
    school_type_requirement: list[str] = Field(default_factory=list, description="Human-readable school-type requirements.")
    experience_requirement: str | None = Field(default=None, description="Human-readable work experience requirement phrase.")
    gender_requirement: str | None = Field(default=None, description="Human-readable gender requirement phrase.")
    age_requirement: str | None = Field(default=None, description="Human-readable age requirement phrase.")
    company_names: list[str] = Field(default_factory=list, description="Explicit company-name constraints mentioned by the input.")
    preferred_locations: list[str] = Field(default_factory=list, description="Ordered preferred locations only when the input states a priority.")
    preferred_companies: list[str] = Field(default_factory=list, description="Preferred companies or employers.")
    preferred_domains: list[str] = Field(default_factory=list, description="Preferred industry or business domains.")
    preferred_backgrounds: list[str] = Field(default_factory=list, description="Preferred candidate background signals.")
    preferred_query_terms: list[str] = Field(default_factory=list, description="Reusable query-term hints, not a round query.")
    scoring_rationale: str = Field(min_length=1, description="Short explanation of the core scoring emphasis.")

    @model_validator(mode="before")
    @classmethod
    def fill_title_anchor_compatibility(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        legacy_anchor = data.pop("title_anchor_term", None)
        if "title_anchor_terms" not in data and legacy_anchor is not None:
            data["title_anchor_terms"] = [legacy_anchor]
        if "title_anchor_rationale" not in data and data.get("title_anchor_terms"):
            data["title_anchor_rationale"] = "Primary title anchor carried forward from the legacy title_anchor_term field."
        return data

    @property
    def title_anchor_term(self) -> str:
        return self.title_anchor_terms[0]


class CanonicalQuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lane_type: LaneType
    anchors: list[str] = Field(default_factory=list)
    expansion_terms: list[str] = Field(default_factory=list)
    promoted_prf_expression: str | None = None
    generic_explore_terms: list[str] = Field(default_factory=list)
    required_terms: list[str] = Field(default_factory=list)
    optional_terms: list[str] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)
    location_key: str | None = None
    provider_filters: dict[str, ConstraintValue] = Field(default_factory=dict)
    boolean_template: str
    rendered_provider_query: str
    provider_name: str
    source_plan_version: str


class DegreeRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_degree: str
    raw_text: str
    pinned: bool = False


class SchoolTypeRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_types: list[str] = Field(default_factory=list)
    raw_text: str
    pinned: bool = False


class ExperienceRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_years: int | None = None
    max_years: int | None = None
    raw_text: str
    pinned: bool = False


class GenderRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_gender: str
    raw_text: str
    pinned: bool = False


class AgeRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_age: int | None = None
    max_age: int | None = None
    raw_text: str
    pinned: bool = False


class HardConstraintSlots(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locations: list[str] = Field(default_factory=list)
    school_names: list[str] = Field(default_factory=list)
    degree_requirement: DegreeRequirement | None = None
    school_type_requirement: SchoolTypeRequirement | None = None
    experience_requirement: ExperienceRequirement | None = None
    gender_requirement: GenderRequirement | None = None
    age_requirement: AgeRequirement | None = None
    company_names: list[str] = Field(default_factory=list)


class PreferenceSlots(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_locations: list[str] = Field(default_factory=list)
    preferred_companies: list[str] = Field(default_factory=list)
    preferred_domains: list[str] = Field(default_factory=list)
    preferred_backgrounds: list[str] = Field(default_factory=list)
    preferred_query_terms: list[str] = Field(default_factory=list)


def _default_retrieval_role(category: str) -> QueryRetrievalRole:
    if category == "role_anchor":
        return "role_anchor"
    if category == "tooling":
        return "framework_tool"
    return "domain_context"


def is_primary_anchor_role(role: QueryRetrievalRole | str) -> bool:
    return role in {"primary_role_anchor", "role_anchor"}


def is_title_anchor_role(role: QueryRetrievalRole | str) -> bool:
    return role in {"primary_role_anchor", "secondary_title_anchor", "role_anchor"}


def _default_lane_type(query_role: QueryRole) -> LaneType:
    if query_role == "exploit":
        return "exploit"
    return "generic_explore"


def _default_query_family(term: str, category: str) -> str:
    clean = "".join(char.lower() for char in term.strip() if char.isalnum())
    if not clean:
        clean = "unknown"
    if category == "role_anchor":
        return f"role.{clean}"
    if category == "tooling":
        return f"framework.{clean}"
    return f"domain.{clean}"


class QueryTermCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str
    source: QueryTermSource
    category: QueryTermCategory
    priority: int
    evidence: str
    first_added_round: int
    active: bool = True
    retrieval_role: QueryRetrievalRole = "domain_context"
    queryability: Queryability = "admitted"
    family: str = "domain.unknown"

    @model_validator(mode="before")
    @classmethod
    def fill_search_metadata(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "retrieval_role" not in data:
            data["retrieval_role"] = _default_retrieval_role(str(data.get("category", "")))
        if "family" not in data:
            data["family"] = _default_query_family(str(data.get("term", "")), str(data.get("category", "")))
        return data


class RequirementSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_title: str
    title_anchor_terms: list[str] = Field(min_length=1, max_length=2)
    title_anchor_rationale: str = Field(min_length=1)
    role_summary: str
    must_have_capabilities: list[str] = Field(default_factory=list)
    preferred_capabilities: list[str] = Field(default_factory=list)
    exclusion_signals: list[str] = Field(default_factory=list)
    hard_constraints: HardConstraintSlots = Field(default_factory=HardConstraintSlots)
    preferences: PreferenceSlots = Field(default_factory=PreferenceSlots)
    initial_query_term_pool: list[QueryTermCandidate] = Field(default_factory=list)
    scoring_rationale: str

    @model_validator(mode="before")
    @classmethod
    def fill_title_anchor_compatibility(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        legacy_anchor = data.pop("title_anchor_term", None)
        if "title_anchor_terms" not in data and legacy_anchor is not None:
            data["title_anchor_terms"] = [legacy_anchor]
        if "title_anchor_rationale" not in data and data.get("title_anchor_terms"):
            data["title_anchor_rationale"] = "Primary title anchor carried forward from the legacy title_anchor_term field."
        return data

    @property
    def title_anchor_term(self) -> str:
        return self.title_anchor_terms[0]


class RequirementDigest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_title: str
    role_summary: str
    top_must_have_capabilities: list[str] = Field(default_factory=list)
    top_preferences: list[str] = Field(default_factory=list)
    hard_constraint_summary: list[str] = Field(default_factory=list)


class ReflectionKeywordAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggested_activate_terms: list[str] = Field(default_factory=list)
    suggested_keep_terms: list[str] = Field(default_factory=list)
    suggested_deprioritize_terms: list[str] = Field(default_factory=list)
    suggested_drop_terms: list[str] = Field(default_factory=list)


class ReflectionKeywordAdviceDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggested_activate_terms: list[str] = Field(default_factory=list)
    suggested_keep_terms: list[str] = Field(default_factory=list)
    suggested_deprioritize_terms: list[str] = Field(default_factory=list)
    suggested_drop_terms: list[str] = Field(default_factory=list)


class ReflectionFilterAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggested_keep_filter_fields: list[FilterField] = Field(default_factory=list)
    suggested_drop_filter_fields: list[FilterField] = Field(default_factory=list)
    suggested_add_filter_fields: list[FilterField] = Field(default_factory=list)


class ReflectionFilterAdviceDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggested_keep_filter_fields: list[FilterField] = Field(default_factory=list)
    suggested_drop_filter_fields: list[FilterField] = Field(default_factory=list)
    suggested_add_filter_fields: list[FilterField] = Field(default_factory=list)


class ReflectionAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword_advice: ReflectionKeywordAdvice = Field(default_factory=ReflectionKeywordAdvice, description="Field-safe query-term advice for the next round.")
    filter_advice: ReflectionFilterAdvice = Field(default_factory=ReflectionFilterAdvice, description="Field-level non-location filter advice for the next round.")
    reflection_rationale: str = Field(
        default="",
        max_length=REFLECTION_RATIONALE_MAX_CHARS,
        description="Human-readable explanation for the reflection advice. Used for TUI trace only.",
    )
    suggest_stop: bool = Field(
        default=False,
        description="Advisory only: whether reflection recommends stopping after this round. Runtime/controller own the final stop decision.",
    )
    suggested_stop_reason: str | None = Field(default=None, description="Concrete stop reason when suggest_stop is true.")
    reflection_summary: str = Field(min_length=1, description="Compact log-safe summary of the reflection output.")

    @model_validator(mode="after")
    def validate_stop_fields(self) -> ReflectionAdvice:
        if self.suggest_stop and not self.suggested_stop_reason:
            raise ValueError("suggested_stop_reason is required when suggest_stop is true")
        if not self.suggest_stop and self.suggested_stop_reason is not None:
            raise ValueError("suggested_stop_reason must be null when suggest_stop is false")
        return self


class ReflectionAdviceDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword_advice: ReflectionKeywordAdviceDraft = Field(description="Field-safe query-term advice for the next round.")
    filter_advice: ReflectionFilterAdviceDraft = Field(description="Field-level non-location filter advice for the next round.")
    reflection_rationale: str = Field(
        min_length=1,
        max_length=REFLECTION_RATIONALE_MAX_CHARS,
        description="Explain the round quality, coverage, and next action within schema budget.",
    )
    suggest_stop: bool = Field(
        description="Advisory only: whether reflection recommends stopping after this round. Runtime/controller own the final stop decision."
    )
    suggested_stop_reason: str | None = Field(default=None, description="Concrete stop reason when suggest_stop is true.")


class RuntimeConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: FilterField
    normalized_value: ConstraintValue
    source: ConditionSource
    rationale: str
    blocking: bool


class ConstraintProjectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_filters: dict[str, ConstraintValue] = Field(default_factory=dict)
    runtime_only_constraints: list[RuntimeConstraint] = Field(default_factory=list)
    adapter_notes: list[str] = Field(default_factory=list)


class ProposedFilterPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pinned_filters: dict[FilterField, ConstraintValue] = Field(default_factory=dict)
    optional_filters: dict[FilterField, ConstraintValue] = Field(default_factory=dict)
    dropped_filter_fields: list[FilterField] = Field(default_factory=list)
    added_filter_fields: list[FilterField] = Field(default_factory=list)


class LocationExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: LocationExecutionMode
    allowed_locations: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    priority_order: list[str] = Field(default_factory=list)
    balanced_order: list[str] = Field(default_factory=list)
    rotation_offset: int = 0
    target_new: int


class RoundRetrievalPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_version: int
    round_no: int
    query_terms: list[str] = Field(default_factory=list)
    role_anchor_terms: list[str] = Field(default_factory=list)
    must_have_anchor_terms: list[str] = Field(default_factory=list)
    keyword_query: str
    projected_provider_filters: dict[str, ConstraintValue] = Field(default_factory=dict)
    runtime_only_constraints: list[RuntimeConstraint] = Field(default_factory=list)
    location_execution_plan: LocationExecutionPlan
    target_new: int
    rationale: str


class SentQueryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_no: int
    query_role: QueryRole = "exploit"
    lane_type: LaneType | None = None
    query_instance_id: str | None = None
    query_fingerprint: str | None = None
    city: str | None = None
    phase: LocationExecutionPhase | None = None
    batch_no: int
    requested_count: int
    query_terms: list[str] = Field(default_factory=list)
    keyword_query: str
    source_plan_version: int
    rationale: str

    @model_validator(mode="after")
    def fill_lane_type(self) -> SentQueryRecord:
        if self.lane_type is None:
            self.lane_type = _default_lane_type(self.query_role)
        return self


class QueryResumeHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    query_instance_id: str
    query_fingerprint: str
    resume_id: str
    round_no: int
    lane_type: LaneType
    location_key: str | None = None
    location_type: str | None = None
    batch_no: int
    rank_in_query: int
    provider_name: str
    provider_page_no: int | None = None
    provider_fetch_no: int | None = None
    provider_score_if_any: float | None = None
    dedup_key: str | None = None
    was_new_to_pool: bool
    was_duplicate: bool
    scored_fit_bucket: FitBucket | None = None
    overall_score: int | None = None
    must_have_match_score: int | None = None
    risk_score: int | None = None
    off_intent_reason_count: int = 0
    final_candidate_status: str | None = None


class ReplaySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    round_no: int
    retrieval_snapshot_id: str
    second_lane_query_fingerprint: str | None = None
    provider_request: dict[str, Any] = Field(default_factory=dict)
    provider_response_resume_ids: list[str] = Field(default_factory=list)
    provider_response_raw_rank: list[str] = Field(default_factory=list)
    dedupe_version: str
    scoring_model_version: str
    query_plan_version: str
    prf_gate_version: str
    generic_explore_version: str | None = None
    prf_span_model_name: str | None = None
    prf_span_model_revision: str | None = None
    prf_span_schema_version: str | None = None
    prf_embedding_model_name: str | None = None
    prf_embedding_model_revision: str | None = None
    prf_familying_version: str | None = None
    prf_model_backend: str | None = None
    prf_sidecar_endpoint_contract_version: str | None = None
    prf_sidecar_dependency_manifest_hash: str | None = None
    prf_sidecar_image_digest: str | None = None
    prf_span_tokenizer_revision: str | None = None
    prf_embedding_dimension: int | None = None
    prf_embedding_normalized: bool | None = None
    prf_embedding_dtype: str | None = None
    prf_embedding_pooling: str | None = None
    prf_embedding_truncation: bool | None = None
    prf_fallback_reason: str | None = None
    prf_candidate_span_artifact_ref: str | None = None
    prf_expression_family_artifact_ref: str | None = None
    prf_policy_decision_artifact_ref: str | None = None


class SecondLaneDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_no: int
    attempted_prf: bool
    prf_gate_passed: bool
    selected_lane_type: LaneType | None = None
    selected_query_instance_id: str | None = None
    selected_query_fingerprint: str | None = None
    accepted_prf_expression: str | None = None
    accepted_prf_term_family_id: str | None = None
    prf_seed_resume_ids: list[str] = Field(default_factory=list)
    prf_candidate_expression_count: int = 0
    reject_reasons: list[str] = Field(default_factory=list)
    fallback_lane_type: LaneType | None = None
    fallback_query_fingerprint: str | None = None
    no_fetch_reason: str | None = None
    prf_policy_version: str
    generic_explore_version: str | None = None
    prf_v1_5_mode: Literal["disabled", "shadow", "mainline"] | None = None
    shadow_prf_v1_5_artifact_ref: str | None = None


class RetrievalState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_plan_version: int = 0
    candidate_feedback_attempted: bool = False
    anchor_only_broaden_attempted: bool = False
    rescue_lane_history: list[dict[str, object]] = Field(default_factory=list)
    query_term_pool: list[QueryTermCandidate] = Field(default_factory=list)
    sent_query_history: list[SentQueryRecord] = Field(default_factory=list)
    reflection_keyword_advice_history: list[ReflectionKeywordAdvice] = Field(default_factory=list)
    reflection_filter_advice_history: list[ReflectionFilterAdvice] = Field(default_factory=list)
    last_projection_result: ConstraintProjectionResult | None = None


class CTSQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_role: QueryRole = "exploit"
    lane_type: LaneType | None = None
    query_instance_id: str | None = None
    query_fingerprint: str | None = None
    query_terms: list[str] = Field(default_factory=list)
    keyword_query: str
    native_filters: dict[str, ConstraintValue] = Field(default_factory=dict)
    page: int = 1
    page_size: int = 10
    rationale: str
    adapter_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def fill_lane_type(self) -> CTSQuery:
        if self.lane_type is None:
            self.lane_type = _default_lane_type(self.query_role)
        return self


class SearchAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_role: QueryRole = "exploit"
    city: str | None = None
    phase: LocationExecutionPhase | None = None
    batch_no: int | None = None
    attempt_no: int
    requested_page: int
    requested_page_size: int
    raw_candidate_count: int
    batch_duplicate_count: int
    batch_unique_new_count: int
    cumulative_unique_new_count: int
    consecutive_zero_gain_attempts: int = 0
    continue_refill: bool
    exhausted_reason: str | None = None
    adapter_notes: list[str] = Field(default_factory=list)
    request_payload: dict[str, Any] = Field(default_factory=dict)


class CitySearchSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_role: QueryRole = "exploit"
    city: str
    phase: LocationExecutionPhase
    batch_no: int
    requested_count: int
    unique_new_count: int
    shortage_count: int
    start_page: int
    next_page: int
    fetch_attempt_count: int
    exhausted_reason: str | None = None


class SearchObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_no: int
    requested_count: int
    raw_candidate_count: int
    unique_new_count: int
    shortage_count: int
    fetch_attempt_count: int
    exhausted_reason: str | None = None
    new_resume_ids: list[str] = Field(default_factory=list)
    new_candidate_summaries: list[str] = Field(default_factory=list)
    adapter_notes: list[str] = Field(default_factory=list)
    city_search_summaries: list[CitySearchSummary] = Field(default_factory=list)


class QueryOutcomeThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low_recall_threshold: int = 2
    high_precision_threshold: float = 0.7
    noise_threshold: float = 0.1
    must_have_noise_threshold: float = 30.0
    drift_must_have_drop: float = 15.0
    drift_off_intent_min_count: int = 2


class QueryOutcomeClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_label: str
    labels: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class ResumeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str
    source_resume_id: str | None = None
    snapshot_sha256: str = ""
    dedup_key: str
    used_fallback_id: bool = False
    source_round: int | None = None
    first_query_instance_id: str | None = None
    first_query_fingerprint: str | None = None
    first_round_no: int | None = None
    first_lane_type: LaneType | None = None
    first_location_key: str | None = None
    first_location_type: str | None = None
    first_batch_no: int | None = None
    age: int | None = None
    gender: str | None = None
    now_location: str | None = None
    work_year: int | None = None
    expected_location: str | None = None
    expected_job_category: str | None = None
    expected_industry: str | None = None
    expected_salary: str | None = None
    active_status: str | None = None
    job_state: str | None = None
    education_summaries: list[str] = Field(default_factory=list)
    work_experience_summaries: list[str] = Field(default_factory=list)
    project_names: list[str] = Field(default_factory=list)
    work_summaries: list[str] = Field(default_factory=list)
    search_text: str
    raw: dict[str, Any] = Field(default_factory=dict)

    def compact_summary(self) -> str:
        parts = [
            self.expected_job_category or "",
            self.now_location or "",
            f"{self.work_year or 0}y",
            ", ".join(self.project_names[:2]),
        ]
        return " | ".join(part for part in parts if part)


class NormalizedExperience(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""
    company: str = ""
    duration: str = ""
    summary: str = ""


class NormalizedResume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str
    dedup_key: str
    used_fallback_id: bool = False
    candidate_name: str = ""
    headline: str = ""
    current_title: str = ""
    current_company: str = ""
    years_of_experience: int | None = None
    locations: list[str] = Field(default_factory=list)
    education_summary: str = ""
    skills: list[str] = Field(default_factory=list)
    industry_tags: list[str] = Field(default_factory=list)
    language_tags: list[str] = Field(default_factory=list)
    recent_experiences: list[NormalizedExperience] = Field(default_factory=list)
    key_achievements: list[str] = Field(default_factory=list)
    raw_text_excerpt: str = ""
    completeness_score: int = Field(ge=0, le=100)
    missing_fields: list[str] = Field(default_factory=list)
    normalization_notes: list[str] = Field(default_factory=list)
    source_round: int | None = None

    def compact_summary(self) -> str:
        parts = [
            self.current_title or self.headline,
            self.current_company,
            "/".join(self.locations[:2]),
            f"{self.years_of_experience}y" if self.years_of_experience is not None else "",
        ]
        return " | ".join(part for part in parts if part)

    @property
    def scoring_text(self) -> str:
        experience_blobs = [
            " ".join(part for part in [item.title, item.company, item.duration, item.summary] if part)
            for item in self.recent_experiences
        ]
        chunks = [
            self.candidate_name,
            self.headline,
            self.current_title,
            self.current_company,
            " ".join(self.locations),
            self.education_summary,
            " ".join(self.skills),
            " ".join(self.industry_tags),
            " ".join(self.language_tags),
            " ".join(self.key_achievements),
            self.raw_text_excerpt,
            *experience_blobs,
        ]
        return " ".join(chunk for chunk in chunks if chunk)


class ScoringPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_title: str
    role_summary: str
    must_have_capabilities: list[str] = Field(default_factory=list)
    preferred_capabilities: list[str] = Field(default_factory=list)
    exclusion_signals: list[str] = Field(default_factory=list)
    hard_constraints: HardConstraintSlots = Field(default_factory=HardConstraintSlots)
    preferences: PreferenceSlots = Field(default_factory=PreferenceSlots)
    scoring_rationale: str


class ScoringContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_no: int
    scoring_policy: ScoringPolicy
    normalized_resume: NormalizedResume
    requirement_sheet_sha256: str = Field(min_length=1)
    runtime_only_constraints: list[RuntimeConstraint] = Field(default_factory=list)


class ScoredCandidateDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fit_bucket: FitBucket = Field(description="Top-level keep-or-drop decision for this resume.")
    overall_score: int = Field(ge=0, le=100, description="Overall role-fit score.")
    must_have_match_score: int = Field(ge=0, le=100, description="Score for critical must-have alignment.")
    preferred_match_score: int = Field(ge=0, le=100, description="Score for preferred-signal alignment.")
    risk_score: int = Field(ge=0, le=100, description="Risk score where higher means more concern.")
    risk_flags: list[str] = Field(default_factory=list, description="Concise risk flags grounded in the resume.")
    reasoning_summary: str = Field(min_length=1, description="Short scoring rationale for reviewers and logs.")
    matched_must_haves: list[str] = Field(default_factory=list, description="Must-have signals supported by resume evidence.")
    missing_must_haves: list[str] = Field(default_factory=list, description="Must-have signals missing or unsupported by resume evidence.")
    matched_preferences: list[str] = Field(default_factory=list, description="Preferred signals supported by resume evidence.")
    negative_signals: list[str] = Field(default_factory=list, description="Resume signals that count against the candidate.")


class ScoredCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str = Field(description="Stable resume identifier from the candidate source.")
    fit_bucket: FitBucket = Field(description="Top-level keep-or-drop decision for this resume.")
    overall_score: int = Field(ge=0, le=100, description="Overall role-fit score.")
    must_have_match_score: int = Field(ge=0, le=100, description="Score for critical must-have alignment.")
    preferred_match_score: int = Field(ge=0, le=100, description="Score for preferred-signal alignment.")
    risk_score: int = Field(ge=0, le=100, description="Risk score where higher means more concern.")
    risk_flags: list[str] = Field(default_factory=list, description="Concise risk flags grounded in the resume.")
    reasoning_summary: str = Field(min_length=1, description="Short scoring rationale for reviewers and logs.")
    evidence: list[str] = Field(default_factory=list, description="Resume-grounded evidence snippets supporting the judgment.")
    confidence: ScoringConfidence = Field(description="Confidence in the scoring judgment.")
    matched_must_haves: list[str] = Field(default_factory=list, description="Must-have signals supported by resume evidence.")
    missing_must_haves: list[str] = Field(default_factory=list, description="Must-have signals missing or unsupported by resume evidence.")
    matched_preferences: list[str] = Field(default_factory=list, description="Preferred signals supported by resume evidence.")
    negative_signals: list[str] = Field(default_factory=list, description="Resume signals that count against the candidate.")
    strengths: list[str] = Field(default_factory=list, description="Concise strengths worth surfacing downstream.")
    weaknesses: list[str] = Field(default_factory=list, description="Concise weaknesses worth surfacing downstream.")
    source_round: int = Field(description="Round number in which this resume entered the scoring flow.")


class ScoringFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str
    branch_id: str
    round_no: int
    attempts: int
    error_message: str
    latency_ms: int | None = None


class TopPoolEntryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str
    fit_bucket: FitBucket
    overall_score: int = Field(ge=0, le=100)
    must_have_match_score: int = Field(ge=0, le=100)
    risk_score: int = Field(ge=0, le=100)
    matched_must_haves: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    reasoning_summary: str


class StopGuidance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    can_stop: bool
    reason: str
    continue_reasons: list[str] = Field(default_factory=list)
    tried_families: list[str] = Field(default_factory=list)
    untried_admitted_families: list[str] = Field(default_factory=list)
    productive_round_count: int = 0
    zero_gain_round_count: int = 0
    top_pool_strength: TopPoolStrength
    fit_count: int = 0
    strong_fit_count: int = 0
    high_risk_fit_count: int = 0
    quality_gate_status: StopQualityGateStatus = "pass"
    broadening_attempted: bool = False


class SearchObservationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unique_new_count: int
    shortage_count: int
    fetch_attempt_count: int
    exhausted_reason: str | None = None
    new_candidate_summaries: list[str] = Field(default_factory=list)
    adapter_notes: list[str] = Field(default_factory=list)
    city_search_summaries: list[CitySearchSummary] = Field(default_factory=list)


class ReflectionSummaryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: DecisionType
    stop_reason: str | None = None
    reflection_summary: str
    reflection_rationale: str = ""


class ControllerContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_jd: str
    full_notes: str
    requirement_sheet: RequirementSheet
    round_no: int
    min_rounds: int
    max_rounds: int
    retrieval_rounds_completed: int
    rounds_remaining_after_current: int
    budget_used_ratio: float
    near_budget_limit: bool
    is_final_allowed_round: bool
    target_new: int
    stop_guidance: StopGuidance
    requirement_digest: RequirementDigest | None = None
    query_term_pool: list[QueryTermCandidate] = Field(default_factory=list)
    current_top_pool: list[TopPoolEntryView] = Field(default_factory=list)
    latest_search_observation: SearchObservationView | None = None
    previous_reflection: ReflectionSummaryView | None = None
    latest_reflection_keyword_advice: ReflectionKeywordAdvice | None = None
    latest_reflection_filter_advice: ReflectionFilterAdvice | None = None
    sent_query_history: list[SentQueryRecord] = Field(default_factory=list)
    shortage_history: list[int] = Field(default_factory=list)
    budget_reminder: str = ""


class SearchControllerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thought_summary: str = Field(
        min_length=1,
        max_length=THOUGHT_SUMMARY_MAX_CHARS,
        description="Short summary of the controller's current decision.",
    )
    action: Literal["search_cts"] = Field(description="Continue to the next CTS search round.")
    decision_rationale: str = Field(
        min_length=1,
        max_length=DECISION_RATIONALE_MAX_CHARS,
        description="Short operational rationale for the search decision.",
    )
    proposed_query_terms: list[str] = Field(description="Proposed round query terms before runtime canonicalization.")
    proposed_filter_plan: ProposedFilterPlan = Field(description="Proposed non-location filter plan before runtime canonicalization.")
    response_to_reflection: str | None = Field(
        default=None,
        max_length=RESPONSE_TO_REFLECTION_MAX_CHARS,
        description="Explicit response to the previous round's reflection when one exists.",
    )


class StopControllerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thought_summary: str = Field(
        min_length=1,
        max_length=THOUGHT_SUMMARY_MAX_CHARS,
        description="Short summary of the controller's current decision.",
    )
    action: Literal["stop"] = Field(description="Stop retrieval and finish the run.")
    decision_rationale: str = Field(
        min_length=1,
        max_length=DECISION_RATIONALE_MAX_CHARS,
        description="Short operational rationale for the stop decision.",
    )
    response_to_reflection: str | None = Field(
        default=None,
        max_length=RESPONSE_TO_REFLECTION_MAX_CHARS,
        description="Explicit response to the previous round's reflection when one exists.",
    )
    stop_reason: str = Field(min_length=1, description="Concrete stop reason for ending retrieval.")


ControllerDecision = Annotated[
    SearchControllerDecision | StopControllerDecision,
    Field(discriminator="action"),
]


class PoolDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str
    round_no: int
    decision: PoolDecisionType
    rank_in_round: int | None = None
    reasons_for_selection: list[str] = Field(default_factory=list)
    reasons_for_rejection: list[str] = Field(default_factory=list)
    compared_against_pool_summary: str = ""


class FinalCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str = Field(description="Stable resume identifier copied from the scored candidate.")
    rank: int = Field(description="Final shortlist rank, contiguous and starting at 1.")
    final_score: int = Field(description="Final score surfaced in the shortlist.")
    fit_bucket: FitBucket = Field(description="Top-level fit decision copied from scoring.")
    match_summary: str = Field(min_length=1, description="Short presentation summary of the candidate match.")
    strengths: list[str] = Field(default_factory=list, description="Strengths kept for reviewer display.")
    weaknesses: list[str] = Field(default_factory=list, description="Weaknesses kept for reviewer display.")
    matched_must_haves: list[str] = Field(default_factory=list, description="Matched must-have signals to surface downstream.")
    matched_preferences: list[str] = Field(default_factory=list, description="Matched preference signals to surface downstream.")
    risk_flags: list[str] = Field(default_factory=list, description="Risk flags to surface downstream.")
    why_selected: str = Field(min_length=1, description="Short explanation of why the candidate was selected.")
    source_round: int = Field(description="Round number in which this candidate first entered the pool.")


class FinalCandidateDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str = Field(description="Stable resume identifier copied from the scored candidate.")
    match_summary: str = Field(min_length=1, description="Short presentation summary of the candidate match.")
    why_selected: str = Field(min_length=1, description="Short explanation of why the candidate was selected.")


class FinalResultDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, description="Short top-level summary of the final shortlist.")
    candidates: list[FinalCandidateDraft] = Field(description="Candidate presentation text in runtime ranking order.")


class FinalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(description="Runtime-generated run identifier.")
    run_dir: str = Field(description="Filesystem path for the run artifacts.")
    rounds_executed: int = Field(description="Number of rounds executed before finalization.")
    stop_reason: str = Field(description="Canonical stop reason chosen by runtime.")
    candidates: list[FinalCandidate] = Field(description="Final shortlist candidates in runtime-preserved order.")
    summary: str = Field(min_length=1, description="Short top-level summary of the final shortlist.")


class ReflectionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_no: int
    full_jd: str
    full_notes: str
    requirement_sheet: RequirementSheet
    current_retrieval_plan: RoundRetrievalPlan
    search_observation: SearchObservation
    search_attempts: list[SearchAttempt] = Field(default_factory=list)
    top_candidates: list[ScoredCandidate] = Field(default_factory=list)
    dropped_candidates: list[ScoredCandidate] = Field(default_factory=list)
    scoring_failures: list[ScoringFailure] = Field(default_factory=list)
    sent_query_history: list[SentQueryRecord] = Field(default_factory=list)
    query_term_pool: list[QueryTermCandidate] = Field(default_factory=list)


class FinalizeContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    run_dir: str
    rounds_executed: int
    stop_reason: str
    top_candidates: list[ScoredCandidate] = Field(default_factory=list)
    requirement_digest: RequirementDigest | None = None
    sent_query_history: list[SentQueryRecord] = Field(default_factory=list)


class TerminalControllerRound(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_no: int
    controller_decision: StopControllerDecision
    stop_guidance: StopGuidance


class RoundState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_no: int
    controller_decision: ControllerDecision
    retrieval_plan: RoundRetrievalPlan
    constraint_projection_result: ConstraintProjectionResult | None = None
    cts_queries: list[CTSQuery] = Field(default_factory=list)
    search_observation: SearchObservation | None = None
    search_attempts: list[SearchAttempt] = Field(default_factory=list)
    top_candidates: list[ScoredCandidate] = Field(default_factory=list)
    dropped_candidates: list[ScoredCandidate] = Field(default_factory=list)
    top_pool_ids: list[str] = Field(default_factory=list)
    dropped_candidate_ids: list[str] = Field(default_factory=list)
    reflection_advice: ReflectionAdvice | None = None


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_truth: InputTruth
    requirement_sheet: RequirementSheet
    scoring_policy: ScoringPolicy
    retrieval_state: RetrievalState
    seen_resume_ids: list[str] = Field(default_factory=list)
    candidate_store: dict[str, ResumeCandidate] = Field(default_factory=dict)
    normalized_store: dict[str, NormalizedResume] = Field(default_factory=dict)
    scorecards_by_resume_id: dict[str, ScoredCandidate] = Field(default_factory=dict)
    top_pool_ids: list[str] = Field(default_factory=list)
    round_history: list[RoundState] = Field(default_factory=list)


def stable_fallback_resume_id(payload: dict[str, Any]) -> str:
    digest = sha1(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"fallback-{digest}"


def scored_candidate_sort_key(candidate: ScoredCandidate) -> tuple[int, int, int, int, str]:
    return (
        0 if candidate.fit_bucket == "fit" else 1,
        -candidate.overall_score,
        -candidate.must_have_match_score,
        candidate.risk_score,
        candidate.resume_id,
    )
