# Multi-Source Recruiter Workbench Design

## Context

SeekTalent already has a working end-to-end CLI flow for CTS-style sourcing:

```text
JD + optional notes
  -> requirement extraction
  -> search/controller rounds
  -> retrieval provider
  -> scoring
  -> reflection
  -> finalization
  -> artifacts
```

The Liepin connector work adds a controlled Liepin source, not a new product shape by itself. The real product now needed is an internal web application for recruiters on the same LAN: one Mac can run the server, business users open it from the same WiFi, log into SeekTalent, and use a browser UI without installing a plugin, Node.js, Bun, or Playwright.

The UI reference is `/Users/frankqdwang/Documents/工作/seektalent/references/Recruiter Agent _Standalone_.html`. The reference is not a disposable moodboard. Its three-panel agent workbench structure, bottom stage timeline, and source cards are the visual baseline. The missing piece is a far-left session rail, similar to Zed's leftmost session list, because one JD plus optional notes is one SeekTalent session.

The new UI must not be Liepin-only. It is a multi-source recruiter workbench. V1 must support CTS and Liepin as sources behind the same session experience, with room for internal resume DB, ATS collaboration, public information, or other recruiting sources later.

## Decision

Build a source-agnostic recruiter workbench around `WorkbenchSession` and `SourceRun`, not around a single Liepin run.

- `WorkbenchSession`: one JD plus optional notes, owned by one SeekTalent user inside one tenant/workspace.
- `SourceRun`: one provider execution inside a session, initially `cts` and `liepin`.
- `CandidateEvidence`: a candidate observation from one source, with source attribution, card/detail evidence level, scoring evidence source, artifact refs, and provider deeplink when safe.
- `CandidateReviewItem`: the merged and deduplicated candidate shown in the right review queue.

The current Liepin connector remains a provider implementation with special constraints:

- login is required;
- detail opening has a daily budget and must be sequential and human-paced;
- card-level review is cheap and should happen before detail opening;
- the worker may observe authenticated network responses only after the real page triggered them;
- the provider must preserve detail-open ledger and protected snapshot boundaries.

CTS remains a provider without Liepin login or detail-budget behavior. The frontend must therefore render source-specific status inside generic source cards instead of branching the whole product around Liepin.

## CEO Review Scope For This Phase

The first implementation is a UI-first vertical slice over the existing CTS runtime and the existing Liepin connector. It is not a CTS rewrite and not a new Liepin adapter project.

The phase includes four recruiter-workflow levers that must be visible in the workbench and represented in backend state:

- Requirement Triage Gate: before source runs start, the session must expose editable must-haves, nice-to-haves, synonyms, seniority filters, exclusions, and generated query direction.
- Strong Profile Seed Lane: the user may manually paste 3-5 strong profile summaries; SeekTalent extracts shared attributes for search and scoring context. V1 does not auto-import these profiles from external systems.
- Detail-Open Approval Queue: Liepin detail opens default to human-in-the-loop approval. A user-configurable bypass mode may skip per-candidate confirmation, but it must still obey compliance gates, daily budget, per-connection lease, human-paced sequencing, risk-control pause, and visible source-card state.
- Recruiter-Time-Saved Metrics: the UI should estimate reviewed cards, skipped detail opens, opened details, accepted/rejected candidates, and minutes saved. These are directional operator metrics, not payroll or performance accounting.

Post-run learning capsules and personalized education tips are deferred until real session history and candidate feedback exist.

## Rollout And Rollback Posture

The workbench replaces the old one-run UI as the target product surface, but the first internal rollout must still have an operational escape hatch.

V1 needs:

- a local feature gate that can disable the new workbench routes or redirect users to a clear maintenance/fallback screen;
- a startup backup of the local SQLite workbench database before applying new workbench schema changes;
- explicit schema version recording for workbench tables;
- a documented rollback procedure: stop server, restore prior app code, restore SQLite backup when needed, restart server, and run a smoke test;
- a manual gate before making the workbench the default LAN UI.

This does not mean preserving the old UI as a parallel long-term product. It means a broken internal rollout can be safely paused or rolled back without corrupting sessions, source-run state, or Liepin detail ledger accounting.

## Vertical Delivery Gates

The first build must produce usable internal-tool slices, not a large backend shell with no recruiter-facing path.

Implementation should advance through these gates:

- M0: bootstrap admin, login/logout, loopback/LAN startup guard, session rail shell, and settings entry are usable.
- M1: a recruiter can create a JD session, review/edit requirement triage, run the existing CTS path from the workbench, and see SSE progress in the strategy area.
- M2: real CTS candidates appear in the review queue with source badges, notes/actions persistence, and directional time-saved metrics.
- M3: Liepin connection/login status appears in the source card, with either the isolated login relay or explicit Mac-host-local fallback messaging.
- M4: Liepin card-level search contributes candidate evidence without opening detail pages.
- M5: Liepin detail-open approval queue, bypass mode, ledger, lease, pacing, and known-detail action handling are usable.
- M6: visual parity pass, LAN/manual QA, rollback smoke test, redaction/security audit checks, and docs are complete.

Each gate should end with a short demo or verification note. Shared infrastructure is allowed only when it is needed for the current or next gate; avoid building broad hidden layers before the first CTS-backed workbench path is visible.

## SourceRun Job Execution Boundary

Long source runs are durable jobs, not long-lived HTTP requests. The route layer creates or updates source-run jobs and returns; a local worker executes claimed jobs and writes progress through the store/event layer.

V1 should use a SQLite-backed local job runner:

- no Redis, Celery, Temporal, or external queue is required for the single-Mac internal experiment;
- FastAPI `BackgroundTasks` may be used only for trivial fire-and-forget work, not as the primary source-run executor;
- `source_run_jobs` records the durable execution state for CTS and Liepin source runs;
- jobs are claimed through a short SQLite lease with owner, heartbeat, expiry, attempt count, and idempotency key;
- CTS jobs may run with small configured local concurrency;
- Liepin jobs are serialized by `connection_id` and must also respect detail-open ledger leases;
- pause, resume, and cancel are cooperative: routes set durable flags, workers check them at safe checkpoints, and source-run state/events reflect the transition;
- server startup reconciles leased jobs with stale heartbeats into `orphaned`, `paused_recoverable`, or failed/blocked states according to the error map.

