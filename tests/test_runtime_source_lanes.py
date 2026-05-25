from __future__ import annotations

import asyncio
import json

import pytest

from seektalent.core.retrieval.provider_contract import ProviderSnapshot, SearchRequest, SearchResult
from seektalent.core.retrieval.service import RetrievalService
from seektalent.models import (
    FinalResult,
    InputTruth,
    CTSQuery,
    RequirementSheet,
    ResumeCandidate,
    RetrievalState,
    RuntimeFinalizationRevision,
    RuntimeIdentitySignals,
    RunState,
    RuntimeSourceCoverageSummary,
    RuntimeSourceEvidence,
    ScoredCandidate,
    ScoringPolicy,
    NormalizedResume,
)
from seektalent.runtime.source_lanes import (
    RuntimeApprovedDetailLease,
    RuntimeDetailRecommendation,
    RuntimeDetailEnrichmentResult,
    RuntimeSourceBudgetPolicy,
    RuntimeSourceLaneEvent,
    RuntimeSourceLanePlan,
    RuntimeSourceLaneRequest,
    RuntimeSourceLaneResult,
    apply_source_lane_result,
    build_runtime_source_plan,
    clone_run_state_for_source_lane,
    normalize_source_kinds,
)
from seektalent.runtime.orchestrator import RunArtifacts, WorkflowRuntime
from seektalent.runtime.source_round_dispatch import SourceRoundAdapterResult, SourceRoundDispatchResult
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


def _normalized_resume(
    resume_id: str,
    *,
    candidate_name: str = "王明",
    current_company: str = "海光集成电路",
    current_title: str = "高级主管工程师",
    completeness_score: int = 80,
    score_evidence_source: str = "card",
) -> NormalizedResume:
    return NormalizedResume(
        resume_id=resume_id,
        dedup_key=resume_id,
        candidate_name=candidate_name,
        headline=current_title,
        current_title=current_title,
        current_company=current_company,
        education_summary="南京邮电大学 硕士",
        completeness_score=completeness_score,
        score_evidence_source=score_evidence_source,
    )


