from __future__ import annotations

import asyncio
import json

import pytest

from seektalent.core.retrieval.provider_contract import ProviderSnapshot, SearchRequest, SearchResult
from seektalent.models import RequirementSheet, ResumeCandidate
from seektalent.providers.liepin.client import LiepinWorkerModeError
import seektalent.sources.liepin.runtime_lane as runtime_lane
from seektalent.sources.liepin.runtime_lane import (
    liepin_backend_posture,
    run_liepin_logical_query_bundle,
    run_liepin_source_lane,
    runtime_safe_reason_code_from_worker_failure_code,
)
from seektalent.providers.liepin.worker_contracts import LiepinWorkerPartialSearchError
from seektalent.runtime.logical_query_dispatch import LogicalQueryDispatch
from seektalent.runtime.source_filters import RuntimeLocationExecutionIntent
from seektalent.runtime.source_lanes import RuntimeApprovedDetailLease, RuntimeSourceBudgetPolicy, RuntimeSourceLaneRequest
from seektalent.runtime.source_query_intent import RuntimeSourceQueryIntent
from seektalent.storage.json import sha256_json
from seektalent.sources.liepin.reason_codes import LIEPIN_SOURCE_LANE_REASON_CODE_MAP
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
                "request": request,
                "provider_context": request.provider_context,
                "keyword_query": request.keyword_query,
                "page_size": request.page_size,
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
                    raw={
                        "safe_card_summary": {
                            "current_or_recent_title": "Backend Engineer",
                            "current_or_recent_company": "Retrieval Ranking Systems",
                            "skill_tags": ["FastAPI", "ranking"],
                        }
                    },
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


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        job_title="AI Agent Engineer",
        title_anchor_terms=("AI Agent",),
        title_anchor_rationale="AI Agent is the searchable title anchor.",
        role_summary="Build agentic retrieval workflows.",
        must_have_capabilities=("LangGraph", "RAG"),
        preferred_capabilities=("evaluation",),
        exclusion_signals=("pure frontend",),
        hard_constraints={},
        preferences={"preferred_query_terms": ["LangGraph", "RAG"]},
        initial_query_term_pool=[],
        scoring_rationale="Prioritize agent workflow and retrieval evidence.",
    )


def test_liepin_lane_passes_requirement_sheet_json_to_worker_context() -> None:
    worker = FakeWorker()
    result = asyncio.run(
        run_liepin_source_lane(
            settings=make_settings(),
            request=RuntimeSourceLaneRequest(
                source="liepin",
                lane_mode="card",
                job_title="AI Agent Engineer",
                jd="Build LangGraph and RAG systems.",
                notes="Prefer evaluation.",
                requirement_sheet=_requirement_sheet(),
                source_query_terms=("LangGraph", "RAG"),
                logical_query_instance_id="q-exploit",
                logical_query_role="exploit",
                logical_keyword_query="LangGraph RAG",
                logical_requested_count=7,
                logical_provider_scan_limit=30,
            ),
            worker_client=worker,
        )
    )

    assert result.status == "completed"
    provider_context = worker.search_calls[0]["provider_context"]
    requirement_payload = json.loads(provider_context["liepin_requirement_sheet_json"])
    assert requirement_payload["job_title"] == "AI Agent Engineer"
    assert requirement_payload["must_have_capabilities"] == ["LangGraph", "RAG"]
    assert "liepin_must_haves_json" not in provider_context
    assert "liepin_nice_to_haves_json" not in provider_context


def test_liepin_runtime_lane_appends_opencli_workflow_step_events() -> None:
    class WorkflowWorker(FakeWorker):
        async def search(
            self,
            request: SearchRequest,
            *,
            round_no: int,
            trace_id: str,
            provider_account_hash: str | None = None,
        ) -> SearchResult:
            del request, round_no, trace_id, provider_account_hash
            raw_payload = {"protectedSnapshotRef": "artifact://protected/liepin-opencli/raw/run-1/1.json"}
            return SearchResult(
                candidates=[
                    ResumeCandidate(
                        resume_id="liepin-candidate-1",
                        source_resume_id="provider-secret-id",
                        snapshot_sha256=sha256_json(raw_payload),
                        dedup_key="dedup-secret-id",
                        search_text="完整原始简历文本",
                        raw={},
                    )
                ],
                provider_snapshots=[
                    ProviderSnapshot(
                        provider_name="liepin",
                        payload_kind="detail",
                        raw_payload=raw_payload,
                        normalized_text="完整原始简历文本",
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
                        score_evidence_source="detail_enriched",
                    )
                ],
                diagnostics=[],
                exhausted=True,
                raw_candidate_count=1,
                request_payload={
                    "workflowSteps": [
                        {
                            "event_type": "source_workflow_step_completed",
                            "step_name": "capture_detail",
                            "status": "completed",
                            "safe_counts": {"details_opened": 1},
                            "safe_metadata": {"rank": 1},
                            "artifact_refs": ["artifact://protected/liepin-opencli/raw/run-1/1.json"],
                        }
                    ],
                    "actionTraceRef": "artifact://protected/liepin-opencli/trace/run-1/action-trace.json",
                },
            )

    result = asyncio.run(
        run_liepin_source_lane(
            settings=make_settings(),
            request=RuntimeSourceLaneRequest(
                source="liepin",
                lane_mode="card",
                job_title="数据开发专家",
                jd="JD",
                notes="",
                requirement_sheet=_requirement_sheet(),
                runtime_run_id="run-1",
                source_plan_id="run-1:source:liepin",
                source_lane_run_id="run-1:source:liepin:round:1:lane:1",
                source_query_terms=("数据开发", "Python"),
                logical_query_role="exploit",
                logical_keyword_query="数据开发 Python",
                logical_requested_count=2,
                logical_provider_scan_limit=6,
            ),
            worker_client=WorkflowWorker(),
        )
    )

    workflow_events = [event for event in result.events if event.step_name == "capture_detail"]
    assert len(workflow_events) == 1
    assert workflow_events[0].event_type == "source_workflow_step_completed"
    assert workflow_events[0].safe_counts == {"details_opened": 1}


def test_liepin_runtime_lane_preserves_workflow_steps_on_blocked_opencli_error() -> None:
    class BlockedWorkflowWorker(FakeWorker):
        async def search(
            self,
            request: SearchRequest,
            *,
            round_no: int,
            trace_id: str,
            provider_account_hash: str | None = None,
        ) -> SearchResult:
            del request, round_no, trace_id, provider_account_hash
            error = LiepinWorkerModeError(
                "Liepin OpenCLI resume search blocked.",
                code="liepin_opencli_detail_not_opened",
            )
            error.partial_search_result = SearchResult(
                candidates=[],
                diagnostics=[],
                exhausted=False,
                raw_candidate_count=0,
                request_payload={
                    "workflowSteps": [
                        {
                            "event_type": "source_workflow_step_failed",
                            "step_name": "open_detail",
                            "status": "failed",
                            "safe_reason_code": "liepin_opencli_detail_not_opened",
                            "safe_counts": {},
                            "safe_metadata": {"rank": 1},
                            "artifact_refs": [],
                        }
                    ]
                },
            )
            error.cards_collected = 0
            raise error

    result = asyncio.run(
        run_liepin_source_lane(
            settings=make_settings(),
            request=RuntimeSourceLaneRequest(
                source="liepin",
                lane_mode="card",
                job_title="数据开发专家",
                jd="JD",
                notes="",
                requirement_sheet=_requirement_sheet(),
                runtime_run_id="run-1",
                source_plan_id="run-1:source:liepin",
                source_lane_run_id="run-1:source:liepin:round:1:lane:1",
                source_query_terms=("数据开发", "Python"),
                logical_query_role="exploit",
                logical_keyword_query="数据开发 Python",
                logical_requested_count=2,
                logical_provider_scan_limit=6,
            ),
            worker_client=BlockedWorkflowWorker(),
        )
    )

    assert result.status == "blocked"
    workflow_events = [event for event in result.events if event.step_name == "open_detail"]
    assert len(workflow_events) == 1
    assert workflow_events[0].event_type == "source_workflow_step_failed"
    assert workflow_events[0].status == "blocked"
    assert workflow_events[0].safe_reason_code == "liepin_opencli_detail_not_opened"


