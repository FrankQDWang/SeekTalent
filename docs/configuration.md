# Configuration

`SeekTalent` reads runtime settings from environment variables.

- `SEEKTALENT_*` variables are loaded by `pydantic-settings`.
- Provider-native variables such as `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `ANTHROPIC_API_KEY`, and `GOOGLE_API_KEY` remain optional compatibility inputs when explicitly configured.
- The canonical text-LLM runtime surface is the `SEEKTALENT_TEXT_LLM_*` tuple, Domi `SEEKTALENT_DOMI_*` auth/transport when the provider label is `domi`, and bare `*_MODEL_ID` variables. The provider-native variables do not replace that surface.
- `seektalent init` writes the starter env template from `.env.example` in a source checkout, or the packaged template from `src/seektalent/default.env` in an installed package.

In this repository, `.env.example` and `src/seektalent/default.env` are intentionally minimal user templates. Product defaults live in `AppSettings`.

## Minimal Setup

For the default non-Domi Liepin/OpenCLI run, you need one direct provider key:

```dotenv
SEEKTALENT_TEXT_LLM_API_KEY=your-text-llm-key
```

The code defaults the text runtime to Bailian's OpenAI-compatible chat-completions endpoint in Beijing:

```dotenv
SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY=openai_chat_completions_compatible
SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=bailian
SEEKTALENT_TEXT_LLM_ENDPOINT_KIND=bailian_openai_chat_completions
SEEKTALENT_TEXT_LLM_ENDPOINT_REGION=beijing
```

Leave `SEEKTALENT_TEXT_LLM_BASE_URL_OVERRIDE` empty unless you need to override the built-in endpoint mapping.

For non-Domi installed PyPI users and source checkout users, `seektalent init` writes a minimal `.env` with one required value:

```env
SEEKTALENT_TEXT_LLM_API_KEY=
```

All other runtime, output, cleanup, source, OpenCLI, Liepin, and model settings use product defaults.

For installed PyPI users, `seektalent workbench` uses Domi Node to prepare the pinned OpenCLI CLI package under `~/.seektalent/opencli-runtime` when needed. It does not download a replacement Node runtime. The OpenCLI Chrome extension and Liepin login are still user-owned browser state; runtime source actions report stable `reason_code=...` diagnostics if either is unavailable.

## Prepared-Machine Domi Workbench

On a machine where Domi is already installed, Chrome is already logged in to Liepin, and the OpenCLI Chrome extension is installed and enabled, the prepared-machine contract is two commands after `SEEKTALENT_DOMI_JWT` is present in the current terminal. The install command loads the release script remotely, so the target machine does not need a SeekTalent source checkout.

Windows PowerShell:

```powershell
Invoke-Expression (Invoke-RestMethod "https://raw.githubusercontent.com/FrankQDWang/SeekTalent/v0.7.25/scripts/install-seektalent-domi.ps1"); Install-SeekTalentDomi -Version 0.7.25
seektalent workbench
```

The Windows defaults are:

```text
%APPDATA%\Domi\runtime\python\bin\python.exe
%APPDATA%\Domi\runtime\node\node.exe
```

macOS shell:

```bash
source <(curl -fsSL "https://raw.githubusercontent.com/FrankQDWang/SeekTalent/v0.7.25/scripts/install-seektalent-domi.sh") 0.7.25
seektalent workbench
```

The macOS Domi Python default is:

```text
/Applications/Domi.app/Contents/Resources/extraResources/python/runtime/bin/python
```

If Domi Node is not present in one of SeekTalent's known Domi Node candidate paths on macOS, set `DOMI_NODE` or `SEEKTALENT_DOMI_NODE` to the Domi node executable before running the install script. The install script writes only under `~/.seektalent`: it installs the PyPI package into `~/.seektalent/python-prefix/<version>`, generates the `seektalent` command shim under `~/.seektalent/bin`, wires that shim to Domi Python plus Domi Node, refreshes the root-level `~/.seektalent/seektalent.*` Windows compatibility shims so existing WindowsApps launchers cannot point at stale prefixes, and updates `PATH` only for the current terminal session. It does not modify the Domi app/runtime, Chrome, or the OpenCLI Chrome extension.

The generated shim sets `SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi` and `SEEKTALENT_OPENCLI_NODE=<resolved Domi Node path>` through the package's Domi launcher before delegating to the Workbench. It fails before server launch if the Domi JWT or Domi Node path is missing.

## Source Checkout Starter Env Snapshot

The checked-in source checkout starter env currently contains only:

```dotenv
SEEKTALENT_TEXT_LLM_API_KEY=
```

Advanced settings are documented below and should be set only when the default product behavior is not enough.

## Provider Boundary Variables

These variables exist at the process boundary. For non-Domi Workbench and direct Bailian-compatible runs, `SEEKTALENT_TEXT_LLM_API_KEY` is the required direct provider key. For prepared-machine Domi Workbench, `SEEKTALENT_DOMI_JWT` is the required authorization value and must be provided explicitly.

| Variable | Required | Notes |
| --- | --- | --- |
| `SEEKTALENT_TEXT_LLM_PROVIDER_LABEL` | Optional | Selects the text LLM provider label. Supported labels include `bailian` and `domi`; default is `bailian`. |
| `SEEKTALENT_TEXT_LLM_API_KEY` | Required for non-Domi/direct providers | Direct provider credential for Bailian-compatible configuration. Not required when `SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi`. |
| `SEEKTALENT_DOMI_JWT` | Required when `SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi` | Manually supplied Domi authorization token. SeekTalent does not discover it from Domi Electron storage. |
| `SEEKTALENT_DOMI_LLM_BASE_URL` | Optional for Domi | Domi LLM proxy base URL. |
| `SEEKTALENT_DOMI_LLM_CHANNEL` | Optional for Domi | Domi LLM proxy channel. |
| `OPENAI_API_KEY` | Optional | Convenience mirror for tools or integrations that expect the provider-native OpenAI env var. |
| `OPENAI_BASE_URL` | Optional | Convenience mirror for tools that expect the provider-native OpenAI base URL. |
| `ANTHROPIC_API_KEY` | Optional | Convenience mirror for tools or integrations that expect the Anthropic-native env var. |
| `GOOGLE_API_KEY` | Optional | Convenience mirror for tools or integrations that expect the Google-native env var. |

## Optional CTS Variables

CTS is not part of the default run path. These variables are required only when `SEEKTALENT_PROVIDER_NAME=cts` is set explicitly.

| Variable | Required | Starter value | Notes |
| --- | --- | --- | --- |
| `SEEKTALENT_CTS_BASE_URL` | No | `https://link.hewa.cn` | Base URL for CTS. |
| `SEEKTALENT_CTS_TENANT_KEY` | Required only when `SEEKTALENT_PROVIDER_NAME=cts` | empty | Sent as the `tenant_key` header. |
| `SEEKTALENT_CTS_TENANT_SECRET` | Required only when `SEEKTALENT_PROVIDER_NAME=cts` | empty | Sent as the `tenant_secret` header. |
| `SEEKTALENT_CTS_TIMEOUT_SECONDS` | No | `20` | HTTP timeout for CTS requests. |
| `SEEKTALENT_CTS_SPEC_PATH` | No | `cts.validated.yaml` | Default resolves to the packaged CTS spec. Custom values resolve relative to the current working directory unless absolute. |

