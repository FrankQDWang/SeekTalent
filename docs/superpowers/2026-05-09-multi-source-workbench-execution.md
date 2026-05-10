# Multi-Source Workbench Execution Log

Plan: `docs/superpowers/plans/2026-05-08-multi-source-recruiter-workbench.md`

## M0

Status: completed.

Completed backend shell:

- Workbench auth/session store with scoped users, sessions, workspace membership, source runs, source cards, login attempts, CSRF, and LAN guard.
- FastAPI `APIRouter` workbench routes wired from `server.py`.
- Loopback-only bootstrap, explicit LAN bind flag/env, Host guard, allowed-Origin CORS, HTTPS-only Secure cookies, and HTTP LAN login support.
- Session and CSRF bearer material stored only as digests.
- Failed-login metadata bounds and temporary lockout.

Backend verification:

- `uv run pytest tests/test_workbench_auth_security.py tests/test_workbench_api.py tests/test_workbench_network_guard.py tests/test_ui_api.py tests/test_liepin_api_scope.py` -> 48 passed.
- `git diff --check` -> passed.
- Superpowers spec compliance review -> passed.
- Superpowers security/code-quality review -> passed.

Completed frontend shell:

- Replaced the old one-run UI entry with a React + TanStack Router/Query workbench shell under `apps/web`.
- Added `/setup`, `/login`, `/sessions`, `/sessions/$sessionId`, `/settings`, and `/settings/sources`.
- Added protected route guard, CSRF capture/retry handling, session rail search/collapse, create-session form, real backend source cards, settings source list, and explicit query error states.
- Kept Storybook deferred.

Frontend verification:

- `cd apps/web && bun run test && bun run build && bun run typecheck` -> passed, 15 tests.
- Frontend spec compliance review -> passed.
- Frontend TypeScript/code-quality review -> passed.
- Local Vite HTTP smoke at `http://127.0.0.1:5176/` -> passed.

Known deferrals:

- Real app-level SSE stream wiring.
- Requirement triage persistence and source-run start gating.
- Candidate review queue with real evidence.
- Liepin managed-browser login relay.
- Playwright and `odiff-bin` visual smoke baseline.

## M1

Status: completed.

Completed backend CTS path:

- Added durable provider-neutral `session_events` storage with global and per-session sequence numbers.
- Added requirement triage update/approval storage and scoped API routes.
- Added source-run start API for CTS, keeping the old compatibility route and adding the planned source-kind route.
- Added local source-run job records, lease/reconcile behavior, and heartbeat renewal while long CTS jobs are active.
- Added `WorkbenchJobRunner` and `WorkbenchRuntimeBridge` to call the existing `WorkflowRuntime` without replacing the CLI path.
- Added app-level event list and SSE routes at `/api/workbench/events` and `/api/workbench/events/stream`.
- Added central redaction before event persistence, SSE/API payloads, bridge errors, and logs.
- Allowed the default loopback Vite dev origins for the default loopback backend bind while keeping LAN origins explicit.

Backend verification:

- `uv run pytest tests/test_workbench_api.py tests/test_workbench_auth_security.py tests/test_workbench_network_guard.py tests/test_ui_api.py -q` -> 51 passed.
- `uv run ruff check src/seektalent_ui tests/test_workbench_api.py tests/test_workbench_network_guard.py` -> passed.
- `git diff --check -- src/seektalent_ui/event_routes.py src/seektalent_ui/job_runner.py src/seektalent_ui/workbench_store.py src/seektalent_ui/redaction.py tests/test_workbench_api.py tests/test_workbench_network_guard.py apps/web/src/app.tsx apps/web/src/app.test.tsx apps/web/src/types.ts apps/web/src/api.ts apps/web/src/styles.css` -> passed.
- Superpowers backend spec compliance review -> passed.
- Superpowers backend quality review -> passed after heartbeat/auth/redaction fixes.

Completed frontend CTS path:

- Added real requirement triage editing and approval in the session workbench.
- Added CTS source card start action gated by approved triage.
- Added one app-level `EventSource` per authenticated browser window.
- Added durable Strategy Timeline backed by `/api/workbench/events`, with source filtering.
- Added logout lifecycle so the app-level stream closes and auth cache is cleared.
- Preserved dirty triage edits during same-session SSE refetches while resetting local drafts when switching sessions.
- Avoided partial fresh event caches when SSE arrives before the timeline query has loaded.

Frontend verification:

- `cd apps/web && bun run test -- --run src/app.test.tsx` -> 26 passed.
- `cd apps/web && bun run typecheck` -> passed.
- `cd apps/web && bun run test && bun run typecheck && bun run build` -> passed, 26 tests.
- Local Playwright smoke at `http://127.0.0.1:5176/setup` -> passed; bootstrap, login, create session, approve triage, and CTS start unlock worked. Screenshot: `/tmp/seektalent-workbench-m1-smoke.png`.
- Superpowers frontend spec compliance review -> passed after generic/custom SSE event subscription and logout lifecycle fixes.
- Superpowers frontend TypeScript/code-quality review -> passed after dirty approval, dirty refetch, event cache, session-switch, and partial-cache fixes.

Known deferrals:

- Candidate review queue still shows shell state; real CTS candidate evidence/review queue is M2.
- Liepin source card remains blocked/login-required; login relay and card-level Liepin evidence are M3/M4.
- Detail-open ledger, human approval loop, and bypass mode are later Liepin slices.
- Playwright/`odiff-bin` visual baseline and Storybook remain deferred until the workbench surface stabilizes.
- The reference HTML is a multi-frame interactive design, not a single static screenshot. Visual parity must cover at least the initial frame, the frame after pressing the lower-left start/play control, and the paused frame after pressing the pause control.

## M2

Status: completed.

Completed backend candidate queue path:

- Added durable `candidate_review_items`, `candidate_evidence`, and `candidate_actions` storage.
- Persisted CTS `FinalResult` candidates into provider-neutral review items and evidence rows.
- Added scoped candidate queue API:
  - `GET /api/workbench/sessions/{session_id}/candidates`
  - `PUT /api/workbench/sessions/{session_id}/candidates/{review_item_id}`
- Kept raw resume payloads, run directories, trace paths, and provider identifiers out of ordinary candidate API/SSE payloads.
- Changed CTS completion so candidate upsert, source-run job completion, source-run state update, and terminal event append happen in one store transaction.
- Made candidate review updates reject empty bodies and avoid duplicate audit/events when a repeated no-op update is submitted.

Completed frontend candidate queue path:

- Replaced the shell queue with real candidate review cards backed by TanStack Query.
- Rendered display identity, aggregate score, source badges, evidence level, must-have matches, risks, and notes.
- Added Mark promising, Reject, and Save note actions with CSRF-backed API calls.
- Wired candidate events to targeted candidate queue invalidation.
- Limited SSE subscription to the generic `workbench_event` channel so backend dual generic/specific events do not double-invalidate.
- Preserved dirty candidate note drafts during same-item SSE refetches.

Review fixes:

- Backend review found provider-derived `resumeId` could leak through ordinary evidence response. Fixed by using internal stable candidate IDs for stored evidence and removing `resumeId` from the candidate evidence API response.
- Backend review found candidate upsert and source-run completion were split across transactions. Fixed with atomic CTS completion in `WorkbenchStore.complete_cts_source_run_with_candidate_results`.
- Frontend review found duplicate SSE handling and dirty note overwrite risk. Fixed both and added regression coverage.

Verification:

- `uv run pytest tests/test_workbench_api.py -q` -> 24 passed.
- `uv run pytest tests/test_workbench_api.py tests/test_workbench_auth_security.py tests/test_workbench_network_guard.py tests/test_ui_api.py -q` -> 53 passed.
- `uv run ruff check src/seektalent_ui tests/test_workbench_api.py tests/test_workbench_auth_security.py tests/test_workbench_network_guard.py tests/test_ui_api.py` -> passed.
- `cd apps/web && bun run test -- --run src/app.test.tsx` -> 29 passed.
- `cd apps/web && bun run test && bun run typecheck && bun run build` -> passed, 29 tests.
- `git diff --check` -> passed.
- Local Playwright smoke at `http://127.0.0.1:5176/` -> passed; login, create session, source cards, timeline, session rail, and empty review queue rendered. Screenshot: `/tmp/seektalent-workbench-m2-smoke.png`.

Known deferrals:

- This is the functional candidate queue slice, not the final visual-parity pass.
- Liepin source card remains blocked/login-required; login relay and card-level Liepin evidence are M3/M4.
- Detail-open ledger, human approval loop, and bypass mode remain later Liepin slices.
- Playwright/`odiff-bin` visual baseline and Storybook remain deferred until the workbench surface stabilizes.
- The reference HTML is a multi-frame interactive design. Visual parity must cover at least the initial frame, the frame after pressing the lower-left start/play control, and the paused frame after pressing the pause control.

## M2.5

Status: completed.

Reason:

- The previous functional UI was directionally useful but visually drifted away from the approved reference HTML.
- This slice corrected the app shell before starting M3 Liepin connection work, so future feature slices land inside the right layout contract instead of requiring a later broad UI rewrite.

Added project context:

- Added `PRODUCT.md` as the short product/personality baseline for Impeccable-driven UI work.
- Added `DESIGN.md` as the reference-aligned layout and visual contract.
- Kept these files as lightweight project context, not as a replacement for the Superpowers spec/plan.

