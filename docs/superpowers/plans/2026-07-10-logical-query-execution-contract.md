# Logical Query Execution Contract Implementation Plan

> **Status:** Implemented and verified on 2026-07-11; pre-existing static-gate debt is recorded below.
>
> **Completion-record convention:** A checked step records that its intended deliverable is present on `main` and covered by the current verification evidence below. The planned red-phase commands are historical TDD steps and cannot be re-run as failures on the completed tree; they are checked only after the corresponding final behavior was independently verified.

**Goal:** Make every source query observable and novel at the logical-query level, then expose those real query groups to controller, reflection, and both Workbench routes.

**Architecture:** `QueryExecutionReceipt` is the one terminal physical-source fact for a `RuntimeSourceQueryIntent`; runtime derives `LogicalQueryOutcome` from receipts by `query_instance_id`. `term_group_key` is a separate unordered semantic identity used for no-replay policy. The public v2 stage contract exposes only safe query-group fields, and both BFFs plus the shared React rail render those groups without reading raw runtime/provider payloads.

**Tech Stack:** Python 3.12, Pydantic v2, asyncio, pytest, FastAPI/OpenAPI generation, React, TypeScript, Vitest, pnpm.

## Global Constraints

- Use `QueryExecutionReceipt` for source-neutral execution truth; retain `SentQueryRecord` for CTS/city physical-attempt audit detail.
- A preflight-blocked receipt has `dispatch_started=False` and does not consume a term group; any source attempt with `dispatch_started=True` does.
- `term_group_key` is source-, lane-, round-, and order-independent; it uses sorted compiler family IDs and only falls back to normalized terms when no family exists.
- No automatic replay: exhausted novelty routes to current rescue/stop behavior, never an already-used term group.
- This is a breaking `runtime-public-stage-output/v2` and BFF contract. Do not backfill or heuristically reconstruct historic v1 query groups.
- React consumes typed BFF DTOs only. Never surface raw filters, provider payloads, URLs, browser refs, candidate IDs, or error refs.
- Preserve the existing source-neutral runtime boundary. Do not add CTS/Liepin conditionals to receipt aggregation.
- Preserve existing scoring concurrency and structured-resume-only evidence behavior.

## Execution Gates

- **Gate A — runtime contract:** Complete Tasks 1–4, run their focused Python suites, and review the persisted receipt/outcome contract before changing the public-stage schema.
- **Gate B — public/UI contract:** Start Tasks 5–6 only after Gate A passes. Regenerate OpenAPI types and run the full Python/frontend slice before merging either route's DTO changes.

---

## File Structure

- Create: `src/seektalent/retrieval/query_identity.py` — pure term-group identity.
- Create: `src/seektalent/runtime/query_identity.py` — novelty checks and receipt-to-logical-outcome aggregation.
- Modify: `src/seektalent/models.py` — receipt, outcome, ledger, round-state, and bounded controller/reflection evidence models.
- Modify: `src/seektalent/runtime/retrieval_runtime.py` and `src/seektalent/source_contracts/logical_query.py` — carry a term-group key on logical state and dispatch.
- Modify: `src/seektalent/runtime/logical_query_dispatch.py` and `src/seektalent/runtime/orchestrator.py` — calculate the key while the compiled term pool is available, then preserve it through dispatch.
- Modify: `src/seektalent/runtime/source_query_intent.py` — propagate query-instance and term-group identity into `RuntimeQueryPackage`.
- Modify: `src/seektalent/runtime/source_round_dispatch.py` — normalize one terminal receipt per selected source intent.
- Modify: `src/seektalent/runtime/retrieval_runtime.py` — carry receipts/outcomes beside legacy retrieval details.
- Modify: `src/seektalent/source_adapters/round_adapters.py` — derive CTS outcomes and forward Liepin outcomes.
- Modify: `src/seektalent/source_contracts/runtime_lanes.py` and `src/seektalent/sources/liepin/runtime_lane.py` — return one Liepin outcome per logical query.
- Modify: `src/seektalent/runtime/orchestrator.py` — append ledger, construct round outcomes after identity merge, emit v2 safe query groups.
- Modify: controller/reflection/query-planning modules — use consumed logical groups rather than `sent_query_history` for novelty and prompt evidence.
- Modify: public-stage, V2, legacy BFF, React DTO, React rail, fixtures, schema generation, and contract tests listed per task below.

### Implemented Boundary Adjustment

The final architecture review moved the pure `build_term_group_key()` helper to the retrieval leaf in `src/seektalent/retrieval/query_identity.py`; `src/seektalent/runtime/query_identity.py` retains runtime ledger and outcome policy. This is a post-plan placement correction, not a contract change: the semantic identity, receipt, novelty, and public-query-group interfaces specified here are unchanged.

### Task 1: Add Query Receipt, Outcome, And Semantic Group Foundations

**Files:**

- Create: `src/seektalent/runtime/query_identity.py`
- Modify: `src/seektalent/models.py:426-561,1185-1211,1331-1387`
- Modify: `src/seektalent/runtime/source_query_intent.py:19-65`
- Modify: `src/seektalent/runtime/retrieval_runtime.py:165-249`
- Modify: `src/seektalent/source_contracts/logical_query.py:8-18`
- Modify: `src/seektalent/runtime/logical_query_dispatch.py:10-37`
- Modify: `src/seektalent/runtime/orchestrator.py:3898-3950`
- Test: `tests/test_query_execution_contract.py`
- Test: `tests/test_query_identity.py`

**Interfaces:**

- Produces `QueryExecutionReceipt`, `LogicalQueryOutcome`, `RetrievalState.query_execution_ledger`, `RoundState.query_outcomes`, `build_term_group_key()`, `used_term_group_keys()`, and `logical_outcomes_from_receipts()`.
- Later tasks consume `QueryExecutionReceipt.dispatch_started`, `LogicalQueryOutcome.query_instance_id`, and `LogicalQueryOutcome.term_group_key`.

- [x] **Step 1: Write failing semantic-identity and receipt tests**

Create `tests/test_query_execution_contract.py` with the following focused cases:

```python
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
            term="Platform", source="title", category="role", priority=1,
            evidence="title", first_added_round=0,
            retrieval_role="primary_role_anchor", queryability="admitted", family="role.platform",
        ),
        QueryTermCandidate(
            term="Python", source="jd", category="skill", priority=2,
            evidence="jd", first_added_round=0,
            retrieval_role="core_skill", queryability="admitted", family="skill.python",
        ),
    ]


def _receipt(*, source_kind: str, dispatch_started: bool) -> QueryExecutionReceipt:
    return QueryExecutionReceipt(
        round_no=2, source_kind=source_kind, query_instance_id="query-2-primary",
        query_fingerprint=f"{source_kind}-fingerprint-2", term_group_key="group-1", query_role="exploit",
        lane_type="primary", query_terms=["Platform", "Python"], keyword_query="Platform Python",
        requested_count=10, source_plan_version="v2", status="completed",
        dispatch_started=dispatch_started, raw_candidate_count=3,
        unique_candidate_count=2, duplicate_candidate_count=1,
    )


def test_term_group_key_is_order_and_source_independent() -> None:
    first = build_term_group_key(query_terms=["Platform", "Python"], query_term_pool=_pool())
    second = build_term_group_key(query_terms=[" python ", "platform"], query_term_pool=_pool())
    assert first == second


def test_blocked_before_dispatch_does_not_consume_term_group() -> None:
    assert used_term_group_keys([_receipt(source_kind="liepin", dispatch_started=False)]) == set()


def test_receipts_aggregate_by_logical_query_instance() -> None:
    outcomes = logical_outcomes_from_receipts([
        _receipt(source_kind="cts", dispatch_started=True),
        _receipt(source_kind="liepin", dispatch_started=True),
    ])
    assert len(outcomes) == 1
    assert outcomes[0].query_instance_id == "query-2-primary"
    assert outcomes[0].raw_candidate_count == 6
    assert outcomes[0].unique_candidate_count == 0
    assert outcomes[0].duplicate_candidate_count == 0
    assert {receipt.query_fingerprint for receipt in outcomes[0].receipts} == {
        "cts-fingerprint-2", "liepin-fingerprint-2",
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
def test_logical_status_preserves_partial_source_coverage(statuses, expected) -> None:
    receipts = [_receipt(source_kind=f"source-{index}", dispatch_started=True).model_copy(update={"status": status}) for index, status in enumerate(statuses)]
    assert logical_outcomes_from_receipts(receipts)[0].status == expected
```

- [x] **Step 2: Run the tests and verify the missing-contract failure**

Run:

```bash
uv run pytest -q tests/test_query_execution_contract.py tests/test_query_identity.py
```

Expected: collection fails because `QueryExecutionReceipt` and `seektalent.runtime.query_identity` do not exist.

- [x] **Step 3: Define the persistent models**

Add these models in `src/seektalent/models.py` immediately after `SentQueryRecord`:

```python
QueryExecutionStatus = Literal["completed", "partial", "blocked", "failed"]


class QueryExecutionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_no: int
    source_kind: RuntimeSourceKind
    query_instance_id: str
    query_fingerprint: str
    term_group_key: str
    query_role: QueryRole
    lane_type: LaneType
    query_terms: list[str] = Field(default_factory=list)
    keyword_query: str
    requested_count: int
    source_plan_version: str
    status: QueryExecutionStatus
    dispatch_started: bool
    raw_candidate_count: int = Field(default=0, ge=0)
    unique_candidate_count: int = Field(default=0, ge=0)
    duplicate_candidate_count: int = Field(default=0, ge=0)
    exhausted_reason: str | None = None
    safe_reason_code: str | None = None


class LogicalQueryOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_instance_id: str
    term_group_key: str
    query_role: QueryRole
    lane_type: LaneType
    query_terms: list[str] = Field(default_factory=list)
    keyword_query: str
    attempted: bool
    status: QueryExecutionStatus
    raw_candidate_count: int = Field(default=0, ge=0)
    unique_candidate_count: int = Field(default=0, ge=0)
    duplicate_candidate_count: int = Field(default=0, ge=0)
    receipts: list[QueryExecutionReceipt] = Field(default_factory=list)
```

Add `query_execution_ledger: list[QueryExecutionReceipt] = Field(default_factory=list)` to `RetrievalState`, `query_outcomes: list[LogicalQueryOutcome] = Field(default_factory=list)` to `RoundState`, and the bounded evidence fields named in the spec to `ControllerContext` and `ReflectionContext` with empty-list defaults where permitted.

- [x] **Step 4: Implement pure identity and aggregation helpers**

Create `src/seektalent/runtime/query_identity.py` with this public surface:

```python
from __future__ import annotations

import json
from collections import defaultdict
from hashlib import sha256
from typing import Sequence

from seektalent.models import (
    LogicalQueryOutcome,
    QueryExecutionReceipt,
    QueryExecutionStatus,
    QueryTermCandidate,
)


def _term_key(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def build_term_group_key(*, query_terms: Sequence[str], query_term_pool: Sequence[QueryTermCandidate]) -> str:
    families = {_term_key(item.term): item.family for item in query_term_pool}
    semantic_terms = sorted({families.get(_term_key(term), f"term:{_term_key(term)}") for term in query_terms if _term_key(term)})
    if not semantic_terms:
        raise ValueError("term_group_key_requires_terms")
    payload = json.dumps({"version": "term-group-v1", "members": semantic_terms}, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()[:32]


def used_term_group_keys(receipts: Sequence[QueryExecutionReceipt]) -> set[str]:
    return {receipt.term_group_key for receipt in receipts if receipt.dispatch_started}


def _logical_identity(receipt: QueryExecutionReceipt) -> tuple[object, ...]:
    return (
        receipt.term_group_key,
        receipt.query_role,
        receipt.lane_type,
        tuple(_term_key(term) for term in receipt.query_terms),
        receipt.keyword_query,
    )


def _logical_status(statuses: set[QueryExecutionStatus]) -> QueryExecutionStatus:
    if statuses == {"completed"}:
        return "completed"
    if statuses == {"blocked"}:
        return "blocked"
    if statuses == {"failed"}:
        return "failed"
    return "partial"


def logical_outcomes_from_receipts(receipts: Sequence[QueryExecutionReceipt]) -> list[LogicalQueryOutcome]:
    grouped: dict[str, list[QueryExecutionReceipt]] = defaultdict(list)
    for receipt in receipts:
        grouped[receipt.query_instance_id].append(receipt)
    outcomes: list[LogicalQueryOutcome] = []
    for query_instance_id, members in sorted(grouped.items()):
        first = members[0]
        if any(_logical_identity(item) != _logical_identity(first) for item in members):
            raise ValueError("logical_query_receipt_identity_mismatch")
        statuses = {item.status for item in members}
        outcomes.append(LogicalQueryOutcome(
            query_instance_id=query_instance_id,
            term_group_key=first.term_group_key, query_role=first.query_role, lane_type=first.lane_type,
            query_terms=list(first.query_terms), keyword_query=first.keyword_query,
            attempted=any(item.dispatch_started for item in members), status=_logical_status(statuses),
            raw_candidate_count=sum(item.raw_candidate_count for item in members),
            unique_candidate_count=0,
            duplicate_candidate_count=0,
            receipts=members,
        ))
    return outcomes
```

- [x] **Step 5: Calculate once while the compiled term pool is available, then preserve identity**

Add `term_group_key: str = ""` to `LogicalQueryState` and a required `term_group_key: str` to `LogicalQueryDispatch`. In `_build_round_query_bundle()`, immediately after the primary and optional second-lane states are assembled, assign each state with:

```python
for query_state in query_states:
    query_state.term_group_key = build_term_group_key(
        query_terms=query_state.query_terms,
        query_term_pool=query_term_pool,
    )
```

Extend `build_logical_query_dispatches()` to copy `query.term_group_key`, then extend `RuntimeSourceQueryIntent` and `RuntimeQueryPackage` with `term_group_key`, `query_instance_id`, and `query_fingerprint`. `query_package_from_intent()` copies all three. The intent builder must reject an empty dispatch key with `ValueError("runtime_source_query_intent_missing_term_group_key")`.

- [x] **Step 6: Run foundation tests**

Run:

```bash
uv run pytest -q tests/test_query_execution_contract.py tests/test_query_identity.py tests/test_runtime_source_adapter_boundary.py
```

Expected: PASS. Existing package-boundary tests must still validate source-neutral imports.

- [x] **Step 7: Commit the isolated foundation**

```bash
git add src/seektalent/models.py src/seektalent/runtime/query_identity.py src/seektalent/runtime/retrieval_runtime.py src/seektalent/source_contracts/logical_query.py src/seektalent/source_contracts/runtime_lanes.py src/seektalent/runtime/logical_query_dispatch.py src/seektalent/runtime/orchestrator.py src/seektalent/runtime/source_query_intent.py tests/test_query_execution_contract.py tests/test_query_identity.py
git commit -m "feat: add logical query execution identities"
```

### Task 2: Enforce One Terminal Receipt Per Source Intent

**Files:**

- Modify: `src/seektalent/runtime/source_round_dispatch.py:36-152`
- Modify: `src/seektalent/runtime/retrieval_runtime.py:291-305`
- Modify: `src/seektalent/source_adapters/round_adapters.py:72-189`
- Modify: `src/seektalent/source_contracts/runtime_lanes.py:294-344`
- Modify: `src/seektalent/sources/liepin/runtime_lane.py:191-299`
- Test: `tests/test_runtime_source_adapter_boundary.py`
- Test: `tests/test_liepin_runtime_source_lane.py`
- Test: `tests/test_runtime_multi_source_round_dispatch.py`

**Interfaces:**

- Consumes `RuntimeSourceQueryIntent` and `QueryExecutionReceipt` from Task 1.
- Produces `SourceRoundDispatchResult.query_execution_receipts` and `RuntimeSourceLaneResult.query_execution_outcomes`.

- [x] **Step 1: Add conformance tests for CTS and Liepin**

Add these tests:

```python
def test_source_dispatch_receipt_parity_for_completed_cts_and_liepin() -> None:
    result = asyncio.run(dispatch_source_rounds(
        request=_two_source_two_query_request(),
        source_adapters={"cts": _completed_cts_adapter, "liepin": _completed_liepin_adapter},
    ))

    assert len(result.query_execution_receipts) == 4
    assert {(item.source_kind, item.query_instance_id) for item in result.query_execution_receipts} == {
        ("cts", "primary-1"), ("cts", "explore-1"),
        ("liepin", "primary-1"), ("liepin", "explore-1"),
    }
    assert all(item.status == "completed" for item in result.query_execution_receipts)


def test_source_dispatch_rejects_outcome_without_matching_intent() -> None:
    with pytest.raises(RuntimeSourceInvariantError, match="unmatched_source_query_outcome"):
        asyncio.run(dispatch_source_rounds(
            request=_one_liepin_query_request(),
            source_adapters={"liepin": _adapter_with_outcome("unknown-query")},
        ))


def test_post_dispatch_failure_receipt_remains_started() -> None:
    result = asyncio.run(dispatch_source_rounds(
        request=_one_liepin_query_request(),
        source_adapters={"liepin": _adapter_with_outcome("primary-1", status="failed", dispatch_started=True)},
    ))
    receipt = result.query_execution_receipts[0]
    assert receipt.status == "failed"
    assert receipt.dispatch_started is True
```

In `tests/test_liepin_runtime_source_lane.py`, add:

```python
def test_liepin_bundle_preserves_one_execution_outcome_per_logical_query() -> None:
    result = asyncio.run(_run_fixture_two_query_liepin_bundle())
    assert [item.query_instance_id for item in result.query_execution_outcomes] == ["primary-1", "explore-1"]
    assert all(item.status in {"completed", "partial"} for item in result.query_execution_outcomes)
```

- [x] **Step 2: Run the new tests and verify they fail**

Run:

```bash
uv run pytest -q tests/test_runtime_source_adapter_boundary.py tests/test_liepin_runtime_source_lane.py tests/test_runtime_multi_source_round_dispatch.py
```

Expected: FAIL because adapter and lane-result contracts have no execution outcomes or receipts.

- [x] **Step 3: Add adapter outcomes and normalize at dispatch**

Add this frozen, source-neutral outcome DTO to `src/seektalent/source_contracts/runtime_lanes.py`, next to `RuntimeQueryPackage`; `source_round_dispatch.py` imports it. Do not make a source contract depend on a runtime implementation module:

```python
@dataclass(frozen=True)
class SourceQueryExecutionOutcome:
    query_instance_id: str
    status: QueryExecutionStatus
    dispatch_started: bool
    raw_candidate_count: int = 0
    unique_candidate_count: int = 0
    duplicate_candidate_count: int = 0
    exhausted_reason: str | None = None
    safe_reason_code: str | None = None


@dataclass(frozen=True)
class RuntimeQueryCandidateAttribution:
    source_kind: SourceKind
    query_instance_id: str
    resume_id: str
    dedup_key: str | None
```

Add these fields exactly:

```python
# RuntimeSourceLaneResult in source_contracts/runtime_lanes.py
query_execution_outcomes: tuple[SourceQueryExecutionOutcome, ...] = ()
candidate_query_attributions: tuple[RuntimeQueryCandidateAttribution, ...] = ()

# SourceRoundAdapterResult in runtime/source_round_dispatch.py
query_execution_outcomes: tuple[SourceQueryExecutionOutcome, ...] = ()
candidate_query_attributions: tuple[RuntimeQueryCandidateAttribution, ...] = ()

# SourceRoundDispatchResult in runtime/source_round_dispatch.py
query_execution_receipts: tuple[QueryExecutionReceipt, ...] = ()
candidate_query_attributions: tuple[RuntimeQueryCandidateAttribution, ...] = ()
```

Add a source-contract-private `RuntimeQueryCandidateAttribution(source_kind, query_instance_id, resume_id, dedup_key)` and carry an immutable `candidate_query_attributions` tuple on `RuntimeSourceLaneResult` and `SourceRoundAdapterResult`; omit it from every public serializer. CTS derives it from its existing `QueryResumeHit` collection. Liepin emits it from each individual logical query result before bundle merge, then concatenates it when merging lane results. This is the only provenance used for post-identity query counts.

