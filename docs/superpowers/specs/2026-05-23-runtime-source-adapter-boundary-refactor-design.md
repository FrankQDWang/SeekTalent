# Runtime Source Adapter Boundary Refactor Design

## Linked Plan

- Plan: [2026-05-23-runtime-source-adapter-boundary-refactor.md](../plans/2026-05-23-runtime-source-adapter-boundary-refactor.md)

## Summary

SeekTalent's sourcing runtime must treat Runtime as the product core and CTS/Liepin as source adapters. The current codebase has moved important orchestration into Runtime, including multi-source dispatch, shared logical query identity, merge, scoring, and finalization. That is the right direction, but the source boundary is still uneven:

- CTS still receives mature Runtime search plans with filters, query roles, refill, location rebalance, query outcome scoring, and budget semantics.
- Liepin receives the shared logical query metadata, but its runtime path is still mostly keyword/card-search driven and does not receive the same filter, rebalance, query-role, or budget contract as CTS.

This refactor is not a broad cleanup. It is a contract correction: Runtime owns the search strategy; source adapters only compile and execute that strategy against their provider.

## Current Code Facts

The following facts are true in the current working tree and shape the design:

- `SearchControllerDecision.proposed_filter_plan` exists in `src/seektalent/models.py`.
- `src/seektalent/runtime/orchestrator.py` currently projects constraints to CTS with `project_constraints_to_cts(...)` before building the retrieval plan.
- `SearchControllerDecision.action` and the controller prompt still use the historical value `search_cts` for "continue searching"; this is a legacy internal label, not evidence that CTS should own Runtime orchestration.
- `LogicalQueryDispatch` carries shared query identity and requested count, but it does not carry provider-neutral filter intents, location execution intent, age intent, or provider scan limits.
- CTS runtime execution uses the mature `RetrievalRuntime` search path and keeps CTS filter/rebalance behavior.
- Liepin runtime execution uses `run_liepin_logical_query_bundle(...)` and `run_liepin_source_lane(...)`.
- The Liepin lane builds `SearchRequest(..., query_role="primary")` and defaults `provider_filters` to an empty mapping.
- The current Pi/OpenCLI search tool accepts `sourceRunId`, `query`, `maxPages`, and `maxCards`; it does not accept first-class city, age, or requirement filters.
- `RuntimeSourceBudgetPolicy` has separate CTS and Liepin knobs. Liepin's default card scan limit can currently exceed the Runtime logical requested count.
- Liepin is a card-first source. It can collect result-page summaries before opening details. Detail opening is a separate, approval-sensitive action and must not be conflated with card count.

## Product Contract

Runtime owns:

- source selection for the session;
- round count and stop/continue decisions;
- explore/exploit lane allocation;
- candidate feedback incorporation;
- logical query identity and fingerprinting;
- requested candidate count per logical query;
- provider scan limit per logical query and source;
- provider-neutral filter intent;
- city/location and age execution intent;
- budget, refill, and broadening strategy;
- source failure taxonomy;
- merge-before-ranking;
- final scoring and final Top 10 order.

Source adapters own:

- compiling Runtime query intent to provider-native API fields, browser UI steps, or safe post-filtering;
- executing provider-specific search;
- returning source evidence, counts, and safe coverage state;
- reporting unsupported or degraded provider capabilities without hiding them.

Workbench owns:

- session lifecycle;
- user-facing projections;
- source cards;
- notes;
- strategy graph rendering;
- approval and review state.

Workbench must not become the source orchestration owner again.

## Required Behavior

### 1. Runtime Generates Provider-Neutral Search Intent

Runtime must produce a source-neutral intent before any CTS-specific or Liepin-specific compilation. The intent must be derived from:

- `RequirementSheet`;
- `SearchControllerDecision.proposed_filter_plan`;
- round state;
- candidate feedback;
- location and age rebalance state;
- logical query bundle;
- selected source set;
- Runtime budget policy.

The intent must not contain CTS-only filter keys or Liepin-only UI details. Provider-specific vocabulary belongs in source compilers.

The legacy controller action value `search_cts` may remain accepted in this slice for backward compatibility with existing structured-output tests and audit fixtures. Runtime must normalize it as "continue source search" before source planning. New source intent, adapter, Workbench, and public event contracts must use Runtime-neutral names.

### 2. Shared Query Intent Reaches Every Selected Adapter

Each selected source adapter must receive the same Runtime-owned query identity and role metadata:

- `round_no`;
- `query_role`;
- `lane_type`;
- `query_instance_id`;
- `query_fingerprint`;
- `query_terms`;
- `keyword_query`;
- `requested_count`;
- `provider_scan_limit`;
- `source_plan_version`;
- provider-neutral filter intents;
- provider-neutral location execution intent;
- provider-neutral age execution intent when available.

Adapters must not regenerate `query_instance_id`, `query_fingerprint`, lane role, or requested count.

### 3. Filter Compilation Is Adapter-Specific

CTS must compile Runtime filter intent to CTS-native request fields using the existing mature CTS behavior where possible.

Liepin must compile Runtime filter intent to the best supported Liepin execution path:

- native search UI action when implemented and safe;
- keyword augmentation when the provider lacks a structured field and the behavior is explainable;
- post-filtering of card summaries or normalized resumes when safe;
- explicit unsupported/degraded coverage state when a filter cannot be enforced.

