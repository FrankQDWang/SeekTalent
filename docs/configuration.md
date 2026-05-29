# Configuration

`SeekTalent` reads runtime settings from environment variables.

- `SEEKTALENT_*` variables are loaded by `pydantic-settings`.
- Provider-native variables such as `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `ANTHROPIC_API_KEY`, and `GOOGLE_API_KEY` are also imported from `.env` into the process environment at startup.
- The canonical text-LLM runtime surface is the `SEEKTALENT_TEXT_LLM_*` tuple plus bare `*_MODEL_ID` variables. The provider-native variables do not replace that surface.
- `seektalent init` writes the starter env template from `.env.example` in a source checkout, or the minimal packaged template from `src/seektalent/default.env` in an installed package.

In this repository, `.env.example` is the editable source-checkout starter env. `src/seektalent/default.env` is the packaged user template.

The checked-in starter env is the operator-facing default. Some code-level fallbacks in `AppSettings` still differ when no env template is present. For example, the starter env pins `SEEKTALENT_REASONING_EFFORT=off` and `SEEKTALENT_SCORING_MAX_CONCURRENCY=5`, while code-level fallbacks remain valid without the template.

## Minimal Setup

For a real CTS run, you need:

```dotenv
SEEKTALENT_TEXT_LLM_API_KEY=your-text-llm-key
SEEKTALENT_CTS_TENANT_KEY=your-cts-tenant-key
SEEKTALENT_CTS_TENANT_SECRET=your-cts-tenant-secret
```

The current starter env defaults the text runtime to Bailian's OpenAI-compatible chat-completions endpoint in Beijing:

```dotenv
SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY=openai_chat_completions_compatible
SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=bailian
SEEKTALENT_TEXT_LLM_ENDPOINT_KIND=bailian_openai_chat_completions
SEEKTALENT_TEXT_LLM_ENDPOINT_REGION=beijing
```

Leave `SEEKTALENT_TEXT_LLM_BASE_URL_OVERRIDE` empty unless you need to override the built-in endpoint mapping.

For installed PyPI users, `seektalent init` writes a minimal `.env` with only three required values:

```env
SEEKTALENT_TEXT_LLM_API_KEY=
SEEKTALENT_CTS_TENANT_KEY=
SEEKTALENT_CTS_TENANT_SECRET=
```

All other runtime, output, cleanup, and model settings use product defaults. Source checkout developers should use `.env.example` for the full development configuration surface.

## Source Checkout Starter Env Snapshot

The source checkout starter env currently uses these main values:

```dotenv
SEEKTALENT_TEXT_LLM_API_KEY=
OPENAI_API_KEY=
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=

SEEKTALENT_CTS_BASE_URL=https://link.hewa.cn
SEEKTALENT_CTS_TENANT_KEY=
SEEKTALENT_CTS_TENANT_SECRET=
SEEKTALENT_CTS_TIMEOUT_SECONDS=20
SEEKTALENT_CTS_SPEC_PATH=cts.validated.yaml

SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY=openai_chat_completions_compatible
SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=bailian
SEEKTALENT_TEXT_LLM_ENDPOINT_KIND=bailian_openai_chat_completions
SEEKTALENT_TEXT_LLM_ENDPOINT_REGION=beijing
SEEKTALENT_TEXT_LLM_BASE_URL_OVERRIDE=
SEEKTALENT_REQUIREMENTS_MODEL_ID=deepseek-v4-pro
SEEKTALENT_CONTROLLER_MODEL_ID=deepseek-v4-pro
SEEKTALENT_SCORING_MODEL_ID=deepseek-v4-flash
SEEKTALENT_FINALIZE_MODEL_ID=deepseek-v4-flash
SEEKTALENT_REFLECTION_MODEL_ID=deepseek-v4-pro
SEEKTALENT_REQUIREMENTS_ENABLE_THINKING=true
SEEKTALENT_STRUCTURED_REPAIR_MODEL_ID=deepseek-v4-flash
SEEKTALENT_STRUCTURED_REPAIR_REASONING_EFFORT=off
SEEKTALENT_JUDGE_MODEL_ID=deepseek-v4-pro
SEEKTALENT_TUI_SUMMARY_MODEL_ID=
SEEKTALENT_REASONING_EFFORT=off
SEEKTALENT_JUDGE_REASONING_EFFORT=high
SEEKTALENT_CONTROLLER_ENABLE_THINKING=true
SEEKTALENT_REFLECTION_ENABLE_THINKING=true
SEEKTALENT_CANDIDATE_FEEDBACK_ENABLED=true
SEEKTALENT_CANDIDATE_FEEDBACK_MODEL_ID=deepseek-v4-flash
SEEKTALENT_CANDIDATE_FEEDBACK_REASONING_EFFORT=off
SEEKTALENT_PRF_PROBE_PROPOSAL_BACKEND=llm_deepseek_v4_flash
SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MODEL_ID=deepseek-v4-flash
SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_REASONING_EFFORT=off
SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_TIMEOUT_SECONDS=30
SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MAX_OUTPUT_TOKENS=2048