Implement `_receipts_for_source_result(request, result)` so it maps each requested source intent by `query_instance_id`, rejects duplicate or unmatched outcome IDs, and creates one `QueryExecutionReceipt` for every intent. An explicitly preflight-blocked adapter returns a `blocked` outcome with `dispatch_started=False` for every intent. A partial or failed source result must carry explicit per-intent outcomes: an outcome is `dispatch_started=True` whenever the adapter cannot prove the provider/browser query was never sent. Missing outcomes for a non-preflight result are an invariant failure; do not synthesize `dispatch_started=False`, and never inspect `result.source == "liepin"` or `result.source == "cts"` in this function.

- [x] **Step 4: Forward source-specific real outcomes**

In `round_adapters.py`:

- CTS derives one outcome per intent from its executed query/hit data. It sets `dispatch_started=True` once the retrieval service call begins.
- CTS maps every `QueryResumeHit` into `RuntimeQueryCandidateAttribution(source_kind="cts", query_instance_id=hit.query_instance_id, resume_id=hit.resume_id, dedup_key=hit.dedup_key)` and forwards that tuple with its adapter result.
- Liepin forwards `lane_result.query_execution_outcomes` unchanged.

Both adapters forward `candidate_query_attributions` unchanged; neither attempts cross-source identity arithmetic.

In `runtime_lanes.py`, add `query_execution_outcomes` to `RuntimeSourceLaneResult` but omit it from `to_public_payload()`.

In `runtime_lane.py`, aggregate each logical query after its target loop:

```python
SourceQueryExecutionOutcome(
    query_instance_id=logical_query.query_instance_id,
    status=_outcome_status(lane_results),
    dispatch_started=any(item.query_started for item in lane_results),
    raw_candidate_count=sum(item.raw_candidate_count for item in lane_results),
    unique_candidate_count=len(merged_candidate_ids),  # source-local only
    duplicate_candidate_count=sum(item.duplicate_candidate_count for item in lane_results),
    exhausted_reason=_shared_safe_reason(lane_results),
)
```

For that same logical result, emit its private candidate provenance before merging bundles:

```python
candidate_query_attributions=tuple(
    RuntimeQueryCandidateAttribution(
        source_kind="liepin",
        query_instance_id=logical_query.query_instance_id,
        resume_id=candidate.resume_id,
        dedup_key=candidate.dedup_key,
    )
    for candidate in logical_result.candidate_store_updates.values()
)
```

In `merge_liepin_card_lane_results()`, concatenate both `query_execution_outcomes` and `candidate_query_attributions` exactly as it already concatenates evidence, snapshots, events, and packages. In `dispatch_source_rounds()`, concatenate adapter attributions into `SourceRoundDispatchResult.candidate_query_attributions`, normalize all receipts, and pass both collections into `RetrievalExecutionResult`.

Do not derive a status from `RuntimeQueryPackage`; packages remain display summaries only. The source-local unique/duplicate values above must not be summed into a cross-source logical outcome.

- [x] **Step 5: Carry receipts through retrieval result**

Add `query_execution_receipts: list[QueryExecutionReceipt]`, `candidate_query_attributions: list[RuntimeQueryCandidateAttribution]`, and `query_outcomes: list[LogicalQueryOutcome]` to `RetrievalExecutionResult`. `SourceRoundDispatchResult` supplies the receipts and attributions; the orchestrator derives final logical counts after identity merge in Task 3.

- [x] **Step 6: Run source conformance tests**

Run:

```bash
uv run pytest -q tests/test_runtime_source_adapter_boundary.py tests/test_liepin_runtime_source_lane.py tests/test_runtime_multi_source_round_dispatch.py
```

Expected: PASS, including a successful Liepin bundle with one terminal receipt per logical query.

- [x] **Step 7: Commit receipt conformance**

```bash
git add src/seektalent/runtime/source_round_dispatch.py src/seektalent/runtime/retrieval_runtime.py src/seektalent/source_adapters/round_adapters.py src/seektalent/source_contracts/runtime_lanes.py src/seektalent/sources/liepin/runtime_lane.py tests/test_runtime_source_adapter_boundary.py tests/test_liepin_runtime_source_lane.py tests/test_runtime_multi_source_round_dispatch.py
git commit -m "feat: require source query execution receipts"
```

### Task 3: Persist Receipt Truth And Enforce No-Replay

**Files:**

- Modify: `src/seektalent/runtime/orchestrator.py:1551-1811,2158-2195,2352-2405`
- Modify: `src/seektalent/runtime/query_identity.py`
- Modify: `src/seektalent/runtime/controller_context.py`
- Modify: `src/seektalent/controller/react_controller.py`
- Modify: `src/seektalent/runtime/round_decision_runtime.py`
- Modify: `src/seektalent/retrieval/query_plan.py`
- Modify: `src/seektalent/runtime/second_lane_runtime.py`
- Modify: `src/seektalent/runtime/rescue_execution_runtime.py`
- Test: `tests/test_query_plan.py`
- Test: `tests/test_second_lane_runtime.py`
- Test: `tests/test_controller_contract.py`
- Test: `tests/test_runtime_state_flow.py`
- Test: `tests/test_runtime_multi_source_round_dispatch.py`

**Interfaces:**

- Consumes Task 1 identities and Task 2 receipts.
- Produces `RetrievalState.query_execution_ledger`, `RoundState.query_outcomes`, and an invariant that no used `term_group_key` reaches source dispatch.

- [x] **Step 1: Add no-replay tests**

Add these tests to `tests/test_query_plan.py` and `tests/test_controller_contract.py`:

```python
def test_derive_explore_returns_none_when_every_semantic_group_is_used() -> None:
    assert derive_explore_query_terms(
        ["Platform", "Python"],
        title_anchor_terms=[], query_term_pool=_three_term_pool(),
        used_term_group_keys={
            build_term_group_key(query_terms=["Platform", "Python"], query_term_pool=_three_term_pool()),
            build_term_group_key(query_terms=["Platform", "Rust"], query_term_pool=_three_term_pool()),
        },
    ) is None


def test_controller_rejects_semantically_reordered_used_group() -> None:
    context = _controller_context(used_term_group_keys=[
        build_term_group_key(query_terms=["Platform", "Python"], query_term_pool=_pool()),
    ])
    decision = _search_decision(proposed_query_terms=["python", "platform"])
    assert validate_controller_decision(context=context, decision=decision) == "proposed_term_group_already_executed"


def test_failed_after_dispatch_consumes_term_group() -> None:
    receipt = _receipt(source_kind="liepin", dispatch_started=True).model_copy(
        update={"status": "failed"},
    )
    assert used_term_group_keys([receipt]) == {"group-1"}
```

Add a runtime-state test whose default source is Liepin and asserts:

```python
assert len(run_state.retrieval_state.query_execution_ledger) == 2
assert len(run_state.round_history[0].query_outcomes) == 2
assert {item.term_group_key for item in run_state.round_history[0].query_outcomes}
```

Add two identity-attribution regressions in `tests/test_runtime_multi_source_round_dispatch.py`:

- the same canonical candidate returned by CTS and Liepin for one `query_instance_id` yields one `unique_candidate_count` in that logical outcome, not the sum of the two receipt-local counts;
- the same canonical candidate returned by primary and explore is allocated to the earlier logical dispatch only, while the later group records it as a duplicate.

- [x] **Step 2: Run the policy tests and verify they fail**

Run:

```bash
uv run pytest -q tests/test_query_plan.py tests/test_second_lane_runtime.py tests/test_controller_contract.py tests/test_runtime_state_flow.py
```

Expected: FAIL because generic explore falls back to `used_candidates`, controller has no group history, and `RoundState` has no outcomes.

- [x] **Step 3: Append receipts and derive outcomes after identity merge**

In `_round_search_result_from_source_dispatch()`, assign dispatch receipts to the returned `RetrievalExecutionResult` instead of collecting history only from non-null `retrieval_result`.

Before source candidate stores are merged, retain a private round attribution for every returned candidate: `(source_kind, query_instance_id, resume_id, dedup_key)`. CTS can derive it from its existing `QueryResumeHit`; Liepin must preserve `logical_query_instance_id` alongside its source evidence/result rather than infer it from a display package. This provenance is internal and must not be added to public events or BFF DTOs.

Add this helper to `src/seektalent/runtime/query_identity.py`:

```python
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence

from seektalent.source_contracts.runtime_lanes import RuntimeQueryCandidateAttribution


def apply_post_merge_query_counts(
    *,
    outcomes: Sequence[LogicalQueryOutcome],
    candidate_attributions: Sequence[RuntimeQueryCandidateAttribution],
    candidate_identity_by_resume_id: Mapping[str, str],
    dispatch_order: Sequence[str],
    identities_seen_before_round: Collection[str],
) -> list[LogicalQueryOutcome]:
    outcome_by_query = {outcome.query_instance_id: outcome for outcome in outcomes}
    if set(dispatch_order) != set(outcome_by_query):
        raise ValueError("query_outcome_dispatch_order_mismatch")
    attributions_by_query: dict[str, list[RuntimeQueryCandidateAttribution]] = defaultdict(list)
    for attribution in candidate_attributions:
        if attribution.query_instance_id not in outcome_by_query:
            raise ValueError("query_candidate_attribution_without_outcome")
        attributions_by_query[attribution.query_instance_id].append(attribution)

    allocated_identities = set(identities_seen_before_round)
    counted: list[LogicalQueryOutcome] = []
    for query_instance_id in dispatch_order:
        unique_count = 0
        duplicate_count = 0
        identities_in_query: set[str] = set()
        for attribution in sorted(
            attributions_by_query[query_instance_id],
            key=lambda item: (item.source_kind, item.resume_id, item.dedup_key or ""),
        ):
            identity_id = candidate_identity_by_resume_id.get(attribution.resume_id)
            if identity_id is None:
                raise ValueError("query_candidate_attribution_missing_identity")
            if identity_id in allocated_identities or identity_id in identities_in_query:
                duplicate_count += 1
                continue
            identities_in_query.add(identity_id)
            unique_count += 1
        allocated_identities.update(identities_in_query)
        counted.append(outcome_by_query[query_instance_id].model_copy(update={
            "unique_candidate_count": unique_count,
            "duplicate_candidate_count": duplicate_count,
        }))
    return counted
```

After runtime candidate identity merge completes, derive and store outcomes:

```python
run_state.retrieval_state.query_execution_ledger.extend(retrieval_result.query_execution_receipts)
receipt_outcomes = logical_outcomes_from_receipts(retrieval_result.query_execution_receipts)
round_query_outcomes = apply_post_merge_query_counts(
    outcomes=receipt_outcomes,
    candidate_attributions=round_candidate_attributions,
    candidate_identity_by_resume_id=run_state.candidate_identity_by_resume_id,
    dispatch_order=[item.query_instance_id for item in logical_dispatches],
    identities_seen_before_round=identities_seen_before_round,
)
round_state.query_outcomes = round_query_outcomes
tracer.write_json(
    f"round.{round_no:02d}.query_execution_receipts",
    [item.model_dump(mode="json") for item in retrieval_result.query_execution_receipts],
)
```

`apply_post_merge_query_counts()` unions canonical identities within a logical query across sources, then allocates an identity to the first logical dispatch in deterministic dispatch order when it was not already present before the round. Later logical groups and all previously seen identities count as duplicates. Populate outcome unique/duplicate counts only from this helper; do not use package lengths or sums of receipt-local counts as a proxy.

- [x] **Step 4: Replace used-query policy inputs**

In `controller_context.py`, derive:

```python
used_keys = sorted(used_term_group_keys(run_state.retrieval_state.query_execution_ledger))
previous_outcomes = run_state.round_history[-1].query_outcomes[-2:] if run_state.round_history else []
```

Pass them to `ControllerContext`. In `validate_controller_decision()`, canonicalize terms, compute `build_term_group_key()`, and return exactly `"proposed_term_group_already_executed"` when the key is used.

In `query_plan.py`, replace the ordered `used_queries` tuple logic with the passed `used_term_group_keys` and delete the `used_candidates` list plus fallback. `derive_explore_query_terms()` returns `None` when all valid groups collide.

In `second_lane_runtime.py`, set `no_fetch_reason="no_novel_generic_explore_query"` when generic exploration returns `None`. Check PRF's group key against used keys before creating its logical query.

Call the same novelty assertion after the runtime builds its full logical bundle so controller repair, PRF, and rescue cannot bypass it. Rescue routes to its existing stop result when no unseen anchor-only group exists.

- [x] **Step 5: Update physical-history compatibility without source branches**

Keep all existing `SentQueryRecord` persistence and city-level artifacts. Update callers that answer novelty, tried-family, or broadening questions to read `query_execution_ledger`; retain `sent_query_history` only in diagnostics that need city/batch fields. Do not add source-name conditionals.

- [x] **Step 6: Run no-replay regression suite**

Run:

```bash
uv run pytest -q tests/test_query_execution_contract.py tests/test_query_plan.py tests/test_second_lane_runtime.py tests/test_controller_contract.py tests/test_runtime_state_flow.py tests/test_runtime_multi_source_round_dispatch.py
```

Expected: PASS. The exhaustion case must now return `None`/rescue rather than a previously used group.

- [x] **Step 7: Commit ledger and policy changes**

```bash
git add src/seektalent/runtime/orchestrator.py src/seektalent/runtime/controller_context.py src/seektalent/controller/react_controller.py src/seektalent/runtime/round_decision_runtime.py src/seektalent/retrieval/query_plan.py src/seektalent/runtime/second_lane_runtime.py src/seektalent/runtime/rescue_execution_runtime.py tests/test_query_plan.py tests/test_second_lane_runtime.py tests/test_controller_contract.py tests/test_runtime_state_flow.py tests/test_runtime_multi_source_round_dispatch.py
git commit -m "fix: prevent logical query replay"
```