def _run_state() -> RunState:
    requirement_sheet = RequirementSheet(
        job_title="Data Engineer",
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
            job_title=requirement_sheet.job_title,
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


def _requirement_sheet() -> RequirementSheet:
    return _run_state().requirement_sheet


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


def test_opencli_safe_reason_code_maps_to_business_safe_runtime_public_payload() -> None:
    event = RuntimeSourceLaneEvent(
        schema_version="runtime_source_lane_event_v1",
        runtime_run_id="run-1",
        source_plan_id="plan-1",
        source_lane_run_id="lane-1",
        source="liepin",
        attempt=1,
        event_seq=1,
        event_type="source_lane_blocked",
        status="blocked",
        safe_reason_code="liepin_opencli_extension_disconnected",
    )

    payload = event.to_public_payload()

    assert payload["safe_reason_code"] == "source_browser_backend_unavailable"


def test_apply_source_lane_result_populates_identity_store_and_canonical_selection() -> None:
    run_state = _run_state()
    cts_result = RuntimeSourceLaneResult(
        runtime_run_id="run-1",
        source_plan_id="plan-cts",
        source_lane_run_id="lane-cts",
        source="cts",
        lane_mode="card",
        attempt=1,
        status="completed",
        candidate_store_updates={"resume-cts": _candidate("resume-cts")},
        normalized_store_updates={"resume-cts": _normalized_resume("resume-cts", score_evidence_source="card")},
        source_evidence_updates=(
            RuntimeSourceEvidence(
                evidence_id="evidence-cts",
                source="cts",
                provider="cts",
                evidence_level="card",
                candidate_resume_id="resume-cts",
                provider_candidate_key_hash="same-person-hash",
                collected_at="2026-05-14T00:00:00Z",
                safe_reason_codes=("source_card_candidate",),
            ),
        ),
    )
    liepin_detail_result = RuntimeSourceLaneResult(
        runtime_run_id="run-1",
        source_plan_id="plan-liepin",
        source_lane_run_id="lane-liepin",
        source="liepin",
        lane_mode="detail",
        attempt=1,
        status="completed",
        candidate_store_updates={"resume-liepin": _candidate("resume-liepin")},
        normalized_store_updates={
            "resume-liepin": _normalized_resume(
                "resume-liepin",
                completeness_score=95,
                score_evidence_source="detail",
            )
        },
        source_evidence_updates=(
            RuntimeSourceEvidence(
                evidence_id="evidence-liepin-detail",
                source="liepin",
                provider="liepin",
                evidence_level="detail",
                candidate_resume_id="resume-liepin",
                provider_candidate_key_hash="same-person-hash",
                collected_at="2026-05-15T00:00:00Z",
                safe_reason_codes=("source_detail_candidate",),
            ),
        ),
    )

    apply_source_lane_result(run_state=run_state, result=cts_result, source_order={"cts": 0, "liepin": 1})
    apply_source_lane_result(run_state=run_state, result=liepin_detail_result, source_order={"cts": 0, "liepin": 1})

    assert len(run_state.candidate_identities) == 1
    identity_id = run_state.candidate_identity_by_resume_id["resume-cts"]
    assert run_state.candidate_identity_by_resume_id["resume-liepin"] == identity_id
    assert [item.evidence_id for item in run_state.source_evidence_by_identity_id[identity_id]] == [
        "evidence-cts",
        "evidence-liepin-detail",
    ]
    assert run_state.canonical_resume_by_identity_id[identity_id].canonical_resume_id == "resume-liepin"


def test_apply_source_lane_result_does_not_merge_masked_liepin_card_into_visible_cts_candidate() -> None:
    run_state = _run_state()
    visible = RuntimeSourceLaneResult(
        runtime_run_id="run-1",
        source_plan_id="plan-cts",
        source_lane_run_id="lane-cts",
        source="cts",
        lane_mode="card",
        attempt=1,
        status="completed",
        candidate_store_updates={"resume-visible": _candidate("resume-visible")},
        normalized_store_updates={"resume-visible": _normalized_resume("resume-visible", candidate_name="王明")},
        source_evidence_updates=(
            RuntimeSourceEvidence(
                evidence_id="evidence-visible",
                source="cts",
                provider="cts",
                evidence_level="card",
                candidate_resume_id="resume-visible",
                provider_candidate_key_hash="cts-provider-hash",
                collected_at="2026-05-15T00:00:00Z",
            ),
        ),
    )
    masked = RuntimeSourceLaneResult(
        runtime_run_id="run-1",
        source_plan_id="plan-liepin",
        source_lane_run_id="lane-liepin",
        source="liepin",
        lane_mode="card",
        attempt=1,
        status="completed",
        candidate_store_updates={"resume-masked": _candidate("resume-masked")},
        normalized_store_updates={"resume-masked": _normalized_resume("resume-masked", candidate_name="王**")},
        source_evidence_updates=(
            RuntimeSourceEvidence(
                evidence_id="evidence-masked",
                source="liepin",
                provider="liepin",
                evidence_level="card",
                candidate_resume_id="resume-masked",
                provider_candidate_key_hash="liepin-provider-hash",
                collected_at="2026-05-15T00:00:00Z",
            ),
        ),
    )

    apply_source_lane_result(run_state=run_state, result=visible, source_order={"cts": 0, "liepin": 1})
    apply_source_lane_result(run_state=run_state, result=masked, source_order={"cts": 0, "liepin": 1})

    assert len(run_state.candidate_identities) == 2
    assert run_state.candidate_identity_by_resume_id["resume-visible"] != run_state.candidate_identity_by_resume_id[
        "resume-masked"
    ]


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
        backend_mode="worker_compat",
        safe_posture={
            "connection_state": "connected",
            "approval_secret": "secret-value",
            "cookie": "sid=secret",
        },
    )

    payload = plan.to_public_payload()

    assert payload["source"] == "liepin"
    assert payload["backend_mode"] == "worker_compat"
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
    assert payload["safe_reason_code"] == "unknown_reason"
    assert payload["artifact_refs"] == ["artifact://safe"]


def test_event_public_payload_allowlists_safe_count_keys() -> None:
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
        safe_counts={
            "cards_seen": 4,
            "details_opened": 2,
            "raw_resume_SECRET": 1,
            "cards_filtered": -1,
        },
    )

    payload = event.to_public_payload()

    assert payload["safe_counts"] == {"cards_seen": 4, "details_opened": 2}
    assert "raw_resume_SECRET" not in repr(payload)


def test_runtime_contract_schema_freeze_for_identity_coverage_and_revision_models() -> None:
    signals = RuntimeIdentitySignals(
        normalized_name="wang ming",
        is_masked_name=False,
        current_company_norm="hygon",
        current_title_norm="senior engineer",
        school_norms=("njupt",),
        work_chronology_fingerprints=("hygon:2023-10:present",),
        provider_candidate_key_hash="hash-provider",
        protected_contact_hashes=("contact-hash",),
    )
    coverage = RuntimeSourceCoverageSummary(
        status="degraded",
        selected_source_kinds=("cts", "liepin"),
        completed_source_kinds=("cts",),
        failed_source_kinds=("liepin",),
    )
    revision = RuntimeFinalizationRevision(
        revision=2,
        runtime_run_id="run-1",
        reason_code="detail_enrichment_applied",
        selected_source_kinds=("cts", "liepin"),
        candidate_identity_ids=("identity-1",),
    )

    assert signals.to_public_payload()["protected_contact_hash_count"] == 1
    assert coverage.to_public_payload()["failed_source_kinds"] == ["liepin"]
    assert revision.to_public_payload()["revision"] == 2
    assert "contact-hash" not in repr(signals.to_public_payload())


