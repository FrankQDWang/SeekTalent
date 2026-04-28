from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from time import perf_counter
from typing import Any

from seektalent.models import RetrievalState, RunState
from seektalent.progress import ProgressCallback
from seektalent.requirements import build_input_truth, build_scoring_policy
from seektalent.requirements.extractor import render_requirements_prompt
from seektalent.tracing import RunTracer


def _register_runtime_artifacts(tracer: RunTracer) -> None:
    tracer.session.register_path(
        "runtime.requirements_call",
        "runtime/requirements_call.json",
        content_type="application/json",
        schema_version="v1",
    )
    tracer.session.register_path(
        "runtime.repair_requirements_call",
        "runtime/repair_requirements_call.json",
        content_type="application/json",
        schema_version="v1",
    )
    tracer.session.register_path(
        "input.requirement_extraction_draft",
        "input/requirement_extraction_draft.json",
        content_type="application/json",
        schema_version="v1",
    )
    tracer.session.register_path(
        "input.requirement_sheet",
        "input/requirement_sheet.json",
        content_type="application/json",
        schema_version="v1",
    )
    tracer.session.register_path(
        "input.scoring_policy",
        "input/scoring_policy.json",
        content_type="application/json",
        schema_version="v1",
    )


def _build_requirements_call_metadata(*, settings: Any, requirement_extractor: Any) -> dict[str, Any]:
    repair_attempt_count = int(getattr(requirement_extractor, "last_repair_attempt_count", 0))
    provider_usage = getattr(requirement_extractor, "last_provider_usage", None)
    return {
        "cache_hit": bool(getattr(requirement_extractor, "last_cache_hit", False)),
        "cache_key": getattr(requirement_extractor, "last_cache_key", None),
        "cache_lookup_latency_ms": getattr(requirement_extractor, "last_cache_lookup_latency_ms", None),
        "prompt_cache_key": getattr(requirement_extractor, "last_prompt_cache_key", None),
        "prompt_cache_retention": getattr(requirement_extractor, "last_prompt_cache_retention", None),
        "repair_attempt_count": repair_attempt_count,
        "repair_succeeded": bool(getattr(requirement_extractor, "last_repair_succeeded", False)),
        "repair_model": settings.structured_repair_model if repair_attempt_count > 0 else None,
        "repair_reason": getattr(requirement_extractor, "last_repair_reason", None),
        "full_retry_count": int(getattr(requirement_extractor, "last_full_retry_count", 0)),
        "provider_usage": provider_usage.model_dump(mode="json") if provider_usage is not None else None,
    }


