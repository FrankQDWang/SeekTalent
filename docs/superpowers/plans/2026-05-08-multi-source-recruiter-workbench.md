# Multi-Source Recruiter Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` for parallelizable slices or `superpowers:executing-plans` for single-thread execution. Track every checkbox as work progresses.

**Goal:** Replace the local one-run web UI with a source-agnostic internal recruiter workbench that supports scoped users, sessions, CTS + Liepin source runs, FastAPI/SSE streaming, and a TanStack UI shaped like the supplied recruiter-agent HTML plus a collapsible session rail. The first slice must also expose the accepted CEO-review workflow levers: requirement triage, manual strong-profile seeds, Liepin detail-open approval controls, and recruiter-time-saved metrics.

**Architecture:** Python owns auth, tenant/workspace scope, workbench sessions, source-run orchestration, provider context, SSE events, and result aggregation. The existing `WorkflowRuntime` remains the execution core. CTS and Liepin are source adapters under one `WorkbenchSession`. V1 uses a SQLite-backed local SourceRun job runner so long runs are durable jobs instead of long HTTP requests. The Liepin Bun worker remains internal-only and only performs managed browser/page work.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, `sse-starlette`, Pydantic, SQLite, existing ArtifactStore/CorpusStore/FlywheelStore, Bun + TypeScript + Playwright for the internal Liepin worker, Vite + TypeScript + TanStack Router/Query/Table/Form/Virtual for the frontend, Pretext only for text-heavy UI/report surfaces when needed, pytest, FastAPI TestClient or httpx ASGITransport, Bun test, Vitest, jsdom, Playwright browser tests, and `odiff-bin` for local visual smoke comparison.

## Scope Notes

This plan starts the new web product surface. It does not build public SaaS deployment, multi-Liepin-account rotation, browser extension support, user-side runtime installation, personalized learning tips, or full ATS/CRM.

The existing `docs/superpowers/plans/2026-05-07-liepin-connector-verified-loop.md` remains the provider-level Liepin plan. This plan sits above it and connects CTS + Liepin into the real recruiter workbench.

CEO review scope locked for this phase:

- Requirement Triage Gate before source runs start.
- Strong Profile Seed Lane for manually pasted strong profile summaries.
- Detail-Open Approval Queue for Liepin, defaulting to human confirmation.
- Configurable bypass mode for detail opens, constrained by ledger, daily budget, per-connection lease, pacing, and risk-control state.
- Recruiter-Time-Saved Metrics based on real source-run, candidate, and ledger state.

Deferred: post-run learning capsules and personalized education tips.

## Vertical Milestone Gates

Execute the plan as vertical slices. Do not spend a long phase building hidden infrastructure before a recruiter can open a useful workbench path.

Milestone order:

- M0: bootstrap admin, login/logout, loopback/LAN startup guard, session rail shell, settings entry, and route guard.
- M1: create a JD session, generate/edit/approve requirement triage, run the existing CTS runtime path from the workbench, and stream real progress with SSE.
- M2: show real CTS candidates in the review queue with source badges, notes/actions persistence, and directional recruiter-time-saved metrics.
- M3: show Liepin connection/login state in the source card and implement the isolated server-side browser login relay for LAN users.
- M4: run Liepin card-level search and persist candidate evidence without opening detail pages.
- M5: add Liepin detail-open approval queue, configurable bypass mode, ledger, per-connection lease, human-paced sequencing, and known-detail action handling.
- M6: complete visual parity, LAN/manual QA, rollback smoke test, redaction/security audit verification, and docs.

Gate rules:

- Each milestone must include backend tests, frontend tests where UI changed, and a short manual/browser verification note before moving on.
- The numeric tasks below are a reference checklist, not the execution order. Implement by milestone order and pull only the necessary checklist items into each milestone.
- M1 is the first major execution target. Shared store/auth/SSE work should be just enough to make the CTS-backed workbench path real.
- M3-M5 must reuse the existing Liepin connector/provider work instead of starting a new adapter.
- Do not mark a later Liepin gate complete if earlier CTS-backed workbench gates are still only mocked.
- If a milestone is intentionally skipped or narrowed, update this section before coding the later milestone.

## Binding Execution Order

Follow this order even when later checklist tasks appear earlier in this file:

```text
Task 0 baseline
  -> M0 auth/shell slice
  -> M1 CTS source-run slice
  -> M2 CTS candidate-review slice
  -> M3 Liepin connection/login slice
  -> M4 Liepin card-evidence slice
  -> M5 Liepin detail-approval/ledger slice
  -> M6 visual parity, LAN QA, rollback, docs
```

The architecture-hardening items in Task 0.5 are invariants to apply slice-by-slice. Do not implement every table, endpoint, security helper, and background runner in one hidden infrastructure phase before M1.

Milestone implementation map:

- M0 pulls the minimum from Task 0.5, Task 1, Task 2, Task 2A-2F, Task 5, Task 6, and Task 7 needed for login, route guard, session rail shell, settings entry, loopback/LAN guard, CSRF/CORS/Host checks, and rollback feature gate.
- M1 adds the minimum from Task 1, Task 2, Task 2G, Task 3, Task 3A, Task 5, and Task 7 needed to create a JD session, approve requirement triage, enqueue a CTS source run, stream real SSE progress, and recover after refresh.
- M2 adds candidate evidence/review queue, notes/actions, source badges, CTS result mapping, corpus refs, memory firewall checks, and time-saved metrics from Task 1, Task 2H, Task 3, Task 5, and Task 7.
- M3 adds Liepin connection/settings/login state from Task 2E, Task 3, Task 5, and Task 6. Remote isolated server-side browser login relay is required for M3 completion. Mac-host-local login may exist as a development fallback, but it does not satisfy the M3 gate.
- M4 adds Liepin card-level source run and candidate evidence from Task 3, Task 4 mapping work, Task 5, and provider tests, with no detail opening.
- M5 adds detail-open approval queue, bypass mode, workbench-owned detail ledger, per-connection lease, provider child-attempt refs, safe provider actions, and budget UI from Task 4 and Task 5.
- M6 completes visual parity against the reference HTML, full LAN/manual QA, rollback proof, security/redaction audit, docs, and final verification from Task 7.

Each milestone must end with:

- focused backend tests for the new slice;
- frontend tests for changed routes/components;
- browser/manual evidence for the user-visible path;
- an update to the milestone execution log;
- no untracked placeholder that claims real behavior for a later milestone.

## Hard Constraints

- Do not build a Liepin-only UI. All workbench models, routes, and components must support multiple sources.
- Do not require business users to install Node.js, Bun, Playwright, a browser extension, or a local daemon.
- Do not bypass `WorkflowRuntime` for UI runs.
- Do not expose the Liepin worker, CDP endpoints, Playwright websocket URLs, storage state, cookies, auth headers, raw provider payloads, or auth-bearing provider URLs to the frontend.
- Do not use Playwright `APIRequestContext`, `page.request`, `browserContext.request`, or equivalent direct authenticated HTTP calls to Liepin.
- Liepin detail opening remains budgeted, sequential, and human-paced.
- Source cards must be backed by real source-run state and event rows, not frontend-only mock state.
- Stream progress through FastAPI + `sse-starlette`; do not reintroduce hand-rolled stdlib streaming.
- Do not execute long source runs directly inside request handlers.
- Do not use FastAPI `BackgroundTasks` as the primary source-run executor.
- V1 source runs execute through a SQLite-backed local job runner with leases, heartbeats, idempotency keys, cooperative pause/cancel, and startup recovery.
- Keep the job runner contract cloud-migratable. Future Celery/RQ/Dramatiq/Temporal-style execution may replace the local runner, but source-run state, connection serialization, detail budget, approval, candidate evidence, and audit records remain SeekTalent-owned state.
- Remote LAN Liepin login must bind to the Mac server's managed browser context through an isolated login relay. Mac-host-local login is a development fallback only and cannot satisfy M3 completion.
- Cookie-auth mutating routes require CSRF protection. SSE routes are read-only and side-effect free.
- LAN serving is opt-in. Default startup binds to loopback only, unknown `Host` headers are rejected, and credentialed CORS is allowed only for configured origins.
- SQLite workbench storage must use WAL, foreign keys, bounded busy timeout, short transactions, and no long read transaction inside SSE loops.
- Source-run completion and session completion are separate events and lifecycle transitions.
- Secret redaction must be centralized and shared by API, SSE, events, artifacts, logs, worker mapping, and exception serialization.
- Requirement triage state is user-visible and editable before source runs spend search effort.
- Strong profile seeds are manual user input in V1; do not add automatic ATS/CRM/provider import.
- Liepin detail opening defaults to human confirmation. Bypass mode may auto-approve eligible requests, but cannot bypass compliance, budget, ledger, lease, pacing, or risk-control checks.
- Time-saved metrics are directional product metrics, not compensation or performance accounting.
- Add a local workbench feature gate and rollback procedure before making the new workbench the default LAN UI.
- Back up the SQLite workbench database before schema changes that cannot be trivially recreated from source artifacts.
- Every async/provider/database failure path must map to durable state, an audit/event row where useful, and a user-visible recovery message.
- Treat JD text, notes, strong profile seeds, provider snippets, candidate notes, and LLM output as untrusted data. Validate, escape, sanitize, and schema-check them before persistence, rendering, artifact writing, or runtime decisions.
- Local auth must be hardened for multi-user LAN use: no permanent shared default account, hashed passwords, server-side sessions, expiry, logout/revocation, session rotation, failed-login throttling, and role checks for sensitive mutations.
- Workbench SQLite, artifacts, backups, and managed browser profiles must live under a restricted local data root outside the repo and outside common sync folders unless explicitly overridden with a warning.
- Original resume/profile material may be retained for benchmark, but only through the existing protected corpus raw-payload boundary with scoped access, audit logging, and future benchmark manifests owned outside workbench session state.
- Candidate raw data must not enter memory, SSE, ordinary API responses, logs, diagnostics, support exports, or ordinary artifacts.
- Sensitive operations must write redacted security audit events with actor, scope, target, action, result, and reason.

## Task 0: Reference And Baseline Audit

- [ ] **Step 1: Reconfirm current runtime and UI entrypoints**

  Files to inspect:

  - `src/seektalent/api.py`
  - `src/seektalent/runtime/orchestrator.py`
  - `src/seektalent/runtime/retrieval_runtime.py`
  - `src/seektalent/core/retrieval/provider_contract.py`
  - `src/seektalent_ui/server.py`
  - `src/seektalent_ui/models.py`
  - `apps/web/src/app.ts`
  - `docs/ui.md`

  Confirm in notes that CTS CLI/UI runs still flow through `WorkflowRuntime`, and identify where provider context must be injected for Liepin.

- [ ] **Step 1A: Lock FastAPI module boundaries before adding routes**

  `src/seektalent_ui/server.py` already contains the old run API, Liepin scoped API, SSE handlers, app creation, and stdlib compatibility server. Do not add the new workbench as another large inline block in that file.

  New workbench routes must use FastAPI `APIRouter` modules. `server.py` should remain the app assembly and legacy compatibility entrypoint:

  ```text
  server.py
    -> create_app(), app setup, legacy /api/runs compatibility, include_router(...)

  auth.py
    -> current-user dependency, session lookup, CSRF checks, role/workspace scope

  workbench_routes.py
    -> session, requirement triage, source-run, candidates, metrics routes

  event_routes.py
    -> durable event recovery and SSE stream routes

  source_connection_routes.py
    -> source connection reads and explicit login/refresh/revoke/disconnect actions

  detail_routes.py
    -> detail-open approval/rejection and safe provider action routes
  ```

  Keep names small and literal; split further only when a file becomes hard to reason about. Tests should import the app factory and route modules without depending on private route-local functions inside `server.py`.

- [ ] **Step 2: Preserve Pinpin notes as reference-only evidence**

  Review `docs/references/pinpin-liepin-mapping-notes.md`.

  Confirm implementation rules:

  - use Pinpin endpoint/field/state knowledge for fixtures and mapping;
  - do not copy source code;
  - do not implement Pinpin-style direct cookie/header replay.

- [ ] **Step 3: Run baseline tests**

  Commands:

  ```bash
  uv run pytest tests/test_ui_api.py tests/test_ui_mapper.py tests/test_cts_provider_adapter.py tests/test_retrieval_service.py tests/test_liepin_provider_adapter.py tests/test_liepin_verified_loop.py
  cd apps/web && bun run test
  cd apps/liepin-worker && bun run test
  ```

  Expected: existing tests pass before replacing the UI surface.

- [ ] **Step 4: Create the milestone execution log**

  Before coding the first slice, create or update a short execution note under `docs/superpowers/` that tracks M0-M6 status, test evidence, manual/browser verification, and known deferrals.

  Rules:

  - M0-M2 should be the first implementation target because CTS already works through the CLI/runtime path;
  - do not start broad Liepin detail work before M1 proves the web workbench can run a real CTS session;
  - record any temporary mock or placeholder explicitly and remove it before the milestone that claims the corresponding real behavior.

## Task 0.5: Architecture Hardening Invariants

