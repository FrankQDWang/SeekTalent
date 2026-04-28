from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from time import perf_counter
from typing import Any, TypedDict

from seektalent.config import AppSettings
from seektalent.controller import ReActController
from seektalent.controller.react_controller import render_controller_prompt
from seektalent.models import ControllerContext, ControllerDecision, SearchControllerDecision, StopControllerDecision
from seektalent.progress import ProgressCallback
from seektalent.tracing import RunTracer, json_sha256


type BuildSnapshot = Callable[..., Any]
type EmitLLMEvent = Callable[..., None]
type EmitProgress = Callable[..., None]
type PromptCacheKey = Callable[..., str | None]
type RunStageErrorBuilder = Callable[[str, str], Exception]
type WriteAuxCallArtifact = Callable[..., None]


def _round_artifact(round_no: int, subsystem: str, name: str, *, extension: str = "json") -> str:
    return f"rounds/{round_no:02d}/{subsystem}/{name}.{extension}"


class ControllerStageState(TypedDict):
    call_id: str
    call_payload: dict[str, Any]
    prompt: str
    prompt_cache_key: str | None
    prompt_cache_retention: str | None
    artifacts: list[str]
    started_at: str
    controller_latency_ms: int


async def run_controller_stage(
    *,
    settings: AppSettings,
    controller: object,
    controller_context: ControllerContext,
    round_no: int,
    tracer: RunTracer,
    progress_callback: ProgressCallback | None,
    build_llm_call_snapshot: BuildSnapshot,
    write_aux_llm_call_artifact: WriteAuxCallArtifact,
    emit_llm_event: EmitLLMEvent,
    emit_progress: EmitProgress,
    prompt_cache_key: PromptCacheKey,
    run_stage_error: RunStageErrorBuilder,
) -> tuple[ControllerDecision, ControllerStageState]:
    controller_call_id = f"controller-r{round_no:02d}"
    controller_call_payload = {"CONTROLLER_CONTEXT": controller_context.model_dump(mode="json")}
    controller_prompt = render_controller_prompt(controller_context)
    controller_prompt_cache_key = prompt_cache_key(
        stage="controller",
        model_id=settings.controller_model,
        input_hash=json_sha256(controller_context.requirement_sheet.model_dump(mode="json")),
    )
    controller_prompt_cache_retention = (
        settings.openai_prompt_cache_retention if controller_prompt_cache_key is not None else None
    )
    controller_artifacts = [
        _round_artifact(round_no, "controller", "controller_context"),
        _round_artifact(round_no, "controller", "controller_call"),
        _round_artifact(round_no, "controller", "controller_decision"),
    ]
    controller_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    controller_started_clock = perf_counter()
    emit_llm_event(
        tracer=tracer,
        event_type="controller_started",
        round_no=round_no,
        call_id=controller_call_id,
        model_id=settings.controller_model,
        status="started",
        summary=f"Planning round {round_no} action.",
        artifact_paths=controller_artifacts,
    )
    emit_progress(
        progress_callback,
        "controller_started",
        f"正在判断第 {round_no} 轮搜索策略。",
        round_no=round_no,
        payload={"stage": "controller"},
    )
    try:
        if isinstance(controller, ReActController):
            controller_decision = await controller.decide(
                context=controller_context,
                prompt_cache_key=controller_prompt_cache_key,
            )
        else:
            controller_decision = await controller.decide(context=controller_context)
    except Exception as exc:  # noqa: BLE001
        latency_ms = max(1, int((perf_counter() - controller_started_clock) * 1000))
        controller_repair_attempt_count = int(getattr(controller, "last_repair_attempt_count", 0))
        controller_repair_model = (
            settings.structured_repair_model if controller_repair_attempt_count > 0 else None
        )
        controller_provider_usage = getattr(controller, "last_provider_usage", None)
        tracer.session.register_path(
            f"round.{round_no:02d}.controller.controller_call",
            _round_artifact(round_no, "controller", "controller_call"),
            content_type="application/json",
            schema_version="v1",
        )
        tracer.write_json(
            f"round.{round_no:02d}.controller.controller_call",
            build_llm_call_snapshot(
                stage="controller",
                call_id=controller_call_id,
                model_id=settings.controller_model,
                prompt_name="controller",
                user_payload=controller_call_payload,
                user_prompt_text=controller_prompt,
                input_artifact_refs=_controller_input_artifact_refs(round_no),
                output_artifact_refs=[],
                started_at=controller_started_at,
                latency_ms=latency_ms,
                status="failed",
                retries=0,
                output_retries=2,
                error_message=str(exc),
                round_no=round_no,
                validator_retry_count=getattr(controller, "last_validator_retry_count", 0),
                validator_retry_reasons=getattr(controller, "last_validator_retry_reasons", []),
                prompt_cache_key=controller_prompt_cache_key,
                prompt_cache_retention=controller_prompt_cache_retention,
                repair_attempt_count=controller_repair_attempt_count,
                repair_succeeded=bool(getattr(controller, "last_repair_succeeded", False)),
                repair_model=controller_repair_model,
                repair_reason=getattr(controller, "last_repair_reason", None),
                full_retry_count=int(getattr(controller, "last_full_retry_count", 0)),
                provider_usage=controller_provider_usage,
            ).model_dump(mode="json"),
        )
        write_aux_llm_call_artifact(
            tracer=tracer,
            path=f"round.{round_no:02d}.controller.repair_controller_call",
            call_artifact=getattr(controller, "last_repair_call_artifact", None),
            input_artifact_refs=[
                f"round.{round_no:02d}.controller.controller_context",
                f"round.{round_no:02d}.controller.controller_call",
            ],
            output_artifact_refs=[],
            round_no=round_no,
        )
        emit_llm_event(
            tracer=tracer,
            event_type="controller_failed",
            round_no=round_no,
            call_id=controller_call_id,
            model_id=settings.controller_model,
            status="failed",
            summary=str(exc),
            artifact_paths=controller_artifacts[:2],
            latency_ms=latency_ms,
            error_message=str(exc),
        )
        emit_progress(
            progress_callback,
            "controller_failed",
            str(exc),
            round_no=round_no,
            payload={"stage": "controller", "error_type": type(exc).__name__},
        )
        raise run_stage_error("controller", str(exc)) from exc
    controller_latency_ms = max(1, int((perf_counter() - controller_started_clock) * 1000))
    return controller_decision, {
        "call_id": controller_call_id,
        "call_payload": controller_call_payload,
        "prompt": controller_prompt,
        "prompt_cache_key": controller_prompt_cache_key,
        "prompt_cache_retention": controller_prompt_cache_retention,
        "artifacts": controller_artifacts,
        "started_at": controller_started_at,
        "controller_latency_ms": controller_latency_ms,
    }