SEEKTALENT_MIN_ROUNDS=3
SEEKTALENT_MAX_ROUNDS=10
SEEKTALENT_SCORING_MAX_CONCURRENCY=5
SEEKTALENT_JUDGE_MAX_CONCURRENCY=5
SEEKTALENT_SEARCH_MAX_PAGES_PER_ROUND=3
SEEKTALENT_SEARCH_MAX_ATTEMPTS_PER_ROUND=3
SEEKTALENT_SEARCH_NO_PROGRESS_LIMIT=2
SEEKTALENT_RUNTIME_MODE=dev
SEEKTALENT_LLM_CACHE_DIR=.seektalent/cache
SEEKTALENT_OPENAI_PROMPT_CACHE_ENABLED=false
SEEKTALENT_OPENAI_PROMPT_CACHE_RETENTION=
SEEKTALENT_MOCK_CTS=false
SEEKTALENT_ENABLE_EVAL=false
SEEKTALENT_ENABLE_REFLECTION=true
SEEKTALENT_WANDB_PROJECT=seektalent
SEEKTALENT_WEAVE_PROJECT=seektalent
SEEKTALENT_RUNS_DIR=runs
```

See `.env.example` for the full template, including PRF and local sidecar settings.

## Provider Boundary Variables

These variables exist at the process boundary. Only `SEEKTALENT_TEXT_LLM_API_KEY` is part of the canonical text-LLM runtime surface.

| Variable | Required | Notes |
| --- | --- | --- |
| `SEEKTALENT_TEXT_LLM_API_KEY` | Required for active text-LLM calls | Canonical runtime credential for both supported text protocols. |
| `OPENAI_API_KEY` | Optional | Convenience mirror for tools or integrations that expect the provider-native OpenAI env var. |
| `OPENAI_BASE_URL` | Optional | Convenience mirror for tools that expect the provider-native OpenAI base URL. The starter template mirrors the default Bailian OpenAI-compatible endpoint here. |
| `ANTHROPIC_API_KEY` | Optional | Convenience mirror for tools or integrations that expect the Anthropic-native env var. |
| `GOOGLE_API_KEY` | Optional | Convenience mirror for tools or integrations that expect the Google-native env var. |

## CTS Variables

| Variable | Required | Starter value | Notes |
| --- | --- | --- | --- |
| `SEEKTALENT_CTS_BASE_URL` | No | `https://link.hewa.cn` | Base URL for CTS. |
| `SEEKTALENT_CTS_TENANT_KEY` | Required in real CTS mode | empty | Sent as the `tenant_key` header. |
| `SEEKTALENT_CTS_TENANT_SECRET` | Required in real CTS mode | empty | Sent as the `tenant_secret` header. |
| `SEEKTALENT_CTS_TIMEOUT_SECONDS` | No | `20` | HTTP timeout for CTS requests. |
| `SEEKTALENT_CTS_SPEC_PATH` | No | `cts.validated.yaml` | Default resolves to the packaged CTS spec. Custom values resolve relative to the current working directory unless absolute. |

## Canonical Text LLM Surface

The active text runtime is configured by one protocol tuple plus per-stage model ids. The current codebase supports two protocol families and one provider label:

- `openai_chat_completions_compatible`
- `anthropic_messages_compatible`
- `bailian`

