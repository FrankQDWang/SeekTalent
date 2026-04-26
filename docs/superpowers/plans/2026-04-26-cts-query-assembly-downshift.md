# CTS Query Assembly Downshift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move CTS-specific query assembly out of runtime and into `providers/cts`, while preserving current behavior, current model names, and focused regression coverage.

**Architecture:** Keep `RetrievalRuntime` responsible for logical query execution and location dispatch, but stop letting it instantiate `CTSQuery` or inject CTS-native fields directly. Add a small CTS-local builder module that accepts a thin input object and returns `CTSQuery`, then route both runtime assembly sites through that builder.

**Tech Stack:** Python 3.12, Pydantic models, pytest, existing `providers/cts` slice, existing `runtime/retrieval_runtime.py`

---

## File Map

- Create: `src/seektalent/providers/cts/query_builder.py`
  Purpose: hold `CTSQueryBuildInput` plus a pure `build_cts_query(...)` helper for CTS-local request assembly.

- Modify: `src/seektalent/runtime/retrieval_runtime.py`
  Purpose: replace direct `CTSQuery(...)` construction with calls into the CTS-local builder, while keeping dispatch/state logic unchanged.

- Modify: `tests/test_runtime_state_flow.py`
  Purpose: lock the new runtime-to-builder seam and verify runtime no longer needs to hand-assemble CTS request shape.

- Modify: `tests/test_runtime_audit.py`
  Purpose: preserve audit payload and query artifact behavior after ownership moves.

- Create: `tests/test_cts_query_builder.py`
  Purpose: isolated behavior tests for CTS query assembly, including base filters, city injection, and adapter note behavior.

## Task 1: Add CTS Query Builder Module

**Files:**
- Create: `src/seektalent/providers/cts/query_builder.py`
- Create: `tests/test_cts_query_builder.py`

- [ ] **Step 1: Write the failing CTS builder tests**

Add to `tests/test_cts_query_builder.py`:

```python
from seektalent.models import CTSQuery
from seektalent.providers.cts.query_builder import CTSQueryBuildInput, build_cts_query


def test_build_cts_query_without_city_keeps_base_filters() -> None:
    result = build_cts_query(
        CTSQueryBuildInput(
            query_role="exploit",
            query_terms=["python", "retrieval"],
            keyword_query="python retrieval",
            base_filters={"age": 3, "position": "backend"},
            adapter_notes=["projection: age mapped to CTS code 3"],
            page=2,
            page_size=5,
            rationale="builder test",
        )
    )

    assert isinstance(result, CTSQuery)
    assert result.native_filters == {"age": 3, "position": "backend"}
    assert result.page == 2
    assert result.page_size == 5
    assert result.adapter_notes == ["projection: age mapped to CTS code 3"]


def test_build_cts_query_with_city_injects_location_and_note() -> None:
    result = build_cts_query(
        CTSQueryBuildInput(
            query_role="exploit",
            query_terms=["python"],
            keyword_query="python",
            base_filters={"age": 3},
            adapter_notes=["projection: age mapped to CTS code 3"],
            page=1,
            page_size=10,
            rationale="builder test",
            city="上海",
        )
    )

    assert result.native_filters == {"age": 3, "location": ["上海"]}
    assert result.adapter_notes == [
        "projection: age mapped to CTS code 3",
        "runtime location dispatch: 上海",
    ]
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run:

```bash
./.venv/bin/pytest tests/test_cts_query_builder.py -q
```

Expected: FAIL with import error because `seektalent.providers.cts.query_builder` does not exist yet.

- [ ] **Step 3: Write the minimal CTS builder implementation**

Create `src/seektalent/providers/cts/query_builder.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from seektalent.models import CTSQuery, ConstraintValue, QueryRole, unique_strings


@dataclass(frozen=True)
class CTSQueryBuildInput:
    query_role: QueryRole
    query_terms: list[str]
    keyword_query: str
    base_filters: dict[str, ConstraintValue]
    adapter_notes: list[str]
    page: int
    page_size: int
    rationale: str
    city: str | None = None


def build_cts_query(input: CTSQueryBuildInput) -> CTSQuery:
    native_filters = dict(input.base_filters)
    adapter_notes = list(input.adapter_notes)
    if input.city is not None:
        native_filters["location"] = [input.city]
        adapter_notes = unique_strings([*adapter_notes, f"runtime location dispatch: {input.city}"])
    return CTSQuery(
        query_role=input.query_role,
        query_terms=input.query_terms,
        keyword_query=input.keyword_query,
        native_filters=native_filters,
        page=input.page,
        page_size=input.page_size,
        rationale=input.rationale,
        adapter_notes=adapter_notes,
    )
