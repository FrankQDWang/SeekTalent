# Liepin Connector Verified Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a verified Liepin provider loop where users only log into Liepin while SeekTalent handles authenticated search, passive network extraction, detail-budget control, protected corpus persistence, replay, and quality traceability.

**Architecture:** Python remains the business authority for API scope, compliance, query planning, detail-open policy, scoring, corpus/flywheel writes, and artifacts. Bun/TypeScript is the V1 production worker runtime and owns only managed Chromium/Playwright browser execution, passive network capture, DOM fallback extraction, and detail-page execution. The worker is internal-only; all client-facing API calls go through Python.

**Tech Stack:** Python 3.12, Pydantic, SQLite, FastAPI, Uvicorn, `sse-starlette`, existing ArtifactStore/CorpusStore/FlywheelStore, Bun, TypeScript, Playwright Chromium, Vite, TanStack Router, TanStack Query, TanStack Table/Form/Virtual where needed, Pretext for text-heavy or layout-sensitive UI/report surfaces where needed, pytest, FastAPI TestClient or httpx ASGITransport, Bun test, Vitest, jsdom.

**Frontend Stack Note:** This rollout does not build the full web UI, but the client stack decision is not deferred. API and event contracts must be ready for a Vite + TanStack client from the first implementation. Use native `EventSource` for server-sent events, TanStack Query for cached final resources and invalidation, and keep the Bun worker internal-only.

---

## Scope Notes

This plan implements the V1 connector loop from `docs/superpowers/specs/2026-05-07-liepin-cloud-connector-design.md`.

This plan does not build the full TanStack UI, static benchmark qrels, personalized memory, Lightpanda, a browser extension, local Chrome profile reuse, or a generic website automation platform.

The user-facing contract is strict: the only required user action is logging into Liepin inside the managed browser session.

## Hard Constraints

- Bun/TypeScript is the V1 production worker runtime. Node.js may be used only as explicit diagnostic comparison and never as a fallback.
- Live Liepin search/detail calls must fail closed unless an approved compliance gate exists for the tenant, workspace, actor, provider account hash, and purpose. A pending gate may only create a connection and login handoff until Python binds the post-login provider account hash.
- The Bun worker API is internal-only. External clients must not reach CDP, Playwright, remote debugging ports, worker endpoints, storage state, or arbitrary browser controls.
- Fake worker mode must be explicit and test/fixture-only. `provider_name="liepin"` must never silently return fake candidates.
- Raw Liepin provider payloads must not be placed in `ResumeCandidate.raw`, run results, ordinary debug artifacts, fixtures, or logs. Raw payloads go through protected corpus snapshots and artifact refs.
- `page.request`, `browserContext.request`, `APIRequestContext`, replayed authenticated requests, provider signature generation, stealth plugins, proxy rotation, and header manipulation are forbidden in V1 production.
- Detail opens are scarce and must be recorded as idempotent per-day budget transactions before the worker opens a detail page.
- Card-only and detail-enriched scorecards are different evidence conditions and must remain separated in scoring, query-hit metadata, flywheel outcomes, and metrics.
- Worker dependencies must be reproducible: commit the generated `apps/liepin-worker/bun.lock`, use `bun ci` when a lockfile exists, and install Playwright Chromium with `bunx playwright install chromium` before compatibility and live smoke gates. Missing browser binaries are a setup failure, not a reason to fall back to Node.js.
- Client-facing Python API must use FastAPI + Uvicorn. SSE endpoints must use `sse-starlette` `EventSourceResponse`; do not keep V1 streaming on `BaseHTTPRequestHandler`, `ThreadingHTTPServer`, or hand-formatted long-lived stdlib responses.
- Live run and connection progress must be streamable through Python-owned `text/event-stream` server-sent events. Do not design the client around polling-first progress.
- TanStack is the default client family from the first UI-facing contract. Do not introduce an ad hoc frontend state or routing stack for Liepin.
- Pretext is allowed only for UI/report/text-layout surfaces that benefit from responsive text layout. It is not part of the connector worker, provider adapter, compliance gate, or detail-budget core.

## Boundary Diagrams

```text
External client / future TanStack UI
        |
        v
FastAPI routes + seektalent_ui.models
        |
        | auth, tenant/workspace/actor scope, compliance, corpus policy,
        | artifact policy, detail budget, API response translation
        v
Python Liepin provider boundary
        |
        | internal worker_contracts.py DTOs only
        v
Bun worker on localhost
        |
        | managed Chromium, passive network capture, DOM fallback
        v
Liepin web session

Forbidden outward leaks:
worker URL, CDP URL, storageState, cookies, auth headers, raw provider payload,
raw account identity hint, store row DTOs, worker DTOs.
```

```text
Compliance gate state

create
  |
  v
pending_account_binding -- login handoff only --> managed login ready
  |                                                   |
  | bind scoped connection's account hint internally  |
  v                                                   v
approved --------------------------------------> search/detail allowed

denied / expired / pending_account_binding -> no search, no detail, no live fixture export
wrong tenant/workspace/actor/account hash  -> no search, no detail
```

```text
SSE event flow

domain event producer
  -> liepin_events append_event(sequence=N, safe domain payload)
  -> EventSourceResponse reads committed rows after Last-Event-ID
  -> browser EventSource receives id=N
  -> TanStack Query cache update/invalidate
  -> durable result endpoints remain source of final truth
```

## File Structure

### Python API and provider boundary

- Modify `pyproject.toml` and `uv.lock`
  - Add FastAPI, Uvicorn, and `sse-starlette` as runtime dependencies. Keep the dependency addition narrow; do not add a broader web framework stack.
- Modify `src/seektalent_ui/models.py`
  - Add external API request/response models for Liepin connections, login handoff, scoped API context, run submission with `provider="liepin"`, SSE event rows, and result summaries. Do not expose worker or store DTOs here.
- Modify `src/seektalent_ui/server.py`
  - Replace the stdlib `BaseHTTPRequestHandler`/`ThreadingHTTPServer` surface with a FastAPI ASGI app factory, Uvicorn `main()`, authenticated tenant/workspace scoped endpoints for compliance gates, Liepin connections, login handoff, run submission, `sse-starlette` server-sent events, and results.
- Create `src/seektalent/providers/liepin/__init__.py`
  - Export `LiepinProviderAdapter`.
- Create `src/seektalent/providers/liepin/models.py`
  - Shared Liepin domain enums and value objects only: connection status, candidate identity, protected snapshot metadata, detail attempt status, and score evidence source.
- Create `src/seektalent/providers/liepin/compliance.py`
  - Compliance gate model plus `allows_connection_handoff()` and `allows_live_search()` checks.
- Create `src/seektalent/providers/liepin/worker_contracts.py`
  - Python-side internal worker request/response contracts for card search, detail open, session status, login handoff, and redacted diagnostics.
- Create `src/seektalent/providers/liepin/security.py`
  - HMAC account hash, structured secret guards, artifact redaction guards, and storage-state leak checks.
- Create `src/seektalent/providers/liepin/store.py`
  - SQLite connector ledger for compliance gates, unified connection/run events, session metadata, and detail-open attempts.
- Create `src/seektalent/providers/liepin/session_store.py`
  - Python-facing protected session metadata and revoke operations. The encrypted browser state bytes remain worker-owned.
- Create `src/seektalent/providers/liepin/worker_runtime.py`
  - Python-managed local Bun worker subprocess lifecycle, health checks, port selection, crash handling, and redacted diagnostics.
- Create `src/seektalent/providers/liepin/client.py`
  - Explicit fake-fixture, managed-local, and external-HTTP worker clients.
- Create `src/seektalent/providers/liepin/mapper.py`
  - Map protected worker card/detail payload metadata into `ResumeCandidate` without embedding raw provider payloads.
- Create `src/seektalent/providers/liepin/policy.py`
  - Detail-open planning, per-day budget checks, identity confidence rules, and idempotency keys.
- Create `src/seektalent/providers/liepin/adapter.py`
  - Implement `ProviderAdapter` with compliance/session enforcement and explicit worker mode.
- Create `src/seektalent/providers/liepin/verified_loop.py`
  - Build connector metrics, traceability rows, and artifact payloads.

### Existing Python integration points

- Modify `src/seektalent/config.py`
  - Add provider selection, Liepin worker mode, optional external worker URL, connector DB path, session key ID, API token, and budget settings.
