# Logical Query Execution Contract Design

> **Date:** 2026-07-10
> **Status:** Approved design, implemented and verified on 2026-07-11
> **Owner:** SeekTalent runtime and Workbench

## Summary

SeekTalent currently treats a source query as two different facts:

- CTS records a physical `SentQueryRecord` after retrieval;
- Liepin returns a lane result and a display-oriented `RuntimeQueryPackage`.

The second path does not enter `sent_query_history`. As a result, the default Liepin/OpenCLI product path can execute a logical query that the controller, reflection, stop guidance, diagnostics, and Workbench history do not regard as executed. The same contract loss also causes the ThinkingProcess UI to collapse actual lanes into one keyword card.

This design makes one source-neutral logical-query receipt the authoritative execution fact. It also adds a semantic term-group identity, uses that identity as a no-replay invariant, gives reflection bounded query-level evidence, and exposes actual query groups through both live Workbench projections.

## Confirmed Decisions

- A **logical query** is one planned query lane, independent of how many sources execute it.
- A **source execution receipt** is one terminal result for one `RuntimeSourceQueryIntent`.
- A logical query becomes **used for novelty** only when at least one receipt says that external execution started. A preflight-blocked query is observable but is not marked used.
- `SentQueryRecord` remains a CTS/city physical-attempt audit record. It is not renamed, overloaded, or made to impersonate the logical-query ledger.
- `term_group_key` represents the unordered semantic term-family set. It is independent of source, round, lane, order, and execution fingerprint.
- Automatic replay is not part of this product slice. When no unseen valid group exists, runtime chooses existing rescue/stop behavior rather than silently resending a group.
- The Workbench must display the actual number of logical query groups. Round one and anchor-only rounds may correctly have one group; later rounds may have one or two groups. The product must never invent a second lane for presentation.
- This is a breaking pre-1.0 public-stage/BFF contract change. New runs use the new contract; there is no historical backfill or heuristic reconstruction of old query groups.
- Both V2 and legacy conversation projections must consume the new contract while both routes remain live.

## Goals

1. Every `RuntimeSourceQueryIntent` produces exactly one terminal `QueryExecutionReceipt`.
2. A completed or partially completed Liepin logical query becomes visible in the authoritative execution ledger.
3. Semantically identical term groups cannot execute twice in one run merely because terms are reordered or a source changes.
4. Controller, reflection, stop guidance, and Workbench use the same receipt-derived facts.
5. ThinkingProcess preserves query-instance identity, lane identity, source execution status, and bounded counts without exposing provider payloads.
6. Reflection evaluates the current controller decision against current per-query evidence.

## Non-Goals

- Do not move all agent behavior into a new `agent_core/` package.
- Do not split `models.py` as part of this correctness slice.
- Do not change how PRF is proposed or scored beyond preventing automatic replay of an already-used group.
- Do not make raw provider requests, filters, URLs, candidate identifiers, or browser payloads visible to React.
- Do not retain the old ThinkingProcess card schema as a compatibility path.
- Do not backfill historic stage outputs or historic conversations.
- Do not alter scoring concurrency or candidate scoring semantics.

## Current Failure Flow

```text
LogicalQueryDispatch
  -> RuntimeSourceQueryIntent (identity and requested count are present)
  -> RuntimeQueryPackage (identity is discarded)
  -> Liepin RuntimeSourceLaneResult (packages only)
  -> SourceRoundAdapterResult
  -> aggregation reads only RetrievalExecutionResult.sent_query_records
  -> sent_query_history remains unchanged
```

The same lossy package is then used by Workbench projection, where multiple packages are flattened into one `关键词` card.

## Design

### 1. Source-Neutral Query Execution Receipt

Add a persisted Pydantic DTO named `QueryExecutionReceipt`. It is a terminal source execution fact, not a provider request and not a UI DTO.

```python
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
    dispatch_started: bool
    status: Literal["completed", "partial", "blocked", "failed"]
    raw_candidate_count: int = 0
    unique_candidate_count: int = 0
    duplicate_candidate_count: int = 0
    exhausted_reason: str | None = None
    safe_reason_code: str | None = None
```

Rules:

- `dispatch_started=True` means the source received or began the external query. This includes a failure after dispatch.
- `blocked` before provider/browser query submission has `dispatch_started=False`.
- Counts are non-negative and describe only this source plus this query instance. Receipt unique/duplicate counts are source-local; they are not summed to make a cross-source logical result.
- A source must not emit two receipts for the same `(round_no, source_kind, query_instance_id)` within a run.
- All receipts are terminal. Streaming provider details stay in existing source events rather than extending this DTO.

`RuntimeSourceQueryIntent` remains the complete plan input. Add `term_group_key` to it, then preserve both `query_instance_id` and `term_group_key` in `RuntimeQueryPackage`. The package remains a small summary transport, but it is no longer anonymous.

