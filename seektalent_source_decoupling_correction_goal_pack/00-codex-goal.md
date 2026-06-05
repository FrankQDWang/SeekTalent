# Codex Goal: Source Decoupling Correction

## Objective

Fix the unfinished core of the previous `source-decoupling-2026-06` goal.

The prior implementation added useful contracts, registry code, tests, and
provider work, but the runtime still contains concrete CTS/Liepin execution
knowledge and the verification gates missed that fact. This goal completes the
original architectural promise:

```text
runtime depends on source-neutral contracts and registry behavior.
CTS, Liepin, and any future source are registered external implementations.
runtime does not know what "cts" or "liepin" means.
```

This is a corrective architecture task, not a new feature and not a broad
cleanup pass.

## Required First Reads

Before editing product code, read:

1. `AGENTS.md`
2. this directory in document order
3. `seektalent_codex_goal_pack/00-codex-goal.md`
4. `seektalent_codex_goal_pack/03-target-architecture.md`
5. `seektalent_codex_goal_pack/04-source-contract.md`
6. `seektalent_codex_goal_pack/10-acceptance.md`
7. `docs/governance/agent-goals/source-decoupling-2026-06-progress.md`
8. current runtime/source/provider code and tests

The old progress ledger is not trusted completion evidence. It is input for
finding mismatches between claimed and actual architecture.

## Non-Negotiables

- Do not weaken gates to pass.
- Do not claim success because `scripts/verify-source-decoupling.sh` passes
  before it is hardened.
- Do not satisfy this goal by moving provider code under
  `seektalent.sources.cts` or `seektalent.sources.liepin` and letting runtime
  import it.
- Do not leave runtime CTS/Liepin branch maps, whitelist checks, source-specific
  budget fields, detail lease restrictions, or reason-code imports as the main
  path.
- Do not replace explicit source coupling with a generic-looking wrapper that
  still only supports CTS/Liepin in runtime.
- Do not use fixture source tests that bypass `WorkflowRuntime`.
- Do not delete or degrade existing CTS/Liepin behavior to make the architecture
  simpler.

## Completion Definition

This goal is complete only when:

- runtime production code no longer imports concrete CTS/Liepin source modules;
- runtime source execution is registry-driven;
- a new `fixture_source` can execute through a full `WorkflowRuntime` round path
  without runtime code changes;
- source-boundary checks fail on the original missed violation patterns and pass
  on the corrected architecture;
- Tach no longer permits runtime/sources/providers cycles;
- existing CTS and Liepin focused suites still pass;
- progress ledger records red evidence, fixes, reruns, and final verification.

