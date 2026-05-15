from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import seektalent.runtime.finalize_runtime as finalize_runtime
import seektalent.runtime.post_finalize_runtime as post_finalize_runtime
from seektalent.core.retrieval.provider_contract import ProviderSnapshot, SearchRequest, SearchResult
from seektalent.models import (
    FinalResult,
    InputTruth,
    RequirementSheet,
    ResumeCandidate,
    RetrievalState,
    RunState,
    RuntimeSourceEvidence,
    ScoredCandidate,
    ScoringPolicy,
)
from seektalent.runtime.source_lanes import (
    RuntimeDetailRecommendation,
    RuntimeSourceLaneEvent,
    RuntimeSourceLanePlan,
    RuntimeSourceLaneRequest,
    RuntimeSourceLaneResult,
    apply_source_lane_result,
    build_runtime_source_plan,
    clone_run_state_for_source_lane,
    normalize_source_kinds,
)
from seektalent.runtime.orchestrator import WorkflowRuntime
from seektalent.tracing import RunTracer
from seektalent.storage.json import sha256_json
from tests.settings_factory import make_settings


def _candidate(resume_id: str) -> ResumeCandidate:
    return ResumeCandidate(
        resume_id=resume_id,
        source_resume_id=f"provider-{resume_id}",
        snapshot_sha256=f"snapshot-{resume_id}",
        dedup_key=resume_id,
        search_text=f"{resume_id} python data platform",
        raw={"resume_id": resume_id},
    )


