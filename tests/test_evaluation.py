from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from seektalent.config import AppSettings
from seektalent.evaluation import (
    WANDB_REPORT_TITLE,
    JudgeCache,
    ResumeJudge,
    ResumeJudgeResult,
    _upsert_wandb_report,
    evaluate_run,
    ndcg_at_10,
    precision_at_10,
    snapshot_sha256,
)
from seektalent.models import ResumeCandidate
from seektalent.prompting import LoadedPrompt


class FakeExprConfig:
    def __init__(self, name: str) -> None:
        self.name = name

    def __eq__(self, other: object):  # type: ignore[override]
        return (self.name, other)


def test_snapshot_sha256_is_stable_for_key_order() -> None:
    left = {"b": 2, "a": 1}
    right = {"a": 1, "b": 2}

    assert snapshot_sha256(left) == snapshot_sha256(right)


def test_ndcg_at_10_is_one_for_full_ten_result_perfect_recall() -> None:
    assert ndcg_at_10([3] * 10) == 1.0


def test_ndcg_at_10_penalizes_short_recall_even_when_order_is_perfect() -> None:
    assert ndcg_at_10([3, 2, 2, 1, 0]) == pytest.approx(0.4176267724363269)


def test_ndcg_at_10_returns_zero_for_empty_recall() -> None:
    assert ndcg_at_10([]) == 0.0


def test_precision_at_10_counts_scores_two_and_above() -> None:
    assert precision_at_10([3, 2, 1, 0]) == 0.2


def test_judge_cache_round_trip(tmp_path: Path) -> None:
    cache = JudgeCache(tmp_path)
    try:
        result = ResumeJudgeResult(score=3, rationale="Strong direct match.")
        cache.put(
            jd_sha256_value="jd",
            snapshot_sha256_value="resume",
            model_id="openai-chat:deepseek-v3.2",
            result=result,
        )

        loaded = cache.get(
            jd_sha256_value="jd",
            snapshot_sha256_value="resume",
            model_id="openai-chat:deepseek-v3.2",
        )

        assert loaded == result
    finally:
        cache.close()