## Canonical Text LLM Surface

The active text runtime is configured by one protocol tuple plus per-stage model ids. The current codebase supports these protocol families:

- `openai_chat_completions_compatible`
- `anthropic_messages_compatible`

Supported provider labels include:

- `bailian`
- `domi`

`domi` uses the OpenAI-compatible protocol with a Domi JWT and Domi proxy transport. It does not read Domi Electron storage or automatically discover JWTs.

| Variable | Starter value | Notes |
| --- | --- | --- |
| `SEEKTALENT_TEXT_LLM_PROTOCOL_FAMILY` | `openai_chat_completions_compatible` | Selects the wire protocol. |
| `SEEKTALENT_TEXT_LLM_PROVIDER_LABEL` | `bailian` | Provider label for the current compatibility matrix. Supported labels include `bailian` and `domi`. |
| `SEEKTALENT_TEXT_LLM_ENDPOINT_KIND` | `bailian_openai_chat_completions` | Must match the selected protocol family. For `domi`, use the OpenAI-compatible protocol. |
| `SEEKTALENT_TEXT_LLM_ENDPOINT_REGION` | `beijing` | Current active regions are `beijing` and `singapore`. |
| `SEEKTALENT_TEXT_LLM_BASE_URL_OVERRIDE` | empty | Optional full base URL override. Leave empty to use the built-in mapping. |
| `SEEKTALENT_TEXT_LLM_API_KEY` | empty | Required for non-Domi direct provider configuration. |
| `SEEKTALENT_DOMI_JWT` | empty | Required when `SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi`; provide it manually. |
| `SEEKTALENT_DOMI_LLM_BASE_URL` | `https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1` | Domi LLM proxy base URL. |
| `SEEKTALENT_DOMI_LLM_CHANNEL` | `seek_talent` | Domi LLM proxy channel. |

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
| `SEEKTALENT_SEARCH_MAX_PAGES_PER_ROUND` | `3` | Per-round source page budget. |
| `SEEKTALENT_SEARCH_MAX_ATTEMPTS_PER_ROUND` | `3` | Per-round source attempt budget. |
| `SEEKTALENT_SEARCH_NO_PROGRESS_LIMIT` | `2` | Repeated no-progress threshold. |
| `SEEKTALENT_RUNTIME_MODE` | `dev` | Resolves default SQLite, artifact, cache, backup, and maintenance behavior for source checkouts versus packaged runs. |
| `SEEKTALENT_LLM_CACHE_DIR` | `.seektalent/cache` | Local cache root. Relative paths resolve from the workspace root. |
| `SEEKTALENT_ENABLE_REFLECTION` | `true` | Enables reflection after each completed round. |
| `SEEKTALENT_ARTIFACTS_DIR` | `artifacts` | Diagnostic/export artifact root. Relative paths resolve from the workspace root. |
| `SEEKTALENT_RUNS_DIR` | `runs` | Legacy CLI compatibility root. The active product output root is `SEEKTALENT_ARTIFACTS_DIR`; the legacy root is rejected for active runtime output. |

