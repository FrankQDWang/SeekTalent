from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from time import perf_counter
from typing import Any

from seektalent.config import AppSettings
from seektalent.llm import resolve_stage_model_config
from seektalent.models import PoolDecision, ReflectionAdvice, RoundState, RunState
from seektalent.progress import ProgressCallback
from seektalent.reflection.critic import render_reflection_prompt
from seektalent.runtime.reflection_context import build_reflection_context
from seektalent.tracing import RunTracer, json_sha256


type BuildSnapshot = Callable[..., Any]
type EmitLLMEvent = Callable[..., None]
type EmitProgress = Callable[..., None]
type PromptCacheKey = Callable[..., str | None]
type ReflectRound = Callable[..., Awaitable[ReflectionAdvice]]
type RenderRoundReview = Callable[..., str]
type RunStageErrorBuilder = Callable[[str, str], Exception]
type SlimReflectionContext = Callable[..., dict[str, object]]
type WriteAuxCallArtifact = Callable[..., None]


def _round_artifact(round_no: int, subsystem: str, name: str, *, extension: str = "json") -> str:
    return f"rounds/{round_no:02d}/{subsystem}/{name}.{extension}"


def _resolved_stage_model_id(settings: AppSettings, *, stage: str) -> str:
    return resolve_stage_model_config(settings, stage=stage).model_id


