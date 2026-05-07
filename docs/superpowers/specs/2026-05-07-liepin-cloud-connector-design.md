# Liepin Connector Verified Loop Design

## Context

SeekTalent needs a Liepin provider path that can be used from a future web UI or from another department's client through APIs. The user does not want to depend on the other department's Electron codebase and does not want a browser extension. For the experimental product path, the user accepts a managed embedded browser where the recruiter logs into Liepin inside our controlled session.

The current SeekTalent runtime already has:

- a provider adapter contract for retrieval;
- Python scoring, PRF, corpus, flywheel, benchmark, and artifact systems;
- a corpus layer that saves every provider-returned resume snapshot by default.

The Liepin work should add a provider connector, not replace the Python product core.

This design is intentionally larger than a thin adapter. The V1 target is a verified loop: connection, search, extraction, detail-budget use, quality measurement, asset persistence, and replay must all be observable and testable. The loop should prove what happened and why, not merely return candidates.

## Decision

Use a split architecture:

```text
Bun/TypeScript Liepin browser worker
  owns Chromium/Playwright session execution, page navigation, network capture, DOM fallback extraction, detail-page execution

Python SeekTalent core
  owns search planning, detail-open decisions, scoring, PRF, corpus/flywheel writes, artifacts, eval
```

The browser worker is an execution boundary. It must not own business ranking, LLM calls, PRF, corpus policy, or detail-budget policy.

V1 uses managed Chromium through Playwright as the production browser path. Lightweight alternative browsers such as Lightpanda are not part of V1. They may be revisited later for fixture replay or low-risk headless extraction, but they must not be required for login, Liepin search, detail opening, risk-control handling, or acceptance.

V1 uses Bun as the production runner for the TypeScript worker. Performance is a primary product requirement, and the implementation should not silently downgrade the worker to Node.js. If a concrete Playwright/CDP dependency blocks Bun in live smoke, the implementation must stop and surface the blocker rather than changing the runtime decision. Node.js may only be used as an explicit diagnostic comparison path, not as the V1 production baseline.

Because Bun is the production runner, live Liepin execution requires a pre-live Bun compatibility gate. The gate must prove the exact runtime path that production will use:

- launch Playwright Chromium from Bun;
- create and reuse a persistent browser context;
- preserve session state in the protected session store;
- navigate a page and wait for visible workflow state;
- passively capture network responses from page-triggered requests;
- open a detail-like page under a worker command;
- restart after a worker crash without leaking session state;
- run fixture redaction;
- pass `bun test` and TypeScript typecheck.

If any gate fails, implementation stops and reports a Bun blocker. Node.js may be used only for explicit diagnostic comparison, never as an automatic fallback or V1 production runtime.

V1 is a closed-loop connector, not only a browser scraper. A successful run must leave enough evidence to answer:

- did the user connection work;
- what searches were sent to Liepin;
- what card and detail payloads were returned;
- which extraction path was used;
- which candidates were saved;
- which details were opened, skipped, or blocked by budget;
- whether detail opens improved candidate quality;
- where any failure occurred;
- whether extraction and mapping can be replayed without live Liepin access.

The first implementation should be API-first and full-UI-later:

- expose backend APIs for connection, login status, run submission, run events, and result retrieval;
- do not implement the full web UI in this rollout;
- make API and event contracts usable by a TanStack web client from the first implementation;
- keep the API usable by another client without coupling to that client's runtime.

Frontend stack note: the client stack is Vite, TypeScript, and the TanStack family from the start. Use TanStack Router for route ownership, TanStack Query for server-state caching and invalidation, and TanStack Table / Virtual / Form where the UI needs tabular results, large lists, or structured forms. Vitest and jsdom remain the unit-test defaults. If a text-heavy report, readable HTML artifact, or layout-sensitive narrative view needs higher-quality responsive text layout, use Pretext at that surface. Do not make Pretext part of the connector worker or Python business core.

Python API stack note: client-facing APIs should move to an ASGI stack using FastAPI, Uvicorn, and `sse-starlette`. FastAPI owns route declarations, request/response validation, scoped auth dependencies, and OpenAPI-compatible contracts. Uvicorn is the local and deployable ASGI server. `sse-starlette`'s `EventSourceResponse` is the V1 SSE implementation so the connector does not hand-roll long-lived `text/event-stream` framing, pings, disconnect handling, or cancellation behavior on top of the existing stdlib HTTP server.

External and client-facing APIs live in the Python backend. The Bun worker API is internal-only and must not be exposed to external clients. External callers must always pass through Python authorization, tenant/workspace scope checks, compliance gates, corpus policy, artifact policy, and detail-budget policy before any worker command is dispatched.

