# Architecture Dependency Boundaries

This document records the active `src/` dependency model. It is a current architecture note, not a historical analysis report.

## Active Gates

Use these commands when changing runtime, provider, source, or UI boundary code:

```bash
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
uv run python tools/check_arch_imports.py
```

`tools/check_source_boundaries.py` is the strict source-decoupling gate. It blocks runtime imports of concrete provider modules, provider imports of runtime DTOs, runtime CTS/Liepin source-id branches, runtime OpenCLI/Liepin reason-code literals, and stale two-source-only `Literal["cts", "liepin"]` runtime contracts.

`tools/check_tach_baseline.py` keeps Tach at `0 current accepted failures`. Tach remains intentionally coarse; it is used to detect drift, not to force pattern-heavy layering.

## Intended Direction

The current high-level dependency direction is:

```text
entrypoints -> runtime -> sources -> providers -> clients / worker
             -> retrieval/core contracts
             -> requirements/controller/scoring/reflection/finalize

seektalent_ui -> runtime and providers bootstrap
apps/web-svelte -> seektalent_ui HTTP/OpenAPI boundary
```

The important negative rules are:

- `src/seektalent/runtime/**` must not import `seektalent.providers.*`.
- `src/seektalent/providers/**` must not import `seektalent.runtime.*`.
- `src/seektalent` must not import `seektalent_ui` or `experiments`.
- Provider-specific safe reason-code mapping must not live in runtime.

## Source Adapter Bridge

`src/seektalent/sources/` is the deliberate bridge between runtime/source contracts and provider-backed execution.

- `sources/contracts.py` contains source-neutral contracts and unsupported-filter reporting.
- `sources/registry.py` supports registered source ids beyond the built-in CTS/Liepin pair.
- `sources/filter_plan.py` owns canonical filter-plan normalization.
- `sources/cts/filter_projection.py` owns CTS source projection.
- `sources/liepin/runtime_lane.py`, `smoke_cli.py`, and `reason_codes.py` own Liepin runtime bridge behavior and provider-safe public codes.
- `sources/provider_card_lane.py` routes provider-backed card searches through the source-neutral retrieval service.

This bridge is why `seektalent.sources` may depend on runtime contracts and providers, while runtime and providers still remain directly decoupled from each other.

## Tach Model

The coarse Tach modules are package-folder boundaries under `src/`. The current notable allowances are:

- `seektalent.runtime` may depend on `seektalent.sources`, retrieval/core contracts, and runtime-owned agent stages.
- `seektalent.sources` may depend on `seektalent.runtime` and `seektalent.providers` because it is the integration bridge.
- `seektalent.retrieval` may depend on `seektalent.providers` only for service construction and provider-backed retrieval boundaries.
- `seektalent.providers` may depend on `seektalent.sources` contracts, retrieval primitives, core contracts, and concrete clients.
- `seektalent_ui` may depend on runtime and provider bootstrap because it owns the local Workbench BFF/API surface.

Do not add new Tach modules or public-interface rules just to make a small change look more architectural. Tighten Tach only when there is a repeated boundary problem and the check stays low-noise.

## Risk Files

High-fan-in shared files remain stability-critical:

- `src/seektalent/models.py`
- `src/seektalent/config.py`
- `src/seektalent/llm.py`
- `src/seektalent/prompting.py`

High-fan-out orchestration files remain review-critical:

- `src/seektalent/runtime/orchestrator.py`
- `src/seektalent/runtime/source_lanes.py`
- `src/seektalent_ui/workbench_store.py`
- `src/seektalent_ui/server.py`

When these files change, prefer focused tests plus the boundary gates above. Avoid creating generic managers, helper containers, or fallback layers to hide boundary pressure.
