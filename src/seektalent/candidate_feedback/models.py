from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from seektalent.models import QueryTermCandidate


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
