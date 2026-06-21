# Architecture

`SeekTalent` is a local-first recruiter workbench. The conversation agent is the user-facing thread/turn layer, the workflow runtime is the execution engine, `runtime_control.sqlite3` is the canonical workflow control store, and Workbench tables are recruiter-facing projections. LLM calls are bounded stages that return structured outputs; they do not execute tools directly.

## Public entrypoints

| Entrypoint | Files | Role |
| --- | --- | --- |
| CLI | `src/seektalent/cli.py` | Primary user-facing `seektalent` command, env loading, argument parsing, human and JSON output. |
| Python API | `src/seektalent/api.py` | Production wrapper functions: `run_match(...)` and `run_match_async(...)` return `ProductionMatchResultV1`; debug entrypoints `run_match_debug(...)` and `run_match_debug_async(...)` return `MatchRunResult`. |
| Local UI API | `src/seektalent_ui/server.py` | Local HTTP API over the conversation agent, runtime-control store, worker, Workbench projection, and maintenance surfaces. |
| Web UI | `apps/web-react/` | React Agent Workbench shell over the local UI BFF. |

All product workflow starts, interventions, progress, checkpoints, candidate truth, and projection state flow through runtime-control. The runtime still owns recruiting execution, but it does not make artifacts the Workbench source of truth.

## Architecture diagram

```mermaid
flowchart LR
    user["Terminal user / wrapper"] --> cli["CLI\nsrc/seektalent/cli.py"]
    wrapper["Python integrator"] --> api["Python API\nsrc/seektalent/api.py"]
    browser["Browser UI"] --> webapp["apps/web-react"]
    webapp --> uiapi["UI API\nsrc/seektalent_ui/server.py"]

    cli --> api
    api --> runtime["WorkflowRuntime\nruntime/orchestrator.py"]
    uiapi --> agent["ConversationAgentService\nseektalent_conversation_agent"]
    agent --> control["RuntimeControlStore\nruntime_control.sqlite3"]
    control --> worker["RuntimeExecutionWorker"]
    worker --> runtime
    control --> workbench["WorkbenchStore projection\nworkbench.sqlite3"]

    config["AppSettings\nconfig.py"] --> runtime
    prompts["PromptRegistry\nprompting.py + prompts/*.md"] --> runtime

    runtime --> req["RequirementExtractor\nrequirements/"]
    runtime --> controller["ReActController\ncontroller/"]
    runtime --> source_contracts["Source contracts + registry\nsources/"]
    runtime --> retrieval["Retrieval planning\nretrieval/ + core/retrieval/"]
    runtime --> rescue["Rescue routing\nruntime/rescue_router.py"]
    runtime --> service_factory["Retrieval service factory\nretrieval/service_factory.py"]
    runtime --> service["Retrieval service\ncore/retrieval/service.py"]
    source_contracts --> source_adapters["Source adapters\nsources/cts + sources/liepin"]
    service_factory --> registry["Provider registry\nproviders/registry.py"]
    service --> adapter["Provider adapters\nproviders/cts + providers/liepin"]
    runtime --> scoring["ResumeScorer\nscoring/"]
    runtime --> reflection["ReflectionCritic\nreflection/"]
    runtime --> finalizer["Finalizer\nfinalize/"]
    runtime --> eval["Optional evaluator\nevaluation.py"]
    runtime --> sink["Runtime event/checkpoint sink"]
    sink --> control
    runtime --> tracer["RunTracer diagnostics\ntracing.py"]

    req -. "structured output" .-> llm["LLM provider\npydantic-ai"]
    controller -. "structured decision" .-> llm
    scoring -. "per-resume scorecards" .-> llm
    reflection -. "round advice" .-> llm
    finalizer -. "presentation text" .-> llm
    eval -. "judge calls when enabled" .-> llm

    retrieval --> source_contracts
    source_adapters --> service
    registry --> adapter
    adapter --> livects["Live CTS service"]
    adapter --> mockcts["Mock CTS corpus\ndev/tests only"]
    adapter --> liepinworker["Liepin worker / OpenCLI boundary"]

    rescue --> feedback["Candidate feedback\ncandidate_feedback/"]
    rescue --> discovery["Company discovery\ncompany_discovery/"]
    discovery --> bocha["Bocha search\nwhen enabled"]
    discovery -. "planning / evidence reduction" .-> llm

    tracer --> artifacts["artifacts/runs/YYYY/MM/DD/run_*\ndev/debug diagnostics only"]
```

## Runtime sequence

