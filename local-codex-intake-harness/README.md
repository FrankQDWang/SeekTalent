# Local Codex Intake Harness Goal Pack

This directory is a Codex Goal pack for implementing the local intake harness.

## Entry Point

Start with:

```text
local-codex-intake-harness/00-codex-goal.md
```

Then follow:

```text
local-codex-intake-harness/MANIFEST.md
local-codex-intake-harness/13-execution-control.md
```

## Use

Use Codex Goal mode with the invocation text in `13-execution-control.md`.

Do not paste every file into the Goal prompt. The files in this directory are the source of truth.

## Key Boundaries

- Local product only.
- Codex App Server only.
- Project-isolated `CODEX_HOME`.
- Default non-OpenAI provider.
- No `~/.codex` read/write.
- No runtime refactor.
- No initial runtime-in-progress mutation.
- Source selection must be registry-driven.
