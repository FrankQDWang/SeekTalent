# Pack Manifest

- Pack name: `conversational-agent-runtime-goal-pack`
- Goal id: `conversational-agent-runtime-2026-06`
- Created for: Goal 1 runtime control plus the combined Goal 2 conversational agent, transcript compaction, and advisory memory execution
- Date: 2026-06-08
- Primary entrypoint: `00-codex-goal.md`

## Required Run Control

Before product code changes, each Codex Goal worker must:

1. read `00-codex-goal.md`;
2. read this manifest;
3. read `04-operating-policies-and-runtime-contracts.md`;
4. read every shared document listed in `00-index.md`, including `05-sqlite-event-log-and-projection-contract.md`;
5. read the selected goal's `SPEC.md` and `PLAN.md` in full; for combined Goal 2, also read `goal-2-agent-memory-extension/SPEC.md` and `goal-2-agent-memory-extension/PLAN.md`;
6. run and record the preflight commands from the selected goal's `PLAN.md`;
7. create or update that goal's progress ledger inside the selected goal subdirectory;
8. record branch, HEAD, `origin/main`, merge-base, dirty state, stash inventory, and first verification evidence;
9. complete and record the repository-required plan review gate before product code edits;
10. keep unrelated dirty files untouched;
11. stop before product edits if the current source/runtime/Workbench state makes the selected goal unsafe to execute.

## Required Goal Order

1. Run plan review for Goal 1 before implementation.
2. Complete `goal-1-runtime-control-plane`.
3. Verify Goal 1 fully before starting Goal 2.
4. Run plan review for the combined Goal 2 before implementation.
5. Complete the core `goal-2-conversational-agent` transcript-agent service, store, `AgentRuntime`, routes, persisted transcript messages, and persisted activity items.
6. Complete the integrated advisory memory phase described by `goal-2-agent-memory-extension`.
7. Verify the cross-goal acceptance criteria and the advisory memory acceptance criteria before claiming combined Goal 2 complete.

Goal 2 must not start until Goal 1 has real runtime-control APIs, storage, events, snapshots, command semantics, and tests.

The integrated memory phase must not start until Goal 2 has real conversation-agent APIs, transcript persistence, activity item persistence, `AgentRuntime`, routes, and focused verification evidence.

## Required Evidence Themes

- Requirement extraction produces a stable editable draft contract.
- Free-form user additions are normalized through runtime control and Workflow Runtime parsing before confirmation.
- Requirement confirmation sends an approved revision to the runtime control plane.
- Runtime commands are durable and idempotent.
- Pause/cancel/resume take effect only at declared safe boundaries.
- SQLite durable event log, cursor ownership, projection idempotency, and gap recovery follow `05-sqlite-event-log-and-projection-contract.md`.
- Runtime progress shown in the transcript comes from real runtime events.
- Transcript experience is Codex-like at the backend contract level: durable message stream, tool-call lifecycle state, lifecycle activity items, streamable activity deltas, working-process narration, reloadable history, event cursors, and compaction summaries.
- Agent business facts come from runtime-control, Workbench, and checkpoint stores, not Codex memory or frontend component state.
- The conversational agent runtime uses OpenAI Agents SDK as a packaged dependency and does not require Codex CLI, Codex App Server, Codex MCP server, or Codex SDK at product runtime.
- Agent transcript state is persisted in the conversation-agent store and linked to runtime-control state.
- Conversation title, archive state, reopen metadata, and list visibility are persisted in the conversation-agent store.
- Local Codex source is used only as an ignored reference checkout, with inspected paths, adopted patterns, rejected patterns, local adaptations, and tests recorded in the progress ledger.
- Agent API routes use the same local host/origin/auth/CSRF posture as Workbench routes.
- Agent API routes return camelCase, schema-versioned, UI-ready DTOs and transcript view models for the deferred designer-backed UI.
- Agent model turns, tool calls, streams, and state-changing routes enforce explicit token, cost, timeout, recovery, and rate-limit policies.
- Artifact and trace output modes protect production disk use and sensitive data.
- SQLite retention and compaction policies protect production disk use without deleting active or required audit state.
- Conversation compaction persists lifecycle state and quality evidence before any summary is used as model input.
- Source selection remains registry/catalog-driven.
- The final transcript summary is grounded in final runtime result and user instruction.
- Integrated memory is advisory and never replaces requirement confirmation, runtime-control state, or candidate facts.
- No implementation ships fake adapters, empty tools, or data values used only to make tests or screens appear complete.
- No implementation ships temporary transcript UI or memory UI before design-backed UI work starts.

## Required Completion Phrases

Goal 1 completion packet must include:

```text
This PR completes the runtime control plane goal. It is a complete local runtime-control implementation for the agreed scope.
```

Goal 2 completion packet must include:

```text
This PR completes the conversational agent transcript goal. It is a complete local transcript-agent implementation for the agreed scope.
```

The final cross-goal completion packet must include:

```text
This product now has a real conversational agent over a durable runtime control plane for the agreed local product scope.
```

Integrated advisory memory phase evidence in the Goal 2 completion packet must include:

```text
This PR completes the integrated advisory memory phase. It is a complete local advisory memory implementation for the agreed Goal 2 scope.
```

## Forbidden Final State

The work is not accepted if any of these are true:

- Agent code imports `seektalent.runtime.orchestrator.WorkflowRuntime`.
- Agent code reads or mutates `RunState` directly.
- Runtime control APIs return fake progress or generated status text not backed by persisted events.
- Pause/cancel/resume only updates UI state.
- Requirement confirmation only changes frontend state.
- Requirement draft item checkboxes are not persisted.
- User edits are not represented as versioned revisions.
- Free-form requirement additions are mapped to backend fields by the agent without runtime-control normalization.
- Free-form requirement text containing obvious candidate PII, raw resume blocks, provider payload fragments, cookies, auth headers, or secrets reaches runtime-control normalization unfiltered.
- Agent budget, timeout, stream disconnect, or rate-limit failures silently fall back, drop context, or appear as successful progress.
- `/api/agent` responses lack a schema version or expose DTO shape only through frontend component assumptions.
- Production mode still writes full debug artifacts to a user-visible local path by default.
- Codex memory is canonical product state.
- SeekTalent memory is canonical requirement, runtime, or candidate state.
- Memory stores candidate PII, raw resume text, provider payloads, or secrets.
- Recalled memory bypasses current privacy filters or is injected outside the advisory-memory boundary markers.
- Product runtime requires `codex`, `codex app-server`, `codex mcp-server`, `openai-codex`, or `@openai/codex-sdk`.
- Product code imports, shells out to, vendors, or packages files from `.external/codex-reference`.
- Workbench session state and runtime-control state can diverge silently.
- `cts` and `liepin` are treated as the complete source universe.
- Goal 2 relies on frontend-only state, temporary UI controls, or display-only data paths instead of real APIs and persisted view models.
- Conversation rename or archive only changes frontend state instead of backend metadata.