The runner boundary must be cloud-migratable later. Future deployment may replace the SQLite local runner with Postgres plus a queue worker, Celery/RQ/Dramatiq, or a durable workflow engine such as Temporal, but the business contract stays the same: source-run state, connection serialization, detail budget, approval decisions, candidate evidence, and audit events remain first-class SeekTalent state, not hidden queue-framework state.

## Error And Rescue Contract

Long-running workbench actions must fail into explicit durable state. They must not leave the UI spinning, source runs permanently `running`, or Liepin detail budget ambiguous.

Every new codepath that can fail needs a mapped outcome:

| Codepath | Failure examples | Durable outcome | User-visible outcome |
|---|---|---|---|
| Auth and scope | missing cookie, expired session, wrong workspace | no mutation, audit/security event where useful | login prompt, forbidden, or not found |
| CSRF-protected mutation | missing/stale CSRF token | no mutation | refresh/retry message |
| LAN exposure/config | unknown `Host`, unapproved `Origin`, accidental public bind, unsafe interface | reject request or fail startup before serving workbench traffic | startup/config warning or forbidden request |
| SQLite store write | locked database, constraint failure, failed event append | bounded retry, then no partial state; state/event update stays atomic | temporary storage error or conflict message |
| SSE stream | client disconnect, malformed `Last-Event-ID` | no mutation from stream route | reconnect or refresh recovery |
| SourceRun job runner | stale lease, worker crash, duplicate claim, cancellation during checkpoint | lease expires or job becomes recoverable; no duplicate Liepin connection execution | paused/recoverable/cancelled/failed source-card state |
| Requirement triage | empty JD, malformed LLM structured output, timeout | triage stays editable; bounded retry only for malformed structured output | editable draft/error state |
| Strong profile seeds | empty seed, too long seed, extraction failure | seed disabled or extraction failed without blocking the session | seed-level warning |
| Runtime bridge | runtime exception, source adapter failure | source run `failed` or `blocked`; session may become `partially_completed` | source card error with next action |
| Liepin connection | login expired, verification required, permission missing | connection/source run `blocked`; connection status event persisted | login or verification call-to-action |
| Detail approval and ledger | budget exhausted, active lease exists, rejected request | request `blocked`/`rejected`; no ledger spend unless lease/open rules say so | pending/blocked/rejected queue state |
| Liepin worker after dispatch | crash or lost response after opening may have occurred | ledger `maybe_used`, no silent refund | budget ambiguity warning |
| Candidate merge | conflicting evidence, low merge confidence | separate review items or auditable manual decision | visible source/evidence ambiguity |
| Rollback and startup | server restart, stale running run, expired detail lease | `orphaned`, `paused_recoverable`, or expired lease | recover/resume message |

Rescued errors must either retry with a bounded policy, transition durable state, or re-raise with scoped context. Catch-all exception handling without a mapped state transition is not acceptable around source runs, detail ledger, SSE event persistence, login handoff, or candidate merge.

## Input, Rendering, And Prompt-Injection Boundary

Recruiter-provided text, provider text, and model output are untrusted data. They may inform search and scoring, but they must not become executable UI, backend instructions, browser automation commands, SQL fragments, shell commands, or provider actions.

Input validation must cover:

- JD text and optional notes: required where a session run needs them, bounded length, normalized newlines, control-character rejection, and explicit empty-state handling;
- requirement triage edits: bounded lists and string lengths for must-haves, nice-to-haves, synonyms, seniority filters, and exclusions;
- strong profile seeds: manual text only, per-seed length limit, count limit, disable/delete state, and extraction failure state;
- candidate notes and actions: scoped to tenant/workspace/user/session, bounded text length, and no HTML/script execution;
- manual query/search fields: bounded length, source-aware validation, and no direct provider URL or request replay input;
- source settings and detail-open policy changes: enum validation and authorization checks before mutation.

Rendering rules:

- render user text, provider text, and LLM output as escaped text by default;
- do not use unsafe HTML insertion for JD, notes, candidate summaries, provider snippets, model explanations, or event payloads;
- if Markdown or Pretext is used for text-heavy reports, run a sanitizer allowlist and reject scripts, event handlers, iframes, auth-bearing links, and dangerous URL schemes;
- provider deeplinks must be safe action descriptors or known-safe URLs, never raw auth-bearing URLs.

Prompt-injection rules:

- JD, notes, provider snippets, strong profile seeds, and candidate resumes are data inside prompts, not instructions for the system;
- model output cannot choose source-run policy, enable bypass mode, approve detail opens, expose raw provider data, or call provider/browser actions;
- structured LLM output must be schema-validated before persistence or display;
- malformed structured output gets only the bounded retry allowed by the repository's LLM policy;
- prompt-injection test fixtures must include text that asks the model to ignore instructions, reveal secrets, open details, change budget policy, or emit HTML/script.

## Visual Product Shape

The main screen is a four-column workbench:

```text
session rail | JD/source panel | strategy timeline/canvas | candidate review queue
```

The design should stay close to the supplied HTML:

- restrained, dense internal-tool layout;
- top chrome with search, run controls, account/settings entry, and source health indicators;
- far-left collapsible session rail with search, recent sessions, status, and create-session action;
- left content panel for JD, notes, must-haves, nice-to-haves, and source cards;
- center panel for agent strategy, query evolution, run events, reasoning checkpoints, and stage progress;
- right panel for candidate queue, fit score, evidence source, source badges, and action buttons;
- bottom stage strip showing search, extraction, scoring, reflection, and finalization states.

The supplied HTML represents one JD/session workbench and does not include session management. Its lower-left cards are source cards, not sessions. The new session rail must be added as an outer far-left column while preserving the HTML's JD/source panel, center strategy panel, right candidate queue, and bottom timeline structure.

The source cards from the HTML are first-class. They should show provider-neutral fields:

- source name and enabled state;
- connection/auth state;
- queued/running/paused/completed/blocked/error state;
- searched queries and scanned card count;
- unique candidates found;
- shortlisted candidates;
- detail opens used, skipped, or blocked where the source has a budget;
- last meaningful event;
- source-specific warning, such as Liepin verification required or CTS config missing.

For V1 the visible source set is:

- CTS;
- Liepin.

Other source cards may exist as disabled placeholders only if they clarify the future layout and do not imply working functionality.

## User And Tenant Model

This is not a SaaS product in V1. It is an internal real business tool.

Even so, V1 requires accounts and tenant isolation because recruiter memory is personal and because source credentials cannot be shared casually.

Minimum model:

- `tenant_id`: local organization boundary;
- `workspace_id`: team or business workspace boundary;
- `user_id` / `actor_id`: human recruiter using the app;
- `connection_id`: provider account binding, such as one Liepin account;
- `session_id`: one JD work session;
- `source_run_id`: one provider run under a session.

V1 default can seed one local tenant and one workspace, but the code must not store all sessions in a global unscoped table. Every session, source run, event, connection, candidate evidence row, and memory row needs tenant/workspace/user scope.

V1 Liepin policy: one SeekTalent user binds one Liepin account. Multi-account rotation is not part of V1.

## Local Auth And Session Security

The app is internal, but it is not anonymous or shared-account software. Local accounts are required because session data, recruiter memory, candidate notes, source connections, and detail-open authority are user-scoped.

V1 auth requirements:

- bootstrap creates a first admin user through an explicit local setup path, not a permanent shared default account;
- passwords are stored only as modern salted password hashes, never plaintext or reversible encryption;
- login issues an HttpOnly scoped session cookie;
- session cookies have expiry and logout invalidates the server-side session;
- successful login rotates the session identifier;
- failed login attempts are rate-limited or temporarily locked per account/IP boundary suitable for LAN use;
- disabled users cannot authenticate or keep using old sessions;
- every request resolves tenant, workspace, user, role, and membership before loading scoped resources;
- sensitive mutations such as source connection changes, detail-open policy changes, approval/bypass decisions, and user administration require an authorized role;
- auth/session events are auditable without logging passwords, session tokens, cookies, or credentials.

If the first implementation uses a bootstrap-only auth mode for a narrow development slice, the UI and docs must label it as not ready for multi-user LAN use. The internal rollout gate cannot be passed until the hardened account/session behavior is implemented and tested.

## LAN Network Exposure Boundary

Same-WiFi access is a deployment mode, not a trust model. V1 must make LAN exposure explicit and bounded.

Default behavior:

- the server binds to loopback only, such as `127.0.0.1`, unless the operator explicitly enables LAN mode;
- LAN mode requires an explicit flag or config value, such as `--lan` or `SEEKTALENT_UI_LAN=1`;
- when LAN mode is enabled, startup prints the exact LAN URL, bind address, configured allowed hosts, configured allowed origins, and whether HTTP or HTTPS is active;
- the app must not silently bind to all interfaces as a development convenience.

Request boundary:

- reject unknown `Host` headers before routing workbench requests;
- allow credentialed CORS only for configured frontend origins;
- keep CSRF protection on all cookie-auth mutating routes regardless of LAN mode;
- do not accept provider callbacks, stream tokens, auth tokens, or CSRF tokens from query parameters as a workaround for cross-origin access;
- if a reverse proxy or HTTPS terminator is used later, trusted proxy headers must be explicitly configured rather than accepted by default.

Startup safety checks:

- warn or fail closed when the configured bind address appears to be public, VPN-only, hotspot/shared, or otherwise outside the intended local network;
- HTTP LAN mode must show that `Secure` cookies are not active and must rely on Host, Origin, CSRF, account auth, and physical network trust;
- HTTPS LAN mode must document local certificate and trust setup;
- public internet exposure is a non-goal for V1 and must require a future deployment/security review.

## Local Data At Rest Security

The app runs on a Mac, but stored data is still sensitive. Web auth does not protect data if SQLite files, artifacts, backups, or managed browser profiles are world-readable or synced into shared folders.

V1 data-at-rest requirements:

- all workbench data lives under one configured local data root outside the git repo and outside common sync folders such as iCloud Drive, Dropbox, Google Drive, and OneDrive unless the operator explicitly overrides with a warning;
- the data root, SQLite directory, artifact directory, corpus raw-payload directory, benchmark directory, backup directory, and managed-browser profile directory are created with owner-only permissions;
- SQLite databases, WAL/SHM files, artifacts, corpus raw-payload files, benchmark artifacts, backups, and browser profile files must not be world-readable;
- startup checks warn or fail closed when required paths are symlinks, world-readable, world-writable, inside the repo, or inside a known sync folder;
- backups inherit restrictive permissions and have a documented retention policy;
- backup/restore commands must not copy browser profiles, cookies, or raw provider session state into ordinary artifacts or support bundles;
- backup/restore may include raw candidate data only through the protected corpus raw-data backup path, with explicit operator intent and restrictive permissions;
- managed browser profiles for Liepin live under a restricted provider profile directory and are treated as credential-bearing data;
- logs and exported diagnostics never include raw SQLite rows, browser profile paths containing secrets, cookies, session tokens, or raw provider payloads;
- FileVault or equivalent full-disk protection is recommended for the Mac host; V1 does not add custom application-level encryption unless a later security review requires it.

## Corpus-Backed Raw Data, Benchmark Governance, And Memory Firewall

SeekTalent should preserve original resume/profile material for benchmark, replay, extraction-quality evaluation, and adapter regression. The rule is not "never store raw resume data." The rule is "raw resume data stays in the existing corpus/raw-payload boundary and never leaks into memory, ordinary UI payloads, logs, or ordinary artifacts."

The existing `CorpusStore` and corpus artifact path are the V1 raw-resume vault for provider-returned resume/profile material. Workbench must reference that boundary instead of creating a second independent raw-vault table or raw-file store.

V1 corpus-backed raw-data requirements:

- original resume/profile material, raw page text, raw provider payloads allowed by the provider boundary, and captured profile snapshots are stored through `CorpusStore`/`ArtifactStore` corpus ingest artifacts or an equivalent corpus-backed facade;
- raw provider payloads live under the restricted data root as corpus raw-payload artifacts with DB refs, content hash, size, tenant/workspace scope, and provider/run/query provenance;
- `candidate_evidence` and `candidate_review_items` may reference `resume_doc_id`, `observation_id`, `subject_id`, and raw `artifact_ref_id` values, but must not inline raw resume/profile payloads;
- ordinary API responses, SSE events, logs, session events, diagnostics, and ordinary artifacts must never include raw resume/profile payloads;
- raw corpus artifact reads require an authorized role, explicit purpose such as `benchmark`, `debugging`, or `manual_review`, and a redacted security audit event;
- support exports and ordinary backups exclude raw corpus content unless the operator explicitly chooses a protected raw-data export path;
- benchmark fixtures must not be copied into the git repo unless they are synthetic or explicitly redacted.

Benchmark governance:

- the existing benchmark method is not redefined by the workbench; future TREC-pooling/static benchmark tables remain owned by the corpus/benchmark boundary, not by `seektalent_ui` session state;
- workbench source runs must preserve enough provenance to feed the future static benchmark and first-party search engine: `jd_doc_id`/task hash, session/source-run/provider, query instance and fingerprint, provider request/page/rank, `resume_doc_id`, `subject_id`, `observation_id`, snapshot hash, evidence level, detail-open ledger state, and human review actions;
- benchmark jobs read raw artifacts through the authorized corpus access path rather than ad hoc file copies;
- benchmark outputs can contain aggregate metrics and redacted excerpts, not unrestricted raw resumes by default;
- future static benchmark manifests, pool versions, qrels, and execution results should reference immutable corpus exports and corpus document/observation IDs without exposing provider credentials, cookies, browser state, or auth-bearing URLs.

Memory firewall:

- `memory_rows` are for recruiter preferences, search strategies, market/role learnings, workflow habits, and user-confirmed high-level lessons;
- raw resumes, full candidate profiles, contact information, identity-rich candidate text, sensitive evaluations, rejection reasons, and provider payloads do not enter memory by default;
- writing candidate-derived learning into memory requires a bounded, redacted abstraction step and user-confirmed or policy-approved memory category;
- memory rows may link to session/source/candidate IDs for provenance only when scoped and authorized, but they must not become a second raw candidate database;
- prompt-injection or model output cannot authorize memory writes containing candidate PII or raw profile material.

## Security Audit Trail

Session events explain product progress. Security audit events explain sensitive operator actions. V1 needs a separate lightweight audit trail so non-session actions are still attributable.

Audit-worthy actions include:

- bootstrap admin creation;
- login, logout, failed login lockout, disabled-user rejection;
- user creation, role changes, workspace membership changes, user disable/enable;
- source connection create/update/delete and Liepin login status changes;
- compliance gate changes;
- detail-open policy changes, manual approvals, rejections, and bypass decisions;
- data-root override, startup permission warnings, backup creation, restore, and support export;
- raw corpus artifact read/export, corpus export creation, and benchmark dataset access;
- feature-gate enable/disable;
- manual candidate merge/split where it affects review identity.

`security_audit_events` should record:

- `audit_event_id`;
- `tenant_id`, nullable `workspace_id`, nullable `session_id`, nullable `source_run_id`;
- `actor_id`;
- `actor_role`;
- `action`;
- `target_type`;
- `target_id`;
- `result`: `succeeded`, `failed`, `blocked`;
- `reason_code`;
- request/IP/device fingerprint where useful and safe for LAN diagnostics;
- redacted metadata;
- `created_at`.

Audit rows must never contain passwords, password hashes, session tokens, CSRF tokens, cookies, auth headers, browser storage state, CDP URLs, Playwright websocket URLs, raw provider payloads, raw browser profile material, or auth-bearing provider URLs.

## Durable Workbench Data Model

The workbench cannot rely on route-level filtering and runtime memory for core state. V1 needs small, explicit SQLite tables for the durable entities below:

- `tenants`
- `workspaces`
- `users`
- `user_sessions`
- `login_attempts`
- `workspace_memberships`
- `sessions`
- `source_runs`
- `source_run_jobs`
- `source_connections`
- `connection_status_events`
- `compliance_gates`
- `detail_open_ledger`
- `session_events`
- `security_audit_events`
- `candidate_evidence`
- `candidate_review_items`
- `candidate_actions`
- `candidate_notes`
- `artifact_refs`
- `memory_rows`
- `external_write_intents`
- `session_requirement_triage`
- `strong_profile_seeds`
- `source_run_policies`
- `detail_open_requests`
- `recruiter_time_metrics`

`workspace_memberships` is required even for an internal tool. "User A cannot read user B's session" is not enough once a team workspace exists. Access checks should resolve tenant, workspace, user, and role before loading sessions, source connections, candidate evidence, memory, or artifacts.

`user_sessions` stores server-side session identity, owning user, expiry, revocation/logout state, issued/rotated timestamps, and last activity. Browser cookies store only scoped session identifiers, not user data or source credentials.

`login_attempts` records bounded login-attempt metadata for LAN-safe throttling and lockout decisions. It must not store passwords or raw session tokens.

`source_connections` is a first-class table. A Liepin `connection_id` must not be a loose field on a run. It represents the bound provider account, connection lifecycle, auth state, verification state, provider account hash when available, and owning user/workspace scope.

`connection_status_events` records recoverable source-card history such as login expired, verification required, permission missing, budget blocked, and healthy. `source_connections` stores the current materialized state; connection events store the audit trail.

Connection status facts are system-owned. Browser-facing workbench routes may read connection state and trigger explicit user actions such as login, refresh, revoke, or disconnect, but they must not expose a generic "write status event" API. Status event writes come from internal store/service functions called by the login relay, source-run runner, provider adapter, and startup reconciliation. If a future external worker callback is required, it must be an internal authenticated endpoint with worker auth and redaction, not a normal user API.

`source_runs` stores materialized UI state instead of forcing the frontend to recompute every source card from the event log:

- `status`: `queued`, `running`, `paused`, `paused_recoverable`, `blocked`, `completed`, `failed`, `cancelled`, `orphaned`
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

`source_run_jobs` stores durable local execution state for source runs:

- `job_id`
- `tenant_id`
- `workspace_id`
- `actor_id`
- `session_id`
- `source_run_id`
- `source_kind`
- nullable `connection_id`
- `status`: `queued`, `leased`, `running`, `pausing`, `paused`, `cancel_requested`, `cancelling`, `cancelled`, `completed`, `failed`, `blocked`, `orphaned`
- `lease_owner`
- `lease_expires_at`
- `heartbeat_at`
- `pause_requested_at`
- `cancel_requested_at`
- `attempt_count`
- `max_attempts`
- `idempotency_key`
- `last_error_code`
- `last_error_message`
- `created_at`
- `started_at`
- `completed_at`
- `updated_at`

Job rows are execution control records. They do not replace `source_runs`; they drive workers that update `source_runs`, `session_events`, candidate evidence, and audit rows through normal store functions.

`sessions` has a separate lifecycle:

- `draft`
- `running`
- `partially_completed`
- `completed`
- `failed`
- `cancelled`

