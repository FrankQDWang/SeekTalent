from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from seektalent.models import QueryTermCandidate


CandidateTermType = Literal[
    "skill",
    "tool_or_framework",
    "product_or_platform",
    "technical_phrase",
    "responsibility_phrase",
    "company_entity",
    "location",
    "degree",
    "compensation",
    "administrative",
    "generic",
    "unknown_high_risk",
    "unknown",
]


class FeedbackCandidateTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str
    supporting_resume_ids: list[str] = Field(default_factory=list)
    linked_requirements: list[str] = Field(default_factory=list)
    field_hits: dict[str, int] = Field(default_factory=dict)
    fit_support_rate: float = 0.0
    not_fit_support_rate: float = 0.0
    score: float = 0.0
    risk_flags: list[str] = Field(default_factory=list)
    rejection_reason: str | None = None


class FeedbackCandidateExpression(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term_family_id: str
    canonical_expression: str
    surface_forms: list[str] = Field(default_factory=list)
    candidate_term_type: CandidateTermType
    source_seed_resume_ids: list[str] = Field(default_factory=list)
    linked_requirements: list[str] = Field(default_factory=list)
    field_hits: dict[str, int] = Field(default_factory=dict)
    positive_seed_support_count: int = 0
    negative_support_count: int = 0
    fit_support_rate: float = 0.0
    not_fit_support_rate: float = 0.0
    tried_query_fingerprints: list[str] = Field(default_factory=list)
    score: float = 0.0
    reject_reasons: list[str] = Field(default_factory=list)

    @property
    def supporting_resume_ids(self) -> list[str]:
        return self.source_seed_resume_ids

    @property
    def fit_support_count(self) -> int:
        return self.positive_seed_support_count

    @property
    def not_fit_support_count(self) -> int:
        return self.negative_support_count


class CandidateFeedbackDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed_resume_ids: list[str] = Field(default_factory=list)
    candidate_terms: list[FeedbackCandidateTerm] = Field(default_factory=list)
    rejected_terms: list[FeedbackCandidateTerm] = Field(default_factory=list)
    accepted_candidates: list[FeedbackCandidateTerm] = Field(default_factory=list)
    accepted_term: QueryTermCandidate | None = None
    forced_query_terms: list[str] = Field(default_factory=list)
    skipped_reason: str | None = None


class CandidateFeedbackModelRanking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted_terms: list[str] = Field(default_factory=list)
    rejected_terms: dict[str, str] = Field(default_factory=dict)
    rationale: str

    def accepted_from(self, candidates: list[FeedbackCandidateTerm]) -> list[str]:
        allowed = {item.term for item in candidates}
        output: list[str] = []
        for term in self.accepted_terms:
            if term in allowed and term not in output:
                output.append(term)
        return output


class PRFProposalArtifactRefs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # These refs point at the same proposal family identity across spans,
    # expression families, and the downstream policy decision.
    candidate_span_artifact_ref: str
    expression_family_artifact_ref: str
    policy_decision_artifact_ref: str


class PRFProposalVersionVector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    span_extractor_version: str
    span_model_name: str
    span_model_revision: str
    span_tokenizer_revision: str
    span_schema_version: str
    span_thresholds_version: str
    embedding_model_name: str
    embedding_model_revision: str
    familying_version: str
    familying_thresholds: dict[str, object] = Field(default_factory=dict)
    runtime_mode: str
    top_n_candidate_cap: int = Field(ge=0)
    model_backend: str = "legacy"
    sidecar_endpoint_contract_version: str | None = None
    sidecar_dependency_manifest_hash: str | None = None
    sidecar_image_digest: str | None = None
    embedding_dimension: int | None = None
    embedding_normalized: bool | None = None
    embedding_dtype: str | None = None
    embedding_pooling: str | None = None
    embedding_truncation: bool | None = None
    fallback_reason: str | None = None
