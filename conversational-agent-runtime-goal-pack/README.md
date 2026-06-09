# Conversational Agent Runtime Goal Pack

## Status

Planning artifact for Goal 1 runtime control plus the combined Goal 2 conversational agent, transcript compaction, and advisory memory execution.

This directory is the source of truth for building the SeekTalent conversational agent system. It is intentionally placed at the repository root instead of the Superpowers docs tree.

## Reading Order

1. `00-index.md`
2. `00-codex-goal.md`
3. `MANIFEST.md`
4. `01-shared-product-and-architecture.md`
5. `02-agent-tool-and-requirement-contracts.md`
6. `03-runtime-control-state-and-events.md`
7. `04-operating-policies-and-runtime-contracts.md`
8. `05-sqlite-event-log-and-projection-contract.md`
9. `goal-1-runtime-control-plane/SPEC.md`
10. `goal-1-runtime-control-plane/PLAN.md`
11. `goal-2-conversational-agent/SPEC.md`
12. `goal-2-conversational-agent/PLAN.md`
13. `goal-2-agent-memory-extension/SPEC.md`
14. `goal-2-agent-memory-extension/PLAN.md`

## Scope Summary

Build a complete local conversational agent backend and UI-ready data contract for SeekTalent.

The Svelte transcript UI and memory-management UI are deferred until designer-provided screens are available. These goals still build the real runtime-control layer, SQLite durable event log, conversational-agent service, routes, persistence, typed/versioned DTOs, conversation list/rename/archive/reopen metadata, transcript view models, event projection, lifecycle activity projection, command state, detail answers, transcript compaction with quality evidence, advisory memory APIs, token/cost/error/rate-limit policies, final summaries, and verification needed by that future UI. No temporary UI, fake UI controls, or display-only data paths are accepted.

## Goal Split

This pack has one shared product/architecture spec and two execution goals. Goal 2 includes an internal advisory memory phase.

- `goal-1-runtime-control-plane/`: build the durable subworkflow control layer and agent-callable tool contract.
- `goal-2-conversational-agent/`: build the transcript agent backend, APIs, transcript compaction, UI-ready view models, and final summary flow on top of the completed runtime control plane.
- `goal-2-agent-memory-extension/`: source contract for the integrated Goal 2 advisory memory phase, including product-owned memory backend and UI-ready management APIs.

The two goals are separate long-running executions because they touch different risk surfaces. They are not independent products. Goal 2 depends on Goal 1's real APIs and must not invent replacement state, fake tools, or direct runtime calls.

The memory phase is not allowed to start at the beginning of Goal 2. It starts only after the core conversation-agent service, store, `AgentRuntime`, transcript routes, persisted transcript messages, and persisted activity items are real and verified. It may be split into a later branch only through an explicit product gate.

## Code Ownership

Both goals should add new top-level packages under `src/` for their main code:

- Goal 1 primary package: `src/seektalent_runtime_control/`
- Goal 2 primary package: `src/seektalent_conversation_agent/`
- Memory phase primary package: `src/seektalent_agent_memory/`

Existing packages such as `src/seektalent/` and `src/seektalent_ui/` may receive only narrow integration changes: runtime hooks, executor adapter seams, route wiring, model DTOs, server registration, or compatibility bridges. New runtime-control logic and new conversational-agent logic must not be placed inside the existing `seektalent` package.

Goal 2 uses OpenAI Agents SDK as a packaged product dependency. It must not introduce Codex CLI, Codex App Server, Codex MCP server, Codex SDK, or an operator-installed `codex` binary as a SeekTalent product runtime dependency.

Goal 2 may inspect the local Codex source checkout as an implementation reference for transcript flow, history compaction, tool-call lifecycle, and memory boundary ideas. That checkout must live under `.external/codex-reference`, stay ignored and untracked, and never become a product import, package dependency, subprocess dependency, or vendored source tree.

The memory phase builds SeekTalent-owned advisory memory. It must not use Codex memory files or Codex runtime components, memory must not become canonical requirement, runtime, or candidate state, and recalled memory must pass current privacy filters before it is injected as advisory data.

## Directory Contract

Planning documents for this work stay in:

```text
conversational-agent-runtime-goal-pack/
```

Do not move this pack into the Superpowers docs tree. Do not reduce it to medium-task fragments. The pack uses many documents because this is a multi-goal product and runtime architecture change.
The pack is compacted into a small set of high-context documents so long-running Codex goals can load stable execution boundaries without chasing dozens of fragments.
