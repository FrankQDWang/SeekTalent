import asyncio
import json
from pathlib import Path
from typing import Any

from seektalent.core.retrieval.provider_contract import ProviderSnapshot, SearchRequest, SearchResult
from seektalent.models import RequirementExtractionDraft, RequirementSheet
from seektalent.models import ResumeCandidate
from seektalent.providers.liepin.compliance import ComplianceGate
from seektalent.providers.liepin.runtime_lane import run_liepin_source_lane
from seektalent.providers.liepin.store import LiepinStore
from seektalent.providers.liepin.worker_contracts import (
    LiepinDetailOpenResponse,
    LiepinDetailOpenResult,
    LiepinDetailWorkerDiagnostics,
    LiepinWorkerCandidateDetail,
    SessionStatus,
)
from seektalent.requirements import build_input_truth, normalize_requirement_draft
from seektalent.runtime.liepin_context import RuntimeLiepinContext
from seektalent.runtime.source_lanes import (
    RuntimeApprovedDetailLease,
    RuntimeSourceLaneRequest,
    build_runtime_source_plan,
)
from seektalent.storage.json import sha256_json
from tests.settings_factory import make_settings


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "replay" / "requirements_flink_v1.json"
FORBIDDEN_OUTPUT_TOKENS = (
    "authorization",
    "cookie",
    "raw_provider_payload",
    "raw_resume",
)
LIEPIN_FORBIDDEN_REPLAY_TOKENS = (
    *FORBIDDEN_OUTPUT_TOKENS,
    "acct_hash_secret",
    "approval-secret",
    "detail-open:v1",
    "compliance_gate_ref",
    "must-not-leak",
    "provider-secret-id",
)
TENANT_ID = "tenant-replay"
WORKSPACE_ID = "workspace-replay"
ACTOR_ID = "actor-replay"
CONNECTION_ID = "conn-replay"
ACCOUNT_HASH = "acct_hash_secret"


def test_requirement_replay_fixture_is_deterministic() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    input_payload = fixture["input"]
    input_truth = build_input_truth(
        job_title=input_payload["job_title"],
        jd=input_payload["jd"],
        notes=input_payload["notes"],
    )
    draft = RequirementExtractionDraft.model_validate(fixture["requirement_extraction_draft"])

    sheet = normalize_requirement_draft(draft, job_title=input_truth.job_title)
    snapshot = _requirement_replay_snapshot(
        case_id=fixture["case_id"],
        source_artifact=fixture["source_artifact"],
        input_truth_hashes={
            "job_title_sha256": input_truth.job_title_sha256,
            "jd_sha256": input_truth.jd_sha256,
            "notes_sha256": input_truth.notes_sha256,
        },
        sheet=sheet,
    )

    assert snapshot == fixture["expected_snapshot"]
    serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    assert all(token not in serialized.casefold() for token in FORBIDDEN_OUTPUT_TOKENS)


def test_liepin_runtime_replay_set_is_deterministic_and_public_safe(tmp_path: Path) -> None:
    first = _liepin_runtime_replay_snapshot(tmp_path / "first")
    second = _liepin_runtime_replay_snapshot(tmp_path / "second")

    assert first == second
    serialized = json.dumps(first, ensure_ascii=False, sort_keys=True)
    assert all(token.casefold() not in serialized.casefold() for token in LIEPIN_FORBIDDEN_REPLAY_TOKENS)
    assert first["worker_contract"] == {
        "card_account_hash_bound": True,
        "card_provider_context_bound": True,
        "detail_account_hash_bound": True,
        "detail_approval_key_issued": True,
    }


def _requirement_replay_snapshot(
    *,
    case_id: str,
    source_artifact: str,
    input_truth_hashes: dict[str, str],
    sheet: RequirementSheet,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "source_artifact": source_artifact,
        "input_truth_hashes": input_truth_hashes,
        "requirement_sheet": {
            "job_title": sheet.job_title,
            "title_anchor_terms": sheet.title_anchor_terms,
            "role_summary": sheet.role_summary,
            "must_have_capabilities": sheet.must_have_capabilities,
            "preferred_capabilities": sheet.preferred_capabilities,
            "exclusion_signals": sheet.exclusion_signals,
            "hard_constraints": sheet.hard_constraints.model_dump(mode="json"),
            "preferences": sheet.preferences.model_dump(mode="json"),
            "initial_query_term_pool": [
                {
                    "term": item.term,
                    "source": item.source,
                    "category": item.category,
                    "priority": item.priority,
                    "active": item.active,
                    "retrieval_role": item.retrieval_role,
                    "queryability": item.queryability,
                    "family": item.family,
                }
                for item in sheet.initial_query_term_pool
            ],
            "scoring_rationale": sheet.scoring_rationale,
        },
    }