The state machines below are normative. Any transition that changes UI-visible materialized state must write the state change and a redacted event in the same workbench transaction where that state is owned by the workbench. Illegal transitions must be rejected by store functions, not silently accepted by route or worker code.

```text
Session lifecycle

draft
  | triage approved or explicitly accepted
  v
running
  | all enabled sources terminal, at least one blocked/failed and usable results exist
  v
partially_completed
  | aggregator writes final artifacts after enabled sources are terminal or blocked
  v
completed

running -------------- fatal session-level failure ----------> failed
draft/running/partially_completed -- user cancel -----------> cancelled

Terminal: completed, failed, cancelled.
```

```text
Source run and job lifecycle

source_runs:
queued -> running -> completed
                  -> blocked -- explicit retry after fix --> queued
                  -> failed
                  -> cancelled
                  -> paused -- resume --> queued
                  -> orphaned -- startup reconciliation --> paused_recoverable
paused_recoverable -- resume --> queued
paused_recoverable -- cancel --> cancelled

source_run_jobs:
queued -> leased -> running -> completed
                         -> failed
                         -> blocked
                         -> pausing -> paused -- resume --> queued
                         -> cancel_requested -> cancelling -> cancelled
leased/running with stale heartbeat -> orphaned
orphaned -> queued | failed | blocked, only through startup reconciliation or repair.

Terminal job states: completed, failed, blocked, cancelled.
```

```text
Detail-open lifecycle

detail_open_requests:
pending -- user approve --> approved -- acquire lease --> ledger.planned
pending -- user reject  --> rejected
pending -- bypass mode  --> bypassed -- acquire lease --> ledger.planned
pending/approved/bypassed -- gate, budget, lease, or risk block --> blocked

Rejected requests never touch the ledger. Blocked requests can be retried only by
creating or explicitly reopening an auditable request; they cannot silently acquire
a lease later.

detail_open_ledger:
planned -> leased -> opened
planned -> skipped
planned -> blocked
leased  -> failed
leased  -> maybe_used

maybe_used is conservative and budget-visible. It is not silently refunded.
```

```text
External write intent lifecycle

pending -> in_progress -> resolved
pending -> failed -> pending
in_progress with stale heartbeat -> failed
failed -> tombstoned

resolved and tombstoned are terminal. Retries must reuse idempotency keys and
attach existing external refs instead of duplicating corpus rows, provider attempts,
artifact files, or budget consumption.
```

Events are the audit stream. Materialized rows are the current state used by the UI after refresh, reconnect, or server restart. State changes that affect both a materialized row and an event should happen in one short transaction where practical.

`external_write_intents` is the workbench outbox for writes that cross out of the workbench SQLite transaction boundary, such as corpus raw-payload ingestion, artifact file writes, provider child-attempt rows, and future benchmark/corpus export materialization. The workbench must not pretend those writes are covered by the same SQLite transaction as `source_runs`, `session_events`, or `candidate_evidence`.

The workbench is the transaction coordinator for UI-visible state. In one short workbench transaction it should write the materialized state change, redacted event, scoped evidence refs, and any pending external-write intent with an idempotency key. Corpus, provider, and artifact writes then execute idempotently against their own stores. If an external write fails after the workbench transaction commits, the intent remains `pending` or `failed` and startup reconciliation or an explicit repair job retries, marks it terminal, and emits a redacted repair event. If an external write succeeds but the final workbench ref update fails, reconciliation must be able to discover the idempotent external record and attach or tombstone the workbench ref without duplicating raw data or budget consumption.

`session_requirement_triage` stores the current editable requirement split for a session, including must-haves, nice-to-haves, synonyms, seniority filters, exclusions, generated query hints, approval state, and user edits.

`strong_profile_seeds` stores user-pasted strong profile summaries, extracted shared attributes, and whether each seed is active for the current session. It must not imply external CRM/ATS import in V1.

`source_run_policies` stores per-source and per-session controls such as Liepin detail-open mode: `human_confirm` by default, or `bypass_confirm` when explicitly enabled by an authorized user.

`detail_open_requests` is the approval queue before a detail-open ledger lease is acquired. Approved or bypassed requests may try to acquire a ledger lease. Rejected requests must not consume budget.

The workbench owns the canonical detail-open request and lease state. Existing Liepin provider detail-attempt rows remain provider execution evidence: they record worker dispatch, page load, payload observation, and conservative consumption state. They do not replace the workbench approval queue or per-connection active lease.

`recruiter_time_metrics` stores materialized estimates used by the UI: cards reviewed, detail opens skipped, detail opens used, candidates accepted/rejected, and estimated minutes saved. The numbers are product feedback and operator guidance, not a compensation or performance record.

`candidate_evidence` stores corpus references where provider-returned evidence exists: `resume_doc_id`, `observation_id`, `subject_id`, raw `artifact_ref_id`, source kind, provider candidate key hash, content kind, evidence level, schema version, collection time, compliance-gate state, redaction state, allowed uses, and creation actor. Ordinary candidate APIs may expose stable refs and redacted metadata, but not raw resume/profile content.

Workbench does not own static benchmark manifests, pool versions, qrels, or benchmark execution-result tables. When those are implemented, they should live in the corpus/benchmark boundary and reference corpus exports, `jd_doc_id`, `resume_doc_id`, `observation_id`, snapshot hashes, source filters, collection windows, schema versions, redaction state, allowed use, creator, and creation time. They must not inline raw resumes.

## Login And Connection UX

SeekTalent login and Liepin login are separate.

SeekTalent login:

- opens the internal app;
- identifies the user for tenant isolation and memory;
- controls access to sessions, source connections, settings, and results.

Liepin login:

- lives in an isolated route, for example `/settings/sources/liepin` or `/connections/liepin/:connectionId/login`;
- is reachable from the main workbench source card and settings;
- lets the user return to the current session after login;
- does not ask for copied cookies, pasted tokens, extension install, local daemon install, or manual Playwright setup.

The running app may show structured live status from the Liepin browser session. V1 does not need to embed a fully interactive remote browser inside the main workbench. The login page can be visible and controlled; the main workbench should show safe events, counters, and possibly sanitized screenshots later. Exposing CDP, Playwright websocket URLs, raw storage state, or arbitrary browser controls remains forbidden.

## Liepin Login Handoff Decision

V1 uses remote isolated server-side browser login as the primary LAN-compatible handoff.

The business user may open the SeekTalent app from another device on the same WiFi, but Liepin authentication still happens inside a server-side managed browser context owned by the Mac server. The isolated login route relays only safe interaction primitives:

- rendered frames or screenshots without cookies, headers, storage state, CDP endpoints, or worker URLs;
- user input events scoped to the login page;
- connection status events such as `login_required`, `login_in_progress`, `verification_required`, `connected`, `expired`, and `failed`;
- a route back to the originating workbench session.

The route must not expose:

- browser storage state;
- cookies;
- auth headers;
- CDP URLs;
- Playwright websocket URLs;
- worker base URLs;
- arbitrary browser automation APIs;
- direct authenticated HTTP replay against Liepin.

The fallback mode is Mac-host-local login only. If the remote isolated login relay is not implemented in a slice, the plan must explicitly mark Liepin binding as available only from the Mac host. The UI must not imply that a remote LAN user can bind a fresh Liepin account unless the server-side browser login relay exists and is covered by tests.

## Workflow

A recruiter creates a session by pasting JD text and optional notes. SeekTalent extracts must-haves, nice-to-haves, likely synonyms, seniority filters, and initial query strategy.

Before any CTS or Liepin source run starts, the user sees a Requirement Triage Gate. The user can accept or edit the split. Source runs should use the approved triage state, not a hidden one-off extraction.

The user may optionally paste 3-5 strong profile summaries into the Strong Profile Seed Lane. The system extracts shared attributes and feeds them into query generation, seniority filtering, scoring context, and the candidate comparison UI. This is manual paste only in V1.

The user can enable CTS and Liepin source cards before running. One approved JD/notes/triage input creates one `WorkbenchSession`; each enabled source creates its own `SourceRun` under that session. Source runs may execute concurrently only where the source constraints allow it, while sharing the same session-level planning state and preserving source-specific constraints:

- CTS can retrieve normally through the existing provider path.
- Liepin must check connection status, compliance gate, account budget, and detail-open ledger before opening details.
- Liepin should first review card-level summaries, then open details only for candidates that pass threshold.
- Liepin detail opening must be sequential and human-paced. No concurrent detail-open burst.
- Liepin detail opening enters the approval queue by default. Bypass mode can auto-approve eligible requests, but it cannot bypass ledger, budget, per-connection lease, or risk-control blocks.

The center strategy panel should show the agent's actual work:

- requirement split;
- query generation and cleanup;
- source-specific query attempts;
- false-positive review;
- strong-profile cloning or reverse-engineered attributes when available;
- detail-open approval or bypass decisions;
- estimated time-saved updates;
- reflection and next query decision;
- finalization.

The center strategy panel defaults to a merged session timeline so recruiters can follow the actual run without switching between sources. Every event remains source-attributed. The UI must provide a source filter or equivalent drilldown for CTS-only, Liepin-only, and all-sources views. This keeps the session readable while preserving provider-specific debugging and audit trails.

The right candidate queue defaults to merged cross-source results without hiding attribution. A candidate card should show source badges, card/detail evidence level, why it matched, missing risks, and actions:

- open candidate in provider;
- mark promising;
- reject;
- add note;
- copy/share summary;
- compare to strong profile.

For Liepin, `open candidate in provider` should prefer the already known or already opened detail URL when available so the product does not waste detail-open budget by re-opening unknown routes.

## Candidate Evidence And Merge Rules

Candidate review is evidence-first. A merged review row must never hide which source produced which observation.

`candidate_evidence` minimum fields:

- `source_kind`
- `source_run_id`
- `source_candidate_id` or `provider_candidate_key`
- `evidence_level`: `card`, `detail`, `artifact`, `inferred`
- `provider_deeplink_kind`: `known_safe`, `managed_browser_only`, `unavailable`
- observed name, company, title, location, education, seniority, and recent activity where available;
- normalized comparison fields;
- `artifact_ref_ids`
- `score_components`
- `missing_risks`
- extraction source and score evidence source;
- raw provider payload reference only through protected artifact or corpus refs, never inline public JSON.

`candidate_review_items` minimum fields:

- `review_item_id`
- `merge_confidence`
- `primary_display_identity`
- `evidence_ids`
- source badges derived from evidence;
- aggregate score derived from evidence, not manually overwritten;
- manual split and manual merge markers, even if the V1 UI hides the controls.

Required merge behavior:

- same name and same company is not enough to auto-merge people;
- the same person from different sources should merge without losing source badges or evidence level;
- Liepin detail evidence may append evidence or raise confidence, but it must not overwrite CTS evidence;
- manual split/merge decisions must be auditable as candidate actions.

## Liepin Detail Ledger Rules

Liepin detail opening is a strong transactional budget ledger, not a normal source-run counter.

`detail_open_ledger` is workbench-owned and tracks:

- `tenant_id`
- `workspace_id`
- `actor_id`
- `connection_id`
- `source_run_id`
- `candidate_evidence_id`
- `provider_candidate_key`
- `detail_url_hash` or managed safe route key
- `status`: `planned`, `leased`, `opened`, `skipped`, `blocked`, `failed`, `maybe_used`
- `opened_at`
- `budget_day`
- `idempotency_key`
- `lease_expires_at`
- optional provider child-attempt refs, such as `liepin_detail_attempt_id`

At most one active detail-open lease may exist per `connection_id`. Sequential opening must be enforced in backend state, not only by `await` calls in worker code.

The detail approval queue is separate from the ledger. A request can be `pending`, `approved`, `rejected`, `bypassed`, `blocked`, or `expired`. Only `approved` and `bypassed` requests may attempt to acquire a ledger lease, and the backend may still block them if compliance, budget, connection state, or risk-control checks fail.

Rendering or opening a known provider deeplink action does not itself consume detail budget. Budget is consumed only when the managed browser actually causes Liepin to open an unknown detail page or when the system cannot prove whether a dispatched open consumed a view. If a detail URL might contain auth-bearing or session-bearing material, the frontend receives a managed-browser action descriptor, not the raw URL.

## Runtime Integration

Do not fork the agent workflow for the UI.

The workbench backend should wrap the existing `WorkflowRuntime` instead of replacing it. The current CTS CLI behavior is the correctness baseline. UI runs should eventually produce the same core artifacts and candidate reasoning as CLI runs.

V1 implementation shape has three semantic layers:

1. Session-level planning: JD and notes produce requirement extraction, must-have/nice-to-have separation, and a strategy seed.
2. Source-run execution: each source receives provider context and executes source-specific query, retrieval, card/detail extraction, and source evidence scoring.
3. Aggregation-level finalization: candidates are merged and deduplicated, cross-source scoring/reflection runs, session artifacts are finalized, and the session lifecycle advances.

