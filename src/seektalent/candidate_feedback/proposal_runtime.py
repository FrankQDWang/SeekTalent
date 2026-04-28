from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from seektalent.candidate_feedback.extraction import build_term_family_id
from seektalent.candidate_feedback.familying import should_merge_spans
from seektalent.candidate_feedback.models import PRFProposalArtifactRefs, PRFProposalVersionVector
from seektalent.candidate_feedback.span_extractors import (
    GLiNER2SpanExtractor,
    LegacyRegexSpanExtractor,
    SpanModelBackend,
)
from seektalent.candidate_feedback.span_models import CandidateSpan, CandidateTermType, PhraseFamily, ProposalMetadata
from seektalent.config import AppSettings
from seektalent.models import ScoredCandidate

_PROPOSAL_SOURCE_FIELDS = (
    ("evidence", lambda resume: list(resume.evidence)),
    ("matched_must_haves", lambda resume: list(resume.matched_must_haves)),
    ("matched_preferences", lambda resume: list(resume.matched_preferences)),
    ("strengths", lambda resume: list(resume.strengths)),
)


class PRFProposalOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_spans: list[CandidateSpan] = Field(default_factory=list)
    phrase_families: list[PhraseFamily] = Field(default_factory=list)
    metadata: ProposalMetadata
    artifact_refs: PRFProposalArtifactRefs
    version_vector: PRFProposalVersionVector


def build_prf_proposal_artifact_refs(*, round_no: int) -> PRFProposalArtifactRefs:
    prefix = f"round.{round_no:02d}.retrieval"
    return PRFProposalArtifactRefs(
        candidate_span_artifact_ref=f"{prefix}.prf_span_candidates",
        expression_family_artifact_ref=f"{prefix}.prf_expression_families",
        policy_decision_artifact_ref=f"{prefix}.prf_policy_decision",
    )


def build_proposal_output(
    *,
    candidate_spans: list[CandidateSpan],
    metadata: ProposalMetadata,
    round_no: int,
    embedding_similarity: Callable[[CandidateSpan, CandidateSpan], float] | None = None,
) -> PRFProposalOutput:
    return PRFProposalOutput(
        candidate_spans=list(candidate_spans),
        phrase_families=list(
            build_phrase_families(
                positive_spans=candidate_spans,
                negative_spans=[],
                embedding_similarity=embedding_similarity,
            ).values()
        ),
        metadata=metadata,
        artifact_refs=build_prf_proposal_artifact_refs(round_no=round_no),
        version_vector=_build_version_vector(metadata),
    )


def build_prf_proposal_bundle(
    *,
    positive_seed_resumes: list[ScoredCandidate],
    negative_seed_resumes: list[ScoredCandidate],
    extractor: LegacyRegexSpanExtractor | GLiNER2SpanExtractor,
    metadata: ProposalMetadata,
    round_no: int,
    embedding_similarity: Callable[[CandidateSpan, CandidateSpan], float] | None = None,
) -> PRFProposalOutput:
    positive_spans = extract_candidate_spans(
        resumes=positive_seed_resumes,
        extractor=extractor,
    )
    negative_spans = extract_candidate_spans(
        resumes=negative_seed_resumes,
        extractor=extractor,
    )
    phrase_families = list(
        build_phrase_families(
            positive_spans=positive_spans,
            negative_spans=negative_spans,
            embedding_similarity=embedding_similarity,
        ).values()
    )
    return PRFProposalOutput(
        candidate_spans=[*_ordered_spans(positive_spans), *_ordered_spans(negative_spans)],
        phrase_families=phrase_families,
        metadata=metadata,
        artifact_refs=build_prf_proposal_artifact_refs(round_no=round_no),
        version_vector=_build_version_vector(metadata),
    )


def extract_candidate_spans(
    *,
    resumes: list[ScoredCandidate],
    extractor: LegacyRegexSpanExtractor | GLiNER2SpanExtractor,
) -> list[CandidateSpan]:
    spans: list[CandidateSpan] = []
    for resume in resumes:
        for source_field, getter in _PROPOSAL_SOURCE_FIELDS:
            texts = getter(resume)
            if not texts:
                continue
            spans.extend(
                extractor.extract(
                    resume_id=resume.resume_id,
                    source_field=source_field,
                    texts=texts,
                )
            )
    return spans


def model_dependency_gate_allows_mainline(settings: AppSettings) -> bool:
    required_revisions = (
        settings.prf_span_model_revision,
        settings.prf_span_tokenizer_revision,
        settings.prf_embedding_model_revision,
        settings.prf_span_schema_version,
    )
    if settings.prf_require_pinned_models_for_mainline and any(not value.strip() for value in required_revisions):
        return False
    if not settings.prf_allow_remote_code and not settings.prf_remote_code_audit_revision:
        return False
    return True


def build_prf_span_extractor(
    settings: AppSettings,
    *,
    backend: SpanModelBackend | None = None,
) -> LegacyRegexSpanExtractor | GLiNER2SpanExtractor:
    if backend is None:
        return LegacyRegexSpanExtractor()
    if not model_dependency_gate_allows_mainline(settings):
        return LegacyRegexSpanExtractor()
    return make_model_span_extractor(
        backend=backend,
        schema_version=settings.prf_span_schema_version,
    )


