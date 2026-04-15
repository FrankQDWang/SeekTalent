# Configuration

`SeekTalent` reads configuration from environment variables. By default:

- `SEEKTALENT_*` settings are loaded through `pydantic-settings`
- selected provider variables are loaded from `.env` at process start

The recommended way to create a starter env file is:

```bash
seektalent init
```

You can also write to a custom path:

```bash
seektalent init --env-file ./local.env
```

In a source checkout, `.env.example` is the single editable template. `seektalent init` reads that file when it is present. The package keeps a mirror at `src/seektalent/default.env` so installed wheels can still generate starter env files outside the repo.

The default package install is OpenAI-only and includes `pydantic-ai-slim[openai]`. OpenAI-family model IDs and OpenAI-compatible `OPENAI_BASE_URL` endpoints are supported out of the box.

## Minimal setup

For a normal local setup, you usually need:

- one working LLM provider key
- CTS credentials
- the default model IDs are usually enough

Minimum values for a real CTS run:

```dotenv
OPENAI_API_KEY=your-openai-key
SEEKTALENT_CTS_TENANT_KEY=your-cts-tenant-key
SEEKTALENT_CTS_TENANT_SECRET=your-cts-tenant-secret
```

If you keep the default `openai-responses:*` models, `OPENAI_API_KEY` is the only provider key you need.

If you want to run `anthropic:*` or `google-gla:*` models, extend the installation to include the matching `pydantic-ai-slim[...]` extras first.

## Example `.env`

```dotenv
OPENAI_API_KEY=
OPENAI_BASE_URL=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=

SEEKTALENT_CTS_BASE_URL=https://link.hewa.cn
SEEKTALENT_CTS_TENANT_KEY=
SEEKTALENT_CTS_TENANT_SECRET=
SEEKTALENT_CTS_TIMEOUT_SECONDS=20
SEEKTALENT_CTS_SPEC_PATH=cts.validated.yaml

SEEKTALENT_REQUIREMENTS_MODEL=openai-responses:gpt-5.4-mini
SEEKTALENT_CONTROLLER_MODEL=openai-responses:gpt-5.4-mini
SEEKTALENT_SCORING_MODEL=openai-responses:gpt-5.4-mini
SEEKTALENT_FINALIZE_MODEL=openai-responses:gpt-5.4-mini
SEEKTALENT_REFLECTION_MODEL=openai-responses:gpt-5.4
SEEKTALENT_REASONING_EFFORT=medium

SEEKTALENT_MIN_ROUNDS=3
SEEKTALENT_MAX_ROUNDS=5
SEEKTALENT_SCORING_MAX_CONCURRENCY=5
SEEKTALENT_SEARCH_MAX_PAGES_PER_ROUND=3
SEEKTALENT_SEARCH_MAX_ATTEMPTS_PER_ROUND=3
SEEKTALENT_SEARCH_NO_PROGRESS_LIMIT=2
SEEKTALENT_MOCK_CTS=false
SEEKTALENT_ENABLE_REFLECTION=true
SEEKTALENT_RUNS_DIR=runs
```

## LLM provider variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | Required for `openai:*`, `openai-chat:*`, `openai-responses:*` models | empty | Needed if any configured model uses an OpenAI-family provider prefix. |
| `OPENAI_BASE_URL` | Optional | unset | Use this when routing OpenAI-compatible traffic to a custom endpoint. |
| `ANTHROPIC_API_KEY` | Required for `anthropic:*` models | empty | Needed only if you install the Anthropic extra and configure an Anthropic model. |
| `GOOGLE_API_KEY` | Required for `google-gla:*` models | empty | Needed only if you install the Google extra and configure a Google model. |

## CTS variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `SEEKTALENT_CTS_BASE_URL` | No | `https://link.hewa.cn` | Base URL for the CTS service. |
| `SEEKTALENT_CTS_TENANT_KEY` | Required in real CTS mode | `None` | Used as the `tenant_key` request header. |
| `SEEKTALENT_CTS_TENANT_SECRET` | Required in real CTS mode | `None` | Used as the `tenant_secret` request header. |
| `SEEKTALENT_CTS_TIMEOUT_SECONDS` | No | `20` | HTTP timeout for CTS requests. |
| `SEEKTALENT_CTS_SPEC_PATH` | No | `cts.validated.yaml` | If left as the default, `SeekTalent` uses the packaged spec file. If you set a different value, it is resolved relative to the current working directory unless absolute. |

## Model variables

All model settings must use the `provider:model` format.

| Variable | Required | Default |
| --- | --- | --- |
| `SEEKTALENT_REQUIREMENTS_MODEL` | No | `openai-responses:gpt-5.4-mini` |
| `SEEKTALENT_CONTROLLER_MODEL` | No | `openai-responses:gpt-5.4-mini` |
| `SEEKTALENT_SCORING_MODEL` | No | `openai-responses:gpt-5.4-mini` |
| `SEEKTALENT_FINALIZE_MODEL` | No | `openai-responses:gpt-5.4-mini` |
| `SEEKTALENT_REFLECTION_MODEL` | No | `openai-responses:gpt-5.4` |
| `SEEKTALENT_REASONING_EFFORT` | No | `medium` |

Notes:

- valid reasoning effort values are `low`, `medium`, and `high`
- `openai-responses:*` models additionally use `reasoning_summary=concise` and `text_verbosity=low`

## Agent variables

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `SEEKTALENT_MIN_ROUNDS` | No | `3` | Minimum number of rounds before stopping is allowed. |
| `SEEKTALENT_MAX_ROUNDS` | No | `5` | Hard stop for the Agent. Must be `>= min_rounds`. |
| `SEEKTALENT_SCORING_MAX_CONCURRENCY` | No | `5` | Max parallel per-resume scoring workers. |
| `SEEKTALENT_SEARCH_MAX_PAGES_PER_ROUND` | No | `3` | Per-round pagination budget. |
| `SEEKTALENT_SEARCH_MAX_ATTEMPTS_PER_ROUND` | No | `3` | Per-round CTS fetch attempt limit. |
| `SEEKTALENT_SEARCH_NO_PROGRESS_LIMIT` | No | `2` | Repeated no-progress threshold. |
| `SEEKTALENT_ENABLE_REFLECTION` | No | `true` | Enables the reflection step at the end of each round. |
| `SEEKTALENT_RUNS_DIR` | No | `runs` | Root output directory for run artifacts. Resolved relative to the current working directory unless absolute. |

## Provider matching rules

Before each run, the runtime checks provider credentials based on the configured model prefixes:

- OpenAI-family models require `OPENAI_API_KEY`
- Anthropic models require `ANTHROPIC_API_KEY` and an install that includes the Anthropic extra
- Google GLA models require `GOOGLE_API_KEY` and an install that includes the Google extra

Use `seektalent doctor` to validate the current local setup without making network calls:

```bash
seektalent doctor
```

## Development-only setting

| Variable | Default | Notes |
| --- | --- | --- |
| `SEEKTALENT_MOCK_CTS` | `false` | Enables the local mock CTS client for source-checkout development and tests. The published PyPI CLI rejects this mode. |

## Related docs

- [CLI](cli.md)
- [UI](ui.md)
- [Outputs](outputs.md)