def test_public_reason_codes_and_artifact_refs_are_allowlisted() -> None:
    event = RuntimeSourceLaneEvent(
        schema_version="runtime_source_lane_event_v1",
        runtime_run_id="run-1",
        source_plan_id="plan-1",
        source_lane_run_id="lane-1",
        source="liepin",
        attempt=1,
        event_seq=1,
        event_type="source_lane_failed",
        status="failed",
        safe_reason_code="raw_resume copied token=secret-token",
        artifact_refs=("artifact://public-summary/1", "file:///tmp/raw.html", "https://example.com/raw"),
    )

    payload = event.to_public_payload()

    assert payload["safe_reason_code"] == "unknown_reason"
    assert payload["artifact_refs"] == ["artifact://public-summary/1"]
    assert "secret-token" not in repr(payload)
    assert "raw.html" not in repr(payload)


def test_source_evidence_public_payload_is_first_class_and_hash_only() -> None:
    evidence = RuntimeSourceEvidence(
        evidence_id="evidence-1",
        source="liepin",
        provider="liepin",
        source_plan_id="plan-liepin",
        source_lane_run_id="lane-liepin",
        evidence_level="card",
        candidate_resume_id="resume-1",
        provider_candidate_key_hash="hash-liepin-evidence-1",
        protected_contact_hashes=("contact-hash",),
        provider_rank=3,
        query_fingerprint="query-1",
        provider_snapshot_ref="artifact://protected-snapshot/1",
        safe_summary_ref="artifact://public-summary/1",
        protected_artifact_ref="artifact://protected-snapshot/1",
        collected_at="2026-05-15T00:00:00Z",
        safe_reason_codes=("source_card_candidate",),
    )

    payload = evidence.to_public_payload()

    assert payload["provider_candidate_key_hash"] == "hash-liepin-evidence-1"
    assert payload["source_plan_id"] == "plan-liepin"
    assert payload["source_lane_run_id"] == "lane-liepin"
    assert payload["protected_contact_hash_count"] == 1
    assert payload["provider_rank"] == 3
    assert payload["safe_reason_codes"] == ["source_card_candidate"]
    assert "provider_candidate_key" not in payload
    assert "raw_resume" not in payload
    assert "contact-hash" not in repr(payload)


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
        provider_rank=2,
        card_policy_rank=1,
        hard_filter_status="hard_filter_passed",
        budget_reason_code="within_run_detail_budget",
        reason_code="high_value_card",
        safe_reason_codes=("matched_card_terms",),
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
    assert payload["detail_recommendations"][0]["provider_rank"] == 2
    assert payload["detail_recommendations"][0]["card_policy_rank"] == 1
    assert payload["detail_recommendations"][0]["hard_filter_status"] == "hard_filter_passed"
    assert payload["detail_recommendations"][0]["budget_reason_code"] == "within_run_detail_budget"
    assert payload["detail_recommendations"][0]["safe_reason_codes"] == ["matched_card_terms"]
    assert "safe_reason" not in payload["detail_recommendations"][0]
    assert "events" not in payload["detail_recommendations"][0]


def test_card_policy_reason_codes_are_public_allowlisted() -> None:
    recommendation = RuntimeDetailRecommendation(
        recommendation_id="rec-card-policy",
        source="liepin",
        source_evidence_id="evidence-1",
        candidate_resume_id="resume-1",
        provider_candidate_key_hash="hash-1",
        hard_filter_status="obvious_role_mismatch",
        budget_reason_code="insufficient_card_signal",
        reason_code="must_have_zero_overlap",
        safe_reason_codes=("hard_location_mismatch", "hard_education_mismatch"),
    )

    payload = recommendation.to_public_payload()

    assert payload["hard_filter_status"] == "obvious_role_mismatch"
    assert payload["budget_reason_code"] == "insufficient_card_signal"
    assert payload["reason_code"] == "must_have_zero_overlap"
    assert payload["safe_reason_codes"] == ["hard_location_mismatch", "hard_education_mismatch"]


def test_runtime_budget_policy_public_payload_is_count_only() -> None:
    policy = RuntimeSourceBudgetPolicy(
        max_cts_pages=1,
        cts_page_size=10,
        liepin_card_page_size=30,
        liepin_max_cards=30,
        liepin_max_detail_recommendations=6,
        liepin_max_detail_opens_per_run=4,
        policy_version="runtime_source_budget_v1",
    )

    payload = policy.to_public_payload()

    assert payload == {
        "policy_version": "runtime_source_budget_v1",
        "max_cts_pages": 1,
        "cts_page_size": 10,
        "liepin_card_page_size": 30,
        "liepin_max_cards": 30,
        "liepin_max_detail_recommendations": 6,
        "liepin_max_detail_opens_per_run": 4,
    }


