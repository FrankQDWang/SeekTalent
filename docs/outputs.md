# Outputs

`SeekTalent v0.3.3 active` returns a structured `SearchRunBundle` and persists run artifacts.

`seektalent run` writes:

- human mode: a compact stdout summary plus live stderr trace
- `--json` mode: `SearchRunBundle.model_dump(mode="json")`

Python API returns the same bundle as `run_match(...)`.

The stable product result pointer is `final_result.final_candidate_cards`.

## What currently writes files

### `seektalent run`

Writes `runs/<run_id>/bundle.json`, `final_result.json`, and `eval.json`.

### `seektalent init`

Writes one env file, `.env` by default.

### `seektalent doctor`

Ensures the configured `runs` directory exists and validates the active runtime manifest.

## What exists on disk now

- `runs/<run_id>/bundle.json`
- `runs/<run_id>/final_result.json`
- `runs/<run_id>/eval.json`
- `artifacts/runtime/active.json`
- `artifacts/runtime/policies/*.json`
- `artifacts/runtime/cases/<case_id>/...`
- `artifacts/runtime/evals/e5-matrix.json`

## `bundle.json` now includes full prompt surfaces

Every one of the 5 LLM callpoints writes a full `LLMCallAudit` into the bundle:

- `bootstrap.requirement_extraction_audit`
- `bootstrap.bootstrap_keyword_generation_audit`
- `rounds[*].controller_audit`
- `rounds[*].branch_evaluation_audit`
- `finalization_audit`

Each audit now contains:

- fixed runtime audit fields: `output_mode`, `retries`, `output_retries`, `validator_retry_count`, `model_name`, `model_settings_snapshot`
- a full `prompt_surface`

`output_mode` is now provider-aware and may be `NativeOutput(strict=True)`, `ToolOutput(strict=True)`, or `PromptedOutput`.

Each `prompt_surface` stores:

- `instructions_text`
- `input_text`
- `instructions_sha1`
- `input_sha1`
- ordered `sections[*]`

Each section stores:

- `title`
- `body_text`
- `source_paths`
- `is_dynamic`

This is the only prompt audit owner. There is no sidecar prompt file, no prompt preview field, and no hash-only audit fallback.

For rewrite-driven search rounds, `bundle.json` now also stores `rounds[*].rewrite_choice_trace`:

- `seed_query_terms`
- `selected_query_terms`
- `candidate_count`
- `selected_total_score`
- `selected_breakdown`
- optional runner-up summary

This is the compact GA-lite choice trace owner. It does not log the full rewrite population.

## `eval.json` now includes phased diagnostics

`SearchRunBundle.eval` remains a flat metric list, but it now includes machine-friendly phased diagnostics derived from search rounds:

- `search_round_indexes`
- `search_phase_by_search_round`
- `selected_operator_by_search_round`
- `eligible_open_node_count_by_search_round`
- `selection_margin_by_search_round`
- `must_have_query_coverage_by_search_round`
- `net_new_shortlist_gain_by_search_round`
- `run_shortlist_size_after_search_round`
- `operator_distribution_explore`
- `operator_distribution_balance`
- `operator_distribution_harvest`
- `time_to_first_advance_round`
- `pages_per_advance_candidate`
- `advance_candidates_per_query`
- `query_churn_rate`
- `review_burden_candidate_count`
- `review_burden_per_advance`

These metrics live only in `bundle.eval` / `eval.json`. They do not change the business-case `artifacts/runtime/evals/e5-matrix.json` schema.

## What remains intentionally absent

- `trace.log`
- `events.jsonl`
- UI payload artifacts

Realtime progress exists only on `stderr`:

- `--progress text` writes a human-readable business trace
- `--progress jsonl` writes stable JSONL progress events
- `--progress off` disables it

## Related docs

- [System Model](/Users/frankqdwang/Agents/SeekTalent/docs/v-0.3.3/SYSTEM_MODEL.md)
- [CLI](/Users/frankqdwang/Agents/SeekTalent/docs/cli.md)
- [Configuration](/Users/frankqdwang/Agents/SeekTalent/docs/configuration.md)
- [Archived Implementation Checklist](/Users/frankqdwang/Agents/SeekTalent/docs/_archive/v-0.3.1/implementation-checklist.md)
- [Archived LLM Context Surfaces](/Users/frankqdwang/Agents/SeekTalent/docs/_archive/v-0.3.1/llm-context-surfaces.md)
