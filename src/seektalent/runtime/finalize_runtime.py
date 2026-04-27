from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from time import perf_counter
from typing import Any

from seektalent.config import AppSettings
from seektalent.finalize.finalizer import render_finalize_prompt
from seektalent.models import FinalResult, FinalizeContext
from seektalent.progress import ProgressCallback
from seektalent.tracing import RunTracer


type BuildSnapshot = Callable[..., Any]
type EmitLLMEvent = Callable[..., None]
type EmitProgress = Callable[..., None]
type RenderFinalMarkdown = Callable[[FinalResult], str]
type RunStageErrorBuilder = Callable[[str, str], Exception]
type SlimFinalizeContext = Callable[[FinalizeContext], dict[str, object]]
type WritePostFinalizeArtifacts = Callable[..., list[str]]


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
    write_post_finalize_artifacts: WritePostFinalizeArtifacts,
    run_stage_error: RunStageErrorBuilder,
) -> tuple[FinalResult, str]:
    tracer.write_json("finalizer_context.json", slim_finalize_context(finalize_context))
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
        "finalizer_context.json",
        "finalizer_call.json",
        "final_candidates.json",
        "final_answer.md",
    ]
    finalizer_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    finalizer_started_clock = perf_counter()
    emit_llm_event(
        tracer=tracer,
        event_type="finalizer_started",
        call_id=finalizer_call_id,
        model_id=settings.finalize_model,
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
            "finalizer_call.json",
            build_llm_call_snapshot(
                stage="finalize",
                call_id=finalizer_call_id,
                model_id=settings.finalize_model,
                prompt_name="finalize",
                user_payload=finalizer_payload,
                user_prompt_text=finalizer_prompt,
                input_artifact_refs=["finalizer_context.json"],
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
            model_id=settings.finalize_model,
            status="failed",
            summary=str(exc),
            artifact_paths=["finalizer_call.json", "finalizer_context.json"],
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
        "finalizer_call.json",
        build_llm_call_snapshot(
            stage="finalize",
            call_id=finalizer_call_id,
            model_id=settings.finalize_model,
            prompt_name="finalize",
            user_payload=finalizer_payload,
            user_prompt_text=finalizer_prompt,
            input_artifact_refs=["finalizer_context.json"],
            output_artifact_refs=["final_candidates.json"],
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
    tracer.write_json("final_candidates.json", final_result.model_dump(mode="json"))
    tracer.write_text("final_answer.md", final_markdown)
    post_finalize_artifacts = write_post_finalize_artifacts(
        final_result=final_result,
        final_markdown=final_markdown,
    )
    emit_llm_event(
        tracer=tracer,
        event_type="finalizer_completed",
        call_id=finalizer_call_id,
        model_id=settings.finalize_model,
        status="succeeded",
        summary=final_result.summary,
        artifact_paths=finalizer_artifacts + post_finalize_artifacts,
        latency_ms=latency_ms,
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
    return final_result, final_markdown


async def _finalize(*, finalizer: object, finalize_context: FinalizeContext) -> FinalResult:
    return await finalizer.finalize(
        run_id=finalize_context.run_id,
        run_dir=finalize_context.run_dir,
        rounds_executed=finalize_context.rounds_executed,
        stop_reason=finalize_context.stop_reason,
        ranked_candidates=finalize_context.top_candidates,
    )
