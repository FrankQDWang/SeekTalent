from __future__ import annotations

import argparse
from datetime import datetime
from typing import Any

import wandb
from wandb_workspaces.reports.v2 import BarPlot, H1, H2, MarkdownBlock, P, PanelGrid, Report, Runset
from wandb_workspaces.reports.v2.interface import expr


WANDB_REPORT_TITLE = "SeekTalent Version Metrics"
REQUIRED_SUMMARY_KEYS = (
    "final_total_score",
    "final_precision_at_10",
    "final_ndcg_at_10",
    "round_01_total_score",
    "round_01_precision_at_10",
    "round_01_ndcg_at_10",
)


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _fetch_runs(entity: str, project: str) -> list[dict[str, Any]]:
    api = wandb.Api()
    rows: list[dict[str, Any]] = []
    for run in api.runs(f"{entity}/{project}"):
        row = {
            "run_name": run.name,
            "run_url": run.url,
            "created_at": run.created_at,
            "state": run.state,
            "eval_enabled": bool(run.config.get("eval_enabled")),
            "seektalent_version": run.config.get("seektalent_version"),
            "judge_model": run.config.get("judge_model"),
            "final_total_score": run.summary.get("final_total_score"),
            "final_precision_at_10": run.summary.get("final_precision_at_10"),
            "final_ndcg_at_10": run.summary.get("final_ndcg_at_10"),
            "round_01_total_score": run.summary.get("round_01_total_score"),
            "round_01_precision_at_10": run.summary.get("round_01_precision_at_10"),
            "round_01_ndcg_at_10": run.summary.get("round_01_ndcg_at_10"),
        }
        if row["state"] != "finished" or not row["eval_enabled"] or not row["seektalent_version"]:
            continue
        if any(row[key] is None for key in REQUIRED_SUMMARY_KEYS):
            continue
        rows.append(row)
    return rows


def _best_runs_by_version_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_version: dict[str, dict[str, Any]] = {}
    for run in runs:
        version = str(run["seektalent_version"])
        current = best_by_version.get(version)
        if current is None:
            best_by_version[version] = run
            continue
        current_score = float(current["final_total_score"])
        candidate_score = float(run["final_total_score"])
        if candidate_score > current_score:
            best_by_version[version] = run
            continue
        if candidate_score == current_score and _parse_timestamp(str(run["created_at"])) > _parse_timestamp(
            str(current["created_at"])
        ):
            best_by_version[version] = run
    return [best_by_version[version] for version in sorted(best_by_version.keys(), reverse=True)]


def _version_means_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        grouped.setdefault(str(run["seektalent_version"]), []).append(run)
    rows: list[dict[str, Any]] = []
    for version in sorted(grouped.keys(), reverse=True):
        bucket = grouped[version]
        rows.append(
            {
                "version": version,
                "run_count": len(bucket),
                "final_total_mean": sum(float(run["final_total_score"]) for run in bucket) / len(bucket),
                "final_precision_mean": sum(float(run["final_precision_at_10"]) for run in bucket) / len(bucket),
                "final_ndcg_mean": sum(float(run["final_ndcg_at_10"]) for run in bucket) / len(bucket),
                "round1_total_mean": sum(float(run["round_01_total_score"]) for run in bucket) / len(bucket),
                "round1_precision_mean": sum(float(run["round_01_precision_at_10"]) for run in bucket) / len(bucket),
                "round1_ndcg_mean": sum(float(run["round_01_ndcg_at_10"]) for run in bucket) / len(bucket),
            }
        )
    return rows


