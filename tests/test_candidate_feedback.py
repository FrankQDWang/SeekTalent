from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from seektalent.candidate_feedback import (
    build_feedback_decision,
    classify_feedback_expressions,
    extract_surface_terms,
    extract_feedback_candidate_expressions,
    select_feedback_seed_resumes,
)
from seektalent.candidate_feedback.span_extractors import (
    FakeSpanModelBackend,
    GLiNER2SpanExtractor,
    LegacyRegexSpanExtractor,
)
from seektalent.candidate_feedback.model_steps import CandidateFeedbackModelSteps
from seektalent.candidate_feedback.models import (
    CandidateFeedbackModelRanking,
    FeedbackCandidateExpression,
    FeedbackCandidateTerm,
)
from seektalent.candidate_feedback.policy import PRFGateInput, build_prf_policy_decision
from seektalent.candidate_feedback.proposal_runtime import build_prf_proposal_bundle
from seektalent.models import (
    FitBucket,
    QueryRetrievalRole,
    QueryTermCandidate,
    QueryTermCategory,
    QueryTermSource,
    Queryability,
    ScoredCandidate,
)
from seektalent.prf_sidecar.client import (
    HttpEmbeddingBackend,
    HttpSpanModelBackend,
    SidecarRevisionMismatch,
    SidecarSchemaMismatch,
    fetch_sidecar_readyz,
)
from seektalent.prompting import LoadedPrompt
from seektalent.llm import ResolvedTextModelConfig
from tests.settings_factory import make_settings


def _scored_candidate(
    resume_id: str,
    *,
    fit_bucket: FitBucket = "fit",
    overall_score: int = 80,
    must_have_match_score: int = 70,
    risk_score: int = 20,
    reasoning_summary: str = "Seed summary.",
    evidence: list[str] | None = None,
    matched_must_haves: list[str] | None = None,
    matched_preferences: list[str] | None = None,
    strengths: list[str] | None = None,
    weaknesses: list[str] | None = None,
    negative_signals: list[str] | None = None,
) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        fit_bucket=fit_bucket,
        overall_score=overall_score,
        must_have_match_score=must_have_match_score,
        preferred_match_score=65,
        risk_score=risk_score,
        risk_flags=[],
        reasoning_summary=reasoning_summary,
        evidence=evidence or [],
        confidence="high",
        matched_must_haves=matched_must_haves or [],
        missing_must_haves=[],
        matched_preferences=matched_preferences or [],
        negative_signals=negative_signals or [],
        strengths=strengths or [],
        weaknesses=weaknesses or [],
        source_round=1,
    )


def _query_term(
    term: str,
    *,
    source: QueryTermSource = "jd",
    category: QueryTermCategory = "domain",
    retrieval_role: QueryRetrievalRole = "domain_context",
    queryability: Queryability = "admitted",
    active: bool = True,
    family: str | None = None,
) -> QueryTermCandidate:
    return QueryTermCandidate(
        term=term,
        source=source,
        category=category,
        priority=1,
        evidence="Seed evidence.",
        first_added_round=1,
        active=active,
        retrieval_role=retrieval_role,
        queryability=queryability,
        family=family or f"feedback.{term.casefold().replace(' ', '').replace('.', '')}",
    )


def _expression(
    expression: str,
    *,
    candidate_term_type: str = "technical_phrase",
    positive_seed_support_count: int = 2,
    negative_support_count: int = 0,
    not_fit_support_rate: float = 0.0,
    reject_reasons: list[str] | None = None,
    field_hits: dict[str, int] | None = None,
) -> FeedbackCandidateExpression:
    return FeedbackCandidateExpression(
        term_family_id=f"feedback.{expression.casefold().replace(' ', '-')}",
        canonical_expression=expression,
        surface_forms=[expression],
        candidate_term_type=candidate_term_type,
        source_seed_resume_ids=[f"seed-{index}" for index in range(1, positive_seed_support_count + 1)],
        positive_seed_support_count=positive_seed_support_count,
        negative_support_count=negative_support_count,
        not_fit_support_rate=not_fit_support_rate,
        reject_reasons=reject_reasons or [],
        field_hits=field_hits or {},
    )


def test_select_feedback_seed_resumes_selects_only_strict_fit_seeds() -> None:
    selected = select_feedback_seed_resumes(
        [
            _scored_candidate("weak-fit", overall_score=74, must_have_match_score=80, risk_score=20),
            _scored_candidate("best-fit", overall_score=91, must_have_match_score=88, risk_score=18),
            _scored_candidate("mid-fit", overall_score=84, must_have_match_score=77, risk_score=45),
            _scored_candidate("too-risky", overall_score=88, must_have_match_score=80, risk_score=46),
            _scored_candidate("not-fit", fit_bucket="not_fit", overall_score=99, must_have_match_score=99, risk_score=1),
            _scored_candidate("lower-fit", overall_score=83, must_have_match_score=70, risk_score=20),
        ],
        limit=2,
    )

    assert [item.resume_id for item in selected] == ["best-fit", "mid-fit"]


