# Runtime-Owned Multi-Source Workbench Execution Design

## Summary

SeekTalent's first usable local BYOK milestone needs one recruiter workflow: create a Workbench session, confirm search criteria, start the agent, run CTS and Liepin as peer sources, merge likely duplicate people, choose the freshest canonical resume per person, and show one final Top 10.

The current product already has substantial pieces: CTS runtime search, Liepin Pi/OpenCLI browser execution, source evidence, identity projection, final-top10 API, Svelte Workbench parity UI, safe source state, and a runtime-owned multi-source execution baseline. This spec assumes the implementation worktree contains the baseline introduced by `dc48d44 Implement runtime-owned multi-source Workbench` or an equivalent branch with the same symbols. Public `origin/main` can lag this local baseline, so the linked plan must verify baseline files and symbols before building. The remaining defect is projection and cleanup correctness: the Workbench strategy graph still does not fully express Runtime-decided rounds, source cards can rely on stale or raw source-run counts, public payload boundaries need to be explicit, live notes can repeat unsafe/duplicate text, and OpenCLI browser cleanup needs a conservative owned-tab garbage-collection path.

This feature finishes that boundary. Runtime stays the only owner of multi-source sourcing execution. Workbench stays the session state, approval, projection, and display layer. The follow-up work makes Runtime public events the graph contract, makes final/source counts identity-safe, and closes owned browser artifacts without touching unrelated user tabs.

## Current Code Facts

- `src/seektalent_ui/workbench_store.py` already has `runtime_sourcing_jobs`, runtime job claim, runtime lease heartbeat, expired runtime job reconciliation, and Runtime finalization persistence.
- `src/seektalent_ui/workbench_routes.py` already starts primary Workbench sourcing through `start_runtime_sourcing_job(...)` instead of enqueuing independent CTS/Liepin primary source-run jobs.
- `src/seektalent_ui/runtime_bridge.py::run_runtime_sourcing_job(...)` calls `WorkflowRuntime.run(...)` once for the selected source kinds.
- `src/seektalent/runtime/logical_query_dispatch.py` already defines `LogicalQueryDispatch` with Runtime-owned query identity, fingerprint, terms, keyword query, and requested count.
- `src/seektalent/runtime/source_round_dispatch.py` already defines source-round dispatch request/result contracts and a provider-vs-invariant error boundary.
- `src/seektalent/runtime/orchestrator.py` already imports `dispatch_source_rounds(...)` and routes multi-source rounds through the mature `_run_rounds(...)` flow.
- `src/seektalent/runtime/retrieval_runtime.py::allocate_initial_lane_targets(...)` already implements the 70/30 initial allocation for exploit plus secondary logical lanes.
- `src/seektalent/runtime/rescue_router.py::choose_rescue_lane(...)` already prefers candidate feedback before anchor-only rescue when feedback seeds exist.
- `src/seektalent_ui/workbench_routes.py` already prefers `list_runtime_final_top_review_items(...)` for `/final-top10` when Runtime finalization exists, with legacy projection fallback for old sessions.
- `apps/web-svelte/src/lib/workbench/runStory.ts` still needs to treat `runtime_public_event_v1` events as the authoritative round graph backbone and keep old `cts-round-*` / `liepin-card-*` nodes only as legacy fallback.
- OpenCLI cleanup currently has lease-based cleanup behavior, but the follow-up must add an owned-page-marker GC path so orphaned SeekTalent-owned browser tabs are removed after the lease disappears without closing user-opened Liepin tabs.

## Product Contract

Runtime owns:

- source plan creation for selected source kinds
- full run lifecycle
- controller and round decision flow
- 70% exploit / 30% secondary logical query allocation
- candidate-feedback rescue priority
- generic explore fallback when candidate feedback has no safe usable seed
- per-source parallel dispatch of the same logical query bundle
- source budget enforcement
- source evidence preservation
- candidate identity merge
- canonical resume selection
- scoring after merge
- final Top 10
- safe public Runtime events and finalization payloads

Workbench owns:

- session creation and requirement triage approval
- one session-level Runtime sourcing job queue
- source card display state projected from Runtime results
- persisted candidate review/action state
- approved detail request, lease, budget, and audit state
- notes and graph rendering from public Runtime payloads

Source adapters own:

- source-specific execution only
- CTS API search
- Liepin Pi/OpenCLI browser search and card extraction
- translating source results into safe Runtime evidence

Source adapters must not choose sources, choose query strategy, decide final ranking, approve detail opens, or write Workbench graph semantics.

## Required Behavior

### One Runtime Sourcing Job Per Session

When a user starts a Workbench session with CTS and Liepin selected, Workbench must enqueue one session-level Runtime sourcing job. It must not enqueue independent CTS and Liepin source-run jobs for the primary agent run.

The Runtime job receives the selected source kinds, approved triage notes, and Liepin connection context. Source-run rows remain visible status projections, but they are not independent execution owners for the primary run.

The Runtime sourcing job uses the same lease safety as existing source-run jobs. The job runner must heartbeat the runtime job lease for the duration of execution, and expired runtime jobs must not be blindly requeued without an attempt limit. If a running runtime job already has an attached `runtime_run_id`, expiry reconciliation must treat it as an uncertain in-flight run and avoid starting a duplicate primary run unless the recovery path can prove the owner is dead.

The Workbench Start API may perform a lightweight Liepin preflight, but it must still enqueue one runtime job. If Liepin is not ready, the response may include `blockedSources` and the Liepin source projection may become blocked; CTS must still be allowed to run through the Runtime-owned job. The response must not claim that per-source execution jobs were created when the primary run is represented by a runtime sourcing job.

### Full Runtime Round Loop For Multi-Source

Multi-source Workbench execution must use the same controller and round decision loop as CTS-only Runtime execution. The mature `_run_rounds(...)` path is the canonical path for:

- requirement-derived retrieval planning
- controller decisions
- rescue decisions
- 70/30 logical query bundle construction
- candidate feedback
- generic explore fallback
- scoring
- reflection
- finalization

The simplified source-lane full-run path must not remain the Workbench primary multi-source path. It may stay for narrow lane-level APIs, approved detail enrichment, or tests, but it must not produce the main Workbench final shortlist.

### Same Logical Query Bundle Across Sources

For each Runtime search round, Runtime builds one logical query bundle. The bundle can include:

- exploit lane
- generic explore lane
- candidate feedback rescue lane
- other existing Runtime-supported logical lanes

Runtime dispatches that same logical query bundle to each selected source adapter. CTS and Liepin differ only in how they execute the source query:

- CTS maps logical queries to provider API search.
- Liepin maps logical queries to Pi/OpenCLI browser search and card extraction.

The query bundle must preserve:

- `round_no`
- `query_role`
- `lane_type`
- `query_instance_id`
- `query_fingerprint`
- `query_terms`
- `keyword_query`
- requested count per logical lane
- `source_plan_version`

The dispatch contract is explicit and immutable:

```python
@dataclass(frozen=True)
class LogicalQueryDispatch:
    round_no: int
    query_role: QueryRole
    lane_type: LaneType
    query_instance_id: str
    query_fingerprint: str
    query_terms: tuple[str, ...]
    keyword_query: str
    requested_count: int
    source_plan_version: str
```

Runtime builds `tuple[LogicalQueryDispatch, ...]` inside `_run_rounds(...)` after it chooses the 70/30/refill allocation for that round. CTS and Liepin adapters consume this dispatch tuple. They must not recompute `query_instance_id`, `query_fingerprint`, `requested_count`, or the logical lane role. Provider-specific query compilers may render source-specific syntax from the dispatch object, but the Runtime logical query identity remains authoritative.

Liepin source evidence created from a Runtime logical query must retain the dispatch `query_fingerprint` and source lane/query references. It must not replace the Runtime query identity with a `source_lane_run_id`-derived value.

### Parallel Source Dispatch

CTS and Liepin provider calls must start concurrently for a selected multi-source round. A source failure must not cancel the other source unless the failure is a Runtime invariant or programmer error.

