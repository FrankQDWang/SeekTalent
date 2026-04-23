# Run Latency Investigation Design

Date: 2026-04-23

## Context

SeekTalent run latency is now too high at the whole-run level. The first target is not a behavior change. The target is to rule out technical causes and make the latency budget visible enough that later changes can be justified by evidence.

Recent local artifacts suggest the main wall-clock pressure is not CTS retrieval. In recent runs, `tool_succeeded` latency is usually milliseconds to a few seconds. The larger waits are concentrated in LLM stages, especially controller and reflection. Some high-latency controller calls also show `validator_retry_count > 0`.

The current local config enables Bailian DeepSeek thinking for controller and reflection:

```env
SEEKTALENT_CONTROLLER_ENABLE_THINKING=true
SEEKTALENT_REFLECTION_ENABLE_THINKING=true
```

The code uses native strict structured output for `openai-chat:deepseek-v3.2`, but strict schema output only validates JSON shape and field types. The runtime also has local output validators that can reject structurally valid JSON when it violates business rules.

## Goals

- Attribute total `seektalent run` wall-clock time to stages.
- Separate technical latency causes from product/search-quality causes.
- Confirm whether controller/reflection thinking is the dominant technical cost.
- Explain each structured-output retry with a concrete local reason.
- Keep the first pass read-only where possible.
- Avoid changing retrieval semantics, ranking, scoring policy, or fallback chains during diagnosis.

## Non-Goals

- No model fallback chain.
- No broad prompt rewrite.
- No retry policy expansion.
- No quality-tuning change before latency attribution.
- No new dashboard or long-lived observability subsystem.
- No speculative caching layer.

## Current Hypotheses

### H1: Controller and reflection thinking dominate wall-clock time

Controller and reflection run serially across rounds. With thinking enabled, each call can take tens of seconds to minutes. Since each retrieval round includes controller and usually reflection, total run time scales directly with round count.

Evidence to confirm:

- controller/reflection latency share across recent `runs/*/events.jsonl`;
- same-JD A/B with thinking enabled vs disabled;
- per-call `input_payload_chars`, `output_chars`, and `validator_retry_count`.

### H2: Structured-output retries are local semantic retries, not native schema failures

The code uses `NativeOutput(..., strict=True)` for `openai-chat:deepseek-v3.2`. That still permits a local `output_validator` to raise `ModelRetry` after a valid structured object is parsed.

Known controller retry causes:

- `search_cts` without `proposed_query_terms`;
- query terms rejected by `canonicalize_controller_query_terms`;
- missing `response_to_reflection` when previous reflection exists.

Known finalizer retry causes:

- unknown resume id;
- duplicate resume id;
- candidate count mismatch;
- ranking order mismatch.

Recent artifacts show retries mostly in controller calls.

### H3: CTS is not the primary technical bottleneck

Recent `tool_succeeded` totals are small compared with controller/reflection and scoring LLM totals. CTS can still affect round count through poor recall, but current evidence does not point to CTS HTTP latency as the direct wall-clock bottleneck.

### H4: Scoring contributes to wall-clock through provider throughput

Scoring is parallelized with `SEEKTALENT_SCORING_MAX_CONCURRENCY=5`. The summed scoring latency can be high, but wall-clock impact depends on concurrency, provider queueing, and candidate count per round.

## Investigation Plan

### Step 1: Artifact-only latency audit

Read existing run artifacts and produce a compact table per run:

- run directory;
- rounds executed;
- stop reason;
- total observed duration from trace timestamps;
- total recorded latency by event type;
- controller total/max/count;
- reflection total/max/count;
- scoring sum/max/count;
- CTS tool total/max/count;
- finalizer latency;
- validator retry count by stage;
- whether rescue lanes fired.

Preferred inputs:

- `events.jsonl`
- `trace.log`
- `requirements_call.json`
- `rounds/round_*/controller_call.json`
- `rounds/round_*/reflection_call.json`
- `rounds/round_*/scoring_calls.jsonl`
- `finalizer_call.json`
- `run_summary.md`
- `search_diagnostics.json` when present

This step should not write runtime code.

### Step 2: Retry reason attribution

Inspect all calls where `validator_retry_count > 0`.

Current artifacts tell us a retry happened, but not which local validator branch triggered it. If existing traces are insufficient, make the smallest instrumentation change:

- add `last_validator_retry_reasons: list[str]` to controller and finalizer;
- append the exact retry reason before raising `ModelRetry`;
- write those reasons into `*_call.json`.

Do not change retry count or validator rules.

### Step 3: Controlled thinking A/B

Run the same JD under a small matrix:

| Variant | Controller Thinking | Reflection Thinking |
| --- | --- | --- |
| baseline | true | true |
| controller-off | false | true |
| reflection-off | true | false |
| both-off | false | false |

Keep all other variables fixed:

- same JD, notes, CTS credentials, model ids, round limits, and concurrency;
- `SEEKTALENT_ENABLE_EVAL=false`;
- same output root with variant names.

Collect:

- total wall-clock duration;
- rounds executed;
- controller/reflection latency totals;
- validator retry counts;
- final shortlist count and top score distribution for a rough quality sanity check.

### Step 4: Round-count contribution

If thinking is confirmed as a major cost, estimate how much total time is forced by round count:

- compare normal `MIN_ROUNDS=3` with a diagnostic `MIN_ROUNDS=1`;
- keep this as a diagnostic, not a product change;
- check whether early stop would have occurred and what candidate pool quality looked like.

### Step 5: Scoring throughput check

If controller/reflection is not enough to explain wall-clock time, run a narrow scoring concurrency check:

- `SCORING_MAX_CONCURRENCY=3`
- `SCORING_MAX_CONCURRENCY=5`
- `SCORING_MAX_CONCURRENCY=8`

Keep JD fixed. Watch for worse latency at higher concurrency, which would suggest provider-side queueing or throttling.

## Acceptance Criteria

- We can name the top two contributors to whole-run wall-clock time from local evidence.
- For every observed structured-output retry in the sampled runs, we can classify it as:
  - native schema/parse failure;
  - local controller semantic validator failure;
  - local finalizer semantic validator failure;
  - unknown, requiring instrumentation.
- We can say whether controller/reflection thinking is worth disabling for latency, based on same-JD A/B evidence.
- No runtime behavior changes are made before attribution is complete.

## Risks

- Same-JD live runs are noisy because provider latency varies by time of day.
- Disabling thinking may reduce controller/reflection quality, so latency wins need a later quality gate.
- Existing artifacts do not record exact validator retry reasons, so a small instrumentation pass may be required.

## Recommended First Execution

Start with Step 1 and Step 2. They are cheap, mostly read-only, and should answer whether the latency issue is primarily model thinking, retry amplification, round count, or scoring throughput.