def test_liepin_lane_keeps_runtime_requirement_sheet_when_compiled_context_is_stale() -> None:
    worker = FakeWorker()
    compiled_request = SearchRequest(
        query_terms=["stale"],
        query_role="primary",
        keyword_query="stale",
        adapter_notes=[],
        runtime_constraints=[],
        fetch_mode="summary",
        page_size=3,
        provider_context={
            "liepin_requirement_sheet_json": json.dumps(
                {"job_title": "Stale Title"},
                ensure_ascii=False,
                sort_keys=True,
            ),
            "liepin_max_cards": "3",
        },
    )

    asyncio.run(
        run_liepin_source_lane(
            settings=make_settings(),
            request=RuntimeSourceLaneRequest(
                source="liepin",
                lane_mode="card",
                job_title="AI Agent Engineer",
                jd="Build LangGraph and RAG systems.",
                notes="Prefer evaluation.",
                requirement_sheet=_requirement_sheet(),
                source_query_terms=("LangGraph", "RAG"),
                logical_query_instance_id="q-exploit",
                logical_query_role="exploit",
                logical_keyword_query="LangGraph RAG",
                logical_requested_count=7,
                logical_provider_scan_limit=30,
            ),
            worker_client=worker,
            compiled_search_request=compiled_request,
        )
    )

    provider_context = worker.search_calls[0]["provider_context"]
    requirement_payload = json.loads(provider_context["liepin_requirement_sheet_json"])
    assert requirement_payload["job_title"] == "AI Agent Engineer"
    assert requirement_payload["must_have_capabilities"] == ["LangGraph", "RAG"]


def test_liepin_logical_query_bundle_uses_runtime_query_identity_and_requested_count() -> None:
    worker = FakeWorker()
    logical_query = LogicalQueryDispatch(
        round_no=3,
        query_role="exploit",
        lane_type="exploit",
        query_terms=("数据开发", "平台"),
        keyword_query="数据开发 平台",
        query_instance_id="runtime-query-1",
        query_fingerprint="runtime-fingerprint-1",
        term_group_key="term-group-data-platform",
        requested_count=4,
        source_plan_version="7",
    )

    result = asyncio.run(
        run_liepin_logical_query_bundle(
            settings=make_settings(),
            runtime_run_id="runtime-run-1",
            source_plan_id="plan-liepin",
            job_title="数据开发专家",
            jd="负责数据平台建设",
            notes="Python",
            requirement_sheet=_requirement_sheet(),
            logical_queries=(logical_query,),
            source_budget_policy=RuntimeSourceBudgetPolicy(page_size=30, max_cards=30),
            liepin_context={"provider_account_hash": "acct_hash_123"},
            worker_client=worker,
        )
    )

    provider_request = worker.search_calls[0]["request"]
    provider_context = worker.search_calls[0]["provider_context"]
    assert provider_request.keyword_query == "数据开发 平台"
    assert provider_request.page_size == 4
    assert worker.search_calls[0]["trace_id"] == "plan-liepin:round:3:lane:1"
    assert provider_context["query_instance_id"] == "runtime-query-1"
    assert provider_context["query_fingerprint"] == "runtime-fingerprint-1"
    assert result.source_lane_run_id == "plan-liepin:round:3:lane:1"
    assert result.source_evidence_updates[0].query_fingerprint == "runtime-fingerprint-1"
    assert [
        (package.query_instance_id, package.query_fingerprint, package.term_group_key)
        for package in result.executed_query_packages
    ] == [("runtime-query-1", "runtime-fingerprint-1", "term-group-data-platform")]


async def _run_fixture_two_query_liepin_bundle():
    return await run_liepin_logical_query_bundle(
        settings=make_settings(),
        runtime_run_id="runtime-run-1",
        source_plan_id="plan-liepin",
        job_title="数据开发专家",
        jd="负责数据平台建设",
        notes="Python",
        requirement_sheet=_requirement_sheet(),
        logical_queries=(
            LogicalQueryDispatch(
                round_no=3,
                query_role="exploit",
                lane_type="exploit",
                query_terms=("数据开发", "平台"),
                keyword_query="数据开发 平台",
                query_instance_id="primary-1",
                query_fingerprint="fingerprint-primary-1",
                term_group_key="term-group-primary-1",
                requested_count=4,
                source_plan_version="7",
            ),
            LogicalQueryDispatch(
                round_no=3,
                query_role="explore",
                lane_type="generic_explore",
                query_terms=("数据开发", "flink"),
                keyword_query="数据开发 flink",
                query_instance_id="explore-1",
                query_fingerprint="fingerprint-explore-1",
                term_group_key="term-group-explore-1",
                requested_count=2,
                source_plan_version="7",
            ),
        ),
        source_budget_policy=RuntimeSourceBudgetPolicy(page_size=30, max_cards=30),
        liepin_context={"provider_account_hash": "acct_hash_123"},
        worker_client=FakeWorker(),
    )


def test_liepin_bundle_preserves_one_execution_outcome_per_logical_query() -> None:
    result = asyncio.run(_run_fixture_two_query_liepin_bundle())

    assert [item.query_instance_id for item in result.query_execution_outcomes] == ["primary-1", "explore-1"]
    assert all(item.status in {"completed", "partial"} for item in result.query_execution_outcomes)
    assert {(item.query_instance_id, item.resume_id) for item in result.candidate_query_attributions} == {
        ("primary-1", "liepin-candidate-1"),
        ("explore-1", "liepin-candidate-1"),
    }


def test_liepin_bundle_does_not_start_query_when_worker_readiness_blocks() -> None:
    class NotReadyWorker(FakeWorker):
        async def ensure_ready(self, *, on_event=None) -> None:
            del on_event
            raise LiepinWorkerModeError("worker_not_ready")

    worker = NotReadyWorker()
    result = asyncio.run(
        run_liepin_logical_query_bundle(
            settings=make_settings(),
            runtime_run_id="runtime-run-1",
            source_plan_id="plan-liepin",
            job_title="数据开发专家",
            jd="负责数据平台建设",
            notes="Python",
            requirement_sheet=_requirement_sheet(),
            logical_queries=(
                LogicalQueryDispatch(
                    round_no=3,
                    query_role="exploit",
                    lane_type="exploit",
                    query_terms=("数据开发", "平台"),
                    keyword_query="数据开发 平台",
                    query_instance_id="primary-1",
                    query_fingerprint="fingerprint-primary-1",
                    term_group_key="term-group-primary-1",
                    requested_count=4,
                    source_plan_version="7",
                ),
            ),
            source_budget_policy=RuntimeSourceBudgetPolicy(page_size=30, max_cards=30),
            liepin_context={"provider_account_hash": "acct_hash_123"},
            worker_client=worker,
        )
    )

    assert worker.search_calls == []
    assert result.status == "blocked"
    assert [(item.status, item.dispatch_started) for item in result.query_execution_outcomes] == [
        ("blocked", False),
    ]


