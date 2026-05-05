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
    LLMPRFSourceText,
    build_conservative_prf_family_id,
    build_llm_prf_artifact_refs,
    build_llm_prf_input,
    build_llm_prf_source_text_id,
    feedback_expressions_from_llm_grounding,
    ground_llm_prf_candidates,
    select_llm_prf_negative_resumes,
    text_sha256,
)
from seektalent.config import AppSettings
from seektalent.models import FitBucket, NormalizedExperience, NormalizedResume, ScoredCandidate
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
    source_section: str = "scorecard_evidence",
    source_text_id: str = "source-hash",
    source_text_index: int = 0,
    source_hash: str = "text-hash",
    candidate_term_type: str = "technical_phrase",
    linked_requirements: list[str] | None = None,
    rationale: str = "Grounded candidate.",
    risk_flags: list[str] | None = None,
) -> LLMPRFCandidate:
    return LLMPRFCandidate(
        surface=surface,
        normalized_surface=normalized_surface or surface,
        candidate_term_type=candidate_term_type,
        source_evidence_refs=[
            LLMPRFSourceEvidenceRef(
                resume_id=resume_id,
                source_section=source_section,
                source_text_id=source_text_id,
                source_text_index=source_text_index,
                source_text_hash=source_hash,
            )
        ],
        source_resume_ids=[resume_id],
        linked_requirements=linked_requirements or [],
        rationale=rationale,
        risk_flags=risk_flags or [],
    )


def _extraction(*candidates: LLMPRFCandidate) -> LLMPRFExtraction:
    return LLMPRFExtraction(
        schema_version=LLM_PRF_SCHEMA_VERSION,
        extractor_version=LLM_PRF_EXTRACTOR_VERSION,
        candidates=list(candidates),
    )


def _source_ref_kwargs(payload, source_id: str) -> dict[str, str | int]:
    resume_id, source_section, source_text_index = source_id.split("|")
    source = next(
        item
        for item in payload.source_texts
        if item.resume_id == resume_id
        and item.source_section == source_section
        and item.source_text_index == int(source_text_index)
    )
    return {
        "resume_id": source.resume_id,
        "source_section": source.source_section,
        "source_text_id": source.source_text_id,
        "source_text_index": source.source_text_index,
        "source_hash": source.source_text_hash,
    }


