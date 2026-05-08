from __future__ import annotations

from seektalent.flywheel.outcomes import build_runtime_query_outcome_rows_from_hits
from seektalent.flywheel.runtime import query_hit_rows_from_hits
from seektalent.models import QueryResumeHit, ScoredCandidate


def test_query_hit_rows_preserve_score_evidence_source_for_flywheel() -> None:
    hit = QueryResumeHit(
        run_id="run-1",
        query_instance_id="query-1",
        query_fingerprint="fingerprint-1",
        hit_sequence_no=1,
        snapshot_sha256="snapshot-1",
        resume_id="resume-1",
        round_no=1,
        lane_type="prf_probe",
        batch_no=1,
        rank_in_query=1,
        provider_name="liepin",
        was_new_to_pool=True,
        was_duplicate=False,
        scored_fit_bucket="fit",
        overall_score=88,
        must_have_match_score=86,
        risk_score=15,
        score_evidence_source="detail_enriched",
        card_scorecard_ref="artifact:scorecards/card/resume-1.json",
        detail_scorecard_ref="artifact:scorecards/detail/resume-1.json",
        score_delta=12,
        detail_open_reason="detail_budget_available",
        detail_open_policy_version="detail-policy-v1",
    )

    rows = query_hit_rows_from_hits([hit])
    outcomes = build_runtime_query_outcome_rows_from_hits(run_id="run-1", hits=rows)

    assert rows[0]["score_evidence_source"] == "detail_enriched"
    assert rows[0]["card_scorecard_ref"] == "artifact:scorecards/card/resume-1.json"
    assert rows[0]["detail_scorecard_ref"] == "artifact:scorecards/detail/resume-1.json"
    assert rows[0]["score_delta"] == 12
    assert rows[0]["detail_open_reason"] == "detail_budget_available"
    assert rows[0]["detail_open_policy_version"] == "detail-policy-v1"
    assert "score_evidence:detail_enriched" in outcomes[0]["labels_json"]
    assert "detail_enriched" in outcomes[0]["reasons_json"]


def test_liepin_detail_scored_hit_without_evidence_marker_does_not_improve_lane() -> None:
    rows = [
        {
            "run_id": "run-1",
            "query_instance_id": "query-1",
            "query_fingerprint": "fingerprint-1",
            "hit_sequence_no": 1,
            "snapshot_sha256": "snapshot-1",
            "resume_id": "resume-1",
            "round_no": 1,
            "lane_type": "exploit",
            "batch_no": 1,
            "rank_in_query": 1,
            "provider_name": "liepin",
            "was_new_to_pool": True,
            "was_duplicate": False,
            "scored_fit_bucket": "fit",
            "overall_score": 92,
            "must_have_match_score": 90,
            "risk_score": 10,
        }
    ]

    outcomes = build_runtime_query_outcome_rows_from_hits(run_id="run-1", hits=rows)

    assert outcomes[0]["scored_resume_count"] == 0
    assert outcomes[0]["new_fit_count"] == 0
    assert "score_evidence_source_missing" in outcomes[0]["labels_json"]


def test_scored_candidate_accepts_only_compact_scorecard_refs_not_payloads() -> None:
    scorecard = ScoredCandidate(
        resume_id="candidate-1",
        fit_bucket="fit",
        overall_score=86,
        must_have_match_score=83,
        preferred_match_score=80,
        risk_score=12,
        reasoning_summary="detail evidence improves must-have confidence",
        confidence="high",
        source_round=1,
        score_evidence_source="detail_enriched",
        card_scorecard_ref="artifact:scorecards/card/candidate-1.json",
        detail_scorecard_ref="artifact:scorecards/detail/candidate-1.json",
        score_delta=14,
        detail_open_reason="detail_budget_available",
        detail_open_policy_version="detail-policy-v1",
    )

    payload = scorecard.model_dump(mode="json")

    assert payload["card_scorecard_ref"] == "artifact:scorecards/card/candidate-1.json"
    assert payload["detail_scorecard_ref"] == "artifact:scorecards/detail/candidate-1.json"
    assert payload["score_delta"] == 14
    assert "card_scorecard" not in payload
    assert "detail_scorecard" not in payload
