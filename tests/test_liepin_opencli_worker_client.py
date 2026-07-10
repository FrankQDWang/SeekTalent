from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest

from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.providers.liepin.detail_open_claims import DetailOpenClaimLedger
from seektalent.providers.liepin.opencli_worker_client import LiepinOpenCliWorkerClient
from seektalent.providers.liepin.worker_contracts import (
    LiepinResumeSearchResponse,
    LiepinWorkerModeError,
    LiepinWorkerPartialSearchError,
    SessionStatus,
)


@dataclass
class FakeRetriever:
    calls: list[object]
    ready_calls: int = 0
    session_status_calls: list[dict[str, object]] | None = None

    def search_resumes(self, request):
        self.calls.append(request)
        return LiepinResumeSearchResponse(
            resumes=[],
            exhausted=True,
            requestPayload={"backend": "opencli"},
            rawCandidateCount=3,
        )

    def ensure_ready(self) -> None:
        self.ready_calls += 1

    def session_status(
        self,
        *,
        connection_id: str,
        provider_account_hash: str | None,
    ) -> SessionStatus:
        if self.session_status_calls is None:
            self.session_status_calls = []
        self.session_status_calls.append(
            {
                "connection_id": connection_id,
                "provider_account_hash": provider_account_hash,
            }
        )
        return SessionStatus(
            connectionId=connection_id,
            status="ready",
            providerAccountHash="liepin-opencli-local-browser-profile",
        )


