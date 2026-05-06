from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from typing import Any

from seektalent.models import QueryOutcomeClassification, QueryOutcomeThresholds
from seektalent.storage.json import canonical_json

QUERY_OUTCOME_SCHEMA_VERSION = "query-outcome-v1"
QUERY_OUTCOME_POLICY_VERSION = "query-outcome-policy-v1"
JUDGE_QUERY_OUTCOME_SCHEMA_VERSION = "query-judge-outcome-v1"
JUDGE_QUERY_OUTCOME_POLICY_VERSION = "query-judge-outcome-policy-v1"
TERM_OUTCOME_SCHEMA_VERSION = "term-outcome-v1"
DEDUPE_VERSION = "dedupe-v1"


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def build_runtime_query_outcome_row(
    *,
    run_id: str,
    query_instance_id: str,
    query_fingerprint: str,
    round_no: int,
    lane_type: str,
    provider_returned_count: int,
    new_unique_resume_count: int,
    duplicate_count: int,
    scored_resume_count: int,
    new_fit_count: int,
    must_have_match_scores: list[float],
    risk_scores: list[float],
    off_intent_reason_count: int,
    classification: QueryOutcomeClassification,
    thresholds_payload: dict[str, Any],
) -> dict[str, object]:
    thresholds_json = canonical_json(thresholds_payload)
    return {
        "run_id": run_id,
        "query_instance_id": query_instance_id,
        "query_fingerprint": query_fingerprint,
        "outcome_schema_version": QUERY_OUTCOME_SCHEMA_VERSION,
        "outcome_policy_version": QUERY_OUTCOME_POLICY_VERSION,
        "outcome_thresholds_hash": sha256(thresholds_json.encode("utf-8")).hexdigest(),
        "outcome_thresholds_json": thresholds_json,
        "scoring_policy_version": None,
        "dedupe_version": DEDUPE_VERSION,
        "outcome_basis": "runtime_score",
        "round_no": round_no,
        "lane_type": lane_type,
        "provider_returned_count": provider_returned_count,
        "new_unique_resume_count": new_unique_resume_count,
        "duplicate_count": duplicate_count,
        "scored_resume_count": scored_resume_count,
        "new_fit_count": new_fit_count,
        "new_near_fit_count": 0,
        "fit_rate_denominator": "scored_resume_count" if scored_resume_count else None,
        "fit_rate": new_fit_count / scored_resume_count if scored_resume_count else None,
        "must_have_match_avg": _avg(must_have_match_scores),
        "risk_score_avg": _avg(risk_scores),
        "off_intent_reason_count": off_intent_reason_count,
        "primary_label": classification.primary_label,
        "labels_json": canonical_json(classification.labels),
        "reasons_json": canonical_json(classification.reasons),
        "latency_ms": None,
        "cost_estimate_usd": None,
        "artifact_ref_id": None,
    }


