# Pack Manifest

- Pack name: `seektalent_codex_goal_pack`
- Created for: one-shot Codex Goal refactor of `FrankQDWang/SeekTalent`
- Date: 2026-06-04
- Primary entrypoint: `00-codex-goal.md`

## Required run control

Before product code changes, the Codex Goal worker must:

1. read `13-execution-control.md`;
2. run the preflight commands from that file;
3. create `docs/governance/agent-goals/source-decoupling-2026-06-progress.md`;
4. record branch, HEAD, `origin/main`, merge-base, dirty state, stash inventory, and missing expected future outputs;
5. treat missing `tools/check_source_boundaries.py` and `scripts/verify-source-decoupling.sh` as required harness outputs, not as permission to skip source-boundary verification.
6. leave unrelated dirty files untouched; pause and ask before editing a dirty product file required by the current phase.

## Required completion phrase for PR summary

The PR summary should include:

```text
This PR completes the source decoupling goal. It is not an MVP scaffold.
```

## Required issue coverage

`#58 #59 #60 #61 #62 #63 #64 #65 #66 #67 #68 #69`
