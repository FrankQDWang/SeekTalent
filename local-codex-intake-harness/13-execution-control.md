# Codex Goal Execution Control

This file controls how a future Codex Goal should execute the Local Codex Intake Harness work. It is not product design; it is the run protocol.

## Goal Invocation

Use Codex Goal mode with this objective:

```text
Complete the Local Codex Intake Harness goal described in local-codex-intake-harness/00-codex-goal.md. Before editing product code, read the full local-codex-intake-harness directory, follow 13-execution-control.md, create or update the progress ledger described there, then execute the full goal through verification. This is not an MVP scaffold.
```

Do not paste every file into the Goal text. The files are the source of truth.

## Required First Reads

Read these files before product edits:

1. `local-codex-intake-harness/00-codex-goal.md`
2. `local-codex-intake-harness/MANIFEST.md`
3. `local-codex-intake-harness/13-execution-control.md`
4. `local-codex-intake-harness/10-acceptance-criteria.md`
5. `local-codex-intake-harness/11-implementation-plan.md`

Then read the rest of the pack in document order.

If present, also read:

1. `seektalent_source_decoupling_correction_goal_pack/00-codex-goal.md`
2. `seektalent_source_decoupling_correction_goal_pack/MANIFEST.md`
3. `seektalent_source_decoupling_correction_goal_pack/03-acceptance.md`
4. `seektalent_source_decoupling_correction_goal_pack/07-execution-control.md`
5. `docs/governance/agent-goals/source-decoupling-correction-2026-06-progress.md`

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
test -d local-codex-intake-harness && echo "local-codex-intake-harness present" || echo "MISSING local-codex-intake-harness"
test -f local-codex-intake-harness/00-codex-goal.md && echo "00-codex-goal present" || echo "MISSING 00-codex-goal"
test -f local-codex-intake-harness/MANIFEST.md && echo "MANIFEST present" || echo "MISSING MANIFEST"
test -f local-codex-intake-harness/13-execution-control.md && echo "execution-control present" || echo "MISSING execution-control"
test -d apps/web-svelte && echo "apps/web-svelte present" || echo "MISSING required dir: apps/web-svelte"
test -f 'apps/web-svelte/src/routes/(app)/sessions/+page.svelte' && echo "sessions page present" || echo "MISSING sessions page"
test -f src/seektalent_ui/workbench_routes.py && echo "workbench_routes present" || echo "MISSING workbench_routes"
test -f src/seektalent_ui/models.py && echo "ui models present" || echo "MISSING ui models"
test -d seektalent_source_decoupling_correction_goal_pack && echo "source-decoupling correction pack present" || echo "source-decoupling correction pack absent"
test -f docs/governance/agent-goals/source-decoupling-correction-2026-06-progress.md && echo "source-decoupling correction ledger present" || echo "source-decoupling correction ledger absent"
rg -n '"sourceKinds": \["cts", "liepin"\]|one or both of `cts`, `liepin`|Default source kinds:' local-codex-intake-harness || true
```

The final `rg` command is a baseline check for stale hard-coded source language. It may print lines before this pack is corrected; after implementation starts, those lines must be resolved or explicitly marked as current registered-source examples rather than source-universe constraints.

Do not apply stashes unless the user explicitly says to. Record their names because they may contain older planning context.

If dirty state includes unrelated local files, leave them untouched. If a dirty product file must be changed for the current phase, pause and ask before editing it.

## Progress Ledger

Create `docs/governance/agent-goals/local-codex-intake-harness-2026-06-progress.md` during Goal setup, before product code changes. If the directory does not exist, create it.

Use this schema:

```markdown
# Local Codex Intake Harness Goal Progress

## Run Identity

- Goal pack: `local-codex-intake-harness`
- Started at:
- Branch:
- HEAD at start:
- Origin main at start:
- Merge-base with origin/main:
- Worktree path:
- Dirty state at start:
- Stashes observed:
- Source-decoupling correction pack status:
- Source-decoupling correction ledger status:

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
| Source catalog calibration |  |  |  |  |
| Path/config guardrails |  |  |  |  |
| Store/models/errors |  |  |  |  |
| Codex App Server adapter |  |  |  |  |
| Intake state machine |  |  |  |  |
| Workbench handoff and workflow reader |  |  |  |  |
| API routes and OpenAPI |  |  |  |  |
| Frontend transcript |  |  |  |  |
| Packaging notice/exclusions |  |  |  |  |
| Full verification |  |  |  |  |

