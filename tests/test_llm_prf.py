from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from pydantic_ai import NativeOutput, PromptedOutput, ToolOutput

import seektalent.candidate_feedback.llm_prf as llm_prf
from seektalent.candidate_feedback.llm_prf import (
    LLM_PRF_EXTRACTOR_VERSION,
    LLM_PRF_FAMILYING_VERSION,
    LLM_PRF_SCHEMA_VERSION,
    LLM_PRF_OUTPUT_RETRIES,
    LLM_PRF_TOP_N_CANDIDATE_CAP,
    LLMPRFCandidate,
    LLMPRFExtraction,
    LLMPRFSourceEvidenceRef,
    build_conservative_prf_family_id,
    build_llm_prf_artifact_refs,
    build_llm_prf_input,
    feedback_expressions_from_llm_grounding,
    ground_llm_prf_candidates,
    select_llm_prf_negative_resumes,
)
from seektalent.config import AppSettings
from seektalent.models import FitBucket, ScoredCandidate
from seektalent.prompting import LoadedPrompt
from seektalent.tracing import ProviderUsageSnapshot


def _scored_candidate(
    resume_id: str,
    *,
    fit_bucket: FitBucket = "fit",
    overall_score: int = 80,
    must_have_match_score: int = 70,
    risk_score: int = 20,
    evidence: list[str] | None = None,
    matched_must_haves: list[str] | None = None,
    matched_preferences: list[str] | None = None,
    strengths: list[str] | None = None,
) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        fit_bucket=fit_bucket,
        overall_score=overall_score,
        must_have_match_score=must_have_match_score,
        preferred_match_score=65,
        risk_score=risk_score,
        risk_flags=[],
        reasoning_summary="Seed summary.",
        evidence=evidence or [],
        confidence="high",
        matched_must_haves=matched_must_haves or [],
        missing_must_haves=[],
        matched_preferences=matched_preferences or [],
        negative_signals=[],
        strengths=strengths or [],
        weaknesses=[],
        source_round=1,
    )


def _candidate(
    surface: str,
    *,
    normalized_surface: str | None = None,
    resume_id: str = "seed-1",
    source_field: str = "evidence",
    source_text_index: int = 0,
    source_hash: str | None = None,
    candidate_term_type: str = "technical_phrase",
) -> LLMPRFCandidate:
    return LLMPRFCandidate(
        surface=surface,
        normalized_surface=normalized_surface or surface,
        candidate_term_type=candidate_term_type,
        source_evidence_refs=[
            LLMPRFSourceEvidenceRef(
                resume_id=resume_id,
                source_field=source_field,
                source_text_index=source_text_index,
                source_text_hash=source_hash or "",
            )
        ],
        source_resume_ids=[resume_id],
        linked_requirements=[],
        rationale="Grounded candidate.",
        risk_flags=[],
    )


def _extraction(*candidates: LLMPRFCandidate) -> LLMPRFExtraction:
    return LLMPRFExtraction(
        schema_version=LLM_PRF_SCHEMA_VERSION,
        extractor_version=LLM_PRF_EXTRACTOR_VERSION,
        candidates=list(candidates),
    )


def _source_hash(payload, source_id: str) -> str:
    resume_id, source_field, source_text_index = source_id.split("|")
    return next(
        item.source_text_hash
        for item in payload.source_texts
        if item.resume_id == resume_id and item.source_field == source_field and item.source_text_index == int(source_text_index)
    )


def _settings() -> AppSettings:
    return AppSettings(text_llm_api_key="unit-test-key")


def _prompt() -> LoadedPrompt:
    content = "Return json only."
    return LoadedPrompt(
        name="prf_probe_phrase_proposal",
        path=Path("prf_probe_phrase_proposal.md"),
        content=content,
        sha256=sha256(content.encode("utf-8")).hexdigest(),
    )