def test_select_feedback_seed_resumes_never_returns_more_than_five() -> None:
    selected = select_feedback_seed_resumes(
        [_scored_candidate(f"fit-{index}", overall_score=90 - index) for index in range(6)],
        limit=10,
    )

    assert [item.resume_id for item in selected] == ["fit-0", "fit-1", "fit-2", "fit-3", "fit-4"]


def test_extract_surface_terms_preserves_technical_and_mixed_shapes() -> None:
    terms = extract_surface_terms(
        [
            "LangGraph, RAG, tool calling, C++, Node.js, Flink CDC, 实时数仓, 任务编排, 平台, 系统, 开发",
            "We use LangGraph for tool calling in Node.js.",
        ]
    )

    for term in ["LangGraph", "RAG", "tool calling", "C++", "Node.js", "Flink CDC", "实时数仓", "任务编排"]:
        assert term in terms
    for term in ["平台", "系统", "开发"]:
        assert term not in terms


def test_legacy_regex_span_extractor_rejects_generic_chinese_fragments() -> None:
    text = "掌握至少一种OLAP引擎（如Doris/ClickHouse），精通Python及主流Web框架（FastAPI/Flask/Django）"
    extractor = LegacyRegexSpanExtractor()

    spans = extractor.extract(
        resume_id="resume-1",
        source_field="matched_must_haves",
        texts=[text],
    )

    surfaces = {span.raw_surface for span in spans}

    for rejected in {"掌握至少一种", "引擎", "精通", "及主流", "框架"}:
        assert rejected not in surfaces


def test_legacy_regex_span_extractor_keeps_grounded_framework_terms() -> None:
    text = "掌握至少一种OLAP引擎（如Doris/ClickHouse），精通Python及主流Web框架（FastAPI/Flask/Django）"
    extractor = LegacyRegexSpanExtractor()

    spans = extractor.extract(
        resume_id="resume-1",
        source_field="matched_must_haves",
        texts=[text],
    )

    surfaces = {span.raw_surface for span in spans}

    for expected in {"ClickHouse", "FastAPI", "Flask", "Django"}:
        assert expected in surfaces


def test_legacy_regex_span_extractor_preserves_slash_compound_surface_and_children() -> None:
    text = "精通Python及主流Web框架（FastAPI/Flask/Django）"
    extractor = LegacyRegexSpanExtractor()

    spans = extractor.extract(
        resume_id="resume-1",
        source_field="matched_must_haves",
        texts=[text],
    )

    surfaces = {span.raw_surface for span in spans}

    assert "FastAPI/Flask/Django" in surfaces
    for expected in {"FastAPI", "Flask", "Django"}:
        assert expected in surfaces


def test_legacy_regex_span_extractor_keeps_distinct_list_items_separate() -> None:
    extractor = LegacyRegexSpanExtractor()

    spans = extractor.extract(
        resume_id="resume-1",
        source_field="matched_must_haves",
        texts=["ClickHouse", "ClickHouse"],
    )

    assert len(spans) == 2
    assert [span.source_text_index for span in spans] == [0, 1]
    assert len({span.span_id for span in spans}) == 2


def test_fake_span_model_backend_surfaces_are_aligned_back_to_exact_offsets() -> None:
    text = "精通Python及主流Web框架（FastAPI/Flask/Django）"
    backend = FakeSpanModelBackend(outputs=[{"label": "tool_or_framework", "surface": "FastAPI"}])
    extractor = GLiNER2SpanExtractor(backend=backend, schema_version="gliner2-schema-v1")

    spans = extractor.extract(
        resume_id="resume-1",
        source_field="matched_must_haves",
        texts=[text],
    )

    assert len(spans) == 1
    assert spans[0].raw_surface == "FastAPI"
    assert spans[0].source_text_index == 0
    assert text[spans[0].start_char : spans[0].end_char] == "FastAPI"


def test_model_surface_without_exact_match_rejects_as_non_extractive() -> None:
    text = "掌握至少一种OLAP引擎（如Doris/ClickHouse）"
    backend = FakeSpanModelBackend(outputs=[{"label": "technical_phrase", "surface": "Doris OLAP"}])
    extractor = GLiNER2SpanExtractor(backend=backend, schema_version="gliner2-schema-v1")

    spans = extractor.extract(
        resume_id="resume-1",
        source_field="matched_must_haves",
        texts=[text],
    )

    assert spans == []


def test_support_counts_distinct_seed_resumes_not_span_occurrences() -> None:
    proposal = build_prf_proposal_bundle(
        positive_seed_resumes=[
            _scored_candidate(
                "seed-1",
                evidence=["Flink CDC"],
                strengths=["Flink CDC"],
                must_have_match_score=80,
            ),
            _scored_candidate(
                "seed-2",
                matched_must_haves=["Flink CDC"],
                must_have_match_score=80,
            ),
        ],
        negative_seed_resumes=[],
        extractor=LegacyRegexSpanExtractor(),
        metadata=_proposal_metadata(),
        round_no=2,
    )

    family = _family_by_surface(proposal.phrase_families, "Flink CDC")
    assert family.positive_seed_support_count == 2


