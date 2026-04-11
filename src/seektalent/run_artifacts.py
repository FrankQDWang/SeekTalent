from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from seektalent.models import SearchRunBundle, SearchRunEval, SearchRunEvalMetric
from seektalent.query_terms import query_terms_hit


RUNTIME_STATUS = "v0.3.2_offline_artifacts_active"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_run_id(*, job_description_sha256: str, created_at_utc: datetime) -> str:
    return f"{utc_timestamp(created_at_utc)}_{job_description_sha256[:8]}"


def build_search_run_eval(bundle: SearchRunBundle) -> SearchRunEval:
    search_rounds = [round_artifact for round_artifact in bundle.rounds if round_artifact.execution_result]
    scored_rows = [
        row
        for round_artifact in bundle.rounds
        if round_artifact.scoring_result is not None
        for row in round_artifact.scoring_result.scored_candidates
    ]
    total_runtime_audit_tags = sum(
        len(tags)
        for round_artifact in bundle.rounds
        for tags in round_artifact.runtime_audit_tags.values()
    )
    prompt_surfaces = [
        bundle.bootstrap.requirement_extraction_audit.prompt_surface,
        bundle.bootstrap.bootstrap_keyword_generation_audit.prompt_surface,
        bundle.finalization_audit.prompt_surface,
        *[round_artifact.controller_audit.prompt_surface for round_artifact in bundle.rounds],
        *[
            round_artifact.branch_evaluation_audit.prompt_surface
            for round_artifact in bundle.rounds
            if round_artifact.branch_evaluation_audit is not None
        ],
    ]
    search_round_indexes = [round_artifact.runtime_round_index for round_artifact in search_rounds]
    search_phase_by_search_round = [
        round_artifact.controller_context.runtime_budget_state.search_phase
        for round_artifact in search_rounds
    ]
    selected_operator_by_search_round = [
        round_artifact.controller_decision.selected_operator_name for round_artifact in search_rounds
    ]
    eligible_open_node_count_by_search_round = [
        len(round_artifact.controller_context.selection_ranking) for round_artifact in search_rounds
    ]
    selection_margin_by_search_round = [
        _selection_margin(round_artifact) for round_artifact in search_rounds
    ]
    must_have_query_coverage_by_search_round = [
        _must_have_query_coverage(bundle, round_artifact) for round_artifact in search_rounds
    ]
    net_new_shortlist_gain_by_search_round = [
        _net_new_shortlist_gain(round_artifact) for round_artifact in search_rounds
    ]
    run_shortlist_size_after_search_round = [
        len(round_artifact.frontier_state_after.run_shortlist_candidate_ids)
        for round_artifact in search_rounds
    ]
    operator_distribution_by_phase = {
        phase: dict(
            Counter(
                round_artifact.controller_decision.selected_operator_name
                for round_artifact in search_rounds
                if round_artifact.controller_context.runtime_budget_state.search_phase == phase
            )
        )
        for phase in ("explore", "balance", "harvest")
    }
    metrics = [
        SearchRunEvalMetric(
            name="routing_mode",
            value=bundle.bootstrap.routing_result.routing_mode,
        ),
        SearchRunEvalMetric(
            name="selected_knowledge_pack_ids",
            value=bundle.bootstrap.routing_result.selected_knowledge_pack_ids,
        ),
        SearchRunEvalMetric(
            name="routing_confidence",
            value=bundle.bootstrap.routing_result.routing_confidence,
        ),
        SearchRunEvalMetric(name="round_count", value=len(bundle.rounds)),
        SearchRunEvalMetric(name="search_round_count", value=len(search_rounds)),
        SearchRunEvalMetric(
            name="search_round_indexes",
            value=search_round_indexes,
        ),
        SearchRunEvalMetric(
            name="search_phase_by_search_round",
            value=search_phase_by_search_round,
        ),
        SearchRunEvalMetric(
            name="selected_operator_by_search_round",
            value=selected_operator_by_search_round,
        ),
        SearchRunEvalMetric(
            name="eligible_open_node_count_by_search_round",
            value=eligible_open_node_count_by_search_round,
        ),
        SearchRunEvalMetric(
            name="selection_margin_by_search_round",
            value=selection_margin_by_search_round,
        ),
        SearchRunEvalMetric(
            name="must_have_query_coverage_by_search_round",
            value=must_have_query_coverage_by_search_round,
        ),
        SearchRunEvalMetric(
            name="net_new_shortlist_gain_by_search_round",
            value=net_new_shortlist_gain_by_search_round,
        ),
        SearchRunEvalMetric(
            name="run_shortlist_size_after_search_round",
            value=run_shortlist_size_after_search_round,
        ),
        SearchRunEvalMetric(
            name="operator_distribution_explore",
            value=operator_distribution_by_phase["explore"],
        ),
        SearchRunEvalMetric(
            name="operator_distribution_balance",
            value=operator_distribution_by_phase["balance"],
        ),
        SearchRunEvalMetric(
            name="operator_distribution_harvest",
            value=operator_distribution_by_phase["harvest"],
        ),
        SearchRunEvalMetric(
            name="stop_reason",
            value=bundle.final_result.stop_reason,
        ),
        SearchRunEvalMetric(
            name="final_shortlist_count",
            value=len(bundle.final_result.final_shortlist_candidate_ids),
        ),
        SearchRunEvalMetric(
            name="top_candidate_id",
            value=(
                bundle.final_result.final_shortlist_candidate_ids[0]
                if bundle.final_result.final_shortlist_candidate_ids
                else ""
            ),
        ),
        SearchRunEvalMetric(
            name="total_pages_fetched",
            value=sum(
                round_artifact.execution_result.search_page_statistics.pages_fetched
                for round_artifact in search_rounds
            ),
        ),
        SearchRunEvalMetric(
            name="deduplicated_candidate_count",
            value=sum(
                len(round_artifact.execution_result.deduplicated_candidates)
                for round_artifact in search_rounds
            ),
        ),
        SearchRunEvalMetric(
            name="average_novelty",
            value=(
                0.0
                if not bundle.rounds
                else sum(
                    round_artifact.branch_evaluation.novelty_score
                    for round_artifact in bundle.rounds
                    if round_artifact.branch_evaluation is not None
                )
                / max(
                    1,
                    sum(
                        1
                        for round_artifact in bundle.rounds
                        if round_artifact.branch_evaluation is not None
                    ),
                )
            ),
        ),
        SearchRunEvalMetric(
            name="average_usefulness",
            value=(
                0.0
                if not bundle.rounds
                else sum(
                    round_artifact.branch_evaluation.usefulness_score
                    for round_artifact in bundle.rounds
                    if round_artifact.branch_evaluation is not None
                )
                / max(
                    1,
                    sum(
                        1
                        for round_artifact in bundle.rounds
                        if round_artifact.branch_evaluation is not None
                    ),
                )
            ),
        ),
        SearchRunEvalMetric(
            name="average_stability_risk",
            value=(
                0.0
                if not scored_rows
                else sum(row.risk_score for row in scored_rows) / len(scored_rows)
            ),
        ),
        SearchRunEvalMetric(
            name="runtime_audit_tag_count",
            value=total_runtime_audit_tags,
        ),
        SearchRunEvalMetric(
            name="prompt_surface_count",
            value=len(prompt_surfaces),
        ),
        SearchRunEvalMetric(
            name="budget_warning_round_count",
            value=sum(
                1
                for round_artifact in bundle.rounds
                if _has_budget_warning(round_artifact.controller_audit.prompt_surface)
                or (
                    round_artifact.branch_evaluation_audit is not None
                    and _has_budget_warning(
                        round_artifact.branch_evaluation_audit.prompt_surface
                    )
                )
            ),
        ),
        SearchRunEvalMetric(
            name="llm_validator_retry_count",
            value=(
                bundle.bootstrap.requirement_extraction_audit.validator_retry_count
                + bundle.bootstrap.bootstrap_keyword_generation_audit.validator_retry_count
                + bundle.finalization_audit.validator_retry_count
                + sum(round_artifact.controller_audit.validator_retry_count for round_artifact in bundle.rounds)
                + sum(
                    round_artifact.branch_evaluation_audit.validator_retry_count
                    for round_artifact in bundle.rounds
                    if round_artifact.branch_evaluation_audit is not None
                )
            ),
        ),
    ]
    return SearchRunEval(experiment_id="E5", run_id=bundle.run_id, metrics=metrics)