### Task 4: Add Bounded Controller And Reflection Evidence

**Files:**

- Modify: `src/seektalent/runtime/reflection_context.py`
- Modify: `src/seektalent/reflection/critic.py`
- Modify: `src/seektalent/runtime/reflection_runtime.py`
- Modify: `src/seektalent/prompts/reflection.md`
- Modify: `src/seektalent/runtime/runtime_diagnostics.py`
- Test: `tests/test_context_builder.py`
- Test: `tests/test_llm_input_prompts.py`
- Test: `tests/test_reflection_contract.py`
- Test: `tests/test_runtime_audit.py`

**Interfaces:**

- Consumes `RoundState.query_outcomes` and `ControllerDecision` from Task 3.
- Produces a current-round, maximum-two-query `ReflectionContext.query_outcomes` evidence block.

- [x] **Step 1: Write failing context and prompt tests**

Add:

```python
def test_reflection_context_contains_controller_decision_and_current_query_outcomes() -> None:
    context = build_reflection_context(run_state=_run_state_with_two_outcomes(), round_state=_round_state())
    assert context.controller_decision.response_to_reflection == "Kept the role anchor; changed the support skill."
    assert [item.query_instance_id for item in context.query_outcomes] == ["primary-2", "explore-2"]


def test_reflection_prompt_renders_safe_query_evidence_only() -> None:
    prompt = render_reflection_prompt(_reflection_context_with_outcomes())
    assert "CONTROLLER DECISION" in prompt
    assert "QUERY OUTCOMES" in prompt
    assert "rawCandidateCount" in prompt
    assert "https://h.liepin.com" not in prompt
    assert "candidate-" not in prompt
```

- [x] **Step 2: Run the tests and verify they fail**

Run:

```bash
uv run pytest -q tests/test_context_builder.py tests/test_llm_input_prompts.py tests/test_reflection_contract.py tests/test_runtime_audit.py
```

Expected: FAIL because `ReflectionContext` has neither field and its prompt cannot render them.

- [x] **Step 3: Assemble bounded evidence and render it**

In `reflection_context.py`, pass the current `round_state.controller_decision` and `round_state.query_outcomes[:2]`. In `critic.py`, append two typed, untrusted JSON blocks:

```python
"CONTROLLER DECISION\n" + render_untrusted_json_block(
    "CONTROLLER_DECISION", context.controller_decision.model_dump(mode="json")
),
"QUERY OUTCOMES\n" + render_untrusted_json_block(
    "QUERY_OUTCOMES", [item.model_dump(mode="json") for item in context.query_outcomes],
),
```

Update `reflection.md` with this explicit instruction:

```markdown
Evaluate the controller's rationale and response to the prior reflection against QUERY OUTCOMES. Cite only query role, lane, status, and safe aggregate counts. Do not infer facts not present in those outcomes.
```

Extend the slim diagnostics artifact with the same safe fields for audit parity; do not import diagnostics into prompt construction.

- [x] **Step 4: Run evidence-contract tests**

Run:

```bash
uv run pytest -q tests/test_context_builder.py tests/test_llm_input_prompts.py tests/test_reflection_contract.py tests/test_runtime_audit.py
```

Expected: PASS. The rendered prompt contains the two named blocks and no provider/candidate secrets.

- [x] **Step 5: Commit reflection evidence**

```bash
git add src/seektalent/runtime/reflection_context.py src/seektalent/reflection/critic.py src/seektalent/runtime/reflection_runtime.py src/seektalent/prompts/reflection.md src/seektalent/runtime/runtime_diagnostics.py tests/test_context_builder.py tests/test_llm_input_prompts.py tests/test_reflection_contract.py tests/test_runtime_audit.py
git commit -m "feat: give reflection query outcome evidence"
```

### Task 5: Publish V2 Query Groups Through Both Workbench BFFs

**Files:**

- Modify: `src/seektalent/runtime/public_events.py`
- Modify: `src/seektalent_runtime_control/stage_outputs.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `src/seektalent_ui/agent_workbench_models.py`
- Modify: `src/seektalent_ui/agent_workbench_rounds.py`
- Modify: `src/seektalent_ui/agent_workbench_response.py`
- Modify: `src/seektalent_ui/agent_workbench_stream_projection.py`
- Modify: `src/seektalent_workbench_v2/models.py`
- Modify: `src/seektalent_workbench_v2/views.py`
- Modify: `src/seektalent_workbench_v2/runtime_display.py`
- Test: `tests/test_runtime_public_event_contract.py`
- Test: `tests/test_runtime_control_event_contract.py`
- Test: `tests/test_agent_workbench_contract.py`
- Test: `tests/test_workbench_v2_service.py`

**Interfaces:**

- Consumes `LogicalQueryOutcome` from Task 3.
- Produces `queryGroups[]` in v2 stage output and both thinking-process DTO families.

- [x] **Step 1: Write failing public and BFF contract tests**

Add a public-stage test with a two-lane outcome:

```python
def test_runtime_public_feedback_v2_allows_safe_query_groups() -> None:
    payload = sanitize_runtime_public_stage_output({
        "schemaVersion": "runtime-public-stage-output/v2",
        "stage": "feedback",
        "details": {"queryGroups": [{
            "queryInstanceId": "explore-2", "termGroupKey": "group-2",
            "queryRole": "explore", "laneType": "generic_explore",
            "queryTerms": ["Platform", "Rust"], "keywordQuery": "Platform Rust",
            "lifecycle": "executed", "executionStatus": "completed", "attempted": True,
            "rawCandidateCount": 4, "uniqueCandidateCount": 2,
            "duplicateCandidateCount": 2,
            "executions": [{"sourceKind": "liepin", "status": "completed", "rawCandidateCount": 4}],
            "providerUrl": "https://h.liepin.com/private",
        }]},
    })
    group = payload["details"]["queryGroups"][0]
    assert group["queryInstanceId"] == "explore-2"
    assert "providerUrl" not in group