def test_approved_detail_lease_binds_source_recommendation_evidence_and_budget() -> None:
    lease = RuntimeApprovedDetailLease(
        lease_ref="lease-ref-1",
        lease_id="lease-1",
        runtime_run_id="run-1",
        source_plan_id="plan-liepin",
        source_lane_run_id="lane-liepin",
        source="liepin",
        recommendation_id="rec-1",
        source_evidence_id="evidence-1",
        candidate_evidence_id="evidence-1",
        candidate_resume_id="resume-1",
        provider_candidate_key_hash="hash-1",
        approved_by_actor_hash="actor-hash",
        approved_at="2026-05-15T00:00:00Z",
        budget_policy_hash="budget-hash",
        lease_signature_ref="artifact://protected-lease/1",
        request_id="request-1",
        ledger_id="ledger-1",
        connection_id="conn-1",
        compliance_gate_ref="gate-1",
        provider_account_hash="acct-hash",
        detail_candidates_json="[]",
        daily_budget=4,
        budget_date="2026-05-15",
        provider_day_key="liepin:2026-05-15",
        timezone="Asia/Shanghai",
        open_policy_version="policy-v1",
        expires_at="2026-05-15T01:00:00Z",
    )

    payload = lease.to_public_payload()

    assert payload["lease_id"] == "lease-1"
    assert payload["runtime_run_id"] == "run-1"
    assert payload["source_evidence_id"] == "evidence-1"
    assert payload["candidate_evidence_id"] == "evidence-1"
    assert payload["candidate_resume_id"] == "resume-1"
    assert payload["budget_policy_hash"] == "budget-hash"
    assert "actor-hash" not in repr(payload)
    assert "acct-hash" not in repr(payload)


def test_detail_enrichment_result_carries_revision_without_mutating_lane_result_contract() -> None:
    lane_result = RuntimeSourceLaneResult(
        runtime_run_id="run-1",
        source_plan_id="plan-liepin",
        source_lane_run_id="lane-detail",
        source="liepin",
        lane_mode="detail",
        attempt=1,
        status="completed",
    )
    revision = RuntimeFinalizationRevision(
        revision=2,
        runtime_run_id="run-1",
        reason_code="detail_enrichment_applied",
        selected_source_kinds=("cts", "liepin"),
        candidate_identity_ids=("identity-1",),
    )
    result = RuntimeDetailEnrichmentResult(
        runtime_run_id="run-1",
        base_finalization_revision=1,
        lane_result=lane_result,
        finalization_revision=revision,
    )

    lane_payload = lane_result.to_public_payload()
    payload = result.to_public_payload()

    assert "finalization_revision" not in lane_payload
    assert payload["finalization_revision"]["revision"] == 2


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


def test_source_lane_result_public_payload_is_retained_for_workbench_projection() -> None:
    run_state = _run_state()
    recommendation = RuntimeDetailRecommendation(
        recommendation_id="rec-1",
        source="liepin",
        source_evidence_id="evidence-1",
        candidate_resume_id="resume-1",
        provider_candidate_key_hash="provider-hash-1",
        safe_reason_codes=("matched_card_terms",),
    )
    result = RuntimeSourceLaneResult(
        runtime_run_id="run-1",
        source_plan_id="plan-liepin",
        source_lane_run_id="lane-liepin",
        source="liepin",
        lane_mode="card",
        attempt=1,
        status="completed",
        candidate_store_updates={"resume-1": _candidate("resume-1")},
        source_evidence_updates=(_evidence("evidence-1", source="liepin", resume_id="resume-1"),),
        detail_recommendations=(recommendation,),
        events=(
            RuntimeSourceLaneEvent(
                schema_version="runtime_source_lane_event_v1",
                runtime_run_id="run-1",
                source_plan_id="plan-liepin",
                source_lane_run_id="lane-liepin",
                source="liepin",
                attempt=1,
                event_seq=1,
                event_type="detail_recommended",
                status="completed",
                safe_counts={"detail_recommendations": 1},
            ),
        ),
    )

    apply_source_lane_result(run_state=run_state, result=result, source_order={"cts": 0, "liepin": 1})

    assert len(run_state.runtime_source_lane_results) == 1
    payload = run_state.runtime_source_lane_results[0]
    assert payload["source"] == "liepin"
    assert payload["detail_recommendations"][0]["source_evidence_id"] == "evidence-1"
    assert payload["events"][0]["event_type"] == "detail_recommended"


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
    search_once_calls: list[CTSQuery] = []

    async def fake_run_rounds(**kwargs):
        del kwargs
        raise AssertionError("CTS source lane must not call the multi-round controller path")

    runtime._run_rounds = fake_run_rounds  # type: ignore[method-assign]

    async def fake_search_once(*, attempt_query, runtime_constraints, round_no, attempt_no, tracer, provider_context=None):
        del runtime_constraints, round_no, attempt_no, tracer, provider_context
        search_once_calls.append(attempt_query)
        return SearchResult(candidates=[_candidate("resume-cts")], raw_candidate_count=1, exhausted=True)

    object.__setattr__(runtime.retrieval_runtime, "search_once", fake_search_once)
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
    assert len(search_once_calls) == 1
    assert search_once_calls[0].page == 1
    assert search_once_calls[0].page_size == 10
    assert result.candidate_store_updates == {"resume-cts": _candidate("resume-cts")}
    assert result.source_evidence_updates[0].candidate_resume_id == "resume-cts"
    assert "resume-cts" not in run_state.candidate_store
    assert run_state.candidate_store == {"resume-existing": _candidate("resume-existing")}


