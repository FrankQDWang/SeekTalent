from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from seektalent.bootstrap_assets import BootstrapAssets, default_bootstrap_assets
from seektalent.canonical_cases import (
    _branch_payload,
    _candidate,
    _llm_keyword_payload,
    _llm_pack_scores,
    _llm_requirement_payload,
    _run_case,
    _runtime_assets,
    _search_payload,
    _stop_payload,
    build_case_bundle,
    build_case_eval,
    canonical_case_specs,
)
from seektalent.candidate_text import build_candidate_search_text
from seektalent.clients.cts_client import CTSFetchResult
from seektalent.frontier_ops import generate_search_controller_decision, select_active_frontier_node
from seektalent.models import (
    FrontierNode_t,
    FrontierState_t,
    PhaseSelectionWeights,
    RetrievedCandidate_t,
    RewriteTermCandidate,
    RewriteTermScoreBreakdown,
    RewriteFitnessWeights,
    ScoringPolicy,
    SearchControllerDecisionDraft_t,
    SearchRoundArtifact,
    RuntimeSearchBudget,
    RuntimeSelectionPolicy,
    RequirementSheet,
    SearchRunBundle,
    StopGuardThresholds,
)
from seektalent.runtime_budget import build_runtime_budget_state
from seektalent.run_artifacts import build_search_run_eval


@dataclass(frozen=True)
class ReplayTuningProfile:
    profile_id: str
    selection_policy: RuntimeSelectionPolicy
    stop_guard_thresholds: StopGuardThresholds
    rewrite_fitness_weights: RewriteFitnessWeights


@dataclass(frozen=True)
class ReplayTuningCase:
    case_id: str
    scenario: str
    bundle_factory: Callable[[ReplayTuningProfile], SearchRunBundle]


def profile_grid_v1() -> list[ReplayTuningProfile]:
    baseline_selection = RuntimeSelectionPolicy()
    baseline_stop = StopGuardThresholds()
    baseline_rewrite = RewriteFitnessWeights()
    selection_profiles = {
        "baseline": baseline_selection,
        "coverage_heavy": RuntimeSelectionPolicy(
            explore=_selection_weights((0.5, 1.8, 1.4, 0.2, 0.9, 0.35)),
            balance=_selection_weights((0.95, 1.05, 0.95, 0.75, 0.35, 0.75)),
            harvest=_selection_weights((1.3, 0.3, 0.3, 1.1, 0.0, 1.1)),
        ),
        "exploit_heavy": RuntimeSelectionPolicy(
            explore=_selection_weights((0.7, 1.3, 1.0, 0.3, 0.7, 0.5)),
            balance=_selection_weights((1.1, 0.9, 0.7, 0.9, 0.25, 0.9)),
            harvest=_selection_weights((1.6, 0.2, 0.15, 1.35, 0.0, 1.3)),
        ),
        "freshness_heavy": RuntimeSelectionPolicy(
            explore=_selection_weights((0.55, 1.7, 1.15, 0.2, 1.0, 0.4)),
            balance=_selection_weights((0.95, 1.1, 0.8, 0.75, 0.5, 0.8)),
            harvest=baseline_selection.harvest,
        ),
    }
    stop_profiles = {
        "baseline": baseline_stop,
        "conservative_stop": StopGuardThresholds(
            novelty_floor=0.20,
            usefulness_floor=0.20,
            reward_floor=1.2,
        ),
        "aggressive_stop": StopGuardThresholds(
            novelty_floor=0.30,
            usefulness_floor=0.30,
            reward_floor=1.8,
        ),
    }
    rewrite_profiles = {
        "baseline": baseline_rewrite,
        "must_have_heavy": RewriteFitnessWeights(
            must_have_repair=1.8,
            anchor_preservation=0.9,
            rewrite_coherence=1.0,
            provenance_coherence=0.7,
            query_length_penalty=0.35,
            redundancy_penalty=0.45,
        ),
        "coherence_heavy": RewriteFitnessWeights(
            must_have_repair=1.2,
            anchor_preservation=1.1,
            rewrite_coherence=1.6,
            provenance_coherence=1.0,
            query_length_penalty=0.35,
            redundancy_penalty=0.45,
        ),
    }
    profiles: list[ReplayTuningProfile] = []
    for selection_name, selection_policy in selection_profiles.items():
        for stop_name, stop_thresholds in stop_profiles.items():
            for rewrite_name, rewrite_weights in rewrite_profiles.items():
                profiles.append(
                    ReplayTuningProfile(
                        profile_id=(
                            f"selection={selection_name}"
                            f"__stop={stop_name}"
                            f"__rewrite={rewrite_name}"
                        ),
                        selection_policy=selection_policy,
                        stop_guard_thresholds=stop_thresholds,
                        rewrite_fitness_weights=rewrite_weights,
                    )
                )
    return profiles


