# Shared Product And Architecture

This compacted document preserves the content from the source documents below. Source headings are demoted one level so the merged document remains navigable without changing the substantive requirements.

## Source Documents

- `01-shared-product-spec.md`
- `02-shared-architecture.md`

---

## Source: `01-shared-product-spec.md`

## Shared Product Spec

### Product Outcome

SeekTalent should support a local recruiting agent that works in a transcript. The current goals build the backend, APIs, and UI-ready data required for that transcript; the Svelte UI itself is deferred until designer-provided screens are available.

A backend caller or future UI gives the agent a JD or rough hiring intent. The agent extracts structured requirements, returns confirmation-ready data, accepts persisted edit and confirmation operations, runs the sourcing workflow, produces transcript-ready progress messages from real execution state, accepts runtime-control commands, answers detail questions, and summarizes the outcome.

### Primary User Flow

```text
User or future UI submits JD
  -> Agent asks runtime control plane to extract requirements
  -> Runtime control plane returns structured editable draft
  -> Agent returns transcript-ready review data with selected checkbox state
  -> Caller edits, moves, deletes, or deselects requirement items through real APIs
  -> Caller confirms
  -> Agent sends approved requirement revision to runtime control plane
  -> Runtime control plane starts workflow
  -> Agent watches persisted events and snapshots
  -> Agent persists transcript-ready progress messages
  -> Caller can pause, cancel, resume, ask details, or add next-round requirement through real APIs
  -> Runtime completes
  -> Agent summarizes final result from final snapshot/result and user instruction
```

### Requirement Confirmation Surface

The transcript-ready requirement confirmation data must contain these sections:

| Display section | Backend field | Display content | Supported user actions |
| --- | --- | --- | --- |
| 必须满足 | `must_have_capabilities` | 候选人必须具备的能力 | select/unselect, edit, delete, move to 加分项 |
| 加分项 | `preferred_capabilities` | 有则更匹配的能力或背景 | select/unselect, edit, delete, move to 必须满足 |
| 硬性筛选条件 | `hard_constraints` | 地点、学历、经验、年龄、性别、学校、学校类型、公司 | select/unselect, edit, delete |
| 排除信号 | `exclusion_signals` | 出现后明显不匹配的信号 | select/unselect, edit, delete |
| 检索关键词 | `initial_query_term_pool[].term` | 用于召回简历的关键词 | select/unselect, enable/disable, edit, delete |

Default state: every extracted item is selected.

User deselection must be persisted as part of the draft revision. It must not exist only in frontend state.

### Extra User Requirements

The user can add extra requirements that were not extracted from the JD.

Free-form user additions must not be interpreted by the conversational agent as final business fields. The agent sends the text, the current draft revision, and any UI section hint to the runtime control plane. Runtime control calls the Workflow Runtime requirement parsing/normalization path and returns a draft amendment.

Examples:

- user types: `另外希望有 Kafka 实战，最好做过 toB SaaS`;
- user types while focused in 排除信号: `频繁跳槽的不要`;
- user edits 硬性筛选条件 with: `上海，本科，5年以上，35岁以下`.

The result must become normal draft items with backend fields, selected state, provenance, and revision history before confirmation. Confirmation still sends a validated `RequirementSheet` to Workflow Runtime.

### Runtime Control Commands

During execution, the caller can submit natural-language command messages:

- pause the run;
- cancel/end the run;
- resume a paused run;
- add an extra requirement for the next safe boundary;
- ask why a round searched a specific keyword;
- ask why a candidate was scored as fit or not fit;
- ask what the runtime is doing now;
- ask for a final summary after completion.

The agent interprets the intent. The runtime control plane executes the command. The transcript-ready message stream reports accepted, pending, applied, completed, or rejected command state from persisted command records.

### Complete First Version

The first shippable version must be complete for the local product scope:

- real storage;
- real APIs;
- real runtime integration;
- real API operations for every future UI control;
- real UI-ready DTOs and view models;
- real command state;
- real event-driven progress;
- real tests;
- no fake tools;
- no data values used only to make tests or screens appear complete;
- no "UI only" controls;
- no temporary UI screens before design-backed UI work.

The first version does not need to become Temporal, a cloud control plane, or a general workflow runtime.

