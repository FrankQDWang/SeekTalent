from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from seektalent.api import MatchRunResult
from seektalent.evaluation import EvaluationResult, EvaluationStageResult
from seektalent.models import FinalCandidate, FinalResult, RetrievalState, SecondLaneDecision
from seektalent.runtime.production_contract import (
    ProductionMatchResultV1,
    PublicRuntimeErrorV1,
    PublicRuntimeWarningV1,
    SourceCoverageSummaryV1,
    SourceSelectionV1,
)


def _debug_result(tmp_path: Path, *, run_state: object | None = None) -> MatchRunResult:
    trace_log = tmp_path / "trace.log"
    trace_log.write_text("debug", encoding="utf-8")
    return MatchRunResult(
        final_result=FinalResult(
            run_id="run-1",
            run_dir=str(tmp_path),
            rounds_executed=2,
            stop_reason="controller_stop",
            summary="Shortlist is ready.",
            candidates=[
                FinalCandidate(
                    resume_id="resume-1",
                    rank=1,
                    final_score=88,
                    fit_bucket="fit",
                    source_provider="liepin",
                    evidence_level="detail",
                    detail_open_status="opened",
                    score_evidence_source="detail_enriched",
                    card_scorecard_ref="artifact:scorecards/card/resume-1.json",
                    detail_scorecard_ref="artifact:scorecards/detail/resume-1.json",
                    detail_open_reason="detail_budget_available",
                    detail_open_policy_version="detail-policy-v1",
                    match_summary="Strong Python match.",
                    strengths=["Python"],
                    weaknesses=[],
                    matched_must_haves=["Python"],
                    matched_preferences=["RAG"],
                    risk_flags=[],
                    why_selected="Best current fit.",
                    source_round=1,
                )
            ],
        ),
        final_markdown="# result",
        run_id="run-1",
        run_dir=tmp_path,
        trace_log_path=trace_log,
        evaluation_result=EvaluationResult(
            run_id="run-1",
            judge_model="judge",
            jd_sha256="jd",
            round_01=EvaluationStageResult(stage="round_01", ndcg_at_10=0.0, precision_at_10=0.0, total_score=0.0),
            final=EvaluationStageResult(stage="final", ndcg_at_10=0.0, precision_at_10=0.0, total_score=0.0),
        ),
        run_state=run_state,
    )


def test_production_result_schema_is_versioned_and_strict() -> None:
    schema = ProductionMatchResultV1.model_json_schema()

    assert "schema_version" in schema["properties"]
    assert schema["additionalProperties"] is False
    with pytest.raises(ValidationError):
        PublicRuntimeWarningV1(code="x", message="safe", unexpected=True)  # ty:ignore[unknown-argument]
    with pytest.raises(ValidationError):
        PublicRuntimeErrorV1(code="x", message="safe", http_status=500, cli_exit_code=1, raw="no")  # ty:ignore[unknown-argument]


def test_production_projection_excludes_debug_paths_and_payloads(tmp_path: Path) -> None:
    result = ProductionMatchResultV1.from_debug_result(
        _debug_result(tmp_path),
        input_digest="input-hash",
        source_selection=SourceSelectionV1(),
    )

    payload = result.model_dump_json()
    assert result.schema_version == "seektalent.production_match_result.v1"
    assert result.runtime_profile == "prod_core"
    assert result.final_candidates[0].candidate_id == "resume-1"
    assert result.final_candidates[0].source_provider == "liepin"
    assert result.final_candidates[0].evidence_level == "detail"
    assert result.final_candidates[0].detail_open_status == "opened"
    assert result.final_candidates[0].score_evidence_source == "detail_enriched"
    assert result.final_candidates[0].card_scorecard_ref == "artifact:scorecards/card/resume-1.json"
    assert result.final_candidates[0].detail_scorecard_ref == "artifact:scorecards/detail/resume-1.json"
    assert result.source_coverage == SourceCoverageSummaryV1(required=(), optional=())
    assert result.prf_summary.status == "unavailable"
    assert result.prf_summary.reason_code == "prf_runtime_summary_unavailable"
    assert str(tmp_path) not in payload
    assert "trace_log_path" not in payload
    assert "final_markdown" not in payload
    assert "candidate_store" not in payload
    assert "normalized_store" not in payload


def test_production_projection_rejects_missing_candidate_source_metadata(tmp_path: Path) -> None:
    debug_result = _debug_result(tmp_path)
    final_result = debug_result.final_result.model_copy(
        update={
            "candidates": [
                debug_result.final_result.candidates[0].model_copy(update={"source_provider": None})
            ]
        }
    )

    with pytest.raises(ValueError, match="missing source_provider"):
        ProductionMatchResultV1.from_debug_result(
            replace(debug_result, final_result=final_result),
            input_digest="input-hash",
            source_selection=SourceSelectionV1(),
        )


def test_production_projection_projects_successful_prf_from_runtime_state(tmp_path: Path) -> None:
    run_state = SimpleNamespace(
        retrieval_state=RetrievalState(
            second_lane_decision_history=[
                SecondLaneDecision(
                    round_no=2,
                    attempted_prf=True,
                    prf_gate_passed=True,
                    selected_lane_type="prf_probe",
                    accepted_prf_expression="LangGraph",
                    accepted_prf_term_family_id="feedback.langgraph",
                    prf_policy_version="prf-policy-v1",
                )
            ]
        )
    )

    result = ProductionMatchResultV1.from_debug_result(
        _debug_result(tmp_path, run_state=run_state),
        input_digest="input-hash",
        source_selection=SourceSelectionV1(),
    )

    assert result.prf_summary.selected is True
    assert result.prf_summary.status == "succeeded"
    assert result.prf_summary.reason_code == "prf_probe_selected"


def test_production_projection_projects_prf_fallback_reason_from_runtime_state(tmp_path: Path) -> None:
    run_state = SimpleNamespace(
        retrieval_state=RetrievalState(
            second_lane_decision_history=[
                SecondLaneDecision(
                    round_no=2,
                    attempted_prf=True,
                    prf_gate_passed=False,
                    selected_lane_type="generic_explore",
                    reject_reasons=["no_safe_llm_prf_expression"],
                    fallback_lane_type="generic_explore",
                    prf_policy_version="prf-policy-v1",
                )
            ]
        )
    )

    result = ProductionMatchResultV1.from_debug_result(
        _debug_result(tmp_path, run_state=run_state),
        input_digest="input-hash",
        source_selection=SourceSelectionV1(),
    )

    assert result.prf_summary.selected is False
    assert result.prf_summary.status == "degraded"
    assert result.prf_summary.reason_code == "no_safe_llm_prf_expression"


def test_production_projection_fails_closed_when_required_source_coverage_is_missing(tmp_path: Path) -> None:
    result = ProductionMatchResultV1.from_debug_result(
        _debug_result(tmp_path),
        input_digest="input-hash",
        source_selection=SourceSelectionV1(required=("cts",), optional=("liepin",)),
    )

    assert result.completion_status == "failed"
    assert result.source_selection.required == ("cts",)
    assert result.source_coverage.required[0].source_kind == "cts"
    assert result.source_coverage.required[0].status == "failed"
    assert result.source_coverage.required[0].operator_action == "check_source_configuration"
    assert result.source_coverage.optional[0].source_kind == "liepin"
    assert result.source_coverage.optional[0].status == "failed"
    assert result.warnings[0].source == "liepin"
    assert result.public_error is not None
    assert result.public_error.code == "required_sources_unavailable"