```mermaid
sequenceDiagram
    actor User
    participant Entry as CLI / Python API / UI API
    participant Runtime as WorkflowRuntime
    participant Control as RuntimeControlStore
    participant Tracer as RunTracer diagnostics
    participant Req as RequirementExtractor
    participant Controller as ReActController
    participant Retrieval as Retrieval planner
    participant Sources as Source registry/adapters
    participant Service as Retrieval service
    participant Adapter as Provider adapter
    participant Scorer as ResumeScorer
    participant Reflection as ReflectionCritic
    participant Finalizer as Finalizer
    participant LLM as LLM provider
    participant Artifacts as dev/debug artifacts

    User->>Entry: job_title + jd + notes
    Entry->>Control: enqueue or attach runtime run
    Control->>Runtime: worker executes claimed run
    Runtime->>Tracer: write optional diagnostics
    Tracer->>Artifacts: compact diagnostics or explicit debug output
    Runtime->>Req: extract requirements
    Req->>LLM: RequirementExtractionDraft
    LLM-->>Req: structured draft
    Req-->>Runtime: RequirementSheet + scoring policy
    Runtime->>Control: compact checkpoint and stage outputs

    loop round 1..max_rounds
        Runtime->>Controller: decide with controller context
        Controller->>LLM: ControllerDecision
        LLM-->>Controller: search_cts or stop
        Controller-->>Runtime: structured decision

        alt stop is allowed
            Runtime->>Control: final checkpoint and public event
            Runtime-->>Runtime: leave round loop
        else search selected sources
            Runtime->>Retrieval: build source-neutral source plan
            Runtime->>Sources: dispatch selected source lanes
            Sources->>Service: execute provider-backed search where needed
            Service->>Adapter: provider search request
            Adapter-->>Service: provider search result
            Service-->>Sources: raw candidates + audit metadata
            Sources-->>Runtime: source result + public evidence
            Runtime-->>Runtime: normalize, dedupe, update candidate store
            Runtime->>Scorer: score new resumes in parallel
            Scorer->>LLM: ScoredCandidateDraft per resume
            LLM-->>Scorer: structured scorecards
            Scorer-->>Runtime: scorecards + failures
            Runtime->>Reflection: review round
            Reflection->>LLM: ReflectionAdvice
            LLM-->>Reflection: structured advice
            Reflection-->>Runtime: next-round guidance
            Runtime->>Control: public event, checkpoint, and compact stage outputs
            Runtime->>Tracer: optional round diagnostics
        end

        opt low-quality rescue is required
            Runtime-->>Runtime: choose reserve, feedback, company discovery, or anchor-only lane
        end
    end

    Runtime->>Finalizer: finalize ranked top pool
    Finalizer->>LLM: FinalResultDraft
    LLM-->>Finalizer: structured final draft
    Finalizer-->>Runtime: FinalResult
    Runtime->>Runs: final_candidates.json, final_answer.md, run_summary.md
    Runtime-->>Entry: MatchRunResult debug payload
    Entry-->>Entry: project ProductionMatchResultV1 for default Python API
    Entry-->>User: human text or JSON payload
```

The local Workbench API stores runtime-owned session, source-lane, and final-top10 state in its SQLite store. UI endpoints project persisted Runtime fields directly; they do not run a second backend execution flow or translate final artifacts through a legacy UI DTO layer.

## Core modules

| Module | Responsibility |
| --- | --- |
| `src/seektalent/runtime/orchestrator.py` | Main control loop, round lifecycle, progress events, artifact writes, stop handling, rescue handoff, source dispatch, and finalization. |
| `src/seektalent/runtime/context_builder.py` | Builds slim context objects for controller, scoring, reflection, and finalization. |
| `src/seektalent/models.py` | Shared Pydantic contracts for requirements, retrieval plans, controller decisions, scorecards, final results, and run state. |
| `src/seektalent/requirements/` | Turns input truth into a normalized requirement sheet and scoring policy. |
| `src/seektalent/controller/` | Chooses each round's action and proposed query/filter plan. The controller does not execute CTS or other tools. |
| `src/seektalent/retrieval/` | Generic retrieval planning helpers: query-term compilation, query planning, and location execution planning. |
| `src/seektalent/core/retrieval/` | Source-agnostic retrieval contract and service used behind runtime/source adapter execution. |
| `src/seektalent/sources/` | Source-neutral contracts, registry, public event codes, shared source helpers, CTS source projection, and Liepin runtime/smoke bridge code. |
| `src/seektalent/providers/` | Provider registry plus provider-specific adapters, clients, transport models, and provider-local projection logic. Providers do not import runtime DTOs. |
| `src/seektalent/clients/` | Concrete CTS transport clients used behind the CTS provider adapter for live CTS requests or the development mock corpus. |
| `src/seektalent/scoring/` | Scores normalized resumes concurrently, one resume per LLM branch. |
| `src/seektalent/reflection/` | Reviews a completed round and produces advice for subsequent retrieval. |
| `src/seektalent/finalize/` | Preserves runtime ranking order while generating final shortlist presentation text. |
| `src/seektalent/tracing.py` | Writes trace events, JSON artifacts, prompt snapshots, hashes, and compact LLM call metadata. |

