from __future__ import annotations

import json
from hashlib import sha1
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


ConstraintValue = str | int | list[str]


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