| Variable | Starter value | Notes |
| --- | --- | --- |
| `SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY` | `openai_chat_completions_compatible` | Selects the wire protocol. |
| `SEEKTALENT_TEXT_LLM_PROVIDER_LABEL` | `bailian` | Provider label for the current compatibility matrix. |
| `SEEKTALENT_TEXT_LLM_ENDPOINT_KIND` | `bailian_openai_chat_completions` | Must match the selected protocol family. |
| `SEEKTALENT_TEXT_LLM_ENDPOINT_REGION` | `beijing` | Current active regions are `beijing` and `singapore`. |
| `SEEKTALENT_TEXT_LLM_BASE_URL_OVERRIDE` | empty | Optional full base URL override. Leave empty to use the built-in mapping. |
| `SEEKTALENT_TEXT_LLM_API_KEY` | empty | Required for canonical text-LLM configuration. |

Built-in endpoint mapping:

- `openai_chat_completions_compatible` + `bailian_openai_chat_completions` + `beijing` -> `https://dashscope.aliyuncs.com/compatible-mode/v1`
- `anthropic_messages_compatible` + `bailian_anthropic_messages` + `beijing` -> `https://dashscope.aliyuncs.com/apps/anthropic`
- `anthropic_messages_compatible` + `bailian_anthropic_messages` + `singapore` -> `https://dashscope-intl.aliyuncs.com/apps/anthropic`

Legacy `provider:model` strings are decommissioned. Do not set `SEEKTALENT_REQUIREMENTS_MODEL`, `SEEKTALENT_JUDGE_MODEL`, or any other legacy `*_MODEL` key, and do not place prefixes such as `openai-chat:` or `anthropic:` on `*_MODEL_ID` values.

## Model ID Variables

All stage model settings now use bare model ids.

| Variable | Starter value | Notes |
| --- | --- | --- |
| `SEEKTALENT_REQUIREMENTS_MODEL_ID` | `deepseek-v4-pro` | Requirement extraction. |
| `SEEKTALENT_CONTROLLER_MODEL_ID` | `deepseek-v4-pro` | Round controller. |
| `SEEKTALENT_SCORING_MODEL_ID` | `deepseek-v4-flash` | Per-resume scoring. |
| `SEEKTALENT_FINALIZE_MODEL_ID` | `deepseek-v4-flash` | Final shortlist presentation. |
| `SEEKTALENT_REFLECTION_MODEL_ID` | `deepseek-v4-pro` | Round reflection. |
| `SEEKTALENT_STRUCTURED_REPAIR_MODEL_ID` | `deepseek-v4-flash` | Structured-output repair lane. |
| `SEEKTALENT_JUDGE_MODEL_ID` | `deepseek-v4-pro` | Eval judge. |
| `SEEKTALENT_TUI_SUMMARY_MODEL_ID` | empty | Optional short progress summary model. Falls back to the scoring model when unset. |
| `SEEKTALENT_CANDIDATE_FEEDBACK_MODEL_ID` | `deepseek-v4-flash` | Reserved for dormant model-ranked candidate feedback steps; the active rescue lane remains deterministic. |
| `SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MODEL_ID` | `deepseek-v4-flash` | LLM PRF phrase proposal extractor used by the `llm_deepseek_v4_flash` PRF probe backend. |

## Thinking, Reasoning, And Prompt Behavior

Reasoning effort values are `off`, `low`, `medium`, and `high`. Stage-specific support is validated against the selected protocol and model capability matrix.

| Variable | Starter value | Notes |
| --- | --- | --- |
| `SEEKTALENT_REQUIREMENTS_ENABLE_THINKING` | `true` | Enables provider-side thinking for the requirements stage. |
| `SEEKTALENT_CONTROLLER_ENABLE_THINKING` | `true` | Enables provider-side thinking for the controller stage. |
| `SEEKTALENT_REFLECTION_ENABLE_THINKING` | `true` | Enables provider-side thinking for the reflection stage. |
| `SEEKTALENT_REASONING_EFFORT` | `off` | Shared default reasoning effort. The starter env keeps it off. |
| `SEEKTALENT_STRUCTURED_REPAIR_REASONING_EFFORT` | `off` | Structured-repair reasoning effort. |
| `SEEKTALENT_JUDGE_REASONING_EFFORT` | `high` | Judge reasoning effort. Falls back to `SEEKTALENT_REASONING_EFFORT` when unset. |
| `SEEKTALENT_CANDIDATE_FEEDBACK_REASONING_EFFORT` | `off` | Candidate-feedback reasoning effort for the dormant model-ranked lane. |
| `SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_REASONING_EFFORT` | `off` | Reasoning effort for the LLM PRF phrase proposal extractor. |
| `SEEKTALENT_OPENAI_PROMPT_CACHE_ENABLED` | `false` | Enables prompt caching for OpenAI-compatible requests that support it. |
| `SEEKTALENT_OPENAI_PROMPT_CACHE_RETENTION` | empty | Optional prompt-cache retention policy. |