Apply these invariants inside the M0-M6 slices. Do not complete Task 0.5 as a standalone horizontal backend phase before M1.

- [ ] **Step 1: Lock the Liepin login handoff mode**

  Update the implementation notes before coding to choose one mode:

  - primary V1 mode: remote isolated server-side managed-browser login page;
  - development fallback mode: Mac-host-local Liepin login only. This fallback cannot count as M3 completion.

  Primary V1 mode requirements:

  - the Liepin browser context lives on the Mac server;
  - the remote LAN user's browser receives only safe rendered frames, input-event relay, and status events;
  - the login route never exposes cookies, storage state, auth headers, CDP URLs, Playwright websocket URLs, worker URLs, or arbitrary browser automation APIs;
  - the route can return to the originating workbench session;
  - tests prove a remote LAN login binds the server-side `source_connection`.

  The M3 milestone is not complete until this relay is implemented and tested. Before M3, the UI may display a temporary Mac-host-local development fallback, but it must clearly mark remote LAN binding as unavailable.

- [ ] **Step 2: Define the full minimal schema**

  `src/seektalent_ui/workbench_store.py` must own or coordinate tables for:

  - `tenants`
  - `workspaces`
  - `users`
  - `user_sessions`
  - `login_attempts`
  - `workspace_memberships`
  - `sessions`
  - `session_requirement_triage`
  - `source_runs`
  - `source_run_jobs`
  - `source_run_policies`
  - `source_connections`
  - `connection_status_events`
  - `compliance_gates`
  - `detail_open_requests`
  - `detail_open_ledger`
  - `session_events`
  - `security_audit_events`
  - `candidate_evidence`
  - `candidate_review_items`
  - `candidate_actions`
  - `candidate_notes`
  - `strong_profile_seeds`
  - `artifact_refs`
  - `memory_rows`
  - `external_write_intents`
  - `recruiter_time_metrics`

  If an existing Liepin provider table already owns a subset such as detail ledger or compliance gates, the workbench store may reference it by stable IDs, but the workbench plan must document the ownership and foreign-key-like boundary. Do not duplicate budget state in two independent ledgers.

- [ ] **Step 2A: Define indexes and pagination rules**

  Add explicit SQLite indexes for the high-frequency UI and worker paths. At minimum:

  - `sessions(tenant_id, workspace_id, updated_at DESC, session_id)` for the session rail;
  - `sessions(tenant_id, workspace_id, user_id, updated_at DESC, session_id)` for user-scoped session rail queries;
  - `source_runs(tenant_id, workspace_id, session_id, source_kind, status)` for source cards and session recovery;
  - `source_runs(tenant_id, workspace_id, status, updated_at)` for startup reconciliation;
  - `source_run_jobs(status, lease_expires_at, job_id)` for claiming queued/stale jobs;
  - `source_run_jobs(source_run_id, status)` for pause/cancel/resume lookup;
  - `session_events(tenant_id, workspace_id, global_seq)` for app-level SSE and `after_seq` recovery;
  - `session_events(tenant_id, workspace_id, session_id, session_seq)` for session timeline reads;
  - `candidate_evidence(tenant_id, workspace_id, session_id, source_run_id, evidence_level)` for source drilldown;
  - `candidate_review_items(tenant_id, workspace_id, session_id, aggregate_score DESC, review_item_id)` for the review queue;
  - `candidate_actions(tenant_id, workspace_id, session_id, review_item_id, created_at DESC)` for candidate history;
  - `candidate_notes(tenant_id, workspace_id, session_id, review_item_id, created_at DESC)` for note history;
  - `detail_open_requests(tenant_id, workspace_id, status, created_at, request_id)` for the approval queue;
  - `detail_open_ledger(connection_id, budget_day, status)` for budget counters and lease checks;
  - unique partial-equivalent enforcement for one active detail-open lease per `connection_id` using SQLite-supported constraints or a transactional guard with tests;
  - `external_write_intents(status, updated_at, intent_id)` for reconciliation.

  Durable list endpoints must use keyset pagination or bounded `after_seq`/cursor style reads. Avoid `OFFSET` pagination for `session_events`, candidate queues, and candidate history. Every list endpoint must have an explicit default and maximum page size.

- [ ] **Step 3: Define source-run materialized state**

  `source_runs` must include materialized source-card fields:

  - `status`
  - `auth_state`
  - `health_state`
  - `cards_scanned_count`
  - `unique_candidates_count`
  - `shortlisted_count`
  - `detail_open_used_count`
  - `detail_open_skipped_count`
  - `detail_open_blocked_count`
  - `last_event_seq`
  - `last_meaningful_event`
  - `warning_code`
  - `warning_message`
  - `started_at`
  - `completed_at`
  - `failed_at`
  - `blocked_reason`

  Allowed source-run statuses:

  - `queued`
  - `running`
  - `paused`
  - `paused_recoverable`
  - `blocked`
  - `completed`
  - `failed`
  - `cancelled`
  - `orphaned`

- [ ] **Step 4: Define session lifecycle separately**

  Allowed session statuses:

  - `draft`
  - `running`
  - `partially_completed`
  - `completed`
  - `failed`
  - `cancelled`

  A source run may complete without completing the session. `session_completed` is emitted only by the aggregator after all enabled source runs are terminal or blocked and session-level final artifacts are written.

- [ ] **Step 4A: Define state-machine diagrams and transition guards**

  Add ASCII lifecycle diagrams to the spec and keep matching transition tables in the implementation tests.

  Required state machines:

  ```text
  sessions:
  draft -> running -> partially_completed -> completed
  running -> failed
  draft/running/partially_completed -> cancelled

  source_runs:
  queued -> running -> completed | blocked | failed | cancelled | paused | orphaned
  blocked -> queued, only through explicit retry after the blocking condition is fixed
  paused -> queued, only through resume
  orphaned -> paused_recoverable, only through startup reconciliation
  paused_recoverable -> queued | cancelled

  source_run_jobs:
  queued -> leased -> running -> completed | failed | blocked
  running -> pausing -> paused -> queued
  running -> cancel_requested -> cancelling -> cancelled
  leased/running with stale heartbeat -> orphaned
  orphaned -> queued | failed | blocked, only through reconciliation or repair

  detail_open_requests:
  pending -> approved | rejected | bypassed | blocked
  approved/bypassed -> ledger.planned, only after backend lease acquisition

  detail_open_ledger:
  planned -> leased -> opened | failed | maybe_used
  planned -> skipped | blocked

  external_write_intents:
  pending -> in_progress -> resolved
  pending/in_progress -> failed
  failed -> pending | tombstoned
  ```

  Store functions must reject illegal transitions, especially terminal-state mutation, rejected detail requests acquiring leases, cancel/resume after completion, and duplicate external writes after `resolved` or `tombstoned`.

- [ ] **Step 5: Define durable event schema**

  `session_events` must include:

  - global `event_id`
  - global monotonic `global_seq`
  - per-session `session_seq`
  - `event_name`
  - `tenant_id`
  - `workspace_id`
  - `user_id`
  - `session_id`
  - nullable `source_run_id`
  - `schema_version`
  - `payload_redacted_json`
  - `created_at`

  Events are append-only audit rows. Corrections are emitted as later events.

- [ ] **Step 6: Define SQLite concurrency settings**

  Store initialization must execute:

  ```sql
  PRAGMA journal_mode=WAL;
  PRAGMA busy_timeout=5000;
  PRAGMA foreign_keys=ON;
  ```

  Rules:

  - write transactions are short;
  - source-run job claim, heartbeat, pause/cancel flag update, and lease release use short transactions;
  - source-run state update plus event append are atomic where required;
  - no SSE loop sleeps while holding a read transaction;
  - concurrent source-run event writes use a single writer queue or bounded retry policy;
  - server startup reconciles orphaned `running` source runs and expired detail-open leases.

- [ ] **Step 6A: Define cross-store outbox and reconciliation**

  The workbench store coordinates UI-visible state, but it must not claim a single transaction covers separate `CorpusStore`, `LiepinStore`, `ArtifactStore`, or file-system writes.

  Requirements:

  - add `external_write_intents` as the workbench outbox for corpus ingestion, artifact writes, provider child-attempt rows, and future corpus/benchmark export materialization;
  - write materialized state, redacted session event, scoped evidence refs, and external-write intent in one short workbench transaction;
  - give every external write an idempotency key, target kind, target scope, status, attempt count, last error code/message, created/updated timestamps, and optional resolved external ref;
  - perform CorpusStore, LiepinStore, and artifact writes after the workbench transaction using the idempotency key;
  - reconcile `pending` and `failed` intents on startup and through an explicit repair function;
  - if an external write succeeds but the final workbench ref update fails, attach the discovered external ref or tombstone the intent without duplicating raw payloads, provider attempts, or detail budget consumption;
  - emit redacted reconciliation events instead of mutating past session events.

- [ ] **Step 7: Define cookie, CSRF, CORS, LAN exposure, and stream rules**

  Requirements:

  - cookie-auth mutating routes require signed double-submit CSRF or equivalent;
  - SSE routes are read-only `GET` routes with no side effects;
  - local dev across Vite and FastAPI ports has explicit CORS credentials tests;
  - default server binding is loopback-only unless LAN mode is explicitly enabled;
  - LAN mode requires an explicit flag or config value and prints the bind address, LAN URL, allowed hosts, allowed origins, and HTTP/HTTPS status at startup;
  - unknown `Host` headers are rejected before workbench routing;
  - credentialed CORS is allowed only for configured frontend origins;
  - startup warns or fails closed when the bind address appears public, VPN-only, hotspot/shared, or outside the intended local network;
  - HTTP LAN mode does not claim `Secure` cookies are active;
  - HTTPS LAN mode must document certificate and trust setup;
  - stream tokens never appear in URLs, JSON bodies, logs, artifacts, or diagnostics;
  - API supports `GET /api/workbench/events?after_seq=...` for refresh recovery in addition to `Last-Event-ID`.

- [ ] **Step 8: Define centralized redaction**

  Add a shared redaction contract before implementing routes:

  - API response serialization uses it;
  - SSE event payloads use it;
  - session event persistence uses it;
  - artifact writers use it;
  - log and exception serializers use it;
  - Liepin worker contract mapping uses it.

  Required forbidden keys and values include:

  - `cookie`
  - `Cookie`
  - `Authorization`
  - `Bearer`
  - `storageState`
  - `localStorage`
  - `sessionStorage`
  - `cdp`
  - `wsEndpoint`
  - `webSocketDebuggerUrl`
  - `playwright`
  - `browserContext`
  - `authHeader`
  - `set-cookie`
  - `rawPayload`

- [ ] **Step 9: Define accepted CEO-scope entities and policies**

  Before implementation, write the store/API notes for:

  - `session_requirement_triage`: must-haves, nice-to-haves, synonyms, seniority filters, exclusions, generated query hints, approval state, and user edits;
  - `strong_profile_seeds`: user-pasted profile summaries, extracted shared attributes, active/inactive state, and session ownership;
  - `source_run_policies`: source controls such as Liepin detail-open mode, with `human_confirm` as the default and `bypass_confirm` as an explicit setting;
  - `detail_open_requests`: pending/approved/rejected/bypassed/blocked/expired approval queue rows before any ledger lease is acquired;
  - `recruiter_time_metrics`: cards reviewed, detail opens skipped, details opened, candidates accepted/rejected, and estimated minutes saved.

  The bypass path must be documented as "skip per-candidate confirmation only." It does not skip compliance, daily budget, ledger lease, sequential pacing, connection checks, or risk-control pauses.

- [ ] **Step 10: Define rollout gate and rollback procedure**

  Add implementation notes before replacing the user-facing UI:

  - local setting/env flag for enabling the new workbench surface;
  - behavior when the flag is disabled, such as a clear maintenance/fallback screen rather than a broken route;
  - SQLite schema version recording for workbench tables;
  - backup path before first workbench schema migration or before destructive schema changes;
  - rollback steps: stop server, restore prior app code, restore SQLite backup when needed, restart, run smoke test;
  - smoke test after rollback: login, list sessions, read source cards, read candidate rows, and verify no detail-open ledger ambiguity.

  Do not keep the old one-run UI as a long-term parallel product. The rollback gate is operational safety for the first internal rollout.