Completed frontend alignment:

- Reframed the authenticated app shell around the reference topology:
  - fixed global top bar
  - collapsible session rail
  - JD/source control panel
  - central strategy canvas
  - right activity/candidate rail
  - fixed bottom playback bar
- Added idle/running/paused playback state so the lower-left control produces distinct frames like the reference.
- Kept the paused frame stateful: pressing pause preserves the strategy graph instead of returning to the ready state.
- Moved source cards into the JD/source column and kept CTS/Liepin visible as source cards.
- Changed the timeline presentation into a central graph-like strategy canvas with source filtering.
- Kept the right rail split between source/session activity log and candidate review queue.
- Made the requirement triage block compact and scrollable so source cards remain visible in the first viewport.

Visual verification:

- Reference screenshots:
  - `/tmp/reference-recruiter-core-1176-initial.png`
  - `/tmp/reference-recruiter-core-1176-running.png`
  - `/tmp/reference-recruiter-core-1176-paused.png`
- Current screenshots:
  - `/tmp/seektalent-workbench-m25-full-initial.png`
  - `/tmp/seektalent-workbench-m25-full-running.png`
  - `/tmp/seektalent-workbench-m25-full-paused.png`
  - `/tmp/seektalent-workbench-m25-core-initial.png`
  - `/tmp/seektalent-workbench-m25-core-running.png`
  - `/tmp/seektalent-workbench-m25-core-paused.png`
- Browser smoke confirmed:
  - session rail at 264px
  - JD/source panel at 304px
  - strategy panel at 512px in the 1440px viewport
  - right rail at 360px
  - bottom playback bar at 48px
  - paused frame still has `.strategy-canvas`
  - CTS and Liepin source cards are visible
- `odiff-bin` structural smoke against the reference core screenshots:
  - initial: `43270;4.09`, exit 22
  - running: `41102;3.88`, exit 22
  - paused: `41017;3.88`, exit 22
- The nonzero odiff exit is expected here because the product content is not a pixel-identical clone; the score is kept as a structural drift signal for future visual passes.

Verification:

- `cd apps/web && bun run test -- --run src/app.test.tsx` -> 29 passed.
- `cd apps/web && bun run typecheck` -> passed.
- `cd apps/web && bun run test && bun run typecheck && bun run build` -> passed, 29 tests.
- `git diff --check` -> passed.

Known deferrals:

- This is the reference-aligned shell pass, not final pixel polish.
- Storybook remains deferred until after the Liepin connection/login surface exists.
- M3 should now continue inside this shell with Liepin connection settings/status and the isolated login route.
- Future UI changes must preserve the reference topology and verify at least initial/running/paused frames.

## M3-M6

Status: M3A in progress.

## M3A

Status: completed.

Completed backend connection shell:

- Added workbench-owned `source_connections` and `connection_status_events` tables.
- Added scoped source connection APIs:
  - `GET /api/workbench/source-connections`
  - `POST /api/workbench/source-connections/liepin`
  - `GET /api/workbench/source-connections/{connection_id}`
  - `POST /api/workbench/source-connections/{connection_id}/login`
- Added a single Liepin connection per tenant/workspace/user in this slice.
- Added durable connection status events and app-level `source_connection_status_changed` events.
- Threaded Liepin connection ID/status/warning fields into session source cards.
- Kept Liepin search blocked; this slice does not start card-level search or detail opens.
- The login response is a safe handoff descriptor only. It does not expose cookies, storage state, auth headers, CDP URLs, Playwright websocket URLs, worker URLs, or raw browser internals.

Completed frontend connection shell:

- Added `/settings/sources/liepin`.
- Added `/connections/liepin/$connectionId/login`.
- Added Liepin connection creation from settings and from the Liepin source card.
- Added an isolated login page separate from the main workbench.
- Added EventSource invalidation for connection status events.
- Source cards now show Liepin connection status when a connection exists.

Important boundary:

- This is M3A, not full M3 completion. The isolated route and durable status state exist, but the actual server-side managed-browser interaction bridge is still pending. The UI labels the current handoff as `relay_pending_worker` instead of implying that remote LAN binding is complete.

Verification:

- `uv run pytest tests/test_workbench_api.py -q` -> 26 passed.
- `uv run pytest tests/test_workbench_api.py tests/test_workbench_auth_security.py tests/test_workbench_network_guard.py tests/test_ui_api.py -q` -> 55 passed.
- `uv run ruff check src/seektalent_ui tests/test_workbench_api.py` -> passed.
- `cd apps/web && bun run test -- --run src/app.test.tsx` -> 31 passed.
- `cd apps/web && bun run test && bun run typecheck && bun run build` -> passed, 31 tests.
- `git diff --check` -> passed.
- Local browser smoke at `http://127.0.0.1:5176/settings/sources/liepin` -> passed; created a Liepin connection, opened `/connections/liepin/{connectionId}/login`, started the safe handoff, saw `relay_pending_worker`, and confirmed the page text did not include forbidden browser-internal terms. Screenshots:
  - `/tmp/seektalent-workbench-m3a-liepin-settings.png`
  - `/tmp/seektalent-workbench-m3a-login-relay.png`