Provider/session/login/risk/backend errors from Liepin become safe source coverage states. CTS can still produce an available-source finalization when Liepin is blocked or failed.

Runtime dispatch must distinguish source provider failures from Runtime invariant failures:

- Provider/session/backend/browser failures become source-scoped `blocked`, `failed`, or `partial` results.
- `RunStageError`, `AssertionError`, illegal source kind, identity merge invariant failure, malformed dispatch contract, and programmer errors such as unexpected `TypeError` must propagate and fail/cancel the whole round.
- The source dispatch layer must not use a broad `except Exception` that converts all failures into provider coverage.

Runtime uses these conceptual error boundaries:

```python
class SourceProviderBlocked(Exception): ...
class SourceProviderFailed(Exception): ...
class SourceProviderPartial(Exception): ...
class RuntimeSourceInvariantError(RuntimeError): ...
```

### Merge Before Ranking

Runtime must merge source results before final scoring and final Top 10 projection. The merge must preserve all source evidence and deduplicate likely same-person candidates.

There must be one Runtime merge point per search round. CTS and Liepin adapters return source-specific deltas plus source metadata; they do not mutate `RunState` directly. Runtime merges all completed/partial source outputs after the per-source fan-out joins, then rebuilds candidate identities once before scoring.

CTS retrieval metadata remains first-class. Multi-source dispatch must preserve the CTS `RetrievalExecutionResult` fields used by the mature round loop, including `cts_queries`, `sent_query_records`, `search_attempts`, `query_resume_hits`, and `provider_returned_candidates`. Multi-source support must not replace those records with fabricated empty arrays.

When CTS and Liepin return the same person with different resume versions:

- the person appears once in the final Top 10
- source evidence lists both sources
- canonical display fields come from the freshest and most complete resume
- detail evidence beats card evidence when freshness/completeness does not clearly contradict it
- Runtime keeps enough evidence for audit and notes without exposing raw provider payloads

### Final Top 10 Is Authoritative

The Workbench final shortlist and strategy graph final node must use Runtime identity-level finalization. They must not count raw candidate review items, raw source candidates, event payload candidate arrays, or graph node candidate references as the final shortlist.

Workbench persistence must derive final ranked rows from Runtime identity finalization state, preferably `RunState.top_pool_ids` and `RuntimeFinalizationRevision.candidate_identity_ids` mapped through canonical resume selection. `final_result.candidates` may provide display/rationale fields, but it is not the authoritative identity boundary.

The final list must have at most 10 identities.

Workbench must persist the Runtime finalization order separately from review item projection:

```text
runtime_finalization_revisions
  session_id
  runtime_run_id
  revision
  reason_code
  ordered_candidate_identity_ids_json
  coverage_summary_json
  created_at

runtime_candidate_identity_snapshots
  session_id
  runtime_run_id
  identity_id
  canonical_resume_id
  merged_resume_ids_json
  source_evidence_ids_json
```

`/api/workbench/sessions/{session_id}/final-top10` must first read the latest finalization revision, then map the ordered identity ids to canonical review items and all source evidence for each identity. `project_final_top_candidates(...)` may remain as a legacy fallback for sessions without runtime finalization data, but it must not be the primary ranking source for new runtime-owned runs.

When one identity has CTS and Liepin evidence, the final Top 10 row must appear once and include source evidence from both sources. Persisting only the canonical resume's source is not sufficient.

### Strategy Graph Contract

The Workbench graph must be round-centric. The Runtime controller decides how many rounds exist during the run; Workbench must not pre-allocate a fixed number of rounds. Each Runtime search round becomes one graph module. Within a round, selected sources fan out from the same logical query bundle, then fan back into a single merge/dedupe point before scoring and feedback.

For dual-source runs, the graph should show:

```text
Job
  -> Requirement triage
  -> Round 1 query bundle
      -> CTS dispatch -> CTS result
      -> Liepin dispatch -> Liepin result
  -> Round 1 merge/dedupe
  -> Round 1 scoring/top pool
  -> Round 1 feedback/next strategy
  -> Round 2 query bundle
      -> CTS dispatch -> CTS result
      -> Liepin dispatch -> Liepin result
  -> Round 2 merge/dedupe
  -> Round 2 scoring/top pool
  -> Round 2 feedback/next strategy
  -> Runtime finalization
  -> Final Top 10
```

For single-source runs, the same model degrades to a linear round:

```text
Round N query bundle -> Selected source dispatch -> Selected source result -> Round N scoring/top pool -> Round N feedback
```

The graph must not render unselected sources. If a selected source is blocked, it remains visible as a blocked source node for that round and the merge/dedupe node must show that only available-source evidence entered the merge.

Workbench must render rounds vertically and stages horizontally so many Runtime-decided rounds remain readable:

```text
Round row: Query bundle | Source dispatch/results | Merge/dedupe | Scoring/top pool | Feedback
```

Each new Runtime round starts again at the left side of the next row. The graph must not create one long horizontal chain for many rounds, and it must not keep Liepin as one global lower lane independent of round number.

Runtime public events are the graph contract. Workbench must not infer graph shape from historical node ids such as `cts-round-*`, `liepin-card-search`, or source-run job ownership. Public graph event payloads must use:

```json
{
  "schemaVersion": "runtime_public_event_v1",
  "runtimeRunId": "run_123",
  "eventId": "run_123:round:1:source_result:cts",
  "eventSeq": 42,
  "stage": "round_query|source_dispatch|source_result|merge|scoring|feedback|finalization",
  "roundNo": 1,
  "sourceKind": "cts",
  "sourcePlanId": "run_123:source:cts",
  "roundQueryBundleId": "run_123:round:1:query_bundle",
  "status": "running",
  "counts": {
    "requested": 20,
    "roundReturned": 14,
    "roundIdentities": 11,
    "topPoolCount": 10,
    "sourceCumulativeReturned": 23,
    "sourceCumulativeIdentities": 17
  },
  "safeReasonCode": null,
  "createdAt": "2026-05-22T00:00:00Z"
}
```

The `sourceKind` field is `null` for shared round stages such as query bundle, merge/dedupe, scoring, feedback, and finalization. It is set only for source-specific dispatch/result stages. Round-local counts use explicit names such as `roundReturned` and `roundIdentities`. Source-card counts must use Runtime-provided cumulative source counts, such as `sourceCumulativeReturned` and `sourceCumulativeIdentities`, or another identity-backed cumulative projection. Workbench must not sum per-round counts into `uniqueCandidatesCount`, because the same identity can appear in more than one round.

Runtime public events must be durable as well as real-time:

- Runtime emits `runtime_public_event_v1` through the progress callback for live UI updates.
- Runtime also writes the same public events to a run artifact, for example `runtime/public_events.jsonl`.
- `run_runtime_sourcing_job(...)` or Workbench completion persistence reconciles public events from that artifact after the run completes or fails.
- Workbench stores events idempotently by `eventId`, not by timestamp, so progress callback writes and completion reconciliation cannot create duplicates. This must be enforced by the store helper and by a database uniqueness invariant for `runtime_public_event_v1` rows.
- Workbench rejects a public event when `eventName` does not match the payload `stage`.
- Workbench rejects unknown public event stages; the graph contract is closed and must not persist unsupported stages as generic Runtime public events.
- Finalization public events use `stage="finalization"` and may have `roundNo=null`. The graph must use them only for final/finalization state, never as a synthetic `round-0` row.

This event envelope is also the source of live source-card progress; source cards must not remain at `0/0` while Runtime has already emitted source result counts. Completion reconciliation must backfill any missing graph/source-card events after an interrupted progress callback.

Source-card live counts must be computed through a store-level projection instead of ad hoc route code. The projection chooses status/reason and counts independently per source:

- per-source counts use the latest valid cumulative source count event ordered by `(roundNo, eventSeq)`;
- a later `blocked` or `failed` source event without cumulative counts must update status/reason without resetting previous cumulative counts to zero;
- each selected source can advance independently, so CTS can show round 2 counts while Liepin still shows round 1 counts;
- `GET /sessions/{id}` and list-session responses should consume the same projection to avoid slow route-local event scans.

Strategy graph layout must support unbounded Runtime-decided rounds. The canvas content height must grow with the number of round rows and remain scrollable. Runtime round rows must not be clamped into a fixed viewport height in a way that overlaps later rounds. A later refinement may add row collapsing, but the first implementation must keep every rendered row readable.

Dual-source round rows must reserve enough vertical space for both source result nodes inside each row. The layout must prove that six or more dual-source rows do not overlap, because source fan-out/fan-in is the default Workbench shape.

The final graph node must use the Runtime finalization-backed final Top 10 API. It must not count raw candidate review items, raw graph candidates, or event candidate arrays.

### Workbench Note And Browser Cleanup Hardening

Workbench live notes must never persist or render hidden-reasoning tags such as `<think>` or `</think>`, internal Runtime/provider terms, local paths, raw provider labels, browser command names, or unsupported numbers. The note writer must treat validation failures as expected dropped notes, but it must not hide programmer/runtime warnings that indicate an async call path is wrong.

The note writer must dedupe semantically identical progress notes across adjacent ticks. A changed event cursor or context hash must not allow the same business sentence to be appended repeatedly.

OpenCLI browser cleanup must close owned/orphaned SeekTalent Liepin tabs at the end of a run or dev-session cleanup even when the lease file is already gone. Lease-based cleanup is necessary but insufficient. Browser-state cleanup must be conservative:

- close only tabs proven to be owned by the configured OpenCLI session, using an active lease or a durable ownership marker recorded by the current session before the lease disappeared;
- never close a user-opened Liepin tab based on URL alone;
- close blank windows created by the OpenCLI-owned session when configured;
- never close unrelated user tabs;
- report counts for `leases`, `closedTabs`, and `blankWindows`;
- run during explicit cleanup and from the dev workbench shutdown path.

The durable ownership marker must be stronger than page id plus URL. It must include at least:

```json
{
  "schema_version": "seektalent.opencli_owned_page.v1",
  "session": "seektalent-liepin",
  "page_id": "page-1",
  "url": "https://h.liepin.com/search/getConditionItem#session",
  "opened_at": 1780000000.0,
  "runtime_run_id": "run_123",
  "source_lane_run_id": "run_123:source:liepin:lane:1",
  "owner_nonce": "random"
}
```

Cleanup must require marker schema validity, current configured OpenCLI session equality, marker TTL validity, and exact tab page id plus URL match. If the marker file is malformed, cleanup must fail safely or delete only the marker; it must not broaden cleanup to URL-matched tabs. Opening a newly owned OpenCLI tab may quarantine or delete a malformed stale marker file before writing a fresh marker, so local GC state does not block future real-browser runs.

## Non-Goals

This feature does not:

- redesign the Svelte UI
- add a manual Liepin card review UI
- add manual merge or unmerge controls
- change CTS-only CLI behavior unless explicitly invoked through Workbench multi-source
- optimize source strategy automatically
- add new providers beyond CTS and Liepin
- replace Pi/OpenCLI internals
- add A2A or a plugin marketplace

## Safety And Public Payload Requirements

- Public payloads must use safe reason codes and allowlisted fields.
- Runtime events must not include cookies, authorization headers, local browser state, raw provider payloads, raw resumes, local filesystem paths, or OpenCLI trace internals.
- Workbench graph and notes must show business-facing source state, not Pi/OpenCLI implementation terms.
- Liepin blocked states must be explicit and source-scoped, without downgrading CTS results.

Public API payloads and rendered UI must expose business-safe reason codes, not implementation reason codes. Internal audit artifacts may keep precise provider/backend reason codes, but Workbench session, source card, event, graph, note, final-top10, and detail/evidence serializers must map them before exposure. Reason-code mapping must be a shared public boundary, not a helper used only by Runtime public events.

The public reason taxonomy must preserve business meaning without exposing implementation names:

- `source_login_required`
- `source_account_mismatch`
- `source_browser_timeout`
- `source_browser_backend_unavailable`
- `source_browser_extension_disconnected`
- `source_browser_policy_blocked`
- `source_risk_or_verification_required`
- `source_budget_exhausted`
- `source_provider_failed`
- `source_partial`
- `source_unknown`

Examples:

```text
internal: liepin_opencli_timeout
public:   source_browser_timeout

internal: liepin_pi_mcp_config_invalid
public:   source_browser_backend_unavailable
```

Safety validation must test API responses and rendered DOM, not only source-code grep. Internal source files and tests may contain implementation terms when they are part of internal adapters, deny lists, or mapping tests.

Frontend business labels must understand the same public taxonomy. Components must not need internal codes such as `liepin_opencli_login_required` to show useful labels; `source_login_required`, `source_browser_timeout`, `source_browser_backend_unavailable`, and the rest of the public taxonomy must render as actionable business text.

## Acceptance Criteria

1. Starting a dual-source Workbench session enqueues one Runtime sourcing job and does not enqueue separate CTS and Liepin primary source jobs.
2. Workbench calls `WorkflowRuntime.run(..., source_kinds=("cts", "liepin"), ...)` once for the primary run.
3. Workbench does not call `WorkflowRuntime.run_source_lane(...)` for the primary multi-source run.
4. Runtime multi-source Workbench execution uses the mature round loop that builds logical query bundles.
5. A round with exploit plus secondary lane preserves the existing 70/30 allocation.
6. Candidate feedback is selected before generic fallback when safe feedback seed resumes exist.
7. Generic explore remains the fallback when candidate feedback has no safe usable term.
8. CTS and Liepin receive the same logical query bundle metadata for a round.
9. CTS and Liepin source dispatches both reach their provider-call barrier before either is released in the concurrency regression test.
10. A Liepin blocked or failed result does not cancel CTS and produces degraded available-source coverage.
11. Cross-source duplicate candidates merge into one Runtime identity.
12. Canonical resume selection chooses the fresher or more complete source resume for a merged identity.
13. Final Top 10 contains no more than 10 identities even when source result counts exceed 10.
14. `/api/workbench/sessions/{session_id}/final-top10` returns Runtime finalization-backed ranking.
15. Svelte `runStory` final node uses final-top10/finalization data, not raw candidate review item count.
16. Source graph branches are rendered per Runtime round: one query bundle, selected source dispatch/result nodes, one merge/dedupe node for multi-source rounds, one scoring/top-pool node, and one feedback node per round.
17. No public payload, note, graph node, event response, or UI DOM leaks cookies, auth headers, browser storage state, raw provider payloads, raw resumes, local artifact paths, Pi tool raw output, or OpenCLI command internals.
18. The Workbench start API response reflects one runtime sourcing job and does not pretend to return per-source execution jobs when no per-source jobs were created.
19. Workbench SQLite maintenance metadata recognizes the runtime sourcing job table and indexes.
20. Runtime sourcing jobs renew their lease while running and do not get duplicated by lease expiry during long runs.
21. Source dispatch propagates Runtime invariant/programmer errors instead of converting them to provider coverage.
22. Public Workbench reason codes are business-safe even when internal provider codes include Pi/OpenCLI/DokoBot/MCP-specific values.
23. Runtime public events include round-scoped graph envelopes for query, source dispatch/result, merge/dedupe, scoring, feedback, and finalization stages without introducing Runtime-to-UI module dependencies.
24. The graph supports any number of Runtime-decided rounds by adding vertical round rows, each row restarting at the left, and the canvas grows or scrolls instead of overlapping later rounds, including six or more dual-source rows.
25. Single-source sessions omit unselected source nodes and degrade to a readable linear per-round layout.
26. Selected-but-blocked sources remain visible as blocked round source nodes and do not hide available-source progress.
27. Source cards use a store-level live Runtime source-count projection when available and do not remain at `0/0` after Runtime has emitted source progress.
28. Workbench live notes reject hidden-reasoning tags and dedupe adjacent semantically identical notes.
29. Workbench note writer async failures are not swallowed as silent success; validation failures are dropped deliberately without producing runtime coroutine warnings.
30. OpenCLI cleanup closes owned/orphaned SeekTalent Liepin tabs even when lease files are missing, while preserving unrelated user tabs and user-opened Liepin tabs that are not proven OpenCLI-owned by a valid, unexpired ownership marker.
31. Runtime public events are reconciled from durable Runtime artifacts at job completion and are idempotent by `eventId` at both helper and database levels.
32. Runtime finalization public events do not create a fake `round-0` module in the Svelte graph.
33. Frontend source reason labels render every public source reason code as business-facing text.

