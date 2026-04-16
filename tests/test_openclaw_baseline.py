from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from experiments.openclaw_baseline import OPENCLAW_MAX_ROUNDS
from experiments.openclaw_baseline.cts_tools import SearchCandidatesTool
from experiments.openclaw_baseline.harness import run_openclaw_baseline
from experiments.openclaw_baseline.judge_eval import evaluate_openclaw_run
from experiments.openclaw_baseline.wandb_logging import log_openclaw_failure_to_wandb, log_openclaw_to_wandb
from seektalent.config import AppSettings
from seektalent.evaluation import EvaluationArtifacts, EvaluationResult, EvaluationStageResult, ResumeJudgeResult
from seektalent.models import ResumeCandidate
from seektalent.prompting import LoadedPrompt
from seektalent.tracing import RunTracer


def _candidate(resume_id: str, *, source_round: int = 1) -> ResumeCandidate:
    return ResumeCandidate(
        resume_id=resume_id,
        source_resume_id=resume_id,
        snapshot_sha256=f"sha-{resume_id}",
        dedup_key=resume_id,
        source_round=source_round,
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


def _evaluation(run_id: str = "openclaw-run") -> EvaluationResult:
    stage = EvaluationStageResult(stage="round_01", ndcg_at_10=0.4, precision_at_10=0.3, total_score=0.33, candidates=[])
    return EvaluationResult(
        run_id=run_id,
        judge_model="openai-responses:gpt-5.4",
        jd_sha256="jd-hash",
        round_01=stage,
        final=EvaluationStageResult(stage="final", ndcg_at_10=0.7, precision_at_10=0.6, total_score=0.63, candidates=[]),
    )


class FakeResponsesClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)

    def create_response(self, payload: dict[str, object]) -> dict[str, object]:
        assert payload["model"] == "openclaw"
        return self.responses.pop(0)

    def close(self) -> None:
        return None


def test_search_candidates_enforces_round_budget(tmp_path: Path) -> None:
    settings = AppSettings(_env_file=None).with_overrides(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        search_max_pages_per_round=1,
        search_max_attempts_per_round=1,
    )
    tracer = RunTracer(settings.runs_path)
    tool = SearchCandidatesTool(settings=settings, tracer=tracer)
    tool.start_round(1)
    first = asyncio.run(tool.invoke_async({"query_terms": ["python"], "page": 1, "page_size": 2}))
    second = asyncio.run(tool.invoke_async({"query_terms": ["python"], "page": 2, "page_size": 2}))
    tracer.close()

    assert first["status"] == "ok"
    assert second["status"] == "budget_exhausted"
    assert tool.total_calls == 1


def test_search_candidates_tracks_total_cts_calls_across_rounds(tmp_path: Path) -> None:
    settings = AppSettings(_env_file=None).with_overrides(runs_dir=str(tmp_path / "runs"), mock_cts=True)
    tracer = RunTracer(settings.runs_path)
    tool = SearchCandidatesTool(settings=settings, tracer=tracer)
    tool.start_round(1)
    first = asyncio.run(tool.invoke_async({"query_terms": ["python"], "page": 1, "page_size": 2}))
    tool.start_round(2)
    second = asyncio.run(tool.invoke_async({"query_terms": ["trace"], "page": 1, "page_size": 2}))
    tracer.close()

    assert first["cts_round_no"] == 1
    assert second["cts_round_no"] == 2
    assert tool.total_calls == 2


def test_run_openclaw_baseline_freezes_first_cts_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings(_env_file=None).with_overrides(runs_dir=str(tmp_path / "runs"), mock_cts=True)
    evaluation = _evaluation()

    async def _fake_evaluate(**kwargs):  # noqa: ANN003
        return EvaluationArtifacts(result=evaluation, path=kwargs["run_dir"] / "evaluation" / "evaluation.json")

    monkeypatch.setattr("experiments.openclaw_baseline.harness.evaluate_openclaw_run", _fake_evaluate)
    monkeypatch.setattr("experiments.openclaw_baseline.harness.log_openclaw_to_wandb", lambda **kwargs: None)

    responses = [
        {
            "id": "r1-step1",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "search_candidates",
                    "arguments": json.dumps({"query_terms": ["python"], "page": 1, "page_size": 3}),
                }
            ],
        },
        {
            "id": "r1-step2",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "action": "continue",
                                    "summary": "Round 1 shortlist ready.",
                                    "stop_reason": None,
                                    "ranked_resume_ids": ["mock-r001", "mock-r002"],
                                }
                            ),
                        }
                    ],
                }
            ],
        },
        {
            "id": "r2-step1",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call-2",
                    "name": "search_candidates",
                    "arguments": json.dumps({"query_terms": ["trace"], "page": 1, "page_size": 3}),
                }
            ],
        },
        {
            "id": "r2-step2",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "action": "stop",
                                    "summary": "Final shortlist ready.",
                                    "stop_reason": "target_satisfied",
                                    "ranked_resume_ids": ["mock-r003", "mock-r001"],
                                }
                            ),
                        }
                    ],
                }
            ],
        },
    ]

    result = asyncio.run(
        run_openclaw_baseline(
            job_title="Python Engineer",
            jd="Python engineer with retrieval experience.",
            notes="Shanghai preferred.",
            settings=settings,
            responses_client=FakeResponsesClient(responses),
        )
    )

    assert [item["resume_id"] for item in result.round_01_candidates] == ["mock-r001", "mock-r002", "mock-r003"]
    assert [item["resume_id"] for item in result.final_candidates] == ["mock-r003", "mock-r001"]
    assert result.stop_reason == "target_satisfied"


