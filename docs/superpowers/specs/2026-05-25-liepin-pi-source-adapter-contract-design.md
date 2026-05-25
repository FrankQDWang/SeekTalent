# Liepin PI Source Adapter Contract Design

## Summary

The current baseline is the restored CLI runtime data flow from the `0.6.2` line:

```text
job_title + JD + optional notes
-> RequirementExtractionDraft
-> RequirementSheet
-> RequirementDigest / ScoringPolicy
-> controller query dispatch
-> source retrieval
-> shared normalization
-> scoring / reflection / finalization
```

CTS already fits this shape because it is a direct API adapter. Liepin must fit the same runtime shape, but it cannot behave like CTS internally because the Liepin source is operated by a PI Harness child agent through browser/OpenCLI automation. This slice makes Liepin a real runtime source adapter instead of a parallel Workbench/browser data flow.

The runtime owns the round, the source split, the 70/30 logical query split, and the next-stage barrier. CTS and Liepin are source adapters. Liepin internally starts two PI child-agent jobs per round, one for `exploit` and one for `explore`, and returns detail-backed raw resumes to the same normalization path used by CTS.

## Current Code Facts

The active code already has pieces of the desired structure:

- `src/seektalent/runtime/source_round_dispatch.py` dispatches selected sources with `asyncio.TaskGroup`.
- `src/seektalent/runtime/orchestrator.py` calls `dispatch_source_rounds()` and merges `SourceRoundAdapterResult` objects after dispatch returns.
- `tests/test_runtime_multi_source_round_dispatch.py` already verifies the 70/30 query allocation and some multi-source dispatch behavior.
- `src/seektalent/providers/liepin/runtime_lane.py` has `run_liepin_logical_query_bundle()` and `run_liepin_source_lane()`.
- `src/seektalent/providers/liepin/pi_worker_client.py` bridges runtime `SearchRequest` into `PiLiepinExecutor.search_resumes()`.
- `src/seektalent/providers/liepin/pi_executor.py` already validates a structured `seektalent.pi_liepin_resumes.v1` envelope.
- `src/seektalent/providers/pi_agent/pi_external.py` owns the actual PI RPC prompt contract, expected tool schema, subprocess lifecycle, and OpenCLI cleanup call. The active contract there still points at v1 and old must-have/nice-to-have screening language.
- `tests/test_liepin_runtime_source_lane.py` currently contains a regression that serializes shared OpenCLI detail searches. That test protected an old shared-session design and must be replaced by a two-child-agent concurrency contract.

The active drift is:

- `run_liepin_logical_query_bundle()` runs logical queries sequentially. The two PI child-agent jobs for `exploit` and `explore` are not parallel.
- The old sequential test assumes one shared browser session. The new architecture requires separate child-agent lifecycles/resources per lane so parallelism does not mean two lanes mutating the same resource.
- `RuntimeSourceLaneRequest` carries `job_title`, `jd`, and `notes`, but not the canonical `RequirementSheet`.
- The Liepin PI resume task still sends old `must_haves` and `nice_to_haves` lists instead of the full `RequirementSheet`.
- The current `PiRpcAgentClient.run_json_task_result()` is a one-prompt lifecycle. It starts one RPC request, parses one result, and runs cleanup in a `finally` block. A second `run_json_task_result()` is not the same child-agent context and cannot satisfy the semantic repair requirement.
- The runtime Liepin path still contains card-first and detail-recommendation concepts that were useful for Workbench experiments but are not the full runtime contract for this slice.
- Liepin detail-backed results provide candidates, but the active source lane does not make normalization an explicit adapter contract the way this slice requires.
- Workbench-era code paths can still make it look like UI/browser retrieval owns a separate source flow.

## Goals

- Keep the core runtime as the single owner of source rounds and downstream scoring flow.
- Keep CTS and Liepin as adapters under the same runtime dispatch contract.
- Keep the 70/30 logical split:
  - `exploit`: 7 full resumes
  - `explore`: 3 full resumes
- Run CTS and Liepin concurrently, then wait until both selected sources reach a terminal state before moving to merge, scoring, next round, reflection, or finalization.
- Run the two Liepin PI child-agent jobs concurrently within each Liepin round.
- Pass the full canonical `RequirementSheet` into the Liepin PI request.
- Treat a missing `RequirementSheet` in active Liepin runtime/source-lane execution as a contract error, not as a fallback to `job_title`, JD, or old must-have/nice-to-have fields.
- Stop sending old active PI fields named `must_haves` and `nice_to_haves`.
- Make the Liepin PI prompt/task explain:
  - the complete requirement sheet
  - the lane role and lane target
  - the query terms for that lane
  - provider rank preservation
  - exclude only cards that are clearly mismatched
  - open detail pages only up to the lane budget
  - return structured full resume outputs, not just card summaries