def _source_hash(payload, source_id: str) -> str:
    resume_id, source_section, source_text_index = source_id.split("|")
    return next(
        item.source_text_hash
        for item in payload.source_texts
        if item.resume_id == resume_id
        and item.source_section == source_section
        and item.source_text_index == int(source_text_index)
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


def test_llm_prf_source_text_uses_source_section_and_stable_id() -> None:
    raw = "Built LangGraph workflows for multi-agent retrieval."
    source_id = build_llm_prf_source_text_id(
        resume_id="seed-1",
        source_section="recent_experience_summary",
        original_field_path="recent_experiences[0].summary",
        normalized_text=raw,
        preparation_version="llm-prf-source-prep-v1",
    )

    source = LLMPRFSourceText(
        resume_id="seed-1",
        source_section="recent_experience_summary",
        source_text_id=source_id,
        source_text_index=0,
        source_text_raw=raw,
        source_text_hash=text_sha256(raw),
        original_field_path="recent_experiences[0].summary",
        source_kind="grounding_eligible",
        support_eligible=True,
        hint_only=False,
        preparation_version="llm-prf-source-prep-v1",
        dedupe_key="langgraph workflows for multi-agent retrieval",
        rank_reason="matched:LangGraph,multi-agent",
    )

    assert source.source_id == source_id
    assert source.source_section == "recent_experience_summary"
    assert source.support_eligible is True
    assert source.hint_only is False


def test_llm_prf_source_ref_resolves_by_source_text_id() -> None:
    ref = LLMPRFSourceEvidenceRef(
        resume_id="seed-1",
        source_section="skill",
        source_text_id="source-hash",
        source_text_index=3,
        source_text_hash="text-hash",
    )

    assert ref.source_text_id == "source-hash"
    assert ref.source_section == "skill"


@pytest.mark.parametrize("field_name", ["source_text_id", "source_text_hash"])
def test_llm_prf_source_ref_rejects_empty_ref_identity(field_name: str) -> None:
    kwargs = {
        "resume_id": "seed-1",
        "source_section": "skill",
        "source_text_id": "source-hash",
        "source_text_index": 3,
        "source_text_hash": "text-hash",
    }
    kwargs[field_name] = ""

    with pytest.raises(ValidationError):
        LLMPRFSourceEvidenceRef(**kwargs)


@pytest.mark.parametrize(
    ("source_kind", "support_eligible", "hint_only"),
    [
        ("grounding_eligible", False, False),
        ("grounding_eligible", True, True),
        ("hint_only", True, True),
        ("hint_only", False, False),
    ],
)
def test_llm_prf_source_text_rejects_conflicting_support_flags(
    source_kind: str,
    support_eligible: bool,
    hint_only: bool,
) -> None:
    with pytest.raises(ValidationError):
        LLMPRFSourceText(
            resume_id="seed-1",
            source_section="skill",
            source_text_id="source-hash",
            source_text_index=0,
            source_text_raw="LangGraph",
            source_text_hash="text-hash",
            original_field_path="skills[0]",
            source_kind=source_kind,
            support_eligible=support_eligible,
            hint_only=hint_only,
            dedupe_key="langgraph",
        )


def _normalized_resume(resume_id: str, *, raw_text_excerpt: str, key_achievements: list[str] | None = None) -> NormalizedResume:
    return NormalizedResume(
        resume_id=resume_id,
        dedup_key=resume_id,
        headline="AI Engineer",
        current_title="AI Engineer",
        current_company="Example Co",
        years_of_experience=5,
        locations=["上海"],
        education_summary="Computer Science",
        skills=["Python", "LangGraph"],
        industry_tags=[],
        language_tags=[],
        recent_experiences=[
            NormalizedExperience(
                title="AI Engineer",
                company="Example Co",
                duration="2023-2026",
                summary=raw_text_excerpt,
            )
        ],
        key_achievements=key_achievements or [raw_text_excerpt],
        raw_text_excerpt=raw_text_excerpt,
        completeness_score=90,
        missing_fields=[],
        normalization_notes=[],
    )


def _agent_model_client_max_retries(agent: Any) -> int:
    model = getattr(agent, "_model")
    return getattr(model.client, "max_retries")


def test_llm_prf_extraction_enforces_top_n_candidate_cap() -> None:
    with pytest.raises(ValidationError):
        LLMPRFExtraction(candidates=[_candidate(f"term-{index}") for index in range(LLM_PRF_TOP_N_CANDIDATE_CAP + 1)])


def test_llm_prf_extraction_rejects_more_than_four_candidates() -> None:
    with pytest.raises(ValidationError):
        LLMPRFExtraction(candidates=[_candidate(f"term-{index}") for index in range(5)])


def test_llm_prf_candidate_schema_keeps_verbose_fields_bounded() -> None:
    candidate = _candidate(
        "Flink CDC",
        rationale="x" * 120,
        linked_requirements=["streaming", "cdc", "data integration", "ingestion"],
        risk_flags=["company", "location", "generic", "title_only"],
    )

    assert candidate.rationale == "x" * 120
    with pytest.raises(ValidationError):
        _candidate("Flink CDC", rationale="x" * 121)
    with pytest.raises(ValidationError):
        _candidate("Flink CDC", linked_requirements=["one", "two", "three", "four", "five"])
    with pytest.raises(ValidationError):
        _candidate("Flink CDC", risk_flags=["one", "two", "three", "four", "five"])


def test_llm_prf_system_prompt_hard_limits_output_volume() -> None:
    prompt = Path("src/seektalent/prompts/prf_probe_phrase_proposal.md").read_text()

    assert "Return at most 4 candidates" in prompt
    assert "at least two distinct fit seed resumes" in prompt
    assert "rationale <= 80 chars" in prompt


def test_llm_prf_prompt_requires_extractive_surfaces() -> None:
    system_prompt = Path("src/seektalent/prompts/prf_probe_phrase_proposal.md").read_text()
    user_prompt = llm_prf.render_llm_prf_prompt(_payload_for_extractor())

    assert "surface must be copied exactly" in system_prompt
    assert "surface must be copied exactly" in user_prompt
    assert "Do not synthesize descriptive phrases" in system_prompt
    assert "Do not synthesize descriptive phrases" in user_prompt


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
    assert [(item.resume_id, item.source_section, item.source_text_index) for item in payload.source_texts] == [
        ("seed-1", "scorecard_evidence", 0),
        ("seed-1", "scorecard_strength", 0),
        ("seed-2", "scorecard_matched_must_have", 0),
    ]
    assert payload.source_texts[0].source_text_raw == "Built Flink CDC pipelines."
    assert payload.source_texts[0].source_text_hash == sha256("Built Flink CDC pipelines.".encode()).hexdigest()
    assert payload.source_texts[1].source_kind == "hint_only"
    assert payload.source_texts[1].hint_only is True
    assert payload.source_texts[1].support_eligible is False
    assert payload.source_texts[2].source_kind == "grounding_eligible"


def test_build_llm_prf_input_prefers_normalized_resume_snippets_over_scorecard_labels() -> None:
    payload = build_llm_prf_input(
        round_no=2,
        role_title="AI Agent Engineer",
        role_summary="Build agent workflows.",
        must_have_capabilities=["AI Agent", "LangGraph"],
        retrieval_query_terms=["AI Agent"],
        existing_query_terms=["AI Agent", "LangGraph"],
        seed_resumes=[
            _scored_candidate(
                "seed-1",
                evidence=["2年以上软件工程经验", "掌握RAG技术"],
                matched_must_haves=["2年以上软件工程经验"],
            ),
            _scored_candidate(
                "seed-2",
                evidence=["2年以上软件工程经验", "掌握RAG技术"],
                matched_must_haves=["2年以上软件工程经验"],
            ),
        ],
        negative_resumes=[],
        normalized_resumes_by_id={
            "seed-1": _normalized_resume(
                "seed-1",
                raw_text_excerpt="Built LangGraph workflow orchestration for agent memory and tool use.",
            ),
            "seed-2": _normalized_resume(
                "seed-2",
                raw_text_excerpt="Maintained LangGraph agent runtime and evaluation workflows.",
            ),
        },
    )

    assert payload is not None
    source_texts = [item.source_text_raw for item in payload.source_texts]
    assert any("LangGraph workflow orchestration" in text for text in source_texts)
    assert any("LangGraph agent runtime" in text for text in source_texts)
    assert "2年以上软件工程经验" not in source_texts
    assert all(item.source_kind == "grounding_eligible" for item in payload.source_texts)
    assert len(payload.source_texts) <= 8


def test_llm_prf_input_uses_normalized_resume_source_sections() -> None:
    seed = _scored_candidate("seed-1", overall_score=92, must_have_match_score=90)
    normalized = NormalizedResume(
        resume_id="seed-1",
        dedup_key="seed-1",
        completeness_score=90,
        skills=["LangGraph", "Agent Skills"],
        recent_experiences=[
            NormalizedExperience(
                company="Example",
                title="Agent Engineer",
                summary="Built LangGraph workflows for multi-agent retrieval.",
            )
        ],
        key_achievements=["Delivered Agent Skills modules for resume matching."],
        raw_text_excerpt="Agent Skills and LangGraph were used in production retrieval.",
    )
    seed_2 = _scored_candidate("seed-2", overall_score=91, must_have_match_score=89)
    normalized_2 = NormalizedResume(
        resume_id="seed-2",
        dedup_key="seed-2",
        completeness_score=90,
        skills=["LangGraph"],
        recent_experiences=[
            NormalizedExperience(
                company="Example",
                title="Agent Engineer",
                summary="Built LangGraph workflows for agent evaluation.",
            )
        ],
        key_achievements=["Delivered Agent Skills modules for talent matching."],
        raw_text_excerpt="Agent Skills and LangGraph were used in matching retrieval.",
    )

    payload = build_llm_prf_input(
        seed_resumes=[seed, seed_2],
        negative_resumes=[],
        round_no=2,
        role_title="AI Agent Engineer",
        must_have_capabilities=["LangGraph"],
        normalized_resumes_by_id={"seed-1": normalized, "seed-2": normalized_2},
    )

    assert payload is not None
    sections = {item.source_section for item in payload.source_texts}
    assert {"skill", "recent_experience_summary", "key_achievement", "raw_text_excerpt"} <= sections
    assert all(item.support_eligible for item in payload.source_texts if item.source_section != "scorecard_strength")


def test_llm_prf_source_sanitizer_rejects_metadata_dominated_snippets() -> None:
    seed = _scored_candidate("seed-1", overall_score=92, must_have_match_score=90)
    seed_2 = _scored_candidate("seed-2", overall_score=91, must_have_match_score=89)
    normalized = NormalizedResume(
        resume_id="seed-1",
        dedup_key="seed-1",
        completeness_score=85,
        skills=["LangGraph"],
        recent_experiences=[
            NormalizedExperience(company="阿里云", title="高级工程师", summary="阿里云 上海团队 高级工程师"),
            NormalizedExperience(company="Example", title="Engineer", summary="使用 LangGraph 构建 Agent 工作流"),
        ],
    )
    normalized_2 = NormalizedResume(
        resume_id="seed-2",
        dedup_key="seed-2",
        completeness_score=85,
        skills=["LangGraph"],
        recent_experiences=[
            NormalizedExperience(company="Example", title="Engineer", summary="使用 LangGraph 构建 Agent 检索系统"),
        ],
    )

    payload = build_llm_prf_input(
        seed_resumes=[seed, seed_2],
        negative_resumes=[],
        round_no=2,
        role_title="AI Agent Engineer",
        must_have_capabilities=["LangGraph"],
        normalized_resumes_by_id={"seed-1": normalized, "seed-2": normalized_2},
    )

    assert payload is not None
    raw_texts = [item.source_text_raw for item in payload.source_texts]
    assert "阿里云 上海团队 高级工程师" not in raw_texts
    assert any("LangGraph" in text for text in raw_texts)
    assert payload.source_preparation["dropped_reason_counts"]["metadata_dominated"] == 1


def test_llm_prf_scorecard_source_payload_uses_prepared_source_text() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["  Built   LangGraph   workflows.  "]),
            _scored_candidate("seed-2", evidence=["Built LangGraph retrieval workflows."]),
        ],
        negative_resumes=[],
        round_no=2,
        role_title="Agent Engineer",
    )

    assert payload is not None
    source = next(
        item
        for item in payload.source_texts
        if item.resume_id == "seed-1" and item.source_section == "scorecard_evidence"
    )
    assert source.source_text_raw == "Built LangGraph workflows."
    assert source.source_text_hash == text_sha256("Built LangGraph workflows.")