def _best_runs_markdown(runs: list[dict[str, Any]]) -> str:
    rows = _best_runs_by_version_rows(runs)
    if not rows:
        return "No successful eval-enabled runs yet."
    lines = [
        "| Version | Best run | Created | Judge model | Final total | Final p@10 | Final ndcg@10 | Round1 total | Round1 p@10 | Round1 ndcg@10 |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["seektalent_version"]),
                    f"[{row['run_name']}]({row['run_url']})",
                    str(row["created_at"]),
                    str(row["judge_model"]),
                    f"{float(row['final_total_score']):.4f}",
                    f"{float(row['final_precision_at_10']):.4f}",
                    f"{float(row['final_ndcg_at_10']):.4f}",
                    f"{float(row['round_01_total_score']):.4f}",
                    f"{float(row['round_01_precision_at_10']):.4f}",
                    f"{float(row['round_01_ndcg_at_10']):.4f}",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _version_means_summary_markdown(runs: list[dict[str, Any]]) -> str:
    rows = _version_means_rows(runs)
    if not rows:
        return "No successful eval-enabled runs yet."
    lines = [
        "| Version | Runs | Final total mean | Final p@10 mean | Final ndcg@10 mean | Round1 total mean | Round1 p@10 mean | Round1 ndcg@10 mean |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["version"]),
                    str(row["run_count"]),
                    f"{row['final_total_mean']:.4f}",
                    f"{row['final_precision_mean']:.4f}",
                    f"{row['final_ndcg_mean']:.4f}",
                    f"{row['round1_total_mean']:.4f}",
                    f"{row['round1_precision_mean']:.4f}",
                    f"{row['round1_ndcg_mean']:.4f}",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _metric_panel(stage: str, metric_key: str, title: str) -> BarPlot:
    return BarPlot(
        title=title,
        metrics=[f"{stage}_{metric_key}"],
        groupby="config.seektalent_version",
        groupby_aggfunc="mean",
        orientation="v",
        title_x="SeekTalent version",
        title_y="Mean",
    )


def _blocks(entity: str, project: str, runs: list[dict[str, Any]]) -> list[object]:
    runset = Runset(
        entity=entity,
        project=project,
        name="Successful Eval Runs",
        filters=[
            expr.Config("eval_enabled") == True,  # noqa: E712
            expr.Summary("final_total_score") >= 0,
        ],
    )
    return [
        H1(WANDB_REPORT_TITLE),
        P(
            "This report compares successful eval-enabled SeekTalent runs by version. "
            "Eval-off smoke tests are excluded. Each bar shows the mean metric value aggregated from W&B runs."
        ),
        H2("Best Runs By Version"),
        MarkdownBlock(text=_best_runs_markdown(runs)),
        H2("Version Means"),
        H2("Final Metrics"),
        PanelGrid(
            runsets=[runset],
            panels=[
                _metric_panel("final", "total_score", "Final total_score"),
                _metric_panel("final", "precision_at_10", "Final precision@10"),
                _metric_panel("final", "ndcg_at_10", "Final ndcg@10"),
            ],
        ),
        H2("Round 1 Metrics"),
        PanelGrid(
            runsets=[runset],
            panels=[
                _metric_panel("round_01", "total_score", "Round 1 total_score"),
                _metric_panel("round_01", "precision_at_10", "Round 1 precision@10"),
                _metric_panel("round_01", "ndcg_at_10", "Round 1 ndcg@10"),
            ],
        ),
        H2("Version Means Summary"),
        MarkdownBlock(text=_version_means_summary_markdown(runs)),
    ]


def _upsert_report(entity: str, project: str) -> str:
    api = wandb.Api()
    blocks = _blocks(entity, project, _fetch_runs(entity, project))
    reports = list(api.reports(f"{entity}/{project}", per_page=100))
    matches = [
        report
        for report in reports
        if getattr(report, "display_name", None) == WANDB_REPORT_TITLE or getattr(report, "title", None) == WANDB_REPORT_TITLE
    ]
    existing = matches[0] if matches else None
    if existing is None:
        report = Report(
            project=project,
            entity=entity,
            title=WANDB_REPORT_TITLE,
            description="Version-level SeekTalent eval metrics.",
            blocks=blocks,
            width="fluid",
        )
    else:
        report = Report.from_url(existing.url)
        report.title = WANDB_REPORT_TITLE
        report.description = "Version-level SeekTalent eval metrics."
        report.blocks = blocks
        report.width = "fluid"
    report.save()
    saved_url = getattr(report, "url", None) or getattr(existing, "url", "")
    for duplicate in matches[1:]:
        if getattr(duplicate, "url", None) == saved_url:
            continue
        Report.from_url(duplicate.url).delete()
    return saved_url


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the SeekTalent W&B report from finished runs.")
    parser.add_argument("--entity", default="frankqdwang1-personal-creations")
    parser.add_argument("--project", default="seektalent")
    args = parser.parse_args()
    print(_upsert_report(args.entity, args.project))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
