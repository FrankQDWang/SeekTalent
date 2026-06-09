# Goal 2 Conversational Agent Plan

This compacted document preserves the content from the source documents below. Source headings are demoted one level so the merged document remains navigable without changing the substantive requirements.

## Source Documents

- `goal-2-conversational-agent/05-acceptance-criteria.md`
- `goal-2-conversational-agent/06-implementation-sequence.md`
- `goal-2-conversational-agent/07-execution-control.md`

---

## Source: `goal-2-conversational-agent/05-acceptance-criteria.md`

## Goal 2 Acceptance Criteria

### Product Acceptance

1. A backend caller can create and reload an agent conversation.
2. A backend caller can list, rename, archive, unarchive, and reopen conversations through backend metadata APIs.
3. Reopen returns `conversation_reopen_state` with title, archive state, cursors, runtime links, pending counts, compaction cursor, and allowed actions.
4. A backend caller can submit a JD or rough hiring request.
5. The agent calls runtime control and returns a structured requirement draft view model.
6. Requirement draft data contains the five required sections with Chinese labels.
7. Every extracted item has selected-by-default state in persisted response data.
8. A backend caller can unselect, edit, delete, move supported items, and enable/disable query terms through real APIs.
9. A backend caller can add free-form extra requirements and receive normalized draft additions before confirmation.
10. Review-required free-form amendments return accept/edit/move/reject allowed actions and block confirmation until resolved.
11. Each requirement edit persists through runtime-control draft revision APIs.
12. Confirmation persists the approved requirement revision.
13. Workflow starts only after confirmation.
14. Transcript-ready progress messages are projected from runtime-control events and snapshots.
15. Runtime-control events also project into durable activity items with queued/started/in-progress/completed/failed/cancelled/superseded lifecycle states.
16. Activity item deltas can be streamed or polled, and a full conversation reload reconstructs the same state without parsing assistant text.
17. A backend caller can pause a run and receive accepted/pending/applied or rejected command state.
18. A backend caller can cancel a run and receive accepted/pending/applied or rejected command state.
19. A backend caller can resume a paused run.
20. A backend caller can add a next-round requirement and receive accepted, scheduled, activated, or rejected state.
21. A backend caller can ask current-status and detail questions.
22. Detail answers cite runtime-control facts internally and do not expose unsafe raw payloads.
23. Final summary is grounded in final runtime result and user instruction.
24. Running next-round requirement messages show target round and do not imply current-round mutation.
25. Running next-round requirement review states do not imply scheduling until review resolution succeeds.
26. Conversation-agent retention and compaction protect terminal transcript storage without corrupting cursors or active conversations.
27. Long-running model-input context compaction preserves reloadable requirement review history, command history, activity item state, runtime event cursors, tool-call references, and final-summary context.
28. Advisory memory suggestions are presented as suggestions and never change requirements, runtime state, source selection, or candidate facts without normal confirmation flow.
29. Backend transcript messages and activity items expose Codex-like working-process states without requiring any Codex runtime dependency or UI implementation.
30. Long-running conversations enforce explicit token, cost, timeout, and compaction budgets with typed user-visible errors.
31. Model, tool, and stream failures produce recoverable transcript state instead of silent fallback or invented progress.

### Technical Acceptance

