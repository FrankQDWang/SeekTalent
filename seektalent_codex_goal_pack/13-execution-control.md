# Codex Goal Execution Control

This file controls how a future Codex Goal should execute the SeekTalent source-decoupling refactor. It is not the product design. It is the run protocol.

## Goal Invocation

Use Goal mode with a short objective that points at the pack:

```text
Complete the SeekTalent source-decoupling goal described in seektalent_codex_goal_pack/00-codex-goal.md. Before editing product code, read the full seektalent_codex_goal_pack directory, follow 13-execution-control.md, create the progress ledger described there, then execute the full goal through verification. This is not an MVP scaffold.
```

The long instructions live in this directory. Do not paste every file into the Goal text.

## Required First Reads

Read these files before product edits:

1. `seektalent_codex_goal_pack/00-codex-goal.md`
2. `seektalent_codex_goal_pack/MANIFEST.md`
3. `seektalent_codex_goal_pack/13-execution-control.md`
4. `seektalent_codex_goal_pack/12-execution-sequence.md`
5. `seektalent_codex_goal_pack/10-acceptance.md`

Then skim the remaining pack files in document order.

## Preflight

Run and record:

```bash
pwd
git branch --show-current
git rev-parse HEAD
git rev-parse --verify origin/main || echo "MISSING origin/main; fetch before final verification"
git merge-base HEAD origin/main || echo "MISSING merge-base with origin/main"
git status --short --untracked-files=all
git stash list
test -d apps/web-svelte && echo "apps/web-svelte present" || echo "MISSING required dir: apps/web-svelte"
test -d apps/liepin-worker && echo "apps/liepin-worker present" || echo "MISSING required dir: apps/liepin-worker"
test -d src/seektalent_ui && echo "src/seektalent_ui present" || echo "MISSING required dir: src/seektalent_ui"
test -f tools/check_source_boundaries.py && echo "tools/check_source_boundaries.py present" || echo "MISSING expected future output: tools/check_source_boundaries.py"
test -f scripts/verify-source-decoupling.sh && echo "scripts/verify-source-decoupling.sh present" || echo "MISSING expected future output: scripts/verify-source-decoupling.sh"
```

Missing `tools/check_source_boundaries.py` and `scripts/verify-source-decoupling.sh` is expected at the start. They are required outputs of the harness recalibration slice.

Do not apply stashes unless the user explicitly says to. Record their names because they may contain older planning context.

If dirty state includes unrelated local files, leave them untouched. If a dirty product file must be changed for the current phase, pause and ask before editing it.

## Progress Ledger

Create `docs/governance/agent-goals/source-decoupling-2026-06-progress.md` during Goal run setup, before harness recalibration. If the directory does not exist, create it.

Use this schema:

```markdown
# Source Decoupling Goal Progress

## Run Identity

- Goal pack: `seektalent_codex_goal_pack`
- Started at:
- Branch:
- HEAD at start:
- Origin main at start:
- Merge-base with origin/main:
- Worktree path:
- Dirty state at start:
- Stashes observed:

## Current Phase

- Phase:
- Status: not-started | in-progress | blocked | complete
- Latest successful command:
- Latest failed command:
- Current blocker:

## Phase Evidence

| Phase | Status | Files changed | Tests/checks | Evidence |
| --- | --- | --- | --- | --- |
| Run setup |  |  |  |  |
| Harness recalibration |  |  |  |  |
| Source-neutral contract |  |  |  |  |
| CTS migration |  |  |  |  |
| Liepin migration |  |  |  |  |
| Liepin worker lifecycle |  |  |  |  |
| Runtime performance and identity |  |  |  |  |
| PRF, SQLite, duplicates |  |  |  |  |
| BFF/frontend |  |  |  |  |
| Cleanup and docs |  |  |  |  |
| Full verification |  |  |  |  |

## Decisions

| Time | Decision | Reason | Files affected |
| --- | --- | --- | --- |

## Known Risks

| Risk | Status | Mitigation |
| --- | --- | --- |
```

Update the ledger after each phase and before any pause.

## Resume Protocol

After pause, crash, or context compaction:

1. Read `00-codex-goal.md`.
2. Read this file.
3. Read the progress ledger.
4. Inspect `git status --short --untracked-files=all`.
5. Re-run only the latest failed command or the smallest relevant verification command.
6. Continue from the current ledger phase.

Do not restart from phase 1 if the ledger shows completed phases with evidence.

## Phase Gates

Follow `12-execution-sequence.md`. Each phase must leave a working repository state. A phase is complete only when:

- required code or docs are implemented;
- old replaced paths are deleted when applicable;
- focused tests or machine checks pass;
- the progress ledger records files changed and evidence.

## Failure Handling

When a command fails:

1. Record the exact command and failure summary in the ledger.
2. Fix the root cause.
3. Re-run the smallest failing command.
4. Re-run the broader phase gate.

Do not:

- lower a gate to pass;
- add broad fallback code to hide a failure;
- skip a test because it is inconvenient;
- leave a compatibility path for an unpublished old architecture;
- continue to the next phase without recording the failure.

## Verification Evidence

The final PR summary must include:

| Command | Required by | Result | Evidence |
| --- | --- | --- | --- |
| `uv run ruff check src tests experiments` | Python quality |  |  |
| `uv run ty check src tests` | Python typing |  |  |
| `uv run pytest` | Python tests |  |  |
| `uv run python tools/check_arch_imports.py` | Architecture |  |  |
| `uv run python tools/check_tach_baseline.py` | Architecture |  |  |
| `uv run python tools/check_privacy_gate.py --base origin/main` | Privacy |  |  |
| `uv run python tools/check_ai_bad_smells.py --base origin/main` | AI coding governance |  |  |
| `uv run python tools/check_source_boundaries.py` | Source boundary |  |  |
| `scripts/verify-source-decoupling.sh` | Source decoupling |  |  |
| `scripts/verify-red-zone.sh` | Red-zone |  |  |
| `scripts/verify-dev-workbench.sh` | Workbench/BFF/frontend |  |  |
| `cd apps/web-svelte && bun run test` | Frontend unit |  |  |
| `cd apps/web-svelte && bun run test:e2e` | Frontend e2e |  |  |
| `cd apps/web-svelte && bun run build` | Frontend build |  |  |
| `cd apps/liepin-worker && bun test` | Worker tests |  |  |
| `cd apps/liepin-worker && bun run typecheck` | Worker typecheck |  |  |
| `cd apps/liepin-worker && bun run boundary-check` | Worker boundary |  |  |
| `cd apps/liepin-worker && bun run compatibility-gate` | Worker compatibility |  |  |

If a command name changes, list the replacement and why it is equivalent.

## Current-State Docs Rule

Active docs describe the repository before this goal is complete. Use them as current-state evidence only. After implementation, refresh active docs so they describe the new source-neutral architecture.

## Completion Packet

The final response or PR summary must include:

- source decoupling summary;
- harness changes;
- Liepin stabilization changes;
- BFF/frontend changes;
- issue `#58` through `#69` coverage;
- deletion list;
- docs refresh list;
- verification evidence table;
- known risks, or `无已知未覆盖风险`;
- the required manifest phrase: `This PR completes the source decoupling goal. It is not an MVP scaffold.`