def test_runtime_cts_source_lane_enforces_single_page_at_provider_request_boundary(tmp_path) -> None:
    class ProviderSpy:
        name = "cts-provider-spy"

        def __init__(self) -> None:
            self.requests: list[SearchRequest] = []

        async def search(self, request: SearchRequest, *, round_no: int, trace_id: str) -> SearchResult:
            del round_no, trace_id
            self.requests.append(request)
            return SearchResult(
                candidates=[_candidate(f"resume-{index}") for index in range(12)],
                raw_candidate_count=12,
                exhausted=False,
            )

    settings = make_settings(runs_dir=str(tmp_path / "runs"), liepin_worker_mode="managed_local")
    runtime = WorkflowRuntime(settings)
    provider = ProviderSpy()
    runtime.retrieval_service = RetrievalService(provider=provider)
    tracer = RunTracer(settings.artifacts_path)
    source_plan = build_runtime_source_plan(source_kinds=["cts"], settings=settings, runtime_run_id=tracer.run_id)[0]
    try:
        result = asyncio.run(
            runtime._run_cts_source_lane(
                run_state=_run_state(),
                tracer=tracer,
                source_plan=source_plan,
                progress_callback=None,
            )
        )
    finally:
        tracer.close()

    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.cursor == "1"
    assert request.page_size == 10
    assert request.provider_context == {
        "runtime_source_lane_mode": "cts_single_page",
        "target_new": "10",
        "max_pages": "1",
        "allow_pagination": "false",
    }
    assert len(result.candidate_store_updates) == 10


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
        requirement_sheet=_requirement_sheet(),
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
        requirement_sheet=_requirement_sheet(),
        runtime_run_id="run-liepin",
    )

    result = asyncio.run(runtime.run_source_lane_async(request, liepin_worker_client=worker))

    assert result.status == "blocked"
    assert result.blocked_reason_code == "blocked_approval_missing"
    assert worker.ensure_ready_calls == 0
    assert worker.search_calls == []
    assert worker.open_details_calls == 0


def test_approved_detail_enrichment_creates_new_finalization_revision(tmp_path, monkeypatch) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"))
    runtime = WorkflowRuntime(settings)
    run_state = _run_state()
    first_revision = RuntimeFinalizationRevision(
        revision=1,
        runtime_run_id="run-1",
        reason_code="source_lanes_completed",
        selected_source_kinds=("cts", "liepin"),
        candidate_identity_ids=(),
    )
    run_state.finalization_revisions = [first_revision]
    base = RunArtifacts(
        final_result=FinalResult(
            run_id="run-1",
            run_dir=str(tmp_path / "runs" / "run-1"),
            rounds_executed=1,
            stop_reason="source_lanes_completed",
            candidates=[],
            summary="base",
        ),
        final_markdown="base",
        run_id="run-1",
        run_dir=tmp_path / "runs" / "run-1",
        trace_log_path=tmp_path / "runs" / "run-1" / "trace.jsonl",
        candidate_store=run_state.candidate_store,
        normalized_store=run_state.normalized_store,
        evaluation_result=None,
        terminal_stop_guidance=None,
        finalization_revision=first_revision,
        run_state=run_state,
    )
    lease = RuntimeApprovedDetailLease(
        lease_ref="lease://detail/1",
        lease_id="lease-1",
        runtime_run_id="run-1",
        source_plan_id="plan-liepin",
        source_lane_run_id="lane-liepin-card",
        source="liepin",
        recommendation_id="rec-1",
        source_evidence_id="evidence-card",
        request_id="detail-request-1",
        ledger_id="detail-ledger-1",
        candidate_evidence_id="evidence-card",
        candidate_resume_id="resume-liepin-detail",
        provider_candidate_key_hash="hash-1",
        approved_by_actor_hash="actor-hash",
        approved_at="2026-05-15T00:00:00Z",
        budget_policy_hash="budget-hash",
        lease_signature_ref="artifact://protected-lease/1",
        connection_id="conn-1",
        compliance_gate_ref="gate-1",
        provider_account_hash="acct_hash_123",
        detail_candidates_json="[]",
        daily_budget=3,
        budget_date="2026-05-15",
        provider_day_key="liepin:acct_hash_123:2026-05-15",
        timezone="Asia/Shanghai",
        open_policy_version="detail-policy-v1",
    )
    detail_request = RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="detail",
        job_title="Backend Engineer",
        jd="FastAPI retrieval",
        notes=None,
        requirement_sheet=_requirement_sheet(),
        runtime_run_id="run-1",
        source_plan_id="plan-liepin",
        source_lane_run_id="lane-liepin-detail",
        approved_detail_lease=lease,
    )

    async def fake_run_source_lane_async(request, *, liepin_worker_client=None):
        del request, liepin_worker_client
        return RuntimeSourceLaneResult(
            runtime_run_id="run-1",
            source_plan_id="plan-liepin",
            source_lane_run_id="lane-liepin-detail",
            source="liepin",
            lane_mode="detail",
            attempt=1,
            status="completed",
            candidate_store_updates={"resume-liepin-detail": _candidate("resume-liepin-detail")},
            normalized_store_updates={
                "resume-liepin-detail": _normalized_resume(
                    "resume-liepin-detail",
                    completeness_score=95,
                    score_evidence_source="detail",
                )
            },
            source_evidence_updates=(
                RuntimeSourceEvidence(
                    evidence_id="evidence-detail",
                    source="liepin",
                    provider="liepin",
                    evidence_level="detail",
                    candidate_resume_id="resume-liepin-detail",
                    provider_candidate_key_hash="hash-1",
                    collected_at="2026-05-15T00:00:00Z",
                ),
            ),
        )

    async def fake_score_round(*, round_no, new_candidates, run_state, tracer, runtime_only_constraints):
        del tracer, runtime_only_constraints
        scored = [_scored_candidate(candidate.resume_id, source_round=round_no) for candidate in new_candidates]
        for item in scored:
            run_state.scorecards_by_resume_id[item.resume_id] = item
        run_state.top_pool_ids = [item.resume_id for item in scored]
        return scored, [], []

    monkeypatch.setattr(runtime, "run_source_lane_async", fake_run_source_lane_async)
    monkeypatch.setattr(runtime, "_score_round", fake_score_round)

    result = asyncio.run(
        runtime.apply_approved_detail_lane_to_run_async(
            base_run_artifacts=base,
            base_finalization_revision=1,
            detail_lane_request=detail_request,
        )
    )

    assert result.lane_result.lane_mode == "detail"
    assert "finalization_revision" not in result.lane_result.to_public_payload()
    assert result.finalization_revision.revision == 2
    assert result.finalization_revision.reason_code == "detail_enrichment_applied"


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


