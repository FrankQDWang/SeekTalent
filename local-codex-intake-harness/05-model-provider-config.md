# Model Provider Config

## Default Provider Posture

The local intake harness must not default to OpenAI API. The default target is an OpenAI-compatible non-OpenAI endpoint, matching the current repository's Bailian/DashScope posture.

Default environment key:

```text
SEEKTALENT_TEXT_LLM_API_KEY
```

Default provider config:

```toml
model = "deepseek-v4-flash"
model_provider = "dashscope"

[model_providers.dashscope]
name = "DashScope OpenAI-compatible"
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
env_key = "SEEKTALENT_TEXT_LLM_API_KEY"
```

## Forbidden Defaults

The implementation must not default to:

```text
OPENAI_API_KEY
OPENAI_BASE_URL
codex login
requires_openai_auth = true
```

Those can only appear in documentation as forbidden examples or explicit user overrides.

## Provider Smoke Gate

The implementation must include a smoke check that verifies:

- the Codex CLI/app-server can start;
- `CODEX_HOME` is project-local;
- the selected model provider is loaded from project-local config;
- a short intake turn completes;
- the response can be parsed into the intake contract.

Expected success output:

```text
status: ready
provider: dashscope
model: deepseek-v4-flash
codex_home: .seektalent/codex_home
memory: enabled
openai_auth: false
```

Expected missing credential output:

```text
status: provider_not_configured
reason: SEEKTALENT_TEXT_LLM_API_KEY is not set
```

Expected incompatible provider output:

```text
status: provider_smoke_failed
reason: codex_provider_request_failed
```

## App Server Compatibility Rule

Using Codex App Server must not reduce provider control. If an App Server path hides provider config, requires OpenAI login, or does not honor project-local `CODEX_HOME`, it is not acceptable for this feature.

The fallback is not SDK usage and not another cloud provider. The fallback is a clear local diagnostic that tells the user what is missing.

## Runtime Separation

This provider config is for Codex intake only. It must not change existing SeekTalent stage model configuration for:

- requirements extraction;
- controller;
- scoring;
- finalization;
- reflection;
- Workbench note writer.

The existing runtime provider config remains owned by `src/seektalent/config.py` and the runtime refactor effort.