def _run_state() -> RunState:
    requirement_sheet = RequirementSheet(
        role_title="Data Engineer",
        title_anchor_terms=["Data Engineer"],
        title_anchor_rationale="Job title.",
        role_summary="Build data systems.",
        scoring_rationale="Score data systems first.",
    )
    return RunState(
        input_truth=InputTruth(
            job_title="Data Engineer",
            jd="Build data systems.",
            notes="",
            job_title_sha256="job",
            jd_sha256="jd",
            notes_sha256="notes",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            role_title=requirement_sheet.role_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=requirement_sheet.must_have_capabilities,
            preferred_capabilities=requirement_sheet.preferred_capabilities,
            exclusion_signals=requirement_sheet.exclusion_signals,
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(),
    )


def _evidence(
    evidence_id: str,
    *,
    source: str = "cts",
    evidence_level: str = "card",
    resume_id: str = "resume-1",
    collected_at: str = "2026-05-15T00:00:00Z",
) -> RuntimeSourceEvidence:
    return RuntimeSourceEvidence(
        evidence_id=evidence_id,
        source=source,
        provider=source,
        evidence_level=evidence_level,
        candidate_resume_id=resume_id,
        provider_candidate_key_hash=f"hash-{source}-{evidence_id}",
        query_fingerprint="query-1",
        provider_snapshot_ref=f"artifact://{source}/{evidence_id}",
        safe_summary_ref=f"artifact://summary/{evidence_id}",
        collected_at=collected_at,
        score_hint=80,
        reason_code="card_match",
    )


class _FakeLiepinWorker:
    def __init__(self) -> None:
        self.ensure_ready_calls = 0
        self.search_calls: list[dict[str, object]] = []
        self.open_details_calls = 0

    async def ensure_ready(self, *, on_event=None) -> None:
        del on_event
        self.ensure_ready_calls += 1

    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult:
        raw_payload = {"candidateId": "provider-resume-liepin"}
        self.search_calls.append(
            {
                "request": request,
                "round_no": round_no,
                "trace_id": trace_id,
                "provider_account_hash": provider_account_hash,
            }
        )
        return SearchResult(
            candidates=[
                ResumeCandidate(
                    resume_id="resume-liepin",
                    source_resume_id="provider-resume-liepin",
                    snapshot_sha256=sha256_json(raw_payload),
                    dedup_key="resume-liepin",
                    search_text="resume-liepin python data platform",
                    raw={"resume_id": "resume-liepin"},
                )
            ],
            provider_snapshots=[
                ProviderSnapshot(
                    provider_name="liepin",
                    payload_kind="card",
                    raw_payload=raw_payload,
                    normalized_text="resume-liepin python data platform",
                    provider_subject_id="provider-resume-liepin",
                    provider_listing_id=None,
                    synthetic_candidate_fingerprint="resume-liepin",
                    identity_confidence="provider_subject_id",
                    extraction_source="test",
                    extractor_version="test",
                    pii_classification="no_direct_contact",
                    retention_policy="provider_snapshot_7d",
                    access_scope="local_run_only",
                    redaction_state="raw_provider_payload",
                    score_evidence_source="card_only",
                )
            ],
            diagnostics=["fake liepin card lane"],
            exhausted=True,
            raw_candidate_count=1,
        )

    async def open_details(self, request) -> object:
        del request
        self.open_details_calls += 1
        raise AssertionError("card lane must not open details")


def test_normalize_source_kinds_defaults_to_cts() -> None:
    assert normalize_source_kinds(None) == ("cts",)
    assert normalize_source_kinds(["cts", "liepin"]) == ("cts", "liepin")


def test_normalize_source_kinds_rejects_unknown_and_duplicate_sources() -> None:
    with pytest.raises(ValueError, match="Unsupported runtime source"):
        normalize_source_kinds(["linkedin"])
    with pytest.raises(ValueError, match="Duplicate runtime source"):
        normalize_source_kinds(["cts", "cts"])


def test_source_plan_public_payload_uses_allowlist_and_redacts_posture() -> None:
    plan = RuntimeSourceLanePlan(
        source_plan_id="plan-1",
        runtime_run_id="run-1",
        source="liepin",
        label="Liepin",
        backend_mode="legacy_worker_compat",
        safe_posture={
            "connection_state": "connected",
            "approval_secret": "secret-value",
            "cookie": "sid=secret",
        },
    )

    payload = plan.to_public_payload()

    assert payload["source"] == "liepin"
    assert payload["backend_mode"] == "legacy_worker_compat"
    assert payload["safe_posture"] == {"connection_state": "connected"}
    assert "secret-value" not in repr(payload)
    assert "sid=secret" not in repr(payload)


def test_event_public_payload_is_finite_and_redacts_secret_like_values() -> None:
    event = RuntimeSourceLaneEvent(
        schema_version="runtime_source_lane_event_v1",
        runtime_run_id="run-1",
        source_plan_id="plan-1",
        source_lane_run_id="lane-1",
        source="liepin",
        attempt=1,
        event_seq=1,
        event_type="source_lane_completed",
        status="completed",
        safe_counts={"cards_seen": 4},
        safe_reason_code="Bearer token-value",
        artifact_refs=("artifact://safe", "cookie=session"),
    )

    payload = event.to_public_payload()

    assert set(payload) == {
        "schema_version",
        "runtime_run_id",
        "source_plan_id",
        "source_lane_run_id",
        "source",
        "attempt",
        "event_seq",
        "event_type",
        "status",
        "safe_counts",
        "safe_reason_code",
        "artifact_refs",
    }
    assert payload["safe_reason_code"] == "[REDACTED]"
    assert payload["artifact_refs"] == ["artifact://safe", "[REDACTED]"]


def test_source_evidence_public_payload_is_first_class_and_hash_only() -> None:
    evidence = _evidence("evidence-1", source="liepin")

    payload = evidence.to_public_payload()

    assert payload["provider_candidate_key_hash"] == "hash-liepin-evidence-1"
    assert "provider_candidate_key" not in payload
    assert "raw_resume" not in payload


def test_source_evidence_public_payload_redacts_secret_like_refs() -> None:
    evidence = RuntimeSourceEvidence(
        evidence_id="evidence-1",
        source="liepin",
        provider="liepin",
        evidence_level="card",
        candidate_resume_id="resume-1",
        provider_candidate_key_hash="hash-1",
        query_fingerprint="query-1",
        provider_snapshot_ref="artifact://protected?cookie=session-value",
        safe_summary_ref="Bearer token-value",
        collected_at="2026-05-15T00:00:00Z",
        reason_code="raw_resume copied into reason",
    )

    payload = evidence.to_public_payload()

    rendered = repr(payload)
    assert "session-value" not in rendered
    assert "token-value" not in rendered
    assert "raw_resume" not in rendered


def test_detail_recommendations_are_top_level_result_fields() -> None:
    recommendation = RuntimeDetailRecommendation(
        recommendation_id="rec-1",
        source="liepin",
        source_evidence_id="evidence-1",
        candidate_resume_id="resume-1",
        provider_candidate_key_hash="hash-1",
        value_score=91,
        reason_code="high_value_card",
        provider_snapshot_ref="artifact://snapshot",
        safe_summary_ref="artifact://summary",
    )
    result = RuntimeSourceLaneResult(
        runtime_run_id="run-1",
        source_plan_id="plan-1",
        source_lane_run_id="lane-1",
        source="liepin",
        lane_mode="card",
        attempt=1,
        status="completed",
        detail_recommendations=(recommendation,),
    )

    payload = result.to_public_payload()

    assert result.detail_recommendations == (recommendation,)
    assert payload["detail_recommendation_count"] == 1
    assert payload["detail_recommendations"][0]["recommendation_id"] == "rec-1"
    assert "events" not in payload["detail_recommendations"][0]


def test_apply_source_lane_result_preserves_multi_source_evidence_idempotently() -> None:
    run_state = _run_state()
    cts_result = RuntimeSourceLaneResult(
        runtime_run_id="run-1",
        source_plan_id="plan-1",
        source_lane_run_id="lane-cts",
        source="cts",
        lane_mode="card",
        attempt=1,
        status="completed",
        candidate_store_updates={"resume-1": _candidate("resume-1")},
        source_evidence_updates=(_evidence("evidence-cts", source="cts", evidence_level="card"),),
    )
    liepin_result = RuntimeSourceLaneResult(
        runtime_run_id="run-1",
        source_plan_id="plan-1",
        source_lane_run_id="lane-liepin",
        source="liepin",
        lane_mode="card",
        attempt=1,
        status="completed",
        candidate_store_updates={"resume-1": _candidate("resume-1")},
        source_evidence_updates=(
            _evidence("evidence-liepin-card", source="liepin", evidence_level="card"),
            _evidence("evidence-liepin-detail", source="liepin", evidence_level="detail"),
        ),
    )

    apply_source_lane_result(run_state=run_state, result=cts_result, source_order={"cts": 0, "liepin": 1})
    apply_source_lane_result(run_state=run_state, result=liepin_result, source_order={"cts": 0, "liepin": 1})
    apply_source_lane_result(run_state=run_state, result=liepin_result, source_order={"cts": 0, "liepin": 1})

    assert run_state.seen_resume_ids == ["resume-1"]
    assert [item.evidence_id for item in run_state.source_evidence_by_resume_id["resume-1"]] == [
        "evidence-cts",
        "evidence-liepin-card",
        "evidence-liepin-detail",
    ]


def test_clone_run_state_for_source_lane_removes_prior_lane_outputs() -> None:
    run_state = _run_state()
    run_state.candidate_store["resume-1"] = _candidate("resume-1")
    run_state.seen_resume_ids.append("resume-1")
    run_state.source_evidence_by_resume_id["resume-1"] = [_evidence("evidence-1")]

    lane_state = clone_run_state_for_source_lane(run_state)

    assert lane_state.input_truth == run_state.input_truth
    assert lane_state.requirement_sheet == run_state.requirement_sheet
    assert lane_state.scoring_policy == run_state.scoring_policy
    assert lane_state.candidate_store == {}
    assert lane_state.normalized_store == {}
    assert lane_state.source_evidence_by_resume_id == {}
    assert lane_state.seen_resume_ids == []
    assert run_state.candidate_store == {"resume-1": _candidate("resume-1")}


def test_blocked_lane_result_records_safe_event_without_candidate_mutation() -> None:
    run_state = _run_state()
    event = RuntimeSourceLaneEvent(
        schema_version="runtime_source_lane_event_v1",
        runtime_run_id="run-1",
        source_plan_id="plan-1",
        source_lane_run_id="lane-liepin",
        source="liepin",
        attempt=1,
        event_seq=1,
        event_type="source_lane_blocked",
        status="blocked",
        safe_reason_code="login_required",
    )
    result = RuntimeSourceLaneResult(
        runtime_run_id="run-1",
        source_plan_id="plan-1",
        source_lane_run_id="lane-liepin",
        source="liepin",
        lane_mode="card",
        attempt=1,
        status="blocked",
        events=(event,),
        blocked_reason_code="login_required",
    )

    apply_source_lane_result(run_state=run_state, result=result, source_order={"cts": 0, "liepin": 1})

    assert run_state.candidate_store == {}
    assert run_state.source_evidence_by_resume_id == {}


def test_build_runtime_source_plan_defaults_to_cts_and_uses_safe_liepin_context() -> None:
    settings = make_settings(liepin_worker_mode="managed_local")

    default_plan = build_runtime_source_plan(source_kinds=None, settings=settings, runtime_run_id="run-1")
    multi_source_plan = build_runtime_source_plan(
        source_kinds=["cts", "liepin"],
        settings=settings,
        runtime_run_id="run-1",
        liepin_context={"connection_state": "connected", "approval_secret": "secret"},
    )

    assert [plan.source for plan in default_plan] == ["cts"]
    assert [plan.source for plan in multi_source_plan] == ["cts", "liepin"]
    assert "secret" not in repr([plan.to_public_payload() for plan in multi_source_plan])


def test_runtime_writes_source_plan_artifact_with_public_payload(tmp_path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), liepin_worker_mode="managed_local")
    runtime = WorkflowRuntime(settings)
    tracer = RunTracer(settings.artifacts_path)
    try:
        source_plan = build_runtime_source_plan(
            source_kinds=["cts", "liepin"],
            settings=settings,
            runtime_run_id=tracer.run_id,
            liepin_context={"connection_state": "connected", "approval_secret": "secret-value"},
        )

        path = runtime._write_source_plan_artifact(tracer=tracer, source_plan=source_plan)
        payload = json.loads(path.read_text(encoding="utf-8"))
    finally:
        tracer.close()

    assert payload["schema_version"] == "runtime_source_plan_v1"
    assert payload["runtime_run_id"] == tracer.run_id
    assert [lane["source"] for lane in payload["source_lanes"]] == ["cts", "liepin"]
    assert "secret-value" not in repr(payload)


