# Liepin Connector Verified Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a verified Liepin provider loop where users only log into Liepin while SeekTalent handles authenticated search, passive network extraction, detail-budget control, protected corpus persistence, replay, and quality traceability.

**Architecture:** Python remains the business authority for API scope, compliance, query planning, detail-open policy, scoring, corpus/flywheel writes, and artifacts. Bun/TypeScript is the V1 production worker runtime and owns only managed Chromium/Playwright browser execution, passive network capture, DOM fallback extraction, and detail-page execution. The worker is internal-only; all client-facing API calls go through Python.

**Tech Stack:** Python 3.12, Pydantic, SQLite, existing `seektalent_ui` stdlib HTTP server, existing ArtifactStore/CorpusStore/FlywheelStore, Bun, TypeScript, Playwright Chromium, pytest, Bun test.

---

## Scope Notes

This plan implements the V1 connector loop from `docs/superpowers/specs/2026-05-07-liepin-cloud-connector-design.md`.

This plan does not build the Vite/TanStack UI, static benchmark qrels, personalized memory, Lightpanda, a browser extension, local Chrome profile reuse, or a generic website automation platform.

The user-facing contract is strict: the only required user action is logging into Liepin inside the managed browser session.

## Hard Constraints

- Bun/TypeScript is the V1 production worker runtime. Node.js may be used only as explicit diagnostic comparison and never as a fallback.
- Live Liepin calls must fail closed unless a passing compliance gate exists for the tenant, workspace, actor, provider account hash, and purpose.
- The Bun worker API is internal-only. External clients must not reach CDP, Playwright, remote debugging ports, worker endpoints, storage state, or arbitrary browser controls.
- Fake worker mode must be explicit and test/fixture-only. `provider_name="liepin"` must never silently return fake candidates.
- Raw Liepin provider payloads must not be placed in `ResumeCandidate.raw`, run results, ordinary debug artifacts, fixtures, or logs. Raw payloads go through protected corpus snapshots and artifact refs.
- `page.request`, `browserContext.request`, `APIRequestContext`, replayed authenticated requests, provider signature generation, stealth plugins, proxy rotation, and header manipulation are forbidden in V1 production.
- Detail opens are scarce and must be recorded as idempotent per-day budget transactions before the worker opens a detail page.
- Card-only and detail-enriched scorecards are different evidence conditions and must remain separated in scoring, query-hit metadata, flywheel outcomes, and metrics.

## File Structure

### Python API and provider boundary

- Modify `src/seektalent_ui/models.py`
  - Add request/response models for Liepin connections, login handoff, scoped API context, run submission with `provider="liepin"`, event rows, and result summaries.
- Modify `src/seektalent_ui/server.py`
  - Add authenticated tenant/workspace scoped endpoints for Liepin connections, login handoff, run submission, events, and results.
- Create `src/seektalent/providers/liepin/__init__.py`
  - Export `LiepinProviderAdapter`.
- Create `src/seektalent/providers/liepin/models.py`
  - Pydantic/dataclass contracts for connection status, compliance gate, candidate identity, worker cards/details, protected snapshots, detail attempts, and score evidence source.
- Create `src/seektalent/providers/liepin/security.py`
  - HMAC account hash, structured secret guards, artifact redaction guards, and storage-state leak checks.
- Create `src/seektalent/providers/liepin/store.py`
  - SQLite connector ledger for compliance gates, connection events, session metadata, and detail-open attempts.
- Create `src/seektalent/providers/liepin/session_store.py`
  - Python-facing protected session metadata and revoke operations. The encrypted browser state bytes remain worker-owned.
- Create `src/seektalent/providers/liepin/client.py`
  - Explicit fake-fixture and HTTP worker clients.
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
  - Add provider selection, Liepin worker mode, worker URL, connector DB path, session key ID, API token, and budget settings.
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
  - Add optional protected snapshot privacy metadata for Liepin card/detail payloads.
- Modify `src/seektalent/models.py`
  - Add card/detail score evidence fields only where they are needed by scoring and flywheel ledgers.
- Modify `src/seektalent/cli.py`
  - Add manual-only fixture replay, Bun compatibility gate, and low-budget live smoke commands.

### Bun/TypeScript worker

- Create `apps/liepin-worker/package.json`
  - Bun scripts: `test`, `typecheck`, `boundary-check`, `compatibility-gate`, `dev`.
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
LiepinWorkerMode = Literal["disabled", "fake_fixture", "http"]