## PRF Probe Variables

These settings control the mainline PRF probe proposal backend. The default backend calls the LLM phrase proposal extractor in round 2+ when enough feedback seed support exists, then applies deterministic grounding and PRF policy gates before a PRF probe query can run.

| Variable | Starter value | Notes |
| --- | --- | --- |
| `SEEKTALENT_PRF_PROBE_PROPOSAL_BACKEND` | `llm_deepseek_v4_flash` | Mainline PRF probe proposal backend. Other supported values keep legacy or sidecar span proposal paths. |
| `SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MODEL_ID` | `deepseek-v4-flash` | Model id for the LLM PRF phrase proposal stage. |
| `SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_REASONING_EFFORT` | `off` | Reasoning effort for the LLM PRF phrase proposal stage. |
| `SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_TIMEOUT_SECONDS` | `30` | Per-call timeout for phrase proposal extraction. |
| `SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MAX_OUTPUT_TOKENS` | `2048` | Maximum output tokens for phrase proposal extraction. |

Before using `llm_deepseek_v4_flash` as production-ready benchmark behavior, run the live LLM PRF bakeoff manually and require `blocker_count == 0`:

```bash
uv run python -m seektalent.candidate_feedback.llm_prf_bakeoff \
  --live \
  --cases tests/fixtures/llm_prf_bakeoff/cases.jsonl \
  --output-dir artifacts/manual/llm-prf-bakeoff
```

The checked-in three-case fixture is only a harness smoke test. Production promotion requires an external private sanitized slice, preferably at least 30 cases across English, Chinese, and mixed-language roles. Inspect `generic_fallback_rate`, `structured_output_failure_rate`, and p50/p95 latency in addition to `blocker_count`.

## Runtime Variables

| Variable | Starter value | Notes |
| --- | --- | --- |
| `SEEKTALENT_MIN_ROUNDS` | `3` | Minimum completed retrieval rounds before stopping is allowed. |
| `SEEKTALENT_MAX_ROUNDS` | `10` | Hard cap for controller/search rounds. Must be `>= min_rounds` and `<= 10`. |
| `SEEKTALENT_SCORING_MAX_CONCURRENCY` | `5` | Max concurrent per-resume scoring calls. |
| `SEEKTALENT_JUDGE_MAX_CONCURRENCY` | `5` | Max concurrent judge calls. |
| `SEEKTALENT_SEARCH_MAX_PAGES_PER_ROUND` | `3` | Per-round CTS page budget. |
| `SEEKTALENT_SEARCH_MAX_ATTEMPTS_PER_ROUND` | `3` | Per-round CTS attempt budget. |
| `SEEKTALENT_SEARCH_NO_PROGRESS_LIMIT` | `2` | Repeated no-progress threshold. |
| `SEEKTALENT_RUNTIME_MODE` | `dev` | Resolves default output/cache roots for source checkouts versus packaged runs. |
| `SEEKTALENT_LLM_CACHE_DIR` | `.seektalent/cache` | Local cache root. Relative paths resolve from the workspace root. |
| `SEEKTALENT_ENABLE_REFLECTION` | `true` | Enables reflection after each completed round. |
| `SEEKTALENT_RUNS_DIR` | `runs` | Output root. Relative paths resolve from the workspace root. |

## Local Product Data Roots

The local-first CLI and local workbench keep business data on the user's machine by default. `seektalent doctor` and `seektalent inspect --json` report posture metadata for these roots; they do not print provider tokens, cookies, raw session values, or candidate material.

| Setting | Purpose |
| --- | --- |
| `SEEKTALENT_WORKSPACE_ROOT` | Base for local workbench state when provided. |
| `SEEKTALENT_ARTIFACTS_DIR` | Artifact root. Relative paths resolve from the workspace root. |
| `SEEKTALENT_RUNS_DIR` | Legacy run output root for CLI compatibility. |
| `SEEKTALENT_LLM_CACHE_DIR` | Local cache root. Relative paths resolve from the workspace root. |

