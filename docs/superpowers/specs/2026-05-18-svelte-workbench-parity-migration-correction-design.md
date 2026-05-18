# Svelte Workbench Parity Migration Correction Design

## Product Correction

This spec corrects the previous Svelte milestone direction.

The user does not want a new Workbench product surface. The requested work is a technical stack refactor: migrate the existing Workbench UI from React/Vite to Svelte/SvelteKit while preserving the existing Workbench information architecture, route behavior, visual language, and recruiter workflow.

The existing React app under `apps/web` is the golden master for the migration. The current Svelte app under `apps/web-svelte` is useful as a technical spike and backend-integration proof, but its product UI is not accepted as the target shape.

## Investigation Summary

This plan was written after reading the current repo truth, not from memory alone.

Files inspected:

- `apps/web/src/app.tsx`
- `apps/web/src/styles.css`
- `apps/web/src/api.ts`
- `apps/web/src/types.ts`
- `apps/web/src/app.test.tsx`
- `apps/web-svelte/src/routes/**`
- `apps/web-svelte/src/lib/components/**`
- `apps/web-svelte/src/lib/api/**`
- `apps/web-svelte/src/lib/workbench/**`
- `apps/web-svelte/tests/e2e/**`
- `src/seektalent_ui/models.py`
- `src/seektalent_ui/workbench_routes.py`
- `src/seektalent_ui/workbench_store.py`
- `src/seektalent_ui/final_top_candidates.py`
- `scripts/verify-dev-workbench.sh`
- `apps/web-svelte/SPIKE_REPORT.md`
- `TODOS.md`

Observed React golden-master behavior:

- Routes include `/setup`, `/login`, authenticated `/sessions`, `/sessions/$sessionId`, `/settings`, `/settings/sources`, `/settings/sources/liepin`, and `/connections/liepin/$connectionId/login`.
- Protected routes call `/api/auth/me` before loading session data and redirect `401` to `/login`.
- The authenticated shell has a topbar, user display, logout action, source settings link, app-level event stream, collapsible session rail, and `workbench-main` content area.
- The session index route is not a card-heavy landing page. It is the same three-column Workbench shell: JD/create panel, strategy-ready panel, and right rail empty state.
- The session detail route uses the dense three-column `reference-grid`: left JD/source/triage panel, center strategy canvas, right tabs for running notes and node detail.
- The React UI has business-facing Workbench labels: `岗位简报`, `检索渠道`, `检索策略图`, `运行笔记`, `节点详情`, `最终短名单`, `详情审批`.
- The old UI includes source cards, requirement triage editing, central start behavior, SSE event-stream invalidation, final candidate queue, graph-candidate loading, lazy resume snapshots, candidate review actions, detail request actions, settings source rows, Liepin connection status, and source login handoff.
- The visual system is defined in `apps/web/src/styles.css`: IBM Plex/Noto Sans family, warm neutral background `#f6f5f1`, surface colors `#fbfaf6` and `#fffefb`, green accent `#3c5a4a`, 5-8px radii, compact controls, session rail width around `232px`, dense panels, and a full-height grid shell.

Observed Svelte mismatch:

- The Svelte app has `/login`, `/sessions`, and `/sessions/[sessionId]`, but no `/setup`, no authenticated route guard, no settings routes, and no Liepin connection route.
- `/` redirects directly to `/sessions`; unauthenticated users can land on the app shell without a natural login redirect.
- The Svelte app shell says `Svelte 5 Workbench Spike`, which is not product copy.
- The Svelte layout is a new topbar plus card grid, not the React Workbench shell.
- The Svelte session index is a new dev-mode readiness/create/list dashboard, not the React three-column Workbench entry.
- The Svelte detail page is a new page composition, not the React `reference-grid` with left JD/source/triage, center strategy canvas, and right tabs.
- The Svelte CSS uses a different color system, spacing, typography, and component density.
- Svelte tests prove a dev-mode pilot, but not React UI parity.

Backend truth to preserve:

- The backend now has safe dev-mode diagnostics, blocked/partial source status propagation, source badges, identity-level final Top 10 projection, runtime source state, and blank-triage approval rejection.
- `src/seektalent_ui/final_top_candidates.py` groups duplicate candidate review items by runtime identity, provider key hash, and stable candidate field keys, then chooses a canonical row by freshness, evidence level, score, and update time.
- These backend semantics are useful and must be preserved, but the Svelte UI must consume them inside the React-equivalent Workbench shell instead of presenting a separate product experience.

## Goal

Rebuild `apps/web-svelte` into a Svelte implementation of the existing Workbench product UI.

The migration succeeds when a user who knows the current React Workbench can use the Svelte Workbench without learning a new product. Svelte may improve internal maintainability and typed API usage, but it must not change the product surface unless the change is explicitly listed as an exception in this spec.

## Scope

In scope:

- Replace the current Svelte product pages with React-parity Svelte routes and components.
- Keep the Svelte app as a separate package under `apps/web-svelte`.
- Preserve useful Svelte spike foundations:
  - SvelteKit static app setup;
  - OpenAPI-generated types;
  - `openapi-fetch` client;
  - `@tanstack/svelte-query`;
  - `@xyflow/svelte`;
  - migrated `runStory` and graph layout helpers where behavior matches React.
- Port the React Workbench shell, route structure, component semantics, and shared visual system to Svelte.
- Keep backend semantic guardrails from the dev-mode dual-source milestone.
- Use identity-level final Top 10 for final ranking while preserving the React candidate card workflow and actions.
- Ensure CTS and Liepin appear as peer source branches in the same strategy graph and candidate workflow.
- Keep Liepin/Pi/DokoBot execution inside the provider boundary. Svelte must not directly use DokoBot, browser automation, cookies, storage state, or provider raw payloads.

Out of scope:

- No new product redesign.
- No new landing page.
- No broad backend re-architecture.
- No replacement of Runtime source-lane contracts.
- No manual card-review queue beyond preserving the current detail recommendation/request surfaces.
- No data-root readiness panel in the primary recruiter UI. Local data-root posture may remain in backend diagnostics, but the Svelte recruiter surface must not display paths or data-root posture by default.
- No Storybook catalog in this correction slice.
- No A2A protocol.
- No generic provider marketplace.
- No broad deletion of the React app during this slice.

## Parity Rules

### React Golden Master

`apps/web/src/app.tsx` and `apps/web/src/styles.css` are the source of truth for product UI parity.

The Svelte implementation must preserve:

- route names;
- protected route behavior;
- topbar structure;
- session rail behavior;
- three-column Workbench layout;
- source-card position and meaning;
- central strategy start behavior;
- right-rail tabs;
- candidate card workflow;
- settings/source settings flow;
- business-facing labels;
- no raw provider/debug data in visible UI;
- compact visual density.

### Allowed Parity Exceptions

Only these differences are allowed:

- Svelte may use generated OpenAPI types instead of hand-written TypeScript API types.
- Svelte may use `@xyflow/svelte` instead of `@xyflow/react`.
- Svelte may use Svelte Query instead of React Query.
- Final shortlist ranking must come from the backend identity-level final Top 10 endpoint, not a naive local slice of review items.
- In `pi_agent` Liepin mode, Svelte must not recreate legacy managed-browser fallback UI or iframe handoff. It may keep a route at `/connections/liepin/[connectionId]/login`, but the visible behavior must be a safe connection/session status surface unless the backend exposes an explicitly supported safe handoff.
- Candidate ranking is an intentional product correction from the React golden master: final candidate order and canonical display come from backend final Top 10, while React-era candidate actions may be joined from review items.
- Data-root posture must not be shown in the primary UI, even though backend diagnostics may include it.

Any other visual or behavioral difference is a migration bug unless a later spec changes the product.

## Required Route Contract

Svelte must expose these routes:

- `/`
  - Redirects to `/sessions`.
- `/setup`
  - Public admin bootstrap page.
  - Uses the same auth shell as React.
- `/login`
  - Public login page.
  - On successful login, invalidates current user state and navigates to `/sessions`.
- Authenticated layout for all app routes below:
  - Calls `/api/auth/me` before rendering protected content.
  - Redirects `401` to `/login`.
  - Opens one app-level event stream after authentication.
  - Renders topbar, session rail, and `workbench-main`.
- `/sessions`
  - React-parity Workbench empty/session-create screen.
