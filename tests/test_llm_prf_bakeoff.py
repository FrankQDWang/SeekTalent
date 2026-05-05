from __future__ import annotations

import json
from pathlib import Path

import pytest

from seektalent.candidate_feedback.llm_prf import (
    LLMPRFCandidate,
    LLMPRFExtraction,
    LLMPRFInput,
    LLM_PRF_SOURCE_PREPARATION_VERSION,
    LLMPRFSourceEvidenceRef,
    LLMPRFSourceText,
    build_llm_prf_source_text_id,
    text_sha256,
)
from seektalent.candidate_feedback.llm_prf_bakeoff import (
    LLMPRFBakeoffResult,
    LLMPRFLiveValidationCase,
    LLMPRFLiveValidationResult,
    classify_live_validation_blockers,
    load_bakeoff_cases,
    load_live_validation_cases,
    main,
    run_live_validation,
    score_live_validation_results,
    score_llm_prf_bakeoff_results,
)
from tests.settings_factory import make_settings


FIXTURE_PATH = Path("tests/fixtures/llm_prf_bakeoff/cases.jsonl")
LIVE_FIXTURE_PATH = Path("tests/fixtures/llm_prf_live_validation/cases.jsonl")


def test_bakeoff_metrics_mark_blocker_for_accepted_non_extractive_phrase() -> None:
    result = LLMPRFBakeoffResult(
        case_id="case-1",
        language_bucket="english",
        accepted_expression="Invented Phrase",
        accepted_grounded=False,
        accepted_reject_reasons=[],
        fallback_reason=None,
        structured_output_failed=False,
        latency_ms=1200,
    )

    metrics = score_llm_prf_bakeoff_results([result])

    assert metrics["blocker_count"] == 1
    assert metrics["non_extractive_accepted_count"] == 1


def test_bakeoff_metrics_count_no_safe_expression_as_fallback_not_blocker() -> None:
    result = LLMPRFBakeoffResult(
        case_id="case-1",
        language_bucket="mixed",
        accepted_expression=None,
        accepted_grounded=False,
        accepted_reject_reasons=[],
        fallback_reason="no_safe_llm_prf_expression",
        structured_output_failed=False,
        latency_ms=900,
    )

    metrics = score_llm_prf_bakeoff_results([result])

    assert metrics["generic_fallback_rate"] == 1.0
    assert metrics["blocker_count"] == 0


def test_bakeoff_metrics_report_language_counts_and_latency_percentiles() -> None:
    results = [
        LLMPRFBakeoffResult(case_id="a", language_bucket="english", latency_ms=100),
        LLMPRFBakeoffResult(case_id="b", language_bucket="chinese", latency_ms=200, structured_output_failed=True),
        LLMPRFBakeoffResult(case_id="c", language_bucket="mixed", latency_ms=300, fallback_reason="timeout"),
    ]

    metrics = score_llm_prf_bakeoff_results(results)

    assert metrics["case_count"] == 3
    assert metrics["structured_output_failure_rate"] == pytest.approx(1 / 3)
    assert metrics["generic_fallback_rate"] == pytest.approx(1 / 3)
    assert metrics["latency_ms_p50"] == 200
    assert metrics["latency_ms_p95"] == 300
    assert metrics["language_bucket_counts"] == {"english": 1, "chinese": 1, "mixed": 1}


def test_load_bakeoff_cases_reads_checked_in_smoke_fixture() -> None:
    cases = load_bakeoff_cases(FIXTURE_PATH)

    assert [case.case_id for case in cases] == ["english_streaming", "chinese_algorithm", "mixed_llm_ops"]
    assert {case.language_bucket for case in cases} == {"english", "chinese", "mixed"}


def test_load_live_validation_cases_reads_checked_in_fixtures() -> None:
    cases = load_live_validation_cases(LIVE_FIXTURE_PATH)

    assert [case.case_id for case in cases] == [
        "should_activate_shared_exact_phrase",
        "should_fallback_no_safe_phrase",
        "should_reject_existing_query_term",
        "should_reject_single_seed_support",
        "should_handle_cjk_ascii_boundaries",
    ]
    assert cases[0].input.source_texts[0].support_eligible is True


