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

`tools/check_tach_baseline.py` keeps Tach at `0 current accepted failures`. Tach remains intentionally coarse; it is used to detect drift, including the package-level OpenCLI browser boundary, not to force pattern-heavy layering.

## Intended Direction

The current high-level dependency direction is:

```text
entrypoints -> runtime -> sources -> providers -> clients / worker
             -> retrieval/core contracts
             -> requirements/controller/scoring/reflection/finalize

seektalent_ui -> runtime and providers bootstrap
apps/web-react -> seektalent_ui Agent Workbench BFF/OpenAPI boundary
```

The important negative rules are:

- `src/seektalent/runtime/**` must not import `seektalent.providers.*`.
- `src/seektalent/providers/**` must not import `seektalent.runtime.*`.
- `src/seektalent/opencli_browser/**` must not import provider, source, runtime, source-adapter, or UI packages.
- `src/seektalent` must not import `seektalent_ui` or `experiments`.
- Provider-specific safe reason-code mapping must not live in runtime.

## Directory Ownership Map

Use this map to orient AI coding sessions before moving code. Directory placement follows runtime and process boundaries, not product nouns. Do not move files just to make every CTS or Liepin symbol live under one top-level folder.

| Directory | Owns | Does not own |
| --- | --- | --- |
| `src/seektalent/runtime/` | Source-neutral workflow orchestration, budgets, source plans, scoring, finalization, runtime public events | concrete provider clients, browser automation, BFF response DTOs |
| `src/seektalent/source_contracts/` | Thin source contract layer: DTO/dataclass shapes, protocols/callable signatures, `SourceRegistry`, safe serialization | orchestration, source-specific budget/query/reason-code rules, provider calls, runtime merge or scheduling logic |
| `src/seektalent/sources/` | Source adapter bridge between runtime/source contracts and provider-backed execution | concrete provider transport except through provider boundaries |
| `src/seektalent/sources/cts/` | CTS source projection and source-specific planning glue | runtime orchestration or generic contract definitions |
| `src/seektalent/sources/liepin/` | Liepin source-lane bridge, runtime Liepin context normalization, safe public reason-code mapping, Liepin smoke entrypoint | Playwright/browser server implementation or Workbench login UI |
| `src/seektalent/opencli_browser/` | Generic OpenCLI browser command/session automation, command-shape validation, subprocess execution, Chrome window helpers, and generic `opencli_*` internal reason codes | provider page semantics, Liepin URLs, Liepin public reason-code mapping, source/runtime orchestration, or UI behavior |
| `src/seektalent/providers/` | Provider registry and provider-owned integration code | runtime DTO imports or Workbench response projection |
| `src/seektalent/providers/liepin/` | Liepin provider transport, worker-compatible HTTP client, provider DTOs, mapping, filters, safety, detail grants, Liepin site adapter, Liepin site config, Liepin OpenCLI public reason mapping, Liepin Chrome tab reuse fragments, and local drift classification | generic OpenCLI command/session automation, source-neutral runtime orchestration, cloud drift scheduling, or React UI |
| `src/seektalent_ui/` | Local Workbench BFF/API, local actor ownership, persistence, source-connection routes, packaged Workbench static serving | provider adapters, runtime control internals, or remote user identity |
| `apps/web-react/` | React Agent Workbench UI, API adapter calls, generated OpenAPI TypeScript types, frontend state/query/event handling | Python backend imports, core runtime/provider payloads, or backend business logic |

For AI-heavy work, prefer this document as the lookup table and keep `AGENTS.md` focused on behavior rules. If a change needs new ownership guidance, update this table or the focused source-contract docs rather than adding broad instructions to every agent prompt.

## Source Adapter Bridge

`src/seektalent/sources/` is the deliberate bridge between runtime/source contracts and provider-backed execution.

- `sources/contracts.py` contains source-neutral contracts and unsupported-filter reporting.
- `sources/registry.py` supports registered source ids beyond the built-in CTS/Liepin pair.
- `sources/filter_plan.py` owns canonical filter-plan normalization.
- `sources/cts/filter_projection.py` owns CTS source projection.
- `sources/liepin/runtime_lane.py`, `smoke_cli.py`, and `reason_codes.py` own Liepin runtime bridge behavior and provider-safe public codes.
- `sources/provider_card_lane.py` routes provider-backed card searches through the source-neutral retrieval service.
- `opencli_browser/` owns generic OpenCLI command/session behavior and returns generic `opencli_*` internal reason codes.
- `providers/liepin/liepin_opencli_policy.py` owns Liepin OpenCLI URL constants, Liepin Chrome tab reuse fragments, and generic-to-Liepin public reason mapping.
- `providers/liepin/liepin_site_adapter.py` owns Liepin site config and Liepin page behavior over the generic OpenCLI automation port.
- `providers/liepin/liepin_drift_smoke.py` owns local drift classification. Cloud scheduling is out of scope for the provider package.

This bridge is why `seektalent.sources` may depend on runtime contracts and providers, while runtime and providers still remain directly decoupled from each other.

## Tach Model

The coarse Tach modules are package-folder boundaries under `src/`. The current notable allowances are:

- `seektalent.runtime` may depend on `seektalent.sources`, retrieval/core contracts, and runtime-owned agent stages.
- `seektalent.sources` may depend on `seektalent.runtime` and `seektalent.providers` because it is the integration bridge.
- `seektalent.retrieval` may depend on `seektalent.providers` only for service construction and provider-backed retrieval boundaries.
- `seektalent.opencli_browser` may not depend on provider, source, runtime, source-adapter, or UI packages.
- `seektalent.providers` may depend on `seektalent.opencli_browser`, source contracts, retrieval primitives, core contracts, and concrete clients.
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
