# Domi Prod LLM Provider And Runtime Smoke Design

## Goal

Validate that the packaged SeekTalent Workbench can run on this machine using Domi-provided runtime pieces, while production LLM calls go through the Domi LLM proxy with a Domi JWT.

This is a first-stage production-environment slice. It proves the runtime shape before adding a formal Domi launch API or plugin contract.

## Scope

- Keep source-checkout development unchanged: `scripts/start-dev-workbench.sh` continues to use the current direct provider configuration, repo-local React dependencies, and repo-local OpenCLI path.
- Add a production LLM transport path selected by `SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi`.
- Use explicit JWT injection for the first stage through a dedicated environment variable such as `SEEKTALENT_DOMI_JWT`.
- Keep all existing stage model configuration and defaults. Domi mode changes transport and credential handling only.
- Add a smoke path that uses Domi's bundled Python runtime to create an isolated `~/.seektalent/domi-runtime` environment, install the current repository wheel, start/check the packaged Workbench, verify a Domi LLM proxy hello call, and check OpenCLI bootstrap/daemon/extension readiness.
- Support test and production Domi proxy hosts through configuration. The first-stage smoke defaults to the test host.

## Non-Goals

- Do not implement the final Domi launch interface.
- Do not implement a full Domi plugin/runtime protocol.
- Do not read Domi Electron storage as the production JWT source.
- Do not install or modify files inside `/Applications/Domi.app`.
- Do not make a full live Liepin recruiting run a hard gate for this slice.
- Do not change the default model ids because of Domi. The developer's `qwen3.7-plus` curl is an example request, not a SeekTalent default.

## Configuration

Domi mode uses the existing text LLM configuration surface plus a small Domi transport surface:

```env
SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi
SEEKTALENT_DOMI_JWT=<jwt>
SEEKTALENT_DOMI_LLM_BASE_URL=https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1
SEEKTALENT_DOMI_LLM_CHANNEL=seek_talent
```

Existing model fields keep their current defaults and behavior:

```env
SEEKTALENT_REQUIREMENTS_MODEL_ID=
SEEKTALENT_CONTROLLER_MODEL_ID=
SEEKTALENT_SCORING_MODEL_ID=
SEEKTALENT_REFLECTION_MODEL_ID=
SEEKTALENT_WORKBENCH_CONVERSATION_MODEL_ID=
```

Operators do not need to set these stage model variables for the normal Domi smoke. If they do set them, the values mean the same thing as they do in direct Bailian mode.

`SEEKTALENT_TEXT_LLM_API_KEY` remains the direct-provider credential and is not reused for the Domi JWT.

## LLM Client Behavior

`seektalent.llm` remains the central model construction boundary.

When `provider_label == "bailian"`, existing behavior is preserved:

- resolve the configured Bailian-compatible base URL;
- require `SEEKTALENT_TEXT_LLM_API_KEY`;
- construct the current OpenAI-compatible or Anthropic-compatible provider.

When `provider_label == "domi"`, the OpenAI-compatible path constructs a chat client with:

- `base_url` from `SEEKTALENT_DOMI_LLM_BASE_URL`;
- `Authorization: Bearer <SEEKTALENT_DOMI_JWT>`;
- default query `channel=<SEEKTALENT_DOMI_LLM_CHANNEL>`;
- model id from the existing stage model config.

Domi mode fails fast if the JWT is missing. The error must name the missing Domi credential and must not suggest `SEEKTALENT_TEXT_LLM_API_KEY` as the fix.

Secrets must not appear in logs, doctor output, runtime events, public errors, or test assertions.

Structured-output behavior starts from the existing model capability policy. If the Domi proxy is later proven incompatible with native JSON schema for a model, handle that as a provider-specific policy adjustment instead of changing stage model defaults.

## Prod Workbench Environment

`build_workbench_command_env()` currently forces the packaged Workbench into prod mode and only passes the direct LLM key. It should continue to force:

- `SEEKTALENT_WORKSPACE_ROOT=$HOME`;
- `SEEKTALENT_RUNTIME_MODE=prod`;
- `SEEKTALENT_PROVIDER_NAME=liepin`;
- `SEEKTALENT_LIEPIN_WORKER_MODE=opencli`;
- `SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND=opencli`.

It should also allow the minimal Domi LLM variables through:

- `SEEKTALENT_TEXT_LLM_PROVIDER_LABEL`;
- `SEEKTALENT_DOMI_JWT`;
- `SEEKTALENT_DOMI_LLM_BASE_URL`;
- `SEEKTALENT_DOMI_LLM_CHANNEL`.

Workbench preflight should check the credential required by the selected text LLM provider:

- direct Bailian mode requires `SEEKTALENT_TEXT_LLM_API_KEY`;
- Domi mode requires `SEEKTALENT_DOMI_JWT`.

## Domi Runtime Smoke

Add an explicit smoke tool such as `scripts/smoke-domi-runtime.sh`.

The smoke tool should:

1. Locate Domi's bundled Python at `/Applications/Domi.app/Contents/Resources/extraResources/python/runtime/bin/python`.
2. Create or reuse a venv under `~/.seektalent/domi-runtime`.
3. Build a wheel from the current repository.
4. Install that wheel into the Domi-runtime venv.
5. Run `seektalent doctor` under Domi provider configuration.
6. Start or dry-check `seektalent workbench` enough to prove packaged Workbench startup.
7. Send a Domi LLM proxy hello request using `SEEKTALENT_DOMI_JWT`.
8. Check OpenCLI bootstrap, daemon status, and extension connection.

The script defaults to the Domi test API host. Production host testing is an explicit env override.

The script must not read Domi storage by default. A future local-only helper may add an explicit opt-in token discovery mode for smoke testing, but that mode remains outside the production design.

## Error Handling

Credential failures should be specific:

- missing Domi JWT: `seektalent_domi_jwt_missing`;
- missing direct provider key: existing text LLM key missing reason;
- Domi proxy request failure: preserve HTTP status and a safe summary, without token or raw provider payload;
- OpenCLI not installed, daemon stale, extension disconnected, or Liepin login missing: preserve existing OpenCLI reason-code style.

If OpenCLI extension is not connected during the smoke, the result is an environment readiness failure, not a code failure.

## Tests

Add focused tests for:

- `AppSettings` accepts `text_llm_provider_label=domi` and parses Domi base URL, channel, and JWT fields.
- Domi provider missing-JWT behavior fails with a Domi-specific error.
- Domi OpenAI-compatible client construction uses the Domi base URL, Bearer JWT, and `channel` query.
- Direct Bailian tests continue to pass unchanged.
- `build_workbench_command_env()` passes only the minimal Domi variables and does not pass unrelated runtime overrides.
- `seektalent workbench` preflight requires the correct credential for the selected provider.
- Smoke script dry-run or static tests cover Domi Python path, target venv path, wheel install step, and OpenCLI check commands.

## Manual Acceptance

The first-stage smoke is accepted when:

- Domi's bundled Python creates `~/.seektalent/domi-runtime`.
- The current repository wheel installs into that venv.
- `seektalent doctor` passes under Domi provider configuration.
- The packaged Workbench starts and serves its UI.
- A hello request through Domi LLM proxy succeeds with the explicit JWT.
- OpenCLI bootstrap and daemon/extension checks run and report clear status.

A complete live Liepin recruiting workflow remains a later manual acceptance step after the environment smoke is stable.