## Regression Tests Required

- Workbench session start creates one Runtime sourcing job for selected sources.
- Runtime bridge invokes `run` once with both source kinds and never invokes `run_source_lane` for the primary run.
- Runtime multi-source round dispatch sends identical logical query fingerprints to CTS and Liepin.
- Runtime multi-source round dispatch sends identical requested counts to CTS and Liepin.
- Liepin evidence records the Runtime logical query fingerprint.
- Runtime multi-source dispatch is concurrent.
- Runtime source invariant errors fail the round instead of becoming degraded source coverage.
- 70/30 logical query allocation remains intact.
- Candidate-feedback rescue priority remains intact.
- Generic explore fallback remains intact.
- Liepin blocked coverage preserves CTS candidates.
- Duplicate CTS/Liepin identity merges to one final result.
- Fresh Liepin detail or fresher CTS normalized resume becomes canonical according to existing canonical selection policy.
- Workbench final-top10 is capped at 10.
- Workbench final-top10 follows persisted Runtime finalization identity order.
- Workbench final-top10 includes all source evidence for merged identities.
- Runtime public graph events expose round-scoped stage envelopes from the Runtime layer without provider internals.
- Runtime public graph events are written to a durable run artifact and completion reconciliation backfills missing Workbench events without duplicates.
- Runtime public graph events reject mismatched `eventName`/`stage` pairs and duplicate `eventId`s.
- Runtime public graph event idempotency is enforced by a database uniqueness invariant for `runtime_public_event_v1`.
- Runtime public graph events reject unknown stages and persist source dispatch events before source result events for every selected source.
- Runtime finalization events do not render as round-zero graph rows.
- Strategy graph displays many Runtime rounds as vertical rows, restarts each round at the query column, and keeps later rounds readable by growing or scrolling the graph area.
- Strategy graph displays at least six dual-source Runtime rounds without source-node or row overlap.
- Strategy graph displays CTS and Liepin source nodes inside each dual-source round and joins them into that round's merge/dedupe node.
- Strategy graph displays a CTS-only or Liepin-only run without rendering the unselected source or a fake cross-source merge.
- Strategy graph displays selected blocked Liepin as a blocked source node and continues CTS into merge/scoring.
- Strategy graph final node displays the final-top10 count, not raw review item count.
- Source cards prefer live Runtime cumulative source-result counts over stale source-run projection counts and do not double-count identities seen in multiple rounds.
- Source-card projections keep previous cumulative counts when a later blocked/failed source event has no cumulative count payload.
- Frontend `sourceReasonLabel(...)` covers every public source reason code and does not require internal Pi/OpenCLI/DokoBot/MCP codes for normal public UI.
- Workbench note validation rejects `<think>`, `</think>`, provider/browser implementation terms, and unsupported path-like payloads.
- Workbench note writer does not append duplicate adjacent business notes when only event cursors change.
- OpenCLI cleanup closes owned orphan Liepin tabs when no lease file remains and does not close unrelated user tabs or user-opened Liepin tabs.
- OpenCLI cleanup ignores, safely removes, or safely fails on expired/malformed ownership markers and never falls back to URL-only tab ownership.
- Runtime sourcing job lease heartbeat renews active jobs.
- Workbench session, event, final-top10 API responses and rendered DOM do not expose internal provider/browser implementation terms.
