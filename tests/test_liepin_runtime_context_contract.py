from __future__ import annotations

import asyncio
import json

from seektalent.core.retrieval.provider_contract import ProviderSnapshot, SearchRequest, SearchResult
from seektalent.models import RequirementSheet, ResumeCandidate
from seektalent.providers.liepin.runtime_lane import run_liepin_source_lane
from seektalent.runtime.liepin_context import RuntimeLiepinContext
from seektalent.runtime.source_lanes import RuntimeSourceLaneRequest
from seektalent.storage.json import sha256_json
from tests.settings_factory import make_settings


class CapturingWorker:
    def __init__(self) -> None:
        self.search_requests: list[SearchRequest] = []
        self.provider_account_hashes: list[str | None] = []

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
        del round_no, trace_id
        self.search_requests.append(request)
        self.provider_account_hashes.append(provider_account_hash)
        raw_payload = {"candidateId": "provider-secret-id", "raw_resume": "must-not-leak"}
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


def test_runtime_liepin_context_separates_provider_context_from_safe_posture() -> None:
    context = RuntimeLiepinContext(
        tenant_id="local",
        workspace_id="workspace-1",
        actor_id="user-1",
        connection_id="conn-1",
        compliance_gate_ref="gate-secret",
        provider_account_hash="acct_hash_secret",
        backend_mode="opencli",
    )

    assert context.to_provider_context()["liepin_provider_account_hash"] == "acct_hash_secret"
    safe_posture = context.to_safe_posture()
    assert safe_posture["provider_account_bound"] is True
    assert "acct_hash_secret" not in json.dumps(safe_posture, sort_keys=True)
    assert "gate-secret" not in json.dumps(safe_posture, sort_keys=True)


def test_liepin_runtime_lane_accepts_typed_context_without_leaking_public_payload() -> None:
    worker = CapturingWorker()
    request = RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="card",
        job_title="Backend Engineer",
        jd="FastAPI retrieval",
        notes=None,
        requirement_sheet=_requirement_sheet(),
        runtime_run_id="runtime-run-1",
        source_lane_run_id="lane-run-1",
        source_query_terms=("FastAPI", "ranking"),
        liepin_context=RuntimeLiepinContext(
            tenant_id="local",
            workspace_id="workspace-1",
            actor_id="user-1",
            connection_id="conn-1",
            compliance_gate_ref="gate-secret",
            provider_account_hash="acct_hash_secret",
        ),
    )

    result = asyncio.run(run_liepin_source_lane(settings=make_settings(), request=request, worker_client=worker))

    provider_context = worker.search_requests[0].provider_context
    assert provider_context["liepin_connection_id"] == "conn-1"
    assert provider_context["liepin_compliance_gate_ref"] == "gate-secret"
    assert worker.provider_account_hashes == ["acct_hash_secret"]
    public_text = repr(result.to_public_payload())
    assert "acct_hash_secret" not in public_text
    assert "gate-secret" not in public_text
    assert "must-not-leak" not in public_text


def _requirement_sheet() -> RequirementSheet:
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
