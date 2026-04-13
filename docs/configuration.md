# Configuration

`SeekTalent v0.3.3 active` keeps CTS, rerank API, local path settings, and a `.env`-driven LLM callpoint matrix.

## Starter env

Generate the template with:

```bash
seektalent init
```

This writes the bundled starter template that ships with the package.

The repo-root [.env.example](/Users/frankqdwang/Agents/SeekTalent/.env.example) remains the single source of truth for the starter template, and the same content is bundled into the package for `seektalent init`.

## LLM callpoints

The active runtime has five LLM callpoints:

- `requirement_extraction`
- `bootstrap_keyword_generation`
- `search_controller_decision`
- `branch_outcome_evaluation`
- `search_run_finalization`

Each callpoint can be configured independently through `.env`:

- `SEEKTALENT_<CALLPOINT>_PROVIDER`
- `SEEKTALENT_<CALLPOINT>_MODEL`
- `SEEKTALENT_<CALLPOINT>_BASE_URL`
- `SEEKTALENT_<CALLPOINT>_API_KEY`
- `SEEKTALENT_<CALLPOINT>_OUTPUT_MODE`

Supported callpoint providers in v1:

- `openai`
- `dashscope`
- `moonshot`
- `glm`

Supported output modes:

- `auto`
- `native`
- `tool`
- `prompted`

`auto` is resolved once at startup. It is not a runtime fallback chain.

### Provider credentials

`OPENAI_API_KEY` and `OPENAI_BASE_URL` are the default fallback for `provider=openai`.

`ANTHROPIC_API_KEY` and `GOOGLE_API_KEY` remain in the template as reserved provider credentials, but they are not part of the current callpoint factory.

### Output-mode policy

Current callpoint allowlist:

| Callpoint | Allowed modes |
| --- | --- |
| `requirement_extraction` | `native`, `tool`, `prompted` |
| `bootstrap_keyword_generation` | `native`, `tool`, `prompted` |
| `search_controller_decision` | `native`, `tool` |
| `branch_outcome_evaluation` | `native`, `tool`, `prompted` |
| `search_run_finalization` | `native`, `tool`, `prompted` |

If a configured mode is not allowed for that callpoint, the runtime fails fast.

## Starter template

```dotenv
# LLM provider credentials
# OPENAI_API_KEY and OPENAI_BASE_URL act as the default fallback for provider=openai.
# ANTHROPIC_API_KEY and GOOGLE_API_KEY are kept here for future provider expansion.
OPENAI_API_KEY=
OPENAI_BASE_URL=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=

# LLM callpoints: default single-vendor baseline
# Supported providers: openai, dashscope, moonshot, glm
# Supported output modes: auto, native, tool, prompted
SEEKTALENT_REQUIREMENT_EXTRACTION_PROVIDER=openai
SEEKTALENT_REQUIREMENT_EXTRACTION_MODEL=gpt-5.4-mini
SEEKTALENT_REQUIREMENT_EXTRACTION_BASE_URL=
SEEKTALENT_REQUIREMENT_EXTRACTION_API_KEY=
SEEKTALENT_REQUIREMENT_EXTRACTION_OUTPUT_MODE=auto

SEEKTALENT_BOOTSTRAP_KEYWORD_GENERATION_PROVIDER=openai
SEEKTALENT_BOOTSTRAP_KEYWORD_GENERATION_MODEL=gpt-5.4-mini
SEEKTALENT_BOOTSTRAP_KEYWORD_GENERATION_BASE_URL=
SEEKTALENT_BOOTSTRAP_KEYWORD_GENERATION_API_KEY=
SEEKTALENT_BOOTSTRAP_KEYWORD_GENERATION_OUTPUT_MODE=auto

SEEKTALENT_SEARCH_CONTROLLER_DECISION_PROVIDER=openai
SEEKTALENT_SEARCH_CONTROLLER_DECISION_MODEL=gpt-5.4-mini
SEEKTALENT_SEARCH_CONTROLLER_DECISION_BASE_URL=
SEEKTALENT_SEARCH_CONTROLLER_DECISION_API_KEY=
SEEKTALENT_SEARCH_CONTROLLER_DECISION_OUTPUT_MODE=auto

SEEKTALENT_BRANCH_OUTCOME_EVALUATION_PROVIDER=openai
SEEKTALENT_BRANCH_OUTCOME_EVALUATION_MODEL=gpt-5.4-mini
SEEKTALENT_BRANCH_OUTCOME_EVALUATION_BASE_URL=
SEEKTALENT_BRANCH_OUTCOME_EVALUATION_API_KEY=
SEEKTALENT_BRANCH_OUTCOME_EVALUATION_OUTPUT_MODE=auto

SEEKTALENT_SEARCH_RUN_FINALIZATION_PROVIDER=openai
SEEKTALENT_SEARCH_RUN_FINALIZATION_MODEL=gpt-5.4-mini
SEEKTALENT_SEARCH_RUN_FINALIZATION_BASE_URL=
SEEKTALENT_SEARCH_RUN_FINALIZATION_API_KEY=
SEEKTALENT_SEARCH_RUN_FINALIZATION_OUTPUT_MODE=auto

# Mixed-vendor example
# SEEKTALENT_SEARCH_CONTROLLER_DECISION_PROVIDER=dashscope
# SEEKTALENT_SEARCH_CONTROLLER_DECISION_MODEL=qwen-max
# SEEKTALENT_SEARCH_CONTROLLER_DECISION_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# SEEKTALENT_SEARCH_CONTROLLER_DECISION_API_KEY=your-dashscope-key
# SEEKTALENT_SEARCH_RUN_FINALIZATION_PROVIDER=moonshot
# SEEKTALENT_SEARCH_RUN_FINALIZATION_MODEL=kimi-k2
# SEEKTALENT_SEARCH_RUN_FINALIZATION_BASE_URL=https://api.moonshot.cn/v1
# SEEKTALENT_SEARCH_RUN_FINALIZATION_API_KEY=your-moonshot-key

# SeekTalent runtime
SEEKTALENT_CTS_BASE_URL=https://link.hewa.cn
SEEKTALENT_CTS_TENANT_KEY=
SEEKTALENT_CTS_TENANT_SECRET=
SEEKTALENT_CTS_TIMEOUT_SECONDS=20
SEEKTALENT_CTS_SPEC_PATH=cts.validated.yaml
SEEKTALENT_MOCK_CTS=false
SEEKTALENT_RUNS_DIR=runs
SEEKTALENT_ROUND_BUDGET=5
SEEKTALENT_RERANK_BASE_URL=http://127.0.0.1:8012
SEEKTALENT_RERANK_TIMEOUT_SECONDS=20

# Local rerank server (optional for seektalent-rerank-api)
SEEKTALENT_RERANK_HOST=127.0.0.1
SEEKTALENT_RERANK_PORT=8012
SEEKTALENT_RERANK_MODEL_ID=mlx-community/Qwen3-Reranker-8B-mxfp8
SEEKTALENT_RERANK_BATCH_SIZE=4
SEEKTALENT_RERANK_MAX_LENGTH=8192
```