```

- [ ] **Step 4: Run the CTS builder tests to verify they pass**

Run:

```bash
./.venv/bin/pytest tests/test_cts_query_builder.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/providers/cts/query_builder.py tests/test_cts_query_builder.py
git commit -m "feat: add CTS query builder"
```

## Task 2: Route Runtime Query Assembly Through The Builder

**Files:**
- Modify: `src/seektalent/runtime/retrieval_runtime.py`
- Modify: `tests/test_runtime_state_flow.py`

- [ ] **Step 1: Write the failing runtime seam test for builder usage**

Add to `tests/test_runtime_state_flow.py`:

```python
def test_runtime_round_search_uses_cts_builder_for_non_location_query(tmp_path: Path, monkeypatch) -> None:
    from seektalent.providers.cts.query_builder import CTSQueryBuildInput

    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))
    tracer = RunTracer(tmp_path / "trace-builder")
    captured: list[CTSQueryBuildInput] = []

    def fake_build_cts_query(input: CTSQueryBuildInput) -> CTSQuery:
        captured.append(input)
        return CTSQuery(
            query_role=input.query_role,
            query_terms=input.query_terms,
            keyword_query=input.keyword_query,
            native_filters=dict(input.base_filters),
            page=input.page,
            page_size=input.page_size,
            rationale=input.rationale,
            adapter_notes=list(input.adapter_notes),
        )

    monkeypatch.setattr("seektalent.runtime.retrieval_runtime.build_cts_query", fake_build_cts_query)

    query_states = runtime._build_round_query_states(
        round_no=1,
        retrieval_plan=RoundRetrievalPlan(
            plan_version=1,
            round_no=1,
            query_terms=["python"],
            keyword_query="python",
            projected_cts_filters={"age": 3},
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
            rationale="builder seam test",
        ),
        title_anchor_terms=["python"],
        query_term_pool=[],
        sent_query_history=[],
    )

    try:
        asyncio.run(
            runtime._execute_location_search_plan(
                round_no=1,
                retrieval_plan=RoundRetrievalPlan(
                    plan_version=1,
                    round_no=1,
                    query_terms=["python"],
                    keyword_query="python",
                    projected_cts_filters={"age": 3},
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
                    rationale="builder seam test",
                ),
                query_states=query_states,
                base_adapter_notes=["projection: age mapped to CTS code 3"],
                target_new=1,
                seen_resume_ids=set(),
                seen_dedup_keys=set(),
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert len(captured) == 1
    assert captured[0].base_filters == {"age": 3}
    assert captured[0].city is None
```

- [ ] **Step 2: Run the seam test to verify it fails**

Run:

```bash
./.venv/bin/pytest tests/test_runtime_state_flow.py::test_runtime_round_search_uses_cts_builder_for_non_location_query -q
```

Expected: FAIL because `retrieval_runtime.py` does not yet import or call `build_cts_query`.

- [ ] **Step 3: Replace direct `CTSQuery(...)` construction in runtime**

Update `src/seektalent/runtime/retrieval_runtime.py`:

```python
from seektalent.providers.cts.query_builder import CTSQueryBuildInput, build_cts_query
```

Replace the non-location branch with:

```python
query = build_cts_query(
    CTSQueryBuildInput(
        query_role=query_state.query_role,
        query_terms=query_state.query_terms,
        keyword_query=query_state.keyword_query,
        base_filters=retrieval_plan.projected_cts_filters,
        adapter_notes=query_state.adapter_notes,
        page=query_state.next_page,
        page_size=requested_count,
        rationale=retrieval_plan.rationale,
    )
)
```

Replace `_run_city_dispatch()` assembly with:

```python
cts_query = build_cts_query(
    CTSQueryBuildInput(
        query_role=query_state.query_role,
        query_terms=query_state.query_terms,
        keyword_query=query_state.keyword_query,
        base_filters=retrieval_plan.projected_cts_filters,
        adapter_notes=query_state.adapter_notes,
        page=city_state.next_page,
        page_size=requested_count,
        rationale=retrieval_plan.rationale,
        city=city,
    )
)
```

Do not change dispatch state, `SentQueryRecord`, or search-attempt behavior.

- [ ] **Step 4: Run the targeted runtime slice**

Run:

```bash
./.venv/bin/pytest tests/test_cts_query_builder.py tests/test_runtime_state_flow.py::test_runtime_round_search_uses_cts_builder_for_non_location_query -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/runtime/retrieval_runtime.py tests/test_runtime_state_flow.py
git commit -m "refactor: route runtime CTS assembly through builder"
```

## Task 3: Lock City Dispatch And Audit Behavior

**Files:**
- Modify: `tests/test_runtime_state_flow.py`
- Modify: `tests/test_runtime_audit.py`

- [ ] **Step 1: Add a city-dispatch builder assertion**

Add to `tests/test_runtime_state_flow.py`:

```python
def test_runtime_city_dispatch_passes_city_to_cts_builder(tmp_path: Path, monkeypatch) -> None:
    from seektalent.providers.cts.query_builder import CTSQueryBuildInput

    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))
    tracer = RunTracer(tmp_path / "trace-city-builder")
    captured: list[CTSQueryBuildInput] = []

    def fake_build_cts_query(input: CTSQueryBuildInput) -> CTSQuery:
        captured.append(input)
        return CTSQuery(
            query_role=input.query_role,
            query_terms=input.query_terms,
            keyword_query=input.keyword_query,
            native_filters={"location": [input.city]} if input.city is not None else {},
            page=input.page,
            page_size=input.page_size,
            rationale=input.rationale,
            adapter_notes=list(input.adapter_notes),
        )

    monkeypatch.setattr("seektalent.runtime.retrieval_runtime.build_cts_query", fake_build_cts_query)