def test_source_round_dispatch_merge_preserves_selected_source_candidates_before_finalization(tmp_path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), liepin_worker_mode="managed_local")
    runtime = WorkflowRuntime(settings)
    run_state = _run_state()
    source_plan = build_runtime_source_plan(
        source_kinds=["cts", "liepin"],
        settings=settings,
        runtime_run_id="run-1",
        liepin_context={"status": "ready"},
    )
    cts_candidate = _candidate("resume-cts")
    liepin_candidate = _candidate("resume-liepin")
    cts_lane = RuntimeSourceLaneResult(
        runtime_run_id="run-1",
        source_plan_id=source_plan[0].source_plan_id,
        source_lane_run_id=f"{source_plan[0].source_plan_id}:round:1:cts",
        source="cts",
        lane_mode="card",
        attempt=1,
        status="completed",
        candidate_store_updates={cts_candidate.resume_id: cts_candidate},
        source_evidence_updates=(_evidence("evidence-cts", source="cts", resume_id=cts_candidate.resume_id),),
    )
    liepin_lane = RuntimeSourceLaneResult(
        runtime_run_id="run-1",
        source_plan_id=source_plan[1].source_plan_id,
        source_lane_run_id=f"{source_plan[1].source_plan_id}:round:1:liepin",
        source="liepin",
        lane_mode="card",
        attempt=1,
        status="completed",
        candidate_store_updates={liepin_candidate.resume_id: liepin_candidate},
        source_evidence_updates=(
            _evidence("evidence-liepin", source="liepin", resume_id=liepin_candidate.resume_id),
        ),
    )
    dispatch_result = SourceRoundDispatchResult(
        source_results=(
            SourceRoundAdapterResult(
                source="cts",
                status="completed",
                candidates=(cts_candidate,),
                raw_candidate_count=1,
                lane_result=cts_lane,
            ),
            SourceRoundAdapterResult(
                source="liepin",
                status="completed",
                candidates=(liepin_candidate,),
                raw_candidate_count=1,
                lane_result=liepin_lane,
            ),
        ),
        candidates=(cts_candidate, liepin_candidate),
        raw_candidate_count=2,
    )

    runtime._merge_source_round_dispatch_result(
        run_state=run_state,
        dispatch_result=dispatch_result,
        source_plan=source_plan,
        round_no=1,
        tracer=RunTracer(settings.artifacts_path),
    )
    for candidate in dispatch_result.candidates:
        run_state.scorecards_by_resume_id[candidate.resume_id] = _scored_candidate(candidate.resume_id, source_round=1)

    identity_top_candidates = runtime._apply_identity_top_pool(run_state)

    assert set(run_state.candidate_store) == {"resume-cts", "resume-liepin"}
    assert {item.source for item in run_state.source_evidence_by_resume_id["resume-cts"]} == {"cts"}
    assert {item.source for item in run_state.source_evidence_by_resume_id["resume-liepin"]} == {"liepin"}
    assert [candidate.resume_id for candidate in identity_top_candidates] == ["resume-cts", "resume-liepin"]
    assert run_state.source_coverage_summary is not None
    assert run_state.source_coverage_summary.status == "complete"


