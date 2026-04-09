from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from seektalent.models import (
    BusinessPolicyPack,
    ExplanationPreferences,
    FitGateConstraints,
    FusionWeightPreferences,
    GroundingKnowledgeBaseSnapshot,
    GroundingKnowledgeCard,
    KnowledgeRetrievalBudget,
    RerankerCalibration,
    RuntimeSearchBudget,
    StabilityPolicy,
)
from seektalent.resources import (
    artifacts_root as default_artifacts_root,
    calibration_file,
    compiled_cards_file,
    compiled_snapshot_file,
    reviewed_reports_dir,
)


DEFAULT_OPERATOR_CATALOG = (
    "must_have_alias",
    "strict_core",
    "domain_company",
    "crossover_compose",
)
DEFAULT_SNAPSHOT_ID = "kb-2026-04-07-v1"
DEFAULT_CALIBRATION_ID = "qwen3-reranker-8b-mxfp8-2026-04-07-v1"


@dataclass(frozen=True)
class BootstrapAssets:
    business_policy_pack: BusinessPolicyPack
    knowledge_base_snapshot: GroundingKnowledgeBaseSnapshot
    knowledge_cards: tuple[GroundingKnowledgeCard, ...]
    reranker_calibration: RerankerCalibration
    knowledge_retrieval_budget: KnowledgeRetrievalBudget
    runtime_search_budget: RuntimeSearchBudget
    operator_catalog: tuple[str, ...]


def default_bootstrap_assets(*, artifacts_root: Path | None = None) -> BootstrapAssets:
    base_dir = artifacts_root or default_artifacts_root()
    cards = _load_cards(base_dir)
    snapshot = _load_snapshot(base_dir, DEFAULT_SNAPSHOT_ID)
    calibration = _load_calibration(base_dir, DEFAULT_CALIBRATION_ID)
    _validate_knowledge_assets(base_dir, snapshot, cards)
    return BootstrapAssets(
        business_policy_pack=BusinessPolicyPack(
            domain_pack_ids=[],
            fusion_weight_preferences=FusionWeightPreferences(
                rerank=0.55,
                must_have=0.25,
                preferred=0.10,
                risk_penalty=0.10,
            ),
            fit_gate_overrides=FitGateConstraints(),
            stability_policy=StabilityPolicy(
                mode="soft_penalty",
                penalty_weight=1.0,
                confidence_floor=0.6,
                allow_hard_gate=False,
            ),
            explanation_preferences=ExplanationPreferences(
                top_n_for_explanation=5,
                emphasize_business_delivery=True,
            ),
        ),
        knowledge_base_snapshot=snapshot,
        knowledge_cards=cards,
        reranker_calibration=calibration,
        knowledge_retrieval_budget=KnowledgeRetrievalBudget(max_cards=8, max_inferred_domain_packs=2),
        runtime_search_budget=RuntimeSearchBudget(
            initial_round_budget=5,
            default_target_new_candidate_count=10,
            max_target_new_candidate_count=20,
        ),
        operator_catalog=DEFAULT_OPERATOR_CATALOG,
    )


def _load_cards(base_dir: Path) -> tuple[GroundingKnowledgeCard, ...]:
    payload = json.loads((base_dir / compiled_cards_file().relative_to(default_artifacts_root())).read_text(encoding="utf-8"))
    return tuple(GroundingKnowledgeCard.model_validate(card) for card in payload)


def _load_snapshot(base_dir: Path, snapshot_id: str) -> GroundingKnowledgeBaseSnapshot:
    path = base_dir / compiled_snapshot_file(snapshot_id).relative_to(default_artifacts_root())
    return GroundingKnowledgeBaseSnapshot.model_validate_json(path.read_text(encoding="utf-8"))


def _load_calibration(base_dir: Path, calibration_id: str) -> RerankerCalibration:
    path = base_dir / calibration_file(calibration_id).relative_to(default_artifacts_root())
    return RerankerCalibration.model_validate_json(path.read_text(encoding="utf-8"))


def _validate_knowledge_assets(
    base_dir: Path,
    snapshot: GroundingKnowledgeBaseSnapshot,
    cards: tuple[GroundingKnowledgeCard, ...],
) -> None:
    report_index = _reviewed_report_index(base_dir)
    snapshot_card_ids = set(snapshot.card_ids)
    card_ids = {card.card_id for card in cards}
    if len(snapshot_card_ids) != len(snapshot.card_ids):
        raise ValueError("duplicate_snapshot_card_id")
    if len(card_ids) != len(cards):
        raise ValueError("duplicate_compiled_card_id")
    if snapshot_card_ids != card_ids:
        missing_cards = sorted(snapshot_card_ids - card_ids)
        extra_cards = sorted(card_ids - snapshot_card_ids)
        raise ValueError(f"knowledge_card_id_mismatch: missing={missing_cards}, extra={extra_cards}")
    if len(set(snapshot.compiled_report_ids)) != len(snapshot.compiled_report_ids):
        raise ValueError("duplicate_snapshot_report_id")
    missing_reports = [
        report_id for report_id in snapshot.compiled_report_ids if report_id not in report_index
    ]
    if missing_reports:
        raise ValueError(f"missing_reviewed_reports: {missing_reports}")
    compiled_report_ids = set(snapshot.compiled_report_ids)
    for card in cards:
        unknown_reports = [
            report_id for report_id in card.source_report_ids if report_id not in compiled_report_ids
        ]
        if unknown_reports:
            raise ValueError(
                f"card_source_reports_outside_snapshot: card_id={card.card_id}, source_report_ids={unknown_reports}"
            )


def _reviewed_report_index(base_dir: Path) -> dict[str, Path]:
    reports_dir = base_dir / reviewed_reports_dir().relative_to(default_artifacts_root())
    index: dict[str, Path] = {}
    for path in sorted(reports_dir.glob("*.md")):
        report_id = _parse_report_id(path)
        if report_id in index:
            raise ValueError(f"duplicate_reviewed_report_id: {report_id}")
        index[report_id] = path
    return index


def _parse_report_id(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"missing_front_matter: {path}")
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if stripped.startswith("report_id:"):
            report_id = stripped.partition(":")[2].strip()
            if report_id:
                return report_id
            break
    raise ValueError(f"missing_report_id: {path}")


__all__ = ["BootstrapAssets", "DEFAULT_OPERATOR_CATALOG", "default_bootstrap_assets"]