def test_liepin_logical_query_bundle_uses_compiled_source_intent_resume_budget() -> None:
    class DetailBudgetWorker(FakeWorker):
        async def search(
            self,
            request: SearchRequest,
            *,
            round_no: int,
            trace_id: str,
            provider_account_hash: str | None = None,
        ) -> SearchResult:
            del round_no, trace_id, provider_account_hash
            self.search_calls.append(
                {
                    "request": request,
                    "provider_context": request.provider_context,
                    "page_size": request.page_size,
                }
            )
            candidates = []
            snapshots = []
            for index in range(request.page_size):
                raw_payload = {
                    "provider_candidate_key_hash": f"hash-{index}",
                    "provider_snapshot_ref": f"artifact://protected/pi-detail/{index}",
                    "safe_summary_ref": f"artifact://public-summary/pi-detail/{index}",
                    "score_evidence_source": "detail_enriched",
                    "currentTitle": "数据开发专家",
                    "currentCompany": "数据平台公司",
                    "workExperienceList": [
                        {
                            "company": "数据平台公司",
                            "title": "数据开发专家",
                            "summary": f"{request.keyword_query} resume {index}",
                        }
                    ],
                    "skills": list(request.query_terms),
                }
                candidates.append(
                    ResumeCandidate(
                        resume_id=f"liepin-detail-{index}",
                        source_resume_id=None,
                        snapshot_sha256=sha256_json(raw_payload),
                        dedup_key=f"liepin-detail-{index}",
                        search_text=f"{request.keyword_query} resume {index}",
                        raw=raw_payload,
                    )
                )
                snapshots.append(
                    ProviderSnapshot(
                        provider_name="liepin",
                        payload_kind="detail",
                        raw_payload=raw_payload,
                        normalized_text=f"{request.keyword_query} resume {index}",
                        provider_subject_id=str(raw_payload["provider_candidate_key_hash"]),
                        provider_listing_id=None,
                        synthetic_candidate_fingerprint=f"liepin-detail-{index}",
                        identity_confidence="provider_subject_id",
                        extraction_source="test",
                        extractor_version="pi-agent-liepin-detail-v1",
                        pii_classification="no_direct_contact",
                        retention_policy="provider_snapshot_30d",
                        access_scope="local_run_only",
                        redaction_state="redacted",
                        score_evidence_source="detail_enriched",
                    )
                )
            return SearchResult(
                candidates=candidates,
                provider_snapshots=snapshots,
                raw_candidate_count=len(candidates),
                exhausted=True,
            )

    worker = DetailBudgetWorker()
    logical_query = LogicalQueryDispatch(
        round_no=1,
        query_role="exploit",
        lane_type="exploit",
        query_terms=("数据开发", "平台"),
        keyword_query="数据开发 平台",
        query_instance_id="runtime-query-1",
        query_fingerprint="runtime-fingerprint-1",
        term_group_key="term-group-data-platform",
        requested_count=7,
        source_plan_version="7",
    )
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="runtime-query-1",
        query_fingerprint="runtime-fingerprint-1",
        term_group_key="term-group-data-platform",
        query_terms=("数据开发", "平台"),
        keyword_query="数据开发 平台",
        requested_count=2,
        provider_scan_limit=6,
        source_plan_version="7",
        filter_intents=(),
        location_intent=None,
        age_intent=None,
    )

    result = asyncio.run(
        run_liepin_logical_query_bundle(
            settings=make_settings(),
            runtime_run_id="runtime-run-1",
            source_plan_id="plan-liepin",
            job_title="数据开发专家",
            jd="负责数据平台建设",
            notes="Python",
            requirement_sheet=_requirement_sheet(),
            logical_queries=(logical_query,),
            source_budget_policy=RuntimeSourceBudgetPolicy(page_size=30, max_cards=30),
            liepin_context={"provider_account_hash": "acct_hash_123"},
            source_query_intents=(intent,),
            worker_client=worker,
        )
    )

    provider_request = worker.search_calls[0]["request"]
    provider_context = worker.search_calls[0]["provider_context"]
    assert provider_request.page_size == 2
    assert provider_context["liepin_max_cards"] == "6"
    assert [
        (package.query_instance_id, package.query_fingerprint, package.term_group_key)
        for package in result.executed_query_packages
    ] == [("runtime-query-1", "runtime-fingerprint-1", "term-group-data-platform")]


def test_liepin_logical_query_bundle_executes_filter_targets_until_provider_scan_limit() -> None:
    class TargetWorker(FakeWorker):
        async def search(
            self,
            request: SearchRequest,
            *,
            round_no: int,
            trace_id: str,
            provider_account_hash: str | None = None,
        ) -> SearchResult:
            native_filters = json.loads(str(request.provider_context["liepin_native_filters_json"]))
            city_filter = native_filters["city"]
            city = str(city_filter["label"] if isinstance(city_filter, dict) else city_filter)
            self.search_calls.append(
                {
                    "request": request,
                    "provider_context": request.provider_context,
                    "native_filters": native_filters,
                    "round_no": round_no,
                    "trace_id": trace_id,
                    "provider_account_hash": provider_account_hash,
                }
            )
            candidates: list[ResumeCandidate] = []
            snapshots: list[ProviderSnapshot] = []
            for offset in range(2):
                provider_key = f"{city}-{offset}"
                raw_payload = {"candidateId": provider_key}
                candidates.append(
                    ResumeCandidate(
                        resume_id=f"liepin-{provider_key}",
                        source_resume_id=provider_key,
                        snapshot_sha256=sha256_json(raw_payload),
                        dedup_key=provider_key,
                        search_text=f"{city} 数据开发专家 {offset}",
                        raw={"score_evidence_source": "detail_enriched"},
                    )
                )
                snapshots.append(
                    ProviderSnapshot(
                        provider_name="liepin",
                        payload_kind="detail",
                        raw_payload=raw_payload,
                        normalized_text=f"{city} 数据开发专家 {offset}",
                        provider_subject_id=provider_key,
                        provider_listing_id=None,
                        synthetic_candidate_fingerprint=provider_key,
                        identity_confidence="provider_subject_id",
                        extraction_source="test",
                        extractor_version="test",
                        pii_classification="no_direct_contact",
                        retention_policy="provider_snapshot_30d",
                        access_scope="local_run_only",
                        redaction_state="redacted",
                        score_evidence_source="detail_enriched",
                    )
                )
            return SearchResult(
                candidates=candidates,
                provider_snapshots=snapshots,
                diagnostics=[],
                exhausted=True,
                raw_candidate_count=len(candidates),
            )

    worker = TargetWorker()
    logical_query = LogicalQueryDispatch(
        round_no=2,
        query_role="exploit",
        lane_type="exploit",
        query_terms=("数据开发专家",),
        keyword_query="数据开发专家",
        query_instance_id="runtime-query-1",
        query_fingerprint="runtime-fingerprint-1",
        term_group_key="term-group-data-platform",
        requested_count=4,
        source_plan_version="7",
    )
    intent = RuntimeSourceQueryIntent(
        round_no=2,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="runtime-query-1",
        query_fingerprint="runtime-fingerprint-1",
        term_group_key="term-group-data-platform",
        query_terms=("数据开发专家",),
        keyword_query="数据开发专家",
        requested_count=4,
        provider_scan_limit=4,
        source_plan_version="7",
        filter_intents=(),
        location_intent=RuntimeLocationExecutionIntent(
            mode="priority_then_fallback",
            allowed_locations=("上海", "北京", "深圳"),
            preferred_locations=("上海",),
            priority_order=("上海",),
            balanced_order=("北京", "深圳"),
            rotation_offset=0,
            target_new=4,
        ),
        age_intent=None,
    )

    result = asyncio.run(
        run_liepin_logical_query_bundle(
            settings=make_settings(),
            runtime_run_id="runtime-run-1",
            source_plan_id="plan-liepin",
            job_title="数据开发专家",
            jd="负责数据平台建设",
            notes="Python",
            requirement_sheet=_requirement_sheet(),
            logical_queries=(logical_query,),
            source_budget_policy=RuntimeSourceBudgetPolicy(page_size=30, max_cards=30),
            liepin_context={"provider_account_hash": "acct_hash_123"},
            source_query_intents=(intent,),
            worker_client=worker,
        )
    )

    assert [call["trace_id"] for call in worker.search_calls] == [
        "plan-liepin:round:2:lane:1:target:1",
        "plan-liepin:round:2:lane:1:target:2",
    ]
    assert [call["native_filters"]["city"] for call in worker.search_calls] == [
        {"section": "expected", "label": "上海"},
        {"section": "expected", "label": "北京"},
    ]
    assert len(result.candidate_store_updates) == 4
    assert all(item.query_fingerprint == "runtime-fingerprint-1" for item in result.source_evidence_updates)


