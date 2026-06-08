# Local Codex Intake Harness

## Status

Planning artifact for the next long-running Codex Goal execution.

This directory is the source of truth for the local intake harness work. It is intentionally placed at the repository root and intentionally not nested under a `specs/` directory.

## Reading Order

1. `00-codex-goal.md`
2. `MANIFEST.md`
3. `13-execution-control.md`
4. `01-product-goal.md`
5. `02-boundaries.md`
6. `03-system-architecture.md`
7. `04-codex-harness-and-memory.md`
8. `05-model-provider-config.md`
9. `06-intake-conversation-contract.md`
10. `07-workbench-integration-contract.md`
11. `08-frontend-ux.md`
12. `09-data-storage-and-migrations.md`
13. `10-acceptance-criteria.md`
14. `11-implementation-plan.md`

## Scope Summary

Build a fully local conversation intake harness for SeekTalent.

The user enters hiring needs in a general conversation window. The harness uses a project-isolated Codex App Server runtime and project-isolated Codex memory to parse the conversation into a recruiter-facing requirement confirmation. After the user confirms, the harness creates a normal Workbench session and triggers the existing requirement preparation flow. The intake conversation is the main agent experience; the current SeekTalent runtime remains the downstream subworkflow.

## Source Of Truth

The canonical project requirements are the files in this directory plus the repository code. Codex memory is supporting context only. It is not canonical product state, not a database, and not the source of truth for a Workbench session.

## Workflow Decision

The `fw-ceo-review` result was HOLD SCOPE:

- do not expand into SaaS;
- do not fork Codex;
- do not build a new agent runtime from scratch;
- use Codex App Server directly, not the Codex SDK;
- do not refactor the current runtime;
- do not touch Codex's own user memory;
- keep the new subsystem decoupled enough for parallel runtime refactoring in another window.

## Implementation Owner

The next stage is expected to be a single long-running Codex Goal execution. That execution must treat `00-codex-goal.md` as its top-level instruction document, follow `13-execution-control.md`, and verify every acceptance criterion in `10-acceptance-criteria.md`.

## Directory Contract

The implementation may add product code elsewhere in the repository, but planning documents for this work stay in:

```text
local-codex-intake-harness/
```

Do not rename this directory during implementation.