def _payload_for_extractor():
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["Built Flink CDC pipelines."]),
            _scored_candidate("seed-2", evidence=["Owned Flink CDC ingestion."]),
        ],
        negative_resumes=[],
    )
    assert payload is not None
    return payload


def _agent_model_client_max_retries(agent: Any) -> int:
    model = getattr(agent, "_model")
    return getattr(model.client, "max_retries")


def test_llm_prf_extraction_enforces_top_n_candidate_cap() -> None:
    with pytest.raises(ValidationError):
        LLMPRFExtraction(candidates=[_candidate(f"term-{index}") for index in range(LLM_PRF_TOP_N_CANDIDATE_CAP + 1)])


@pytest.mark.parametrize("surface", ["", " ", "\t"])
def test_llm_prf_candidate_rejects_empty_surfaces_before_grounding(surface: str) -> None:
    with pytest.raises(ValidationError):
        LLMPRFCandidate(
            surface=surface,
            normalized_surface=surface,
            source_evidence_refs=[],
            source_resume_ids=[],
            linked_requirements=[],
            rationale="Empty surfaces are invalid.",
            risk_flags=[],
        )


def test_build_llm_prf_input_freezes_source_text_hashes() -> None:
    payload = build_llm_prf_input(
        round_no=2,
        role_title="Data Engineer",
        role_summary="Build realtime data pipelines.",
        must_have_capabilities=["Flink"],
        retrieval_query_terms=["data engineer"],
        existing_query_terms=["Kafka"],
        sent_query_terms=["Flink"],
        tried_term_family_ids=["feedback.kafka"],
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["Built Flink CDC pipelines."], strengths=["Flink CDC"]),
            _scored_candidate("seed-2", matched_must_haves=["Owned Flink CDC ingestion."]),
        ],
        negative_resumes=[],
    )

    assert payload is not None
    assert payload.round_no == 2
    assert payload.role_title == "Data Engineer"
    assert payload.role_summary == "Build realtime data pipelines."
    assert payload.must_have_capabilities == ["Flink"]
    assert payload.retrieval_query_terms == ["data engineer"]
    assert payload.existing_query_terms == ["Kafka"]
    assert payload.sent_query_terms == ["Flink"]
    assert payload.tried_term_family_ids == ["feedback.kafka"]
    assert [(item.resume_id, item.source_field, item.source_text_index) for item in payload.source_texts] == [
        ("seed-1", "evidence", 0),
        ("seed-1", "strengths", 0),
        ("seed-2", "matched_must_haves", 0),
    ]
    assert payload.source_texts[0].source_text_raw == "Built Flink CDC pipelines."
    assert payload.source_texts[0].source_text_hash == sha256("Built Flink CDC pipelines.".encode()).hexdigest()
    assert payload.source_texts[1].source_kind == "hint_only"
    assert payload.source_texts[2].source_kind == "grounding_eligible"


def test_build_llm_prf_input_returns_none_with_fewer_than_two_seed_resumes() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[_scored_candidate("seed-1", evidence=["Flink CDC"])],
        negative_resumes=[],
    )

    assert payload is None


def test_ground_llm_prf_candidates_uses_exact_raw_substring_offsets() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["Built Flink CDC pipelines."]),
            _scored_candidate("seed-2", evidence=["Built Flink CDC ingestion."]),
        ],
        negative_resumes=[],
    )
    assert payload is not None

    grounding = ground_llm_prf_candidates(
        payload,
        _extraction(
            _candidate("Flink CDC", source_hash=_source_hash(payload, "seed-1|evidence|0")),
            _candidate("Flink CDC", resume_id="seed-2", source_hash=_source_hash(payload, "seed-2|evidence|0")),
        ),
    )

    assert grounding.familying_version == LLM_PRF_FAMILYING_VERSION
    assert set(type(grounding.records[0]).model_fields) == {
        "surface",
        "normalized_surface",
        "advisory_candidate_term_type",
        "accepted",
        "reject_reasons",
        "resume_id",
        "source_field",
        "source_text_index",
        "source_text_hash",
        "start_char",
        "end_char",
        "raw_surface",
    }
    assert grounding.records[0].accepted is True
    assert grounding.records[0].resume_id == "seed-1"
    assert grounding.records[0].source_text_hash == _source_hash(payload, "seed-1|evidence|0")
    assert grounding.records[0].start_char == len("Built ")
    assert grounding.records[0].end_char == len("Built Flink CDC")
    assert grounding.records[0].raw_surface == "Flink CDC"
    assert grounding.records[0].reject_reasons == []


