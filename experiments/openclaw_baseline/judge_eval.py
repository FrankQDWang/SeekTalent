from __future__ import annotations

import json
import shutil
from hashlib import sha256
from pathlib import Path

from seektalent.config import AppSettings
from seektalent.evaluation import (
    TOP_K,
    EvaluationArtifacts,
    EvaluationResult,
    JudgeCache,
    ResumeJudge,
    _remove_path,
    _stage_result,
    export_judge_tasks,
    persist_raw_resume_snapshot,
)
from seektalent.models import ResumeCandidate
from seektalent.prompting import LoadedPrompt


async def evaluate_openclaw_run(
    *,
    settings: AppSettings,
    prompt: LoadedPrompt,
    run_id: str,
    run_dir: Path,
    jd: str,
    notes: str,
    round_01_candidates: list[ResumeCandidate],
    final_candidates: list[ResumeCandidate],
    rounds_executed: int,
) -> EvaluationArtifacts:
    del rounds_executed
    cache = JudgeCache(settings.project_root)
    temp_root = run_dir / "._evaluation_tmp"
    final_evaluation_dir = run_dir / "evaluation"
    final_raw_dir = run_dir / "raw_resumes"
    try:
        jd_hash = sha256(jd.encode("utf-8")).hexdigest()
        unique_candidates: dict[str, ResumeCandidate] = {}
        for candidate in [*round_01_candidates[:TOP_K], *final_candidates[:TOP_K]]:
            unique_candidates[candidate.resume_id] = candidate

        judged, pending_cache_writes = await ResumeJudge(settings, prompt).judge_many(
            jd=jd,
            notes=notes,
            candidates=list(unique_candidates.values()),
            cache=cache,
        )
        evaluation = EvaluationResult(
            run_id=run_id,
            judge_model=settings.effective_judge_model,
            jd_sha256=jd_hash,
            round_01=_stage_result(stage="round_01", candidates=round_01_candidates, judged=judged),
            final=_stage_result(stage="final", candidates=final_candidates, judged=judged),
        )
        _remove_path(temp_root)
        export_judge_tasks(run_dir=temp_root, stage="round_01", jd_sha256_value=jd_hash, candidates=round_01_candidates)
        export_judge_tasks(run_dir=temp_root, stage="final", jd_sha256_value=jd_hash, candidates=final_candidates)
        (temp_root / "raw_resumes").mkdir(parents=True, exist_ok=True)
        for candidate in unique_candidates.values():
            persist_raw_resume_snapshot(run_dir=temp_root, candidate=candidate)

        path = temp_root / "evaluation" / "evaluation.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evaluation.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")

        _remove_path(final_evaluation_dir)
        _remove_path(final_raw_dir)
        shutil.move(str(temp_root / "evaluation"), str(final_evaluation_dir))
        shutil.move(str(temp_root / "raw_resumes"), str(final_raw_dir))
        cache.put_many(pending_cache_writes)
        _remove_path(temp_root)
        return EvaluationArtifacts(result=evaluation, path=final_evaluation_dir / "evaluation.json")
    except Exception:
        _remove_path(temp_root)
        _remove_path(final_evaluation_dir)
        _remove_path(final_raw_dir)
        raise
    finally:
        cache.close()