def test_liepin_bundle_counts_dedup_key_repeated_across_filter_targets_once() -> None:
    class RepeatedCandidateWorker(FakeWorker):
        async def search(
            self,
            request: SearchRequest,
            *,
            round_no: int,
            trace_id: str,
            provider_account_hash: str | None = None,
        ) -> SearchResult:
            native_filters = json.loads(str(request.provider_context["liepin_native_filters_json"]))
            city = str(native_filters["city"]["label"])
            raw_payload = {"candidateId": f"listing-{city}"}
            self.search_calls.append(
                {
                    "request": request,
                    "provider_context": request.provider_context,
                    "round_no": round_no,
                    "trace_id": trace_id,
                    "provider_account_hash": provider_account_hash,
                }
            )
            return SearchResult(
                candidates=[
                    ResumeCandidate(
                        resume_id=f"liepin-listing-{city}",
                        source_resume_id=f"listing-{city}",
                        snapshot_sha256=sha256_json(raw_payload),
                        dedup_key="provider-subject-a",
                        search_text=f"{city} 数据开发专家",
                        raw={
                            "score_evidence_source": "detail_enriched",
                            "currentTitle": "数据开发专家",
                            "currentCompany": "数据平台公司",
                        },
                    )
                ],
                provider_snapshots=[
                    ProviderSnapshot(
                        provider_name="liepin",
                        payload_kind="detail",
                        raw_payload=raw_payload,
                        normalized_text=f"{city} 数据开发专家",
                        provider_subject_id="provider-subject-a",
                        provider_listing_id=f"listing-{city}",
                        synthetic_candidate_fingerprint="provider-subject-a",
                        identity_confidence="provider_subject_id",
                        extraction_source="test",
                        extractor_version="test",
                        pii_classification="no_direct_contact",
                        retention_policy="provider_snapshot_30d",
                        access_scope="local_run_only",
                        redaction_state="redacted",
                        score_evidence_source="detail_enriched",
                    )
                ],
                diagnostics=[],
                exhausted=True,
                raw_candidate_count=1,
            )

    worker = RepeatedCandidateWorker()
    logical_query = LogicalQueryDispatch(
        round_no=2,
        query_role="exploit",
        lane_type="exploit",
        query_terms=("数据开发专家",),
        keyword_query="数据开发专家",
        query_instance_id="runtime-query-1",
        query_fingerprint="runtime-fingerprint-1",
        term_group_key="term-group-data-platform",
        requested_count=2,
        source_plan_version="7",
    )
    intent = RuntimeSourceQueryIntent(
        round_no=2,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="runtime-query-1",
        query_fingerprint="runtime-fingerprint-1",
        term_group_key="term-group-data-platform",
        query_terms=("数据开发专家",),
        keyword_query="数据开发专家",
        requested_count=2,
        provider_scan_limit=2,
        source_plan_version="7",
        filter_intents=(),
        location_intent=RuntimeLocationExecutionIntent(
            mode="priority_then_fallback",
            allowed_locations=("上海", "北京"),
            preferred_locations=("上海",),
            priority_order=("上海",),
            balanced_order=("北京",),
            rotation_offset=0,
            target_new=2,
        ),
        age_intent=None,
    )

    result = asyncio.run(
        run_liepin_logical_query_bundle(
            settings=make_settings(),
            runtime_run_id="runtime-run-1",
            source_plan_id="plan-liepin",
            job_title="数据开发专家",
            jd="负责数据平台建设",
            notes="Python",
            requirement_sheet=_requirement_sheet(),
            logical_queries=(logical_query,),
            source_budget_policy=RuntimeSourceBudgetPolicy(page_size=30, max_cards=30),
            liepin_context={"provider_account_hash": "acct_hash_123"},
            source_query_intents=(intent,),
            worker_client=worker,
        )
    )

    assert len(worker.search_calls) == 2
    outcome = result.query_execution_outcomes[0]
    assert outcome.raw_candidate_count == 2
    assert outcome.unique_candidate_count == 1
    assert outcome.duplicate_candidate_count == 1


class ParallelDetailWorker(FakeWorker):
    def __init__(self) -> None:
        super().__init__()
        self.started: list[dict[str, object]] = []
        self.both_started = asyncio.Event()
        self.release = asyncio.Event()

    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult:
        del round_no, provider_account_hash
        self.started.append({"page_size": request.page_size, "trace_id": trace_id})
        if len(self.started) == 2:
            self.both_started.set()
        await self.release.wait()
        candidates = []
        snapshots = []
        for index in range(request.page_size):
            resume_id = f"liepin-{trace_id}-{index}"
            raw_payload = {
                "provider_candidate_key_hash": f"hash-{trace_id}-{index}",
                "provider_snapshot_ref": f"artifact://protected/pi-detail/{trace_id}/{index}",
                "safe_summary_ref": f"artifact://public-summary/pi-detail/{trace_id}/{index}",
                "score_evidence_source": "detail_enriched",
                "currentTitle": "数据开发专家",
                "currentCompany": "数据平台公司",
                "workExperienceList": [
                    {
                        "company": "数据平台公司",
                        "title": "数据开发专家",
                        "summary": f"{request.keyword_query} detail resume {index}",
                    }
                ],
                "skills": list(request.query_terms),
            }
            candidates.append(
                ResumeCandidate(
                    resume_id=resume_id,
                    source_resume_id=None,
                    snapshot_sha256=sha256_json(raw_payload),
                    dedup_key=resume_id,
                    search_text=f"{request.keyword_query} detail resume {index}",
                    raw=raw_payload,
                )
            )
            snapshots.append(
                ProviderSnapshot(
                    provider_name="liepin",
                    payload_kind="detail",
                    raw_payload=raw_payload,
                    normalized_text=f"{request.keyword_query} detail resume {index}",
                    provider_subject_id=str(raw_payload["provider_candidate_key_hash"]),
                    provider_listing_id=None,
                    synthetic_candidate_fingerprint=resume_id,
                    identity_confidence="provider_subject_id",
                    extraction_source="test",
                    extractor_version="pi-agent-liepin-detail-v1",
                    pii_classification="no_direct_contact",
                    retention_policy="provider_snapshot_30d",
                    access_scope="local_run_only",
                    redaction_state="redacted",
                    score_evidence_source="detail_enriched",
                )
            )
        return SearchResult(
            candidates=candidates,
            provider_snapshots=snapshots,
            raw_candidate_count=len(candidates),
            exhausted=True,
        )


async def _run_parallel_liepin_bundle(worker: ParallelDetailWorker) -> None:
    task = asyncio.create_task(
        run_liepin_logical_query_bundle(
            settings=make_settings(),
            runtime_run_id="run-1",
            source_plan_id="run-1:source:1:liepin",
            job_title="AI Agent Engineer",
            jd="Build LangGraph and RAG systems.",
            notes="Prefer evaluation.",
            requirement_sheet=_requirement_sheet(),
            logical_queries=(
                LogicalQueryDispatch(
                    round_no=1,
                    query_role="exploit",
                    lane_type="exploit",
                    query_terms=("LangGraph", "RAG"),
                    keyword_query="LangGraph RAG",
                    query_instance_id="q-exploit",
                    query_fingerprint="fingerprint-exploit",
                    term_group_key="term-group-langgraph-rag",
                    requested_count=7,
                    source_plan_version="7",
                ),
                LogicalQueryDispatch(
                    round_no=1,
                    query_role="explore",
                    lane_type="generic_explore",
                    query_terms=("agent workflow", "evaluation"),
                    keyword_query="agent workflow evaluation",
                    query_instance_id="q-explore",
                    query_fingerprint="fingerprint-explore",
                    term_group_key="term-group-agent-evaluation",
                    requested_count=3,
                    source_plan_version="7",
                ),
            ),
            source_budget_policy=RuntimeSourceBudgetPolicy.defaults(),
            liepin_context={"backend_mode": "external_http"},
            worker_client=worker,
        )
    )

    await asyncio.wait_for(worker.both_started.wait(), timeout=1)
    assert sorted(item["page_size"] for item in worker.started) == [3, 7]
    assert not task.done()
    worker.release.set()
    result = await asyncio.wait_for(task, timeout=1)
    assert result.status == "completed"
    assert len(result.candidate_store_updates) == 10