async def run_reflection_stage(
    *,
    settings: AppSettings,
    reflection_critic: object,
    run_state: RunState,
    round_state: RoundState,
    round_no: int,
    tracer: RunTracer,
    progress_callback: ProgressCallback | None,
    reflect_round: ReflectRound,
    slim_reflection_context: SlimReflectionContext,
    build_llm_call_snapshot: BuildSnapshot,
    write_aux_llm_call_artifact: WriteAuxCallArtifact,
    emit_llm_event: EmitLLMEvent,
    emit_progress: EmitProgress,
    prompt_cache_key: PromptCacheKey,
    render_round_review: RenderRoundReview,
    next_step: str,
    newly_scored_count: int,
    pool_decisions: list[PoolDecision],
    run_stage_error: RunStageErrorBuilder,
) -> ReflectionAdvice:
    reflection_model_id = _resolved_stage_model_id(settings, stage="reflection")
    reflection_context = build_reflection_context(run_state=run_state, round_state=round_state)
    tracer.write_json(
        f"round.{round_no:02d}.reflection.reflection_context",
        slim_reflection_context(reflection_context),
    )
    reflection_call_id = f"reflection-r{round_no:02d}"
    reflection_call_payload = {"REFLECTION_CONTEXT": reflection_context.model_dump(mode="json")}
    reflection_prompt = render_reflection_prompt(reflection_context)
    reflection_prompt_cache_key = prompt_cache_key(
        stage="reflection",
        model_id=reflection_model_id,
        input_hash=json_sha256(reflection_context.model_dump(mode="json")),
    )
    reflection_prompt_cache_retention = (
        settings.openai_prompt_cache_retention if reflection_prompt_cache_key is not None else None
    )
    reflection_artifacts = [
        _round_artifact(round_no, "reflection", "reflection_context"),
        _round_artifact(round_no, "reflection", "reflection_call"),
        _round_artifact(round_no, "reflection", "reflection_advice"),
    ]
    reflection_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    reflection_started_clock = perf_counter()
    emit_llm_event(
        tracer=tracer,
        event_type="reflection_started",
        round_no=round_no,
        call_id=reflection_call_id,
        model_id=reflection_model_id,
        status="started",
        summary="Starting round reflection.",
        artifact_paths=reflection_artifacts,
    )
    emit_progress(
        progress_callback,
        "reflection_started",
        f"正在复盘第 {round_no} 轮关键词、候选人质量和下一步。",
        round_no=round_no,
        payload={"stage": "reflection"},
    )
    try:
        reflection_advice = await reflect_round(
            context=reflection_context,
            run_state=run_state,
            prompt_cache_key=reflection_prompt_cache_key,
            source_user_prompt=reflection_prompt,
        )
    except Exception as exc:  # noqa: BLE001
        latency_ms = max(1, int((perf_counter() - reflection_started_clock) * 1000))
        reflection_repair_attempt_count = int(getattr(reflection_critic, "last_repair_attempt_count", 0))
        reflection_repair_model = (
            _resolved_stage_model_id(settings, stage="structured_repair") if reflection_repair_attempt_count > 0 else None
        )
        reflection_provider_usage = getattr(reflection_critic, "last_provider_usage", None)
        tracer.session.register_path(
            f"round.{round_no:02d}.reflection.reflection_call",
            _round_artifact(round_no, "reflection", "reflection_call"),
            content_type="application/json",
            schema_version="v1",
        )
        tracer.write_json(
            f"round.{round_no:02d}.reflection.reflection_call",
            build_llm_call_snapshot(
                stage="reflection",
                call_id=reflection_call_id,
                model_id=reflection_model_id,
                prompt_name="reflection",
                user_payload=reflection_call_payload,
                user_prompt_text=reflection_prompt,
                input_artifact_refs=_reflection_input_artifact_refs(round_no),
                output_artifact_refs=[],
                started_at=reflection_started_at,
                latency_ms=latency_ms,
                status="failed",
                retries=0,
                output_retries=2,
                error_message=str(exc),
                round_no=round_no,
                validator_retry_count=int(getattr(reflection_critic, "last_validator_retry_count", 0)),
                validator_retry_reasons=list(getattr(reflection_critic, "last_validator_retry_reasons", [])),
                prompt_cache_key=reflection_prompt_cache_key,
                prompt_cache_retention=reflection_prompt_cache_retention,
                repair_attempt_count=reflection_repair_attempt_count,
                repair_succeeded=bool(getattr(reflection_critic, "last_repair_succeeded", False)),
                repair_model=reflection_repair_model,
                repair_reason=getattr(reflection_critic, "last_repair_reason", None),
                full_retry_count=int(getattr(reflection_critic, "last_full_retry_count", 0)),
                provider_usage=reflection_provider_usage,
            ).model_dump(mode="json"),
        )
        write_aux_llm_call_artifact(
            tracer=tracer,
            path=f"round.{round_no:02d}.reflection.repair_reflection_call",
            call_artifact=getattr(reflection_critic, "last_repair_call_artifact", None),
            input_artifact_refs=[
                f"round.{round_no:02d}.reflection.reflection_context",
                f"round.{round_no:02d}.reflection.reflection_call",
            ],
            output_artifact_refs=[],
            round_no=round_no,
        )
        emit_llm_event(
            tracer=tracer,
            event_type="reflection_failed",
            round_no=round_no,
            call_id=reflection_call_id,
            model_id=reflection_model_id,
            status="failed",
            summary=str(exc),
            artifact_paths=reflection_artifacts[:2],
            latency_ms=latency_ms,
            error_message=str(exc),
        )
        emit_progress(
            progress_callback,
            "reflection_failed",
            str(exc),
            round_no=round_no,
            payload={"stage": "reflection", "error_type": type(exc).__name__},
        )
        if isinstance(exc, run_stage_error):
            raise
        raise run_stage_error("reflection", str(exc)) from exc

    round_state.reflection_advice = reflection_advice
    tracer.write_json(
        f"round.{round_no:02d}.reflection.reflection_advice",
        reflection_advice.model_dump(mode="json"),
    )
    latency_ms = max(1, int((perf_counter() - reflection_started_clock) * 1000))
    reflection_repair_attempt_count = int(getattr(reflection_critic, "last_repair_attempt_count", 0))
    reflection_provider_usage = getattr(reflection_critic, "last_provider_usage", None)
    tracer.session.register_path(
        f"round.{round_no:02d}.reflection.reflection_call",
        _round_artifact(round_no, "reflection", "reflection_call"),
        content_type="application/json",
        schema_version="v1",
    )
    tracer.write_json(
        f"round.{round_no:02d}.reflection.reflection_call",
        build_llm_call_snapshot(
            stage="reflection",
            call_id=reflection_call_id,
            model_id=reflection_model_id,
            prompt_name="reflection",
            user_payload=reflection_call_payload,
            user_prompt_text=reflection_prompt,
            input_artifact_refs=_reflection_input_artifact_refs(round_no),
            output_artifact_refs=[f"round.{round_no:02d}.reflection.reflection_advice"],
            started_at=reflection_started_at,
            latency_ms=latency_ms,
            status="succeeded",
            retries=0,
            output_retries=2,
            structured_output=reflection_advice.model_dump(mode="json"),
            round_no=round_no,
            validator_retry_count=int(getattr(reflection_critic, "last_validator_retry_count", 0)),
            validator_retry_reasons=list(getattr(reflection_critic, "last_validator_retry_reasons", [])),
            prompt_cache_key=reflection_prompt_cache_key,
            prompt_cache_retention=reflection_prompt_cache_retention,
            repair_attempt_count=reflection_repair_attempt_count,
            repair_succeeded=bool(getattr(reflection_critic, "last_repair_succeeded", False)),
            repair_model=(
                _resolved_stage_model_id(settings, stage="structured_repair") if reflection_repair_attempt_count > 0 else None
            ),
            repair_reason=getattr(reflection_critic, "last_repair_reason", None),
            full_retry_count=int(getattr(reflection_critic, "last_full_retry_count", 0)),
            provider_usage=reflection_provider_usage,
        ).model_dump(mode="json"),
    )
    write_aux_llm_call_artifact(
        tracer=tracer,
        path=f"round.{round_no:02d}.reflection.repair_reflection_call",
        call_artifact=getattr(reflection_critic, "last_repair_call_artifact", None),
        input_artifact_refs=[
            f"round.{round_no:02d}.reflection.reflection_context",
            f"round.{round_no:02d}.reflection.reflection_call",
        ],
        output_artifact_refs=[f"round.{round_no:02d}.reflection.reflection_advice"],
        round_no=round_no,
    )
    emit_llm_event(
        tracer=tracer,
        event_type="reflection_completed",
        round_no=round_no,
        call_id=reflection_call_id,
        model_id=reflection_model_id,
        status="succeeded",
        summary=reflection_advice.reflection_summary,
        artifact_paths=reflection_artifacts,
        latency_ms=latency_ms,
    )
    emit_progress(
        progress_callback,
        "reflection_completed",
        reflection_advice.reflection_rationale or reflection_advice.reflection_summary,
        round_no=round_no,
        payload={
            "stage": "reflection",
            "reflection_summary": reflection_advice.reflection_summary,
            "reflection_rationale": reflection_advice.reflection_rationale,
            "suggest_stop": reflection_advice.suggest_stop,
            "suggested_stop_reason": reflection_advice.suggested_stop_reason,
        },
    )
    tracer.write_text(
        f"round.{round_no:02d}.reflection.round_review",
        render_round_review(
            round_no=round_no,
            controller_decision=round_state.controller_decision,
            retrieval_plan=round_state.retrieval_plan,
            observation=round_state.search_observation,
            newly_scored_count=newly_scored_count,
            pool_decisions=pool_decisions,
            top_candidates=round_state.top_candidates,
            dropped_candidates=round_state.dropped_candidates,
            reflection=reflection_advice,
            next_step=next_step,
        ),
    )
    return reflection_advice


def _reflection_input_artifact_refs(round_no: int) -> list[str]:
    return [
        f"round.{round_no:02d}.reflection.reflection_context",
        "input.requirement_sheet",
        "runtime.sent_query_history",
    ]