def test_negative_support_counts_distinct_negative_resumes() -> None:
    proposal = build_prf_proposal_bundle(
        positive_seed_resumes=[
            _scored_candidate(
                "seed-1",
                evidence=["Flink CDC"],
                must_have_match_score=80,
            )
        ],
        negative_seed_resumes=[
            _scored_candidate(
                "neg-1",
                fit_bucket="not_fit",
                evidence=["Flink CDC"],
                matched_preferences=["Flink CDC"],
            ),
            _scored_candidate(
                "neg-2",
                fit_bucket="not_fit",
                evidence=["Flink CDC"],
            ),
        ],
        extractor=LegacyRegexSpanExtractor(),
        metadata=_proposal_metadata(),
        round_no=2,
    )

    family = _family_by_surface(proposal.phrase_families, "Flink CDC")
    assert family.negative_support_count == 2


def test_prf_v1_5_proposal_runtime_exposes_typed_artifact_refs_and_versions() -> None:
    proposal = build_prf_proposal_bundle(
        positive_seed_resumes=[
            _scored_candidate(
                "seed-1",
                evidence=["Flink CDC"],
                must_have_match_score=80,
            )
        ],
        negative_seed_resumes=[],
        extractor=LegacyRegexSpanExtractor(),
        metadata=_proposal_metadata(),
        round_no=2,
    )

    assert proposal.artifact_refs.candidate_span_artifact_ref == "round.02.retrieval.prf_span_candidates"
    assert proposal.artifact_refs.expression_family_artifact_ref == "round.02.retrieval.prf_expression_families"
    assert proposal.artifact_refs.policy_decision_artifact_ref == "round.02.retrieval.prf_policy_decision"
    assert proposal.version_vector.span_model_name == "span-model"
    assert proposal.version_vector.span_model_revision == "span-rev"
    assert proposal.version_vector.familying_version == "familying-v1"
    assert proposal.metadata.runtime_mode == "shadow"


def test_responsibility_phrase_is_shadow_only_in_phase_1_5() -> None:
    decision = build_prf_policy_decision(
        PRFGateInput(
            round_no=2,
            seed_resume_ids=["seed-1", "seed-2", "seed-3"],
            seed_count=3,
            negative_resume_ids=[],
            candidate_expressions=[
                _expression(
                    "负责系统设计",
                    candidate_term_type="responsibility_phrase",
                    positive_seed_support_count=3,
                )
            ],
            candidate_expression_count=1,
            tried_term_family_ids=[],
            tried_query_fingerprints=[],
            policy_version="prf-policy-v1",
        )
    )

    assert decision.gate_passed is False
    assert decision.reject_reasons == ["no_safe_prf_expression"]
    assert "shadow_only_responsibility_phrase" in decision.candidate_expressions[0].reject_reasons


def test_ambiguous_company_or_product_entity_is_rejected_by_default() -> None:
    decision = build_prf_policy_decision(
        PRFGateInput(
            round_no=2,
            seed_resume_ids=["seed-1", "seed-2", "seed-3"],
            seed_count=3,
            negative_resume_ids=[],
            candidate_expressions=[
                _expression(
                    "Databricks",
                    candidate_term_type="product_or_platform",
                    positive_seed_support_count=3,
                    reject_reasons=["ambiguous_company_or_product_entity"],
                )
            ],
            candidate_expression_count=1,
            tried_term_family_ids=[],
            tried_query_fingerprints=[],
            policy_version="prf-policy-v1",
        )
    )

    assert decision.gate_passed is False
    assert "ambiguous_company_or_product_entity" in decision.candidate_expressions[0].reject_reasons


def test_strengths_only_span_is_shadow_hint_not_promotable() -> None:
    decision = build_prf_policy_decision(
        PRFGateInput(
            round_no=2,
            seed_resume_ids=["seed-1", "seed-2"],
            seed_count=2,
            negative_resume_ids=[],
            candidate_expressions=[
                _expression(
                    "Flink CDC",
                    candidate_term_type="technical_phrase",
                    positive_seed_support_count=2,
                    field_hits={"strengths": 2},
                )
            ],
            candidate_expression_count=1,
            tried_term_family_ids=[],
            tried_query_fingerprints=[],
            policy_version="prf-policy-v1",
        )
    )

    assert decision.gate_passed is False
    assert "derived_summary_only_grounding" in decision.candidate_expressions[0].reject_reasons