def build_phrase_families(
    *,
    positive_spans: list[CandidateSpan],
    negative_spans: list[CandidateSpan],
    embedding_similarity: Callable[[CandidateSpan, CandidateSpan], float] | None = None,
) -> dict[str, PhraseFamily]:
    similarity = embedding_similarity or _default_embedding_similarity
    mutable_families: list[_MutablePhraseFamily] = []

    for span in _ordered_spans(positive_spans):
        family = _family_for_span(mutable_families, span, similarity=similarity)
        family.add_positive(span)

    for span in _ordered_spans(negative_spans):
        family = _family_for_span(mutable_families, span, similarity=similarity)
        family.add_negative(span)

    families = [item.freeze() for item in mutable_families]
    families.sort(key=lambda item: (-item.positive_seed_support_count, item.canonical_surface.casefold(), item.family_id))
    return {family.family_id: family for family in families}


def make_model_span_extractor(
    *,
    backend: SpanModelBackend,
    schema_version: str,
    labels: list[str] | None = None,
) -> GLiNER2SpanExtractor:
    return GLiNER2SpanExtractor(
        backend=backend,
        schema_version=schema_version,
        labels=labels,
    )


def _ordered_spans(spans: list[CandidateSpan]) -> list[CandidateSpan]:
    return sorted(
        spans,
        key=lambda span: (
            -span.model_score,
            span.normalized_surface.casefold(),
            span.source_resume_id,
            span.source_field,
            span.source_text_index,
            span.start_char,
            span.end_char,
        ),
    )


def _family_for_span(
    families: list[_MutablePhraseFamily],
    span: CandidateSpan,
    *,
    similarity: Callable[[CandidateSpan, CandidateSpan], float],
) -> _MutablePhraseFamily:
    for family in families:
        merged, rule = should_merge_spans(
            family.representative,
            span,
            embedding_similarity=similarity(family.representative, span),
        )
        if merged:
            family.familying_rule = rule
            family.familying_score = max(family.familying_score, similarity(family.representative, span))
            return family

    created = _MutablePhraseFamily(
        representative=span,
        family_id=build_term_family_id(span.normalized_surface),
        canonical_surface=span.normalized_surface,
        candidate_term_type=_candidate_term_type(span.model_label),
        familying_rule="canonical_surface_match",
        familying_score=1.0,
    )
    families.append(created)
    return created


def _candidate_term_type(model_label: str) -> CandidateTermType:
    allowed: set[str] = {
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
    }
    if model_label in allowed:
        return model_label  # type: ignore[return-value]
    if model_label == "legacy_regex_surface":
        return "technical_phrase"
    return "unknown"


def _default_embedding_similarity(left: CandidateSpan, right: CandidateSpan) -> float:
    if left.normalized_surface.casefold() == right.normalized_surface.casefold():
        return 1.0
    return 0.0


def _build_version_vector(metadata: ProposalMetadata) -> PRFProposalVersionVector:
    return PRFProposalVersionVector(
        span_extractor_version=metadata.extractor_version,
        span_model_name=metadata.span_model_name,
        span_model_revision=metadata.span_model_revision,
        span_tokenizer_revision=metadata.tokenizer_revision,
        span_schema_version=metadata.schema_version,
        span_thresholds_version=metadata.thresholds_version,
        embedding_model_name=metadata.embedding_model_name,
        embedding_model_revision=metadata.embedding_model_revision,
        familying_version=metadata.familying_version,
        familying_thresholds=dict(metadata.familying_thresholds),
        runtime_mode=metadata.runtime_mode,
        top_n_candidate_cap=metadata.top_n_candidate_cap,
    )


@dataclass
class _MutablePhraseFamily:
    representative: CandidateSpan
    family_id: str
    canonical_surface: str
    candidate_term_type: CandidateTermType
    familying_rule: str
    familying_score: float
    surfaces: set[str] = field(default_factory=set)
    source_span_ids: list[str] = field(default_factory=list)
    positive_resume_ids: set[str] = field(default_factory=set)
    negative_resume_ids: set[str] = field(default_factory=set)
    reject_reasons: list[str] = field(default_factory=list)

    def add_positive(self, span: CandidateSpan) -> None:
        self._record_span(span)
        self.positive_resume_ids.add(span.source_resume_id)

    def add_negative(self, span: CandidateSpan) -> None:
        self._record_span(span)
        self.negative_resume_ids.add(span.source_resume_id)

    def _record_span(self, span: CandidateSpan) -> None:
        self.surfaces.add(span.raw_surface)
        if span.span_id not in self.source_span_ids:
            self.source_span_ids.append(span.span_id)
        for reason in span.reject_reasons:
            if reason not in self.reject_reasons:
                self.reject_reasons.append(reason)

    def freeze(self) -> PhraseFamily:
        return PhraseFamily(
            family_id=self.family_id,
            canonical_surface=self.canonical_surface,
            candidate_term_type=self.candidate_term_type,
            surfaces=sorted(self.surfaces, key=str.casefold),
            source_span_ids=list(self.source_span_ids),
            positive_seed_support_count=len(self.positive_resume_ids),
            negative_support_count=len(self.negative_resume_ids),
            familying_rule=self.familying_rule,
            familying_score=self.familying_score,
            reject_reasons=list(self.reject_reasons),
        )
