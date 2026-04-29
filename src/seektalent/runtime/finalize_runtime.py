from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from time import perf_counter
from typing import Any, TypedDict

from seektalent.config import AppSettings
from seektalent.finalize.finalizer import render_finalize_prompt
from seektalent.llm import resolve_stage_model_config
from seektalent.models import FinalResult, FinalizeContext
from seektalent.progress import ProgressCallback
from seektalent.tracing import RunTracer


type BuildSnapshot = Callable[..., Any]
type EmitLLMEvent = Callable[..., None]
type EmitProgress = Callable[..., None]
type RenderFinalMarkdown = Callable[[FinalResult], str]
type RunStageErrorBuilder = Callable[[str, str], Exception]
type SlimFinalizeContext = Callable[[FinalizeContext], dict[str, object]]


def _register_runtime_artifacts(tracer: RunTracer) -> None:
    tracer.session.register_path(
        "runtime.finalizer_context",
        "runtime/finalizer_context.json",
        content_type="application/json",
        schema_version="v1",
    )
    tracer.session.register_path(
        "runtime.finalizer_call",
        "runtime/finalizer_call.json",
        content_type="application/json",
        schema_version="v1",
    )
    tracer.session.register_path(
        "output.final_answer",
        "output/final_answer.md",
        content_type="text/markdown",
        schema_version=None,
    )


class FinalizerStageState(TypedDict):
    call_id: str
    artifacts: list[str]
    latency_ms: int


def _finalize_model_id(settings: AppSettings) -> str:
    return resolve_stage_model_config(settings, stage="finalize").model_id