- `/sessions/[sessionId]`
  - React-parity session detail Workbench.
- `/settings`
  - Same source settings page as `/settings/sources`.
- `/settings/sources`
  - Source list with CTS and Liepin rows.
- `/settings/sources/liepin`
  - Liepin connection status and connection creation surface.
- `/connections/liepin/[connectionId]/login`
  - Safe Liepin connection status route.
  - In Pi-first mode, this route must explain that Liepin browser/session work happens inside Pi/DokoBot and must not expose browser internals.

## Visual Contract

The Svelte app must port the React visual system instead of inventing a new one:

- global CSS variables must match the React palette and typography;
- `body` must use the React warm neutral background and fixed-height Workbench behavior on desktop;
- topbar height, session rail width, panel borders, card radii, and dense control sizing must match React;
- `reference-grid` must use the React three-column layout at desktop widths and the same responsive breakpoints;
- Svelte Flow nodes must look like the React Flow nodes, including size, tone colors, line weights, text truncation, and selected/focus states;
- source cards, candidate cards, queue panels, detail request cards, and settings cards must reuse the React class vocabulary or exact visual equivalents;
- Svelte component-scoped styles must be audited and brought into parity too; changing only `apps/web-svelte/src/routes/layout.css` is not sufficient if a component still carries the spike visual language;
- no default page should show `Svelte`, `Spike`, `Dev mode BYOK`, or other implementation-oriented copy unless it is inside a clearly secondary settings/debug affordance.

## Auth And Startup Contract

Unauthenticated users must not see the main Workbench shell.

Required behavior:

- `/sessions` and `/sessions/[sessionId]` redirect to `/login` on `401`.
- `/settings/**` and `/connections/liepin/**` redirect to `/login` on `401`.
- The authenticated Svelte layout must not render protected children until `/api/auth/me` succeeds. Protected page queries must be gated on the same auth-ready state so unauthenticated navigation cannot trigger session, candidate, graph, source, or settings requests before redirecting.
- Login captures CSRF from the auth response and later mutating requests send the CSRF header through the existing Svelte API client.
- Logout clears query state and navigates to `/login`.
- Setup form exists and calls `/api/auth/bootstrap`.
- The normal startup path makes the login position discoverable without a user knowing the `/login` URL.

## Workbench Shell Contract

The Svelte authenticated shell must include:

- Topbar:
  - `Recruiter / 简历智能检索`
  - `project · seektalent-workbench`
  - session count
  - user avatar/display name
  - `Sources`
  - `Log out`
- Session rail:
  - `ST` rail logo
  - collapse/expand button with accessible state
  - session search input
  - loading/error/empty states
  - active session styling
- Workbench main:
  - route content only;
  - no duplicate page-level product header replacing the topbar;
  - no separate card-dashboard layout.
- Event stream:
  - after authentication, the app opens exactly one Workbench event stream;
  - on a session route, use `/api/workbench/sessions/{session_id}/events/stream`;
  - outside a session route, use `/api/workbench/events/stream`;
  - switch streams on route change and close the previous stream;
  - SSE is the primary freshness path for authenticated pages. Short polling must not remain as a second primary source of truth; it is allowed only as a deliberately named degraded fallback with tests.

## Session Index Contract

`/sessions` must preserve the React entry screen:

- left `jd-panel create-panel` with create session form;
- center `strategy-panel` with `ReadyStatePanel`;
- right `right-rail` with job brief empty state and node detail empty state;
- session list lives in the global session rail, not in a page card grid;
- source selection uses the React source-picker visual language.

The default source selection must remain explicit. For the local dual-source milestone, the Svelte create form may default to CTS + Liepin, but it must show both selected controls and must allow deselection. No hidden source default is allowed.

## Session Detail Contract

`/sessions/[sessionId]` must preserve the React Workbench structure:

- left column:
  - collapsible job brief;
  - criteria chips;
  - source cards;
  - requirement triage gate;
- center column:
  - strategy toolbar;
  - strategy canvas;
  - central start overlay;
  - source lane bands when CTS and Liepin are both active;
  - completion toast;