async def build_run_state(
    *,
    settings: Any,
    requirement_extractor: Any,
    tracer: RunTracer,
    job_title: str,
    jd: str,
    notes: str,
    progress_callback: ProgressCallback | None,
    emit_llm_event: Callable[..., None],
    emit_progress: Callable[..., None],
    build_llm_call_snapshot: Callable[..., Any],
    write_aux_llm_call_artifact: Callable[..., None],
    run_stage_error_factory: Callable[[str, str], Exception],
) -> RunState:
    input_truth = build_input_truth(job_title=job_title, jd=jd, notes=notes)
    call_id = "requirements"
    call_payload = {"INPUT_TRUTH": input_truth.model_dump(mode="json")}
    user_prompt = render_requirements_prompt(input_truth)
    artifact_paths = [
        "input/requirement_extraction_draft.json",
        "runtime/requirements_call.json",
        "input/requirement_sheet.json",
    ]
    _register_runtime_artifacts(tracer)
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    started_clock = perf_counter()
    emit_llm_event(
        tracer=tracer,
        event_type="requirements_started",
        call_id=call_id,
        model_id=settings.requirements_model,
        status="started",
        summary="Extracting requirement truth from the job title, JD, and notes.",
        artifact_paths=artifact_paths,
    )
    emit_progress(
        progress_callback,
        "requirements_started",
        "正在分析岗位标题、JD 和 notes。",
        payload={"stage": "requirements"},
    )
    try:
        requirement_draft, requirement_sheet = await requirement_extractor.extract_with_draft(input_truth=input_truth)
    except Exception as exc:  # noqa: BLE001
        latency_ms = max(1, int((perf_counter() - started_clock) * 1000))
        tracer.write_json(
            "runtime.requirements_call",
            build_llm_call_snapshot(
                stage="requirements",
                call_id=call_id,
                model_id=settings.requirements_model,
                prompt_name="requirements",
                user_payload=call_payload,
                user_prompt_text=user_prompt,
                input_artifact_refs=["input.input_truth", "input.input_snapshot"],
                output_artifact_refs=[],
                started_at=started_at,
                latency_ms=latency_ms,
                status="failed",
                retries=0,
                output_retries=2,
                error_message=str(exc),
                **_build_requirements_call_metadata(settings=settings, requirement_extractor=requirement_extractor),
            ).model_dump(mode="json"),
        )
        write_aux_llm_call_artifact(
            tracer=tracer,
            path="runtime.repair_requirements_call",
            call_artifact=getattr(requirement_extractor, "last_repair_call_artifact", None),
            input_artifact_refs=["input.input_truth", "runtime.requirements_call"],
            output_artifact_refs=[],
        )
        emit_llm_event(
            tracer=tracer,
            event_type="requirements_failed",
            call_id=call_id,
            model_id=settings.requirements_model,
            status="failed",
            summary=str(exc),
            artifact_paths=["runtime/requirements_call.json"],
            latency_ms=latency_ms,
            error_message=str(exc),
        )
        emit_progress(
            progress_callback,
            "requirements_failed",
            str(exc),
            payload={"stage": "requirements", "error_type": type(exc).__name__},
        )
        raise run_stage_error_factory("requirement_extraction", str(exc)) from exc

    latency_ms = max(1, int((perf_counter() - started_clock) * 1000))
    tracer.write_json("input.requirement_extraction_draft", requirement_draft.model_dump(mode="json"))
    tracer.write_json(
        "runtime.requirements_call",
        build_llm_call_snapshot(
            stage="requirements",
            call_id=call_id,
            model_id=settings.requirements_model,
            prompt_name="requirements",
            user_payload=call_payload,
            user_prompt_text=user_prompt,
            input_artifact_refs=["input.input_truth", "input.input_snapshot"],
            output_artifact_refs=["input.requirement_extraction_draft", "input.requirement_sheet"],
            started_at=started_at,
            latency_ms=latency_ms,
            status="succeeded",
            retries=0,
            output_retries=2,
            structured_output=requirement_draft.model_dump(mode="json"),
            **_build_requirements_call_metadata(settings=settings, requirement_extractor=requirement_extractor),
        ).model_dump(mode="json"),
    )
    write_aux_llm_call_artifact(
        tracer=tracer,
        path="runtime.repair_requirements_call",
        call_artifact=getattr(requirement_extractor, "last_repair_call_artifact", None),
        input_artifact_refs=["input.input_truth", "runtime.requirements_call"],
        output_artifact_refs=["input.requirement_extraction_draft", "input.requirement_sheet"],
    )
    scoring_policy = build_scoring_policy(requirement_sheet)
    run_state = RunState(
        input_truth=input_truth,
        requirement_sheet=requirement_sheet,
        scoring_policy=scoring_policy,
        retrieval_state=RetrievalState(
            current_plan_version=0,
            query_term_pool=requirement_sheet.initial_query_term_pool,
        ),
    )
    tracer.write_json("input.input_truth", input_truth.model_dump(mode="json"))
    tracer.write_json("input.requirement_sheet", requirement_sheet.model_dump(mode="json"))
    tracer.write_json("input.scoring_policy", scoring_policy.model_dump(mode="json"))
    tracer.write_json("runtime.sent_query_history", [])
    emit_llm_event(
        tracer=tracer,
        event_type="requirements_completed",
        call_id=call_id,
        model_id=settings.requirements_model,
        status="succeeded",
        summary=requirement_sheet.role_title,
        artifact_paths=artifact_paths,
        latency_ms=latency_ms,
    )
    emit_progress(
        progress_callback,
        "requirements_completed",
        f"岗位需求解析完成：{requirement_sheet.role_title}",
        payload={
            "stage": "requirements",
            "role_title": requirement_sheet.role_title,
            "must_have_capabilities": requirement_sheet.must_have_capabilities,
            "preferred_capabilities": requirement_sheet.preferred_capabilities,
        },
    )
    return run_state