def test_policy_gate_does_not_mutate_persisted_phrase_family_objects() -> None:
    original = _expression("Databricks", reject_reasons=["ambiguous_company_or_product_entity"])
    frozen = original.model_copy(deep=True)

    build_prf_policy_decision(
        PRFGateInput(
            round_no=2,
            seed_resume_ids=["seed-1", "seed-2"],
            seed_count=2,
            negative_resume_ids=[],
            candidate_expressions=[original],
            candidate_expression_count=1,
            tried_term_family_ids=[],
            tried_query_fingerprints=[],
            policy_version="prf-policy-v1",
        )
    )

    assert original.model_dump(mode="json") == frozen.model_dump(mode="json")


def test_sidecar_span_backend_returns_validated_rows() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/span-extract"
        return httpx.Response(
            200,
            json={
                "schema_version": "prf-sidecar-span-v1",
                "model_name": "fastino/gliner2-multi-v1",
                "model_revision": "rev-span",
                "rows": [
                    {
                        "request_text_index": 0,
                        "surface": "FastAPI",
                        "label": "tool_or_framework",
                        "score": 0.97,
                        "model_start_char": 0,
                        "model_end_char": 7,
                        "alignment_hint_only": True,
                    }
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://prf-model-sidecar:8741")
    backend = HttpSpanModelBackend(
        endpoint="http://prf-model-sidecar:8741",
        model_name="fastino/gliner2-multi-v1",
        model_revision="rev-span",
        schema_version="gliner2-schema-v1",
        timeout_seconds=0.5,
        http_client=client,
    )

    rows = backend.extract(text="FastAPI", labels=["tool_or_framework"])

    assert rows == [
        {
            "request_text_index": 0,
            "surface": "FastAPI",
            "label": "tool_or_framework",
            "score": 0.97,
            "model_start_char": 0,
            "model_end_char": 7,
            "alignment_hint_only": True,
        }
    ]


def test_sidecar_embedding_backend_returns_validated_vectors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embed"
        return httpx.Response(
            200,
            json={
                "schema_version": "prf-sidecar-embed-v1",
                "model_name": "Alibaba-NLP/gte-multilingual-base",
                "model_revision": "rev-embed",
                "embedding_dimension": 3,
                "normalized": True,
                "pooling": "mean",
                "dtype": "float32",
                "max_input_tokens": 8192,
                "truncation": True,
                "vectors": [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://prf-model-sidecar:8741")
    backend = HttpEmbeddingBackend(
        endpoint="http://prf-model-sidecar:8741",
        model_name="Alibaba-NLP/gte-multilingual-base",
        model_revision="rev-embed",
        timeout_seconds=0.5,
        http_client=client,
    )

    response = backend.embed(["Flink CDC", "flink-cdc"])

    assert response.embedding_dimension == 3
    assert response.normalized is True
    assert len(response.vectors) == 2


def test_sidecar_embedding_backend_rejects_wrong_revision() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "schema_version": "prf-sidecar-embed-v1",
                "model_name": "Alibaba-NLP/gte-multilingual-base",
                "model_revision": "wrong-rev",
                "embedding_dimension": 2,
                "normalized": True,
                "pooling": "mean",
                "dtype": "float32",
                "max_input_tokens": 8192,
                "truncation": True,
                "vectors": [[1.0, 0.0], [0.0, 1.0]],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://prf-model-sidecar:8741")
    backend = HttpEmbeddingBackend(
        endpoint="http://prf-model-sidecar:8741",
        model_name="Alibaba-NLP/gte-multilingual-base",
        model_revision="rev-embed",
        timeout_seconds=0.5,
        http_client=client,
    )

    with pytest.raises(SidecarRevisionMismatch):
        backend.embed(["Flink CDC", "flink-cdc"])


def test_sidecar_embedding_backend_rejects_invalid_vector_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "schema_version": "prf-sidecar-embed-v1",
                "model_name": "Alibaba-NLP/gte-multilingual-base",
                "model_revision": "rev-embed",
                "embedding_dimension": 3,
                "normalized": True,
                "pooling": "mean",
                "dtype": "float32",
                "max_input_tokens": 8192,
                "truncation": True,
                "vectors": [[1.0, 0.0], [0.0, 1.0]],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://prf-model-sidecar:8741")
    backend = HttpEmbeddingBackend(
        endpoint="http://prf-model-sidecar:8741",
        model_name="Alibaba-NLP/gte-multilingual-base",
        model_revision="rev-embed",
        timeout_seconds=0.5,
        http_client=client,
    )

    with pytest.raises(SidecarSchemaMismatch):
        backend.embed(["Flink CDC", "flink-cdc"])


def test_fetch_sidecar_readyz_preserves_not_ready_payload_from_503() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/readyz"
        return httpx.Response(
            503,
            json={
                "status": "not_ready",
                "endpoint_contract_version": "prf-sidecar-http-v1",
                "dependency_manifest_hash": None,
                "sidecar_image_digest": None,
                "span_model_loaded": False,
                "embedding_model_loaded": False,
                "span_model_name": "fastino/gliner2-multi-v1",
                "span_model_revision": "rev-span",
                "span_tokenizer_revision": "rev-tokenizer",
                "embedding_model_name": "Alibaba-NLP/gte-multilingual-base",
                "embedding_model_revision": "rev-embed",
                "not_ready_reason": "pinned cache missing",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://prf-model-sidecar:8741")

    ready = fetch_sidecar_readyz(
        endpoint="http://prf-model-sidecar:8741",
        timeout_seconds=0.5,
        http_client=client,
    )

    assert ready.status == "not_ready"
    assert ready.not_ready_reason == "pinned cache missing"


def test_extract_feedback_candidate_expressions_keeps_short_phrase_as_single_family() -> None:
    expressions = extract_feedback_candidate_expressions(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["tool calling"]),
            _scored_candidate("seed-2", evidence=["tool calling"]),
        ],
        negative_resumes=[],
    )

    assert len(expressions) == 1
    assert expressions[0].canonical_expression == "tool calling"
    assert expressions[0].surface_forms == ["tool calling"]
    assert expressions[0].term_family_id == "feedback.tool-calling"
    assert expressions[0].positive_seed_support_count == 2
    assert expressions[0].candidate_term_type == "technical_phrase"


def _proposal_metadata():
    from seektalent.candidate_feedback.span_models import ProposalMetadata

    return ProposalMetadata(
        extractor_version="proposal-runtime-v1",
        span_model_name="span-model",
        span_model_revision="span-rev",
        tokenizer_revision="tokenizer-rev",
        schema_version="schema-v1",
        schema_payload={"labels": ["technical_phrase"]},
        thresholds_version="thresholds-v1",
        embedding_model_name="embed-model",
        embedding_model_revision="embed-rev",
        familying_version="familying-v1",
        familying_thresholds={"embedding_similarity": 0.92},
        runtime_mode="shadow",
        top_n_candidate_cap=16,
    )


def _family_by_surface(families, canonical_surface: str):
    for family in families:
        if family.canonical_surface == canonical_surface:
            return family
    raise AssertionError(f"missing family for {canonical_surface}")


def test_classify_feedback_expressions_rejects_known_company_entity_but_keeps_product() -> None:
    expressions = classify_feedback_expressions(
        ["ByteDance", "Databricks"],
        known_company_entities={"ByteDance"},
        known_product_platforms={"Databricks"},
    )

    assert [item.canonical_expression for item in expressions] == ["ByteDance", "Databricks"]
    assert expressions[0].candidate_term_type == "company_entity"
    assert expressions[0].reject_reasons == ["company_entity"]
    assert expressions[1].candidate_term_type == "product_or_platform"
    assert expressions[1].reject_reasons == []


def test_extract_feedback_candidate_expressions_returns_low_support_evidence_without_prf_gate() -> None:
    expressions = extract_feedback_candidate_expressions(
        seed_resumes=[_scored_candidate("seed-1", evidence=["Databricks"])],
        negative_resumes=[],
        known_product_platforms={"Databricks"},
    )

    assert len(expressions) == 1
    assert expressions[0].canonical_expression == "Databricks"
    assert expressions[0].positive_seed_support_count == 1
    assert expressions[0].negative_support_count == 0
    assert expressions[0].reject_reasons == []


def test_extract_feedback_candidate_expressions_handles_negative_only_evidence() -> None:
    expressions = extract_feedback_candidate_expressions(
        seed_resumes=[],
        negative_resumes=[_scored_candidate("not-fit-1", fit_bucket="not_fit", evidence=["Databricks"])],
        known_product_platforms={"Databricks"},
    )

    assert len(expressions) == 1
    assert expressions[0].canonical_expression == "Databricks"
    assert expressions[0].positive_seed_support_count == 0
    assert expressions[0].negative_support_count == 1
    assert expressions[0].fit_support_rate == 0.0
    assert expressions[0].not_fit_support_rate == 1.0


def test_build_prf_policy_decision_carries_full_gate_input_context() -> None:
    decision = build_prf_policy_decision(
        PRFGateInput(
            round_no=2,
            seed_resume_ids=["seed-1", "seed-2"],
            seed_count=2,
            negative_resume_ids=["neg-1"],
            candidate_expressions=[_expression("LangGraph")],
            candidate_expression_count=1,
            tried_term_family_ids=["feedback.rag"],
            tried_query_fingerprints=["fp-1"],
            min_seed_count=2,
            max_negative_support_rate=0.4,
            policy_version="prf-policy-v1",
        )
    )

    assert decision.attempted is True
    assert decision.gate_passed is True
    assert decision.gate_input.round_no == 2
    assert decision.gate_input.seed_resume_ids == ["seed-1", "seed-2"]
    assert decision.gate_input.seed_count == 2
    assert decision.gate_input.negative_resume_ids == ["neg-1"]
    assert decision.gate_input.candidate_expression_count == 1
    assert decision.gate_input.tried_term_family_ids == ["feedback.rag"]
    assert decision.gate_input.tried_query_fingerprints == ["fp-1"]
    assert decision.gate_input.min_seed_count == 2
    assert decision.gate_input.max_negative_support_rate == 0.4
    assert decision.gate_input.policy_version == "prf-policy-v1"
    assert decision.accepted_expression is not None
    assert decision.accepted_expression.canonical_expression == "LangGraph"


def test_build_prf_policy_decision_rejects_when_seed_count_is_insufficient() -> None:
    decision = build_prf_policy_decision(
        PRFGateInput(
            round_no=2,
            seed_resume_ids=["seed-1"],
            seed_count=1,
            negative_resume_ids=[],
            candidate_expressions=[_expression("LangGraph", positive_seed_support_count=1)],
            candidate_expression_count=1,
            tried_term_family_ids=[],
            tried_query_fingerprints=[],
            min_seed_count=2,
            max_negative_support_rate=0.4,
            policy_version="prf-policy-v1",
        )
    )

    assert decision.attempted is True
    assert decision.gate_passed is False
    assert decision.reject_reasons == ["insufficient_high_quality_seeds"]
    assert decision.accepted_expression is None


def test_build_prf_policy_decision_rejects_candidate_with_insufficient_positive_seed_support() -> None:
    decision = build_prf_policy_decision(
        PRFGateInput(
            round_no=2,
            seed_resume_ids=["seed-1", "seed-2"],
            seed_count=2,
            negative_resume_ids=[],
            candidate_expressions=[_expression("Agent Skills", positive_seed_support_count=1)],
            candidate_expression_count=1,
            tried_term_family_ids=[],
            tried_query_fingerprints=[],
            min_seed_count=2,
            max_negative_support_rate=0.4,
            policy_version="prf-policy-v1",
        )
    )

    assert decision.gate_passed is False
    assert decision.accepted_expression is None
    assert decision.reject_reasons == ["no_safe_prf_expression"]
    assert decision.candidate_expressions[0].reject_reasons == ["insufficient_seed_support"]


def test_build_prf_policy_decision_marks_company_and_tried_family_rejections_on_candidates() -> None:
    decision = build_prf_policy_decision(
        PRFGateInput(
            round_no=2,
            seed_resume_ids=["seed-1", "seed-2"],
            seed_count=2,
            negative_resume_ids=[],
            candidate_expressions=[
                _expression(
                    "ByteDance",
                    candidate_term_type="company_entity",
                    reject_reasons=["company_entity"],
                ),
                _expression("Databricks"),
            ],
            candidate_expression_count=2,
            tried_term_family_ids=["feedback.databricks"],
            tried_query_fingerprints=["fp-1"],
            min_seed_count=2,
            max_negative_support_rate=0.4,
            policy_version="prf-policy-v1",
        )
    )

    assert decision.gate_passed is False
    assert decision.reject_reasons == ["no_safe_prf_expression"]
    assert decision.candidate_expressions[0].reject_reasons == ["company_entity_rejected"]
    assert decision.candidate_expressions[1].reject_reasons == ["existing_or_tried_family"]


def test_build_prf_policy_decision_rejects_candidate_when_negative_support_is_too_high() -> None:
    decision = build_prf_policy_decision(
        PRFGateInput(
            round_no=2,
            seed_resume_ids=["seed-1", "seed-2"],
            seed_count=2,
            negative_resume_ids=["neg-1", "neg-2"],
            candidate_expressions=[
                _expression(
                    "LangGraph",
                    positive_seed_support_count=2,
                    negative_support_count=1,
                    not_fit_support_rate=0.5,
                ),
            ],
            candidate_expression_count=1,
            tried_term_family_ids=[],
            tried_query_fingerprints=[],
            min_seed_count=2,
            max_negative_support_rate=0.4,
            policy_version="prf-policy-v1",
        )
    )

    assert decision.gate_passed is False
    assert decision.reject_reasons == ["no_safe_prf_expression"]
    assert decision.candidate_expressions[0].reject_reasons == ["negative_support_too_high"]


def test_build_feedback_decision_picks_one_supported_novel_term() -> None:
    seed_resumes = [
        _scored_candidate(
            "seed-1",
            overall_score=90,
            must_have_match_score=82,
            evidence=["LangGraph", "RAG"],
        ),
        _scored_candidate(
            "seed-2",
            overall_score=86,
            must_have_match_score=79,
            evidence=["LangGraph", "RAG"],
        ),
    ]
    negative_resumes: list[ScoredCandidate] = []
    existing_terms = [
        _query_term("AI Agent", source="job_title", category="role_anchor", retrieval_role="role_anchor", family="role.aiagent"),
        _query_term("RAG", source="jd", category="domain", retrieval_role="domain_context", family="feedback.rag"),
    ]
    sent_query_terms = ["RAG"]

    decision = build_feedback_decision(
        seed_resumes=seed_resumes,
        negative_resumes=negative_resumes,
        existing_terms=existing_terms,
        sent_query_terms=sent_query_terms,
        round_no=4,
    )

    expected = QueryTermCandidate(
        term="LangGraph",
        source="candidate_feedback",
        category="expansion",
        priority=1,
        evidence="Supported by 2 seed resumes: seed-1, seed-2.",
        first_added_round=4,
        active=True,
        retrieval_role="core_skill",
        queryability="admitted",
        family="feedback.langgraph",
    )

    assert decision.skipped_reason is None
    assert decision.seed_resume_ids == ["seed-1", "seed-2"]
    assert decision.accepted_term == expected
    assert [item.term for item in decision.accepted_candidates] == ["LangGraph"]
    assert [item.term for item in decision.rejected_terms] == ["RAG"]
    assert decision.forced_query_terms == ["AI Agent", "LangGraph"]


def test_build_feedback_decision_ignores_seed_negative_fields() -> None:
    decision = build_feedback_decision(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["LangGraph"], negative_signals=["Missing Kubernetes"]),
            _scored_candidate("seed-2", evidence=["LangGraph"], weaknesses=["Missing Kubernetes"]),
        ],
        negative_resumes=[],
        existing_terms=[
            _query_term("AI Agent", source="job_title", category="role_anchor", retrieval_role="role_anchor", family="role.aiagent")
        ],
        sent_query_terms=[],
        round_no=4,
    )

    assert decision.accepted_term is not None
    assert decision.accepted_term.term == "LangGraph"
    assert "Missing Kubernetes" not in {item.term for item in decision.candidate_terms}


def test_build_feedback_decision_does_not_let_tiny_negative_sample_suppress_seed_term() -> None:
    decision = build_feedback_decision(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["LangGraph"]),
            _scored_candidate("seed-2", evidence=["LangGraph"]),
        ],
        negative_resumes=[
            _scored_candidate("not-fit-1", fit_bucket="not_fit", evidence=["LangGraph"]),
        ],
        existing_terms=[
            _query_term("AI Agent", source="job_title", category="role_anchor", retrieval_role="role_anchor", family="role.aiagent")
        ],
        sent_query_terms=[],
        round_no=4,
    )

    assert decision.accepted_term is not None
    assert decision.accepted_term.term == "LangGraph"


def test_build_feedback_decision_prefers_clean_term_over_narrative_phrase() -> None:
    decision = build_feedback_decision(
        seed_resumes=[
            _scored_candidate("seed-1", reasoning_summary="Built LangGraph workflow orchestration with RAG.", evidence=["LangGraph"]),
            _scored_candidate("seed-2", reasoning_summary="Built LangGraph workflow orchestration with RAG.", evidence=["LangGraph"]),
        ],
        negative_resumes=[],
        existing_terms=[
            _query_term("AI Agent", source="job_title", category="role_anchor", retrieval_role="role_anchor", family="role.aiagent"),
            _query_term("RAG", family="feedback.rag"),
        ],
        sent_query_terms=["RAG"],
        round_no=4,
    )

    assert decision.accepted_term is not None
    assert decision.accepted_term.term == "LangGraph"
    assert not decision.accepted_term.term.startswith("Built ")


def test_build_feedback_decision_prefers_shaped_term_over_plain_english_phrase() -> None:
    decision = build_feedback_decision(
        seed_resumes=[
            _scored_candidate("seed-1", reasoning_summary="Delivered backend orchestration with Node.js.", evidence=["Node.js"]),
            _scored_candidate("seed-2", reasoning_summary="Delivered backend orchestration with Node.js.", evidence=["Node.js"]),
        ],
        negative_resumes=[],
        existing_terms=[
            _query_term("AI Agent", source="job_title", category="role_anchor", retrieval_role="role_anchor", family="role.aiagent"),
        ],
        sent_query_terms=[],
        round_no=4,
    )

    assert decision.accepted_term is not None
    assert decision.accepted_term.term == "Node.js"


def test_build_feedback_decision_uses_primary_role_anchor() -> None:
    decision = build_feedback_decision(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["LangGraph"]),
            _scored_candidate("seed-2", evidence=["LangGraph"]),
        ],
        negative_resumes=[],
        existing_terms=[
            _query_term(
                "AI Agent",
                source="job_title",
                category="role_anchor",
                retrieval_role="primary_role_anchor",
                family="role.aiagent",
            )
        ],
        sent_query_terms=[],
        round_no=4,
    )

    assert decision.accepted_term is not None
    assert decision.forced_query_terms == ["AI Agent", "LangGraph"]


