from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from seektalent.artifacts import ArtifactStore
from seektalent.candidate_feedback import build_llm_prf_artifact_refs
from seektalent.flywheel.outcomes import (
    build_query_judge_outcome_rows,
    build_rejected_prf_term_event,
    build_runtime_query_outcome_row,
)
from seektalent.flywheel.runtime import build_run_query_rows, materialize_flywheel_run_artifacts, query_hit_rows_from_hits
from seektalent.flywheel.store import FlywheelStore
from seektalent.models import QueryOutcomeClassification, QueryResumeHit, SentQueryRecord
from seektalent.runtime.orchestrator import WorkflowRuntime
from seektalent.tracing import RunTracer
from tests.settings_factory import make_settings


def test_build_run_query_rows_include_canonical_query_spec(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="run", producer="test")

    rows = build_run_query_rows(
        run_id=session.manifest.artifact_id,
        artifact_id=session.manifest.artifact_id,
        sent_query_records=[
            SentQueryRecord(
                round_no=1,
                lane_type="exploit",
                query_instance_id="query-1",
                query_fingerprint="fingerprint-1",
                batch_no=1,
                requested_count=5,
                query_terms=["LangGraph", "Agent"],
                keyword_query="LangGraph Agent",
                source_plan_version=1,
                rationale="test",
            )
        ],
        canonical_query_specs={
            "query-1": {
                "lane_type": "exploit",
                "rendered_provider_query": "LangGraph Agent",
                "provider_filters": {"city": "上海"},
            }
        },
        job_intent_fingerprint="intent-1",
        query_policy_version="query-policy-v1",
    )

    row = rows[0]
    assert row["canonical_query_spec_json"]
    assert json.loads(row["canonical_query_spec_json"])["lane_type"] == "exploit"
    assert row["query_spec_schema_version"] == "canonical-query-spec-v1"
    assert row["job_intent_fingerprint"] == "intent-1"


def test_query_hit_rows_require_snapshot_hash_for_normal_hits() -> None:
    hit = QueryResumeHit(
        run_id="run-1",
        query_instance_id="query-1",
        query_fingerprint="fingerprint-1",
        hit_sequence_no=1,
        snapshot_sha256="snapshot-1",
        resume_id="resume-1",
        round_no=1,
        lane_type="exploit",
        batch_no=1,
        rank_in_query=1,
        provider_name="cts",
        dedup_key="resume-1",
        was_new_to_pool=True,
        was_duplicate=False,
    )

    rows = query_hit_rows_from_hits([hit])

    assert rows[0]["snapshot_sha256"] == "snapshot-1"
    assert rows[0]["snapshot_missing_reason"] is None


def test_zero_recall_runtime_outcome_uses_null_precision_fields() -> None:
    row = build_runtime_query_outcome_row(
        run_id="run-1",
        query_instance_id="query-1",
        query_fingerprint="fingerprint-1",
        round_no=1,
        lane_type="exploit",
        provider_returned_count=0,
        new_unique_resume_count=0,
        duplicate_count=0,
        scored_resume_count=0,
        new_fit_count=0,
        must_have_match_scores=[],
        risk_scores=[],
        off_intent_reason_count=0,
        classification=QueryOutcomeClassification(
            primary_label="zero_recall",
            labels=["zero_recall"],
            reasons=["provider_returned_count == 0"],
        ),
        thresholds_payload={"low_recall_threshold": 2},
    )

    assert row["fit_rate"] is None
    assert row["must_have_match_avg"] is None
    assert row["risk_score_avg"] is None
    assert row["scored_resume_count"] == 0
    assert row["outcome_schema_version"] == "query-outcome-v1"


def test_materialize_flywheel_run_artifacts_backfills_artifact_refs(tmp_path: Path) -> None:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    session = artifact_store.create_root(kind="run", display_name="run", producer="test")
    store = FlywheelStore(tmp_path / "flywheel.sqlite3")
    try:
        task_id = store.upsert_task(job_title="Agent Engineer", jd_text="JD", notes_text="")
        store.start_run(
            run_id=session.manifest.artifact_id,
            task_id=task_id,
            version="0.6.2",
            git_sha="abc123",
            artifact_ref_id=None,
            artifact_root=str(session.root),
            config_hash="config",
            config_payload={},
            status="completed",
            eval_enabled=False,
            benchmark_id=None,
            benchmark_case_id=None,
        )
        store.record_query_outcomes(
            [
                build_runtime_query_outcome_row(
                    run_id=session.manifest.artifact_id,
                    query_instance_id="query-1",
                    query_fingerprint="fingerprint-1",
                    round_no=1,
                    lane_type="exploit",
                    provider_returned_count=0,
                    new_unique_resume_count=0,
                    duplicate_count=0,
                    scored_resume_count=0,
                    new_fit_count=0,
                    must_have_match_scores=[],
                    risk_scores=[],
                    off_intent_reason_count=0,
                    classification=QueryOutcomeClassification(
                        primary_label="zero_recall",
                        labels=["zero_recall"],
                        reasons=[],
                    ),
                    thresholds_payload={},
                )
            ]
        )

        materialize_flywheel_run_artifacts(session=session, store=store, run_id=session.manifest.artifact_id)

        rows = store.rows_for_run("query_outcomes", run_id=session.manifest.artifact_id)
        assert rows[0]["artifact_ref_id"] is not None
        assert (session.root / "flywheel/query_outcomes.jsonl").exists()
    finally:
        store.close()


