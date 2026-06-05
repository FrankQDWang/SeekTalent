# Pack Manifest

- Pack name: `seektalent_source_decoupling_correction_goal_pack`
- Goal id: `source-decoupling-correction-2026-06`
- Created for: corrective Codex Goal after incomplete source-decoupling delivery
- Date: 2026-06-05
- Primary entrypoint: `00-codex-goal.md`

## Required Run Control

Before product code changes, the Codex Goal worker must:

1. read `07-execution-control.md`;
2. run and record the preflight commands from that file;
3. update `docs/governance/agent-goals/source-decoupling-correction-2026-06-progress.md`;
4. record branch, HEAD, `origin/main`, merge-base, dirty state, stash inventory,
   and first red-gate evidence;
5. keep unrelated dirty files untouched;
6. prove the current boundary checks fail on the known violations before fixing
   product code;
7. stop if asked to weaken the source boundary gate instead of fixing the source
   architecture.

## Required Completion Phrase

The final PR summary or Goal completion packet must include:

```text
This PR completes the source-decoupling correction goal. The runtime no longer knows CTS or Liepin as concrete source implementations.
```

## Required Evidence Themes

- hardened source-boundary checker catches the original missed violations;
- full `WorkflowRuntime` fixture source test fails before the fix and passes
  after the fix;
- Tach no longer permits runtime/sources/providers cycles;
- neutral source contracts are separated from concrete source adapters;
- runtime production code has no CTS/Liepin-specific source execution path;
- existing CTS and Liepin behavior remains covered by regression tests.

## Fixed Architecture Decisions

- Neutral source contracts/registry package:
  `src/seektalent/source_contracts/`
- Concrete source adapter package:
  `src/seektalent/source_adapters/`
- Runtime may import `seektalent.source_contracts.*`.
- Runtime must not import `seektalent.source_adapters.*`,
  `seektalent.sources.cts.*`, or `seektalent.sources.liepin.*`.
- Production app/bootstrap owns CTS/Liepin registration and passes a registry to
  `WorkflowRuntime`.
- Preferred Workbench bootstrap file: `src/seektalent_ui/source_registry.py`.
- Do not add Tach cycles to `tools/tach_baseline.json`; cycles are red evidence
  until removed.