1. Agent package does not import `seektalent.runtime`.
2. Agent package does not import provider modules.
3. New agent business logic lives under `src/seektalent_conversation_agent/`, not under `src/seektalent/` or `src/seektalent_ui/`.
4. OpenAI Agents SDK usage is isolated behind `AgentRuntime`.
5. Product runtime does not require Codex CLI, Codex App Server, Codex MCP server, Codex SDK, `openai-codex`, or `@openai/codex-sdk`.
6. `src/seektalent_ui/` changes are limited to route wiring, DTOs, server registration, event projection, and generated static output when intentionally rebuilt.
7. `/api/agent` is covered by host/origin guard, current-user auth, and CSRF rules for writes.
8. Frontend does not read runtime-control SQLite or runtime internals.
9. Transcript state is persisted server-side through `src/seektalent_conversation_agent/`.
10. Conversation title, archive state, and reopen state are persisted server-side through `src/seektalent_conversation_agent/`.
11. Conversation store path is configured through `AppSettings` and resolved through workspace-root rules.
12. Tool call results are stored with transcript messages.
13. Latest event cursor prevents duplicate progress messages and event gaps are handled explicitly.
14. SQLite event log cursor, projection idempotency, and gap recovery satisfy `../05-sqlite-event-log-and-projection-contract.md`.
15. Requirement edit, review resolution, confirmation, and free-form amendment operations include base revision ids and handle stale revision errors.
16. Next-round requirement transcript projection handles accepted, scheduled, activated, review-required, superseded, and rejected states.
17. Activity item projection is persisted, idempotent, monotonic by `source_event_seq_latest`, and covered by tests.
18. Stream or polling responses expose activity lifecycle deltas over persisted state.
19. Routes have backend tests.
20. Conversation metadata APIs have store, route, security, and reload tests.
21. Requirement review view models have API tests.
22. Free-form amendment view models have API tests.
23. Agent tool routing and grounding evals pass.
24. Frontend type generation or API typing is current after route changes when applicable.
25. Conversation transcript and tool-call payload retention is tested.
26. Workbench static output is regenerated only when intentionally needed.
27. Boundary checks still pass.
28. `agent_context_summaries` or an equivalent persisted model-input compaction table is implemented and tested as derived state.
29. Integrated memory package, store, routes, privacy filter, recall, deletion, retention, and prompt-injection behavior satisfy `goal-2-agent-memory-extension/PLAN.md`.
30. Product code has no import, package dependency, subprocess call, or vendored source from `.external/codex-reference`.
31. The progress ledger records Codex reference evidence for each substantial phase, including event lifecycle, item lifecycle, compaction, and memory-boundary source paths.
32. `/api/agent` HTTP DTOs use camelCase, include `schemaVersion`, and have tests for incompatible schema changes.
33. State-changing `/api/agent` routes enforce local per-user/per-conversation rate limits.
34. Free-form amendment text rejects obvious candidate PII, provider payload fragments, raw resume blocks, cookies, auth headers, and secrets before runtime-control normalization.
35. Context compaction persists `in_progress`, `completed`, and `failed` state with quality-check evidence.

### Required Focused Verification

Run and record:

```bash
uv run --group dev python -m pytest tests/test_conversation_agent_store.py tests/test_conversation_agent_metadata.py tests/test_conversation_agent_service.py tests/test_conversation_agent_tools.py tests/test_conversation_agent_runtime.py tests/test_conversation_agent_routes.py tests/test_conversation_agent_security.py tests/test_agent_requirement_transcript.py tests/test_agent_requirement_review_resolution.py tests/test_agent_runtime_event_projection.py tests/test_agent_transcript_activity_projection.py tests/test_agent_final_summary.py tests/test_conversation_agent_retention.py tests/test_conversation_agent_compaction.py tests/test_workbench_api.py -q
uv run --group dev python -m pytest tests/test_agent_memory_store.py tests/test_agent_memory_settings.py tests/test_agent_memory_privacy.py tests/test_agent_memory_extraction.py tests/test_agent_memory_routes.py tests/test_agent_memory_security.py tests/test_agent_memory_consolidation.py tests/test_agent_memory_recall.py tests/test_agent_memory_agent_runtime.py tests/test_agent_memory_prompt_injection.py -q
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
uv run python tools/check_arch_imports.py
uv run --group dev python -m pytest tests/evals/test_conversation_agent_tool_routing_eval.py tests/evals/test_conversation_agent_grounding_eval.py -q
uv run --group dev python -m pytest tests/evals/test_agent_memory_extraction_eval.py tests/evals/test_agent_memory_privacy_eval.py tests/evals/test_agent_memory_prompt_injection_eval.py -q
uv run --group dev ruff check src tests
uv run --group dev ty check src tests
scripts/verify-dev-workbench.sh
scripts/verify-red-zone.sh
git diff --check
```

If route or test names change during implementation, the progress ledger must map replacement tests to the acceptance criteria above.

### Completion Evidence

