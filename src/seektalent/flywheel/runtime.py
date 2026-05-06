from __future__ import annotations

from hashlib import sha256
from typing import Any

from seektalent.models import QueryResumeHit, SentQueryRecord
from seektalent.storage.json import canonical_json

QUERY_SPEC_SCHEMA_VERSION = "canonical-query-spec-v1"

RUN_MATERIALIZED_TABLES = {
    "query_outcomes": "flywheel.query_outcomes",
    "query_judge_outcomes": "flywheel.query_judge_outcomes",
    "term_events": "flywheel.term_events",
    "term_outcomes": "flywheel.term_outcomes",
}


def build_run_query_rows(
    *,
    run_id: str,
    artifact_id: str,
    sent_query_records: list[SentQueryRecord],
    canonical_query_specs: dict[str, dict[str, object]],
    job_intent_fingerprint: str,
    query_policy_version: str,
) -> list[dict[str, object]]:
    del artifact_id
    rows: list[dict[str, object]] = []
    for record in sent_query_records:
        if record.query_instance_id is None or record.query_fingerprint is None:
            continue
        spec = canonical_query_specs.get(record.query_instance_id) or {
            "lane_type": record.lane_type,
            "query_terms": record.query_terms,
            "keyword_query": record.keyword_query,
        }
        filters = spec.get("provider_filters", {}) if isinstance(spec, dict) else {}
        rows.append(
            {
                "run_id": run_id,
                "query_instance_id": record.query_instance_id,
                "query_fingerprint": record.query_fingerprint,
                "round_no": record.round_no,
                "lane_type": record.lane_type,
                "query_role": record.query_role,
                "canonical_query_spec_json": canonical_json(spec),
                "query_spec_schema_version": QUERY_SPEC_SCHEMA_VERSION,
                "query_policy_version": query_policy_version,
                "job_intent_fingerprint": job_intent_fingerprint,
                "provider_name": str(spec.get("provider_name") or "cts"),
                "rendered_provider_query": str(spec.get("rendered_provider_query") or record.keyword_query),
                "keyword_query": record.keyword_query,
                "query_terms_json": canonical_json(record.query_terms),
                "filters_json": canonical_json(filters),
                "location_key": record.city,
                "batch_no": record.batch_no,
                "source_plan_version": str(record.source_plan_version),
                "selected_prf_expression": None,
                "accepted_prf_term_family_id": None,
                "fallback_reason": None,
                "artifact_ref_id": None,
            }
        )
    return rows


def query_hit_rows_from_hits(hits: list[QueryResumeHit]) -> list[dict[str, Any]]:
    return [hit.model_dump(mode="json") for hit in hits]


def materialize_flywheel_run_artifacts(*, session: Any, store: Any, run_id: str) -> None:
    for table, logical_name in RUN_MATERIALIZED_TABLES.items():
        rows = store.rows_for_run(table, run_id=run_id)
        if not rows:
            continue
        path = session.write_jsonl(logical_name, rows)
        artifact_ref_id = store.record_artifact_ref(
            artifact_kind=session.manifest.artifact_kind.value,
            artifact_id=session.manifest.artifact_id,
            artifact_root=str(session.root),
            logical_name=logical_name,
            relative_path=str(path.relative_to(session.root)),
            content_sha256=sha256(path.read_bytes()).hexdigest(),
            schema_version="v1",
        )
        store.attach_artifact_ref_to_run_rows(table=table, run_id=run_id, artifact_ref_id=artifact_ref_id)