Next gate:

- M3B should connect the actual server-side managed-browser interaction relay, prove LAN binding writes back to the same `source_connection`, and keep the login route free of browser internals.

## M3B Managed Liepin Login Relay Hardening

Status: completed.

Completed:

- Connected the isolated Liepin login route to the server-side managed-browser relay without exposing CDP URLs, storage state, cookies, auth headers, or worker internals to the web UI.
- Tightened the handoff boundary so the API validates source-connection existence and user scope before starting a worker browser relay session.
- Tightened login completion semantics:
  - worker completion must verify a Liepin-domain session-like authenticated cookie before encrypted storage state is persisted;
  - `login_not_verified` is mapped to a safe 409 response;
  - the workbench keeps the connection in `login_in_progress` instead of marking it `connected` when verification fails.
- Clarified the current Liepin source-run start behavior: Liepin is implemented enough to be blocked by connection state, so starting it while disconnected returns `409 liepin_connection_not_connected` instead of the old pre-M4 `501`.

Verification:

- `uv run pytest tests/test_workbench_api.py::test_liepin_login_handoff_rejects_unknown_connection_before_worker_call tests/test_workbench_api.py::test_liepin_login_relay_complete_keeps_connection_unconnected_when_worker_cannot_verify_login tests/test_workbench_api.py::test_source_run_start_by_source_kind_is_idempotent tests/test_liepin_worker_client.py::test_default_http_json_maps_login_not_verified_without_leaking_worker_payload -q` -> passed, 4 tests.
- `cd apps/liepin-worker && bun test tests/server.test.ts -t "login relay completion|authenticated cookie"` -> passed, 2 tests.
- `uv run ruff check src/seektalent_ui src/seektalent/providers/liepin/client.py tests/test_workbench_api.py tests/test_liepin_worker_client.py tests/test_workbench_auth_security.py tests/test_workbench_network_guard.py` -> passed.
- `uv run pytest tests/test_workbench_api.py tests/test_workbench_auth_security.py tests/test_workbench_network_guard.py tests/test_liepin_worker_client.py -q` -> passed, 75 tests.
- `cd apps/liepin-worker && bun run test` -> passed, 63 tests.
- `cd apps/liepin-worker && bun run typecheck` -> passed.
- `cd apps/liepin-worker && bun run boundary-check` -> passed.
- `cd apps/web && bun run test && bun run typecheck` -> passed, 32 frontend tests plus TypeScript typecheck.
- `git diff --check` -> passed.

Remaining boundary:

- The M3B relay proves server-side state binding and safe completion semantics. M4 should still treat Liepin search as connection-gated and should not open detail pages until the detail ledger/human confirmation slice is active.

## M4 Liepin Card-Level Source Run

Status: completed.

Completed:

- Liepin source runs can start from a workbench session after requirement triage is approved and the user's Liepin source connection is connected.
- The local job runner routes Liepin jobs through `run_liepin_card_source_run`, not the CTS runtime path.
- The Liepin runtime bridge builds a summary/card-only `SearchRequest` from the approved session triage and source-run job context.
- Card-level search persists candidate review items and candidate evidence with:
  - `sourceKind=liepin`;
  - `evidenceLevel=card`;
  - source badges preserved in the review queue;
  - provider candidate keys hashed rather than returned to the web API.
- Source cards update from materialized backend state:
  - completed status;
  - scanned card count;
  - unique candidate count;
  - warning state cleared on success.
- Disconnected Liepin source runs fail closed before queueing a job and leave the source card in `blocked/login_required`.
- This slice does not open detail pages. The worker `open_details` path is not called by card-level source runs, and no detail event is emitted by the M4 path.

Verification:

- `uv run pytest tests/test_workbench_api.py::test_liepin_card_level_source_run_persists_card_evidence_without_opening_details tests/test_workbench_api.py::test_source_run_start_requires_approved_triage_and_blocks_liepin -q` -> passed, 2 tests.

Remaining boundary:

- M4 only contributes card-level evidence. M5 owns detail-open ledger rows, human confirmation, bypass mode, sequential leases, and safe provider-detail actions.

## M2.6 Design Package Alignment And Frontend Cleanup

Status: completed.

Completed:

- Read the extracted Recruiter Agent design package in order:
  - `DESIGN.md`
  - `CONTENT_MODEL.md`
  - `ANIMATION.md`
  - `VISUAL_STATES.md`
  - `IMPLEMENTATION_GUIDE.md`
