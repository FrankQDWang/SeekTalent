from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from seektalent.config import AppSettings
from seektalent.models import RequirementSheet, ResumeCandidate, RuntimeSourceEvidence
from seektalent.runtime import RunArtifacts
from seektalent.runtime.source_lanes import RuntimeDetailRecommendation, RuntimeSourceLaneEvent, RuntimeSourceLaneResult
from seektalent_ui.runtime_bridge import run_liepin_detail_open_intent, run_runtime_sourcing_job
from seektalent_ui.workbench_store import WorkbenchStore, WorkbenchUser


@dataclass
class FakeRuntime:
    calls: list[dict[str, Any]]

    def run(self, **kwargs: Any) -> RunArtifacts:
        self.calls.append(kwargs)
        return RunArtifacts(
            run_id="run_dual_source_1",
            run_dir=Path("/tmp/seektalent-test-run"),
            trace_log_path=Path("/tmp/seektalent-test-run/trace.jsonl"),
            final_markdown="final",
            final_result=None,
            candidate_store={},
            normalized_store={},
            evaluation_result=None,
            terminal_stop_guidance=None,
            run_state=None,
        )

    def run_source_lane(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("Workbench primary run must not call run_source_lane")


@dataclass
class FakeDetailRuntime:
    calls: list[dict[str, Any]]

    def run_source_lane(self, request, *, liepin_worker_client=None):
        self.calls.append({"request": request, "liepin_worker_client": liepin_worker_client})
        return RuntimeSourceLaneResult(
            runtime_run_id=request.runtime_run_id or "run-detail-1",
            source_plan_id=request.source_plan_id or "run-detail-1:source:liepin",
            source_lane_run_id=request.source_lane_run_id or "run-detail-1:lane:liepin:detail",
            source="liepin",
            lane_mode="detail",
            attempt=1,
            status="completed",
            candidate_store_updates={
                "provider-candidate-1": ResumeCandidate(
                    resume_id="provider-candidate-1",
                    source_resume_id="provider-candidate-1",
                    dedup_key="provider-candidate-1",
                    search_text="数据开发专家 Python Spark 实时数仓",
                    raw={
                        "candidate_name": "L 候选人",
                        "current_title": "数据开发专家",
                        "current_company": "Example Data",
                        "provider_candidate_key_hash": "hash-liepin-provider-candidate-1",
                    },
                )
            },
            source_evidence_updates=(
                RuntimeSourceEvidence(
                    evidence_id="evidence-liepin-detail-a",
                    source="liepin",
                    provider="liepin",
                    evidence_level="detail",
                    candidate_resume_id="provider-candidate-1",
                    provider_candidate_key_hash="hash-liepin-provider-candidate-1",
                    collected_at="2026-05-21T00:00:00+08:00",
                    reason_code="source_detail_candidate",
                    safe_reason_codes=("source_detail_candidate",),
                ),
            ),
            raw_candidate_count=1,
            events=(
                RuntimeSourceLaneEvent(
                    schema_version="runtime_source_lane_event_v1",
                    runtime_run_id=request.runtime_run_id or "run-detail-1",
                    source_plan_id=request.source_plan_id or "run-detail-1:source:liepin",
                    source_lane_run_id=request.source_lane_run_id or "run-detail-1:lane:liepin:detail",
                    source="liepin",
                    attempt=1,
                    event_seq=1,
                    event_type="detail_completed",
                    status="completed",
                    safe_counts={"details_opened": 1},
                ),
            ),
        )


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        artifacts_path=tmp_path / "artifacts",
        cache_path=tmp_path / "cache",
        corpus_path=tmp_path / "corpus",
        flywheel_path=tmp_path / "flywheel.sqlite3",
        workbench_db_path=tmp_path / "workbench.sqlite3",
        mock_cts=True,
        text_llm_provider="deepseek",
        provider_api_key="test-key",
    )


def _user() -> WorkbenchUser:
    return WorkbenchUser(
        user_id="user_qa",
        email="qa@example.com",
        display_name="QA",
        role="admin",
        workspace_id="default",
    )


def _approved_source_session(store: WorkbenchStore, *, source_kinds: list[str]):
    user, _workspace = store.bootstrap_admin(
        email="qa@example.com",
        display_name="QA",
        password_hash="test-hash",
    )
    connection, _created = store.get_or_create_liepin_source_connection(user=user)
    connected = store.mark_liepin_connection_connected(
        user=user,
        connection_id=connection.connection_id,
        provider_account_hash="acct_hash_123",
    )
    assert connected is not None
    session = store.create_workbench_session(
        user=user,
        job_title="数据开发专家",
        jd_text="负责数据平台建设",
        notes="必备条件：Python",
        source_kinds=source_kinds,
    )
    sheet = RequirementSheet(
        job_title=session.job_title,
        title_anchor_terms=["数据开发"],
        title_anchor_rationale="数据开发 is the searchable title anchor.",
        role_summary="负责数据平台建设。",
        must_have_capabilities=["Python"],
        preferred_capabilities=[],
        exclusion_signals=[],
        hard_constraints={},
        preferences={"preferred_query_terms": ["数据开发"]},
        initial_query_term_pool=[],
        scoring_rationale="Prioritize Python data platform evidence.",
    )
    review = store.update_requirement_review(
        user=user,
        session_id=session.session_id,
        requirement_sheet=sheet,
    )
    assert review is not None
    store.approve_requirement_review(user=user, session_id=session.session_id)
    return user, session


def _approved_dual_source_session(store: WorkbenchStore):
    return _approved_source_session(store, source_kinds=["cts", "liepin"])


def _source_statuses_by_kind(store: WorkbenchStore, session_id: str) -> dict[str, str]:
    with sqlite3.connect(store.db_path) as conn:
        return {
            source_kind: status
            for source_kind, status in conn.execute(
                "SELECT source_kind, status FROM source_runs WHERE session_id = ?",
                (session_id,),
            ).fetchall()
        }


