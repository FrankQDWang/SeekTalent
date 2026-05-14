# User-Visible Behavior Harness Design

## Purpose

SeekTalent now has user-visible product behavior: CLI runs, local setup/login, session creation, source selection, requirement triage, strategy graph, running notes, node details, candidate snapshots, final shortlist, Liepin login, and detail approval. Future refactors must not break these paths silently.

This spec defines the E2E, visual, and Storybook harness that freezes product behavior while still allowing implementation refactors.

## Product Contract

- E2E tests protect full user journeys.
- Storybook isolates business-facing components and states.
- Visual regression protects the workbench layout, graph, right inspector, and source cards.
- Fixture replay drives stable UI states without live CTS, live Liepin, or real candidate PII.
- Live smoke remains a separate manual/operator gate for real provider login and budget behavior.

## Current Code Facts

- `apps/web` already uses Vitest, Testing Library, Playwright visual tests, React Flow, ELK, and TanStack Query.
- `docs/ui.md` documents `bun run test:visual` with Playwright and `odiff-bin`.
- Existing visual gates cover desktop selected-node detail and a tablet node-detail reachability state.
- Storybook has been deferred in prior workbench execution notes.
- Backend tests already cover workbench API/auth/network/security and Liepin provider behavior.

## Decisions

1. Add E2E tests for the product journeys that users now rely on.
2. Add Storybook as component isolation for workbench UI, not as a marketing demo.
3. Build UI fixtures from safe workbench API/event payloads and protected snapshot projections.
4. Keep live provider smoke out of deterministic CI unless an explicit live gate is requested.
5. Add visual baselines only after states are deterministic and fixture-backed.
6. E2E tests must either seed the backend or route API calls to safe fixtures before asserting graph, shortlist, or candidate detail states. A newly created empty session is not allowed to stand in for a completed run.

## Required Journeys

### CLI

- `seektalent --help`
- `seektalent doctor`
- `seektalent inspect --json`
- fixture/mock run path where allowed in source checkout
- missing credentials path
- output artifact path contract

### Local Workbench

- setup first admin;
- login/logout;
- create JD session;
- select CTS and Liepin source cards;
- start requirement triage;
- approve criteria;
- start CTS source run with fixture provider;
- render running notes;
- click graph node and open `节点详情`;
- show final shortlist;
- expand safe candidate snapshot.

### Liepin

- connection source card;
- isolated login route status;
- card-search fixture;
- detail approval request;
- approve/reject detail open;
- budget blocked state;
- login expired or verification required state.

## Storybook Scope

Initial stories:

- app shell layout;
- session rail;
- JD/source panel;
- source card states;
- `StrategyGraph` with CTS-only and CTS+Liepin graphs;
- `NodeDetailPanel` variants;
- running-note stream;
- candidate card;
- detail approval panel;
- setup/login forms;
- empty, loading, blocked, and error states.

Stories must use fixture data that is safe to commit.

## Acceptance Criteria

- CI or local verify can run deterministic E2E and Storybook smoke without live credentials.
- E2E tests use actual accessible labels from the current UI and fixture-seeded states for completed-run assertions.
- Storybook stories cover key component states before the next large UI refactor.
- Visual tests use fixture states and fail on serious layout regressions.
- Docs explain the difference between deterministic E2E, visual regression, Storybook, and live provider smoke.
- No fixture contains real candidate PII, cookies, auth headers, storage state, CDP endpoints, provider tokens, or raw provider payloads.
