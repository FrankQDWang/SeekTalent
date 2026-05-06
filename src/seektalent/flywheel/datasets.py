from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from seektalent.artifacts import ArtifactStore
from seektalent.flywheel.store import FlywheelStore
from seektalent.storage.json import canonical_json

DATASET_BUILDER_VERSION = "query-rewrite-builder-v1"
QUERY_REWRITE_SAMPLE_SCHEMA_VERSION = "query-rewrite-sample-v1"


@dataclass(frozen=True)
class DatasetExportResult:
    export_id: str
    root: Path
    sha256: str
    row_count: int


def _sample_id(payload: dict[str, object]) -> str:
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def export_query_rewriting_dataset(
    *,
    store: FlywheelStore,
    artifact_store: ArtifactStore,
    dataset_version: str,
    builder_config: dict[str, Any],
    run_ids: list[str],
) -> DatasetExportResult:
    rows = store.query_rewrite_source_rows(run_ids=run_ids)
    builder_config_hash = sha256(canonical_json(builder_config).encode("utf-8")).hexdigest()
    samples = []
    for row in rows:
        sample_payload = {
            "task_id": row["task_id"],
            "source_query_instance_ids": [row["query_instance_id"]],
            "dataset_version": dataset_version,
            "schema_version": QUERY_REWRITE_SAMPLE_SCHEMA_VERSION,
            "builder_version": DATASET_BUILDER_VERSION,
            "builder_config_hash": builder_config_hash,
            "sample_basis": "judge_outcome",
        }
        input_payload = {
            "job_title": row.get("job_title"),
            "requirement_digest": row.get("task_sha256"),
            "query_history": row.get("query_history", []),
            "failed_terms": row.get("failed_terms", []),
            "successful_terms": row.get("successful_terms", []),
            "prf_evidence_summaries": row.get("prf_evidence_summaries", []),
            "top_positive_signals": row.get("top_positive_signals", []),
            "top_negative_signals": row.get("top_negative_signals", []),
        }
        target_payload = {
            "select_terms": row.get("select_terms", []),
            "suppress_terms": row.get("suppress_terms", []),
            "rank_terms": row.get("rank_terms", []),
            "primary_label": row["primary_label"],
        }
        reward_payload = {
            "high_score_gain": row["new_judge_positive_count"],
            "precision_gain": row["judge_positive_rate"],
            "zero_recall_recovery": 1 if row["primary_label"] == "zero_recall_recovered" else 0,
            "duplicate_penalty": row["duplicate_count"],
            "broad_noise_penalty": 1 if row["primary_label"] == "broad_noise" else 0,
            "drift_penalty": 1 if row["primary_label"] == "off_intent" else 0,
        }
        samples.append(
            {
                "sample_id": _sample_id(sample_payload),
                "task_id": row["task_id"],
                "run_id": row["run_id"],
                "source_query_instance_ids_json": canonical_json([row["query_instance_id"]]),
                "sample_basis": "judge_outcome",
                "input_json": canonical_json(input_payload),
                "target_json": canonical_json(target_payload),
                "reward_json": canonical_json(reward_payload),
                "schema_version": QUERY_REWRITE_SAMPLE_SCHEMA_VERSION,
                "dataset_version": dataset_version,
                "builder_version": DATASET_BUILDER_VERSION,
                "builder_config_hash": builder_config_hash,
                "artifact_ref_id": None,
            }
        )
    samples = sorted(samples, key=lambda item: item["sample_id"])

    session = artifact_store.create_root(
        kind="export",
        display_name="query rewriting dataset export",
        producer="FlywheelDatasetBuilder",
    )
    for table, logical_name in {
        "query_outcomes": "flywheel.query_outcomes",
        "query_judge_outcomes": "flywheel.query_judge_outcomes",
        "term_events": "flywheel.term_events",
        "term_outcomes": "flywheel.term_outcomes",
    }.items():
        session.write_jsonl(logical_name, store.rows_for_runs(table, run_ids=run_ids))
    session.write_jsonl("flywheel.query_rewrite_samples", samples)
    store.record_query_rewrite_samples(samples)

    content = "\n".join(canonical_json(item) for item in samples) + ("\n" if samples else "")
    digest = sha256(content.encode("utf-8")).hexdigest()
    manifest = {
        "dataset_version": dataset_version,
        "builder_version": DATASET_BUILDER_VERSION,
        "builder_config_hash": builder_config_hash,
        "row_count": len(samples),
        "sha256": digest,
    }
    manifest_path = session.write_json("flywheel.dataset_export_manifest", manifest)
    session.finalize(status="completed")
    source_artifact_refs = store.source_artifact_refs_for_runs(run_ids=run_ids)
    manifest_ref_id = store.record_artifact_ref(
        artifact_kind="export",
        artifact_id=session.manifest.artifact_id,
        artifact_root=str(session.root),
        logical_name="flywheel.dataset_export_manifest",
        relative_path=str(manifest_path.relative_to(session.root)),
        content_sha256=sha256(manifest_path.read_bytes()).hexdigest(),
        schema_version="v1",
    )
    store.record_dataset_export(
        export_id=session.manifest.artifact_id,
        dataset_name="query_rewriting",
        dataset_version=dataset_version,
        schema_version=QUERY_REWRITE_SAMPLE_SCHEMA_VERSION,
        builder_version=DATASET_BUILDER_VERSION,
        builder_config=builder_config,
        source_run_ids=run_ids,
        source_query="query_judge_outcomes",
        source_db_sha256=sha256(store.path.read_bytes()).hexdigest(),
        source_artifact_refs=source_artifact_refs,
        git_sha=None,
        artifact_root=str(session.root),
        output_path=str(session.root / "flywheel"),
        artifact_ref_id=manifest_ref_id,
        row_count=len(samples),
        sha256_value=digest,
    )
    return DatasetExportResult(
        export_id=session.manifest.artifact_id,
        root=session.root,
        sha256=digest,
        row_count=len(samples),
    )
