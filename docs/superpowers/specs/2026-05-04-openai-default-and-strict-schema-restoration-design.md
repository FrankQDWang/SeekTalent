# OpenAI Default And Strict Schema Restoration Design

## Summary

Restore the repository default text-LLM protocol to Bailian OpenAI Chat Completions-compatible, preserve Anthropic Messages-compatible support as a non-default capability, restore strict structured output on the default OpenAI-compatible path for structured stages, and replace the remaining `candidate_feedback_model_id` default from `qwen3.5-flash` to `deepseek-v4-flash`.

This change is intentionally narrow. It does not redesign candidate-feedback behavior, does not reconnect dormant model-ranked candidate-feedback helpers to the active rescue lane, and does not change stopping or retrieval policy.

## Motivation

The current canonical text-LLM surface defaults to:

- `text_llm_protocol_family = anthropic_messages_compatible`
- `text_llm_endpoint_kind = bailian_anthropic_messages`
- `text_llm_endpoint_region = beijing`

At the same time, the old `openai-chat:deepseek-v3.2` path previously had repository-local strict structured output behavior. After the dual-protocol rollout, strict schema is no longer the default behavior for the main structured stages because the current Bailian/DeepSeek V4 capability map resolves to `prompted_json` on both active protocol families.

The intended product direction is:

1. keep both protocol families supported long-term;
2. make Bailian OpenAI Chat Completions-compatible the default again;
3. restore strict schema behavior where the runtime genuinely depends on structured outputs;
4. stop carrying `qwen3.5-flash` as the remaining active default model id in `candidate_feedback_model_id`.

## Scope

This change includes:

- changing the repository default text-LLM protocol family to `openai_chat_completions_compatible`;
- changing the default endpoint kind to `bailian_openai_chat_completions`;
- preserving `anthropic_messages_compatible` as an explicitly selectable protocol family;
- restoring `NativeOutput(..., strict=True)` on the default OpenAI-compatible path for structured stages;
- keeping Anthropic-compatible structured stages on prompted structured output;
- changing `candidate_feedback_model_id` default from `qwen3.5-flash` to `deepseek-v4-flash`;
- syncing checked-in env defaults and local env defaults.

This change does not include:

- changing active candidate-feedback rescue behavior;
- reconnecting `CandidateFeedbackModelSteps` to the active rescue lane;
- changing PRF v1.5 rollout mode;
- redesigning stopping or exhaustion policy;
- reintroducing legacy `provider:model` config surfaces.

## Current State

### Active candidate-feedback rescue path

The active `candidate_feedback` rescue lane is deterministic and local. The runtime path goes through `rescue_execution_runtime.force_candidate_feedback_decision(...)`, which builds feedback expressions and a forced search decision from scored seed resumes without using the dormant LLM helper.

The active rescue lane therefore does not currently depend on `candidate_feedback_model_id`.

### Dormant candidate-feedback model helper

`CandidateFeedbackModelSteps` still exists and still resolves `stage="candidate_feedback"`. That helper is not the active rescue path today, but it is still part of the canonical text-LLM config surface and should not keep a stale Qwen default.

### Current structured-output behavior

For canonical `ResolvedTextModelConfig` inputs, `build_output_spec(...)` uses:

- `NativeOutput(..., strict=True)` only when `structured_output_mode == "native_json_schema"`;
- `PromptedOutput(...)` otherwise.

The current capability matrix resolves Bailian DeepSeek V4 entries to `prompted_json` on both OpenAI-compatible and Anthropic-compatible paths, so default strict structured output is not currently restored.

## Design

### 1. Default protocol family

Change the default canonical text-LLM surface to:

- `text_llm_protocol_family = openai_chat_completions_compatible`
- `text_llm_provider_label = bailian`
- `text_llm_endpoint_kind = bailian_openai_chat_completions`
- `text_llm_endpoint_region = beijing`

Anthropic-compatible remains supported through explicit configuration:

- `text_llm_protocol_family = anthropic_messages_compatible`
- `text_llm_endpoint_kind = bailian_anthropic_messages`
- `text_llm_endpoint_region = beijing` or other supported configured regions

