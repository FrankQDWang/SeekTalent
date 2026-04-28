from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from experiments.baseline_evaluation import evaluate_baseline_run
from experiments.baseline_wandb import log_baseline_failure_to_wandb, log_baseline_to_wandb
from experiments.jd_text_baseline import JD_TEXT_ARTIFACT_PREFIX, JD_TEXT_ROUNDS, JD_TEXT_VERSION
from experiments.jd_text_baseline.adapters import candidate_rows
from experiments.jd_text_baseline.cts_search import JDTextCTSClient
from seektalent.config import AppSettings
from seektalent.evaluation import EvaluationResult, TOP_K
from seektalent.prompting import PromptRegistry
from seektalent.tracing import RunTracer


@dataclass(frozen=True)
class JDTextRunResult:
    run_id: str
    run_dir: Path
    trace_log_path: Path
    rounds_executed: int
    stop_reason: str
    round_01_candidates: list[dict[str, object]]
    final_candidates: list[dict[str, object]]
    evaluation_result: EvaluationResult


async def run_jd_text_baseline(
    *,
    job_title: str,
    jd: str,
    notes: str,
    settings: AppSettings,
    client: JDTextCTSClient | None = None,
) -> JDTextRunResult:
    tracer = RunTracer(settings.artifacts_path)
    prompt_registry = PromptRegistry(settings.prompt_dir)
    judge_prompt = prompt_registry.load("judge")
    rounds_executed = 0
    tracer.write_json(
        "run_config.json",
        {
            "job_title": job_title,
            "judge_model": settings.effective_judge_model,
            "max_rounds": JD_TEXT_ROUNDS,
            "page": 1,
            "page_size": TOP_K,
            "request_fields": ["jd", "page", "pageSize"],
        },
    )
    tracer.emit("run_started", summary="Starting JD text CTS baseline run.")
    try:
        if client is None:
            settings.require_cts_credentials()
            client = JDTextCTSClient(settings)
        rounds_executed = JD_TEXT_ROUNDS
        search_result = await client.search_by_jd(jd=jd, trace_id=f"jd-text-{tracer.run_id}")
        tracer.write_json("cts_request.json", search_result.request_payload)
        tracer.write_json("cts_response.json", search_result.response_body)
        tracer.append_jsonl(
            "tool_calls.jsonl",
            {
                "status": "ok",
                "round_no": JD_TEXT_ROUNDS,
                "trace_id": f"jd-text-{tracer.run_id}",
                "request_payload": search_result.request_payload,
                "raw_candidate_count": search_result.raw_candidate_count,
                "total": search_result.total,
                "latency_ms": search_result.latency_ms,
                "adapter_notes": search_result.adapter_notes,
                "response_message": search_result.response_message,
            },
        )
        candidates = search_result.candidates[:TOP_K]
        if not candidates:
            raise ValueError("CTS JD search returned zero candidates.")

        round_01_candidates = list(candidates)
        final_candidates = list(candidates)
        tracer.write_json("round_01_candidates.json", candidate_rows(round_01_candidates))
        tracer.write_json("final_candidates.json", candidate_rows(final_candidates))
        tracer.write_text(
            "run_summary.md",
            "\n".join(
                [
                    "# JD Text Baseline Summary",
                    "",
                    f"- Rounds executed: `{rounds_executed}`",
                    "- Stop reason: `single_cts_jd_search`",
                    f"- Candidate ids: `{', '.join(candidate.resume_id for candidate in candidates)}`",
                ]
            ),
        )
        evaluation_artifacts = await evaluate_baseline_run(
            settings=settings,
            prompt=judge_prompt,
            run_id=tracer.run_id,
            run_dir=tracer.run_dir,
            jd=jd,
            notes=notes,
            round_01_candidates=round_01_candidates,
            final_candidates=final_candidates,
        )
        tracer.emit(
            "evaluation_completed",
            status="succeeded",
            summary=(
                f"round_01 total={evaluation_artifacts.result.round_01.total_score:.4f}; "
                f"final total={evaluation_artifacts.result.final.total_score:.4f}"
            ),
            artifact_paths=[str(evaluation_artifacts.path.relative_to(tracer.run_dir))],
        )
        log_baseline_to_wandb(
            settings=settings,
            artifact_root=tracer.run_dir,
            evaluation=evaluation_artifacts.result,
            rounds_executed=rounds_executed,
            version=JD_TEXT_VERSION,
            artifact_prefix=JD_TEXT_ARTIFACT_PREFIX,
            backing_model="cts.jd",
            init_timeout_seconds=300,
        )
        tracer.emit("run_finished", status="succeeded", stop_reason="single_cts_jd_search", summary="JD text baseline finished.")
        return JDTextRunResult(
            run_id=tracer.run_id,
            run_dir=tracer.run_dir,
            trace_log_path=tracer.trace_log_path,
            rounds_executed=rounds_executed,
            stop_reason="single_cts_jd_search",
            round_01_candidates=candidate_rows(round_01_candidates),
            final_candidates=candidate_rows(final_candidates),
            evaluation_result=evaluation_artifacts.result,
        )
    except Exception as exc:
        tracer.write_json("failure.json", {"error_type": type(exc).__name__, "error_message": str(exc)})
        log_baseline_failure_to_wandb(
            settings=settings,
            run_id=tracer.run_id,
            jd=jd,
            rounds_executed=rounds_executed,
            error_message=str(exc),
            version=JD_TEXT_VERSION,
            backing_model="cts.jd",
            failure_metric_prefix="jd_text",
            init_timeout_seconds=300,
        )
        tracer.emit("run_failed", status="failed", summary=str(exc), error_message=str(exc))
        raise
    finally:
        tracer.close()
