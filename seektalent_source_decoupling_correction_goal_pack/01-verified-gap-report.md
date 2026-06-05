# Verified Gap Report

This file records repository facts observed before this correction goal. The Goal
worker must re-verify line numbers because the working tree may change.

During phase 0, paste the current `rg -n` output for each violation class into
the progress ledger. Do not copy stale line numbers from this report into final
evidence.

## Prior Goal Claim

The original goal required:

- runtime only depends on source-neutral contracts;
- runtime has no concrete CTS/Liepin source branches;
- a third test source can execute through registry without runtime changes;
- verification gates enforce the boundary.

Relevant source documents:

- `seektalent_codex_goal_pack/00-codex-goal.md`
- `seektalent_codex_goal_pack/03-target-architecture.md`
- `seektalent_codex_goal_pack/04-source-contract.md`
- `seektalent_codex_goal_pack/10-acceptance.md`

## Verified Current Problems

### 1. Runtime Imports Concrete Source Implementations

Observed examples:

- `src/seektalent/runtime/orchestrator.py` imports
  `seektalent.sources.liepin.runtime_lane`.
- `src/seektalent/runtime/orchestrator.py` imports
  `seektalent.sources.cts.filter_projection`.
- `src/seektalent/runtime/source_lanes.py` imports
  `seektalent.sources.liepin.reason_codes`.
- `src/seektalent/runtime/public_events.py` imports
  `seektalent.sources.liepin.reason_codes`.

This violates the intended meaning of source-neutral runtime. The old checker
only blocked `seektalent.providers`, so these imports passed.

### 2. Runtime Rejects Third Sources

Observed examples:

- `src/seektalent/runtime/source_query_intent.py` rejects any source not in
  `{"cts", "liepin"}`.
- `src/seektalent/runtime/source_lanes.py` rejects any runtime source not in
  `{"cts", "liepin"}`.

Observed reproduction:

```text
build_runtime_source_query_intents(source_kinds=("fixture_source", ...))
=> ValueError: runtime_source_query_intent_unsupported_source:fixture_source

build_runtime_source_plan(source_kinds=("fixture_source", ...))
=> ValueError: Unsupported runtime source: fixture_source
```

### 3. Fixture Source Test Bypasses WorkflowRuntime

`tests/test_source_registry_contract.py` validates:

```text
SourceRegistry -> source.plan() -> source.run_card_lane() -> runtime result merge
```

It does not validate:

```text
WorkflowRuntime.run/_run_rounds/_execute_round_retrieval -> registry source execution
```

Therefore the test does not prove that a new source can execute through the
real runtime.

### 4. Boundary Checker Misses the Actual Violations

`tools/check_source_boundaries.py` currently blocks runtime imports of
`seektalent.providers` and CTS client modules, but not concrete
`seektalent.sources.cts` or `seektalent.sources.liepin` modules.

It also misses branch forms such as:

- `source not in {"cts", "liepin"}`
- `source != "liepin"`
- `provider_name != "liepin"`
- source-specific maps keyed by `"cts"` or `"liepin"`

Observed command result before correction:

```text
uv run python tools/check_source_boundaries.py
=> exit 0
```

### 5. Tach Allows Cycles That Hide the Coupling

Observed current dependency shape:

```text
seektalent.runtime -> seektalent.sources
seektalent.sources -> seektalent.runtime
seektalent.sources -> seektalent.providers
seektalent.providers -> seektalent.sources
```

Observed command result before correction:

```text
uv run tach check
=> All modules validated
```

The configuration is therefore not enforcing the intended boundary.

### 6. Runtime Still Has Source-Specific Execution and Data Rules

Observed examples include:

- `RuntimeSourceBudgetPolicy` has CTS/Liepin-specific fields.
- `RuntimeApprovedDetailLease` supports only Liepin.
- `WorkflowRuntime` has CTS/Liepin runner maps and adapter lambdas.
- `WorkflowRuntime` has `_SOURCE_LANE_REQUEST_RUNNERS = {"liepin": ...}`.
- `WorkflowRuntime.apply_approved_detail_lane_to_run_async()` defaults selected
  sources to `("cts", "liepin")`.
- `WorkflowRuntime._execute_round_retrieval()` indexes source plans by
  `source_plan_by_source["cts"]` and `source_plan_by_source["liepin"]`.
- `retrieval_runtime.py` branches on `provider_name != "liepin"`.
- runtime public event source validation accepts only CTS/Liepin.
- `runtime/public_events.py` imports Liepin reason-code mappings from a concrete
  source module.

Some of these may require careful migration to preserve behavior. They cannot be
left in runtime as the final architecture.

## Required Re-Verification Commands

The Goal worker must run these inventory commands during phase 0 or phase 1 and
paste relevant output into the progress ledger:

```bash
rg -n "from seektalent\\.sources\\.(cts|liepin)|import seektalent\\.sources\\.(cts|liepin)" src/seektalent/runtime -S
rg -n "source not in \\{\\\"cts\\\", \\\"liepin\\\"\\}|source != \\\"liepin\\\"|provider_name != \\\"liepin\\\"|_SOURCE_LANE_REQUEST_RUNNERS|source_plan_by_source\\[\\\"cts\\\"\\]|source_plan_by_source\\[\\\"liepin\\\"\\]|\\(\\\"cts\\\", \\\"liepin\\\"\\)" src/seektalent/runtime -S
rg -n "cts_|liepin_|opencli|RuntimeApprovedDetailLease" src/seektalent/runtime -S
uv run tach check
```

If these commands show new equivalent violations not listed above, add them to
the red-green evidence table before product migration.