- Modify `src/seektalent/default.env`
  - Add commented Liepin connector settings with Chinese comments.
- Modify `src/seektalent/providers/registry.py`
  - Select CTS or Liepin adapter from settings. Do not instantiate a fake worker unless settings explicitly request fixture mode.
- Modify `src/seektalent/core/retrieval/provider_contract.py`
  - Add `ProviderSnapshot` and `SearchResult.provider_snapshots` so raw provider payloads can flow to protected corpus storage without using `ResumeCandidate.raw`.
- Modify `src/seektalent/runtime/retrieval_runtime.py`
  - Include provider name in canonical query specs and record all provider-returned snapshots from `SearchResult.provider_snapshots`.
- Modify `src/seektalent/artifacts/registry.py`
  - Register Liepin logical artifacts and keep corpus kind resolution guarded.
- Modify `src/seektalent/corpus/runtime.py`
  - Prefer explicit `ProviderSnapshot` raw payloads over `candidate.raw` for Liepin; preserve Liepin privacy metadata.
- Modify `src/seektalent/corpus/documents.py`
  - Add optional protected snapshot privacy metadata for Liepin card/detail payloads under `sensitivity_json["liepin_snapshot"]`; do not add corpus columns in V1.
- Modify `src/seektalent/models.py`
  - Add card/detail score evidence fields only where they are needed by scoring and flywheel ledgers.
- Modify `src/seektalent/cli.py`
  - Add manual-only compliance-gate create/verify, fixture replay, Bun compatibility gate, and low-budget live smoke commands.

### Bun/TypeScript worker

- Create `apps/liepin-worker/package.json`
  - Bun scripts: `test`, `typecheck`, `boundary-check`, `compatibility-gate`, `dev`.
- Create `apps/liepin-worker/bun.lock`
  - Generated by `bun install` and committed so worker dependency versions are reproducible.
- Create `apps/liepin-worker/tsconfig.json`
  - Strict TypeScript config.
- Create `apps/liepin-worker/src/contracts.ts`
  - Worker request/response contracts.
- Create `apps/liepin-worker/src/sessionStore.ts`
  - AES-GCM encrypted storage-state persistence using a key supplied by environment.
- Create `apps/liepin-worker/src/session.ts`
  - Managed Chromium persistent context, login handoff, status detection, and revoke.
- Create `apps/liepin-worker/src/networkCapture.ts`
  - Passive Playwright `page.on("response")` capture and response-shape classification.
- Create `apps/liepin-worker/src/extraction.ts`
  - Network-first and DOM fallback extraction functions.
- Create `apps/liepin-worker/src/detail.ts`
  - Detail-open command execution and status diagnostics.
- Create `apps/liepin-worker/src/redaction.ts`
  - Recursive fixture redaction and fail-closed safety checks.
- Create `apps/liepin-worker/src/server.ts`
  - Internal Bun HTTP API for Python worker client only.
- Create `apps/liepin-worker/scripts/checkBoundaries.ts`
  - TypeScript AST guard against forbidden Playwright API-request patterns and secret leaks.
- Create `apps/liepin-worker/scripts/compatibilityGate.ts`
  - Bun + Playwright Chromium compatibility gate.
- Create `apps/liepin-worker/tests/*.test.ts`
  - Unit and integration tests for redaction, extraction, session store, network capture, detail open, boundaries, and compatibility harness.

## Task 0: Boundary Preflight

**Files:**
- Create: `tests/test_liepin_boundary_preflight.py`

- [ ] **Step 1: Write preflight tests**

Add tests that prove the plan is aligned with current repo shape:

```python
def test_corpus_artifact_kind_exists(tmp_path):
    from seektalent.artifacts import ArtifactStore

    session = ArtifactStore(tmp_path).create_root(kind="corpus", display_name="preflight", producer="test")
    assert session.manifest.artifact_kind.value == "corpus"


def test_search_result_can_be_extended_without_breaking_defaults():
    from seektalent.core.retrieval.provider_contract import SearchResult

    result = SearchResult()
    assert result.candidates == []
    assert result.request_payload == {}
```

- [ ] **Step 2: Run the preflight tests**

Run:

```bash
uv run pytest tests/test_liepin_boundary_preflight.py -q
```

Expected: tests pass. This task verifies existing repository capabilities before the Liepin-specific schema is added.

- [ ] **Step 3: Commit**

```bash
git add tests/test_liepin_boundary_preflight.py
git commit -m "test: add liepin boundary preflight"
```

## Task 1: Config, Provider Mode, Provider Snapshots, And Artifact Names

**Files:**
- Modify: `src/seektalent/config.py`
- Modify: `src/seektalent/default.env`
- Modify: `src/seektalent/providers/registry.py`
- Modify: `src/seektalent/core/retrieval/provider_contract.py`
- Modify: `src/seektalent/artifacts/registry.py`
- Modify: `tests/test_provider_registry.py`
- Modify: `tests/test_artifact_store.py`
- Modify: `tests/test_liepin_boundary_preflight.py`

- [ ] **Step 1: Write failing tests**

Add tests that require:

- `AppSettings(provider_name="liepin", liepin_worker_mode="fake_fixture", liepin_allow_fake_fixture_worker=True)` returns `LiepinProviderAdapter`.
- `AppSettings(provider_name="liepin", liepin_worker_mode="fake_fixture", liepin_allow_fake_fixture_worker=False)` raises a settings or registry error.
- `SearchResult(provider_snapshots=[])` is accepted.
- Liepin logical artifacts resolve:
  - `runtime.liepin_connection_events`
  - `round.02.retrieval.liepin_connection_status`
  - `round.02.retrieval.liepin_search_requests`
  - `round.02.retrieval.liepin_card_extraction`
  - `round.02.retrieval.liepin_detail_open_plan`
  - `round.02.retrieval.liepin_detail_open_results`
  - `round.02.retrieval.liepin_connector_metrics`
  - `assets.provider_snapshots.liepin.cards`
  - `assets.provider_snapshots.liepin.details`

- [ ] **Step 2: Run focused tests and confirm failure**

```bash
uv run pytest tests/test_provider_registry.py tests/test_artifact_store.py tests/test_liepin_boundary_preflight.py -q
```

Expected: failures for missing Liepin settings, missing adapter, missing `ProviderSnapshot`, and missing logical artifacts.

- [ ] **Step 3: Implement settings and provider mode**

Add settings:

```python
ProviderName = Literal["cts", "liepin"]
LiepinWorkerMode = Literal["disabled", "fake_fixture", "managed_local", "external_http"]

provider_name: ProviderName = "cts"
liepin_worker_mode: LiepinWorkerMode = "disabled"
liepin_allow_fake_fixture_worker: bool = False
liepin_worker_base_url: str | None = None
liepin_worker_host: str = "127.0.0.1"
liepin_worker_port: int = 0
liepin_worker_startup_timeout_seconds: float = 15.0
liepin_worker_timeout_seconds: float = 30.0
liepin_connector_db_path: str = ".seektalent/liepin_connector.sqlite3"
liepin_session_store_dir: str = ".seektalent/liepin_sessions"
liepin_session_store_key_id: str = "local-development"
liepin_api_token: str = "local-development-liepin-api-token"
liepin_default_daily_detail_budget: int = 20
liepin_live_enabled: bool = False
```

Validation rules:

- timeout must be positive;
- daily budget must be non-negative;
- `fake_fixture` requires `liepin_allow_fake_fixture_worker=True`;
- `managed_local` is the default live-capable local worker mode and does not require a preconfigured worker URL;
- `external_http` requires `liepin_worker_base_url`;
- `provider_name="liepin"` with `liepin_worker_mode="disabled"` must fail at provider registry selection.

- [ ] **Step 4: Add provider snapshot contract**

In `provider_contract.py`, add:

```python
ProviderPayloadKind = Literal["card", "detail"]

@dataclass(frozen=True)
class ProviderSnapshot:
    provider_name: str
    payload_kind: ProviderPayloadKind
    raw_payload: dict[str, Any]
    normalized_text: str
    provider_subject_id: str | None
    provider_listing_id: str | None
    synthetic_candidate_fingerprint: str
    identity_confidence: str
    extraction_source: str
    extractor_version: str
    pii_classification: str
    retention_policy: str
    access_scope: str
    redaction_state: str
    score_evidence_source: str
```

Extend `SearchResult`:

```python
provider_snapshots: list[ProviderSnapshot] = field(default_factory=list)
```

- [ ] **Step 5: Add artifacts and default env comments**

Add Liepin logical artifacts through `artifacts/registry.py`. Add Chinese comments to `default.env` explaining provider mode, worker URL, live gate, session store, and detail budget.

- [ ] **Step 6: Run tests and commit**

```bash
uv run pytest tests/test_provider_registry.py tests/test_artifact_store.py tests/test_liepin_boundary_preflight.py -q
git add src/seektalent/config.py src/seektalent/default.env src/seektalent/providers/registry.py src/seektalent/core/retrieval/provider_contract.py src/seektalent/artifacts/registry.py tests/test_provider_registry.py tests/test_artifact_store.py tests/test_liepin_boundary_preflight.py
git commit -m "feat: add liepin provider boundary settings"
```

## Task 2: Python API Boundary, Auth Scope, And Compliance Gate

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/seektalent_ui/models.py`
- Modify: `src/seektalent_ui/server.py`
- Create: `src/seektalent/providers/liepin/models.py`
- Create: `src/seektalent/providers/liepin/compliance.py`
- Create: `src/seektalent/providers/liepin/security.py`
- Create: `src/seektalent/providers/liepin/store.py`
- Modify: `src/seektalent/cli.py`
- Create: `tests/test_liepin_api_scope.py`
- Create: `tests/test_liepin_compliance_gate.py`
- Create: `tests/test_liepin_cli.py`

- [ ] **Step 1: Write API scope and ASGI tests**

Add tests against a FastAPI app factory such as `seektalent_ui.server.create_app` using FastAPI `TestClient` or httpx `ASGITransport`:

- missing `X-SeekTalent-API-Key` returns 401;
- wrong token returns 403;
- missing `X-Tenant-ID`, `X-Workspace-ID`, or `X-Actor-ID` returns 400;
- existing UI API tests are updated from stdlib server threads to the ASGI app while preserving current run lifecycle behavior;
- `POST /api/liepin/compliance-gates` returns a scoped compliance gate ref only when the payload satisfies live-search policy;
- `GET /api/liepin/compliance-gates/{gate_ref}` cannot read a gate from another workspace;
- a connection created in workspace A cannot be read from workspace B;
- `/api/liepin/connections/{connection_id}/login-url` returns a domain-level handoff payload, not CDP or worker URLs;
- `POST /api/liepin/connections/{connection_id}/stream-token` sets a short-lived HttpOnly stream-token cookie only for the same tenant/workspace/actor and connection;
- `/api/liepin/connections/{connection_id}/events` is implemented with `sse-starlette` `EventSourceResponse`, responds with `Content-Type: text/event-stream`, and streams scoped domain-level events;
- `POST /api/runs/{run_id}/stream-token` sets a short-lived HttpOnly stream-token cookie only for the same tenant/workspace/actor and run;
- `/api/runs/{run_id}/events` is implemented with `sse-starlette` `EventSourceResponse`, responds with `Content-Type: text/event-stream`, includes stable event names and sequence numbers where practical, and never includes raw provider payloads or worker internals;
- event endpoints reject tokens in URL query parameters;
- stream-token responses, logs, artifacts, and diagnostic payloads never include the raw token value;
- connection and run event streams read from the persisted `liepin_events` ledger, not a per-process-only queue;
- reconnect with `Last-Event-ID` or an equivalent last-sequence cursor resumes after the last seen event without replaying unsafe payloads;
- idle event streams do not busy-loop SQLite and event reads fetch bounded batches before polling again;
- `/api/runs` with `provider="liepin"` and no `complianceGateRef` returns 403.

- [ ] **Step 2: Write compliance gate tests**

Add tests that prove:

- gate must include account holder authorization;
- gate must include human initiated recruiting;
- `allowed_purposes=["research"]` does not satisfy search permission;
- allowed purposes are parsed as JSON/list, never matched with SQL `LIKE`;
- gate must include candidate personal information processing basis, personal-information processor, deletion SLA, operator/audit owner, and raw detail retention decision;
- denied or missing gate blocks live search before worker calls.
- creating a gate without `provider_account_hash` produces `pending_account_binding`, which can create a connection/login handoff but cannot start search or detail;
- binding a worker-observed provider account identity hint computes an HMAC account hash in Python and transitions a matching pending gate to `approved`;
- binding the wrong account hash or a different tenant/workspace/actor leaves the gate unable to start search;
- `seektalent liepin-compliance-gate create --tenant-id ... --workspace-id ... --actor-id ... --purpose search ...` writes a pending scoped gate and prints only the gate ref;
- `seektalent liepin-compliance-gate verify --gate-ref ... --tenant-id ... --workspace-id ... --actor-id ... --provider-account-hash ...` exits nonzero when the gate is missing, denied, expired, wrong purpose, wrong scope, or still pending account binding.

- [ ] **Step 3: Run tests and confirm failure**

```bash
uv run pytest tests/test_liepin_api_scope.py tests/test_liepin_compliance_gate.py tests/test_liepin_cli.py::test_liepin_compliance_gate_create_and_verify -q
```

Expected: failures for missing models, store, and routes.

- [ ] **Step 4: Implement compliance models and store**

Create `ComplianceGate` in `compliance.py` with fields:

```python
tenant_id: str
workspace_id: str
actor_id: str
provider_account_hash: str | None
status: Literal["pending_account_binding", "approved", "denied", "expired"]
candidate_personal_info_processing_basis: str
personal_information_processor: str
operator_audit_owner: str
account_holder_authorized: bool
human_initiated_recruiting: bool
allowed_purposes: list[str]
retention_policy: Literal["run_debug_short", "workspace_recruiting_record", "forbidden_persist"]
deletion_sla_days: int
deletion_path: str
raw_payload_access_scope: Literal["run_only", "workspace", "admin_only"]
raw_detail_retention_allowed_after_debug: bool
fixture_export_allowed: bool
policy_ref: str
```

`allows_connection_handoff()` may return true for `pending_account_binding` when all tenant/workspace/actor/purpose/policy checks pass. `allows_live_search()` must return true only when status is `approved`, `provider_account_hash` exactly matches the caller's post-login bound account hash, all required booleans are true, and `"search"` is an exact list member.

Keep `models.py` limited to shared domain enums and value objects. `store.py` may persist rows and JSON, but FastAPI routes must translate those internal rows into `seektalent_ui.models` responses rather than returning store objects directly.

- [ ] **Step 5: Implement event ledger**

Create a unified append-only `liepin_events` table in `store.py` for both connection and run events:

```text
tenant_id TEXT NOT NULL
workspace_id TEXT NOT NULL
actor_id TEXT NOT NULL
subject_type TEXT NOT NULL CHECK(subject_type IN ('connection', 'run'))
subject_id TEXT NOT NULL
sequence INTEGER NOT NULL
event_name TEXT NOT NULL
payload_json TEXT NOT NULL CHECK(json_valid(payload_json))
redaction_state TEXT NOT NULL
created_at TEXT NOT NULL
PRIMARY KEY (tenant_id, workspace_id, subject_type, subject_id, sequence)
```

Add indexes for `(tenant_id, workspace_id, actor_id, subject_type, subject_id, sequence)` and for cleanup by `created_at` if retention is bounded. `append_event()` must assign the next sequence transactionally, reject payloads containing raw provider payloads or worker internals, and store only domain JSON. `iter_events_after(limit=...)` must read committed rows newer than a sequence cursor in bounded batches and short transactions so SSE polling does not hold a long SQLite read transaction or busy-loop while idle.

- [ ] **Step 6: Implement FastAPI auth, routes, and event streams**

Add the narrow runtime dependencies:

```bash
uv add fastapi uvicorn sse-starlette
```

Extend `seektalent_ui.server` with a FastAPI ASGI app:

- expose `create_app(registry: RunRegistry, settings: AppSettings | None = None) -> FastAPI`;
- update `main()` to run the app with Uvicorn;
- remove new Liepin work from `BaseHTTPRequestHandler` and `ThreadingHTTPServer`;
- preserve the existing UI run lifecycle behavior through FastAPI routes before adding Liepin-specific routes.

Add header-based local API auth:

- `X-SeekTalent-API-Key` must equal `settings.liepin_api_token`;
- `X-Tenant-ID`, `X-Workspace-ID`, and `X-Actor-ID` are required for Liepin API routes;
- external routes call Python service methods only, never the Bun worker directly;
- API response models come from `seektalent_ui.models`; worker DTOs from `worker_contracts.py` and store rows from `store.py` must not be returned directly.

Add routes:

- `POST /api/liepin/compliance-gates`
- `GET /api/liepin/compliance-gates/{gate_ref}`
- `POST /api/liepin/connections`
- `GET /api/liepin/connections/{connection_id}`
- `POST /api/liepin/connections/{connection_id}/login-url`
- `POST /api/liepin/connections/{connection_id}/stream-token`
- `GET /api/liepin/connections/{connection_id}/events` streams `text/event-stream`
- `POST /api/runs` accepts `provider="liepin"` plus `connectionId` and `complianceGateRef`
- `POST /api/runs/{run_id}/stream-token`
- `GET /api/runs/{run_id}/events` streams `text/event-stream`
- `GET /api/runs/{run_id}/results`

The run endpoints may return queued/in-memory status in V1, but they must enforce scope and compliance before queuing a Liepin run. Event streams must be browser-consumable by a TanStack client: native `EventSource`, newline-delimited SSE frames, stable event names such as `connection_status`, `run_started`, `search_progress`, `detail_budget`, `quality_summary`, and `run_failed`, plus JSON payloads that contain only domain status, counters, artifact refs, and redacted diagnostics.

Because browser `EventSource` cannot attach arbitrary auth headers, do not require `X-SeekTalent-API-Key` directly on browser stream subscriptions. V1 uses a short-lived scoped stream token issued by Python after normal API auth, bound to tenant, workspace, actor, connection/run ID, and expiry, and stored only as an HttpOnly cookie. The raw token must not be returned in JSON, accepted as a query parameter, written to artifacts, or included in logs or diagnostics. Use `SameSite=Lax` for same-origin local UI/API by default, `Secure` outside localhost, a narrow cookie path for the matching event route where practical, and `Max-Age` no longer than the expected stream setup window. If a later full UI needs cross-origin credentials, configure CORS and `EventSource(..., { withCredentials: true })` without moving the token into the URL.

The event generator must read from `liepin_events`; do not rely on per-process memory for stream correctness. Each SSE event uses `sequence` as the SSE `id`. On reconnect, support `Last-Event-ID` or an equivalent last-sequence cursor and resume after that sequence. Keep polling intervals bounded, check `request.is_disconnected()`, configure keepalive pings/send timeout through `EventSourceResponse`, fetch events in limited batches, and avoid holding a SQLite transaction while waiting for new events.

Add CLI commands:

- `seektalent liepin-compliance-gate create --tenant-id ... --workspace-id ... --actor-id ... --purpose search --policy-ref ... --deletion-sla-days ... --deletion-path ...`
- `seektalent liepin-compliance-gate bind-account --gate-ref ... --tenant-id ... --workspace-id ... --actor-id ... --connection-id ...`
- `seektalent liepin-compliance-gate verify --gate-ref ... --tenant-id ... --workspace-id ... --actor-id ... --provider-account-hash ...`

The CLI must print gate refs, status, and validation failures only. It must not print raw candidate data, account identifiers, or connector secrets.

- [ ] **Step 7: Run tests and commit**

```bash
uv run pytest tests/test_liepin_api_scope.py tests/test_liepin_compliance_gate.py tests/test_liepin_cli.py tests/test_ui_api.py -q
git add pyproject.toml uv.lock src/seektalent_ui/models.py src/seektalent_ui/server.py src/seektalent/providers/liepin/models.py src/seektalent/providers/liepin/compliance.py src/seektalent/providers/liepin/security.py src/seektalent/providers/liepin/store.py src/seektalent/cli.py tests/test_liepin_api_scope.py tests/test_liepin_compliance_gate.py tests/test_liepin_cli.py tests/test_ui_api.py
git commit -m "feat: add liepin api and compliance gate"
```

## Task 2A: Bun Worker Test Harness Skeleton

**Files:**
- Create: `apps/liepin-worker/package.json`
- Create: `apps/liepin-worker/bun.lock`
- Create: `apps/liepin-worker/tsconfig.json`
- Create: `apps/liepin-worker/src/contracts.ts`
- Create: `apps/liepin-worker/tests/harness.test.ts`

- [ ] **Step 1: Create minimal Bun package tests**

Require:

- `bun test tests/harness.test.ts` succeeds;
- `bun run typecheck` succeeds;
- `apps/liepin-worker/bun.lock` is generated from `bun install` and committed;
- package scripts exist for `test`, `typecheck`, `boundary-check`, `compatibility-gate`, and `dev`, even if the last three are placeholders that fail with a clear "not implemented yet" message until their later tasks fill them in.

- [ ] **Step 2: Implement worker scaffold**

Create the minimal package, strict TypeScript config, and shared contract file needed by later worker tasks. Do not implement redaction, session storage, browser launch, or server behavior here. This task exists only so Task 3 and later Bun tests run inside one stable package boundary.

- [ ] **Step 3: Install, verify, and commit**

```bash
cd apps/liepin-worker && bun install && bun ci && bun test tests/harness.test.ts && bun run typecheck
git add apps/liepin-worker/package.json apps/liepin-worker/bun.lock apps/liepin-worker/tsconfig.json apps/liepin-worker/src/contracts.ts apps/liepin-worker/tests/harness.test.ts
git commit -m "chore: scaffold liepin worker test harness"
```

## Task 3: Protected Session Store And Managed Login Contract

**Files:**
- Create: `src/seektalent/providers/liepin/session_store.py`
- Modify: `src/seektalent/providers/liepin/store.py`
- Create: `apps/liepin-worker/src/sessionStore.ts`
- Create: `apps/liepin-worker/src/session.ts`
- Create: `apps/liepin-worker/tests/session-store.test.ts`
- Create: `apps/liepin-worker/tests/session.test.ts`
- Create: `tests/test_liepin_session_store.py`

- [ ] **Step 1: Write Python session metadata tests**

Require:

- connection rows are tenant/workspace scoped;
- provider account hash is HMAC, not a plain hash;
- session state path/bytes are never returned by Python API;
- revoke records a revocation event and clears session metadata;
- artifacts/log payload guard rejects cookies, storageState, auth headers, CDP URLs, debug websocket URLs, bearer/access/refresh tokens, localStorage, and sessionStorage.

- [ ] **Step 2: Write Bun session-store tests**

Require:

- storage state is encrypted before writing to disk;
- plaintext cookie names/values do not appear in the session file;
- wrong key ID or key fails decryption;
- revoke deletes encrypted state;
- session path is namespaced by tenant/workspace/account/connection.

- [ ] **Step 3: Run tests and confirm failure**

```bash
uv run pytest tests/test_liepin_session_store.py -q
cd apps/liepin-worker && bun test tests/session-store.test.ts tests/session.test.ts
```

Expected: missing modules fail.

- [ ] **Step 4: Implement protected session store**

Implement Bun AES-GCM encryption using WebCrypto. The key comes from environment and is identified by `liepin_session_store_key_id`; the key value is never logged or returned. Python stores only session metadata and revoke state.

- [ ] **Step 5: Implement managed login contract**

Worker session statuses:

- `logged_out`
- `ready`
- `needs_user_action`
- `risk_control_wait`
- `temporarily_rate_limited`
- `failed`

Login handoff returns:

```json
{
  "connection_id": "conn_...",
  "handoff_token": "opaque",
  "browser_view_url": null,
  "expires_at": "UTC-Z",
  "status_event_stream": "/api/liepin/connections/conn_.../events"
}
```

V1 may open a local headed Chromium window for the user. The handoff must not expose CDP, remote debugging, Playwright websocket, storageState, or worker base URL.

- [ ] **Step 6: Run tests and commit**

```bash
uv run pytest tests/test_liepin_session_store.py tests/test_liepin_api_scope.py -q
cd apps/liepin-worker && bun test tests/session-store.test.ts tests/session.test.ts
git add src/seektalent/providers/liepin/session_store.py src/seektalent/providers/liepin/store.py apps/liepin-worker tests/test_liepin_session_store.py tests/test_liepin_api_scope.py
git commit -m "feat: add protected liepin session store"
```

## Task 4: Detail Ledger State Machine And Per-Day Budget

**Files:**
- Modify: `src/seektalent/providers/liepin/store.py`
- Create: `src/seektalent/providers/liepin/policy.py`
- Create: `tests/test_liepin_detail_ledger.py`
- Create: `tests/test_liepin_detail_policy.py`

- [ ] **Step 1: Write ledger tests**

Require:

- `reserve_detail_attempt()` is idempotent by tenant/workspace/account/budget date/idempotency key;
- `budget_date` and `provider_day_key` are persisted;
- consumed count resets by provider day;
- duplicate worker response is applied once;
- `possibly_consumed` and `unknown` count against budget;
- `blocked_by_risk_control` records evidence and does not mark completed;
- `failed_before_consumption` does not consume budget;
- `failed_after_possible_consumption` consumes budget conservatively;
- transitions reject invalid jumps, such as completed directly from approved_not_started.

- [ ] **Step 2: Write policy tests**

Require:

- already-opened stable provider ID is skipped;
- weak fingerprints do not hard-suppress duplicates;
- low card-value candidates are skipped before budget is spent;
- budget exhaustion degrades to card-only candidates;
- detail plan emits an artifact-ready reason for every opened/skipped candidate.

- [ ] **Step 3: Run tests and confirm failure**

```bash
uv run pytest tests/test_liepin_detail_ledger.py tests/test_liepin_detail_policy.py -q
```

Expected: missing ledger methods fail.

- [ ] **Step 4: Implement state machine**

Add detail-attempt states exactly matching the spec:

- `approved_not_started`
- `started`
- `provider_page_loaded`
- `detail_payload_seen`
- `completed`
- `blocked_by_risk_control`
- `failed_before_consumption`
- `failed_after_possible_consumption`
- `unknown`

Add consumption states:

- `not_consumed`
- `consumed`
- `possibly_consumed`
- `unknown`

Store `started_at`, `completed_at`, `worker_command_id`, `raw_evidence_ref`, `budget_date`, `provider_day_key`, and `timezone`.

- [ ] **Step 5: Run tests and commit**

```bash
uv run pytest tests/test_liepin_detail_ledger.py tests/test_liepin_detail_policy.py -q
git add src/seektalent/providers/liepin/store.py src/seektalent/providers/liepin/policy.py tests/test_liepin_detail_ledger.py tests/test_liepin_detail_policy.py
git commit -m "feat: add liepin detail budget ledger"
```

## Task 5: Worker Runtime And Explicit Client Modes

**Files:**
- Create: `src/seektalent/providers/liepin/worker_runtime.py`
- Create: `src/seektalent/providers/liepin/client.py`
- Create: `src/seektalent/providers/liepin/worker_contracts.py`
- Create: `tests/test_liepin_worker_client.py`
- Create: `tests/test_liepin_worker_runtime.py`
- Modify: `src/seektalent/providers/liepin/adapter.py`
- Modify: `tests/test_liepin_provider_adapter.py`

- [ ] **Step 1: Write worker runtime and client mode tests**

Require:

- fake fixture client can be constructed only when settings use `liepin_worker_mode="fake_fixture"` and `liepin_allow_fake_fixture_worker=True`;
- managed local worker mode starts a Bun subprocess, chooses a localhost port when configured port is `0`, waits for `/internal/health`, and returns an internal base URL only to Python;
- managed local worker runtime is reused within the API/CLI process and does not spawn a new Bun subprocess per request or per run;
- managed local startup timeout records a `worker_start_timeout` domain event and fails before search dispatch;
- missing Bun executable or missing worker package reports a setup failure that names the missing prerequisite without falling back to Node.js;
- worker crash records a `worker_failed` event and redacts stdout/stderr before any diagnostic is surfaced;
- occupied configured port either picks a free port when `liepin_worker_port=0` or fails with a clear setup status;
- external HTTP client is required for `liepin_worker_mode="external_http"`;
- missing external HTTP worker URL fails before search dispatch;
- worker health, session status, login handoff, and redacted diagnostics decode through `worker_contracts.py`, not through external API response models;
- provider adapter never substitutes fake worker when no worker client is passed;
- fake fixture mode is rejected when `liepin_live_enabled=True`.

- [ ] **Step 2: Run tests and confirm failure**

```bash
uv run pytest tests/test_liepin_worker_runtime.py tests/test_liepin_worker_client.py tests/test_liepin_provider_adapter.py -q
```

Expected: missing runtime, client, and adapter modules fail.

- [ ] **Step 3: Implement worker runtime and client classes**

Implement:

- `ManagedLiepinWorkerRuntime`;
- `LiepinWorkerClient` protocol;
- `FakeLiepinWorkerClient`;
- `ManagedLocalLiepinWorkerClient`;
- `ExternalHttpLiepinWorkerClient`;
- `build_liepin_worker_client(settings)`;
- `LiepinWorkerModeError`.

Fake responses must be deterministic and labeled `fixture_only=True`. Managed local mode is the default live-capable local path. It starts one reusable Bun worker subprocess for the API/CLI process, waits for `/internal/health`, sends the worker auth token from settings, tears down the process on Python exit, and never exposes the worker base URL through external API responses. External HTTP mode is for diagnostics or later deployment only.

Create the initial `worker_contracts.py` with internal health, session status, login handoff, and redacted diagnostic contracts. Card/detail worker payload contracts are extended in Task 6 and Task 12 when mapping and detail-open behavior exist.

- [ ] **Step 4: Run tests and commit**

```bash
uv run pytest tests/test_liepin_worker_runtime.py tests/test_liepin_worker_client.py -q
git add src/seektalent/providers/liepin/worker_runtime.py src/seektalent/providers/liepin/client.py src/seektalent/providers/liepin/worker_contracts.py tests/test_liepin_worker_runtime.py tests/test_liepin_worker_client.py
git commit -m "feat: manage liepin worker runtime"
```

## Task 6: Protected Mapping And Corpus Snapshot Contract

**Files:**
- Create: `src/seektalent/providers/liepin/mapper.py`
- Modify: `src/seektalent/providers/liepin/worker_contracts.py`
- Modify: `src/seektalent/providers/liepin/models.py`
- Modify: `src/seektalent/core/retrieval/provider_contract.py`
- Modify: `src/seektalent/corpus/runtime.py`
- Modify: `src/seektalent/corpus/documents.py`
- Create: `tests/test_liepin_provider_mapping.py`
- Create: `tests/test_liepin_corpus_integration.py`

- [ ] **Step 1: Write mapping tests**

Require:

- `ResumeCandidate.raw` for Liepin contains only provider metadata and artifact refs;
- `ResumeCandidate.raw` does not contain `raw_payload`, `payload`, resume free text, phone, email, cookies, storageState, auth headers, or Liepin detail body;
- every worker card/detail returns a `ProviderSnapshot` with raw payload and privacy metadata;
- mapper sets `score_evidence_source="card_only"` for card candidates and `"detail_enriched"` for detail candidates.
- mapper tests construct worker card/detail payloads through `worker_contracts.py`, not through external API response models.

- [ ] **Step 2: Write corpus integration tests**

Require:

- `record_corpus_provider_results()` writes Liepin raw payload from `ProviderSnapshot`, not `candidate.raw`;
- card and detail snapshots carry `pii_classification`, `retention_policy`, `access_scope`, and `redaction_state` under `resume_documents.sensitivity_json["liepin_snapshot"]`;
- V1 does not add new corpus table columns for Liepin privacy metadata; if an implementation needs queryable columns, it must bump `CORPUS_SCHEMA_VERSION` and add explicit schema-version tests in `tests/test_corpus_store.py`;
- raw payload artifact ref is persisted;
- raw payload is omitted from materialized corpus export unless explicitly self-contained in a future design;
- duplicate provider returns produce one resume document and multiple observations.

- [ ] **Step 3: Run tests and confirm failure**

```bash
uv run pytest tests/test_liepin_provider_mapping.py tests/test_liepin_corpus_integration.py -q
```

Expected: current corpus runtime falls back to `candidate.raw`, and mapping modules are missing.

- [ ] **Step 4: Implement protected mapping**

`ResumeCandidate.raw` may include only:

- `provider`
- `provider_subject_id`
- `provider_listing_id`
- `synthetic_candidate_fingerprint`
- `identity_confidence`
- `extraction_source`
- `extractor_version`
- `pii_classification`
- `retention_policy`
- `access_scope`
- `redaction_state`
- `raw_payload_artifact_ref`
- `score_evidence_source`

Actual raw payload stays in `ProviderSnapshot.raw_payload`.

Worker card/detail request and response contracts live in `worker_contracts.py`. Keep `models.py` limited to shared domain enums/value objects used by mapping, policy, compliance, and result evidence.

- [ ] **Step 5: Update corpus runtime**

When `SearchResult.provider_snapshots` exists, runtime must pass those snapshots to corpus storage. For CTS and legacy tests, existing `candidate.raw` behavior remains available. For `provider_name="liepin"`, missing provider snapshots is an error.

Update `build_resume_document_row()` to accept optional provider privacy metadata and merge it into the existing sensitivity field:

```python
sensitivity_json = {
    "contains_pii": True,
    "contains_external_text": True,
}
if provider_privacy_metadata:
    sensitivity_json["liepin_snapshot"] = provider_privacy_metadata