def test_runtime_cts_source_lane_uses_lane_local_state(tmp_path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), liepin_worker_mode="managed_local")
    runtime = WorkflowRuntime(settings)
    run_state = _run_state()
    run_state.candidate_store["resume-existing"] = _candidate("resume-existing")
    tracer = RunTracer(settings.artifacts_path)

    async def fake_run_rounds(*, run_state: RunState, tracer: RunTracer, progress_callback) -> tuple[list, str, int, None]:
        assert run_state.candidate_store == {}
        run_state.candidate_store["resume-cts"] = _candidate("resume-cts")
        return [], "max_rounds_reached", 1, None

    runtime._run_rounds = fake_run_rounds  # type: ignore[method-assign]
    source_plan = build_runtime_source_plan(source_kinds=["cts"], settings=settings, runtime_run_id=tracer.run_id)[0]
    try:
        result = asyncio.run(
            runtime._run_cts_source_lane(
                run_state=run_state,
                tracer=tracer,
                source_plan=source_plan,
                progress_callback=None,
            )
        )
    finally:
        tracer.close()

    assert result.source == "cts"
    assert result.status == "completed"
    assert result.candidate_store_updates == {"resume-cts": _candidate("resume-cts")}
    assert result.source_evidence_updates[0].candidate_resume_id == "resume-cts"
    assert "resume-cts" not in run_state.candidate_store
    assert run_state.candidate_store == {"resume-existing": _candidate("resume-existing")}


