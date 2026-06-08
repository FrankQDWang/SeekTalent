# Boundaries

## Hard Rules

These rules are mandatory for the long-running implementation.

1. Do not read from or write to `~/.codex`.
2. Do not read from or write to `~/.codex/memories`.
3. Do not use `OPENAI_API_KEY` as the default credential path.
4. Do not call `codex login`.
5. Do not set `requires_openai_auth = true` in project-generated Codex config.
6. Do not use the Codex SDK for this feature.
7. Do not refactor `src/seektalent/runtime/**`.
8. Do not change `WorkflowRuntime.run(...)` or `WorkflowRuntime.extract_requirements(...)`.
9. Do not make Codex memory the source of truth for business facts.
10. Do not let Codex App Server run with the repository root as its working directory.
11. Do not fork OpenAI Codex source into this repository.
12. Do not introduce a hosted service, remote database, or SaaS control plane.
13. Do not implement hidden fallback chains that silently switch providers.
14. Do not package Codex auth state, Codex memory, or the user's global `~/.codex` into SeekTalent.
15. Do not redistribute Codex CLI, App Server, binary, source, or other Codex artifacts without retaining the applicable Apache-2.0 license notice.
16. Do not implement runtime-in-progress intervention in the initial version.

## Allowed Product Scope

The implementation may add:

- a new backend package for intake harness code;
- a thin FastAPI router for intake endpoints;
- local SQLite persistence for intake conversations;
- project-local Codex home and workspace directories;
- Codex App Server integration;
- frontend conversation UI for new sessions;
- tests, fixtures, and local smoke commands.

## Preferred File Boundaries

Create a new package:

```text
src/seektalent_intake/
```

Recommended responsibilities:

```text
src/seektalent_intake/models.py              # intake domain and API-safe contracts
src/seektalent_intake/paths.py               # project-local data paths
src/seektalent_intake/store.py               # SQLite persistence
src/seektalent_intake/codex_config.py        # project-local Codex config generation and validation
src/seektalent_intake/codex_app_server.py    # narrow Codex app-server client adapter
src/seektalent_intake/intake_service.py      # conversation state machine
src/seektalent_intake/workbench_bridge.py    # Workbench session creation and requirement preparation handoff
src/seektalent_intake/errors.py              # named exceptions and public reason codes
```

Create a thin UI API router:

```text
src/seektalent_ui/intake_routes.py
```

Modify the app setup only as needed:

```text
src/seektalent_ui/server.py
```

Frontend code should live under existing Svelte boundaries:

```text
apps/web-svelte/src/lib/intake/
apps/web-svelte/src/lib/components/IntakeConversation.svelte
apps/web-svelte/src/routes/(app)/sessions/+page.svelte
```

Generated OpenAPI types may update:

```text
apps/web-svelte/src/lib/api/schema.d.ts
```

## High-Risk Paths

Avoid these paths unless the implementation cannot work without them:

```text
src/seektalent/runtime/**
src/seektalent/models.py
src/seektalent/config.py
src/seektalent/prompts/**
src/seektalent/providers/**
src/seektalent_ui/workbench_store.py
src/seektalent_ui/runtime_bridge.py
src/seektalent_ui/runtime_graph.py
scripts/verify-dev-workbench.sh
scripts/verify-red-zone.sh
```

If a high-risk path is touched, the final report must explain why and must run the matching verification gate from `docs/governance/ai-coding-policy.md`.

## Parallel Runtime Refactor Boundary

Another Codex window may refactor the current workflow runtime in parallel. This intake work must therefore avoid shared runtime ownership.

The initial crossing point is:

```text
Intake confirmation
  -> Workbench session creation
  -> Workbench requirement preparation
  -> existing runtime bridge
  -> existing WorkflowRuntime
```

Do not add direct calls from intake code into runtime internals. Do not import from `src/seektalent/runtime/**` inside `src/seektalent_intake/**`.

## Workflow Control Boundary

Initial version:

- the intake conversation is the main agent;
- the SeekTalent runtime is a child workflow;
- the intake agent may start the Workbench workflow after user confirmation;
- the intake agent may read workflow progress through existing Workbench/session/event/runtime-graph surfaces;
- the intake agent may read final workflow results;
- the intake agent must not modify, pause, resume, replan, inject candidates into, or otherwise mutate an in-progress runtime workflow.

Long-term direction:

- runtime intervention may become a later feature;
- intervention requires a separate spec, explicit user approval, and dedicated runtime control APIs;
- do not smuggle intervention into the initial implementation through generic tool calls or hidden admin endpoints.

## State Boundary

The following are canonical product state:

- intake conversation rows in the intake SQLite database;
- Workbench session rows in the existing Workbench store;
- Workbench requirement review rows;
- existing runtime artifacts after Workbench starts sourcing.

The following are not canonical product state:

- Codex memories;
- Codex thread summaries;
- Codex transient turn output;
- frontend component state.

## OpenAI Boundary

OpenAI API must not be required for local operation. The default provider posture is DashScope/OpenAI-compatible or another user-configured non-OpenAI provider.

The implementation may support OpenAI only as an explicit user override. It must never be the default path for this project.

## Commercial Packaging Boundary

The intended commercial shape is:

```text
SeekTalent commercial product
  -> local/internal call to Codex App Server
  -> project-isolated CODEX_HOME
  -> default non-OpenAI provider
  -> Apache-2.0 notice retained for redistributed Codex artifacts
  -> no Codex memory/auth/global ~/.codex packaged into product
```

Packaging rules:

- If SeekTalent distributes Codex artifacts, include the applicable Apache-2.0 license notice in the shipped product and in the repository's third-party notice surface.
- If SeekTalent does not distribute Codex artifacts and only invokes an installed local Codex binary, document that clearly.
- Never copy `.seektalent/codex_home/memories`, Codex auth files, or `~/.codex` into a product package.
- Generated product archives must exclude project-local Codex memory and auth material even if they include project-local config templates.
