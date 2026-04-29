# Remove Company Discovery And Target Company Design

Date: 2026-04-29

## Context

The repository already has a clearer retrieval boundary than it did earlier in productization:

- typed second lane
- `prf_probe if safe else generic_explore`
- replayable retrieval artifacts
- explicit `PRF v1.5` proposal boundaries
- sidecar-backed model-serving seams

But one older branch still remains in the runtime:

- explicit target-company term injection
- web company discovery
- company rescue continuation

This branch adds meaningful code surface across:

- runtime orchestration
- rescue routing
- prompt loading
- model call artifact logging
- web search/page-reading integrations
- benchmark and TUI reporting
- config and environment surface

It also no longer matches current product intent.

The latest benchmark smoke run showed the practical downside clearly:

- one `agent` sample run failed because company discovery hit an external security-check redirect instead of completing the main retrieval workflow
- the failure came from the company-discovery side channel, not from the primary retrieval path

At the same time, explicit target-company behavior is already effectively disabled by default, while `company_discovery_enabled` still defaults to `True`. This leaves the codebase carrying a partially active branch that adds complexity without serving the current roadmap.

## Problem

The company-discovery branch now has three costs:

1. It increases runtime complexity and rescue-branch complexity.
2. It introduces external web failure modes into otherwise valid retrieval runs.
3. It no longer supports a committed product direction.

The current direction is:

- keep retrieval controlled
- keep second-lane behavior comparable and replayable
- avoid company-led rescue logic in the primary system
- focus effort on stronger generic phrase extraction and multi-provider retrieval instead

Under that direction, company discovery is not a useful branch to preserve.

## Superseded Prior Decisions

This change supersedes the earlier Phase 1 decision to keep web company discovery as rescue-only company hypothesis generation.

That earlier decision was valid when the product still wanted a company-isolated late-rescue branch. It is no longer the active direction.

After this change:

- company rescue is not an active runtime branch
- company discovery is not an active prompt-loading branch
- company discovery is not an active artifact-generating branch
- explicit target-company retrieval is not an active query-planning branch

Historical artifacts may still contain company-discovery fields, company-rescue lane values, target-company records, or company prompt references. New runs must not.

## Goals

- Remove web company discovery from the active product.
- Remove explicit target-company term injection from the active product.
- Reduce rescue-path complexity and runtime failure surface.
- Ensure the main retrieval workflow no longer depends on company-side-channel behavior.
- Preserve replay, artifact, and benchmark integrity after removal.
- Leave `candidate_feedback` and `PRF v1.5` work intact.

## Non-Goals

- Do not switch `candidate_feedback` to `PRF v1.5 mainline` in this change.
- Do not redesign the remaining rescue policy in this change.
- Do not remove historical artifacts from old runs.
- Do not rewrite benchmark history or replay history.
- Do not change provider integration behavior outside the company-discovery branch.
- Do not remove `company_entity` or `ambiguous_company_or_product_entity` rejection semantics from `candidate_feedback` or `PRF v1.5`.

## Current State

Today the relevant defaults are:

- `target_company_enabled = False`
- `company_discovery_enabled = True`
- `prf_v1_5_mode = "shadow"`
- `prf_model_backend = "legacy"`

This means:

- explicit target-company behavior is already not meant to be active by default
- company discovery still remains available in runtime flow
- `candidate_feedback` is not yet using the new model-backed path as active mainline behavior

The new `PRF v1.5` stack is integrated into the system, but the default mainline still falls back to the legacy extractor path unless sidecar-backed PRF is explicitly promoted and enabled.

## Approaches Considered

### Option A: Config-Disable Only

Keep the company-discovery code and set the feature flags to disabled by default.

Pros:

- smallest immediate diff
- lower short-term risk

Cons:

- leaves dead branch complexity in runtime
- keeps tests, prompts, services, and audit paths around
- does not solve maintenance cost
- does not solve future regression risk

### Option B: Runtime Disable First, Delete Later

First short-circuit the runtime path, then remove code in a later cleanup.

Pros:

- safer staged rollout
- easier to isolate runtime regressions

Cons:

- still preserves a dead branch for another cycle
- delays the actual simplification benefit
- risks “temporary” dead code becoming semi-permanent

### Option C: Remove The Entire Branch Now

Delete:

- company discovery runtime branch
- target-company injection branch
- company-discovery package
- related prompts, config, artifacts, and tests

Pros:

- delivers real complexity reduction immediately
- removes external web failure from rescue flow
- aligns codebase with product direction
- simplifies benchmark interpretation

Cons:

- larger one-time change
- requires broad but mechanical test updates

## Decision

Choose **Option C**.

This is the cleanest match for the current roadmap. The feature is not strategically important, default explicit target-company behavior is already off, and the remaining company-discovery branch creates more productization risk than value.

## Scope Of Removal

This change removes both of the following, together:

1. **web company discovery**
2. **explicit target-company retrieval terms**

There will be no partial preservation of:

- `target_company_enabled`
- `retrieval_role="target_company"`
- `inject_target_company_terms()`
- `web_company_discovery` rescue lane
- company seed-term scheduling
- company-specific prompt steps

The repository should not keep an inactive shell of these behaviors after this change.

## Target Runtime Behavior

After this change:

- rescue flow must not route to `web_company_discovery`
- query-term pools must never be augmented with `target_company` terms
- controller and rescue decisions must not mention company-discovery branches
- main retrieval runs must not fail because of external company-search or page-read behavior

Rescue behavior remains limited to the non-company paths that still belong in the product, such as:

- `candidate_feedback`
- `anchor_only`

The exact ordering and stopping behavior of those remaining rescue paths is not changed here.

## Candidate Feedback Boundary

This change does **not** change the activation status of the new `candidate_feedback` stack.

After this removal, the system still behaves as follows unless changed by a separate rollout:

- `PRF v1.5` remains available
- sidecar-backed span extraction remains available
- embedding-backed familying remains available
- default runtime mode remains `shadow`
- default backend remains `legacy`

In other words:

- this change removes the company branch
- it does not simultaneously promote model-backed `candidate_feedback` to mainline

That promotion remains a separate product decision.

Removing company discovery does **not** mean allowing company-like terms into PRF promotion.

The following PRF boundaries remain valid after this change:

- `company_entity` may still be a rejected candidate term type
- `ambiguous_company_or_product_entity` may still be a valid reject reason
- product/platform handling remains separate from company-led retrieval

This change removes company-derived retrieval behavior. It does not relax PRF safety policy around company-like spans.

## Code Boundaries

The implementation should remove the company branch from all of the following layers.

### 1. Runtime And Routing

Remove:

- `src/seektalent/runtime/company_discovery_runtime.py`
- company-discovery helpers and call sites in `src/seektalent/runtime/orchestrator.py`
- any rescue-routing logic that selects `web_company_discovery`

The runtime should no longer construct or hold a `CompanyDiscoveryService`.

### 2. Domain Package

Remove the `src/seektalent/company_discovery/` package, including:

- service
- provider
- page reader
- scheduler
- query injection
- model steps
- models

### 3. Config Surface

Remove company-specific settings from `AppSettings`, environment defaults, and `.env.example`, including:

- `target_company_enabled`
- `company_discovery_enabled`
- `company_discovery_provider`
- `bocha_api_key`
- `company_discovery_model`
- `company_discovery_reasoning_effort`
- search-call budgets
- open-page limits
- timeout settings
- confidence thresholds
- accepted-company limits

No company-discovery flag should remain as a dormant no-op compatibility switch.

For deprecated local environment variables:

- checked-in `.env.example` and `default.env` should remove the company-discovery keys
- stale local `.env` values should be ignored rather than mapped into active settings
- stale values must not appear in `run_config`, audit output, or manifests

This is a decommission cleanup rule, not a compatibility feature. The removed variables must not remain first-class settings.

### 4. Prompt And Asset Surface

Remove company-discovery prompt requirements and prompt hashes from:

- prompt maps
- runtime audit output
- prompt snapshot logic
- benchmark prompt expectations

This includes the named prompts:

- `company_discovery_plan`
- `company_discovery_extract`
- `company_discovery_reduce`

### 5. Artifact Surface

New runs must stop generating company-discovery artifacts such as:

- `company_discovery_input`
- `company_discovery_result`
- `company_discovery_plan`
- `company_discovery_decision`
- `query_term_pool_after_company_discovery`
- company-discovery model-call artifacts
- company-search query artifacts
- company-evidence-card artifacts

Historical runs remain readable. No migration or deletion of old artifacts is required.

### 6. UI And Reporting Surface

Remove company-discovery-specific handling from:

- benchmark summaries
- TUI rendering
- runtime audit summaries
- replay/export metadata that only exists for the company branch

### 7. Active Vocabulary

After removal, active runtime vocabulary must be narrowed.

Allowed active retrieval and rescue concepts include:

- `exploit`
- `prf_probe`
- `generic_explore`
- remaining non-company rescue choices such as `candidate_feedback` and `anchor_only`

Disallowed active values include:

- `company_rescue`
- `web_company_discovery`
- `target_company`

Historical artifacts may still contain those removed values. Active runtime state, active decision models, and active new artifacts must not.

## Data And Replay Expectations

This removal must preserve the typed artifact and replay rules already established elsewhere.

Specifically:

- no new ad hoc paths may be introduced as part of the cleanup
- remaining rescue artifacts must still flow through `ArtifactStore` and logical names
- old runs containing company-discovery artifacts must remain readable as historical records

No compatibility layer is required for generating company-discovery artifacts in new runs.

## Historical Read Compatibility

Deleting the active branch must not make archived runs unreadable.

Archive-aware readers must tolerate historical:

- `company_discovery_*` artifacts
- `company_rescue` or `web_company_discovery` lane values
- `company_rescue_policy_version`
- company-discovery prompt refs
- `retrieval_role="target_company"` records
- retrieval-state fields such as `company_discovery_attempted` and `target_company_plan`

This tolerance is read-only.

It must not preserve:

- active config fields
- active runtime routes
- active prompt loading
- active artifact generation

If a reader needs filtering, compatibility parsing, or legacy-field dropping to preserve historical readability, that logic should live in read-only compatibility paths rather than in active runtime models.

## Provider Secret Boundary

`bocha_api_key` and related web-search configuration may be removed as part of this change because the current codebase uses them only for company-discovery-owned behavior.

If any provider-level web-search integration is later introduced outside company discovery, it must come back under a separate provider-owned configuration surface rather than by preserving this deleted branch.

## Failure-Mode Expectations

After removal:

- retrieval runs must not issue company-discovery web requests
- retrieval runs must not fail because of company-side-channel HTTP redirects or anti-bot pages
- rescue decisions must degrade only through the remaining supported rescue branches

This change intentionally reduces the system’s failure surface by deleting a branch instead of adding soft-fail recovery logic around it.

## Testing Expectations

At minimum, implementation must update or add tests that prove:

1. company-discovery runtime selection is gone
2. target-company term injection is gone
3. `AppSettings` no longer expose company-discovery settings
4. prompt-loading and runtime-audit expectations no longer include company-discovery prompts
5. no company-discovery artifacts are emitted in new runs
6. benchmark and TUI reporting no longer mention `web_company_discovery`
7. the benchmark smoke path can run without entering any company-discovery branch
8. historical replay and archive-aware readers still tolerate legacy company-discovery artifacts in read-only mode
9. `PRF v1.5` company/entity rejection behavior remains intact
10. deleting company discovery does not change default `PRF v1.5` rollout settings

Tests that only validated company-discovery functionality should be removed.

Tests that validated company isolation or company absence should be replaced with explicit absence tests, such as:

- active runtime no longer imports `seektalent.company_discovery`
- active prompt registries no longer include company-discovery prompts
- active artifact registries no longer include company-discovery logical artifacts
- active lane vocabulary no longer includes `web_company_discovery`
- default `PRF v1.5` settings remain `shadow + legacy`

## Risks

### Risk 1: Broad Diff Surface

The removal touches runtime, prompts, config, audit, reporting, and tests.

Mitigation:

- keep the change mechanical
- do not redesign unrelated rescue logic in the same patch
- verify benchmark smoke behavior after removal

### Risk 2: Hidden Coupling In Audit Or Reporting

Company prompts and artifacts are currently referenced across audit and benchmark paths.

Mitigation:

- remove them systematically from prompt hashes, run config snapshots, summary exporters, and UI renderers
- rely on focused regression tests rather than manual spot edits

### Risk 3: Accidental Candidate Feedback Behavior Change

Because company discovery currently lives adjacent to rescue logic, cleanup could accidentally perturb `candidate_feedback`.

Mitigation:

- treat `candidate_feedback` as preserved behavior in this change
- verify rescue decisions and PRF proposal artifacts separately
- do not combine this change with a `PRF v1.5 mainline` promotion

## Acceptance Criteria

This design is complete when all of the following are true:

- no active runtime path can invoke company discovery
- no active runtime path can inject target-company query terms
- no company-discovery settings remain in the app configuration surface
- no company-discovery prompt names remain in active audit or prompt-loading paths
- no new company-discovery artifacts are written
- historical runs remain readable
- `candidate_feedback` and `PRF v1.5` boundaries remain intact
- benchmark smoke runs no longer fail because of company-discovery web behavior

## Follow-Up

After this removal lands, the next product questions become clearer:

1. whether `candidate_feedback` should remain in legacy-mainline mode for longer
2. when to promote `PRF v1.5` from `shadow` to `mainline`
3. how to tighten rescue stopping behavior now that the company branch is gone

Those are separate changes and should be evaluated on their own, not bundled into this deletion.