- right column:
  - tabs for `运行笔记` and `节点详情`;
  - running notes from Workbench note events, not raw runtime event names;
  - selected node details;
  - final shortlist candidate queue;
  - graph-candidate list;
  - resume snapshot expansion;
  - Liepin detail approval queue where the selected node requires it.

## Source And Runtime Contract

Svelte must display source state from the Workbench session and runtime source state without inventing new source semantics.

Required behavior:

- CTS and Liepin source cards are peers.
- Source cards do not expose per-source start buttons.
- Start remains a session-level strategy action after requirement criteria are approved.
- Liepin disconnected state keeps the `连接猎聘` or equivalent route action.
- Blocked, partial, failed, running, queued, and completed runtime source states must be business-facing.
- Raw reason codes may drive deterministic labels but must not be the primary copy.
- If Liepin blocks or partially completes, CTS candidates remain visible and final coverage is honest.

## Candidate And Top 10 Contract

The previous user requirement still stands:

- CTS and Liepin are multi-source search lanes.
- Candidates from both sources must be ranked together.
- The final output remains Top 10.
- The same person may appear in both sources with different resume freshness or completeness.
- The UI must show a single canonical candidate row for merged identities, with source evidence preserved.
- Canonical display should prefer the newest or richest safe resume evidence according to the backend final Top 10 projection.

Svelte must not implement identity merge in the browser. It must consume `/api/workbench/sessions/{session_id}/final-top10` for final ranking and use `/api/workbench/sessions/{session_id}/candidates` only for action affordances and legacy review-item details where needed.

The Svelte candidate queue must build a `FinalCandidateViewModel` or equivalent internal shape with these ownership rules:

- `rank`, display fields, `runtimeIdentityId`, `canonicalReviewItemId`, `mergedReviewItemIds`, `sourceEvidence`, `sourceBadges`, `evidenceLevel`, and score fields come from `/final-top10`.
- Candidate action target defaults to `canonicalReviewItemId`.
- If the canonical review item has no safe action affordance, the UI may choose a safe action target from `mergedReviewItemIds` after joining `/candidates`; it must not invent a target id.
- Detail-open action target is separate from the generic candidate action target. It must be `detailActionReviewItemId`, selected from the joined `/candidates` rows as the review item that has Liepin card evidence and no Liepin detail evidence. If no such row exists, the detail request button must not render.
- Status and note display default to the canonical review item when present.
- If merged review items have conflicting statuses or notes, show a small merged-state hint instead of pretending the identity has one unambiguous raw review state.
- Resume snapshot expansion is enabled only when the chosen action review item has a graph candidate id and backend marks it expandable.
- Liepin detail request is shown only for an identity with Liepin card evidence and no Liepin detail evidence.
- Provider/browser open actions are shown only when the backend provider-action endpoint returns a safe action for the chosen review item.

Candidate cards must still support the React-era user actions where backend data allows:

- mark promising;
- reject;
- save note;
- request Liepin detail for card evidence;
- open provider action only when backend returns a safe provider action;
- expand safe resume snapshot through graph-candidate snapshot endpoint.

Candidate queue copy should make multi-source value visible without exposing internals:

- show `Multiple sources` and source badges when available;
- show that duplicate evidence was merged when `mergedReviewItemIds` has more than one item;
- show a concise canonical/latest resume hint when the backend canonical row differs from another merged source;
- show coverage status in product language, for example dual-source complete, Liepin partial, or CTS-only because Liepin is blocked.

## Liepin And Pi Boundary

Svelte must not call DokoBot, Pi, or browser tools directly.

The boundary remains:

- Workbench UI: display, approvals, source connection state, safe actions.
- Backend Workbench store: persistence, job ownership, leases, audit.
- Runtime: source strategy, merge, scoring, finalization.
- Pi Agent: bounded Liepin provider execution.
- DokoBot: browser read/action capability inside Pi only.

Svelte must not render:

- cookies;
- auth headers;
- CSRF header names;
- storage state;
- raw provider payloads;
- protected artifact paths;
- browser debugger URLs;
- DokoBot tool internals;
- Pi prompt text;
- raw exception messages.

In `pi_agent` Liepin mode, these legacy managed-browser endpoints are forbidden in the Svelte handwritten UI and API wrappers:

- `/api/workbench/source-connections/{connection_id}/login`;
- `/api/workbench/source-connections/{connection_id}/login/frame`;
- `/api/workbench/source-connections/{connection_id}/login/snapshot`;
- `/api/workbench/source-connections/{connection_id}/login/input`;
- `/api/workbench/source-connections/{connection_id}/login/complete`.

The generated OpenAPI schema may still contain those backend routes while the React app remains the golden master. Their presence in generated types is not evidence that Svelte may call them.

## Verification Contract

The correction is not complete until tests prove parity and safety.

Required verification:

- Svelte auth route tests:
  - protected route redirects to login on `401`;
  - setup and login forms call auth APIs;
  - logout returns to login.
- Svelte shell tests:
  - topbar, session rail, collapse state, rail search, session count, source settings link.
  - EventSource does not open before auth succeeds, uses the global stream on authenticated non-session routes, uses the session stream on session routes, and closes the old stream on route changes.
- Svelte session index tests:
  - create form appears in `jd-panel`;
  - page does not show spike/dev-mode product copy;
  - session list is in rail, not page grid.
- Svelte session detail tests:
  - three-column Workbench shell;
  - triage generate/edit/approve behavior;
  - central strategy start behavior;
  - source cards;
  - right tabs;
  - running notes;
  - node detail;
  - final Top 10 source badges and merged identity evidence.
- Svelte settings tests:
  - `/settings/sources`;
  - `/settings/sources/liepin`;
  - `/connections/liepin/[connectionId]/login`.
- Visual smoke:
  - desktop and tablet screenshots for login, session index, session detail, and source settings;
  - React and Svelte screenshots for the same route/state must be compared side by side or against an explicit React golden-master baseline;
  - parity artifacts must be written to deterministic folders such as `apps/web-svelte/test-results/parity/react/` and `apps/web-svelte/test-results/parity/svelte/`;
  - the parity check must include `/login`, `/sessions`, `/sessions/[sessionId]`, `/settings/sources`, `/settings/sources/liepin`, `/connections/liepin/[connectionId]/login`, and mobile/tablet responsive states;
  - no overlapping controls;
  - old Workbench palette/classes present;
  - no `Svelte 5 Workbench Spike` copy.
- Backend regression:
  - existing dual-source semantic tests still pass.
- No-leak checks:
  - no visible raw provider strings, cookies, CSRF header names, auth headers, protected artifact paths, raw provider payloads, or browser internals in rendered DOM, screenshots, user-visible copy, or frontend logs;
  - source-code checks must not fail only because the API client contains a legitimate CSRF header constant.
- Static no-fallback check:
  - handwritten Svelte source code must not call legacy browser fallback endpoints or name browser fallback modes as an action path;
  - generated `apps/web-svelte/src/lib/api/schema.d.ts` is excluded from this static fallback scan because it mirrors backend routes used by the React golden master.

## Acceptance Criteria

1. `/sessions` redirects unauthenticated users to `/login`.
2. `/setup` exists and uses the old auth-shell visual language.
3. `/login` exists and is reachable from normal unauthenticated navigation.
4. Authenticated Svelte routes render the old Workbench topbar, session rail, and `workbench-main`.
5. `/sessions` is the old Workbench create/ready/empty route, not a new dashboard.
6. `/sessions/[sessionId]` uses the old three-column Workbench layout.
7. The Svelte UI does not show `Svelte 5 Workbench Spike`, `Dev mode BYOK`, data-root posture, or implementation-first copy in the primary recruiter surface.
8. Source cards, triage gate, strategy canvas, right tabs, running notes, candidate cards, detail requests, settings pages, and Liepin connection route are present in Svelte.
9. The final candidate queue uses identity-level final Top 10 and a documented final-candidate view model; it shows merged duplicate evidence and canonical/latest resume hints without browser-side identity merge.
10. CTS and Liepin can be selected and displayed as peer sources in the same Workbench flow.
11. Liepin/Pi/DokoBot boundaries remain backend/provider-owned; Svelte never directly uses DokoBot, browser automation, or legacy managed-browser login relay endpoints.
12. Existing Python backend semantic verification still passes.
13. Svelte check, lint, unit tests, build, e2e, React/Svelte visual parity smoke, no-leak, and no-fallback checks pass.