def test_llm_prf_scorecard_dedupe_happens_before_source_text_indexing() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate(
                "seed-1",
                evidence=["Built LangGraph workflows.", " Built  LangGraph   workflows. "],
            ),
            _scored_candidate("seed-2", evidence=["Built LangGraph retrieval workflows."]),
        ],
        negative_resumes=[],
        round_no=2,
        role_title="Agent Engineer",
    )

    assert payload is not None
    evidence_sources = [
        item
        for item in payload.source_texts
        if item.resume_id == "seed-1" and item.source_section == "scorecard_evidence"
    ]
    assert [(item.source_text_raw, item.source_text_index) for item in evidence_sources] == [
        ("Built LangGraph workflows.", 0)
    ]


def test_scorecard_strength_is_hint_only_and_support_ineligible() -> None:
    seed = _scored_candidate(
        "seed-1",
        overall_score=92,
        must_have_match_score=90,
        strengths=["LangGraph workflows"],
    )
    seed_2 = _scored_candidate("seed-2", overall_score=91, must_have_match_score=89)

    payload = build_llm_prf_input(
        seed_resumes=[seed, seed_2],
        negative_resumes=[],
        round_no=2,
        role_title="Agent Engineer",
    )

    assert payload is not None
    strength_sources = [item for item in payload.source_texts if item.source_section == "scorecard_strength"]
    assert strength_sources
    assert all(item.hint_only for item in strength_sources)
    assert all(not item.support_eligible for item in strength_sources)


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
            _candidate("Flink CDC", **_source_ref_kwargs(payload, "seed-1|scorecard_evidence|0")),
            _candidate("Flink CDC", **_source_ref_kwargs(payload, "seed-2|scorecard_evidence|0")),
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
        "source_section",
        "source_text_id",
        "source_text_index",
        "source_text_hash",
        "support_eligible",
        "hint_only",
        "start_char",
        "end_char",
        "raw_surface",
    }
    assert grounding.records[0].accepted is True
    assert grounding.records[0].resume_id == "seed-1"
    assert grounding.records[0].source_text_hash == _source_hash(payload, "seed-1|scorecard_evidence|0")
    assert grounding.records[0].start_char == len("Built ")
    assert grounding.records[0].end_char == len("Built Flink CDC")
    assert grounding.records[0].raw_surface == "Flink CDC"
    assert grounding.records[0].reject_reasons == []


