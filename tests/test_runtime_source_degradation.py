from __future__ import annotations

from pathlib import Path

from seektalent.api import MatchRunResult
from seektalent.evaluation import EvaluationResult, EvaluationStageResult
from seektalent.models import (
    FinalCandidate,
    FinalResult,
    RuntimeFinalizationRevision,
    RuntimeSourceCoverageSummary,
)
from seektalent.runtime.production_contract import ProductionMatchResultV1, SourceSelectionV1


def _debug_result(
    tmp_path: Path,
    *,
    coverage_summary: RuntimeSourceCoverageSummary | None = None,
    finalization_revision: RuntimeFinalizationRevision | None = None,
    stop_reason: str = "source_lanes_completed",
) -> MatchRunResult:
    trace_log = tmp_path / "trace.log"
    trace_log.write_text("debug", encoding="utf-8")
    return MatchRunResult(
        final_result=FinalResult(
            run_id="run-source-degradation",
            run_dir=str(tmp_path),
            candidates=[
                FinalCandidate(
                    resume_id="candidate-1",
                    rank=1,
                    final_score=86,
                    fit_bucket="fit",
                    source_provider="cts",
                    evidence_level="card",
                    detail_open_status="not_supported",
                    match_summary="Strong backend match.",
                    strengths=["Python"],
                    weaknesses=[],
                    matched_must_haves=["Python"],
                    matched_preferences=[],
                    risk_flags=[],
                    why_selected="Best available candidate.",
                    source_round=1,
                )
            ],
            summary="done",
            stop_reason=stop_reason,
            rounds_executed=1,
        ),
        final_markdown="# Final",
        run_id="run-source-degradation",
        run_dir=tmp_path,
        trace_log_path=trace_log,
        evaluation_result=EvaluationResult(
            run_id="run-source-degradation",
            judge_model="judge",
            jd_sha256="jd",
            final=EvaluationStageResult(stage="final", ndcg_at_10=0.0, precision_at_10=0.0, total_score=0.0),
            round_01=EvaluationStageResult(stage="round_01", ndcg_at_10=0.0, precision_at_10=0.0, total_score=0.0),
        ),
        source_coverage_summary=coverage_summary,
        finalization_revision=finalization_revision,
    )


def test_production_contract_projects_degraded_optional_source_failure(tmp_path: Path) -> None:
    coverage_summary = RuntimeSourceCoverageSummary(
        status="degraded",
        selected_source_kinds=("cts", "liepin"),
        completed_source_kinds=("cts",),
        failed_source_kinds=("liepin",),
    )

    result = ProductionMatchResultV1.from_debug_result(
        _debug_result(tmp_path, coverage_summary=coverage_summary, stop_reason="source_lanes_degraded"),
        input_digest="digest",
        source_selection=SourceSelectionV1(required=("cts",), optional=("liepin",)),
    )

    assert result.completion_status == "degraded"
    assert result.source_coverage.required[0].source_kind == "cts"
    assert result.source_coverage.required[0].status == "succeeded"
    assert result.source_coverage.optional[0].source_kind == "liepin"
    assert result.source_coverage.optional[0].status == "failed"
    assert result.source_coverage.optional[0].retryable is True
    assert result.source_coverage.optional[0].operator_action == "retry_source_or_continue_with_available_results"
    assert result.warnings[0].code == "source_degraded"
    assert str(tmp_path) not in result.model_dump_json()


def test_production_contract_marks_required_empty_source_as_failed(tmp_path: Path) -> None:
    coverage_summary = RuntimeSourceCoverageSummary(
        status="empty",
        selected_source_kinds=("cts",),
        empty_source_kinds=("cts",),
    )

    result = ProductionMatchResultV1.from_debug_result(
        _debug_result(tmp_path, coverage_summary=coverage_summary, stop_reason="source_lanes_empty"),
        input_digest="digest",
        source_selection=SourceSelectionV1(required=("cts",)),
    )

    assert result.completion_status == "failed"
    assert result.source_coverage.required[0].source_kind == "cts"
    assert result.source_coverage.required[0].status == "empty"
    assert result.source_coverage.required[0].operator_action == "adjust_query_or_source_configuration"
    assert result.public_error is not None
    assert result.public_error.code == "required_sources_unavailable"


def test_production_contract_uses_finalization_revision_coverage_when_debug_result_has_none(tmp_path: Path) -> None:
    coverage_summary = RuntimeSourceCoverageSummary(
        status="degraded",
        selected_source_kinds=("cts", "liepin"),
        completed_source_kinds=("cts",),
        partial_source_kinds=("liepin",),
    )
    revision = RuntimeFinalizationRevision(
        revision=1,
        runtime_run_id="run-source-degradation",
        reason_code="source_lanes_degraded",
        selected_source_kinds=("cts", "liepin"),
        candidate_identity_ids=("candidate-1",),
        created_at="2026-06-21T12:00:00+08:00",
        coverage_summary=coverage_summary,
    )

    result = ProductionMatchResultV1.from_debug_result(
        _debug_result(tmp_path, finalization_revision=revision, stop_reason="source_lanes_degraded"),
        input_digest="digest",
        source_selection=SourceSelectionV1(required=("cts",), optional=("liepin",)),
    )

    assert result.completion_status == "degraded"
    assert result.source_coverage.optional[0].status == "partial"
    assert result.core_commit is not None
    assert result.core_commit.idempotency_key == "run-source-degradation:finalization:1"


def test_production_contract_projects_runtime_selected_sources_when_public_selection_is_empty(tmp_path: Path) -> None:
    coverage_summary = RuntimeSourceCoverageSummary(
        status="degraded",
        selected_source_kinds=("cts", "liepin"),
        completed_source_kinds=("cts",),
        failed_source_kinds=("liepin",),
    )

    result = ProductionMatchResultV1.from_debug_result(
        _debug_result(tmp_path, coverage_summary=coverage_summary, stop_reason="source_lanes_degraded"),
        input_digest="digest",
        source_selection=SourceSelectionV1(),
    )

    assert result.completion_status == "failed"
    assert result.source_selection.required == ("cts", "liepin")
    assert [source.source_kind for source in result.source_coverage.required] == ["cts", "liepin"]
    assert result.source_coverage.required[0].status == "succeeded"
    assert result.source_coverage.required[1].status == "failed"
    assert result.public_error is not None
    assert result.public_error.code == "required_sources_unavailable"


def test_production_contract_fails_closed_for_required_source_missing_from_coverage_buckets(
    tmp_path: Path,
) -> None:
    coverage_summary = RuntimeSourceCoverageSummary(
        status="complete",
        selected_source_kinds=("cts",),
        completed_source_kinds=("cts",),
    )

    result = ProductionMatchResultV1.from_debug_result(
        _debug_result(tmp_path, coverage_summary=coverage_summary),
        input_digest="digest",
        source_selection=SourceSelectionV1(required=("cts", "liepin")),
    )

    assert result.completion_status == "failed"
    assert result.source_coverage.required[0].status == "succeeded"
    assert result.source_coverage.required[1].source_kind == "liepin"
    assert result.source_coverage.required[1].status == "failed"
    assert result.source_coverage.required[1].operator_action == "check_source_configuration"
    assert result.public_error is not None
    assert result.public_error.code == "required_sources_unavailable"