def test_liepin_logical_query_bundle_runs_independent_child_agents_in_parallel() -> None:
    asyncio.run(_run_parallel_liepin_bundle(ParallelDetailWorker()))


async def _run_opencli_liepin_bundle_serially(worker: ParallelDetailWorker) -> None:
    task = asyncio.create_task(
        run_liepin_logical_query_bundle(
            settings=make_settings(),
            runtime_run_id="run-1",
            source_plan_id="run-1:source:1:liepin",
            job_title="AI Agent Engineer",
            jd="Build LangGraph and RAG systems.",
            notes="Prefer evaluation.",
            requirement_sheet=_requirement_sheet(),
            logical_queries=(
                LogicalQueryDispatch(
                    round_no=1,
                    query_role="exploit",
                    lane_type="exploit",
                    query_terms=("LangGraph", "RAG"),
                    keyword_query="LangGraph RAG",
                    query_instance_id="q-exploit",
                    query_fingerprint="fingerprint-exploit",
                    term_group_key="term-group-langgraph-rag",
                    requested_count=7,
                    source_plan_version="7",
                ),
                LogicalQueryDispatch(
                    round_no=1,
                    query_role="explore",
                    lane_type="generic_explore",
                    query_terms=("agent workflow", "evaluation"),
                    keyword_query="agent workflow evaluation",
                    query_instance_id="q-explore",
                    query_fingerprint="fingerprint-explore",
                    term_group_key="term-group-agent-evaluation",
                    requested_count=3,
                    source_plan_version="7",
                ),
            ),
            source_budget_policy=RuntimeSourceBudgetPolicy.defaults(),
            liepin_context={"backend_mode": "opencli"},
            worker_client=worker,
        )
    )

    while not worker.started and not task.done():
        await asyncio.sleep(0)
    if task.done():
        await task
    await asyncio.sleep(0.01)
    assert len(worker.started) == 1
    worker.release.set()
    result = await asyncio.wait_for(task, timeout=1)
    assert [item["page_size"] for item in worker.started] == [7, 3]
    assert result.status == "completed"
    assert len(result.candidate_store_updates) == 10


def test_liepin_opencli_logical_query_bundle_runs_child_agents_serially() -> None:
    asyncio.run(_run_opencli_liepin_bundle_serially(ParallelDetailWorker()))


class SingleDetailWorker(FakeWorker):
    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult:
        self.search_calls.append(
            {
                "request": request,
                "provider_context": request.provider_context,
                "page_size": request.page_size,
                "round_no": round_no,
                "trace_id": trace_id,
                "provider_account_hash": provider_account_hash,
            }
        )
        raw_payload = {
            "provider_candidate_key_hash": "hash-detail-1",
            "provider_snapshot_ref": "artifact://protected/pi-detail/run-1/1",
            "safe_summary_ref": "artifact://public-summary/pi-detail/run-1/1",
            "score_evidence_source": "detail_enriched",
            "currentTitle": "AI Agent Engineer",
            "currentCompany": "Agent Platform",
            "workExperienceList": [
                {
                    "company": "Agent Platform",
                    "title": "AI Agent Engineer",
                    "summary": "LangGraph RAG detail resume",
                }
            ],
            "skills": ["LangGraph", "RAG"],
        }
        candidate = ResumeCandidate(
            resume_id="liepin-detail-1",
            source_resume_id=None,
            snapshot_sha256=sha256_json(raw_payload),
            dedup_key="liepin-detail-1",
            search_text="LangGraph RAG detail resume",
            raw=raw_payload,
        )
        snapshot = ProviderSnapshot(
            provider_name="liepin",
            payload_kind="detail",
            raw_payload=raw_payload,
            normalized_text="LangGraph RAG detail resume",
            provider_subject_id="hash-detail-1",
            provider_listing_id=None,
            synthetic_candidate_fingerprint="liepin-detail-1",
            identity_confidence="provider_subject_id",
            extraction_source="test",
            extractor_version="pi-agent-liepin-detail-v1",
            pii_classification="no_direct_contact",
            retention_policy="provider_snapshot_30d",
            access_scope="local_run_only",
            redaction_state="redacted",
            score_evidence_source="detail_enriched",
        )
        return SearchResult(candidates=[candidate], provider_snapshots=[snapshot], raw_candidate_count=1)


def test_liepin_detail_backed_lane_returns_raw_candidates_without_normalized_updates() -> None:
    worker = SingleDetailWorker()
    result = asyncio.run(
        run_liepin_source_lane(
            settings=make_settings(),
            request=RuntimeSourceLaneRequest(
                source="liepin",
                lane_mode="card",
                job_title="AI Agent Engineer",
                jd="Build LangGraph and RAG systems.",
                notes="Prefer evaluation.",
                requirement_sheet=_requirement_sheet(),
                source_query_terms=("LangGraph", "RAG"),
                logical_query_instance_id="q-exploit",
                logical_query_role="exploit",
                logical_keyword_query="LangGraph RAG",
                logical_requested_count=7,
                logical_provider_scan_limit=30,
                source_context={"liepin_fetch_strategy": "detail_backed_resume_search"},
            ),
            worker_client=worker,
        )
    )

    assert result.status == "completed"
    assert result.candidate_store_updates
    assert result.normalized_store_updates == {}


def test_liepin_detail_backed_opencli_candidates_populate_candidate_refs() -> None:
    class OpenCliDetailWorker(FakeWorker):
        async def search(
            self,
            request: SearchRequest,
            *,
            round_no: int,
            trace_id: str,
            provider_account_hash: str | None = None,
        ) -> SearchResult:
            del request, round_no, trace_id, provider_account_hash
            raw_payload = {
                "provider_candidate_key_hash": "stable-opencli-provider-hash",
                "provider_snapshot_ref": "artifact://protected/liepin-opencli/raw/run-1/1.json",
                "normalized_snapshot_ref": "artifact://protected/liepin-opencli/normalized/run-1/1.json",
                "score_evidence_source": "detail_enriched",
                "currentTitle": "数据开发专家",
                "currentCompany": "数据平台公司",
                "workExperienceList": [
                    {
                        "company": "数据平台公司",
                        "title": "数据开发专家",
                        "summary": "数据平台 Python resume",
                    }
                ],
                "skills": ["Python"],
            }
            candidate = ResumeCandidate(
                resume_id="liepin-opencli-1",
                source_resume_id="stable-opencli-provider-hash",
                snapshot_sha256=sha256_json(raw_payload),
                dedup_key="liepin-opencli-1",
                search_text="数据平台 Python resume",
                raw=raw_payload,
            )
            snapshot = ProviderSnapshot(
                provider_name="liepin",
                payload_kind="detail",
                raw_payload=raw_payload,
                normalized_text="数据平台 Python resume",
                provider_subject_id="stable-opencli-provider-hash",
                provider_listing_id=None,
                synthetic_candidate_fingerprint="liepin-opencli-1",
                identity_confidence="provider_subject_id",
                extraction_source="dom_fallback",
                extractor_version="liepin-opencli-deterministic-v1",
                pii_classification="no_direct_contact",
                retention_policy="provider_snapshot_7d",
                access_scope="local_run_only",
                redaction_state="raw_provider_payload",
                score_evidence_source="detail_enriched",
            )
            return SearchResult(candidates=[candidate], provider_snapshots=[snapshot], raw_candidate_count=1)

    result = asyncio.run(
        run_liepin_source_lane(
            settings=make_settings(),
            request=RuntimeSourceLaneRequest(
                source="liepin",
                lane_mode="card",
                job_title="数据开发专家",
                jd="负责数据平台建设",
                notes="Python",
                requirement_sheet=_requirement_sheet(),
                source_query_terms=("数据开发", "Python"),
                logical_query_instance_id="q-exploit",
                logical_query_role="exploit",
                logical_keyword_query="数据开发 Python",
                logical_requested_count=2,
                logical_provider_scan_limit=10,
            ),
            worker_client=OpenCliDetailWorker(),
        )
    )

    assert result.status == "completed"
    assert result.candidate_store_updates["liepin-opencli-1"].search_text == "数据平台 Python resume"
    assert result.source_evidence_updates[0].evidence_level == "detail"
    assert result.source_evidence_updates[0].provider_snapshot_ref == (
        "artifact://protected/liepin-opencli/raw/run-1/1.json"
    )
    assert result.normalized_store_updates == {}