def test_ground_llm_prf_candidates_recovers_raw_offsets_after_nfkc_match() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["Built Ｆｌｉｎｋ CDC pipelines."]),
            _scored_candidate("seed-2", evidence=["Built Flink CDC ingestion."]),
        ],
        negative_resumes=[],
    )
    assert payload is not None

    grounding = ground_llm_prf_candidates(
        payload,
        _extraction(_candidate("Flink CDC", source_hash=_source_hash(payload, "seed-1|evidence|0"))),
    )

    record = grounding.records[0]
    assert record.raw_surface == "Ｆｌｉｎｋ CDC"
    assert payload.source_texts[0].source_text_raw[record.start_char : record.end_char] == "Ｆｌｉｎｋ CDC"
    assert record.reject_reasons == []


def test_ground_llm_prf_candidates_rejects_unsafe_substrings() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["Used JavaScript, React Native, and 阿里云."]),
            _scored_candidate("seed-2", evidence=["Used JavaScript, React Native, and 阿里云."]),
        ],
        negative_resumes=[],
    )
    assert payload is not None

    grounding = ground_llm_prf_candidates(
        payload,
        _extraction(
            _candidate("Java", source_hash=_source_hash(payload, "seed-1|evidence|0")),
            _candidate("React", source_hash=_source_hash(payload, "seed-1|evidence|0")),
            _candidate("阿里", source_hash=_source_hash(payload, "seed-1|evidence|0")),
        ),
    )

    assert [record.reject_reasons for record in grounding.records] == [
        ["unsafe_substring_match"],
        ["unsafe_substring_match"],
        ["unsafe_substring_match"],
    ]


def test_family_support_counts_separator_and_camelcase_variants_as_one_family() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["Built Flink CDC pipelines."]),
            _scored_candidate("seed-2", matched_must_haves=["Owned FlinkCDC ingestion."]),
        ],
        negative_resumes=[],
    )
    assert payload is not None

    grounding = ground_llm_prf_candidates(
        payload,
        _extraction(
            _candidate("Flink CDC", source_hash=_source_hash(payload, "seed-1|evidence|0")),
            _candidate(
                "FlinkCDC",
                resume_id="seed-2",
                source_field="matched_must_haves",
                source_hash=_source_hash(payload, "seed-2|matched_must_haves|0"),
            ),
        ),
    )
    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities=set(),
        tried_term_family_ids=set(),
    )

    assert len(expressions) == 1
    assert expressions[0].term_family_id == "feedback.flinkcdc"
    assert expressions[0].positive_seed_support_count == 2
    assert set(expressions[0].surface_forms) == {"Flink CDC", "FlinkCDC"}


def test_llm_candidate_term_type_is_advisory_and_runtime_reclassifies_company_entity() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["Built OpenAI integrations."]),
            _scored_candidate("seed-2", evidence=["Scaled OpenAI API usage."]),
        ],
        negative_resumes=[],
    )
    assert payload is not None
    grounding = ground_llm_prf_candidates(
        payload,
        _extraction(
            _candidate(
                "OpenAI",
                source_hash=_source_hash(payload, "seed-1|evidence|0"),
                candidate_term_type="skill",
            )
        ),
    )

    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities={"OpenAI"},
        tried_term_family_ids=set(),
    )

    assert expressions[0].candidate_term_type == "company_entity"
    assert "company_entity_rejected" in expressions[0].reject_reasons