If the first implementation temporarily reuses the complete existing `WorkflowRuntime` per source run, the bridge must still translate completion semantics correctly. A source run may emit `source_run_completed`; it must not emit `session_completed`. The session can emit `session_completed` only after the aggregator determines that every enabled source is `completed`, `blocked`, `failed`, or `cancelled` and final session artifacts have been written.

The UI subscribes to an app-level event stream and reads durable resources through normal API endpoints. Event payloads carry `session_id` and `source_run_id` so the frontend can filter the current view without opening one EventSource per tab or per session.

The immediate backend gap is that the existing UI server has separate local run handling and Liepin run placeholders. It needs a durable workbench layer that can drive CTS and Liepin through one source-run abstraction.

The immediate provider gap is provider context injection. Liepin already requires `liepin_tenant_id`, `liepin_workspace_id`, `liepin_actor_id`, `liepin_connection_id`, compliance gate reference, detail budget, and detail candidate metadata. The runtime bridge must be able to pass those values into retrieval without hard-coding them in generic retrieval logic. CTS should pass an empty or minimal provider context.

## API And Streaming

Client-facing backend stack:

- FastAPI;
- Uvicorn;
- `sse-starlette` `EventSourceResponse`.

Frontend streaming:

- browser native `EventSource`;
- TanStack Query remains the cache for durable session, source, result, and candidate resources;
- SSE updates live state and invalidates or patches TanStack Query data.

The event stream is app-level, while each event is session-scoped and source-aware. Event names should be provider-neutral:

- `session_created`;
- `requirement_triage_started`;
- `requirement_triage_updated`;
- `requirement_triage_approved`;
- `strong_profile_seed_added`;
- `strong_profile_attributes_extracted`;
- `source_status_changed`;
- `source_search_started`;
- `source_candidates_found`;
- `source_detail_budget_changed`;
- `detail_open_requested`;
- `detail_open_approved`;
- `detail_open_rejected`;
- `detail_open_bypassed`;
- `recruiter_time_metrics_updated`;
- `candidate_scored`;
- `candidate_merged`;
- `strategy_event_added`;
- `source_run_completed`;
- `source_run_failed`;
- `session_partially_completed`;
- `session_completed`;
- `session_failed`.

Event payloads must include tenant/workspace/session/source scope and monotonically increasing sequence IDs. Event payloads must not include raw provider payloads, cookies, storage state, auth headers, CDP URLs, debug websocket URLs, or auth-bearing provider URLs.

Because browser `EventSource` cannot attach arbitrary auth headers, stream auth should use a scoped HttpOnly cookie pattern. Cookie auth requires explicit web security rules:

- all mutating routes require CSRF protection, such as signed double-submit CSRF tokens;
- SSE routes are read-only `GET` routes and must have no side effects;
- the frontend and API may run on different local ports in dev, so CORS with credentials must be explicit and tested;
- unknown `Host` headers and unconfigured credentialed origins are rejected;
- LAN serving is opt-in and startup must not silently bind the workbench to all interfaces;
- `SameSite=Lax` is the default for same-origin local use;
- `Secure` cookies require HTTPS. If the local LAN deployment is HTTP-only, the deployment must not pretend `Secure` is active. If HTTPS is enabled, docs must explain certificate/trust setup;
- stream tokens must not appear in URLs, JSON bodies, logs, or artifacts.

`Last-Event-ID` is used for browser reconnect. The API must also support durable refresh recovery through a normal endpoint such as `GET /api/workbench/events?after_seq=...`, because page reloads and app restarts should not rely only on browser-managed SSE reconnect behavior.

SQLite must be configured for the workbench event/store workload:

- `PRAGMA journal_mode=WAL`
- `PRAGMA busy_timeout=...`
- `PRAGMA foreign_keys=ON`
- short write transactions;
- no waiting inside a long read transaction in the SSE loop;
- event append and materialized state update are atomic where required;
- concurrent source-run event writes use a single writer queue or a bounded retry policy;
- orphaned `running` source runs and expired detail leases are reconciled on server startup.

SSE implementation rules:

- use one app-level stream per browser window where possible, not one stream per session or per source;
- yield structured events with `id`, `event`, and JSON `data`;
- create DB sessions inside the generator loop, not outside it for the lifetime of the stream;
- check `request.is_disconnected()`;
- configure send timeouts and keepalive pings;
- never hold a SQLite transaction while sleeping or waiting for new events.

## Redaction Boundary

Secret and sensitive-data redaction must be centralized. Do not rely on every route, mapper, logger, and event writer to remember the same list.

V1 should provide shared redaction helpers for:

- API response serialization;
- SSE event payloads;
- session event persistence;
- artifact writing;
- logging;
- worker contract mapping;
- exception serialization.

The redaction test corpus must include at least:

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

## Frontend Stack

Use the TanStack family from the beginning:

- Vite + TypeScript;
- TanStack Router for route ownership;
- TanStack Query for server-state and invalidation;
- TanStack Table where candidate/result tables need real table behavior;
- TanStack Virtual for long candidate or event lists;
- TanStack Form for JD/session/settings/source forms where it reduces state noise.

Use Pretext only where it clearly helps text-heavy, layout-sensitive surfaces such as readable candidate reports, final briefings, or transcript-like narrative panes. Do not put Pretext into provider workers or Python business logic.

The old one-run web UI is not the target surface. The only frontend app for this workbench is `apps/web`; do not preserve a compatibility copy or parallel legacy directory.

## Pinpin Reference Boundary

Pinpin is a useful source of product and platform mapping evidence. It is already a mature multi-source recruiting product and its local extension source is readable on this machine.

Use Pinpin for:

- source taxonomy and multi-source UI comparison;
- Liepin card fields and detail URL shape;
- search-version and endpoint-family awareness;
- login, verification, pagination, no-permission, and missing-payload state ideas;
- fixture design for redacted network and DOM examples.

Do not copy Pinpin source code. Do not adopt its direct cookie/header replay model as SeekTalent production behavior. SeekTalent keeps the already chosen boundary: managed browser login, page-triggered navigation, passive network capture, DOM fallback, no `APIRequestContext`, no extension dependency, no cookie export.

Detailed notes live in `docs/references/pinpin-liepin-mapping-notes.md`.

