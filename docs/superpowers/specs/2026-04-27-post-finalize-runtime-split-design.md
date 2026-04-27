# Post-Finalize Runtime Split Design

## Goal

Split the run-closing shell that happens after finalization out of `WorkflowRuntime` while preserving artifact schemas, evaluation behavior, run-finished signaling, and final `RunArtifacts` assembly behavior.

This is a narrow structural refactor:

- move post-finalize artifact writing
- move evaluation invocation shell
- move run-finished event/progress emission
- keep finalizer shell unchanged
- keep final `RunArtifacts(...)` assembly in `WorkflowRuntime`

## Why This Next

After extracting requirements, controller, round-decision, retrieval, scoring, reflection, and finalizer shells, the next obvious bounded concentration is the run-closing block after `finalize_runtime`.

That block is now the cleanest remaining stage-shaped unit because it already has:

- a clear start boundary: finalized shortlist already exists
- a clear end boundary: values needed for final `RunArtifacts(...)`
- a coherent data flow: diagnostics, evaluation, audit, and run-finished reporting

This is lower-risk than further restructuring `_run_rounds(...)`, and it aligns well with later flywheel/data work because it centralizes end-of-run derived artifacts.

## Current State

`src/seektalent/runtime/orchestrator.py` currently owns a post-finalize run-closing block inside `run_async(...)` that does all of the following after `finalize_runtime` completes:

- write `judge_packet.json`
- write `run_summary.md`
- write `search_diagnostics.json`
- complete the finalizer-completed artifact list
- invoke evaluation
- emit `evaluation_completed` or `evaluation_skipped`
- write `term_surface_audit.json`
- emit `run_finished`
- emit `run_completed`
- derive values later used in final `RunArtifacts(...)`

## Problem

This logic is not top-level orchestration anymore. It is a bounded post-finalize execution shell that still lives inline in `run_async(...)`.

That keeps `WorkflowRuntime` mixing:

- top-level run coordination
- finalizer shell coordination
- end-of-run diagnostics generation
- evaluation shell handling
- end-of-run event/progress emission
- final artifact assembly

The runtime is much thinner than before, but this closing block is now the next concentrated stage-shaped host.

## Recommended Approach

Create a new module:

- `src/seektalent/runtime/post_finalize_runtime.py`

Use plain module-level functions, not a class.

This stage should stay literal and local. It does not need a generic closing framework, a manager class, or a reusable lifecycle abstraction.

## Module Boundary

### New module: `post_finalize_runtime.py`

Own:

- `judge_packet.json` writing
- `run_summary.md` writing
- `search_diagnostics.json` writing
- evaluation invocation shell
- `evaluation_completed` / `evaluation_skipped` event emission
- `term_surface_audit.json` writing
- `run_finished` event emission
- `run_completed` progress emission

Return:

- a thin `PostFinalizeResult`

### Keep in `WorkflowRuntime`

Keep these in `orchestrator.py`:

- `finalize_runtime.run_finalizer_stage(...)`
- `finalize_runtime.finalize_finalizer_stage(...)`
- top-level `try/except/finally`
- final `RunArtifacts(...)` assembly
- tracer lifecycle

Injected helpers may still come from `WorkflowRuntime`, but the run-closing shell itself should move.

## Result Shape

Prefer a thin dataclass:

```python
@dataclass
class PostFinalizeResult:
    evaluation_result: EvaluationResult | None
    terminal_stop_guidance: StopGuidance | None
    run_finished_summary: str
```

Why this shape:

- `evaluation_result` is needed by final `RunArtifacts(...)`
- `terminal_stop_guidance` is needed by final `RunArtifacts(...)`
- `run_finished_summary` is currently reused for both `run_finished` and `run_completed`

Do not wrap more than needed. This should stay a small explicit carrier, not a new architecture layer.

## Function Boundary

Preferred shape:

```python
async def run_post_finalize_stage(
    *,
    settings: AppSettings,
    tracer: RunTracer,
    run_state: RunState,
    final_result: FinalResult,
    rounds_executed: int,
    stop_reason: str,
    terminal_controller_round: TerminalControllerRound | None,
    evaluation_runner,
    judge_prompt,
    judge_limiter,
    eval_remote_logging: bool,
    materialize_candidates,
    build_judge_packet,
    build_search_diagnostics,
    build_term_surface_audit,
    render_run_summary,
    render_run_finished_summary,
    emit_progress,
    ...
) -> PostFinalizeResult:
    ...
```

Key rules:

- do not pass the whole `WorkflowRuntime`
- do not move the finalizer shell into this module
- do not move final `RunArtifacts(...)` assembly into this module
- keep artifact names and payload schemas unchanged
- keep event/progress semantics unchanged

## What This Step Does Not Do

This step does not:

- change evaluation algorithms
- change diagnostics schema
- change report markdown wording
- change finalizer behavior
- change `finalize_runtime.py`
- change `_run_rounds(...)`
- change provider/runtime boundaries
- introduce a generic run-closing framework
- start cursor-generalization or flywheel work

## Testing Strategy

Do not add `direct == wrapper` parity tests.

Primary protection should come from existing observable tests that already cover:

- `finalizer_call.json`
- `judge_packet.json`
- `search_diagnostics.json`
- `term_surface_audit.json`
- `run_summary.md`
- final API / CLI outputs

If an extra seam test is needed, prefer a minimal host-boundary test that proves `WorkflowRuntime` delegates the run-closing shell while still assembling the final `RunArtifacts(...)` itself.

## Success Criteria

This step is successful if:

- `post_finalize_runtime.py` becomes the host for the run-closing shell after finalization
- `WorkflowRuntime.run_async(...)` becomes visibly thinner after the finalizer stage
- evaluation behavior, diagnostics artifacts, run-finished signaling, and final API/CLI outputs remain unchanged
- final `RunArtifacts(...)` assembly remains in `WorkflowRuntime`
- no generic framework is introduced
