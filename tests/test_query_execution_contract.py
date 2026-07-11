import pytest

from seektalent.models import LogicalQueryOutcome, QueryExecutionReceipt, QueryTermCandidate
from seektalent.retrieval.query_identity import (
    ResolvedQueryIdentity,
    build_term_group_key,
    resolve_query_identity,
)
from seektalent.runtime.query_identity import (
    assert_novel_query_identities,
    consumed_non_anchor_term_family_ids,
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


def _receipt(
    *,
    source_kind: str,
    dispatch_started: bool,
    non_anchor_term_family_ids: list[str] | None = None,
) -> QueryExecutionReceipt:
    return QueryExecutionReceipt(
        round_no=2,
        source_kind=source_kind,
        query_instance_id="query-2-primary",
        query_fingerprint=f"{source_kind}-fingerprint-2",
        term_group_key="group-1",
        primary_anchor_family_id="role.platform",
        non_anchor_term_family_ids=non_anchor_term_family_ids or ["skill.python"],
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


def test_attempted_query_consumes_non_anchor_families_but_blocked_preflight_does_not() -> None:
    attempted = _receipt(
        source_kind="liepin",
        dispatch_started=True,
        non_anchor_term_family_ids=["domain.multiagent", "domain.python"],
    )
    blocked = _receipt(
        source_kind="cts",
        dispatch_started=False,
        non_anchor_term_family_ids=["domain.rag"],
    )
    assert consumed_non_anchor_term_family_ids([attempted, blocked]) == {
        "domain.multiagent",
        "domain.python",
    }


def test_query_identity_uses_explicit_prf_family_override() -> None:
    identity = resolve_query_identity(
        query_terms=["Platform", "agentic memory"],
        query_term_pool=_pool(),
        explicit_family_overrides={"agentic memory": "prf.memory.system"},
    )
    assert identity.non_anchor_term_family_ids == ("prf.memory.system",)


def test_query_identity_rejects_distinct_alias_surfaces_for_one_semantic_family() -> None:
    aliases = _pool()
    aliases.append(aliases[1].model_copy(update={"term": "Py"}))

    with pytest.raises(ValueError, match="query_semantic_family_repeated"):
        resolve_query_identity(
            query_terms=["Platform", "Python", "Py"],
            query_term_pool=aliases,
        )


def test_query_identity_rejects_prf_override_that_resolves_to_anchor_family() -> None:
    with pytest.raises(ValueError, match="query_non_anchor_surface_resolves_to_anchor_family"):
        resolve_query_identity(
            query_terms=["Platform", "agent"],
            query_term_pool=_pool(),
            explicit_family_overrides={"agent": "role.platform"},
        )


def test_bundle_novelty_rejects_history_and_sibling_family_reuse() -> None:
    exploit = ResolvedQueryIdentity("group-exploit", "role.aiagent", ("domain.python",))
    explore = ResolvedQueryIdentity("group-explore", "role.aiagent", ("domain.rag",))
    with pytest.raises(ValueError, match="non_anchor_term_family_already_executed"):
        assert_novel_query_identities(
            identities=[exploit, explore],
            used_term_group_keys=set(),
            consumed_non_anchor_family_ids={"domain.rag"},
        )
    with pytest.raises(ValueError, match="non_anchor_term_family_already_executed"):
        assert_novel_query_identities(
            identities=[exploit, ResolvedQueryIdentity("group-explore-2", "role.aiagent", ("domain.python",))],
            used_term_group_keys=set(),
            consumed_non_anchor_family_ids=set(),
        )


def test_same_family_alias_is_consumed_from_receipt_identity() -> None:
    receipt = _receipt(source_kind="liepin", dispatch_started=True, non_anchor_term_family_ids=["skill.python"])
    alias_identity = ResolvedQueryIdentity("group-py", "role.platform", ("skill.python",))

    with pytest.raises(ValueError, match="non_anchor_term_family_already_executed"):
        assert_novel_query_identities(
            identities=[alias_identity],
            used_term_group_keys=set(),
            consumed_non_anchor_family_ids=consumed_non_anchor_term_family_ids([receipt]),
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


def test_failed_after_dispatch_consumes_term_group() -> None:
    receipt = _receipt(source_kind="liepin", dispatch_started=True).model_copy(
        update={"status": "failed"},
    )

    assert used_term_group_keys([receipt]) == {"group-1"}


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


def test_receipts_for_same_logical_query_must_agree_on_family_identity() -> None:
    first = _receipt(source_kind="cts", dispatch_started=True)
    conflicting = _receipt(source_kind="liepin", dispatch_started=True).model_copy(
        update={"primary_anchor_family_id": "role.conflicting"}
    )

    with pytest.raises(ValueError, match="logical_query_receipt_identity_mismatch"):
        logical_outcomes_from_receipts([first, conflicting])


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


def test_post_merge_counts_union_source_candidates_by_canonical_identity() -> None:
    from seektalent.runtime.query_identity import apply_post_merge_query_counts
    from seektalent.source_contracts.runtime_lanes import RuntimeQueryCandidateAttribution

    outcome = LogicalQueryOutcome(
        query_instance_id="query-1",
        term_group_key="group-1",
        primary_anchor_family_id="role.platform",
        non_anchor_term_family_ids=["skill.python"],
        query_role="exploit",
        lane_type="exploit",
        query_terms=["Platform", "Python"],
        keyword_query="Platform Python",
        attempted=True,
        status="completed",
    )

    counted = apply_post_merge_query_counts(
        outcomes=[outcome],
        candidate_attributions=[
            RuntimeQueryCandidateAttribution(
                source_kind="cts",
                query_instance_id="query-1",
                resume_id="cts-1",
                dedup_key="candidate-a",
            ),
            RuntimeQueryCandidateAttribution(
                source_kind="liepin",
                query_instance_id="query-1",
                resume_id="liepin-1",
                dedup_key="candidate-a",
            ),
        ],
        candidate_identity_by_resume_id={"cts-1": "identity-a", "liepin-1": "identity-a"},
        dispatch_order=["query-1"],
        identities_seen_before_round=set(),
    )

    assert counted[0].unique_candidate_count == 1
    assert counted[0].duplicate_candidate_count == 1


def test_post_merge_counts_allocate_shared_identity_to_earlier_logical_query() -> None:
    from seektalent.runtime.query_identity import apply_post_merge_query_counts
    from seektalent.source_contracts.runtime_lanes import RuntimeQueryCandidateAttribution

    outcomes = [
        LogicalQueryOutcome(
            query_instance_id="query-primary",
            term_group_key="group-primary",
            primary_anchor_family_id="role.platform",
            non_anchor_term_family_ids=["skill.python"],
            query_role="exploit",
            lane_type="exploit",
            query_terms=["Platform", "Python"],
            keyword_query="Platform Python",
            attempted=True,
            status="completed",
        ),
        LogicalQueryOutcome(
            query_instance_id="query-explore",
            term_group_key="group-explore",
            primary_anchor_family_id="role.platform",
            non_anchor_term_family_ids=["skill.rust"],
            query_role="explore",
            lane_type="generic_explore",
            query_terms=["Platform", "Rust"],
            keyword_query="Platform Rust",
            attempted=True,
            status="completed",
        ),
    ]

    counted = apply_post_merge_query_counts(
        outcomes=outcomes,
        candidate_attributions=[
            RuntimeQueryCandidateAttribution(
                source_kind="cts",
                query_instance_id="query-primary",
                resume_id="candidate-1",
                dedup_key="candidate-a",
            ),
            RuntimeQueryCandidateAttribution(
                source_kind="liepin",
                query_instance_id="query-explore",
                resume_id="candidate-2",
                dedup_key="candidate-a",
            ),
        ],
        candidate_identity_by_resume_id={"candidate-1": "identity-a", "candidate-2": "identity-a"},
        dispatch_order=["query-primary", "query-explore"],
        identities_seen_before_round=set(),
    )

    assert [(item.unique_candidate_count, item.duplicate_candidate_count) for item in counted] == [(1, 0), (0, 1)]


@pytest.mark.parametrize(
    "term_group_keys",
    [
        ["group-1"],
        ["group-2", "group-2"],
    ],
)
def test_bundle_novelty_rejects_prior_or_in_bundle_term_group_replay(term_group_keys: list[str]) -> None:
    from seektalent.runtime.query_identity import assert_novel_term_group_keys

    with pytest.raises(ValueError, match="term_group_already_executed"):
        assert_novel_term_group_keys(
            term_group_keys=term_group_keys,
            used_term_group_keys={"group-1"},
        )