def test_build_llm_prf_artifact_refs_uses_centralized_round_refs() -> None:
    refs = build_llm_prf_artifact_refs(round_no=2)

    assert set(type(refs).model_fields) == {
        "input_artifact_ref",
        "call_artifact_ref",
        "candidates_artifact_ref",
        "grounding_artifact_ref",
        "policy_decision_artifact_ref",
    }
    assert refs.input_artifact_ref == "round.02.retrieval.llm_prf_input"
    assert refs.call_artifact_ref == "round.02.retrieval.llm_prf_call"
    assert refs.candidates_artifact_ref == "round.02.retrieval.llm_prf_candidates"
    assert refs.grounding_artifact_ref == "round.02.retrieval.llm_prf_grounding"
    assert refs.policy_decision_artifact_ref == "round.02.retrieval.prf_policy_decision"


def test_llm_prf_extractor_builds_prompted_agent_with_zero_temperature_and_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    fake_model = object()

    def fake_agent(**kwargs):
        captured.update(kwargs)
        return object()

    def fake_build_model(config, **kwargs):  # noqa: ANN001, ARG001
        captured["build_model_kwargs"] = kwargs
        return fake_model

    monkeypatch.setattr(
        llm_prf,
        "build_model",
        fake_build_model,
    )
    monkeypatch.setattr(llm_prf, "Agent", fake_agent)

    llm_prf.LLMPRFExtractor(_settings(), _prompt())._build_agent()

    assert captured["build_model_kwargs"] == {"provider_max_retries": 0}
    assert captured["model"] is fake_model
    assert captured["system_prompt"] == "Return json only."
    assert captured["retries"] == 0
    assert captured["output_retries"] == LLM_PRF_OUTPUT_RETRIES == 2
    assert captured["model_settings"]["temperature"] == 0
    assert captured["model_settings"]["max_tokens"] == 2048


def test_llm_prf_output_spec_is_prompted_output_not_native_or_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(llm_prf, "build_model", lambda config, **kwargs: object())
    monkeypatch.setattr(llm_prf, "Agent", lambda **kwargs: captured.update(kwargs) or object())

    llm_prf.LLMPRFExtractor(_settings(), _prompt())._build_agent()

    output_spec = captured["output_type"]
    assert isinstance(output_spec, PromptedOutput)
    assert not isinstance(output_spec, NativeOutput | ToolOutput)
    assert output_spec.outputs is LLMPRFExtraction


def test_render_llm_prf_prompt_uses_compact_json_and_names_json() -> None:
    prompt = llm_prf.render_llm_prf_prompt(_payload_for_extractor())

    assert "json" in prompt.casefold()
    assert '"schema_version":"llm-prf-v1"' in prompt
    assert '"source_text_raw":"Built Flink CDC pipelines."' in prompt


def test_llm_prf_extractor_provider_failure_calls_model_once_without_internal_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class FakeAgent:
        async def run(self, user_prompt: str):  # noqa: ARG002
            nonlocal calls
            calls += 1
            raise RuntimeError("provider boom")

    extractor = llm_prf.LLMPRFExtractor(_settings(), _prompt())
    monkeypatch.setattr(extractor, "_build_agent", lambda: FakeAgent())

    with pytest.raises(RuntimeError, match="provider boom"):
        asyncio.run(extractor.propose(_payload_for_extractor()))

    assert calls == 1


def test_llm_prf_schema_retry_budget_is_agent_construction_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pydantic-AI local retry behavior for prompted JSON depends on model internals,
    # so this pins the retry budget at the Agent boundary where this extractor owns it.
    captured: dict[str, Any] = {}
    monkeypatch.setattr(llm_prf, "build_model", lambda config, **kwargs: object())
    monkeypatch.setattr(llm_prf, "Agent", lambda **kwargs: captured.update(kwargs) or object())

    llm_prf.LLMPRFExtractor(_settings(), _prompt())._build_agent()

    assert captured["retries"] == 0
    assert captured["output_retries"] == 2