def test_candidate_feedback_model_ranking_forbids_unknown_terms() -> None:
    ranking = CandidateFeedbackModelRanking(
        accepted_terms=["langgraph", "InventedTerm"],
        rejected_terms={"平台": "generic"},
        rationale="Lowercase langgraph was not copied exactly.",
    )
    terms = [
        FeedbackCandidateTerm(term="LangGraph", supporting_resume_ids=["r1", "r2"]),
        FeedbackCandidateTerm(term="平台", supporting_resume_ids=["r1", "r2"]),
    ]

    assert ranking.accepted_from(terms) == []


def test_candidate_feedback_model_steps_filters_model_output_exactly(monkeypatch) -> None:
    class FakeResult:
        output = CandidateFeedbackModelRanking(
            accepted_terms=["langgraph", "InventedTerm"],
            rejected_terms={},
            rationale="No term was copied exactly.",
        )

    class FakeAgent:
        prompt = ""

        async def run(self, prompt: str) -> FakeResult:
            self.prompt = prompt
            return FakeResult()

    fake_agent = FakeAgent()
    steps = CandidateFeedbackModelSteps(
        make_settings(),
        LoadedPrompt(name="candidate_feedback", path=Path("candidate_feedback.md"), content="feedback prompt", sha256="hash"),
    )
    monkeypatch.setattr(steps, "_agent", lambda: fake_agent)

    async def run_rank() -> CandidateFeedbackModelRanking:
        return await steps.rank_terms(
            role_title="AI Agent Engineer",
            must_have_capabilities=["Agent workflow orchestration"],
            existing_terms=["AI Agent"],
            candidates=[FeedbackCandidateTerm(term="LangGraph", supporting_resume_ids=["r1", "r2"])],
        )

    ranking = asyncio.run(run_rank())

    assert ranking.accepted_terms == []
    assert "candidate_terms" in fake_agent.prompt
    assert "accepted_terms must be copied exactly" in fake_agent.prompt