- Validate Liepin PI output in runtime:
  - exact source run id
  - exact query
  - exact target count for success
  - protected refs present
  - detail payload present
  - provider ranks unique
  - no unsafe raw browser payload in public fields
- On validation failure or underfilled output, send a bounded semantic repair request that names the missing pieces. Do not start a full new search task for repair.
- Keep a Liepin lane child-agent context open across first output validation and the single repair attempt. Cleanup happens only after the lane is terminal.
- Close child-agent resources/tabs at terminal success, terminal failure, or terminal blocked state.
- Feed Liepin detail-backed resumes through the same normalization/preprocessing contract used before scoring CTS candidates.
- Clean active runtime code that only exists to preserve the old Workbench/card-recommendation path for this full-runtime lane.

## Non-Goals

- Do not run live Liepin/OpenCLI/browser automation in this slice.
- Do not change CTS query generation semantics.
- Do not change requirement extraction, scoring, reflection, or finalization models.
- Do not change the final top-10 UI display in this slice.
- Do not redesign the Workbench frontend in this slice.
- Do not delete the old React UI in this slice.
- Do not introduce a compatibility layer for old PI payload fields.
- Do not solve provider risk-control throttling globally in this slice. This slice must make terminal blocked states explicit and testable with fakes.

## Target Data Flow

### Runtime Source Round

```text
RequirementSheet + current RunState
-> controller logical queries
-> source query intents by source
-> SourceRoundDispatchRequest
-> CTS adapter task
-> Liepin adapter task
-> wait for both selected sources to reach terminal state
-> merge source lane results
-> normalize candidates
-> scoring
```

`completed`, `partial`, `blocked`, and `failed` are all terminal source states for the barrier. The runtime must not continue with CTS-only results while Liepin is still running. After both sources are terminal, existing coverage policy decides whether the round is complete, degraded, empty, or failed.

### Liepin Adapter Round

```text
SourceRoundDispatchRequest
-> run_liepin_logical_query_bundle()
-> create one lane request per logical query
-> run exploit and explore lane requests concurrently
-> each lane invokes one PI child-agent resume task
-> each PI task returns detail-backed raw resumes
-> runtime validates first output while the same child-agent context is still open
-> runtime sends one semantic repair prompt into that same child-agent context when needed
-> runtime closes lane resources after terminal success / partial / failed / blocked state
-> merge lane outputs in original logical query order
-> normalize detail-backed resumes
-> RuntimeSourceLaneResult
```

The merged Liepin result for a normal two-lane round must contain 10 detail-backed resumes:

```text
exploit: 7
explore: 3
```

If the controller emits a different logical plan, Liepin must use each logical query's `requested_count`; this slice should not hard-code `7` and `3` outside the existing allocation/intent layer.

### PI Child-Agent Input

The PI child-agent receives a JSON task, but the requirement content inside it is the canonical structured runtime object:

```json
{
  "task": "liepin.search_resumes",
  "schema_version": "seektalent.pi_liepin_resumes.v2",
  "source_run_id": "...",
  "query": "...",
  "query_terms": ["..."],
  "lane": {
    "query_instance_id": "...",
    "query_role": "exploit",
    "target_resumes": 7,
    "max_cards": 30,
    "rank_policy": "preserve_provider_rank_exclude_clear_mismatch_only"
  },
  "requirement_sheet": {
    "job_title": "...",
    "role_summary": "...",
    "title_anchor_terms": ["..."],
    "must_have_capabilities": ["..."],
    "preferred_capabilities": ["..."],
    "exclusion_signals": ["..."],
    "hard_constraints": {},
    "preferences": {},
    "initial_query_term_pool": [],
    "scoring_rationale": "..."
  },
  "session_context": {
    "connection_id": "...",
    "provider_account_hash": "..."
  },
  "native_filters": {}
}
```

The active task payload must not contain:

```text
must_haves
nice_to_haves
liepin_must_haves_json
liepin_nice_to_haves_json
```

### PI Child-Agent Output

The PI child-agent returns a structured envelope:

```json
{
  "schema_version": "seektalent.pi_liepin_resumes.v2",
  "status": "succeeded",
  "stop_reason": "completed",
  "source_run_id": "...",
  "query": "...",
  "lane": {
    "query_instance_id": "...",
    "query_role": "exploit",
    "target_resumes": 7
  },
  "cards_seen": 18,
  "cards_excluded": [
    {
      "provider_rank": 4,
      "safe_reason_code": "obvious_role_mismatch"
    }
  ],
  "resumes_returned": 7,
  "pages_visited": 1,
  "detail_pages_opened": 7,
  "action_trace_ref": "artifact://protected/pi-trace/...",
  "protected_snapshot_refs": ["artifact://protected/pi-detail/..."],
  "resumes": [
    {
      "provider_rank": 1,
      "provider_candidate_key_material_ref": "artifact://protected/pi-provider-key/...",
      "candidate_resume_id": "liepin-...",
      "protected_snapshot_ref": "artifact://protected/pi-detail/...",
      "detail_payload": {},
      "normalized_text": "..."
    }
  ]
}
```