Repository-local and known sync-folder roots are acceptable only as source-checkout development warnings. Packaged or production users should use a non-repository local data root such as the user's `.seektalent` directory.

## Privacy Defaults

`doctor`, `inspect --json`, cleanup, and Workbench startup do not upload local databases, provider cookies, browser sessions, raw resumes, or configured secrets. Runtime network calls are limited to the configured LLM provider and CTS provider. Remote eval logging through W&B/Weave is off by default and requires explicit configuration.

## Dev Mode BYOK Readiness

For the first local dual-source Svelte milestone, BYOK readiness is diagnostic. The Workbench may report whether text LLM, CTS, Liepin/Pi settings, and local data-root posture are configured or risky, but it must not display API keys, tokens, cookies, command secrets, protected artifact paths, raw provider payloads, or sensitive local filesystem paths.

Required live variables:

| Variable | Notes |
| --- | --- |
| `SEEKTALENT_TEXT_LLM_API_KEY` | User-provided text LLM key. |
| `SEEKTALENT_CTS_TENANT_KEY` | User-provided CTS tenant key. |
| `SEEKTALENT_CTS_TENANT_SECRET` | User-provided CTS tenant secret. |
| `SEEKTALENT_LIEPIN_WORKER_MODE=pi_agent` | Enables the Pi-backed Liepin executor. |
| `SEEKTALENT_LIEPIN_PI_COMMAND=pi --mode rpc --no-session` | Pi RPC command; do not put secrets in it. In dev, `scripts/start-dev-workbench.sh` replaces this with the repo-local Pi binary plus the Bailian provider extension and pinned `pi-mcp-adapter` extension. Manual overrides must include `--mode rpc`, `--no-session`, the repo-owned Bailian provider extension, and the pinned MCP adapter extension. |
| `SEEKTALENT_LIEPIN_PI_SKILL_PATH=src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md` | Repo-owned Liepin card-search skill. |
| `SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH=.pi/mcp.json` | Project-local Pi MCP config inspected by SeekTalent static diagnostics. |
| `SEEKTALENT_LIEPIN_PI_MODEL_ID=deepseek-v4-flash` | Optional Pi browser-agent model override. Empty means reuse the Runtime workbench-note/scoring model from the same root `.env`. |
| `SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME=dokobot` | Project-local Pi MCP server name. |
| `SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND=` | Explicit DokoBot MCP server command. Empty means the Liepin browser channel is not configured. |
| `SEEKTALENT_LIEPIN_DOKOBOT_MCP_ARGS_JSON=[]` | Optional JSON array of DokoBot MCP server args. |
| `SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON=[]` | Optional JSON array of direct Pi tool names to expose through `pi-mcp-adapter`. |
| `SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON=[]` | Required for live Liepin Pi runs: exact Pi tool-event names that prove the DokoBot browser actions were observed. Empty means the live browser channel fails closed. |
| `SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND=disabled` | Optional Pi-internal browser action backend. `opencli` enables the OpenCLI path; Runtime and Workbench still do not call browser tools directly. |
| `SEEKTALENT_LIEPIN_OPENCLI_COMMAND=apps/web-svelte/node_modules/.bin/opencli` | Repo-local OpenCLI CLI command. Missing binaries are reported by readiness/live checks, not by settings construction. |
| `SEEKTALENT_LIEPIN_OPENCLI_SESSION=seektalent-liepin` | OpenCLI browser session name used inside the Pi tool wrapper. |
| `SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_HOSTS_JSON=[...]` | JSON allowlist of Liepin hosts the OpenCLI wrapper may touch. |
| `SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_START_URLS_JSON=[...]` | JSON allowlist of source-policy start URLs. Broad identity entrypoints should not be defaults. |
| `SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET=<local non-placeholder secret>` | Local HMAC/account-binding secret. |

In dev mode, the Svelte workspace carries Pi as an npm dependency (`@earendil-works/pi-coding-agent`). The local Workbench product launcher starts the backend and Svelte frontend with that repo-local Pi dependency:

```bash
scripts/start-dev-workbench.sh
```

