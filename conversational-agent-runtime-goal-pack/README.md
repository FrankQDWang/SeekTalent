# Conversational Agent Runtime Goal Pack

## Status

Planning artifact for two primary long-running Codex Goal executions plus a post-Goal-2 memory extension.

This directory is the source of truth for building the SeekTalent conversational agent system. It is intentionally placed at the repository root instead of the Superpowers docs tree.

## Reading Order

1. `00-index.md`
2. `00-codex-goal.md`
3. `MANIFEST.md`
4. `01-shared-product-and-architecture.md`
5. `02-agent-tool-and-requirement-contracts.md`
6. `03-runtime-control-state-and-events.md`
7. `04-operating-policies-and-runtime-contracts.md`
8. `goal-1-runtime-control-plane/SPEC.md`
9. `goal-1-runtime-control-plane/PLAN.md`
10. `goal-2-conversational-agent/SPEC.md`
11. `goal-2-conversational-agent/PLAN.md`
12. `goal-2-agent-memory-extension/SPEC.md`
13. `goal-2-agent-memory-extension/PLAN.md`

## Scope Summary

Build a complete local conversational agent backend and UI-ready data contract for SeekTalent.

The Svelte transcript UI and memory-management UI are deferred until designer-provided screens are available. These goals still build the real runtime-control layer, conversational-agent service, routes, persistence, typed DTOs, transcript view models, event projection, command state, detail answers, final summaries, and verification needed by that future UI. No temporary UI, fake UI controls, or display-only data paths are accepted.

## Goal Split

This pack has one shared product/architecture spec, two primary execution goals, and one post-Goal-2 extension package.

- `goal-1-runtime-control-plane/`: build the durable subworkflow control layer and agent-callable tool contract.
- `goal-2-conversational-agent/`: build the transcript agent backend, APIs, and UI-ready view models on top of the completed runtime control plane.
- `goal-2-agent-memory-extension/`: build product-owned advisory memory backend and UI-ready management APIs after Goal 2 is complete.

The two goals are separate long-running executions because they touch different risk surfaces. They are not independent products. Goal 2 depends on Goal 1's real APIs and must not invent replacement state, fake tools, or direct runtime calls.

The memory extension is a follow-up package. It is not required for Goal 1, Goal 2, or cross-goal acceptance. It must start only after Goal 2 is complete and verified.

## Code Ownership

Both goals should add new top-level packages under `src/` for their main code:

- Goal 1 primary package: `src/seektalent_runtime_control/`
- Goal 2 primary package: `src/seektalent_conversation_agent/`
- Memory extension primary package: `src/seektalent_agent_memory/`

Existing packages such as `src/seektalent/` and `src/seektalent_ui/` may receive only narrow integration changes: runtime hooks, executor adapter seams, route wiring, model DTOs, server registration, or compatibility bridges. New runtime-control logic and new conversational-agent logic must not be placed inside the existing `seektalent` package.

Goal 2 uses OpenAI Agents SDK as a packaged product dependency. It must not introduce Codex CLI, Codex App Server, Codex MCP server, Codex SDK, or an operator-installed `codex` binary as a SeekTalent product runtime dependency.

The memory extension builds SeekTalent-owned advisory memory. It must not use Codex memory files or Codex runtime components, and memory must not become canonical requirement, runtime, or candidate state.

## Directory Contract

Planning documents for this work stay in:

```text
conversational-agent-runtime-goal-pack/
```

Do not move this pack into the Superpowers docs tree. Do not reduce it to medium-task fragments. The pack uses many documents because this is a multi-goal product and runtime architecture change.
The pack is compacted into a small set of high-context documents so long-running Codex goals can load stable execution boundaries without chasing dozens of fragments.