provider_name: ProviderName = "cts"
liepin_worker_mode: LiepinWorkerMode = "disabled"
liepin_allow_fake_fixture_worker: bool = False
liepin_worker_base_url: str = "http://127.0.0.1:8765"
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
- Modify: `src/seektalent_ui/models.py`
- Modify: `src/seektalent_ui/server.py`
- Create: `src/seektalent/providers/liepin/models.py`
- Create: `src/seektalent/providers/liepin/security.py`
- Create: `src/seektalent/providers/liepin/store.py`
- Create: `tests/test_liepin_api_scope.py`
- Create: `tests/test_liepin_compliance_gate.py`

- [ ] **Step 1: Write API scope tests**

Add tests against `seektalent_ui.server.create_server`:

- missing `X-SeekTalent-API-Key` returns 401;
- wrong token returns 403;
- missing `X-Tenant-ID`, `X-Workspace-ID`, or `X-Actor-ID` returns 400;
- a connection created in workspace A cannot be read from workspace B;
- `/api/liepin/connections/{connection_id}/login-url` returns a domain-level handoff payload, not CDP or worker URLs;
- `/api/runs` with `provider="liepin"` and no `complianceGateRef` returns 403.

- [ ] **Step 2: Write compliance gate tests**

Add tests that prove:

- gate must include account holder authorization;
- gate must include human initiated recruiting;
- `allowed_purposes=["research"]` does not satisfy search permission;
- allowed purposes are parsed as JSON/list, never matched with SQL `LIKE`;
- gate must include candidate personal information processing basis, personal-information processor, deletion SLA, operator/audit owner, and raw detail retention decision;
- denied or missing gate blocks live search before worker calls.

- [ ] **Step 3: Run tests and confirm failure**

```bash
uv run pytest tests/test_liepin_api_scope.py tests/test_liepin_compliance_gate.py -q
```

Expected: failures for missing models, store, and routes.

- [ ] **Step 4: Implement compliance models and store**

Create a `ComplianceGate` model with fields:

```python
tenant_id: str
workspace_id: str
actor_id: str
provider_account_hash: str
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

`allows_live_search()` must return true only when all required booleans are true and `"search"` is an exact list member.

- [ ] **Step 5: Implement API auth and routes**

Extend `seektalent_ui.server` with header-based local API auth:

- `X-SeekTalent-API-Key` must equal `settings.liepin_api_token`;
- `X-Tenant-ID`, `X-Workspace-ID`, and `X-Actor-ID` are required for Liepin API routes;
- external routes call Python service methods only, never the Bun worker directly.

Add routes:

- `POST /api/liepin/connections`
- `GET /api/liepin/connections/{connection_id}`
- `POST /api/liepin/connections/{connection_id}/login-url`
- `POST /api/runs` accepts `provider="liepin"` plus `connectionId` and `complianceGateRef`
- `GET /api/runs/{run_id}/events`
- `GET /api/runs/{run_id}/results`

The run endpoints may return queued/in-memory status in V1, but they must enforce scope and compliance before queuing a Liepin run.

- [ ] **Step 6: Run tests and commit**

```bash
uv run pytest tests/test_liepin_api_scope.py tests/test_liepin_compliance_gate.py tests/test_ui_api.py -q
git add src/seektalent_ui/models.py src/seektalent_ui/server.py src/seektalent/providers/liepin/models.py src/seektalent/providers/liepin/security.py src/seektalent/providers/liepin/store.py tests/test_liepin_api_scope.py tests/test_liepin_compliance_gate.py
git commit -m "feat: add liepin api and compliance gate"
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

## Task 5: Explicit Worker Client Modes

**Files:**
- Create: `src/seektalent/providers/liepin/client.py`
- Create: `tests/test_liepin_worker_client.py`
- Modify: `src/seektalent/providers/liepin/adapter.py`
- Modify: `tests/test_liepin_provider_adapter.py`

- [ ] **Step 1: Write client mode tests**

Require:

- fake fixture client can be constructed only when settings use `liepin_worker_mode="fake_fixture"` and `liepin_allow_fake_fixture_worker=True`;
- HTTP client is required for `liepin_worker_mode="http"`;
- missing HTTP worker URL fails before search dispatch;
- provider adapter never substitutes fake worker when no worker client is passed;
- fake fixture mode is rejected when `liepin_live_enabled=True`.

- [ ] **Step 2: Run tests and confirm failure**

```bash
uv run pytest tests/test_liepin_worker_client.py tests/test_liepin_provider_adapter.py -q
```

Expected: missing client and adapter modules fail.

- [ ] **Step 3: Implement client classes**

Implement:

- `LiepinWorkerClient` protocol;
- `FakeLiepinWorkerClient`;
- `HttpLiepinWorkerClient`;
- `build_liepin_worker_client(settings)`;
- `LiepinWorkerModeError`.

