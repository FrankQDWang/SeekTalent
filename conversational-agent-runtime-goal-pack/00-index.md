# Index

## Shared Documents

| File | Purpose |
| --- | --- |
| `00-codex-goal.md` | Top-level instruction for running Goal 1, then the combined Goal 2 transcript/compaction/advisory-memory goal. |
| `MANIFEST.md` | Run contract, required evidence, completion phrases, forbidden final states. |
| `01-shared-product-and-architecture.md` | Product behavior, system architecture, dependency direction, state machine, and tool flow shared by both goals. |
| `02-agent-tool-and-requirement-contracts.md` | Stable agent-callable tool/API surface plus checkbox/edit/move requirement draft contract. |
| `03-runtime-control-state-and-events.md` | Shared run, checkpoint, command, event, snapshot, and detail read-model contracts. |
| `04-operating-policies-and-runtime-contracts.md` | Artifact/trace policy, risks, cross-goal acceptance, execution protocol, OpenAI Agents SDK boundary, budget/error/rate-limit policy, UI-ready DTO contract, eval contract, and retention policy. |
| `05-sqlite-event-log-and-projection-contract.md` | Local SQLite durable event log, event write transaction, cursor, idempotent projection, gap recovery, retention, and future broker/orchestrator upgrade criteria. |

## Goal 1: Runtime Control Plane

Goal 1 builds the durable subworkflow control layer.

| File | Purpose |
| --- | --- |
| `goal-1-runtime-control-plane/SPEC.md` | Goal objective, current system facts, runtime-control service/API contract, SQLite schema, migrations, runtime hooks, and Workbench bridge boundaries. |
| `goal-1-runtime-control-plane/PLAN.md` | Goal 1 product and technical acceptance, phase plan, run protocol, preflight, and verification ledger. |

## Goal 2: Conversational Agent

Goal 2 builds the transcript-agent backend, conversation metadata APIs, Codex-like lifecycle activity projection, transcript compaction, advisory memory phase, and UI-ready view models on top of Goal 1.

| File | Purpose |
| --- | --- |
| `goal-2-conversational-agent/SPEC.md` | Goal objective, UI/API facts, Codex-like transcript and activity lifecycle behavior, agent orchestration, frontend/backend boundaries, transcript persistence schema, and integrated memory sequencing. |
| `goal-2-conversational-agent/PLAN.md` | Goal 2 product and technical acceptance, phase plan, run protocol, Codex reference evidence, preflight, and verification ledger. |

## Goal 2 Advisory Memory Phase

This directory supplies the source contract for the integrated advisory memory phase inside the combined Goal 2 execution. It starts only after the core conversation-agent service, store, `AgentRuntime`, transcript routes, persisted transcript messages, and persisted activity items are real and verified.

| File | Purpose |
| --- | --- |
| `goal-2-agent-memory-extension/SPEC.md` | Advisory memory phase objective, product behavior, architecture, SQLite schema, privacy filters, OpenAI Agents SDK injection, and prompt boundary. |
| `goal-2-agent-memory-extension/PLAN.md` | Advisory memory phase plan, product and technical acceptance, run protocol, preflight, and verification ledger. |