- [ ] **Step 11: Define the error and rescue map**

  Add a single implementation note or table before coding with these mappings:

  | Codepath | Failure examples | Rescue action | User sees |
  |---|---|---|---|
  | Auth/scope | missing cookie, expired session, wrong workspace | no mutation; return 401/403/404 according to scope | login, forbidden, or not found |
  | CSRF mutation | missing or stale CSRF token | no mutation; return 403 | refresh and retry |
  | LAN exposure/config | unknown `Host`, unapproved `Origin`, accidental public bind, unsafe interface | reject request or fail startup before serving workbench traffic | startup/config warning or forbidden request |
  | SQLite write | locked DB, constraint failure, event append failure | bounded retry; keep state/event atomic; rollback partial transaction | temporary storage error or conflict |
  | SSE stream | disconnect, malformed `Last-Event-ID` | no mutation; reconnect from durable `after_seq` | reconnecting or refresh state |
  | SourceRun job runner | stale lease, duplicate claim, worker crash, cancel at checkpoint | expire lease, keep single owner, mark recoverable/cancelled/failed | source-card recover/cancel/failure state |
  | Requirement triage | empty JD, malformed structured output, timeout | validation error or bounded structured-output retry; keep editable draft | triage warning/edit prompt |
  | Strong profile seed | empty, too long, extraction failure | reject input or mark seed extraction failed | seed-level warning |
  | Runtime bridge | runtime exception or provider failure | mark source run `failed`/`blocked`; session can remain `partially_completed` | source-card error and next action |
  | Liepin connection | login expired, verification required, permission missing | mark connection/source run `blocked`; persist connection status event | login/verification call-to-action |
  | Detail approval/ledger | budget exhausted, active lease, rejection | block/reject request before ledger spend; never silently free ambiguous budget | queue blocked/rejected state |
  | Worker after dispatch | crash or lost response after possible detail open | ledger `maybe_used`; no silent refund | budget ambiguity warning |
  | Candidate merge | low confidence, conflicting evidence | keep separate rows or record manual merge/split action | visible evidence ambiguity |
  | Startup/rollback | old running run, expired lease, restored DB | reconcile to `orphaned`, `paused_recoverable`, or expired lease | recover/resume message |

  Rules:

  - no catch-all handler may swallow source-run, ledger, event persistence, or login-handoff failures;
  - rescued errors must either retry with a bounded policy, transition durable state, or re-raise with scoped context;
  - user-visible messages must not contain secrets, raw provider payloads, CDP URLs, worker URLs, cookies, or auth-bearing URLs;
  - SSE routes never repair or mutate state; recovery comes from durable endpoints and startup reconciliation.

- [ ] **Step 12: Define input, rendering, and prompt-injection security**

  Add one security contract before coding:

  - field limits and empty-state rules for JD text, notes, requirement triage edits, strong profile seeds, candidate notes, manual search/query fields, settings, and detail policy changes;
  - control-character and dangerous URL scheme rejection where text can become a link or action;
  - escaped rendering for user text, provider snippets, model explanations, event payloads, and candidate summaries;
  - sanitizer allowlist for any Markdown/Pretext-generated report surface;
  - enum and authorization validation before source policy or detail-open mode changes;
  - prompt-injection rule: user/provider/model text is data, not executable instruction;
  - model output cannot approve detail opens, enable bypass, mutate source settings, expose raw provider data, or call provider/browser actions;
  - structured LLM output must validate against schemas before persistence or display, with only the bounded structured-output retry allowed by `AGENTS.md`.

- [ ] **Step 13: Define local auth and session security**

  Add the V1 local auth contract before coding:

  - first admin user is created through an explicit local bootstrap setup path;
  - no permanent shared default account may pass internal rollout;
  - store passwords only as modern salted password hashes;
  - store server-side sessions in `user_sessions` with expiry, revocation/logout state, issued/rotated timestamps, and last activity;
  - login rotates session identifiers and sets scoped HttpOnly cookies;
  - logout revokes the server-side session;
  - failed logins are recorded in `login_attempts` and throttled or temporarily locked per account/IP boundary suitable for LAN use;
  - disabled users cannot authenticate or keep using old sessions;
  - every request resolves tenant, workspace, user, role, and membership before reading scoped resources;
  - source connection changes, detail-open policy changes, approval/bypass decisions, and user administration require an authorized role;
  - auth/session logs and events never include passwords, password hashes, session tokens, cookies, or source credentials.

- [ ] **Step 14: Define local data-at-rest security**

  Add the V1 filesystem/data-root contract before coding:

  - configure one local data root for workbench SQLite, artifacts, backups, and managed browser profiles;
  - default data root must be outside the git repo and outside common sync folders such as iCloud Drive, Dropbox, Google Drive, and OneDrive;
  - data root, SQLite directory, artifact directory, backup directory, and provider browser profile directory are owner-only;
  - SQLite database, WAL/SHM files, artifacts, backups, and browser profile files are not world-readable or world-writable;
  - startup checks warn or fail closed when required paths are symlinks, world-readable, world-writable, inside the repo, or inside a known sync folder;
  - backups inherit restrictive permissions and have a retention policy;
  - backup/restore excludes managed browser profiles and raw provider session state from ordinary support bundles;
  - managed browser profiles are treated as credential-bearing and never copied into artifacts, logs, or diagnostics;
  - docs recommend FileVault or equivalent full-disk protection for the Mac host; do not invent custom app-level crypto in V1.

- [ ] **Step 15: Define corpus-backed raw data, memory firewall, and benchmark governance**

  Add the V1 candidate data contract before coding:

  - original resume/profile material is retained through the existing `CorpusStore`/`ArtifactStore` corpus raw-payload boundary under the restricted data root;
  - do not create a second independent workbench raw-vault table or ad hoc raw-file store for provider-returned resumes;
  - `candidate_evidence` stores scoped corpus references and metadata for raw resumes, raw profile text, raw page snapshots, and provider payloads allowed by the provider boundary: `resume_doc_id`, `observation_id`, `subject_id`, raw `artifact_ref_id`, source kind, evidence level, provider key hash, schema version, collection time, compliance-gate state, redaction state, allowed uses, and creation actor;
  - ordinary candidate APIs, SSE events, session events, logs, diagnostics, support exports, and ordinary artifacts never inline raw resume/profile material;
  - `candidate_evidence` and `candidate_review_items` may reference raw artifacts by ID but cannot embed raw content;
  - raw corpus artifact reads require authorized role, explicit purpose such as `benchmark`, `debugging`, or `manual_review`, and a security audit event;
  - preserve enough source-run/corpus provenance for future TREC-pooling/static benchmark construction: `jd_doc_id`, session/source-run/provider, query instance/fingerprint, provider request/page/rank, `resume_doc_id`, `observation_id`, evidence level, detail ledger state, and human actions;
  - do not define new workbench-owned benchmark metrics, qrels, pool versions, or benchmark manifests; those belong to the corpus/benchmark boundary and future static benchmark implementation;
  - benchmark fixtures must not be copied into the git repo unless synthetic or explicitly redacted;
  - `memory_rows` may store recruiter preferences, search strategy, market/role learning, workflow habits, and user-confirmed high-level lessons;
  - `memory_rows` must not store raw resumes, full candidate profiles, contact information, identity-rich candidate text, sensitive candidate evaluations, rejection reasons, or raw provider payloads by default;
  - candidate-derived memory writes require a bounded redaction/abstraction step and user-confirmed or policy-approved memory category;
  - model output and prompt-injection text cannot authorize candidate PII or raw profile material entering memory.

- [ ] **Step 16: Define security audit trail**

  Add a lightweight audit contract before coding:

  - add `security_audit_events` as a separate audit table from `session_events`;
  - audit bootstrap admin creation, login/logout, failed-login lockout, disabled-user rejection, user admin, role changes, workspace membership changes, source connection changes, Liepin login status changes, compliance gate changes, detail policy changes, manual approvals/rejections/bypass decisions, data-root override, startup permission warnings, backup/restore/support export, feature-gate changes, and sensitive candidate merge/split decisions;
  - audit rows include actor, actor role, tenant/workspace/session/source scope where relevant, target type/id, action, result, reason code, safe request/IP/device metadata where useful, redacted metadata, and created time;
  - audit rows never include passwords, password hashes, session tokens, CSRF tokens, cookies, auth headers, browser storage state, CDP URLs, Playwright websocket URLs, raw provider payloads, raw browser profile material, or auth-bearing provider URLs;
  - failed and blocked sensitive actions are audited as well as successful actions.

- [ ] **Step 17: Define LAN network exposure guard**

  Add the V1 serving boundary before coding:

  - default local startup binds to loopback only, such as `127.0.0.1`;
  - non-loopback serving requires explicit LAN mode, such as `--lan` or `SEEKTALENT_UI_LAN=1`;
  - LAN startup prints the exact LAN URL, bind address, allowed hosts, allowed origins, and HTTP/HTTPS status;
  - unknown `Host` headers are rejected before workbench request handling;
  - credentialed CORS is allowed only for configured frontend origins;
  - CSRF remains required on mutating routes in both local and LAN modes;
  - startup warns or fails closed for public bind addresses, VPN-only interfaces, hotspot/shared interfaces, or other interfaces outside the intended local network;
  - HTTP LAN mode clearly reports that `Secure` cookies are not active;
  - HTTPS LAN mode documents certificate and trust setup;
  - public internet exposure is a non-goal and requires a future deployment/security review.

- [ ] **Step 18: Define the SourceRun job runner boundary**

  Add the V1 execution contract before coding:

  - source-run start routes create durable job rows and return; they do not run `WorkflowRuntime` inside the request handler;
  - FastAPI `BackgroundTasks` is not the primary source-run execution mechanism;
  - `source_run_jobs` stores durable execution state for CTS and Liepin jobs;
  - the local worker claims jobs with a SQLite lease, `lease_owner`, `lease_expires_at`, heartbeat, attempt count, and idempotency key;
  - CTS jobs may use small configured local concurrency;
  - Liepin jobs are serialized by `connection_id` and must also respect detail-open ledger leases;
  - pause, resume, and cancel use durable flags checked by the worker at safe checkpoints;
  - duplicate job claims are prevented by the lease/idempotency rules;
  - startup reconciliation handles stale leased jobs and missing heartbeats as `orphaned`, `paused_recoverable`, `failed`, or `blocked` according to the error map;
  - the runner API boundary is explicit enough to later replace the SQLite runner with Postgres plus queue workers, Celery/RQ/Dramatiq, or Temporal without changing source-run, ledger, candidate, or audit semantics.

## Task 1: Workbench Data Model And Store

- [ ] **Step 1: Write store tests first**

  Add `tests/test_workbench_store.py`.

  Required failing tests:

  - creates a local default tenant/workspace/user without leaking data across scopes;
  - creates bootstrap admin state only through an explicit setup path;
  - stores password hashes without plaintext, raw passwords, or reversible encrypted passwords;
  - creates, rotates, expires, and revokes server-side `user_sessions`;
  - records bounded `login_attempts` without passwords or session tokens;
  - records redacted `security_audit_events` for sensitive successful, failed, and blocked actions;
  - initializes a restricted local data root outside the repo for workbench state;
  - verifies SQLite, artifact, backup, and browser-profile paths are not world-readable or world-writable;
  - rejects or warns on symlinked, repo-local, or known sync-folder data roots according to configured policy;
  - enforces `workspace_memberships` before reading sessions, connections, candidate evidence, artifacts, or memory;
  - enforces role checks before source connection changes, detail policy updates, approvals, bypass decisions, and user administration;
  - rejects wrong-workspace reads without leaking whether the resource exists;
  - rejects oversized JD text, notes, requirement fields, strong profile seeds, and candidate notes before persistence;
  - rejects control characters and dangerous URL schemes in fields that can become links or actions;
  - creates one `WorkbenchSession` from JD and optional notes;
  - creates editable requirement triage state for the session and records user approval/edits;
  - stores manual strong-profile seeds and extracted shared attributes under the session;
  - creates CTS and Liepin `SourceRun` rows under one session;
  - creates `source_run_jobs` rows from source-run start requests without executing long work in the route;
  - list endpoints for session rail, events, candidates, candidate actions, candidate notes, detail-open requests, and source-run jobs use indexed keyset/cursor reads with explicit default and maximum page sizes;
  - tests inspect SQLite `EXPLAIN QUERY PLAN` or equivalent behavior for the highest-frequency reads and fail if they degrade to unbounded full scans;
  - claims source-run jobs with a SQLite lease and idempotency key;
  - prevents duplicate active job claims for the same source run;
  - serializes Liepin jobs by `connection_id`;
  - updates job heartbeat and expires stale leases during startup reconciliation;
  - persists pause/cancel requested flags and exposes them to worker checkpoints;
  - stores source-run policy rows with Liepin detail mode defaulting to `human_confirm`;
  - materializes source-card counters on `source_runs`;
  - creates a `source_connections` row for Liepin and records connection status events;
  - persists compliance gate decisions or references the existing provider-owned compliance gate row;
  - creates detail-open request rows before ledger lease acquisition;
  - rejects rejected detail-open requests without consuming budget;
  - supports explicit bypass detail-open requests without bypassing ledger or lease rules;
  - creates and leases one workbench detail-open attempt per connection at a time;
  - links workbench ledger rows to provider `liepin_detail_attempts` as child execution evidence when the worker is dispatched;
  - appends monotonic session events with source scope;
  - rejects event payloads containing cookies, auth headers, storage state, CDP URLs, worker URLs, or raw provider payload keys;
  - stores artifact refs without exposing raw provider payloads;
  - links candidate evidence to existing corpus `resume_doc_id`, `observation_id`, `subject_id`, and raw `artifact_ref_id` values without inlining raw content in ordinary candidate rows;
  - commits workbench outbox intents with UI-visible state before attempting CorpusStore, LiepinStore, or artifact writes;
  - reconciles failed external writes without duplicating raw corpus rows, provider attempt rows, or detail budget ledger entries;
  - keeps connection status-event writes internal to store/service functions called by login relay, runner, provider adapter, and startup reconciliation;
  - rejects any browser-facing attempt to write arbitrary connection status facts;
  - requires role, purpose, and audit metadata before reading raw corpus artifacts;
  - preserves source-run/corpus provenance needed by future TREC-pooling/static benchmark construction without creating workbench-owned qrels or pool-version tables;
  - stores user-scoped memory rows;
  - rejects memory rows that contain raw resumes, contact information, identity-rich candidate text, sensitive candidate evaluations, rejection reasons, or raw provider payloads by default;
  - allows only redacted, user-confirmed or policy-approved candidate-derived learning into memory;
  - stores candidate notes and actions;
  - stores candidate evidence with provider/source attribution and card/detail evidence level;
  - merges candidate review rows without losing source evidence;
  - refuses high-confidence auto-merge for same-name same-company different-person evidence;
  - preserves CTS evidence when Liepin detail evidence is added for the same review item;
  - startup reconciliation marks old `running` source runs as `orphaned` or `paused_recoverable`;
  - startup reconciliation marks stale leased source-run jobs as `orphaned`, `paused_recoverable`, `failed`, or `blocked` according to the mapped error;
  - startup reconciliation expires stale detail-open leases;
  - backup metadata records retention and excludes managed browser profile paths;
  - store write failure rolls back source-run state and session-event append together;
  - store functions reject illegal state transitions for sessions, source runs, jobs, detail-open requests, detail ledger rows, and external write intents;
  - simulated SQLite lock uses bounded retry, then returns a mapped storage error;
  - updates recruiter-time-saved metrics from cards reviewed, skipped detail opens, opened details, and candidate actions.