### 2. Adapter and Dispatch Contract

Extend `RuntimeSourceLaneResult` and `SourceRoundAdapterResult` with source-neutral `query_execution_outcomes`. The adapter, not the aggregator, owns the source-specific answer to these questions:

- which intent actually started;
- which terminal status applied;
- how many raw/new/duplicate candidates that query produced.

CTS adapts its existing physical city attempts into one receipt per query intent. `SentQueryRecord` continues to capture city/batch-level detail underneath it.

Liepin produces one outcome per logical query it runs. Its existing `RuntimeSourceLaneRequest` already carries logical query ID, fingerprint, role, terms, and requested count; the lane result must return those facts with a terminal outcome instead of only display packages. The source dispatcher combines each outcome with its original intent to construct the persisted receipt.

`dispatch_source_rounds()` validates two invariants before returning:

```text
for each selected source:
  requested intents == terminal receipts by query_instance_id

for every receipt:
  receipt.source_kind == adapter result source
  receipt.round_no == request.round_no
```

The only allowed exception is an invariant failure that stops the round. A preflight-blocked adapter may explicitly return a non-started `blocked` outcome for every intended query. Any partial/failed adapter result without an explicit per-intent started/not-started outcome is an invariant failure, not a reason to manufacture non-started receipts. The runtime must never branch on a source name to guess dispatch history.

### 3. Logical Execution Ledger

Persist receipts in `RetrievalState.query_execution_ledger`. Add a derived runtime helper that groups receipts by `query_instance_id` and exposes a logical outcome:

```python
class LogicalQueryOutcome(BaseModel):
    query_instance_id: str
    term_group_key: str
    query_role: QueryRole
    lane_type: LaneType
    query_terms: list[str] = Field(default_factory=list)
    keyword_query: str
    attempted: bool
    status: Literal["completed", "partial", "blocked", "failed"]
    receipts: list[QueryExecutionReceipt]
    raw_candidate_count: int
    unique_candidate_count: int
    duplicate_candidate_count: int
```

`attempted` is true when any child receipt has `dispatch_started=True`. `raw_candidate_count` is the sum of raw source counts. `unique_candidate_count` and `duplicate_candidate_count` are assigned after the run-level identity merge from query-attributed candidates; they are never created by summing receipt-local counts.

Runtime retains internal `(source_kind, query_instance_id, resume_id, dedup_key)` attribution until identity merge. It unions canonical identities within one logical query across sources, then assigns a newly admitted identity to the earliest logical dispatch in deterministic dispatch order. A repeated sighting in the same group, a later logical group, or an identity already present before the round is counted as a duplicate. This attribution is never public output.

The logical outcome has no single `query_fingerprint`: source-specific fingerprints remain on child receipts. All receipts grouped under one `query_instance_id` must agree on term-group key, role, lane, canonical term sequence, and keyword query, but source-specific execution fingerprints may differ.

Logical status is deterministic:

| Receipt statuses | Logical status |
| --- | --- |
| all `completed` | `completed` |
| all `blocked` | `blocked` |
| all `failed` | `failed` |
| any `partial`, or any mixed terminal statuses | `partial` |

Runtime appends all receipts for observability. It derives the used term groups only from attempted logical outcomes. Existing `sent_query_history` remains available for city-level diagnostics until those callers have a precise reason to use physical attempts.

Replace novelty consumers in controller context, second-lane selection, feedback/rescue logic, and stop guidance with the receipt-derived attempted logical ledger. Preserve `sent_query_history` only where city or batch detail is the actual requirement.

### 4. Semantic `term_group_key` and No-Replay Policy

Add a pure helper beside query identity code:

```python
def build_term_group_key(
    *,
    query_terms: Sequence[str],
    query_term_pool: Sequence[QueryTermCandidate],
) -> str: ...
```

The helper:

1. canonicalizes terms with the existing query-term normalization;
2. resolves each term to its compiler family;
3. uses a normalized term only when a family is unavailable;
4. deduplicates and sorts those semantic identifiers;
5. hashes a stable JSON payload to a fixed opaque key.

This key is deliberately different from `query_fingerprint`:

| Identity | Purpose | Includes source/filters/round? |
| --- | --- | --- |
| `term_group_key` | semantic novelty | no |
| `query_fingerprint` | provider execution identity | yes |
| `query_instance_id` | one run/round execution instance | yes |

After controller canonicalization and before source dispatch, runtime checks every logical query group against attempted prior groups and against other current-round logical queries. A duplicate cannot reach a source.

For an invalid duplicate:

- primary/exploit queries use the deterministic unseen-group selector;
- second lanes use an unseen generic/PRF candidate only;
- rescue may use anchor-only only when it creates an unseen group;
- if none is available, runtime follows the existing rescue/stop policy without dispatching a replay.

This slice has no `replay_reason_code`. Adding strategic replay later requires a separate product decision, explicit user-visible reason, and a new spec.