def canonical_replay_cases(*, repo_root: Path, runs_root: Path) -> list[ReplayTuningCase]:
    cases: list[ReplayTuningCase] = []
    for spec in canonical_case_specs():
        cases.append(
            ReplayTuningCase(
                case_id=spec.case_id,
                scenario=spec.scenario,
                bundle_factory=lambda profile, spec=spec: build_case_bundle(
                    spec,
                    repo_root=repo_root,
                    assets_override=_profile_assets(profile),
                    runs_dir_override=runs_root / "canonical" / profile.profile_id / spec.case_id,
                ),
            )
        )
    return cases


def synthetic_replay_cases(*, repo_root: Path, runs_root: Path) -> list[ReplayTuningCase]:
    return [
        ReplayTuningCase(
            case_id="selection_tradeoff",
            scenario="selection coverage-vs-exploit tension",
            bundle_factory=lambda profile: _build_selection_tradeoff_bundle(
                repo_root=repo_root,
                assets=_selection_tradeoff_assets(_profile_assets(profile)),
                runs_dir=runs_root / "tuning" / profile.profile_id / "selection_tradeoff",
            ),
        ),
        ReplayTuningCase(
            case_id="harvest_stop_threshold",
            scenario="harvest stop threshold sensitivity",
            bundle_factory=lambda profile: _build_harvest_stop_threshold_bundle(
                repo_root=repo_root,
                assets=_harvest_stop_assets(_profile_assets(profile)),
                runs_dir=runs_root / "tuning" / profile.profile_id / "harvest_stop_threshold",
            ),
        ),
        ReplayTuningCase(
            case_id="rewrite_evidence_productive",
            scenario="rewrite evidence pool changes final rewrite",
            bundle_factory=lambda profile: _build_rewrite_evidence_productive_bundle(
                repo_root=repo_root,
                assets=_rewrite_evidence_assets(_profile_assets(profile)),
                runs_dir=runs_root / "tuning" / profile.profile_id / "rewrite_evidence_productive",
            ),
        ),
        ReplayTuningCase(
            case_id="rewrite_coherence_tradeoff",
            scenario="rewrite coherence scoring changes final rewrite",
            bundle_factory=lambda profile: _build_rewrite_coherence_tradeoff_bundle(
                repo_root=repo_root,
                assets=_rewrite_evidence_assets(_profile_assets(profile)),
                runs_dir=runs_root / "tuning" / profile.profile_id / "rewrite_coherence_tradeoff",
            ),
        ),
    ]


def run_replay_tuning(
    *,
    repo_root: Path,
    output_dir: Path,
    profile_set: str = "v1",
    case_set: str = "all",
    profile_ids: set[str] | None = None,
    case_ids: set[str] | None = None,
) -> dict[str, object]:
    if profile_set != "v1":
        raise ValueError(f"unsupported_profile_set: {profile_set}")
    if case_set not in {"canonical", "tuning", "all"}:
        raise ValueError(f"unsupported_case_set: {case_set}")

    output_dir.mkdir(parents=True, exist_ok=True)
    runs_root = output_dir / "_tmp_runs"
    profiles = [
        profile
        for profile in profile_grid_v1()
        if profile_ids is None or profile.profile_id in profile_ids
    ]
    canonical_cases = canonical_replay_cases(repo_root=repo_root, runs_root=runs_root)
    tuning_cases = synthetic_replay_cases(repo_root=repo_root, runs_root=runs_root)
    if case_ids is not None:
        canonical_cases = [case for case in canonical_cases if case.case_id in case_ids]
        tuning_cases = [case for case in tuning_cases if case.case_id in case_ids]
    if case_set == "canonical":
        active_canonical_cases = canonical_cases
        active_tuning_cases: list[ReplayTuningCase] = []
    elif case_set == "tuning":
        active_canonical_cases = []
        active_tuning_cases = tuning_cases
    else:
        active_canonical_cases = canonical_cases
        active_tuning_cases = tuning_cases

    reports: list[dict[str, object]] = []
    try:
        for profile in profiles:
            reports.append(
                _profile_report(
                    profile,
                    canonical_cases=active_canonical_cases,
                    tuning_cases=active_tuning_cases,
                )
            )
    finally:
        if runs_root.exists():
            shutil.rmtree(runs_root)
    reports.sort(
        key=lambda item: (
            -int(item["business_pass_count"]),
            -float(item["objective_score"]),
            str(item["profile_id"]),
        )
    )
    report = {
        "profile_set": profile_set,
        "case_set": case_set,
        "profile_count": len(reports),
        "profiles": reports,
    }
    _write_report(output_dir, report)
    return report