## Local Product Data Roots

The local-first CLI and local workbench keep business data on the user's machine by default. `seektalent doctor` and `seektalent inspect --json` report posture metadata for these roots; they do not print provider tokens, cookies, raw session values, or candidate material.

| Setting | Purpose |
| --- | --- |
| `SEEKTALENT_WORKSPACE_ROOT` | Base for local workbench state when provided. |
| `SEEKTALENT_ARTIFACTS_DIR` | Artifact root. Relative paths resolve from the workspace root. |
| `SEEKTALENT_RUNS_DIR` | Legacy run output root for CLI compatibility. |
| `SEEKTALENT_LLM_CACHE_DIR` | Local cache root. Relative paths resolve from the workspace root. |

Repository-local and known sync-folder roots are acceptable only as source-checkout development warnings. Packaged or production users should use a non-repository local data root such as the user's `.seektalent` directory.

## Runtime Control Plane

The local Workbench is DB-first. The conversation agent is the user-facing thread/turn layer, the workflow runtime is the execution engine, and `runtime_control.sqlite3` is the canonical workflow store for run identity, start idempotency, commands, public progress events, checkpoints, stage outputs, candidate truth, finalization revisions, executor leases, and projection marks.

Workbench tables are recruiter-facing projections. They can be rebuilt or repaired from runtime-control source identities, and normal production paths do not read `runtime/public_events.jsonl` or artifact manifests to discover progress or completion. Old artifact imports are explicit debug/repair operations only.

Runtime-control stores compact product state and indexes. Raw prompts, raw provider payloads, raw resume text, full traces, and large debug snapshots belong only in bounded developer diagnostics or `debug_full_local` artifacts. `prod` skips corpus raw-provider-payload capture; `dev` and `debug_full_local` may write those payloads under corpus diagnostics with retention cleanup.

## Artifact Modes