- [ ] **Step 2: Implement the store**

  Add `src/seektalent_ui/workbench_store.py`.

  Use SQLite with explicit tables for:

  - tenants;
  - workspaces;
  - users;
  - user_sessions;
  - login_attempts;
  - workspace_memberships;
  - sessions;
  - session_requirement_triage;
  - source_runs;
  - source_run_jobs;
  - source_run_policies;
  - source_connections;
  - connection_status_events;
  - compliance_gates or stable references to provider-owned gates;
  - detail_open_requests;
  - workbench-owned detail_open_ledger with optional provider attempt refs;
  - session_events;
  - security_audit_events;
  - candidate_evidence;
  - candidate_review_items;
  - candidate_actions;
  - candidate_notes;
  - strong_profile_seeds;
  - artifact_refs;
  - memory_rows;
  - external_write_intents;
  - recruiter_time_metrics.

  Configure SQLite with WAL, `busy_timeout`, and foreign keys. Keep write transactions short. Do not compute current source-card state by replaying all events on every request.

  Keep it simple. Use module-level functions or a small stateful store class only if it owns the DB path/connection lifecycle. Do not create manager/helper junk containers.

- [ ] **Step 3: Add API-facing models**

  Update `src/seektalent_ui/models.py` with Pydantic request/response models:

  - `WorkbenchSessionCreateRequest`;
  - `WorkbenchSessionResponse`;
  - `LoginRequest`;
  - `LoginResponse`;
  - `CurrentUserResponse`;
  - `BootstrapAdminRequest`;
  - `RequirementTriageResponse`;
  - `RequirementTriageUpdateRequest`;
  - `StrongProfileSeedCreateRequest`;
  - `StrongProfileSeedResponse`;
  - `SourceRunResponse`;
  - `SourceRunJobResponse`;
  - `SourceRunPolicyResponse`;
  - `SourceRunPolicyUpdateRequest`;
  - `SessionEventResponse`;
  - `CandidateEvidenceResponse`;
  - `CandidateReviewItemResponse`;
  - `CandidateActionResponse`;
  - `CandidateNoteResponse`;
  - `CandidateRawArtifactRefResponse`;
  - `DetailOpenRequestResponse`;
  - `DetailOpenDecisionRequest`;
  - `RecruiterTimeMetricsResponse`;
  - `SourceConnectionResponse`;
  - `SourceRunStatus`;
  - `SessionStatus`;
  - `SourceKind = Literal["cts", "liepin"]`.

  Keep external API models separate from Liepin worker DTOs and store rows.

## Task 2: Auth Scope, Workbench API, And SSE

- [ ] **Step 0: Write local auth/session security tests**

  Add `tests/test_workbench_auth_security.py`.

  Required failing tests:

  - bootstrap admin setup is explicit and cannot silently create a permanent shared default account;
  - password storage uses salted hashes and never persists plaintext or reversible password material;
  - login creates a server-side session and sets only a scoped HttpOnly session cookie;
  - successful login rotates the session identifier;
  - logout revokes the server-side session and old cookies no longer authenticate;
  - expired sessions cannot access workbench routes;
  - disabled users cannot log in and existing sessions are rejected;
  - repeated failed login attempts are throttled or temporarily locked;
  - auth/session logs and API responses never expose passwords, password hashes, session tokens, cookies, or source credentials;
  - source connection changes, detail-open policy changes, approval/bypass decisions, and user administration require an authorized role.

- [ ] **Step 1: Write FastAPI route tests**

  Add `tests/test_workbench_api.py`.

  Required failing tests:

  - new workbench endpoints are registered through FastAPI `APIRouter` modules rather than a new inline route block in `server.py`;
  - list endpoints enforce default and maximum page sizes and return keyset/cursor metadata where needed;
  - session events, candidate queue, candidate history, detail-open request, and source-run job list endpoints reject unbounded `limit` values;
  - unauthenticated requests cannot list sessions;
  - user A cannot read user B's sessions;
  - expired auth returns a login response without mutating state;
  - wrong-workspace IDs return forbidden or not found consistently;
  - session creation returns session plus default source cards;
  - session creation rejects empty or oversized JD text with explicit validation errors;
  - session creation returns requirement triage state and no source run starts before triage is approved or explicitly accepted;
  - requirement triage update and approval routes are scoped and CSRF-protected;
  - strong-profile seed create/update/delete routes are scoped and CSRF-protected;
  - strong-profile seed routes enforce count and length limits;
  - source-run start validates enabled source names;
  - source-run start uses the approved requirement triage and active strong-profile seed attributes;
  - source-run start creates a durable job and returns without running `WorkflowRuntime` inside the request;
  - repeated source-run start with the same idempotency key does not create duplicate active jobs;
  - source-run policy route can set Liepin detail mode to `human_confirm` or `bypass_confirm`;
  - source-run policy route rejects model-supplied or unauthorized detail-mode changes;
  - pause, resume, and cancel routes set durable job flags and transition source-run state correctly;
  - detail-open approval route creates a ledger attempt only after approval or allowed bypass;
  - detail-open rejection does not consume budget;
  - mutating routes reject missing or invalid CSRF tokens;
  - validation errors for empty JD, empty strong-profile seed, and unsupported source return explicit 4xx responses;
  - local dev CORS allows credentials only for configured frontend origins;
  - unknown `Host` headers are rejected before route handling;
  - credentialed requests from unconfigured origins are rejected;
  - SSE stream resumes from `Last-Event-ID`;
  - malformed `Last-Event-ID` returns a mapped client error or starts from a safe default without mutation;
  - client SSE disconnect does not leave a DB transaction open;
  - app-level SSE stream can deliver events for multiple sessions with session/source IDs in payload;
  - `GET /api/workbench/events?after_seq=...` returns durable refresh recovery data;
  - SSE payloads contain source scope and no forbidden secrets;
  - candidate result endpoint preserves source attribution;
  - recruiter metrics endpoint returns directional estimates from durable state;
  - browser-facing APIs cannot write arbitrary `connection_status_events`; only scoped user actions such as login, refresh, revoke, or disconnect may request a transition, and the internal service writes the resulting status fact.

- [ ] **Step 2: Implement local auth dependencies**

  Add `src/seektalent_ui/auth.py` and wire it through the FastAPI router modules. Do not bury auth/session/CSRF dependencies inside route-local closures in `server.py`.

  V1 must implement local account/session behavior before passing internal rollout. A bootstrap-only mode is allowed only for narrow development slices and must be visibly labeled as not ready for multi-user LAN use.

  The route layer must resolve:

  - `tenant_id`;
  - `workspace_id`;
  - `user_id` / `actor_id`;
  - role;
  - active workspace membership.

  Do not leave new workbench routes globally unscoped.

  Requirements:

  - create the first admin user only through explicit local bootstrap setup;
  - hash passwords with a modern salted password hashing scheme;
  - keep session identity server-side in `user_sessions`;
  - set scoped HttpOnly cookies for session lookup;
  - rotate session identifiers on login;
  - expire and revoke sessions on timeout/logout;
  - record bounded login attempts for throttling/temporary lockout;
  - reject disabled users and revoke or reject their existing sessions;
  - implement CSRF protection before adding mutating workbench routes.

  The CSRF token must be scoped to the authenticated user/session and must not be accepted from query parameters.

- [ ] **Step 3: Add workbench routes**

  Add workbench routes through `APIRouter` modules and include them from `src/seektalent_ui/server.py`. `server.py` should assemble the app and keep legacy `/api/runs` compatibility while the new frontend migrates.

  Add routes:

  - `POST /api/auth/bootstrap-admin`;
  - `POST /api/auth/login`;
  - `POST /api/auth/logout`;
  - `GET /api/auth/me`;
  - `POST /api/workbench/sessions`;
  - `GET /api/workbench/sessions`;
  - `GET /api/workbench/sessions/{session_id}`;
  - `GET /api/workbench/sessions/{session_id}/requirements`;
  - `PUT /api/workbench/sessions/{session_id}/requirements`;
  - `POST /api/workbench/sessions/{session_id}/requirements/approve`;
  - `GET /api/workbench/sessions/{session_id}/strong-profile-seeds`;
  - `POST /api/workbench/sessions/{session_id}/strong-profile-seeds`;
  - `PATCH /api/workbench/sessions/{session_id}/strong-profile-seeds/{seed_id}`;
  - `DELETE /api/workbench/sessions/{session_id}/strong-profile-seeds/{seed_id}`;
  - `POST /api/workbench/sessions/{session_id}/source-runs`;
  - `GET /api/workbench/source-runs/{source_run_id}/policy`;
  - `PUT /api/workbench/source-runs/{source_run_id}/policy`;
  - `POST /api/workbench/source-runs/{source_run_id}/pause`;
  - `POST /api/workbench/source-runs/{source_run_id}/resume`;
  - `POST /api/workbench/source-runs/{source_run_id}/cancel`;
  - `GET /api/workbench/detail-open-requests`;
  - `POST /api/workbench/detail-open-requests/{request_id}/approve`;
  - `POST /api/workbench/detail-open-requests/{request_id}/reject`;
  - `GET /api/workbench/sessions/{session_id}/events`;
  - `GET /api/workbench/events?after_seq=...`;
  - `GET /api/workbench/events/stream`;
  - `GET /api/workbench/sessions/{session_id}/events/stream`;
  - `GET /api/workbench/sessions/{session_id}/candidates`;
  - `GET /api/workbench/sessions/{session_id}/metrics`;
  - `GET /api/workbench/sessions/{session_id}/sources`;
  - `GET /api/workbench/source-connections`;
  - `GET /api/workbench/source-connections/{connection_id}`.

  Keep the older `/api/runs` endpoints working until the new frontend is fully switched.

  Do not add a public `POST .../status-events` route. Connection status events are written through internal service/store functions. Browser-facing routes may trigger explicit scoped actions such as Liepin login handoff, refresh status, revoke session, or disconnect connection; the backend then records the resulting status and audit events.

  Prefer the app-level stream at `/api/workbench/events/stream`. The session-scoped stream may exist for compatibility, but the frontend should not open one stream per visible session or source.

- [ ] **Step 4: Implement SSE with `sse-starlette`**

  Use `EventSourceResponse`.

  Read events from committed SQLite rows in bounded batches. Use the global sequence as SSE `id`. Support `Last-Event-ID`. Also support explicit durable recovery through `GET /api/workbench/events?after_seq=...`.

  The generator must:

  - create DB sessions inside the loop;
  - yield structured events with `id`, `event`, and JSON `data`;
  - check `request.is_disconnected()`;
  - configure send timeout and keepalive pings;
  - avoid holding a SQLite read transaction while sleeping or waiting for new events;
  - never mutate state from the stream route.

## Task 2A: Central Redaction Boundary

- [ ] **Step 1: Write redaction tests**

  Add `tests/test_workbench_redaction.py`.

  Required failing tests:

  - API responses reject or redact forbidden keys;
  - SSE payloads reject or redact forbidden keys;
  - `session_events.payload_redacted_json` cannot persist forbidden keys;
  - exception serialization redacts forbidden values;
  - ordinary artifact refs and candidate API responses do not inline raw provider payloads or raw resume/profile material;
  - corpus raw-data references expose only stable IDs and redacted metadata outside authorized corpus read paths;
  - redaction catches `cookie`, `Cookie`, `Authorization`, `Bearer`, `storageState`, `localStorage`, `sessionStorage`, `cdp`, `wsEndpoint`, `webSocketDebuggerUrl`, `playwright`, `browserContext`, `authHeader`, `set-cookie`, and `rawPayload`.

