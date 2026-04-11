from __future__ import annotations

import json
from pathlib import Path

from seektalent.replay_tuning import (
    _business_case_pass,
    profile_grid_v1,
    run_replay_tuning,
)
from seektalent.resources import repo_root


def test_profile_grid_v1_has_36_stable_profiles() -> None:
    profiles = profile_grid_v1()

    assert len(profiles) == 36
    assert len({profile.profile_id for profile in profiles}) == 36
    assert profiles[0].profile_id == "selection=baseline__stop=baseline__rewrite=baseline"
    assert profiles[-1].profile_id == "selection=freshness_heavy__stop=aggressive_stop__rewrite=coherence_heavy"


def test_business_case_pass_ignores_numeric_metrics() -> None:
    assert _business_case_pass(
        {
            "metrics": [
                {"name": "route_match", "value": True},
                {"name": "stop_reason_match", "value": True},
                {"name": "round_count", "value": 0},
            ]
        }
    )


def test_replay_tuning_smoke_writes_json_and_markdown(tmp_path: Path) -> None:
    output_dir = tmp_path / "report"
    report = run_replay_tuning(
        repo_root=repo_root(),
        output_dir=output_dir,
        profile_ids={"selection=baseline__stop=baseline__rewrite=baseline"},
        case_set="canonical",
        case_ids={"case-bootstrap-inferred-single-pack"},
    )

    json_report = output_dir / "replay-tuning-report.json"
    markdown_report = output_dir / "replay-tuning-report.md"

    assert report["profile_count"] == 1
    assert json.loads(json_report.read_text(encoding="utf-8"))["profile_count"] == 1
    assert "# Replay Tuning Report" in markdown_report.read_text(encoding="utf-8")


def test_replay_tuning_profile_ranking_is_deterministic_on_tie(tmp_path: Path) -> None:
    report = run_replay_tuning(
        repo_root=repo_root(),
        output_dir=tmp_path / "report",
        profile_ids={
            "selection=baseline__stop=baseline__rewrite=baseline",
            "selection=baseline__stop=baseline__rewrite=must_have_heavy",
        },
        case_set="canonical",
        case_ids={"case-bootstrap-inferred-single-pack"},
    )

    assert [profile["profile_id"] for profile in report["profiles"]] == [
        "selection=baseline__stop=baseline__rewrite=baseline",
        "selection=baseline__stop=baseline__rewrite=must_have_heavy",
    ]


def test_replay_tuning_does_not_write_tracked_canonical_artifacts(tmp_path: Path) -> None:
    tracked_paths = [
        repo_root() / "artifacts" / "runtime" / "cases",
        repo_root() / "artifacts" / "runtime" / "evals",
        repo_root() / "docs" / "v-0.3.2" / "traces",
        repo_root() / "docs" / "v-0.3.2" / "trace-index.md",
    ]
    before = _snapshot_files(tracked_paths)
    run_replay_tuning(
        repo_root=repo_root(),
        output_dir=tmp_path / "report",
        profile_ids={"selection=baseline__stop=baseline__rewrite=baseline"},
        case_set="canonical",
        case_ids={"case-bootstrap-inferred-single-pack"},
    )
    after = _snapshot_files(tracked_paths)

    assert after == before


def test_replay_tuning_rewrite_coherence_tradeoff_differs_by_profile(tmp_path: Path) -> None:
    report = run_replay_tuning(
        repo_root=repo_root(),
        output_dir=tmp_path / "report",
        profile_ids={
            "selection=baseline__stop=baseline__rewrite=baseline",
            "selection=baseline__stop=baseline__rewrite=coherence_heavy",
        },
        case_set="tuning",
        case_ids={"rewrite_coherence_tradeoff"},
    )

    summaries = {
        profile["profile_id"]: profile["case_summaries"][0]
        for profile in report["profiles"]
    }

    assert summaries["selection=baseline__stop=baseline__rewrite=baseline"]["last_query_terms"] != summaries[
        "selection=baseline__stop=baseline__rewrite=coherence_heavy"
    ]["last_query_terms"]


def _snapshot_files(paths: list[Path]) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for path in paths:
        if path.is_file():
            snapshot[str(path)] = path.read_bytes()
            continue
        for file_path in sorted(item for item in path.rglob("*") if item.is_file()):
            snapshot[str(file_path)] = file_path.read_bytes()
    return snapshot