## Runtime variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `SEEKTALENT_CTS_BASE_URL` | No | `https://link.hewa.cn` | Base URL for the real CTS service. |
| `SEEKTALENT_CTS_TENANT_KEY` | Required in real CTS mode | empty | Used as the `tenant_key` header. |
| `SEEKTALENT_CTS_TENANT_SECRET` | Required in real CTS mode | empty | Used as the `tenant_secret` header. |
| `SEEKTALENT_CTS_TIMEOUT_SECONDS` | No | `20` | HTTP timeout for CTS calls. |
| `SEEKTALENT_CTS_SPEC_PATH` | No | `cts.validated.yaml` | Uses the packaged spec when left at the default value. |
| `SEEKTALENT_MOCK_CTS` | No | `false` | Enables the local mock CTS corpus. |
| `SEEKTALENT_RUNS_DIR` | No | `runs` | Output root for `run` artifacts and `doctor`. |
| `SEEKTALENT_ROUND_BUDGET` | No | `5` | Default runtime round budget. Values are clamped to `5..12`. |
| `SEEKTALENT_RERANK_BASE_URL` | No | `http://127.0.0.1:8012` | Runtime client base URL for the local rerank HTTP API. |
| `SEEKTALENT_RERANK_TIMEOUT_SECONDS` | No | `20` | Runtime client HTTP timeout for rerank requests. |

## Local rerank server variables

These are only used by `seektalent-rerank-api`.

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `SEEKTALENT_RERANK_HOST` | No | `127.0.0.1` | Bind host for the local rerank API server. |
| `SEEKTALENT_RERANK_PORT` | No | `8012` | Bind port for the local rerank API server. |
| `SEEKTALENT_RERANK_MODEL_ID` | No | `mlx-community/Qwen3-Reranker-8B-mxfp8` | Model loaded by the local rerank API server. |
| `SEEKTALENT_RERANK_BATCH_SIZE` | No | `4` | Batch size used by the local rerank engine. |
| `SEEKTALENT_RERANK_MAX_LENGTH` | No | `8192` | Maximum token length passed into the rerank engine. |

## Minimal examples

### Default OpenAI baseline

```dotenv
OPENAI_API_KEY=your-openai-key
SEEKTALENT_CTS_TENANT_KEY=your-cts-tenant-key
SEEKTALENT_CTS_TENANT_SECRET=your-cts-tenant-secret
```

### Mixed vendor setup

```dotenv
OPENAI_API_KEY=your-openai-key
SEEKTALENT_SEARCH_CONTROLLER_DECISION_PROVIDER=dashscope
SEEKTALENT_SEARCH_CONTROLLER_DECISION_MODEL=qwen-max
SEEKTALENT_SEARCH_CONTROLLER_DECISION_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
SEEKTALENT_SEARCH_CONTROLLER_DECISION_API_KEY=your-dashscope-key
SEEKTALENT_SEARCH_RUN_FINALIZATION_PROVIDER=moonshot
SEEKTALENT_SEARCH_RUN_FINALIZATION_MODEL=kimi-k2
SEEKTALENT_SEARCH_RUN_FINALIZATION_BASE_URL=https://api.moonshot.cn/v1
SEEKTALENT_SEARCH_RUN_FINALIZATION_API_KEY=your-moonshot-key
```

## Validation

Use:

```bash
seektalent doctor
seektalent inspect --json
```

`doctor` checks:

- the packaged CTS spec path
- settings loading
- the configured runs directory
- the active runtime manifest (`artifacts/runtime/active.json`)
- CTS credentials, unless mock mode is enabled
- rerank base URL and timeout settings
- each LLM callpoint's provider/model/output-mode configuration

`inspect --json` reports:

- provider
- model
- whether a base URL is configured
- requested output mode
- resolved output mode

## Related docs

- [CLI](cli.md)
- [Architecture](architecture.md)
- [Outputs](outputs.md)