- [ ] **Step 2: Implement shared redaction helpers**

  Add:

  - `src/seektalent_ui/redaction.py`
  - update or reuse `src/seektalent/providers/liepin/security.py` or add a provider-local redaction adapter if needed.

  Wire the helper into:

  - API response serialization;
  - SSE event construction;
  - session event persistence;
  - ordinary artifact writing where workbench artifacts are produced;
  - corpus raw-data metadata responses while preserving raw-payload storage for authorized benchmark/debug reads;
  - logger and exception serialization around workbench routes;
  - Liepin worker contract mapping.

  Do not scatter separate deny-lists across route handlers.

## Task 2B: Error And Rescue Map

- [ ] **Step 1: Write error/rescue tests**

  Add `tests/test_workbench_error_rescue.py`.

  Required failing tests:

  - auth failures return mapped 401/403/404 responses without mutating scoped resources;
  - missing/stale CSRF rejects mutations before store writes;
  - SQLite write lock retries are bounded and return a mapped storage error after retry exhaustion;
  - source-run state update plus event append rolls back together on failure;
  - SSE disconnect closes the generator without writing state;
  - malformed `Last-Event-ID` cannot crash the server or leak traceback details;
  - duplicate source-run job claim fails without running the job twice;
  - stale source-run job lease becomes recoverable during startup reconciliation;
  - cancel requested during a worker checkpoint produces a durable cancelled state and event;
  - runtime exception marks only the affected source run `failed` and emits a redacted failure event;
  - Liepin login expired or verification required marks source run `blocked` and persists a connection status event;
  - detail budget exhausted blocks the detail request before ledger spend;
  - active detail lease leaves the next request `pending` or `blocked`, not concurrently opened;
  - worker crash after possible detail dispatch records ledger `maybe_used`;
  - startup reconciliation converts stale `running` source runs and leases into recoverable states;
  - all user-visible error payloads pass the shared redaction helper.

- [ ] **Step 2: Implement mapped workbench errors**

  Add the smallest shared error module that keeps route behavior consistent, for example `src/seektalent_ui/errors.py`.

  Requirements:

  - name specific exceptions for auth/scope, CSRF, validation, storage, event persistence, source-run job claim/lease, runtime bridge, source connection, detail ledger, SSE resume, and rollback/startup reconciliation;
  - map each exception to HTTP status, source/session state transition where applicable, event name where useful, and user-safe message;
  - preserve internal context for logs without exposing secrets or raw provider payloads;
  - avoid broad catch-all handlers except at process or request boundaries where the exception is immediately classified, redacted, and re-raised or converted to a mapped response;
  - use the same mapping from API routes, runtime bridge callbacks, startup reconciliation, and Liepin provider integration.

## Task 2C: Input, Rendering, And Prompt-Injection Security

- [ ] **Step 1: Write input/rendering security tests**

  Add `tests/test_workbench_input_security.py`.

  Required failing tests:

  - empty JD, oversized JD, oversized notes, oversized requirement fields, and oversized candidate notes are rejected with explicit 4xx responses;
  - strong profile seed count and per-seed length limits are enforced;
  - dangerous URL schemes and control characters are rejected where text can become links, provider actions, or query parameters;
  - HTML/script in JD, notes, strong profile seeds, provider snippets, candidate summaries, event payloads, and model output is rendered as escaped text;
  - Markdown or Pretext report rendering uses a sanitizer allowlist and strips scripts, event handlers, iframes, and unsafe links;
  - prompt-injection fixtures cannot make the runtime expose secrets, approve detail opens, enable bypass mode, mutate source settings, or call provider/browser actions;
  - malformed structured LLM output gets the bounded structured-output retry and then a mapped validation failure;
  - model output is schema-validated before persistence or display.

- [ ] **Step 2: Implement validation and rendering helpers**

  Add a small shared module if needed, for example `src/seektalent_ui/input_security.py`.

  Requirements:

  - centralize field length constants and text normalization for workbench inputs;
  - validate source settings and detail-open mode as explicit enums with authorization checks;
  - expose safe display helpers for frontend/API DTOs where text-heavy fields are rendered;
  - treat provider snippets and LLM output as untrusted text even when they come from internal runtime paths;
  - wire sanitizer behavior into artifact/report writers where Markdown or Pretext is used;
  - keep prompt-injection fixtures in tests, not in production prompts.

## Task 2D: Local Data At Rest Security

- [ ] **Step 1: Write filesystem/data-root security tests**

  Add `tests/test_workbench_data_security.py`.

  Required failing tests:

  - default data root is outside the git repo and outside known sync-folder paths;
  - data root, SQLite directory, artifact directory, corpus raw-payload directory, benchmark directory, backup directory, and managed browser profile directory are created owner-only;
  - SQLite database, WAL/SHM files, artifacts, corpus raw-payload files, benchmark artifacts, backups, and browser profile files are not world-readable or world-writable;
  - startup check detects and blocks or warns on symlinked data roots according to policy;
  - startup check detects and blocks or warns on repo-local and known sync-folder data roots according to policy;
  - backup creation preserves restrictive permissions and records retention metadata;
  - ordinary backup/support export excludes managed browser profiles, cookies, raw provider session state, raw corpus payload data, and raw provider payloads;
  - protected corpus raw-data export requires explicit operator intent and restrictive permissions;
  - diagnostic logs never include credential-bearing browser profile data or raw SQLite row dumps.

- [ ] **Step 2: Implement data-root and backup safeguards**

  Add a small module if needed, for example `src/seektalent_ui/data_paths.py`.

  Requirements:

  - centralize workbench data paths instead of scattering path construction across store, artifacts, backups, and Liepin profile code;
  - create directories and files with restrictive permissions;
  - run startup permission/path checks before serving LAN traffic;
  - provide an explicit operator override for sync-folder or nonstandard paths only with a visible warning;
  - include corpus raw-payload and benchmark directories in path construction and permission checks;
  - keep managed browser profiles under a restricted provider profile directory;
  - exclude provider profiles and raw corpus content from ordinary backups and support bundles;
  - document backup retention and FileVault/full-disk-protection recommendation.

## Task 2E: Security Audit Trail

- [ ] **Step 1: Write security audit tests**

  Add `tests/test_workbench_security_audit.py`.

  Required failing tests:

  - bootstrap admin creation writes a redacted audit row;
  - login, logout, failed-login lockout, and disabled-user rejection write audit rows without passwords, password hashes, session tokens, cookies, or CSRF tokens;
  - user admin, role changes, and workspace membership changes are audited;
  - source connection create/update/delete and Liepin login status changes are audited;
  - compliance gate changes are audited;
  - detail policy changes, manual approvals, rejections, and bypass decisions are audited;
  - raw corpus artifact read/export, corpus export creation, and benchmark dataset access are audited;
  - data-root override, startup permission warning, backup creation, restore, support export, and feature-gate changes are audited;
  - manual candidate merge/split decisions that affect review identity are audited;
  - failed and blocked sensitive actions are audited as well as successful actions;
  - audit metadata passes the shared redaction helper and cannot persist provider secrets or browser internals.

- [ ] **Step 2: Implement audit event writer**

  Add a small audit module if needed, for example `src/seektalent_ui/audit.py`.

  Requirements:

  - centralize audit writes instead of scattering ad hoc event inserts;
  - require actor, actor role, action, target type/id, result, reason code, and scope fields where applicable;
  - write audit rows in the same transaction as the sensitive mutation where practical;
  - audit failed/blocked attempts without leaking whether unauthorized resources exist beyond the route's normal response semantics;
  - use the central redaction helper before persistence and before any API/debug response;
  - keep `security_audit_events` separate from `session_events` so non-session actions are attributable.

## Task 2F: LAN Network Exposure Guard

- [ ] **Step 1: Write LAN exposure tests**

  Add `tests/test_workbench_network_exposure.py`.

  Required failing tests:

  - default UI startup config binds to loopback only;
  - non-loopback bind requires explicit LAN mode;
  - LAN mode startup reports bind address, LAN URL, allowed hosts, allowed origins, and HTTP/HTTPS status;
  - unknown `Host` headers are rejected before authenticated workbench routes run;
  - credentialed CORS is allowed only for configured frontend origins;
  - unconfigured origins cannot use cookies for mutating routes;
  - HTTP LAN mode reports that `Secure` cookies are inactive rather than pretending they are enabled;
  - public bind addresses, VPN-only interfaces, and hotspot/shared interfaces warn or fail closed according to configured policy;
  - trusted proxy headers are ignored unless explicitly configured.

- [ ] **Step 2: Implement network exposure guard**

  Add a small module if needed, for example `src/seektalent_ui/network_guard.py`.

  Requirements:

  - centralize bind-mode, allowed-host, and allowed-origin config;
  - keep default startup loopback-only;
  - require explicit LAN mode for non-loopback binding;
  - add Host validation middleware before workbench route handling;
  - configure CORS credentials from the allowed-origin list only;
  - emit safe startup diagnostics for LAN URL, HTTP/HTTPS cookie posture, bind address, and exposure warnings;
  - reject or ignore untrusted proxy headers by default;
  - do not make the LAN guard depend on Liepin-specific code.

## Task 2G: SourceRun Local Job Runner

- [ ] **Step 1: Write job runner tests**

  Add `tests/test_workbench_job_runner.py`.

  Required failing tests:

  - source-run start creates a `source_run_jobs` row and returns before executing runtime work;
  - job claim uses a SQLite lease with owner, expiry, heartbeat, attempt count, and idempotency key;
  - a second worker cannot claim the same active job;
  - repeated start with the same idempotency key returns the existing active job;
  - CTS jobs can run up to the configured local concurrency limit;
  - Liepin jobs are serialized by `connection_id`;
  - Liepin job execution also respects detail-open ledger leases before opening details;
  - worker heartbeat extends or refreshes the active lease without holding long DB transactions;
  - pause request transitions job/source run into paused or pausing at a safe checkpoint;
  - cancel request transitions job/source run into cancelled or cancelling at a safe checkpoint;
  - worker crash before runtime dispatch leaves the job recoverable without emitting false provider progress;
  - worker crash after possible Liepin detail dispatch preserves ledger ambiguity as `maybe_used` where applicable;
  - startup reconciliation converts stale leased jobs into `orphaned`, `paused_recoverable`, `failed`, or `blocked`;
  - illegal job transitions are rejected, including resume after completion, cancel after completion, and direct `queued` to `completed`;
  - runtime exceptions update job status, source-run status, and redacted session events atomically where practical.

- [ ] **Step 2: Implement the local runner**

  Add a small local execution module if needed, for example `src/seektalent_ui/job_runner.py`.

  Requirements:

  - provide functions for enqueue, claim, heartbeat, mark completed, mark failed/blocked, request pause, request cancel, and reconcile stale jobs;
  - run source jobs outside request handlers;
  - avoid FastAPI `BackgroundTasks` as the source-run executor;
  - keep worker concurrency configurable and conservative by default;
  - serialize Liepin jobs by `connection_id`;
  - expose checkpoint helpers so runtime bridge and provider adapters can honor pause/cancel requests;
  - update source-run materialized state and append events through store functions, not direct ad hoc SQL from worker code;
  - keep the public runner boundary small enough to replace later with a cloud queue or durable workflow engine without changing API or UI semantics.

## Task 2H: Corpus-Backed Raw Data, Memory Firewall, And Benchmark Governance

- [ ] **Step 1: Write corpus-access and memory-firewall tests**

  Add `tests/test_workbench_corpus_access.py`.

  Required failing tests:

  - provider-returned resume/profile material is stored through the existing corpus raw-payload boundary, not a parallel workbench raw-vault table;
  - ordinary candidate APIs return corpus IDs, raw artifact IDs, and redacted metadata, not raw resume/profile content;
  - SSE events, session events, logs, diagnostics, support exports, and ordinary artifacts reject raw resume/profile content;
  - raw corpus artifact read requires an authorized role and explicit purpose: `benchmark`, `debugging`, or `manual_review`;
  - raw corpus artifact read/export writes a redacted security audit event;
  - candidate evidence stores `resume_doc_id`, `observation_id`, `subject_id`, raw `artifact_ref_id`, evidence level, source-run ID, query instance/fingerprint, provider request/page/rank, and detail-open ledger reference when available;
  - workbench does not create qrels, benchmark pool versions, or workbench-owned benchmark manifests;
  - corpus raw-payload ingestion uses `external_write_intents` and idempotency keys so a partial failure can be retried or reconciled;
  - benchmark fixtures cannot be written into the repo unless marked synthetic or redacted;
  - memory rows reject raw resumes, contact details, identity-rich candidate text, sensitive candidate evaluations, rejection reasons, and raw provider payloads by default;
  - candidate-derived memory writes require redaction/abstraction and a user-confirmed or policy-approved memory category;
  - prompt-injection text cannot authorize raw candidate data entering memory.