The Goal 2 final packet must list:

- changed backend agent/API files and why each was touched;
- changed API/view-model files and why each was touched;
- conversation metadata, rename, archive, unarchive, and reopen-state evidence;
- runtime-control tools used;
- transcript state persistence path;
- requirement review behavior evidence;
- free-form amendment behavior evidence;
- command flow evidence;
- activity lifecycle projection and stream/poll reload evidence;
- SQLite event log cursor, projection idempotency, and gap recovery evidence;
- context compaction and reload evidence;
- integrated advisory memory evidence;
- Codex reference evidence, including inspected source paths and local adaptations or rejected patterns;
- no-scaffold scan evidence;
- final summary evidence;
- verification output;
- remaining risks, if any.

---

## Source: `goal-2-conversational-agent/06-implementation-sequence.md`

## Goal 2 Implementation Sequence

### Phase 1: Preflight And Goal 1 Evidence

1. Run shared preflight from `../04-operating-policies-and-runtime-contracts.md`.
2. Read Goal 1 final packet and `goal-1-runtime-control-plane/progress.md`.
3. Read `../05-sqlite-event-log-and-projection-contract.md`.
4. Verify runtime-control APIs, tests, migrations, SQLite event log, event transaction behavior, event cursor reads, and gap recovery exist.
5. Inspect current Workbench frontend and backend route structure.
6. Record branch, HEAD, dirty state, stashes, and Goal 1 evidence in `progress.md`.
7. Record the current Goal 2 bootstrap gaps from `SPEC.md` and do not treat split runtime-control service ownership as missing Goal 1 implementation.
8. Read `../goal-2-agent-memory-extension/SPEC.md` and `../goal-2-agent-memory-extension/PLAN.md` so the core transcript interfaces expose the required memory integration points.
9. Verify `.external/codex-reference` exists, record its commit, and add initial Codex reference targets to the ledger.
10. Inspect and record Codex reference paths for transcript lifecycle, item lifecycle, app-server thread items, compaction, and memory boundaries:
   - `.external/codex-reference/sdk/typescript/src/events.ts`
   - `.external/codex-reference/sdk/typescript/src/items.ts`
   - `.external/codex-reference/codex-rs/app-server-protocol/schema/typescript/v2/ThreadItem.ts`
   - `.external/codex-reference/codex-rs/app-server-protocol/src/protocol/common.rs`
   - `.external/codex-reference/codex-rs/core/src/compact_remote.rs`
   - `.external/codex-reference/codex-rs/core/src/session/mod.rs`
   - `.external/codex-reference/codex-rs/ext/memories/src/extension.rs`

Verification:

```bash
uv run --group dev python -m pytest tests/test_runtime_control_store.py tests/test_runtime_control_commands.py tests/test_runtime_control_workflow_adapter.py -q
uv run python tools/check_source_boundaries.py
uv run python tools/check_arch_imports.py
test -d .external/codex-reference && git -C .external/codex-reference rev-parse HEAD
test -f conversational-agent-runtime-goal-pack/05-sqlite-event-log-and-projection-contract.md && echo "sqlite event log contract present" || echo "MISSING sqlite event log contract"
```

### Phase 1A: Bootstrap And Contract Corrections

Complete these corrections before Phase 2 product agent implementation:

1. Add `openai-agents` to project dependencies and update `uv.lock`.
2. Register `seektalent_conversation_agent` in `pyproject.toml` build metadata.
3. Add `src/seektalent_conversation_agent/` with the first tested conversation store/service code. Do not add an empty package as the only deliverable.
4. Add a `tach.toml` module for `seektalent_conversation_agent` with dependency on `seektalent_runtime_control` and any other explicitly allowed public packages only.
5. Add `AppSettings.conversation_agent_db_path`, a workspace-root-resolved `conversation_agent_path` property, `.env.example` documentation, and focused settings tests.
6. Expand runtime-control public exports or add a narrow tool-facing facade for the 15 runtime-control tool names, including facade methods for `get_workflow_snapshot` and `list_workflow_events` if they remain backed by store methods.
7. Verify and, if still missing, implement running next-round `needs_review` resolution in runtime-control: `runtimeRunId`, `baseApprovedRequirementRevisionId`, amendment stale detection, retargeting after `runtime_round_input_locked`, and `runtime_no_future_round_available`.
8. Keep the existing conversation-agent source-boundary rules unless a new import pattern needs additional coverage.
9. Add or confirm product boundary tests that forbid importing, packaging, vendoring, or shelling out to `.external/codex-reference` or Codex runtime components.
10. Record Codex reference evidence for bootstrap choices in `progress.md`.
11. Add agent budget and timeout settings with defaults in `AppSettings` and `.env.example`.
12. Add stable reason-code constants or typed errors for `agent_token_budget_exceeded`, `agent_cost_budget_exceeded`, `agent_model_timeout`, `agent_model_unavailable`, `agent_tool_timeout`, `agent_stream_disconnected`, and `agent_rate_limited`.

Verification:

```bash
uv run --group dev python -m pytest tests/test_runtime_control_next_round_requirements.py tests/test_runtime_control_requirement_review.py tests/test_runtime_control_store.py -q
uv run --group dev python -m pytest tests/test_source_boundaries.py tests/test_agent_safety_gate.py -q
uv run --group dev python -m pytest tests/test_conversation_agent_budget_policy.py tests/test_conversation_agent_error_recovery.py -q
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
uv run python tools/check_arch_imports.py
rg -n "TODO|FIXME|placeholder|mock|fake|stub|dummy|hard-coded" src/seektalent_conversation_agent src/seektalent_agent_memory src/seektalent_ui tests || test $? -eq 1
```

### Phase 2: Conversation Store And Agent Service

1. Continue the `src/seektalent_conversation_agent/` package created in Phase 1A.
2. Add conversation and transcript message models.
3. Add transcript activity item models and store methods using deterministic activity keys.
4. Add conversation metadata fields and store methods for title, archive state, last-opened tracking, list filtering, and `conversation_reopen_state`.
5. Add server-side conversation store.
6. Add persisted context summary storage for model-input compaction as derived state, not canonical transcript state.
7. Use `AppSettings.conversation_agent_db_path` and the workspace-root-resolved path from Phase 1A.
8. Add `AgentRuntime` using OpenAI Agents SDK.
9. Add runtime-control tool adapter.
10. Add service methods for user messages, requirement operations, review resolution, command routing, event polling, activity projection, conversation metadata operations, compaction, and final summary.
11. Add dependency and boundary tests proving no Codex CLI/App Server/MCP/SDK product runtime dependency and no `.external/codex-reference` runtime dependency.
12. Add usage accounting for model turns: model name, input tokens, output tokens, cost basis, timeout state, and budget reason code.
13. Add error recovery paths for model timeout, model unavailable, tool timeout, and structured-output validation retry.

Verification:

```bash
uv run --group dev python -m pytest tests/test_conversation_agent_store.py tests/test_conversation_agent_metadata.py tests/test_conversation_agent_service.py tests/test_conversation_agent_tools.py tests/test_conversation_agent_compaction.py tests/test_conversation_agent_budget_policy.py tests/test_conversation_agent_error_recovery.py -q
```

### Phase 3: Backend Routes

1. Add agent conversation routes.
2. Wire routes into the local server.
3. Add request/response DTOs.
4. Add list, read/reopen, rename, archive, and unarchive endpoints with typed DTOs.
5. Add event polling or streaming endpoint that exposes persisted transcript message and activity item deltas.
6. Add `/api/agent` to guarded API prefixes.
7. Add auth/CSRF dependencies matching Workbench read/write posture.
8. Add local per-user/per-conversation rate limiting for state-changing agent routes.
9. Add `schemaVersion` and camelCase DTO conversion tests for request and response models.
10. Add tests for every transcript and metadata operation plus security boundary.

Verification:

```bash
uv run --group dev python -m pytest tests/test_conversation_agent_routes.py tests/test_conversation_agent_metadata.py tests/test_conversation_agent_security.py tests/test_conversation_agent_dto_contract.py tests/test_conversation_agent_rate_limit.py tests/test_agent_runtime_event_projection.py -q
```

