from __future__ import annotations

from pathlib import Path

from seektalent.api import MatchRunResult, run_match, run_match_debug
from seektalent.models import FinalCandidate, FinalResult
from seektalent.runtime import RunArtifacts
from seektalent.runtime.production_contract import ProductionMatchResultV1, SourceSelectionV1
from tests.settings_factory import make_settings


def _artifacts(tmp_path: Path) -> RunArtifacts:
    trace_log = tmp_path / "trace.log"
    trace_log.write_text("debug", encoding="utf-8")
    return RunArtifacts(
        final_result=FinalResult(
            run_id="run-1",
            run_dir=str(tmp_path),
            rounds_executed=1,
            stop_reason="controller_stop",
            summary="done",
            candidates=[
                FinalCandidate(
                    resume_id="resume-1",
                    rank=1,
                    final_score=92,
                    fit_bucket="fit",
                    source_provider="cts",
                    evidence_level="card",
                    detail_open_status="not_supported",
                    match_summary="Strong match.",
                    strengths=[],
                    weaknesses=[],
                    matched_must_haves=["Python"],
                    matched_preferences=[],
                    risk_flags=[],
                    why_selected="Best fit.",
                    source_round=1,
                )
            ],
        ),
        final_markdown="# debug",
        run_id="run-1",
        run_dir=tmp_path,
        trace_log_path=trace_log,
        candidate_store={},
        normalized_store={},
        evaluation_result=None,
        terminal_stop_guidance=None,
    )


def test_run_match_defaults_to_prod_core_contract(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeRuntime:
        def __init__(self, settings, **kwargs):  # noqa: ANN001
            del settings, kwargs

        def run(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return _artifacts(tmp_path)

    monkeypatch.setattr("seektalent.api.build_source_enabled_runtime", FakeRuntime)

    settings = make_settings(mock_cts=True, max_rounds=6, search_max_pages_per_round=3)

    result = run_match(
        job_title="Python Engineer",
        jd="JD",
        notes="Notes",
        settings=settings,
        env_file=None,
        source_selection=SourceSelectionV1(required=("cts",), optional=("liepin",)),
    )

    assert isinstance(result, ProductionMatchResultV1)
    assert result.runtime_profile == "prod_core"
    assert result.input_digest
    assert result.final_candidates[0].candidate_id == "resume-1"
    assert result.runtime_constraints is not None
    assert result.runtime_constraints.max_rounds == 6
    assert result.runtime_constraints.search_max_pages_per_round == 3
    assert captured["source_kinds"] == ("cts", "liepin")
    assert "run_dir" not in result.model_dump()


def test_run_match_debug_keeps_legacy_artifact_contract(monkeypatch, tmp_path: Path) -> None:
    class FakeRuntime:
        def __init__(self, settings, **kwargs):  # noqa: ANN001
            del settings, kwargs

        def run(self, **kwargs):  # noqa: ANN003
            del kwargs
            return _artifacts(tmp_path)

    monkeypatch.setattr("seektalent.api.build_source_enabled_runtime", FakeRuntime)

    result = run_match_debug(
        job_title="Python Engineer",
        jd="JD",
        settings=make_settings(mock_cts=True),
        env_file=None,
    )

    assert isinstance(result, MatchRunResult)
    assert result.run_dir == tmp_path
    assert result.final_markdown == "# debug"
