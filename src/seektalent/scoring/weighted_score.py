from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from seektalent.models import ScoringPolicy

MUST_HAVE_WEIGHT = 60
PREFERRED_WEIGHT = 25
INVERTED_RISK_WEIGHT = 15


@dataclass(frozen=True)
class ScoreDimensionApplicability:
    preferred: bool
    risk: bool


def score_dimension_applicability(policy: ScoringPolicy) -> ScoreDimensionApplicability:
    preferred = bool(
        policy.preferred_capabilities
        or policy.preferences.preferred_locations
        or policy.preferences.preferred_companies
        or policy.preferences.preferred_domains
        or policy.preferences.preferred_backgrounds
    )
    return ScoreDimensionApplicability(
        preferred=preferred,
        risk=bool(policy.exclusion_signals),
    )


def calculate_overall_score(
    *,
    must_have_match_score: int,
    preferred_match_score: int | None,
    risk_score: int | None,
    applicability: ScoreDimensionApplicability,
) -> int:
    if applicability.preferred != (preferred_match_score is not None):
        code = "preferred_score_required" if applicability.preferred else "preferred_score_not_applicable"
        raise ValueError(code)
    if applicability.risk != (risk_score is not None):
        code = "risk_score_required" if applicability.risk else "risk_score_not_applicable"
        raise ValueError(code)

    weighted = [(must_have_match_score, MUST_HAVE_WEIGHT)]
    if preferred_match_score is not None:
        weighted.append((preferred_match_score, PREFERRED_WEIGHT))
    if risk_score is not None:
        weighted.append((100 - risk_score, INVERTED_RISK_WEIGHT))
    numerator = sum(Decimal(value * weight) for value, weight in weighted)
    denominator = Decimal(sum(weight for _value, weight in weighted))
    rounded = int((numerator / denominator).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return min(100, max(0, rounded))


def risk_at_or_above(score: int | None, threshold: int) -> bool:
    return score is not None and score >= threshold


def risk_at_or_below(score: int | None, threshold: int) -> bool:
    return score is None or score <= threshold
