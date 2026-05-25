# Runtime Source Adapter Boundary Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use Superpowers `test-driven-development` for the implementation tasks, and use the repository `fw-build` workflow before touching code.

**Goal:** Refactor the Runtime/source boundary so Runtime owns the shared search strategy and CTS/Liepin are source adapters that compile and execute Runtime-owned query, filter, rebalance, and budget intent.

**Architecture:** Add a provider-neutral Runtime source query intent layer, compile it through CTS and Liepin adapters, preserve mature CTS behavior, make Liepin budget/filter behavior explicit, and keep Workbench as projection rather than orchestration owner.

**Tech Stack:** Python 3, pytest, existing SeekTalent Runtime modules, existing CTS and Liepin provider modules, Svelte Workbench for verification surfaces, Chrome/OpenCLI for real browser QA.

**Spec:** [2026-05-23-runtime-source-adapter-boundary-refactor-design.md](../specs/2026-05-23-runtime-source-adapter-boundary-refactor-design.md)

---

## Runtime Source Boundary Flow

```text
ControllerDecision
  |  legacy action search_cts means "continue source search"
  v
Runtime neutral intent builders
  |-- filter/location/age intent
  |-- logical query dispatch
  |-- requested_count and provider_scan_limit
  v
RuntimeSourceQueryIntent by source
  |-- cts    -> CTS compiler    -> mature RetrievalRuntime path
  |-- liepin -> Liepin compiler -> card-first provider path
  v
Runtime merge -> scoring -> finalization -> Workbench projections
```

## Execution Mode

Use inline/sequential execution for the first build pass. The plan touches shared Runtime modules, source dispatch, CTS projection, Liepin runtime lane, and Workbench public serializers. Parallel subagent worktrees would create high merge-conflict risk before the shared intent contract lands. After Task 4 is green, CTS compiler tests and Liepin compiler tests can be developed in parallel only if the shared intent module is frozen.

## Preconditions

- Work on a dedicated branch with the `codex/` prefix.
- Start from a clean working tree or explicitly record unrelated local changes before editing.
- Do not pop or apply existing stashes without explicit user approval.
- Do not change node-detail UI completeness in this plan; keep that deferred.
- Do not put credentials in docs, tests, commits, or logs.

## Task 0: Baseline Verification And Guard Rails

**Goal:** Confirm the target branch has the current runtime-owned baseline and record any local state that could affect the refactor.

**Files to inspect:**

- `src/seektalent/runtime/orchestrator.py`
- `src/seektalent/runtime/source_round_dispatch.py`
- `src/seektalent/runtime/logical_query_dispatch.py`
- `src/seektalent/runtime/retrieval_runtime.py`
- `src/seektalent/providers/liepin/runtime_lane.py`
- `src/seektalent/providers/liepin/pi_client.py`
- `src/seektalent/providers/cts/filter_projection.py`
- `TODOS.md`

**Steps:**

- [ ] Run `git status --short` and confirm whether the working tree is clean.
- [ ] Run `git stash list` and record stash names in the build notes; do not apply them.
- [ ] Run `test -f src/seektalent/runtime/logical_query_dispatch.py`.
- [ ] Run `rg -n "class LogicalQueryDispatch|LogicalQueryDispatch" src/seektalent/runtime/logical_query_dispatch.py src/seektalent/runtime/orchestrator.py`.
- [ ] Run `rg -n "dispatch_source_rounds|SourceRoundDispatchRequest|run_liepin_logical_query_bundle" src/seektalent/runtime src/seektalent/providers`.
- [ ] Run `rg -n "project_constraints_to_cts|proposed_filter_plan|LocationExecutionPlan|allocate_balanced_city_targets" src/seektalent`.
- [ ] Run baseline tests:
  - [ ] `uv run pytest tests/test_runtime_multi_source_round_dispatch.py -q`
  - [ ] `uv run pytest tests/test_runtime_state_flow.py -q`
  - [ ] `uv run pytest tests/test_liepin_runtime_source_lane.py -q`

**Expected result:** The commands confirm the current Runtime multi-source baseline exists. If any required file or symbol is missing, stop and update the plan to first restore or merge the runtime-owned baseline.