def test_runtime_liepin_card_source_lane_returns_delta_without_detail_open(tmp_path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"))
    runtime = WorkflowRuntime(settings)
    worker = _FakeLiepinWorker()
    request = RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="card",
        job_title="Backend Engineer",
        jd="FastAPI retrieval",
        notes="ranking",
        runtime_run_id="run-liepin",
        source_query_terms=("FastAPI", "retrieval"),
        liepin_context={
            "workspace_id": "workspace-1",
            "actor_id": "user-1",
            "connection_id": "conn-1",
            "provider_account_hash": "acct_hash_123",
        },
    )

    result = asyncio.run(runtime.run_source_lane_async(request, liepin_worker_client=worker))

    assert result.source == "liepin"
    assert result.lane_mode == "card"
    assert result.status == "completed"
    assert list(result.candidate_store_updates) == ["resume-liepin"]
    assert result.candidate_store_updates["resume-liepin"].snapshot_sha256 == sha256_json({"candidateId": "provider-resume-liepin"})
    assert result.source_evidence_updates[0].source == "liepin"
    assert result.raw_candidate_count == 1
    assert result.detail_recommendations == ()
    assert worker.ensure_ready_calls == 1
    assert worker.search_calls[0]["provider_account_hash"] == "acct_hash_123"
    assert worker.open_details_calls == 0


def test_runtime_liepin_detail_lane_requires_approved_lease(tmp_path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"))
    runtime = WorkflowRuntime(settings)
    worker = _FakeLiepinWorker()
    request = RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="detail",
        job_title="Backend Engineer",
        jd="FastAPI retrieval",
        notes=None,
        runtime_run_id="run-liepin",
    )

    result = asyncio.run(runtime.run_source_lane_async(request, liepin_worker_client=worker))

    assert result.status == "blocked"
    assert result.blocked_reason_code == "blocked_approval_missing"
    assert worker.ensure_ready_calls == 0
    assert worker.search_calls == []
    assert worker.open_details_calls == 0


