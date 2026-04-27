# Rescue Execution Runtime Split Design

## Goal

Split the remaining rescue-lane execution logic out of `WorkflowRuntime` so the orchestrator owns less lane-specific behavior while preserving current rescue decisions, artifacts, and query-term mutation semantics.

This is a narrow structural refactor:

- move candidate-feedback rescue execution
- move reserve-broaden and anchor-only forced decision execution
- keep rescue routing unchanged
- keep rescue artifacts and decision semantics unchanged

## Why This Next

After extracting requirements bootstrap, scoring, diagnostics/reporting, and company-discovery lane execution, `WorkflowRuntime` is thinner but still owns the rest of the rescue execution details.

The next best target is the non-company rescue execution path because it is:

- bounded to a small group of execution helpers
- already separated from rescue routing by `rescue_router.py`
- lower-risk than restructuring `_run_rounds(...)`
- the natural follow-up after the company-discovery lane split

## Current State

`src/seektalent/runtime/orchestrator.py` currently owns:

- `_force_candidate_feedback_decision(...)`
- `_force_anchor_only_decision(...)`
- `_force_broaden_decision(...)`
- `_active_admitted_anchor(...)`
- `_untried_admitted_non_anchor_reserve(...)`
- `_tried_query_families(...)`

These functions currently do all of the following:

- compute feedback seed resumes and negative examples
- build candidate-feedback terms and artifacts
- mutate `run_state.retrieval_state` for feedback and broaden state
- select active admitted anchor terms
- select reserve admitted non-anchor families
- generate forced broaden or anchor-only `SearchControllerDecision`s

## Problem

This logic is no longer orchestration shell logic. It is the execution layer for rescue lanes that still lives inside the same class as:

- round-loop coordination
- controller/retrieval/scoring handoff
- company-discovery delegation
- generic progress/event and artifact plumbing

That keeps `WorkflowRuntime` more concentrated than necessary.

## Recommended Approach

Create a new module:

- `src/seektalent/runtime/rescue_execution_runtime.py`

Use plain module-level functions, not a class.

There is no long-lived rescue execution state that justifies a dedicated runtime object. Explicit functions are sufficient and fit the repo style better.

## Module Boundary

### New module: `rescue_execution_runtime.py`

Own:

- `force_candidate_feedback_decision(...)`
- `force_anchor_only_decision(...)`
- `force_broaden_decision(...)`
- `active_admitted_anchor(...)`
- `untried_admitted_non_anchor_reserve(...)`
- `tried_query_families(...)`

If `_query_term_key(...)` is only needed by this slice after extraction, move it too. If it remains reused elsewhere, keep it in `orchestrator.py`.

### Keep in `WorkflowRuntime`

Keep these in `orchestrator.py`:

- `_choose_rescue_decision(...)`
- `_write_rescue_decision(...)`
- `_company_discovery_useful(...)`
- `_continue_after_empty_feedback(...)`
- round-loop dispatch based on `rescue_decision.selected_lane`
- `_emit_progress(...)`

These belong either to rescue routing, company-discovery delegation, or shared runtime plumbing.

## Function Boundary

Preferred shapes:

```python
def force_candidate_feedback_decision(
    *,
    run_state: RunState,
    round_no: int,
    reason: str,
    tracer: RunTracer,
    progress_callback: ProgressCallback | None,
    emit_progress,
) -> SearchControllerDecision | None:
    ...
```

```python
def force_anchor_only_decision(
    *,
    run_state: RunState,
    round_no: int,
    reason: str,
) -> SearchControllerDecision:
    ...
```

```python
def force_broaden_decision(
    *,
    run_state: RunState,
    round_no: int,
    reason: str,
) -> SearchControllerDecision:
    ...
```

Internal helpers should remain in the same module and be called directly.

Key rules:

- do not pass the whole `WorkflowRuntime`
- keep current return shapes
- preserve current artifact names and payload schemas
- preserve current `None` semantics from `force_candidate_feedback_decision(...)`

## What This Step Does Not Do

This step does not:

- change `rescue_router.py`
- change `choose_rescue_lane(...)`
- change `RescueDecision` or `RescueInputs`
- change company-discovery lane execution
- change `_write_rescue_decision(...)`
- touch `_run_rounds(...)`
- change the candidate-feedback domain module itself
- change `search_cts` action naming
- introduce a generic rescue framework

## Testing Strategy

Do not add `direct == wrapper` seam tests.

Primary protection should come from existing rescue-oriented state-flow and audit tests.

If an extra test is needed, prefer:

- a boundary test that `WorkflowRuntime` delegates to the new rescue execution host
- or a direct-output test that checks observable rescue artifacts or controller decisions

Avoid tautological wrapper-parity tests.

## Success Criteria

This step is successful if:

- `WorkflowRuntime` no longer owns the remaining rescue execution bodies
- `rescue_execution_runtime.py` becomes the single host for candidate-feedback / broaden / anchor-only execution helpers
- rescue behavior, artifacts, and query-term mutations remain unchanged
- existing runtime/state-flow/audit coverage remains green