| Mode | Intended user | Behavior |
| --- | --- | --- |
| `prod` | Recruiters | Product state is SQLite-first. Full runtime traces and public-event JSONL mirrors are not required for Workbench progress or completion. |
| `dev` | Developers and advanced users | Emits compact bounded diagnostics under `artifacts/` while keeping runtime-control as the source of truth. |
| `debug_full_local` | Explicit troubleshooting | Emits full local diagnostics with short retention. Do not use it as normal production behavior. |

Removed legacy mode names such as `dev_full_local`, `prod_compact_local`, and `off_except_db` fail fast instead of being silently normalized.

## Maintenance And Operator Health

Whole-product local storage lifecycle covers SQLite DB files, WAL/SHM siblings, artifacts, caches, backups, memory workspace files, provider/session state, and corpus outputs. Cleanup and retention support dry-run reporting; retention is age/size based and is separate from privacy erasure.

Privacy erasure is subject-scoped. Candidate erasure de-identifies runtime-control candidate truth and Workbench product rows while preserving lineage rows; backup beyond-use handling and support-bundle safeguards are operator concerns, not ordinary retention cleanup.

DB-group backup uses SQLite online backup copies plus a group manifest for the product stores: Workbench, runtime-control, conversation, Workbench stream, agent memory, Liepin connector, and corpus. Operator health reports disk preflight, schema version posture, integrity status, DB/WAL/SHM sizes, missing stores, and backup/projection/cleanup state.

## Privacy Defaults

`doctor`, `inspect --json`, cleanup, and Workbench startup do not upload local databases, provider cookies, browser sessions, raw resumes, or configured secrets. Runtime network calls are limited to the configured LLM provider and the local browser's Liepin session unless an optional provider is explicitly configured. Remote eval logging through W&B/Weave is off by default and requires explicit configuration.

## Liepin Local Browser Retrieval

Local Liepin retrieval uses deterministic OpenCLI browser actions by default in the packaged Workbench configuration.

| Setting | Meaning |
| --- | --- |
| `SEEKTALENT_LIEPIN_WORKER_MODE=opencli` | Use the deterministic OpenCLI Liepin retriever. |
| `SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND=opencli` | Enable the local browser action backend used by the OpenCLI retriever. |
| `SEEKTALENT_LIEPIN_OPENCLI_COMMAND=apps/web-react/node_modules/.bin/opencli` | OpenCLI command resolved from the code root when relative. |
| `SEEKTALENT_LIEPIN_OPENCLI_SESSION=seektalent-liepin` | Local OpenCLI browser session name. |
| `SEEKTALENT_LIEPIN_DEFAULT_DAILY_DETAIL_BUDGET=20` | Daily detail-open budget. This is a safety cap, not a per-query target. |
| `SEEKTALENT_LIEPIN_EXPLOIT_DETAIL_TARGET=2` | Maximum detail-backed resumes opened per exploit query. |
| `SEEKTALENT_LIEPIN_EXPLORE_DETAIL_TARGET=1` | Maximum detail-backed resumes opened per explore query. |
| `SEEKTALENT_LIEPIN_OPENCLI_MAX_CARDS_PER_TASK=10` | Maximum search cards scanned by one OpenCLI task before detail-open caps are applied. |

`opencli` is the local Liepin execution path. `external_http` remains available only for an explicitly supplied worker-compatible HTTP endpoint.

Local drift smoke should be operator-triggered and low volume. Search/card probes are the default bounded checks. Filter probes and detail probes must remain opt-in because they interact with provider UI state, and detail probes open candidate detail pages and consume risk budget.

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
- Non-Domi direct providers require `SEEKTALENT_TEXT_LLM_API_KEY`; prepared-machine Domi requires a manually provided `SEEKTALENT_DOMI_JWT`.
- CTS tenant credentials are required only when `SEEKTALENT_PROVIDER_NAME=cts` is set explicitly.
- Removed legacy text-LLM keys and provider-prefixed `*_MODEL_ID` values now fail fast with a migration error.

Use `seektalent doctor` to validate local configuration without making network calls.

## Related Docs

- [CLI](cli.md)
- [UI](ui.md)
- [Outputs](outputs.md)
