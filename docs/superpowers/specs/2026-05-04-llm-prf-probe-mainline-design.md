# LLM PRF Probe Mainline Design

## Summary

Replace the current rule-heavy PRF candidate-expression proposal for the 30% typed second lane with a DeepSeek V4 Flash LLM extractor. The LLM proposes grounded candidate phrases from high-quality top-pool resumes, but deterministic runtime validation and the existing PRF gate remain the only acceptance boundary.

This change targets the `prf_probe` second lane, not the low-quality rescue `candidate_feedback` lane.

## Motivation

The existing `prf_probe` route is the 30% side of the round 2+ typed second-lane search budget. Its runtime shape is already right:

- run one `exploit` lane from the controller query;
- run one second lane using `prf_probe` when PRF has a safe expression;
- fall back to `generic_explore` when PRF has no safe expression;
- keep 70/30 fetch allocation in retrieval execution.

The weak part is candidate-expression proposal. The current rule/regex path in `candidate_feedback/extraction.py` is brittle across domains, especially for Chinese and mixed-language resumes. Maintaining domain rules, phrase lists, or industry-specific dictionaries is not viable because JD and resume domains vary widely.

The intended product behavior is general pseudo relevance feedback:

1. inspect the best scored resumes;
2. extract explicit common query material;
3. search the 30% side lane with one safe expansion phrase;
4. avoid free-form query rewriting and domain-specific rule growth.

## Decisions

1. Use DeepSeek V4 Flash for PRF phrase proposal.
2. Make the LLM extractor the direct mainline source for `prf_probe` candidate expressions.
3. Keep extraction strictly grounded in top-pool seed resume evidence.
4. Keep deterministic grounding validation and `build_prf_policy_decision(...)` as the final acceptance boundary.
5. If LLM extraction fails or no safe candidate survives, fall back to `generic_explore`.
6. Use `output_retries=2` for structured-output parse/schema failures, matching the repository's structured-output retry discipline.
7. Add both deterministic CI harness coverage and a non-CI live DeepSeek bakeoff harness.

## Current State

### Typed second lane

The active 70/30 path lives in:

- `src/seektalent/runtime/second_lane_runtime.py`
- `src/seektalent/runtime/retrieval_runtime.py`
- `src/seektalent/runtime/orchestrator.py`

For round 2+, runtime builds:

- an `exploit` logical query from the controller retrieval plan;
- a second logical query from `build_second_lane_decision(...)`.

If PRF gate passes, the second query is `prf_probe`. Otherwise it is `generic_explore`.

### Existing PRF proposal

The current PRF policy input is produced by:

- selecting seed resumes with `select_feedback_seed_resumes(...)`;
- extracting candidate expressions from structured scoring fields with regex/rule logic;
- passing those expressions into `build_prf_policy_decision(...)`.

The gate is useful and should remain. The proposal layer is the part being replaced.

### Low-quality rescue lane

The low-quality rescue lane still exists in:

- `src/seektalent/runtime/rescue_router.py`
- `src/seektalent/runtime/rescue_execution_runtime.py`

That path is separate from the 70/30 typed second lane. This design does not remove or change it.

## Design

### 1. Runtime boundary

Keep the current round-level retrieval shape:

```text
controller query
  -> exploit lane
  -> LLM PRF proposal
  -> grounding validation
  -> deterministic PRF gate
  -> prf_probe if safe else generic_explore
  -> 70/30 search execution
```

The LLM extractor only replaces candidate-expression proposal. It must not own:

- 70/30 lane allocation;
- CTS query execution;
- scoring;
- second-lane fallback selection;
- deterministic acceptance policy.

### 2. LLM extractor

Add a focused PRF LLM extractor boundary under `seektalent.candidate_feedback`:

- `llm_prf.py`
- `llm_prf_bakeoff.py`

The extractor uses canonical text-LLM stage resolution with `stage="candidate_feedback"`.