Unsupported filters must not silently disappear.

### 4. Budget Semantics Are Runtime-Owned

`requested_count` means the number of desired candidates for a logical query lane.

`provider_scan_limit` means the maximum provider-side cards/results that a source adapter may inspect for that logical query. An adapter may return fewer results, but it must not widen the scan beyond the Runtime-provided limit.

For this slice, Liepin's default per-query `provider_scan_limit` is:

```text
min(logical_query.requested_count, source_budget_policy.liepin_max_cards)
```

`liepin_card_page_size` controls provider pagination shape only. It must not increase `maxCards` beyond `provider_scan_limit`. Any later Liepin card overfetch multiplier requires a separate plan, explicit product decision, and tests proving it does not reintroduce hidden broad scans.

For Liepin, card summaries and opened full resumes are different units:

- card count is the number of search-result summaries inspected;
- detail-open count is the number of full resumes opened after a card-level value judgment;
- this refactor does not require opening every Liepin card.

### 5. CTS Behavior Must Remain Mature

CTS must preserve:

- query outcome scoring;
- refill behavior;
- broad/noise detection;
- city/location rebalance;
- age-related filtering where already supported;
- existing candidate scoring and audit semantics.

The source adapter boundary must not replace CTS query outcome scoring with an empty scorer.

### 6. Liepin Must Use the Same Runtime Flow

Liepin must participate in the same Runtime rounds, query roles, query identity, requested counts, provider scan limits, merge, scoring, and finalization.

Liepin may differ only at the provider execution layer:

- it starts from search-result cards;
- it may need a card-level value judgment before detail opening;
- it may support fewer native filters than CTS;
- unsupported filters must be reflected in safe source coverage state.

### 7. Merge Before Ranking Remains Mandatory

Candidates from CTS and Liepin must merge by Runtime candidate identity before final ranking. One person must appear once in the final Top 10, with all available source evidence retained.

### 8. Public Payloads Must Stay Business-Safe

Workbench public APIs, events, notes, graph payloads, and DOM must expose business-facing source state, not provider implementation terms.

Internal audit may keep provider-specific reason codes. Public serializers must map them to safe reason codes such as:

- `source_filter_unsupported`;
- `source_filter_degraded`;
- `source_location_filter_unsupported`;
- `source_age_filter_unsupported`;
- `source_budget_limited`;
- `source_login_required`;
- `source_browser_backend_unavailable`;
- `source_provider_failed`;
- `source_partial`.

### 9. Real Browser QA Is Required

Because Liepin execution depends on real browser behavior and logged-in provider state, build verification must include a Chrome run against the real local Workbench, using the same real session input already used in QA. The test must include regular screenshots and an explicit cleanup pass for browser windows, tabs, and OpenCLI-owned pages.

## Non-Goals

This refactor must not include:

- a full repository rewrite;
- a new provider marketplace;
- a generic capability descriptor framework for future sources;
- a full candidate evidence graph rewrite;
- node-detail visual completeness fixes;
- automatic Liepin detail opening for every card;
- OpenCLI native city/age UI automation beyond explicitly implemented and verified adapter actions;
- a global rename of all historical `search_cts` prompt, audit, test, and tool-name strings;
- deployment or production migration.

Node-detail completeness remains a deferred Workbench UI follow-up.

## Acceptance Criteria

1. Tests prove CTS and Liepin receive Runtime query intents with identical `round_no`, `query_role`, `lane_type`, `query_instance_id`, `query_fingerprint`, `query_terms`, `keyword_query`, and `requested_count` for the same logical query.
2. Tests prove Runtime query intents carry provider-neutral filter and location intent before CTS/Liepin compilation.
3. Tests prove Liepin does not replace Runtime query role with a hard-coded primary role.
4. Tests prove Liepin card scan limit is derived from Runtime `provider_scan_limit` and cannot silently widen to its previous default.
5. CTS-only session behavior remains compatible with the existing Runtime CTS tests.
6. Liepin-only session behavior can run through Runtime without requiring CTS source-run orchestration.
7. Dual-source sessions merge CTS and Liepin identities before final ranking.
8. Unsupported Liepin filters produce safe source coverage state instead of being silently dropped.
9. Public Workbench payload tests show no provider implementation terms in session, event, final Top 10, or graph responses.
10. A real Chrome QA run verifies the Workbench can start a session with the previous real input, dispatch selected sources through Runtime, show the strategy graph, and complete browser cleanup.
11. New Runtime source intent, adapter, Workbench, and public event code does not add new CTS-owned orchestration naming. Existing `search_cts` controller strings are treated as legacy compatibility labels only.

## Resolved Design Decisions From Plan Review

1. `RuntimeSourceQueryIntent` remains a separate adapter-facing object. `LogicalQueryDispatch` stays the logical query bundle contract.
2. Neutral filter canonicalization starts in `src/seektalent/runtime/source_filters.py`. Do not create a repository-wide generic filter layer in this slice.
3. Unsupported filters are reported per compiled query and rolled up per source round for source-card and graph projection.
4. Historical `search_cts` controller action strings are accepted as compatibility input, then normalized to Runtime-neutral source planning. A global prompt/schema rename is deferred.