async def run_finalizer_stage(
    *,
    settings: AppSettings,
    finalizer: object,
    finalize_context: FinalizeContext,
    tracer: RunTracer,
    progress_callback: ProgressCallback | None,
    build_llm_call_snapshot: BuildSnapshot,
    emit_llm_event: EmitLLMEvent,
    emit_progress: EmitProgress,
    slim_finalize_context: SlimFinalizeContext,
    render_final_markdown: RenderFinalMarkdown,
    run_stage_error: RunStageErrorBuilder,
) -> tuple[FinalResult, str, FinalizerStageState]:
    finalize_model_id = _finalize_model_id(settings)
    _register_runtime_artifacts(tracer)
    tracer.write_json("runtime.finalizer_context", slim_finalize_context(finalize_context))
    finalizer_call_id = "finalizer"
    finalizer_payload = {
        "FINALIZATION_CONTEXT": {
            "run_id": finalize_context.run_id,
            "run_dir": finalize_context.run_dir,
            "rounds_executed": finalize_context.rounds_executed,
            "stop_reason": finalize_context.stop_reason,
            "ranked_candidates": [
                candidate.model_dump(mode="json") for candidate in finalize_context.top_candidates
            ],
        }
    }
    finalizer_prompt = render_finalize_prompt(
        run_id=finalize_context.run_id,
        run_dir=finalize_context.run_dir,
        rounds_executed=finalize_context.rounds_executed,
        stop_reason=finalize_context.stop_reason,
        ranked_candidates=finalize_context.top_candidates,
    )
    finalizer_artifacts = [
        "runtime/finalizer_context.json",
        "runtime/finalizer_call.json",
        "output/final_candidates.json",
        "output/final_answer.md",
    ]
    finalizer_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    finalizer_started_clock = perf_counter()
    emit_llm_event(
        tracer=tracer,
        event_type="finalizer_started",
        call_id=finalizer_call_id,
        model_id=finalize_model_id,
        status="started",
        summary="Generating final shortlist output.",
        artifact_paths=finalizer_artifacts,
    )
    emit_progress(
        progress_callback,
        "finalizer_started",
        "正在整理最终候选人名单。",
        payload={"stage": "finalizer"},
    )
    try:
        final_result = await _finalize(finalizer=finalizer, finalize_context=finalize_context)
    except Exception as exc:  # noqa: BLE001
        latency_ms = max(1, int((perf_counter() - finalizer_started_clock) * 1000))
        finalizer_provider_usage = getattr(finalizer, "last_provider_usage", None)
        tracer.write_json(
            "runtime.finalizer_call",
            build_llm_call_snapshot(
                stage="finalize",
                call_id=finalizer_call_id,
                model_id=finalize_model_id,
                prompt_name="finalize",
                user_payload=finalizer_payload,
                user_prompt_text=finalizer_prompt,
                input_artifact_refs=["runtime.finalizer_context"],
                output_artifact_refs=[],
                started_at=finalizer_started_at,
                latency_ms=latency_ms,
                status="failed",
                retries=0,
                output_retries=2,
                error_message=str(exc),
                validator_retry_count=int(getattr(finalizer, "last_validator_retry_count", 0)),
                validator_retry_reasons=list(getattr(finalizer, "last_validator_retry_reasons", [])),
                provider_usage=finalizer_provider_usage,
            ).model_dump(mode="json"),
        )
        emit_llm_event(
            tracer=tracer,
            event_type="finalizer_failed",
            call_id=finalizer_call_id,
            model_id=finalize_model_id,
            status="failed",
            summary=str(exc),
            artifact_paths=["runtime/finalizer_call.json", "runtime/finalizer_context.json"],
            latency_ms=latency_ms,
            error_message=str(exc),
        )
        emit_progress(
            progress_callback,
            "finalizer_failed",
            str(exc),
            payload={"stage": "finalizer", "error_type": type(exc).__name__},
        )
        raise run_stage_error("finalization", str(exc)) from exc

    latency_ms = max(1, int((perf_counter() - finalizer_started_clock) * 1000))
    finalizer_structured_output = getattr(finalizer, "last_draft_output", None)
    finalizer_provider_usage = getattr(finalizer, "last_provider_usage", None)
    tracer.write_json(
        "runtime.finalizer_call",
        build_llm_call_snapshot(
            stage="finalize",
            call_id=finalizer_call_id,
            model_id=finalize_model_id,
            prompt_name="finalize",
            user_payload=finalizer_payload,
            user_prompt_text=finalizer_prompt,
            input_artifact_refs=["runtime.finalizer_context"],
            output_artifact_refs=["output.final_candidates"],
            started_at=finalizer_started_at,
            latency_ms=latency_ms,
            status="succeeded",
            retries=0,
            output_retries=2,
            structured_output=(
                finalizer_structured_output.model_dump(mode="json")
                if finalizer_structured_output is not None
                else final_result.model_dump(mode="json")
            ),
            validator_retry_count=int(getattr(finalizer, "last_validator_retry_count", 0)),
            validator_retry_reasons=list(getattr(finalizer, "last_validator_retry_reasons", [])),
            provider_usage=finalizer_provider_usage,
        ).model_dump(mode="json"),
    )
    final_markdown = render_final_markdown(final_result)
    tracer.write_json("output.final_candidates", final_result.model_dump(mode="json"))
    tracer.write_text("output.final_answer", final_markdown)
    return final_result, final_markdown, {
        "call_id": finalizer_call_id,
        "artifacts": finalizer_artifacts,
        "latency_ms": latency_ms,
    }


def finalize_finalizer_stage(
    *,
    settings: AppSettings,
    finalize_context: FinalizeContext,
    final_result: FinalResult,
    finalizer_stage_state: FinalizerStageState,
    completed_artifact_paths: list[str],
    tracer: RunTracer,
    progress_callback: ProgressCallback | None,
    emit_llm_event: EmitLLMEvent,
    emit_progress: EmitProgress,
) -> None:
    finalize_model_id = _finalize_model_id(settings)
    emit_llm_event(
        tracer=tracer,
        event_type="finalizer_completed",
        call_id=finalizer_stage_state["call_id"],
        model_id=finalize_model_id,
        status="succeeded",
        summary=final_result.summary,
        artifact_paths=finalizer_stage_state["artifacts"] + completed_artifact_paths,
        latency_ms=finalizer_stage_state["latency_ms"],
    )
    emit_progress(
        progress_callback,
        "finalizer_completed",
        final_result.summary,
        payload={
            "stage": "finalizer",
            "final_candidate_count": len(final_result.candidates),
            "stop_reason": finalize_context.stop_reason,
        },
    )


async def _finalize(*, finalizer: object, finalize_context: FinalizeContext) -> FinalResult:
    return await finalizer.finalize(
        run_id=finalize_context.run_id,
        run_dir=finalize_context.run_dir,
        rounds_executed=finalize_context.rounds_executed,
        stop_reason=finalize_context.stop_reason,
        ranked_candidates=finalize_context.top_candidates,
    )
