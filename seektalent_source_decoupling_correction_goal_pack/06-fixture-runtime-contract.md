# Fixture Source Runtime Contract

The fixture source is the proof that source decoupling is real.

## Required Shape

Create or update a test fixture source that:

- has source id `fixture_source`;
- is not part of production defaults;
- is registered through `SourceRegistry` or the production-equivalent bootstrap
  hook;
- returns one or two candidates and source evidence;
- uses generic `SourceBudget`, `SourcePlan`, `SourceLaneRequest`, and
  `SourceLaneResult`;
- does not import CTS or Liepin code.

## Required Runtime Path

The test must go through the real runtime orchestration path, not only direct
contract calls.

Preferred proof path:

```text
WorkflowRuntime(settings, source_registry=SourceRegistry([fixture_source]))
  -> run(..., source_kinds=("fixture_source",))
  -> runtime source planning through registry
  -> runtime round execution through RegisteredSource.run_card_lane
  -> merge SourceLaneResult into RunState
  -> expose source coverage/candidate evidence in returned RunArtifacts
```

If `WorkflowRuntime.run(...)` remains too expensive for the focused test, add a
small public test seam whose implementation is shared with production `run()`:

```python
WorkflowRuntime.run_source_round_for_testing(
    run_state=run_state,
    source_kinds=("fixture_source",),
    logical_queries=...,
)
```

This seam is acceptable only if:

- it calls the same registry-driven source planning and source execution method
  used by production `run()`;
- it uses the `source_registry` already injected into `WorkflowRuntime`;
- it does not accept a `source_adapters={"fixture_source": ...}` override;
- it does not special-case `fixture_source`;
- it fails before the product fix on the same whitelist/dispatch problem that
  blocks the production path.

Recommended seam signature if needed:

```python
async def run_source_round_for_testing(
    self,
    *,
    run_state: RunState,
    source_kinds: Sequence[str],
    logical_queries: Sequence[LogicalQueryDispatch],
    round_no: int = 1,
) -> SourceRoundDispatchResult:
    ...
```

This method must be a thin call into the production registry-driven source round
method. It must not build its own source adapter map.

The resulting proof path must still be:

```text
WorkflowRuntime
  -> build/source plan through registry
  -> execute round/source lane through registered source
  -> merge source result into RunState
  -> expose source coverage/candidate evidence
```

The test must fail if runtime contains a CTS/Liepin-only source whitelist.

## Required Test Skeleton

The test should be placed in one of:

- `tests/test_source_registry_contract.py` if it can stay focused and fast;
- `tests/test_runtime_source_adapter_boundary.py` if it needs runtime internals.

Use a name that states the required contract:

```python
def test_fixture_source_executes_through_workflow_runtime_without_runtime_source_branch(...) -> None:
    registry = SourceRegistry([fixture_source], default_source_ids=("fixture_source",))
    runtime = WorkflowRuntime(settings, source_registry=registry)

    artifacts = runtime.run(
        job_title="Data Engineer",
        jd="Build data systems.",
        notes="",
        source_kinds=("fixture_source",),
        # use existing fake LLM/retrieval hooks or the smallest shared runtime
        # test seam required to make this deterministic.
    )

    assert artifacts.run_state is not None
    assert "fixture_source" in artifacts.source_coverage_summary.selected_source_kinds
    assert any(
        evidence.source == "fixture_source"
        for values in artifacts.run_state.source_evidence_by_resume_id.values()
        for evidence in values
    )
```

If the exact public `run()` signature differs, adapt the call to the current API
but preserve the `WorkflowRuntime -> registry -> RegisteredSource.run_card_lane`
path.

## Required Red Evidence

After adding the full-runtime fixture test but before product fixes, the focused
test command must fail:

```bash
uv run pytest tests/test_source_registry_contract.py::test_fixture_source_executes_through_workflow_runtime_without_runtime_source_branch -q
```

Acceptable pre-fix failure messages:

```text
runtime_source_query_intent_unsupported_source:fixture_source
Unsupported runtime source: fixture_source
unsupported_source_kind:fixture_source
missing source registry injection
```

If the test passes before product migration, it is not exercising the real
runtime source path.

## What Does Not Count

These are useful unit tests but do not satisfy the acceptance criterion:

- `SourceRegistry.enabled_sources(["fixture_source"])` only;
- direct `source.plan(...)` only;
- direct `source.run_card_lane(...)` only;
- direct `runtime_source_lane_result_from_source_result(...)` merge only;
- `dispatch_source_rounds(..., source_adapters={"fixture_source": ...})` without
  `WorkflowRuntime`.
- a `WorkflowRuntime` test that injects a runtime-local adapter map instead of a
  registered source;
- a test-only branch in runtime for `fixture_source`.

## Practical Guidance

Keep the fixture source small. Use existing fake LLM/retrieval settings where
possible. If full `WorkflowRuntime.run()` is too expensive, use the smallest
public or semi-public `WorkflowRuntime` entry point that still constructs round
source plans and executes registered source lanes.

Do not make runtime special-case `fixture_source`.

## Escalation Rule

If full `WorkflowRuntime.run()` cannot be made deterministic without broad LLM or
retrieval setup, stop and record two options in the progress ledger:

1. add the small shared runtime test seam described above;
2. expand existing fake runtime services so the public run path is deterministic.

Do not silently fall back to registry-only tests.
