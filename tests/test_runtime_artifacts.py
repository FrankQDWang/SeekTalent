from __future__ import annotations

import json
from pathlib import Path

from seektalent.api import MatchRunResult
from seektalent.evaluation import EvaluationResult, EvaluationStageResult
from seektalent.models import FinalResult
from seektalent.artifacts.lifecycle import RuntimeArtifactLifecycleRef
from seektalent.runtime.production_contract import ProductionMatchResultV1, SourceSelectionV1
from seektalent.tracing import RunTracer


def _debug_result(tmp_path: Path, lifecycle_ref: RuntimeArtifactLifecycleRef) -> MatchRunResult:
    trace_log = tmp_path / "trace.log"
    trace_log.write_text("debug", encoding="utf-8")
    return MatchRunResult(
        final_result=FinalResult(
            run_id=lifecycle_ref.artifact_id,
            run_dir=str(tmp_path),
            candidates=[],
            summary="done",
            stop_reason="max_rounds_reached",
            rounds_executed=1,
        ),
        final_markdown="# Final",
        run_id=lifecycle_ref.artifact_id,
        run_dir=tmp_path,
        trace_log_path=trace_log,
        evaluation_result=EvaluationResult(
            run_id=lifecycle_ref.artifact_id,
            judge_model="judge",
            jd_sha256="jd",
            final=EvaluationStageResult(stage="final", ndcg_at_10=0.0, precision_at_10=0.0, total_score=0.0),
            round_01=EvaluationStageResult(stage="round_01", ndcg_at_10=0.0, precision_at_10=0.0, total_score=0.0),
        ),
        artifact_lifecycle_ref=lifecycle_ref,
    )


def test_prod_runtime_artifact_lifecycle_ref_is_suppressed_and_path_free() -> None:
    ref = RuntimeArtifactLifecycleRef.from_output_mode(artifact_id="run_prod_1", output_mode="prod")

    payload = ref.model_dump()
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload == {
        "artifact_id": "run_prod_1",
        "artifact_uri": None,
        "retention_policy": "none",
        "debug_artifacts_available": False,
        "delete_eligible": False,
        "safety_class": "product_db_only",
        "max_bytes": 0,
        "support_bundle_only": False,
    }
    assert "/tmp" not in serialized
    assert "/Users/" not in serialized


def test_run_tracer_exposes_artifact_lifecycle_without_local_paths(tmp_path: Path) -> None:
    tracer = RunTracer(tmp_path, output_mode="dev")
    try:
        ref = tracer.artifact_lifecycle_ref()
    finally:
        tracer.close()

    serialized = ref.model_dump_json()
    assert ref.artifact_id == tracer.run_id
    assert ref.artifact_uri == f"artifact://run/{tracer.run_id}"
    assert ref.retention_policy == "dev_debug"
    assert ref.debug_artifacts_available is True
    assert str(tmp_path) not in serialized


def test_production_contract_uses_runtime_artifact_lifecycle_ref(tmp_path: Path) -> None:
    lifecycle_ref = RuntimeArtifactLifecycleRef.from_output_mode(artifact_id="run_dev_1", output_mode="dev")

    result = ProductionMatchResultV1.from_debug_result(
        _debug_result(tmp_path, lifecycle_ref),
        input_digest="digest",
        source_selection=SourceSelectionV1(required=("cts",)),
    )

    assert result.artifact_ref is not None
    assert result.artifact_ref.artifact_id == "run_dev_1"
    assert result.artifact_ref.artifact_uri == "artifact://run/run_dev_1"
    assert result.artifact_ref.retention_policy == "dev_debug"
    assert result.artifact_ref.debug_artifacts_available is True
