# Provider Interaction Agent With DokoBot Plan Set

> **For agentic workers:** This is an index, not an executable build plan. Use the linked implementation plans below with superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Split the PI Agent / DokoBot work into independently reviewable implementation plans while keeping one shared design contract.

**Architecture:** The shared spec owns the provider-interaction contract. The linked plans build the system in dependency order: contracts first, DokoBot capability and artifacts second, runtime grants and backend dispatch third, boundary guardrails last.

**Tech Stack:** Python 3.12, Pydantic v2, dataclasses, DokoBot CLI plus explicit DokoBot-compatible action manifests, pytest, Bun worker boundary checks.

**Spec:** `docs/superpowers/specs/2026-05-13-provider-interaction-agent-dokobot-design.md`

---

## Execution Order

1. `docs/superpowers/plans/2026-05-13-pi-agent-contracts-and-skill-recipes.md`
   - Builds typed PI task/action/result contracts, runtime detail-open grant models, backend/failure enums, and Liepin skill recipes.
   - Must land first because every later plan imports these contracts.

2. `docs/superpowers/plans/2026-05-13-dokobot-capability-and-protected-artifacts.md`
   - Builds DokoBot read-only capability probing, explicit action manifest negotiation, no-install/no-downgrade checks, structured read results, and artifact UI-safety checks.
   - Depends on the contract plan.

3. `docs/superpowers/plans/2026-05-13-detail-grants-and-backend-dispatch.md`
   - Builds detail-open grant validation, durable ledger/idempotency verification, connection/provider-account locking, explicit backend modes, and Liepin runner dispatch.
   - Depends on the contract and DokoBot/artifact plans.

4. `docs/superpowers/plans/2026-05-13-pi-agent-boundary-guards-and-compat.md`
   - Builds direct authenticated API replay scanning, Bun worker boundary alignment, and the final PI verification gate.
   - Depends on the first three plans.

## Build Gate

Do not execute this index as a build plan. Pick one linked plan at a time, run `fw-plan-review` for that plan, then run `fw-build` only on the reviewed plan.

## Self-Review

- Spec coverage remains centralized in the shared DokoBot design spec.
- The former 1316-line implementation plan has been split by independently testable boundary: contracts, DokoBot/artifacts, dispatch/grants, and guardrails.
- The old link stays valid as an index so prior review notes still have a landing page.