def _selection_weights(values: tuple[float, float, float, float, float, float]) -> PhaseSelectionWeights:
    return PhaseSelectionWeights(
        exploit=values[0],
        explore=values[1],
        coverage=values[2],
        incremental=values[3],
        fresh=values[4],
        redundancy=values[5],
    )


def _profile_assets(profile: ReplayTuningProfile) -> BootstrapAssets:
    return replace(
        default_bootstrap_assets(),
        runtime_selection_policy=profile.selection_policy,
        stop_guard_thresholds=profile.stop_guard_thresholds,
        rewrite_fitness_weights=profile.rewrite_fitness_weights,
    )


def _profile_report(
    profile: ReplayTuningProfile,
    *,
    canonical_cases: list[ReplayTuningCase],
    tuning_cases: list[ReplayTuningCase],
) -> dict[str, object]:
    summaries = [
        _bundle_summary(case, case.bundle_factory(profile), suite="canonical")
        for case in canonical_cases
    ] + [
        _bundle_summary(case, case.bundle_factory(profile), suite="tuning")
        for case in tuning_cases
    ]
    business_pass_count = sum(
        int(summary["business_pass"])
        for summary in summaries
        if summary["suite"] == "canonical"
    )
    objective_score = _objective_score(summaries)
    return {
        "profile_id": profile.profile_id,
        "business_pass_count": business_pass_count,
        "business_case_count": len(canonical_cases),
        "objective_score": round(objective_score, 6),
        "mean_final_must_have_query_coverage": _mean(
            [float(summary["final_must_have_query_coverage"]) for summary in summaries]
        ),
        "mean_total_net_new_shortlist_gain": _mean(
            [float(summary["total_net_new_shortlist_gain"]) for summary in summaries]
        ),
        "mean_final_shortlist_count": _mean(
            [float(summary["final_shortlist_count"]) for summary in summaries]
        ),
        "mean_first_shortlist_round_index": _mean(
            [float(summary["first_shortlist_round_index"]) for summary in summaries]
        ),
        "mean_search_round_count": _mean(
            [float(summary["search_round_count"]) for summary in summaries]
        ),
        "case_summaries": summaries,
    }


def _bundle_summary(
    case: ReplayTuningCase,
    bundle: SearchRunBundle,
    *,
    suite: str,
) -> dict[str, object]:
    eval_metrics = {
        metric.name: metric.value
        for metric in (bundle.eval or build_search_run_eval(bundle)).metrics
    }
    final_coverage_series = _float_list(eval_metrics.get("must_have_query_coverage_by_search_round"))
    net_new_series = _int_list(eval_metrics.get("net_new_shortlist_gain_by_search_round"))
    shortlist_series = _int_list(eval_metrics.get("run_shortlist_size_after_search_round"))
    search_round_count = int(eval_metrics.get("search_round_count", 0))
    business_pass = None
    if suite == "canonical":
        spec = next(spec for spec in canonical_case_specs() if spec.case_id == case.case_id)
        business_pass = _business_case_pass(build_case_eval(spec, bundle))
    return {
        "case_id": case.case_id,
        "scenario": case.scenario,
        "suite": suite,
        "business_pass": business_pass,
        "stop_reason": bundle.final_result.stop_reason,
        "search_round_count": search_round_count,
        "final_must_have_query_coverage": final_coverage_series[-1] if final_coverage_series else 0.0,
        "total_net_new_shortlist_gain": sum(net_new_series),
        "final_shortlist_count": len(bundle.final_result.final_candidate_cards),
        "first_shortlist_round_index": _first_shortlist_round_index(shortlist_series, search_round_count),
        "selected_operator_by_search_round": _string_list(
            eval_metrics.get("selected_operator_by_search_round")
        ),
        "active_frontier_node_ids_by_round": [
            round_artifact.controller_context.active_frontier_node_summary.frontier_node_id
            for round_artifact in bundle.rounds
        ],
        "last_query_terms": _last_query_terms(bundle),
    }


def _business_case_pass(case_eval: dict[str, object]) -> bool:
    metrics = case_eval.get("metrics")
    if not isinstance(metrics, list):
        return False
    boolean_metrics = [
        metric["value"]
        for metric in metrics
        if isinstance(metric, dict) and isinstance(metric.get("value"), bool)
    ]
    return bool(boolean_metrics) and all(boolean_metrics)