def test_grounding_rejects_source_ref_with_wrong_source_section() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["Built Flink CDC pipelines."]),
            _scored_candidate("seed-2", evidence=["Built Flink CDC ingestion."]),
        ],
        negative_resumes=[],
    )
    assert payload is not None
    ref_kwargs = _source_ref_kwargs(payload, "seed-1|scorecard_evidence|0")
    ref_kwargs["source_section"] = "scorecard_strength"

    grounding = ground_llm_prf_candidates(payload, _extraction(_candidate("Flink CDC", **ref_kwargs)))

    assert grounding.records[0].accepted is False
    assert grounding.records[0].reject_reasons == ["source_reference_not_found"]


def test_grounding_rejects_source_ref_with_wrong_source_text_index() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["Built Flink CDC pipelines."]),
            _scored_candidate("seed-2", evidence=["Built Flink CDC ingestion."]),
        ],
        negative_resumes=[],
    )
    assert payload is not None
    ref_kwargs = _source_ref_kwargs(payload, "seed-1|scorecard_evidence|0")
    ref_kwargs["source_text_index"] += 99

    grounding = ground_llm_prf_candidates(payload, _extraction(_candidate("Flink CDC", **ref_kwargs)))

    assert grounding.records[0].accepted is False
    assert grounding.records[0].reject_reasons == ["source_index_mismatch"]
    assert grounding.records[0].source_text_index == ref_kwargs["source_text_index"]
    assert grounding.records[0].source_text_hash == ref_kwargs["source_hash"]


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
        _extraction(_candidate("Flink CDC", **_source_ref_kwargs(payload, "seed-1|scorecard_evidence|0"))),
    )

    record = grounding.records[0]
    assert record.raw_surface == "Flink CDC"
    assert payload.source_texts[0].source_text_raw[record.start_char : record.end_char] == "Flink CDC"
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
            _candidate("Java", **_source_ref_kwargs(payload, "seed-1|scorecard_evidence|0")),
            _candidate("React", **_source_ref_kwargs(payload, "seed-1|scorecard_evidence|0")),
            _candidate("阿里", **_source_ref_kwargs(payload, "seed-1|scorecard_evidence|0")),
        ),
    )

    assert [record.reject_reasons for record in grounding.records] == [
        ["unsafe_substring_match"],
        ["unsafe_substring_match"],
        ["unsafe_substring_match"],
    ]