- Removed the old frontend path instead of keeping a compatibility copy:
  - deleted the legacy frontend directory
  - moved the workbench frontend to `apps/web`
  - removed legacy lock/workspace files
  - added `apps/web/bun.lock`
- Kept the intended frontend stack:
  - Bun package manager and script runner
  - React + Vite
  - TanStack Router
  - TanStack Query
- Added a 34-second recruiter playback timeline as a single UI state source:
  - top status and timer
  - bottom phase progress
  - graph node reveal
  - event log reveal
  - source card counters
  - candidate shortlist progression
- Adjusted the left panel back toward the reference information architecture:
  - session rail remains far left
  - JD brief shows hard requirements and bonus tags
  - CTS/Liepin source cards are visible in the first viewport
  - requirement triage remains available after source cards instead of displacing them

Important boundary:

- Business users still only need a browser. They do not install Node.js, Bun, Playwright, or a browser extension. Bun is a developer/runtime dependency for the local server/frontend stack, not a LAN user requirement.

Verification:

- `cd apps/web && bun run typecheck` -> passed.
- `cd apps/web && bun run test` -> passed, 31 tests.
- `cd apps/web && bun run build` -> passed.
- `uv run pytest tests/test_workbench_api.py tests/test_workbench_auth_security.py tests/test_workbench_network_guard.py tests/test_ui_api.py -q` -> passed, 55 tests.
- `uv run ruff check src/seektalent_ui tests/test_workbench_api.py tests/test_workbench_auth_security.py tests/test_workbench_network_guard.py tests/test_ui_api.py` -> passed.
- `git diff --check` -> passed.
- Local browser smoke at `http://127.0.0.1:5176/sessions/{sessionId}` -> passed:
  - status reached `已完成`
  - nodes reached `27 / 27`
  - candidates reached `4 / 4`
  - timeline reached `34.0 / 34s`
  - 2 source cards visible
  - page text did not mention the legacy frontend path
  - screenshots:
    - `/tmp/seektalent-web-animation-idle.png`
    - `/tmp/seektalent-web-animation-08s.png`
    - `/tmp/seektalent-web-animation-20s.png`
    - `/tmp/seektalent-web-animation-34s.png`

## M2.7 UI Parity Tightening

Status: completed.

Completed:

- Rechecked the extracted design package in the required order and reloaded the Impeccable PRODUCT/DESIGN context.
- Compared the reference key frames against the current workbench at idle and completed states.
- Reduced session rail width from 264px to 232px so the added session column does not overpower the reference core layout.
- Kept the reference core proportions closer to the design package:
  - JD/source panel: 304px
  - strategy canvas: flexible, measured at 1040px in the 1920px smoke viewport
  - right rail: 344px
- Removed ordinary `session_created` noise from the idle right rail by feeding the activity log only strategy/workbench events.
- Tightened source cards, graph nodes, and log rows:
  - smaller source cards and actions
  - smaller graph nodes with wrapping details
  - right log text can wrap instead of truncating important events
- Changed the idle right candidate state back to Chinese copy matching the reference style.

Verification:

- `cd apps/web && bun run typecheck` -> passed.
- `cd apps/web && bun run test src/app.test.tsx` -> passed, 31 tests.
- `cd apps/web && bun run build` -> passed.
- `git diff --check` -> passed.
- Local Playwright smoke at `http://127.0.0.1:5176/sessions/{sessionId}` -> passed:
  - session rail width: 232px
  - JD panel width: 304px
  - strategy canvas width: 1040px
  - right rail width: 344px
  - graph nodes reached `27`
  - log rows visible: `8`
  - shortlist reached `4 / 4`
  - screenshots:
    - `/tmp/seektalent-ui-parity-idle.png`
    - `/tmp/seektalent-ui-parity-08s.png`
    - `/tmp/seektalent-ui-parity-20s.png`
    - `/tmp/seektalent-ui-parity-34s.png`
- Added `odiff-bin` to `apps/web` devDependencies and recorded full-frame visual drift baselines:
  - idle: `78594;3.79`
  - 8s: `99405;4.79`
  - 20s: `142129;6.85`
  - 34s: `199305;9.61`
  - diff outputs:
    - `/tmp/seektalent-ui-parity-idle-diff.png`
    - `/tmp/seektalent-ui-parity-08s-diff.png`
    - `/tmp/seektalent-ui-parity-20s-diff.png`
    - `/tmp/seektalent-ui-parity-34s-diff.png`

Known deferrals:

- This is still not final pixel parity. The source card content remains product-specific because V1 has only CTS + Liepin and must expose connection/triage controls.
- M6 should do the full key-frame parity sweep against `frames/00-idle.png`, `frames/06-08s.png`, `frames/09-14s.png`, `frames/12-20s.png`, `frames/16-28s.png`, `frames/17-30s.png`, and `frames/19-34_5s.png`.
- Full-frame `odiff` baselines include expected differences from the added session rail, CTS/Liepin-only source cards, and product-specific controls. M6 should compare both full-frame and reference-core crops before enforcing thresholds.

## M2.8 UI Parity Closure Before Backend Continuation

Status: completed.

Reason for interruption:

- The user explicitly asked to do UI first before continuing M4/M5 backend work.
- The previous visual pass was directionally close, but source cards still felt like backend/debug controls and the topbar still carried too much ordinary admin-button weight.

Completed:

- Kept the current Bun + React + Vite + TanStack frontend in `apps/web`.
- Did not restore `apps/web-user-lite` or pnpm compatibility.
- Reworked source cards from raw enum fields into compact channel cards:
  - localized status labels instead of `queued`, `blocked`, `authState`, or connection enum text;
  - source subtitles aligned with the reference card style;
  - scanning and hit counters still backed by materialized API state when present;
  - source capability chips for local library, sequential review, budget protection, and replay;
  - Liepin no longer shows the generic backend blocking sentence when the user simply needs to connect or continue login.
- Tightened topbar utility actions:
  - neutral brand mark instead of a heavy accent square;
  - subtle source/settings and logout controls;
  - added a small separator so the controls read as background utilities instead of primary actions.
- Fixed a visual runtime bug found during screenshot review:
  - if an older dev backend response lacks the new source counter fields, the frontend now renders `0` instead of `NaN`.

Verification:

- `cd apps/web && bun run typecheck` -> passed.
- `cd apps/web && bun run test` -> passed, 32 tests.
- `cd apps/web && bun run build` -> passed.
- `git diff --check` -> passed.
- Old frontend/package-manager scan (`web-user-lite|pnpm`, excluding historical execution/plan notes) -> clean.
- Local Playwright visual smoke at `http://127.0.0.1:5176/sessions/session_0084b61baadc4073` -> passed:
  - screenshots:
    - `/tmp/seektalent-ui-m28-1920/idle.png`
    - `/tmp/seektalent-ui-m28-1920/08s.png`
    - `/tmp/seektalent-ui-m28-1920/20s.png`
    - `/tmp/seektalent-ui-m28-1920/34s.png`
  - `NaN` source counters were eliminated from the idle frame.
- Updated full-frame `odiff` drift baselines against the extracted reference frames:
  - idle: `77165;3.72`
  - 8s: `92821;4.48`
  - 20s: `140232;6.76`
  - 34s: `197418;9.52`
  - diff outputs:
    - `/tmp/seektalent-ui-m28-1920/diff-idle.png`
    - `/tmp/seektalent-ui-m28-1920/diff-08s.png`
    - `/tmp/seektalent-ui-m28-1920/diff-20s.png`
    - `/tmp/seektalent-ui-m28-1920/diff-34s.png`

Remaining visual truth:

- The surface is now closer to the reference while preserving the product-specific session rail and CTS/Liepin-only V1 source model.
- It is still not a pixel clone, and should not be treated as final M6 visual acceptance. M6 still needs the full key-frame parity sweep and should decide whether to mask the extra session rail or compare a reference-core crop.

## M5 Detail Approval Ledger

Status: completed and merged before M6.

Completed:

- Added Liepin detail-open approval queue with `human_confirm` default and `bypass_confirm` policy control.
- Added workbench-owned `detail_open_requests`, `detail_open_ledger`, per-connection active lease protection, and source-card detail counters.
- Added safe managed-browser provider action descriptors for known detail evidence or already reserved/used ledger rows.
- Fixed the final M5 review gap:
  - approved detail requests now expose an `Open Liepin` action in the UI;
  - candidates with existing Liepin detail evidence no longer show `Request detail`;
  - backend rejects redundant detail-open requests for existing detail evidence with `detail_open_not_required`.

Verification:

- `uv run pytest tests/test_workbench_api.py tests/test_liepin_detail_ledger.py tests/test_liepin_detail_policy.py tests/test_liepin_detail_integration.py tests/test_liepin_verified_loop.py tests/test_liepin_worker_client.py -q` -> passed, 95 tests.
- `uv run ruff check src/seektalent_ui/models.py src/seektalent_ui/workbench_routes.py src/seektalent_ui/workbench_store.py tests/test_workbench_api.py` -> passed.
- `uv run ty check src/seektalent_ui/models.py src/seektalent_ui/workbench_routes.py src/seektalent_ui/workbench_store.py` -> passed.
- `cd apps/web && bun --bun run typecheck && bun --bun run test && bun --bun run build` -> passed, 36 frontend tests.
- `git diff --check` -> passed.