def _scored_candidate(resume_id: str, *, source_round: int) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        fit_bucket="fit",
        overall_score=88,
        must_have_match_score=90,
        preferred_match_score=80,
        risk_score=10,
        reasoning_summary=f"{resume_id} matches the role.",
        evidence=["Python data platform evidence."],
        confidence="high",
        matched_must_haves=["Python"],
        source_round=source_round,
    )


def test_full_runtime_run_merges_selected_source_lanes_before_finalization(tmp_path, monkeypatch) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), liepin_worker_mode="managed_local")
    runtime = WorkflowRuntime(settings)
    finalized_candidate_ids: list[str] = []

    async def fake_build_run_state(**kwargs) -> RunState:
        del kwargs
        return _run_state()

    async def fake_cts_lane(**kwargs) -> RuntimeSourceLaneResult:
        source_plan = kwargs["source_plan"]
        return RuntimeSourceLaneResult(
            runtime_run_id=source_plan.runtime_run_id,
            source_plan_id=source_plan.source_plan_id,
            source_lane_run_id=f"{source_plan.source_plan_id}:lane:1",
            source="cts",
            lane_mode="card",
            attempt=1,
            status="completed",
            candidate_store_updates={"resume-cts": _candidate("resume-cts")},
            source_evidence_updates=(_evidence("evidence-cts", source="cts", resume_id="resume-cts"),),
        )

    async def fake_liepin_lane(request, *, liepin_worker_client=None) -> RuntimeSourceLaneResult:
        del liepin_worker_client
        return RuntimeSourceLaneResult(
            runtime_run_id=request.runtime_run_id or "run-1",
            source_plan_id=request.source_plan_id or "plan-liepin",
            source_lane_run_id=request.source_lane_run_id or "lane-liepin",
            source="liepin",
            lane_mode="card",
            attempt=1,
            status="completed",
            candidate_store_updates={"resume-liepin": _candidate("resume-liepin")},
            source_evidence_updates=(
                _evidence("evidence-liepin", source="liepin", resume_id="resume-liepin"),
            ),
        )

    async def fake_score_round(*, round_no, new_candidates, run_state, tracer, runtime_only_constraints):
        del tracer, runtime_only_constraints
        scored = [_scored_candidate(candidate.resume_id, source_round=round_no) for candidate in new_candidates]
        for item in scored:
            run_state.scorecards_by_resume_id[item.resume_id] = item
        run_state.top_pool_ids = [item.resume_id for item in scored]
        return scored, [], []

    async def fake_run_finalizer_stage(**kwargs):
        context = kwargs["finalize_context"]
        finalized_candidate_ids.extend(candidate.resume_id for candidate in context.top_candidates)
        return (
            FinalResult(
                run_id=context.run_id,
                run_dir=context.run_dir,
                rounds_executed=context.rounds_executed,
                stop_reason=context.stop_reason,
                candidates=[],
                summary="Finalized merged sources.",
            ),
            "final markdown",
            {"call_id": "fake-finalizer", "artifacts": [], "latency_ms": 0},
        )

    async def fake_run_post_finalize_stage(**kwargs):
        del kwargs
        return SimpleNamespace(evaluation_result=None)

    monkeypatch.setattr(runtime, "_require_live_llm_config", lambda: None)
    monkeypatch.setattr(runtime, "_build_run_state", fake_build_run_state)
    monkeypatch.setattr(runtime, "_run_cts_source_lane", fake_cts_lane)
    monkeypatch.setattr(runtime, "_run_liepin_source_lane_request", fake_liepin_lane)
    monkeypatch.setattr(runtime, "_score_round", fake_score_round)
    monkeypatch.setattr(finalize_runtime, "run_finalizer_stage", fake_run_finalizer_stage)
    monkeypatch.setattr(finalize_runtime, "finalize_finalizer_stage", lambda **kwargs: None)
    monkeypatch.setattr(post_finalize_runtime, "write_post_finalize_artifacts", lambda **kwargs: [])
    monkeypatch.setattr(
        post_finalize_runtime,
        "run_post_finalize_stage",
        fake_run_post_finalize_stage,
    )

    artifacts = asyncio.run(
        runtime.run_async(
            job_title="Backend Engineer",
            jd="FastAPI retrieval",
            notes="",
            source_kinds=["cts", "liepin"],
        )
    )

    assert set(artifacts.candidate_store) == {"resume-cts", "resume-liepin"}
    assert finalized_candidate_ids == ["resume-cts", "resume-liepin"]


