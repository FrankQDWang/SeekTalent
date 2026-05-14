# Provider Interaction Agent With DokoBot Design

## Purpose

Liepin execution requires browser-like interaction through the user's logged-in provider account: search by keywords, turn pages, read card summaries, decide whether a candidate is worth a detail-open request, open approved details, and extract full resume content. DokoBot is therefore a core provider execution engine, not an optional reader.

This spec defines a provider-scoped PI Agent, short for Provider Interaction Agent. It is the bounded web operator that uses DokoBot as hands and feet while the core workflow runtime remains the authority for policy, budget, scoring, artifacts, and audit.

## Product Contract

```text
WorkflowRuntime
  owns JD/session state, query plans, budget, approval, scoring, artifacts, and audit

Liepin Source Runner
  owns source-run lifecycle and creates typed provider interaction tasks

PI Agent
  owns bounded provider page work through DokoBot skills

DokoBot / compatible backend
  executes browser-backed read or action operations in the user's logged-in account after capability negotiation
```

The PI Agent can be agentic inside the provider interaction boundary. It cannot self-authorize budget, bypass detail approval, rewrite global search strategy, expose provider secrets, or write raw candidate data into ordinary UI/artifacts.

## Current Code Facts

- `docs/ui.md` already treats Liepin as summary-first with human-confirmed detail-open approval by default.
- `apps/liepin-worker` currently owns Playwright/Bun worker pieces such as card search, detail open, login relay, extraction, and redaction.
- Existing Liepin plans forbid direct authenticated API replay and require page-triggered provider actions.
- DokoBot local mode is available on this machine and has already been used successfully for browser-backed collection in a separate project.
- `src/seektalent/providers/liepin` already contains adapter, worker client, compliance, policy, store, and verified-loop modules.

## Decisions

1. Rename the conceptual execution layer from generic browser worker to provider-scoped PI Agent where DokoBot is the execution engine.
2. Keep provider interaction tasks typed and narrow:
   - `liepin.search_cards`;
   - `liepin.read_card_page`;
   - `liepin.classify_card_summary`;
   - `liepin.request_detail_open`;
   - `liepin.open_detail_after_approval`;
   - `liepin.extract_detail_resume`;
   - `liepin.detect_login_or_risk_state`.
3. Represent each provider skill as a versioned action recipe with allowed URLs, allowed actions, output schema, redaction policy, failure codes, pacing, and evidence requirements.
4. Preserve core runtime authority. The PI Agent returns observations, candidate summaries, detail requests, detail payload refs, and action traces. Workflow runtime decides scoring, approval, budget, stop/continue, and finalization.
5. Treat raw provider material as protected snapshots only.
6. Separate DokoBot capability discovery from Liepin execution. `dokobot read` is enough for page snapshot extraction, but it is not enough for search, paging, clicking, or detail-open execution. The implementation must bind an already configured, explicitly action-capable DokoBot-compatible manifest before `dokobot_action` can execute provider actions. If that manifest is unavailable, `dokobot_action` must fail closed; it must not attempt to install tools, silently downgrade to read-only, or silently switch to another backend.
7. Model PI Agent tasks and actions as discriminated unions. Search, risk detection, approval request, detail opening, and resume extraction must not share one broad payload shape.
8. Require a WorkflowRuntime-issued detail-open grant before any detail page open. Budget reservation, approval id, candidate ref, expiry, and idempotency key are runtime-owned data, not PI Agent decisions.

## PI Action Contract

The PI Agent speaks in provider-scoped actions, not raw browser APIs:

- `navigate_to_search`;
- `submit_keyword_search`;
- `read_card_page`;
- `turn_page`;
- `classify_card_summary`;
- `request_detail_open`;
- `open_detail_after_approval`;
- `extract_detail_resume`;
- `detect_login_or_risk_state`.

Each action has:

- typed input;
- allowed host plus pre-action route and post-action route expectations;
- allowed UI operation category;
- forbidden operation list;
- expected observation schema;
- redacted evidence requirement.

Forbidden operations always include direct authenticated request replay, `page.request`, `browserContext.request`, `APIRequestContext`, provider signature generation, stealth plugins, proxy rotation, header/cookie injection, DokoBot/DevTools network request inspection, and arbitrary in-page script evaluation.

## Runtime Grant Contract

The PI Agent cannot infer permission from `detail_policy` or a remaining budget count. `open_detail_after_approval` requires a runtime-issued grant:

```json
{
  "schema_version": "detail-open-grant-v1",
  "approval_id": "approval_123",
  "budget_reservation_id": "budget_reservation_123",
  "candidate_ref": "candidate_ref_123",
  "source_run_id": "source_run_123",
  "provider": "liepin",
  "max_detail_opens": 1,
  "expires_at": "2026-05-14T08:30:00Z",
  "issued_by": "workflow_runtime",
  "idempotency_key": "detail_open_candidate_ref_123_approval_123",
  "grant_signature": "runtime-signature"
}
```

Missing, expired, or mismatched grants must block with stable failure codes before execution. Duplicate grant execution must be prevented by durable runtime reservation/idempotency state, not by PI Agent memory. Bypass-confirm mode still uses a runtime grant; it only changes how the runtime obtains approval before issuing the grant.

## DokoBot Capability Contract

DokoBot is negotiated as a backend capability, not assumed:

- `read` support can create text/chunk snapshots.
- The public `dokobot read` CLI is read-only for PI Agent purposes. It must not be treated as enough to type into a provider search box or submit a search.
- Typing into the Liepin keyword input requires an explicitly discovered DokoBot-compatible action manifest that declares text-entry, click, navigation, and pagination operations. The implementation must treat public `dokobot read` / `dokobot search` capability as read-only unless the action manifest proves otherwise.
- session continuation must model `session_id`, whether more page content is available, stop reason, screen count, duration, and redacted stderr.
- click/type/navigation/pagination support must be discovered explicitly before live action use.
- if action capability is unavailable, `dokobot_action` mode must fail closed with `dokobot_action_capability_unavailable`.
- no implementation may auto-install a DokoBot action surface, mutate browser tool permissions, or auto-select `legacy_worker_compat` as a fallback.
- `legacy_worker_compat` may remain only as an operator-selected explicit backend mode while DokoBot action capability is not available.

## Backend Modes

Initial backend modes are:

- `disabled`;
- `dokobot_read_only`;
- `dokobot_action`;
- `legacy_worker_compat`;
- `fake_fixture`.

`dokobot_read_only` cannot submit searches, click pagination, or open details. `dokobot_action` requires an already configured action manifest. `legacy_worker_compat` is allowed only when explicitly configured before the run starts. `fake_fixture` is test/dev only. Silent fallback or automatic downgrade between modes is forbidden.

## Protected Artifact Contract

Provider material is split into three artifact classes:

- `safe_summary_artifact`: safe UI-facing candidate/source-run summaries.
- `redacted_evidence_artifact`: redacted text or visual evidence for audit and replay.
- `protected_provider_snapshot`: raw or near-raw provider material with stricter access, shorter retention, and access audit.

Safe summaries and redacted evidence must record the redaction policy that made them safe to expose. Protected provider snapshots must record a protection policy and must not be labeled as redacted evidence. Artifact refs must be non-empty opaque store refs, not local paths, traversal strings, or raw URI-like paths.

Ordinary workbench UI and ordinary logs can read only safe summaries. Audit/replay code can read redacted evidence. Protected snapshots require a stricter reader path and must not appear in stdout, stderr, SSE payloads, ordinary candidate fields, or unredacted exception messages.

## Concurrency Contract

Liepin runs are scoped to a logged-in provider account and browser profile. The initial implementation must enforce:

- one active PI run per `connection_id`;
- one active browser profile lock per provider account;
- no shared `--reuse-tab` execution across concurrent source runs;
- explicit stop state if a lock cannot be acquired.

Parallelism can be added later only through separate provider connections or isolated browser profiles.

## Non-Goals

- No generic web automation platform.
- No arbitrary user prompt controlling browser actions.
- No bypass of Liepin compliance gates.
- No CAPTCHA bypass, stealth plugin, proxy rotation, signature generation, or direct authenticated HTTP replay.
- No migration of scoring, PRF, corpus, flywheel, or eval logic into the PI Agent.

## PI Agent Task Shape

Example card-search task:

```json
{
  "task_type": "liepin.search_cards",
  "schema_version": "pi-agent-task-v1",
  "session_id": "session_123",
  "source_run_id": "source_run_123",
  "connection_id": "connection_123",
  "query_terms": ["大模型", "RAG", "Python"],
  "keyword_query": "大模型 RAG Python",
  "max_pages": 5,
  "max_cards": 80,
  "stop_conditions": ["page_exhausted", "enough_strong_cards", "risk_control"],
  "artifact_policy": "protected_snapshots_only"
}
```

Example result:

```json
{
  "schema_version": "pi-agent-result-v1",
  "status": "needs_approval",
  "cards_seen": 80,
  "cards_selected": 12,
  "detail_requests": 6,
  "details_opened": 0,
  "stop_reason": "detail_budget_waiting_for_human",
  "action_trace_ref": {
    "artifact_class": "redacted_evidence_artifact",
    "artifact_ref": "artifact_trace_123",
    "content_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "redaction_policy_id": "liepin-trace-redaction-v1"
  },
  "protected_snapshot_refs": [
    {
      "artifact_class": "protected_provider_snapshot",
      "artifact_ref": "snapshot_123",
      "content_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
      "protection_policy_id": "liepin-protected-snapshot-v1"
    }
  ],
  "safe_summary_refs": [
    {
      "artifact_class": "safe_summary_artifact",
      "artifact_ref": "summary_123",
      "content_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
      "redaction_policy_id": "liepin-summary-redaction-v1"
    }
  ]
}
```

## Safety And Audit Requirements

- Every DokoBot action must produce an action trace entry with a timezone-aware timestamp, provider skill id, safe target descriptor, result code, and redacted evidence ref.
- Action traces must also carry interaction id, source run id, connection id, action sequence, backend mode, capability version, duration, failure code when present, retry count, redaction policy id, and evidence hash.
- Action trace result code, failure code, evidence ref, and evidence hash must be internally consistent: successful trace rows cannot carry failure codes, blocked/failed rows require failure codes, and evidence refs/hashes must appear together.
- The ordinary UI receives source-run state and safe candidate summaries, not raw DokoBot output.
- Boundary model validation errors must hide raw input values so candidate material, grant signatures, provider snapshots, and command output cannot leak through exceptions or logs.
- Detail open attempts must reserve budget before the PI Agent opens the page.
- Human-confirm mode must create approval requests and stop before detail opening.
- Bypass mode may skip per-candidate approval only after core runtime policy checks.
- Login expired, verification required, risk control, selector drift, extraction failure, and page timeout must be distinct failure codes.
- Normal completion reasons such as page exhausted, enough strong cards, budget exhausted, and waiting for human approval must be modeled separately from failure codes.
- PI results must validate `status` and `stop_reason` together so success cannot carry failure reasons, blocked/failed results require failure reasons, and approval waits use the dedicated human-approval completion reason.
- Liepin skill URL matching must reject direct API/AJAX-like routes even when they appear under an otherwise allowed UI route prefix.

## Implementation Plan Set

This spec is implemented through a linked plan set rather than one large plan:

- `docs/superpowers/plans/2026-05-13-provider-interaction-agent-dokobot.md` is the plan-set index.
- `docs/superpowers/plans/2026-05-13-pi-agent-contracts-and-skill-recipes.md` builds the typed PI contracts and Liepin skill recipes.
- `docs/superpowers/plans/2026-05-13-dokobot-capability-and-protected-artifacts.md` builds DokoBot capability probing, explicit action manifest handling, structured read results, and artifact leakage guards.
- `docs/superpowers/plans/2026-05-13-detail-grants-and-backend-dispatch.md` builds detail grant validation, durable ledger/idempotency verification, connection/provider-account locking, and explicit runner primitives.
- `docs/superpowers/plans/2026-05-13-pi-agent-boundary-guards-and-compat.md` builds direct API replay guardrails and compatibility verification.

## Acceptance Criteria

- There is a typed PI Agent task/result contract in Python.
- There is a typed PI action contract that covers search, paging, card reading, detail opening, extraction, and login/risk detection.
- Detail opening requires a runtime-issued `detail-open-grant-v1` and idempotency key.
- PI tasks and actions use discriminated unions with typed payloads.
- Liepin source runner dispatches PI Agent tasks instead of embedding DokoBot command details directly in workflow runtime.
- DokoBot capabilities are discovered and versioned before live use; unsupported action capabilities fail closed instead of falling back, downgrading, or installing another surface.
- DokoBot action mode never attempts automatic tool installation, permission mutation, read-only downgrade, or automatic `legacy_worker_compat` fallback.
- DokoBot read results include session continuation state and redacted command output references.
- DokoBot skills or compatibility backends are versioned and fixture-tested.
- The PI Agent has replay tests using redacted page snapshots/action traces.
- Protected provider snapshots, redacted evidence, and safe summaries have distinct access paths.
- Same-connection concurrent source runs are blocked unless the connection has an isolated browser profile.
- The workbench can show cards seen, cards selected, details requested, details opened, and stop reason without raw provider leakage.
- Existing Liepin worker boundary tests are updated or superseded by PI Agent boundary tests.