def test_query_judge_outcome_counts_only_new_hits_as_gain() -> None:
    rows = build_query_judge_outcome_rows(
        run_id="run-1",
        task_id="task-1",
        query_hits=[
            {
                "query_instance_id": "query-1",
                "query_fingerprint": "fingerprint-1",
                "snapshot_sha256": "snapshot-old",
                "was_new_to_pool": False,
                "was_duplicate": True,
            },
            {
                "query_instance_id": "query-1",
                "query_fingerprint": "fingerprint-1",
                "snapshot_sha256": "snapshot-new",
                "was_new_to_pool": True,
                "was_duplicate": False,
            },
        ],
        judged_by_snapshot={
            "snapshot-old": {"score": 3},
            "snapshot-new": {"score": 1},
        },
        judge_contract_hash="contract",
        judge_model_id="deepseek-v4-pro",
        judge_prompt_hash="prompt",
        label_schema_version="judge-label-v1",
        thresholds_payload={},
    )

    assert rows[0]["new_judge_positive_count"] == 0
    assert rows[0]["judged_resume_count"] == 1


def test_rejected_prf_term_event_is_not_bound_to_generic_query() -> None:
    event = build_rejected_prf_term_event(
        run_id="run-1",
        proposal_id="proposal-1",
        prf_decision_id="decision-1",
        prf_candidate_artifact_ref_id="artifact-candidates",
        prf_policy_decision_artifact_ref_id="artifact-policy",
        prf_proposal_extractor_version="llm-prf-v1",
        prf_familying_version="conservative-family-v1",
        prf_gate_version="prf-gate-v1",
        term_surface="Agent工作流",
        term_family_id="feedback.agent-workflow",
        round_no=2,
        reject_reasons=["generic_phrase"],
        supporting_resume_ids=["r1", "r2"],
        negative_resume_ids=[],
    )

    assert event["executed_query_instance_id"] is None
    assert event["selected_query_instance_id"] is None
    assert event["source"] == "llm_prf_candidate"


def test_runtime_registers_prf_artifact_refs_before_rejected_term_events(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        flywheel_path=str(tmp_path / "flywheel.sqlite3"),
        mock_cts=True, provider_name="cts",
    )
    runtime = WorkflowRuntime(settings)
    tracer = RunTracer(settings.runs_path)
    task_id = runtime.flywheel_store.upsert_task(job_title="Data Engineer", jd_text="Build data pipelines.", notes_text="")
    manifest_ref_id = runtime.flywheel_store.record_artifact_ref(
        artifact_kind=tracer.session.manifest.artifact_kind.value,
        artifact_id=tracer.run_id,
        artifact_root=str(tracer.run_dir),
        logical_name="manifest.run",
        relative_path="manifests/run_manifest.json",
        content_sha256=None,
        schema_version=tracer.session.manifest.manifest_schema_version,
    )
    runtime.flywheel_store.start_run(
        run_id=tracer.run_id,
        task_id=task_id,
        version=tracer.session.manifest.producer_version,
        git_sha=tracer.session.manifest.git_sha,
        artifact_ref_id=manifest_ref_id,
        artifact_root=str(tracer.run_dir),
        config_hash="test-config",
        config_payload={},
        status="running",
        eval_enabled=False,
        benchmark_id=None,
        benchmark_case_id=None,
    )
    artifact_refs = build_llm_prf_artifact_refs(round_no=2)
    tracer.write_json(artifact_refs.candidates_artifact_ref, {"candidate_expressions": []})
    tracer.write_json(artifact_refs.policy_decision_artifact_ref, {"reject_reasons": ["generic_phrase"]})

    refs = runtime._record_prf_flywheel_artifact_refs(
        tracer=tracer,
        prf_selection=SimpleNamespace(
            prf_decision=object(),
            llm_prf_candidates_artifact_ref=artifact_refs.candidates_artifact_ref,
            llm_prf_policy_decision_artifact_ref=artifact_refs.policy_decision_artifact_ref,
        ),
    )
    event = build_rejected_prf_term_event(
        run_id=tracer.run_id,
        proposal_id="proposal-1",
        prf_decision_id="decision-1",
        prf_candidate_artifact_ref_id=refs["candidates"],
        prf_policy_decision_artifact_ref_id=refs["policy_decision"],
        prf_proposal_extractor_version="llm-prf-v1",
        prf_familying_version="conservative-family-v1",
        prf_gate_version="prf-gate-v1",
        term_surface="Agent工作流",
        term_family_id="feedback.agent-workflow",
        round_no=2,
        reject_reasons=["generic_phrase"],
        supporting_resume_ids=["r1", "r2"],
        negative_resume_ids=[],
    )

    runtime.flywheel_store.record_term_events([event])

    assert refs["candidates"] != artifact_refs.candidates_artifact_ref
    assert refs["policy_decision"] != artifact_refs.policy_decision_artifact_ref
    row = runtime.flywheel_store.connect().execute(
        """
        SELECT prf_candidate_artifact_ref_id, prf_policy_decision_artifact_ref_id
        FROM term_events
        WHERE run_id = ? AND term_event_id = ?
        """,
        (tracer.run_id, event["term_event_id"]),
    ).fetchone()
    assert tuple(row) == (refs["candidates"], refs["policy_decision"])
    tracer.close(status="completed")