Python model ownership follows the same trust boundary. `seektalent_ui.models` owns external API request/response contracts only. `seektalent.providers.liepin.models` owns shared Liepin domain enums and value objects only. Python-side worker request/response payloads live in `seektalent.providers.liepin.worker_contracts`, and compliance gate models/checks live in `seektalent.providers.liepin.compliance`. External API routes must translate from store rows and worker DTOs into API responses; they must not directly return worker or storage DTOs.

The user-action contract is intentionally narrow: the only required user action is logging into Liepin inside the managed browser session. Users must not need to install an extension, run a local daemon, export cookies, paste tokens, configure Playwright, or understand the connector worker.

## Why This Is The Performance-Oriented Choice

The dominant latency and cost are not Python versus Bun. The dominant costs are:

- browser startup and session readiness;
- Liepin page navigation and rendering;
- provider throttling and user-action pauses;
- opening candidate detail pages;
- duplicate detail opens across runs.

Performance should therefore come from product policy and execution discipline:

1. Search result cards first.
2. Score and dedupe card-level candidates before opening details.
3. Open details only for candidates selected by Python core.
4. Never reopen a detail page that was already opened for the same account and candidate unless explicitly forced.
5. Reuse the managed browser session.
6. Keep concurrency conservative and account-safe.
7. Treat CAPTCHA, login expiry, and verification as user-action states, not retry loops.

Bun/TypeScript is the V1 worker stack because it is lightweight for web/browser automation, fits the future Vite/TanStack UI stack, and keeps browser-facing code close to the JS ecosystem. The design should keep the worker behind a thin process/API boundary so runtime issues can be isolated, measured, and escalated without moving browser or business logic into Python.

The primary extraction optimization is browser-assisted API extraction: while Chromium drives the authenticated Liepin page, the worker observes network responses and parses provider JSON when it is available. DOM extraction is the fallback, not the first choice.

## Goals

1. Add a Liepin provider connector that can search and extract resume cards through a managed browser session.
2. Make the connector loop verifiable end to end: connection, search, extraction, detail budget, quality, assets, replay.
3. Keep the Python runtime as the authority for query planning, detail-open decisions, scoring, PRF, corpus, and flywheel.
4. Save all provider-returned card/detail snapshots into the existing corpus asset path.
5. Prevent wasted detail opens through a durable per-account detail-open ledger.
6. Provide API boundaries that can support both a future web UI and another department's client.
7. Build a fixture replay harness so network extraction, DOM fallback, provider mapping, and budget policy can be tested without live Liepin access.
8. Avoid browser extensions, local Chrome profile scraping, and dependency on a user's existing local Chrome login state.

## Non-Goals

This design does not implement:

- the Vite/TanStack web UI;
- a browser extension;
- local Chrome profile reuse;
- CAPTCHA bypass or anti-bot evasion as a product feature;
- first-party resume search engine indexing;
- static benchmark pools or qrels;
- personalized memory;
- Lightpanda or another non-Chromium browser as the V1 production path;
- full production dashboards and alerting;
- Node.js as the V1 production worker runtime;
- migration of Python scoring, PRF, corpus, flywheel, or eval logic to TypeScript.

## Compliance Gate

Live Liepin connector runs process recruiter account data and candidate personal information. V1 must fail closed unless the caller has passed an explicit compliance gate.

The compliance gate must record:

- the tenant and workspace;
- the actor or client initiating the run;
- the policy or lawful basis for processing candidate personal information;
- who is the personal-information processor for this run;
- the operator or audit owner responsible for this run;
- confirmation that the Liepin account holder authorized assisted retrieval through this connector;
- confirmation that the run is account-holder initiated human-in-the-loop recruiting work, not bulk data resale, enrichment, or unrelated profiling;
- allowed purposes;
- retention policy;
- deletion SLA;
- deletion path;
- access scope for raw payloads and normalized snapshots;
- whether raw detail payloads may be retained after run debugging;
- whether fixture export is allowed;
- approval or policy reference for other-department or customer API use.

The live connector must not run when this gate is missing or denied. Fixture, CI, benchmark, and replay assets must not contain real candidate-identifying information unless a separate approved redaction policy marks them fixture-safe.

V1 must also provide a first-class way to create and verify compliance gates before live execution. Do not rely on hand-edited SQLite rows or hidden test fixtures for live smoke. The minimum accepted entrypoints are:

- a protected Python API route for creating and reading a scoped compliance gate;
- a manual CLI command that can create or verify a gate for local operator smoke tests.

Both entrypoints must enforce exact `"search"` purpose membership, tenant/workspace scope, actor identity, account-holder authorization, human-initiated recruiting, deletion policy, raw payload access scope, and fixture-export policy before returning a gate reference that a live run can use.

The compliance gate has two live states so account binding does not require a pre-existing provider account hash:

- `pending_account_binding`: the gate has passed tenant/workspace/actor/purpose/policy checks and may be used only to create a managed connection and login handoff;
- `approved`: Python has derived and bound `provider_account_hash` from a worker-observed domain-level account identity hint after login; only this state may start search or detail calls.

`denied` and `expired` gates fail closed. Search, detail opens, corpus writes from live provider payloads, and live smoke must require `approved` plus an exact tenant/workspace/actor/provider-account-hash/purpose match. A pending gate must not authorize search, detail, or fixture export from live data.

## Components

### 1. Liepin Connector API

The backend should expose an API surface that is stable enough for both our future UI and external clients.

Every API request must be authenticated and scoped to `tenant_id` and `workspace_id`. Connections, runs, events, artifacts, and results must belong to the caller's workspace. Results APIs should return selected candidate summaries and artifact references by default; raw provider payload access must require a separate protected asset path and audit entry.

Initial API shape:

```text
POST /api/liepin/connections
  create or reuse a managed Liepin browser connection

GET /api/liepin/connections/{connection_id}
  return login/session/risk-control status

POST /api/liepin/connections/{connection_id}/stream-token
  set a short-lived scoped HttpOnly cookie for browser EventSource subscription

GET /api/liepin/connections/{connection_id}/events
  stream connection status as server-sent events

POST /api/liepin/connections/{connection_id}/login-url
  return a controlled browser view URL or local handoff token for the user to log in

POST /api/runs
  submit a SeekTalent run using provider=liepin

GET /api/runs/{run_id}
  return run status and high-level counters

POST /api/runs/{run_id}/stream-token
  set a short-lived scoped HttpOnly cookie for browser EventSource subscription

GET /api/runs/{run_id}/events
  stream progress events as server-sent events

GET /api/runs/{run_id}/results
  return selected candidates and artifact refs
```

Streaming is part of V1, not a UI add-on. Connection status and run progress must be available as `text/event-stream` server-sent events from Python-owned endpoints. Event payloads must be domain-level JSON objects with stable event names, tenant/workspace scope checks, monotonically increasing sequence numbers where practical, and no raw provider payloads, cookies, storageState, CDP URLs, worker URLs, or auth-bearing provider URLs. A TanStack client can then use the stream to update UI state and invalidate TanStack Query caches for final run/results resources.

The Python implementation should expose these streams through FastAPI routes returning `sse_starlette.EventSourceResponse`. Do not keep the V1 stream path on `BaseHTTPRequestHandler`, `ThreadingHTTPServer`, or a hand-formatted `StreamingResponse`. Browser clients should subscribe with native `EventSource`; because `EventSource` cannot reliably attach arbitrary authorization headers, V1 stream authentication uses a short-lived scoped token set as an HttpOnly cookie after the normal API auth check. The token must not appear in the event-stream URL, JSON response body, artifacts, or log-like diagnostics. Use same-origin requests by default; if a future full UI needs cross-origin credentials, it must explicitly configure CORS and `EventSource(..., { withCredentials: true })` without moving the token into the URL.

The API must not expose raw browser internals. Browser status should be domain-level:

- `logged_out`
- `ready`
- `needs_user_action`
- `risk_control_wait`
- `daily_detail_budget_exhausted`
- `temporarily_rate_limited`
- `failed`

Login sessions must expose domain-level controls, not browser internals:

- `connection_id`
- `browser_view_url` or local handoff token
- `expires_at`
- `status_event_stream`
- allowed origin or client ID
- revoke endpoint

The API must not expose CDP endpoints, remote debugging ports, Playwright debug websocket URLs, raw browser contexts, or arbitrary browser control to external clients.

### 2. Bun/TypeScript Browser Worker

The worker owns:

- launching and reusing managed Chromium/Playwright sessions;
- presenting Liepin login to the user;
- detecting session readiness;
- executing keyword searches;
- capturing authenticated network responses from search and detail pages;
- extracting search result cards from network payloads first and DOM fallback second;
- opening detail pages only when instructed;
- returning raw card/detail payloads plus lightweight diagnostics;
- recording page structure and selector health for harness tests.

The worker does not decide which candidates are worth opening. It receives a detail-open plan from Python and executes it.

The worker should keep its contract small:

```text
search_cards(request) -> card batch + cursor + diagnostics
open_details(request) -> detail payloads + budget/status diagnostics
get_session_status(connection_id) -> status
```

For local V1, Python owns the worker process lifecycle. The default worker mode is managed local: Python starts the Bun worker as a localhost subprocess, waits for `/internal/health`, sends only internal authenticated commands, and shuts the worker down with the API/CLI process. The managed worker runtime is reused within the Python process and must not spawn a new Bun worker per API request or per run. An explicit external HTTP worker mode may be used for diagnostics or later deployment, but it is not the default user path. Users must not need to run a separate worker daemon.

Worker lifecycle failures must become domain events, not raw process errors:

- Bun executable missing or unsupported -> setup failure that names the required Bun install step;
- worker start timeout -> `worker_start_timeout`;
- health check failed -> `worker_unhealthy`;
- worker crashed during a run -> `worker_failed`;
- port unavailable -> choose another localhost port or fail with a clear setup state.

Worker stdout/stderr must pass the same secret guard as artifacts and fixtures before any diagnostic is surfaced.

### 3. Browser-Assisted API Extraction

The worker should prefer network-derived structured data over page DOM scraping.

Execution rule:

1. Navigate through the real authenticated Liepin page in Chromium.
2. Capture network responses triggered by user-session page actions.
3. Identify candidate-card and detail payloads from response shape and request context.
4. Parse those payloads into worker-level card/detail records.
5. Use DOM extraction only when network payloads are absent, incomplete, or encrypted in a way the worker cannot safely decode.

This is not a standalone reverse-engineered API client. The worker must not bypass the managed browser session or replay authenticated requests outside the session policy. Network extraction is allowed because it is attached to the visible browser workflow and preserves the same login, risk-control, and account-safety boundary.

Allowed in V1 production:

- `page.goto`, click, fill, wait, and other visible browser workflow actions;
- passive observation of requests and responses triggered by those page actions;
- parsing response bodies that were triggered by the managed browser workflow;
- DOM fallback on the loaded page when network payloads are missing or incomplete.

Forbidden in V1 production:

- direct Liepin API calls using browser cookies;
- Playwright `page.request`, `browserContext.request`, or equivalent `APIRequestContext` calls to Liepin endpoints;
- replaying captured provider requests outside browser navigation;
- generating provider API signatures, tokens, or encrypted parameters;
- modifying headers to defeat risk control;
- proxy rotation, fingerprint spoofing, stealth plugins, or anti-bot evasion tooling;
- saving raw request headers or auth-bearing URLs into artifacts or fixtures.

Network extraction artifacts should record:

- extractor version;
- endpoint fingerprint, with volatile query params removed;
- response shape hash;
- extraction source: `network` or `dom_fallback`;
- missing-field diagnostics;
- redacted fixture payloads for CI replay.

DOM fallback artifacts should record selector health and enough redacted HTML to repair the extractor.

### 4. Python Liepin Provider Adapter

The Python adapter implements the existing `ProviderAdapter` contract. It calls the connector API or internal worker client and maps Liepin card/detail payloads into `ResumeCandidate` objects.

The adapter should support two phases:

1. `fetch_mode=summary`: search card pages and return card-level candidates.
2. `fetch_mode=detail`: open approved detail pages and return detail-enriched candidates.

The current runtime should remain card-first. Detail fetch is a separate enrichment step, not the default retrieval path.

### 5. Detail Open Policy

Daily detail openings are scarce. Python core owns the policy.

The policy should:

- maintain per-tenant, per-workspace, per-Liepin-account, per-day budgets;
- check whether a provider candidate was already opened before;
- prefer opening candidates with stronger card-level evidence;
- avoid opening duplicate candidates across exploit, PRF, and generic explore lanes;
- stop opening details when budget is exhausted, while allowing the run to continue with card-level evidence;
- emit artifacts explaining which candidates were opened, skipped, or deferred.

The worker only executes the approved plan.

### 6. Detail Open Ledger

Add a durable connector ledger separate from `CorpusStore`.

The ledger owns provider/account operational facts:

- connection ID;
- provider account hash;
- session status;
- daily budget counters;
- detail-open events;
- provider candidate identity;
- skip/open reasons;
- last successful detail-open timestamp;
- user-action or risk-control states.

`CorpusStore` remains the document asset store. It saves card/detail snapshots and observations. The connector ledger answers "should we spend a detail open"; the corpus answers "what documents have we seen and saved."

Detail opens must be transactional and idempotent.

`DetailOpenAttempt` records:

- `attempt_id`
- `idempotency_key`
- `tenant_id`
- `workspace_id`
- `provider_name`
- `account_hash`
- `run_id`
- `query_instance_id`
- `provider_candidate_identity`
- `identity_confidence`
- `approved_by_policy_ref`
- `worker_command_id`
- `state`
- `consumption_state`
- `started_at`
- `completed_at`
- `raw_evidence_ref`

Allowed `state` values:

- `approved_not_started`
- `started`
- `provider_page_loaded`
- `detail_payload_seen`
- `completed`
- `blocked_by_risk_control`
- `failed_before_consumption`
- `failed_after_possible_consumption`
- `unknown`

Allowed `consumption_state` values:

- `not_consumed`
- `consumed`
- `possibly_consumed`
- `unknown`

Rules:

- Python reserves budget before dispatching `open_details`.
- `open_details` must be idempotent by `idempotency_key`.
- duplicate worker responses are applied once.
- worker crash after dispatch marks the attempt `possibly_consumed` unless there is evidence that Liepin did not consume a detail view.
- `possibly_consumed` and `unknown` are treated as consumed until manually reconciled.
- conservative accounting is preferred over accidentally opening the same candidate again.

### 7. Provider Candidate Identity

The connector must not assume every Liepin card exposes a stable person ID.

V1 should track three identity levels:

- `provider_subject_id`: stable Liepin candidate/resume/person identifier when available;
- `provider_listing_id`: listing, card, URL, or detail-page identifier tied to the current result;
- `synthetic_candidate_fingerprint`: conservative fingerprint from visible card fields such as masked name, title, experience, education, location, recent company mask, and provider-specific stable hints.

Each identity record must include `identity_confidence`:

- `stable_provider_id`
- `strong_card_fingerprint`
- `weak_card_fingerprint`

Weak fingerprints must not drive hard duplicate suppression. They may only trigger conservative downgrade, manual review, or a lower priority detail-open decision.

### 8. Corpus Integration

Every Liepin provider-returned card should be saved as a provider snapshot. Detail pages should be saved as richer snapshots or detail observations.

The corpus row must distinguish:

- card-level payload;
- detail-level payload;
- provider name `liepin`;
- query/run/stage provenance;
- raw payload artifact ref;
- normalized text availability;
- whether the snapshot is sufficient for scoring or only for dedupe/search preview.

Raw Liepin payloads remain untrusted external content and must be treated as quoted data when sent to LLMs.

Liepin snapshots must also carry privacy and access metadata:

- `pii_classification`: `card_minimal`, `resume_detail`, `contact_sensitive`, or `unknown`;
- `retention_policy`: `run_debug_short`, `workspace_recruiting_record`, or `forbidden_persist`;
- `access_scope`: `run_only`, `workspace`, or `admin_only`;
- `redaction_state`: `raw_encrypted`, `normalized_redacted`, `fixture_safe`, or `not_fixture_safe`.

V1 stores these Liepin-specific privacy values inside the existing corpus document sensitivity metadata, under `sensitivity_json["liepin_snapshot"]`, while also preserving the existing generic corpus columns such as `retention_policy`, `content_trust_level`, `llm_ingestion_policy`, and `redaction_status`. This avoids a corpus schema migration for V1. If a later design needs these values as queryable first-class columns, that later design must bump `CORPUS_SCHEMA_VERSION` and include migration or recreate instructions.

Raw detail payloads are protected corpus assets. Fixture export must fail closed unless the snapshot is explicitly `fixture_safe`.

## Data Flow

```text
User creates Liepin connection
  -> managed browser session opens
  -> user logs into Liepin
  -> connector reports ready

SeekTalent run starts with provider=liepin
  -> Python builds search requests
  -> worker searches card pages through Chromium
  -> worker extracts cards from network payloads when possible, DOM otherwise
  -> Python saves all card snapshots to CorpusStore
  -> Python scores/dedupes card-level candidates
  -> Python builds detail-open plan under budget
  -> worker opens approved details
  -> worker extracts detail payloads from network responses when possible, DOM otherwise
  -> Python saves detail snapshots
  -> Python continues scoring, PRF, finalization, artifacts, flywheel
```

## Verified Loop Scope

The V1 loop has seven checkpoints. Each checkpoint must emit enough structured status for run events, artifacts, and tests.

### 1. Connection Loop

Purpose: prove that the Liepin account session is usable or explain why it is not.

Outputs:

- connection status;
- provider account hash;
- session age;
- login/user-action state;
- risk-control state;
- last successful search timestamp;
- last successful detail-open timestamp.

Pass condition:

- status reaches `ready`, or a user-action/risk-control state is returned without crashing the run.

### 2. Search Loop

Purpose: prove which searches were executed and what Liepin returned.

Outputs:

- search request ID;
- query instance ID;
- rendered keyword query;
- provider filters;
- page/cursor;
- raw provider card count;
- saved corpus snapshot count;
- browser latency and provider latency where measurable.

Pass condition:

- every provider-returned card is either saved to CorpusStore or has a deterministic save failure reason.

### 3. Extraction Loop

Purpose: prove how candidate data was extracted.

Outputs:

- extraction source: `network` or `dom_fallback`;
- extractor version;
- endpoint fingerprint for network extraction;
- response shape hash;
- selector health for DOM fallback;
- required-field completeness;
- redacted fixture refs.

Pass condition:

- network extraction is used when available;
- DOM fallback works for missing or incomplete network payloads;
- incomplete extraction records missing fields instead of inventing data.

### 4. Detail-Budget Loop

Purpose: prove that scarce detail opens were spent deliberately.

Outputs:

- daily detail budget;
- detail opens available before run;
- candidates considered for detail;
- candidates approved for detail;
- candidates skipped because already opened;
- candidates skipped because low card-level value;
- candidates skipped because budget exhausted;
- opened detail count;
- detail-open failure reasons.

Pass condition:

- no duplicate candidate consumes detail budget twice;
- budget exhaustion degrades to card-level results instead of failing the run.

### 5. Quality Loop

Purpose: prove whether Liepin retrieval helped the search.

Outputs:

- new candidate count;
- duplicate candidate count;
- card-only fit distribution;
- detail-enriched fit distribution;
- detail-open lift where both card and detail scores exist;
- PRF lane contribution;
- generic/exploit lane contribution;
- judge/eval outcome when eval is enabled;
- runtime score outcome when eval is disabled.

Pass condition:

- the run summary can explain whether details improved quality, whether the provider produced useful new candidates, and which lane contributed them.

Card-only and detail-enriched evidence must be scored and stored separately:

- `card_scorecard`
- `detail_scorecard`
- `score_delta`
- `detail_open_reason`
- `detail_open_policy_version`
- `score_evidence_source`: `card_only` or `detail_enriched`

PRF and flywheel metrics must distinguish card-only evidence from detail-enriched evidence. A lane should not receive credit merely because detail budget was spent on candidates from that lane.

### 6. Asset Loop

Purpose: make the run auditable after the browser worker exits.

Outputs:

- corpus raw payload refs;
- normalized card/detail snapshots;
- query-hit rows;
- detail-open ledger rows;
- extraction artifact refs;
- run event stream;
- final run summary.

Pass condition:

- a candidate can be traced from final selection back to Liepin query, provider payload, extraction source, corpus snapshot, and detail-open decision.

### 7. Replay Loop

Purpose: test parser, mapping, policy, and traceability changes without live Liepin access.

Outputs:

- redacted network fixtures;
- redacted DOM fallback fixtures;
- expected card/detail records;
- expected mapping into `ResumeCandidate`;
- expected detail-budget decisions.

Pass condition:

- CI can verify extraction, mapping, and budget policy without a live Liepin account.

Replay does not prove:

- current Liepin search freshness;
- login session availability;
- risk-control behavior;
- whether opening a detail page consumes account quota;
- whether provider network shapes have changed since the fixture was captured.

Fixture export must remove or tokenize:

- names;
- phone, email, WeChat, and other contact handles;
- company/person identifiers unless synthetic;
- resume free text that can identify a person;
- cookies, tokens, auth headers, storageState, CDP URLs, debug websocket URLs;
- exact provider URLs with sensitive IDs unless tokenized.

Fixture export must fail closed when redaction cannot prove the fixture is safe.

## Risk-Control And Account Safety

This connector must not treat Liepin as a generic scraping target.

Rules:

- no CAPTCHA bypass;
- no credential collection outside the managed Liepin login page;
- no use of the user's local Chrome cookies or profile;
- no aggressive concurrency;
- no hidden retry storm when Liepin asks for verification;
- no automatic detail opening beyond the approved budget;
- account-risk statuses must be surfaced to the API and artifacts.

If Liepin requires user action, the run should pause or degrade gracefully instead of failing after a long partial run. For example, card-level results can still be preserved if details cannot be opened.

## Session And Secret Boundary

Liepin login state is a connector secret, not a corpus or flywheel asset.

Rules:

- never ask the user to paste Liepin credentials into SeekTalent;
- only let the user authenticate inside the managed Liepin browser page;
- store session cookies/tokens only in the connector's protected session store;
- do not write cookies, tokens, request headers, or account identifiers into run artifacts;
- store provider account identity only as a stable hash for budget and audit purposes;
- do not include connector secrets in fixture replay files;
- allow explicit session revocation from the API.

SessionStore V1 minimum:

- encrypted at rest;
- scoped by tenant, workspace, connection, and provider account;
- local development store separated from production store;
- no session state in artifacts, corpus, flywheel, logs, traces, fixtures, or benchmark assets;
- no cookies, tokens, auth headers, storageState, CDP URLs, or debug websocket URLs in ordinary run output;
- explicit revoke deletes cookies, storageState, and related connector session records;
- session store key ID may be recorded, but secret material must not be recorded;
- provider account identity uses keyed HMAC, not an unsalted hash.

Provider account identity:

```text
provider_account_hash = HMAC_SHA256(connector_secret, provider_account_stable_id)
```

Plain account IDs, mobile numbers, emails, and login names must not be used as artifact IDs, corpus fields, or metric labels.

The worker may return a domain-level account identity hint after the managed login reaches `ready`. The hint must be sufficient for Python to compute the HMAC but must not be exposed through external API responses, artifacts, logs, fixtures, or stream payloads. Binding that hash transitions a matching `pending_account_binding` gate to `approved`.

## Artifact Boundary

Connector artifacts must use logical names through the existing ArtifactStore boundary, not ad hoc paths.