def test_liepin_backend_posture_records_worker_modes_without_removed_fallback() -> None:
    assert liepin_backend_posture(make_settings(liepin_worker_mode="opencli")) == {
        "backend_mode": "opencli",
        "reason": "opencli",
    }
    assert liepin_backend_posture(
        make_settings(liepin_worker_mode="external_http", liepin_worker_base_url="http://127.0.0.1:8123")
    ) == {
        "backend_mode": "external_http",
        "reason": "external_http",
    }
    assert liepin_backend_posture(
        make_settings(liepin_worker_mode="fake_fixture", liepin_allow_fake_fixture_worker=True)
    ) == {"backend_mode": "fake_fixture", "reason": "explicit_test_fixture"}
    assert liepin_backend_posture(make_settings(liepin_worker_mode="disabled")) == {
        "backend_mode": "blocked",
        "reason": "no_live_action_backend",
    }


def test_pi_failure_codes_preserve_opencli_safe_reason_codes() -> None:
    for reason_code in (
        "liepin_opencli_extension_disconnected",
        "liepin_opencli_login_required",
        "liepin_opencli_risk_page",
        "liepin_opencli_detail_not_opened",
        "liepin_opencli_filter_unapplied",
        "liepin_opencli_search_not_ready",
        "liepin_opencli_results_not_ready",
        "liepin_opencli_stale_ref",
        "liepin_opencli_selector_not_found",
        "liepin_opencli_selector_ambiguous",
        "liepin_opencli_target_not_found",
        "liepin_opencli_daemon_not_running",
        "liepin_opencli_daemon_stale",
        "liepin_opencli_removed_config",
    ):
        assert runtime_safe_reason_code_from_worker_failure_code(reason_code) == reason_code


@pytest.mark.parametrize(
    "reason_code",
    [
        "liepin_opencli_search_not_ready",
        "liepin_opencli_results_not_ready",
        "liepin_opencli_removed_config",
    ],
)
def test_opencli_backend_unavailable_reasons_map_to_source_lane_backend_unavailable(reason_code: str) -> None:
    assert (
        LIEPIN_SOURCE_LANE_REASON_CODE_MAP[reason_code]
        == "source_browser_backend_unavailable"
    )


def test_liepin_runtime_lane_uses_provider_adapter_context_and_public_payload_is_safe() -> None:
    worker = FakeWorker()
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
        source_context={
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


def test_liepin_runtime_lane_preserves_pi_provider_hash_and_artifact_refs_in_evidence() -> None:
    class PiMappedWorker(FakeWorker):
        async def search(
            self,
            request: SearchRequest,
            *,
            round_no: int,
            trace_id: str,
            provider_account_hash: str | None = None,
        ) -> SearchResult:
            self.search_calls.append(
                {
                    "provider_context": request.provider_context,
                    "round_no": round_no,
                    "trace_id": trace_id,
                    "provider_account_hash": provider_account_hash,
                }
            )
            raw = {
                "provider_candidate_key_hash": "stable-pi-provider-hash",
                "provider_snapshot_ref": "artifact://protected/pi-card/run-1/1",
                "safe_summary_ref": "artifact://public-summary/pi-card/run-1/1",
                "safe_card_summary": {
                    "current_or_recent_title": "Backend Engineer",
                    "skill_tags": ["FastAPI", "ranking"],
                },
            }
            candidate = ResumeCandidate(
                resume_id="pi-fingerprint-resume",
                source_resume_id=None,
                snapshot_sha256=sha256_json(raw),
                dedup_key="pi-fingerprint-resume",
                search_text="FastAPI ranking backend engineer.",
                raw=raw,
            )
            snapshot = ProviderSnapshot(
                provider_name="liepin",
                payload_kind="card",
                raw_payload=raw,
                normalized_text="FastAPI ranking backend engineer.",
                provider_subject_id=None,
                provider_listing_id=None,
                synthetic_candidate_fingerprint="pi-fingerprint-resume",
                identity_confidence="synthetic_fingerprint",
                extraction_source="dom_fallback",
                extractor_version="pi-agent-liepin-card-v1",
                pii_classification="no_direct_contact",
                retention_policy="provider_snapshot_30d",
                access_scope="local_run_only",
                redaction_state="redacted",
                score_evidence_source="card_only",
            )
            return SearchResult(candidates=[candidate], provider_snapshots=[snapshot], raw_candidate_count=1)

    request = RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="card",
        job_title="Backend Engineer",
        jd="FastAPI ranking",
        notes=None,
        requirement_sheet=_requirement_sheet(),
        runtime_run_id="runtime-run-1",
        source_lane_run_id="lane-run-1",
        source_query_terms=("FastAPI", "ranking"),
        source_context={"provider_account_hash": "acct_hash_123"},
    )

    result = asyncio.run(run_liepin_source_lane(settings=make_settings(), request=request, worker_client=PiMappedWorker()))

    evidence = result.source_evidence_updates[0]
    assert evidence.provider_candidate_key_hash == "stable-pi-provider-hash"
    assert evidence.provider_snapshot_ref == "artifact://protected/pi-card/run-1/1"
    assert evidence.safe_summary_ref == "artifact://public-summary/pi-card/run-1/1"
    assert result.detail_recommendations[0].provider_candidate_key_hash == "stable-pi-provider-hash"
    assert result.detail_recommendations[0].provider_snapshot_ref == "artifact://protected/pi-card/run-1/1"
    assert result.detail_recommendations[0].safe_summary_ref == "artifact://public-summary/pi-card/run-1/1"


