# LLM PRF Live Validation Harness Design

Date: 2026-05-05
Status: Draft for user review
Branch context: `codex/llm-prf-probe-mainline`

## Context

LLM-backed `prf_probe` proposal now sits on the typed second-lane retrieval path. Recent live smoke work found several bugs that ordinary unit tests did not expose:

- PRF input could be built from scorecard labels instead of raw normalized resume evidence.
- DeepSeek V4 Flash could return too many verbose candidates, causing timeout or truncated JSON.
- The model could synthesize descriptive phrases that failed deterministic grounding.
- Mixed CJK/ASCII evidence could be misclassified as unsafe substring matches.
- Policy could accept a candidate with only one positive seed.
- Existing, sent, or tried query terms could be accepted again as if they were new PRF discoveries.

Full 12-JD benchmark/eval is too expensive and noisy for isolating these PRF-specific risks. It includes controller, CTS, scoring, reflection, and eval behavior, any of which can hide or distort the PRF failure mode. The next step is a small checked-in live validation harness focused only on the LLM PRF chain.

## Goal

Create a reusable manual validation gate for LLM PRF before running full benchmark/eval.

The harness answers one question:

> Is the current LLM PRF extractor, grounding validator, and policy gate safe enough to justify running the 12-JD benchmark?

It does not replace the 12-JD benchmark. It runs before it.

## Non-Goals

- Do not add a product runtime component.
- Do not change `WorkflowRuntime` behavior for production runs.
- Do not change retrieval lane allocation, scoring, eval, CTS, rescue `candidate_feedback`, or active PRF policy semantics.
- Do not run in CI by default.
- Do not store full raw resumes, full benchmark artifacts, API keys, or provider responses in repo fixtures.
- Do not create a second PRF implementation path.

## Recommended Approach

Extend the existing checked-in PRF bakeoff area rather than adding a new subsystem.

Primary implementation target:

- Reuse or lightly extend `src/seektalent/candidate_feedback/llm_prf_bakeoff.py`.

The harness will support a new `--case-format llm-prf-input` mode that reads sanitized case wrappers containing `LLMPRFInput` payloads and expected behavior metadata, then runs:

```text
LLMPRFExtractor
  -> ground_llm_prf_candidates
  -> feedback_expressions_from_llm_grounding
  -> build_prf_policy_decision
  -> validation summary
```

This keeps the boundary narrow. The harness depends on the same extractor, grounding, familying, and policy code used by runtime, but runtime does not depend on the harness.

## Fixture Design

Add a small checked-in fixture set at:

```text
tests/fixtures/llm_prf_live_validation/cases.jsonl
```

Each JSONL row is a wrapper with:

- `case_id`;
- `expected_behavior`;
- `input`, a minimal `LLMPRFInput` JSON object;
- optional `blocked_terms`;
- optional `notes`.

The fixtures must be:

- minimal `LLMPRFInput` payloads;
- sanitized and safe to commit;
- small enough to review in diffs;
- representative of real PRF evidence;
- not full benchmark run directories.

Initial fixture categories:

1. `should_activate_shared_exact_phrase`
   - Contains at least two fit seed resumes with a shared exact technical phrase.
   - Expected result: PRF may pass if the model proposes the shared grounded phrase.

2. `should_fallback_no_safe_phrase`
   - Contains seeds without a safe shared phrase.
   - Expected result: empty candidates or rejected expressions; fallback is acceptable.

3. `should_reject_existing_query_term`
   - Contains a shared phrase already present in `existing_query_terms`, `sent_query_terms`, or `tried_term_family_ids`.
   - Expected result: policy must not accept that phrase.

4. `should_reject_single_seed_support`
   - Contains a phrase grounded in only one seed.
   - Expected result: policy rejects with `insufficient_seed_support`.

5. `should_handle_cjk_ascii_boundaries`
   - Contains mixed evidence such as `Langgraph框架`, `Multi-Agent 协作`, or `Agent Skills模块化`.
   - Expected result: legitimate exact phrase matches are not rejected as unsafe substrings.

## Validation Rules

Each fixture produces a per-case result with:

- case id;
- input fixture path;
- model id;
- prompt hash;
- latency;
- extraction status;
- candidate surfaces;
- grounding accepted count;
- candidate expressions;
- policy decision;
- blocker list;
- warning list.