## M6 Visual, QA, Docs, And Rollback Closure

Status: complete for code merge. Real-device LAN and real-provider account checks remain operator rollout gates before business use.

Completed in this slice:

- Added a repeatable visual smoke gate for the stabilized workbench shell:
  - `apps/web/playwright.config.ts`;
  - `apps/web/tests/visual/workbench.visual.spec.ts`;
  - tracked local screenshot baselines under `apps/web/tests/visual/baselines/`.
- Added `cd apps/web && bun run test:visual`.
- The visual smoke uses deterministic mocked workbench API state rather than a live backend or provider login.
- The visual smoke uses tracked current-shell baselines and, when the extracted design package is present locally, compares desktop key frames against the extracted reference frames with a tolerant 8% `odiff-bin` structural threshold. `SEEKTALENT_REFERENCE_FRAME_DIR_REQUIRED=1` turns missing extracted reference frames into a hard failure for local design-parity checks.
- The smoke verifies:
  - desktop shell bounding boxes for topbar, session rail, JD/source panel, strategy panel, right rail, and bottom playback bar;
  - desktop idle, 8s, 14s, 20s, 28s, 30s, and 34s playback frames;
  - mobile shell non-overlap and horizontal overflow;
  - control/chip/link text does not overflow visible controls;
  - screenshot drift with `odiff-bin` against tracked local baselines.
- Updated `docs/ui.md` from the old minimal one-run UI document to the current internal workbench runbook:
  - loopback startup;
  - explicit LAN startup with Host/Origin allowlists;
  - account/session behavior;
  - CTS + Liepin workflow;
  - Liepin login and detail-open approval boundaries;
  - data/privacy boundaries;
  - first-class SQLite backup/verify/restore command and rollback smoke path;
  - backend/frontend/worker verification commands.
- Updated `README.zh-CN.md` so it no longer describes the UI as a minimal thin shell.
- Corrected Task 7 verification commands in the plan to use the actual existing test files instead of stale future-placeholder filenames.
- Added first-class M6 hardening for rollout and rollback:
  - `SEEKTALENT_WORKBENCH_ENABLED` / `--disable-workbench` feature gate for `/api/auth/*` and `/api/workbench/*`;
  - `security_audit_events` store table plus admin-only audit API for implemented sensitive actions;
  - `seektalent-ui-maintenance` backup, verify-backup, and restore commands using SQLite's backup API and restrictive file permissions;
  - backup metadata records schema, app version, git commit when available, retention policy, required workbench tables/columns/indexes, integrity status, and explicit exclusions;
  - restore validates sibling metadata, current canonical workbench column signatures, explicit index DDL, foreign-key integrity, required column-definition fragments, no triggers/views, and workbench read-path smoke checks before creating a verified temporary database;
  - restore quarantines the stopped target database plus SQLite sidecars and restores the original database if post-replace audit recording fails.

Verification:

- `uv run pytest tests/test_workbench_api.py tests/test_workbench_auth_security.py tests/test_workbench_network_guard.py tests/test_liepin_detail_ledger.py tests/test_liepin_detail_policy.py tests/test_liepin_detail_integration.py tests/test_liepin_verified_loop.py tests/test_liepin_worker_client.py -q` -> passed, 120 tests.
- `cd apps/web && bun run test` -> passed, 36 tests.
- `cd apps/web && UPDATE_VISUAL_BASELINES=1 bun run test:visual` -> passed, 2 tests and refreshed tracked baselines.
- `cd apps/web && bun run test:visual` -> passed, 2 tests.
- `cd apps/web && bun --bun run typecheck && bun --bun run test && bun --bun run build && bun --bun run test:visual` -> passed, 36 Vitest tests, production build, and 2 Playwright visual tests.
- `uv run pytest tests/test_workbench_api.py tests/test_workbench_auth_security.py tests/test_workbench_network_guard.py tests/test_ui_api.py tests/test_ui_mapper.py -q` -> passed, 70 tests.
- `uv run pytest tests/test_liepin_api_scope.py tests/test_liepin_boundaries.py tests/test_liepin_compliance_gate.py tests/test_liepin_corpus_integration.py tests/test_liepin_detail_ledger.py tests/test_liepin_detail_policy.py tests/test_liepin_detail_integration.py tests/test_liepin_provider_adapter.py tests/test_liepin_verified_loop.py tests/test_liepin_worker_client.py tests/test_liepin_worker_runtime.py -q` -> passed, 149 tests.
- `cd apps/liepin-worker && bun --bun run test && bun --bun run typecheck && bun --bun run boundary-check` -> passed, 63 Bun tests plus typecheck and boundary check.
- `uv run pytest tests/test_workbench_security_audit.py tests/test_workbench_maintenance.py -q` -> passed, 13 tests with real WorkbenchStore backup/restore fixtures, table/column/schema spoof rejection, column-fragment spoof rejection, trigger rejection, and restore rollback on post-replace audit failure.
- `cd apps/web && UPDATE_VISUAL_BASELINES=1 bun --bun run test:visual` -> passed after refreshing the 1920x1080 desktop baselines and adding 14s/28s/30s key frames.
- `cd apps/web && bun --bun run test:visual` -> passed with local reference-frame comparisons available. Observed reference `odiff` drift was 2.27%-4.94% across idle, 8s, 14s, 20s, 28s, 30s, and 34s, below the 8% gate.
- `uv run ruff check src/seektalent/config.py src/seektalent_ui/server.py src/seektalent_ui/workbench_store.py src/seektalent_ui/workbench_routes.py src/seektalent_ui/models.py src/seektalent_ui/maintenance.py tests/test_workbench_security_audit.py tests/test_workbench_maintenance.py` -> passed.
- `uv run ty check src/seektalent/config.py src/seektalent_ui/server.py src/seektalent_ui/workbench_store.py src/seektalent_ui/workbench_routes.py src/seektalent_ui/models.py src/seektalent_ui/maintenance.py` -> passed.
- `uv run seektalent-ui-maintenance --help` -> passed.
- `uv run pytest` -> passed, 1058 tests.
- `cd apps/web && bun --bun run typecheck && bun --bun run test && bun --bun run build && SEEKTALENT_REFERENCE_FRAME_DIR_REQUIRED=1 bun --bun run test:visual` -> passed.
- `cd apps/liepin-worker && bun --bun run test && bun --bun run typecheck && bun --bun run boundary-check` -> passed.
- `uv run seektalent-ui-maintenance --help` -> passed after metadata/schema validation hardening.
- `git diff --check` -> passed.

