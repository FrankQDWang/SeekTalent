from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest

from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.providers.liepin.opencli_worker_client import LiepinOpenCliWorkerClient
from seektalent.providers.liepin.worker_contracts import (
    LiepinResumeSearchResponse,
    LiepinWorkerModeError,
    LiepinWorkerPartialSearchError,
)


@dataclass
class FakeRetriever:
    calls: list[object]

    def search_resumes(self, request):
        self.calls.append(request)
        return LiepinResumeSearchResponse(
            resumes=[],
            exhausted=True,
            requestPayload={"backend": "opencli"},
            rawCandidateCount=3,
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
                    "liepin_requirement_sheet_json": "{\"job_title\":\"数据开发专家\"}",
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
                        "liepin_requirement_sheet_json": "{\"job_title\":\"数据开发专家\"}",
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
                        "liepin_requirement_sheet_json": "{\"job_title\":\"数据开发专家\"}",
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
                        "liepin_requirement_sheet_json": "{\"job_title\":\"数据开发专家\"}",
                    },
                ),
                round_no=1,
                trace_id="run-1",
            )
        )

    assert error.value.code == "liepin_opencli_detail_not_opened"
    partial_result = getattr(error.value, "partial_search_result")
    assert partial_result.request_payload["workflowSteps"][0]["step_name"] == "open_detail"


def test_opencli_worker_session_status_is_ready() -> None:
    client = LiepinOpenCliWorkerClient(
        retriever=FakeRetriever(calls=[]),
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    status = asyncio.run(client.session_status(connection_id="liepin-opencli"))

    assert status.status == "ready"
    assert status.provider_account_hash is None


def test_opencli_worker_session_status_echoes_bound_provider_hash() -> None:
    client = LiepinOpenCliWorkerClient(
        retriever=FakeRetriever(calls=[]),
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
    assert status.provider_account_hash == "workbench-bound-hash"
