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

The first implementation should be API-first and UI-later:

- expose backend APIs for connection, login status, run submission, run events, and result retrieval;
- do not implement the web UI in this rollout;
- keep the worker usable by a future Vite + TanStack web UI;
- keep the API usable by another client without coupling to that client's runtime.

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

Bun/TypeScript is still the preferred worker stack because it is lightweight for web/browser automation, fits the future Vite/TanStack UI stack, and keeps browser-facing code close to the JS ecosystem. The design should keep a thin process boundary so the same worker code can be run under Node if a specific Playwright dependency is not stable under Bun.

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
- migration of Python scoring, PRF, corpus, flywheel, or eval logic to TypeScript.

## Components

### 1. Liepin Connector API

The backend should expose an API surface that is stable enough for both our future UI and external clients.

Initial API shape:

```text
POST /liepin/connections
  create or reuse a managed Liepin browser connection

GET /liepin/connections/{connection_id}
  return login/session/risk-control status

POST /liepin/connections/{connection_id}/login-url
  return a URL or session handle for the user to log in

POST /runs
  submit a SeekTalent run using provider=liepin

GET /runs/{run_id}
  return run status and high-level counters

GET /runs/{run_id}/events
  stream progress events

GET /runs/{run_id}/results
  return selected candidates and artifact refs
```

The API must not expose raw browser internals. Browser status should be domain-level:

- `logged_out`
- `ready`
- `needs_user_action`
- `risk_control_wait`
- `daily_detail_budget_exhausted`
- `temporarily_rate_limited`
- `failed`

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

### 3. Browser-Assisted API Extraction

The worker should prefer network-derived structured data over page DOM scraping.

Execution rule:

1. Navigate through the real authenticated Liepin page in Chromium.
2. Capture network responses triggered by user-session page actions.
3. Identify candidate-card and detail payloads from response shape and request context.
4. Parse those payloads into worker-level card/detail records.
5. Use DOM extraction only when network payloads are absent, incomplete, or encrypted in a way the worker cannot safely decode.

This is not a standalone reverse-engineered API client. The worker must not bypass the managed browser session or replay authenticated requests outside the session policy. Network extraction is allowed because it is attached to the visible browser workflow and preserves the same login, risk-control, and account-safety boundary.

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

### 7. Corpus Integration

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

Purpose: test parser and mapping changes without live Liepin access.

Outputs:

- redacted network fixtures;
- redacted DOM fallback fixtures;
- expected card/detail records;
- expected mapping into `ResumeCandidate`;
- expected detail-budget decisions.

Pass condition:

- CI can verify extraction, mapping, and budget policy without a live Liepin account.

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

## Performance Strategy

Use these defaults unless testing proves otherwise:

- one managed browser context per active Liepin connection;
- one active page for card search per connection initially;
- detail page openings serialized or very low concurrency;
- browser session reuse across runs;
- network response extraction before DOM extraction;
- fixture replay for parser iteration;
- API event streaming so long-running operations remain observable.

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
- expected network payload missing -> use DOM fallback and record extraction source;
- page structure changed -> parser health failure with saved fixture;
- temporary rate limit -> stop provider calls and record status;
- worker crashed -> run can resume from saved corpus/ledger/artifacts in a future resumability rollout.

Retries should be bounded and only used where they reduce transient browser flakiness. They must not bypass user-action states or consume detail budget repeatedly.

## Testing And Harness

The first implementation plan should include:

1. Contract tests for the Python `LiepinProviderAdapter`.
2. Fixture replay tests for network-response card extraction and detail extraction.
3. DOM fallback tests for missing or incomplete network payloads.
4. Detail-budget tests proving duplicate candidates are not reopened.
5. Corpus tests proving card and detail payloads are saved as provider snapshots.
6. Quality-loop tests proving detail-enriched candidates can be compared with card-only candidates.
7. Traceability tests proving a final candidate links back to query, payload, extraction source, corpus snapshot, and detail-open decision.
8. Risk-state tests for logged-out, needs-user-action, and budget-exhausted states.
9. A small live smoke command gated behind explicit local credentials/session setup.

The live command should be manual-only. CI should use fixture replay.

## Rollout

Recommended order:

1. Add provider selection config for `cts` vs `liepin`.
2. Add connector API client and Python adapter with fake worker fixtures.
3. Add connector run status/events and the verified-loop summary shape.
4. Add detail-open ledger and policy.
5. Add Bun/TypeScript Chromium worker skeleton with fixture replay.
6. Add network-response extraction harness and DOM fallback fixtures.
7. Add live session login and card search.
8. Add selective detail opening.
9. Add quality-loop summary and traceability checks.
10. Run one JD end-to-end with low budgets.
11. Only after live behavior is stable, design the Vite/TanStack UI.

## Acceptance Criteria

- Python remains the source of truth for query planning, scoring, PRF, corpus, flywheel, and detail-open policy.
- Bun/TypeScript worker only executes Chromium browser/session/page operations, network capture, extraction, and detail-open commands.
- Lightpanda or another non-Chromium engine is not required for V1.
- No browser extension is required.
- No local Chrome profile or local user cookies are read.
- Worker extraction prefers authenticated network payloads and uses DOM extraction as fallback.
- All provider-returned Liepin cards are saved to CorpusStore.
- Detail pages are opened only when Python approves them.
- Repeated candidates do not consume detail budget again.
- Budget exhaustion does not fail the whole run.
- A final candidate can be traced back to query, provider payload, extraction source, corpus snapshot, and detail-open decision.
- Run output can explain connection status, search count, extraction source distribution, detail-open usage, quality outcome, and failure reasons.
- Fixture replay can test extraction without live Liepin access.
- Fixture replay can test mapping and detail-budget policy without live Liepin access.
- API status exposes user-action and account-risk states clearly.
