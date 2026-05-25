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
- Liepin native filter adapter: compile Runtime city, work-experience, age, and other confirmed JD filters into safe Liepin browser UI actions so Liepin and CTS use the same Runtime search logic with provider-specific execution. The current Liepin card search receives Runtime filter intent but reports unsupported/degraded coverage instead of clicking native filters.
- Offline entity-merge evaluation set: create redacted, replayable same-person/different-person cases to measure false-positive and false-negative identity merge rates, especially for masked Liepin names.
- Trace context alignment: map runtime run id, source plan id, source lane run id, attempt, and event sequence to a standard trace/correlation format if source lanes later become out-of-process.
- A2A bridge evaluation: revisit only if PI Agent becomes out-of-process with separate lifecycle, identity, capability discovery, and negotiated task execution.
- Provider candidate hash migration: evaluate tenant-scoped HMAC provider-key hashes without breaking existing Workbench data.
- Artifact access controls: expand protected artifact classification, retention, open audit, and notes/graph resolution policy beyond the first source-lane safety contract.
- Runtime request object expansion: move the full `WorkflowRuntime.run(...)` API to a richer request object when budgets, actor context, artifact policy, and entitlement policy become real runtime inputs.
- Generic ProviderAgentExecutor protocol: extract a reusable provider-agent executor interface after the Liepin Pi path proves stable across real runs.
- Generic external-agent local setup model: extract `PiAgentLocalSetupStatus` into a provider-agnostic external-agent readiness model only after a second browser-backed source needs it.
- Confirm and pin the official DokoBot MCP server startup command/tool names once DokoBot exposes a stable local command/config export. The current Pi bridge refuses fake defaults, accepts explicit root `.env` settings, and blocks Liepin live runs until those values are proven.
- Browser capability abstraction: map provider requirements to generic `browser.read`, `browser.navigate`, `browser.click`, and `browser.type_text` capabilities once another MCP/browser helper is planned; keep the first slice DokoBot-specific.
- Protected tool-manifest handshake: let Pi report adapter version, server name, declared tools, observed tools, and allowed hosts through protected artifact refs instead of requiring manual `SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON` configuration.
- Protected Pi MCP adapter proxy-proof validation: add a safe proof path for `pi-mcp-adapter` proxy-only tool calls if direct DokoBot tools cannot be exposed reliably. The first implementation accepts direct observed Pi tool events only.
- Shared safe public payload/redaction utility: consolidate path/secret/raw-output redaction across CLI, Workbench, Runtime events, and external-agent diagnostics after the Pi setup path proves stable.
- Global doctor public-safe JSON mode: design a separate path-redacted public diagnostic payload if doctor output is later shown outside developer/operator surfaces; the current doctor may show non-sensitive local paths by design.
- Legacy direct DokoBot CLI diagnostics cleanup: decide whether to remove or isolate `src/seektalent/providers/pi_agent/dokobot_client.py` and `capabilities.py` after the Pi-owned MCP path is stable; these modules must not become Runtime/Workbench live execution paths.
- Dedicated `pi-agent probe` CLI: consider adding `seektalent pi-agent probe --json` only after `doctor --live-pi-agent --json` has stabilized and there is a clear operator need for a separate live Pi probe entry point.
- Full Pi/DokoBot trace replay harness: store replayable protected golden traces for browser-agent regression testing beyond the first card-mode trace validator.
- Multi-action backend support: evaluate browser_mcp, Pi extension browser tools, and human-assisted mode only after the Pi+DokoBot path is stable; do not add fallback selection in the first implementation.
- Artifact registry expansion for external agents: add retention, protected-open audit, and UI access policy for Pi/DokoBot protected artifacts beyond the first minimal local registry and protected material resolver.
- Provider-agent capability descriptor: define a small source capability descriptor once at least one more browser source is planned; avoid building a plugin marketplace.

**Effort:** XL
**Priority:** P2
**Depends on:** Runtime multi-source source-lane build, Workbench graph/notes coverage, detail approval lease path, and real multi-source usage feedback.

## Frontend

### Runtime Strategy Graph Node Detail Completeness Audit

**What:** Audit the Svelte Runtime strategy graph node-detail coverage after the multi-source graph layout change, and make every business node render the data it owns rather than falling back to sparse generic payload rows.

**Why:** Real Chrome QA showed several graph nodes are clickable but do not expose the node-specific content recruiters expect. This is likely from newer Runtime round/source/merge/finalization nodes outgrowing the existing `detailKind`/`detailPayload` renderers.

**Context:** Start from `apps/web-svelte/src/lib/workbench/runStory.ts`, `apps/web-svelte/src/lib/components/NodeDetailPanel.svelte`, and `apps/web-svelte/src/lib/components/GraphNodeCandidateList.svelte`. Compare each generated Runtime node type with the intended detail content: logical query bundle, per-source counts/status/reasons, merged identity counts, scoring/top-pool snapshot, feedback/reflection, finalization order, Liepin card recommendations, and detail approval state.

**Effort:** M
**Priority:** P1
**Depends on:** Runtime multi-source graph layout stabilization and real-session event payloads.

### Runtime Workbench Note Projection Consistency

**What:** Align Workbench running notes with runtime source-card and public-event projections so notes cannot continue saying both sources are still waiting after durable `runtime_round_source_result`, merge, or scoring events have already arrived.

**Why:** Real Chrome QA showed source cards correctly advancing CTS/Liepin counts while the running notes still repeated stale waiting language. This makes the run look stuck even when Runtime has returned source results and moved into merge/scoring.

**Context:** Start from `src/seektalent_ui/workbench_routes.py`, note writer validation/drop handling, and the Svelte run-note rendering path. The fix should make note generation consume durable Runtime public events or source-count projections, and should cover note validation drops so stale notes do not dominate the visible run narrative.

**Effort:** M
**Priority:** P1
**Depends on:** Runtime public event projection and Workbench source-card count projection.

### Svelte React-Parity Follow-Ups

**What:** Finish the work that should remain outside the current Svelte parity migration slice.

**Why:** The active Svelte frontend now targets React Workbench parity. These items are real follow-ups, but they should not block the current verification/docs cleanup milestone.

**Deferred items:**

- Remove the React app only after Svelte parity is signed off by the user.
- Automate visual snapshot baselines for the React/Svelte route parity set.
- Optimize Svelte bundle size and graph performance after parity behavior is stable.
- Broaden source connection UX after the Pi-first live path stabilizes.

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
