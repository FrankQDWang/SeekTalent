# Liepin Native Filter Adapter Design

## Goal

Make Liepin use the same Runtime-owned search logic as CTS for JD-derived filters: city, work experience, and age must come from Runtime query/filter intent, be compiled by the Liepin adapter into provider-specific browser actions, and be verified in a real Chrome run.

## Problem

The Runtime source adapter boundary now passes shared query identity, query role, lane type, requested count, budget, and filter intent to both CTS and Liepin. CTS already compiles Runtime filter intent into provider filters. Liepin currently receives the intent but does not apply native page filters; it searches with keywords and reports unsupported filter coverage.

That creates a product mismatch:

- CTS search is constrained by JD filters.
- Liepin search is broader than the same Runtime intent.
- Final Top 10 can be biased toward CTS because Liepin card search sees a different candidate distribution.
- The Workbench can show dual-source completion while one source has not actually used the same filter semantics.

## Current Code Facts

- Runtime filter intent is represented in `src/seektalent/runtime/source_filters.py`.
- Runtime source query intent is represented in `src/seektalent/runtime/source_query_intent.py`.
- CTS compiles Runtime filter intent in `src/seektalent/providers/cts/source_compiler.py`.
- Liepin currently emits `provider_filters={}` and marks location, age, and other Runtime filters unsupported in `src/seektalent/providers/liepin/source_compiler.py`.
- OpenCLI card search currently accepts only source run id, query, max pages, and max cards in `src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts` and `src/seektalent/providers/pi_agent/opencli_browser_cli.py`.
- `OpenCliBrowserRunner.search_liepin_cards(...)` fills the keyword query, clicks search, and reads card summaries without applying page filters.

## Product Requirements

1. Runtime remains the only owner of search strategy.
   - Liepin must not re-parse the JD.
   - Liepin must not independently decide explore/exploit, budget, location rebalance, age, or experience semantics.
   - Liepin adapter only compiles Runtime intent into provider-specific execution.

2. Liepin native filters must be represented as a provider-specific plan.
   - The plan supports city, work experience, and age in the first implementation.
   - The plan carries only normalized safe values, never full JD text, credentials, cookies, local paths, or raw provider payloads.
   - The plan distinguishes `applied`, `skipped`, and `unsupported` filters in protected action trace data.

3. City handling must respect Runtime location execution intent.
   - Single-city requirements select that city.
   - Multi-city requirements use Runtime's balanced/priority order for the current round.
   - Liepin may apply only one city per browser search, but the adapter must emit one safe browser search per Runtime city target so the full source lane still follows the same location plan as CTS.
   - The adapter must record per-city target metadata in protected traces without exposing implementation-specific browser details in public payloads.

4. Budget handling stays Runtime-owned.
   - Liepin `maxCards` remains derived from Runtime requested count / source budget.
   - Applying filters must not increase card budget or open detail pages.

5. Liepin card mode remains summary-only.
   - The browser may interact with filter UI and search results.
   - It must not open candidate details during card search.
   - It must not click contact, chat, download, phone, email, payment, or account settings.

6. Failure behavior is source-scoped.
   - Login, verification, risk, selector drift, or backend browser issues return a Liepin source-scoped blocked/partial/failed result.
   - Runtime invariant/programmer errors still raise and fail the round.
   - CTS must not be canceled because Liepin cannot apply a native filter.

7. Public payloads stay business-safe.
   - Workbench public APIs may show `source_filter_applied`, `source_filter_partial`, or `source_filter_unavailable`.
   - Workbench public APIs must not expose OpenCLI, PI, selector, local path, cookie, authorization, raw browser state, or raw provider payload terms.

8. Real completion verification is mandatory.
   - Unit tests alone are not enough.
   - Completion requires a real Chrome Workbench run using the saved login state and the prior real "数据开发专家" session input.
   - The run must include regular screenshots and final database/event checks.
   - The run must end with browser/tab/window cleanup and OpenCLI orphan cleanup.

## Non-Goals

- Do not redesign Runtime round decision logic.
- Do not change CTS search behavior except shared test assertions.
- Do not add automatic source allocation optimization.
- Do not implement Liepin detail opening beyond the existing approval/detail flow.
- Do not guarantee every possible Liepin filter field; first pass is city, work experience, and age.

## Acceptance Criteria

1. A new Liepin filter compiler maps Runtime filter/location intent to typed per-search native filter targets for city, experience, and age.
2. Liepin source compiler passes each native filter target through `SearchRequest.provider_context` without using raw JD text or nested `provider_filters`.
3. PI/OpenCLI task and tool boundaries accept a safe `nativeFilters` payload.
4. OpenCLI runner applies native filters before card extraction.
5. Action trace records attempted/applied/skipped native filters.
6. Unsupported, partially applied, or failed native-filter actions produce business-safe reason codes.
7. Existing Runtime logical query identity, fingerprint, role, lane type, and requested count are unchanged.
8. Existing CTS compiler behavior is unchanged.
9. New unit tests cover compiler mapping, OpenCLI payload propagation, browser action ordering, and safe public reason mapping.
10. `uv run pytest` targeted source-adapter/OpenCLI suites pass.
11. `uv run ruff check` passes for changed files.
12. `uv run ty check` passes for changed Python files.
13. `uv build` passes.
14. `cd apps/web-svelte && bun run build` passes.
15. Real Chrome QA verifies a "数据开发专家" session:
    - Workbench starts a Runtime-owned dual-source run.
    - Liepin source completes or degrades source-scoped without canceling CTS.
    - Liepin action trace shows native filter attempts.
    - The visible Liepin browser/search state shows the selected filter chip or equivalent applied filter state when the site supports it.
    - Final Top 10 is Runtime-finalization-backed.
    - Screenshots are captured at start, form-filled, agent-started, Liepin-filter-applied, mid-run, and final states.
    - Chrome tabs/windows and OpenCLI owned pages are cleaned at the end.