def test_full_source_lanes_keep_cts_when_liepin_backend_is_blocked(tmp_path, monkeypatch) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), liepin_worker_mode="disabled")
    runtime = WorkflowRuntime(settings)
    run_state = _run_state()
    tracer = RunTracer(settings.artifacts_path)
    source_plan = build_runtime_source_plan(
        source_kinds=["cts", "liepin"],
        settings=settings,
        runtime_run_id=tracer.run_id,
    )

    async def fake_cts_lane(**kwargs) -> RuntimeSourceLaneResult:
        source_plan = kwargs["source_plan"]
        return RuntimeSourceLaneResult(
            runtime_run_id=source_plan.runtime_run_id,
            source_plan_id=source_plan.source_plan_id,
            source_lane_run_id=f"{source_plan.source_plan_id}:lane:1",
            source="cts",
            lane_mode="card",
            attempt=1,
            status="completed",
            candidate_store_updates={"resume-cts": _candidate("resume-cts")},
            source_evidence_updates=(_evidence("evidence-cts", source="cts", resume_id="resume-cts"),),
        )

    async def fake_score_round(*, round_no, new_candidates, run_state, tracer, runtime_only_constraints):
        del tracer, runtime_only_constraints
        scored = [_scored_candidate(candidate.resume_id, source_round=round_no) for candidate in new_candidates]
        for item in scored:
            run_state.scorecards_by_resume_id[item.resume_id] = item
        run_state.top_pool_ids = [item.resume_id for item in scored]
        return scored, [], []

    monkeypatch.setattr(runtime, "_run_cts_source_lane", fake_cts_lane)
    monkeypatch.setattr(runtime, "_score_round", fake_score_round)
    try:
        top_scored, stop_reason, _, _ = asyncio.run(
            runtime._run_full_source_lanes(
                run_state=run_state,
                tracer=tracer,
                source_plan=source_plan,
                liepin_context=None,
                progress_callback=None,
            )
        )
    finally:
        tracer.close()

    assert [item.resume_id for item in top_scored] == ["resume-cts"]
    assert stop_reason == "source_lanes_degraded"
    assert set(run_state.candidate_store) == {"resume-cts"}


def test_full_source_lanes_do_not_publish_raw_provider_exception_text(tmp_path, monkeypatch) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), liepin_worker_mode="managed_local")
    runtime = WorkflowRuntime(settings)
    run_state = _run_state()
    tracer = RunTracer(settings.artifacts_path)
    run_dir = tracer.run_dir
    source_plan = build_runtime_source_plan(
        source_kinds=["liepin"],
        settings=settings,
        runtime_run_id=tracer.run_id,
    )

    async def fake_liepin_lane(request, *, liepin_worker_client=None) -> RuntimeSourceLaneResult:
        del request, liepin_worker_client
        raise RuntimeError("provider raw resume phone 13800138000")

    monkeypatch.setattr(runtime, "_run_liepin_source_lane_request", fake_liepin_lane)
    try:
        asyncio.run(
            runtime._run_full_source_lanes(
                run_state=run_state,
                tracer=tracer,
                source_plan=source_plan,
                liepin_context={},
                progress_callback=None,
            )
        )
    finally:
        tracer.close()

    source_lane_artifacts = sorted(run_dir.glob("runtime.source_lane.liepin.card.1"))
    assert source_lane_artifacts
    payload_text = source_lane_artifacts[-1].read_text(encoding="utf-8")
    assert "13800138000" not in payload_text
    assert "raw resume" not in payload_text


def test_runtime_source_lane_request_public_payload_excludes_callbacks_and_secret_refs() -> None:
    request = RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="detail",
        job_title="Data Engineer",
        jd="Build data systems.",
        notes=None,
        approved_detail_lease_ref="lease-1",
        liepin_context={"approval_secret_ref": "secret-ref", "connection_id": "conn-1"},
        progress_callback=lambda event: None,
    )

    payload = request.to_public_payload()

    assert payload["source"] == "liepin"
    assert payload["lane_mode"] == "detail"
    assert payload["approved_detail_lease_ref"] == "lease-1"
    assert "progress_callback" not in payload
    assert "secret-ref" not in repr(payload)