def finalize_controller_stage(
    *,
    settings: AppSettings,
    controller: object,
    controller_decision: ControllerDecision,
    controller_stage_state: ControllerStageState,
    round_no: int,
    tracer: RunTracer,
    progress_callback: ProgressCallback | None,
    build_llm_call_snapshot: BuildSnapshot,
    write_aux_llm_call_artifact: WriteAuxCallArtifact,
    emit_llm_event: EmitLLMEvent,
    emit_progress: EmitProgress,
) -> None:
    tracer.write_json(
        f"round.{round_no:02d}.controller.controller_decision",
        controller_decision.model_dump(mode="json"),
    )
    controller_provider_usage = getattr(controller, "last_provider_usage", None)
    tracer.session.register_path(
        f"round.{round_no:02d}.controller.controller_call",
        _round_artifact(round_no, "controller", "controller_call"),
        content_type="application/json",
        schema_version="v1",
    )
    tracer.write_json(
        f"round.{round_no:02d}.controller.controller_call",
        build_llm_call_snapshot(
            stage="controller",
            call_id=controller_stage_state["call_id"],
            model_id=settings.controller_model,
            prompt_name="controller",
            user_payload=controller_stage_state["call_payload"],
            user_prompt_text=controller_stage_state["prompt"],
            input_artifact_refs=_controller_input_artifact_refs(round_no),
            output_artifact_refs=[f"round.{round_no:02d}.controller.controller_decision"],
            started_at=controller_stage_state["started_at"],
            latency_ms=controller_stage_state["controller_latency_ms"],
            status="succeeded",
            retries=0,
            output_retries=2,
            structured_output=controller_decision.model_dump(mode="json"),
            round_no=round_no,
            validator_retry_count=getattr(controller, "last_validator_retry_count", 0),
            validator_retry_reasons=getattr(controller, "last_validator_retry_reasons", []),
            prompt_cache_key=controller_stage_state["prompt_cache_key"],
            prompt_cache_retention=controller_stage_state["prompt_cache_retention"],
            repair_attempt_count=int(getattr(controller, "last_repair_attempt_count", 0)),
            repair_succeeded=bool(getattr(controller, "last_repair_succeeded", False)),
            repair_model=(
                settings.structured_repair_model if int(getattr(controller, "last_repair_attempt_count", 0)) > 0 else None
            ),
            repair_reason=getattr(controller, "last_repair_reason", None),
            full_retry_count=int(getattr(controller, "last_full_retry_count", 0)),
            provider_usage=controller_provider_usage,
        ).model_dump(mode="json"),
    )
    write_aux_llm_call_artifact(
        tracer=tracer,
        path=f"round.{round_no:02d}.controller.repair_controller_call",
        call_artifact=getattr(controller, "last_repair_call_artifact", None),
        input_artifact_refs=[
            f"round.{round_no:02d}.controller.controller_context",
            f"round.{round_no:02d}.controller.controller_call",
        ],
        output_artifact_refs=[f"round.{round_no:02d}.controller.controller_decision"],
        round_no=round_no,
    )
    emit_llm_event(
        tracer=tracer,
        event_type="controller_completed",
        round_no=round_no,
        call_id=controller_stage_state["call_id"],
        model_id=settings.controller_model,
        status="succeeded",
        summary=controller_decision.decision_rationale,
        artifact_paths=controller_stage_state["artifacts"],
        latency_ms=controller_stage_state["controller_latency_ms"],
    )
    emit_progress(
        progress_callback,
        "controller_completed",
        controller_decision.decision_rationale,
        round_no=round_no,
        payload={
            "stage": "controller",
            "action": controller_decision.action,
            "query_terms": (
                controller_decision.proposed_query_terms
                if isinstance(controller_decision, SearchControllerDecision)
                else []
            ),
            "stop_reason": (
                controller_decision.stop_reason if isinstance(controller_decision, StopControllerDecision) else None
            ),
        },
    )


def _controller_input_artifact_refs(round_no: int) -> list[str]:
    return [
        f"round.{round_no:02d}.controller.controller_context",
        "input.requirement_sheet",
        "runtime.sent_query_history",
    ]