def test_source_round_dispatch_merge_finalizes_top_10_by_identity_not_raw_resume(tmp_path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), liepin_worker_mode="managed_local")
    runtime = WorkflowRuntime(settings)
    run_state = _run_state()
    source_plan = build_runtime_source_plan(
        source_kinds=["cts", "liepin"],
        settings=settings,
        runtime_run_id="run-1",
        liepin_context={"status": "ready"},
    )
    cts_candidate = _candidate("resume-cts")
    liepin_candidate = _candidate("resume-liepin")
    cts_lane = RuntimeSourceLaneResult(
        runtime_run_id="run-1",
        source_plan_id=source_plan[0].source_plan_id,
        source_lane_run_id=f"{source_plan[0].source_plan_id}:round:1:cts",
        source="cts",
        lane_mode="card",
        attempt=1,
        status="completed",
        candidate_store_updates={cts_candidate.resume_id: cts_candidate},
        normalized_store_updates={cts_candidate.resume_id: _normalized_resume(cts_candidate.resume_id)},
        source_evidence_updates=(
            RuntimeSourceEvidence(
                evidence_id="evidence-cts",
                source="cts",
                provider="cts",
                evidence_level="card",
                candidate_resume_id=cts_candidate.resume_id,
                provider_candidate_key_hash="same-person-hash",
                collected_at="2026-05-14T00:00:00Z",
            ),
        ),
    )
    liepin_lane = RuntimeSourceLaneResult(
        runtime_run_id="run-1",
        source_plan_id=source_plan[1].source_plan_id,
        source_lane_run_id=f"{source_plan[1].source_plan_id}:round:1:liepin",
        source="liepin",
        lane_mode="card",
        attempt=1,
        status="completed",
        candidate_store_updates={liepin_candidate.resume_id: liepin_candidate},
        normalized_store_updates={
            liepin_candidate.resume_id: _normalized_resume(
                liepin_candidate.resume_id,
                completeness_score=95,
                score_evidence_source="detail",
            )
        },
        source_evidence_updates=(
            RuntimeSourceEvidence(
                evidence_id="evidence-liepin",
                source="liepin",
                provider="liepin",
                evidence_level="detail",
                candidate_resume_id=liepin_candidate.resume_id,
                provider_candidate_key_hash="same-person-hash",
                collected_at="2026-05-15T00:00:00Z",
            ),
        ),
    )
    dispatch_result = SourceRoundDispatchResult(
        source_results=(
            SourceRoundAdapterResult(
                source="cts",
                status="completed",
                candidates=(cts_candidate,),
                raw_candidate_count=1,
                lane_result=cts_lane,
            ),
            SourceRoundAdapterResult(
                source="liepin",
                status="completed",
                candidates=(liepin_candidate,),
                raw_candidate_count=1,
                lane_result=liepin_lane,
            ),
        ),
        candidates=(cts_candidate, liepin_candidate),
        raw_candidate_count=2,
    )

    runtime._merge_source_round_dispatch_result(
        run_state=run_state,
        dispatch_result=dispatch_result,
        source_plan=source_plan,
        round_no=1,
        tracer=RunTracer(settings.artifacts_path),
    )
    run_state.scorecards_by_resume_id["resume-cts"] = _scored_candidate("resume-cts", source_round=1)
    run_state.scorecards_by_resume_id["resume-liepin"] = _scored_candidate("resume-liepin", source_round=1)

    identity_top_candidates = runtime._apply_identity_top_pool(run_state)

    assert [candidate.resume_id for candidate in identity_top_candidates] == ["resume-liepin"]
    assert run_state.top_pool_ids == ["resume-liepin"]
    assert len(run_state.candidate_identities) == 1
    identity_id = run_state.candidate_identity_by_resume_id["resume-cts"]
    assert run_state.candidate_identity_by_resume_id["resume-liepin"] == identity_id
    assert run_state.canonical_resume_by_identity_id[identity_id].canonical_resume_id == "resume-liepin"
    assert [item.source for item in run_state.source_evidence_by_identity_id[identity_id]] == ["cts", "liepin"]


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


def test_full_source_lanes_enter_cts_and_liepin_provider_calls_concurrently(tmp_path, monkeypatch) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), liepin_worker_mode="managed_local")
    runtime = WorkflowRuntime(settings)
    run_state = _run_state()
    tracer = RunTracer(settings.artifacts_path)
    source_plan = build_runtime_source_plan(
        source_kinds=["cts", "liepin"],
        settings=settings,
        runtime_run_id=tracer.run_id,
    )
    entered: set[str] = set()

    async def await_both(source: str) -> None:
        entered.add(source)
        deadline = asyncio.get_running_loop().time() + 1
        while entered != {"cts", "liepin"}:
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError(f"{source} lane did not run concurrently; entered={entered}")
            await asyncio.sleep(0.01)

    async def fake_cts_lane(**kwargs) -> RuntimeSourceLaneResult:
        source_plan = kwargs["source_plan"]
        await await_both("cts")
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
        await await_both("liepin")
        return RuntimeSourceLaneResult(
            runtime_run_id=request.runtime_run_id or "run-1",
            source_plan_id=request.source_plan_id or "plan-liepin",
            source_lane_run_id=request.source_lane_run_id or "lane-liepin",
            source="liepin",
            lane_mode="card",
            attempt=1,
            status="completed",
            candidate_store_updates={"resume-liepin": _candidate("resume-liepin")},
            source_evidence_updates=(_evidence("evidence-liepin", source="liepin", resume_id="resume-liepin"),),
        )

    async def fake_score_round(*, round_no, new_candidates, run_state, tracer, runtime_only_constraints):
        del tracer, runtime_only_constraints
        scored = [_scored_candidate(candidate.resume_id, source_round=round_no) for candidate in new_candidates]
        for item in scored:
            run_state.scorecards_by_resume_id[item.resume_id] = item
        run_state.top_pool_ids = [item.resume_id for item in scored]
        return scored, [], []

    monkeypatch.setattr(runtime, "_run_cts_source_lane", fake_cts_lane)
    monkeypatch.setattr(runtime, "_run_liepin_source_lane_request", fake_liepin_lane)
    monkeypatch.setattr(runtime, "_score_round", fake_score_round)
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

    assert entered == {"cts", "liepin"}