The intended default model is:

```text
candidate_feedback_model_id = deepseek-v4-flash
```

This design assumes the OpenAI-default restoration work has made that the repository default. If this work lands before that branch, implementation must either merge that default first or explicitly set the same default as part of the PRF LLM change.

The extractor should use a new schema for phrase proposals, not the current dormant `CandidateFeedbackModelRanking` shape. The current ranking helper ranks already-generated terms; this feature needs proposals from seed evidence.

### 3. Input contract

The LLM input should be compact and replayable.

Include:

- `role_title`
- `role_summary`
- `must_have_capabilities`
- current round `retrieval_plan.query_terms`
- existing active/inactive query terms
- sent query terms and tried term families
- up to five high-quality seed resumes from `select_feedback_seed_resumes(...)`
- negative resumes only as compact evidence used to avoid noisy shared phrases

For each seed resume, include bounded structured fields:

- `evidence`
- `matched_must_haves`
- `matched_preferences`
- `strengths`

`strengths` may guide proposal, but it must not be the only grounding source for an accepted phrase because it is derived scoring prose.

Raw full resumes are out of scope for this change.

### 4. Output contract

The LLM returns candidate phrase proposals, not final search query terms.

Each candidate should include:

- `surface`
- `normalized_surface`
- `candidate_term_type`
- `source_evidence_refs`
- `source_resume_ids`
- `linked_requirements`
- `rationale`
- `risk_flags`

`candidate_term_type` should support the current PRF policy vocabulary:

- `skill`
- `tool_or_framework`
- `product_or_platform`
- `technical_phrase`
- `responsibility_phrase`
- `company_entity`
- `location`
- `degree`
- `compensation`
- `administrative`
- `generic`
- `unknown_high_risk`
- `unknown`

The extractor must be prompted to:

- return only phrases visible in seed evidence;
- prefer common phrases supported by multiple fit seed resumes;
- avoid company, location, school, degree, salary, age, title-only, and generic boilerplate phrases;
- avoid rewriting the query;
- avoid inventing implied capabilities that do not appear in seed evidence.

### 5. Grounding validation

Runtime must not trust the LLM's candidate list directly.

For each candidate:

1. Find `surface` or `normalized_surface` in the referenced seed evidence text after deterministic whitespace and Unicode normalization.
2. Align the accepted surface back to concrete source text.
3. Record aligned source field, source text index, start/end offsets, raw surface, normalized surface, and resume id.
4. Reject unaligned candidates with a clear reason such as `non_extractive_or_unmatched_surface`.

Acceptance eligibility requires:

- support from at least two seed resumes;
- support from non-`strengths` fields;
- no existing query term, sent query term, or tried term family conflict;
- no high negative-support signal;
- no rejected entity/filter/generic type.

Grounding failures do not trigger LLM retries. They are unsafe candidate outputs, not structured-output failures.

### 6. PRF gate integration

Convert grounded LLM candidates into `FeedbackCandidateExpression` objects and pass them through `build_prf_policy_decision(...)`.

The existing deterministic gate remains responsible for:

- minimum seed support;
- negative-support rejection;
- tried-family rejection;
- company/entity rejection;
- strengths-only rejection;
- responsibility-phrase shadow-only rejection;
- selecting at most one accepted expression.

If one expression survives, `build_second_lane_decision(...)` produces `prf_probe` as today.

If no expression survives, second lane falls back to `generic_explore` as today.

### 7. Error handling

Use the same disciplined failure behavior as the rest of the codebase:

- transport/network/provider failure: no retry chain; record failure and fall back to `generic_explore`;
- structured-output parse/schema validation failure: allow `output_retries=2`;
- exhausted structured-output retries: record `llm_prf_structured_output_failed` and fall back to `generic_explore`;
- grounding failure: reject the candidate, do not retry the model;
- PRF gate rejection: reject the candidate, do not retry the model;
- all candidates rejected: record `no_safe_llm_prf_expression` and fall back to `generic_explore`.

