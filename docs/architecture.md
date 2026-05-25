# Architecture

`SeekTalent` is a CLI-first, local-first resume matching engine. Public entrypoints collect a job title, JD, and optional notes, then hand them to one deterministic runtime rooted in `src/seektalent/runtime/orchestrator.py`. The runtime owns orchestration, budgets, provider selection, retrieval execution, deduplication, artifact writing, and final ranking. LLM calls are bounded stages that return structured outputs; they do not execute tools directly.

## Public entrypoints

| Entrypoint | Files | Role |
| --- | --- | --- |
| CLI | `src/seektalent/cli.py` | Primary user-facing `seektalent` command, env loading, argument parsing, human and JSON output. |
| Python API | `src/seektalent/api.py` | Stable wrapper functions: `run_match(...)` and `run_match_async(...)`. |
| Local UI API | `src/seektalent_ui/server.py` | Thin local HTTP API that runs the same runtime in a background thread. |
| Web UI | `apps/web/` | Minimal browser shell over the local UI API. |

All product surfaces converge on `WorkflowRuntime` in `src/seektalent/runtime/orchestrator.py`.

## Architecture diagram

```mermaid
flowchart LR
    user["Terminal user / wrapper"] --> cli["CLI\nsrc/seektalent/cli.py"]
    wrapper["Python integrator"] --> api["Python API\nsrc/seektalent/api.py"]
    browser["Browser UI"] --> webapp["apps/web"]
    webapp --> uiapi["UI API\nsrc/seektalent_ui/server.py"]

    cli --> api
    api --> runtime["WorkflowRuntime\nruntime/orchestrator.py"]
    uiapi --> runtime

    config["AppSettings\nconfig.py"] --> runtime
    prompts["PromptRegistry\nprompting.py + prompts/*.md"] --> runtime

    runtime --> req["RequirementExtractor\nrequirements/"]
    runtime --> controller["ReActController\ncontroller/"]
    runtime --> retrieval["Retrieval planning\nretrieval/ + core/retrieval/"]
    runtime --> rescue["Rescue routing\nruntime/rescue_router.py"]
    runtime --> registry["Provider registry\nproviders/registry.py"]
    runtime --> service["Retrieval service\ncore/retrieval/service.py"]
    service --> adapter["Provider adapter\nproviders/cts/adapter.py"]
    runtime --> scoring["ResumeScorer\nscoring/"]
    runtime --> reflection["ReflectionCritic\nreflection/"]
    runtime --> finalizer["Finalizer\nfinalize/"]
    runtime --> eval["Optional evaluator\nevaluation.py"]
    runtime --> tracer["RunTracer\ntracing.py"]

    req -. "structured output" .-> llm["LLM provider\npydantic-ai"]
    controller -. "structured decision" .-> llm
    scoring -. "per-resume scorecards" .-> llm
    reflection -. "round advice" .-> llm
    finalizer -. "presentation text" .-> llm
    eval -. "judge calls when enabled" .-> llm

    retrieval --> service
    registry --> adapter
    adapter --> livects["Live CTS service"]
    adapter --> mockcts["Mock CTS corpus\ndev/tests only"]

    rescue --> feedback["Candidate feedback\ncandidate_feedback/"]
    rescue --> discovery["Company discovery\ncompany_discovery/"]
    discovery --> bocha["Bocha search\nwhen enabled"]
    discovery -. "planning / evidence reduction" .-> llm

    tracer --> runs["runs/<timestamp>_<run_id>/\ntrace, events, JSON, markdown"]
```

## Runtime sequence

```mermaid
sequenceDiagram
    actor User
    participant Entry as CLI / Python API / UI API
    participant Runtime as WorkflowRuntime
    participant Tracer as RunTracer
    participant Req as RequirementExtractor
    participant Controller as ReActController
    participant Retrieval as Retrieval planner
    participant Registry as Provider registry
    participant Service as Retrieval service
    participant Adapter as Provider adapter
    participant Scorer as ResumeScorer
    participant Reflection as ReflectionCritic
    participant Finalizer as Finalizer
    participant LLM as LLM provider
    participant Runs as runs/ artifacts

    User->>Entry: job_title + jd + notes
    Entry->>Runtime: run or run_async
    Runtime->>Tracer: create run directory and snapshots
    Tracer->>Runs: run_config, input_snapshot, prompt_snapshots
    Runtime->>Req: extract requirements
    Req->>LLM: RequirementExtractionDraft
    LLM-->>Req: structured draft
    Req-->>Runtime: RequirementSheet + scoring policy
    Runtime->>Runs: input_truth, requirement_sheet, scoring_policy

    loop round 1..max_rounds
        Runtime->>Controller: decide with controller context
        Controller->>LLM: ControllerDecision
        LLM-->>Controller: search_cts or stop
        Controller-->>Runtime: structured decision

        alt stop is allowed
            Runtime->>Runs: controller_decision
            Runtime-->>Runtime: leave round loop
        else search CTS
            Runtime->>Retrieval: project filters and build retrieval plan
            Retrieval-->>Runtime: CTS queries + runtime constraints
            Runtime->>Registry: resolve provider adapter
            Runtime->>Service: execute paginated search
            Service->>Adapter: provider search request
            Adapter-->>Service: provider search result
            Service-->>Runtime: raw candidates + audit metadata
            Runtime-->>Runtime: normalize, dedupe, update candidate store
            Runtime->>Scorer: score new resumes in parallel
            Scorer->>LLM: ScoredCandidateDraft per resume
            LLM-->>Scorer: structured scorecards
            Scorer-->>Runtime: scorecards + failures
            Runtime->>Reflection: review round
            Reflection->>LLM: ReflectionAdvice
            LLM-->>Reflection: structured advice
            Reflection-->>Runtime: next-round guidance
            Runtime->>Runs: round artifacts and round_review.md
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
    Runtime-->>Entry: MatchRunResult
    Entry-->>User: human text or JSON payload
```

