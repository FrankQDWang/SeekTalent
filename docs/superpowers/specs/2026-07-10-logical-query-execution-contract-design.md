# Logical Query Execution Contract Design

> **Date:** 2026-07-10
> **Status:** Implemented for the v0.7.40 candidate; deterministic and package verification complete on 2026-07-12, with the real Domi production acceptance still blocked because the isolated execution process did not receive the user JWT environment variable and a pre-existing Domi-owned listener occupies port 8011
> **Owner:** SeekTalent runtime and Workbench

## Summary

SeekTalent currently treats a source query as two different facts:

- CTS records a physical `SentQueryRecord` after retrieval;
- Liepin returns a lane result and a display-oriented `RuntimeQueryPackage`.

The second path does not enter `sent_query_history`. As a result, the default Liepin/OpenCLI product path can execute a logical query that the controller, reflection, stop guidance, diagnostics, and Workbench history do not regard as executed. The same contract loss also causes the ThinkingProcess UI to collapse actual lanes into one keyword card.

This design makes one source-neutral logical-query receipt the authoritative execution fact. The 2026-07-11 amendment strengthens novelty from exact-group uniqueness to run-wide, non-anchor family at-most-once execution. It also reduces the Workbench keyword section to the only information users asked for: the main path keywords and, when present, the expansion path keywords.

## Confirmed Decisions

- A **logical query** is one planned query lane, independent of how many sources execute it.
- A **source execution receipt** is one terminal result for one `RuntimeSourceQueryIntent`.
- A logical query becomes **used for novelty** only when at least one receipt says that external execution started. A preflight-blocked query is observable but is not marked used.
- `SentQueryRecord` remains a CTS/city physical-attempt audit record. It is not renamed, overloaded, or made to impersonate the logical-query ledger.
- `term_group_key` represents the unordered semantic term-family set. It remains useful for exact-group identity, but it is not sufficient for run-wide family novelty.
- The compiler primary-anchor family may repeat. Every non-anchor family becomes consumed when its logical query has any `dispatch_started=True` receipt; a consumed family cannot appear in any later round or sibling lane.
- Automatic replay is not part of this product slice. When no unseen valid group exists, runtime chooses existing rescue/stop behavior rather than silently resending a group.
- The Workbench must display the actual number of logical query paths. Round one and anchor-only rounds may correctly show only `主路径`; later rounds may show `主路径` and `扩展路径`. The product must never invent a second path for presentation.
- The keyword section displays only the path label and its deduplicated `queryTerms`. It does not display lifecycle badges, provider/source status, keyword-query prose, raw/new/duplicate counts, or source execution rows.
- This is a breaking pre-1.0 public-stage/BFF contract change. New runs use the new contract; there is no historical backfill or heuristic reconstruction of old query groups.
- Both V2 and legacy conversation projections must consume the new contract while both routes remain live.

## Goals

1. Every `RuntimeSourceQueryIntent` produces exactly one terminal `QueryExecutionReceipt`.
2. A completed or partially completed Liepin logical query becomes visible in the authoritative execution ledger.
3. The primary anchor is the only family allowed to repeat; every attempted non-anchor family is globally unique across rounds and sibling lanes.
4. Controller, reflection, stop guidance, and Workbench use the same receipt-derived facts.
5. ThinkingProcess preserves query-instance and lane identity internally while rendering only one deduplicated keyword list for each visible path.
6. Reflection evaluates the current controller decision against current per-query evidence.

## Non-Goals

- Do not move all agent behavior into a new `agent_core/` package.
- Do not split `models.py` as part of this correctness slice.
- Do not change how PRF is proposed or scored; this amendment changes only explicit PRF family-identity persistence and consumed-family rejection/fallback before dispatch.
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
    non_anchor_term_family_ids: list[str]
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

`RuntimeSourceQueryIntent` remains the complete plan input. Preserve `query_instance_id`, `term_group_key`, and `non_anchor_term_family_ids` through `LogicalQueryState -> LogicalQueryDispatch -> RuntimeSourceQueryIntent -> QueryExecutionReceipt -> LogicalQueryOutcome`. The package remains a small summary transport, but it is no longer anonymous. Family identity is resolved during planning and persisted; later code must not reconstruct it from a mutable term pool.

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
    non_anchor_term_family_ids: list[str]
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

Runtime appends all receipts for observability. It derives used exact groups and consumed non-anchor families only from attempted logical outcomes. Existing `sent_query_history` remains available for city-level diagnostics until those callers have a precise reason to use physical attempts.