### Phase 4: Requirement Review View Models

1. Add transcript view-model DTOs.
2. Add requirement review response DTOs.
3. Return five required sections.
4. Return default selected checkbox state.
5. Expose edit/delete/move/enable-disable allowed actions backed by backend operations.
6. Add free-form extra requirement API flow and wire it to amendment normalization.
7. Add review-required resolution response data and wire it to `resolve_requirement_review`.
8. Wire confirm to backend confirmation.
9. Handle stale revision, ambiguous amendment, and validation errors in typed response payloads.
10. Add free-form text safety screening before runtime-control normalization for candidate PII, raw resume markers, provider payload fragments, cookies, auth headers, and secrets.
11. Persist rejected free-form unsafe fragments only as hash plus stable reason code.

Verification:

```bash
uv run --group dev python -m pytest tests/test_agent_requirement_transcript.py tests/test_agent_requirement_review_resolution.py tests/test_agent_free_text_safety.py -q
```

### Phase 5: Runtime Transcript Progress

1. Poll or stream runtime-control events.
2. Project progress messages without duplication.
3. Project activity items with stable `activityKey`, lifecycle status, event seq range, Chinese title, Chinese summary, and machine-readable payload.
4. Persist current snapshot status in transcript-ready form.
5. Wire pause/cancel/resume/add requirement commands.
6. Return next-round requirement target round, pending state, activation state, review-required state, and rejection reason from runtime-control records.
7. Return command states from runtime-control records.
8. Handle `runtime_event_gap_detected` by snapshot refresh and recoverable transcript sync state without advancing message or activity cursors.
9. Add detail-question flow.
10. Add stream reconnect handling from persisted message sequence and activity cursor.
11. Add tests proving repeated event polling, browser-tab concurrency, process restart, stream disconnect, and reconnect do not duplicate messages or activity items.

Verification:

```bash
uv run --group dev python -m pytest tests/test_agent_requirement_transcript.py tests/test_agent_runtime_event_projection.py tests/test_agent_transcript_activity_projection.py -q
uv run --group dev python -m pytest tests/evals/test_conversation_agent_tool_routing_eval.py tests/evals/test_conversation_agent_grounding_eval.py -q
```

### Phase 6: Final Summary

1. Trigger final summary after runtime completion or user request.
2. Call `prepare_final_summary`.
3. Persist final summary message and source summary id.
4. Persist final summary in transcript-ready form.
5. Add conversation-agent retention and storage-payload compaction from `../04-operating-policies-and-runtime-contracts.md`.
6. Add model-input context compaction using persisted context summaries from `SPEC.md`.
7. Persist context compaction as a visible `context_compaction` activity item backed by summary creation or a recorded safe fallback reason.
8. Prove compaction does not advance runtime event cursors, drop pending user actions, drop active activity item state, or make the future UI depend on summary text as canonical transcript.
9. Persist compaction `in_progress`, `completed`, and `failed` rows with pending-state and quality-check metadata.
10. Add quality validation proving summaries preserve requirement revision ids, runtime run id, latest rendered event cursor, final summary id, active activity item ids, pending counts, and safe refs.
11. Fail compaction with `agent_compaction_quality_failed` and use bounded recent history when quality validation does not pass.

Verification:

```bash
uv run --group dev python -m pytest tests/test_agent_final_summary.py tests/test_conversation_agent_retention.py tests/test_conversation_agent_compaction.py -q
```

### Phase 6A: Integrated Advisory Memory Phase

Start this phase only after Phases 2 through 6 have real conversation-agent APIs, store, `AgentRuntime`, routes, persisted transcript messages, persisted activity items, and focused green tests.

1. Execute the memory implementation sequence in `../goal-2-agent-memory-extension/PLAN.md`, using its package, schema, privacy, extraction, route, recall, injection, retention, and eval acceptance criteria.
2. Implement memory through `src/seektalent_agent_memory/` and injected transcript reader protocols. Do not read conversation SQLite directly from memory code.
3. Inject advisory memory through `ConversationAgentService` before `AgentRuntime` runs.
4. Persist which memory fact ids were supplied to each agent turn.
5. Represent memory recall and review as `memory_recall` or `memory_review` activity items when they affect an agent turn or require user-visible review.
6. Ensure memory suggestions route through normal requirement amendment, review, and confirmation APIs before changing runtime input.
7. Record Codex memory-boundary references inspected from `.external/codex-reference`, or record why no relevant Codex source applied.