### 5. Controller and Reflection Evidence

Add the following bounded fields to `ReflectionContext`:

```python
controller_decision: ControllerDecision
query_outcomes: list[LogicalQueryOutcome]
```

The context includes only the current round's safe, aggregated outcomes. It does not include raw resume IDs or provider payloads.

The reflection prompt must require the model to evaluate:

```text
previous reflection claim
-> controller response_to_reflection and decision rationale
-> actual current query outcomes
-> next constrained advice
```

No separate generic debate object is introduced. `ControllerDecision.response_to_reflection` remains the controller's explicit response; receipts provide the evidence seam that is currently missing.

### 6. Public Stage Output and Workbench Query Groups

Move the public runtime-stage schema to `runtime-public-stage-output/v2`. The v2 `round_query` stage publishes planned query groups with `queryInstanceId` and `termGroupKey`. The v2 `feedback` stage publishes executed query groups derived from the receipt ledger. The two states share identity but not an invented execution status: planned groups have `lifecycle="planned"` and `executionStatus=null`; executed groups have `lifecycle="executed"` and a terminal `executionStatus`.

The public payload allows only:

```text
queryInstanceId
termGroupKey
queryRole
laneType
queryTerms
keywordQuery
sourceKind
lifecycle
executionStatus
attempted
rawCandidateCount
uniqueCandidateCount
duplicateCandidateCount
safeReasonCode
```

Do not publish provider URLs, raw filters, browser refs, candidate IDs, or error references.

Replace the fixed `cards: [关键词, observation, 反思和下一轮变更]` model with a typed round shape:

```text
ThinkingProcessRound
  queryGroups[]
  observation
  reflection
```

Each query group contains its logical ID and a list of source executions. The React rail renders a `关键词` section containing one card per group and uses `queryInstanceId` as the React key. `observation` and `reflection` remain distinct cards.

The round reducer keys by `queryInstanceId`: it first renders a planned group if that is the only event, then replaces its lifecycle/count/status fields when its executed group arrives. It rejects an executed group that changes logical identity fields.

V2 and legacy projection builders must both consume the canonical v2 public stage output. Neither path parses raw runtime events, provider payloads, or localized strings.

### 7. Migration and Compatibility

- New runtime executions emit only v2 public stage outputs.
- V2 and legacy Workbench projections accept only v2 output for new query-group rendering.
- Historic v1 output is not transformed, reconstructed, or backfilled. A historic conversation is outside this pre-1.0 breaking contract.
- Public types, generated TypeScript schema, fixtures, and contract tests change together.

## Acceptance Criteria

1. A successful Liepin query produces one attempted receipt and one logical-history entry.
2. For every selected source, every query intent has exactly one terminal receipt.
3. A blocked preflight receipt is visible but does not mark the group used.
4. Reordered terms with the same families produce the same `term_group_key`.
5. Query-group exhaustion produces rescue/stop, never automatic replay.
6. A controller-generated duplicate primary group is repaired deterministically or rejected before dispatch.
7. Round one displays one actual query group; a later two-lane round displays two actual groups.
8. One logical query executed by two sources renders one group with two source executions.
9. Reflection receives the controller decision, response to reflection, and safe per-query current-round outcomes.
10. No public Workbench payload contains provider URL, browser ref, raw filter, or candidate ID.
11. A planned-only group transitions to its matching executed group without creating a second UI group or inventing a terminal status.

## Test Strategy

Add focused tests before implementation for:

- CTS and Liepin intent-to-terminal-receipt conformance;
- receipt aggregation and attempted ledger semantics;
- blocked-before-start versus failed-after-start novelty behavior;
- same-family reordered term groups;
- no-replay behavior after group exhaustion;
- controller duplicate primary group rejection/repair;
- V2 and legacy public projection of one and two query groups;
- React rendering keys and accessible lane/source status;
- reflection context inclusion of decision and bounded outcomes;
- public-stage v2 sanitization and rejection of raw provider fields.

Run the focused Python and frontend suites, then repository contract/boundary checks before merge.

## Implementation Verification (2026-07-11)

This approved design is implemented on `main` at `cee9c7cc`. The detailed, task-by-task completion record is in the [logical-query implementation plan](../plans/2026-07-10-logical-query-execution-contract.md).

Current evidence verifies receipt parity across CTS/Liepin, term-group no-replay, controller/reflection query evidence, safe V2 and legacy Workbench groups, and typed React rendering. The combined focused Python suite passed 824 tests; the full Python suite passed 3476 tests; `apps/web-react` passed 170 tests, type check, and lint; the compact dual-lane 375px interaction and visual checks each passed.

The pure term-group helper was placed in `src/seektalent/retrieval/query_identity.py` during the final boundary review, while runtime aggregation remains in `src/seektalent/runtime/query_identity.py`. This is a dependency-direction correction, not a change to the approved contract.
