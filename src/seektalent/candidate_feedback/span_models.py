from __future__ import annotations

import json
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


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
    source_text_index: int = Field(ge=0)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    raw_surface: str = Field(min_length=1)
    normalized_surface: str = Field(min_length=1)
    model_label: str
    model_score: float = Field(ge=0.0, le=1.0)
    extractor_schema_version: str
    reject_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_coordinate_order(self) -> CandidateSpan:
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        return self

    @classmethod
    def build(
        cls,
        *,
        source_resume_id: str,
        source_field: SourceField,
        source_text_index: int,
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
            source_text_index=source_text_index,
            start_char=start_char,
            end_char=end_char,
            raw_surface=raw_surface,
            extractor_schema_version=extractor_schema_version,
        )
        return cls(
            span_id=span_id,
            source_resume_id=source_resume_id,
            source_field=source_field,
            source_text_index=source_text_index,
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

    @computed_field(return_type=str)
    @property
    def term_family_id(self) -> str:
        return self.family_id


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


def _build_span_id(
    *,
    source_resume_id: str,
    source_field: SourceField,
    source_text_index: int,
    start_char: int,
    end_char: int,
    raw_surface: str,
    extractor_schema_version: str,
) -> str:
    payload = json.dumps(
        [
            source_resume_id,
            source_field,
            source_text_index,
            start_char,
            end_char,
            raw_surface,
            extractor_schema_version,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = sha256(payload.encode("utf-8")).hexdigest()
    return f"span.{digest[:12]}"