Fake responses must be deterministic and labeled `fixture_only=True`.

- [ ] **Step 4: Run tests and commit**

```bash
uv run pytest tests/test_liepin_worker_client.py -q
git add src/seektalent/providers/liepin/client.py tests/test_liepin_worker_client.py
git commit -m "feat: add explicit liepin worker modes"
```

## Task 6: Protected Mapping And Corpus Snapshot Contract

**Files:**
- Create: `src/seektalent/providers/liepin/mapper.py`
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

- [ ] **Step 2: Write corpus integration tests**

Require:

- `record_corpus_provider_results()` writes Liepin raw payload from `ProviderSnapshot`, not `candidate.raw`;
- card and detail snapshots carry `pii_classification`, `retention_policy`, `access_scope`, and `redaction_state`;
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

- [ ] **Step 5: Update corpus runtime**

When `SearchResult.provider_snapshots` exists, runtime must pass those snapshots to corpus storage. For CTS and legacy tests, existing `candidate.raw` behavior remains available. For `provider_name="liepin"`, missing provider snapshots is an error.

- [ ] **Step 6: Run tests and commit**

```bash
uv run pytest tests/test_liepin_provider_mapping.py tests/test_liepin_corpus_integration.py tests/test_corpus_runtime.py -q
git add src/seektalent/providers/liepin/mapper.py src/seektalent/providers/liepin/models.py src/seektalent/core/retrieval/provider_contract.py src/seektalent/corpus/runtime.py src/seektalent/corpus/documents.py tests/test_liepin_provider_mapping.py tests/test_liepin_corpus_integration.py
git commit -m "feat: protect liepin provider snapshots"
```

## Task 7: Bun Worker Package, Recursive Redaction, And Boundary Guard

**Files:**
- Create: `apps/liepin-worker/package.json`
- Create: `apps/liepin-worker/tsconfig.json`
- Create: `apps/liepin-worker/src/contracts.ts`
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

Expected: missing worker package and scripts fail.

- [ ] **Step 4: Implement worker package and guards**

Use `bun:test`, `zod`, `playwright`, and `typescript`. The AST guard uses the TypeScript compiler API; it must not be a plain substring-only check.

- [ ] **Step 5: Run tests and commit**

```bash
cd apps/liepin-worker && bun test tests/redaction.test.ts tests/boundaries.test.ts && bun run boundary-check
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
cd apps/liepin-worker && bun test tests/compatibility-gate.test.ts
uv run pytest tests/test_liepin_cli.py::test_liepin_bun_compatibility_gate_command -q
```

Expected: missing script/CLI command fail.

- [ ] **Step 4: Implement compatibility gate**

Use a local `data:` or file URL for test navigation. Do not contact Liepin. Do not expose CDP endpoint in output.

- [ ] **Step 5: Run tests and commit**

```bash
cd apps/liepin-worker && bun test tests/compatibility-gate.test.ts && bun run compatibility-gate
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
- Create: `apps/liepin-worker/tests/server.test.ts`
- Create: `tests/test_liepin_worker_client.py`

- [ ] **Step 1: Write server tests**

Require:

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

The worker server is bound to localhost by default and is internal-only. Python client sends worker auth token from settings. No external API route may return the worker base URL.

- [ ] **Step 4: Run tests and commit**

```bash
cd apps/liepin-worker && bun test tests/server.test.ts tests/session.test.ts
uv run pytest tests/test_liepin_worker_client.py tests/test_liepin_api_scope.py -q
git add apps/liepin-worker src/seektalent/providers/liepin/client.py tests/test_liepin_worker_client.py tests/test_liepin_api_scope.py
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
- Modify: `src/seektalent/providers/liepin/verified_loop.py`
- Modify: `src/seektalent/models.py`
- Modify: `src/seektalent/runtime/retrieval_runtime.py`
- Modify: `src/seektalent/flywheel/runtime.py`
- Create: `tests/test_liepin_detail_integration.py`
- Create: `tests/test_liepin_verified_loop.py`

- [ ] **Step 1: Write detail integration tests**

Require full flow:

1. Python policy selects candidate.
2. Ledger reserves budget before dispatch.
3. Worker opens detail.
4. Ledger marks started, page loaded, payload seen, completed consumed.
5. Detail snapshot is saved to corpus.
6. Detail-enriched candidate keeps `detail_scorecard` separate from `card_scorecard`.
7. Unknown worker crash after dispatch marks attempt `possibly_consumed`.

- [ ] **Step 2: Write scoring/flywheel tests**

Require:

- card-only scorecard and detail-enriched scorecard are stored separately;
- score delta is recorded;
- PRF seed/flywheel outcomes record evidence source;
- detail-enriched candidates do not make the original lane look better without an evidence-source marker.