def write_run_bundle(bundle: SearchRunBundle, *, runs_root: Path) -> Path:
    run_dir = runs_root / bundle.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "bundle.json", bundle.model_dump(mode="json"))
    _write_json(
        run_dir / "final_result.json",
        bundle.final_result.model_dump(mode="json"),
    )
    if bundle.eval is not None:
        _write_json(run_dir / "eval.json", bundle.eval.model_dump(mode="json"))
    return run_dir


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _has_budget_warning(prompt_surface: object) -> bool:
    sections = getattr(prompt_surface, "sections", [])
    return any(getattr(section, "title", "") == "Budget Warning" for section in sections)


def _selection_margin(round_artifact: object) -> float:
    ranking = round_artifact.controller_context.selection_ranking
    if len(ranking) < 2:
        return 0.0
    return ranking[0].breakdown.final_selection_score - ranking[1].breakdown.final_selection_score


def _must_have_query_coverage(bundle: SearchRunBundle, round_artifact: object) -> float:
    capabilities = bundle.bootstrap.requirement_sheet.must_have_capabilities
    hit_count = sum(
        query_terms_hit(round_artifact.execution_plan.query_terms, capability)
        for capability in capabilities
    )
    return hit_count / max(1, len(capabilities))


def _net_new_shortlist_gain(round_artifact: object) -> int:
    node_shortlist_ids = set(round_artifact.scoring_result.node_shortlist_candidate_ids)
    run_shortlist_ids_before = set(round_artifact.frontier_state_before.run_shortlist_candidate_ids)
    return len(node_shortlist_ids - run_shortlist_ids_before)


__all__ = [
    "RUNTIME_STATUS",
    "build_run_id",
    "build_search_run_eval",
    "utc_isoformat",
    "utc_now",
    "write_run_bundle",
]
