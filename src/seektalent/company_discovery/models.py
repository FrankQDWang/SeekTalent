from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CompanySource = Literal["explicit_jd", "explicit_notes", "web_inferred", "candidate_backfill"]
CompanyIntent = Literal[
    "target",
    "similar_to_target",
    "competitor",
    "same_domain",
    "exclude",
    "client_company",
    "unknown",
]
CompanySearchUsage = Literal["keyword_term", "company_filter", "keyword_and_filter", "score_boost", "exclude", "holdout"]
CompanySourceType = Literal["web", "user_input"]
SearchIntent = Literal["market_map", "competitor_map", "role_evidence", "industry_list"]


class WebSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int
    title: str
    url: str
    site_name: str = ""
    snippet: str = ""
    summary: str = ""
    published_at: str | None = None


class SearchRerankResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int
    source_index: int
    score: float
    title: str
    url: str


class PageReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    title: str = ""
    text: str = ""


class CompanyEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    url: str
    snippet: str
    source_type: CompanySourceType


class TargetCompanyCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    aliases: list[str] = Field(default_factory=list)
    source: CompanySource
    intent: CompanyIntent
    confidence: float = Field(ge=0, le=1)
    fit_axes: list[str] = Field(default_factory=list)
    search_usage: CompanySearchUsage
    evidence: list[CompanyEvidence] = Field(default_factory=list)
    rationale: str


class TargetCompanyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    explicit_targets: list[TargetCompanyCandidate] = Field(default_factory=list)
    inferred_targets: list[TargetCompanyCandidate] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    holdout_companies: list[str] = Field(default_factory=list)
    rejected_companies: list[str] = Field(default_factory=list)
    web_discovery_attempted: bool = False
    stop_reason: str | None = None

    @property
    def accepted_targets(self) -> list[TargetCompanyCandidate]:
        return [*self.explicit_targets, *self.inferred_targets]

    @property
    def has_accepted_companies(self) -> bool:
        return bool(self.accepted_targets)


class CompanyDiscoveryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_title: str
    title_anchor_term: str
    must_have_capabilities: list[str] = Field(default_factory=list)
    preferred_domains: list[str] = Field(default_factory=list)
    preferred_backgrounds: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)


class CompanySearchTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str
    query: str
    intent: SearchIntent
    rationale: str


class CompanySearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[CompanySearchTask] = Field(default_factory=list)


class CompanyEvidenceExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[TargetCompanyCandidate] = Field(default_factory=list)


class CompanyDiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: TargetCompanyPlan
    discovery_input: CompanyDiscoveryInput | None = None
    search_tasks: list[CompanySearchTask] = Field(default_factory=list)
    search_results: list[WebSearchResult] = Field(default_factory=list)
    reranked_results: list[SearchRerankResult] = Field(default_factory=list)
    page_reads: list[PageReadResult] = Field(default_factory=list)
    evidence_candidates: list[TargetCompanyCandidate] = Field(default_factory=list)
    search_result_count: int = 0
    opened_page_count: int = 0
    trigger_reason: str