def test_liepin_runtime_card_lane_passes_compliance_gate_to_live_adapter() -> None:
    worker = FakeWorker()
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
        source_context={
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


def test_liepin_card_policy_keeps_provider_rank_primary_after_hard_filters_and_budget() -> None:
    class MultiCandidateWorker(FakeWorker):
        async def search(
            self,
            request: SearchRequest,
            *,
            round_no: int,
            trace_id: str,
            provider_account_hash: str | None = None,
        ) -> SearchResult:
            self.search_calls.append(
                {
                    "request": request,
                    "provider_context": request.provider_context,
                    "round_no": round_no,
                    "trace_id": trace_id,
                    "provider_account_hash": provider_account_hash,
                }
            )
            rows = [
                (
                    "rank-1",
                    "provider-rank-1",
                    "FastAPI ranking distributed systems.",
                    {
                        "current_or_recent_title": "Backend Engineer",
                        "current_or_recent_company": "Distributed Systems",
                        "skill_tags": ["FastAPI", "ranking"],
                    },
                ),
                (
                    "rank-2",
                    "provider-rank-2",
                    "FastAPI ranking Python services.",
                    {
                        "current_or_recent_title": "Python Engineer",
                        "current_or_recent_company": "Python Services",
                        "skill_tags": ["FastAPI", "ranking"],
                    },
                ),
                (
                    "rank-3-obvious-mismatch",
                    "provider-rank-3",
                    "retail sales store manager.",
                    {"current_or_recent_title": "Store Manager"},
                ),
                (
                    "rank-4-over-budget",
                    "provider-rank-4",
                    "FastAPI ranking platform reliability.",
                    {
                        "current_or_recent_title": "Backend Engineer",
                        "current_or_recent_company": "Platform Reliability",
                        "skill_tags": ["FastAPI", "ranking"],
                    },
                ),
            ]
            candidates = []
            snapshots = []
            for resume_id, provider_id, text, safe_card_summary in rows:
                raw_payload = {"candidateId": provider_id, "text": text}
                candidates.append(
                    ResumeCandidate(
                        resume_id=resume_id,
                        source_resume_id=provider_id,
                        snapshot_sha256=sha256_json(raw_payload),
                        dedup_key=resume_id,
                        search_text=text,
                        raw={"safe_card_summary": safe_card_summary},
                    )
                )
                snapshots.append(
                    ProviderSnapshot(
                        provider_name="liepin",
                        payload_kind="card",
                        raw_payload=raw_payload,
                        normalized_text=text,
                        provider_subject_id=provider_id,
                        provider_listing_id=None,
                        synthetic_candidate_fingerprint=resume_id,
                        identity_confidence="provider_subject_id",
                        extraction_source="test",
                        extractor_version="test",
                        pii_classification="no_direct_contact",
                        retention_policy="provider_snapshot_7d",
                        access_scope="local_run_only",
                        redaction_state="raw_provider_payload",
                        score_evidence_source="card_only",
                    )
                )
            return SearchResult(
                candidates=candidates,
                provider_snapshots=snapshots,
                raw_candidate_count=len(candidates),
                exhausted=True,
            )

    worker = MultiCandidateWorker()
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
        source_budget_policy=RuntimeSourceBudgetPolicy(
            page_size=5,
            max_cards=5,
            max_detail_recommendations=2,
        ),
        source_context={"provider_account_hash": "acct_hash_123"},
    )

    result = asyncio.run(run_liepin_source_lane(settings=make_settings(), request=request, worker_client=worker))

    provider_request = worker.search_calls[0]["request"]
    assert provider_request.page_size == 5
    assert provider_request.provider_context["liepin_card_page_size"] == "5"
    assert provider_request.provider_context["liepin_max_cards"] == "5"
    assert provider_request.provider_context["liepin_max_pages"] == "1"
    assert [item.candidate_resume_id for item in result.detail_recommendations] == ["rank-1", "rank-2"]
    assert [item.provider_rank for item in result.detail_recommendations] == [1, 2]
    assert [item.card_policy_rank for item in result.detail_recommendations] == [1, 2]
    assert {item.hard_filter_status for item in result.detail_recommendations} == {"hard_filter_passed"}
    assert {item.budget_reason_code for item in result.detail_recommendations} == {"within_run_detail_budget"}
    assert all("safe_reason" not in item.to_public_payload() for item in result.detail_recommendations)
    assert result.events[-1].safe_counts == {"detail_recommendations": 2}


def test_liepin_runtime_card_policy_ignores_candidate_search_text() -> None:
    class SearchTextOnlyWorker(FakeWorker):
        async def search(
            self,
            request: SearchRequest,
            *,
            round_no: int,
            trace_id: str,
            provider_account_hash: str | None = None,
        ) -> SearchResult:
            del request, round_no, trace_id, provider_account_hash
            raw_payload = {"candidateId": "search-text-only"}
            candidate = ResumeCandidate(
                resume_id="search-text-only",
                source_resume_id="search-text-only",
                snapshot_sha256=sha256_json(raw_payload),
                dedup_key="search-text-only",
                search_text="FastAPI ranking Backend Engineer SEARCH_TEXT_SENTINEL",
                raw={"safe_card_summary": {"current_or_recent_title": "Store Manager"}},
            )
            snapshot = ProviderSnapshot(
                provider_name="liepin",
                payload_kind="card",
                raw_payload=raw_payload,
                normalized_text=candidate.search_text,
                provider_subject_id="search-text-only",
                provider_listing_id=None,
                synthetic_candidate_fingerprint="search-text-only",
                identity_confidence="provider_subject_id",
                extraction_source="test",
                extractor_version="test",
                pii_classification="no_direct_contact",
                retention_policy="provider_snapshot_7d",
                access_scope="local_run_only",
                redaction_state="raw_provider_payload",
                score_evidence_source="card_only",
            )
            return SearchResult(candidates=[candidate], provider_snapshots=[snapshot], raw_candidate_count=1)

    request = RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="card",
        job_title="Backend Engineer",
        jd="FastAPI ranking",
        notes=None,
        requirement_sheet=_requirement_sheet(),
        runtime_run_id="runtime-run-1",
        source_lane_run_id="lane-run-1",
        source_query_terms=("FastAPI", "ranking"),
        source_context={"provider_account_hash": "acct_hash_123"},
    )

    result = asyncio.run(run_liepin_source_lane(settings=make_settings(), request=request, worker_client=SearchTextOnlyWorker()))

    assert result.detail_recommendations == ()


def test_liepin_card_summary_for_candidate_ignores_candidate_search_text() -> None:
    sentinel = "SENTINEL raw-ish visible_text normalized_card_text fullText"
    candidate = ResumeCandidate(
        resume_id="structured-summary-only",
        source_resume_id="structured-summary-only",
        snapshot_sha256=sha256_json({"candidateId": "structured-summary-only"}),
        dedup_key="structured-summary-only",
        search_text=f"Backend Engineer FastAPI ranking {sentinel}",
        raw={
            "safe_card_summary": {
                "current_or_recent_company": "结构化科技",
                "current_or_recent_title": "AI平台工程师",
                "skill_tags": ["Python", "RAG"],
                "experience_preview": [
                    {
                        "company": "结构化科技",
                        "title": "AI平台工程师",
                        "date_range": "2021.04-至今",
                        "duration": "3年",
                    }
                ],
                "education_preview": [
                    {
                        "school": "齐齐哈尔大学",
                        "major": "计算机科学与技术",
                        "degree": "本科",
                    }
                ],
            }
        },
    )

    summary = runtime_lane._card_summary_for_candidate(candidate=candidate, provider_rank=7)

    assert summary.provider_rank == 7
    assert summary.current_or_recent_company == "结构化科技"
    assert summary.current_or_recent_title == "AI平台工程师"
    assert summary.skill_tags == ("Python", "RAG")
    assert summary.experience_preview == (
        {
            "company": "结构化科技",
            "title": "AI平台工程师",
            "date_range": "2021.04-至今",
            "duration": "3年",
        },
    )
    assert summary.education_preview == (
        {"school": "齐齐哈尔大学", "major": "计算机科学与技术", "degree": "本科"},
    )
    assert sentinel not in repr(summary)


def test_liepin_runtime_card_summary_filters_preview_mappings_to_allowed_scalars() -> None:
    candidate = ResumeCandidate(
        resume_id="preview-filtering",
        source_resume_id="preview-filtering",
        snapshot_sha256=sha256_json({"candidateId": "preview-filtering"}),
        dedup_key="preview-filtering",
        search_text="FastAPI ranking Backend Engineer SEARCH_TEXT_SENTINEL",
        raw={
            "safe_card_summary": {
                "experience_preview": [
                    {
                        "company": "  Acme  ",
                        "title": "Backend Engineer",
                        "date_range": ["2021-2024"],
                        "duration": {"months": 6},
                        "is_current": True,
                        "visible_text": "FastAPI ranking SEARCH_TEXT_SENTINEL",
                    }
                ],
                "education_preview": [
                    {
                        "school": "  Qiqihar University  ",
                        "major": object(),
                        "degree": "本科",
                        "recruitment_type": "统招",
                        "date_range": {"raw": "2017-2021"},
                        "normalized_card_text": "FastAPI ranking SEARCH_TEXT_SENTINEL",
                    }
                ],
            }
        },
    )

    summary = runtime_lane._card_summary_for_candidate(candidate=candidate, provider_rank=1)

    assert summary.experience_preview == (
        {"company": "Acme", "title": "Backend Engineer", "is_current": True},
    )
    assert summary.education_preview == (
        {"school": "Qiqihar University", "degree": "本科", "recruitment_type": "统招"},
    )


