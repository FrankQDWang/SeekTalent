# Configuration

`cv-match` reads configuration from environment variables. By default:

- `CVMATCH_*` settings are loaded through `pydantic-settings`
- selected provider variables are loaded from `.env` at process start

The repository includes a starter file:

```bash
cp .env.example .env
```

## Run the Agent

For a normal user setup, you usually do not need to change the default model names in `.env.example`.

You must fill these values before running the Agent:

- one provider key that matches your configured models
- `CVMATCH_CTS_TENANT_KEY`
- `CVMATCH_CTS_TENANT_SECRET`

Example:

```dotenv
OPENAI_API_KEY=your-openai-key
CVMATCH_CTS_TENANT_KEY=your-cts-tenant-key
CVMATCH_CTS_TENANT_SECRET=your-cts-tenant-secret
```

If you keep the default `openai-responses:*` models from `.env.example`, you only need `OPENAI_API_KEY` on the provider side.

## Minimal user setup

For the normal user path, you usually need:

- one working LLM provider key
- CTS credentials
- the default model IDs from `.env.example` are usually enough

## Example `.env`

```dotenv
OPENAI_API_KEY=
OPENAI_BASE_URL=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=

CVMATCH_CTS_BASE_URL=https://link.hewa.cn
CVMATCH_CTS_TENANT_KEY=
CVMATCH_CTS_TENANT_SECRET=
CVMATCH_CTS_TIMEOUT_SECONDS=20
CVMATCH_CTS_SPEC_PATH=cts.validated.yaml

CVMATCH_REQUIREMENTS_MODEL=openai-responses:gpt-5.4-mini
CVMATCH_CONTROLLER_MODEL=openai-responses:gpt-5.4-mini
CVMATCH_SCORING_MODEL=openai-responses:gpt-5.4-mini
CVMATCH_FINALIZE_MODEL=openai-responses:gpt-5.4-mini
CVMATCH_REFLECTION_MODEL=openai-responses:gpt-5.4
CVMATCH_REASONING_EFFORT=medium

CVMATCH_MIN_ROUNDS=3
CVMATCH_MAX_ROUNDS=5
CVMATCH_SCORING_MAX_CONCURRENCY=5
CVMATCH_SEARCH_MAX_PAGES_PER_ROUND=3
CVMATCH_SEARCH_MAX_ATTEMPTS_PER_ROUND=3
CVMATCH_SEARCH_NO_PROGRESS_LIMIT=2
CVMATCH_MOCK_CTS=false
CVMATCH_ENABLE_REFLECTION=true
CVMATCH_RUNS_DIR=runs
```

## LLM provider variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | Required for `openai:*`, `openai-chat:*`, `openai-responses:*` models | empty | Needed if any configured model uses an OpenAI provider prefix. |
| `OPENAI_BASE_URL` | Optional | unset | Use this when routing OpenAI-compatible traffic to a custom endpoint. |
| `ANTHROPIC_API_KEY` | Required for `anthropic:*` models | empty | Needed if any configured model uses the Anthropic provider. |
| `GOOGLE_API_KEY` | Required for `google-gla:*` models | empty | Needed if any configured model uses the Google provider. |

## CTS variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `CVMATCH_CTS_BASE_URL` | No | `https://link.hewa.cn` | Base URL for the CTS service. |
| `CVMATCH_CTS_TENANT_KEY` | Required in real CTS mode | `None` | Used as the `tenant_key` request header. |
| `CVMATCH_CTS_TENANT_SECRET` | Required in real CTS mode | `None` | Used as the `tenant_secret` request header. |
| `CVMATCH_CTS_TIMEOUT_SECONDS` | No | `20` | HTTP timeout for CTS requests. |
| `CVMATCH_CTS_SPEC_PATH` | No | `cts.validated.yaml` | Local OpenAPI file used as the adapter's spec reference. |

## Model variables

All model settings must use the `provider:model` format.

| Variable | Required | Default |
| --- | --- | --- |
| `CVMATCH_REQUIREMENTS_MODEL` | No | `openai-responses:gpt-5.4-mini` |
| `CVMATCH_CONTROLLER_MODEL` | No | `openai-responses:gpt-5.4-mini` |
| `CVMATCH_SCORING_MODEL` | No | `openai-responses:gpt-5.4-mini` |
| `CVMATCH_FINALIZE_MODEL` | No | `openai-responses:gpt-5.4-mini` |
| `CVMATCH_REFLECTION_MODEL` | No | `openai-responses:gpt-5.4` |
| `CVMATCH_REASONING_EFFORT` | No | `medium` |

Notes:

- Valid reasoning effort values are `low`, `medium`, and `high`.
- `openai-responses:*` models additionally use `reasoning_summary=concise` and `text_verbosity=low` internally.

## Agent variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `CVMATCH_MIN_ROUNDS` | No | `3` | Minimum number of rounds before stopping is allowed. |
| `CVMATCH_MAX_ROUNDS` | No | `5` | Hard stop for the Agent. Must be `>= min_rounds`. |
| `CVMATCH_SCORING_MAX_CONCURRENCY` | No | `5` | Max parallel per-resume scoring workers. |
| `CVMATCH_SEARCH_MAX_PAGES_PER_ROUND` | No | `3` | Per-round pagination budget. |
| `CVMATCH_SEARCH_MAX_ATTEMPTS_PER_ROUND` | No | `3` | Per-round CTS fetch attempt limit. |
| `CVMATCH_SEARCH_NO_PROGRESS_LIMIT` | No | `2` | Repeated no-progress threshold. |
| `CVMATCH_ENABLE_REFLECTION` | No | `true` | Enables the reflection step at the end of each round. |
| `CVMATCH_RUNS_DIR` | No | `runs` | Root output directory for run artifacts. |

## Provider matching rules

The Agent performs model preflight before each run:

- OpenAI-family model IDs require `OPENAI_API_KEY`
- Anthropic model IDs require `ANTHROPIC_API_KEY`
- Google GLA model IDs require `GOOGLE_API_KEY`

## Development-only settings

The following setting is intended for development and testing, not for the normal user path:

| Variable | Default | Notes |
| --- | --- | --- |
| `CVMATCH_MOCK_CTS` | `false` in `.env.example` | Enables the local mock CTS client. Use this only for local development and tests. |

### Real CTS + OpenAI

```dotenv
OPENAI_API_KEY=your-key
CVMATCH_CTS_TENANT_KEY=your-tenant-key
CVMATCH_CTS_TENANT_SECRET=your-tenant-secret
```

## Related docs

- [CLI](cli.md)
- [UI](ui.md)
- [Outputs](outputs.md)