def test_opencli_worker_forwards_runtime_request_to_deterministic_retriever() -> None:
    retriever = FakeRetriever(calls=[])
    client = LiepinOpenCliWorkerClient(
        retriever=retriever,
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    result = asyncio.run(
        client.search(
            SearchRequest(
                query_terms=["数据开发", "Python"],
                query_role="primary",
                keyword_query="数据开发 Python",
                adapter_notes=[],
                runtime_constraints=[],
                fetch_mode="detail",
                page_size=2,
                provider_context={
                    "liepin_requirement_sheet_json": '{"job_title":"数据开发专家"}',
                    "liepin_max_cards": "10",
                    "liepin_max_pages": "1",
                },
            ),
            round_no=1,
            trace_id="run-1",
        )
    )

    assert result.raw_candidate_count == 3
    assert retriever.calls[0].target_resumes == 2
    assert retriever.calls[0].max_cards == 10
    assert retriever.calls[0].requirement_sheet == {"job_title": "数据开发专家"}


def test_opencli_worker_private_claim_route_forwards_same_ledger_and_logical_provenance() -> None:
    class ClaimAwareRetriever(FakeRetriever):
        def __init__(self) -> None:
            super().__init__(calls=[])
            self.private_contexts: list[object] = []

        def search_resumes(self, request):
            raise AssertionError("private claim route must not use the normal retriever method")

        def _search_resumes_with_detail_open_claim_context(self, request, *, detail_open_claim_context):
            self.calls.append(request)
            self.private_contexts.append(detail_open_claim_context)
            return LiepinResumeSearchResponse(
                resumes=[],
                exhausted=True,
                requestPayload={"backend": "opencli"},
                rawCandidateCount=3,
            )

    retriever = ClaimAwareRetriever()
    ledger = DetailOpenClaimLedger({})
    client = LiepinOpenCliWorkerClient(
        retriever=retriever,
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    result = asyncio.run(
        client.search_with_detail_open_claim_ledger(
            SearchRequest(
                query_terms=["数据开发", "Python"],
                query_role="primary",
                keyword_query="数据开发 Python",
                adapter_notes=[],
                runtime_constraints=[],
                fetch_mode="detail",
                page_size=2,
                provider_context={"liepin_requirement_sheet_json": '{"job_title":"数据开发专家"}'},
            ),
            round_no=5,
            trace_id="run-claim-5",
            detail_open_claim_ledger=ledger,
            logical_round_no=5,
            query_instance_id="logical-query-5",
        )
    )

    assert result.raw_candidate_count == 3
    assert len(retriever.private_contexts) == 1
    context = retriever.private_contexts[0]
    assert context.detail_open_claim_ledger is ledger
    assert context.logical_round_no == 5
    assert context.query_instance_id == "logical-query-5"


def test_opencli_worker_search_does_not_block_event_loop() -> None:
    class SlowRetriever(FakeRetriever):
        def search_resumes(self, request):
            time.sleep(0.08)
            return super().search_resumes(request)

    client = LiepinOpenCliWorkerClient(
        retriever=SlowRetriever(calls=[]),
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    async def run_search_with_probe() -> float:
        start = time.perf_counter()
        task = asyncio.create_task(
            client.search(
                SearchRequest(
                    query_terms=["数据开发", "Python"],
                    query_role="primary",
                    keyword_query="数据开发 Python",
                    adapter_notes=[],
                    runtime_constraints=[],
                    fetch_mode="detail",
                    page_size=2,
                    provider_context={
                        "liepin_requirement_sheet_json": '{"job_title":"数据开发专家"}',
                    },
                ),
                round_no=1,
                trace_id="run-1",
            )
        )
        await asyncio.sleep(0.01)
        elapsed = time.perf_counter() - start
        await task
        return elapsed

    elapsed = asyncio.run(run_search_with_probe())

    assert elapsed < 0.05


def test_opencli_worker_raises_partial_error_with_captured_candidates() -> None:
    class PartialRetriever(FakeRetriever):
        def search_resumes(self, request):
            response = super().search_resumes(request)
            return response.model_copy(
                update={
                    "request_payload": {
                        "backend": "opencli",
                        "opencliStatus": "partial",
                        "safeReasonCode": "partial_timeout",
                    }
                }
            )

    client = LiepinOpenCliWorkerClient(
        retriever=PartialRetriever(calls=[]),
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    with pytest.raises(LiepinWorkerPartialSearchError) as error:
        asyncio.run(
            client.search(
                SearchRequest(
                    query_terms=["数据开发", "Python"],
                    query_role="primary",
                    keyword_query="数据开发 Python",
                    adapter_notes=[],
                    runtime_constraints=[],
                    fetch_mode="detail",
                    page_size=2,
                    provider_context={
                        "liepin_requirement_sheet_json": '{"job_title":"数据开发专家"}',
                    },
                ),
                round_no=1,
                trace_id="run-1",
            )
        )

    assert error.value.code == "partial_timeout"
    assert error.value.partial_search_result.raw_candidate_count == 3


def test_opencli_worker_blocked_error_preserves_workflow_steps() -> None:
    class BlockedRetriever(FakeRetriever):
        def search_resumes(self, request):
            return LiepinResumeSearchResponse(
                resumes=[],
                exhausted=False,
                requestPayload={
                    "backend": "opencli",
                    "opencliStatus": "blocked",
                    "safeReasonCode": "liepin_opencli_detail_not_opened",
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
                    ],
                },
                rawCandidateCount=0,
            )

    client = LiepinOpenCliWorkerClient(
        retriever=BlockedRetriever(calls=[]),
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    with pytest.raises(LiepinWorkerModeError) as error:
        asyncio.run(
            client.search(
                SearchRequest(
                    query_terms=["数据开发", "Python"],
                    query_role="primary",
                    keyword_query="数据开发 Python",
                    adapter_notes=[],
                    runtime_constraints=[],
                    fetch_mode="detail",
                    page_size=2,
                    provider_context={
                        "liepin_requirement_sheet_json": '{"job_title":"数据开发专家"}',
                    },
                ),
                round_no=1,
                trace_id="run-1",
            )
        )

    assert error.value.code == "liepin_opencli_detail_not_opened"
    partial_result = getattr(error.value, "partial_search_result")
    assert partial_result.request_payload["workflowSteps"][0]["step_name"] == "open_detail"


def test_opencli_worker_drops_removed_cleanup_workflow_steps() -> None:
    removed_step = "cleanup_" + "detail_tabs"
    removed_count = "closed_" + "tabs"

    class BlockedRetriever(FakeRetriever):
        def search_resumes(self, request):
            return LiepinResumeSearchResponse(
                resumes=[],
                exhausted=False,
                requestPayload={
                    "backend": "opencli",
                    "opencliStatus": "blocked",
                    "safeReasonCode": "liepin_opencli_detail_not_opened",
                    "workflowSteps": [
                        {
                            "event_type": "source_workflow_step_completed",
                            "step_name": removed_step,
                            "status": "completed",
                            "safe_counts": {removed_count: 3},
                            "safe_metadata": {},
                            "artifact_refs": [],
                        },
                        {
                            "event_type": "source_workflow_step_failed",
                            "step_name": "open_detail",
                            "status": "failed",
                            "safe_reason_code": "liepin_opencli_detail_not_opened",
                            "safe_counts": {},
                            "safe_metadata": {"rank": 1},
                            "artifact_refs": [],
                        },
                    ],
                },
                rawCandidateCount=0,
            )

    client = LiepinOpenCliWorkerClient(
        retriever=BlockedRetriever(calls=[]),
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    with pytest.raises(LiepinWorkerModeError) as error:
        asyncio.run(
            client.search(
                SearchRequest(
                    query_terms=["数据开发", "Python"],
                    query_role="primary",
                    keyword_query="数据开发 Python",
                    adapter_notes=[],
                    runtime_constraints=[],
                    fetch_mode="detail",
                    page_size=2,
                    provider_context={
                        "liepin_requirement_sheet_json": '{"job_title":"数据开发专家"}',
                    },
                ),
                round_no=1,
                trace_id="run-1",
            )
        )

    partial_result = getattr(error.value, "partial_search_result")
    assert [step["step_name"] for step in partial_result.request_payload["workflowSteps"]] == ["open_detail"]


def test_opencli_worker_drops_removed_cleanup_workflow_count() -> None:
    removed_count = "closed_" + "tabs"

    class BlockedRetriever(FakeRetriever):
        def search_resumes(self, request):
            return LiepinResumeSearchResponse(
                resumes=[],
                exhausted=False,
                requestPayload={
                    "backend": "opencli",
                    "opencliStatus": "blocked",
                    "safeReasonCode": "liepin_opencli_detail_not_opened",
                    "workflowSteps": [
                        {
                            "event_type": "source_workflow_step_failed",
                            "step_name": "open_detail",
                            "status": "failed",
                            "safe_reason_code": "liepin_opencli_detail_not_opened",
                            "safe_counts": {"details_opened": 1, removed_count: 3},
                            "safe_metadata": {"rank": 1},
                            "artifact_refs": [],
                        }
                    ],
                },
                rawCandidateCount=0,
            )

    client = LiepinOpenCliWorkerClient(
        retriever=BlockedRetriever(calls=[]),
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    with pytest.raises(LiepinWorkerModeError) as error:
        asyncio.run(
            client.search(
                SearchRequest(
                    query_terms=["数据开发", "Python"],
                    query_role="primary",
                    keyword_query="数据开发 Python",
                    adapter_notes=[],
                    runtime_constraints=[],
                    fetch_mode="detail",
                    page_size=2,
                    provider_context={
                        "liepin_requirement_sheet_json": '{"job_title":"数据开发专家"}',
                    },
                ),
                round_no=1,
                trace_id="run-1",
            )
        )

    partial_result = getattr(error.value, "partial_search_result")
    assert partial_result.request_payload["workflowSteps"][0]["safe_counts"] == {"details_opened": 1}


def test_opencli_worker_session_status_delegates_to_retriever_probe_without_readiness_check() -> None:
    retriever = FakeRetriever(calls=[])
    client = LiepinOpenCliWorkerClient(
        retriever=retriever,
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    status = asyncio.run(client.session_status(connection_id="liepin-opencli"))

    assert status.status == "ready"
    assert status.provider_account_hash == "liepin-opencli-local-browser-profile"
    assert retriever.ready_calls == 0
    assert retriever.session_status_calls == [
        {
            "connection_id": "liepin-opencli",
            "provider_account_hash": None,
        }
    ]


def test_opencli_worker_session_status_does_not_echo_bound_provider_hash() -> None:
    retriever = FakeRetriever(calls=[])
    client = LiepinOpenCliWorkerClient(
        retriever=retriever,
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    status = asyncio.run(
        client.session_status(
            connection_id="liepin-opencli",
            provider_account_hash="workbench-bound-hash",
        )
    )

    assert status.status == "ready"
    assert status.provider_account_hash == "liepin-opencli-local-browser-profile"
    assert retriever.session_status_calls == [
        {
            "connection_id": "liepin-opencli",
            "provider_account_hash": "workbench-bound-hash",
        }
    ]
