from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from seektalent.config import AppSettings
from seektalent.evaluation import AsyncJudgeLimiter, EvaluationArtifacts, EvaluationResult
from seektalent.models import FinalResult, ResumeCandidate, RunState, ScoredCandidate, TerminalControllerRound
from seektalent.progress import ProgressCallback
from seektalent.prompting import LoadedPrompt
from seektalent.tracing import RunTracer

type BuildJudgePacket = Callable[..., dict[str, object]]
type BuildSearchDiagnostics = Callable[..., dict[str, object]]
type BuildTermSurfaceAudit = Callable[..., dict[str, object]]
type EmitProgress = Callable[..., None]
type EvaluationRunner = Callable[..., Awaitable[EvaluationArtifacts]]
type MaterializeCandidates = Callable[..., list[ResumeCandidate]]
type RenderRunFinishedSummary = Callable[..., str]
type RenderRunSummary = Callable[..., str]


@dataclass(frozen=True)
class PostFinalizeResult:
    evaluation_result: EvaluationResult | None


def write_post_finalize_artifacts(
    *,
    settings: AppSettings,
    tracer: RunTracer,
    run_state: RunState,
    final_result: FinalResult,
    rounds_executed: int,
    stop_reason: str,
    terminal_controller_round: TerminalControllerRound | None,
    build_judge_packet: BuildJudgePacket,
    render_run_summary: RenderRunSummary,
    build_search_diagnostics: BuildSearchDiagnostics,
) -> list[str]:
    completed_artifact_paths: list[str] = []
    if settings.enable_eval:
        tracer.write_json(
            "output.judge_packet",
            build_judge_packet(
                tracer=tracer,
                run_state=run_state,
                final_result=final_result,
                rounds_executed=rounds_executed,
                stop_reason=stop_reason,
                terminal_controller_round=terminal_controller_round,
            ),
        )
        completed_artifact_paths.append("output/judge_packet.json")
    tracer.write_text(
        "output.run_summary",
        render_run_summary(
            run_state=run_state,
            final_result=final_result,
            terminal_controller_round=terminal_controller_round,
        ),
    )
    tracer.write_json(
        "runtime.search_diagnostics",
        build_search_diagnostics(
            tracer=tracer,
            run_state=run_state,
            final_result=final_result,
            terminal_controller_round=terminal_controller_round,
        ),
    )
    completed_artifact_paths.append("runtime/search_diagnostics.json")
    completed_artifact_paths.append("output/run_summary.md")
    return completed_artifact_paths


async def run_post_finalize_stage(
    *,
    settings: AppSettings,
    tracer: RunTracer,
    progress_callback: ProgressCallback | None,
    emit_progress: EmitProgress,
    run_state: RunState,
    final_result: FinalResult,
    top_scored: list[ScoredCandidate],
    rounds_executed: int,
    stop_reason: str,
    terminal_controller_round: TerminalControllerRound | None,
    judge_prompt: LoadedPrompt,
    evaluation_runner: EvaluationRunner,
    judge_limiter: AsyncJudgeLimiter | None,
    eval_remote_logging: bool,
    materialize_candidates: MaterializeCandidates,
    build_term_surface_audit: BuildTermSurfaceAudit,
    render_run_finished_summary: RenderRunFinishedSummary,
) -> PostFinalizeResult:
    evaluation_result: EvaluationResult | None = None
    if settings.enable_eval:
        round_01_candidates = materialize_candidates(
            scored_candidates=run_state.round_history[0].top_candidates if run_state.round_history else [],
            candidate_store=run_state.candidate_store,
        )
        final_candidates = materialize_candidates(
            scored_candidates=top_scored,
            candidate_store=run_state.candidate_store,
        )
        evaluation_artifacts = await evaluation_runner(
            settings=settings,
            prompt=judge_prompt,
            run_id=tracer.run_id,
            run_dir=tracer.run_dir,
            jd=run_state.input_truth.jd,
            notes=run_state.input_truth.notes,
            round_01_candidates=round_01_candidates,
            final_candidates=final_candidates,
            rounds_executed=rounds_executed,
            terminal_stop_guidance=(
                terminal_controller_round.stop_guidance if terminal_controller_round is not None else None
            ),
            judge_limiter=judge_limiter,
            log_remote=eval_remote_logging,
        )
        evaluation_result = evaluation_artifacts.result
        tracer.emit(
            "evaluation_completed",
            model=settings.effective_judge_model,
            status="succeeded",
            summary=(
                f"round_01 total={evaluation_result.round_01.total_score:.4f}; "
                f"final total={evaluation_result.final.total_score:.4f}"
            ),
            artifact_paths=[str(evaluation_artifacts.path.relative_to(tracer.run_dir))],
        )
    else:
        tracer.emit(
            "evaluation_skipped",
            status="skipped",
            summary="Eval disabled for this run.",
        )
    tracer.write_json(
        "runtime.term_surface_audit",
        build_term_surface_audit(
            tracer=tracer,
            run_state=run_state,
            final_result=final_result,
            evaluation_result=evaluation_result,
        ),
    )
    run_finished_summary = render_run_finished_summary(
        rounds_executed=rounds_executed,
        terminal_controller_round=terminal_controller_round,
    )
    tracer.emit(
        "run_finished",
        stop_reason=stop_reason,
        summary=run_finished_summary,
    )
    emit_progress(
        progress_callback,
        "run_completed",
        run_finished_summary,
        payload={
            "stage": "runtime",
            "rounds_executed": rounds_executed,
            "stop_reason": stop_reason,
        },
    )
    return PostFinalizeResult(evaluation_result=evaluation_result)