def build_runtime_query_outcome_rows_from_hits(
    *,
    run_id: str,
    hits: list[dict[str, Any]],
    thresholds_payload: dict[str, Any] | None = None,
) -> list[dict[str, object]]:
    thresholds_payload = thresholds_payload or QueryOutcomeThresholds().model_dump(mode="json")
    thresholds = QueryOutcomeThresholds.model_validate(thresholds_payload)
    from seektalent.runtime.runtime_diagnostics import classify_query_outcome

    hits_by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for hit in hits:
        hits_by_query[str(hit["query_instance_id"])].append(hit)

    rows: list[dict[str, object]] = []
    for query_instance_id, query_hits in sorted(hits_by_query.items()):
        provider_returned_count = len(query_hits)
        new_hits = [hit for hit in query_hits if bool(hit.get("was_new_to_pool"))]
        scored_hits = [hit for hit in new_hits if hit.get("scored_fit_bucket") is not None]
        must_have_scores = [
            float(hit["must_have_match_score"]) for hit in scored_hits if hit.get("must_have_match_score") is not None
        ]
        risk_scores = [float(hit["risk_score"]) for hit in scored_hits if hit.get("risk_score") is not None]
        new_fit_count = sum(1 for hit in scored_hits if hit.get("scored_fit_bucket") == "fit")
        scored_resume_count = len(scored_hits)
        must_have_match_avg = _avg(must_have_scores) or 0.0
        fit_rate = new_fit_count / scored_resume_count if scored_resume_count else 0.0
        off_intent_reason_count = sum(int(hit.get("off_intent_reason_count") or 0) for hit in scored_hits)
        classification = classify_query_outcome(
            provider_returned_count=provider_returned_count,
            new_unique_resume_count=len(new_hits),
            new_fit_or_near_fit_count=new_fit_count,
            fit_rate=fit_rate,
            must_have_match_avg=must_have_match_avg,
            exploit_baseline_must_have_match_avg=must_have_match_avg,
            off_intent_reason_count=off_intent_reason_count,
            thresholds=thresholds,
        )
        first_hit = query_hits[0]
        rows.append(
            build_runtime_query_outcome_row(
                run_id=run_id,
                query_instance_id=query_instance_id,
                query_fingerprint=str(first_hit["query_fingerprint"]),
                round_no=int(first_hit["round_no"]),
                lane_type=str(first_hit["lane_type"]),
                provider_returned_count=provider_returned_count,
                new_unique_resume_count=len(new_hits),
                duplicate_count=sum(1 for hit in query_hits if bool(hit.get("was_duplicate"))),
                scored_resume_count=scored_resume_count,
                new_fit_count=new_fit_count,
                must_have_match_scores=must_have_scores,
                risk_scores=risk_scores,
                off_intent_reason_count=off_intent_reason_count,
                classification=classification,
                thresholds_payload=thresholds_payload,
            )
        )
    return rows


def build_query_judge_outcome_rows(
    *,
    run_id: str,
    task_id: str,
    query_hits: list[dict[str, Any]],
    judged_by_snapshot: dict[str, dict[str, Any]],
    judge_contract_hash: str,
    judge_model_id: str,
    judge_prompt_hash: str,
    label_schema_version: str,
    thresholds_payload: dict[str, Any],
) -> list[dict[str, object]]:
    hits_by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for hit in query_hits:
        if not hit.get("snapshot_sha256"):
            continue
        hits_by_query[str(hit["query_instance_id"])].append(hit)

    thresholds_json = canonical_json(thresholds_payload)
    thresholds_hash = sha256(thresholds_json.encode("utf-8")).hexdigest()
    rows: list[dict[str, object]] = []
    for query_instance_id, hits in sorted(hits_by_query.items()):
        new_hits = [hit for hit in hits if bool(hit.get("was_new_to_pool"))]
        judged_new_hits = [
            judged_by_snapshot[str(hit["snapshot_sha256"])]
            for hit in new_hits
            if str(hit["snapshot_sha256"]) in judged_by_snapshot
        ]
        positive_count = sum(1 for item in judged_new_hits if int(item["score"]) >= 2)
        near_positive_count = sum(1 for item in judged_new_hits if int(item["score"]) == 2)
        if not judged_new_hits:
            labels = ["judge_coverage_missing"]
            reasons = ["no new query hits had judge labels"]
        elif positive_count:
            labels = ["marginal_gain"]
            reasons = ["new judge-positive resumes joined to query hits"]
        else:
            labels = ["low_recall_high_precision"]
            reasons = ["new query hits were judged but no positive label was found"]
        rows.append(
            {
                "run_id": run_id,
                "query_instance_id": query_instance_id,
                "query_fingerprint": str(hits[0]["query_fingerprint"]),
                "task_id": task_id,
                "judge_contract_hash": judge_contract_hash,
                "judge_model_id": judge_model_id,
                "judge_prompt_hash": judge_prompt_hash,
                "label_schema_version": label_schema_version,
                "outcome_schema_version": JUDGE_QUERY_OUTCOME_SCHEMA_VERSION,
                "outcome_policy_version": JUDGE_QUERY_OUTCOME_POLICY_VERSION,
                "outcome_thresholds_hash": thresholds_hash,
                "outcome_thresholds_json": thresholds_json,
                "provider_returned_count": len(hits),
                "new_unique_resume_count": len(new_hits),
                "judged_resume_count": len(judged_new_hits),
                "new_judge_positive_count": positive_count,
                "new_judge_near_positive_count": near_positive_count,
                "judge_positive_rate": positive_count / len(judged_new_hits) if judged_new_hits else None,
                "duplicate_count": sum(1 for hit in hits if bool(hit.get("was_duplicate"))),
                "primary_label": labels[0],
                "labels_json": canonical_json(labels),
                "reasons_json": canonical_json(reasons),
                "artifact_ref_id": None,
            }
        )
    return rows