def test_runtime_bridge_calls_runtime_once_for_dual_source_session(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_dual_source_session(store)
    job = store.start_runtime_sourcing_job(
        user=user,
        session_id=session.session_id,
        idempotency_key="start-agent",
    )
    assert job is not None
    context = store.claim_next_runtime_sourcing_job(
        owner_id="test-owner",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    assert context is not None
    fake_runtime = FakeRuntime(calls=[])

    run_runtime_sourcing_job(
        context=context,
        store=store,
        settings=_settings(tmp_path),
        runtime_factory=lambda settings: fake_runtime,
        progress_callback=None,
    )

    assert len(fake_runtime.calls) == 1
    call = fake_runtime.calls[0]
    assert call["source_kinds"] == ("cts", "liepin")
    assert call["notes"] == session.notes
    assert call["approved_requirement_sheet"].job_title == session.job_title
    assert call["requirement_cache_scope"] == session.session_id


def test_runtime_sourcing_job_scope_excludes_completed_source_runs(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_dual_source_session(store)
    refreshed = store.get_workbench_session(user=user, session_id=session.session_id)
    assert refreshed is not None
    source_run_ids = {source_run.source_kind: source_run.source_run_id for source_run in refreshed.source_runs}
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE source_runs
            SET status = CASE source_kind WHEN 'cts' THEN 'completed' ELSE 'queued' END
            WHERE session_id = ?
            """,
            (session.session_id,),
        )

    created = store.start_runtime_sourcing_job(
        user=user,
        session_id=session.session_id,
        idempotency_key="liepin-rerun",
    )

    assert created is not None
    job, was_created = created
    assert was_created is True
    assert job.source_kinds == ("liepin",)
    assert _source_statuses_by_kind(store, session.session_id) == {"cts": "completed", "liepin": "queued"}
    context = store.claim_next_runtime_sourcing_job(
        owner_id="test-owner",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    assert context is not None
    assert context.job.source_kinds == ("liepin",)
    assert _source_statuses_by_kind(store, session.session_id) == {"cts": "completed", "liepin": "running"}

    store.fail_runtime_sourcing_job(context=context, error_message="provider failed")

    assert _source_statuses_by_kind(store, session.session_id) == {"cts": "completed", "liepin": "failed"}
    events = store.list_session_workbench_events(user=user, session_id=session.session_id, after_seq=0)
    source_events = [
        (event.source_kind, event.event_name)
        for event in events
        if event.event_name in {"source_run_started", "source_run_failed"}
    ]
    assert ("liepin", "source_run_started") in source_events
    assert ("liepin", "source_run_failed") in source_events
    assert ("cts", "source_run_started") not in source_events
    assert ("cts", "source_run_failed") not in source_events
    assert source_run_ids["cts"] != source_run_ids["liepin"]


def test_start_runtime_sourcing_job_keeps_unblocked_selected_source_when_liepin_is_blocked(
    tmp_path: Path,
) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_dual_source_session(store)
    refreshed = store.get_workbench_session(user=user, session_id=session.session_id)
    assert refreshed is not None
    liepin_run = next(source_run for source_run in refreshed.source_runs if source_run.source_kind == "liepin")
    store.block_source_run_for_start_probe(
        user=user,
        session_id=session.session_id,
        source_run_id=liepin_run.source_run_id,
        warning_code="liepin_opencli_risk_page",
        warning_message="Risk verification required.",
    )

    created = store.start_runtime_sourcing_job(
        user=user,
        session_id=session.session_id,
        idempotency_key="runtime",
    )

    assert created is not None
    job, was_created = created
    assert was_created is True
    assert job.source_kinds == ("cts",)
    refreshed = store.get_workbench_session(user=user, session_id=session.session_id)
    assert refreshed is not None
    source_statuses = {source_run.source_kind: source_run.status for source_run in refreshed.source_runs}
    assert source_statuses == {"cts": "queued", "liepin": "blocked"}


def test_start_runtime_sourcing_job_rejects_when_every_selected_source_is_blocked(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_source_session(store, source_kinds=["liepin"])
    refreshed = store.get_workbench_session(user=user, session_id=session.session_id)
    assert refreshed is not None
    liepin_run = refreshed.source_runs[0]
    store.block_source_run_for_start_probe(
        user=user,
        session_id=session.session_id,
        source_run_id=liepin_run.source_run_id,
        warning_code="liepin_opencli_risk_page",
        warning_message="Risk verification required.",
    )

    with pytest.raises(PermissionError, match="selected_source_blocked"):
        store.start_runtime_sourcing_job(
            user=user,
            session_id=session.session_id,
            idempotency_key="runtime",
        )


def test_runtime_bridge_runs_only_unblocked_sources_for_partially_blocked_job(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_dual_source_session(store)
    refreshed = store.get_workbench_session(user=user, session_id=session.session_id)
    assert refreshed is not None
    liepin_run = next(source_run for source_run in refreshed.source_runs if source_run.source_kind == "liepin")
    store.block_source_run_for_start_probe(
        user=user,
        session_id=session.session_id,
        source_run_id=liepin_run.source_run_id,
        warning_code="liepin_opencli_risk_page",
        warning_message="Risk verification required.",
    )
    job = store.start_runtime_sourcing_job(
        user=user,
        session_id=session.session_id,
        idempotency_key="start-agent",
    )
    assert job is not None
    context = store.claim_next_runtime_sourcing_job(
        owner_id="test-owner",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    assert context is not None
    fake_runtime = FakeRuntime(calls=[])

    run_runtime_sourcing_job(
        context=context,
        store=store,
        settings=_settings(tmp_path),
        runtime_factory=lambda settings: fake_runtime,
        progress_callback=None,
    )

    assert len(fake_runtime.calls) == 1
    assert fake_runtime.calls[0]["source_kinds"] == ("cts",)
    assert "liepin_context" not in fake_runtime.calls[0]


def test_runtime_bridge_does_not_seed_requirement_cache(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, _workspace = store.bootstrap_admin(
        email="qa@example.com",
        display_name="QA",
        password_hash="test-hash",
    )
    connection, _created = store.get_or_create_liepin_source_connection(user=user)
    connected = store.mark_liepin_connection_connected(
        user=user,
        connection_id=connection.connection_id,
        provider_account_hash="acct_hash_123",
    )
    assert connected is not None
    session = store.create_workbench_session(
        user=user,
        job_title="数据开发专家",
        jd_text=(
            "JD基本信息\n"
            "任职要求\n"
            "1、本科及以上学历，计算机、数学、软件工程等相关专业；\n"
            "2、5 年及以上数据开发相关经验。\n"
            "工作城市:\n"
            "北京，招聘1人，详细地址：北京市海淀区清华科技园B座\n"
            "职位要求\n"
            "学历要求:\n"
            "本科·统招·985/211\n"
            "工作年限:\n"
            "不限\n"
        ),
        notes="无补充",
        source_kinds=["cts", "liepin"],
    )
    sheet = RequirementSheet(
        job_title=session.job_title,
        title_anchor_terms=["数据开发"],
        title_anchor_rationale="数据开发 is the searchable title anchor.",
        role_summary="负责数据平台建设。",
        must_have_capabilities=["大规模数据处理与治理"],
        preferred_capabilities=["大数据技术栈"],
        exclusion_signals=[],
        hard_constraints={},
        preferences={"preferred_query_terms": ["数据开发", "ETL"]},
        initial_query_term_pool=[],
        scoring_rationale="Prioritize data platform evidence.",
    )
    review = store.update_requirement_review(
        user=user,
        session_id=session.session_id,
        requirement_sheet=sheet,
    )
    assert review is not None
    store.approve_requirement_review(user=user, session_id=session.session_id)
    job = store.start_runtime_sourcing_job(
        user=user,
        session_id=session.session_id,
        idempotency_key="start-agent",
    )
    assert job is not None
    context = store.claim_next_runtime_sourcing_job(
        owner_id="test-owner",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    assert context is not None
    settings = _settings(tmp_path)
    fake_runtime = FakeRuntime(calls=[])

    run_runtime_sourcing_job(
        context=context,
        store=store,
        settings=settings,
        runtime_factory=lambda settings: fake_runtime,
        progress_callback=None,
    )

    call = fake_runtime.calls[0]
    assert call["notes"] == session.notes
    assert call["approved_requirement_sheet"] == sheet


def test_starting_dual_source_session_does_not_enqueue_primary_source_run_jobs(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_dual_source_session(store)

    created = store.start_runtime_sourcing_job(
        user=user,
        session_id=session.session_id,
        idempotency_key="start-agent",
    )

    assert created is not None
    with store._connect() as conn:
        source_job_count = conn.execute(
            "SELECT COUNT(*) FROM source_run_jobs WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()[0]
        runtime_job_count = conn.execute(
            "SELECT COUNT(*) FROM runtime_sourcing_jobs WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()[0]
    assert source_job_count == 0
    assert runtime_job_count == 1


def test_workbench_store_no_longer_exposes_primary_source_run_queue_api() -> None:
    removed_methods = {
        "_".join(parts)
        for parts in [
            ("start", "source", "run", "job"),
            ("claim", "next", "source", "run", "job"),
            ("extend", "source", "run", "job", "lease"),
            ("complete", "cts", "source", "run", "with", "candidate", "results"),
            ("complete", "liepin", "card", "source", "run", "with", "lane", "result"),
            ("complete", "liepin", "source", "run", "with", "lane", "result"),
            ("mark", "source", "run", "failed"),
            ("reconcile", "expired", "running", "jobs"),
        ]
    }

    for method_name in removed_methods:
        assert not hasattr(WorkbenchStore, method_name), method_name


def _runtime_candidate(
    resume_id: str,
    *,
    source_resume_id: str | None = None,
    source_round: int | None = 1,
) -> ResumeCandidate:
    return ResumeCandidate(
        resume_id=resume_id,
        source_resume_id=source_resume_id or resume_id,
        dedup_key=resume_id,
        source_round=source_round,
        search_text=f"{resume_id} data platform python",
        raw={"candidate_name": resume_id, "current_title": "数据开发专家", "current_company": "Example"},
    )


def _source_evidence(
    *,
    evidence_id: str,
    source: str,
    source_run_id: str,
    resume_id: str,
    source_lane_run_id: str | None = None,
) -> RuntimeSourceEvidence:
    return RuntimeSourceEvidence(
        evidence_id=evidence_id,
        source=source,
        provider=source,
        source_plan_id=f"plan-{source}",
        source_lane_run_id=source_lane_run_id or source_run_id,
        evidence_level="card",
        candidate_resume_id=resume_id,
        provider_candidate_key_hash=f"hash-{source}-{resume_id}",
        collected_at="2026-05-21T00:00:00+08:00",
        reason_code="source_card_candidate",
        safe_reason_codes=("source_card_candidate",),
    )


def _source_lane_result(
    *,
    runtime_run_id: str,
    source: str,
    candidate_count: int,
    detail_recommendations: tuple[RuntimeDetailRecommendation, ...] = (),
) -> RuntimeSourceLaneResult:
    source_plan_id = f"{runtime_run_id}:source:{source}"
    source_lane_run_id = f"{source_plan_id}:round:1"
    events = [
        RuntimeSourceLaneEvent(
            schema_version="runtime_source_lane_event_v1",
            runtime_run_id=runtime_run_id,
            source_plan_id=source_plan_id,
            source_lane_run_id=source_lane_run_id,
            source=source,
            attempt=1,
            event_seq=1,
            event_type="source_lane_completed",
            status="completed",
            safe_counts={"cards_seen": candidate_count, "candidates": candidate_count},
        )
    ]
    if detail_recommendations:
        events.append(
            RuntimeSourceLaneEvent(
                schema_version="runtime_source_lane_event_v1",
                runtime_run_id=runtime_run_id,
                source_plan_id=source_plan_id,
                source_lane_run_id=source_lane_run_id,
                source=source,
                attempt=1,
                event_seq=2,
                event_type="detail_recommended",
                status="completed",
                safe_counts={"detail_recommendations": len(detail_recommendations)},
                safe_reason_code="matched_card_terms",
            )
        )
    return RuntimeSourceLaneResult(
        runtime_run_id=runtime_run_id,
        source_plan_id=source_plan_id,
        source_lane_run_id=source_lane_run_id,
        source=source,
        lane_mode="card",
        attempt=1,
        status="completed",
        raw_candidate_count=candidate_count,
        detail_recommendations=detail_recommendations,
        events=tuple(events),
    )


def _single_final_candidate_artifacts(
    *,
    label: str,
    runtime_run_id: str,
    source_run_id: str,
) -> SimpleNamespace:
    run_state = SimpleNamespace(
        top_pool_ids=["resume-a"],
        candidate_identity_by_resume_id={"resume-a": "identity-a"},
        canonical_resume_by_identity_id={"identity-a": SimpleNamespace(canonical_resume_id="resume-a")},
        candidate_identities={"identity-a": SimpleNamespace(resume_ids=("resume-a",))},
        source_evidence_by_identity_id={
            "identity-a": [
                _source_evidence(
                    evidence_id="evidence-cts-a",
                    source="cts",
                    source_run_id=source_run_id,
                    resume_id="resume-a",
                )
            ],
        },
        source_coverage_summary=SimpleNamespace(to_public_payload=lambda: {"status": "complete"}),
    )
    return SimpleNamespace(
        run_id=runtime_run_id,
        run_state=run_state,
        candidate_store={"resume-a": _runtime_candidate("resume-a")},
        normalized_store={},
        final_result=SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    resume_id="resume-a",
                    final_score=90,
                    fit_bucket="fit",
                    match_summary=f"summary-{label}",
                    why_selected=f"why-{label}",
                    strengths=[f"strength-{label}"],
                    weaknesses=[f"weakness-{label}"],
                    matched_must_haves=[f"must-{label}"],
                    matched_preferences=[f"preference-{label}"],
                    risk_flags=[f"risk-{label}"],
                    source_round=1,
                )
            ]
        ),
    )


def test_runtime_completion_persists_finalization_order_and_all_source_evidence(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_dual_source_session(store)
    job = store.start_runtime_sourcing_job(user=user, session_id=session.session_id, idempotency_key="final")
    assert job is not None
    context = store.claim_next_runtime_sourcing_job(
        owner_id="test-owner",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    assert context is not None
    source_run_by_kind = {source_run.source_kind: source_run for source_run in context.session.source_runs}
    run_state = SimpleNamespace(
        top_pool_ids=["resume-b", "resume-a", "resume-c"],
        candidate_identity_by_resume_id={
            "resume-a": "identity-a",
            "resume-c": "identity-a",
            "resume-b": "identity-b",
            "resume-d": "identity-d",
        },
        canonical_resume_by_identity_id={
            "identity-a": SimpleNamespace(canonical_resume_id="resume-a"),
            "identity-b": SimpleNamespace(canonical_resume_id="resume-b"),
            "identity-d": SimpleNamespace(canonical_resume_id="resume-d"),
        },
        candidate_identities={
            "identity-a": SimpleNamespace(resume_ids=("resume-a", "resume-c")),
            "identity-b": SimpleNamespace(resume_ids=("resume-b",)),
            "identity-d": SimpleNamespace(resume_ids=("resume-d",)),
        },
        source_evidence_by_identity_id={
            "identity-a": [
                _source_evidence(
                    evidence_id="evidence-cts-a",
                    source="cts",
                    source_run_id=source_run_by_kind["cts"].source_run_id,
                    resume_id="resume-a",
                ),
                _source_evidence(
                    evidence_id="evidence-liepin-a",
                    source="liepin",
                    source_run_id=source_run_by_kind["liepin"].source_run_id,
                    resume_id="resume-c",
                ),
            ],
            "identity-b": [
                _source_evidence(
                    evidence_id="evidence-cts-b",
                    source="cts",
                    source_run_id=source_run_by_kind["cts"].source_run_id,
                    resume_id="resume-b",
                )
            ],
            "identity-d": [
                _source_evidence(
                    evidence_id="evidence-liepin-d",
                    source="liepin",
                    source_run_id=source_run_by_kind["liepin"].source_run_id,
                    resume_id="resume-d",
                )
            ],
        },
        source_coverage_summary=SimpleNamespace(to_public_payload=lambda: {"status": "complete"}),
    )
    artifacts = SimpleNamespace(
        run_id="run-final-1",
        run_state=run_state,
        candidate_store={
            "resume-a": _runtime_candidate("resume-a"),
            "resume-b": _runtime_candidate("resume-b"),
            "resume-c": _runtime_candidate("resume-c"),
            "resume-d": _runtime_candidate("resume-d"),
        },
        normalized_store={},
        final_result=SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    resume_id="resume-a",
                    final_score=91,
                    fit_bucket="fit",
                    match_summary="A",
                    why_selected="A is selected for stronger direct evidence.",
                    strengths=["Strong retrieval evidence"],
                    weaknesses=["Compensation unknown"],
                    matched_must_haves=["retrieval systems"],
                    matched_preferences=["agent tooling"],
                    risk_flags=["availability unclear"],
                    source_round=1,
                ),
                SimpleNamespace(
                    resume_id="resume-b",
                    final_score=88,
                    fit_bucket="fit",
                    match_summary="B",
                    why_selected="B is selected for Python platform depth.",
                    strengths=["Strong backend systems"],
                    weaknesses=["Needs calibration on leadership scope"],
                    matched_must_haves=["Python", "distributed systems"],
                    matched_preferences=["agent tooling"],
                    risk_flags=["management scope unclear"],
                    source_round=1,
                ),
            ]
        ),
    )

    store.complete_runtime_sourcing_job_with_artifacts(context=context, artifacts=artifacts)

    final = store.list_runtime_final_top_review_items(user=user, session_id=session.session_id)
    assert final is not None
    revision, items = final
    assert revision == 1
    assert [item.evidence[0].runtime_identity_id for item in items] == ["identity-b", "identity-a"]
    identity_b = items[0]
    assert identity_b.summary == "B"
    assert identity_b.aggregate_score == 88
    assert identity_b.fit_bucket == "fit"
    assert identity_b.why_selected == "B is selected for Python platform depth."
    assert identity_b.source_round == 1
    assert identity_b.matched_must_haves == ["Python", "distributed systems"]
    assert identity_b.matched_preferences == ["agent tooling"]
    assert identity_b.missing_risks == ["management scope unclear"]
    assert identity_b.strengths == ["Strong backend systems"]
    assert identity_b.weaknesses == ["Needs calibration on leadership scope"]
    identity_a = items[1]
    assert {evidence.source_kind for evidence in identity_a.evidence} == {"cts", "liepin"}
    with store._connect() as conn:
        persisted_order = conn.execute(
            """
            SELECT ordered_candidate_identity_ids_json
            FROM runtime_finalization_revisions
            WHERE session_id = ?
            """,
            (session.session_id,),
        ).fetchone()[0]
        non_final_evidence = conn.execute(
            """
            SELECT cri.source_round
            FROM candidate_evidence ce
            JOIN candidate_review_items cri ON cri.review_item_id = ce.review_item_id
            WHERE ce.evidence_id = 'evidence-liepin-d'
            """
        ).fetchone()
    assert persisted_order == '["identity-b","identity-a"]'
    assert non_final_evidence is not None
    assert non_final_evidence[0] == 1


def test_runtime_final_candidate_persistence_batches_homogeneous_rows() -> None:
    source = Path("src/seektalent_ui/workbench_store.py").read_text(encoding="utf-8")
    section = source.split("def _persist_runtime_final_candidate_results_conn", 1)[1].split(
        "def _persist_runtime_source_lane_events_conn",
        1,
    )[0]

    assert section.count("conn.executemany(") >= 3


def test_runtime_checkpoint_persists_candidate_index_without_finalization(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_dual_source_session(store)
    job = store.start_runtime_sourcing_job(user=user, session_id=session.session_id, idempotency_key="checkpoint")
    assert job is not None
    context = store.claim_next_runtime_sourcing_job(
        owner_id="test-owner",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    assert context is not None
    source_run_by_kind = {source_run.source_kind: source_run for source_run in context.session.source_runs}
    run_state = SimpleNamespace(
        top_pool_ids=["resume-a"],
        candidate_identity_by_resume_id={"resume-a": "identity-a"},
        canonical_resume_by_identity_id={"identity-a": SimpleNamespace(canonical_resume_id="resume-a")},
        candidate_identities={"identity-a": SimpleNamespace(resume_ids=("resume-a",))},
        source_evidence_by_identity_id={
            "identity-a": [
                _source_evidence(
                    evidence_id="evidence-cts-a",
                    source="cts",
                    source_run_id=source_run_by_kind["cts"].source_run_id,
                    resume_id="resume-a",
                )
            ],
        },
        source_coverage_summary=SimpleNamespace(to_public_payload=lambda: {"status": "running"}),
    )
    artifacts = SimpleNamespace(
        run_id="run-checkpoint-1",
        run_state=run_state,
        candidate_store={"resume-a": _runtime_candidate("resume-a")},
        normalized_store={},
        final_result=SimpleNamespace(candidates=[]),
    )

    store.refresh_runtime_candidate_index_with_artifacts(context=context, artifacts=artifacts)

    items = store.list_candidate_review_items(user=user, session_id=session.session_id)
    assert items is not None
    assert [item.display_name for item in items] == ["resume-a"]
    assert items[0].source_round == 1
    assert items[0].evidence[0].source_kind == "cts"
    final = store.list_runtime_final_top_review_items(user=user, session_id=session.session_id)
    assert final is None


def test_runtime_final_candidate_evidence_facts_update_on_repeated_finalization(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_source_session(store, source_kinds=["cts"])
    first_job = store.start_runtime_sourcing_job(user=user, session_id=session.session_id, idempotency_key="first")
    assert first_job is not None
    first_context = store.claim_next_runtime_sourcing_job(
        owner_id="test-owner",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    assert first_context is not None
    source_run = first_context.session.source_runs[0]
    store.complete_runtime_sourcing_job_with_artifacts(
        context=first_context,
        artifacts=_single_final_candidate_artifacts(
            label="A",
            runtime_run_id="run-final-a",
            source_run_id=source_run.source_run_id,
        ),
    )
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE source_runs SET status = 'queued' WHERE source_run_id = ?", (source_run.source_run_id,))

    second_job = store.start_runtime_sourcing_job(user=user, session_id=session.session_id, idempotency_key="second")
    assert second_job is not None
    second_context = store.claim_next_runtime_sourcing_job(
        owner_id="test-owner",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    assert second_context is not None
    store.complete_runtime_sourcing_job_with_artifacts(
        context=second_context,
        artifacts=_single_final_candidate_artifacts(
            label="B",
            runtime_run_id="run-final-b",
            source_run_id=source_run.source_run_id,
        ),
    )

    final = store.list_runtime_final_top_review_items(user=user, session_id=session.session_id)
    assert final is not None
    revision, items = final
    assert revision == 2
    assert len(items) == 1
    item = items[0]
    assert item.summary == "summary-B"
    assert item.why_selected == "why-B"
    assert item.matched_must_haves == ["must-B"]
    assert item.matched_preferences == ["preference-B"]
    assert item.missing_risks == ["risk-B"]
    assert item.strengths == ["strength-B"]
    assert item.weaknesses == ["weakness-B"]


def test_runtime_completion_projects_source_lane_state_and_finalization_revision(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_dual_source_session(store)
    job = store.start_runtime_sourcing_job(user=user, session_id=session.session_id, idempotency_key="state")
    assert job is not None
    context = store.claim_next_runtime_sourcing_job(
        owner_id="test-owner",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    assert context is not None
    source_run_by_kind = {source_run.source_kind: source_run for source_run in context.session.source_runs}
    runtime_run_id = "run-state-1"
    run_state = SimpleNamespace(
        top_pool_ids=["resume-a"],
        candidate_identity_by_resume_id={"resume-a": "identity-a"},
        canonical_resume_by_identity_id={"identity-a": SimpleNamespace(canonical_resume_id="resume-a")},
        candidate_identities={"identity-a": SimpleNamespace(resume_ids=("resume-a",))},
        source_evidence_by_identity_id={
            "identity-a": [
                _source_evidence(
                    evidence_id="evidence-cts-a",
                    source="cts",
                    source_run_id=source_run_by_kind["cts"].source_run_id,
                    resume_id="resume-a",
                )
            ]
        },
        source_coverage_summary=SimpleNamespace(
            to_public_payload=lambda: {
                "status": "complete",
                "selected_source_kinds": ["cts", "liepin"],
                "completed_source_kinds": ["cts", "liepin"],
            }
        ),
        runtime_source_lane_results=[
            _source_lane_result(runtime_run_id=runtime_run_id, source="cts", candidate_count=1),
            _source_lane_result(runtime_run_id=runtime_run_id, source="liepin", candidate_count=0),
        ],
    )
    artifacts = SimpleNamespace(
        run_id=runtime_run_id,
        run_state=run_state,
        candidate_store={"resume-a": _runtime_candidate("resume-a")},
        normalized_store={},
        final_result=SimpleNamespace(candidates=[SimpleNamespace(resume_id="resume-a", final_score=90)]),
    )

    store.complete_runtime_sourcing_job_with_artifacts(context=context, artifacts=artifacts)

    states = store.list_runtime_source_lane_latest_state(user=user, session_id=session.session_id)
    state_by_source = {state.source_kind: state for state in states}
    assert set(state_by_source) == {"cts", "liepin"}
    assert state_by_source["cts"].payload["source_coverage_summary"]["status"] == "complete"
    assert state_by_source["cts"].payload["finalization_revision"]["revision"] == 1
    assert state_by_source["cts"].payload["safe_counts"]["candidates"] == 1


def test_runtime_completion_projects_source_lane_state_when_final_top_is_empty(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_dual_source_session(store)
    job = store.start_runtime_sourcing_job(user=user, session_id=session.session_id, idempotency_key="empty-state")
    assert job is not None
    context = store.claim_next_runtime_sourcing_job(
        owner_id="test-owner",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    assert context is not None
    runtime_run_id = "run-empty-state-1"
    run_state = SimpleNamespace(
        top_pool_ids=[],
        candidate_identity_by_resume_id={},
        canonical_resume_by_identity_id={},
        candidate_identities={},
        source_evidence_by_identity_id={},
        source_coverage_summary=SimpleNamespace(to_public_payload=lambda: {"status": "empty"}),
        runtime_source_lane_results=[
            _source_lane_result(runtime_run_id=runtime_run_id, source="cts", candidate_count=0),
        ],
    )
    artifacts = SimpleNamespace(
        run_id=runtime_run_id,
        run_state=run_state,
        candidate_store={},
        normalized_store={},
        final_result=SimpleNamespace(candidates=[]),
    )

    store.complete_runtime_sourcing_job_with_artifacts(context=context, artifacts=artifacts)

    states = store.list_runtime_source_lane_latest_state(user=user, session_id=session.session_id)
    assert [state.source_kind for state in states] == ["cts"]
    assert states[0].payload["source_coverage_summary"]["status"] == "empty"


def test_runtime_completion_auto_creates_liepin_detail_open_requests(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_dual_source_session(store)
    connection, _created = store.get_or_create_liepin_source_connection(user=user)
    connected = store.mark_liepin_connection_connected(
        user=user,
        connection_id=connection.connection_id,
        provider_account_hash="acct_hash_123",
        compliance_gate_ref="gate-runtime-1",
    )
    assert connected is not None
    job = store.start_runtime_sourcing_job(user=user, session_id=session.session_id, idempotency_key="detail")
    assert job is not None
    context = store.claim_next_runtime_sourcing_job(
        owner_id="test-owner",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    assert context is not None
    source_run_by_kind = {source_run.source_kind: source_run for source_run in context.session.source_runs}
    runtime_run_id = "run-detail-1"
    recommendation = RuntimeDetailRecommendation(
        recommendation_id="rec-liepin-a",
        source="liepin",
        source_evidence_id="evidence-liepin-a",
        candidate_resume_id="resume-a",
        provider_candidate_key_hash="hash-liepin-resume-a",
        value_score=87,
        provider_rank=1,
        card_policy_rank=1,
        hard_filter_status="hard_filter_passed",
        budget_reason_code="within_run_detail_budget",
        safe_reason_codes=("matched_card_terms",),
    )
    run_state = SimpleNamespace(
        top_pool_ids=["resume-a"],
        candidate_identity_by_resume_id={"resume-a": "identity-a"},
        canonical_resume_by_identity_id={"identity-a": SimpleNamespace(canonical_resume_id="resume-a")},
        candidate_identities={"identity-a": SimpleNamespace(resume_ids=("resume-a",))},
        source_evidence_by_identity_id={
            "identity-a": [
                _source_evidence(
                    evidence_id="evidence-liepin-a",
                    source="liepin",
                    source_run_id=source_run_by_kind["liepin"].source_run_id,
                    resume_id="resume-a",
                )
            ]
        },
        source_coverage_summary=SimpleNamespace(to_public_payload=lambda: {"status": "complete"}),
        runtime_source_lane_results=[
            _source_lane_result(
                runtime_run_id=runtime_run_id,
                source="liepin",
                candidate_count=1,
                detail_recommendations=(recommendation,),
            )
        ],
    )
    artifacts = SimpleNamespace(
        run_id=runtime_run_id,
        run_state=run_state,
        candidate_store={"resume-a": _runtime_candidate("resume-a")},
        normalized_store={},
        final_result=SimpleNamespace(candidates=[SimpleNamespace(resume_id="resume-a", final_score=90)]),
    )

    store.complete_runtime_sourcing_job_with_artifacts(context=context, artifacts=artifacts)

    requests = store.list_liepin_detail_open_requests(user=user, session_id=session.session_id, status="pending")
    assert len(requests) == 1
    assert "Agent recommends opening detail" in requests[0].decision_note
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        request_row = conn.execute(
            """
            SELECT candidate_evidence_id, provider_candidate_key_hash
            FROM detail_open_requests
            WHERE request_id = ?
            """,
            (requests[0].request_id,),
        ).fetchone()
    assert request_row["candidate_evidence_id"] == "evidence-liepin-a"
    assert request_row["provider_candidate_key_hash"] == "hash-liepin-resume-a"
    events = store.list_session_workbench_events(user=user, session_id=session.session_id, after_seq=0)
    assert "runtime_detail_recommended" in {event.event_name for event in events}
    assert "liepin_detail_open_auto_recommended" in {event.event_name for event in events}


def test_runtime_completion_creates_liepin_detail_requests_for_recommended_cards_outside_final_top10(
    tmp_path: Path,
) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_dual_source_session(store)
    connection, _created = store.get_or_create_liepin_source_connection(user=user)
    connected = store.mark_liepin_connection_connected(
        user=user,
        connection_id=connection.connection_id,
        provider_account_hash="acct_hash_123",
        compliance_gate_ref="gate-runtime-1",
    )
    assert connected is not None
    job = store.start_runtime_sourcing_job(user=user, session_id=session.session_id, idempotency_key="detail-card")
    assert job is not None
    context = store.claim_next_runtime_sourcing_job(
        owner_id="test-owner",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    assert context is not None
    source_run_by_kind = {source_run.source_kind: source_run for source_run in context.session.source_runs}
    runtime_run_id = "run-detail-card"
    recommendation = RuntimeDetailRecommendation(
        recommendation_id="rec-liepin-card-a",
        source="liepin",
        source_evidence_id="evidence-liepin-card-a",
        candidate_resume_id="liepin-card-a",
        provider_candidate_key_hash="hash-liepin-card-a",
        value_score=87,
        provider_rank=1,
        card_policy_rank=1,
        hard_filter_status="hard_filter_passed",
        budget_reason_code="within_run_detail_budget",
        safe_reason_codes=("matched_card_terms",),
    )
    cts_evidence = _source_evidence(
        evidence_id="evidence-cts-a",
        source="cts",
        source_run_id=source_run_by_kind["cts"].source_run_id,
        resume_id="resume-cts-a",
    )
    liepin_evidence = _source_evidence(
        evidence_id="evidence-liepin-card-a",
        source="liepin",
        source_run_id=source_run_by_kind["liepin"].source_run_id,
        resume_id="liepin-card-a",
        source_lane_run_id=f"{runtime_run_id}:source:liepin:round:2:lane:1",
    )
    run_state = SimpleNamespace(
        top_pool_ids=["resume-cts-a"],
        candidate_identity_by_resume_id={
            "resume-cts-a": "identity-cts-a",
            "liepin-card-a": "identity-liepin-a",
        },
        canonical_resume_by_identity_id={"identity-cts-a": SimpleNamespace(canonical_resume_id="resume-cts-a")},
        candidate_identities={
            "identity-cts-a": SimpleNamespace(resume_ids=("resume-cts-a",)),
            "identity-liepin-a": SimpleNamespace(resume_ids=("liepin-card-a",)),
        },
        source_evidence_by_identity_id={
            "identity-cts-a": [cts_evidence],
            "identity-liepin-a": [liepin_evidence],
        },
        source_coverage_summary=SimpleNamespace(to_public_payload=lambda: {"status": "complete"}),
        runtime_source_lane_results=[
            _source_lane_result(
                runtime_run_id=runtime_run_id,
                source="liepin",
                candidate_count=1,
                detail_recommendations=(recommendation,),
            )
        ],
    )
    artifacts = SimpleNamespace(
        run_id=runtime_run_id,
        run_state=run_state,
        candidate_store={
            "resume-cts-a": _runtime_candidate("resume-cts-a"),
            "liepin-card-a": _runtime_candidate(
                "liepin-card-a",
                source_resume_id="provider-liepin-card-a",
                source_round=None,
            ),
        },
        normalized_store={},
        final_result=SimpleNamespace(candidates=[SimpleNamespace(resume_id="resume-cts-a", final_score=90)]),
    )

    store.complete_runtime_sourcing_job_with_artifacts(context=context, artifacts=artifacts)

    runtime_final = store.list_runtime_final_top_review_items(user=user, session_id=session.session_id)
    assert runtime_final is not None
    _revision, final_items = runtime_final
    assert len(final_items) == 1
    assert {badge for item in final_items for badge in item.source_badges} == {"CTS"}
    requests = store.list_liepin_detail_open_requests(user=user, session_id=session.session_id, status="pending")
    assert len(requests) == 1
    assert requests[0].candidate is not None
    assert requests[0].candidate.display_name == "liepin-card-a"
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        request_row = conn.execute(
            """
            SELECT candidate_evidence_id, provider_candidate_key_hash, detail_candidates_json
            FROM detail_open_requests
            WHERE request_id = ?
            """,
            (requests[0].request_id,),
        ).fetchone()
        evidence_row = conn.execute(
            """
            SELECT ce.source_kind, ce.evidence_level, ce.runtime_identity_id, cri.source_round
            FROM candidate_evidence ce
            JOIN candidate_review_items cri ON cri.review_item_id = ce.review_item_id
            WHERE ce.evidence_id = ?
            """,
            ("evidence-liepin-card-a",),
        ).fetchone()
    assert request_row["candidate_evidence_id"] == "evidence-liepin-card-a"
    assert request_row["provider_candidate_key_hash"] == "hash-liepin-card-a"
    assert '"candidate_id":"liepin-card-a"' in request_row["detail_candidates_json"]
    assert evidence_row["source_kind"] == "liepin"
    assert evidence_row["evidence_level"] == "card"
    assert evidence_row["runtime_identity_id"] == "identity-liepin-a"
    assert evidence_row["source_round"] == 2


def test_leased_liepin_detail_open_intent_executes_detail_lane_and_persists_detail_evidence(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_dual_source_session(store)
    connection, _created = store.get_or_create_liepin_source_connection(user=user)
    connected = store.mark_liepin_connection_connected(
        user=user,
        connection_id=connection.connection_id,
        provider_account_hash="acct_hash_123",
        compliance_gate_ref="gate-runtime-1",
    )
    assert connected is not None
    policy = store.update_liepin_source_run_policy(
        user=user,
        session_id=session.session_id,
        detail_open_mode="bypass_confirm",
    )
    assert policy is not None
    job = store.start_runtime_sourcing_job(user=user, session_id=session.session_id, idempotency_key="detail-execute")
    assert job is not None
    context = store.claim_next_runtime_sourcing_job(
        owner_id="test-owner",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    assert context is not None
    source_run_by_kind = {source_run.source_kind: source_run for source_run in context.session.source_runs}
    runtime_run_id = "run-detail-1"
    recommendation = RuntimeDetailRecommendation(
        recommendation_id="rec-liepin-a",
        source="liepin",
        source_evidence_id="evidence-liepin-a",
        candidate_resume_id="provider-candidate-1",
        provider_candidate_key_hash="hash-liepin-provider-candidate-1",
        value_score=87,
        provider_rank=1,
        card_policy_rank=1,
        hard_filter_status="hard_filter_passed",
        budget_reason_code="within_run_detail_budget",
        safe_reason_codes=("matched_card_terms",),
    )
    run_state = SimpleNamespace(
        top_pool_ids=["provider-candidate-1"],
        candidate_identity_by_resume_id={"provider-candidate-1": "identity-a"},
        canonical_resume_by_identity_id={"identity-a": SimpleNamespace(canonical_resume_id="provider-candidate-1")},
        candidate_identities={"identity-a": SimpleNamespace(resume_ids=("provider-candidate-1",))},
        source_evidence_by_identity_id={
            "identity-a": [
                _source_evidence(
                    evidence_id="evidence-liepin-a",
                    source="liepin",
                    source_run_id=source_run_by_kind["liepin"].source_run_id,
                    resume_id="provider-candidate-1",
                )
            ]
        },
        source_coverage_summary=SimpleNamespace(to_public_payload=lambda: {"status": "complete"}),
        runtime_source_lane_results=[
            _source_lane_result(
                runtime_run_id=runtime_run_id,
                source="liepin",
                candidate_count=1,
                detail_recommendations=(recommendation,),
            )
        ],
    )
    artifacts = SimpleNamespace(
        run_id=runtime_run_id,
        run_state=run_state,
        candidate_store={"provider-candidate-1": _runtime_candidate("provider-candidate-1")},
        normalized_store={},
        final_result=SimpleNamespace(candidates=[SimpleNamespace(resume_id="provider-candidate-1", final_score=90)]),
    )
    store.complete_runtime_sourcing_job_with_artifacts(context=context, artifacts=artifacts)

    detail_context = store.claim_next_liepin_detail_open_intent()
    assert detail_context is not None
    fake_runtime = FakeDetailRuntime(calls=[])

    run_liepin_detail_open_intent(
        context=detail_context,
        store=store,
        settings=_settings(tmp_path),
        runtime_factory=lambda settings: fake_runtime,
    )

    assert len(fake_runtime.calls) == 1
    detail_request = fake_runtime.calls[0]["request"]
    assert detail_request.lane_mode == "detail"
    assert detail_request.requirement_sheet.job_title == session.job_title
    assert list(detail_request.requirement_sheet.must_have_capabilities) == ["Python"]
    assert detail_request.approved_detail_lease is not None
    assert '"candidate_id":"provider-candidate-1"' in detail_request.approved_detail_lease.detail_candidates_json
    requests = store.list_liepin_detail_open_requests(user=user, session_id=session.session_id)
    assert requests[0].ledger is not None
    assert requests[0].ledger.status == "opened"
    items = store.list_candidate_review_items(user=user, session_id=session.session_id)
    assert items is not None
    assert any(evidence.evidence_level == "detail" for item in items for evidence in item.evidence)
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        intent = conn.execute("SELECT status, resolved_external_ref FROM external_write_intents").fetchone()
    assert intent["status"] == "succeeded"
    assert intent["resolved_external_ref"] == "evidence-liepin-detail-a"


def test_runtime_sourcing_job_can_retry_after_failed_attempt(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_dual_source_session(store)
    first = store.start_runtime_sourcing_job(
        user=user,
        session_id=session.session_id,
        idempotency_key="workbench-primary-runtime-sourcing",
    )
    assert first is not None
    first_job, _ = first
    context = store.claim_next_runtime_sourcing_job(
        owner_id="test-owner",
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )
    assert context is not None
    store.fail_runtime_sourcing_job(context=context, error_message="provider exploded")

    second = store.start_runtime_sourcing_job(
        user=user,
        session_id=session.session_id,
        idempotency_key="workbench-primary-runtime-sourcing",
    )

    assert second is not None
    second_job, was_created = second
    assert was_created is True
    assert second_job.status == "queued"
    assert second_job.job_id != first_job.job_id


def test_expired_attached_runtime_sourcing_job_is_reconciled(tmp_path: Path) -> None:
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_dual_source_session(store)
    job = store.start_runtime_sourcing_job(user=user, session_id=session.session_id, idempotency_key="lease")
    assert job is not None
    context = store.claim_next_runtime_sourcing_job(
        owner_id="test-owner",
        lease_expires_at="2000-01-01T00:00:00+00:00",
    )
    assert context is not None
    store.attach_runtime_sourcing_job_runtime_run_id(context=context, runtime_run_id="run-attached-stale")

    reconciled = store.reconcile_expired_runtime_sourcing_jobs()

    assert reconciled == 1
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status, error_message FROM runtime_sourcing_jobs WHERE job_id = ?", (job[0].job_id,)).fetchone()
    assert row["status"] == "failed"
    assert "lease expired" in row["error_message"]
