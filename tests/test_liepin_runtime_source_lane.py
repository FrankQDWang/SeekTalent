from __future__ import annotations

import asyncio

from seektalent.core.retrieval.provider_contract import ProviderSnapshot, SearchRequest, SearchResult
from seektalent.models import ResumeCandidate
import seektalent.providers.liepin.runtime_lane as runtime_lane
from seektalent.providers.liepin.runtime_lane import liepin_backend_posture, run_liepin_source_lane
from seektalent.runtime.source_lanes import RuntimeApprovedDetailLease, RuntimeSourceLaneRequest
from seektalent.storage.json import sha256_json
from tests.settings_factory import make_settings


class FakeWorker:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, object]] = []

    async def ensure_ready(self, *, on_event=None) -> None:
        del on_event

    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult:
        raw_payload = {"candidateId": "provider-secret-id", "raw_resume": "must-not-leak"}
        self.search_calls.append(
            {
                "provider_context": request.provider_context,
                "round_no": round_no,
                "trace_id": trace_id,
                "provider_account_hash": provider_account_hash,
            }
        )
        return SearchResult(
            candidates=[
                ResumeCandidate(
                    resume_id="liepin-candidate-1",
                    source_resume_id="provider-secret-id",
                    snapshot_sha256=sha256_json(raw_payload),
                    dedup_key="dedup-secret-id",
                    search_text="FastAPI retrieval ranking systems.",
                    raw={},
                )
            ],
            provider_snapshots=[
                ProviderSnapshot(
                    provider_name="liepin",
                    payload_kind="card",
                    raw_payload=raw_payload,
                    normalized_text="FastAPI retrieval ranking systems.",
                    provider_subject_id="provider-secret-id",
                    provider_listing_id=None,
                    synthetic_candidate_fingerprint="dedup-secret-id",
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
            diagnostics=[],
            exhausted=True,
            raw_candidate_count=1,
        )

    async def open_details(self, request) -> object:
        raise AssertionError("card runtime lane must not fetch details")


def test_liepin_backend_posture_records_worker_modes_without_dokobot_action_fallback() -> None:
    assert liepin_backend_posture(make_settings(liepin_worker_mode="managed_local")) == {
        "backend_mode": "legacy_worker_compat",
        "reason": "managed_local",
    }
    assert liepin_backend_posture(
        make_settings(liepin_worker_mode="fake_fixture", liepin_allow_fake_fixture_worker=True)
    ) == {"backend_mode": "fake_fixture", "reason": "explicit_test_fixture"}
    assert liepin_backend_posture(make_settings(liepin_worker_mode="disabled")) == {
        "backend_mode": "blocked",
        "reason": "no_live_action_backend",
    }


def test_liepin_runtime_lane_uses_provider_adapter_context_and_public_payload_is_safe() -> None:
    worker = FakeWorker()
    request = RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="card",
        job_title="Backend Engineer",
        jd="FastAPI retrieval",
        notes=None,
        runtime_run_id="runtime-run-1",
        source_lane_run_id="lane-run-1",
        source_query_terms=("FastAPI", "ranking"),
        liepin_context={
            "tenant_id": "local",
            "workspace_id": "workspace-1",
            "actor_id": "user-1",
            "connection_id": "conn-1",
            "provider_account_hash": "acct_hash_123",
        },
    )

    result = asyncio.run(run_liepin_source_lane(settings=make_settings(), request=request, worker_client=worker))

    provider_context = worker.search_calls[0]["provider_context"]
    assert provider_context["liepin_tenant_id"] == "local"
    assert provider_context["liepin_workspace_id"] == "workspace-1"
    assert provider_context["liepin_actor_id"] == "user-1"
    assert provider_context["liepin_connection_id"] == "conn-1"
    assert worker.search_calls[0]["provider_account_hash"] == "acct_hash_123"
    assert result.detail_recommendations[0].candidate_resume_id == "liepin-candidate-1"
    assert "must-not-leak" not in repr(result.to_public_payload())


def test_liepin_runtime_card_lane_passes_compliance_gate_to_live_adapter() -> None:
    worker = FakeWorker()
    request = RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="card",
        job_title="Backend Engineer",
        jd="FastAPI retrieval",
        notes=None,
        runtime_run_id="runtime-run-1",
        source_lane_run_id="lane-run-1",
        source_query_terms=("FastAPI", "ranking"),
        liepin_context={
            "tenant_id": "local",
            "workspace_id": "workspace-1",
            "actor_id": "user-1",
            "connection_id": "conn-1",
            "compliance_gate_ref": "gate-1",
            "provider_account_hash": "acct_hash_123",
        },
    )

    asyncio.run(run_liepin_source_lane(settings=make_settings(), request=request, worker_client=worker))

    provider_context = worker.search_calls[0]["provider_context"]
    assert provider_context["liepin_compliance_gate_ref"] == "gate-1"