def test_full_source_lanes_failed_liepin_does_not_cancel_cts_lane(tmp_path, monkeypatch) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), liepin_worker_mode="managed_local")
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
        await asyncio.sleep(0.01)
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
        del request, liepin_worker_client
        raise RuntimeError("provider raw resume phone 13800138000")

    async def fake_score_round(*, round_no, new_candidates, run_state, tracer, runtime_only_constraints):
        del tracer, runtime_only_constraints
        scored = [_scored_candidate(candidate.resume_id, source_round=round_no) for candidate in new_candidates]
        for item in scored:
            run_state.scorecards_by_resume_id[item.resume_id] = item
        run_state.top_pool_ids = [item.resume_id for item in scored]
        return scored, [], []

    monkeypatch.setattr(runtime, "_run_cts_source_lane", fake_cts_lane)
    monkeypatch.setattr(runtime, "_run_liepin_source_lane_request", fake_liepin_lane)
    monkeypatch.setattr(runtime, "_score_round", fake_score_round)
    try:
        top_scored, stop_reason, _, _ = asyncio.run(
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

    assert [item.resume_id for item in top_scored] == ["resume-cts"]
    assert stop_reason == "source_lanes_degraded"
    payload_text = sorted(tracer.run_dir.glob("runtime.source_lane.liepin.card.1"))[-1].read_text(encoding="utf-8")
    assert "failed_provider_error" in payload_text
    assert "13800138000" not in payload_text


def test_full_source_lanes_records_structured_degraded_coverage_without_missing_source_alias(tmp_path, monkeypatch) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), liepin_worker_mode="managed_local")
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
            candidate_store_updates={},
        )

    async def fake_score_round(*, round_no, new_candidates, run_state, tracer, runtime_only_constraints):
        del tracer, runtime_only_constraints
        scored = [_scored_candidate(candidate.resume_id, source_round=round_no) for candidate in new_candidates]
        for item in scored:
            run_state.scorecards_by_resume_id[item.resume_id] = item
        run_state.top_pool_ids = [item.resume_id for item in scored]
        return scored, [], []

    monkeypatch.setattr(runtime, "_run_cts_source_lane", fake_cts_lane)
    monkeypatch.setattr(runtime, "_run_liepin_source_lane_request", fake_liepin_lane)
    monkeypatch.setattr(runtime, "_score_round", fake_score_round)
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

    assert run_state.source_coverage_summary is not None
    assert run_state.source_coverage_summary.status == "degraded"
    assert run_state.source_coverage_summary.completed_source_kinds == ("cts",)
    assert run_state.source_coverage_summary.empty_source_kinds == ("liepin",)
    payload_text = (tracer.run_dir / "runtime.source_coverage").read_text(encoding="utf-8")
    assert "empty_source_kinds" in payload_text
    assert "source_coverage_status" not in payload_text
    assert "missing_sources" not in payload_text


def test_full_source_lanes_marks_coverage_empty_when_all_selected_sources_return_no_candidates(tmp_path, monkeypatch) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), liepin_worker_mode="managed_local")
    runtime = WorkflowRuntime(settings)
    run_state = _run_state()
    tracer = RunTracer(settings.artifacts_path)
    source_plan = build_runtime_source_plan(source_kinds=["cts"], settings=settings, runtime_run_id=tracer.run_id)

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
            candidate_store_updates={},
        )

    monkeypatch.setattr(runtime, "_run_cts_source_lane", fake_cts_lane)
    try:
        asyncio.run(
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

    assert run_state.source_coverage_summary is not None
    assert run_state.source_coverage_summary.status == "empty"
    assert run_state.source_coverage_summary.empty_source_kinds == ("cts",)


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
        requirement_sheet=_requirement_sheet(),
        approved_detail_lease_ref="lease-1",
        liepin_context={"approval_secret_ref": "secret-ref", "connection_id": "conn-1"},
        progress_callback=lambda event: None,
    )

    payload = request.to_public_payload()

    assert payload["source"] == "liepin"
    assert payload["lane_mode"] == "detail"
    assert payload["requirement_sheet"] == {
        "job_title": "Data Engineer",
        "must_have_count": 0,
        "preferred_count": 0,
        "exclusion_count": 0,
    }
    assert payload["approved_detail_lease_ref"] == "lease-1"
    assert "progress_callback" not in payload
    assert "secret-ref" not in repr(payload)