Blockers:

- provider failure, timeout, empty/truncated invalid JSON, or schema validation exhaustion;
- accepted expression has `positive_seed_support_count < 2`;
- accepted expression belongs to existing, sent, or tried query family;
- accepted expression is company, location, school, degree, compensation, generic, or other high-risk rejected type;
- accepted expression cannot be traced back to deterministic raw evidence spans;
- accepted expression violates fixture expectation.

Warnings:

- model returns no candidates for an activation fixture;
- latency exceeds target but stays within configured timeout;
- all candidates are rejected but for expected deterministic reasons;
- model proposes only existing/tried terms and policy rejects them.

A fallback is not automatically a blocker. It is acceptable when the fixture has no safe shared phrase or when deterministic policy rejects unsafe proposals correctly.

## CLI / Invocation

Use this manual command shape:

```bash
uv run python -m seektalent.candidate_feedback.llm_prf_bakeoff \
  --live \
  --case-format llm-prf-input \
  --cases tests/fixtures/llm_prf_live_validation/cases.jsonl \
  --env-file /Users/frankqdwang/Agents/SeekTalent-0.2.4/.env \
  --output-dir artifacts/manual/llm-prf-live-validation
```

This keeps the existing `--live` guard, so real provider calls remain explicit. The key requirement is that this command remains manually invoked and never becomes part of normal product runtime.

## Output Artifacts

Write a compact summary JSON and JSONL case details under the provided output directory.

Required summary fields:

- `case_count`
- `passed_count`
- `blocker_count`
- `warning_count`
- `accepted_count`
- `fallback_count`
- `timeout_count`
- `schema_failure_count`
- `max_latency_ms`
- `p95_latency_ms` when enough cases exist
- `model_id`
- `prompt_hash`

Required per-case fields:

- `case_id`
- `expected_behavior`
- `status`
- `blockers`
- `warnings`
- `candidate_surfaces`
- `accepted_expression`
- `accepted_positive_seed_support_count`
- `accepted_negative_support_count`
- `reject_reasons`
- `latency_ms`

Do not store API keys. Do not store full raw provider responses unless a redaction path already exists and is explicitly reused.

## Pass Criteria Before 12-JD Benchmark

The live validation gate passes when:

- `blocker_count == 0`;
- no accepted expression has fewer than two positive seed resumes;
- no accepted expression repeats existing, sent, or tried query families;
- no accepted expression is a deterministic rejected entity or generic term;
- every accepted expression has deterministic grounding records;
- provider and schema failures are zero across the checked-in smoke fixture set.

Warnings do not block, but they must be visible in the summary. If an activation fixture falls back repeatedly, treat it as a product-quality concern before running the full benchmark.

## Code Boundary

Keep the code small:

- Prefer extending `llm_prf_bakeoff.py` over adding a new top-level subsystem.
- Reuse existing Pydantic models and policy functions.
- Add no runtime hooks in `WorkflowRuntime`.
- Add no new settings.
- Keep fixtures small and reviewable.

If implementation starts requiring broad changes outside `candidate_feedback`, tests, and fixture plumbing, stop and re-evaluate the design.

## Testing

Unit tests should cover:

- fixture loading and validation;
- blocker classification;
- fallback not counted as blocker for no-safe-phrase fixtures;
- single-seed accepted expression counted as blocker;
- existing/sent/tried family acceptance counted as blocker;
- summary aggregation.

Live validation itself remains manual because it calls a provider model.

## Rollout

1. Implement the harness and checked-in sanitized fixtures.
2. Run unit tests and lint.
3. Run the live validation harness manually.
4. If `blocker_count == 0`, run one complete JD smoke with eval disabled.
5. If smoke is acceptable, proceed to the 12-JD benchmark/eval.

## Acceptance Criteria

- A manual live validation command exists.
- The command reads checked-in sanitized `LLMPRFInput` fixtures.
- The command runs only the LLM PRF extractor, grounding, expression construction, and PRF policy chain.
- The command produces summary and per-case artifacts.
- The harness is not invoked by normal product runtime or benchmark unless manually requested.
- Unit tests cover blocker and summary logic.
- Documentation clearly says this is a pre-benchmark validation gate, not a product component.