## Task 0A: Lock The Legacy Controller Action Boundary

**Goal:** Prevent the historical `search_cts` controller action from expanding back into CTS-owned orchestration while avoiding a broad prompt/schema migration in this refactor.

**Files to inspect or modify:**

- `src/seektalent/models.py`
- `src/seektalent/prompts/controller.md`
- `src/seektalent/runtime/orchestrator.py`
- `src/seektalent/runtime/round_decision_runtime.py`
- `tests/test_controller_contract.py`
- `tests/test_runtime_source_adapter_boundary.py`

**Steps:**

- [ ] Do not globally rename existing `search_cts` strings in this refactor.
- [ ] Add a boundary test proving a controller continue decision with `action="search_cts"` enters Runtime source planning, not a CTS-only execution branch.
- [ ] If a helper is needed, add the smallest local normalization function that maps `search_cts` to a Runtime-neutral continue-search meaning before source planning.
- [ ] Ensure new Runtime source intent, adapter, Workbench, and public event names do not introduce new `search_cts` orchestration language.
- [ ] Run:
  - [ ] `uv run pytest tests/test_controller_contract.py -q`
  - [ ] `uv run pytest tests/test_runtime_source_adapter_boundary.py -q`

**Expected result:** Existing controller fixtures remain compatible, but the new adapter boundary is Runtime-neutral.

## Task 1: Add Failing Characterization Tests For The Boundary Gap

**Goal:** Lock the current product requirement before changing implementation.

**Primary test file:**

- `tests/test_runtime_source_adapter_boundary.py`

**Steps:**

- [ ] Create `tests/test_runtime_source_adapter_boundary.py`.
- [ ] Add `test_runtime_source_intent_preserves_query_identity_role_filters_and_budget_for_selected_sources`.
  - Build a small fake Runtime round input with CTS and Liepin selected.
  - Include one exploit query and one explore query.
  - Include a proposed filter plan with location and age constraints.
  - Assert both sources receive the same Runtime query identity fields for the same logical query.
  - Assert both source intents include `requested_count` and `provider_scan_limit`.
  - Assert filter/location intent exists before provider compilation.
- [ ] Add `test_liepin_runtime_lane_uses_runtime_provider_scan_limit`.
  - Use a fake Liepin worker/client that records `maxCards`.
  - Pass a Runtime source query intent with a provider scan limit lower than the old default.
  - Assert Liepin sends the Runtime scan limit, not the old default.
- [ ] Add `test_liepin_source_query_preserves_runtime_query_role`.
  - Pass one explore intent and one exploit intent.
  - Assert Liepin compiled requests do not hard-code all requests to primary.
- [ ] Add `test_unsupported_liepin_filter_is_reported_as_safe_coverage_state`.
  - Pass a location or age filter that the Liepin compiler does not support natively.
  - Assert the compiler reports a safe unsupported/degraded reason instead of silently dropping it.
- [ ] Add `test_legacy_search_cts_action_is_normalized_before_source_planning`.
  - Build or fake a controller continue decision with `action="search_cts"`.
  - Assert Runtime source planning remains selected-source driven and does not create a CTS-only branch.
- [ ] Run `uv run pytest tests/test_runtime_source_adapter_boundary.py -q`.

**Expected result:** The new tests fail because the source-neutral intent contract and Liepin compiler behavior do not exist yet.

## Task 2: Add Provider-Neutral Runtime Filter Intent

**Goal:** Move filter/rebalance intent out of CTS-specific projection so Runtime can give every source the same strategy contract.

**Files to add or modify:**

- Add `src/seektalent/runtime/source_filters.py`
- Modify `src/seektalent/providers/cts/filter_projection.py`
- Modify or add tests in `tests/test_runtime_source_adapter_boundary.py`

**Implementation details:**

- [ ] Add frozen dataclasses in `src/seektalent/runtime/source_filters.py`:
  - `RuntimeFilterIntent`
  - `RuntimeLocationPreference`
  - `RuntimeLocationExecutionIntent`
  - `RuntimeAgeExecutionIntent`
  - `UnsupportedSourceFilter`
- [ ] Add a function:

```python
def build_runtime_filter_intents(
    *,
    requirement_sheet: RequirementSheet,
    proposed_filter_plan: ProposedFilterPlan | None,
) -> tuple[RuntimeFilterIntent, ...]:
    ...
```

- [ ] Add a function:

```python
def build_runtime_location_execution_intent(
    *,
    requirement_sheet: RequirementSheet,
    proposed_filter_plan: ProposedFilterPlan | None,
    round_no: int,
) -> RuntimeLocationExecutionIntent | None:
    ...
```

- [ ] Extract only provider-neutral canonicalization from CTS projection. Keep CTS-specific field naming in `providers/cts/filter_projection.py`.
- [ ] Update CTS projection code to consume the new neutral intent where possible while preserving its existing external behavior.
- [ ] Keep dataclasses small and literal. Do not create a generic provider capability framework in this task.
- [ ] Run:
  - [ ] `uv run pytest tests/test_runtime_source_adapter_boundary.py -q`
  - [ ] `uv run pytest tests/test_cts_filter_projection.py -q` if the file exists.
  - [ ] `uv run pytest tests -q -k "filter_projection or location"`.

**Expected result:** Neutral filter/location intent can be built independently of CTS, and CTS projection behavior remains compatible.

## Task 3: Add Runtime Source Query Intent Contract

**Goal:** Create the adapter-facing object that combines query identity, Runtime budget, and neutral filter/rebalance intent.

**Files to add or modify:**

- Add `src/seektalent/runtime/source_query_intent.py`
- Modify `src/seektalent/runtime/logical_query_dispatch.py` only if extension is simpler than a separate object
- Modify tests in `tests/test_runtime_source_adapter_boundary.py`

**Implementation details:**

- [ ] Add a frozen dataclass:

```python
@dataclass(frozen=True)
class RuntimeSourceQueryIntent:
    round_no: int
    source_kind: str
    query_role: QueryRole
    lane_type: LaneType
    query_instance_id: str
    query_fingerprint: str
    query_terms: tuple[str, ...]
    keyword_query: str
    requested_count: int
    provider_scan_limit: int
    source_plan_version: str
    filter_intents: tuple[RuntimeFilterIntent, ...]
    location_intent: RuntimeLocationExecutionIntent | None
    age_intent: RuntimeAgeExecutionIntent | None
```

- [ ] Add validation in `__post_init__` or a small constructor function:
  - `requested_count >= 0`
  - `provider_scan_limit >= 0`
  - `query_instance_id` and `query_fingerprint` are non-empty
  - `source_kind` is one selected by Runtime
- [ ] Add a builder function that accepts existing logical dispatches and source budget policy:

```python
def build_runtime_source_query_intents(
    *,
    source_kinds: tuple[str, ...],
    logical_dispatches: tuple[LogicalQueryDispatch, ...],
    filter_intents: tuple[RuntimeFilterIntent, ...],
    location_intent: RuntimeLocationExecutionIntent | None,
    age_intent: RuntimeAgeExecutionIntent | None,
    source_budget_policy: RuntimeSourceBudgetPolicy,
) -> dict[str, tuple[RuntimeSourceQueryIntent, ...]]:
    ...
```

- [ ] Compute `provider_scan_limit` in Runtime, not in adapters.
- [ ] For CTS, default the scan limit to the existing CTS page-size/round semantics.
- [ ] For Liepin, compute per-query `provider_scan_limit` exactly as `min(logical_dispatch.requested_count, source_budget_policy.liepin_max_cards)` for this slice.
- [ ] Treat `liepin_card_page_size` as pagination shape only; it must not increase `maxCards`.
- [ ] If future QA proves Liepin needs card overfetch, add a separate `liepin_card_scan_multiplier` proposal instead of changing this refactor silently.
- [ ] Run `uv run pytest tests/test_runtime_source_adapter_boundary.py -q`.

**Expected result:** Tests prove query identity, query role, requested count, provider scan limit, and neutral filter intent are carried by Runtime source query intents.

## Task 4: Wire Source Query Intents Into Runtime Source Dispatch

**Goal:** Ensure CTS and Liepin adapters consume Runtime source query intents instead of deriving strategy fields independently.