```

Keep the existing generic corpus columns intact. `retention_policy` should use the snapshot retention policy when the provider is `liepin`; `content_trust_level` remains `untrusted_external`; `llm_ingestion_policy` remains `quote_as_data_only`.

- [ ] **Step 6: Run tests and commit**

```bash
uv run pytest tests/test_liepin_provider_mapping.py tests/test_liepin_corpus_integration.py tests/test_corpus_runtime.py -q
git add src/seektalent/providers/liepin/mapper.py src/seektalent/providers/liepin/models.py src/seektalent/providers/liepin/worker_contracts.py src/seektalent/core/retrieval/provider_contract.py src/seektalent/corpus/runtime.py src/seektalent/corpus/documents.py tests/test_liepin_provider_mapping.py tests/test_liepin_corpus_integration.py
git commit -m "feat: protect liepin provider snapshots"
```

## Task 7: Bun Worker Package, Recursive Redaction, And Boundary Guard

**Files:**
- Create: `apps/liepin-worker/src/redaction.ts`
- Create: `apps/liepin-worker/scripts/checkBoundaries.ts`
- Create: `apps/liepin-worker/tests/redaction.test.ts`
- Create: `apps/liepin-worker/tests/boundaries.test.ts`
- Create: `tests/test_liepin_boundaries.py`

- [ ] **Step 1: Write redaction tests**

Require recursive redaction of:

- nested `name`, `candidateName`, `realName`;
- phone/mobile numbers;
- email;
- wechat/weixin fields and free-text patterns;
- ID-like values under identity-sensitive keys;
- URLs with query strings;
- HTML text containing contact markers;
- headers/cookies/tokens/storageState/localStorage/sessionStorage/CDP/debug websocket strings.

Require a manifest:

```json
{
  "redaction_policy_version": "liepin-fixture-redaction-v1",
  "redaction_passed": true,
  "unsafe_reasons": []
}
```

- [ ] **Step 2: Write boundary guard tests**

The TypeScript boundary checker must fail on:

- `APIRequestContext`;
- `page.request`;
- `browserContext.request`;
- `context.request`;
- `playwright.request`;
- `request.newContext`;
- computed access such as `page["request"]`;
- imports from OpenCLI.

- [ ] **Step 3: Run tests and confirm failure**

```bash
cd apps/liepin-worker && bun test tests/redaction.test.ts tests/boundaries.test.ts
uv run pytest tests/test_liepin_boundaries.py -q
```

Expected: missing redaction module and boundary checker fail. The Bun package itself already exists from Task 2A.

- [ ] **Step 4: Implement redaction module and guards**

Use `bun:test`, `zod`, `playwright`, and `typescript`. The AST guard uses the TypeScript compiler API; it must not be a plain substring-only check. Add dependencies through `bun add` or `bun add -d` as appropriate, then update and commit `apps/liepin-worker/bun.lock`.

- [ ] **Step 5: Run tests and commit**

```bash
cd apps/liepin-worker && bun ci && bun test tests/redaction.test.ts tests/boundaries.test.ts && bun run boundary-check
uv run pytest tests/test_liepin_boundaries.py -q
git add apps/liepin-worker tests/test_liepin_boundaries.py
git commit -m "feat: add liepin worker redaction guards"
```

## Task 8: Bun Playwright Compatibility Gate

**Files:**
- Create: `apps/liepin-worker/scripts/compatibilityGate.ts`
- Create: `apps/liepin-worker/tests/compatibility-gate.test.ts`
- Modify: `src/seektalent/cli.py`
- Create: `tests/test_liepin_cli.py`

- [ ] **Step 1: Write compatibility gate tests**

The gate must verify:

- `bun ci` succeeds from the committed lockfile;
- Playwright Chromium browser binaries are installed or the gate reports a setup failure that names `bunx playwright install chromium`;
- Bun launches Playwright Chromium;
- persistent context can be created;
- a test page can be navigated;
- page-triggered response can be captured passively;
- a detail-like page can be opened by worker command;
- encrypted session state can be written and reloaded;
- a simulated worker crash leaves no plaintext session state;
- redaction passes;
- `bun test` and `bun run typecheck` pass.

- [ ] **Step 2: Add CLI test**

`seektalent liepin-bun-compatibility-gate` must call the Bun script and return nonzero if the gate fails. It must not run live Liepin.

- [ ] **Step 3: Run tests and confirm failure**

```bash
cd apps/liepin-worker && bun ci && bunx playwright install chromium && bun test tests/compatibility-gate.test.ts
uv run pytest tests/test_liepin_cli.py::test_liepin_bun_compatibility_gate_command -q
```

Expected: missing script/CLI command fail.

- [ ] **Step 4: Implement compatibility gate**

Use a local `data:` or file URL for test navigation. Do not contact Liepin. Do not expose CDP endpoint in output.

- [ ] **Step 5: Run tests and commit**

```bash
cd apps/liepin-worker && bun ci && bunx playwright install chromium && bun test tests/compatibility-gate.test.ts && bun run compatibility-gate
uv run pytest tests/test_liepin_cli.py -q
git add apps/liepin-worker src/seektalent/cli.py tests/test_liepin_cli.py
git commit -m "test: add liepin bun compatibility gate"
```

## Task 9: Passive Network Capture And DOM Fallback Replay

**Files:**
- Create: `apps/liepin-worker/src/networkCapture.ts`
- Create: `apps/liepin-worker/src/extraction.ts`
- Create: `apps/liepin-worker/tests/network-capture.test.ts`
- Create: `apps/liepin-worker/tests/extraction.test.ts`
- Create: `apps/liepin-worker/fixtures/cards.network.redacted.json`
- Create: `apps/liepin-worker/fixtures/detail.network.redacted.json`
- Create: `apps/liepin-worker/fixtures/cards.dom.redacted.html`

- [ ] **Step 1: Write capture tests**

Require:

- capture uses `page.on("response")`;
- parser input only comes from responses triggered by a visible page action;
- auth headers are never saved;
- auth-bearing URLs are tokenized before artifact/fixture output;
- endpoint fingerprint strips volatile query params;
- response shape hash is stable;
- DOM fallback works when network payload is absent.

- [ ] **Step 2: Write extraction tests**

Use synthetic redacted fixtures. Require card extraction and detail extraction to produce worker payloads with:

- provider identity fields;
- extraction source;
- extractor version;
- raw payload;
- normalized searchable text;
- privacy metadata.

- [ ] **Step 3: Run tests and confirm failure**

```bash
cd apps/liepin-worker && bun test tests/network-capture.test.ts tests/extraction.test.ts
```

Expected: missing modules fail.

- [ ] **Step 4: Implement passive capture and extraction**

Implement network capture as a collector around Playwright page events. Do not add request replay, direct API calls, signature generation, or stealth behavior.

- [ ] **Step 5: Run tests and commit**

```bash
cd apps/liepin-worker && bun test tests/network-capture.test.ts tests/extraction.test.ts tests/redaction.test.ts tests/boundaries.test.ts
git add apps/liepin-worker
git commit -m "feat: add liepin passive network extraction"
```

## Task 10: Internal Worker Server And Managed Login

**Files:**
- Create: `apps/liepin-worker/src/server.ts`
- Modify: `apps/liepin-worker/src/session.ts`
- Modify: `src/seektalent/providers/liepin/worker_contracts.py`
- Create: `apps/liepin-worker/tests/server.test.ts`
- Create: `tests/test_liepin_worker_client.py`

- [ ] **Step 1: Write server tests**

Require:

- `/internal/health` returns a minimal readiness payload and no browser/session internals;
- `/internal/session/status` returns domain status only;
- `/internal/session/login-handoff` returns handoff token and no CDP/debug/storage fields;
- `/internal/session/revoke` deletes encrypted session state;
- `/internal/search/cards` refuses to run when session is not ready;
- `/internal/details/open` requires preapproved idempotency key and does not decide budget;
- server rejects requests missing Python worker auth token.

- [ ] **Step 2: Run tests and confirm failure**

```bash
cd apps/liepin-worker && bun test tests/server.test.ts
uv run pytest tests/test_liepin_worker_client.py -q
```

Expected: missing server/client endpoints fail.

- [ ] **Step 3: Implement server and Python HTTP client**

The worker server is bound to localhost by default and is internal-only. Python client sends worker auth token from settings. No external API route may return the worker base URL. Managed local mode starts this server through `ManagedLiepinWorkerRuntime`; external HTTP mode may point at a pre-existing server only when explicitly configured. Python HTTP client responses must decode into `worker_contracts.py` DTOs before being mapped into domain results or external API responses.

- [ ] **Step 4: Run tests and commit**

```bash
cd apps/liepin-worker && bun test tests/server.test.ts tests/session.test.ts
uv run pytest tests/test_liepin_worker_client.py tests/test_liepin_api_scope.py -q
git add apps/liepin-worker src/seektalent/providers/liepin/client.py src/seektalent/providers/liepin/worker_contracts.py tests/test_liepin_worker_client.py tests/test_liepin_api_scope.py
git commit -m "feat: add internal liepin worker server"
```

## Task 11: Liepin Provider Adapter And Live Compliance Enforcement

**Files:**
- Create: `src/seektalent/providers/liepin/adapter.py`
- Create: `tests/test_liepin_provider_adapter.py`
- Modify: `src/seektalent/providers/registry.py`
- Modify: `src/seektalent/runtime/retrieval_runtime.py`
- Modify: `tests/test_query_identity.py`

- [ ] **Step 1: Write adapter tests**

Require:

- summary search calls worker only when session is ready and compliance gate passes;
- missing compliance gate raises `ComplianceGateRequired` before any worker call;
- denied compliance gate raises before any worker call;
- pending account-binding compliance gate raises before any search or detail worker call;
- approved gate with mismatched provider account hash raises before any search or detail worker call;
- missing connection ID raises before worker call;
- fake fixture mode works only when explicitly allowed;
- detail fetch without a detail-open plan raises a domain error;
- `SearchResult.provider_snapshots` contains all returned card snapshots;
- `ResumeCandidate.raw` does not contain raw provider payload.

- [ ] **Step 2: Write query identity test**

Do not assert `query_instance_id.startswith("run_")`. Instead assert:

- `query_instance_id` is non-empty;
- `query_fingerprint` is non-empty;
- canonical query spec contains `provider_name="liepin"`;
- fingerprint differs when the same logical query is rendered for `cts` versus `liepin`.

- [ ] **Step 3: Run tests and confirm failure**

```bash
uv run pytest tests/test_liepin_provider_adapter.py tests/test_query_identity.py -q
```

Expected: missing adapter and provider-name wiring failures.

- [ ] **Step 4: Implement adapter and registry**

The adapter creates `SearchResult` with:

- mapped `ResumeCandidate` list;
- `ProviderSnapshot` list;
- request payload without cookies, headers, storageState, CDP, or raw provider URLs;
- diagnostics and latency.

The adapter does not decide detail budget. It only executes a detail-open plan produced by Python policy.

- [ ] **Step 5: Update runtime corpus recording**

Runtime must include provider snapshots in corpus ingestion for every provider-returned Liepin card/detail. This hook must not be tied to flywheel being enabled.

- [ ] **Step 6: Run tests and commit**

```bash
uv run pytest tests/test_liepin_provider_adapter.py tests/test_query_identity.py tests/test_runtime_state_flow.py tests/test_corpus_runtime.py -q
git add src/seektalent/providers/liepin/adapter.py src/seektalent/providers/registry.py src/seektalent/runtime/retrieval_runtime.py tests/test_liepin_provider_adapter.py tests/test_query_identity.py
git commit -m "feat: add liepin provider adapter"
```

## Task 12: Detail Open Integration And Card/Detail Score Separation

**Files:**
- Create: `apps/liepin-worker/src/detail.ts`
- Create: `apps/liepin-worker/tests/detail.test.ts`
- Modify: `src/seektalent/providers/liepin/adapter.py`
- Modify: `src/seektalent/providers/liepin/worker_contracts.py`
- Modify: `src/seektalent/providers/liepin/verified_loop.py`
- Modify: `src/seektalent/models.py`
- Modify: `src/seektalent/runtime/retrieval_runtime.py`
- Modify: `src/seektalent/flywheel/runtime.py`
- Create: `tests/test_liepin_detail_integration.py`
- Create: `tests/test_liepin_verified_loop.py`

- [x] **Step 1: Write detail integration tests**

Require full flow:

1. Python policy selects candidate.
2. Ledger reserves budget before dispatch.
3. Worker opens detail.
4. Ledger marks started, page loaded, payload seen, completed consumed.
5. Detail snapshot is saved to corpus.
6. Detail-enriched candidate keeps `detail_scorecard` separate from `card_scorecard`.
7. Unknown worker crash after dispatch marks attempt `possibly_consumed`.

- [x] **Step 2: Write scoring/flywheel tests**

Require:

- card-only scorecard and detail-enriched scorecard are stored separately;
- score delta is recorded;
- PRF seed/flywheel outcomes record evidence source;
- detail-enriched candidates do not make the original lane look better without an evidence-source marker.

- [x] **Step 3: Run tests and confirm failure**

```bash
cd apps/liepin-worker && bun test tests/detail.test.ts
uv run pytest tests/test_liepin_detail_integration.py tests/test_liepin_verified_loop.py -q
```

Expected: missing detail worker and score separation fields fail.

- [x] **Step 4: Implement detail open path**

Implement worker `open_details()` command with passive network capture first and DOM fallback second. It receives only approved detail requests from Python and returns payloads plus diagnostics.

Represent detail-open requests and responses in `worker_contracts.py`. External run result models must receive mapped summaries and refs only, not raw worker detail DTOs.

- [x] **Step 5: Implement quality separation**

Add minimal fields needed by current runtime:

- `score_evidence_source`
- `card_scorecard_ref`
- `detail_scorecard_ref`
- `score_delta`
- `detail_open_reason`
- `detail_open_policy_version`

Store refs or compact metadata, not raw scorecard payloads unless existing artifact conventions require the payload.

- [x] **Step 6: Run tests and commit**

```bash
cd apps/liepin-worker && bun test tests/detail.test.ts tests/network-capture.test.ts tests/extraction.test.ts
uv run pytest tests/test_liepin_detail_integration.py tests/test_liepin_verified_loop.py tests/test_flywheel_runtime.py -q
git add apps/liepin-worker src/seektalent/providers/liepin src/seektalent/models.py src/seektalent/runtime/retrieval_runtime.py src/seektalent/flywheel/runtime.py tests/test_liepin_detail_integration.py tests/test_liepin_verified_loop.py
git commit -m "feat: wire liepin detail open loop"
```

## Task 13: Manual Commands And Low-Budget Live Smoke

**Files:**
- Modify: `src/seektalent/cli.py`
- Create: `tests/test_liepin_cli.py`

- [ ] **Step 1: Write CLI tests**

Require:

- `liepin-replay-fixtures` runs without live account;
- `liepin-bun-compatibility-gate` runs without live account;
- `liepin-compliance-gate create` writes a pending scoped gate and prints a gate ref without raw account identifiers or secrets;
- `liepin-compliance-gate bind-account` derives the provider account hash from the scoped connection's worker-observed account identity hint and transitions a pending gate to approved only when tenant/workspace/actor scope matches;
- `liepin-compliance-gate bind-account` never accepts or prints the raw account identity hint as a CLI argument;
- `liepin-compliance-gate verify` exits nonzero for missing, denied, expired, pending, wrong-account, wrong-scope, or no-`"search"` gates;
- `liepin-smoke` requires `--live`;
- `liepin-smoke --live` requires compliance gate ref, connection ID, tenant/workspace, and actor, then verifies the bound provider account hash internally from the scoped connection/gate;
- `liepin-smoke --live` refuses fake fixture worker mode;
- `liepin-smoke --live` starts the managed local worker automatically unless `liepin_worker_mode="external_http"` is explicitly configured;
- `liepin-smoke --live` reports worker setup, startup timeout, health, and crash states without printing raw worker stdout/stderr;
- `liepin-smoke --live --max-detail-opens 1` passes max budget into detail policy.

- [ ] **Step 2: Run tests and confirm failure**

```bash
uv run pytest tests/test_liepin_cli.py -q
```

Expected: missing commands fail.

- [ ] **Step 3: Implement commands**

Commands:

- `seektalent liepin-compliance-gate create --tenant-id ... --workspace-id ... --actor-id ... --purpose search --policy-ref ... --deletion-sla-days ... --deletion-path ...`
- `seektalent liepin-compliance-gate bind-account --gate-ref ... --tenant-id ... --workspace-id ... --actor-id ... --connection-id ...`
- `seektalent liepin-compliance-gate verify --gate-ref ... --tenant-id ... --workspace-id ... --actor-id ... --provider-account-hash ...`
- `seektalent liepin-replay-fixtures`
- `seektalent liepin-bun-compatibility-gate`
- `seektalent liepin-smoke --live --tenant-id ... --workspace-id ... --actor-id ... --connection-id ... --compliance-gate-ref ... --max-detail-opens 1`

Live smoke is manual-only and low budget. It must print compliance/session/detail counters and artifact refs, not raw payloads.

- [ ] **Step 4: Run tests and commit**

```bash
uv run pytest tests/test_liepin_cli.py -q
git add src/seektalent/cli.py tests/test_liepin_cli.py
git commit -m "feat: add liepin manual verification commands"
```

## Task 14: Final Boundary Verification

**Files:**
- Modify: `tests/test_liepin_boundaries.py`
- Modify: `apps/liepin-worker/scripts/checkBoundaries.ts`

- [ ] **Step 1: Add final guard assertions**

Guards must prove:

- no production TypeScript uses `APIRequestContext`, `page.request`, `browserContext.request`, `context.request`, `playwright.request`, `request.newContext`, or computed `["request"]` on Playwright page/context objects;
- no production code imports OpenCLI;
- no production Python path returns worker base URL, CDP endpoint, storageState, cookies, auth headers, or raw provider payload through UI API;
- managed local worker lifecycle is Python-owned by default; worker setup failures, health failures, and crashes become safe domain events and redacted diagnostics;
- client-facing Python API is FastAPI/Uvicorn based, and no V1 Liepin route is implemented on the legacy stdlib HTTP handler;
- external API response models are translated through `seektalent_ui.models` and never directly serialize `worker_contracts.py` DTOs or `store.py` row objects;
- run and connection event endpoints use `sse-starlette` `EventSourceResponse`, return `text/event-stream`, enforce tenant/workspace scope before streaming, and emit only safe domain-level JSON events;
- run and connection event endpoints read from persisted `liepin_events` rows, resume by sequence or `Last-Event-ID`, and are not per-process-only queues;
- idle event streams fetch bounded batches and do not busy-loop SQLite or hold long read transactions;
- stream-token routes set short-lived HttpOnly cookies scoped to one tenant, workspace, actor, and connection/run ID; expired, wrong-scope, URL query, or body-returned tokens cannot open a stream;
- no Liepin mapper writes raw payload into `ResumeCandidate.raw`;
- fake fixture mode is not reachable when `liepin_live_enabled=True`;
- card/detail score evidence source appears in flywheel rows when detail enrichment exists.

- [ ] **Step 2: Run all focused checks**

```bash
uv run pytest tests/test_liepin_boundary_preflight.py tests/test_liepin_api_scope.py tests/test_liepin_compliance_gate.py tests/test_liepin_session_store.py tests/test_liepin_detail_ledger.py tests/test_liepin_detail_policy.py tests/test_liepin_worker_client.py tests/test_liepin_provider_mapping.py tests/test_liepin_corpus_integration.py tests/test_liepin_provider_adapter.py tests/test_liepin_detail_integration.py tests/test_liepin_verified_loop.py tests/test_liepin_cli.py tests/test_liepin_boundaries.py -q
cd apps/liepin-worker && bun ci && bunx playwright install chromium && bun test && bun run typecheck && bun run boundary-check && bun run compatibility-gate
```

Expected: all focused tests pass.

- [ ] **Step 3: Run full Python suite**

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_liepin_boundaries.py apps/liepin-worker/scripts/checkBoundaries.ts
git commit -m "test: verify liepin connector boundaries"
```