def test_ground_llm_prf_candidates_allows_ascii_surface_next_to_cjk_text() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["使用Langgraph框架搭建multi-agent架构。"]),
            _scored_candidate("seed-2", evidence=["支持Multi-Agent 协作与任务拆解。"]),
        ],
        negative_resumes=[],
    )
    assert payload is not None

    grounding = ground_llm_prf_candidates(
        payload,
        _extraction(
            _candidate("Langgraph", **_source_ref_kwargs(payload, "seed-1|scorecard_evidence|0")),
            _candidate("Multi-Agent", **_source_ref_kwargs(payload, "seed-2|scorecard_evidence|0")),
        ),
    )

    assert [record.accepted for record in grounding.records] == [True, True]
    assert [record.reject_reasons for record in grounding.records] == [[], []]


def test_ground_llm_prf_candidates_recovers_case_variant_raw_offsets() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["接入Agent skills组件整合智能体项目中的工具。"]),
            _scored_candidate("seed-2", evidence=["支持Agent Skills模块化实现。"]),
        ],
        negative_resumes=[],
    )
    assert payload is not None

    grounding = ground_llm_prf_candidates(
        payload,
        _extraction(_candidate("Agent Skills", **_source_ref_kwargs(payload, "seed-1|scorecard_evidence|0"))),
    )

    record = grounding.records[0]
    assert record.accepted is True
    assert record.raw_surface == "Agent skills"


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
            _candidate("Flink CDC", **_source_ref_kwargs(payload, "seed-1|scorecard_evidence|0")),
            _candidate(
                "FlinkCDC",
                **_source_ref_kwargs(payload, "seed-2|scorecard_matched_must_have|0"),
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


def test_feedback_expressions_reject_existing_and_sent_query_term_families() -> None:
    payload = build_llm_prf_input(
        existing_query_terms=["AI Agent"],
        sent_query_terms=["LangGraph"],
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["Built AI Agent workflows with LangGraph."]),
            _scored_candidate("seed-2", evidence=["Shipped AI Agent runtime on LangGraph."]),
        ],
        negative_resumes=[],
    )
    assert payload is not None
    grounding = ground_llm_prf_candidates(
        payload,
        _extraction(
            _candidate("AI Agent", **_source_ref_kwargs(payload, "seed-1|scorecard_evidence|0")),
            _candidate("AI Agent", **_source_ref_kwargs(payload, "seed-2|scorecard_evidence|0")),
            _candidate("LangGraph", **_source_ref_kwargs(payload, "seed-1|scorecard_evidence|0")),
            _candidate("LangGraph", **_source_ref_kwargs(payload, "seed-2|scorecard_evidence|0")),
        ),
    )

    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities=set(),
        tried_term_family_ids=set(),
    )

    reject_reasons = {expression.canonical_expression: expression.reject_reasons for expression in expressions}
    assert reject_reasons == {
        "AI Agent": ["existing_or_tried_family"],
        "LangGraph": ["existing_or_tried_family"],
    }


