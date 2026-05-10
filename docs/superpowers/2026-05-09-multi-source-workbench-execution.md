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