- [ ] **Step 2: Implement corpus-backed raw access and memory firewall**

  Add small modules if needed, for example:

  - `src/seektalent_ui/corpus_access.py`
  - `src/seektalent_ui/memory_firewall.py`
  - `src/seektalent_ui/benchmark_boundary.py`

  Requirements:

  - integrate with existing `CorpusStore`/corpus artifact refs instead of writing raw provider payloads through a new workbench store;
  - create and resolve workbench outbox intents for corpus raw-payload writes instead of mixing corpus writes into a workbench transaction;
  - return corpus refs and raw artifact refs to evidence rows without exposing raw content through ordinary APIs;
  - require actor, role, purpose, scope, and audit metadata before raw corpus artifact reads;
  - preserve future static benchmark/TREC-pooling provenance without changing the benchmark evaluation method;
  - make raw-data backup/export an explicit protected operation, separate from ordinary support export;
  - validate memory writes through the memory firewall before persistence;
  - keep memory useful for recruiter preferences, search strategies, market/role learning, workflow habits, and high-level lessons, not private candidate data.

## Task 3: Runtime Bridge For Source Runs

- [ ] **Step 1: Write bridge tests**

  Add `tests/test_workbench_runtime_bridge.py`.

  Required failing tests:

  - one workbench session with CTS and Liepin enabled creates one shared session-level planning state and two source-run jobs;
  - session-level planning emits requirement and strategy events once per session, not once per source when avoidable;
  - requirement triage is generated before the first source run and later runs use the approved triage state;
  - strong-profile seed attributes are available to query generation, filtering, scoring context, and candidate comparison;
  - prompt-injection text in JD, notes, provider snippets, or strong profile seeds is treated as data and cannot change runtime policy;
  - claimed source-run jobs invoke the runtime bridge outside request handlers;
  - pause/cancel checkpoint state is visible to the runtime bridge before expensive provider actions;
  - CTS source run invokes the same retrieval/runtime path used by existing UI/CLI runs;
  - CTS parity regression: using a deterministic fixture or stubbed runtime, the existing CTS runtime/CLI path and the workbench CTS source-run path produce the same stable core result contract;
  - progress callback appends `strategy_event_added`, `source_search_started`, `source_candidates_found`, `candidate_scored`, and `source_run_completed` events;
  - runtime/provider results may create corpus raw-payload artifacts, but ordinary progress events contain only redacted refs;
  - source-run completion never emits `session_completed`;
  - aggregator emits `session_completed` only after every enabled source is terminal or blocked and final artifacts exist;
  - session timeline events remain source-attributed so the frontend can show all-sources, CTS-only, and Liepin-only views from one app-level stream;
  - Liepin source run receives tenant/workspace/actor/connection/compliance/detail-budget provider context;
  - runtime exception persists source-run failure state and a redacted event before surfacing the error;
  - source failure marks only that source run failed unless the whole session must fail;
  - session aggregation can show partial results when one source completes and another is blocked;
  - restart recovery never leaves an old source run permanently `running`.

  The CTS parity assertion should compare stable contract fields only: approved requirement triage input, provider/source kind, top candidate IDs or resume IDs where deterministic, candidate count, evidence refs, source-run terminal state, artifact refs, and redacted summary presence. Do not compare free-form LLM prose byte-for-byte.

- [ ] **Step 2: Implement the bridge**

  Add `src/seektalent_ui/runtime_bridge.py`.

  Responsibilities:

  - create provider-specific `WorkflowRuntime` inputs from a `WorkbenchSession` and `SourceRun`;
  - accept claimed job context from the local job runner rather than being called directly from API routes;
  - read approved requirement triage and active strong-profile seed attributes before constructing source-run inputs;
  - keep session-level planning, source-run execution, and aggregation/finalization as separate concepts;
  - pass progress callbacks into the event store;
  - map existing runtime progress names into workbench event names;
  - persist candidate evidence and result summaries;
  - persist original resume/profile material only through the corpus access boundary when the source provides raw material needed for benchmark;
  - keep source-run state transitions explicit;
  - check pause/cancel state at safe boundaries before source search, detail opens, scoring, and finalization.

  If a first slice temporarily runs the full existing `WorkflowRuntime` per source, normalize the emitted events at the bridge boundary. A source run can produce `source_run_completed`; it cannot complete the session by itself.

- [ ] **Step 3: Add provider context injection**

  Update the narrowest existing runtime boundary needed, likely:

  - `src/seektalent/runtime/retrieval_runtime.py`;
  - `src/seektalent/runtime/orchestrator.py`;
  - `src/seektalent/core/retrieval/service.py`.

  The result should let the workbench pass `SearchRequest.provider_context` without hard-coding Liepin logic into generic retrieval. CTS should continue to work with empty provider context.

- [ ] **Step 4: Implement aggregation boundary**

  Add a small aggregation module, for example `src/seektalent_ui/aggregation.py`.

  Responsibilities:

  - read candidate evidence across enabled source runs;
  - merge/dedupe into `candidate_review_items`;
  - produce the default merged candidate queue for the session while keeping source badges and evidence drilldown;
  - require merge confidence and preserve `evidence_ids`;
  - derive source badges from evidence rows;
  - support auditable manual split and manual merge markers even if the V1 UI hides the controls;
  - prevent Liepin detail evidence from overwriting CTS evidence;
  - update session lifecycle;
  - emit `candidate_merged`, `session_partially_completed`, and `session_completed` events;
  - write or reference final session artifacts without losing source attribution.

## Task 3A: Requirement Triage And Strong-Profile Seed Workflow

- [ ] **Step 1: Write workflow tests**

  Add or update:

  - `tests/test_workbench_store.py`;
  - `tests/test_workbench_api.py`;
  - `tests/test_workbench_runtime_bridge.py`;
  - `tests/test_workbench_requirement_triage_eval.py`.

  Required failing tests:

  - creating a session generates or initializes requirement triage state;
  - source-run start is blocked or explicitly gated until triage is approved/accepted;
  - user edits to must-haves, nice-to-haves, synonyms, seniority filters, and exclusions are persisted;
  - HTML/script and prompt-injection text inside triage edits are persisted and rendered only as safe text;
  - strong-profile seed summaries can be added, disabled, and removed;
  - extracted strong-profile shared attributes are stored separately from raw user-pasted text;
  - strong-profile seed extraction cannot approve detail opens or enable bypass mode through model output;
  - runtime inputs receive approved triage plus active strong-profile attributes;
  - SSE emits `requirement_triage_updated`, `requirement_triage_approved`, `strong_profile_seed_added`, and `strong_profile_attributes_extracted`.

  Add a small golden/eval fixture set for triage and strong-profile shared attributes:

  - 3-5 representative high-end recruiter JDs across different domains;
  - 2-3 strong-profile seed groups with shared attributes a senior recruiter would expect;
  - assertions for structured business contracts, not byte-for-byte LLM prose: must-haves, nice-to-haves, synonyms, seniority filters, exclusions, query hints, and shared attributes;
  - at least one fixture where a nice-to-have must not be promoted into a hard requirement;
  - at least one fixture where seniority or domain terms need synonym expansion;
  - at least one prompt-injection fixture that asks to change policy, reveal secrets, or approve detail opens and is treated only as input data.

  Reuse existing requirement extraction test patterns where possible, but keep the workbench fixture focused on the new user-visible triage gate and strong-profile seed workflow.

- [ ] **Step 2: Implement triage storage and API behavior**

  Keep the implementation small. The first slice may use structured JSON columns for triage details if the API models and tests keep the behavior explicit.

  Requirements:

  - keep triage rows scoped by tenant/workspace/user/session;
  - preserve user edits and approval actor/time;
  - make source-run start read the approved triage;
  - do not hide unapproved generated requirements inside runtime-only state.

- [ ] **Step 3: Implement strong-profile seed extraction boundary**

  Strong profile seeds are manually pasted user text in V1.

  Requirements:

  - store the original summary as user input scoped to the session;
  - store extracted shared attributes in a redacted structured field or artifact ref;
  - let users disable a seed without deleting the historical action;
  - never import profiles automatically from CTS, Liepin, ATS, CRM, or browser state in this phase.

## Task 4: Liepin Detail Ledger, URL, And Candidate Deeplink Support

- [ ] **Step 1: Write detail-ledger and lease tests**

  Add or update:

  - `tests/test_workbench_store.py`;
  - `tests/test_liepin_detail_ledger.py`;
  - `tests/test_liepin_detail_integration.py`;
  - `tests/test_workbench_api.py`;
  - `tests/test_workbench_error_rescue.py`.

  Required failing tests:

  - detail-open request is created in `pending` state before any Liepin detail page is opened;
  - default policy requires explicit user approval before lease acquisition;
  - bypass policy marks eligible requests as `bypassed` but still acquires the same backend lease;
  - rejected requests do not consume daily budget;
  - rejected requests cannot later acquire a ledger lease without a new auditable request or explicit reopen action;
  - blocked requests expose the blocking reason, such as compliance, connection, budget, lease, or risk-control state;
  - budget-exhausted and active-lease cases are user-visible blocked/pending states, not hidden worker failures;
  - only one active detail-open lease exists per `connection_id`;
  - lease acquisition is transactional under concurrent attempts;
  - stale leases expire on startup reconciliation;
  - statuses cover `planned`, `leased`, `opened`, `skipped`, `blocked`, `failed`, and `maybe_used`;
  - idempotency key prevents double-counting the same open attempt;
  - known provider deeplink action does not consume budget;
  - uncertain worker crash after dispatch records `maybe_used` instead of silently freeing budget;
  - provider child-attempt creation is coordinated through a workbench outbox intent and can be retried or reconciled without acquiring a second detail lease;
  - terminal ledger states reject duplicate opens or silent budget refunds;
  - ledger/rescue events are redacted before persistence and SSE emission.

- [ ] **Step 2: Implement the workbench-owned detail ledger**

  The workbench store owns the canonical approval, lease, source-run, candidate-evidence, and source-card budget state. Existing Liepin provider `liepin_detail_attempts` rows are child worker-attempt evidence for dispatch, page load, detail payload observation, and conservative consumption state. They do not replace the workbench approval queue or active per-connection lease.

  Ledger fields:

  - `tenant_id`
  - `workspace_id`
  - `actor_id`
  - `connection_id`
  - `source_run_id`
  - `candidate_evidence_id`
  - `provider_candidate_key`
  - `detail_url_hash` or managed safe route key
  - `status`
  - `opened_at`
  - `budget_day`
  - `idempotency_key`
  - `lease_expires_at`
  - optional `provider_attempt_id` or equivalent provider child-attempt ref

  The budget counter shown on source cards must come from workbench ledger state or a materialized update committed with ledger state, not from frontend guesses or direct frontend reads of provider attempt rows.

  Provider child-attempt rows are external writes from the workbench perspective. Create or resolve them through `external_write_intents` with an idempotency key derived from the workbench ledger row, connection, budget day, and candidate provider key. A retry must attach the existing provider attempt when one already exists; it must not acquire another workbench lease or double-count daily budget.

- [ ] **Step 2A: Implement detail-open approval queue**

  Add the smallest queue surface that supports the accepted product behavior:

  - create a `detail_open_requests` row for each candidate that the scoring/card review wants to inspect in detail;
  - default row status is `pending` under `human_confirm` policy;
  - user approval moves it to `approved`;
  - user rejection moves it to `rejected` and must not touch the ledger;
  - `bypass_confirm` policy may move eligible rows to `bypassed`, but the next step is still normal ledger lease acquisition;
  - backend checks may move any row to `blocked` with a reason before ledger acquisition;
  - provider `liepin_detail_attempts` rows are created only after a workbench lease is acquired, and they are linked back to the workbench ledger row;
  - expired rows are recoverable after server restart and must not leave budget ambiguous.

- [ ] **Step 3: Write worker and mapper tests**

  Update or add tests in:

  - `apps/liepin-worker/tests/extraction.test.ts`;
  - `apps/liepin-worker/tests/detail.test.ts`;
  - `tests/test_liepin_provider_mapping.py`;
  - `tests/test_liepin_verified_loop.py`.

  Required failing tests:

  - Liepin card extraction captures a safe detail URL when present;
  - worker contract carries the detail URL through card payloads;
  - Python mapper stores the provider deeplink as metadata without putting auth-bearing URLs in ordinary artifacts;
  - candidate action can prefer already known or already opened detail URL;
  - detail budget is not consumed by merely rendering a known deeplink;
  - detail-open approval or bypass status is carried into the workbench event stream without exposing provider secrets.

- [ ] **Step 4: Extend contracts and extraction**

  Update:

  - `apps/liepin-worker/src/contracts.ts`;
  - `apps/liepin-worker/src/cardSearch.ts`;
  - `apps/liepin-worker/src/extraction.ts`;
  - `src/seektalent/providers/liepin/worker_contracts.py`;
  - `src/seektalent/providers/liepin/mapper.py`.

  Preserve redaction and boundary checks. Do not expose cookies, tokens, storage state, or raw provider payloads.