class LiepinReplayWorker:
    def __init__(self) -> None:
        self.search_provider_account_hashes: list[str | None] = []
        self.search_provider_contexts: list[dict[str, str]] = []
        self.detail_requests: list[Any] = []

    async def ensure_ready(self, *, on_event=None) -> None:
        del on_event

    async def session_status(
        self,
        *,
        connection_id: str,
        tenant: str | None = None,
        workspace: str | None = None,
        provider_account_hash: str | None = None,
    ) -> SessionStatus:
        del tenant, workspace
        return SessionStatus(
            connectionId=connection_id,
            status="ready",
            providerAccountHash=provider_account_hash,
        )

    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult:
        del round_no, trace_id
        self.search_provider_contexts.append(dict(request.provider_context))
        self.search_provider_account_hashes.append(provider_account_hash)
        raw_payload = {"candidateId": "provider-secret-id", "raw_resume": "must-not-leak"}
        return SearchResult(
            candidates=[
                ResumeCandidate(
                    resume_id="liepin-card-candidate",
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
                    extraction_source="replay",
                    extractor_version="replay-contract-v1",
                    pii_classification="no_direct_contact",
                    retention_policy="provider_snapshot_7d",
                    access_scope="local_run_only",
                    redaction_state="raw_provider_payload",
                    score_evidence_source="card_only",
                )
            ],
            exhausted=True,
            raw_candidate_count=1,
        )

    async def open_details(self, request: Any) -> LiepinDetailOpenResponse:
        self.detail_requests.append(request)
        return LiepinDetailOpenResponse(
            workerCommandId=request.worker_command_id,
            results=[
                LiepinDetailOpenResult(
                    requestId=item.request_id,
                    attemptId=item.attempt_id,
                    idempotencyKey=item.idempotency_key,
                    status="completed",
                    workerResponseId=f"response:{item.candidate_id}",
                    workerCommandId=request.worker_command_id,
                    rawEvidenceRef="artifact://protected/liepin/detail/snapshot-1",
                    diagnostics=LiepinDetailWorkerDiagnostics(
                        pageLoaded=True,
                        payloadSeen=True,
                        extractionSource="network",
                    ),
                    candidate=LiepinWorkerCandidateDetail(
                        payload={"candidateId": item.candidate_id, "raw_resume": "must-not-leak"},
                        normalized_text="FastAPI retrieval ranking detail resume.",
                        provider_subject_id=item.candidate_id,
                        provider_listing_id="listing-1",
                        synthetic_candidate_fingerprint="detail-dedup-secret",
                        identity_confidence="provider_subject_id",
                        extraction_source="network",
                        extractor_version="replay-contract-v1",
                        pii_classification="direct_contact_possible",
                        retention_policy="provider_snapshot_7d",
                        access_scope="local_run_only",
                        redaction_state="raw_provider_payload",
                    ),
                )
                for item in request.requests
            ],
        )


def _liepin_runtime_replay_snapshot(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True)
    db_path = root / "liepin.sqlite3"
    gate_ref = _create_live_liepin_connection(db_path)
    worker = LiepinReplayWorker()
    settings = make_settings(
        workspace_root=str(root),
        liepin_worker_mode="opencli",
        liepin_connector_db_path=str(db_path),
        liepin_detail_open_approval_secret="approval-secret-replay",
    )
    context = RuntimeLiepinContext(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        actor_id=ACTOR_ID,
        connection_id=CONNECTION_ID,
        compliance_gate_ref=gate_ref,
        provider_account_hash=ACCOUNT_HASH,
        backend_mode="opencli",
    )
    source_plan = build_runtime_source_plan(
        source_kinds=["liepin"],
        settings=settings,
        runtime_run_id="runtime-replay",
        liepin_context=context,
    )
    card_result = asyncio.run(
        run_liepin_source_lane(
            settings=settings,
            request=_card_request(context),
            worker_client=worker,
        )
    )
    detail_result = asyncio.run(
        run_liepin_source_lane(
            settings=settings,
            request=_detail_request(context),
            worker_client=worker,
        )
    )
    blocked_detail_result = asyncio.run(
        run_liepin_source_lane(
            settings=settings,
            request=_detail_request_without_lease(context),
            worker_client=worker,
        )
    )

    detail_request = worker.detail_requests[0]
    snapshot = {
        "source_plan": [plan.to_public_payload() for plan in source_plan],
        "account_posture": context.to_safe_posture(),
        "card_result": card_result.to_public_payload(),
        "detail_result": detail_result.to_public_payload(),
        "blocked_detail_result": blocked_detail_result.to_public_payload(),
        "worker_contract": {
            "card_account_hash_bound": worker.search_provider_account_hashes == [ACCOUNT_HASH],
            "card_provider_context_bound": _provider_context_is_bound(worker.search_provider_contexts[0]),
            "detail_account_hash_bound": detail_request.provider_account_hash == ACCOUNT_HASH,
            "detail_approval_key_issued": detail_request.requests[0].approval_key.startswith("detail-open:v1:"),
        },
    }
    assert gate_ref not in json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    return snapshot


def _card_request(context: RuntimeLiepinContext) -> RuntimeSourceLaneRequest:
    return RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="card",
        job_title="Backend Engineer",
        jd="FastAPI retrieval",
        notes=None,
        requirement_sheet=_replay_requirement_sheet(),
        runtime_run_id="runtime-replay",
        source_plan_id="runtime-replay:source:0:liepin",
        source_lane_run_id="runtime-replay:source:0:liepin:card",
        source_query_terms=("FastAPI", "ranking"),
        liepin_context=context,
    )