Replace novelty consumers in controller context, second-lane selection, feedback/rescue logic, and stop guidance with the receipt-derived attempted logical ledger. Preserve `sent_query_history` only where city or batch detail is the actual requirement.

### 4. Exact-Group Identity And Run-Wide Family Novelty

`term_group_key` remains the exact unordered group identity. Add one shared resolver that returns the semantic members used both for that key and for family consumption:

```python
@dataclass(frozen=True)
class ResolvedQueryIdentity:
    term_group_key: str
    primary_anchor_family_id: str
    non_anchor_term_family_ids: tuple[str, ...]


def resolve_query_identity(
    *,
    query_terms: Sequence[str],
    query_term_pool: Sequence[QueryTermCandidate],
    explicit_family_overrides: Mapping[str, str] | None = None,
) -> ResolvedQueryIdentity: ...
```

The resolver canonicalizes surface terms, resolves compiler families, uses a normalized `term:<value>` fallback only when no family exists, rejects two surfaces from the same family inside one query, and produces a stable ordered tuple. Every supplied override key and value must normalize to a non-empty value or resolution fails. A PRF expression must pass its `accepted_prf_term_family_id` as an explicit override; a pool-external PRF term must not silently fall back to surface identity when a stable family ID is already known.

The identities have distinct purposes:

| Identity | Purpose | Includes source/filters/round? |
| --- | --- | --- |
| `term_group_key` | exact unordered group identity | no |
| `non_anchor_term_family_ids` | run-wide family consumption | no |
| `query_fingerprint` | provider execution identity | yes |
| `query_instance_id` | one run/round execution instance | yes |

Runtime derives consumed families from attempted logical outcomes:

```text
consumed_non_anchor_families = union(
  outcome.non_anchor_term_family_ids
  for outcome in logical_history
  if outcome.attempted
)
```

The hard invariant is:

```text
candidate non-anchor families
∩ (consumed prior families ∪ sibling-lane families already selected this round)
= empty
```

Only the compiler primary-anchor family may repeat automatically. A preflight-blocked logical query with no started receipt consumes nothing. Completed, partial, and failed-after-start outcomes all consume their non-anchor families. One logical query executed by several sources consumes its family set once.

Novelty is enforced at every decision boundary and once more immediately before dispatch:

- controller validation and runtime sanitization reject or deterministically repair proposals containing consumed families;
- the exploit selector preserves still-fresh proposed families, then fills from the deterministic fresh-family order;
- generic explore excludes prior consumed families and every family already selected by the current exploit lane;
- PRF falls back to fresh generic explore when its explicit family conflicts with history or the current exploit lane;
- reserve and candidate-feedback terms must introduce a fresh family;
- anchor-only rescue is allowed only when its exact anchor-only group remains unused;
- before the controller LLM runs, runtime returns to normal planning only when a fresh active/controller-selectable family exists; otherwise it deterministically tries a fresh inactive reserve, enabled/unattempted candidate feedback, unused anchor-only, then the canonical `query_family_exhausted` stop;
- `query_family_exhausted` is a correctness terminal and remains legal when ordinary stop guidance or minimum-round policy says stop is not yet allowed, because no legal query remains; the terminal path invokes neither the controller nor a source adapter and preserves that public stop reason unchanged.

The dispatch gate checks both exact group keys and family-set disjointness. This slice has no `replay_reason_code`. Strategic replay requires a separate product decision, explicit user-visible reason, and a new spec.

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

The runtime public stage and BFF retain those safe fields for control-plane evidence and typed identity merging. The React keyword section deliberately derives a minimal visible model:

```text
ThinkingProcessRound
  queryGroups[]                 # rich typed data, not rendered directly
  visibleKeywordPaths[]         # derived in the React component
    pathId                      # queryInstanceId
    pathType: main | expansion
    keywords[]                  # canonical queryTerms only
  observation
  reflection
```

The projection maps `exploit -> main` and both `generic_explore` and `prf_probe -> expansion`. It preserves runtime order and emits at most one main path and one expansion path. `pathId` is the logical query instance ID and is used only as the stable React key.

`keywords` comes only from canonical `queryTerms`. The projection removes empty values and deduplicates normalized terms while preserving their first display spelling and order. React renders each keyword exactly once. It must not also render `keywordQuery`, because doing so repeats every visible keyword as prose plus the plain keyword line.