Minimum logical artifacts:

- `round.XX.retrieval.liepin_connection_status`
- `round.XX.retrieval.liepin_search_requests`
- `round.XX.retrieval.liepin_card_extraction`
- `round.XX.retrieval.liepin_detail_open_plan`
- `round.XX.retrieval.liepin_detail_open_results`
- `round.XX.retrieval.liepin_extraction_fixtures`
- `round.XX.retrieval.liepin_connector_metrics`
- `runtime.liepin_connection_events`
- `assets.provider_snapshots.liepin.cards`
- `assets.provider_snapshots.liepin.details`

Raw payload artifact refs point to protected corpus assets. They are not ordinary JSON dumps and must not be copied into fixture or benchmark artifacts without redaction approval.

## Client Event Streaming Strategy

Use server-sent events for V1 browser-facing streaming:

- `GET /api/liepin/connections/{connection_id}/events` streams connection/login/risk-control status;
- `GET /api/runs/{run_id}/events` streams run lifecycle, search, extraction, detail-budget, quality, and failure states;
- event access is scoped by tenant, workspace, actor, and connection/run ID before the stream starts, using a short-lived HttpOnly stream-token cookie issued after normal API auth;
- every event is safe for browser clients and contains only domain status, counters, artifact refs, and redacted diagnostics;
- streams must degrade to ordinary polling only if the client cannot hold a stream, not because the backend lacks event data.

The server implementation is FastAPI + Uvicorn + `sse-starlette`. Each stream route should return `EventSourceResponse` from an async event source that checks disconnects, emits keepalive pings, and reads domain events from the connector ledger. If multi-process Uvicorn workers are introduced, the event source must not depend on per-process memory.

V1 uses one persisted `liepin_events` ledger for both connection and run streams. Minimum fields:

- `tenant_id`, `workspace_id`, and `actor_id`;
- `subject_type`: `connection` or `run`;
- `subject_id`: connection ID or run ID;
- monotonic `sequence` scoped to `(tenant_id, workspace_id, subject_type, subject_id)`;
- `event_name`;
- `payload_json`, constrained to browser-safe domain JSON;
- `redaction_state`;
- `created_at`.

The stream generator reads committed rows newer than the client's last seen sequence or `Last-Event-ID`, yields SSE `id` values from `sequence`, and then polls in bounded short intervals without holding a long SQLite read transaction or busy-looping when idle. Each read should fetch a limited batch and then resume from the last emitted sequence. This gives reconnect/replay behavior without introducing Redis for V1. Events are append-only; corrections are emitted as later domain events instead of mutating prior rows.

TanStack Query remains the source of cached final resources such as run status, result summaries, and candidate details. The event stream is used for live progress and cache invalidation, not as the only durable data source. The TanStack client should keep the native `EventSource` as a side channel and apply events with `queryClient.setQueryData()` or `queryClient.invalidateQueries()` rather than replacing durable result endpoints with a stream-only state model.

## Performance Strategy

Use these defaults unless testing proves otherwise:

- one managed browser context per active Liepin connection;
- one active page for card search per connection initially;
- detail page openings serialized or very low concurrency;
- browser session reuse across runs;
- network response extraction before DOM extraction;
- fixture replay for parser iteration;
- API event streaming so long-running operations remain observable and TanStack clients can update without polling storms.

Do not optimize by increasing concurrency first. For this provider, high concurrency is likely to hurt account safety and reliability. The faster path is reducing unnecessary detail opens.

Do not optimize V1 by replacing Chromium with a lighter browser engine. Chromium's memory cost is accepted for the login/risk-control path. The implementation should measure browser memory, card-search latency, detail-open latency, network-extraction hit rate, and DOM-fallback rate before revisiting the engine.

## Loop Metrics

Each live run should produce compact connector metrics:

- `connection_ready_latency_ms`
- `browser_rss_mb`
- `card_search_latency_ms`
- `detail_open_latency_ms`
- `raw_card_count`
- `saved_card_snapshot_count`
- `network_extraction_hit_count`
- `dom_fallback_count`
- `extraction_missing_required_field_count`
- `detail_candidates_considered_count`
- `detail_opened_count`
- `detail_skipped_already_opened_count`
- `detail_skipped_low_value_count`
- `detail_skipped_budget_exhausted_count`
- `card_only_fit_count`
- `detail_enriched_fit_count`
- `new_candidate_count`
- `duplicate_candidate_count`

These metrics are not a dashboard requirement. They are the minimum evidence needed for local debugging, benchmark comparison, and future product monitoring.

## Failure Handling

Expected failures should become explicit states:

- login expired -> `needs_user_action`;
- verification required -> `needs_user_action`;
- detail budget exhausted -> continue with card-level results;
- compliance gate missing or denied -> do not start live connector;
- expected network payload missing -> use DOM fallback and record extraction source;
- fixture redaction failed -> do not export fixture;
- page structure changed -> parser health failure with saved fixture;
- temporary rate limit -> stop provider calls and record status;
- worker executable missing, unhealthy, or crashed -> record `worker_failed` or setup status and stop provider calls without leaking process output;
- worker crashed before detail dispatch -> retry command if idempotency permits;
- worker crashed after detail dispatch -> mark detail attempt `possibly_consumed`.

Retries should be bounded and only used where they reduce transient browser flakiness. They must not bypass user-action states or consume detail budget repeatedly.

V1 does not implement full run resumability. It must implement command-level idempotency for search and detail-open commands so partial failures do not corrupt the ledger or spend detail budget twice.

## Testing And Harness

The first implementation plan should include:

1. Contract tests for the Python `LiepinProviderAdapter`.
2. Fixture replay tests for network-response card extraction and detail extraction.
3. DOM fallback tests for missing or incomplete network payloads.
4. Detail-budget tests proving duplicate candidates are not reopened.
5. Detail-open idempotency tests proving uncertain consumption is treated as consumed.
6. Corpus tests proving card and detail payloads are saved as protected provider snapshots.
7. Fixture-redaction tests proving unsafe fixtures fail closed.
8. Quality-loop tests proving detail-enriched candidates can be compared with card-only candidates without merging evidence sources.
9. Traceability tests proving a final candidate links back to query, payload, extraction source, corpus snapshot, and detail-open decision.
10. API scope tests proving connections, events, results, and artifacts are tenant/workspace scoped.
11. Risk-state tests for logged-out, needs-user-action, and budget-exhausted states.
12. Compliance-gate create/verify tests proving live smoke does not depend on direct database seeding.
13. A small live smoke command gated behind explicit compliance and session setup.

The live command should be manual-only. CI should use fixture replay.

## Rollout

Recommended order:

1. Add provider selection config for `cts` vs `liepin`.
2. Add compliance, API scope, session-secret, and artifact logical-name boundaries.
3. Add connector API client and Python adapter with fake worker fixtures.
4. Add connector run status/events and the verified-loop summary shape.
5. Add detail-open ledger, provider candidate identity, and idempotency policy.
6. Add Bun/TypeScript Chromium worker skeleton with fixture replay.
7. Add network-response extraction harness, DOM fallback fixtures, and redaction checks.
8. Add live session login and card search behind compliance gate.
9. Add selective detail opening with low budgets and command idempotency.
10. Add quality-loop summary and traceability checks.
11. Run one JD end-to-end with low budgets.
12. Only after live behavior is stable, expand the TanStack client into the full UI using the V1 API and SSE contracts.

## Acceptance Criteria

- Python remains the source of truth for query planning, scoring, PRF, corpus, flywheel, and detail-open policy.
- Bun/TypeScript worker is the V1 production worker runtime and only executes Chromium browser/session/page operations, network capture, extraction, and detail-open commands.
- Lightpanda or another non-Chromium engine is not required for V1.
- No browser extension is required.
- No local Chrome profile or local user cookies are read.
- The user only needs to log into Liepin in the managed browser session; no extension, local daemon, cookie export, token paste, or runtime configuration is required.
- Live connector runs require a passing compliance gate.
- Compliance gates can be created or verified through first-class Python API or CLI entrypoints before live smoke.
- API requests are authenticated and tenant/workspace scoped.
- Worker extraction prefers authenticated network payloads and uses DOM extraction as fallback.
- No production code path uses Playwright `APIRequestContext` or equivalent direct HTTP client calls to Liepin endpoints.
- No cookies, tokens, auth headers, storageState, CDP URLs, or debug websocket URLs appear in artifacts, logs, traces, or fixtures.
- Login sessions expose controlled browser view or handoff state, not raw CDP/Playwright control.
- All provider-returned Liepin cards are saved to CorpusStore.
- Detail pages are opened only when Python approves them.
- Repeated candidates do not consume detail budget again.
- Detail-open attempts are recorded before worker execution and are idempotent.
- Unknown or possible detail consumption is treated as consumed.
- Budget exhaustion does not fail the whole run.
- Raw detail payloads are protected corpus assets.
- Fixture export fails closed unless redaction policy passes.
- Card-only and detail-enriched scorecards are stored separately.
- PRF and flywheel metrics distinguish card-only evidence from detail-enriched evidence.
- A final candidate can be traced back to query, provider payload, extraction source, corpus snapshot, and detail-open decision.
- Run output can explain connection status, search count, extraction source distribution, detail-open usage, quality outcome, and failure reasons.
- Fixture replay can test extraction without live Liepin access.
- Fixture replay can test mapping and detail-budget policy without live Liepin access.
- API status exposes user-action and account-risk states clearly.
