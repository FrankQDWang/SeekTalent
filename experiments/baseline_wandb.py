from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from seektalent.config import AppSettings
from seektalent.evaluation import EvaluationResult, _upsert_wandb_report


def _wandb_init_kwargs(
    *,
    settings: AppSettings,
    config: dict[str, object],
    name: str,
    wandb: Any,
    init_timeout_seconds: int | None,
) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "project": settings.wandb_project,
        "entity": settings.wandb_entity or None,
        "job_type": "resume-eval",
        "config": config,
        "name": name,
    }
    if init_timeout_seconds is not None and hasattr(wandb, "Settings"):
        kwargs["settings"] = wandb.Settings(init_timeout=init_timeout_seconds)
    return kwargs


def log_baseline_to_wandb(
    *,
    settings: AppSettings,
    artifact_root: Path,
    evaluation: EvaluationResult,
    rounds_executed: int,
    version: str,
    artifact_prefix: str,
    backing_model: str,
    init_timeout_seconds: int | None = None,
) -> None:
    """Write report-compatible W&B artifacts without touching Weave."""
    if not settings.wandb_project:
        return
    import wandb

    run = wandb.init(
        **_wandb_init_kwargs(
            settings=settings,
            config={
                "version": version,
                "seektalent_version": version,
                "eval_enabled": True,
                "judge_model": evaluation.judge_model,
                "jd_sha256": evaluation.jd_sha256,
                "backing_model": backing_model,
            },
            name=evaluation.run_id,
            wandb=wandb,
            init_timeout_seconds=init_timeout_seconds,
        )
    )
    try:
        run.log(
            {
                "round_01_ndcg_at_10": evaluation.round_01.ndcg_at_10,
                "round_01_precision_at_10": evaluation.round_01.precision_at_10,
                "round_01_total_score": evaluation.round_01.total_score,
                "final_ndcg_at_10": evaluation.final.ndcg_at_10,
                "final_precision_at_10": evaluation.final.precision_at_10,
                "final_total_score": evaluation.final.total_score,
                "rounds_executed": rounds_executed,
            }
        )
        for stage_name, stage in (("round_01", evaluation.round_01), ("final", evaluation.final)):
            table = wandb.Table(
                columns=[
                    "rank",
                    "resume_id",
                    "source_resume_id",
                    "snapshot_sha256",
                    "raw_resume_path",
                    "expected_job_category",
                    "now_location",
                    "work_year",
                    "judge_score",
                    "judge_rationale",
                    "cache_hit",
                ]
            )
            for candidate in stage.candidates:
                table.add_data(
                    candidate.rank,
                    candidate.resume_id,
                    candidate.source_resume_id,
                    candidate.snapshot_sha256,
                    candidate.raw_resume_path,
                    candidate.expected_job_category,
                    candidate.now_location,
                    candidate.work_year,
                    candidate.judge_score,
                    candidate.judge_rationale,
                    candidate.cache_hit,
                )
            run.log({f"{stage_name}_top10": table})

        artifact = wandb.Artifact(f"{artifact_prefix}-eval-{evaluation.run_id}", type="evaluation")
        artifact.add_file(str(artifact_root / "evaluation" / "evaluation.json"))
        artifact.add_dir(str(artifact_root / "raw_resumes"), name="raw_resumes")
        run.log_artifact(artifact)
    finally:
        run.finish()
    _upsert_wandb_report(settings)


def log_baseline_failure_to_wandb(
    *,
    settings: AppSettings,
    run_id: str,
    jd: str,
    rounds_executed: int,
    error_message: str,
    version: str,
    backing_model: str,
    failure_metric_prefix: str,
    init_timeout_seconds: int | None = None,
) -> None:
    if not settings.wandb_project:
        return
    import wandb

    run = wandb.init(
        **_wandb_init_kwargs(
            settings=settings,
            config={
                "version": version,
                "seektalent_version": version,
                "eval_enabled": True,
                "judge_model": settings.effective_judge_model,
                "jd_sha256": sha256(jd.encode("utf-8")).hexdigest(),
                "backing_model": backing_model,
            },
            name=run_id,
            wandb=wandb,
            init_timeout_seconds=init_timeout_seconds,
        )
    )
    try:
        run.log(
            {
                "round_01_ndcg_at_10": 0.0,
                "round_01_precision_at_10": 0.0,
                "round_01_total_score": 0.0,
                "final_ndcg_at_10": 0.0,
                "final_precision_at_10": 0.0,
                "final_total_score": 0.0,
                "rounds_executed": rounds_executed,
                f"{failure_metric_prefix}_failed": 1,
                f"{failure_metric_prefix}_failure_message": error_message,
            }
        )
    finally:
        run.finish()
    _upsert_wandb_report(settings)
