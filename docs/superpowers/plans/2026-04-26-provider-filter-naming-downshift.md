# Provider Filter Naming Downshift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the shared CTS-specific filter field names to provider-neutral names, and propagate that rename through runtime-facing contexts, artifacts, and tests without changing behavior.

**Architecture:** Make one clean field rename across shared models and call sites: `cts_native_filters -> provider_filters` and `projected_cts_filters -> projected_provider_filters`. Keep CTS-specific behavior, `CTSQuery`, and `search_cts` in place; this is a semantics-preserving naming cleanup, not a protocol rewrite.

**Tech Stack:** Python 3.12, Pydantic models, pytest, existing retrieval/runtime/provider slices

---

## File Map

- Modify: `src/seektalent/models.py`
  Purpose: rename the shared model fields at the source of truth.

- Modify: `src/seektalent/providers/cts/filter_projection.py`
  Purpose: emit `ConstraintProjectionResult.provider_filters`.

- Modify: `src/seektalent/retrieval/query_plan.py`
  Purpose: accept and store `projected_provider_filters` in `RoundRetrievalPlan`.

- Modify: `src/seektalent/runtime/orchestrator.py`
  Purpose: update retrieval plan construction and audit payload references.

- Modify: `src/seektalent/runtime/retrieval_runtime.py`
  Purpose: consume `projected_provider_filters` when building CTS query input.

- Modify: `src/seektalent/reflection/critic.py`
  Purpose: rename context keys and wording from CTS filters to provider/projected filters.

- Modify tests that directly assert the old field names:
  - `tests/test_filter_projection.py`
  - `tests/test_query_plan.py`
  - `tests/test_v02_models.py`
  - `tests/test_context_builder.py`
  - `tests/test_runtime_state_flow.py`
  - `tests/test_runtime_audit.py`
  - `tests/test_controller_contract.py`
  - `tests/test_llm_input_prompts.py`
  - `tests/test_llm_fail_fast.py`
  - `tests/test_llm_lifecycle.py`
  - `tests/test_location_execution_plan.py`

## Task 1: Rename The Shared Model Fields

**Files:**
- Modify: `src/seektalent/models.py`
- Modify: `tests/test_v02_models.py`

- [ ] **Step 1: Write the failing model serialization tests**

In `tests/test_v02_models.py`, update/add assertions so they expect the new field names:

```python
def test_constraint_projection_result_uses_provider_filters_key() -> None:
    projection = ConstraintProjectionResult(
        provider_filters={"age": 3},
        runtime_only_constraints=[],
        adapter_notes=[],
    )

    dump = projection.model_dump(mode="json")

    assert dump["provider_filters"] == {"age": 3}
    assert "cts_native_filters" not in dump


def test_round_retrieval_plan_uses_projected_provider_filters_key() -> None:
    plan = RoundRetrievalPlan(
        plan_version=1,
        round_no=1,
        query_terms=["python"],
        keyword_query="python",
        projected_provider_filters={"age": 3},
        runtime_only_constraints=[],
        location_execution_plan=LocationExecutionPlan(
            mode="none",
            allowed_locations=[],
            preferred_locations=[],
            priority_order=[],
            balanced_order=[],
            rotation_offset=0,
            target_new=1,
        ),
        target_new=1,
        rationale="test",
    )

    dump = plan.model_dump(mode="json")

    assert dump["projected_provider_filters"] == {"age": 3}
    assert "projected_cts_filters" not in dump
```

- [ ] **Step 2: Run the model tests to verify they fail**

Run:

```bash
./.venv/bin/pytest tests/test_v02_models.py -q
```

Expected: FAIL because the models still expose `cts_native_filters` and `projected_cts_filters`.

- [ ] **Step 3: Rename the fields in `models.py`**

Update `src/seektalent/models.py`:

```python
class ConstraintProjectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_filters: dict[str, ConstraintValue] = Field(default_factory=dict)
    runtime_only_constraints: list[RuntimeConstraint] = Field(default_factory=list)
    adapter_notes: list[str] = Field(default_factory=list)
```

```python
class RoundRetrievalPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_version: int
    round_no: int
    query_terms: list[str] = Field(default_factory=list)
    keyword_query: str
    projected_provider_filters: dict[str, ConstraintValue] = Field(default_factory=dict)
    runtime_only_constraints: list[RuntimeConstraint] = Field(default_factory=list)
    location_execution_plan: LocationExecutionPlan
    target_new: int
    rationale: str
```

- [ ] **Step 4: Run the model tests to verify they pass**

Run:

```bash
./.venv/bin/pytest tests/test_v02_models.py -q
```

Expected: PASS for the updated model expectations, with other failures likely remaining in downstream call sites.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/models.py tests/test_v02_models.py
git commit -m "refactor: rename projected provider filter fields"
```

## Task 2: Propagate The Rename Through Construction Paths

**Files:**
- Modify: `src/seektalent/providers/cts/filter_projection.py`
- Modify: `src/seektalent/retrieval/query_plan.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `src/seektalent/runtime/retrieval_runtime.py`
- Modify: `tests/test_filter_projection.py`
- Modify: `tests/test_query_plan.py`
- Modify: `tests/test_location_execution_plan.py`
- Modify: `tests/test_runtime_state_flow.py`

