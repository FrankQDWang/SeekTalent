# Goal 2 Agent Memory Extension Plan

This compacted document preserves the content from the source documents below. Source headings are demoted one level so the merged document remains navigable without changing the substantive requirements.

## Source Documents

- `goal-2-agent-memory-extension/05-implementation-sequence.md`
- `goal-2-agent-memory-extension/06-acceptance-criteria.md`
- `goal-2-agent-memory-extension/07-execution-control.md`

---

## Source: `goal-2-agent-memory-extension/05-implementation-sequence.md`

## Agent Memory Implementation Sequence

### Phase 1: Preflight And Goal 2 Evidence

1. Run shared preflight from `../04-operating-policies-and-runtime-contracts.md`.
2. Read Goal 2 final packet and `goal-2-conversational-agent/progress.md`.
3. Verify `ConversationAgentService`, `ConversationStore`, and `AgentRuntime` exist.
4. Record branch, HEAD, dirty state, stashes, and Goal 2 evidence in `progress.md`.

Verification:

```bash
uv run --group dev python -m pytest tests/test_conversation_agent_store.py tests/test_conversation_agent_service.py tests/test_conversation_agent_runtime.py -q
uv run python tools/check_source_boundaries.py
uv run python tools/check_arch_imports.py
```

### Phase 2: Memory Store And Settings

1. Create `src/seektalent_agent_memory/`.
2. Add typed models for settings, candidates, facts, summaries, usage, and privacy review results.
3. Add `AppSettings.agent_memory_db_path` with `.seektalent/agent_memory.sqlite3`.
4. Add SQLite migration version `1`.
5. Add indexes from `SPEC.md`.
6. Add settings service with enable, recall, generation, review, fact retention, rejected-candidate retention, source-excerpt retention, and summary budget fields.

Verification:

```bash
uv run --group dev python -m pytest tests/test_agent_memory_store.py tests/test_agent_memory_settings.py -q
```

### Phase 3: Privacy Filter

1. Add deterministic filters for candidate PII, resume text markers, provider payload markers, secrets, runtime state, and confirmed requirement JSON.
2. Add structured validation model for LLM-assisted privacy review.
3. Reject or redact candidates before persistence.
4. Persist only `safe_candidate_text`, `safe_evidence_excerpt`, hash metadata, reason code, and privacy metadata.
5. Add tests for every reject reason code in `SPEC.md`.
6. Add tests proving rejected rows do not contain forbidden input text in any text column.

Verification:

```bash
uv run --group dev python -m pytest tests/test_agent_memory_privacy.py -q
```

### Phase 4: Extraction

1. Add the `TranscriptReader` protocol and safe transcript DTOs in `src/seektalent_agent_memory/`.
2. Add safe transcript reader integration by injecting a conversation-agent implementation of that protocol.
3. Add `MemoryExtractor` that produces typed candidates in allowed categories.
4. Store candidates as `pending_review`, `accepted`, `rejected`, or `redacted` according to settings.
5. Ensure extraction does not block active transcript requests.

Verification:

```bash
uv run --group dev python -m pytest tests/test_agent_memory_extraction.py -q
```

### Phase 5: Review And Management APIs

1. Add memory routes under `/api/agent/memory`.
2. Add accept, edit-and-accept, reject, delete fact, clear scope, and settings endpoints.
3. Add route security matching `/api/agent` write posture.
4. Add ownership tests proving one user/workspace cannot read another scope.
5. Return UI-ready settings, candidate, fact, and clear-scope DTOs from `../04-operating-policies-and-runtime-contracts.md`.
6. Do not build memory-management UI in this extension.

Verification:

```bash
uv run --group dev python -m pytest tests/test_agent_memory_routes.py tests/test_agent_memory_security.py -q
```

### Phase 6: Consolidation And Recall

1. Add `MemoryConsolidator` that builds compact summaries from active facts.
2. Add summary invalidation after accepted, edited, deleted, or expired facts.
3. Add `MemoryRecallService` with token budget and category filtering.
4. Record memory usage for each agent turn that receives memory context.

Verification:

```bash
uv run --group dev python -m pytest tests/test_agent_memory_consolidation.py tests/test_agent_memory_recall.py -q
```

### Phase 7: AgentRuntime Integration

1. Inject advisory memory context through `ConversationAgentService` before `AgentRuntime` runs.
2. Add instruction wrapper that prevents memory from overriding product rules.
3. Add tests proving memory suggestions require user confirmation before requirement changes.
4. Add prompt-injection tests for hostile memory text.

Verification:

```bash
uv run --group dev python -m pytest tests/test_agent_memory_agent_runtime.py tests/test_agent_memory_prompt_injection.py -q
```

### Phase 8: Retention And Full Verification

1. Add retention cleanup for expired facts and rejected candidates.
2. Add bounded-batch cleanup and consolidation.
3. Add clear-scope behavior that invalidates summaries and prevents recall.
4. Run focused extension verification from `06-acceptance-criteria.md`.
5. Record final evidence in `progress.md`.

---

## Source: `goal-2-agent-memory-extension/06-acceptance-criteria.md`

## Agent Memory Acceptance Criteria

### Product Acceptance