```

Add one legacy and one V2 view test asserting a two-lane round has exactly two `queryGroups`, while a round-one fixture has exactly one. Add a reducer test that first receives a planned-only group and then its matching executed group: it must retain one group, change `lifecycle` to `executed`, and reject a changed term-group identity.

- [x] **Step 2: Run public/BFF tests and verify they fail**

Run:

```bash
uv run pytest -q tests/test_runtime_public_event_contract.py tests/test_runtime_control_event_contract.py tests/test_agent_workbench_contract.py tests/test_workbench_v2_service.py
```

Expected: FAIL because the current schema is v1 and both BFFs model only keyword cards/packages.

- [x] **Step 3: Define the safe v2 public group shape**

In `public_events.py` and `stage_outputs.py`, set the schema constant to `runtime-public-stage-output/v2` and accept only these group keys:

```python
_PUBLIC_QUERY_GROUP_KEYS = {
    "queryInstanceId", "termGroupKey", "queryRole", "laneType", "queryTerms",
    "keywordQuery", "lifecycle", "executionStatus", "attempted", "requestedCount", "rawCandidateCount",
    "uniqueCandidateCount", "duplicateCandidateCount", "exhaustedReason", "executions",
}
_PUBLIC_QUERY_EXECUTION_KEYS = {
    "sourceKind", "status", "rawCandidateCount", "uniqueCandidateCount",
    "duplicateCandidateCount", "safeReasonCode",
}
```

Use fixed list caps of two groups per round and one execution per selected source. Sanitize strings with the existing safe text/reason helpers and drop every unlisted field.

- [x] **Step 4: Emit and reduce query groups**

In `orchestrator.py`, emit planned groups in `round_query` from logical dispatches with `lifecycle="planned"`, `executionStatus=None`, `attempted=False`, and empty executions. Emit final groups in `feedback` from `round_state.query_outcomes` with `lifecycle="executed"` and their terminal `executionStatus`. Use a single conversion helper that serializes the safe fields above.

Replace both BFFs' fixed keyword-card data model with:

```python
class AgentWorkbenchQueryGroupResponse(BaseModel):
    queryInstanceId: str
    termGroupKey: str
    queryRole: str
    laneType: str
    queryTerms: list[str] = Field(default_factory=list)
    keywordQuery: str | None = None
    lifecycle: Literal["planned", "executed"]
    executionStatus: str | None = None
    executions: list[AgentWorkbenchQueryExecutionResponse] = Field(default_factory=list)


class AgentWorkbenchThinkingProcessRoundResponse(BaseModel):
    roundNo: int
    status: AgentWorkbenchStatus
    queryGroups: list[AgentWorkbenchQueryGroupResponse] = Field(default_factory=list)
    cards: list[AgentWorkbenchThinkingProcessCardResponse] = Field(default_factory=list)
```

`cards` contains observation and reflection only. The round reducer merges planned and final data by `queryInstanceId`; it never uses title or term order as identity. It must preserve a planned group until feedback arrives, replace only execution fields on a matching final group, and reject a final group whose immutable identity differs. Make the analogous V2 models/views changes.

- [x] **Step 5: Update legacy stream summary behavior**

In `agent_workbench_stream_projection.py`, keep emitting `thinkingProcess.changed` when group-only data arrives. A round with `queryGroups` but no observation/reflection must still update the right rail.

- [x] **Step 6: Run backend contract verification**

Run:

```bash
uv run pytest -q tests/test_runtime_public_event_contract.py tests/test_runtime_control_event_contract.py tests/test_agent_workbench_contract.py tests/test_workbench_v2_service.py
scripts/verify-dev-workbench.sh
```

Expected: PASS. The verification script regenerates OpenAPI and rejects stale generated client types.

- [x] **Step 7: Commit the public/BFF contract**

```bash
git add src/seektalent/runtime/public_events.py src/seektalent_runtime_control/stage_outputs.py src/seektalent/runtime/orchestrator.py src/seektalent_ui/agent_workbench_models.py src/seektalent_ui/agent_workbench_rounds.py src/seektalent_ui/agent_workbench_response.py src/seektalent_ui/agent_workbench_stream_projection.py src/seektalent_workbench_v2/models.py src/seektalent_workbench_v2/views.py src/seektalent_workbench_v2/runtime_display.py tests/test_runtime_public_event_contract.py tests/test_runtime_control_event_contract.py tests/test_agent_workbench_contract.py tests/test_workbench_v2_service.py apps/web-react/src/lib/api/schema.d.ts
git commit -m "feat: publish logical query groups to workbench"
```

### Task 6: Render Typed Query Groups In React And Verify The Complete Slice

**Files:**

- Modify: `apps/web-react/src/lib/api/agentWorkbenchTypes.ts`
- Modify: `apps/web-react/src/lib/api/workbenchV2Types.ts`
- Modify: `apps/web-react/src/components/workbench/ThinkingProcessRail.tsx`
- Modify: `apps/web-react/src/components/workbench/ThinkingProcessRail.css`
- Modify: `apps/web-react/src/components/workbench/ThinkingProcessRail.test.tsx`
- Modify: `apps/web-react/src/test/fixtures/agentWorkbenchBff.ts`
- Modify: `apps/web-react/src/components/workbench/ThinkingProcessRail.stories.tsx`
- Modify: `apps/web-react/tests/storybook-visual.spec.ts`

**Interfaces:**

- Consumes `queryGroups` returned by Task 5's typed BFF contracts.
- Produces an accessible outer `关键词` panel with stable-ID logical-query subsections.

- [x] **Step 1: Write failing React tests**

Add to `ThinkingProcessRail.test.tsx`:

```tsx
it("renders actual two-lane groups with stable query-instance labels", () => {
  render(<ThinkingProcessRail candidates={[]} thinkingProcess={{
    activeRoundNo: 2,
    rounds: [{
      roundNo: 2, status: "running", cards: [],
      queryGroups: [
        { queryInstanceId: "primary-2", termGroupKey: "g1", queryRole: "exploit", laneType: "primary", queryTerms: ["Platform", "Python"], keywordQuery: "Platform Python", lifecycle: "executed", executionStatus: "completed", executions: [{ sourceKind: "liepin", status: "completed", rawCandidateCount: 3, uniqueCandidateCount: 2, duplicateCandidateCount: 1 }] },
        { queryInstanceId: "explore-2", termGroupKey: "g2", queryRole: "explore", laneType: "generic_explore", queryTerms: ["Platform", "Rust"], keywordQuery: "Platform Rust", lifecycle: "executed", executionStatus: "partial", executions: [{ sourceKind: "liepin", status: "partial", rawCandidateCount: 1, uniqueCandidateCount: 1, duplicateCandidateCount: 0 }] },
      ],
    }],
  }} />)

  expect(screen.getByText("Platform Python")).toBeInTheDocument()
  expect(screen.getByText("Platform Rust")).toBeInTheDocument()
  expect(screen.getByText("exploit")).toBeInTheDocument()
  expect(screen.getByText("generic_explore")).toBeInTheDocument()
})
```

- [x] **Step 2: Run the React test and verify it fails**

Run:

```bash
cd apps/web-react && pnpm test -- ThinkingProcessRail.test.tsx
```

Expected: TypeScript/test failure because `queryGroups` is not part of either normalized type.

- [x] **Step 3: Normalize query-group DTOs without raw fallback parsing**

Add shared normalized types in both API adapters:

```ts
export type ThinkingProcessQueryExecution = {
  sourceKind: string
  status: string
  rawCandidateCount: number | null
  uniqueCandidateCount: number | null
  duplicateCandidateCount: number | null
  safeReasonCode?: string | null
}

