# Architecture

`cv-match` is built as a deterministic Python Agent with a small number of LLM-backed stages.

The design goal is controlled behavior and auditability rather than open-ended agent autonomy.

## High-level flow

1. Read `JD + notes`
2. Extract structured requirement truth
3. Ask the controller what to search next
4. Build a retrieval plan and execute CTS search
5. Normalize and score candidate resumes
6. Run reflection on the round
7. Repeat until stop
8. Finalize the shortlist

## Main Agent components

### Requirement extractor

- Converts the raw JD and notes into structured requirement data.
- Produces the initial requirement sheet and scoring policy inputs.

### Controller

- Decides whether to continue or stop.
- Proposes round-specific query terms and filter plans.
- Does not directly execute tools.

### Agent runtime

- Owns orchestration, round budgets, pagination, dedup, normalization, scoring fan-out, and stopping rules.
- Persists run artifacts and prompt snapshots.
- Enforces deterministic control flow around LLM stages.

### CTS client

- Executes real CTS requests in authenticated mode.
- Can use a local mock corpus during development and testing.
- Keeps CTS-specific payload construction inside the adapter layer.

### Resume scorer

- Scores normalized resumes in parallel.
- Works on one resume at a time with a shared scoring context.

### Reflection critic

- Reviews the round outcome.
- Provides advice for the next round when reflection is enabled.

### Finalizer

- Produces the final shortlist output and summary artifacts.

## Design boundaries

- One Agent runtime controls the process.
- Tool execution is explicit and limited.
- Audit files are first-class outputs.
- The repository includes a minimal web UI, but the CLI remains the primary interface.

## Historical notes

Versioned design documents remain under `docs/v-*` and are kept as historical references:

- `docs/v-0.1/`
- `docs/v-0.2/`

This document is intentionally shorter and only describes the current public-facing shape.