```

Then execute a one-city location plan and assert:

```python
    assert captured
    assert captured[0].city == "上海"
    assert captured[0].base_filters == {}
```

- [ ] **Step 2: Add/adjust audit expectations**

In `tests/test_runtime_audit.py`, keep the existing behavior assertions:

```python
assert cts_queries[0]["native_filters"] == {
    **projection_result["cts_native_filters"],
    "location": ["上海"],
}
```

and add one assertion that adapter notes still carry the dispatch note through the query artifact:

```python
assert "runtime location dispatch: 上海" in cts_queries[0]["adapter_notes"]
```

- [ ] **Step 3: Run the state-flow and audit slices**

Run:

```bash
./.venv/bin/pytest tests/test_runtime_state_flow.py tests/test_runtime_audit.py -q
```

Expected: PASS with unchanged CTS query artifacts and audit outputs.

- [ ] **Step 4: Commit**

```bash
git add tests/test_runtime_state_flow.py tests/test_runtime_audit.py
git commit -m "test: lock CTS builder dispatch behavior"
```

## Task 4: Run Provider And Retrieval Regression

**Files:**
- Modify: none expected
- Test: `tests/test_cts_provider_adapter.py`
- Test: `tests/test_provider_registry.py`
- Test: `tests/test_retrieval_service.py`
- Test: `tests/test_location_execution_plan.py`
- Test: `tests/test_runtime_state_flow.py`
- Test: `tests/test_runtime_audit.py`
- Test: `tests/test_api.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_llm_provider_config.py`

- [ ] **Step 1: Run the focused regression suite**

Run:

```bash
./.venv/bin/pytest tests/test_cts_provider_adapter.py tests/test_provider_registry.py tests/test_retrieval_service.py tests/test_location_execution_plan.py tests/test_runtime_state_flow.py tests/test_runtime_audit.py tests/test_api.py tests/test_cli.py tests/test_llm_provider_config.py -q
```

Expected: PASS.

- [ ] **Step 2: If any test still references old runtime-owned CTS assembly assumptions, fix only the test seam**

Allowed changes:

- import path updates
- monkeypatch target updates
- assertions that now belong on builder output instead of runtime string matching

Not allowed in this task:

- renaming `projected_cts_filters`
- changing provider contract shape
- touching cursor behavior

- [ ] **Step 3: Re-run the focused regression suite**

Run:

```bash
./.venv/bin/pytest tests/test_cts_provider_adapter.py tests/test_provider_registry.py tests/test_retrieval_service.py tests/test_location_execution_plan.py tests/test_runtime_state_flow.py tests/test_runtime_audit.py tests/test_api.py tests/test_cli.py tests/test_llm_provider_config.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_cts_query_builder.py tests/test_runtime_state_flow.py tests/test_runtime_audit.py src/seektalent/providers/cts/query_builder.py src/seektalent/runtime/retrieval_runtime.py
git commit -m "test: verify CTS query assembly downshift"
```

## Self-Review

### Spec coverage

- CTS query assembly moves out of runtime: covered by Tasks 1-2.
- CTS-specific location/filter/adapter-note injection moves into `providers/cts`: covered by Tasks 1-3.
- Model names stay unchanged: preserved by Tasks 2 and 4 constraints.
- No top-level directory redesign: preserved by file map and non-goal constraints.
- Focused regression remains green: covered by Tasks 3-4.

### Placeholder scan

- No `TODO`, `TBD`, or deferred placeholders remain.
- Each code-changing task includes exact file paths, code snippets, commands, and expected outcomes.

### Type consistency

- New builder input is consistently named `CTSQueryBuildInput`.
- Builder entrypoint is consistently named `build_cts_query`.
- Runtime continues to use existing `CTSQuery`, `RoundRetrievalPlan`, and `SentQueryRecord` names without renaming them mid-plan.
