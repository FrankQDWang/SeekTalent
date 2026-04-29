from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from experiments.baseline_evaluation import evaluate_baseline_run
from experiments.baseline_wandb import log_baseline_failure_to_wandb, log_baseline_to_wandb
from experiments.jd_text_baseline import JD_TEXT_ARTIFACT_PREFIX, JD_TEXT_VERSION
from experiments.jd_text_baseline.cts_search import JDTextCTSClient, JDTextSearchResult
from experiments.jd_text_baseline.harness import run_jd_text_baseline
from seektalent.evaluation import EvaluationArtifacts, EvaluationResult, EvaluationStageResult, ResumeJudgeResult
from seektalent.models import ResumeCandidate
from seektalent.prompting import LoadedPrompt
from tests.settings_factory import make_settings


def _candidate_body(resume_id: str) -> dict[str, object]:
    return {
        "resume_id": resume_id,
        "activeStatus": "active",
        "age": 30,
        "educationList": [{"school": "复旦大学", "speciality": "计算机", "degree": "本科"}],
        "expectedIndustry": "Internet",
        "expectedIndustryIds": [],
        "expectedJobCategory": "Python Engineer",
        "expectedJobCategoryIds": [],
        "expectedLocation": "上海",
        "expectedLocationIds": [],
        "expectedSalary": "30-50k",
        "gender": "男",
        "jobState": "open",
        "nowLocation": "上海",
        "projectNameAll": ["Resume search"],
        "workExperienceList": [{"company": "Example Co", "title": "Python Engineer", "summary": "Built retrieval workflows."}],
        "workSummariesAll": ["python", "retrieval", "trace"],
        "workYear": 6,
    }


def _cts_response(*resume_ids: str) -> dict[str, object]:
    return {
        "code": 0,
        "status": "success",
        "message": "ok",
        "data": {
            "candidates": [_candidate_body(resume_id) for resume_id in resume_ids],
            "total": len(resume_ids),
            "page": 1,
            "pageSize": 10,
        },
    }


def _candidate(resume_id: str) -> ResumeCandidate:
    return ResumeCandidate(
        resume_id=resume_id,
        source_resume_id=resume_id,
        snapshot_sha256=f"sha-{resume_id}",
        dedup_key=resume_id,
        source_round=1,
        now_location="上海",
        work_year=6,
        expected_job_category="Python Engineer",
        education_summaries=["复旦大学 计算机 本科"],
        work_experience_summaries=["Example Co | Python Engineer | Built retrieval workflows."],
        project_names=["Resume search"],
        work_summaries=["python", "retrieval", "trace"],
        search_text="python retrieval trace",
        raw={"resume_id": resume_id, "candidate_name": resume_id},
    )


def _evaluation(run_id: str = "jd-text-run") -> EvaluationResult:
    stage = EvaluationStageResult(stage="round_01", ndcg_at_10=0.5, precision_at_10=0.4, total_score=0.43, candidates=[])
    return EvaluationResult(
        run_id=run_id,
        judge_model="deepseek-v4-pro",
        jd_sha256="jd-hash",
        round_01=stage,
        final=EvaluationStageResult(stage="final", ndcg_at_10=0.5, precision_at_10=0.4, total_score=0.43, candidates=[]),
    )


def test_jd_text_cts_client_payload_only_contains_jd_page_pagesize() -> None:
    seen_payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_payloads.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_cts_response("jd-r001"))

    settings = make_settings(
        cts_base_url="https://cts.example",
        cts_tenant_key="tenant-key",
        cts_tenant_secret="tenant-secret",
    )
    client = JDTextCTSClient(settings, transport=httpx.MockTransport(handler))

    result = asyncio.run(client.search_by_jd(jd="完整 JD 文本", trace_id="trace-1"))

    assert seen_payloads == [{"jd": "完整 JD 文本", "page": 1, "pageSize": 10}]
    assert result.request_payload == seen_payloads[0]
    assert result.candidates[0].resume_id == "jd-r001"