---

## Source: `02-shared-architecture.md`

## Shared Architecture

### Target Shape

```text
Future Svelte transcript UI / API clients
  -> Conversational Agent API
     -> Agent service
        -> OpenAI Agents SDK runtime adapter
        -> Agent transcript store
        -> Runtime-control tools
           -> Runtime Control Plane
              -> Runtime control SQLite store
              -> Artifact/trace sink policy
              -> Workbench bridge
              -> WorkflowRuntime child executor
              -> WorkbenchStore/event/session surfaces
```

### Dependency Direction

Allowed:

```text
seektalent_ui -> seektalent_conversation_agent -> seektalent_runtime_control
seektalent_conversation_agent -> seektalent_runtime_control public contracts
seektalent_runtime_control -> seektalent.runtime only through approved executor adapter
seektalent_runtime_control -> seektalent_ui.workbench_store through explicit bridge inputs
seektalent_runtime_control -> seektalent.source_contracts
seektalent_conversation_agent -> OpenAI Agents SDK through AgentRuntime only
```

Forbidden:

```text
seektalent_conversation_agent -> seektalent.runtime
seektalent_conversation_agent -> seektalent.providers
seektalent_conversation_agent -> RunState direct access
Frontend -> WorkflowRuntime
Frontend -> runtime-control SQLite
Runtime control plane -> Codex memory
Codex memory -> canonical product state
SeekTalent advisory memory -> canonical requirement state
SeekTalent advisory memory -> canonical runtime state
SeekTalent advisory memory -> candidate facts
Product runtime -> Codex CLI
Product runtime -> Codex App Server
Product runtime -> Codex MCP server
Product runtime -> Codex SDK
src/seektalent_ui routes -> OpenAI Agents SDK direct imports
```

### Package Ownership

Main code for the two goals belongs in new top-level packages under `src/`:

```text
src/seektalent_runtime_control/
src/seektalent_conversation_agent/
```

Existing packages are integration surfaces:

- `src/seektalent/` may receive narrow runtime hooks, executor adapter seams, and artifact/tracing policy hooks required for Goal 1.
- `src/seektalent_ui/` may receive route wiring, request/response DTOs, server registration, and compatibility bridges.
- Neither goal should place new product logic inside `src/seektalent/`.
- Goal 2 should not place agent orchestration logic inside `src/seektalent_ui/`; UI routes delegate to `src/seektalent_conversation_agent/`.

### Runtime Control Plane Responsibility

The runtime control plane owns:

- run lifecycle;
- command lifecycle;
- safe-boundary pause/cancel/resume;
- requirement draft and revision persistence;
- workflow snapshot/read model;
- runtime event persistence;
- checkpoint persistence;
- artifact/trace output policy;
- Workbench session mapping;
- idempotency across duplicate user actions.

### Conversational Agent Responsibility

The conversational agent owns:

- user intent parsing;
- transcript message persistence;
- tool selection;
- OpenAI Agents SDK run setup through `AgentRuntime`;
- Chinese recruiter-facing wording;
- preparing requirement confirmation state for transcript/API clients;
- converting user edits into runtime-control API calls;
- summarizing snapshots and final results.

The agent does not own candidate truth, requirement truth, run truth, or checkpoint truth.

### State Machine Overview

```text
requirement intake
  new
   |
   v
  extracting_requirements
   |
   v
  draft_ready
   |
   v
  confirmed
   |
   v
workflow run
  queued -> running -> paused -> running -> completed
                     \          \
                      \          -> cancelled
                       -> cancelled
```

Failures enter named failed states. The user must see a short localized message and the system must store a stable reason code.

### Tool Flow

```text
Agent decides action
  -> calls runtime-control tool
  -> runtime-control validates state and idempotency
  -> runtime-control writes command/event/snapshot rows
  -> runtime-control invokes child executor or stores pending command
  -> agent lists events/snapshot
  -> transcript renders state
```

### Workbench Relationship

Workbench remains the current user-facing source of session, source-run, requirement-review, event, graph, and final-candidate state. Runtime control must integrate with Workbench instead of creating a second disconnected backend execution flow.

Runtime control can add its own tables, but it must map each runtime-control run to a Workbench session when one exists.
