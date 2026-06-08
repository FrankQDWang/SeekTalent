# Codex Harness And Memory

## Goal

Use Codex's existing local harness and memory behavior without affecting the user's own Codex installation or global Codex memories.

## Primary Integration Choice

The required integration is Codex App Server controlled from the local SeekTalent backend. Do not use the Codex SDK for this feature.

The SeekTalent implementation should not reimplement:

- long-running agent conversation orchestration;
- Codex thread semantics;
- Codex memory generation;
- model provider protocol handling inside Codex.

SeekTalent should implement only the narrow adapter needed to:

- launch Codex in a project-isolated environment;
- send intake-specific prompts;
- parse structured intake output;
- map Codex thread ids to local intake conversations.

## App Server Only Rule

The Codex SDK is out of scope for this feature.

The implementation must use Codex App Server directly. If Codex App Server is unavailable or cannot be launched with project-local `CODEX_HOME`, the implementation must stop with `codex_harness_unavailable` instead of falling back to SDK usage or building a custom harness.

## Memory Isolation

Codex memory must be enabled only inside the project-local Codex home:

```text
<repo>/.seektalent/codex_home/memories/
```

The implementation must enforce these checks:

- resolved `CODEX_HOME` is not the user's home directory;
- resolved `CODEX_HOME` is not `~/.codex`;
- resolved memory directory is inside the project-local Codex home;
- no code path shells out to Codex without explicitly setting `CODEX_HOME`;
- tests fail if a fake Codex process receives missing or unsafe `CODEX_HOME`.

## Config Generation

Create or validate:

```text
.seektalent/codex_home/config.toml
```

Minimum expected shape:

```toml
model = "deepseek-v4-flash"
model_provider = "dashscope"

[features]
memories = true

[model_providers.dashscope]
name = "DashScope OpenAI-compatible"
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
env_key = "SEEKTALENT_TEXT_LLM_API_KEY"
```

The implementation must not write `requires_openai_auth = true`.

## Working Directory Isolation

Codex app-server cwd must be:

```text
.seektalent/codex_workspace/
```

The workspace should contain a small README that tells Codex it is an intake-only workspace and that repository source files are not available for mutation during user intake conversations.

## Memory Semantics

Memory can store durable user preferences such as:

- preferred confirmation language;
- common source kind preference;
- recurring seniority preference;
- recruiter style preferences;
- known exclusion patterns the user repeatedly states.

Memory must not store canonical run data such as:

- final `jobTitle`;
- final `jdText`;
- final `notes`;
- Workbench session id;
- requirement sheet;
- runtime result;
- candidate evidence;
- credentials or provider account data.

## Reset Behavior

The UI and API must support a local reset of this project's Codex memory. Reset must delete or archive only:

```text
.seektalent/codex_home/memories/
```

It must never touch:

```text
~/.codex/
~/.codex/memories/
```

## Packaging Boundary

SeekTalent may call Codex App Server locally from a commercial product, but packaging must not include:

- `~/.codex`;
- project-local Codex memories;
- Codex auth state;
- user provider credentials;
- Codex thread history containing user intake text.

If packaging includes Codex CLI, App Server, binary, source, or other Codex artifacts, the package must retain the applicable Apache-2.0 license notice for those artifacts. This requirement is separate from SeekTalent's own license.

## Main Agent Boundary

The intake conversation is the main agent experience. SeekTalent runtime is a child workflow owned by the existing Workbench/runtime path.

Initial version capabilities:

- parse user intake;
- ask clarifying questions;
- confirm requirements;
- start the Workbench workflow after confirmation;
- read Workbench workflow progress;
- read workflow results;
- summarize progress/results in the transcript.

Initial version non-capabilities:

- no runtime-in-progress mutation;
- no workflow pausing/resuming;
- no requirement auto-approval;
- no candidate injection;
- no source reconfiguration after start;
- no hidden runtime tools exposed to Codex.

## Prompt Boundary

Codex must receive a narrow intake prompt. It should not be asked to operate on repository source code during ordinary user intake.

The structured output request should ask for:

- assistant reply markdown;
- conversation state;
- missing questions;
- extracted draft;
- confidence;
- safety or validation reason code.

For progress questions after workflow start, Codex may receive sanitized, read-only Workbench state summaries. It must not receive runtime internals, provider credentials, raw provider payloads, cookies, or local filesystem paths.

## Required Smoke

Before full implementation proceeds, the long-running task must prove a real or mocked Codex harness path:

```text
codex cli present: yes
app-server available: yes
CODEX_HOME: <repo>/.seektalent/codex_home
memory dir: <repo>/.seektalent/codex_home/memories
provider: dashscope
openai auth required: no
simple intake turn: passed
```

If the real provider smoke cannot be run because credentials are absent, unit tests with a fake Codex adapter may continue, but the final report must mark the real provider smoke as not executed and the UI must display `provider_not_configured` instead of pretending readiness.
