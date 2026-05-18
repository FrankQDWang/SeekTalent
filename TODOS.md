# TODOS

## Product

### Static TREC-Pooling Benchmark And First-Party Search Engine

**What:** Build a static TREC-pooling benchmark and first-party resume search engine from governed corpus exports.

**Why:** This enables repeatable evaluation of retrieval strategies, model changes, source adapters, and ranking quality without depending only on live provider searches.

**Context:** The current workbench plan preserves provenance such as `jd_doc_id`, query fingerprint, provider/page/rank, `resume_doc_id`, `observation_id`, detail ledger state, and human actions, but deliberately does not create workbench-owned qrels, pool versions, benchmark manifests, or search-engine tables. Start from the existing CorpusStore raw-payload boundary and benchmark CLI/evaluation code, then design immutable corpus exports, pool versions, qrels, redaction policy, and execution-result storage outside `seektalent_ui`.

**Effort:** XL
**Priority:** P2
**Depends on:** Multi-source workbench candidate evidence, human review actions, authorized raw artifact access, and corpus provenance from M2/M4/M5.

### Post-Run Learning Capsules

**What:** Add personalized post-run learning capsules that give recruiters short domain or search-strategy tips during idle moments or after a session.

**Why:** Recruiters often search across unfamiliar domains; lightweight learning can improve their judgment and keyword strategy over time.

**Context:** The current workbench plan defers this because memory, candidate feedback, and privacy boundaries must stabilize first. The feature should use redacted recruiter/workflow memory, not raw resumes, contact details, private candidate text, or sensitive evaluations. Useful starting points are session outcomes, user-approved notes/actions, repeated search failures, and high-level domain concepts.

**Effort:** L
**Priority:** P3
**Depends on:** Candidate actions/notes, memory firewall, enough real session history, and user-configurable tip frequency.

### Runtime Multi-Source Platform Follow-Ups

**What:** Extend the runtime multi-source source-lane contract after the first CTS/Liepin implementation lands.

**Why:** The current runtime multi-source plan should first make lane-local execution, source evidence merge, detail recommendations, approved detail leases, typed public events, and Workbench graph/notes behavior correct. Larger platform capabilities are useful, but they should not block the first safe multi-source runtime contract.

**Deferred items:**

- Human card-review UI: let recruiters review Liepin cards before approval or detail opening once the Runtime recommendation contract is stable.
- Manual detail-open approval UI: expose approved-detail request, lease, budget, and audit state without letting Workbench execute provider logic directly.
- Manual source budget editing UI: allow user-visible CTS/Liepin budget changes after the runtime-owned budget policy has enough guardrails.
- Candidate Evidence Graph: model candidate identity, source evidence, artifact refs, action traces, detail recommendations, scorecards, and finalization decisions as an inspectable graph.
- Source Capability Descriptor: add a small descriptor for future API/browser/research sources without creating a broad plugin marketplace.
- Trusted browser action conformance: after the first live DokoBot action path lands, expand DokoBot and future browser backends with dry-run support, broader action audit, richer domain policy, and reusable conformance tests.
- Lane health, cost, and quality metrics: track latency, cards seen, selected candidates, detail opens, duplicate rate, blocked rate, cost estimate, and marginal quality for later Runtime source strategy optimization.
- Progressive enrichment: split card search, detail recommendation, approved detail fetch, and verification lanes once the first card/detail API boundary is stable.
- Offline entity-merge evaluation set: create redacted, replayable same-person/different-person cases to measure false-positive and false-negative identity merge rates, especially for masked Liepin names.
- Trace context alignment: map runtime run id, source plan id, source lane run id, attempt, and event sequence to a standard trace/correlation format if source lanes later become out-of-process.
- A2A bridge evaluation: revisit only if PI Agent becomes out-of-process with separate lifecycle, identity, capability discovery, and negotiated task execution.
- Provider candidate hash migration: evaluate tenant-scoped HMAC provider-key hashes without breaking existing Workbench data.
- Artifact access controls: expand protected artifact classification, retention, open audit, and notes/graph resolution policy beyond the first source-lane safety contract.
- Runtime request object expansion: move the full `WorkflowRuntime.run(...)` API to a richer request object when budgets, actor context, artifact policy, and entitlement policy become real runtime inputs.
- Generic ProviderAgentExecutor protocol: extract a reusable provider-agent executor interface after the Liepin Pi path proves stable across real runs.
- Full Pi/DokoBot trace replay harness: store replayable protected golden traces for browser-agent regression testing beyond the first card-mode trace validator.
- Multi-action backend support: evaluate browser_mcp, Pi extension browser tools, and human-assisted mode only after the Pi+DokoBot path is stable; do not add fallback selection in the first implementation.
- Artifact registry expansion for external agents: add retention, protected-open audit, and UI access policy for Pi/DokoBot protected artifacts beyond the first minimal local registry and protected material resolver.
- Provider-agent capability descriptor: define a small source capability descriptor once at least one more browser source is planned; avoid building a plugin marketplace.

**Effort:** XL
**Priority:** P2
**Depends on:** Runtime multi-source source-lane build, Workbench graph/notes coverage, detail approval lease path, and real multi-source usage feedback.