def test_candidate_feedback_model_steps_store_loaded_prompt() -> None:
    prompt = LoadedPrompt(
        name="candidate_feedback",
        path=Path("candidate_feedback.md"),
        content="feedback prompt",
        sha256="hash",
    )

    steps = CandidateFeedbackModelSteps(make_settings(), prompt)

    assert steps.prompt is prompt


def test_candidate_feedback_model_steps_use_loaded_prompt_content(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeAgent:
        def __class_getitem__(cls, item):  # noqa: ANN001, N805
            del item
            return cls

        def __init__(self, **kwargs):  # noqa: ANN003
            captured["system_prompt"] = kwargs["system_prompt"]

    monkeypatch.setattr("seektalent.candidate_feedback.model_steps.Agent", FakeAgent)
    monkeypatch.setattr("seektalent.candidate_feedback.model_steps.build_model", lambda model_id: object())
    monkeypatch.setattr("seektalent.candidate_feedback.model_steps.build_output_spec", lambda *args, **kwargs: object())
    monkeypatch.setattr("seektalent.candidate_feedback.model_steps.build_model_settings", lambda *args, **kwargs: {})

    prompt = LoadedPrompt(
        name="candidate_feedback",
        path=Path("candidate_feedback.md"),
        content="feedback system prompt",
        sha256="hash",
    )
    steps = CandidateFeedbackModelSteps(make_settings(), prompt)

    steps._agent()

    assert captured["system_prompt"] == "feedback system prompt"


def test_candidate_feedback_model_steps_use_resolved_stage_config(monkeypatch) -> None:
    captured: dict[str, object] = {}
    stage_config = ResolvedTextModelConfig(
        stage="candidate_feedback",
        protocol_family="openai_chat_completions_compatible",
        provider_label="bailian",
        endpoint_kind="bailian_openai_chat_completions",
        endpoint_region="beijing",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="test-key",
        model_id="deepseek-v4-flash",
        structured_output_mode="prompted_json",
        thinking_mode=False,
        reasoning_effort="off",
        openai_prompt_cache_enabled=False,
        openai_prompt_cache_retention=None,
    )

    class FakeAgent:
        def __class_getitem__(cls, item):  # noqa: ANN001, N805
            del item
            return cls

        def __init__(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)

    monkeypatch.setattr("seektalent.candidate_feedback.model_steps.Agent", FakeAgent)
    monkeypatch.setattr(
        "seektalent.candidate_feedback.model_steps.resolve_stage_model_config",
        lambda settings, *, stage: stage_config if stage == "candidate_feedback" else None,
    )
    monkeypatch.setattr("seektalent.candidate_feedback.model_steps.build_model", lambda config: ("model", config))
    monkeypatch.setattr(
        "seektalent.candidate_feedback.model_steps.build_output_spec",
        lambda config, model, output_type: ("output", config, model, output_type),
    )
    monkeypatch.setattr(
        "seektalent.candidate_feedback.model_steps.build_model_settings",
        lambda config: {"config": config},
    )

    prompt = LoadedPrompt(
        name="candidate_feedback",
        path=Path("candidate_feedback.md"),
        content="feedback system prompt",
        sha256="hash",
    )
    settings = make_settings()
    steps = CandidateFeedbackModelSteps(settings, prompt)

    steps._agent()

    assert captured["model"] == ("model", stage_config)
    assert captured["output_type"] == ("output", stage_config, ("model", stage_config), CandidateFeedbackModelRanking)
    assert captured["model_settings"] == {"config": stage_config}