def test_run_openclaw_baseline_counts_cts_calls_as_rounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings(_env_file=None).with_overrides(runs_dir=str(tmp_path / "runs"), mock_cts=True)
    evaluation = _evaluation()

    async def _fake_evaluate(**kwargs):  # noqa: ANN003
        return EvaluationArtifacts(result=evaluation, path=kwargs["run_dir"] / "evaluation" / "evaluation.json")

    monkeypatch.setattr("experiments.openclaw_baseline.harness.evaluate_openclaw_run", _fake_evaluate)
    monkeypatch.setattr("experiments.openclaw_baseline.harness.log_openclaw_to_wandb", lambda **kwargs: None)

    responses = [
        {
            "id": "r1-step1",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "search_candidates",
                    "arguments": json.dumps({"query_terms": ["python"], "page": 1, "page_size": 2}),
                }
            ],
        },
        {
            "id": "r1-step2",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call-2",
                    "name": "search_candidates",
                    "arguments": json.dumps({"query_terms": ["trace"], "page": 1, "page_size": 3}),
                }
            ],
        },
        {
            "id": "r1-step3",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "action": "stop",
                                    "summary": "Final shortlist ready.",
                                    "stop_reason": "target_satisfied",
                                    "ranked_resume_ids": ["mock-r006", "mock-r003"],
                                }
                            ),
                        }
                    ],
                }
            ],
        },
    ]

    result = asyncio.run(
        run_openclaw_baseline(
            job_title="Python Engineer",
            jd="Python engineer with retrieval experience.",
            notes="",
            settings=settings,
            responses_client=FakeResponsesClient(responses),
        )
    )

    assert result.rounds_executed == 2
    assert [item["resume_id"] for item in result.round_01_candidates] == ["mock-r001", "mock-r002"]
    assert [item["resume_id"] for item in result.final_candidates] == ["mock-r006", "mock-r003"]


def test_run_openclaw_baseline_caps_rounds_at_ten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings(_env_file=None).with_overrides(runs_dir=str(tmp_path / "runs"), mock_cts=True)
    evaluation = _evaluation()

    async def _fake_evaluate(**kwargs):  # noqa: ANN003
        return EvaluationArtifacts(result=evaluation, path=kwargs["run_dir"] / "evaluation" / "evaluation.json")

    monkeypatch.setattr("experiments.openclaw_baseline.harness.evaluate_openclaw_run", _fake_evaluate)
    monkeypatch.setattr("experiments.openclaw_baseline.harness.log_openclaw_to_wandb", lambda **kwargs: None)

    responses: list[dict[str, object]] = []
    for index in range(OPENCLAW_MAX_ROUNDS):
        responses.extend(
            [
                {
                    "id": f"round-{index}-call",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": f"call-{index}",
                            "name": "search_candidates",
                            "arguments": json.dumps({"query_terms": ["python"], "page": 1, "page_size": 2}),
                        }
                    ],
                },
                {
                    "id": f"round-{index}-done",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps(
                                        {
                                            "action": "continue",
                                            "summary": f"round {index + 1}",
                                            "stop_reason": None,
                                            "ranked_resume_ids": ["mock-r001" if index % 2 == 0 else "mock-r002"],
                                        }
                                    ),
                                }
                            ],
                        }
                    ],
                },
            ]
        )

    result = asyncio.run(
        run_openclaw_baseline(
            job_title="Python Engineer",
            jd="Python engineer with retrieval experience.",
            notes="",
            settings=settings,
            responses_client=FakeResponsesClient(responses),
        )
    )

    assert result.rounds_executed == OPENCLAW_MAX_ROUNDS
    assert result.stop_reason == "max_rounds_reached"