**Files to modify:**

- `src/seektalent/runtime/orchestrator.py`
- `src/seektalent/runtime/source_round_dispatch.py`
- `src/seektalent/runtime/logical_query_dispatch.py`
- `tests/test_runtime_multi_source_round_dispatch.py`
- `tests/test_runtime_source_adapter_boundary.py`

**Steps:**

- [ ] Extend `SourceRoundDispatchRequest` with `source_query_intents_by_source: Mapping[str, tuple[RuntimeSourceQueryIntent, ...]]`.
- [ ] In `orchestrator.py`, build neutral filter/location/age intent after the controller decision and before CTS projection.
- [ ] Build Runtime source query intents once per round.
- [ ] Pass source query intents into `dispatch_source_rounds(...)`.
- [ ] Update the CTS adapter call path to receive CTS intents.
- [ ] Update the Liepin adapter call path to receive Liepin intents.
- [ ] Add an invariant check: every selected source must have a query intent tuple for the current round.
- [ ] Let invariant failures raise normally; do not convert programmer/runtime errors into source provider failures.
- [ ] Run:
  - [ ] `uv run pytest tests/test_runtime_source_adapter_boundary.py -q`
  - [ ] `uv run pytest tests/test_runtime_multi_source_round_dispatch.py -q`
  - [ ] `uv run pytest tests/test_runtime_state_flow.py -q`

**Expected result:** Multi-source dispatch uses Runtime source query intents while preserving existing successful CTS-only and dual-source round tests.

## Task 5: Add CTS Source Compiler Without Weakening Mature CTS Search

**Goal:** Make CTS an explicit adapter without regressing CTS search quality.

**Files to add or modify:**

- Add `src/seektalent/providers/cts/source_compiler.py`
- Modify `src/seektalent/runtime/source_round_dispatch.py`
- Modify `src/seektalent/runtime/retrieval_runtime.py` only if needed for a narrow adapter-facing entry point
- Add or update `tests/test_cts_source_compiler.py`

**Implementation details:**

- [ ] Add a CTS compiler function:

```python
def compile_cts_source_query_intents(
    intents: tuple[RuntimeSourceQueryIntent, ...],
) -> tuple[CtsCompiledQuery, ...]:
    ...
```

- [ ] Translate neutral filter intent to CTS-native filters using existing projection behavior.
- [ ] Preserve location rebalance behavior.
- [ ] Preserve query outcome scoring. Do not pass `lambda candidates: []` into mature CTS round execution.
- [ ] Keep final ranking scoring separate from query outcome scoring.
- [ ] Add tests asserting compiled CTS filters match the old CTS projection for a representative location and age case.
- [ ] Add tests asserting CTS query role and lane type survive compilation.
- [ ] Run:
  - [ ] `uv run pytest tests/test_cts_source_compiler.py -q`
  - [ ] `uv run pytest tests/test_runtime_source_adapter_boundary.py -q`
  - [ ] `uv run pytest tests/test_runtime_state_flow.py -q`

**Expected result:** CTS adapter becomes explicit while its mature behavior remains intact.

## Task 6: Add Liepin Source Compiler And Enforce Runtime Budget

**Goal:** Make Liepin consume the same Runtime query intent as CTS, while reporting unsupported provider filters explicitly.

**Files to add or modify:**

- Add `src/seektalent/providers/liepin/source_compiler.py`
- Modify `src/seektalent/providers/liepin/runtime_lane.py`
- Modify `src/seektalent/providers/liepin/pi_client.py` only if the existing request context cannot carry the Runtime scan limit cleanly
- Modify `src/seektalent/runtime/source_round_dispatch.py`
- Add or update `tests/test_liepin_source_compiler.py`
- Update `tests/test_liepin_runtime_source_lane.py`

**Implementation details:**

- [ ] Add a Liepin compiler function:

```python
def compile_liepin_source_query_intents(
    intents: tuple[RuntimeSourceQueryIntent, ...],
) -> LiepinCompiledQueryBundle:
    ...
```

- [ ] Preserve Runtime `query_role`:
  - exploit maps to the existing provider primary behavior;
  - explore maps to the closest supported expansion/explore behavior;
  - if the provider path cannot distinguish roles yet, return a safe degraded capability state and keep the Runtime role in evidence metadata.
