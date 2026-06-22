from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from time import perf_counter

from seektalent.models import FinalResult, FinalizeContext
from seektalent.progress import ProgressCallback
from seektalent.finalize.deterministic import build_deterministic_final_result
from seektalent.runtime.stage_contracts import FinalizationStageState
from seektalent.tracing import RunTracer


type EmitProgress = Callable[..., None]
type RenderFinalMarkdown = Callable[[FinalResult], str]
type SlimFinalizeContext = Callable[[FinalizeContext], dict[str, object]]


def _register_runtime_artifacts(tracer: RunTracer) -> None:
    tracer.session.register_path(
        "runtime.finalization_context",
        "runtime/finalization_context.json",
        content_type="application/json",
        schema_version="v1",
    )
    tracer.session.register_path(
        "runtime.finalization_call",
        "runtime/finalization_call.json",
        content_type="application/json",
        schema_version="v1",
    )
    tracer.session.register_path(
        "output.final_answer",
        "output/final_answer.md",
        content_type="text/markdown",
        schema_version=None,
    )


async def run_deterministic_finalization_stage(
    *,
    finalize_context: FinalizeContext,
    tracer: RunTracer,
    progress_callback: ProgressCallback | None,
    emit_progress: EmitProgress,
    slim_finalize_context: SlimFinalizeContext,
    render_final_markdown: RenderFinalMarkdown,
) -> tuple[FinalResult, str, FinalizationStageState]:
    _register_runtime_artifacts(tracer)
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    started_clock = perf_counter()
    tracer.write_json("runtime.finalization_context", slim_finalize_context(finalize_context))
    emit_progress(
        progress_callback,
        "finalization_started",
        "正在整理最终候选人名单。",
        payload={"stage": "finalization", "engine": "deterministic_runtime"},
    )
    final_result = build_deterministic_final_result(finalize_context)
    final_markdown = render_final_markdown(final_result)
    latency_ms = max(1, int((perf_counter() - started_clock) * 1000))
    tracer.write_json(
        "runtime.finalization_call",
        {
            "stage": "finalization",
            "engine": "deterministic_runtime",
            "status": "succeeded",
            "started_at": started_at,
            "latency_ms": latency_ms,
            "input_artifact_refs": ["runtime.finalization_context"],
            "output_artifact_refs": ["output.final_candidates", "output.final_answer"],
            "candidate_count": len(final_result.candidates),
        },
    )
    tracer.write_json("output.final_candidates", final_result.model_dump(mode="json"))
    tracer.write_text("output.final_answer", final_markdown)
    emit_progress(
        progress_callback,
        "finalization_completed",
        final_result.summary,
        payload={
            "stage": "finalization",
            "engine": "deterministic_runtime",
            "final_candidate_count": len(final_result.candidates),
            "stop_reason": finalize_context.stop_reason,
        },
    )
    return final_result, final_markdown, {
        "artifacts": [
            "runtime/finalization_context.json",
            "runtime/finalization_call.json",
            "output/final_candidates.json",
            "output/final_answer.md",
        ],
        "latency_ms": latency_ms,
    }
