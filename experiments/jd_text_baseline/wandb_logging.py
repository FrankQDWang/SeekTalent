from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from experiments.jd_text_baseline import JD_TEXT_ARTIFACT_PREFIX, JD_TEXT_VERSION
from seektalent.config import AppSettings
from seektalent.evaluation import EvaluationResult, _upsert_wandb_report


def log_jd_text_to_wandb(
    *,
    settings: AppSettings,
    artifact_root: Path,
    evaluation: EvaluationResult,
    rounds_executed: int,
) -> None:
    """Write report-compatible W&B artifacts for JD text search without touching Weave."""
    if not settings.wandb_project:
        return
    import wandb

    run = wandb.init(
        project=settings.wandb_project,
        entity=settings.wandb_entity or None,
        job_type="resume-eval",
        config={
            "version": JD_TEXT_VERSION,
            "seektalent_version": JD_TEXT_VERSION,
            "eval_enabled": True,
            "judge_model": evaluation.judge_model,
            "jd_sha256": evaluation.jd_sha256,
            "backing_model": "cts.jd",
        },
        name=evaluation.run_id,
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

        artifact = wandb.Artifact(f"{JD_TEXT_ARTIFACT_PREFIX}-eval-{evaluation.run_id}", type="evaluation")
        artifact.add_file(str(artifact_root / "evaluation" / "evaluation.json"))
        artifact.add_dir(str(artifact_root / "raw_resumes"), name="raw_resumes")
        run.log_artifact(artifact)
    finally:
        run.finish()
    _upsert_wandb_report(settings)


def log_jd_text_failure_to_wandb(
    *,
    settings: AppSettings,
    run_id: str,
    jd: str,
    rounds_executed: int,
    error_message: str,
) -> None:
    if not settings.wandb_project:
        return
    import wandb

    run = wandb.init(
        project=settings.wandb_project,
        entity=settings.wandb_entity or None,
        job_type="resume-eval",
        config={
            "version": JD_TEXT_VERSION,
            "seektalent_version": JD_TEXT_VERSION,
            "eval_enabled": True,
            "judge_model": settings.effective_judge_model,
            "jd_sha256": sha256(jd.encode("utf-8")).hexdigest(),
            "backing_model": "cts.jd",
        },
        name=run_id,
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
                "jd_text_failed": 1,
                "jd_text_failure_message": error_message,
            }
        )
    finally:
        run.finish()
    _upsert_wandb_report(settings)
