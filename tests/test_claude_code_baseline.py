from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from experiments.claude_code_baseline import CLAUDE_CODE_MAX_ROUNDS
from experiments.claude_code_baseline.cts_mcp import CTSToolSession
from experiments.claude_code_baseline.harness import run_claude_code_baseline
from experiments.claude_code_baseline.judge_eval import evaluate_claude_code_run
from experiments.claude_code_baseline.router import chat_completions_url, write_router_config
from experiments.claude_code_baseline.wandb_logging import (
    log_claude_code_failure_to_wandb,
    log_claude_code_to_wandb,
)
from seektalent.config import AppSettings
from seektalent.evaluation import EvaluationArtifacts, EvaluationResult, EvaluationStageResult, ResumeJudgeResult
from seektalent.models import ResumeCandidate
from seektalent.prompting import LoadedPrompt


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


def _evaluation(run_id: str = "claude-code-run") -> EvaluationResult:
    stage = EvaluationStageResult(stage="round_01", ndcg_at_10=0.4, precision_at_10=0.3, total_score=0.33, candidates=[])
    return EvaluationResult(
        run_id=run_id,
        judge_model="openai-responses:gpt-5.4",
        jd_sha256="jd-hash",
        round_01=stage,
        final=EvaluationStageResult(stage="final", ndcg_at_10=0.7, precision_at_10=0.6, total_score=0.63, candidates=[]),
    )