The local Workbench API stores runtime-owned session, source-lane, and final-top10 state in its SQLite store. UI endpoints project persisted Runtime fields directly; they do not run a second backend execution flow or translate final artifacts through a legacy UI DTO layer.

## Core modules

| Module | Responsibility |
| --- | --- |
| `src/seektalent/runtime/orchestrator.py` | Main control loop, round lifecycle, progress events, artifact writes, stop handling, rescue handoff, provider resolution, and finalization. |
| `src/seektalent/runtime/context_builder.py` | Builds slim context objects for controller, scoring, reflection, and finalization. |
| `src/seektalent/models.py` | Shared Pydantic contracts for requirements, retrieval plans, controller decisions, scorecards, final results, and run state. |
| `src/seektalent/requirements/` | Turns input truth into a normalized requirement sheet and scoring policy. |
| `src/seektalent/controller/` | Chooses each round's action and proposed query/filter plan. The controller does not execute CTS or other tools. |
| `src/seektalent/retrieval/` | Generic retrieval planning helpers: query-term compilation, query planning, and location execution planning. |
| `src/seektalent/core/retrieval/` | Source-agnostic retrieval contract and service used by runtime to call providers. |
| `src/seektalent/providers/` | Provider registry plus provider-specific adapters and provider-local projection logic. |
| `src/seektalent/clients/` | Concrete CTS transport clients used behind the CTS provider adapter for live CTS requests or the development mock corpus. |
| `src/seektalent/scoring/` | Scores normalized resumes concurrently, one resume per LLM branch. |
| `src/seektalent/reflection/` | Reviews a completed round and produces advice for subsequent retrieval. |
| `src/seektalent/finalize/` | Preserves runtime ranking order while generating final shortlist presentation text. |
| `src/seektalent/tracing.py` | Writes trace events, JSON artifacts, prompt snapshots, hashes, and compact LLM call metadata. |

## Runtime state

The runtime keeps state explicit:

- `RunState` carries input truth, requirement sheet, scoring policy, retrieval state, candidates, normalized resumes, scorecards, top-pool ids, and round history.
- `RetrievalState` tracks the query-term pool, sent query history, plan version, projection result, and rescue attempts.
- `RoundState` records the controller decision, retrieval plan, CTS queries, search observation, scored top candidates, dropped candidates, and reflection advice for one round.

The state objects live in `src/seektalent/models.py` and are written out as artifacts instead of being hidden behind a long-lived service object.

## Artifact model

Each run writes a directory under `runs/` by default. The important artifact groups are:

- run setup: `run_config.json`, `input_snapshot.json`, `input_truth.json`, `prompt_snapshots/`
- requirement setup: `requirement_extraction_draft.json`, `requirements_call.json`, `requirement_sheet.json`, `scoring_policy.json`
- round outputs: `controller_*`, `retrieval_plan.json`, `cts_queries.json`, `search_observation.json`, `scorecards.jsonl`, `reflection_*`, `round_review.md`
- final outputs: `finalizer_context.json`, `finalizer_call.json`, `final_candidates.json`, `final_answer.md`, `run_summary.md`
- diagnostics: `events.jsonl`, `trace.log`, `sent_query_history.json`, `search_diagnostics.json`, `term_surface_audit.json`

See [Outputs](outputs.md) for the full file reference.

## Boundaries

- CLI, Python API, and UI API are shells around `WorkflowRuntime`, with the CLI as the primary user entrypoint.
- UI depends on core runtime code; `src/seektalent` must not import `seektalent_ui` or `experiments`.
- The controller returns structured decisions only. Python runtime code executes CTS, scoring fan-out, artifact writes, and stop rules.
- Generic retrieval planning stays under `src/seektalent/retrieval/` and `src/seektalent/core/retrieval/`.
- Provider-specific request details stay under `src/seektalent/providers/`; runtime search execution now flows through `get_provider_adapter(...)` plus `RetrievalService` instead of importing CTS clients directly.
- CTS transport details stay inside `src/seektalent/clients/cts_client.py`, behind `src/seektalent/providers/cts/adapter.py`.
- Mock CTS is for source-checkout development and tests; the published CLI rejects it.
- Optional rescue lanes are runtime decisions. They can broaden the term pool, inject candidate feedback, run company discovery, or try anchor-only retrieval when quality gates require more search.
- LLM structured output retries are local to Pydantic AI calls. The runtime does not add fallback model chains.

## Related docs

- [CLI](cli.md)
- [Configuration](configuration.md)
- [Outputs](outputs.md)
- [UI](ui.md)
- [Development](development.md)
- [Architecture dependency observations](architecture-dependencies.md)
- Historical design notes: `docs/v-0.1/`, `docs/v-0.2/`