def test_liepin_runtime_lane_normalizes_blocked_worker_error_codes() -> None:
    class BlockedWorker(FakeWorker):
        async def search(
            self,
            request: SearchRequest,
            *,
            round_no: int,
            trace_id: str,
            provider_account_hash: str | None = None,
        ) -> SearchResult:
            del request, round_no, trace_id, provider_account_hash
            raise LiepinWorkerModeError("raw risk_control secret-token", code="risk_control")

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
        source_context={"provider_account_hash": "acct_hash_123"},
    )

    result = asyncio.run(
        run_liepin_source_lane(settings=make_settings(), request=request, worker_client=BlockedWorker())
    )

    assert result.status == "blocked"
    assert result.blocked_reason_code == "blocked_compliance"
    assert result.stop_reason_code == "blocked_compliance"
    payload = repr(result.to_public_payload())
    assert "risk_control" not in payload
    assert "secret-token" not in payload


def test_liepin_runtime_lane_records_safe_worker_exception_summary() -> None:
    class FailedWorker(FakeWorker):
        async def search(
            self,
            request: SearchRequest,
            *,
            round_no: int,
            trace_id: str,
            provider_account_hash: str | None = None,
        ) -> SearchResult:
            del request, round_no, trace_id, provider_account_hash
            raise LiepinWorkerModeError(
                "Liepin OpenCLI resume search blocked.",
                code="failed_provider_error",
            )

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
        source_context={"provider_account_hash": "acct_hash_123"},
    )

    result = asyncio.run(
        run_liepin_source_lane(settings=make_settings(), request=request, worker_client=FailedWorker())
    )

    assert result.status == "blocked"
    assert result.blocked_reason_code == "failed_provider_error"
    assert result.safe_error_summary == (
        "LiepinWorkerModeError: failed_provider_error; Liepin OpenCLI resume search blocked."
    )


def test_liepin_runtime_lane_preserves_partial_worker_cards_with_safe_reason() -> None:
    class PartialWorker(FakeWorker):
        async def search(
            self,
            request: SearchRequest,
            *,
            round_no: int,
            trace_id: str,
            provider_account_hash: str | None = None,
        ) -> SearchResult:
            del request, round_no, trace_id, provider_account_hash
            raw_payload = {"candidateId": "partial-provider-id"}
            partial = SearchResult(
                candidates=[
                    ResumeCandidate(
                        resume_id="partial-candidate-1",
                        source_resume_id="partial-provider-id",
                        snapshot_sha256=sha256_json(raw_payload),
                        dedup_key="partial-dedup",
                        search_text="FastAPI ranking backend systems.",
                        raw={},
                    )
                ],
                provider_snapshots=[
                    ProviderSnapshot(
                        provider_name="liepin",
                        payload_kind="card",
                        raw_payload=raw_payload,
                        normalized_text="FastAPI ranking backend systems.",
                        provider_subject_id="partial-provider-id",
                        provider_listing_id=None,
                        synthetic_candidate_fingerprint="partial-dedup",
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
                raw_candidate_count=3,
            )
            raise LiepinWorkerPartialSearchError(
                "page_timeout raw transport text",
                code="page_timeout",
                partial_search_result=partial,
                cards_collected=1,
            )

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
        source_context={"provider_account_hash": "acct_hash_123"},
    )

    result = asyncio.run(
        run_liepin_source_lane(settings=make_settings(), request=request, worker_client=PartialWorker())
    )

    assert result.status == "partial"
    assert result.stop_reason_code == "partial_timeout"
    assert result.blocked_reason_code is None
    assert list(result.candidate_store_updates) == ["partial-candidate-1"]
    assert result.events[0].event_type == "source_lane_partial"
    payload = repr(result.to_public_payload())
    assert "partial_timeout" in payload
    assert "page_timeout" not in payload
    assert "raw transport text" not in payload


def test_worker_failure_codes_map_to_runtime_safe_reason_codes() -> None:
    assert runtime_safe_reason_code_from_worker_failure_code("login_expired") == "blocked_login_required"
    assert runtime_safe_reason_code_from_worker_failure_code("verification_required") == "blocked_compliance"
    assert runtime_safe_reason_code_from_worker_failure_code("risk_control") == "blocked_compliance"
    assert runtime_safe_reason_code_from_worker_failure_code("provider_connection_locked") == "blocked_backend_unavailable"
    assert runtime_safe_reason_code_from_worker_failure_code("page_timeout") == "failed_provider_error"
    assert (
        runtime_safe_reason_code_from_worker_failure_code("page_timeout", cards_collected=True)
        == "partial_timeout"
    )
    assert runtime_safe_reason_code_from_worker_failure_code("selector_drift") == "failed_provider_error"
    assert runtime_safe_reason_code_from_worker_failure_code("extraction_failure") == "failed_provider_error"
    assert runtime_safe_reason_code_from_worker_failure_code("blocked_backend_unavailable") == "blocked_backend_unavailable"
    assert runtime_safe_reason_code_from_worker_failure_code("blocked_permission_required") == "blocked_compliance"
    assert runtime_safe_reason_code_from_worker_failure_code("partial_timeout", cards_collected=True) == "partial_timeout"
    assert runtime_safe_reason_code_from_worker_failure_code("unknown") == "failed_provider_error"


def test_liepin_runtime_lane_builds_live_store_for_opencli(monkeypatch, tmp_path) -> None:
    captured_stores: list[object] = []

    class FakeProvider:
        def __init__(self, settings, *, worker_client=None, worker_search_started_callback=None, store=None):
            del settings, worker_client, worker_search_started_callback
            captured_stores.append(store)

        async def search(self, request: SearchRequest, *, round_no: int, trace_id: str) -> SearchResult:
            del request, round_no, trace_id
            return SearchResult(candidates=[], provider_snapshots=[], raw_candidate_count=0, exhausted=True)

    monkeypatch.setattr(runtime_lane, "LiepinProviderAdapter", FakeProvider)
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
        source_context={"provider_account_hash": "acct_hash_123"},
    )
    settings = make_settings(
        workspace_root=str(tmp_path),
        liepin_worker_mode="opencli",
        liepin_connector_db_path=str(tmp_path / "liepin.sqlite3"),
    )

    asyncio.run(run_liepin_source_lane(settings=settings, request=request, worker_client=FakeWorker()))

    assert captured_stores
    assert captured_stores[0].__class__.__name__ == "LiepinStore"


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
        requirement_sheet=_requirement_sheet(),
        runtime_run_id="runtime-run-1",
        source_lane_run_id="lane-detail-1",
            approved_detail_lease=RuntimeApprovedDetailLease(
                lease_ref="lease://detail/1",
                source="liepin",
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
        source_context={
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
        requirement_sheet=_requirement_sheet(),
        runtime_run_id="runtime-run-1",
        source_lane_run_id="lane-detail-1",
        approved_detail_lease_ref="lease://caller-supplied-only",
    )

    result = asyncio.run(run_liepin_source_lane(settings=make_settings(), request=request, worker_client=FakeWorker()))

    assert result.status == "blocked"
    assert result.blocked_reason_code == "blocked_approval_missing"


def test_liepin_runtime_detail_lane_rejects_lease_bound_to_different_run_before_provider_call() -> None:
    worker = FakeWorker()
    request = RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="detail",
        job_title="Backend Engineer",
        jd="FastAPI retrieval",
        notes=None,
        requirement_sheet=_requirement_sheet(),
        runtime_run_id="runtime-run-current",
        source_plan_id="plan-current",
        source_lane_run_id="lane-detail-current",
        approved_detail_lease=RuntimeApprovedDetailLease(
            lease_ref="lease://detail/1",
            lease_id="lease-1",
            runtime_run_id="runtime-run-other",
            source_plan_id="plan-current",
            source_lane_run_id="lane-card-current",
            source="liepin",
            recommendation_id="rec-1",
            source_evidence_id="evidence-1",
            request_id="detail-request-1",
            ledger_id="detail-ledger-1",
            candidate_evidence_id="evidence-1",
            candidate_resume_id="candidate-1",
            provider_candidate_key_hash="provider-hash-1",
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
        ),
    )

    result = asyncio.run(run_liepin_source_lane(settings=make_settings(), request=request, worker_client=worker))

    assert result.status == "blocked"
    assert result.blocked_reason_code == "blocked_approval_missing"
    assert worker.search_calls == []
