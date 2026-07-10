import pytest

from seektalent.models import QueryExecutionReceipt, QueryTermCandidate
from seektalent.runtime.query_identity import (
    build_term_group_key,
    logical_outcomes_from_receipts,
    used_term_group_keys,
)


def _pool() -> list[QueryTermCandidate]:
    return [
        QueryTermCandidate(
            term="Platform",
            source="job_title",
            category="role_anchor",
            priority=1,
            evidence="title",
            first_added_round=0,
            retrieval_role="primary_role_anchor",
            queryability="admitted",
            family="role.platform",
        ),
        QueryTermCandidate(
            term="Python",
            source="jd",
            category="tooling",
            priority=2,
            evidence="jd",
            first_added_round=0,
            retrieval_role="core_skill",
            queryability="admitted",
            family="skill.python",
        ),
    ]


def _receipt(*, source_kind: str, dispatch_started: bool) -> QueryExecutionReceipt:
    return QueryExecutionReceipt(
        round_no=2,
        source_kind=source_kind,
        query_instance_id="query-2-primary",
        query_fingerprint=f"{source_kind}-fingerprint-2",
        term_group_key="group-1",
        query_role="exploit",
        lane_type="exploit",
        query_terms=["Platform", "Python"],
        keyword_query="Platform Python",
        requested_count=10,
        source_plan_version="v2",
        status="completed",
        dispatch_started=dispatch_started,
        raw_candidate_count=3,
        unique_candidate_count=2,
        duplicate_candidate_count=1,
    )


def test_term_group_key_is_order_and_source_independent() -> None:
    first = build_term_group_key(query_terms=["Platform", "Python"], query_term_pool=_pool())
    second = build_term_group_key(query_terms=[" python ", "platform"], query_term_pool=_pool())

    assert first == second


def test_term_group_key_prefers_family_id_over_normalized_term() -> None:
    aliases = _pool()
    aliases.append(aliases[1].model_copy(update={"term": "Py"}))

    assert build_term_group_key(query_terms=["Python"], query_term_pool=aliases) == build_term_group_key(
        query_terms=["Py"],
        query_term_pool=aliases,
    )


def test_blocked_before_dispatch_does_not_consume_term_group() -> None:
    assert used_term_group_keys([_receipt(source_kind="liepin", dispatch_started=False)]) == set()


def test_receipts_aggregate_by_logical_query_instance() -> None:
    outcomes = logical_outcomes_from_receipts(
        [
            _receipt(source_kind="cts", dispatch_started=True),
            _receipt(source_kind="liepin", dispatch_started=True),
        ]
    )

    assert len(outcomes) == 1
    assert outcomes[0].query_instance_id == "query-2-primary"
    assert outcomes[0].raw_candidate_count == 6
    assert outcomes[0].unique_candidate_count == 0
    assert outcomes[0].duplicate_candidate_count == 0
    assert {receipt.query_fingerprint for receipt in outcomes[0].receipts} == {
        "cts-fingerprint-2",
        "liepin-fingerprint-2",
    }


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["completed"], "completed"),
        (["blocked"], "blocked"),
        (["failed"], "failed"),
        (["completed", "blocked"], "partial"),
        (["completed", "failed"], "partial"),
    ],
)
def test_logical_status_preserves_partial_source_coverage(
    statuses: list[str], expected: str
) -> None:
    receipts = [
        _receipt(source_kind=f"source-{index}", dispatch_started=True).model_copy(
            update={"status": status}
        )
        for index, status in enumerate(statuses)
    ]

    assert logical_outcomes_from_receipts(receipts)[0].status == expected