## Manual Live Verification Gate

Manual live verification is not part of CI. Before live smoke:

1. In a fresh worker checkout, run `cd apps/liepin-worker && bun ci && bunx playwright install chromium`.
2. Run `seektalent liepin-bun-compatibility-gate`.
3. Create a pending compliance gate with exact `"search"` purpose using `seektalent liepin-compliance-gate create`.
4. Create a connection and handoff login to the user using the pending gate.
5. Confirm session status is `ready` and Python can derive a provider account hash from the worker's domain-level account identity hint without exposing the hint.
6. Bind the gate to the derived provider account hash using `seektalent liepin-compliance-gate bind-account --connection-id ...`, then verify it with `seektalent liepin-compliance-gate verify`.
7. Run one card search with zero detail opens.
8. Confirm card snapshots were saved to corpus and raw payloads did not enter run results.
9. Subscribe to the run event stream with native `EventSource` or `curl -N` and confirm progress arrives from the FastAPI `sse-starlette` route as `text/event-stream` without polling.
10. Reconnect with the last seen SSE `id` and confirm the stream resumes from persisted `liepin_events` rows without duplicate or missing events.
11. Run one detail-open smoke with `--max-detail-opens 1`.
12. Confirm detail ledger counted the attempt for the provider day.
13. Confirm unknown or failed detail consumption is treated conservatively.
14. Confirm artifacts/logs contain no cookies, auth headers, storageState, CDP/debug URLs, account identity hints, or raw candidate-identifying fixture payloads.