def test_liepin_runtime_detail_lane_executes_provider_detail_mode_with_approved_lease(monkeypatch) -> None:
    provider_calls: list[SearchRequest] = []

    class FakeDetailProvider:
        def __init__(self, settings, *, worker_client=None, **kwargs):
            del settings, worker_client, kwargs

        async def search(self, request: SearchRequest, *, round_no: int, trace_id: str) -> SearchResult:
            del round_no, trace_id
            provider_calls.append(request)
            raw_payload = {"raw_resume": "must-not-leak", "candidateId": "provider-detail-id"}
            return SearchResult(
                candidates=[
                    ResumeCandidate(
                        resume_id="provider-detail-id",
                        source_resume_id="provider-detail-id",
                        snapshot_sha256=sha256_json(raw_payload),
                        dedup_key="provider-detail-id",
                        search_text="FastAPI retrieval ranking detail resume.",
                        raw={
                            "raw_payload_artifact_ref": "artifact://protected/liepin/detail/provider-detail-id",
                            "safe_summary_ref": "artifact://summary/liepin/provider-detail-id",
                        },
                    )
                ],
                provider_snapshots=[
                    ProviderSnapshot(
                        provider_name="liepin",
                        payload_kind="detail",
                        raw_payload=raw_payload,
                        normalized_text="FastAPI retrieval ranking detail resume.",
                        provider_subject_id="provider-detail-id",
                        provider_listing_id=None,
                        synthetic_candidate_fingerprint="provider-detail-id",
                        identity_confidence="provider_subject_id",
                        extraction_source="test",
                        extractor_version="test",
                        pii_classification="no_direct_contact",
                        retention_policy="provider_snapshot_7d",
                        access_scope="local_run_only",
                        redaction_state="raw_provider_payload",
                        score_evidence_source="detail_enriched",
                    )
                ],
                raw_candidate_count=1,
                request_payload={"liepin_detail_open_plan_ref": "lease://detail/1"},
            )

    monkeypatch.setattr(runtime_lane, "LiepinProviderAdapter", FakeDetailProvider)
    request = RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="detail",
        job_title="Backend Engineer",
        jd="FastAPI retrieval",
        notes=None,
        runtime_run_id="runtime-run-1",
        source_lane_run_id="lane-detail-1",
        approved_detail_lease=RuntimeApprovedDetailLease(
            lease_ref="lease://detail/1",
            request_id="detail-request-1",
            ledger_id="detail-ledger-1",
            candidate_evidence_id="evidence-1",
            provider_candidate_key_hash="provider-hash-1",
            connection_id="conn-1",
            compliance_gate_ref="gate-1",
            provider_account_hash="acct_hash_123",
            detail_candidates_json=(
                '[{"candidate_id":"provider-detail-id",'
                '"stable_provider_id":"provider-detail-id",'
                '"weak_fingerprint":"provider-detail-id",'
                '"card_value_score":91}]'
            ),
            daily_budget=3,
            budget_date="2026-05-15",
            provider_day_key="liepin:acct_hash_123:2026-05-15",
            timezone="Asia/Shanghai",
            open_policy_version="detail-policy-v1",
        ),
        liepin_context={
            "tenant_id": "local",
            "workspace_id": "workspace-1",
            "actor_id": "user-1",
            "approval_secret_ref": "approval-secret-ref",
        },
    )

    result = asyncio.run(run_liepin_source_lane(settings=make_settings(), request=request, worker_client=FakeWorker()))

    provider_request = provider_calls[0]
    assert provider_request.fetch_mode == "detail"
    assert provider_request.provider_context["liepin_detail_open_plan_ref"] == "lease://detail/1"
    assert provider_request.provider_context["liepin_detail_open_policy_version"] == "detail-policy-v1"
    assert result.status == "completed"
    assert result.lane_mode == "detail"
    assert result.source_evidence_updates[0].evidence_level == "detail"
    assert result.provider_snapshot_refs == ("artifact://protected/liepin/detail/provider-detail-id",)
    public_payload = result.to_public_payload()
    assert "must-not-leak" not in repr(public_payload)
    assert "approval-secret-ref" not in repr(public_payload)


def test_liepin_runtime_detail_lane_blocks_synthetic_lease_ref_without_typed_lease() -> None:
    request = RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="detail",
        job_title="Backend Engineer",
        jd="FastAPI retrieval",
        notes=None,
        runtime_run_id="runtime-run-1",
        source_lane_run_id="lane-detail-1",
        approved_detail_lease_ref="lease://caller-supplied-only",
    )

    result = asyncio.run(run_liepin_source_lane(settings=make_settings(), request=request, worker_client=FakeWorker()))

    assert result.status == "blocked"
    assert result.blocked_reason_code == "blocked_approval_missing"