def test_evaluate_run_keeps_no_judge_artifacts_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    async def fake_judge_many(self, *, jd, notes, candidates, cache):  # noqa: ANN001
        del self, jd, notes, candidates, cache
        raise RuntimeError("judge failed")

    monkeypatch.setattr("seektalent.evaluation.ResumeJudge.judge_many", fake_judge_many)
    settings = AppSettings(_env_file=None, runs_dir=str(tmp_path / "runs"))
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge prompt", sha256="hash")
    candidate = ResumeCandidate(
        resume_id="resume-1",
        source_resume_id="resume-1",
        snapshot_sha256="snapshot-1",
        dedup_key="resume-1",
        expected_job_category="Engineer",
        now_location="上海",
        work_year=5,
        search_text="engineer",
        raw={"resume_id": "resume-1"},
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with pytest.raises(RuntimeError, match="judge failed"):
        asyncio.run(
            evaluate_run(
                settings=settings,
                prompt=prompt,
                run_id="run-1",
                run_dir=run_dir,
                jd="test jd",
                round_01_candidates=[candidate],
                final_candidates=[candidate],
            )
        )

    assert not (run_dir / "evaluation").exists()
    assert not (run_dir / "raw_resumes").exists()
    assert not (tmp_path / ".seektalent" / "judge_cache.sqlite3").exists()


def test_resume_judge_includes_notes_block_only_when_present(tmp_path: Path) -> None:
    settings = AppSettings(_env_file=None)
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge prompt", sha256="hash")
    candidate = ResumeCandidate(
        resume_id="resume-1",
        source_resume_id="resume-1",
        snapshot_sha256="snapshot-1",
        dedup_key="resume-1",
        expected_job_category="Engineer",
        now_location="上海",
        work_year=5,
        search_text="engineer",
        raw={"resume_id": "resume-1"},
    )
    cache = JudgeCache(tmp_path)
    prompts: list[str] = []

    class FakeAgent:
        async def run(self, prompt_text: str):  # noqa: ANN001
            prompts.append(prompt_text)
            return SimpleNamespace(output=ResumeJudgeResult(score=2, rationale="ok"))

    judge = ResumeJudge(settings, prompt)
    judge._build_agent = lambda: FakeAgent()  # type: ignore[method-assign]
    try:
        asyncio.run(judge.judge_many(jd="JD text", notes="Prefer agent experience", candidates=[candidate], cache=cache))
        asyncio.run(judge.judge_many(jd="JD text", notes="", candidates=[candidate], cache=JudgeCache(tmp_path / "other")))
    finally:
        cache.close()

    assert "JOB_DESCRIPTION" in prompts[0]
    assert "NOTES" in prompts[0]
    assert "Prefer agent experience" in prompts[0]
    assert "RESUME_SNAPSHOT" in prompts[0]
    assert "NOTES" not in prompts[1]


def test_evaluate_run_passes_notes_to_judge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    seen: dict[str, object] = {}

    async def fake_judge_many(self, *, jd, notes, candidates, cache):  # noqa: ANN001
        del self, cache
        seen["jd"] = jd
        seen["notes"] = notes
        result = ResumeJudgeResult(score=3, rationale="Strong fit")
        return (
            {candidate.resume_id: (result, False, 1) for candidate in candidates},
            [("jd", candidate.snapshot_sha256, "openai-responses:gpt-5.4", result) for candidate in candidates],
        )

    monkeypatch.setattr("seektalent.evaluation.ResumeJudge.judge_many", fake_judge_many)
    monkeypatch.setattr("seektalent.evaluation._log_to_wandb", lambda **kwargs: None)
    monkeypatch.setattr("seektalent.evaluation._log_to_weave", lambda **kwargs: None)
    settings = AppSettings(_env_file=None, runs_dir=str(tmp_path / "runs"))
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge prompt", sha256="hash")
    candidate = ResumeCandidate(
        resume_id="resume-1",
        source_resume_id="resume-1",
        snapshot_sha256="snapshot-1",
        dedup_key="resume-1",
        expected_job_category="Engineer",
        now_location="上海",
        work_year=5,
        search_text="engineer",
        raw={"resume_id": "resume-1"},
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    asyncio.run(
        evaluate_run(
            settings=settings,
            prompt=prompt,
            run_id="run-1",
            run_dir=run_dir,
            jd="JD text",
            notes="Notes text",
            round_01_candidates=[candidate],
            final_candidates=[candidate],
        )
    )

    assert seen == {"jd": "JD text", "notes": "Notes text"}


def test_evaluate_run_logs_weave_and_wandb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    class FakePrediction:
        def __init__(self, inputs: dict, output: dict) -> None:
            self.inputs = inputs
            self.output = output
            self.scores: dict[str, object] = {}

        def log_score(self, name: str, value: object) -> None:
            self.scores[name] = value

        def finish(self) -> None:
            return None

    class FakeEvaluationLogger:
        instances: list["FakeEvaluationLogger"] = []

        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs
            self.predictions: list[FakePrediction] = []
            self.views: dict[str, str] = {}
            self.summary: dict | None = None
            self.auto_summarize: bool | None = None
            type(self).instances.append(self)

        def log_prediction(self, *, inputs: dict, output: dict) -> FakePrediction:
            prediction = FakePrediction(inputs, output)
            self.predictions.append(prediction)
            return prediction

        def set_view(self, name: str, content: str, **kwargs) -> None:  # noqa: ANN003
            del kwargs
            self.views[name] = content

        def log_summary(self, summary: dict | None = None, auto_summarize: bool = True) -> None:
            self.summary = summary
            self.auto_summarize = auto_summarize

    init_calls: list[str] = []

    def fake_init(project_name: str):  # noqa: ANN001
        init_calls.append(project_name)
        return object()

    monkeypatch.setitem(
        sys.modules,
        "weave",
        SimpleNamespace(init=fake_init, EvaluationLogger=FakeEvaluationLogger),
    )

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
            self.finished = False

        def log(self, payload: dict[str, object]) -> None:
            self.logged.append(payload)

        def log_artifact(self, artifact: FakeArtifact) -> None:
            self.artifacts.append(artifact)

        def finish(self) -> None:
            self.finished = True

    class FakeSavedReport:
        def __init__(self, url: str) -> None:
            self.url = url
            self.title = ""
            self.description = ""
            self.blocks: list[object] = []
            self.width = "readable"
            self.save_calls = 0

        def save(self) -> None:
            self.save_calls += 1

    class FakeApi:
        def reports(self, path: str, name: str | None = None, per_page: int = 50):  # noqa: ANN001
            del path, name, per_page
            return []

    class FakeWandb:
        def __init__(self) -> None:
            self.runs: list[FakeRun] = []

        def init(self, **kwargs) -> FakeRun:  # noqa: ANN003
            run = FakeRun(**kwargs)
            self.runs.append(run)
            return run

        def Api(self) -> FakeApi:  # noqa: N802
            return FakeApi()

        Artifact = FakeArtifact
        Table = FakeTable

    class FakeSummaryMetric:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeConfig:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeBarPlot:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs

    class FakeRunset:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs

    class FakePanelGrid:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs

    class FakeReport:
        instances: list["FakeReport"] = []

        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs
            self.title = kwargs["title"]
            self.description = kwargs["description"]
            self.blocks = kwargs["blocks"]
            self.width = kwargs["width"]
            self.save_calls = 0
            type(self).instances.append(self)

        @classmethod
        def from_url(cls, url: str) -> FakeSavedReport:
            return FakeSavedReport(url)

        def save(self) -> None:
            self.save_calls += 1

    fake_wandb = FakeWandb()
    monkeypatch.setitem(sys.modules, "wandb", fake_wandb)
    monkeypatch.setitem(
        sys.modules,
        "wandb_workspaces.reports.v2",
        SimpleNamespace(
            BarPlot=FakeBarPlot,
            Config=FakeConfig,
            H1=lambda text: ("H1", text),
            H2=lambda text: ("H2", text),
            P=lambda text: ("P", text),
            PanelGrid=FakePanelGrid,
            Report=FakeReport,
            Runset=FakeRunset,
            SummaryMetric=FakeSummaryMetric,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "wandb_workspaces.reports.v2.interface",
        SimpleNamespace(expr=SimpleNamespace(Config=FakeExprConfig)),
    )

    async def fake_judge_many(self, *, jd, notes, candidates, cache):  # noqa: ANN001
        del self, jd, notes, cache
        result = ResumeJudgeResult(score=3, rationale="Strong fit")
        return (
            {candidate.resume_id: (result, False, 1) for candidate in candidates},
            [("jd", candidate.snapshot_sha256, "openai-responses:gpt-5.4", result) for candidate in candidates],
        )

    monkeypatch.setattr("seektalent.evaluation.ResumeJudge.judge_many", fake_judge_many)
    settings = AppSettings(
        _env_file=None,
        runs_dir=str(tmp_path / "runs"),
        enable_eval=True,
        wandb_entity="frankqdwang1-personal-creations",
        wandb_project="seektalent",
        weave_entity="frankqdwang1-personal-creations",
        weave_project="seektalent",
        judge_model="openai-responses:gpt-5.4",
    )
    prompt = LoadedPrompt(name="judge", path=tmp_path / "judge.md", content="judge prompt", sha256="hash")
    candidate = ResumeCandidate(
        resume_id="resume-1",
        source_resume_id="resume-1",
        snapshot_sha256="snapshot-1",
        dedup_key="resume-1",
        expected_job_category="Engineer",
        now_location="上海",
        work_year=5,
        search_text="engineer",
        raw={"resume_id": "resume-1"},
    )

    first_run_dir = tmp_path / "run-1"
    first_run_dir.mkdir()
    artifacts = asyncio.run(
        evaluate_run(
            settings=settings,
            prompt=prompt,
            run_id="run-1",
            run_dir=first_run_dir,
            jd="test jd",
            round_01_candidates=[candidate],
            final_candidates=[candidate],
        )
    )

    assert init_calls == ["frankqdwang1-personal-creations/seektalent"]
    assert len(FakeEvaluationLogger.instances) == 2
    assert FakeEvaluationLogger.instances[0].summary == {
        "ndcg_at_10": pytest.approx(0.22009176629808017),
        "precision_at_10": 0.1,
        "total_score": pytest.approx(0.13602752988942404),
    }
    assert FakeEvaluationLogger.instances[0].auto_summarize is False
    assert "SeekTalent version" in FakeEvaluationLogger.instances[0].views["summary"]
    assert fake_wandb.runs[0].kwargs["config"]["seektalent_version"] == "0.2.4"
    assert fake_wandb.runs[0].kwargs["config"]["eval_enabled"] is True
    assert any("final_total_score" in payload for payload in fake_wandb.runs[0].logged)
    assert fake_wandb.runs[0].artifacts
    assert FakeReport.instances[0].title == WANDB_REPORT_TITLE
    panel_grids = [block for block in FakeReport.instances[0].blocks if isinstance(block, FakePanelGrid)]
    assert len(panel_grids) == 2
    assert sum(len(panel.kwargs["panels"]) for panel in panel_grids) == 6
    assert artifacts.result.final.total_score == pytest.approx(0.13602752988942404)


def test_upsert_wandb_report_reuses_existing_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    saved_report = SimpleNamespace(
        title="Old",
        description="Old",
        blocks=[],
        width="readable",
        save_calls=0,
        save=lambda: None,
    )

    def _save() -> None:
        saved_report.save_calls += 1

    saved_report.save = _save

    class FakeApi:
        def reports(self, path: str, name: str | None = None, per_page: int = 50):  # noqa: ANN001
            del path, name, per_page
            return [SimpleNamespace(url="https://example.com/report")]

    class FakeWandb:
        def Api(self) -> FakeApi:  # noqa: N802
            return FakeApi()

    class FakeReport:
        @classmethod
        def from_url(cls, url: str):  # noqa: ANN001
            assert url == "https://example.com/report"
            return saved_report

    monkeypatch.setitem(sys.modules, "wandb", FakeWandb())
    monkeypatch.setitem(
        sys.modules,
        "wandb_workspaces.reports.v2",
        SimpleNamespace(
            BarPlot=lambda **kwargs: ("BarPlot", kwargs),
            Config=lambda name: ("Config", name),
            H1=lambda text: ("H1", text),
            H2=lambda text: ("H2", text),
            P=lambda text: ("P", text),
            PanelGrid=lambda **kwargs: ("PanelGrid", kwargs),
            Report=FakeReport,
            Runset=lambda **kwargs: ("Runset", kwargs),
            SummaryMetric=lambda name: ("SummaryMetric", name),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "wandb_workspaces.reports.v2.interface",
        SimpleNamespace(expr=SimpleNamespace(Config=FakeExprConfig)),
    )

    settings = AppSettings(
        _env_file=None,
        wandb_entity="frankqdwang1-personal-creations",
        wandb_project="seektalent",
    )

    _upsert_wandb_report(settings)

    assert saved_report.title == WANDB_REPORT_TITLE
    assert saved_report.width == "fluid"
    assert saved_report.save_calls == 1
