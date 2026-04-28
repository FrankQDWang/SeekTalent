from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PhraseQualityLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extractor: str
    slice_id: str
    language_bucket: Literal["english", "chinese", "mixed"]
    unit_type: Literal["span", "family", "accepted_family"]
    label: Literal[
        "query_material",
        "template_fragment",
        "generic_boilerplate",
        "company_leakage",
        "non_extractive",
    ]
    accepted: bool
    span_id: str | None = None
    family_id: str | None = None
    blocker: bool = False


class PromotionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reject_reasons: list[str] = Field(default_factory=list)


def score_phrase_quality_rows(rows: list[PhraseQualityLabel]) -> dict[str, dict[str, float | int | dict[str, int]]]:
    grouped: dict[str, list[PhraseQualityLabel]] = {}
    for row in rows:
        grouped.setdefault(row.extractor, []).append(row)

    metrics: dict[str, dict[str, float | int | dict[str, int]]] = {}
    for extractor, extractor_rows in grouped.items():
        candidate_span_rows = [row for row in extractor_rows if row.unit_type == "span"]
        family_rows = [row for row in extractor_rows if row.unit_type == "family"]
        accepted_family_rows = [row for row in extractor_rows if row.unit_type == "accepted_family"]
        language_bucket_counts: dict[str, int] = {}
        for row in extractor_rows:
            language_bucket_counts[row.language_bucket] = language_bucket_counts.get(row.language_bucket, 0) + 1

        metrics[extractor] = {
            "candidate_span_count": len(candidate_span_rows),
            "family_count": len(family_rows),
            "accepted_family_count": len(accepted_family_rows),
            "slice_count": len({row.slice_id for row in extractor_rows}),
            "language_bucket_counts": language_bucket_counts,
            "query_material_precision": _safe_rate(
                numerator=sum(1 for row in accepted_family_rows if row.label == "query_material"),
                denominator=len(accepted_family_rows),
            ),
            "template_fragment_rate": _safe_rate(
                numerator=sum(1 for row in candidate_span_rows if row.label == "template_fragment"),
                denominator=len(candidate_span_rows),
            ),
            "generic_boilerplate_rate": _safe_rate(
                numerator=sum(1 for row in candidate_span_rows if row.label == "generic_boilerplate"),
                denominator=len(candidate_span_rows),
            ),
            "company_leakage_count": sum(1 for row in extractor_rows if row.label == "company_leakage"),
            "non_extractive_count": sum(1 for row in extractor_rows if row.label == "non_extractive"),
            "blocker_count": sum(1 for row in extractor_rows if row.blocker),
        }
    return metrics


def evaluate_promotion_criteria(
    current: dict[str, float | int],
    candidate: dict[str, float | int],
) -> PromotionDecision:
    reject_reasons: list[str] = []
    if int(candidate.get("blocker_count", 0)) > 0:
        reject_reasons.append("blockers_present")
    if float(candidate.get("template_fragment_rate", 1.0)) >= float(current.get("template_fragment_rate", 1.0)):
        reject_reasons.append("template_fragment_rate_not_improved")
    if float(candidate.get("query_material_precision", 0.0)) < float(current.get("query_material_precision", 0.0)):
        reject_reasons.append("query_material_precision_regressed")
    return PromotionDecision(allowed=not reject_reasons, reject_reasons=reject_reasons)


def _safe_rate(*, numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator
