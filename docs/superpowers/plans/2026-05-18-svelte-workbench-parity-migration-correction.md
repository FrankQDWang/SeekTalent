# Svelte Workbench Parity Migration Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `apps/web-svelte` into a faithful Svelte/SvelteKit migration of the existing React workbench, preserving the recruiter-facing UI, login/setup/settings routes, workbench layout, source controls, graph, notes, candidate queue, and Liepin/CTS multi-source semantics instead of continuing the current divergent Svelte spike UI.

**Architecture:** Treat `apps/web/src/app.tsx` and `apps/web/src/styles.css` as the golden master. Keep the Python backend and runtime source-lane semantics as the product truth. Use SvelteKit for route/component migration, OpenAPI-generated types for API access, Svelte Query for data fetching, and Svelte Flow only as the graph renderer replacement. The Svelte app must consume the existing backend contracts, especially identity-level final Top 10, source lane runtime state, requirement triage, source connection, and detail recommendation APIs.

**Tech Stack:** SvelteKit, Svelte 5, TypeScript, `@tanstack/svelte-query`, `@xyflow/svelte`, `openapi-fetch`, Vitest, Playwright, Bun, Python/FastAPI backend tests, Ruff.

---

## Spec Link

Spec: `docs/superpowers/specs/2026-05-18-svelte-workbench-parity-migration-correction-design.md`

## Execution Notes

- Start implementation in a new worktree; do not work directly on `main`.
- Do not touch unrelated dirty files already present on `main`, especially `pyproject.toml` and `uv.lock`, unless a task explicitly requires it and the diff is verified as part of this plan.
- Do not redesign the product UI. The old React workbench is the visual and interaction reference.
- Do not remove or rewrite the React app during this migration. It remains the golden master until parity is verified.
- Do not reintroduce legacy browser fallback surfaces in Svelte. In `pi_agent` mode, Svelte must not call login relay, server-managed browser, managed local worker, external HTTP worker, or iframe handoff endpoints.
- Keep local data-root posture out of the primary recruiter UI. Backend diagnostics may retain it, but Svelte should not surface it in the main workbench.
- Preserve the existing backend semantic guardrails from the previous milestone: Liepin blocked/partial status propagation, backend blank-triage rejection, source badges, identity-level final Top 10, and no-leak/no-fallback checks.

## File Structure

Primary files to modify or add:

```text
apps/web-svelte/src/routes/(auth)/login/+page.svelte
apps/web-svelte/src/routes/(auth)/setup/+page.svelte
apps/web-svelte/src/routes/(app)/+layout.svelte
apps/web-svelte/src/routes/(app)/sessions/+page.svelte
apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte
apps/web-svelte/src/routes/(app)/settings/+page.svelte
apps/web-svelte/src/routes/(app)/settings/sources/+page.svelte
apps/web-svelte/src/routes/(app)/settings/sources/liepin/+page.svelte
apps/web-svelte/src/routes/(app)/connections/liepin/[connectionId]/login/+page.svelte
apps/web-svelte/src/routes/layout.css
apps/web-svelte/src/lib/api/workbench.ts
apps/web-svelte/src/lib/query/keys.ts
apps/web-svelte/src/lib/components/*.svelte
apps/web-svelte/src/lib/workbench/*.ts
apps/web-svelte/tests/e2e/*.spec.ts
apps/web-svelte/tests/e2e/parityMockApi.ts
scripts/verify-dev-workbench.sh
docs/ui.md
apps/web-svelte/SPIKE_REPORT.md
TODOS.md
```

## Task 0: Add Parity Test Harness Before UI Migration

**Goal:** Lock the React golden-master behavior in Svelte e2e tests before replacing current spike pages.

- [ ] Create `apps/web-svelte/tests/e2e/parityMockApi.ts`.
- [ ] Add allowed route mocks for:
  - `/api/auth/me`
  - `/api/auth/login`
  - `/api/auth/logout`
  - `/api/auth/bootstrap`
  - `/api/workbench/sessions`
  - `/api/workbench/sessions/{session_id}`
  - `/api/workbench/sessions/{session_id}/candidates`
  - `/api/workbench/sessions/{session_id}/final-top10`
  - `/api/workbench/events`
  - `/api/workbench/events/stream`
  - `/api/workbench/sessions/{session_id}/events`
  - `/api/workbench/sessions/{session_id}/events/stream`
  - `/api/workbench/sessions/{session_id}/graph-candidates`
  - `/api/workbench/sessions/{session_id}/graph-candidates/{graph_candidate_id}/resume-snapshot`
  - `/api/workbench/sessions/{session_id}/source-runs/liepin/policy`
  - `/api/workbench/detail-open-requests`
  - `/api/workbench/detail-open-requests/{request_id}/approve`
  - `/api/workbench/detail-open-requests/{request_id}/reject`
  - `/api/workbench/source-connections`
  - `/api/workbench/source-connections/{connection_id}`
  - `/api/workbench/source-connections/liepin`
  - `/api/workbench/sessions/{session_id}/start`
  - `/api/workbench/sessions/{session_id}/triage/prepare`
  - `/api/workbench/sessions/{session_id}/triage`
  - `/api/workbench/sessions/{session_id}/triage/approve`
  - `/api/workbench/sessions/{session_id}/candidates/{review_item_id}`
  - `/api/workbench/sessions/{session_id}/candidates/{review_item_id}/provider-actions/open`
  - `/api/workbench/sessions/{session_id}/candidates/{review_item_id}/detail-open-requests`
- [ ] Add forbidden-route sentinels that fail the test if Svelte handwritten code calls legacy managed-browser endpoints:
  - `/api/workbench/source-connections/{connection_id}/login`;
  - `/api/workbench/source-connections/{connection_id}/login/frame`;
  - `/api/workbench/source-connections/{connection_id}/login/snapshot`;
  - `/api/workbench/source-connections/{connection_id}/login/input`;
  - `/api/workbench/source-connections/{connection_id}/login/complete`.
- [ ] Model at least three source states:
  - CTS completed + Liepin completed.
  - CTS completed + Liepin blocked/login-required.
  - CTS completed + Liepin partial with valid cards.
- [ ] Create `apps/web-svelte/tests/e2e/workbench-parity.spec.ts`.
- [ ] Cover auth redirect: unauthenticated `/sessions` redirects to `/login` and does not render session content.
- [ ] Assert unauthenticated protected routes do not call session, candidate, graph, source connection, or settings APIs before redirecting to `/login`.
- [ ] Cover login/setup route presence and visual shell.
- [ ] Cover authenticated layout: topbar, source link, logout button, session rail, rail search, rail collapse, and active session highlight.
- [ ] Cover EventSource behavior:
  - unauthenticated protected navigation opens no stream;
  - authenticated `/sessions` opens `/api/workbench/events/stream`;
  - authenticated `/sessions/{sessionId}` opens `/api/workbench/sessions/{session_id}/events/stream`;
  - switching between session and non-session routes closes the previous stream before opening the next one.
- [ ] Cover negative copy checks: the primary UI must not contain `Svelte 5 Workbench Spike`, `Dev mode BYOK`, or data-root posture labels.

Expected tests initially fail until later tasks restore the UI.

## Task 1: Restore Auth, Setup, And Login Route Parity

**Goal:** Make Svelte route behavior match the React app's public entry and authentication flow.

- [ ] Create `apps/web-svelte/src/lib/components/AuthShell.svelte` with the same centered auth layout and brand tone as React.
- [ ] Rewrite `apps/web-svelte/src/routes/(auth)/login/+page.svelte` to match React `LoginPage` behavior:
  - email and password fields;
  - no hard-coded demo default credentials;
  - submit to the generated API wrapper;
  - redirect to `/sessions` after login;
  - safe error copy without backend secret leakage.
- [ ] Add `apps/web-svelte/src/routes/(auth)/setup/+page.svelte` matching React `SetupPage`:
  - first admin setup form;
  - success redirects to `/login`;
  - setup-specific error handling.
- [ ] Keep `/` redirecting to `/sessions`.
- [ ] Add or update API wrappers for setup/login/logout/me if current wrappers are incomplete.
- [ ] Run focused e2e for auth redirect, setup, and login route rendering.

## Task 2: Restore Authenticated Shell, Topbar, Session Rail, And Event Stream

**Goal:** Replace the current Svelte spike layout with the React workbench shell.

- [ ] Create `apps/web-svelte/src/lib/components/AuthenticatedLayout.svelte`.
- [ ] Create `apps/web-svelte/src/lib/components/Topbar.svelte`.
- [ ] Create `apps/web-svelte/src/lib/components/SessionRail.svelte`.
- [ ] Modify `apps/web-svelte/src/routes/(app)/+layout.svelte` to:
  - call `/api/auth/me`;
  - redirect 401/403 to `/login`;
  - avoid rendering protected children until `/api/auth/me` succeeds;
  - expose an auth-ready state or equivalent guard so child route queries are not created before authentication succeeds;
  - render the React-equivalent topbar;
  - render the collapsible session rail;
  - show user identity;
  - link to `/settings/sources`;
  - call logout and redirect to `/login`.
- [ ] Keep session rail behavior aligned with React:
  - loading state;
  - error state;
  - empty state;
  - search filter;
  - active session route highlight;
  - compact collapse button.
- [ ] Add `apps/web-svelte/src/lib/workbench/eventStream.ts` mirroring React `useWorkbenchEventStream` behavior:
  - after authentication, open exactly one Workbench event stream;
  - on `/sessions/[sessionId]`, use `/api/workbench/sessions/{session_id}/events/stream`;
  - on authenticated non-session routes, use `/api/workbench/events/stream`;
  - safely close on session change/unmount;
  - invalidate session, candidates, final Top 10, graph candidates, graph snapshots, detail requests, settings, source connections, source connection detail, and source policy keys on relevant event types.
- [ ] Remove `refetchInterval` polling from the primary session detail queries once the event stream is installed. Polling may exist only as a clearly named degraded fallback, not as a parallel primary state source.
- [ ] Add a Svelte unit or e2e EventSource test with a mock EventSource implementation matching the React app tests:
  - no stream before auth succeeds;
  - one global stream on authenticated non-session routes;
  - one session stream on authenticated session routes;
  - previous stream `close()` is called on route changes and logout.
- [ ] Update query keys in `apps/web-svelte/src/lib/query/keys.ts` so route components and event stream invalidation share one vocabulary.
- [ ] Add missing query keys for global events, source connection detail, settings, detail open requests, source policy, and graph snapshot roots if they are needed by the React-parity event stream.
- [ ] Verify no spike label remains in the layout.

## Task 3: Port The React Visual System Into Svelte

**Goal:** Make the Svelte UI visually read as the same workbench, not a new app.

- [ ] Replace the current divergent `apps/web-svelte/src/routes/layout.css` tokens with the React visual contract from `apps/web/src/styles.css`.
- [ ] Preserve the React palette and density:
  - `#f6f5f1` page background;
  - `#fbfaf6` and `#fffefb` surfaces;
  - `#3c5a4a` accent;
  - IBM Plex Sans / Noto Sans SC stack;
  - compact 5-8px radii;
  - dense operational panels instead of large dashboard cards.
- [ ] Port class families used by the React workbench:
  - `workbench-app`, `topbar`, `session-rail`, `workbench-main`;
  - `reference-grid`, `jd-panel`, `strategy-panel`, `right-rail`;
  - `source-card`, `candidate-card`, `activity-log`, `node-detail`;
  - `auth-page`, `settings-page`, source settings classes.
- [ ] Adapt React Flow selectors to Svelte Flow selectors without changing graph semantics.
- [ ] Audit component-scoped `<style>` blocks in `apps/web-svelte/src/lib/components/**` and migrate or remove spike styling that conflicts with React parity, especially graph nodes, graph canvas, node detail, loading, and error components.
- [ ] Remove Tailwind-specific styling from primary components if it conflicts with the React visual system.
- [ ] Add a static e2e assertion or grep check that the Svelte app does not use the old spike `oklch` theme tokens in primary route CSS or component-scoped styles.

## Task 4: Replace `/sessions` Dashboard With React-Parity Session Index

**Goal:** Make `/sessions` the same recruiter entry surface as the React app.

- [ ] Rewrite `apps/web-svelte/src/routes/(app)/sessions/+page.svelte`.
- [ ] Replace the current dashboard/card grid with the React `reference-grid empty-session` layout:
  - left JD/create panel;
  - center ready-state strategy panel;
  - right rail empty state.
- [ ] Create `apps/web-svelte/src/lib/components/CreateSessionForm.svelte` using React `CreateSessionForm` as the behavior reference.
- [ ] Preserve session creation fields and validation:
  - job title;
  - JD text;
  - notes;
  - source selection for CTS and Liepin.
- [ ] Default source selection must remain explicit in UI. For local dual-source pilot it may default to both CTS and Liepin, but the user must see and be able to change the selected sources.
- [ ] Create `apps/web-svelte/src/lib/components/ReadyStatePanel.svelte` matching React copy and layout.
- [ ] Keep session lists in `SessionRail`, not as the main page's card dashboard.
- [ ] Remove or stop using the current `ReadinessPanel` from the primary `/sessions` screen.

## Task 5: Port Session Detail Workbench Grid

**Goal:** Make `/sessions/[sessionId]` match the React workbench shell and workflows.

- [ ] Rewrite `apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte` around the React `reference-grid`:
  - left JD/source/triage panel;
  - center strategy graph;
  - right notes/detail/candidate rail.
- [ ] Create or port these components:
  - `JobBrief.svelte`;
  - `CriteriaHighlights.svelte`;
  - `RequirementTriageGate.svelte`;
  - `SourceCard.svelte`;
  - `StrategyCanvas.svelte`;
  - `RightWorkbenchTabs.svelte`;
  - `ActivityLog.svelte`.
- [ ] Preserve React central action logic:
  - if triage has not started, show prepare/start requirement analysis action;
  - if triage is pending/running, show progress and disable unsafe start;
  - if triage has visible criteria and is not approved, show approve/start action;
  - if triage is approved and source runs are queued/blocked, show start search action;
  - never allow approving blank/hidden criteria through UI.
- [ ] Render source cards from backend source run state:
  - CTS and Liepin as peer source branches;
  - blocked/partial/completed visible from runtime lane state;
  - coverage reason visible in product language;
  - Liepin detail recommendation budget visible when present.
- [ ] Reuse existing `StrategyGraph` and `buildRunStory` where they match React graph semantics.
- [ ] If current Svelte graph is structurally different, adapt it to the React node/lane composition rather than inventing a new graph.
- [ ] Add component/e2e tests for:
  - blank triage cannot be approved;
  - CTS completed + Liepin blocked does not display all-source complete;
  - Liepin partial still shows collected card evidence and degraded coverage.

## Task 6: Port Candidate Queue, Final Top 10, Detail Requests, And Resume Snapshot

**Goal:** Preserve the product requirement that multi-source results are ranked as identity-level Top 10, with duplicate candidates merged and the canonical/latest resume selected.

- [ ] Add `apps/web-svelte/src/lib/workbench/finalCandidateCards.ts`.
- [ ] Make the visible candidate queue consume `/api/workbench/sessions/{session_id}/final-top10` as the ranking source.
- [ ] Use `/api/workbench/sessions/{session_id}/candidates` only to enrich action affordances that are not present in final Top 10.
- [ ] Do not implement `items.slice(0, 10)` over raw review items as final ranking.
- [ ] Define and test an internal `FinalCandidateViewModel` with these rules:
  - `rank`, display fields, `runtimeIdentityId`, `canonicalReviewItemId`, `mergedReviewItemIds`, `sourceEvidence`, `sourceBadges`, `evidenceLevel`, and score fields come from `/final-top10`;
  - action target defaults to `canonicalReviewItemId`;
  - fallback action target may come from `mergedReviewItemIds` only after joining `/candidates` and confirming the target has the required safe affordance;
  - `detailActionReviewItemId` is a separate field, selected from the joined `/candidates` rows as the review item with Liepin card evidence and no Liepin detail evidence;
  - when canonical is a CTS review item but a merged Liepin card review item exists, detail request uses the Liepin card review item, not the canonical CTS item;
  - if no joined review item satisfies the Liepin card/no-detail rule, do not render a detail request action;
  - status and note display default to the canonical review item;
  - conflicting merged review-item statuses or notes render a small merged-state hint rather than a fake single raw state;
  - resume snapshot expansion is enabled only for a backend-expandable graph candidate tied to the chosen review item;
  - Liepin detail request appears only for identities with Liepin card evidence and no Liepin detail evidence;
  - provider/browser open appears only when the backend provider-action endpoint returns a safe action for the chosen review item.
- [ ] Preserve these candidate semantics in UI:
  - identity-level row;
  - canonical resume id;
  - merged review item ids;
  - source badges such as `CTS final`, `Liepin card`, `Liepin detail`, and `Multiple sources`;
  - evidence levels;
  - duplicate/merge signal when available;
  - newest/canonical resume selection displayed without claiming hidden PII.
- [ ] Add a compact coverage and merge explanation above or inside the final queue:
  - dual-source complete;
  - CTS-only because Liepin is blocked;
  - Liepin partial with card evidence preserved;
  - number of merged duplicate identities when derivable from `mergedReviewItemIds` or backend summary fields;
  - canonical/latest resume hint when final Top 10 chooses a different source row than another merged item.
- [ ] Create or port:
  - `CandidateReviewQueue.svelte`;
  - `CandidateReviewCard.svelte`;
  - `DetailOpenRequestQueue.svelte`;
  - `GraphNodeCandidateList.svelte`;
  - `GraphNodeCandidateCard.svelte`;
  - `ResumeSnapshotView.svelte`.
- [ ] Add missing API wrappers for:
  - update candidate review item status;
  - candidate action endpoints used by React;
  - detail open request list;
  - approve/reject detail open request;
  - resume snapshot retrieval;
  - final Top 10 retrieval if not already present.
- [ ] Keep provider-rank-first Liepin card behavior visible:
  - card ordering follows provider rank after hard filters;
  - detail recommendation budget is shown separately;
  - opening details remains approval/lease-gated.
- [ ] Add tests proving:
  - duplicate CTS/Liepin candidates render as one final identity row;
  - canonical/latest resume is the displayed resume when backend marks it canonical;
  - canonical CTS + merged Liepin card still uses the Liepin card review item for detail request idempotency;
  - source badges include multiple-source evidence;
  - raw provider payload, cookies, tokens, and protected artifact paths do not render.

## Task 7: Port Settings, Source Connections, And Liepin Safe Route

**Goal:** Restore the missing setup points and keep the Pi-first Liepin boundary explicit.

- [ ] Add `apps/web-svelte/src/routes/(app)/settings/+page.svelte`.
- [ ] Add `apps/web-svelte/src/routes/(app)/settings/sources/+page.svelte`.
- [ ] Add `apps/web-svelte/src/routes/(app)/settings/sources/liepin/+page.svelte`.
- [ ] Add `apps/web-svelte/src/routes/(app)/connections/liepin/[connectionId]/login/+page.svelte`.
- [ ] Add API wrappers for:
  - source connection list;
  - source connection detail;
  - create Liepin connection;
  - any safe status endpoint used by the connection pages.
- [ ] In `pi_agent` mode, do not render iframe login relay, browser relay controls, snapshot controls, raw input controls, or managed-browser handoff instructions.
- [ ] In `pi_agent` mode, do not call the legacy login handoff endpoint. The route is a safe status/explanation route unless a later backend contract exposes an explicitly supported Pi-first safe handoff.
- [ ] If the backend reports a safe connection or session status, render it as source readiness/status only.
- [ ] If interactive login is not supported by the Pi-first path yet, show a clear safe message that the browser session must be prepared outside the Svelte workbench and that the workbench will not request credentials.
- [ ] Preserve settings visual style from React rather than current Svelte dashboard cards.
- [ ] Add e2e tests for source settings and Liepin route with no forbidden endpoint calls.

## Task 8: Remove Or Re-scope Spike-Only Dev Mode UI

**Goal:** Remove the product-facing signs that Svelte is a separate dev console.

- [ ] Delete or stop importing current spike-only components after replacement:
  - `ReadinessPanel.svelte` from primary `/sessions`;
  - `SessionCreatePanel.svelte` if replaced by `CreateSessionForm`;
  - old dashboard-only candidate/source cards if superseded.
- [ ] Keep backend dev diagnostics available for tests and operators, but do not show data-root posture in primary UI.
- [ ] If a small diagnostic surface remains, place it under settings/developer context and only show safe component statuses, never secrets or local paths.
- [ ] Add grep verification that primary Svelte routes do not display:
  - `Svelte 5 Workbench Spike`;
  - `Dev mode BYOK`;
  - local data-root labels;
  - raw artifact paths;
  - implementation-only copy such as "mock", "debug", or "spike" in visible recruiter UI.

## Task 9: Verification Script, Responsive Smoke, And No-Fallback Checks

**Goal:** Make the corrected Svelte migration hard to regress.

- [ ] Update `scripts/verify-dev-workbench.sh` or add `scripts/verify-svelte-workbench-parity.sh`.
- [ ] Keep existing backend semantic tests in the verification flow.
- [ ] Add Svelte parity e2e tests to the verification flow.
- [ ] Keep OpenAPI schema generation and drift checks.
- [ ] Add a React/Svelte visual parity smoke path:
  - serve the React app and Svelte app against the same mocked or seeded backend state;
  - capture matching routes and viewport sizes;
  - write React screenshots to `apps/web-svelte/test-results/parity/react/`;
  - write Svelte screenshots to `apps/web-svelte/test-results/parity/svelte/`;
  - compare screenshots side by side or against a checked React golden-master baseline;
  - fail on major layout, route, copy, density, or visual-system drift.
- [ ] Keep static no-fallback checks scoped to handwritten Svelte code, excluding generated OpenAPI schema:
  - scan `apps/web-svelte/src/routes`;
  - scan `apps/web-svelte/src/lib/components`;
  - scan `apps/web-svelte/src/lib/workbench`;
  - scan `apps/web-svelte/src/lib/api/workbench.ts`;
  - do not scan `apps/web-svelte/src/lib/api/schema.d.ts` for fallback strings because it is generated from backend routes still used by React.
- [ ] For those handwritten-code no-fallback checks, reject:
  - `login-relay`;
  - `login/snapshot`;
  - `login/frame`;
  - `server_managed_browser`;
  - `managed_local`;
  - `external_http`;
  - `dokobot_action`;
  - direct browser fallback language.
- [ ] Keep no-leak checks focused on rendered DOM, screenshots, visible copy, browser console output, and safe serialized payloads. Do not fail solely because `apps/web-svelte/src/lib/api/client.ts` contains the legitimate `X-CSRF-Token` header constant.
- [ ] Add a real-backend smoke path, separate from mocked parity e2e:
  - start the FastAPI workbench against a temporary local workspace when the script owns the process;
  - bootstrap or log in;
  - create a CTS+Liepin explicit-source session;
  - load `/sessions`, `/sessions/{sessionId}`, `/final-top10`, `/settings/sources`, and source connections without schema drift.
- [ ] Make `bun run api:gen` deterministic in verification: either start the local backend on the expected port before running it, or skip/regenerate only when the backend is reachable and report the skipped reason.
- [ ] Add static primary-UI checks for spike/dev-mode copy.
- [ ] Run Playwright screenshots or viewport checks for:
  - login page;
  - setup page;
  - `/sessions`;
  - `/sessions/{sessionId}`;
  - `/settings/sources`;
  - `/settings/sources/liepin`;
  - Liepin connection route;
  - mobile width;
  - tablet width.
- [ ] Required verification commands:

```bash
SEEKTALENT_VERIFY_PYTHON_ONLY=1 ./scripts/verify-dev-workbench.sh
./scripts/verify-dev-workbench.sh
uv run pytest tests/test_workbench_api.py tests/test_workbench_semantic_guardrails.py tests/test_workbench_dual_source_dev_mode.py -q
uv run ruff check src/seektalent_ui/workbench_routes.py src/seektalent_ui/workbench_store.py src/seektalent_ui/final_top_candidates.py tests/test_workbench_api.py tests/test_workbench_semantic_guardrails.py tests/test_workbench_dual_source_dev_mode.py
cd apps/web-svelte && bun run check && bun run lint && bun run test && bun run build && bun run test:e2e
git diff --check
```

`bun run api:gen` must be run by the verification script that starts or verifies the local backend on `127.0.0.1:8012`. Do not list a bare standalone `bun run api:gen` as a required command unless the command block also starts the backend.

## Task 10: Documentation, Cleanup, And Handoff

**Goal:** Make the migration state understandable after implementation.

- [ ] Update `apps/web-svelte/SPIKE_REPORT.md`:
  - mark the previous Svelte UI as a technical spike;
  - state it has been superseded by the React-parity migration;
  - keep useful technical evidence about OpenAPI, Svelte Query, and graph rendering.
- [ ] Update `docs/ui.md` with the Svelte parity route map and the fact that React remains the golden master until final parity signoff.
- [ ] Update `TODOS.md` only for deferred items that are real and not required for this milestone:
  - eventual React app removal after parity and user signoff;
  - visual snapshot baseline automation;
  - bundle/performance optimization after parity;
  - broader source connection UX after Pi-first live path stabilizes.
- [ ] Remove unused Svelte spike components only after `rg` confirms no references.
- [ ] Do not remove backend diagnostics or Python semantic tests that protect multi-source behavior.
- [ ] Produce a final implementation note listing:
  - routes migrated;
  - React parity exceptions, if any;
  - verification commands run;
  - known deferred items.

## Self-Review Checklist Before `fw-review`

- [ ] Every route in the spec exists in Svelte.
- [ ] Protected route children and protected page queries are gated until `/api/auth/me` succeeds.
- [ ] Authenticated event stream uses session stream on session routes and global stream on non-session routes.
- [ ] `/sessions` and `/sessions/{sessionId}` visually match the React workbench structure.
- [ ] Auth guard exists and redirects unauthenticated users.
- [ ] Session rail exists and is not replaced by a dashboard session grid.
- [ ] Topbar exposes Sources/settings and logout.
- [ ] Candidate queue uses identity-level final Top 10.
- [ ] Duplicate CTS/Liepin candidates render as a single identity row when backend final Top 10 says they are merged.
- [ ] Liepin card/detail/CTS badges are visible.
- [ ] Detail recommendations and budgets are visible but detail open remains approval-gated.
- [ ] Svelte primary UI does not show data-root posture.
- [ ] Svelte primary UI does not show spike/dev-mode implementation copy.
- [ ] Svelte source connection pages do not call legacy browser fallback endpoints.
- [ ] No raw provider payloads, cookies, tokens, protected artifact paths, approval secrets, or local data-root paths appear in rendered UI, logs, or e2e snapshots.
- [ ] Python, Svelte, OpenAPI, Playwright, no-fallback, and `git diff --check` verification all pass.

## Deferred Non-Goals

- Removing the React app.
- Building a new visual design system.
- Reopening the runtime multi-source architecture decisions.
- Adding manual detail approval UI beyond the existing safe detail request surfaces.
- Adding A2A.
- Replacing Pi/DokoBot strategy.
- Showing local data-root posture in the primary recruiter UI.
