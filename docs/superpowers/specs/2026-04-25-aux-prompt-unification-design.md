# Auxiliary Prompt Unification Design

## Goal

Unify non-primary LLM prompts with the same prompt asset path used by the primary runtime chain.

After this change, auxiliary LLM calls should use:

- markdown prompt files under `src/seektalent/prompts/`
- `PromptRegistry` loading and hashing
- prompt snapshot files in run artifacts
- prompt hashes in `run_config.json`
- `LLMCallSnapshot` records with the same core prompt metadata fields used by the primary chain

This change is only about prompt asset management and runtime observability. It does not change business behavior, model selection, reasoning effort policy, or user-prompt construction.

## In Scope

The following auxiliary prompt entrypoints move onto the unified prompt path:

- `tui_summary`
- `candidate_feedback`
- `company_discovery_plan`
- `company_discovery_extract`
- `company_discovery_reduce`
- `repair_requirements`
- `repair_controller`
- `repair_reflection`

The existing primary-chain prompts stay as-is:

- `requirements`
- `controller`
- `scoring`
- `reflection`
- `finalize`
- `judge`

## Prompt Files

Add these files under `src/seektalent/prompts/`:

- `tui_summary.md`
- `candidate_feedback.md`
- `company_discovery_plan.md`
- `company_discovery_extract.md`
- `company_discovery_reduce.md`
- `repair_requirements.md`
- `repair_controller.md`
- `repair_reflection.md`

Each file contains only the system prompt content for that stage.

User-prompt render functions remain in Python.

## Wiring Rules

`WorkflowRuntime` remains the single prompt assembly point.

It should:

- load all primary and auxiliary prompts through `PromptRegistry.load_many(...)`
- pass `LoadedPrompt` instances into runtime-owned components
- expose all loaded prompt hashes and prompt snapshot files through the existing runtime artifact flow

Component wiring should follow the same pattern as the primary chain:

- `ResumeQualityCommenter(settings, prompt)`
- `CandidateFeedbackModelSteps(settings, prompt)`
- `CompanyDiscoveryModelSteps(settings, prompts_by_name)`
- repair helpers receive explicit `LoadedPrompt` inputs instead of embedding system-prompt strings in code

No new prompt manager or parallel registry should be introduced.

## Runtime Visibility

All auxiliary prompts should be versioned assets in every run:

- present in `run_config.json` prompt hash output
- written to prompt snapshot files through the existing runtime snapshot path
- included in doctor-style packaged prompt verification where prompt file presence is checked

The following auxiliary model calls should also emit `LLMCallSnapshot` artifacts:

- `tui_summary`
- `candidate_feedback`
- `company_discovery_plan`
- `company_discovery_extract`
- `company_discovery_reduce`
- `repair_requirements`
- `repair_controller`
- `repair_reflection`

Each snapshot should carry the same prompt metadata shape already used by the primary chain:

- `prompt_name`
- `prompt_hash`
- `prompt_snapshot_path`
- `model_id`
- `input_payload_sha256`
- `input_summary`
- `output_summary`
- `latency_ms`
- `provider_usage`
- `prompt_cache_key`
- `prompt_cache_retention`

Prompt-cache fields are populated only when that stage already supports prompt caching. This design does not add new caching policy.

## Artifact Placement

Keep artifact placement simple and local to the owning stage.

Suggested filenames:

- `rounds/round_XX/tui_summary_call.json`
- `rounds/round_XX/candidate_feedback_call.json`
- `rounds/round_XX/company_discovery_plan_call.json`
- `rounds/round_XX/company_discovery_extract_call.json`
- `rounds/round_XX/company_discovery_reduce_call.json`
- `requirements_repair_call.json`
- `rounds/round_XX/controller_repair_call.json`
- `rounds/round_XX/reflection_repair_call.json`

Do not create a second orchestration layer just for auxiliary calls.

## Behavior Constraints

This work must not:

- change accepted models or default model routing
- change reasoning-effort defaults
- change user-prompt payload structure except where needed to log call snapshots
- merge multiple company-discovery stages into one prompt
- refactor business logic unrelated to prompt loading or observability

The implementation should prefer surgical diffs in existing code paths.

## Testing

Add or update tests so they prove:

- new prompt files are loadable through `PromptRegistry`
- runtime prompt hashes include the auxiliary prompt names
- prompt snapshot output contains the new files
- auxiliary call artifacts record the expected prompt metadata
- existing behavior of `tui_summary`, candidate feedback, company discovery, and repair remains unchanged aside from prompt source and observability

## Rollout Notes

This is an internal consistency and observability change. There is no intended product-level behavior change.

Success criteria:

- all runtime-used system prompts live in `src/seektalent/prompts/*.md`
- no auxiliary LLM stage relies on embedded system-prompt strings
- prompt hashes and snapshots cover both primary and auxiliary prompt families
- auxiliary LLM calls are inspectable through the same run-artifact conventions as the main chain
