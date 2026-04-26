# CTS Query Assembly Downshift Design

## Goal

Move CTS-specific query assembly out of runtime and into `providers/cts`, while preserving current CTS behavior, tests, and model names.

This is a narrow structural step:

- keep the current top-level directory layout
- keep `RoundRetrievalPlan.projected_cts_filters` as-is for now
- keep `CTSQuery` as an existing model
- keep current CTS page-number behavior
- do not expand into cursor-generalization or `_context_builder` work

## Current State

The current codebase already split retrieval into several layers, but CTS query construction still leaks into runtime.

### What is already provider-specific

- CTS filter projection lives in [src/seektalent/providers/cts/filter_projection.py](/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/providers/cts/filter_projection.py)
- CTS adapter request/response mapping lives in [src/seektalent/providers/cts/adapter.py](/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/providers/cts/adapter.py)

### What still leaks into runtime

`RetrievalRuntime` still directly constructs `CTSQuery` in two places:

- the non-location branch inside `execute_round_search()`
- `_run_city_dispatch()`

That means runtime still knows CTS request-shape details such as:

- CTS `native_filters`
- CTS location field injection with `"location"`
- CTS-specific adapter note strings
- CTS page/page_size request assembly

### Important code reality

`location` and `age` do **not** currently behave the same in code:

- `location` is an execution-time dispatch mechanism
- `age` is currently a CTS filter-projection concern

Specifically:

- location planning lives in `build_location_execution_plan()` and `allocate_balanced_city_targets()`
- location execution lives in `RetrievalRuntime.execute_round_search()` and `_run_city_dispatch()`
- age mapping currently stays in `providers/cts/filter_projection.py` through `_project_range_enum()`

So this step should not invent a fake abstraction that treats age and location as already-unified dispatch dimensions. The code does not do that today.

## Problem

The provider seam is still incomplete because CTS query assembly is split across:

- `providers/cts/adapter.py`
- `runtime/retrieval_runtime.py`

This causes two problems:

1. runtime still knows CTS-native request details
2. CTS-specific logic cannot be cleanly extended when more providers are added

If this remains, `core` and `runtime` will continue to carry half-provider-specific behavior even after the provider boundary work.

## Recommended Approach

Use a small CTS-local builder module and move CTS query assembly into it.

Create:

- `src/seektalent/providers/cts/query_builder.py`

This module should own:

- building `CTSQuery`
- merging projected CTS filters with dispatch-time CTS additions
- injecting CTS-specific adapter notes
- shaping CTS page/page_size/rationale fields

This is preferred over:

- leaving runtime to build `CTSQuery` directly
- passing the whole `RoundRetrievalPlan` into the adapter and letting it understand runtime internals
- renaming models and contracts in the same step

## Target Boundary

### Runtime keeps

- logical query selection
- round-level retrieval execution
- location dispatch decisions
- batch/phase/city state
- requested count and paging progression state

### `providers/cts` owns

- CTS-native filter composition
- CTS location injection
- CTS adapter-note injection
- final `CTSQuery` construction

Runtime should express intent. CTS provider code should express CTS request shape.

## New Module Shape

Create a small builder input model and a pure builder function.

Recommended shape:

```python
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
    ...
```

Why this shape:

- smaller than a new service object
- clearer than a long parameter list
- easy to test in isolation
- easy to extend later if CTS gets another dispatch-time slice input

This should stay as a local CTS module, not a generalized framework.

## Runtime Integration

`RetrievalRuntime` should stop directly instantiating `CTSQuery`.

In practice, the following logic moves out of runtime:

- `CTSQuery(...)` construction
- `dict(retrieval_plan.projected_cts_filters)`
- `{**retrieval_plan.projected_cts_filters, "location": [city]}`
- CTS-specific adapter-note strings such as `"runtime location dispatch: {city}"`

`RetrievalRuntime` should instead prepare `CTSQueryBuildInput` and call the CTS builder.

This applies to both:

- the `location_plan.mode == "none"` branch
- `_run_city_dispatch()`

## Directory Policy

This step does **not** require a top-level directory redesign.

Keep:

- `src/seektalent/core/retrieval/`
- `src/seektalent/providers/`
- `src/seektalent/runtime/`
- `src/seektalent/retrieval/`

Only add a focused CTS-local module:

- `src/seektalent/providers/cts/query_builder.py`

This keeps the repo shallow while making the provider slice more complete.

## Data Model Policy

This step intentionally keeps some CTS-shaped names in place:

- `RoundRetrievalPlan.projected_cts_filters`
- `CTSQuery`

Reason:

- renaming them would widen the change surface
- current priority is moving ownership, not polishing names
- once query assembly is fully downshifted, naming cleanup becomes safer and more local

This is a deliberate sequencing choice, not an endorsement of the current names as the final state.

## Testing Strategy

Primary regression targets:

- `tests/test_runtime_state_flow.py`
- `tests/test_runtime_audit.py`
- provider-level CTS tests

Add focused CTS builder tests that lock the real seam:

- runtime no longer hand-builds CTS-specific location/native filter combinations
- CTS builder produces the same `CTSQuery` shape currently expected by audit and adapter tests
- city dispatch still yields the same query payloads and search summaries

The best tests here are behavior tests, not source-text assertions.

## Non-Goals

This design does **not** do the following:

- rename `projected_cts_filters`
- remove `CTSQuery`
- redesign `SearchRequest`
- generalize cursor handling
- unify age and location into one generic dispatch abstraction
- refactor `_context_builder`
- redesign the provider registry
- change top-level package layout

## Success Criteria

This step is successful if:

- runtime no longer directly constructs `CTSQuery`
- runtime no longer manually injects CTS-native location fields
- runtime no longer manually writes CTS-specific adapter notes
- CTS query assembly lives under `providers/cts`
- focused CTS/runtime regressions remain green

## Likely Next Step

After this step, the next structural candidate is likely one of:

- continue shrinking CTS-specific leakage from model names
- split `_context_builder`
- later, generalize paging to consume provider-owned cursor semantics

That follow-up decision should happen after the query-assembly ownership move lands cleanly.