Post-merge operator rollout gates:

- Security audit coverage now exists for implemented sensitive workbench and maintenance actions; future user-admin, support-export, and raw-corpus export surfaces still need audit hooks when those surfaces are built.
- Full live LAN QA on another device and real Liepin login remain manual operator verification, not automated in CI.
- Final operator-facing LAN screenshots are manual rollout evidence, while the repeatable Playwright visual gate covers the shell and reference-frame drift for code review.

## M7 Internal Rollout Readiness

Status: completed for code merge. Real-device LAN, real Liepin login, and provider-budget checks remain operator rollout gates before business use.

Completed:

- Added `seektalent-ui-maintenance rollout-readiness`.
- Added `run_rollout_readiness()` to validate:
  - durable workbench database schema;
  - backup creation;
  - backup verification;
  - restore-to-temp behavior;
  - restored workbench read-path smoke;
  - redacted JSON and Markdown readiness evidence under `.seektalent/rollout-readiness/`.
- The command exits `0` when automated checks pass and manual gates remain, with report status `manual_required`.
- The command exits `1` when an automated check fails, such as a missing workbench database.
- Readiness evidence records check names, counts, filenames, and operator guidance only; it does not include cookies, browser storage, auth headers, CDP endpoints, provider payloads, raw resumes, or candidate PII.
- Updated `docs/ui.md` with the M7 readiness runbook.

Manual rollout gates that intentionally remain outside this command:

- real-device LAN access from the intended trusted network;
- real Liepin login through the isolated login flow;
- real provider account budget and detail-open behavior.

Verification:

- `uv run pytest tests/test_workbench_maintenance.py tests/test_workbench_security_audit.py -q` -> passed, 18 tests.
- `uv run ruff check src/seektalent_ui/maintenance.py tests/test_workbench_maintenance.py` -> passed.
- `uv run ty check src/seektalent_ui/maintenance.py` -> passed.
- `uv run seektalent-ui-maintenance --help` -> passed and listed `rollout-readiness`.
- Temporary fixture readiness smoke -> passed:
  - status `manual_required`;
  - 8 checks;
  - manual gates `real_device_lan_access`, `real_liepin_login_relay`, and `provider_budget_detail_behavior`;
  - readiness directory mode `0700`;
  - report file modes `0600`;
  - forbidden redaction probe hits `[]`.
- Worktree readiness smoke:
  - `uv run seektalent-ui-maintenance rollout-readiness --workspace-root .` -> passed with status `manual_required`;
  - report: `.seektalent/rollout-readiness/rollout-readiness-20260510T063319512966Z.json`;
  - markdown: `.seektalent/rollout-readiness/rollout-readiness-20260510T063319512966Z.md`.
- `uv run pytest tests/test_workbench_api.py tests/test_workbench_auth_security.py tests/test_workbench_network_guard.py tests/test_ui_api.py tests/test_ui_mapper.py -q` -> passed, 70 tests.
- `git diff --check` -> passed.