def test_feedback_expressions_normalizes_tried_family_conflicts() -> None:
    payload = build_llm_prf_input(
        seed_resumes=[
            _scored_candidate("seed-1", evidence=["Built AI Agent workflows."]),
            _scored_candidate("seed-2", evidence=["Shipped AI Agent runtime."]),
        ],
        negative_resumes=[],
    )
    assert payload is not None
    grounding = ground_llm_prf_candidates(
        payload,
        _extraction(
            _candidate("AI Agent", **_source_ref_kwargs(payload, "seed-1|scorecard_evidence|0")),
            _candidate("AI Agent", **_source_ref_kwargs(payload, "seed-2|scorecard_evidence|0")),
        ),
    )

    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities=set(),
        tried_term_family_ids={"feedback.ai-agent"},
    )

    assert expressions[0].reject_reasons == ["existing_or_tried_family"]


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
                **_source_ref_kwargs(payload, "seed-1|scorecard_evidence|0"),
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
                **_source_ref_kwargs(payload, "seed-1|scorecard_evidence|0"),
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

    ref_kwargs = _source_ref_kwargs(payload, "seed-1|scorecard_evidence|0")
    ref_kwargs["source_hash"] = "wrong"
    grounding = ground_llm_prf_candidates(payload, _extraction(_candidate("Flink CDC", **ref_kwargs)))

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
            _candidate("Flink CDC", **_source_ref_kwargs(payload, "seed-1|scorecard_strength|0")),
            _candidate("Flink CDC", **_source_ref_kwargs(payload, "seed-2|scorecard_strength|0")),
        ),
    )

    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities=set(),
        tried_term_family_ids=set(),
    )

    assert expressions[0].field_hits == {"scorecard_strength": 2}
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
            _candidate("Flink CDC", **_source_ref_kwargs(payload, "seed-1|scorecard_strength|0")),
            _candidate("Flink CDC", **_source_ref_kwargs(payload, "seed-2|scorecard_evidence|0")),
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
    assert expressions[0].field_hits == {"scorecard_strength": 1, "scorecard_evidence": 1}


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
        _extraction(_candidate("Flink CDC", **_source_ref_kwargs(payload, "seed-1|scorecard_evidence|0"))),
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
        _extraction(_candidate("Go", **_source_ref_kwargs(payload, "seed-1|scorecard_evidence|0"))),
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
        _extraction(_candidate("Go", normalized_surface="Python", **_source_ref_kwargs(payload, "seed-1|scorecard_evidence|0"))),
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
        _extraction(_candidate("Flink CDC", **_source_ref_kwargs(payload, "seed-1|scorecard_evidence|0"))),
    )

    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities=set(),
        tried_term_family_ids={build_conservative_prf_family_id("FlinkCDC")},
    )

    assert expressions[0].term_family_id == "feedback.flinkcdc"
    assert "existing_or_tried_family" in expressions[0].reject_reasons