## Red-Green Evidence

| Check | Red command/result | Fix | Green command/result |
| --- | --- | --- | --- |

## Decisions

| Time | Decision | Reason | Files affected |
| --- | --- | --- | --- |

## Known Risks

| Risk | Status | Mitigation |
| --- | --- | --- |
```

Update the ledger after each phase and before any pause.

## Resume Protocol

After pause, crash, context compaction, or thread switch:

1. read `00-codex-goal.md`;
2. read this file;
3. read the progress ledger;
4. inspect `git status --short --untracked-files=all`;
5. re-run only the latest failed command or the smallest relevant verification command;
6. continue from the current ledger phase.

Do not restart from phase 1 if the ledger shows completed phases with evidence.

## Phase Gates

Follow `11-implementation-plan.md`. Each phase must leave a working repository state. A phase is complete only when:

- required code or docs are implemented;
- focused tests or machine checks pass;
- source catalog assumptions are recorded;
- the progress ledger records files changed and evidence.

## Red-Green Evidence

For boundary and integration checks, prove failure before the fix when practical:

- unsafe `CODEX_HOME` is rejected;
- Codex SDK usage is rejected or absent;
- fixed CTS/Liepin source-universe assumptions are rejected by intake tests;
- duplicate confirmation does not create a second Workbench session;
- workflow reader cannot mutate runtime state.

If a red step is impossible because the feature does not exist yet, record `not-applicable: feature absent before implementation` in the ledger.

## Failure Handling

When a command fails:

1. record the exact command and failure summary in the ledger;
2. fix the root cause;
3. re-run the smallest failing command;
4. re-run the broader phase gate.

Do not:

- lower a gate to pass;
- skip a test because it is inconvenient;
- add fallback code to hide a provider or harness failure;
- switch to Codex SDK;
- hard-code `cts`/`liepin` as the future source universe;
- continue to the next phase without recording the failure.

## Architecture Escalation

Stop and ask before continuing when a design conflict prevents satisfying the pack as written.

Mandatory escalation cases:

- direct Codex App Server use is not possible without SDK usage;
- source catalog/registry is mid-refactor and no stable read surface exists;
- Workbench handoff requires runtime internals;
- read-only workflow progress requires mutating runtime state;
- preserving packaging behavior would include Codex auth, memory, or `~/.codex`;
- the implementation would need to edit unrelated dirty files;
- a verification command can only pass by weakening a gate.

Escalation format:

```markdown
## Architecture Escalation

- Constraint conflict:
- Evidence:
- Option A:
- Option B:
- Recommendation:
- Risk if wrong:
```

Do not resolve these conflicts by inventing an unrecorded architecture variant.

## Verification Evidence

The final report must include:

| Command | Required by | Result | Evidence |
| --- | --- | --- | --- |
| `uv run --group dev python -m pytest tests/test_intake_paths.py tests/test_intake_store.py tests/test_intake_codex_config.py tests/test_intake_codex_app_server.py tests/test_intake_source_catalog.py tests/test_intake_service.py tests/test_intake_workbench_bridge.py tests/test_intake_workflow_reader.py tests/test_workbench_api.py -q` | Intake backend |  |  |
| `uv run --group dev ruff check src tests` | Python quality |  |  |
| `cd apps/web-svelte && bun run check` | Frontend typecheck |  |  |
| `cd apps/web-svelte && bun run lint` | Frontend lint |  |  |
| `cd apps/web-svelte && bun run test` | Frontend tests |  |  |
| `cd apps/web-svelte && bun run build` | Frontend build |  |  |
| `scripts/verify-dev-workbench.sh` | Workbench/BFF/frontend |  |  |
| `scripts/verify-red-zone.sh` | Red-zone if touched |  |  |
| `git diff --check` | Patch hygiene |  |  |

If a command name changes, list the replacement and why it is equivalent.

## Completion Packet

The final response or PR summary must include:

- intake harness summary;
- Codex App Server integration evidence;
- memory isolation evidence;
- source catalog evidence;
- Workbench handoff evidence;
- workflow read-only control evidence;
- frontend transcript evidence;
- commercial packaging evidence;
- verification evidence table;
- progress ledger path and resume summary;
- known risks, or `无已知未覆盖风险`;
- required completion phrase from `MANIFEST.md`.