- [ ] Set Liepin `maxCards` from Runtime `provider_scan_limit`.
- [ ] Do not let `LiepinPiWorkerClient` or OpenCLI defaults widen the Runtime scan limit.
- [ ] Preserve Runtime `query_instance_id` and `query_fingerprint` in Liepin evidence metadata.
- [ ] For city, age, and other filters that are not yet supported by the browser tool, return explicit unsupported filter diagnostics with safe reason codes.
- [ ] Emit unsupported filter diagnostics at query level and roll them up into the source-round result.
- [ ] Do not open every Liepin detail page in this task.
- [ ] Add a fake worker/client test that captures `maxCards`, query role, query fingerprint, and unsupported filters.
- [ ] Run:
  - [ ] `uv run pytest tests/test_liepin_source_compiler.py -q`
  - [ ] `uv run pytest tests/test_liepin_runtime_source_lane.py -q`
  - [ ] `uv run pytest tests/test_runtime_source_adapter_boundary.py -q`

**Expected result:** Liepin follows Runtime query identity, role, filter diagnostics, and budget semantics.

## Task 7: Surface Safe Coverage State For Unsupported Or Degraded Filters

**Goal:** Make unsupported source capability visible to users and tests without leaking provider internals.

**Files to modify:**

- `src/seektalent/runtime/source_round_dispatch.py`
- `src/seektalent/runtime/public_events.py` if present
- `src/seektalent_ui/workbench_routes.py`
- `src/seektalent_ui/workbench_store.py`
- Workbench API tests that cover session and events payloads

**Steps:**

- [ ] Add safe public reason codes for unsupported/degraded filter capability:
  - `source_filter_unsupported`
  - `source_filter_degraded`
  - `source_location_filter_unsupported`
  - `source_age_filter_unsupported`
  - `source_budget_limited`
- [ ] Map internal source diagnostics to those public reason codes at the public serializer boundary.
- [ ] Include unsupported filter diagnostics in source-round public events or source-card coverage projection.
- [ ] Ensure public payloads do not contain provider implementation terms.
- [ ] Add tests for:
  - session payload;
  - events payload;
  - final Top 10 payload when source evidence includes filter diagnostics;
  - strategy graph payload if graph events are serialized separately.
- [ ] Run:
  - [ ] `uv run pytest tests/test_workbench_api.py -q`
  - [ ] `uv run pytest tests/test_workbench_runtime_owned_execution.py -q`
  - [ ] `uv run pytest tests -q -k "public_reason or source_filter or workbench"`.

**Expected result:** Users see business-facing source coverage state, and provider implementation details stay out of public Workbench payloads.

## Task 8: Preserve Existing UI Scope And Add Only Required Projection Hooks

**Goal:** Keep this refactor focused. Only touch Workbench UI if source coverage or strategy graph data needs a small projection update.

**Files to inspect before editing:**

- `apps/web-svelte/src/lib/runStory.ts`
- `apps/web-svelte/src/lib/StrategyGraph.svelte`
- `TODOS.md`

**Steps:**

- [ ] Confirm whether the new safe coverage reason codes already render through existing source card or graph metadata.
- [ ] If no UI change is needed, do not edit Svelte files.
- [ ] If a small UI projection change is needed, add or update tests around the projection function only.
- [ ] Do not fix node-detail completeness in this plan.
- [ ] Confirm `TODOS.md` still contains the deferred node-detail work.
- [ ] Run `cd apps/web-svelte && bun test` if any Svelte or TypeScript files change.

**Expected result:** UI scope stays limited to the boundary refactor's projection needs.

## Task 9: Full Verification And Real Chrome QA

**Goal:** Verify behavior with automated tests and a real browser flow.

**Automated commands:**

- [ ] `uv run pytest tests/test_runtime_source_adapter_boundary.py tests/test_runtime_multi_source_round_dispatch.py tests/test_liepin_runtime_source_lane.py tests/test_runtime_state_flow.py -q`
- [ ] `uv run pytest tests/test_workbench_api.py tests/test_workbench_runtime_owned_execution.py -q`
- [ ] `uv run pytest tests -q -k "cts or liepin or runtime_source or final_top10"`
- [ ] `cd apps/web-svelte && bun test` if frontend files changed.