export type ThinkingProcessQueryGroup = {
  queryInstanceId: string
  termGroupKey: string
  queryRole: string
  laneType: string
  queryTerms: string[]
  keywordQuery: string | null
  lifecycle: "planned" | "executed"
  executionStatus: string | null
  executions: ThinkingProcessQueryExecution[]
}
```

Normalize absent optional arrays to `[]`; do not parse `card.text`, provider events, or localized labels to reconstruct groups.

- [x] **Step 4: Render group subsections with stable keys**

In `ThinkingProcessRail.tsx`, render one outer keyword section when `round.queryGroups.length > 0`:

```tsx
<section className="thinking-card" aria-label="关键词">
  <h3>关键词</h3>
  <div className="thinking-query-groups">
    {round.queryGroups.map((group) => (
      <section className="thinking-query-group" key={group.queryInstanceId}>
        <div className="thinking-query-group__header">
          <strong>{group.keywordQuery ?? group.queryTerms.join(" · ")}</strong>
          <span>{group.laneType}</span>
          <span>{group.executionStatus ?? "planned"}</span>
        </div>
        <div className="thinking-card__terms">
          {group.queryTerms.map((term) => <span key={`${group.queryInstanceId}:${term}`}>{term}</span>)}
        </div>
        <ul aria-label={`${group.queryRole} source executions`}>
          {group.executions.map((execution) => (
            <li key={`${group.queryInstanceId}:${execution.sourceKind}`}>
              {`${execution.sourceKind}: ${execution.status}`}
            </li>
          ))}
        </ul>
      </section>
    ))}
  </div>
</section>
```

Render observation/reflection cards after this section. Delete title-as-key usage. Use existing Workbench spacing/color tokens; do not add a visual graph, raw source values, or a second artificial lane.

- [x] **Step 5: Update fixtures, story, and visual proof**

Add round-one single-group and round-two two-group fixtures. Update the rail story so the WTS outer card rhythm remains intact while its keyword card contains labelled group subsections. Update the Playwright visual assertion to cover both group counts.

- [x] **Step 6: Run frontend verification**

Run:

```bash
cd apps/web-react
pnpm test -- ThinkingProcessRail.test.tsx ConversationScreenV2.test.tsx
pnpm check
pnpm lint
```

Expected: PASS. React renders actual group count, keyboard-visible semantic sections, and no duplicate-key console warnings.

- [x] **Step 7: Run complete slice verification and commit**

Run:

```bash
uv run pytest -q tests/test_query_execution_contract.py tests/test_runtime_source_adapter_boundary.py tests/test_liepin_runtime_source_lane.py tests/test_runtime_multi_source_round_dispatch.py tests/test_query_plan.py tests/test_second_lane_runtime.py tests/test_controller_contract.py tests/test_context_builder.py tests/test_llm_input_prompts.py tests/test_reflection_contract.py tests/test_runtime_public_event_contract.py tests/test_runtime_control_event_contract.py tests/test_agent_workbench_contract.py tests/test_workbench_v2_service.py
uv run python tools/check_arch_imports.py
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
git diff --check
```

Expected: all commands exit zero. If `tach check` still reports pre-existing unrelated violations, record them separately; do not weaken Tach configuration in this change.

Commit:

```bash
git add apps/web-react/src/lib/api/agentWorkbenchTypes.ts apps/web-react/src/lib/api/workbenchV2Types.ts apps/web-react/src/components/workbench/ThinkingProcessRail.tsx apps/web-react/src/components/workbench/ThinkingProcessRail.css apps/web-react/src/components/workbench/ThinkingProcessRail.test.tsx apps/web-react/src/test/fixtures/agentWorkbenchBff.ts apps/web-react/src/components/workbench/ThinkingProcessRail.stories.tsx apps/web-react/tests/storybook-visual.spec.ts
git commit -m "feat: render workbench logical query groups"
```

## Completion Verification (2026-07-11)

All six delivery tasks are implemented on `main` (`cee9c7cc`). The following is a current-state verification record, rather than an assertion that an old red test still fails after completion.

| Task | Verified implementation and evidence |
| --- | --- |
| 1. Receipt, outcome, and semantic identity foundations | `QueryExecutionReceipt`, `LogicalQueryOutcome`, persisted ledger/outcomes, and order-independent family identity are present. `tests/test_query_execution_contract.py` covers identity, aggregation, attempted state, and duplicate-group rejection. |
| 2. One terminal receipt per source intent | CTS and Liepin both preserve `query_instance_id`/`term_group_key` and emit terminal receipts. `tests/test_runtime_source_adapter_boundary.py`, `tests/test_liepin_runtime_source_lane.py`, and `tests/test_runtime_multi_source_round_dispatch.py` cover successful, blocked, failed, and parity paths. |
| 3. Persist truth and prevent replay | Runtime appends receipt truth, derives logical outcomes, and rejects prior or in-bundle term-group reuse before dispatch. `tests/test_query_execution_contract.py`, `tests/test_query_plan.py`, `tests/test_second_lane_runtime.py`, `tests/test_controller_contract.py`, and `tests/test_runtime_state_flow.py` cover the policy. |
| 4. Controller/reflection evidence | Bounded query outcomes and controller decision/response evidence reach reflection and prompts. `tests/test_context_builder.py`, `tests/test_llm_input_prompts.py`, `tests/test_reflection_contract.py`, and `tests/test_runtime_audit.py` cover the assembled contract. |
| 5. Safe public query groups | V2 events, RuntimeControl, legacy projection, and both BFFs use canonical `queryGroups` with public-field sanitization. `tests/test_runtime_public_event_contract.py`, `tests/test_runtime_control_event_contract.py`, `tests/test_agent_workbench_contract.py`, and `tests/test_workbench_v2_service.py` cover one- and two-lane groups and unsafe payload rejection. |
| 6. Typed React rendering | The shared rail uses `queryInstanceId` as its stable key and renders actual groups from typed DTOs. `apps/web-react` passed 170 tests, type check, and lint; focused 375px interaction and visual stories each passed. |

Current verification completed after the merge:

- The combined logical-query/detail-claim focused Python suite passed: **824 tests**.
- The repository Python suite passed: **3476 tests**.
- `apps/web-react` passed `pnpm test` (**170 tests**), `pnpm check`, and `pnpm lint`; the compact dual-lane 375px Storybook interaction and visual checks each passed.
- `uv run python tools/check_arch_imports.py` passed, and `git diff --check` is re-run for this documentation update.

Two repository-wide checks remain unable to satisfy the plan's historical “exit zero” expectation, but the failures pre-date the plan base commit `c6fc0e57` and are unchanged by this delivery: `tools/check_source_boundaries.py` reports the two `normalized_artifacts.py:8` Liepin branches, and `tools/check_tach_baseline.py` reports the three existing imports in `liepin_site_adapter.py` and `workbench_liepin_start_probe.py`. The Tach baseline is empty, so that tool still labels those old imports “New”; this record does not claim that either gate passes. They are recorded as unrelated baseline debt, not attributed to this slice.