def _env_file(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1",
                "OPENAI_API_KEY=test-secret",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_chat_completions_url_appends_endpoint() -> None:
    assert chat_completions_url("https://example.com/v1") == "https://example.com/v1/chat/completions"
    assert chat_completions_url("https://example.com/v1/chat/completions") == "https://example.com/v1/chat/completions"


def test_write_router_config_uses_isolated_home_and_env_reference(tmp_path: Path) -> None:
    settings = AppSettings(_env_file=None).with_overrides(controller_model="openai-chat:deepseek-v3.2")
    config_path = write_router_config(
        home_dir=tmp_path / "home",
        settings=settings,
        env_file=_env_file(tmp_path),
        port=45678,
        api_key="local-router-token",
    )

    body = json.loads(config_path.read_text(encoding="utf-8"))
    assert config_path == tmp_path / "home" / ".claude-code-router" / "config.json"
    assert body["Providers"][0]["api_key"] == "$OPENAI_API_KEY"
    assert "test-secret" not in config_path.read_text(encoding="utf-8")
    assert body["Router"]["default"] == "bailian,deepseek-v3.2"


def test_cts_mcp_enforces_ten_accepted_calls(tmp_path: Path) -> None:
    settings = AppSettings(_env_file=None).with_overrides(mock_cts=True)
    session = CTSToolSession(settings=settings, run_dir=tmp_path)

    for _ in range(CLAUDE_CODE_MAX_ROUNDS):
        result = asyncio.run(session.search_candidates({"query_terms": ["python"], "page": 1, "page_size": 1}))
        assert result["status"] == "ok"

    exhausted = asyncio.run(session.search_candidates({"query_terms": ["python"], "page": 1, "page_size": 1}))
    state = json.loads((tmp_path / "cts_state.json").read_text(encoding="utf-8"))

    assert exhausted["status"] == "budget_exhausted"
    assert session.total_calls == CLAUDE_CODE_MAX_ROUNDS
    assert state["total_calls"] == CLAUDE_CODE_MAX_ROUNDS
    assert state["fatal_error"] == "Claude Code CTS budget exhausted."


def test_cts_mcp_freezes_first_successful_search(tmp_path: Path) -> None:
    settings = AppSettings(_env_file=None).with_overrides(mock_cts=True)
    session = CTSToolSession(settings=settings, run_dir=tmp_path)

    first = asyncio.run(session.search_candidates({"query_terms": ["python"], "page": 1, "page_size": 2}))
    second = asyncio.run(session.search_candidates({"query_terms": ["trace"], "page": 1, "page_size": 2}))

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert session.total_calls == 2
    assert session.first_search_resume_ids == ["mock-r001", "mock-r002"]


def test_run_claude_code_baseline_uses_isolated_home_and_counts_cts_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings(_env_file=None).with_overrides(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        controller_model="openai-chat:deepseek-v3.2",
    )
    env_file = _env_file(tmp_path)
    homes: list[str] = []

    def fake_runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:  # noqa: ANN003
        homes.append(kwargs["env"]["HOME"])
        assert command[0] == "claude"
        run_dir = Path(command[command.index("--settings") + 1]).parent
        candidates = {"mock-r001": _candidate("mock-r001").model_dump(mode="json"), "mock-r002": _candidate("mock-r002").model_dump(mode="json")}
        (run_dir / "candidates.json").write_text(json.dumps(candidates), encoding="utf-8")
        (run_dir / "cts_state.json").write_text(
            json.dumps(
                {
                    "total_calls": 2,
                    "first_search_resume_ids": ["mock-r001"],
                    "candidate_ids": ["mock-r001", "mock-r002"],
                    "fatal_error": None,
                }
            ),
            encoding="utf-8",
        )
        stdout = json.dumps(
            {
                "result": json.dumps(
                    {
                        "summary": "Final shortlist ready.",
                        "stop_reason": "target_satisfied",
                        "ranked_resume_ids": ["mock-r002", "mock-r001"],
                    }
                )
            }
        )
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    async def fake_evaluate(**kwargs):  # noqa: ANN003
        assert kwargs["rounds_executed"] == 2
        return EvaluationArtifacts(result=_evaluation(kwargs["run_id"]), path=kwargs["run_dir"] / "evaluation" / "evaluation.json")

    monkeypatch.setattr("experiments.claude_code_baseline.harness.evaluate_claude_code_run", fake_evaluate)
    monkeypatch.setattr("experiments.claude_code_baseline.harness.log_claude_code_to_wandb", lambda **kwargs: None)

    result = asyncio.run(
        run_claude_code_baseline(
            job_title="Python Engineer",
            jd="Python engineer with retrieval experience.",
            notes="",
            settings=settings,
            env_file=env_file,
            process_runner=fake_runner,
            manage_router=False,
        )
    )

    assert result.rounds_executed == 2
    assert result.stop_reason == "target_satisfied"
    assert [item["resume_id"] for item in result.round_01_candidates] == ["mock-r001"]
    assert [item["resume_id"] for item in result.final_candidates] == ["mock-r002", "mock-r001"]
    assert homes and all(Path(home).is_relative_to(tmp_path / "runs") for home in homes)


def test_run_claude_code_baseline_fails_unseen_final_ids_with_zero_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings(_env_file=None).with_overrides(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        controller_model="openai-chat:deepseek-v3.2",
        wandb_project="seektalent",
    )
    env_file = _env_file(tmp_path)
    failures: list[dict[str, object]] = []

    def fake_runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:  # noqa: ANN003
        del kwargs
        run_dir = Path(command[command.index("--settings") + 1]).parent
        (run_dir / "candidates.json").write_text(
            json.dumps({"mock-r001": _candidate("mock-r001").model_dump(mode="json")}),
            encoding="utf-8",
        )
        (run_dir / "cts_state.json").write_text(
            json.dumps(
                {
                    "total_calls": 1,
                    "first_search_resume_ids": ["mock-r001"],
                    "candidate_ids": ["mock-r001"],
                    "fatal_error": None,
                }
            ),
            encoding="utf-8",
        )
        stdout = json.dumps(
            {
                "result": json.dumps(
                    {
                        "summary": "Bad shortlist.",
                        "stop_reason": "target_satisfied",
                        "ranked_resume_ids": ["mock-missing"],
                    }
                )
            }
        )
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("experiments.claude_code_baseline.harness.log_claude_code_failure_to_wandb", lambda **kwargs: failures.append(kwargs))

    with pytest.raises(ValueError, match="unseen resume ids"):
        asyncio.run(
            run_claude_code_baseline(
                job_title="Python Engineer",
                jd="Python engineer with retrieval experience.",
                notes="",
                settings=settings,
                env_file=env_file,
                process_runner=fake_runner,
                manage_router=False,
            )
        )

    assert failures[0]["rounds_executed"] == 1
    assert "mock-missing" in str(failures[0]["error_message"])


def test_evaluate_claude_code_run_writes_eval_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr("experiments.claude_code_baseline.judge_eval.ResumeJudge", FakeJudge)
    run_dir = tmp_path / "runs" / "claude_code"
    run_dir.mkdir(parents=True)
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge", sha256="hash")

    artifacts = asyncio.run(
        evaluate_claude_code_run(
            settings=settings,
            prompt=prompt,
            run_id="claude-code-run",
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


def test_log_claude_code_to_wandb_uses_report_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
    monkeypatch.setattr("experiments.claude_code_baseline.wandb_logging._upsert_wandb_report", lambda settings: upserts.append(settings.wandb_project))
    settings = AppSettings(_env_file=None, wandb_entity="frankqdwang1-personal-creations", wandb_project="seektalent")

    log_claude_code_to_wandb(settings=settings, artifact_root=artifact_root, evaluation=_evaluation(), rounds_executed=4)

    assert fake_wandb.runs[0].kwargs["config"]["version"] == "claude_code"
    assert fake_wandb.runs[0].kwargs["config"]["seektalent_version"] == "claude_code"
    assert fake_wandb.runs[0].logged[0]["rounds_executed"] == 4
    assert upserts == ["seektalent"]


def test_log_claude_code_to_wandb_does_not_touch_weave(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    artifact_root = tmp_path / "artifacts"
    (artifact_root / "evaluation").mkdir(parents=True)
    (artifact_root / "evaluation" / "evaluation.json").write_text("{}", encoding="utf-8")
    (artifact_root / "raw_resumes").mkdir(parents=True)

    class PoisonWeave:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"Claude Code W&B logging must not touch weave.{name}")

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
    monkeypatch.setattr("experiments.claude_code_baseline.wandb_logging._upsert_wandb_report", lambda settings: None)
    settings = AppSettings(_env_file=None, wandb_project="seektalent")

    log_claude_code_to_wandb(
        settings=settings,
        artifact_root=artifact_root,
        evaluation=_evaluation(),
        rounds_executed=1,
    )


def test_log_claude_code_failure_to_wandb_writes_zero_scores(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
    monkeypatch.setattr("experiments.claude_code_baseline.wandb_logging._upsert_wandb_report", lambda settings: upserts.append(settings.wandb_project))
    settings = AppSettings(_env_file=None, wandb_entity="frankqdwang1-personal-creations", wandb_project="seektalent")

    log_claude_code_failure_to_wandb(
        settings=settings,
        run_id="failed-claude-code-run",
        jd="agent jd",
        rounds_executed=1,
        error_message="Claude Code returned unseen resume ids",
    )

    payload = fake_wandb.runs[0].logged[0]
    assert fake_wandb.runs[0].kwargs["config"]["version"] == "claude_code"
    assert payload["final_total_score"] == 0.0
    assert payload["round_01_total_score"] == 0.0
    assert payload["claude_code_failed"] == 1
    assert "unseen resume ids" in str(payload["claude_code_failure_message"])
    assert upserts == ["seektalent"]