## Runtime-Control State

`src/seektalent_runtime_control` owns canonical workflow control:

- run identity, start idempotency, status FSM, commands, and human interventions;
- worker claims, executor leases, attempt fencing, checkpoints, snapshots, and final summaries;
- compact public/developer events and projection watermarks;
- canonical candidate identities, evidence, finalization revisions, and shortlist product facts;
- retention metadata, artifact refs, and repair/import source metadata.

Workbench state is a projection/read model. Runtime public progress reaches Workbench through runtime-control projection, not `runtime/public_events.jsonl`.

## Runtime Execution State

The runtime keeps state explicit:

- `RunState` carries input truth, requirement sheet, scoring policy, retrieval state, candidates, normalized resumes, scorecards, top-pool ids, and round history.
- `RetrievalState` tracks the query-term pool, sent query history, plan version, projection result, and rescue attempts.
- `RoundState` records the controller decision, source/retrieval plan, source search observations, scored top candidates, dropped candidates, and reflection advice for one round.

The state objects live in `src/seektalent/models.py`. Safe boundary state is written to runtime-control checkpoints and compact stage outputs; artifacts are optional diagnostics.

## Artifact model

Artifacts are diagnostics and exports, not product truth. In `dev` and explicit `debug_full_local`, a run may write partitioned files under `artifacts/runs/YYYY/MM/DD/run_*`. Important diagnostic groups include:

- run setup: `run_config.json`, `input_snapshot.json`, `input_truth.json`, `prompt_snapshots/`
- requirement setup: `requirement_extraction_draft.json`, `requirements_call.json`, `requirement_sheet.json`, `scoring_policy.json`
- round outputs: `controller_*`, source/retrieval plans and observations, provider-specific query snapshots where available, `scorecards.jsonl`, `reflection_*`, `round_review.md`
- final outputs: `finalizer_context.json`, `finalizer_call.json`, `final_candidates.json`, `final_answer.md`, `run_summary.md`
- diagnostics: `events.jsonl`, `trace.log`, `sent_query_history.json`, `search_diagnostics.json`, `term_surface_audit.json`

Production Workbench progress, completion, candidate review rows, and final shortlist projection do not depend on these files. Old artifact imports live behind explicit debug/repair commands.

See [Outputs](outputs.md) for the full file reference.

## Boundaries

- CLI, Python API, and UI API are shells around `WorkflowRuntime`, with the CLI as the primary user entrypoint.
- Conversation-agent tools remain domain-specific recruiting workflow tools; SeekTalent does not copy Codex shell, patch, git, file-search, or code-execution tools.
- UI depends on core runtime code; `src/seektalent` must not import `seektalent_ui` or `experiments`.
- The controller returns structured decisions only. Python runtime code executes CTS, scoring fan-out, artifact writes, and stop rules.
- Generic retrieval planning stays under `src/seektalent/retrieval/` and `src/seektalent/core/retrieval/`.
- Runtime depends on source-neutral contracts under `src/seektalent/sources/`; it must not import concrete `seektalent.providers.*` modules.
- Source adapters are the bridge between runtime/source contracts and provider-backed execution. CTS projection lives under `src/seektalent/sources/cts/`; Liepin runtime lane, smoke CLI, and safe reason-code mapping live under `src/seektalent/sources/liepin/`.
- Provider-specific request details stay under `src/seektalent/providers/`. Providers may depend on clients, core retrieval contracts, retrieval primitives, and source contracts, but not runtime DTOs.
- Provider registry construction is outside runtime in `src/seektalent/retrieval/service_factory.py`; runtime receives provider access through retrieval/source boundaries.
- CTS transport details stay inside `src/seektalent/clients/cts_client.py`, behind `src/seektalent/providers/cts/adapter.py`.
- Liepin transport, OpenCLI, worker contracts, browser automation, and provider safety details stay inside `src/seektalent/providers/liepin/`, behind the Liepin source adapter.
- Mock CTS is for source-checkout development and tests; the published CLI rejects it.
- Optional rescue lanes are runtime decisions. They can broaden the term pool, inject candidate feedback, run company discovery, or try anchor-only retrieval when quality gates require more search.
- LLM structured output retries are local to Pydantic AI calls. The runtime does not add fallback model chains.

## Related docs

- [CLI](cli.md)
- [Configuration](configuration.md)
- [Outputs](outputs.md)
- [UI](ui.md)
- [Development](development.md)
- [Data flow](data-flow.md)
- [Source contracts](source-contracts.md)
- [Architecture dependency observations](architecture-dependencies.md)
- Historical design notes: `docs/v-0.1/`, `docs/v-0.2/`
