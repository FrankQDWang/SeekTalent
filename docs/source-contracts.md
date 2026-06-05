# Source Contracts

SeekTalent supports multiple candidate sources without letting runtime code depend on concrete provider packages.

## Ownership

| Layer | Owns | Must not own |
| --- | --- | --- |
| Runtime | orchestration, source-neutral plans, budgets, candidate identity, scoring, finalization, public runtime events | provider transport, browser automation, provider-specific reason-code literals |
| Sources | source-neutral contracts, source registry, source adapters, source-specific runtime bridge code | concrete HTTP/browser clients except through provider boundaries |
| Providers | CTS/Liepin transport adapters, provider DTOs, mapping, safety, worker/OpenCLI details | runtime DTO imports or runtime orchestration |
| BFF | frontend response projection, Workbench persistence/API, OpenAPI generation | backend model normalization or provider error mapping outside source/provider boundaries |

## Core Modules

- `src/seektalent/sources/contracts.py`: source-neutral result/filter contracts.
- `src/seektalent/sources/registry.py`: registered source ids and source plan builders.
- `src/seektalent/sources/filter_plan.py`: canonical default/truth/freeform filter-plan normalization.
- `src/seektalent/sources/range_overlap.py`: shared open-ended range overlap math.
- `src/seektalent/sources/cts/filter_projection.py`: CTS-native filter projection.
- `src/seektalent/sources/liepin/runtime_lane.py`: Liepin runtime source-lane bridge.
- `src/seektalent/sources/liepin/reason_codes.py`: Liepin/OpenCLI safe public reason-code mapping.
- `src/seektalent/retrieval/service_factory.py`: provider registry/service construction outside runtime.

## Runtime Rules

Runtime production code must follow these rules:

- Do not import `seektalent.providers.*`.
- Do not branch on concrete source ids such as `source == "cts"` or `source == "liepin"` for dispatch.
- Do not define two-source-only public contracts such as `Literal["cts", "liepin"]`.
- Do not contain `opencli` or `liepin_opencli` reason-code literals.
- Dispatch source behavior through source maps, selected source plans, and source adapter entry points.

## Provider Rules

Provider production code must follow these rules:

- Do not import `seektalent.runtime.*`.
- Keep provider DTOs, transport clients, and browser/worker mechanics provider-local.
- Use source/provider-local structural protocols when provider compilers need runtime-shaped values.
- Return provider facts through source/retrieval contracts rather than leaking provider internals into runtime.

## Adding A Source

1. Add source-neutral contract tests with a fixture or new source id.
2. Register the source through `src/seektalent/sources/registry.py`.
3. Add source-specific planning/adapter code under `src/seektalent/sources/<source>/`.
4. Keep concrete transport under `src/seektalent/providers/<source>/` or another provider-owned package.
5. Route runtime dispatch through existing source maps rather than adding runtime `if source == ...` branches.
6. Run `uv run python tools/check_source_boundaries.py` and focused runtime/source tests.

The fixture source tests prove a third source can register and execute without changing runtime dispatch code.

## Verification

Use these checks after source/runtime/provider changes:

```bash
uv run python tools/check_source_boundaries.py
scripts/verify-source-decoupling.sh
uv run python tools/check_tach_baseline.py
```

For broad red-zone changes, also run:

```bash
scripts/verify-red-zone.sh
```
