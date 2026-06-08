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
2. A backend caller can submit a JD or rough hiring request.
3. The agent calls runtime control and returns a structured requirement draft view model.
4. Requirement draft data contains the five required sections with Chinese labels.
5. Every extracted item has selected-by-default state in persisted response data.
6. A backend caller can unselect, edit, delete, move supported items, and enable/disable query terms through real APIs.
7. A backend caller can add free-form extra requirements and receive normalized draft additions before confirmation.
8. Review-required free-form amendments return accept/edit/move/reject allowed actions and block confirmation until resolved.
9. Each requirement edit persists through runtime-control draft revision APIs.
10. Confirmation persists the approved requirement revision.
11. Workflow starts only after confirmation.
12. Transcript-ready progress messages are projected from runtime-control events and snapshots.
13. A backend caller can pause a run and receive accepted/pending/applied or rejected command state.
14. A backend caller can cancel a run and receive accepted/pending/applied or rejected command state.
15. A backend caller can resume a paused run.
16. A backend caller can add a next-round requirement and receive accepted, scheduled, activated, or rejected state.
17. A backend caller can ask current-status and detail questions.
18. Detail answers cite runtime-control facts internally and do not expose unsafe raw payloads.
19. Final summary is grounded in final runtime result and user instruction.
20. Running next-round requirement messages show target round and do not imply current-round mutation.
21. Running next-round requirement review states do not imply scheduling until review resolution succeeds.
22. Conversation-agent retention and compaction protect terminal transcript storage without corrupting cursors or active conversations.

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
10. Conversation store path is configured through `AppSettings` and resolved through workspace-root rules.
11. Tool call results are stored with transcript messages.
12. Latest event cursor prevents duplicate progress messages and event gaps are handled explicitly.
13. Requirement edit, review resolution, confirmation, and free-form amendment operations include base revision ids and handle stale revision errors.
14. Next-round requirement transcript projection handles accepted, scheduled, activated, review-required, superseded, and rejected states.
15. Routes have backend tests.
16. Requirement review view models have API tests.
17. Free-form amendment view models have API tests.
18. Agent tool routing and grounding evals pass.
19. Frontend type generation or API typing is current after route changes when applicable.
20. Conversation transcript and tool-call payload retention is tested.
21. Workbench static output is regenerated only when intentionally needed.
22. Boundary checks still pass.

### Required Focused Verification

Run and record:

```bash
uv run --group dev python -m pytest tests/test_conversation_agent_store.py tests/test_conversation_agent_service.py tests/test_conversation_agent_tools.py tests/test_conversation_agent_runtime.py tests/test_conversation_agent_routes.py tests/test_conversation_agent_security.py tests/test_agent_requirement_transcript.py tests/test_agent_requirement_review_resolution.py tests/test_agent_runtime_event_projection.py tests/test_agent_final_summary.py tests/test_conversation_agent_retention.py tests/test_workbench_api.py -q
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
uv run python tools/check_arch_imports.py
uv run --group dev python -m pytest tests/evals/test_conversation_agent_tool_routing_eval.py tests/evals/test_conversation_agent_grounding_eval.py -q
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
- runtime-control tools used;
- transcript state persistence path;
- requirement review behavior evidence;
- free-form amendment behavior evidence;
- command flow evidence;
- final summary evidence;
- verification output;
- remaining risks, if any.

---

## Source: `goal-2-conversational-agent/06-implementation-sequence.md`

## Goal 2 Implementation Sequence

### Phase 1: Preflight And Goal 1 Evidence

1. Run shared preflight from `../04-operating-policies-and-runtime-contracts.md`.
2. Read Goal 1 final packet and `goal-1-runtime-control-plane/progress.md`.
3. Verify runtime-control APIs, tests, and migrations exist.
4. Inspect current Workbench frontend and backend route structure.
5. Record branch, HEAD, dirty state, stashes, and Goal 1 evidence in `progress.md`.

Verification:

```bash
uv run --group dev python -m pytest tests/test_runtime_control_store.py tests/test_runtime_control_commands.py tests/test_runtime_control_workflow_adapter.py -q
uv run python tools/check_source_boundaries.py
uv run python tools/check_arch_imports.py
```

### Phase 2: Conversation Store And Agent Service

1. Add `src/seektalent_conversation_agent/`.
2. Add conversation and transcript message models.
3. Add server-side conversation store.
4. Add `AppSettings.conversation_agent_db_path` and resolve it through workspace-root rules.
5. Add `AgentRuntime` using OpenAI Agents SDK.
6. Add runtime-control tool adapter.
7. Add service methods for user messages, requirement operations, review resolution, command routing, event polling, and final summary.
8. Add dependency and boundary tests proving no Codex CLI/App Server/MCP/SDK product runtime dependency.

Verification:

```bash
uv run --group dev python -m pytest tests/test_conversation_agent_store.py tests/test_conversation_agent_service.py tests/test_conversation_agent_tools.py -q
```

### Phase 3: Backend Routes

1. Add agent conversation routes.
2. Wire routes into the local server.
3. Add request/response DTOs.
4. Add event polling or streaming endpoint.
5. Add `/api/agent` to guarded API prefixes.
6. Add auth/CSRF dependencies matching Workbench read/write posture.
7. Add tests for every transcript operation and security boundary.

Verification:

```bash
uv run --group dev python -m pytest tests/test_conversation_agent_routes.py tests/test_conversation_agent_security.py tests/test_agent_runtime_event_projection.py -q
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

