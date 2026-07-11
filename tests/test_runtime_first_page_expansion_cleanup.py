import asyncio
import json
from typing import Any, cast

import pytest

from seektalent.core.retrieval.provider_contract import ProviderSearchContinuation
from seektalent.runtime.first_page_expansion import (
    decide_first_page_expansion,
    discard_unconsumed_first_page_continuations,
    execute_first_page_decisions,
)
from seektalent.source_contracts.first_page_expansion import (
    SourceFirstPageExpansionError,
    SourceFirstPageExpansionRequest,
    SourceFirstPageExpansionResult,
)
from seektalent.runtime.source_round_dispatch import RuntimeSourceInvariantError


def test_pending_cleanup_false_deletion_ack_is_reported_and_other_carriers_continue() -> None:
    continuations = [
        ProviderSearchContinuation(
            kind="first_page_detail_expansion",
            continuation_id=f"c{index}",
            opaque_ref=f"artifact://private/{index}",
            source_kind="liepin",
            round_no=1,
            query_instance_id=f"q{index}",
            visible_candidate_count=2,
            eligible_candidate_count=2,
            initial_opened_count=1,
        )
        for index in (1, 2)
    ]
    calls: list[str] = []

    async def expander(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        calls.append(request.continuation_id)
        return SourceFirstPageExpansionResult(
            source_kind=request.source_kind,
            query_instance_id=request.query_instance_id,
            continuation_id=request.continuation_id,
            status="completed",
            first_page_visible_count=request.continuation.visible_candidate_count,
            first_page_eligible_count=request.continuation.eligible_candidate_count,
            initial_opened_count=request.continuation.initial_opened_count,
            continuation_deleted=request.continuation_id == "c2",
        )

    records = asyncio.run(
        discard_unconsumed_first_page_continuations(
            runtime_run_id="run",
            round_no=1,
            continuations=continuations,
            expanders={"liepin": expander},
        )
    )
    assert calls == ["c1", "c2"]
    assert len(records) == 2
    assert sum(item.deleted for item in records) == 1
    assert sum(not item.deleted for item in records) == 1
    assert [item.safe_reason_code for item in records if not item.deleted] == ["first_page_continuation_cleanup_failed"]


def test_pending_cleanup_one_false_ack_has_exact_single_failure_record() -> None:
    continuation = ProviderSearchContinuation(
        kind="first_page_detail_expansion",
        continuation_id="one",
        opaque_ref="artifact://private/one",
        source_kind="liepin",
        round_no=1,
        query_instance_id="q1",
        visible_candidate_count=2,
        eligible_candidate_count=2,
        initial_opened_count=1,
    )

    async def false_ack(request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        return SourceFirstPageExpansionResult(
            source_kind=request.source_kind,
            query_instance_id=request.query_instance_id,
            continuation_id=request.continuation_id,
            status="completed",
            first_page_visible_count=2,
            first_page_eligible_count=2,
            initial_opened_count=1,
            continuation_deleted=False,
        )

    records = asyncio.run(
        discard_unconsumed_first_page_continuations(
            runtime_run_id="run",
            round_no=1,
            continuations=[continuation],
            expanders={"liepin": false_ack},
        )
    )
    assert len(records) == 1
    assert records[0].deleted is False
    assert records[0].safe_reason_code == "first_page_continuation_cleanup_failed"


def test_pending_cleanup_rejects_duplicate_carrier_ids() -> None:
    continuation = ProviderSearchContinuation(
        kind="first_page_detail_expansion",
        continuation_id="duplicate",
        opaque_ref="artifact://private/one",
        source_kind="liepin",
        round_no=1,
        query_instance_id="q1",
        visible_candidate_count=2,
        eligible_candidate_count=2,
        initial_opened_count=1,
    )
    with pytest.raises(RuntimeSourceInvariantError, match="duplicate_first_page_continuation_cleanup_carrier"):
        asyncio.run(
            discard_unconsumed_first_page_continuations(
                runtime_run_id="run",
                round_no=1,
                continuations=[continuation, continuation],
                expanders={},
            )
        )


def test_pending_cleanup_does_not_swallow_cancellation() -> None:
    continuation = ProviderSearchContinuation(
        kind="first_page_detail_expansion",
        continuation_id="cancel",
        opaque_ref="artifact://private/cancel",
        source_kind="liepin",
        round_no=1,
        query_instance_id="q1",
        visible_candidate_count=2,
        eligible_candidate_count=2,
        initial_opened_count=1,
    )

    async def cancelled(_request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            discard_unconsumed_first_page_continuations(
                runtime_run_id="run",
                round_no=1,
                continuations=[continuation],
                expanders={"liepin": cancelled},
            )
        )


def test_pending_cleanup_maps_private_provider_reason_to_source_neutral_code() -> None:
    marker = "artifact://protected/x?providerCandidateId=secret"
    continuation = ProviderSearchContinuation(
        kind="first_page_detail_expansion",
        continuation_id="private",
        opaque_ref=marker,
        source_kind="liepin",
        round_no=1,
        query_instance_id="q1",
        visible_candidate_count=2,
        eligible_candidate_count=2,
        initial_opened_count=1,
    )

    async def malicious(_request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        raise SourceFirstPageExpansionError(
            "provider cleanup failed", status="failed", safe_reason_code=marker, continuation_deleted=False
        )

    [record] = asyncio.run(
        discard_unconsumed_first_page_continuations(
            runtime_run_id="run",
            round_no=1,
            continuations=[continuation],
            expanders={"liepin": malicious},
        )
    )
    assert record.safe_reason_code == "first_page_continuation_cleanup_failed"
    assert marker not in json.dumps(record.__dict__)


@pytest.mark.parametrize(
    "status,expected",
    [
        ("blocked", "first_page_expansion_blocked"),
        ("partial", "first_page_expansion_partial"),
        ("failed", "first_page_expansion_failed"),
    ],
)
def test_execute_maps_malicious_provider_reason_by_normalized_status(status: str, expected: str) -> None:
    marker = "https://private.invalid/?providerCandidateId=secret"
    continuation = ProviderSearchContinuation(
        kind="first_page_detail_expansion",
        continuation_id="reason",
        opaque_ref="artifact://private/reason",
        source_kind="liepin",
        round_no=1,
        query_instance_id="q1",
        visible_candidate_count=2,
        eligible_candidate_count=2,
        initial_opened_count=1,
    )
    decision = decide_first_page_expansion(
        continuations=[continuation],
        requested_count=1,
        baseline_opened_count=1,
        baseline_identity_count=0,
        scorecards=[],
    )

    async def malicious(_request: SourceFirstPageExpansionRequest) -> SourceFirstPageExpansionResult:
        raise SourceFirstPageExpansionError(
            "private provider failure",
            status=cast(Any, status),
            safe_reason_code=marker,
            continuation_deleted=True,
        )

    [result] = asyncio.run(
        execute_first_page_decisions(
            runtime_run_id="run",
            round_no=1,
            decisions=[decision],
            expanders={"liepin": malicious},
        )
    )
    assert result.safe_reason_code == expected
    assert marker not in json.dumps(result.__dict__, default=str)