def _objective_score(summaries: list[dict[str, object]]) -> float:
    if not summaries:
        return 0.0
    mean_final_coverage = _mean(
        [float(summary["final_must_have_query_coverage"]) for summary in summaries]
    )
    mean_total_net_new = _mean(
        [float(summary["total_net_new_shortlist_gain"]) for summary in summaries]
    )
    mean_final_shortlist = _mean(
        [float(summary["final_shortlist_count"]) for summary in summaries]
    )
    mean_first_shortlist_round = _mean(
        [float(summary["first_shortlist_round_index"]) for summary in summaries]
    )
    mean_search_round_count = _mean(
        [float(summary["search_round_count"]) for summary in summaries]
    )
    return (
        3.0 * mean_final_coverage
        + 2.0 * mean_total_net_new
        + 1.0 * mean_final_shortlist
        - 0.5 * mean_first_shortlist_round
        - 0.25 * mean_search_round_count
    )


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 6)


def _first_shortlist_round_index(shortlist_series: list[int], search_round_count: int) -> int:
    for index, shortlist_size in enumerate(shortlist_series):
        if shortlist_size > 0:
            return index
    return search_round_count + 1


def _write_report(output_dir: Path, report: dict[str, object]) -> None:
    (output_dir / "replay-tuning-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "replay-tuning-report.md").write_text(
        _markdown_report(report),
        encoding="utf-8",
    )


def _last_query_terms(bundle: SearchRunBundle) -> list[str]:
    search_rounds = [
        round_artifact
        for round_artifact in bundle.rounds
        if round_artifact.execution_plan is not None
    ]
    if not search_rounds:
        return []
    return list(search_rounds[-1].execution_plan.query_terms)


