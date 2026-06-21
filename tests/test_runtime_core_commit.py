from __future__ import annotations

from pathlib import Path

from seektalent.api import MatchRunResult
from seektalent.evaluation import EvaluationResult, EvaluationStageResult
from seektalent.models import FinalResult, RuntimeFinalizationRevision, RuntimeSourceCoverageSummary
from seektalent.runtime.core_commit import core_commit_receipt_from_finalization_revision
from seektalent.runtime.production_contract import ProductionMatchResultV1, SourceSelectionV1


def _debug_result(tmp_path: Path, revision: RuntimeFinalizationRevision) -> MatchRunResult:
    trace_log = tmp_path / "trace.log"
    trace_log.write_text("debug", encoding="utf-8")
    return MatchRunResult(
        final_result=FinalResult(
            run_id=revision.runtime_run_id,
            run_dir=str(tmp_path),
            candidates=[],
            summary="done",
            stop_reason="source_lanes_completed",
            rounds_executed=1,
        ),
        final_markdown="# Final",
        run_id=revision.runtime_run_id,
        run_dir=tmp_path,
        trace_log_path=trace_log,
        evaluation_result=EvaluationResult(
            run_id=revision.runtime_run_id,
            judge_model="judge",
            jd_sha256="jd",
            final=EvaluationStageResult(stage="final", ndcg_at_10=0.0, precision_at_10=0.0, total_score=0.0),
            round_01=EvaluationStageResult(stage="round_01", ndcg_at_10=0.0, precision_at_10=0.0, total_score=0.0),
        ),
        finalization_revision=revision,
    )


def test_core_commit_receipt_is_deterministic_and_idempotent() -> None:
    revision = RuntimeFinalizationRevision(
        revision=2,
        runtime_run_id="run-core-1",
        reason_code="source_lanes_completed",
        selected_source_kinds=("cts", "liepin"),
        candidate_identity_ids=("candidate-1", "candidate-2"),
        created_at="2026-06-21T12:00:00+08:00",
        coverage_summary=RuntimeSourceCoverageSummary(
            status="complete",
            selected_source_kinds=("cts", "liepin"),
            completed_source_kinds=("cts", "liepin"),
        ),
    )

    first = core_commit_receipt_from_finalization_revision(revision)
    replay = core_commit_receipt_from_finalization_revision(revision)

    assert first == replay
    assert first.commit_id.startswith("runtime-finalization:")
    assert first.idempotency_key == "run-core-1:finalization:2"


def test_production_contract_projects_core_commit_receipt(tmp_path: Path) -> None:
    revision = RuntimeFinalizationRevision(
        revision=1,
        runtime_run_id="run-core-2",
        reason_code="source_lanes_completed",
        selected_source_kinds=("cts",),
        candidate_identity_ids=("candidate-1",),
        created_at="2026-06-21T12:00:00+08:00",
    )

    result = ProductionMatchResultV1.from_debug_result(
        _debug_result(tmp_path, revision),
        input_digest="digest",
        source_selection=SourceSelectionV1(required=("cts",)),
    )

    assert result.core_commit is not None
    assert result.core_commit.idempotency_key == "run-core-2:finalization:1"
    assert result.core_commit.commit_id.startswith("runtime-finalization:")
    assert str(tmp_path) not in result.model_dump_json()