The LLM path must never block the `exploit` lane or the current round's search.

### 8. Artifacts

Persist enough data to diagnose and replay the PRF decision.

Required artifacts:

- `round.XX.retrieval.llm_prf_input`
- `round.XX.retrieval.llm_prf_call`
- `round.XX.retrieval.llm_prf_candidates`
- `round.XX.retrieval.llm_prf_grounding`
- `round.XX.retrieval.prf_policy_decision`
- `round.XX.retrieval.second_lane_decision`

`llm_prf_candidates` should preserve raw LLM candidates.

`llm_prf_grounding` should preserve candidate-level validation status and reject reasons.

`prf_policy_decision` remains the final deterministic acceptance artifact.

`second_lane_decision` remains the routing artifact.

### 9. Live bakeoff harness

Add a non-CI harness that can run real DeepSeek V4 Flash extraction on fixed slices.

The harness should:

- require explicit API configuration;
- never run in CI by default;
- use fixed sanitized JD/seed slices for English, Chinese, and mixed-language cases;
- write raw candidate proposals, grounding results, PRF gate results, accepted expression, fallback reason, and metrics;
- make model quality inspectable without changing runtime code.

Primary blocker conditions:

- accepted non-extractive phrase;
- accepted company/entity/location/degree/salary leakage;
- accepted generic boilerplate;
- accepted phrase supported by fewer than two seed resumes;
- accepted phrase grounded only in `strengths`.

Primary metrics:

- accepted phrase precision;
- grounding pass rate;
- structured-output failure rate;
- no-safe-expression rate;
- generic fallback rate;
- blocker count;
- per-language slice pass/fail counts.

Because this rollout is direct mainline, the harness is not a gate that delays implementation. It is the operational tool for proving the behavior on real model calls and catching regressions before broader evaluation runs.

### 10. CI harness

CI tests should use fake LLM outputs and deterministic fixtures.

Required fixture coverage:

- English technical phrase;
- Chinese technical phrase;
- mixed Chinese-English phrase;
- valid grounded candidate accepted into `prf_probe`;
- all rejected candidates falling back to `generic_explore`;
- unmatched LLM surface rejected;
- single-seed support rejected;
- strengths-only grounding rejected;
- existing/sent/tried term rejected;
- company, location, degree, salary, and generic boilerplate rejected;
- structured-output failure attempts two output retries before fallback;
- artifacts contain input, call, candidates, grounding, PRF policy, and second-lane decision refs.

## Non-Goals

Do not change:

- 70/30 second-lane budget allocation;
- exploit lane behavior;
- CTS query execution;
- scoring, finalization, controller, reflection, or judge behavior;
- low-quality rescue `candidate_feedback` lane;
- PRF sidecar deployment;
- PRF embedding sidecar behavior;
- top-pool scoring policy;
- stopping policy.

Do not add:

- maintained domain vocabularies;
- industry dictionaries;
- company knowledge bases;
- broad ontology layers;
- LLM free-form query rewriting;
- fallback model chains;
- network retry scaffolding beyond the existing structured-output retry exception.

## Acceptance Criteria

This design is complete when implementation can prove:

1. round 2+ second-lane PRF candidate proposal uses DeepSeek V4 Flash LLM extraction;
2. accepted `prf_probe` expressions are grounded in seed resume evidence;
3. LLM output never directly becomes a query term without deterministic validation and PRF gate acceptance;
4. unsafe, ungrounded, or unsupported candidates fall back to `generic_explore`;
5. structured-output/schema failures use two output retries before fallback;
6. CI covers English, Chinese, and mixed-language deterministic fixtures;
7. live bakeoff can call the real model and emit quality metrics;
8. existing 70/30 lane allocation remains unchanged;
9. low-quality rescue behavior remains unchanged.