def _markdown_report(report: dict[str, object]) -> str:
    lines = [
        "# Replay Tuning Report",
        "",
        f"- profile_set: `{report['profile_set']}`",
        f"- case_set: `{report['case_set']}`",
        f"- profile_count: `{report['profile_count']}`",
        "",
        "| rank | profile_id | business passes | objective | final coverage | net-new | shortlist | first shortlist | search rounds |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for rank, profile in enumerate(report["profiles"], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(rank),
                    f"`{profile['profile_id']}`",
                    f"{profile['business_pass_count']}/{profile['business_case_count']}",
                    f"{float(profile['objective_score']):.3f}",
                    f"{float(profile['mean_final_must_have_query_coverage']):.3f}",
                    f"{float(profile['mean_total_net_new_shortlist_gain']):.3f}",
                    f"{float(profile['mean_final_shortlist_count']):.3f}",
                    f"{float(profile['mean_first_shortlist_round_index']):.3f}",
                    f"{float(profile['mean_search_round_count']):.3f}",
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _selection_tradeoff_assets(base_assets: BootstrapAssets) -> BootstrapAssets:
    return replace(
        _runtime_assets(base_assets=base_assets),
        runtime_search_budget=RuntimeSearchBudget(
            initial_round_budget=3,
            default_target_new_candidate_count=10,
            max_target_new_candidate_count=20,
        ),
    )


def _harvest_stop_assets(base_assets: BootstrapAssets) -> BootstrapAssets:
    return replace(
        _runtime_assets(base_assets=base_assets),
        runtime_search_budget=RuntimeSearchBudget(
            initial_round_budget=5,
            default_target_new_candidate_count=10,
            max_target_new_candidate_count=20,
        ),
    )


def _rewrite_evidence_assets(base_assets: BootstrapAssets) -> BootstrapAssets:
    return replace(
        _runtime_assets(base_assets=base_assets),
        runtime_search_budget=RuntimeSearchBudget(
            initial_round_budget=4,
            default_target_new_candidate_count=10,
            max_target_new_candidate_count=20,
        ),
    )


def _build_selection_tradeoff_bundle(
    *,
    repo_root: Path,
    assets: BootstrapAssets,
    runs_dir: Path,
) -> SearchRunBundle:
    requirement_payload = {
        "role_title_candidate": "Selection Tradeoff Engineer",
        "role_summary_candidate": "Balance exploit against coverage.",
        "must_have_capability_candidates": ["python backend", "workflow orchestration"],
        "preferred_capability_candidates": [],
        "exclusion_signal_candidates": [],
        "preference_candidates": {"preferred_domains": [], "preferred_backgrounds": []},
        "hard_constraint_candidates": {"locations": ["Shanghai"], "min_years": 5, "max_years": 10},
        "scoring_rationale_candidate": "Make selection tradeoffs observable.",
    }
    keyword_payload = {
        "candidate_seeds": [
            {
                "intent_type": "core_precision",
                "keywords": ["python backend", "retrieval"],
                "source_knowledge_pack_ids": [],
                "reasoning": "precision seed",
            },
            {
                "intent_type": "core_precision",
                "keywords": ["workflow orchestration", "agent"],
                "source_knowledge_pack_ids": [],
                "reasoning": "fresh competing precision seed",
            },
            {
                "intent_type": "relaxed_floor",
                "keywords": ["python backend", "workflow orchestration"],
                "source_knowledge_pack_ids": [],
                "reasoning": "full-hit floor seed",
            },
            {
                "intent_type": "pack_bridge",
                "keywords": ["python backend", "workflow orchestration"],
                "source_knowledge_pack_ids": ["llm_agent_rag_engineering"],
                "reasoning": "full-hit pack seed",
            },
            {
                "intent_type": "vocabulary_bridge",
                "keywords": ["python backend", "workflow orchestration"],
                "source_knowledge_pack_ids": [],
                "reasoning": "full-hit generic seed",
            },
        ],
        "negative_keywords": [],
    }
    return _run_case(
        repo_root=repo_root,
        case_id="selection_tradeoff",
        assets=assets,
        requirement_payload=requirement_payload,
        keyword_payload=keyword_payload,
        pack_scores=_llm_pack_scores(),
        controller_outputs=[
            _search_payload("core_precision", query_terms=["python backend", "retrieval"]),
            _search_payload("vocabulary_bridge", query_terms=["python backend", "workflow orchestration"]),
            _stop_payload(),
        ],
        cts_results=[
            CTSFetchResult(
                request_payload={},
                candidates=[
                    _candidate("selection-fit-a", search_text="python backend retrieval"),
                    _candidate("selection-fit-b", search_text="workflow orchestration backend"),
                ],
                raw_candidate_count=2,
                latency_ms=5,
            ),
            CTSFetchResult(
                request_payload={},
                candidates=[
                    _candidate("selection-fit-c", search_text="python backend workflow orchestration")
                ],
                raw_candidate_count=1,
                latency_ms=5,
            ),
        ],
        candidate_scores=[
            {"selection-fit-a": 2.0, "selection-fit-b": 1.6},
            {"selection-fit-c": 2.1},
        ],
        branch_outputs=[
            _branch_payload(novelty=0.9, usefulness=0.8, repair_operator_hint="core_precision"),
            _branch_payload(novelty=0.4, usefulness=0.4, repair_operator_hint="vocabulary_bridge"),
        ],
        final_summary="Selection tradeoff case finished.",
        runs_dir_override=runs_dir,
    )


def _build_harvest_stop_threshold_bundle(
    *,
    repo_root: Path,
    assets: BootstrapAssets,
    runs_dir: Path,
) -> SearchRunBundle:
    keyword_payload = {
        "candidate_seeds": [
            {
                "intent_type": "core_precision",
                "keywords": ["python backend", "ranking"],
                "source_knowledge_pack_ids": [],
                "reasoning": "stable harvest seed",
            },
            {
                "intent_type": "must_have_alias",
                "keywords": ["python backend", "ranking"],
                "source_knowledge_pack_ids": [],
                "reasoning": "stable alias seed",
            },
            {
                "intent_type": "relaxed_floor",
                "keywords": ["python backend", "ranking"],
                "source_knowledge_pack_ids": [],
                "reasoning": "stable floor seed",
            },
            {
                "intent_type": "pack_bridge",
                "keywords": ["python backend", "ranking"],
                "source_knowledge_pack_ids": ["llm_agent_rag_engineering"],
                "reasoning": "stable pack seed",
            },
            {
                "intent_type": "vocabulary_bridge",
                "keywords": ["python backend", "ranking"],
                "source_knowledge_pack_ids": [],
                "reasoning": "stable generic seed",
            },
            {
                "intent_type": "vocabulary_bridge",
                "keywords": ["python backend", "ranking", "agent"],
                "source_knowledge_pack_ids": [],
                "reasoning": "extra stable generic seed",
            },
        ],
        "negative_keywords": [],
    }
    return _run_case(
        repo_root=repo_root,
        case_id="harvest_stop_threshold",
        assets=assets,
        requirement_payload=_llm_requirement_payload(),
        keyword_payload=keyword_payload,
        pack_scores=_llm_pack_scores(),
        controller_outputs=[
            _search_payload("core_precision", query_terms=["python backend", "ranking"]),
            _search_payload("core_precision", query_terms=["python backend", "ranking"]),
            _search_payload("core_precision", query_terms=["python backend", "ranking"]),
            _search_payload("core_precision", query_terms=["python backend", "ranking"]),
            _search_payload("core_precision", query_terms=["python backend", "ranking"]),
        ],
        cts_results=[
            CTSFetchResult(request_payload={}, candidates=[], raw_candidate_count=0, latency_ms=5),
            CTSFetchResult(request_payload={}, candidates=[], raw_candidate_count=0, latency_ms=5),
            CTSFetchResult(request_payload={}, candidates=[], raw_candidate_count=0, latency_ms=5),
            CTSFetchResult(request_payload={}, candidates=[], raw_candidate_count=0, latency_ms=5),
            CTSFetchResult(
                request_payload={},
                candidates=[_candidate("late-shortlist", search_text="python backend retrieval pipeline")],
                raw_candidate_count=1,
                latency_ms=5,
            ),
        ],
        candidate_scores=[
            {"late-shortlist": 1.9},
            {"late-shortlist": 1.9},
            {"late-shortlist": 1.9},
            {"late-shortlist": 1.9},
            {"late-shortlist": 1.9},
            {"late-shortlist": 1.9},
        ],
        branch_outputs=[
            _branch_payload(novelty=0.6, usefulness=0.6, repair_operator_hint="core_precision"),
            _branch_payload(novelty=0.6, usefulness=0.6, repair_operator_hint="core_precision"),
            _branch_payload(novelty=0.6, usefulness=0.6, repair_operator_hint="core_precision"),
            {
                **_branch_payload(novelty=0.22, usefulness=0.22, repair_operator_hint="core_precision"),
                "branch_exhausted": True,
            },
            _branch_payload(novelty=0.7, usefulness=0.7, repair_operator_hint="core_precision"),
        ],
        final_summary="Harvest stop threshold case finished.",
        runs_dir_override=runs_dir,
    )


def _build_rewrite_evidence_productive_bundle(
    *,
    repo_root: Path,
    assets: BootstrapAssets,
    runs_dir: Path,
) -> SearchRunBundle:
    requirement_payload = {
        "role_title_candidate": "LLM Systems Engineer",
        "role_summary_candidate": "Build backend retrieval and inference systems.",
        "must_have_capability_candidates": ["python backend", "ranking", "deepspeed"],
        "preferred_capability_candidates": [],
        "exclusion_signal_candidates": [],
        "preference_candidates": {"preferred_domains": [], "preferred_backgrounds": []},
        "hard_constraint_candidates": {"locations": ["Shanghai"], "min_years": 5, "max_years": 10},
        "scoring_rationale_candidate": "Make rewrite evidence tradeoffs visible.",
    }
    keyword_payload = {
        "candidate_seeds": [
            {
                "intent_type": "core_precision",
                "keywords": ["python backend", "retrieval", "ranking"],
                "source_knowledge_pack_ids": [],
                "reasoning": "rewrite seed",
            },
            {
                "intent_type": "must_have_alias",
                "keywords": ["python backend", "ranking", "deepspeed"],
                "source_knowledge_pack_ids": [],
                "reasoning": "repair the missing must-have",
            },
            {
                "intent_type": "relaxed_floor",
                "keywords": ["python backend", "ranking", "deepspeed"],
                "source_knowledge_pack_ids": [],
                "reasoning": "full-hit floor seed",
            },
            {
                "intent_type": "vocabulary_bridge",
                "keywords": ["python backend", "ranking", "deepspeed"],
                "source_knowledge_pack_ids": [],
                "reasoning": "full-hit generic seed",
            },
            {
                "intent_type": "pack_bridge",
                "keywords": ["python backend", "ranking", "deepspeed"],
                "source_knowledge_pack_ids": ["llm_agent_rag_engineering"],
                "reasoning": "full-hit pack seed",
            },
        ],
        "negative_keywords": [],
    }
    return _run_case(
        repo_root=repo_root,
        case_id="rewrite_evidence_productive",
        assets=assets,
        requirement_payload=requirement_payload,
        keyword_payload=keyword_payload,
        pack_scores=_llm_pack_scores(),
        controller_outputs=[
            _search_payload("core_precision", query_terms=["python backend", "retrieval", "ranking"]),
            _search_payload("core_precision", query_terms=["python backend", "ranking", "deepspeed"]),
            _search_payload("vocabulary_bridge", query_terms=["python backend", "ranking", "workflow"]),
            _stop_payload(),
        ],
        cts_results=[
            CTSFetchResult(
                request_payload={},
                candidates=[
                    _rewrite_candidate(
                        "rewrite-fit-a",
                        project_names=["rag"],
                        search_text="python backend ranking retrieval deepspeed",
                    ),
                    _rewrite_candidate(
                        "rewrite-fit-b",
                        project_names=["rag"],
                        search_text="python backend ranking retrieval deepspeed workflow",
                    ),
                ],
                raw_candidate_count=2,
                latency_ms=5,
            ),
            CTSFetchResult(
                request_payload={},
                candidates=[_candidate("rewrite-middle", search_text="python backend ranking deepspeed")],
                raw_candidate_count=1,
                latency_ms=5,
            ),
            CTSFetchResult(
                request_payload={},
                candidates=[_candidate("rewrite-followup", search_text="python backend ranking")],
                raw_candidate_count=1,
                latency_ms=5,
            ),
        ],
        candidate_scores=[
            {"rewrite-fit-a": 2.0, "rewrite-fit-b": 1.9},
            {"rewrite-middle": 1.7},
            {"rewrite-followup": 1.8},
        ],
        branch_outputs=[
            _branch_payload(novelty=0.8, usefulness=0.8, repair_operator_hint="vocabulary_bridge"),
            _branch_payload(novelty=0.5, usefulness=0.5, repair_operator_hint="vocabulary_bridge"),
            _branch_payload(novelty=0.4, usefulness=0.4, repair_operator_hint="vocabulary_bridge"),
        ],
        final_summary="Rewrite evidence case finished.",
        runs_dir_override=runs_dir,
    )


def _build_rewrite_coherence_tradeoff_bundle(
    *,
    repo_root: Path,
    assets: BootstrapAssets,
    runs_dir: Path,
) -> SearchRunBundle:
    base_bundle = _build_rewrite_evidence_productive_bundle(
        repo_root=repo_root,
        assets=assets,
        runs_dir=runs_dir,
    )
    return _apply_rewrite_coherence_tradeoff(
        bundle=base_bundle,
        assets=assets,
        profile_rewrite_weights=assets.rewrite_fitness_weights,
    )


def _rewrite_candidate(
    candidate_id: str,
    *,
    project_names: list[str],
    search_text: str,
) -> RetrievedCandidate_t:
    base = _candidate(candidate_id, search_text=search_text)
    title = "LLM Systems Engineer"
    return base.model_copy(
        update={
            "project_names": project_names,
            "work_summaries": ["python", "ranking"],
            "search_text": build_candidate_search_text(
                role_title=title,
                locations=["Shanghai"],
                projects=project_names,
                work_summaries=[search_text],
                education_summaries=base.education_summaries,
                work_experience_summaries=base.work_experience_summaries,
            ),
            "raw_payload": {"title": title, "workExperienceList": []},
        }
    )


def _apply_rewrite_coherence_tradeoff(
    *,
    bundle: SearchRunBundle,
    assets: BootstrapAssets,
    profile_rewrite_weights: RewriteFitnessWeights,
) -> SearchRunBundle:
    requirement_sheet = bundle.bootstrap.requirement_sheet.model_copy(
        update={
            "role_title": "LLM Retrieval Engineer",
            "role_summary": "Build backend retrieval and RAG systems.",
            "must_have_capabilities": ["python backend", "ranking", "rag", "llm"],
            "preferred_capabilities": [],
            "scoring_rationale": "Make rewrite coherence tradeoffs visible.",
        }
    )
    scoring_policy = bundle.bootstrap.scoring_policy.model_copy(
        update={
            "must_have_capabilities_snapshot": list(requirement_sheet.must_have_capabilities),
            "preferred_capabilities_snapshot": [],
            "rerank_query_text": "LLM retrieval engineer with backend ranking and RAG experience.",
        }
    )
    context = _rewrite_coherence_tradeoff_context(
        requirement_sheet=requirement_sheet,
        scoring_policy=scoring_policy,
        assets=assets,
    )
    draft = SearchControllerDecisionDraft_t(
        action="search_cts",
        selected_operator_name="vocabulary_bridge",
        operator_args={"query_terms": ["python backend", "ranking", "rag"]},
        expected_gain_hypothesis="Trade must-have completeness against coherence.",
    )
    decision = generate_search_controller_decision(
        context,
        draft,
        profile_rewrite_weights,
    )
    search_round_indexes = [
        index for index, round_artifact in enumerate(bundle.rounds) if round_artifact.execution_plan is not None
    ]
    target_index = search_round_indexes[-1]
    target_round = bundle.rounds[target_index]
    updated_round = SearchRoundArtifact.model_validate(
        {
            **target_round.model_dump(mode="python"),
            "controller_context": context.model_dump(mode="python"),
            "controller_draft": draft.model_dump(mode="python"),
            "controller_decision": decision.model_dump(mode="python"),
            "execution_plan": {
                **target_round.execution_plan.model_dump(mode="python"),
                "query_terms": list(decision.operator_args["query_terms"]),
            },
        }
    )
    updated_rounds = list(bundle.rounds)
    updated_rounds[target_index] = updated_round
    updated_bootstrap = bundle.bootstrap.model_copy(
        update={
            "requirement_sheet": requirement_sheet,
            "scoring_policy": scoring_policy,
        }
    )
    updated_bundle = SearchRunBundle.model_validate(
        {
            **bundle.model_dump(mode="python"),
            "bootstrap": updated_bootstrap.model_dump(mode="python"),
            "rounds": [round_artifact.model_dump(mode="python") for round_artifact in updated_rounds],
        }
    )
    return updated_bundle.model_copy(update={"eval": build_search_run_eval(updated_bundle)})


def _rewrite_coherence_tradeoff_context(
    *,
    requirement_sheet: RequirementSheet,
    scoring_policy: ScoringPolicy,
    assets: BootstrapAssets,
):
    return select_active_frontier_node(
        FrontierState_t(
            frontier_nodes={
                "seed": FrontierNode_t(
                    frontier_node_id="seed",
                    selected_operator_name="vocabulary_bridge",
                    node_query_term_pool=["python backend", "workflow", "agent"],
                    knowledge_pack_ids=["search_ranking_retrieval_engineering"],
                    rewrite_term_candidates=[
                        _rewrite_term_candidate(
                            "ranking",
                            source_candidate_ids=["shared-1", "shared-2"],
                            source_fields=["work_summaries"],
                            accepted_term_score=5.0,
                            must_have_bonus=1.5,
                        ),
                        _rewrite_term_candidate(
                            "retrieval",
                            source_candidate_ids=["shared-1", "shared-2"],
                            source_fields=["project_names"],
                            accepted_term_score=4.5,
                            pack_bonus=0.5,
                        ),
                        _rewrite_term_candidate(
                            "rag",
                            source_candidate_ids=["rag-1"],
                            source_fields=["title"],
                            accepted_term_score=4.0,
                            must_have_bonus=1.5,
                        ),
                    ],
                    status="open",
                )
            },
            open_frontier_node_ids=["seed"],
            closed_frontier_node_ids=[],
            run_term_catalog=[],
            run_shortlist_candidate_ids=[],
            semantic_hashes_seen=[],
            operator_statistics={},
            remaining_budget=4,
        ),
        requirement_sheet,
        scoring_policy,
        assets.crossover_guard_thresholds,
        assets.runtime_term_budget_policy,
        build_runtime_budget_state(
            initial_round_budget=assets.runtime_search_budget.initial_round_budget,
            runtime_round_index=1,
            remaining_budget=4,
        ),
        assets.runtime_selection_policy,
    )


def _rewrite_term_candidate(
    term: str,
    *,
    source_candidate_ids: list[str],
    source_fields: list[str],
    accepted_term_score: float,
    must_have_bonus: float = 0.0,
    anchor_bonus: float = 0.0,
    pack_bonus: float = 0.0,
) -> RewriteTermCandidate:
    return RewriteTermCandidate(
        term=term,
        source_candidate_ids=source_candidate_ids,
        source_fields=source_fields,
        support_count=len(source_candidate_ids),
        accepted_term_score=accepted_term_score,
        score_breakdown=RewriteTermScoreBreakdown(
            support_score=min(3.0, float(len(source_candidate_ids))),
            candidate_quality_score=0.9,
            field_weight_score=1.0 if "title" in source_fields else 0.8,
            must_have_bonus=must_have_bonus,
            anchor_bonus=anchor_bonus,
            pack_bonus=pack_bonus,
            generic_penalty=0.0,
        ),
    )


def _float_list(value: object) -> list[float]:
    return [float(item) for item in value] if isinstance(value, list) else []


def _int_list(value: object) -> list[int]:
    return [int(item) for item in value] if isinstance(value, list) else []


def _string_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


__all__ = [
    "ReplayTuningCase",
    "ReplayTuningProfile",
    "_business_case_pass",
    "canonical_replay_cases",
    "profile_grid_v1",
    "run_replay_tuning",
    "synthetic_replay_cases",
]