def test_run_jd_text_baseline_uses_one_round_and_same_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"))
    candidates = [_candidate("jd-r001"), _candidate("jd-r002")]

    class FakeClient:
        async def search_by_jd(self, *, jd: str, trace_id: str) -> JDTextSearchResult:
            return JDTextSearchResult(
                request_payload={"jd": jd, "page": 1, "pageSize": 10},
                response_body=_cts_response("jd-r001", "jd-r002"),
                candidates=candidates,
                total=2,
                raw_candidate_count=2,
                response_message=trace_id,
            )

    async def fake_evaluate(**kwargs):  # noqa: ANN003
        assert kwargs["round_01_candidates"] == kwargs["final_candidates"]
        return EvaluationArtifacts(result=_evaluation(kwargs["run_id"]), path=kwargs["run_dir"] / "evaluation" / "evaluation.json")

    monkeypatch.setattr("experiments.jd_text_baseline.harness.evaluate_baseline_run", fake_evaluate)
    monkeypatch.setattr("experiments.jd_text_baseline.harness.log_baseline_to_wandb", lambda **kwargs: None)

    result = asyncio.run(
        run_jd_text_baseline(
            job_title="Python Engineer",
            jd="Python engineer with retrieval experience.",
            notes="",
            settings=settings,
            client=cast(Any, FakeClient()),
        )
    )

    assert result.rounds_executed == 1
    assert result.stop_reason == "single_cts_jd_search"
    assert result.round_01_candidates == result.final_candidates
    assert json.loads((result.run_dir / "cts_request.json").read_text(encoding="utf-8")) == {
        "jd": "Python engineer with retrieval experience.",
        "page": 1,
        "pageSize": 10,
    }


def test_run_jd_text_baseline_failure_logs_zero_score(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), wandb_project="seektalent")
    failures: list[dict[str, object]] = []

    class EmptyClient:
        async def search_by_jd(self, *, jd: str, trace_id: str) -> JDTextSearchResult:
            del trace_id
            return JDTextSearchResult(
                request_payload={"jd": jd, "page": 1, "pageSize": 10},
                response_body=_cts_response(),
                candidates=[],
                total=0,
                raw_candidate_count=0,
            )

    monkeypatch.setattr("experiments.jd_text_baseline.harness.log_baseline_failure_to_wandb", lambda **kwargs: failures.append(kwargs))

    with pytest.raises(ValueError, match="zero candidates"):
        asyncio.run(
            run_jd_text_baseline(
                job_title="Python Engineer",
                jd="Python engineer with retrieval experience.",
                notes="",
                settings=settings,
                client=cast(Any, EmptyClient()),
            )
        )

    assert failures[0]["rounds_executed"] == 1
    assert "zero candidates" in str(failures[0]["error_message"])


def test_evaluate_baseline_run_writes_jd_text_eval_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings()
    monkeypatch.chdir(tmp_path)

    class FakeJudge:
        def __init__(self, settings, prompt) -> None:  # noqa: ANN001
            del settings, prompt

        async def judge_many(self, *, jd, notes, candidates, cache):  # noqa: ANN001
            del jd, notes, cache
            judged = {
                candidate.resume_id: (ResumeJudgeResult(score=3, rationale="Strong fit."), False, 1)
                for candidate in candidates
            }
            return judged, []

    monkeypatch.setattr("experiments.baseline_evaluation.ResumeJudge", FakeJudge)
    run_dir = tmp_path / "runs" / "jd_text"
    run_dir.mkdir(parents=True)
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge", sha256="hash")

    artifacts = asyncio.run(
        evaluate_baseline_run(
            settings=settings,
            prompt=prompt,
            run_id="jd-text-run",
            run_dir=run_dir,
            jd="Python engineer",
            notes="",
            round_01_candidates=[_candidate("a")],
            final_candidates=[_candidate("a")],
        )
    )

    assert artifacts.path.exists()
    assert (run_dir / "evaluation" / "round_01_judge_tasks.jsonl").exists()
    assert (run_dir / "evaluation" / "final_judge_tasks.jsonl").exists()
    assert any((run_dir / "raw_resumes").iterdir())


def test_log_baseline_to_wandb_uses_jd_text_version_and_refreshes_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    artifact_root = tmp_path / "artifacts"
    (artifact_root / "evaluation").mkdir(parents=True)
    (artifact_root / "evaluation" / "evaluation.json").write_text("{}", encoding="utf-8")
    (artifact_root / "raw_resumes").mkdir(parents=True)

    class FakeTable:
        def __init__(self, columns: list[str]) -> None:
            self.columns = columns

        def add_data(self, *row: object) -> None:
            del row

    class FakeArtifact:
        def __init__(self, name: str, type: str) -> None:  # noqa: A002
            self.name = name
            self.type = type

        def add_file(self, path: str) -> None:
            del path

        def add_dir(self, path: str, *, name: str) -> None:
            del path, name

    class FakeRun:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs
            self.logged: list[dict[str, object]] = []

        def log(self, payload: dict[str, object]) -> None:
            self.logged.append(payload)

        def log_artifact(self, artifact: FakeArtifact) -> None:
            del artifact

        def finish(self) -> None:
            return None

    class FakeWandb:
        def __init__(self) -> None:
            self.runs: list[FakeRun] = []

        def init(self, **kwargs) -> FakeRun:  # noqa: ANN003
            run = FakeRun(**kwargs)
            self.runs.append(run)
            return run

        Artifact = FakeArtifact
        Table = FakeTable

    fake_wandb = FakeWandb()
    monkeypatch.setitem(sys.modules, "wandb", fake_wandb)
    upserts: list[str] = []
    monkeypatch.setattr("experiments.baseline_wandb._upsert_wandb_report", lambda settings: upserts.append(settings.wandb_project))
    settings = make_settings(wandb_entity="frankqdwang1-personal-creations", wandb_project="seektalent")

    log_baseline_to_wandb(
        settings=settings,
        artifact_root=artifact_root,
        evaluation=_evaluation(),
        rounds_executed=1,
        version=JD_TEXT_VERSION,
        artifact_prefix=JD_TEXT_ARTIFACT_PREFIX,
        backing_model="cts.jd",
        init_timeout_seconds=300,
    )

    assert fake_wandb.runs[0].kwargs["config"]["version"] == JD_TEXT_VERSION
    assert fake_wandb.runs[0].kwargs["config"]["seektalent_version"] == JD_TEXT_VERSION
    assert fake_wandb.runs[0].logged[0]["rounds_executed"] == 1
    assert upserts == ["seektalent"]