def test_llm_prf_extractor_disables_provider_sdk_retries() -> None:
    agent = llm_prf.LLMPRFExtractor(_settings(), _prompt())._build_agent()

    assert _agent_model_client_max_retries(agent) == 0


def test_llm_prf_extractor_records_provider_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeUsage:
        input_tokens = 11
        output_tokens = 7
        cache_read_tokens = 3
        cache_write_tokens = 2
        details = {"prompt_tokens": 11, "ignored": True}

    class FakeResult:
        output = _extraction(_candidate("Flink CDC"))

        def usage(self):
            return FakeUsage()

    class FakeAgent:
        async def run(self, user_prompt: str):  # noqa: ARG002
            return FakeResult()

    extractor = llm_prf.LLMPRFExtractor(_settings(), _prompt())
    monkeypatch.setattr(extractor, "_build_agent", lambda: FakeAgent())

    result = asyncio.run(extractor.propose(_payload_for_extractor()))

    assert result.candidates[0].surface == "Flink CDC"
    assert extractor.last_provider_usage is not None
    assert extractor.last_provider_usage.model_dump(mode="json") == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
        "cache_read_tokens": 3,
        "cache_write_tokens": 2,
        "details": {"prompt_tokens": 11},
    }


def test_llm_prf_success_call_artifact_records_expected_fields() -> None:
    payload = _payload_for_extractor()
    extraction = _extraction(_candidate("Flink CDC"))
    usage = ProviderUsageSnapshot(input_tokens=10, output_tokens=5, total_tokens=15)

    artifact = llm_prf.build_llm_prf_success_call_artifact(
        settings=_settings(),
        payload=payload,
        user_prompt_text=llm_prf.render_llm_prf_prompt(payload),
        extraction=extraction,
        started_at="2026-05-04T00:00:00+00:00",
        latency_ms=123,
        round_no=2,
        provider_usage=usage,
    )

    assert artifact["stage"] == "prf_probe_phrase_proposal"
    assert artifact["call_id"] == "llm-prf-02"
    assert artifact["model_id"] == "deepseek-v4-flash"
    assert artifact["prompt_name"] == "prf_probe_phrase_proposal"
    assert artifact["status"] == "succeeded"
    assert artifact["retries"] == 0
    assert artifact["output_retries"] == 2
    assert artifact["validator_retry_count"] == 0
    assert artifact["structured_output"] == extraction.model_dump(mode="json")
    assert artifact["provider_usage"] == usage.model_dump(mode="json")


def test_llm_prf_failure_call_artifact_redacts_api_key_and_records_expected_fields() -> None:
    payload = _payload_for_extractor()

    artifact = llm_prf.build_llm_prf_failure_call_artifact(
        settings=_settings(),
        payload=payload,
        user_prompt_text=llm_prf.render_llm_prf_prompt(payload),
        started_at="2026-05-04T00:00:00+00:00",
        latency_ms=17,
        round_no=3,
        failure_kind="provider_error",
        error_message="provider rejected unit-test-key",
    )

    assert artifact["stage"] == "prf_probe_phrase_proposal"
    assert artifact["call_id"] == "llm-prf-03"
    assert artifact["status"] == "failed"
    assert artifact["structured_output"] is None
    assert artifact["failure_kind"] == "provider_error"
    assert artifact["validator_retry_count"] == 0
    assert artifact["error_message"] == "provider rejected [redacted]"
    assert "unit-test-key" not in str(artifact)