- [ ] **Step 5: Store and expose safe provider actions**

  Add a workbench candidate action route only if it can be scoped and safe:

  - `POST /api/workbench/sessions/{session_id}/candidates/{candidate_id}/provider-actions/open`;

  V1 response should be a safe action descriptor, not raw browser internals. If a Liepin detail URL may be auth-bearing in practice, return an instruction to open through the managed browser connection instead of returning the URL to arbitrary clients.

## Task 5: Replace The Frontend With The Workbench UI

- [ ] **Step 1: Slice 1, scoped login + session rail + CTS source card**

  Build the new workbench app under `apps/web`. Remove the old one-run UI completely; do not preserve a compatibility copy or parallel legacy directory.

  Required frontend stack:

  - Vite;
  - TypeScript;
  - TanStack Router;
  - TanStack Query;
  - TanStack Form for JD/settings forms if useful;
  - TanStack Virtual for session/event/candidate long lists if useful;
  - Vitest + jsdom.
  - Playwright for page-level layout smoke tests;
  - `odiff-bin` for local screenshot comparison against reference baselines.

  Do not introduce Storybook in M0-M6. Storybook can be reconsidered later when repeated components such as source cards, candidate cards, detail approval queues, and session rail states need a component catalog.

  Implement:

  - `/login`;
  - authenticated route guard using TanStack Router `beforeLoad`;
  - session rail with search and collapse/expand;
  - create session form;
  - Requirement Triage Gate with editable must-haves, nice-to-haves, synonyms, seniority filters, and exclusions;
  - CTS source card;
  - app-level EventSource connection;
  - merged session event timeline with durable `session_id` and `source_run_id` attribution;
  - targeted TanStack Query keys for sessions, source runs, events, and candidates.

  Tests:

  - unauthenticated route redirects to login;
  - session rail renders and filters sessions;
  - creating a session calls the workbench API;
  - requirement triage renders, edits, approves, and gates source-run start;
  - JD, notes, triage text, and event payload text render escaped HTML/script as text;
  - CTS source card renders from source-run materialized state;
  - event timeline can show all-sources and CTS-only views without opening a second EventSource;
  - SSE event patches or invalidates only targeted query keys, not every query globally.

- [ ] **Step 2: Slice 2, strong-profile seeds, candidate evidence, and review queue**

  Implement:

  - Strong Profile Seed Lane for manual 3-5 profile summaries;
  - extracted shared-attribute display;
  - right candidate review queue;
  - source badges;
  - evidence level labels;
  - missing risk display;
  - candidate note and action hooks.

  Tests:

  - strong profile seed lane can add, disable, and remove manual seeds;
  - extracted attributes are shown as search/scoring context;
  - strong profile seeds, provider snippets, candidate summaries, and notes render escaped HTML/script as text;
  - candidate queue defaults to merged session results and preserves source badges and evidence level;
  - CTS real candidates render from durable candidate endpoints;
  - notes/actions persist through API calls;
  - source badges are derived from evidence, not hard-coded.

- [ ] **Step 3: Slice 3, Liepin connection settings and status card**

  Implement:

  - settings route;
  - source settings list;
  - Liepin connection state card;
  - login expired / verification required / connected / blocked states;
  - route back to originating session.

  Tests:

  - source cards render CTS and Liepin states from API data;
  - connection status events update the Liepin source card;
  - source setting forms reject unsupported enum values and dangerous URL schemes;
  - settings routes are auth protected.

- [x] **Step 4: Slice 4, Liepin card-level source run**

  Implement:

  - Liepin source-run start from a workbench session;
  - card-level events and counters;
  - no detail opening in this slice;
  - source-card warning states.

  Tests:

  - Liepin card-level run updates source card from backend state;
  - Liepin card evidence appears in candidate queue;
  - no detail ledger row is consumed by card-level search alone.

- [ ] **Step 5: Slice 5, Liepin detail ledger and safe provider action**

  Implement:

  - detail-open candidate action;
  - detail-open approval queue;
  - configurable detail mode with `human_confirm` default and explicit `bypass_confirm`;
  - sequential lease UX;
  - detail budget counters;
  - safe managed-browser action descriptor for auth-sensitive detail URLs.

  Tests:

  - detail-open queue requires approval by default;
  - bypass mode skips only per-candidate confirmation and still respects backend lease/budget;
  - prompt-injection text in candidate evidence cannot approve or bypass a detail-open request;
  - rejection does not consume budget;
  - detail action respects backend lease;
  - known safe deeplink does not consume budget;
  - managed-browser-only action does not return raw auth-bearing URL.

- [ ] **Step 6: Slice 6, visual parity pass**

  Build the main screen:

  ```text
  collapsible session rail | JD/source panel | strategy panel | candidate queue
  ```

  Required UI elements:

  - top search and account/settings controls;
  - collapsible far-left session rail with search;
  - the supplied HTML's lower-left cards remain source cards, not session navigation;
  - JD + notes panel;
  - must-have / nice-to-have summary;
  - requirement triage gate;
  - strong profile seed lane;
  - source cards in the lower-left source area;
  - center merged strategy timeline/canvas with source filter or source drilldown;
  - right merged candidate queue with source badges and evidence drilldown;
  - detail-open approval queue state;
  - recruiter-time-saved metrics;
  - bottom phase strip.

  Keep styling close to `/Users/frankqdwang/Documents/工作/seektalent/references/Recruiter Agent _Standalone_.html`: quiet internal-tool density, restrained color, no landing page, no marketing hero.

  Add lightweight Playwright layout smoke tests instead of relying only on manual screenshots:

  - render deterministic mock workbench state for desktop and mobile widths;
  - capture screenshots for both viewports;
  - assert stable bounding boxes for session rail, JD/source panel, source cards, strategy panel, candidate queue, and bottom phase strip;
  - assert those regions do not overlap and that long text does not overflow key controls;
  - compare screenshots against local reference baselines with `odiff-bin` using a tolerant threshold and masking or excluding dynamic counters/timestamps.

  This is not a pixel-perfect design system gate. It is a structural smoke gate that keeps the implementation aligned with `/Users/frankqdwang/Documents/工作/seektalent/references/Recruiter Agent _Standalone_.html` plus the added session rail.

- [ ] **Step 7: Wire TanStack Query and EventSource details**

  Implement API clients for:

  - sessions;
  - requirement triage;
  - strong profile seeds;
  - source runs;
  - source-run policies;
  - events;
  - candidates;
  - detail-open requests;
  - recruiter-time-saved metrics;
  - source connection status.

  Native `EventSource` should patch or invalidate TanStack Query state using targeted query keys. Durable endpoints remain the source of truth after reconnect or refresh. Do not globally invalidate the entire app on every event.

## Task 6: Login, Settings, And Liepin Connection UI

- [ ] **Step 1: Write UI/API tests for login and settings**

  Add tests for:

  - bootstrap admin setup screen or setup-disabled state after first admin exists;
  - local SeekTalent login;
  - logout;
  - expired session redirects to login;
  - disabled user session is rejected;
  - account menu;
  - settings route;
  - source settings list;
  - Liepin connection state;
  - Liepin detail-open mode setting, defaulting to human confirmation;
  - bypass-mode warning copy that makes clear only per-candidate confirmation is bypassed;
  - route back from Liepin login to the originating session;
  - remote isolated login relay binds the server-side managed browser context;
  - the login route never exposes CDP URLs, storage state, cookies, worker URL, or Playwright websocket URL;
  - Mac-host-local development fallback is visible in the UI only before M3 and clearly marked as not supporting remote LAN binding.

- [ ] **Step 2: Implement app routes**

  Add TanStack routes:

  - `/setup`;
  - `/login`;
  - `/sessions`;
  - `/sessions/$sessionId`;
  - `/settings`;
  - `/settings/sources`;
  - `/settings/sources/liepin`;
  - `/settings/sources/liepin/detail-open-mode`;
  - `/connections/liepin/$connectionId/login`.

  Keep Liepin login visually and behaviorally separate from the main workbench.

- [ ] **Step 3: Connect Liepin login handoff**

  Use existing Liepin connection/login handoff backend work from the provider plan, but make the LAN handoff explicit:

  - required M3 path: isolated server-side managed-browser login relay;
  - development-only fallback path before M3: Mac-host-local login only.

  The main workbench source card should show whether Liepin is connected, expired, blocked by verification, or missing compliance/budget. It must not imply remote binding works unless the relay path exists and passes tests.

## Task 7: Documentation, QA, And Visual Verification

- [ ] **Step 1: Update user docs**

  Update:

  - `docs/ui.md`;
  - `README.zh-CN.md` if it mentions local UI startup;
  - any CLI help docs if startup commands change.

  Docs must explain:

  - LAN server startup;
  - loopback default, explicit LAN mode, allowed hosts/origins, and startup exposure warnings;
  - enabling/disabling the new workbench feature gate during internal rollout;
  - local admin bootstrap setup;
  - local account login, logout, and session expiry;
  - local data root, file permissions, sync-folder warning, and backup retention;
  - security audit events for sensitive actions and what data is never logged;
  - corpus-backed raw-data behavior, authorized benchmark/debug/manual-review access, future benchmark compatibility, and memory firewall rules;
  - local SourceRun job runner behavior: enqueue, background execution, pause/cancel, restart recovery, and future cloud-runner migration boundary;
  - creating a session;
  - approving or editing requirement triage;
  - adding manual strong-profile seeds;
  - enabling CTS and Liepin sources;
  - Liepin login route;
  - Liepin detail-open approval mode and bypass mode;
  - recruiter-time-saved metrics are estimates;
  - user-visible error states and recovery actions for blocked/failed/paused runs;
  - input limits, safe rendering, and prompt-injection boundaries for pasted recruiter/provider/model text;
  - rollback procedure, including SQLite backup/restore and smoke-test steps;
  - no plugin or user-side Node/Bun install requirement.

- [ ] **Step 2: Run backend verification**

  Commands:

  ```bash
  uv run pytest tests/test_workbench_store.py tests/test_workbench_api.py tests/test_workbench_auth_security.py tests/test_workbench_data_security.py tests/test_workbench_error_rescue.py tests/test_workbench_input_security.py tests/test_workbench_job_runner.py tests/test_workbench_network_exposure.py tests/test_workbench_corpus_access.py tests/test_workbench_redaction.py tests/test_workbench_runtime_bridge.py tests/test_workbench_requirement_triage_eval.py tests/test_workbench_security_audit.py tests/test_ui_api.py tests/test_ui_mapper.py tests/test_liepin_provider_adapter.py tests/test_liepin_verified_loop.py tests/test_liepin_detail_ledger.py tests/test_liepin_detail_integration.py
  uv run pytest
  ```

- [ ] **Step 3: Run frontend and worker verification**

  Commands:

  ```bash
  cd apps/web && bun run test && bun run build
  cd apps/web && bun run test:visual
  cd apps/liepin-worker && bun run test && bun run typecheck && bun run boundary-check
  ```

  Add `test:visual` when implementing the frontend slice; it should run the Playwright layout smoke tests and local `odiff-bin` screenshot comparisons.

- [ ] **Step 4: Browser QA**

  Start backend and frontend locally. Use the Browser plugin or Playwright to verify:

  - login page;
  - loopback-only default startup and explicit LAN-mode startup messaging;
  - unknown Host and unconfigured Origin rejection;
  - bootstrap setup, logout, and expired-session behavior;
  - local data-root warning or pass state in settings/diagnostics if exposed;
  - security audit event creation for login, source connection, detail approval/bypass, and backup actions;
  - corpus raw-data read/export audit, future benchmark provenance checks, and memory-firewall rejection of candidate PII;
  - route guard and CSRF-protected mutating requests;
  - session rail collapse/expand;
  - session creation;
  - source-run start returns quickly while a local job runner emits progress;
  - requirement triage edit and approval;
  - strong-profile seed add/disable/remove;
  - CTS and Liepin source cards;
  - app-level event streaming visible in the strategy panel;
  - refresh recovery through `after_seq`;
  - candidate queue source badges;
  - candidate note/action persistence;
  - user-visible error states for blocked/failed/paused source runs;
  - pasted HTML/script and prompt-injection fixtures render safely and cannot mutate policy;
  - detail-open approval queue default mode and bypass mode;
  - recruiter-time-saved metrics update from real state;
  - settings and Liepin login route navigation;
  - remote isolated Liepin login relay for LAN users;
  - disabling the workbench gate shows the intended fallback or maintenance state;
  - mobile-width and desktop-width layouts do not overlap text;
  - Playwright visual smoke tests pass for desktop and mobile;
  - local `odiff-bin` comparisons pass with the documented threshold and no unmasked structural drift.

  Capture at least one manual screenshot of the main workbench for the review artifact, but treat `bun run test:visual` as the repeatable gate.

- [ ] **Step 5: Verify milestone gates**

  Before internal rollout, confirm the milestone log has evidence for:

  - M0: auth, route guard, LAN guard, session rail shell, and settings entry;
  - M1: real CTS runtime run from the workbench with requirement triage and SSE progress;
  - M2: real CTS candidates in review queue with notes/actions and time-saved metrics;
  - M3: Liepin connection/login state and remote isolated server-side browser login relay;
  - M4: Liepin card-level evidence with no detail opens;
  - M5: detail approval/bypass/ledger/lease/pacing and known-detail action handling;
  - M6: visual parity, LAN QA, rollback, redaction/security audit checks, and docs.

