from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from seektalent.models import (
    BusinessPolicyPack,
    CrossoverGuardThresholds,
    DomainKnowledgePack,
    RerankerCalibration,
    RewriteFitnessWeights,
    RuntimeActiveManifest,
    RuntimeSearchBudget,
    RuntimeSelectionPolicy,
    RuntimeTermBudgetPolicy,
    StopGuardThresholds,
    stable_deduplicate,
)
from seektalent.resources import (
    artifacts_root as default_artifacts_root,
    calibration_file,
    knowledge_pack_file,
    policy_file,
    runtime_active_file,
)

DEFAULT_OPERATOR_CATALOG = (
    "core_precision",
    "must_have_alias",
    "relaxed_floor",
    "pack_bridge",
    "vocabulary_bridge",
    "crossover_compose",
)


@dataclass(frozen=True)
class BootstrapAssets:
    policy_id: str
    knowledge_pack_ids: tuple[str, ...]
    calibration_id: str
    business_policy_pack: BusinessPolicyPack
    knowledge_packs: tuple[DomainKnowledgePack, ...]
    reranker_calibration: RerankerCalibration
    runtime_search_budget: RuntimeSearchBudget
    runtime_term_budget_policy: RuntimeTermBudgetPolicy
    runtime_selection_policy: RuntimeSelectionPolicy
    crossover_guard_thresholds: CrossoverGuardThresholds
    stop_guard_thresholds: StopGuardThresholds
    rewrite_fitness_weights: RewriteFitnessWeights
    operator_catalog: tuple[str, ...]


def default_bootstrap_assets(*, artifacts_root: Path | None = None) -> BootstrapAssets:
    base_dir = artifacts_root or default_artifacts_root()
    active_manifest = _load_active_manifest(base_dir)
    knowledge_packs = _load_knowledge_packs(base_dir, active_manifest.knowledge_pack_ids)
    calibration = _load_calibration(base_dir, active_manifest.calibration_id)
    business_policy_pack = _load_policy(base_dir, active_manifest.policy_id)
    return BootstrapAssets(
        policy_id=active_manifest.policy_id,
        knowledge_pack_ids=tuple(active_manifest.knowledge_pack_ids),
        calibration_id=active_manifest.calibration_id,
        business_policy_pack=business_policy_pack,
        knowledge_packs=knowledge_packs,
        reranker_calibration=calibration,
        runtime_search_budget=RuntimeSearchBudget(
            initial_round_budget=5,
            default_target_new_candidate_count=10,
            max_target_new_candidate_count=20,
        ),
        runtime_term_budget_policy=RuntimeTermBudgetPolicy(),
        runtime_selection_policy=RuntimeSelectionPolicy(),
        crossover_guard_thresholds=CrossoverGuardThresholds(),
        stop_guard_thresholds=StopGuardThresholds(),
        rewrite_fitness_weights=RewriteFitnessWeights(),
        operator_catalog=DEFAULT_OPERATOR_CATALOG,
    )


def _load_active_manifest(base_dir: Path) -> RuntimeActiveManifest:
    path = base_dir / runtime_active_file().relative_to(default_artifacts_root())
    return RuntimeActiveManifest.model_validate_json(path.read_text(encoding="utf-8"))


def _load_knowledge_packs(
    base_dir: Path,
    knowledge_pack_ids: list[str],
) -> tuple[DomainKnowledgePack, ...]:
    if not knowledge_pack_ids:
        raise ValueError("active_manifest_requires_knowledge_pack_ids")
    packs = tuple(
        DomainKnowledgePack.model_validate_json(
            (
                base_dir / knowledge_pack_file(knowledge_pack_id).relative_to(default_artifacts_root())
            ).read_text(encoding="utf-8")
        )
        for knowledge_pack_id in knowledge_pack_ids
    )
    _validate_knowledge_packs(tuple(knowledge_pack_ids), packs)
    return packs


def _load_calibration(base_dir: Path, calibration_id: str) -> RerankerCalibration:
    path = base_dir / calibration_file(calibration_id).relative_to(default_artifacts_root())
    return RerankerCalibration.model_validate_json(path.read_text(encoding="utf-8"))


def _load_policy(base_dir: Path, policy_id: str) -> BusinessPolicyPack:
    path = base_dir / policy_file(policy_id).relative_to(default_artifacts_root())
    return BusinessPolicyPack.model_validate_json(path.read_text(encoding="utf-8"))


def _validate_knowledge_packs(
    active_pack_ids: tuple[str, ...],
    packs: tuple[DomainKnowledgePack, ...],
) -> None:
    if len(set(active_pack_ids)) != len(active_pack_ids):
        raise ValueError("duplicate_active_knowledge_pack_id")
    for expected_pack_id, pack in zip(active_pack_ids, packs, strict=True):
        if pack.knowledge_pack_id != expected_pack_id:
            raise ValueError(
                f"knowledge_pack_id_mismatch: expected={expected_pack_id}, actual={pack.knowledge_pack_id}"
            )
        if not pack.routing_text.strip():
            raise ValueError(f"empty_routing_text: {pack.knowledge_pack_id}")
        include_keywords = stable_deduplicate(list(pack.include_keywords))
        exclude_keywords = stable_deduplicate(list(pack.exclude_keywords))
        if not include_keywords:
            raise ValueError(f"empty_include_keywords: {pack.knowledge_pack_id}")
        if not exclude_keywords:
            raise ValueError(f"empty_exclude_keywords: {pack.knowledge_pack_id}")


__all__ = ["BootstrapAssets", "DEFAULT_OPERATOR_CATALOG", "default_bootstrap_assets"]
