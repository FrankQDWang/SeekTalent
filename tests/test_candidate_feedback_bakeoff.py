from __future__ import annotations

from seektalent.candidate_feedback.bakeoff import (
    PhraseQualityLabel,
    evaluate_promotion_criteria,
    score_phrase_quality_rows,
)


def test_phrase_quality_rows_track_denominators_and_blockers() -> None:
    rows = [
        PhraseQualityLabel(
            extractor="regex",
            slice_id="slice-1",
            language_bucket="chinese",
            unit_type="span",
            label="template_fragment",
            accepted=False,
        ),
        PhraseQualityLabel(
            extractor="model",
            slice_id="slice-1",
            language_bucket="chinese",
            unit_type="accepted_family",
            label="query_material",
            accepted=True,
            family_id="feedback.flink-cdc",
        ),
        PhraseQualityLabel(
            extractor="model",
            slice_id="slice-1",
            language_bucket="chinese",
            unit_type="accepted_family",
            label="company_leakage",
            accepted=True,
            family_id="feedback.databricks",
            blocker=True,
        ),
    ]

    metrics = score_phrase_quality_rows(rows)

    assert metrics["model"]["accepted_family_count"] == 2
    assert metrics["model"]["blocker_count"] == 1
    assert metrics["model"]["slice_count"] == 1
    assert metrics["model"]["language_bucket_counts"] == {"chinese": 2}


def test_promotion_criteria_require_better_template_fragment_rate_and_no_blockers() -> None:
    current = {
        "template_fragment_rate": 0.30,
        "query_material_precision": 0.70,
        "blocker_count": 0,
    }
    candidate = {
        "template_fragment_rate": 0.10,
        "query_material_precision": 0.72,
        "blocker_count": 0,
    }

    decision = evaluate_promotion_criteria(current, candidate)

    assert decision.allowed is True
    assert decision.reject_reasons == []