- [ ] **Step 1: Write one failing construction-path test**

In `tests/test_query_plan.py`, update/add one assertion that uses the new plan field:

```python
assert plan.projected_provider_filters == {}
```

In `tests/test_filter_projection.py`, update/add one assertion that uses the new projection field:

```python
assert projection.provider_filters == {"age": 3}
```

- [ ] **Step 2: Run the construction-path tests to verify they fail**

Run:

```bash
./.venv/bin/pytest tests/test_filter_projection.py tests/test_query_plan.py tests/test_location_execution_plan.py tests/test_runtime_state_flow.py -q
```

Expected: FAIL due to stale references to `cts_native_filters` and `projected_cts_filters`.

- [ ] **Step 3: Update constructors and runtime consumers**

In `src/seektalent/providers/cts/filter_projection.py`, return:

```python
return ConstraintProjectionResult(
    provider_filters=native_filters,
    runtime_only_constraints=runtime_only_constraints,
    adapter_notes=adapter_notes,
)
```

In `src/seektalent/retrieval/query_plan.py`, rename the function parameter and model construction:

```python
def build_round_retrieval_plan(
    *,
    plan_version: int,
    round_no: int,
    query_terms: list[str],
    title_anchor_terms: list[str],
    query_term_pool: list[QueryTermCandidate],
    projected_provider_filters: dict[str, str | int | list[str]],
    runtime_only_constraints,
    location_execution_plan: LocationExecutionPlan,
    target_new: int,
    rationale: str,
    ...
) -> RoundRetrievalPlan:
```

```python
    return RoundRetrievalPlan(
        ...
        projected_provider_filters=projected_provider_filters,
        ...
    )
```

In `src/seektalent/runtime/orchestrator.py`, update:

```python
projected_provider_filters=projection_result.provider_filters
```

and any later reads of `round_state.retrieval_plan.projected_cts_filters` to:

```python
round_state.retrieval_plan.projected_provider_filters
```

In `src/seektalent/runtime/retrieval_runtime.py`, update both builder input sites:

```python
base_filters=retrieval_plan.projected_provider_filters
```

- [ ] **Step 4: Update the directly affected tests**

Update all direct field references in:

- `tests/test_filter_projection.py`
- `tests/test_query_plan.py`
- `tests/test_location_execution_plan.py`
- `tests/test_runtime_state_flow.py`

Examples:

```python
assert projection.provider_filters == {...}
```

```python
projected_provider_filters={}
```

- [ ] **Step 5: Run the construction-path test slice**

Run:

```bash
./.venv/bin/pytest tests/test_filter_projection.py tests/test_query_plan.py tests/test_location_execution_plan.py tests/test_runtime_state_flow.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/providers/cts/filter_projection.py src/seektalent/retrieval/query_plan.py src/seektalent/runtime/orchestrator.py src/seektalent/runtime/retrieval_runtime.py tests/test_filter_projection.py tests/test_query_plan.py tests/test_location_execution_plan.py tests/test_runtime_state_flow.py
git commit -m "refactor: propagate provider filter naming"
```

## Task 3: Update Reflection, Context, And Audit Semantics

