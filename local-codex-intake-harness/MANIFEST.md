# Pack Manifest

- Pack name: `local-codex-intake-harness`
- Goal id: `local-codex-intake-harness-2026-06`
- Created for: one-shot Codex Goal implementation of the local intake harness
- Primary entrypoint: `00-codex-goal.md`

## Required Run Control

Before product code changes, the Codex Goal worker must:

1. read `00-codex-goal.md`;
2. read this manifest;
3. read `13-execution-control.md`;
4. run and record the preflight commands from `13-execution-control.md`;
5. create or update `docs/governance/agent-goals/local-codex-intake-harness-2026-06-progress.md`;
6. record branch, HEAD, `origin/main`, merge-base, dirty state, stash inventory, source-decoupling pack status, and first verification evidence;
7. keep unrelated dirty files untouched;
8. stop before product edits if source-decoupling state makes source catalog integration unsafe.

## Required Evidence Themes

- Codex App Server is used directly; Codex SDK is not used.
- `CODEX_HOME` and memories are project-isolated.
- Default provider is non-OpenAI.
- No code path reads or writes `~/.codex`.
- Source selection comes from a current source catalog/registry, not a fixed CTS/Liepin universe.
- Workbench handoff creates exactly one session and starts requirement preparation.
- Initial workflow control is start/read-only only.
- Transcript UI supports requirement confirmation and progress/result Q&A.
- Packaging excludes Codex memory/auth/global home and preserves Apache-2.0 notice when redistributing Codex artifacts.

## Required Completion Phrase

The final response or PR summary must include:

```text
This PR completes the local Codex intake harness goal. It is not an MVP scaffold.
```