def test_bakeoff_cli_requires_live_flag(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--cases", str(FIXTURE_PATH), "--output-dir", str(tmp_path)])

    assert "--live is required" in str(exc_info.value)


def test_live_validation_case_loads_llm_prf_input_format(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    payload = LLMPRFInput(round_no=2, role_title="Agent Engineer", seed_resume_ids=["seed-1", "seed-2"])
    row = {
        "case_id": "shared_langgraph",
        "expected_behavior": "should_activate",
        "input": payload.model_dump(mode="json"),
        "blocked_terms": ["阿里云"],
        "notes": "sanitized fixture",
    }
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    cases = load_live_validation_cases(path)

    assert len(cases) == 1
    assert isinstance(cases[0], LLMPRFLiveValidationCase)
    assert isinstance(cases[0].input, LLMPRFInput)
    assert cases[0].expected_behavior == "should_activate"


def test_live_validation_provider_failures_are_not_product_blockers() -> None:
    result = LLMPRFLiveValidationResult(
        case_id="case",
        expected_behavior="should_activate",
        status="provider_failed",
        provider_failure=True,
        blockers=[],
        warnings=[],
    )

    summary = score_live_validation_results([result])

    assert summary["blocker_count"] == 0
    assert summary["provider_failure_count"] == 1


def test_live_validation_schema_failures_are_product_blockers() -> None:
    result = LLMPRFLiveValidationResult(
        case_id="case",
        expected_behavior="should_activate",
        status="schema_failed",
        provider_failure=False,
    )

    blockers, _warnings = classify_live_validation_blockers(result)

    assert "schema_validation_failed" in blockers


def test_activation_fixture_fallback_is_blocker_when_expected() -> None:
    result = LLMPRFLiveValidationResult(
        case_id="case",
        expected_behavior="should_activate",
        status="fallback",
        fallback_reason="no_safe_llm_prf_expression",
        blockers=[],
        warnings=[],
    )

    blockers, warnings = classify_live_validation_blockers(result)

    assert "expected_activation_fell_back" in blockers
    assert warnings == []


def test_live_validation_runs_runtime_chain_with_live_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_1 = _source("seed-1", "Built LangGraph workflows for support agents.", 0)
    source_2 = _source("seed-2", "Delivered LangGraph orchestration for retrieval agents.", 0)
    captured: dict[str, float] = {}

    class FakeExtractor:
        def __init__(self, settings, prompt) -> None:
            captured["timeout"] = settings.prf_probe_phrase_proposal_timeout_seconds

        async def propose(self, payload: LLMPRFInput) -> LLMPRFExtraction:
            return LLMPRFExtraction(
                candidates=[
                    LLMPRFCandidate(
                        surface="LangGraph",
                        normalized_surface="LangGraph",
                        candidate_term_type="tool_or_framework",
                        source_resume_ids=["seed-1", "seed-2"],
                        source_evidence_refs=[
                            _ref(source_1),
                            _ref(source_2),
                        ],
                    )
                ]
            )

    monkeypatch.setattr("seektalent.candidate_feedback.llm_prf_bakeoff.LLMPRFExtractor", FakeExtractor)
    settings = make_settings(
        prompt_dir="src/seektalent/prompts",
        prf_probe_phrase_proposal_timeout_seconds=3.0,
        prf_probe_phrase_proposal_live_harness_timeout_seconds=30.0,
    )
    cases = [
        LLMPRFLiveValidationCase(
            case_id="case",
            expected_behavior="should_activate",
            input=LLMPRFInput(
                round_no=2,
                role_title="Agent Engineer",
                seed_resume_ids=["seed-1", "seed-2"],
                source_texts=[source_1, source_2],
            ),
        )
    ]

    results = run_live_validation(settings=settings, cases=cases, output_dir=tmp_path)

    assert captured["timeout"] == 30.0
    assert results[0].status == "passed"
    assert results[0].accepted_expression == "LangGraph"
    assert results[0].accepted_positive_seed_support_count == 2
    assert (tmp_path / "case" / "policy.json").exists()


def test_live_validation_cli_writes_results_and_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cases_path = tmp_path / "cases.jsonl"
    payload = LLMPRFInput(round_no=2, role_title="Agent Engineer", seed_resume_ids=["seed-1", "seed-2"])
    cases_path.write_text(
        json.dumps(
            {
                "case_id": "case",
                "expected_behavior": "should_activate",
                "input": payload.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_run_live_validation(*, settings, cases, output_dir):
        return [
            LLMPRFLiveValidationResult(
                case_id=cases[0].case_id,
                expected_behavior=cases[0].expected_behavior,
                status="passed",
                accepted_expression="LangGraph",
                accepted_positive_seed_support_count=2,
            )
        ]

    monkeypatch.setattr(
        "seektalent.candidate_feedback.llm_prf_bakeoff.run_live_validation",
        fake_run_live_validation,
    )
    output_dir = tmp_path / "out"

    result = main(
        [
            "--live",
            "--case-format",
            "llm-prf-input",
            "--cases",
            str(cases_path),
            "--output-dir",
            str(output_dir),
            "--env-file",
            str(tmp_path / ".env"),
        ]
    )

    assert result == 0
    summary = json.loads((output_dir / "llm_prf_live_validation_summary.json").read_text(encoding="utf-8"))
    assert summary["blocker_count"] == 0
    assert (output_dir / "llm_prf_live_validation_results.jsonl").exists()


def _source(resume_id: str, text: str, index: int) -> LLMPRFSourceText:
    source_text_id = build_llm_prf_source_text_id(
        resume_id=resume_id,
        source_section="recent_experience_summary",
        original_field_path=f"recent_experiences[{index}].summary",
        normalized_text=text,
        preparation_version=LLM_PRF_SOURCE_PREPARATION_VERSION,
    )
    return LLMPRFSourceText(
        resume_id=resume_id,
        source_section="recent_experience_summary",
        source_text_id=source_text_id,
        source_text_index=index,
        source_text_raw=text,
        source_text_hash=text_sha256(text),
        original_field_path=f"recent_experiences[{index}].summary",
        source_kind="grounding_eligible",
        support_eligible=True,
        hint_only=False,
        dedupe_key=text.casefold(),
        rank_reason="test",
    )


def _ref(source: LLMPRFSourceText) -> LLMPRFSourceEvidenceRef:
    return LLMPRFSourceEvidenceRef(
        resume_id=source.resume_id,
        source_section=source.source_section,
        source_text_id=source.source_text_id,
        source_text_index=source.source_text_index,
        source_text_hash=source.source_text_hash,
    )