**Files:**
- Modify: `src/seektalent/reflection/critic.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `tests/test_runtime_audit.py`
- Modify: `tests/test_context_builder.py`
- Modify: `tests/test_controller_contract.py`
- Modify: `tests/test_llm_input_prompts.py`
- Modify: `tests/test_llm_fail_fast.py`
- Modify: `tests/test_llm_lifecycle.py`

- [ ] **Step 1: Write a failing wording/assertion test**

In `tests/test_runtime_audit.py`, update one assertion first:

```python
assert diagnostic_round["filters"]["projected_provider_filters"] == retrieval_plan["projected_provider_filters"]
```

In `tests/test_llm_input_prompts.py`, update prompt/input expectations so any serialized retrieval plan uses `projected_provider_filters` instead of `projected_cts_filters`.

- [ ] **Step 2: Run the semantics slice to verify it fails**

Run:

```bash
./.venv/bin/pytest tests/test_runtime_audit.py tests/test_context_builder.py tests/test_controller_contract.py tests/test_llm_input_prompts.py tests/test_llm_fail_fast.py tests/test_llm_lifecycle.py -q
```

Expected: FAIL because runtime/reflection/audit still emit the old field names or wording.

- [ ] **Step 3: Update reflection and audit consumers**

In `src/seektalent/reflection/critic.py`, rename:

```python
"projected_filter_fields": sorted(plan.projected_provider_filters)
```

and:

```python
f"- Non-location provider filters: {plan.projected_provider_filters or {}}\n"
```

In `src/seektalent/runtime/orchestrator.py`, rename serialized diagnostic payload keys that mirror the plan field:

```python
"projected_provider_filters": round_state.retrieval_plan.projected_provider_filters
```

Do not rename action names like `search_cts`.

- [ ] **Step 4: Update the affected tests and serialized expectations**

Update direct references in:

- `tests/test_runtime_audit.py`
- `tests/test_context_builder.py`
- `tests/test_controller_contract.py`
- `tests/test_llm_input_prompts.py`
- `tests/test_llm_fail_fast.py`
- `tests/test_llm_lifecycle.py`

Examples:

```python
projected_provider_filters={}
```

```python
assert diagnostic_round["filters"]["projected_provider_filters"] == retrieval_plan["projected_provider_filters"]
```

- [ ] **Step 5: Run the semantics slice**

Run:

```bash
./.venv/bin/pytest tests/test_runtime_audit.py tests/test_context_builder.py tests/test_controller_contract.py tests/test_llm_input_prompts.py tests/test_llm_fail_fast.py tests/test_llm_lifecycle.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/reflection/critic.py src/seektalent/runtime/orchestrator.py tests/test_runtime_audit.py tests/test_context_builder.py tests/test_controller_contract.py tests/test_llm_input_prompts.py tests/test_llm_fail_fast.py tests/test_llm_lifecycle.py
git commit -m "refactor: rename provider filter context fields"
```

## Task 4: Focused Regression And Sweep

**Files:**
- Modify: only if a stale assertion remains
- Test: `tests/test_filter_projection.py`
- Test: `tests/test_query_plan.py`
- Test: `tests/test_location_execution_plan.py`
- Test: `tests/test_runtime_state_flow.py`
- Test: `tests/test_runtime_audit.py`
- Test: `tests/test_context_builder.py`
- Test: `tests/test_controller_contract.py`
- Test: `tests/test_llm_input_prompts.py`
- Test: `tests/test_llm_fail_fast.py`
- Test: `tests/test_llm_lifecycle.py`
- Test: `tests/test_v02_models.py`
- Test: `tests/test_api.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_llm_provider_config.py`

- [ ] **Step 1: Run the focused regression suite**

Run:

```bash
./.venv/bin/pytest tests/test_filter_projection.py tests/test_query_plan.py tests/test_location_execution_plan.py tests/test_runtime_state_flow.py tests/test_runtime_audit.py tests/test_context_builder.py tests/test_controller_contract.py tests/test_llm_input_prompts.py tests/test_llm_fail_fast.py tests/test_llm_lifecycle.py tests/test_v02_models.py tests/test_api.py tests/test_cli.py tests/test_llm_provider_config.py -q
```

Expected: PASS.

- [ ] **Step 2: If any failure still uses the old field names, fix only the stale references**

Allowed changes:

- serialized key updates
- fixture field-name updates
- prompt/context expectation updates

Not allowed:

- renaming `search_cts`
- renaming `CTSQuery`
- changing provider contract shape
- changing cursor behavior

- [ ] **Step 3: Re-run the focused regression suite**

Run:

```bash
./.venv/bin/pytest tests/test_filter_projection.py tests/test_query_plan.py tests/test_location_execution_plan.py tests/test_runtime_state_flow.py tests/test_runtime_audit.py tests/test_context_builder.py tests/test_controller_contract.py tests/test_llm_input_prompts.py tests/test_llm_fail_fast.py tests/test_llm_lifecycle.py tests/test_v02_models.py tests/test_api.py tests/test_cli.py tests/test_llm_provider_config.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/seektalent/models.py src/seektalent/providers/cts/filter_projection.py src/seektalent/retrieval/query_plan.py src/seektalent/runtime/orchestrator.py src/seektalent/runtime/retrieval_runtime.py src/seektalent/reflection/critic.py tests/test_filter_projection.py tests/test_query_plan.py tests/test_location_execution_plan.py tests/test_runtime_state_flow.py tests/test_runtime_audit.py tests/test_context_builder.py tests/test_controller_contract.py tests/test_llm_input_prompts.py tests/test_llm_fail_fast.py tests/test_llm_lifecycle.py tests/test_v02_models.py
git commit -m "test: verify provider filter naming downshift"
```

## Self-Review

### Spec coverage

- Rename `cts_native_filters` to `provider_filters`: covered by Tasks 1-2.
- Rename `projected_cts_filters` to `projected_provider_filters`: covered by Tasks 1-3.
- Update runtime/context/reflection/audit semantics: covered by Tasks 2-3.
- Keep `search_cts`, `CTSQuery`, provider contract, and paging unchanged: preserved by task constraints.
- Prove the rename is semantics-preserving via focused regression: covered by Task 4.

### Placeholder scan

- No `TODO`, `TBD`, or deferred placeholders remain.
- Each task includes exact file paths, code snippets, commands, and expected outcomes.

### Type consistency

- The plan consistently uses `provider_filters` on `ConstraintProjectionResult`.
- The plan consistently uses `projected_provider_filters` on `RoundRetrievalPlan`.
- No task reintroduces `cts_native_filters` or `projected_cts_filters` after the rename.