## Frontend

### Svelte 5 Migration Follow-Ups

**What:** Harden the SvelteKit spike into a migration-ready frontend path before replacing the React app.

**Why:** The spike proved the Svelte 5 + SvelteKit + OpenAPI + Svelte Query + Svelte Flow direction, but full migration still needs real backend fixtures, production bundle work, and stronger contract automation.

**Deferred items:**

- Add a seeded live-backend integration fixture with at least one real non-trivial Workbench session, graph-candidates result, and resume snapshot.
- Split Svelte Flow and ELK from the initial detail route bundle with dynamic import; move ELK layout to a Web Worker if 50/100/300-node smoke tests show main-thread jank.
- Promote the local OpenAPI schema drift check from `scripts/verify-dev-workbench.sh` into CI so generated frontend types stay aligned with FastAPI response models.
- Choose one UI route for the Svelte app, such as Skeleton or shadcn-svelte plus Bits UI, before migrating broad CRUD/form surfaces.
- Add ESLint boundary rules that prevent route/components from raw `fetch`, direct generated-client imports, or imports from `apps/web/src`.
- Harden EventSource operations after the parity migration: reconnect/backoff UX, browser offline behavior, replay from `Last-Event-ID`, and measured fallback polling only when SSE is unavailable.
- Add an internal `SourceLaneDescriptor` for UI metadata such as source label, auth mode, card/detail support, badge labels, source-card position, and budgeted-detail affordances. Keep it internal; do not turn it into a provider marketplace.
- Promote coverage and merge explainability from basic final-queue copy into a reusable UI model once the Svelte parity route is stable: merged duplicate count, ambiguous duplicate count, canonical-source reason, and source coverage reason in recruiter-facing language.
- Add a secondary settings/developer diagnostics surface for local BYOK readiness and safe component status, keeping local paths, data-root posture, secrets, protected artifact refs, Pi prompts, and raw provider payloads out of the primary recruiter UI.
- Build an offline final-candidate view-model fixture set covering duplicate CTS/Liepin identities, newer Liepin detail replacing older CTS evidence, Liepin card-only detail recommendations, blocked Liepin with CTS-only coverage, and conflicting merged review-item statuses.

**Effort:** L
**Priority:** P1
**Depends on:** Svelte 5 frontend spike report, seeded Workbench data, and the next approved migration slice.

### Storybook Component Catalog

**What:** Introduce Storybook for stable workbench components such as source cards, candidate cards, detail approval queues, and session rail states.

**Why:** A component catalog will make complex UI states easier to review once the product shape stabilizes.

**Context:** M0-M6 should not start with Storybook. The current plan uses Playwright plus `odiff-bin` for page-level visual smoke tests because the immediate risk is structural layout drift from the reference HTML. Revisit Storybook after source card, candidate card, detail approval queue, and session rail components are stable enough to avoid story churn.

**Effort:** M
**Priority:** P3
**Depends on:** Stable M2/M3 component shapes and repeated UI states worth cataloging.

## Infrastructure

### Local Product Platform Follow-Ups

**What:** Split the larger local-product platform work into later plans instead of adding it to the first local product contract slice.

**Why:** The current local product contract should establish wording, data-root posture, inspect/doctor safety, and non-leakage checks. Full storage, security posture, schema, installer, connector, entitlement, and launcher work is broader and should be planned separately.

**Deferred items:**

- Complete SQLite lifecycle: WAL policy, busy timeout, migration locking, checkpointing, and cross-database backup/restore. The current workbench already has a SQLite backup path, so this should become a focused local storage reliability plan rather than blocking the first contract slice.
- Local web security posture expansion: Host/Origin/CSRF risks are real, but `network_guard.py` and `tests/test_workbench_network_guard.py` already cover the core guard. Later work should surface network posture in inspect/doctor instead of rewriting the guard here.
- JSON Schema / OpenAPI contracts: add `contract_version` and field tests now; full schema files and OpenAPI contract tests should be a later compatibility plan.
- Platform and packaging expansion: platform-specific user data directories, provider connector posture plugins, entitlement leases/offline grace, and the complete `seektalent workbench` launcher argument/output contract belong to later productization or packaging plans.

**Effort:** L
**Priority:** P1
**Depends on:** Local product contract build, entitlement/key-control plan, and packaging direction.

### Cloud Deployment Migration

**What:** Design the cloud deployment version with domain, HTTPS, Postgres, formal queue/worker, backups, monitoring, and stronger multi-user tenant isolation.

**Why:** The current V1 is an internal LAN experiment, but the product direction includes many users on a cloud server later.

**Context:** The workbench plan keeps V1 on SQLite plus a local SourceRun job runner with explicit LAN mode and public internet exposure out of scope. The state model, source-run job boundary, detail ledger, audit events, and tenant/workspace/user scoping should make a later Postgres/queue migration possible without changing the business contract.

**Effort:** XL
**Priority:** P2
**Depends on:** M0-M6 internal workbench validation, real usage feedback, stable source-run/job/ledger state machines, and a separate cloud security review.

## Completed