The launcher exports `SEEKTALENT_LIEPIN_WORKER_MODE=pi_agent`, sets `SEEKTALENT_LIEPIN_PI_COMMAND` to the repo-local Pi binary plus the repo-owned Bailian provider extension and pinned `pi-mcp-adapter` extension, and creates a local account-binding secret under `.seektalent/` when needed. It does not create or edit `.pi/mcp.json`; registering a DokoBot MCP server for Pi is an explicit setup step. Pi receives the same Runtime text LLM key/base URL/model from the root `.env`; secrets are injected only into the backend/Pi process, not into the Svelte frontend. A plain low-level `seektalent-ui-api` process only reads its explicit configuration and does not change `disabled` into `pi_agent`.

Initialize the project-local Pi MCP config from the checkout:

```bash
seektalent pi-agent init --project --write
```

The command requires an explicit `--dokobot-mcp-command` or `SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND`; it does not invent a default DokoBot command. The generated `.pi/mcp.json` registers the configured DokoBot MCP server for Pi. SeekTalent Runtime and Workbench do not call DokoBot directly; they use Pi RPC plus the repo-owned Liepin skill, then validate the strict JSON envelope and observed Pi tool events. `seektalent doctor --json` performs static setup checks only. Use `seektalent doctor --live-pi-agent --json` only when you intentionally want to launch the configured Pi readiness probe. If the Pi adapter metadata or direct tools have just been configured, Pi may need a reconnect/restart before the expected observed tool events appear.

OpenCLI mode is automatic only for source/dev workspaces that have installed `apps/web-svelte` dependencies. Packaged `seektalent workbench` users get the built Svelte frontend in the wheel, but OpenCLI and browser-extension setup remain development-only unless explicitly configured. If `apps/web-svelte/node_modules/.bin/opencli` is absent, the Liepin OpenCLI source must stay blocked with `liepin_opencli_command_missing` while CTS remains available.

## Eval Variables

Eval is off by default. Enable it with `SEEKTALENT_ENABLE_EVAL=true` or the CLI `--enable-eval` flag.

| Variable | Starter value | Notes |
| --- | --- | --- |
| `SEEKTALENT_ENABLE_EVAL` | `false` | Enables judge + evaluation artifacts. |
| `SEEKTALENT_WANDB_ENTITY` | local template value | Optional W&B entity for eval/report logging. |
| `SEEKTALENT_WANDB_PROJECT` | `seektalent` | Optional W&B project. |
| `SEEKTALENT_WEAVE_ENTITY` | local template value | Optional Weave entity. Falls back to W&B entity when unset. |
| `SEEKTALENT_WEAVE_PROJECT` | `seektalent` | Optional Weave project. |

## Rescue Variables

These settings control the active deterministic rescue lane and its dormant model-ranked extension point.
The rescue `candidate_feedback` lane does not call the LLM PRF extractor; it uses deterministic feedback extraction artifacts when low-quality recall needs repair.

| Variable | Starter value | Notes |
| --- | --- | --- |
| `SEEKTALENT_CANDIDATE_FEEDBACK_ENABLED` | `true` | Allows runtime to derive a safe expansion term from strong scored candidates. |
| `SEEKTALENT_CANDIDATE_FEEDBACK_MODEL_ID` | `deepseek-v4-flash` | Reserved for dormant model-ranked candidate feedback steps; the active rescue lane remains deterministic. |
| `SEEKTALENT_CANDIDATE_FEEDBACK_REASONING_EFFORT` | `off` | Reasoning effort for the dormant model-ranked candidate-feedback lane. |

## Development-Only Mock CTS

| Variable | Starter value | Notes |
| --- | --- | --- |
| `SEEKTALENT_MOCK_CTS` | `false` | Enables the local mock CTS corpus in source-checkout development. The published CLI rejects this mode. |

## Validation And Migration Rules

Before each run, the runtime validates the active config surface:

- `SEEKTALENT_TEXT_LLM_ENDPOINT_KIND` must match `SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY`.
- `SEEKTALENT_TEXT_LLM_API_KEY` is required for active text-LLM calls.
- Real CTS mode requires both CTS tenant credentials.
- Removed legacy text-LLM keys and provider-prefixed `*_MODEL_ID` values now fail fast with a migration error.

Use `seektalent doctor` to validate local configuration without making network calls.

## Related Docs

- [CLI](cli.md)
- [UI](ui.md)
- [Outputs](outputs.md)