def build_rejected_prf_term_event(
    *,
    run_id: str,
    proposal_id: str,
    prf_decision_id: str,
    prf_candidate_artifact_ref_id: str | None,
    prf_policy_decision_artifact_ref_id: str | None,
    prf_proposal_extractor_version: str,
    prf_familying_version: str,
    prf_gate_version: str,
    term_surface: str,
    term_family_id: str,
    round_no: int,
    reject_reasons: list[str],
    supporting_resume_ids: list[str],
    negative_resume_ids: list[str],
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "term_event_id": f"{proposal_id}:{term_family_id}",
        "proposal_id": proposal_id,
        "prf_decision_id": prf_decision_id,
        "prf_candidate_artifact_ref_id": prf_candidate_artifact_ref_id,
        "prf_policy_decision_artifact_ref_id": prf_policy_decision_artifact_ref_id,
        "prf_proposal_extractor_version": prf_proposal_extractor_version,
        "prf_familying_version": prf_familying_version,
        "prf_gate_version": prf_gate_version,
        "candidate_query_fingerprint": None,
        "executed_query_instance_id": None,
        "selected_query_instance_id": None,
        "term_surface": term_surface,
        "term_family_id": term_family_id,
        "term_role": "prf_candidate",
        "source": "llm_prf_candidate",
        "round_no": round_no,
        "lane_type": "prf_probe",
        "accepted_by_prf_gate": 0,
        "prf_reject_reasons_json": canonical_json(reject_reasons),
        "supporting_resume_ids_json": canonical_json(supporting_resume_ids),
        "negative_resume_ids_json": canonical_json(negative_resume_ids),
        "artifact_ref_id": None,
    }


def build_term_outcome_rows(
    *,
    term_events: list[dict[str, object]],
    runtime_outcomes: dict[str, dict[str, object]] | None = None,
    judge_outcomes: dict[str, dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    runtime_outcomes = runtime_outcomes or {}
    judge_outcomes = judge_outcomes or {}
    rows: list[dict[str, object]] = []
    for event in term_events:
        query_id = event.get("executed_query_instance_id")
        runtime_outcome = runtime_outcomes.get(str(query_id)) if query_id is not None else None
        judge_outcome = judge_outcomes.get(str(query_id)) if query_id is not None else None
        if judge_outcome is not None:
            execution_status = "executed_judge_joined"
        elif runtime_outcome is not None:
            execution_status = "executed_runtime"
        else:
            execution_status = "not_executed"
        rows.append(
            {
                "run_id": event["run_id"],
                "term_event_id": event["term_event_id"],
                "term_family_id": event["term_family_id"],
                "term_outcome_schema_version": TERM_OUTCOME_SCHEMA_VERSION,
                "term_familying_version": event.get("prf_familying_version") or "query-term-family-v1",
                "prf_gate_version": event.get("prf_gate_version"),
                "prf_policy_version": None,
                "execution_status": execution_status,
                "runtime_outcome_json": canonical_json(runtime_outcome) if runtime_outcome is not None else None,
                "judge_outcome_json": canonical_json(judge_outcome) if judge_outcome is not None else None,
                "labels_json": canonical_json([execution_status]),
                "reasons_json": canonical_json([]),
                "artifact_ref_id": None,
            }
        )
    return rows
