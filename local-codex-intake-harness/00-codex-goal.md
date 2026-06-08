# Codex Goal: Build Local Codex Intake Harness

## Objective

Build the production-ready Local Codex Intake Harness described in this directory. The finished feature must let a local SeekTalent Workbench user enter hiring needs through a general conversation window, receive a structured requirement confirmation, confirm it, create a Workbench session, and start the existing requirement preparation flow. The intake conversation is the main agent experience. The current SeekTalent runtime remains the downstream child workflow.

## Required Reading

Read these files before editing code:

```text
local-codex-intake-harness/00-codex-goal.md
local-codex-intake-harness/MANIFEST.md
local-codex-intake-harness/13-execution-control.md
local-codex-intake-harness/10-acceptance-criteria.md
local-codex-intake-harness/11-implementation-plan.md
```

Then read the remaining files in this directory:

```text
local-codex-intake-harness/00-index.md
local-codex-intake-harness/01-product-goal.md
local-codex-intake-harness/02-boundaries.md
local-codex-intake-harness/03-system-architecture.md
local-codex-intake-harness/04-codex-harness-and-memory.md
local-codex-intake-harness/05-model-provider-config.md
local-codex-intake-harness/06-intake-conversation-contract.md
local-codex-intake-harness/07-workbench-integration-contract.md
local-codex-intake-harness/08-frontend-ux.md
local-codex-intake-harness/09-data-storage-and-migrations.md
```

Also read:

```text
AGENTS.md
docs/governance/ai-coding-policy.md
src/seektalent_ui/server.py
src/seektalent_ui/workbench_routes.py
src/seektalent_ui/models.py
src/seektalent_ui/runtime_bridge.py
apps/web-svelte/src/routes/(app)/sessions/+page.svelte
apps/web-svelte/src/lib/api/workbench.ts
apps/web-svelte/src/lib/query/keys.ts
apps/web-svelte/src/lib/components/CreateSessionForm.svelte
```

If `seektalent_source_decoupling_correction_goal_pack/` exists, read its `00-codex-goal.md`, `MANIFEST.md`, `03-acceptance.md`, and `07-execution-control.md` before changing source-selection contracts. The intake harness must not reintroduce source-specific coupling that the source-decoupling correction goal is removing.

## Goals

- Build a local transcript-style intake agent for new SeekTalent hiring workflows.
- Use Codex App Server directly with project-isolated `CODEX_HOME`.
- Use a default non-OpenAI provider.
- Keep Codex memory isolated from the user's global Codex memory.
- Persist canonical intake state in a local SeekTalent database.
- Create Workbench sessions only after user confirmation.
- Start the existing requirement-preparation workflow through Workbench.
- Let the transcript answer progress/result questions from read-only Workbench state after workflow start.
- Keep source selection registry-driven rather than hard-coding CTS/Liepin as the fixed source universe.

## Non-Goals

- Do not use the Codex SDK.
- Do not refactor `src/seektalent/runtime/**`.
- Do not mutate in-progress runtime workflow state in the initial version.
- Do not build a SaaS control plane.
- Do not make Codex memory canonical product state.
- Do not fork Codex.
- Do not ship empty adapters, fake integrations, or TODO-driven scaffolding.

## Non-Negotiables

- Do not read or write `~/.codex`.
- Do not touch Codex's own memories.
- Do not default to OpenAI API.
- Do not call `codex login`.
- Do not call runtime internals from intake.
- Do not package Codex memory, Codex auth state, or the user's global `~/.codex`.
- Do not redistribute Codex CLI, App Server, binary, source, or other Codex artifacts without retaining the applicable Apache-2.0 license notice.
- Do not encode `{"cts", "liepin"}` as the complete future source universe. If those ids are present, treat them as current registered source ids only.

## Expected Deliverable

Implement:

- backend intake package;
- local SQLite intake store;
- project-local Codex config and memory path isolation;
- Codex App Server adapter with fakeable tests;
- intake conversation state machine;
- Workbench bridge;
- registry-driven source catalog integration;
- read-only workflow reader for progress/result answers;
- FastAPI intake router;
- Svelte upward-scrolling transcript conversation UI;
- frontend API wrappers and state helpers;
- OpenAPI schema update;
- backend tests;
- frontend tests;
- provider smoke status route;
- memory reset route limited to project-local Codex memory;
- commercial packaging notice/exclusion handling for Codex artifacts, memory, auth, and global `~/.codex`;
- progress ledger updates during the Goal run.

## Acceptance

The task is complete only when every applicable criterion in `10-acceptance-criteria.md` passes and the Goal run protocol in `13-execution-control.md` is followed.

Minimum required verification:

```bash
uv run --group dev python -m pytest \
  tests/test_intake_paths.py \
  tests/test_intake_store.py \
  tests/test_intake_codex_config.py \
  tests/test_intake_codex_app_server.py \
  tests/test_intake_source_catalog.py \
  tests/test_intake_service.py \
  tests/test_intake_workbench_bridge.py \
  tests/test_intake_workflow_reader.py \
  tests/test_workbench_api.py \
  -q

uv run --group dev ruff check src tests

cd apps/web-svelte
bun run check
bun run lint
bun run test
bun run build

cd ../..
scripts/verify-dev-workbench.sh
git diff --check
```

If red-zone files are touched:

```bash
scripts/verify-red-zone.sh
```

## Stop Conditions

Stop and report blocked if:

- Codex App Server is unavailable and no direct App Server surface can be verified;
- a non-OpenAI provider cannot be configured and the implementation would otherwise require OpenAI by default;
- safe Codex memory isolation cannot be enforced;
- implementation would require the Codex SDK;
- implementation would require initial runtime-in-progress intervention;
- commercial packaging would include Codex auth, Codex memory, or global `~/.codex`;
- Codex artifacts would be redistributed without the applicable Apache-2.0 notice;
- the implementation requires runtime refactoring to proceed;
- the source registry/source catalog contract is mid-change and cannot be integrated safely;
- another concurrent change modifies the same files in a way that makes a safe merge impossible.

Do not mark the task complete in any blocked condition.

## Final Report Format

The final report must include:

```text
Status:
Changed files:
Codex harness:
Memory isolation:
Provider smoke:
Commercial packaging:
Source catalog:
Workbench handoff:
Workflow read-only control:
Runtime boundary:
Frontend verification:
Backend verification:
Red-zone verification:
Progress ledger:
Known limitations:
```

The report must explicitly state whether `~/.codex` was untouched.

## Required Completion Phrase

The final response or PR summary must include:

```text
This PR completes the local Codex intake harness goal. It is not an MVP scaffold.
```
