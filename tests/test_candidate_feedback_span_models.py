from __future__ import annotations

from seektalent.candidate_feedback.models import PRFProposalArtifactRefs, PRFProposalVersionVector
from seektalent.candidate_feedback.span_models import CandidateSpan, PhraseFamily, ProposalMetadata


def test_candidate_span_build_preserves_exact_source_coordinates() -> None:
    span = CandidateSpan.build(
        source_resume_id="resume-1",
        source_field="evidence",
        start_char=12,
        end_char=29,
        raw_surface="LangGraph tool calling",
        normalized_surface="langgraph tool calling",
        model_label="technical_phrase",
        model_score=0.93,
        extractor_schema_version="span-extractor-v1",
    )

    assert span.source_resume_id == "resume-1"
    assert span.source_field == "evidence"
    assert span.start_char == 12
    assert span.end_char == 29
    assert span.raw_surface == "LangGraph tool calling"
    assert span.normalized_surface == "langgraph tool calling"
    assert span.model_label == "technical_phrase"
    assert span.model_score == 0.93
    assert span.extractor_schema_version == "span-extractor-v1"
    assert span.span_id != "ignored"
    assert len(span.span_id) > 8


def test_phrase_family_keeps_guard_metadata() -> None:
    family = PhraseFamily(
        family_id="family-1",
        canonical_surface="LangGraph",
        candidate_term_type="tool_or_framework",
        surfaces=["LangGraph", "LangGraph tool calling"],
        source_span_ids=["span-1", "span-2"],
        positive_seed_support_count=2,
        negative_support_count=1,
        familying_rule="surface-normalization",
        familying_score=0.87,
        reject_reasons=["company_entity_rejected"],
    )

    assert family.family_id == "family-1"
    assert family.canonical_surface == "LangGraph"
    assert family.candidate_term_type == "tool_or_framework"
    assert family.surfaces == ["LangGraph", "LangGraph tool calling"]
    assert family.source_span_ids == ["span-1", "span-2"]
    assert family.positive_seed_support_count == 2
    assert family.negative_support_count == 1
    assert family.familying_rule == "surface-normalization"
    assert family.familying_score == 0.87
    assert family.reject_reasons == ["company_entity_rejected"]


def test_proposal_metadata_carries_model_and_familying_versions() -> None:
    metadata = ProposalMetadata(
        extractor_version="extractor-v3",
        span_model_name="span-model",
        span_model_revision="2026-04-01",
        tokenizer_revision="tokenizer-v2",
        schema_version="span-schema-v1",
        schema_payload={"kind": "candidate-span"},
        thresholds_version="thresholds-v4",
        embedding_model_name="embed-model",
        embedding_model_revision="2026-03-20",
        familying_version="familying-v2",
        familying_thresholds={"min_support": 2},
        runtime_mode="batch",
        top_n_candidate_cap=25,
    )

    assert metadata.extractor_version == "extractor-v3"
    assert metadata.span_model_name == "span-model"
    assert metadata.span_model_revision == "2026-04-01"
    assert metadata.tokenizer_revision == "tokenizer-v2"
    assert metadata.schema_version == "span-schema-v1"
    assert metadata.schema_payload == {"kind": "candidate-span"}
    assert metadata.thresholds_version == "thresholds-v4"
    assert metadata.embedding_model_name == "embed-model"
    assert metadata.embedding_model_revision == "2026-03-20"
    assert metadata.familying_version == "familying-v2"
    assert metadata.familying_thresholds == {"min_support": 2}
    assert metadata.runtime_mode == "batch"
    assert metadata.top_n_candidate_cap == 25


def test_prf_proposal_contract_models_are_exposed_from_candidate_feedback_models() -> None:
    refs = PRFProposalArtifactRefs(
        candidate_span_artifact_ref="artifact.candidate_span",
        expression_family_artifact_ref="artifact.expression_family",
        policy_decision_artifact_ref="artifact.policy_decision",
    )
    version_vector = PRFProposalVersionVector(
        span_extractor_version="extractor-v3",
        span_model_name="span-model",
        span_model_revision="2026-04-01",
        span_tokenizer_revision="tokenizer-v2",
        span_schema_version="span-schema-v1",
        span_thresholds_version="thresholds-v4",
        embedding_model_name="embed-model",
        embedding_model_revision="2026-03-20",
        familying_version="familying-v2",
        familying_thresholds={"min_support": 2},
        runtime_mode="batch",
        top_n_candidate_cap=25,
    )

    assert refs.candidate_span_artifact_ref == "artifact.candidate_span"
    assert version_vector.familying_version == "familying-v2"
