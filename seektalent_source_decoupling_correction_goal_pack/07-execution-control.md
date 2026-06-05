# Codex Goal Execution Control

This file controls how the correction Goal should run.

## Goal Invocation

Use Codex Goal mode with this objective:

```text
Execute the corrective source decoupling goal described in seektalent_source_decoupling_correction_goal_pack/00-codex-goal.md. Before editing product code, read the full correction goal pack, follow 07-execution-control.md, update docs/governance/agent-goals/source-decoupling-correction-2026-06-progress.md, harden the gates so they fail on the current violations, then complete the implementation and verification. Do not treat the previous source-decoupling progress ledger as proof of completion.
```

Do not paste the full pack into the Goal text. The files are the source of truth.

## Required First Reads

1. `seektalent_source_decoupling_correction_goal_pack/00-codex-goal.md`
2. `seektalent_source_decoupling_correction_goal_pack/MANIFEST.md`
3. `seektalent_source_decoupling_correction_goal_pack/07-execution-control.md`
4. `seektalent_source_decoupling_correction_goal_pack/03-acceptance.md`
5. `seektalent_source_decoupling_correction_goal_pack/04-execution-sequence.md`
6. `seektalent_source_decoupling_correction_goal_pack/05-boundary-gates.md`
7. `docs/governance/agent-goals/source-decoupling-correction-2026-06-progress.md`

Then read the rest of this correction pack and the old source-decoupling goal
documents as audit context.

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
test -d seektalent_source_decoupling_correction_goal_pack && echo "correction goal pack present" || echo "MISSING correction goal pack"
test -f tools/check_source_boundaries.py && echo "tools/check_source_boundaries.py present" || echo "MISSING source boundary checker"
test -f scripts/verify-source-decoupling.sh && echo "scripts/verify-source-decoupling.sh present" || echo "MISSING verify-source-decoupling.sh"
uv run python tools/check_source_boundaries.py || true
uv run python tools/check_tach_baseline.py || true
scripts/verify-source-decoupling.sh || true
```

The `|| true` commands are for recording baseline behavior only. After phase 1,
they must fail on the known violations until product code is fixed. Final
verification must run without `|| true`.

Do not apply stashes unless the user explicitly says to.

## Progress Ledger

Update `docs/governance/agent-goals/source-decoupling-correction-2026-06-progress.md`
during setup and after every phase.

Required sections:

```markdown
# Source Decoupling Correction Goal Progress

## Run Identity

- Goal pack: `seektalent_source_decoupling_correction_goal_pack`
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
| Gate hardening |  |  |  |  |
| Fixture full-runtime proof |  |  |  |  |
| Registry injection |  |  |  |  |
| CTS/Liepin migration |  |  |  |  |
| Tach boundary repair |  |  |  |  |
| Behavior regression |  |  |  |  |
| Full verification |  |  |  |  |

## Red-Green Evidence

| Violation class | Red command/result | Fix | Green command/result |
| --- | --- | --- | --- |

## Decisions

| Time | Decision | Reason | Files affected |
| --- | --- | --- | --- |

## Known Risks

| Risk | Status | Mitigation |
| --- | --- | --- |
```

## Resume Protocol

After pause, crash, context compaction, or thread switch:

1. read `00-codex-goal.md`;
2. read this file;
3. read the progress ledger;
4. inspect `git status --short --untracked-files=all`;
5. re-run the latest failed command or smallest relevant verification;
6. continue from the ledger phase.

Do not restart from scratch if the ledger contains completed phases with
evidence.

## Failure Handling

For each failed command:

1. record the command and failure summary;
2. fix the root cause;
3. rerun the smallest failing command;
4. rerun the broader phase gate.

Forbidden failure handling:

- weakening the checker;
- skipping tests;
- adding compatibility wrappers for unpublished old architecture;
- hiding source-specific behavior behind generic names while runtime still owns
  CTS/Liepin logic.

## Architecture Escalation

Stop and ask before continuing when a design conflict prevents satisfying the
pack as written.

Mandatory escalation cases:

- the `source_contracts` / `source_adapters` split cannot be represented in
  Tach without a cycle;
- full `WorkflowRuntime.run()` cannot be used for fixture source proof and a
  proposed test seam would not share production source execution code;
- preserving CTS or Liepin behavior appears to require runtime concrete source
  branches;
- the checker needs to allow a concrete source id in runtime production code;
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

## Completion Packet

The final response or PR summary must include:

- correction summary;
- original violation classes and fixed status;
- red-green gate evidence;
- full `WorkflowRuntime` fixture source evidence;
- Tach/source-boundary evidence;
- behavior regression evidence for CTS and Liepin;
- verification command table;
- known risks, or `无已知未覆盖风险`;
- required completion phrase from `MANIFEST.md`.
