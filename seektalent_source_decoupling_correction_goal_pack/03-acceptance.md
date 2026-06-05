# Acceptance Criteria

## Hard Source-Decoupling Acceptance

- `src/seektalent/runtime/**` does not import `seektalent.providers.*`.
- `src/seektalent/runtime/**` does not import concrete
  `seektalent.sources.cts.*` or `seektalent.sources.liepin.*` implementation
  modules.
- `src/seektalent/runtime/**` does not import `seektalent.source_adapters.*`.
- Source-neutral contracts and registry live outside concrete adapter packages,
  under `seektalent.source_contracts` or an equivalently neutral package.
- `src/seektalent/runtime/**` does not contain CTS/Liepin source whitelists,
  including `{"cts", "liepin"}` as an execution constraint.
- `src/seektalent/runtime/**` does not branch on concrete source ids:
  `source == "cts"`, `source != "liepin"`, `source in {"cts", "liepin"}`,
  `source not in {"cts", "liepin"}`, or equivalent match/case/map dispatch as
  the main runtime path.
- `src/seektalent/runtime/**` does not contain OpenCLI/Liepin provider reason
  maps or direct `liepin_opencli` literals.
- Runtime source budget models are generic. CTS page size, Liepin max cards, and
  Liepin detail recommendation limits live in source-local defaults or adapter
  config.
- Runtime detail lease model is generic. Liepin-specific approval material stays
  source-local or behind provider payload refs.
- `WorkflowRuntime` executes sources through `SourceRegistry`/`RegisteredSource`
  contracts, not hard-coded CTS/Liepin runner maps.
- `WorkflowRuntime.__init__` accepts source registry injection or an equivalent
  source-neutral registry provider; production CTS/Liepin registration happens
  outside runtime.
- A `fixture_source` can execute through a full `WorkflowRuntime` round path
  without modifying runtime code.
- `tach.toml` does not allow runtime/sources/providers cycles.
- Existing CTS and Liepin behavior remains covered and passing.

## Required Red-Green Evidence

Before product fixes, add or update checks/tests so they fail on current
violations:

- runtime import of `seektalent.sources.liepin.runtime_lane`;
- runtime import of `seektalent.sources.cts.filter_projection`;
- `source not in {"cts", "liepin"}` style whitelist;
- `provider_name != "liepin"` style branch;
- fixture source rejected by real runtime path;
- Tach runtime/sources/providers cycle.
- runtime defaults such as `("cts", "liepin")`;
- runtime source budget/detail lease/source reason leakage.

After fixes, the same checks/tests must pass.

## Required Pre-Fix Failure Standards

After phase 1 gate hardening and before product migration:

```bash
uv run python tools/check_source_boundaries.py
```

must fail and include messages equivalent to:

```text
runtime must not import concrete source implementation
runtime must not compare against concrete source ids
runtime must not dispatch through concrete source id maps
runtime must not contain source-specific runtime budget/detail/reason leakage
```

```bash
uv run python tools/check_tach_baseline.py
```

must fail and identify a runtime/source/provider or
runtime/source_contracts/source_adapters/provider cycle.

The full-runtime fixture test must fail before product migration with one of:

```text
runtime_source_query_intent_unsupported_source:fixture_source
Unsupported runtime source: fixture_source
unsupported_source_kind:fixture_source
missing source registry injection
```

If any of these commands pass before product migration, the red gate is too weak
and must be fixed before runtime code is changed.

## Required Commands

Focused correction gates:

```bash
uv run pytest tests/test_source_boundaries.py tests/test_source_registry_contract.py tests/test_runtime_source_adapter_boundary.py tests/test_runtime_multi_source_round_dispatch.py tests/test_runtime_source_lanes.py -q
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
scripts/verify-source-decoupling.sh
```

Broader Python gates:

```bash
uv run ruff check src tests experiments
uv run ty check src tests
uv run pytest
uv run python tools/check_arch_imports.py
uv run python tools/check_privacy_gate.py --base origin/main
uv run python tools/check_ai_bad_smells.py --base origin/main
scripts/verify-red-zone.sh
```

Workbench and worker gates if touched or if source contracts affect them:

```bash
scripts/verify-dev-workbench.sh
cd apps/web-svelte && bun run test
cd apps/web-svelte && bun run test:e2e
cd apps/web-svelte && bun run build
cd apps/liepin-worker && bun test
cd apps/liepin-worker && bun run typecheck
cd apps/liepin-worker && bun run boundary-check
cd apps/liepin-worker && bun run compatibility-gate
```

If a command cannot run, record the exact reason and the smallest substitute.
Do not silently omit required gates.