def test_advisory_platform_label_without_known_company_is_still_ambiguous_for_tencent_cloud() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["使用腾讯云部署服务。"]),
            _scored_candidate("seed-2", evidence=["腾讯云上建设数据链路。"]),
        ],
        negative_resumes=[],
    )
    assert payload is not None
    grounding = ground_llm_prf_candidates(
        payload,
        _extraction(
            _candidate(
                "腾讯云",
                source_hash=_source_hash(payload, "seed-1|evidence|0"),
                candidate_term_type="product_or_platform",
            )
        ),
    )

    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities=set(),
        tried_term_family_ids=set(),
    )

    assert expressions[0].candidate_term_type == "company_entity"
    assert "ambiguous_company_or_product_entity" in expressions[0].reject_reasons


def test_hash_mismatch_rejects_with_source_hash_mismatch() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["Built Flink CDC pipelines."]),
            _scored_candidate("seed-2", evidence=["Built Flink CDC ingestion."]),
        ],
        negative_resumes=[],
    )
    assert payload is not None

    grounding = ground_llm_prf_candidates(payload, _extraction(_candidate("Flink CDC", source_hash="wrong")))

    assert grounding.records[0].accepted is False
    assert grounding.records[0].reject_reasons == ["source_hash_mismatch"]


def test_unknown_source_reference_rejects_with_source_reference_not_found() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["Built Flink CDC pipelines."]),
            _scored_candidate("seed-2", evidence=["Built Flink CDC ingestion."]),
        ],
        negative_resumes=[],
    )
    assert payload is not None

    grounding = ground_llm_prf_candidates(
        payload,
        _extraction(_candidate("Flink CDC", resume_id="missing", source_hash="wrong")),
    )

    assert grounding.records[0].accepted is False
    assert grounding.records[0].reject_reasons == ["source_reference_not_found"]


def test_strengths_only_support_tracks_field_hits_without_positive_seed_support() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", strengths=["Flink CDC"]),
            _scored_candidate("seed-2", strengths=["Flink CDC"]),
        ],
        negative_resumes=[],
    )
    assert payload is not None
    grounding = ground_llm_prf_candidates(
        payload,
        _extraction(
            _candidate("Flink CDC", source_field="strengths", source_hash=_source_hash(payload, "seed-1|strengths|0")),
            _candidate("Flink CDC", resume_id="seed-2", source_field="strengths", source_hash=_source_hash(payload, "seed-2|strengths|0")),
        ),
    )

    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities=set(),
        tried_term_family_ids=set(),
    )

    assert expressions[0].field_hits == {"strengths": 2}
    assert expressions[0].positive_seed_support_count == 0
    assert expressions[0].source_seed_resume_ids == []


def test_positive_support_requires_grounding_eligible_hit_per_seed_resume() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", strengths=["Flink CDC"]),
            _scored_candidate("seed-2", evidence=["Flink CDC"]),
        ],
        negative_resumes=[],
    )
    assert payload is not None
    grounding = ground_llm_prf_candidates(
        payload,
        _extraction(
            _candidate("Flink CDC", source_field="strengths", source_hash=_source_hash(payload, "seed-1|strengths|0")),
            _candidate("Flink CDC", resume_id="seed-2", source_hash=_source_hash(payload, "seed-2|evidence|0")),
        ),
    )

    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities=set(),
        tried_term_family_ids=set(),
    )

    assert expressions[0].source_seed_resume_ids == ["seed-2"]
    assert expressions[0].positive_seed_support_count == 1
    assert expressions[0].field_hits == {"strengths": 1, "evidence": 1}


def test_negative_support_is_deterministic_scan_over_negative_source_texts() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["Built Flink CDC pipelines."]),
            _scored_candidate("seed-2", evidence=["Owned Flink CDC ingestion."]),
        ],
        negative_resumes=[
            _scored_candidate("neg-1", fit_bucket="not_fit", evidence=["Built FlinkCDC pipelines."]),
            _scored_candidate("neg-2", fit_bucket="not_fit", matched_preferences=["No CDC experience."]),
            _scored_candidate("neg-3", fit_bucket="not_fit", evidence=["Kafka only."]),
        ],
    )
    assert payload is not None
    grounding = ground_llm_prf_candidates(
        payload,
        _extraction(_candidate("Flink CDC", source_hash=_source_hash(payload, "seed-1|evidence|0"))),
    )

    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities=set(),
        tried_term_family_ids=set(),
    )

    assert expressions[0].negative_support_count == 1
    assert expressions[0].not_fit_support_rate == 1 / 3