Verification:

```bash
uv run --group dev python -m pytest tests/test_agent_memory_store.py tests/test_agent_memory_settings.py tests/test_agent_memory_privacy.py tests/test_agent_memory_extraction.py tests/test_agent_memory_routes.py tests/test_agent_memory_security.py tests/test_agent_memory_consolidation.py tests/test_agent_memory_recall.py tests/test_agent_memory_agent_runtime.py tests/test_agent_memory_prompt_injection.py -q
uv run --group dev python -m pytest tests/evals/test_agent_memory_extraction_eval.py tests/evals/test_agent_memory_privacy_eval.py tests/evals/test_agent_memory_prompt_injection_eval.py -q
```

### Phase 7: Full Combined Goal Verification

Run focused commands from `PLAN.md`, including transcript, compaction, memory, eval, boundary, and no-scaffold verification. Regenerate frontend static assets only if API type generation or an explicitly approved frontend build requires it.

Record final evidence in `progress.md`, including Codex reference evidence and the exact completion statements required by `../MANIFEST.md`.

---

## Source: `goal-2-conversational-agent/07-execution-control.md`

## Goal 2 Execution Control

### Progress Ledger

Use:

```text
conversational-agent-runtime-goal-pack/goal-2-conversational-agent/progress.md
```

Create the ledger before product edits. Keep it current after every phase.

### Goal 2 Preflight

Run:

```bash
pwd
git branch --show-current
git rev-parse HEAD
git rev-parse --verify origin/main || echo "MISSING origin/main; fetch before final verification"
git merge-base HEAD origin/main || echo "MISSING merge-base with origin/main"
git status --short --untracked-files=all
git stash list
test -d src/seektalent_runtime_control && echo "runtime-control package present" || echo "MISSING runtime-control package"
test -d src/seektalent_conversation_agent && echo "conversation-agent package present" || echo "conversation-agent package will be created by Goal 2"
test -d src/seektalent_agent_memory && echo "agent-memory package present" || echo "agent-memory package will be created by integrated Goal 2 memory phase"
test -f conversational-agent-runtime-goal-pack/goal-2-conversational-agent/SPEC.md && echo "conversation storage contract present" || echo "MISSING conversation storage contract"
test -f conversational-agent-runtime-goal-pack/goal-2-agent-memory-extension/SPEC.md && echo "memory phase contract present" || echo "MISSING memory phase contract"
test -d .external/codex-reference && git -C .external/codex-reference rev-parse HEAD || echo "MISSING local Codex reference checkout"
rg -n "class RuntimeControlService|class RuntimeCommandService|class WorkflowRuntimeExecutor|class RuntimeDetailService|def extract_requirements|def start_workflow|def request_pause|def resume_workflow|def get_snapshot|def list_events" src/seektalent_runtime_control tests
rg -n "seektalent_conversation_agent|seektalent_agent_memory|openai-agents|conversation_agent_db_path|conversation_agent_path|agent_memory_db_path" pyproject.toml tach.toml src tests .env.example conversational-agent-runtime-goal-pack || true
rg -n "workbench_routes|event_routes|server" src/seektalent_ui
test -f conversational-agent-runtime-goal-pack/04-operating-policies-and-runtime-contracts.md && echo "ui data contract present" || echo "MISSING ui data contract"
test -f conversational-agent-runtime-goal-pack/04-operating-policies-and-runtime-contracts.md && echo "agent eval contract present" || echo "MISSING agent eval contract"
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
uv run python tools/check_arch_imports.py
```

If the runtime-control package or APIs are missing, stop. Goal 2 must call real Goal 1 APIs and must not recreate runtime-control behavior in the agent layer.

### Ledger Template