This is a default switch, not a protocol-family deletion.

### 2. Structured-output policy

Structured output becomes protocol-sensitive.

#### Default OpenAI-compatible path

For structured stages on the default `openai_chat_completions_compatible` path, restore native strict structured output:

- `requirements`
- `controller`
- `reflection`
- `finalize`
- `judge`
- `structured_repair`

These stages must resolve to `structured_output_mode = "native_json_schema"` and therefore use `NativeOutput(..., strict=True)`.

`scoring` inherits the same behavior when its stage output path is schema-bound through the shared `build_output_spec(...)` flow. `tui_summary` remains free-form summary text and is not promoted into a strict-schema stage.

#### Anthropic-compatible path

For the same structured stages on `anthropic_messages_compatible`, keep `structured_output_mode = "prompted_json"` and therefore keep `PromptedOutput(...)`.

Anthropic-compatible support remains available, but it is not the repository default path for strict structured output.

### 3. Candidate-feedback model default

Change:

- `candidate_feedback_model_id = "qwen3.5-flash"`

to:

- `candidate_feedback_model_id = "deepseek-v4-flash"`

This change is configuration cleanup, not behavioral promotion.

The active rescue lane remains deterministic and local. The dormant helper remains dormant unless a future design explicitly reconnects it.

### 4. Environment defaults

Sync the same default changes in:

- `src/seektalent/default.env`
- `.env.example`
- local `.env`

The checked-in defaults and the local default development environment must match this new canonical default protocol surface.

## Non-Goals

### Candidate-feedback behavior

Do not change:

- active candidate-feedback rescue routing;
- expression extraction behavior;
- forced-term acceptance/rejection behavior;
- seed selection behavior.

This design only changes the dormant helper's default model id, not the active rescue lane semantics.

### PRF and sidecar behavior

Do not change:

- `prf_v1_5_mode`
- `prf_model_backend`
- PRF span-extraction rollout
- sidecar behavior

### Legacy config surface

Do not restore any legacy `openai-chat:...`, `openai-responses:...`, or provider-prefixed model string config path.

The canonical config surface remains the only supported active surface.

## Testing Expectations

### Default protocol expectations

Tests must verify that default `AppSettings()` now resolve to:

- `text_llm_protocol_family == "openai_chat_completions_compatible"`
- `text_llm_endpoint_kind == "bailian_openai_chat_completions"`
- `text_llm_endpoint_region == "beijing"`

Tests must also verify that explicit Anthropic-compatible settings still resolve successfully.

### Structured-output expectations

Tests must verify that on the default OpenAI-compatible path the structured stages resolve to `NativeOutput(..., strict=True)`:

- `requirements`
- `controller`
- `reflection`
- `finalize`
- `judge`
- `structured_repair`

Tests must verify that the same stages under explicit Anthropic-compatible settings resolve to `PromptedOutput(...)`.

### Candidate-feedback config expectations

Tests must verify:

- `candidate_feedback_model_id == "deepseek-v4-flash"` by default;
- `.env.example`, `default.env`, and local `.env` are synchronized;
- active runtime behavior does not start instantiating `CandidateFeedbackModelSteps` on the main rescue path as a side effect of this change.

### Regression expectations

At minimum, rerun:

- `tests/test_llm_provider_config.py`
- `tests/test_candidate_feedback.py`
- `tests/test_runtime_audit.py`
- `tests/test_cli.py`

And rerun the core structured-stage suites that exercise:

- requirements
- controller
- reflection
- scoring
- finalize
- evaluation/judge

## Acceptance Criteria

This change is complete when all of the following are true:

1. the repository default text-LLM protocol is `openai_chat_completions_compatible`;
2. Anthropic-compatible remains available through explicit config;
3. default OpenAI-compatible structured stages use strict native structured output;
4. Anthropic-compatible structured stages remain prompted structured output;
5. `candidate_feedback_model_id` default is `deepseek-v4-flash`;
6. active candidate-feedback rescue behavior is unchanged;
7. no legacy provider-prefixed config surface is reintroduced.
