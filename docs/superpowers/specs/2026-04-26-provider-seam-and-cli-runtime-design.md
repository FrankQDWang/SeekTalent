# Provider Seam And CLI Runtime Design

Date: 2026-04-26

## Context

SeekTalent is currently optimized for fast local iteration around a single source: CTS. That was the right tradeoff early on, but the current runtime boundary now mixes three different concerns in the same place:

- core retrieval and decision flow
- CTS-specific query and filter adaptation
- runtime path resolution and artifact placement

`WorkflowRuntime` became the single place where orchestration, tracing, evaluation, rescue routing, and CTS-aware retrieval behavior accumulate. `_context_builder` also grew around that center of gravity. This still works, but it makes the next source integration more expensive because CTS details are not cleanly isolated.

The next architectural pressure is not UI. The product is CLI-only for the foreseeable future. The real pressure is multi-source retrieval: CTS today, and later Liepin, Boss, and customer-internal resume search SaaS systems. Those sources will differ in query shape, filter semantics, paging, detail fetch capabilities, and rate limits.

This design defines a first-phase refactor that introduces a provider seam, keeps `WorkflowRuntime` as a thin assembly point, moves CTS-specific adaptation behind an adapter boundary, and makes CLI runtime paths explicit instead of implicitly depending on ambient `cwd`.

## Goals

- Introduce a provider seam between core retrieval flow and source-specific retrieval behavior.
- Make CTS the first provider adapter without changing current CLI product scope.
- Keep core models and control flow provider-agnostic.
- Move CTS-specific query assembly, response assembly, filter projection, and source limits out of core.
- Treat thick CTS city and age filter adaptation as provider logic, not generic retrieval logic.
- Keep `WorkflowRuntime` as the main assembly point for now, but stop it from directly owning CTS details.
- Add an explicit runtime root so relative paths do not depend on whatever directory the caller happened to launch from.
- Preserve current CLI ergonomics by default.

## Non-Goals

- No UI redesign or UI API work.
- No dynamic plugin platform or marketplace-style provider system.
- No full decomposition of planner, scorer, reflector, and finalizer into separate frameworks.
- No immediate rewrite of all runtime modules.
- No change to the intentional judge-cache behavior of reusing labels across judge models to save cost.
- No attempt to generalize every future source in phase one.

## Design Summary

Phase one introduces a narrow `provider contract` and a small `retrieval core`.

The retrieval core remains inside SeekTalent core and continues to own source-agnostic logic:

- what to search next
- what constraints are active
- when to continue or stop
- how search observations are accumulated into round state
- how candidates move into normalization, scoring, reflection, and finalization

Each provider adapter owns source-specific logic:

- access control and credentials
- provider query assembly
- provider filter projection
- paging and cursor translation
- response parsing
- summary/detail fetch behavior
- provider-imposed limits and diagnostics

The contract between them is intentionally small:

- `ProviderCapabilities`
- `SearchRequest`
- `SearchResult`
- optional `fetch_details(...)` for providers that support it

This is a capability-declaration interface, not a bare `search(...)` pipe and not a plugin platform. Core sees explicit capability facts that affect control flow, but it does not import provider-specific models or branch on provider names.

## Proposed Structure

Suggested direction:

```text
src/seektalent/
  cli.py
  api.py
  config.py
  core/
    runtime.py
    runtime_context.py
    models.py
    retrieval/
      service.py
      provider_contract.py
      observation.py
      planner_bridge.py
  providers/
    registry.py
    cts/
      adapter.py
      client.py
      models.py
      filter_projection.py
      mapper.py
      auth.py
```

Notes:

- `WorkflowRuntime` may remain in its current module during phase one, but it should begin delegating retrieval-source behavior into the new core retrieval service and provider adapter modules.
- Existing scoring, reflection, finalization, and evaluation modules may continue to consume canonical candidates and observations without provider awareness.
- Existing `clients/cts_client.py` should move behind `providers/cts/` ownership. Core should stop importing it directly.

## Responsibilities

### CLI

CLI remains responsible for:

- parsing arguments
- loading `.env`
- deciding the effective runtime root
- building settings and runtime context
- invoking the run
- rendering results

CLI should not own CTS-specific retrieval behavior.

### Core

Core owns:

- planner output and round strategy
- canonical runtime constraints
- canonical query intent
- retrieval session flow
- canonical search observation
- candidate pool and memory
- normalization, scoring, reflection, stop logic, and finalization

Core must not know how CTS expresses city filters, age ranges, paging, or detail fetch.

### Provider Adapter

Each provider adapter owns:

- credential checks
- request construction
- filter translation
- provider-specific paging
- provider-specific result interpretation
- diagnostics about dropped, narrowed, or expanded constraints
- source-specific limits and rate behaviors

## Provider Contract

The phase-one contract should be small and explicit.

### `ProviderCapabilities`

This declares only facts that influence core behavior. Initial fields should stay close to current needs, for example:

- `supports_structured_filters`
- `supports_detail_fetch`
- `supports_fetch_mode_summary`
- `supports_fetch_mode_detail`
- `paging_mode`
- `recommended_max_concurrency`
- `has_stable_external_id`
- `has_stable_dedup_key`

The capability model should stay narrow. Do not add speculative flags until core actually needs them.

### `SearchRequest`

Core sends a canonical request that does not carry CTS naming or CTS payload structure.

Suggested content:

- query terms
- query role or search intent
- canonical constraints
- page intent or continuation token
- fetch mode: `summary` or `detail`
- per-call budget hints

### `SearchResult`

Provider returns:

- canonical candidate stubs
- canonical search observation fragments
- provider diagnostics
- continuation token or paging outcome
- exhaustion signal
- optional tracing references to raw provider payloads

Core business logic should consume only canonical fields and diagnostics. Raw provider payload references are for tracing and audit, not for branching business logic.

### Optional detail fetch

If a provider declares `supports_detail_fetch`, core may call:

- `fetch_details(items: list[CandidateRef]) -> list[CandidateDetail]`

If the capability is absent, core simply does not use that path.

## Canonical Models

Phase one needs a small set of source-agnostic models for retrieval.

Suggested categories:

- canonical constraints
- canonical search request
- canonical candidate stub
- canonical candidate detail
- canonical search observation
- canonical provider diagnostics

Important rule:

Core models express recruiting intent, not CTS field shape.

For example:

- `locations` means normalized location intent
- `age_range` means a semantic age constraint
- `experience_range` means a semantic experience constraint

Core should not carry CTS enum values, CTS field names, or CTS-only query fragments.

## CTS Adapter Scope

CTS becomes the first real adapter and should absorb the thick CTS-specific logic that currently leaks into generic retrieval code.

Phase-one CTS adapter ownership includes:

- authentication and tenant credentials
- CTS query assembly
- CTS response parsing
- summary/detail mode behavior
- provider page semantics
- provider limits
- CTS city projection
- CTS age projection
- any other CTS enum-heavy filter translation

This is important. City and age adaptation are not generic retrieval concerns. They are CTS expression concerns and belong under `providers/cts/` as CTS-owned projection code, not under generic core retrieval modules.

If existing `retrieval/filter_projection.py` logic is actually CTS-shaped, it should move under `providers/cts/`. Only truly provider-agnostic logic should remain under core retrieval.

## Retrieval Core

Phase one should introduce a small retrieval core instead of letting `WorkflowRuntime` continue to own source interaction directly.

The retrieval core should:

- accept planner output and round state
- build a canonical `SearchRequest`
- call the selected provider adapter
- merge canonical results into runtime state
- emit canonical observations and diagnostics

The retrieval core should not:

- know CTS request fields
- know CTS response fields
- import CTS models
- translate CTS enum filters

This keeps the first extraction bounded. It reduces future growth in `WorkflowRuntime` without forcing a full runtime rewrite in one step.

## WorkflowRuntime In Phase One

`WorkflowRuntime` remains the top-level assembly point in phase one.

That is acceptable if its role becomes narrower:

- assemble settings, prompts, core services, and provider selection
- run the existing high-level pipeline
- delegate source interaction to retrieval core + provider adapter
- stop importing or manipulating CTS-specific retrieval details directly

This is a controlled downgrade from “god runtime” to “thin assembly point,” not a full elimination of the class.

`_context_builder` also does not need a total rewrite in phase one. The rule is simply that provider-specific data shaping must stop expanding there. Only canonical context assembly may remain there.

## Runtime Root And Path Resolution

The current problem is not that CLI uses the current directory. The problem is that path resolution is ambient and implicit.

Phase one should introduce an explicit runtime root, for example `workspace_root` or `runtime_root`.

Rules:

- CLI determines the effective root once at startup.
- By default, CLI uses the launch directory so current user behavior stays the same.
- Settings and runtime services resolve relative paths against this explicit root.
- Python API may still default to current `cwd` if no root is provided, but the root becomes explicit and overridable.

This means:

- `runs_dir`
- `llm_cache_dir`
- judge cache path
- relative spec path
- other runtime artifact paths

all resolve against one explicit root instead of scattered `Path.cwd()` calls.

This fixes the review concern without changing the everyday CLI experience.

## Provider Selection

Phase one does not need a complex registry, but it does need one explicit selection point.

Recommended approach:

- one small provider registry in core
- settings choose the active provider, defaulting to CTS for now
- runtime asks the registry for the provider adapter

This is enough to support a second provider later without introducing plugin infrastructure.

## Migration Plan

Suggested sequence:

1. Define canonical provider contract and canonical retrieval models.
2. Add explicit runtime root and path-resolution plumbing.
3. Introduce a small retrieval core service that depends only on the provider contract.
4. Move CTS client, mapping, filter projection, and limits behind `providers/cts`.
5. Update `WorkflowRuntime` to delegate source interaction through retrieval core and the CTS adapter.
6. Remove remaining direct CTS imports from core runtime and generic retrieval modules.
7. Trim or relocate CTS-shaped logic that still sits under generic names.

## Testing

- Unit-test provider contract with a fake adapter.
- Unit-test provider capability handling for detail-fetch and filter support branches.
- Unit-test runtime root resolution so relative paths resolve against the explicit root rather than ambient `Path.cwd()`.
- Unit-test CTS city projection and age projection under the CTS adapter package.
- Unit-test retrieval core with a fake provider that returns canonical candidates and diagnostics.
- Regression-test CLI behavior so default local CLI runs still write under the same project-local paths when launched from the project root.
- Regression-test that `WorkflowRuntime` no longer imports CTS-specific retrieval modules directly.
- Regression-test that canonical retrieval models contain no CTS enum values or CTS field names.

## Acceptance Criteria

- CTS retrieval behavior is reachable only through a CTS adapter boundary.
- Core retrieval flow imports only canonical provider-contract types.
- Thick CTS city and age filter adaptation no longer lives in generic core retrieval code.
- `WorkflowRuntime` remains the assembly point but no longer directly owns CTS-specific request and response handling.
- CLI remains the only active product surface in scope.
- Relative runtime paths resolve against one explicit runtime root.
- Default CLI usage remains ergonomically unchanged.
- The design leaves room for a second provider without introducing a plugin platform.
