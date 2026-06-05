# Execution Sequence

This is a corrective Goal sequence. Each phase must update the progress ledger.

## Effort Estimate

Expected implementation size is moderate, not a one-file patch:

| Phase | Expected scope | Complexity |
| --- | --- | --- |
| Gate hardening | 150-300 LOC across checker/tests | medium |
| Fixture full-runtime proof | 80-180 LOC test/runtime seam if needed | medium |
| Registry injection | 150-350 LOC runtime/bootstrap/contracts | high |
| CTS/Liepin migration | 300-700 LOC moved or adapted code | high |
| Tach boundary repair | 40-120 LOC config/tests | medium |
| Behavior regression/docs | 50-150 LOC | low-medium |

If the actual change is much smaller, re-check whether it only renamed or wrapped
the old coupling. If it is much larger, pause and verify the task has not turned
into unrelated cleanup.

## 0. Goal Run Setup

- Read `07-execution-control.md`.
- Run preflight commands.
- Update the correction progress ledger.
- Record unrelated dirty files and avoid them.
- Re-read the old progress ledger only as claim evidence.

Do not edit product code in this phase.

## 1. Harden Gates First

Add failing tests/checks for the known blind spots:

- runtime concrete `seektalent.sources.cts/liepin` imports;
- runtime concrete source whitelist and branch forms;
- concrete source dispatch maps in runtime;
- source-specific runtime budget/detail/reason-code leakage;
- Tach runtime/sources/providers cycles.

Expected result before product migration: these checks fail on current code.

Do not continue until the red evidence is recorded.

Required red commands after writing the new checker tests but before checker
implementation:

```bash
uv run pytest tests/test_source_boundaries.py::test_runtime_concrete_source_import_is_reported -q
uv run pytest tests/test_source_boundaries.py::test_runtime_source_membership_whitelist_is_reported -q
uv run pytest tests/test_source_boundaries.py::test_runtime_concrete_source_dispatch_map_is_reported -q
uv run pytest tests/test_tach_baseline.py::test_tach_config_has_no_runtime_source_provider_cycle -q
```

Expected result: each command fails because the checker or Tach test does not yet
detect the violation.

Required red commands after checker/Tach test implementation but before product
migration:

```bash
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
scripts/verify-source-decoupling.sh
```

Expected result: these commands fail against current product code and mention
concrete source imports, concrete source branch/dispatch, and Tach cycle issues.

## 2. Add Full WorkflowRuntime Fixture Proof

Add a fixture source test that goes through the real runtime path. It must prove:

- source is registered outside runtime;
- runtime receives the registry or registry provider;
- runtime creates source plans through the registry;
- runtime executes the source lane through `RegisteredSource.run_card_lane`;
- source result merges into `RunState`;
- no runtime code change is needed to add the source.

Expected result before product migration: test fails because current runtime
rejects or cannot execute `fixture_source`.

Required red command:

```bash
uv run pytest tests/test_source_registry_contract.py::test_fixture_source_executes_through_workflow_runtime_without_runtime_source_branch -q
```

If the test is placed in `tests/test_runtime_source_adapter_boundary.py`, use the
same test name there. The failure must be caused by real runtime source
whitelisting, missing registry injection, or hard-coded dispatch, not by test
setup typos.

## 3. Introduce Registry Injection Into Runtime

Refactor `WorkflowRuntime` so source execution uses a registry abstraction.

Keep the change small:

- production bootstrap registers CTS and Liepin;
- tests can inject `fixture_source`;
- runtime consumes only registered source contracts;
- no plugin/entry-point system unless already necessary.

Architectural decisions for this phase are fixed by `02-target-architecture.md`:

- neutral contracts/registry live in `seektalent.source_contracts`;
- concrete CTS/Liepin adapters live outside runtime, under
  `seektalent.source_adapters`;
- `WorkflowRuntime.__init__` accepts an injected registry or registry provider;
- production app/bootstrap builds and passes the registry;
- runtime dispatches by iterating registered sources, not by concrete id maps.

If this design conflicts with existing package constraints, stop and record the
conflict in the ledger. Do not invent a third structure silently.

## 4. Move Concrete CTS/Liepin Logic Out of Runtime

Move or adapt these responsibilities behind source implementations:

- CTS plan defaults, query/filter projection, source lane execution;
- Liepin worker mode, OpenCLI behavior, reason mapping, card/detail lane;
- provider snapshot validation;
- source-specific budgets;
- source-specific detail approval material.

Delete runtime code paths that became obsolete.

## 5. Fix Tach and Boundary Ownership

Update `tach.toml` and related tests so the intended dependency graph is
enforced, not merely baselined.

The desired model must not permit:

- `seektalent.sources` depending on `seektalent.runtime`;
- direct `seektalent.sources` and `seektalent.providers` cycles;
- runtime depending on concrete source implementation packages.

The target graph is specified in `02-target-architecture.md`. The Tach test must
use graph traversal, not only direct dependency checks.

## 6. Preserve Existing Behavior

Run existing CTS/Liepin tests and fix regressions without reintroducing runtime
coupling.

If a test asserts old architecture instead of behavior, replace it with a
behavioral assertion and record the reason.

## 7. Full Verification and Docs

Run all commands in `03-acceptance.md`.

Update active docs only where they would otherwise describe the old coupling.
Do not churn unrelated docs.

## Escalation Rules

Stop and ask before continuing if any of these occur:

- `WorkflowRuntime.run()` cannot be made deterministic and the proposed test seam
  would not share the production source execution method.
- The package split in `02-target-architecture.md` cannot satisfy Tach without a
  cycle.
- A CTS/Liepin behavior test appears to require reintroducing runtime concrete
  source branching.
- The checker needs an allowlist for a runtime concrete source id. The allowlist
  must be justified in the ledger before use.
- Fixing the goal requires touching unrelated dirty files.

When escalating, present the conflicting constraints and 2-3 concrete options.
Do not bypass the constraint by weakening the gate or adding a generic-looking
wrapper around concrete runtime branching.