def test_log_baseline_to_wandb_does_not_touch_weave_for_jd_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    artifact_root = tmp_path / "artifacts"
    (artifact_root / "evaluation").mkdir(parents=True)
    (artifact_root / "evaluation" / "evaluation.json").write_text("{}", encoding="utf-8")
    (artifact_root / "raw_resumes").mkdir(parents=True)

    class PoisonWeave:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"JD text W&B logging must not touch weave.{name}")

    class FakeTable:
        def __init__(self, columns: list[str]) -> None:
            self.columns = columns

        def add_data(self, *row: object) -> None:
            del row

    class FakeArtifact:
        def __init__(self, name: str, type: str) -> None:  # noqa: A002
            self.name = name
            self.type = type

        def add_file(self, path: str) -> None:
            del path

        def add_dir(self, path: str, *, name: str) -> None:
            del path, name

    class FakeRun:
        def log(self, payload: dict[str, object]) -> None:
            del payload

        def log_artifact(self, artifact: FakeArtifact) -> None:
            del artifact

        def finish(self) -> None:
            return None

    class FakeWandb:
        def init(self, **kwargs) -> FakeRun:  # noqa: ANN003
            del kwargs
            return FakeRun()

        Artifact = FakeArtifact
        Table = FakeTable

    monkeypatch.setitem(sys.modules, "weave", PoisonWeave())
    monkeypatch.setitem(sys.modules, "wandb", FakeWandb())
    monkeypatch.setattr("experiments.baseline_wandb._upsert_wandb_report", lambda settings: None)
    settings = make_settings(wandb_project="seektalent")

    log_baseline_to_wandb(
        settings=settings,
        artifact_root=artifact_root,
        evaluation=_evaluation(),
        rounds_executed=1,
        version=JD_TEXT_VERSION,
        artifact_prefix=JD_TEXT_ARTIFACT_PREFIX,
        backing_model="cts.jd",
        init_timeout_seconds=300,
    )


def test_log_baseline_failure_to_wandb_writes_jd_text_zero_scores(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeRun:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs
            self.logged: list[dict[str, object]] = []

        def log(self, payload: dict[str, object]) -> None:
            self.logged.append(payload)

        def finish(self) -> None:
            return None

    class FakeWandb:
        def __init__(self) -> None:
            self.runs: list[FakeRun] = []

        def init(self, **kwargs) -> FakeRun:  # noqa: ANN003
            run = FakeRun(**kwargs)
            self.runs.append(run)
            return run

    fake_wandb = FakeWandb()
    monkeypatch.setitem(sys.modules, "wandb", fake_wandb)
    upserts: list[str] = []
    monkeypatch.setattr("experiments.baseline_wandb._upsert_wandb_report", lambda settings: upserts.append(settings.wandb_project))
    settings = make_settings(wandb_entity="frankqdwang1-personal-creations", wandb_project="seektalent")

    log_baseline_failure_to_wandb(
        settings=settings,
        run_id="failed-jd-text-run",
        jd="agent jd",
        rounds_executed=1,
        error_message="CTS JD search returned zero candidates.",
        version=JD_TEXT_VERSION,
        backing_model="cts.jd",
        failure_metric_prefix="jd_text",
        init_timeout_seconds=300,
    )

    payload = fake_wandb.runs[0].logged[0]
    assert fake_wandb.runs[0].kwargs["config"]["version"] == JD_TEXT_VERSION
    assert payload["final_total_score"] == 0.0
    assert payload["round_01_total_score"] == 0.0
    assert payload["jd_text_failed"] == 1
    assert "zero candidates" in str(payload["jd_text_failure_message"])
    assert upserts == ["seektalent"]
