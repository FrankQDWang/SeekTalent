from __future__ import annotations

import json
from hashlib import sha1
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FitBucket = Literal["fit", "not_fit"]
DecisionType = Literal["continue", "stop"]
ConditionSource = Literal["jd", "notes", "inferred"]
ConditionStrictness = Literal["hard", "soft"]
FilterOperator = Literal["equals", "contains", "in", "gte", "lte"]
ScoringConfidence = Literal["high", "medium", "low"]


def unique_strings(values: list[str]) -> list[str]:
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


class KeywordAttribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str
    source: ConditionSource
    bucket: Literal["must_have", "preferred", "negative"]
    reason: str


class FilterCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    value: str | int | list[str]
    source: ConditionSource
    rationale: str
    strictness: ConditionStrictness
    operator: FilterOperator = "equals"


class SearchStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    must_have_keywords: list[str] = Field(default_factory=list)
    preferred_keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)
    hard_filters: list[FilterCondition] = Field(default_factory=list)
    soft_filters: list[FilterCondition] = Field(default_factory=list)
    keyword_attributions: list[KeywordAttribution] = Field(default_factory=list)
    search_rationale: str
    strategy_version: int = 1

    def normalized(self) -> "SearchStrategy":
        return self.model_copy(
            update={
                "must_have_keywords": unique_strings(self.must_have_keywords),
                "preferred_keywords": unique_strings(
                    [
                        value
                        for value in self.preferred_keywords
                        if value.casefold()
                        not in {item.casefold() for item in self.must_have_keywords}
                    ]
                ),
                "negative_keywords": unique_strings(self.negative_keywords),
            }
        )

    @property
    def retrieval_keywords(self) -> list[str]:
        return unique_strings(self.must_have_keywords + self.preferred_keywords)


class CTSQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keywords: list[str]
    keyword_query: str
    hard_filters: list[FilterCondition] = Field(default_factory=list)
    soft_filters: list[FilterCondition] = Field(default_factory=list)
    exclude_ids: list[str] = Field(default_factory=list)
    page: int = 1
    page_size: int = 10
    rationale: str
    adapter_notes: list[str] = Field(default_factory=list)


class ResumeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str
    dedup_key: str
    used_fallback_id: bool = False
    source_round: int | None = None
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


class ScoringContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_no: int
    must_have_keywords: list[str] = Field(default_factory=list)
    preferred_keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)
    hard_filters: list[FilterCondition] = Field(default_factory=list)
    soft_filters: list[FilterCondition] = Field(default_factory=list)
    scoring_rationale: str


class ScoredCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str
    fit_bucket: FitBucket
    overall_score: int = Field(ge=0, le=100)
    must_have_match_score: int = Field(ge=0, le=100)
    preferred_match_score: int = Field(ge=0, le=100)
    risk_score: int = Field(ge=0, le=100)
    risk_flags: list[str] = Field(default_factory=list)
    reasoning_summary: str
    evidence: list[str] = Field(default_factory=list)
    confidence: ScoringConfidence
    matched_must_haves: list[str] = Field(default_factory=list)
    missing_must_haves: list[str] = Field(default_factory=list)
    matched_preferences: list[str] = Field(default_factory=list)
    negative_signals: list[str] = Field(default_factory=list)
    source_round: int
    retry_count: int = 0


class ScoringFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str
    branch_id: str
    round_no: int
    attempts: int
    error_message: str
    retried: bool
    final_failure: bool
    latency_ms: int | None = None


class ReflectionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_assessment: str
    quality_assessment: str
    coverage_assessment: str
    adjust_keywords: list[str] = Field(default_factory=list)
    adjust_negative_keywords: list[str] = Field(default_factory=list)
    adjust_hard_filters: list[FilterCondition] = Field(default_factory=list)
    adjust_soft_filters: list[FilterCondition] = Field(default_factory=list)
    decision: DecisionType
    stop_reason: str | None = None
    reflection_summary: str
    strategy_changes: list[str] = Field(default_factory=list)
    hard_filter_relaxation_reason: str | None = None


class RunContextSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    run_dir: str
    round_no: int
    seen_resume_ids: list[str] = Field(default_factory=list)
    current_top_ids: list[str] = Field(default_factory=list)
    strategy: SearchStrategy
    prompt_hashes: dict[str, str]
    model_settings: dict[str, Any]
    reflection_enabled: bool


class RoundResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_no: int
    query: CTSQuery
    new_candidates: list[ResumeCandidate]
    normalized_resumes: list[NormalizedResume]
    scoring_pool_resume_ids: list[str]
    scored_candidates: list[ScoredCandidate]
    scoring_failures: list[ScoringFailure]
    top_candidates: list[ScoredCandidate]
    reflection: ReflectionDecision | None = None
    shortage_count: int = 0
    duplicate_count: int = 0
    stop_reason: str | None = None
    context_snapshot: RunContextSnapshot


class FinalCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_id: str
    rank: int
    final_score: int
    fit_bucket: FitBucket
    match_summary: str
    matched_must_haves: list[str] = Field(default_factory=list)
    matched_preferences: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    why_selected: str
    source_round: int


class FinalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    run_dir: str
    rounds_executed: int
    stop_reason: str
    candidates: list[FinalCandidate]
    summary: str


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
