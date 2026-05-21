# Runtime-Owned Multi-Source Workbench Execution Design

## Summary

SeekTalent's first usable local BYOK milestone needs one recruiter workflow: create a Workbench session, confirm search criteria, start the agent, run CTS and Liepin as peer sources, merge likely duplicate people, choose the freshest canonical resume per person, and show one final Top 10.

The current product already has substantial pieces: CTS runtime search, Liepin Pi/OpenCLI browser execution, source evidence, identity projection, final-top10 API, Svelte Workbench parity UI, and safe source state. The defect is orchestration ownership. Workbench currently starts independent source-run jobs. CTS runs the full Runtime controller loop while Liepin runs a single source lane. That makes the strategy graph and final shortlist reflect two different execution models, and it allows raw source counts to leak into final presentation.

This feature corrects that boundary. Runtime becomes the only owner of multi-source sourcing execution. Workbench becomes the session state, approval, projection, and display layer.

## Current Code Facts

- `src/seektalent_ui/job_runner.py` claims per-source jobs and calls `run_cts_source_run(...)` or `run_liepin_card_source_run(...)`.
- `src/seektalent_ui/runtime_bridge.py::run_cts_source_run(...)` calls `WorkflowRuntime.run(...)`, which executes the full Runtime round loop when only CTS is selected.
- `src/seektalent_ui/runtime_bridge.py::run_liepin_card_source_run(...)` calls `WorkflowRuntime.run_source_lane(...)` with a Liepin card request. That is a delta-only lane path, not the full Runtime flow.
- `src/seektalent/runtime/orchestrator.py::WorkflowRuntime.run_async(...)` branches:
  - `("cts",)` uses `_run_rounds(...)`.
  - multi-source uses `_run_full_source_lanes(...)`.
- `_run_rounds(...)` contains the mature controller, 70/30 logical query split, candidate-feedback rescue, generic explore fallback, scoring, reflection, and finalizer flow.
- `_run_full_source_lanes(...)` currently runs simplified source lanes and bypasses the mature `_run_rounds(...)` query planning loop.
- `src/seektalent/runtime/retrieval_runtime.py::allocate_initial_lane_targets(...)` already implements the 70/30 initial allocation for exploit plus secondary logical lanes.
- `src/seektalent/runtime/rescue_router.py::choose_rescue_lane(...)` already prefers candidate feedback before anchor-only rescue when feedback seeds exist.
- `src/seektalent_ui/final_top_candidates.py::project_final_top_candidates(...)` can return identity-level Top 10 from review items, but it is still a projection over persisted review items rather than the authoritative Runtime finalization artifact.
- `apps/web-svelte/src/lib/workbench/runStory.ts` builds strategy graph nodes from Workbench events, source cards, and review items. It does not yet treat Runtime source plan and Runtime finalization as the authoritative graph backbone.

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

The Workbench graph should show the Runtime-owned structure:

```text
Job
  -> Requirement triage
  -> Search plan
      -> CTS branch
      -> Liepin branch
  -> Cross-source merge
  -> Scoring
  -> Final shortlist
```

The graph may show source-specific status under each branch, but it must not imply CTS and Liepin were separately orchestrated Workbench jobs for the same primary run.

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

Public API payloads and rendered UI must expose business-safe reason codes, not implementation reason codes. Internal audit artifacts may keep precise provider/backend reason codes, but Workbench session, event, graph, note, and final-top10 responses must map them before exposure.

Examples:

```text
internal: liepin_opencli_timeout
public:   source_browser_timeout

internal: liepin_pi_mcp_config_invalid
public:   source_browser_backend_unavailable
```

Safety validation must test API responses and rendered DOM, not only source-code grep. Internal source files and tests may contain implementation terms when they are part of internal adapters, deny lists, or mapping tests.

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
16. Source graph branches show CTS and Liepin under one Runtime source plan.
17. No public payload, note, graph node, event response, or UI DOM leaks cookies, auth headers, browser storage state, raw provider payloads, raw resumes, local artifact paths, Pi tool raw output, or OpenCLI command internals.
18. The Workbench start API response reflects one runtime sourcing job and does not pretend to return per-source execution jobs when no per-source jobs were created.
19. Workbench SQLite maintenance metadata recognizes the runtime sourcing job table and indexes.
20. Runtime sourcing jobs renew their lease while running and do not get duplicated by lease expiry during long runs.
21. Source dispatch propagates Runtime invariant/programmer errors instead of converting them to provider coverage.
22. Public Workbench reason codes are business-safe even when internal provider codes include Pi/OpenCLI/DokoBot/MCP-specific values.

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
- Strategy graph final node displays the final-top10 count, not raw review item count.
- Runtime sourcing job lease heartbeat renews active jobs.
- Workbench session, event, final-top10 API responses and rendered DOM do not expose internal provider/browser implementation terms.