def _detail_request(context: RuntimeLiepinContext) -> RuntimeSourceLaneRequest:
    return RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="detail",
        job_title="Backend Engineer",
        jd="FastAPI retrieval",
        notes=None,
        requirement_sheet=_replay_requirement_sheet(),
        runtime_run_id="runtime-replay",
        source_plan_id="runtime-replay:source:0:liepin",
        source_lane_run_id="runtime-replay:source:0:liepin:detail",
        source_query_terms=("FastAPI", "ranking"),
        liepin_context=context,
        approved_detail_lease=RuntimeApprovedDetailLease(
            lease_ref="lease://detail/replay",
            lease_id="detail-lease-replay",
            runtime_run_id="runtime-replay",
            source_plan_id="runtime-replay:source:0:liepin",
            source_lane_run_id="runtime-replay:source:0:liepin:card",
            source="liepin",
            recommendation_id="detail-rec-1",
            source_evidence_id="source-evidence-1",
            request_id="detail-request-1",
            ledger_id="detail-ledger-1",
            candidate_evidence_id="source-evidence-1",
            candidate_resume_id="liepin-card-candidate",
            provider_candidate_key_hash="provider-key-hash-1",
            approved_by_actor_hash="actor-hash-secret",
            approved_at="2026-05-15T00:00:00Z",
            budget_policy_hash="budget-policy-hash",
            lease_signature_ref="artifact://protected-lease/replay",
            connection_id=CONNECTION_ID,
            compliance_gate_ref=context.compliance_gate_ref or "",
            provider_account_hash=ACCOUNT_HASH,
            detail_candidates_json=(
                '[{"candidate_id":"provider-secret-id",'
                '"stable_provider_id":"provider-secret-id",'
                '"weak_fingerprint":"dedup-secret-id",'
                '"card_value_score":91}]'
            ),
            daily_budget=3,
            budget_date="2026-05-15",
            provider_day_key="liepin:acct_hash_secret:2026-05-15",
            timezone="Asia/Shanghai",
            open_policy_version="detail-policy-v1",
        ),
    )


def _detail_request_without_lease(context: RuntimeLiepinContext) -> RuntimeSourceLaneRequest:
    return RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="detail",
        job_title="Backend Engineer",
        jd="FastAPI retrieval",
        notes=None,
        requirement_sheet=_replay_requirement_sheet(),
        runtime_run_id="runtime-replay",
        source_plan_id="runtime-replay:source:0:liepin",
        source_lane_run_id="runtime-replay:source:0:liepin:detail-unapproved",
        source_query_terms=("FastAPI", "ranking"),
        liepin_context=context,
        approved_detail_lease_ref="lease://caller-supplied-only",
    )


def _create_live_liepin_connection(db_path: Path) -> str:
    store = LiepinStore(db_path)
    gate_ref = store.create_compliance_gate(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        actor_id=ACTOR_ID,
        gate=ComplianceGate(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            actor_id=ACTOR_ID,
            provider_account_hash=ACCOUNT_HASH,
            status="approved",
            candidate_personal_info_processing_basis="candidate consent or active job-seeking context",
            personal_information_processor="SeekTalent local operator",
            operator_audit_owner="local operator",
            account_holder_authorized=True,
            human_initiated_recruiting=True,
            allowed_purposes=["search"],
            retention_policy="run_debug_short",
            deletion_sla_days=14,
            deletion_path="settings/delete",
            raw_payload_access_scope="run_only",
            raw_detail_retention_allowed_after_debug=False,
            fixture_export_allowed=False,
            policy_ref="policy-v1",
        ),
        purpose="search",
    )
    store.create_connection(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        actor_id=ACTOR_ID,
        compliance_gate_ref=gate_ref,
        connection_id=CONNECTION_ID,
    )
    store.record_session_metadata(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        actor_id=ACTOR_ID,
        connection_id=CONNECTION_ID,
        provider_account_hash=ACCOUNT_HASH,
        session_store_key_id="replay-session-key",
        encrypted_state_sha256="0" * 64,
    )
    return gate_ref


def _provider_context_is_bound(provider_context: dict[str, str]) -> bool:
    return all(
        provider_context.get(key)
        for key in (
            "liepin_connection_id",
            "liepin_compliance_gate_ref",
            "liepin_provider_account_hash",
        )
    )


def _replay_requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        job_title="Backend Engineer",
        title_anchor_terms=("Backend Engineer",),
        title_anchor_rationale="Backend Engineer is the searchable title anchor.",
        role_summary="Build retrieval workflows.",
        must_have_capabilities=("FastAPI", "ranking"),
        preferred_capabilities=("evaluation",),
        exclusion_signals=("pure frontend",),
        hard_constraints={},
        preferences={"preferred_query_terms": ["FastAPI", "ranking"]},
        initial_query_term_pool=[],
        scoring_rationale="Prioritize backend retrieval evidence.",
    )