- [ ] **Step 6: Verify rollback path**

  Before marking the phase ready for internal use:

  - create a SQLite backup from the configured workbench database path;
  - record the app version or git commit used for the backup;
  - confirm backup files inherit restrictive permissions and retention metadata;
  - start the server with the workbench gate enabled and run the smoke path;
  - disable the workbench gate and confirm the app does not expose a broken route;
  - restore the SQLite backup in a test copy and confirm login, sessions, source cards, candidates, and detail ledger rows remain readable;
  - document the exact stop/restore/restart/smoke-test commands in `docs/ui.md` or an adjacent runbook.

## Manual Live Verification Gate

Only after the fixture and unit tests pass:

1. Start the backend in default mode and confirm it binds to loopback only.
2. Start the backend in explicit LAN mode and confirm startup prints the LAN URL, allowed hosts/origins, bind address, and HTTP/HTTPS cookie posture.
3. Open the frontend from the same Mac and from another device on the same WiFi.
4. Confirm an unknown Host or unconfigured Origin is rejected.
5. Create the first admin through explicit bootstrap setup; confirm bootstrap cannot create a second default admin.
6. Log into SeekTalent as two users and confirm session isolation.
7. Confirm logout and expired-session behavior reject old cookies.
8. Confirm the data root, SQLite files, artifacts, corpus raw-payload files, benchmark artifacts, backups, and managed browser profile directories are not world-readable or world-writable and are outside repo/sync folders.
9. Confirm security audit rows are written for login/logout, source connection changes, corpus raw-data read/export, corpus export creation or benchmark dataset access, detail approval/bypass, backup/restore, data-root override, and feature-gate changes without secrets.
10. Create one session with CTS only, confirm source-run start returns quickly as a durable job, and confirm the automated CTS parity regression passes against the existing runtime/CLI result contract.
11. Confirm requirement triage appears before source runs and edits affect the run input.
12. Add 3-5 manual strong-profile summaries and confirm extracted attributes appear in strategy/candidate context.
13. Connect Liepin through the isolated login route.
14. Create one session with CTS + Liepin enabled.
15. Confirm one session input creates separate CTS and Liepin source runs while sharing the same approved requirement triage.
16. Confirm source cards update independently.
17. Confirm the strategy panel defaults to an all-sources timeline and can filter or drill down to CTS-only and Liepin-only events.
18. Confirm the candidate queue defaults to merged session results while showing source badges and evidence drilldown.
19. Confirm Liepin card-level search runs before any detail opening.
20. Confirm detail opening defaults to manual approval and rejected requests do not spend budget.
21. Enable bypass mode and confirm it skips only per-candidate confirmation while keeping ledger, budget, lease, pacing, and risk-control checks.
22. Confirm detail opening is sequential and stays under a tiny manual test budget.
23. Confirm a candidate action opens or routes to the known Liepin detail page without consuming extra budget when the detail URL is already known.
24. Confirm recruiter-time-saved metrics update from card review, skipped details, opened details, and candidate decisions.
25. Open multiple workbench tabs and confirm the frontend uses one app-level SSE stream per window rather than one stream per session/source card.
26. Restart the backend during or after a run and confirm old running source runs, stale source-run job leases, and detail leases reconcile to safe states.
27. Simulate one mapped failure for auth, LAN Host/Origin rejection, SQLite write, source-run job lease, SSE reconnect, runtime bridge, Liepin connection, and detail ledger, and confirm each has durable state plus user-visible recovery.
28. Paste HTML/script and prompt-injection fixtures into JD, notes, strong profile seeds, and candidate notes; confirm they render safely and cannot change detail policy or source settings.
29. Disable the workbench feature gate and confirm the operator sees the intended fallback or maintenance state.
30. Restore a copied SQLite backup and confirm sessions, source cards, candidates, and detail ledger rows are readable.
31. Confirm raw resume/profile material is retained only through the corpus raw-payload boundary, ordinary candidate APIs expose only refs/redacted metadata, and memory rows reject candidate PII/raw profile material by default.
32. Confirm no cookie, token, storage state, CDP URL, worker URL, raw provider payload, raw resume/profile content, or auth-bearing provider URL appears in ordinary API responses, SSE events, logs, ordinary artifacts, ordinary backups, diagnostics, or security audit rows.

## Self-Review Checklist

- The plan is source-agnostic and starts with CTS + Liepin.
- The supplied HTML is the UI baseline, with an added session rail.
- Requirement triage, strong-profile seeds, detail-open approval/bypass, and time-saved metrics are explicit implementation tasks.
- Vertical milestone gates M0-M6 prevent the plan from becoming backend-only infrastructure before a recruiter-visible CTS path exists.
- Workbench API and store are scoped by tenant/workspace/user.
- Source-run execution is covered by `tests/test_workbench_job_runner.py` and is not tied to long request handlers.
- Local auth/session security is explicit and covered by `tests/test_workbench_auth_security.py`.
- Corpus-backed raw data, future benchmark compatibility, and memory firewall are covered by `tests/test_workbench_corpus_access.py`.
- LAN exposure is opt-in and covered by `tests/test_workbench_network_exposure.py`.
- Local data-at-rest security is explicit and covered by `tests/test_workbench_data_security.py`.
- Security audit trail is explicit and covered by `tests/test_workbench_security_audit.py`.
- Workbench store includes source-run jobs, source connections, memberships, compliance gates, detail ledger, corpus refs, artifact refs, memory rows, candidate actions, and candidate notes.
- Runtime integration preserves the existing `WorkflowRuntime` path.
- Provider context injection is explicit and not Liepin hard-coded inside generic retrieval.
- Runtime events distinguish source-run completion from session completion.
- SQLite, SSE, cookie auth, CSRF, CORS, and restart recovery are explicit before UI implementation.
- Error/rescue mapping is explicit and covered by `tests/test_workbench_error_rescue.py`.
- Input/rendering/prompt-injection security is explicit and covered by `tests/test_workbench_input_security.py`.
- Workbench rollout has a feature gate, SQLite backup/restore path, and smoke-test rollback steps.
- Liepin detail URL/deeplink support is included without bypassing budget or secret boundaries.
- Liepin detail opening uses a transactional per-connection lease.
- Candidate merge keeps source evidence and prevents false high-confidence merges.
- Central redaction is implemented before route expansion.
- Streaming is FastAPI + `sse-starlette` and frontend state uses TanStack Query.
- Pinpin is reference-only and direct cookie/header replay remains forbidden.
- Frontend verification includes visual QA against the reference structure.

## Eng Review Notes

### NOT In Scope

- Public SaaS deployment: V1 is an internal LAN tool; cloud/domain/HTTPS/Postgres/queue migration is tracked in `TODOS.md`.
- Browser extension or user-side runtime: business users must not install plugins, Node.js, Bun, Playwright, or a local daemon.
- Direct Liepin cookie/header replay: production behavior stays managed browser, page-triggered navigation, passive capture, and DOM fallback.
- Multi-Liepin-account rotation: V1 serializes and budgets a single connection path instead of optimizing account throughput.
- Full ATS/CRM import: strong profile seeds are manually pasted in V1.
- Post-run personalized learning tips: deferred until memory and candidate-feedback boundaries are proven.
- Static benchmark/qrels/search-engine implementation: current work only preserves provenance and raw corpus refs.
- Storybook component catalog: deferred until component shapes stabilize after the workbench is real.

### What Already Exists

- CTS CLI/runtime path already works and remains the correctness baseline.
- Existing `WorkflowRuntime` and retrieval/scoring/reflection/finalization code should be wrapped, not replaced.
- Existing UI API and mapper tests provide a starter pattern for run lifecycle and candidate DTO mapping.
- Existing Liepin provider adapter, provider store, detail attempt rows, and Bun worker provide provider-level behavior beneath the workbench.
- Existing `CorpusStore`, `ArtifactStore`, and benchmark/evaluation code own raw-payload retention and future benchmark materialization boundaries.
- Existing tests around Liepin boundaries, corpus integration, requirements extraction, and CTS provider behavior should be extended rather than duplicated.

### Test Coverage Diagram

```text
CODE PATHS                                             USER FLOWS
[+] Workbench store                                    [+] Internal LAN login/session
  |-- [planned L3] scoped auth/session rows             |-- [planned L3] bootstrap/login/logout/expiry
  |-- [planned L3] source-run/job leases                |-- [planned L3] user/workspace isolation
  |-- [planned L3] detail requests + ledger lease       `-- [planned L3] wrong Host/Origin rejection
  |-- [planned L3] outbox + reconciliation
  `-- [planned L3] indexed keyset reads              [+] Recruiter CTS session
                                                        |-- [planned L3] create session -> approve triage
[+] Runtime bridge                                      |-- [planned L3] CTS durable job + SSE progress
  |-- [planned L3] session planning once                |-- [planned L3] CTS parity regression
  |-- [planned L3] CTS source-run parity                `-- [planned L3] refresh via after_seq
  |-- [planned L3] provider context injection
  `-- [planned L3] source vs session completion      [+] Multi-source session
                                                        |-- [planned L3] CTS + Liepin source cards
[+] Candidate aggregation                               |-- [planned L3] merged timeline with source filter
  |-- [planned L3] evidence-first merge/dedupe          `-- [planned L3] merged queue with source badges
  |-- [planned L3] false merge prevention
  `-- [planned L3] manual split/merge audit          [+] Liepin detail path
                                                        |-- [planned L3] isolated login relay
[+] Frontend workbench                                  |-- [planned L3] card evidence before detail
  |-- [planned L3] route guard + TanStack Query         |-- [planned L3] approval default
  |-- [planned L3] EventSource targeted updates         |-- [planned L3] bypass keeps budget/lease/pacing
  |-- [planned L3] Playwright + odiff visual smoke      `-- [planned L3] known deeplink avoids budget spend
  `-- [planned L3] XSS/prompt-injection fixtures

COVERAGE: 36/36 planned paths covered by named tests or browser QA gates.
QUALITY: planned L3 behavior + edge + error coverage for core paths.
```

### Failure Modes

- Auth/session failure: covered by `tests/test_workbench_auth_security.py`; user sees login/forbidden/not-found instead of silent leakage.
- SQLite lock or constraint failure: covered by store/error-rescue tests; state/event writes roll back atomically and show a storage/conflict error.
- SSE disconnect or malformed resume cursor: covered by route/SSE tests; reconnect uses `Last-Event-ID` or `after_seq`.
- Source-run worker crash or stale lease: covered by job-runner tests; source card becomes recoverable/orphaned/failed instead of hanging forever.
- Liepin login expired or verification required: covered by connection/status tests; source card blocks with an action instead of running blind.
- Detail-open race or budget ambiguity: covered by ledger tests; one active lease per connection and maybe-used is budget-visible.
- Raw resume/profile leakage: covered by corpus/redaction/security-audit tests; ordinary APIs/events/logs/artifacts expose refs only.
- UI layout drift: covered by Playwright + `odiff-bin` smoke tests plus manual screenshot artifact.

Critical silent gaps after review: none.

### Parallelization Strategy

| Lane | Workstream | Modules | Depends on |
|------|------------|---------|------------|
| A | Workbench store, auth, routes, SSE | `src/seektalent_ui/`, `tests/` | none |
| B | Runtime bridge, CTS parity, aggregation | `src/seektalent_ui/`, `src/seektalent/runtime/`, `tests/` | A store contracts |
| C | Frontend workbench UI and visual tests | `apps/web/` | A API contracts |
| D | Liepin login/card/detail integration | `src/seektalent_ui/`, `apps/liepin-worker/`, `src/seektalent/providers/liepin/` | A store/contracts, B provider context |
| E | Docs, rollback, QA runbook | `docs/` | M0-M6 evidence |

Recommended execution: start A first. Once the API/store contract stabilizes, run B and C in parallel. D starts after A plus the provider-context shape from B. E closes each milestone with evidence.

Conflict flags: lanes A, B, and D all touch `src/seektalent_ui/`; use milestone ordering and small router/service modules to reduce merge conflicts.

### Completion Summary

- Step 0 Scope Challenge: scope accepted as full M0-M6.
- Architecture Review: 7 issues found and resolved.
- Code Quality Review: 2 issues found and resolved.
- Test Review: coverage diagram produced; 3 gaps identified and resolved.
- Performance Review: 1 issue found and resolved.
- NOT in scope: written.
- What already exists: written.
- TODOS.md updates: 4 items proposed and accepted.
- Failure modes: 0 critical gaps flagged.
- Outside voice: prior CEO/Codex review incorporated; no new external review run in this pass.
- Parallelization: 5 lanes, 2 parallel after store/API contract, 3 sequential by dependency.
- Lake Score: 13/13 recommendations chose the complete option.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR | Scope decisions accepted before this review |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | - | Not run in this pass |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 13 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | - | Not run |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | - | Not run |

- **UNRESOLVED:** 0
- **VERDICT:** CEO + ENG CLEARED - ready to implement under the M0-M6 milestone gates.
