# SeekTalent Source Decoupling Correction Goal Pack

This corrective goal pack exists because the previous source-decoupling goal left
core runtime/source/provider coupling in place while the verification gates still
passed.

Primary entrypoint:

- `00-codex-goal.md`

Use this pack with Codex Goal mode. Do not run it as a sequence of manual HITL
patch requests.

## Document Order

1. `00-codex-goal.md`: objective, non-negotiables, completion definition.
2. `01-verified-gap-report.md`: repository evidence for the unfinished work.
3. `02-target-architecture.md`: corrected dependency and ownership model.
4. `03-acceptance.md`: hard acceptance criteria and required commands.
5. `04-execution-sequence.md`: execution phases for the Goal worker.
6. `05-boundary-gates.md`: required hardening for static checks and Tach.
7. `06-fixture-runtime-contract.md`: full runtime fixture-source proof.
8. `07-execution-control.md`: Goal invocation, preflight, ledger, resume protocol.

## Operating Rule

The previous goal pack and progress ledger are audit evidence, not proof of
completion. This correction goal must verify the current repository from source
code, strengthen the gates first, then make the architecture pass those gates.