def test_evaluate_openclaw_run_writes_eval_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings(_env_file=None)
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

    monkeypatch.setattr("experiments.openclaw_baseline.judge_eval.ResumeJudge", FakeJudge)
    run_dir = tmp_path / "runs" / "openclaw"
    run_dir.mkdir(parents=True)
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge", sha256="hash")

    artifacts = asyncio.run(
        evaluate_openclaw_run(
            settings=settings,
            prompt=prompt,
            run_id="openclaw-run",
            run_dir=run_dir,
            jd="Python engineer",
            notes="",
            round_01_candidates=[_candidate("a")],
            final_candidates=[_candidate("a"), _candidate("b", source_round=2)],
            rounds_executed=2,
        )
    )

    assert artifacts.path.exists()
    assert (run_dir / "evaluation" / "round_01_judge_tasks.jsonl").exists()
    assert (run_dir / "evaluation" / "final_judge_tasks.jsonl").exists()
    assert any((run_dir / "raw_resumes").iterdir())


def test_log_openclaw_to_wandb_uses_openclaw_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    artifact_root = tmp_path / "artifacts"
    (artifact_root / "evaluation").mkdir(parents=True)
    (artifact_root / "evaluation" / "evaluation.json").write_text("{}", encoding="utf-8")
    (artifact_root / "raw_resumes").mkdir(parents=True)

    class FakeTable:
        def __init__(self, columns: list[str]) -> None:
            self.columns = columns
            self.rows: list[tuple[object, ...]] = []

        def add_data(self, *row: object) -> None:
            self.rows.append(row)

    class FakeArtifact:
        def __init__(self, name: str, type: str) -> None:  # noqa: A002
            self.name = name
            self.type = type
            self.files: list[str] = []
            self.dirs: list[tuple[str, str]] = []

        def add_file(self, path: str) -> None:
            self.files.append(path)

        def add_dir(self, path: str, *, name: str) -> None:
            self.dirs.append((path, name))

    class FakeRun:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs
            self.logged: list[dict[str, object]] = []
            self.artifacts: list[FakeArtifact] = []

        def log(self, payload: dict[str, object]) -> None:
            self.logged.append(payload)

        def log_artifact(self, artifact: FakeArtifact) -> None:
            self.artifacts.append(artifact)

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
    monkeypatch.setattr("experiments.openclaw_baseline.wandb_logging._upsert_wandb_report", lambda settings: upserts.append(settings.wandb_project))
    settings = AppSettings(_env_file=None, wandb_entity="frankqdwang1-personal-creations", wandb_project="seektalent")
    evaluation = EvaluationResult(
        run_id="openclaw-run",
        judge_model="openai-responses:gpt-5.4",
        jd_sha256="jd-hash",
        round_01=EvaluationStageResult(
            stage="round_01",
            ndcg_at_10=0.5,
            precision_at_10=0.4,
            total_score=0.43,
            candidates=[],
        ),
        final=EvaluationStageResult(
            stage="final",
            ndcg_at_10=0.7,
            precision_at_10=0.6,
            total_score=0.63,
            candidates=[],
        ),
    )

    log_openclaw_to_wandb(settings=settings, artifact_root=artifact_root, evaluation=evaluation, rounds_executed=4)

    assert fake_wandb.runs[0].kwargs["config"]["version"] == "openclaw"
    assert fake_wandb.runs[0].kwargs["config"]["seektalent_version"] == "openclaw"
    assert fake_wandb.runs[0].kwargs["config"]["eval_enabled"] is True
    assert upserts == ["seektalent"]


def test_log_openclaw_to_wandb_does_not_touch_weave(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    artifact_root = tmp_path / "artifacts"
    (artifact_root / "evaluation").mkdir(parents=True)
    (artifact_root / "evaluation" / "evaluation.json").write_text("{}", encoding="utf-8")
    (artifact_root / "raw_resumes").mkdir(parents=True)

    class PoisonWeave:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"OpenClaw W&B logging must not touch weave.{name}")

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
    monkeypatch.setattr("experiments.openclaw_baseline.wandb_logging._upsert_wandb_report", lambda settings: None)
    settings = AppSettings(_env_file=None, wandb_project="seektalent")

    log_openclaw_to_wandb(
        settings=settings,
        artifact_root=artifact_root,
        evaluation=_evaluation(),
        rounds_executed=1,
    )


def test_log_openclaw_failure_to_wandb_writes_zero_scores(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
    monkeypatch.setattr("experiments.openclaw_baseline.wandb_logging._upsert_wandb_report", lambda settings: upserts.append(settings.wandb_project))
    settings = AppSettings(_env_file=None, wandb_entity="frankqdwang1-personal-creations", wandb_project="seektalent")

    log_openclaw_failure_to_wandb(
        settings=settings,
        run_id="failed-openclaw-run",
        jd="agent jd",
        rounds_executed=1,
        error_message="Unsupported native filter: work_years",
    )

    payload = fake_wandb.runs[0].logged[0]
    assert fake_wandb.runs[0].kwargs["config"]["version"] == "openclaw"
    assert payload["final_total_score"] == 0.0
    assert payload["round_01_total_score"] == 0.0
    assert payload["openclaw_failed"] == 1
    assert "Unsupported native filter" in str(payload["openclaw_failure_message"])
    assert upserts == ["seektalent"]