def test_negative_support_family_scan_does_not_match_inside_larger_tokens() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["Built Go services."]),
            _scored_candidate("seed-2", evidence=["Owned Go APIs."]),
        ],
        negative_resumes=[
            _scored_candidate("neg-1", fit_bucket="not_fit", evidence=["Used MongoDB heavily."]),
            _scored_candidate("neg-2", fit_bucket="not_fit", evidence=["Built Python services."]),
        ],
    )
    assert payload is not None
    grounding = ground_llm_prf_candidates(
        payload,
        _extraction(_candidate("Go", source_hash=_source_hash(payload, "seed-1|evidence|0"))),
    )

    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities=set(),
        tried_term_family_ids=set(),
    )

    assert expressions[0].negative_support_count == 0
    assert expressions[0].not_fit_support_rate == 0.0


def test_grounding_does_not_trust_llm_normalized_surface_for_accepted_identity() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["Built Go services."]),
            _scored_candidate("seed-2", evidence=["Owned Go APIs."]),
        ],
        negative_resumes=[],
    )
    assert payload is not None
    grounding = ground_llm_prf_candidates(
        payload,
        _extraction(_candidate("Go", normalized_surface="Python", source_hash=_source_hash(payload, "seed-1|evidence|0"))),
    )

    assert grounding.records[0].accepted is True
    assert grounding.records[0].normalized_surface == "Go"
    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities=set(),
        tried_term_family_ids=set(),
    )

    assert expressions[0].canonical_expression == "Go"
    assert expressions[0].term_family_id == "feedback.go"


def test_select_llm_prf_negative_resumes_prefers_non_fit_or_high_risk_by_risk_then_score() -> None:
    selected = select_llm_prf_negative_resumes(
        [
            _scored_candidate("fit-safe", overall_score=99, risk_score=10),
            _scored_candidate("fit-risk", overall_score=95, risk_score=70),
            _scored_candidate("not-fit-low", fit_bucket="not_fit", overall_score=20, risk_score=10),
            _scored_candidate("not-fit-high", fit_bucket="not_fit", overall_score=50, risk_score=90),
            _scored_candidate("not-fit-high-lower-score", fit_bucket="not_fit", overall_score=40, risk_score=90),
        ],
        limit=3,
    )

    assert [item.resume_id for item in selected] == ["not-fit-high-lower-score", "not-fit-high", "fit-risk"]


def test_tried_family_conflicts_use_conservative_prf_family_id() -> None:
    assert LLM_PRF_FAMILYING_VERSION == "llm-prf-conservative-surface-family-v1"
    assert build_conservative_prf_family_id("Flink CDC") == "feedback.flinkcdc"
    assert build_conservative_prf_family_id("FlinkCDC") == "feedback.flinkcdc"

    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["Built Flink CDC pipelines."]),
            _scored_candidate("seed-2", evidence=["Owned Flink CDC ingestion."]),
        ],
        negative_resumes=[],
    )
    assert payload is not None
    grounding = ground_llm_prf_candidates(
        payload,
        _extraction(_candidate("Flink CDC", source_hash=_source_hash(payload, "seed-1|evidence|0"))),
    )

    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities=set(),
        tried_term_family_ids={build_conservative_prf_family_id("FlinkCDC")},
    )

    assert expressions[0].term_family_id == "feedback.flinkcdc"
    assert "existing_or_tried_family" in expressions[0].reject_reasons
