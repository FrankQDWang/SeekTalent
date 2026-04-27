# Post-Finalize Runtime Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the run-closing shell that happens after finalization out of `WorkflowRuntime` without changing evaluation behavior, diagnostics artifacts, run-finished signaling, or final API/CLI outputs.

**Architecture:** Introduce a plain runtime module `post_finalize_runtime.py` that owns the post-finalize shell: diagnostics artifacts, evaluation invocation, term-surface audit, and run-finished signaling. Keep `finalize_runtime.py` unchanged and keep final `RunArtifacts(...)` assembly in `WorkflowRuntime`.

**Tech Stack:** Python 3.12, Pydantic models, pytest, existing runtime audit and API/CLI coverage

---

## File Map

- Create: `src/seektalent/runtime/post_finalize_runtime.py`
  Purpose: host the run-closing shell after finalization.

- Modify: `src/seektalent/runtime/orchestrator.py`
  Purpose: delegate post-finalize shell work to the new runtime module.

- Modify only if needed:
  - `tests/test_runtime_audit.py`
  - `tests/test_runtime_state_flow.py`
  - `tests/test_api.py`
  - `tests/test_cli.py`

Primary validation should continue to rely on existing observable artifact and output coverage rather than wrapper-parity tests.

## Task 1: Extract Post-Finalize Run-Closing Shell

**Files:**
- Create: `src/seektalent/runtime/post_finalize_runtime.py`
- Modify: `src/seektalent/runtime/orchestrator.py`

- [ ] **Step 1: Read the current post-finalize block as a single bounded stage**

Use the current implementation in `src/seektalent/runtime/orchestrator.py` as the source of truth.

Move together:

- `judge_packet.json`
- `run_summary.md`
- `search_diagnostics.json`
- evaluation invocation
- `evaluation_completed` / `evaluation_skipped`
- `term_surface_audit.json`
- `run_finished`
- `run_completed`

Keep behavior unchanged:

- evaluation semantics
- diagnostics artifact schemas
- report markdown wording
- final event/progress semantics
- final API/CLI outputs

- [ ] **Step 2: Create `post_finalize_runtime.py` with a thin dataclass result**

Create:

- `src/seektalent/runtime/post_finalize_runtime.py`

Add a small explicit result carrier, for example:

```python
@dataclass
class PostFinalizeResult:
    evaluation_result: EvaluationResult | None
    terminal_stop_guidance: StopGuidance | None
    run_finished_summary: str
```

Then add a plain async function shaped roughly like:

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

Do not add a class or a generic closing framework.

- [ ] **Step 3: Update `run_async(...)` to delegate the post-finalize shell**

In `src/seektalent/runtime/orchestrator.py`, replace the inline post-finalize block with a call into `post_finalize_runtime.run_post_finalize_stage(...)`.

Keep these in `WorkflowRuntime`:

- `finalize_runtime.run_finalizer_stage(...)`
- `finalize_runtime.finalize_finalizer_stage(...)`
- top-level `try/except/finally`
- final `RunArtifacts(...)` assembly
- tracer lifecycle

Do not move `RunArtifacts(...)` construction into the new module.

- [ ] **Step 4: Run focused regression for post-finalize behavior**

Run:

```bash
/Users/frankqdwang/Agents/SeekTalent-0.2.4/.venv/bin/pytest tests/test_runtime_audit.py tests/test_api.py tests/test_cli.py tests/test_runtime_state_flow.py -q
```

Expected: PASS.

- [ ] **Step 5: Add one minimal boundary test only if existing coverage is insufficient**

Only if needed, add a small host-boundary test that proves `WorkflowRuntime` delegates run-closing shell work while still assembling final `RunArtifacts(...)` itself.

Do not add:

- `direct == wrapper` parity tests

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/runtime/post_finalize_runtime.py src/seektalent/runtime/orchestrator.py tests/test_runtime_audit.py tests/test_api.py tests/test_cli.py tests/test_runtime_state_flow.py
git commit -m "refactor: extract post-finalize runtime"
```

## Task 2: Phase-Level Focused Regression

**Files:**
- Modify: only if a stale import/helper reference remains

- [ ] **Step 1: Run the combined focused regression set**

Run:

```bash
/Users/frankqdwang/Agents/SeekTalent-0.2.4/.venv/bin/pytest tests/test_runtime_state_flow.py tests/test_runtime_audit.py tests/test_api.py tests/test_cli.py tests/test_llm_input_prompts.py tests/test_controller_contract.py -q
```

Expected: PASS.

- [ ] **Step 2: Fix only stale import/helper drift if needed**

If a failure appears:

- inspect whether it is a stale import
- inspect whether a helper reference changed host modules
- fix the smallest thing necessary

Do not expand scope into `_run_rounds(...)`, cursor-generalization, or flywheel/data work.

- [ ] **Step 3: Re-run the same regression command**

Re-run the same command and confirm it passes before claiming completion.

## Notes For Reviewers

Reviewers should check:

- `post_finalize_runtime.py` owns the run-closing shell after finalization
- evaluation stayed behaviorally unchanged
- diagnostics artifacts and run-finished signaling stayed unchanged
- final `RunArtifacts(...)` assembly remains in `WorkflowRuntime`
- no generic framework was introduced

## Done Criteria

This plan is complete when:

- `post_finalize_runtime.py` exists
- `WorkflowRuntime.run_async(...)` delegates the post-finalize shell to the new module
- evaluation behavior, diagnostics artifacts, and run-finished signaling remain unchanged
- final API/CLI outputs remain unchanged
- final `RunArtifacts(...)` assembly remains in `WorkflowRuntime`
- the focused regression passes