Pinpin-derived fixtures must be redacted, synthetic where needed, and stored under `docs/references` or test fixture directories with provenance notes. No production code may import Pinpin extension modules or reuse its request replay path.

## Non-Goals

- Public SaaS packaging.
- Internet-exposed deployment.
- Multi-Liepin-account rotation.
- Browser extension.
- User-side Node.js, Bun, Playwright, or local daemon setup.
- Fully embedded remote browser control inside the main workbench.
- Replacing the existing CLI runtime.
- Personal memory tips or recruiter education nudges.
- Full ATS/CRM.
- Bulk concurrent Liepin detail opening.

## Acceptance Criteria

- A local LAN user can log into SeekTalent and only see their scoped sessions.
- Local auth has a bootstrap admin setup path, hashed passwords, expiring server-side sessions, logout/revocation, session rotation on login, and failed-login throttling.
- LAN serving is opt-in, rejects unknown hosts/origins, and shows explicit startup warnings for HTTP, bind address, and network exposure.
- Workbench SQLite files, artifacts, backups, and managed browser profiles live under a restricted local data root with startup permission checks and documented backup retention.
- Original resume/profile data can be retained for benchmark only through the existing `CorpusStore`/corpus raw-payload boundary with scoped access and audit logging.
- A session rail exists and one session maps to one JD plus optional notes.
- Every started session exposes an editable Requirement Triage Gate before source runs spend search effort.
- Users can manually add strong-profile seed summaries and see extracted shared attributes used as search/scoring context.
- The main workbench visually follows the supplied HTML with an added collapsible session rail.
- CTS and Liepin appear as sources under the same session model.
- Source cards update from real backend source-run state, not hard-coded frontend mock state.
- Source cards can recover current state from `source_runs` materialized columns after refresh or server restart.
- Source runs execute through a durable local job runner with leases, heartbeats, cooperative pause/cancel, and restart reconciliation.
- The UI can start a session run, subscribe to SSE, and show live source and strategy events.
- SSE uses one app-level stream with durable `after_seq` recovery, not one stream per visible session card.
- Cookie-auth mutating routes have CSRF protection and tested CORS/credential behavior for local dev.
- CTS source runs use the existing runtime path.
- Liepin source runs enforce connection, compliance, provider context, detail budget, and sequential detail-open policy.
- Liepin remote LAN login binds to the server-side managed browser context through an isolated login route, or the UI explicitly falls back to Mac-host-local login only.
- Liepin detail open uses a transactional ledger and per-connection lease.
- Liepin detail opens default to a human approval queue, with a configurable bypass mode that still obeys ledger, budget, lease, pacing, and risk-control state.
- The UI shows directional recruiter-time-saved metrics based on real source-run, candidate, and ledger state.
- Candidate results merge across sources while preserving source attribution and evidence level.
- Candidate merge tests prevent same-name false merges and prevent Liepin detail evidence from overwriting CTS evidence.
- Liepin candidate actions can open a known provider detail URL without wasting detail budget when an existing URL is available.
- Running source runs can be paused, resumed, cancelled, or reconciled as orphaned after server restart.
- The workbench is delivered through M0-M6 vertical gates, with a real CTS-backed web path visible by M1 rather than waiting for every infrastructure task to finish.
- The new workbench has a documented local feature gate and rollback path, including SQLite backup/restore and smoke-test steps.
- Major failure paths have an explicit error/rescue map with durable state transitions and user-visible messages.
- The V1 job runner is lightweight for local experimentation but keeps a cloud-migratable boundary for later queue or durable workflow execution.
- User input, provider text, and model output are validated, escaped, schema-checked, and prompt-injection tested before use in UI, artifacts, or runtime decisions.
- Sensitive actions have redacted `security_audit_events` covering actor, scope, target, action, result, and reason.
- Memory rows cannot store raw resumes, contact information, identity-rich candidate text, sensitive candidate evaluations, or raw provider payloads by default.
- Future benchmark datasets have manifests and read raw artifacts through controlled corpus access, not ad hoc file copies into the repo.
- No ordinary production API response outside the authorized corpus raw-data read path, event, log, or ordinary artifact exposes cookies, auth headers, storage state, CDP URLs, worker URLs, raw provider payloads, raw resume/profile content, or auth-bearing provider URLs.

## Self-Review

- The UI is multi-source, not Liepin-only.
- The source cards from the reference HTML are explicitly part of the product contract.
- The accepted CEO scope is in the executable product contract: requirement triage, strong profile seeds, detail approval controls, and time-saved metrics.
- Delivery is milestone-gated so UI, CTS runtime reuse, candidate review, Liepin login, Liepin card search, and detail approval become usable in order.
- The session rail requirement is explicit.
- The CTS working CLI/runtime path remains the correctness baseline.
- Liepin's daily detail-view constraint is preserved.
- Liepin login handoff is now LAN-coherent and server-side-browser specific.
- Store entities cover source connections, compliance gates, detail ledger, memberships, artifacts, memory, and candidate actions.
- Source-run execution is durable job-driven, not tied to long HTTP requests or FastAPI `BackgroundTasks`.
- Local accounts and server-side sessions are hardened enough for multi-user LAN use; no permanent shared default account is accepted.
- LAN exposure is bounded by explicit bind mode, allowed hosts/origins, startup checks, and documented HTTP/HTTPS behavior.
- Local data-at-rest handling protects SQLite, artifacts, backups, and Liepin browser profiles from world-readable paths and accidental sync-folder leakage.
- Raw resume/profile retention is supported through the existing corpus raw-payload boundary for benchmark while ordinary UI/API/memory surfaces stay redacted.
- Runtime events distinguish `source_run_completed` from `session_completed`.
- SSE, SQLite, cookie auth, CORS, CSRF, and redaction boundaries are explicit.
- Rollout can be paused or rolled back through a documented feature gate and SQLite backup/restore path.
- Error handling maps failure to durable state, source-card status, event payload, and user-visible recovery.
- Input validation, rendering safety, and prompt-injection boundaries are explicit for JD, notes, strong profile seeds, candidate notes, provider snippets, and LLM output.
- Security audit events cover auth, user administration, source connections, detail approvals/bypass, backup/restore, data-root overrides, feature gates, and sensitive candidate merge/split decisions.
- Pinpin is used as a mapping reference, not as copied production code.
- FastAPI, Uvicorn, `sse-starlette`, TanStack, streaming, and optional Pretext decisions are preserved.