`cards_excluded` is diagnostic evidence. It must not replace full resume output. Success means the lane returned exactly the requested number of full resumes.

### Stateful Repair Flow

When the first PI output is parseable but contract-invalid or underfilled, runtime sends a repair task to the same child-agent context before cleanup. The repair request names only what must be supplemented or corrected. Fully malformed raw JSON remains covered by the existing bounded structured-output retry path; semantic repair needs a parsed envelope or a classified validation gap.

Only repairable content gaps should trigger semantic repair: missing resume count, missing protected detail refs, or missing/invalid detail payload objects. Identity and control-plane mismatches such as `source_run_id`, `query`, `schema_version`, budget overflow, or unsafe public payload fail fast instead of asking the child agent to rewrite history.

```json
{
  "task": "liepin.repair_resume_output",
  "schema_version": "seektalent.pi_liepin_resume_repair.v1",
  "source_run_id": "...",
  "query": "...",
  "missing": {
    "resume_count": 2,
    "protected_snapshot_refs": ["resume index 3"],
    "detail_payloads": ["liepin-123"]
  },
  "instruction": "Continue from the current search context. Do not restart the full search. Open additional ranked cards or repair missing detail payloads until the lane contract is satisfied."
}
```

Repair is bounded. A single repair attempt is enough for this slice unless existing settings already expose a tighter retry budget. If repair still fails, the lane returns a terminal `partial` or `failed` result with a safe reason code.

The repair implementation must not call the existing stateless `PiRpcAgentClient.run_json_task_result()` twice and call that "same context." The implementation needs an explicit lane-scoped PI RPC session or equivalent abstraction with this lifecycle:

```text
open lane child-agent session
-> send liepin.search_resumes task
-> receive first envelope
-> runtime validates envelope
-> if repair needed: send liepin.repair_resume_output into same session
-> receive repaired envelope
-> runtime validates terminal envelope
-> close session and cleanup OpenCLI tabs/resources once
```

The current one-shot path can remain for probes and card search. The detail-backed runtime resume lane must use the lane-scoped session path.

Cleanup is runtime-owned. The PI output can report counts such as `detail_pages_opened`, but the v2 envelope must not be trusted as proof that tabs/resources were closed. The lane session close path is the source of truth for cleanup.

## Cleanup Rules

This slice should remove or disconnect active old paths where they conflict with the runtime adapter contract:

- Active PI resume-search payloads no longer accept or send `must_haves` or `nice_to_haves`.
- `src/seektalent/providers/pi_agent/pi_external.py` must expose the active v2 resume-search and repair contract. Updating only the markdown skill is insufficient because the runtime injects task-specific instructions and expected schemas from Python.
- Runtime Liepin full-source execution no longer depends on card-only recommendations or manual detail approval.
- Workbench-specific source execution should call the same runtime source adapter rather than owning a second retrieval shape.
- Historical docs, old fixtures, and tests can mention old names, but active runtime code should not.

Do not remove code that is still the only implementation of a live, tested feature unless this slice replaces its active caller.

## Acceptance

- `run_liepin_logical_query_bundle()` starts `exploit` and `explore` PI tasks concurrently.
- `dispatch_source_rounds()` waits until both selected sources have returned terminal source results; CTS finishing first must not allow scoring or next-round progression before Liepin is terminal.
- Liepin PI task payload includes full `RequirementSheet` data.
- Active Liepin runtime/source-lane calls fail when the canonical `RequirementSheet` is missing instead of sending an empty or inferred requirement payload.
- Liepin PI task payload does not include active `must_haves` or `nice_to_haves`.
- Liepin PI lane targets come from logical query `requested_count`, producing 7 and 3 for the normal first round.
- Liepin output validation requires detail-backed resumes with protected refs and detail payloads.
- Underfilled or parseable contract-invalid Liepin PI output triggers a semantic repair task, not a full new search task.
- Underfilled or parseable contract-invalid Liepin PI output repair runs in the same lane child-agent context before cleanup.
- Terminal Liepin PI success/failure/blocked paths invoke runtime-owned cleanup once after the lane is terminal.
- The Python PI RPC task contract recognizes `seektalent.pi_liepin_resumes.v2` and `liepin.repair_resume_output`.
- Liepin detail-backed resumes enter the same normalized store used before scoring.
- Active runtime tests do not rely on the old card-recommendation detail-approval path for normal Liepin source execution.
- No live Liepin/OpenCLI/browser automation is required to pass tests.