## Self-Review Checklist

- Bun V1 runner is preserved and gated by Task 8.
- Worker dependency reproducibility is established in Task 2A and enforced with `apps/liepin-worker/bun.lock`, `bun ci`, and `bunx playwright install chromium` in Tasks 7, 8, and 14.
- TanStack is the starting client stack for UI-facing contracts; V1 event and result APIs are shaped for TanStack Router + Query clients from the first implementation.
- Streaming is first-class: connection and run progress use Python-owned FastAPI + `sse-starlette` SSE endpoints instead of polling-first progress.
- Pretext is reserved for text-heavy UI/report surfaces if needed and is not introduced into the connector worker or Python business core.
- External/client-facing API is Python-owned in Task 2.
- Model ownership is split by trust boundary: external API DTOs in `seektalent_ui.models`, domain value objects in `liepin.models`, compliance gates in `liepin.compliance`, worker DTOs in `liepin.worker_contracts`, and store rows hidden behind translation.
- Bun worker is internal-only in Tasks 3, 8, 10, and 14.
- Compliance gate creation, verification, storage, and enforcement are covered before live worker calls in Tasks 2, 11, and 13.
- Candidate personal information processing basis, processor, deletion SLA, audit owner, and raw detail retention are modeled in Task 2.
- Protected session store and revoke are implemented in Task 3.
- Fake worker mode is explicit and test-only in Task 5.
- Detail ledger is per-day, transactional, and stateful in Task 4.
- Raw provider payload does not enter `ResumeCandidate.raw` in Task 6.
- Liepin privacy metadata is stored under `sensitivity_json["liepin_snapshot"]` in Task 6 without adding corpus columns in V1.
- Network extraction is passive and page-triggered in Task 9.
- APIRequestContext and cookie-sharing request APIs are forbidden by Tasks 7 and 14.
- Managed login is present in Tasks 3 and 10.
- `open_details` is wired through policy, ledger, worker, corpus, and scoring in Task 12.
- Card-only and detail-enriched evidence are separated in Task 12.
- Corpus artifact kind is verified in Task 0; it is not invented by this plan.
- Query identity test no longer expects `run_` prefix in Task 11.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | - | Not run for this final engineering lock review |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | - | Not run |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 2 | clean | 10 issues reviewed/fixed, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | - | Not run; rollout is API/worker first |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | - | Not run |

- **UNRESOLVED:** 0
- **VERDICT:** ENG CLEARED - ready to implement from this plan.