1. Memory can be enabled and disabled per user/workspace.
2. Recall can be disabled while generation remains enabled.
3. Generation can be disabled while recall remains enabled.
4. A new conversation can receive compact advisory memory context.
5. Memory suggestions never silently change requirement drafts or approved requirements.
6. Conversation completion can produce memory candidates in allowed categories.
7. Forbidden candidate, resume, provider, runtime, and secret data is rejected or redacted before persistence.
8. Review-required mode blocks memory activation until user acceptance.
9. Management APIs can accept, edit-and-accept, reject, delete, and clear memory with UI-ready DTOs.
10. Deleted and expired facts are not recalled.
11. Memory usage is recorded for agent turns that receive memory context.
12. The product works without Codex CLI, Codex memory, Codex App Server, or Codex SDK.

### Technical Acceptance

1. New memory business logic lives under `src/seektalent_agent_memory/`.
2. Memory routes are thin wrappers under `src/seektalent_ui/`.
3. `agent_memory_db_path` is configured through `AppSettings` and workspace-root rules.
4. Memory store migrations initialize empty DB and reject future versions.
5. Memory package does not import `seektalent.runtime`.
6. Memory package does not import provider modules.
7. Memory package does not read runtime-control SQLite directly.
8. Memory package reads transcript state only through an injected `TranscriptReader` protocol implementation and does not import conversation-agent modules.
9. Memory context is injected only through `AgentRuntime` advisory instructions.
10. Memory cannot override runtime-control tool boundaries.
11. Memory facts are scoped by owner user id and workspace id.
12. Privacy filter reason codes are stable and tested.
13. Retention cleanup is tested.
14. Clear-scope deletion invalidates summaries and prevents recall.
15. Prompt-injection memory text does not bypass requirement confirmation.
16. Rejected and redacted candidate rows never persist raw forbidden text.
17. Ordinary recall, list, cleanup, and consolidation paths use scoped indexes and bounded batches.
18. Memory-management UI is not implemented in this extension.
19. Boundary checks still pass.

### Required Focused Verification

Run and record:

```bash
uv run --group dev python -m pytest tests/test_agent_memory_store.py tests/test_agent_memory_settings.py tests/test_agent_memory_privacy.py tests/test_agent_memory_extraction.py tests/test_agent_memory_routes.py tests/test_agent_memory_security.py tests/test_agent_memory_consolidation.py tests/test_agent_memory_recall.py tests/test_agent_memory_agent_runtime.py tests/test_agent_memory_prompt_injection.py -q
uv run --group dev python -m pytest tests/evals/test_agent_memory_extraction_eval.py tests/evals/test_agent_memory_privacy_eval.py tests/evals/test_agent_memory_prompt_injection_eval.py -q
uv run python tools/check_source_boundaries.py
uv run python tools/check_tach_baseline.py
uv run python tools/check_arch_imports.py
uv run --group dev ruff check src tests
uv run --group dev ty check src tests
scripts/verify-red-zone.sh
git diff --check
```

If route or test names change during implementation, the progress ledger must map replacement tests to the acceptance criteria above.

### Completion Evidence

The extension final packet must list:

- changed memory package files and why each was touched;
- changed conversation-agent integration points;
- changed route files and security posture;
- memory schema version;
- privacy filter evidence;
- recall and deletion evidence;
- safe candidate persistence evidence;
- index and bounded-cleanup evidence;
- memory management API DTO evidence;
- prompt-injection evidence;
- no Codex runtime dependency evidence;
- verification output;
- remaining risks, if any.

---

## Source: `goal-2-agent-memory-extension/07-execution-control.md`

## Agent Memory Extension Execution Control

This file controls a future Codex Goal worker executing the Goal 2 memory extension.

### Required Preflight

Run shared preflight from `../04-operating-policies-and-runtime-contracts.md`, then run:

```bash
test -f conversational-agent-runtime-goal-pack/goal-2-conversational-agent/progress.md && echo "goal2 progress present" || echo "MISSING goal2 progress"
rg -n "class ConversationAgentService|class AgentRuntime|class ConversationStore|def .*conversation" src/seektalent_conversation_agent tests
uv run --group dev python -m pytest tests/test_conversation_agent_store.py tests/test_conversation_agent_service.py tests/test_conversation_agent_runtime.py -q
```

Stop before product edits if Goal 2 implementation is not present or its focused tests do not pass for reasons unrelated to this extension.

### Progress Ledger

Create or update:

```text
conversational-agent-runtime-goal-pack/goal-2-agent-memory-extension/progress.md
```

Use the ledger format from `../04-operating-policies-and-runtime-contracts.md`.

### Red-Zone Additions

In addition to shared red-zone files, changes to these files require focused tests:

```text
src/seektalent_conversation_agent/
src/seektalent_ui/server.py
src/seektalent_ui/models.py
src/seektalent_ui/agent_routes.py
src/seektalent_ui/conversation_routes.py
```

### Stop Conditions

Stop and ask before continuing if:

- memory cannot be scoped by owner user id and workspace id;
- transcript state can only be read by direct SQLite access;
- privacy filtering cannot remove candidate PII before persistence;
- memory would change requirements without user confirmation;
- OpenAI Agents SDK integration would require Codex CLI, Codex memory, Codex App Server, or Codex SDK;
- deletion cannot reliably prevent future recall.
- implementation requires temporary memory-management UI before designer-provided screens are available;
- memory package would need to import conversation-agent modules directly instead of using an injected transcript reader protocol.

### Completion Statement

The final packet must include:

```text
This PR completes the agent memory extension. It is a complete local advisory memory implementation for the agreed post-Goal-2 scope.
```