```markdown
# Conversational Agent Transcript Progress

## Run Identity

- Goal pack:
- Goal:
- Started at:
- Branch:
- HEAD at start:
- Origin main at start:
- Merge-base with origin/main:
- Worktree path:
- Dirty state at start:
- Stashes observed:
- Goal 1 completion evidence:

## Current Phase

- Phase:
- Status:
- Latest successful command:
- Latest failed command:
- Current blocker:

## Phase Evidence

| Phase | Status | Files changed | Tests/checks | Evidence |
| --- | --- | --- | --- | --- |

## Red-Green Evidence

| Check | Red command/result | Fix | Green command/result |
| --- | --- | --- | --- |

## Codex Reference Evidence

| Phase | Codex commit | Source paths inspected | Pattern adopted or rejected | SeekTalent files/tests |
| --- | --- | --- | --- | --- |

## Decisions

| Time | Decision | Reason | Files affected |
| --- | --- | --- | --- |

## Known Risks

| Risk | Status | Mitigation |
| --- | --- | --- |
```

### Stop Conditions

Stop before edits when:

- Goal 1 is not complete;
- runtime-control public APIs differ from the shared tool contract and cannot satisfy Goal 2;
- agent code would need direct runtime imports;
- transcript state would exist only in frontend state;
- the implementation would require temporary Svelte transcript UI before designer-provided screens are available;
- frontend build output is already dirty and cannot be safely distinguished from Goal 2 output;
- a future user-visible workflow control would lack a real backend route or view-model action.

### Final Goal 2 Verification

Run:

```bash
uv run --group dev python -m pytest tests/test_conversation_agent_store.py tests/test_conversation_agent_metadata.py tests/test_conversation_agent_service.py tests/test_conversation_agent_tools.py tests/test_conversation_agent_runtime.py tests/test_conversation_agent_routes.py tests/test_conversation_agent_security.py tests/test_agent_requirement_transcript.py tests/test_agent_requirement_review_resolution.py tests/test_agent_runtime_event_projection.py tests/test_agent_transcript_activity_projection.py tests/test_agent_final_summary.py tests/test_conversation_agent_retention.py tests/test_conversation_agent_compaction.py tests/test_workbench_api.py -q
uv run --group dev python -m pytest tests/test_conversation_agent_budget_policy.py tests/test_conversation_agent_error_recovery.py tests/test_conversation_agent_dto_contract.py tests/test_conversation_agent_rate_limit.py tests/test_agent_free_text_safety.py -q
uv run --group dev python -m pytest tests/test_agent_memory_store.py tests/test_agent_memory_settings.py tests/test_agent_memory_privacy.py tests/test_agent_memory_extraction.py tests/test_agent_memory_routes.py tests/test_agent_memory_security.py tests/test_agent_memory_consolidation.py tests/test_agent_memory_recall.py tests/test_agent_memory_agent_runtime.py tests/test_agent_memory_prompt_injection.py -q
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
uv run python tools/check_arch_imports.py
uv run --group dev python -m pytest tests/evals/test_conversation_agent_tool_routing_eval.py tests/evals/test_conversation_agent_grounding_eval.py -q
uv run --group dev python -m pytest tests/evals/test_agent_memory_extraction_eval.py tests/evals/test_agent_memory_privacy_eval.py tests/evals/test_agent_memory_prompt_injection_eval.py -q
rg -n "TODO|FIXME|placeholder|mock|fake|stub|dummy|hard-coded" src/seektalent_conversation_agent src/seektalent_agent_memory src/seektalent_ui tests || test $? -eq 1
uv run --group dev ruff check src tests
uv run --group dev ty check src tests
scripts/verify-dev-workbench.sh
scripts/verify-red-zone.sh
git diff --check
```

### Required Final Packet

The final response or release-readiness packet must include:

```text
This PR completes the conversational agent transcript goal. It is a complete local transcript-agent implementation for the agreed scope.
```

and:

```text
This PR completes the integrated advisory memory phase. It is a complete local advisory memory implementation for the agreed Goal 2 scope.
```

and:

```text
This product now has a real conversational agent over a durable runtime control plane for the agreed local product scope.
```
