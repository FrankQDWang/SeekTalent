from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


DetailPlanAction = Literal["open_detail", "card_only"]


@dataclass(frozen=True)
class LiepinCardCandidate:
    candidate_id: str
    stable_provider_id: str | None
    weak_fingerprint: str | None
    card_value_score: float
    detail_url: str | None = None


@dataclass(frozen=True)
class LiepinDetailPlanDecision:
    candidate_id: str
    action: DetailPlanAction
    reason: str
    artifact_reason: dict[str, str]


@dataclass(frozen=True)
class LiepinDetailOpenPlan:
    decisions: list[LiepinDetailPlanDecision]


def build_detail_open_plan(
    *,
    candidates: list[LiepinCardCandidate],
    already_opened_provider_ids: set[str],
    daily_detail_budget: int,
    consumed_detail_budget: int,
    already_seen_weak_fingerprints: set[str] | None = None,
    min_card_value_score: float = 0.0,
) -> LiepinDetailOpenPlan:
    if daily_detail_budget < 0:
        raise ValueError("daily_detail_budget must be >= 0")
    if consumed_detail_budget < 0:
        raise ValueError("consumed_detail_budget must be >= 0")

    seen_weak_fingerprints = already_seen_weak_fingerprints or set()
    used_budget = consumed_detail_budget
    decisions: list[LiepinDetailPlanDecision] = []
    for candidate in candidates:
        weak_fingerprint_seen = bool(
            candidate.weak_fingerprint and candidate.weak_fingerprint in seen_weak_fingerprints
        )
        action: DetailPlanAction = "open_detail"
        reason = "detail_budget_available"
        if candidate.stable_provider_id and candidate.stable_provider_id in already_opened_provider_ids:
            action = "card_only"
            reason = "stable_provider_id_already_opened"
        elif candidate.card_value_score < min_card_value_score:
            action = "card_only"
            reason = "low_card_value"
        elif used_budget >= daily_detail_budget:
            action = "card_only"
            reason = "detail_budget_exhausted"
        else:
            if weak_fingerprint_seen:
                reason = "detail_budget_available"
            used_budget += 1

        decisions.append(_decision(candidate_id=candidate.candidate_id, action=action, reason=reason))
    return LiepinDetailOpenPlan(decisions=decisions)


def _decision(*, candidate_id: str, action: DetailPlanAction, reason: str) -> LiepinDetailPlanDecision:
    return LiepinDetailPlanDecision(
        candidate_id=candidate_id,
        action=action,
        reason=reason,
        artifact_reason={
            "candidate_id": candidate_id,
            "action": action,
            "reason": reason,
        },
    )
