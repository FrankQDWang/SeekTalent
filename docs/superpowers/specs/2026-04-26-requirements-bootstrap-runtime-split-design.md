# Requirements Bootstrap Runtime Split Design

## Goal

Split the requirements/bootstrap execution path out of `WorkflowRuntime` so the orchestrator owns less stage-specific logic and later structural cleanup can proceed from a cleaner base.

This is a narrow refactor:

- extract `_build_run_state`
- keep `RunState` construction semantics unchanged
- keep requirements-stage artifacts unchanged
- keep progress/event behavior unchanged
- leave generic runtime plumbing in `WorkflowRuntime`

## Why This Next

Recent refactors already removed:

- retrieval execution concentration
- context assembly concentration
- diagnostics/reporting concentration

After those moves, the next clearly bounded thick segment in [orchestrator.py](/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py) is the requirements/bootstrap stage beginning at `_build_run_state`.

This stage is a good next target because:

- it is a single stage with a clear start and end
- it has deterministic artifact outputs
- it does not require touching the round loop
- it is easier to isolate than rescue logic or company discovery

## Current State

`WorkflowRuntime._build_run_state(...)` currently owns all of the following:

- `InputTruth` construction
- requirements prompt payload construction
- requirements-stage progress/event emission
- success and failure `requirements_call.json` writing
- `repair_requirements_call.json` writing
- `RunState` initial assembly
- initial artifact writes:
  - `input_truth.json`
  - `requirement_sheet.json`
  - `scoring_policy.json`
  - `sent_query_history.json`

This means `WorkflowRuntime` still mixes:

- orchestration shell behavior
- generic artifact helpers
- stage-specific requirements execution

## Problem

The issue is not only file size. `_build_run_state` is a full stage implementation embedded inside the orchestrator shell.

That makes `WorkflowRuntime` continue to carry:

- stage-specific prompt construction
- stage-specific success/failure branching
- stage-specific initial state assembly

The result is that requirements/bootstrap changes still have to land in the same class that also owns round execution, scoring, rescue lanes, and runtime shell helpers.

## Recommended Approach

Create a new module:

- `src/seektalent/runtime/requirements_runtime.py`

Use a plain async function:

- `build_run_state(...) -> RunState`

This function should execute the full requirements/bootstrap stage, while `WorkflowRuntime` keeps a thin delegating `_build_run_state(...)` method.

Do not introduce a service class. This stage does not need long-lived mutable state.

## Module Boundary

### New module: `requirements_runtime.py`

Own:

- `build_input_truth(...)`
- requirements prompt rendering and payload setup
- `requirement_extractor.extract_with_draft(...)`
- requirements success/failure call-artifact writing logic
- `repair_requirements_call.json` emission
- `RunState` initial assembly
- initial JSON artifact writes for requirements/bootstrap outputs

### Keep in `WorkflowRuntime`

Keep these generic shell helpers in `orchestrator.py`:

- `_write_run_preamble`
- `_write_prompt_snapshots`
- `_build_public_run_config`
- `_build_llm_call_snapshot`
- `_write_aux_llm_call_artifact`
- `_emit_llm_event`
- `_emit_progress`

These are runtime-wide helpers, not requirements-stage-specific logic.

## Function Boundary

Preferred shape:

```python
async def build_run_state(
    *,
    settings,
    requirement_extractor,
    tracer,
    job_title: str,
    jd: str,
    notes: str,
    progress_callback,
    emit_llm_event,
    emit_progress,
    build_llm_call_snapshot,
    write_aux_llm_call_artifact,
) -> RunState:
    ...
```

Key rules:

- do not pass the whole `WorkflowRuntime`
- do not return a new wrapper result object
- continue to raise `RunStageError("requirement_extraction", ...)` on failure

## What This Step Does Not Do

This step does not:

- move `_write_run_preamble`
- move prompt snapshot writing
- move `_build_llm_call_snapshot`
- move `_emit_llm_event` or `_emit_progress`
- change `RunState` schema
- change requirements prompt wording
- change artifact schema
- touch controller/reflection/finalizer stages
- touch `_run_rounds`
- introduce a generalized stage framework

## Testing Strategy

Do not add a `direct == wrapper` seam test.

Use:

- existing integration coverage in [tests/test_runtime_audit.py](/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_audit.py)
- existing state-flow coverage in [tests/test_runtime_state_flow.py](/Users/frankqdwang/Agents/SeekTalent-0.2.4/tests/test_runtime_state_flow.py)

Add at most a small focused test if needed, but it should assert observable outputs directly:

- `RunState` fields
- `requirements_call.json`
- `repair_requirements_call.json`
- failure semantics

## Success Criteria

This step is successful if:

- `WorkflowRuntime` no longer owns the full requirements/bootstrap stage body
- `requirements_runtime.py` owns requirements-stage execution logic
- artifacts and exception semantics remain unchanged
- existing runtime audit/state-flow coverage remains green