Verification:

```bash
uv run --group dev python -m pytest tests/test_agent_requirement_transcript.py tests/test_agent_requirement_review_resolution.py -q
```

### Phase 5: Runtime Transcript Progress

1. Poll or stream runtime-control events.
2. Project progress messages without duplication.
3. Persist current snapshot status in transcript-ready form.
4. Wire pause/cancel/resume/add requirement commands.
5. Return next-round requirement target round, pending state, activation state, review-required state, and rejection reason from runtime-control records.
6. Return command states from runtime-control records.
7. Handle `runtime_event_gap_detected` by snapshot refresh and recoverable transcript sync state.
8. Add detail-question flow.

Verification:

```bash
uv run --group dev python -m pytest tests/test_agent_requirement_transcript.py tests/test_agent_runtime_event_projection.py -q
uv run --group dev python -m pytest tests/evals/test_conversation_agent_tool_routing_eval.py tests/evals/test_conversation_agent_grounding_eval.py -q
```

### Phase 6: Final Summary

1. Trigger final summary after runtime completion or user request.
2. Call `prepare_final_summary`.
3. Persist final summary message and source summary id.
4. Persist final summary in transcript-ready form.
5. Add conversation-agent retention and compaction from `../04-operating-policies-and-runtime-contracts.md`.

Verification:

```bash
uv run --group dev python -m pytest tests/test_agent_final_summary.py tests/test_conversation_agent_retention.py -q
```

### Phase 7: Full Goal Verification

Run focused commands from `PLAN.md`. Regenerate frontend static assets only if API type generation or an explicitly approved frontend build requires it.

Record final evidence in `progress.md`, including the exact completion statement required by `../MANIFEST.md`.

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
test -f conversational-agent-runtime-goal-pack/goal-2-conversational-agent/SPEC.md && echo "conversation storage contract present" || echo "MISSING conversation storage contract"
rg -n "class RuntimeControlService|def extract_requirements|def start_workflow|def request_pause|def resume_workflow|def list_workflow_events" src/seektalent_runtime_control tests
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
uv run --group dev python -m pytest tests/test_conversation_agent_store.py tests/test_conversation_agent_service.py tests/test_conversation_agent_tools.py tests/test_conversation_agent_routes.py tests/test_conversation_agent_security.py tests/test_agent_requirement_transcript.py tests/test_agent_runtime_event_projection.py tests/test_agent_final_summary.py tests/test_conversation_agent_retention.py tests/test_workbench_api.py -q
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
uv run python tools/check_arch_imports.py
uv run --group dev python -m pytest tests/evals/test_conversation_agent_tool_routing_eval.py tests/evals/test_conversation_agent_grounding_eval.py -q
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