**Real Chrome QA steps:**

- [ ] Restart backend and frontend cleanly.
- [ ] Use Chrome, not the Codex in-app browser.
- [ ] Log in using the real QA account provided in the active session; do not write credentials to files.
- [ ] Use the same real Workbench input from the prior QA session; do not invent replacement data.
- [ ] Start a session with CTS and Liepin selected.
- [ ] Capture screenshots at regular milestones:
  - before starting the agent;
  - after first Runtime source dispatch;
  - after source cards update;
  - after merge/scoring/final Top 10;
  - after strategy graph render.
- [ ] Confirm Liepin card counts reflect Runtime provider scan limits rather than the previous broad default.
- [ ] Confirm unsupported Liepin filters are visible as safe coverage state.
- [ ] Confirm final Top 10 can contain merged evidence when both sources return the same identity.
- [ ] Run browser cleanup:
  - close QA windows opened by the test;
  - close OpenCLI-owned tabs/pages opened by the test;
  - run existing OpenCLI/browser GC helper if present;
  - verify no large group of orphaned Chrome tabs remains.

**Expected result:** Automated tests pass, real Chrome QA shows the runtime-owned dual-source flow, and browser cleanup is complete.

## Task 10: Build Notes And Deferred Follow-Ups

**Goal:** Leave clear evidence of what changed and what remains outside this refactor.

**Files to modify only if needed:**

- `TODOS.md`
- release/build notes file if the repository has an established location

**Steps:**

- [ ] Record that Runtime now owns source query/filter/budget intent and source adapters compile it.
- [ ] Record any Liepin native UI filters that remain unsupported by OpenCLI.
- [ ] Keep node-detail completeness as a deferred Workbench UI task.
- [ ] Record the exact automated commands run and their results.
- [ ] Record Chrome QA screenshot paths and cleanup result.

**Expected result:** Follow-up work is explicit without expanding this refactor's implementation scope.

## Risk Controls

- Preserve CTS tests before and after adding CTS compiler.
- Add Liepin fake-worker tests before changing real browser behavior.
- Keep Runtime invariants fail-fast; do not convert programmer errors into source provider failures.
- Keep unsupported provider capability visible as safe degraded coverage.
- Do not broaden Liepin browser execution beyond Runtime budget.
- Treat existing `search_cts` controller strings as legacy compatibility input; do not turn them into new CTS-owned source orchestration.
- Do not include credentials or raw provider payloads in commits, docs, logs, screenshots, or public API responses.

## Review Checklist Before `fw-plan-review`

- [ ] The spec and plan agree that Runtime is Core and CTS/Liepin are adapters.
- [ ] The plan includes tests before implementation for query identity, role, filters, budget, unsupported filter diagnostics, and public payload safety.
- [ ] The plan does not include node-detail UI completeness.
- [ ] The plan does not include a broad repository rewrite.
- [ ] The plan includes real Chrome QA and browser cleanup.
- [ ] The plan avoids provider credential leakage.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `fw-ceo-review` | Scope & strategy | 1 | CLEAR | Runtime Core with CTS/Liepin adapters chosen over small patch or full rewrite |
| Codex Review | `codex review` | Independent 2nd opinion | 0 | NOT RUN | Not required for plan gate |
| Eng Review | `fw-plan-review` | Architecture & tests | 1 | CLEAR AFTER AMENDMENTS | Added controller legacy-action guard, exact Liepin scan-limit formula, source-boundary flow diagram, and inline execution guidance |
| Design Review | `fw-plan-review` conditional | UI/UX gaps | 1 | NOT APPLICABLE | No new screens or visual layout changes; plan only permits small existing projection hooks if coverage state needs rendering |
| DX Review | `fw-plan-devex-review` | Developer experience gaps | 0 | NOT RUN | Not required for this backend boundary refactor |

- **UNRESOLVED:** 0.
- **VERDICT:** ENG CLEARED after amendments. Ready for user-approved `fw-build`.