- [ ] **Step 3: Run tests and confirm failure**

```bash
cd apps/liepin-worker && bun test tests/detail.test.ts
uv run pytest tests/test_liepin_detail_integration.py tests/test_liepin_verified_loop.py -q
```

Expected: missing detail worker and score separation fields fail.

- [ ] **Step 4: Implement detail open path**

Implement worker `open_details()` command with passive network capture first and DOM fallback second. It receives only approved detail requests from Python and returns payloads plus diagnostics.

- [ ] **Step 5: Implement quality separation**

Add minimal fields needed by current runtime:

- `score_evidence_source`
- `card_scorecard_ref`
- `detail_scorecard_ref`
- `score_delta`
- `detail_open_reason`
- `detail_open_policy_version`

Store refs or compact metadata, not raw scorecard payloads unless existing artifact conventions require the payload.

- [ ] **Step 6: Run tests and commit**

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
- `liepin-smoke` requires `--live`;
- `liepin-smoke --live` requires compliance gate ref, connection ID, tenant/workspace, actor, and account hash;
- `liepin-smoke --live` refuses fake fixture worker mode;
- `liepin-smoke --live --max-detail-opens 1` passes max budget into detail policy.

- [ ] **Step 2: Run tests and confirm failure**

```bash
uv run pytest tests/test_liepin_cli.py -q
```

Expected: missing commands fail.

- [ ] **Step 3: Implement commands**

Commands:

- `seektalent liepin-replay-fixtures`
- `seektalent liepin-bun-compatibility-gate`
- `seektalent liepin-smoke --live --tenant-id ... --workspace-id ... --actor-id ... --connection-id ... --compliance-gate-ref ... --provider-account-hash ... --max-detail-opens 1`

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
- no Liepin mapper writes raw payload into `ResumeCandidate.raw`;
- fake fixture mode is not reachable when `liepin_live_enabled=True`;
- card/detail score evidence source appears in flywheel rows when detail enrichment exists.

- [ ] **Step 2: Run all focused checks**

```bash
uv run pytest tests/test_liepin_boundary_preflight.py tests/test_liepin_api_scope.py tests/test_liepin_compliance_gate.py tests/test_liepin_session_store.py tests/test_liepin_detail_ledger.py tests/test_liepin_detail_policy.py tests/test_liepin_worker_client.py tests/test_liepin_provider_mapping.py tests/test_liepin_corpus_integration.py tests/test_liepin_provider_adapter.py tests/test_liepin_detail_integration.py tests/test_liepin_verified_loop.py tests/test_liepin_cli.py tests/test_liepin_boundaries.py -q
cd apps/liepin-worker && bun test && bun run typecheck && bun run boundary-check && bun run compatibility-gate
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

1. Run `seektalent liepin-bun-compatibility-gate`.
2. Create or verify a compliance gate with exact `"search"` purpose.
3. Create a connection and handoff login to the user.
4. Confirm session status is `ready`.
5. Run one card search with zero detail opens.
6. Confirm card snapshots were saved to corpus and raw payloads did not enter run results.
7. Run one detail-open smoke with `--max-detail-opens 1`.
8. Confirm detail ledger counted the attempt for the provider day.
9. Confirm unknown or failed detail consumption is treated conservatively.
10. Confirm artifacts/logs contain no cookies, auth headers, storageState, CDP/debug URLs, or raw candidate-identifying fixture payloads.

## Self-Review Checklist

- Bun V1 runner is preserved and gated by Task 8.
- External/client-facing API is Python-owned in Task 2.
- Bun worker is internal-only in Tasks 3, 8, 10, and 14.
- Compliance gate is stored and enforced before live worker calls in Tasks 2 and 11.
- Candidate personal information processing basis, processor, deletion SLA, audit owner, and raw detail retention are modeled in Task 2.
- Protected session store and revoke are implemented in Task 3.
- Fake worker mode is explicit and test-only in Task 5.
- Detail ledger is per-day, transactional, and stateful in Task 4.
- Raw provider payload does not enter `ResumeCandidate.raw` in Task 6.
- Network extraction is passive and page-triggered in Task 9.
- APIRequestContext and cookie-sharing request APIs are forbidden by Tasks 7 and 14.
- Managed login is present in Tasks 3 and 10.
- `open_details` is wired through policy, ledger, worker, corpus, and scoring in Task 12.
- Card-only and detail-enriched evidence are separated in Task 12.
- Corpus artifact kind is verified in Task 0; it is not invented by this plan.
- Query identity test no longer expects `run_` prefix in Task 11.
