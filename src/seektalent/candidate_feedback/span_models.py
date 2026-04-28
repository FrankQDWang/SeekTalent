from __future__ import annotations

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    "process",
    "generic",
    "unknown_high_risk",
    "unknown",
]

SourceField = Literal["evidence", "matched_must_haves", "matched_preferences", "strengths"]


class CandidateSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    span_id: str
    source_resume_id: str
    source_field: SourceField
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    raw_surface: str = Field(min_length=1)
    normalized_surface: str = Field(min_length=1)
    model_label: str
    model_score: float = Field(ge=0.0, le=1.0)
    extractor_schema_version: str
    reject_reasons: list[str] = Field(default_factory=list)

    @classmethod
    def build(
        cls,
        *,
        source_resume_id: str,
        source_field: SourceField,
        start_char: int,
        end_char: int,
        raw_surface: str,
        normalized_surface: str,
        model_label: str,
        model_score: float,
        extractor_schema_version: str,
        reject_reasons: list[str] | None = None,
    ) -> CandidateSpan:
        span_id = _build_span_id(
            source_resume_id=source_resume_id,
            source_field=source_field,
            start_char=start_char,
            end_char=end_char,
            raw_surface=raw_surface,
            extractor_schema_version=extractor_schema_version,
        )
        return cls(
            span_id=span_id,
            source_resume_id=source_resume_id,
            source_field=source_field,
            start_char=start_char,
            end_char=end_char,
            raw_surface=raw_surface,
            normalized_surface=normalized_surface,
            model_label=model_label,
            model_score=model_score,
            extractor_schema_version=extractor_schema_version,
            reject_reasons=reject_reasons or [],
        )


class PhraseFamily(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # `family_id` is the persisted family artifact identity and should match
    # the expression/policy family id used later in the PRF gate.
    family_id: str
    canonical_surface: str
    candidate_term_type: CandidateTermType
    surfaces: list[str] = Field(default_factory=list)
    source_span_ids: list[str] = Field(default_factory=list)
    positive_seed_support_count: int = 0
    negative_support_count: int = 0
    familying_rule: str
    familying_score: float = Field(ge=0.0, le=1.0)
    reject_reasons: list[str] = Field(default_factory=list)


class ProposalMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extractor_version: str
    span_model_name: str
    span_model_revision: str
    tokenizer_revision: str
    schema_version: str
    schema_payload: dict[str, object] = Field(default_factory=dict)
    thresholds_version: str
    embedding_model_name: str
    embedding_model_revision: str
    familying_version: str
    familying_thresholds: dict[str, object] = Field(default_factory=dict)
    runtime_mode: str
    top_n_candidate_cap: int


def _build_span_id(
    *,
    source_resume_id: str,
    source_field: SourceField,
    start_char: int,
    end_char: int,
    raw_surface: str,
    extractor_schema_version: str,
) -> str:
    payload = "|".join(
        [
            source_resume_id,
            source_field,
            str(start_char),
            str(end_char),
            raw_surface,
            extractor_schema_version,
        ]
    )
    digest = sha256(payload.encode("utf-8")).hexdigest()
    return f"span.{digest[:12]}"
