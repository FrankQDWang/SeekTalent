# Corrected Target Architecture

## Package Decision

Use a split package model. Do not keep neutral contracts and concrete source
adapters in the same import boundary.

```text
src/seektalent/source_contracts/
  __init__.py
  contracts.py
  registry.py
  public_events.py

src/seektalent/source_adapters/
  __init__.py
  cts/
    __init__.py
    adapter.py
    filter_projection.py
  liepin/
    __init__.py
    adapter.py
    runtime_lane.py
    reason_codes.py
```

The existing `src/seektalent/sources/` package may be migrated into this split
or left only as a temporary source-compatible shim during the correction, but the
final Tach graph must not model it as both neutral contracts and concrete adapter
implementation.

Rationale:

- `source_contracts` is source-neutral and may be imported by runtime.
- `source_adapters` is concrete implementation and must not be imported by
  runtime.
- providers may import `source_contracts`, but not runtime.
- concrete adapters may import providers and `source_contracts`.

## Dependency Direction

The corrected dependency direction is:

```text
runtime -> source_contracts/contracts
runtime -> source_contracts/registry interface
BFF/API -> runtime
BFF/API or app bootstrap -> concrete source adapter registration
CTS source adapter -> source_contracts + CTS provider/client
Liepin source adapter -> source_contracts + Liepin provider/worker/store
providers -> core/client/retrieval primitives, not runtime
```

Forbidden:

```text
runtime -> seektalent.sources.cts.*
runtime -> seektalent.sources.liepin.*
runtime -> seektalent.source_adapters.*
runtime -> seektalent.providers.*
source_contracts -> runtime
providers -> runtime
source_contracts -> providers
source_contracts -> source_adapters
sources root as a package -> runtime and providers in a cycle
```

## Runtime Ownership

Runtime may own:

- requirement extraction;
- logical query dispatch;
- registry-driven source orchestration;
- generic source budgets;
- generic source lane request/result merging;
- identity merge;
- scoring/finalization/reflection;
- runtime event persistence and artifacts.

Runtime must not own:

- CTS query construction or filter projection;
- Liepin worker/OpenCLI handling;
- source-specific reason-code translation;
- source-specific budget fields;
- Liepin-only detail lease material;
- source-specific snapshot validation rules.

## Source Implementation Ownership

A registered source owns:

- label and capabilities;
- default source-local budget;
- plan creation;
- query intent compilation, if needed;
- card lane execution;
- detail lane execution, if supported;
- provider-specific error/reason mapping;
- provider snapshot validation;
- source-local approval or lease material.

## Bootstrap Ownership

The production app may register CTS and Liepin explicitly through a bootstrap
function outside runtime. Runtime should receive a `SourceRegistry` or a small
registry provider interface. Adding a `fixture_source` for tests must not require
editing runtime modules.

No entry-point plugin system is required. Explicit registration is acceptable if
it is outside runtime and follows the same contracts.

## Runtime Injection Contract

`WorkflowRuntime.__init__` should accept one source-neutral dependency:

```python
source_registry: SourceRegistry | None = None
```

If `None`, runtime may call a bootstrap function passed from the application
layer, but it must not import concrete adapters itself. Prefer explicit
construction outside runtime:

```python
registry = build_workbench_source_registry(settings=settings, local_services=...)
runtime = WorkflowRuntime(settings, source_registry=registry)
```

Required production bootstrap location:

- prefer `src/seektalent_ui/source_registry.py` for Workbench runtime jobs;
- if another app entry point also needs source registration, use a non-runtime
  app/bootstrap module that imports concrete adapters.

Forbidden location:

- `src/seektalent/runtime/**`.

Recommended bootstrap function:

```python
def build_workbench_source_registry(*, settings: AppSettings, local_services: object | None = None) -> SourceRegistry:
    ...
```

The function may import `seektalent.source_adapters.cts` and
`seektalent.source_adapters.liepin`. Runtime must receive the returned registry;
runtime must not call this function unless it is passed in as a source-neutral
dependency by the app layer.

## RegisteredSource Interface

The neutral contract should keep the current simple shape, made explicit:

```python
SourceId = str
SourceLaneRunner = Callable[[SourceLaneRequest], Awaitable[SourceLaneResult]]

class SourcePlanBuilder(Protocol):
    def __call__(
        self,
        *,
        runtime_run_id: str,
        source_index: int,
        budget_overrides: Mapping[str, int] | None,
    ) -> SourcePlan: ...

@dataclass(frozen=True, kw_only=True)
class RegisteredSource:
    source_id: SourceId
    label: str
    capabilities: SourceCapabilities
    default_budget: SourceBudget
    plan: SourcePlanBuilder
    run_card_lane: SourceLaneRunner
    run_detail_lane: SourceLaneRunner | None = None
```

Do not add an abstract base class or plugin framework. If implementation needs
more fields, add them only when a current CTS/Liepin/fixture path requires them.

## Runtime Dispatch Contract

Runtime must not keep concrete runner maps such as:

```python
{"cts": run_cts_lane, "liepin": run_liepin_lane}
```

Instead, runtime should:

1. resolve selected sources from the injected registry;
2. call each `RegisteredSource.plan(...)` with generic runtime data;
3. build a generic `SourceLaneRequest`;
4. call `RegisteredSource.run_card_lane(request)`;
5. call `RegisteredSource.run_detail_lane(request)` only when the registered
   source declares detail support;
6. merge returned `SourceLaneResult` through source-neutral runtime merge logic.

Any source-specific query compilation, worker mode, provider snapshot validation,
reason mapping, and default budget calculation belongs inside the registered
source implementation.

## Generic Budget and Lease Contract

Runtime should use generic budget and lease shapes:

```python
SourceBudget(card_target=int, detail_target=int, scan_limit=int)
ApprovedDetailLease(
    lease_ref=str,
    runtime_run_id=str | None,
    source_id=str,
    source_plan_id=str | None,
    source_lane_run_id=str | None,
    candidate_evidence_id=str,
    provider_payload_ref=str | None,
)
```

CTS page size, Liepin max cards, Liepin detail recommendation limits, OpenCLI
mode, and provider-specific approval material must be source-local defaults or
payload refs owned by concrete adapters.

## Tach Target Graph

The final `tach.toml` should model this shape:

```text
seektalent.source_contracts -> seektalent.core
seektalent.providers -> seektalent.clients, seektalent.core, seektalent.retrieval, seektalent.source_contracts
seektalent.source_adapters -> seektalent.core, seektalent.retrieval, seektalent.providers, seektalent.source_contracts
seektalent.runtime -> seektalent.cache, seektalent.candidate_feedback, seektalent.controller, seektalent.core, seektalent.finalize, seektalent.reflection, seektalent.requirements, seektalent.retrieval, seektalent.scoring, seektalent.source_contracts
seektalent_ui -> seektalent.runtime, seektalent.source_adapters, seektalent.source_contracts
```

Forbidden final Tach edges:

```text
seektalent.source_contracts -> seektalent.runtime
seektalent.source_contracts -> seektalent.providers
seektalent.runtime -> seektalent.source_adapters
seektalent.runtime -> seektalent.sources
seektalent.source_adapters -> seektalent.runtime
seektalent.providers -> seektalent.runtime
```