The visible query-path block contains only:

```text
主路径
关键词一、关键词二

扩展路径                    # only when a second lane actually exists
关键词三、关键词四
```

There is no visible `关键词` subheading, card shell, border, background panel, pill/chip border, status badge, provider name, source execution row, raw/new/duplicate count, keyword-query sentence, query ID, term-group key, or secondary microcopy. Path labels and keyword text use the normal product text scale; keywords are plain inline text joined with `、`. `observation` and `reflection` remain separate cards below this block.

The round reducer still keys source-stage groups by `queryInstanceId`: it first accepts the planned group, then replaces it with the matching executed truth without creating a second path. React derives the minimal visible paths only after this identity merge. Rich counts/status/source fields remain available to diagnostics and tests but are not rendered in the keyword section.

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
5. Except for the primary anchor, attempted non-anchor family IDs are globally unique across all rounds and sibling lanes.
6. A controller proposal, generic explore candidate, or PRF expression containing a consumed family is repaired or replaced deterministically before dispatch.
7. Final family exhaustion produces canonical public reason `query_family_exhausted` with zero controller/source calls, even when ordinary stop guidance is false; it never replays or raises an ordinary run failure.
8. Round one displays only `主路径`; a later two-lane round displays `主路径` and `扩展路径` once each.
9. Each visible keyword appears exactly once in its path; keyword-query prose, counts, statuses, and source rows are absent.
10. One logical query executed by two sources still renders one keyword path and does not expose two source rows.
11. Reflection receives the controller decision, response to reflection, receipt-derived consumed-family truth, and safe per-query current-round outcomes.
12. No public Workbench payload contains provider URL, browser ref, raw filter, or candidate ID.
13. A planned-only group transitions to its matching executed group without creating a second UI path.

## Test Strategy

Add focused tests before implementation for:

- CTS and Liepin intent-to-terminal-receipt conformance;
- receipt aggregation and attempted ledger semantics;
- blocked-before-start versus failed-after-start novelty behavior;
- same-family reordered term groups;
- `[anchor,A]` followed by `[anchor,A,B]` family-conflict repair;
- history/exploit/explore sibling-family disjointness;
- PRF family conflict fallback and explicit PRF family persistence;
- alias terms resolving to one consumed family;
- no-replay behavior after family exhaustion;
- controller duplicate-family primary-group rejection/repair;
- four-round AI Agent regression where only the primary anchor repeats;
- V2 and legacy public projection of one and two query groups;
- minimal Workbench keyword-path projection;
- React assertions that each term renders once and hidden keyword-query/count/status/source text is absent;
- reflection context inclusion of decision and bounded outcomes;
- public-stage v2 sanitization and rejection of raw provider fields.

Run the focused Python and frontend suites, then repository contract/boundary checks before merge.

## Implementation Status And Reopened Evidence (2026-07-11)

The original exact-group design shipped on `main` beginning at `cee9c7cc`; its task-by-task completion record remains in the [logical-query implementation plan](../plans/2026-07-10-logical-query-execution-contract.md). Receipt parity, exact unordered group keys, controller/reflection query evidence, and typed Workbench groups are implemented.

The clean v0.7.39 production run `rtrun_928387d3d87d4263890b8b3a2247c257` proves the remaining family-level defect. Its seven attempted groups have seven unique `term_group_key` values, but non-anchor families repeat: `Multi-Agent` three times, `记忆系统` twice, and `Python` twice. The ledger is complete and every receipt is attempted; the defect is novelty granularity, not another Liepin receipt loss.

The current React rail also renders `keywordQuery` prose and the same `queryTerms` chips, so each keyword appears twice. It additionally renders lifecycle, raw/new/duplicate counts, and source execution rows that are not part of the requested keyword view. The screenshot-confirmed `原始 6 / 3` values are provider scan caps under the old `2/1 × 3` policy, not unique candidates or opened details.

The family-novelty and minimal-UI amendment is implemented in the v0.7.40 candidate together with deterministic scoring, the `3/2` Liepin baseline, the Workbench 60-point projection threshold, and fixed first-page expansion. The execution record belongs to the [candidate quality and first-page expansion plan](../plans/2026-07-11-candidate-quality-first-page-expansion.md). Exact-group tests remain required, but the release gate now also proves family consumption, sibling disjointness, deterministic exhaustion, click-before-dedup prevention, and private continuation boundaries.
