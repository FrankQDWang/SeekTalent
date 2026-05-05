# LLM PRF Mainline Cleanup And Validation Design

Date: 2026-05-05
Status: Draft for user review
Branch context: `codex/llm-prf-probe-mainline`

Supersedes:

- `docs/superpowers/specs/2026-05-05-llm-prf-live-validation-harness-design.md`

Related prior designs:

- `docs/superpowers/specs/2026-05-04-llm-prf-probe-mainline-design.md`
- `docs/superpowers/specs/2026-04-28-generic-explicit-phrase-extraction-for-prf-v1-5-design.md`
- `docs/superpowers/specs/2026-04-28-prf-model-sidecar-deployment-v0-1-design.md`

## Context

The product direction is settled: the typed second-lane `prf_probe` should use an LLM to propose common grounded phrases from top resumes, then let deterministic grounding and PRF policy decide whether any phrase is safe enough to search.

The previous LLM PRF mainline design kept the old PRF v1.5/sidecar/legacy regex paths as explicit backends. That was useful during transition, but it now creates maintenance and boundary confusion:

- runtime still carries `legacy_regex` and `sidecar_span` proposal backends;
- config and `.env.example` still expose sidecar/span knobs that are no longer the intended product path;
- LLM PRF currently reuses old `SourceField` semantics such as `evidence`, `matched_must_haves`, and `strengths`, even when the evidence now comes from normalized resume sections;
- live smoke testing exposed correctness bugs that should be fixed together with the cleanup, not layered on top of old compatibility code.

The repository is in productization. Experimental shortcuts and unused compatibility branches should be removed once they increase maintenance cost or regression risk.

## Goal

Make LLM PRF the only active proposal path for typed second-lane `prf_probe`, clean up old PRF v1.5/sidecar/legacy proposal code, add precise LLM PRF source provenance, and keep a focused live validation harness before running expensive 12-JD benchmark/eval.

The target behavior:

```text
round 2+ top-pool resumes
  -> compact normalized resume evidence
  -> DeepSeek V4 Flash LLM phrase proposals
  -> deterministic grounding against source_section evidence
  -> deterministic PRF policy
  -> prf_probe if safe, otherwise generic_explore
```

## Non-Goals

- Do not change the 70/30 typed second-lane allocation.
- Do not change exploit lane construction.
- Do not change CTS execution, scoring, controller, reflection, finalization, stopping policy, or eval policy.
- Do not remove or change the low-quality rescue `candidate_feedback` lane.
- Do not add a new product runtime service.
- Do not add domain dictionaries or industry-specific rule layers.
- Do not add model fallback chains.
- Do not retry provider/network/timeout failures.
- Do not run the 12-JD benchmark as part of this design step.

## Decisions

1. LLM PRF is the only typed second-lane PRF proposal path.
2. Remove `legacy_regex` and `sidecar_span` proposal backends from product runtime and config.
3. Remove PRF v1.5 sidecar/span/embedding proposal code once no active imports remain.
4. Preserve low-quality rescue `candidate_feedback`; it is separate active product behavior, not old PRF proposal compatibility.
5. Add `source_section` as LLM PRF source provenance and stop using old PRF `source_field` semantics on the LLM PRF main path.
6. Keep LLM candidate labels advisory only.
7. Keep deterministic grounding and `build_prf_policy_decision(...)` authoritative.
8. Keep structured-output retries limited to LLM answer parse/schema failures, with two retries as already requested.
9. Add a manual live validation harness that exercises only extractor, grounding, expression construction, and PRF policy.
10. Preserve read-only compatibility for historical PRF v1.5/sidecar artifacts without preserving active runtime selection.

## Cleanup Scope

Remove the old PRF proposal runtime surface:

- `PRFProbeProposalBackend` config and `SEEKTALENT_PRF_PROBE_PROPOSAL_BACKEND`;
- `legacy_regex` as a typed second-lane proposal backend;
- `sidecar_span` as a typed second-lane proposal backend;
- `prf_v1_5_mode` and `SEEKTALENT_PRF_V1_5_MODE`;
- `prf_model_backend` and `SEEKTALENT_PRF_MODEL_BACKEND`;
- sidecar runtime settings such as endpoint, profile, serve mode, batch limits, sidecar timeouts, bakeoff promotion, and pinned sidecar model settings;
- sidecar CLI entry points and Docker/compose files if they are not used by another active path;
- sidecar/span-specific artifacts such as `runtime.prf_sidecar_dependency_manifest`;
- sidecar/span-specific replay/eval snapshot fields after migration tests are updated;
- tests whose only purpose is preserving old sidecar/span/legacy proposal compatibility.

Remove or refactor old modules after active imports are gone:

- `seektalent.prf_sidecar.*`;
- `seektalent.candidate_feedback.proposal_runtime`;
- `seektalent.candidate_feedback.span_extractors`;
- `seektalent.candidate_feedback.span_models`;
- `seektalent.candidate_feedback.familying`, unless a small function is still needed and moved into the LLM PRF path with a simpler name.

Keep active shared behavior:

- `select_feedback_seed_resumes(...)`;
- deterministic expression classification used by PRF policy;
- `FeedbackCandidateExpression`;
- `build_prf_policy_decision(...)`;
- low-quality rescue `candidate_feedback` model/ranking behavior.

If `CandidateTermType` is still needed after removing `span_models`, move it to the active candidate feedback model layer instead of keeping `span_models` alive only for one type alias.

## Historical Compatibility

Deleting old proposal runtime must not make historical runs unreadable.

Active runtime rules:

- `WorkflowRuntime`, retrieval runtime, second-lane runtime, and settings must not import or select `legacy_regex` or `sidecar_span`.
- No active runtime setting may select old PRF v1.5, sidecar, span, embedding, or legacy regex proposal behavior.
- Old sidecar/span code must not remain as an inactive fallback chain behind LLM PRF failure.

Historical read rules:

- replay, evaluation, export, and audit readers must tolerate old PRF v1.5/sidecar/span metadata from existing run artifacts;
- if old Pydantic models are deleted, provide read-only legacy parsing models under a clearly named compatibility module such as `seektalent.legacy_artifacts`;
- read-only legacy models may parse fields such as sidecar endpoint contract version, dependency manifest hash, image digest, span model revision, embedding model revision, and familying metadata;
- read-only compatibility models must not expose runtime builders, clients, sidecar HTTP calls, model loading, or proposal execution.

This mirrors the cleanup principle used for other removed branches: active product code is deleted, but historical artifacts remain inspectable.

## LLM PRF Source Provenance

Add a dedicated LLM PRF source provenance field:

```python
LLMPRFSourceSection = Literal[
    "skill",
    "recent_experience_summary",
    "key_achievement",
    "raw_text_excerpt",
    "scorecard_evidence",
    "scorecard_matched_must_have",
    "scorecard_matched_preference",
    "scorecard_strength",
]
```

Use it in:

- `LLMPRFSourceText.source_section`;
- `LLMPRFSourceEvidenceRef.source_section`;
- `LLMPRFGroundingRecord.source_section`;
- LLM PRF input, candidates, grounding, and validation artifacts.

Do not keep `source_field` on the LLM PRF main path. Source identity becomes:

```text
source_text_id = stable_hash(
  resume_id,
  source_section,
  original_field_path,
  normalized_text,
  preparation_version,
)
```

Each source text also keeps `source_text_index` and `source_text_hash`, but index is not the primary identity. Index is for human readability and deterministic tie-breaks. Grounding resolution should prefer `source_text_id` and hash.

Each source text must include:

```python
class LLMPRFSourceText(BaseModel):
    resume_id: str
    source_section: LLMPRFSourceSection
    source_text_id: str
    source_text_index: int
    source_text_raw: str
    source_text_hash: str
    original_field_path: str
    support_eligible: bool
    hint_only: bool
    preparation_version: str
    dedupe_key: str
    rank_reason: str
```

Each source evidence reference must include `source_text_id`, `source_section`, `source_text_index`, and `source_text_hash`. The LLM may echo index for readability, but runtime resolution must use `source_text_id` and hash first.

`source_section` is provenance, not type or safety policy. It helps prompt tuning, artifact review, and harness diagnostics. It must not directly decide whether a phrase is accepted.

Support eligibility is explicit:

- acceptance support requires at least two distinct fit seed resumes with `support_eligible=true` grounding records;
- `hint_only=true` records may help the LLM propose, but cannot satisfy acceptance support;
- `scorecard_strength` must be `hint_only=true` and `support_eligible=false`.

Acceptance still depends on:

- deterministic raw grounding;
- support from at least two distinct fit seed resumes with support-eligible grounding;
- family-level existing/sent/tried conflict checks;
- deterministic type/entity/generic rejection;
- negative support;
- PRF policy.

## Resume Evidence Input

Build LLM PRF input primarily from `NormalizedResume`, not from scorecard prose.

Preferred normalized resume sections:

- `skills` -> `source_section="skill"`;
- `recent_experiences[].summary` -> `source_section="recent_experience_summary"`;
- `key_achievements` -> `source_section="key_achievement"`;
- `raw_text_excerpt` -> `source_section="raw_text_excerpt"`.

Do not directly feed name, current company, location, education, or profile metadata into LLM PRF source texts. Those fields are high-risk for company/location/school leakage and are not the common capability material this PRF lane is trying to discover.

Source text preparation should stay simple and deterministic:

- normalize whitespace;
- drop empty or very short noise fragments;
- drop or mask person names when available;
- drop standalone company/current-employer fields;
- reject snippets dominated by company, location, school, title, department, or profile metadata;
- keep technical product/platform mentions only when local context indicates capability or tool usage rather than employer identity;
- deduplicate per resume;
- rank snippets by overlap with role title, must-have capabilities, retrieval query terms, and already-active query terms;
- cap source texts per seed resume;
- cap negative source texts per negative resume;
- cap characters per source text;
- keep raw text, source text id, and stable hash for grounding;
- record `preparation_version`, sanitizer version, dropped reason counts, dedupe key, and rank reason in the input artifact.

The sanitizer should be deterministic and conservative. It is not a broad domain dictionary or NER system. Its job is to prevent obvious leakage of person/company/location/school/title metadata into LLM PRF proposal input before the downstream deterministic classifier and PRF gate run.

If a normalized resume is unavailable, a bounded scorecard fallback may be used:

- `evidence` -> `scorecard_evidence`;
- `matched_must_haves` -> `scorecard_matched_must_have`;
- `matched_preferences` -> `scorecard_matched_preference`;
- `strengths` -> `scorecard_strength`.

`scorecard_strength` is `hint_only=true` and `support_eligible=false`. A phrase accepted by PRF policy must have support-eligible grounding in at least two distinct seed resumes.

## Prompt Work

The prompt is part of this cleanup because the previous live smoke showed model output quality problems.

The PRF phrase proposal prompt must:

- include `source_section` in the source text payload;
- ask for at most four candidates;
- ask for phrases visible in at least two distinct fit seed resumes;
- require `candidate.surface` to be copied from referenced source text;
- prefer short exact shared phrases over synthesized descriptions;
- tell the model to return an empty candidate list when no safe shared phrase exists;
- prohibit existing query terms, sent query terms, and tried families;
- prohibit company, location, school, degree, compensation, administrative, generic, and title-only terms;
- keep rationale short because it is diagnostic only.

Prompt changes should be tested through fake-output unit tests plus the manual live harness, not by adding more rule extraction.

## Structured Output

The `prf_probe_phrase_proposal` stage must use prompted JSON or an equivalent explicit plain-JSON parse path. It must not use provider-native strict schema or tool output for Bailian-hosted DeepSeek V4.

Required behavior:

- model id defaults to `deepseek-v4-flash`;
- temperature is deterministic, effectively `0`;
- runtime timeout defaults to `3.0` seconds;
- manual live validation may use a separate `30.0` second timeout;
- max output tokens defaults to `2048`;
- output candidate cap is `4`;
- `output_retries=2` applies only to empty raw provider response body, invalid JSON, truncated JSON, or schema-invalid JSON;
- a schema-valid response with `candidates=[]` is a successful no-proposal extractor result and must not trigger structured-output retries;
- provider, transport, timeout, and capability failures call the model once and fall back to `generic_explore`.

The extractor must record a deterministic failure kind for:

- insufficient PRF seed support;
- timeout;
- transport/provider failure;
- structured-output parse/schema exhaustion;
- no safe LLM PRF expression after a successful empty or fully rejected extraction;
- unsupported capability or settings migration error.

## Grounding And Policy Fixes

Fix and preserve the following LLM PRF correctness behavior:

- If fewer than two high-quality seed resumes exist, skip the LLM call and record `insufficient_prf_seed_support`.
- A candidate expression with positive support from fewer than two seed resumes must be rejected.
- Existing query terms, sent query terms, and tried term families must be rejected at family level.
- LLM candidate type labels and risk flags are advisory only.
- Runtime deterministic classification remains authoritative.
- Grounding must support raw match and NFKC/casefold match through an offset map back to raw source text.
- CJK/ASCII adjacent text such as `Langgraph框架` and `Multi-Agent 协作` must not be rejected merely because the neighboring character is CJK.
- Unsafe substring examples such as `Java` from `JavaScript` remain rejected.
- Negative support is computed by deterministic scan over negative source texts, not by LLM judgment.

Grounding failure is not an output retry condition. It is a rejected unsafe proposal.

## Conservative Familying

Family-level behavior must remain active after deleting the old sidecar/span familying module.

Move the minimal conservative familying implementation into `candidate_feedback.llm_prf` or a small focused module such as `candidate_feedback.llm_prf_familying`.

Keep support and conflict checks at family level for:

- positive seed support;
- negative support;
- existing query-term conflicts;
- sent query-term conflicts;
- tried-family conflicts;
- unsafe substring protection.

The active LLM PRF path must still treat safe surface variants as one family, for example:

- `Flink CDC`;
- `flink-cdc`;
- `FlinkCDC`.

It must not use broad embedding similarity for semantic merges. Delete embedding-backed broad familying and sidecar-specific familying surfaces, not family-level conflict behavior itself.

## Runtime Behavior

The round-level behavior remains:

```text
exploit lane always builds from controller retrieval plan
LLM PRF proposal attempts bounded phrase proposal when enough seeds exist
PRF policy chooses at most one accepted expression
accepted expression -> prf_probe second lane
no accepted expression or any LLM failure -> generic_explore second lane
```

LLM PRF failure must not fail the round. It only controls whether the 30% typed second lane becomes `prf_probe` or `generic_explore`.

Do not run old proposal fallback chains after LLM failure. Failure fallback is `generic_explore`, not `legacy_regex`, not `sidecar_span`, and not another model.

## Artifacts And Replay

Keep logical LLM PRF artifacts:

- `round.XX.retrieval.llm_prf_input`;
- `round.XX.retrieval.llm_prf_call`;
- `round.XX.retrieval.llm_prf_candidates`;
- `round.XX.retrieval.llm_prf_grounding`;
- `round.XX.retrieval.prf_policy_decision`;
- `round.XX.retrieval.second_lane_decision`.

LLM PRF artifacts must include:

- schema version;
- extractor version;
- prompt name and prompt hash;
- grounding validator version;
- model id;
- protocol family;
- endpoint kind and region;
- structured-output mode;
- output retry count;
- failure kind when applicable;
- `source_section` provenance for input and grounding records.

Remove sidecar/span-specific artifact and replay metadata once the old path is deleted.

It is acceptable to keep a constant metadata value such as `prf_probe_proposal_backend="llm_deepseek_v4_flash"` in diagnostic snapshots if existing evaluation reports need that label, but it should not remain a runtime configuration knob.

Read-only historical replay metadata is the exception. Old sidecar/span fields may remain in compatibility readers, but they must not be present as active runtime settings or active runtime builders.

## Live Validation Harness

Keep the live validation harness, but scope it to the cleaned LLM PRF path.

The manual command should exercise only:

```text
LLMPRFExtractor
  -> ground_llm_prf_candidates
  -> feedback_expressions_from_llm_grounding
  -> build_prf_policy_decision
  -> validation summary
```

Fixtures should be checked-in sanitized `LLMPRFInput` wrappers under:

```text
tests/fixtures/llm_prf_live_validation/cases.jsonl
```

Fixture categories:

1. shared exact phrase should activate;
2. no safe phrase should fall back;
3. existing/sent/tried family should be rejected;
4. single-seed support should be rejected;
5. mixed CJK/ASCII phrase should ground correctly.

The harness must not run in CI by default and must not store API keys, full raw resumes, or unredacted provider payloads.

There are two validation layers:

1. CI-safe deterministic tests with fake extractor outputs.
2. Manual live provider validation using checked-in sanitized `LLMPRFInput` fixtures.

The deterministic layer must have zero blockers.

The live layer should report provider failures separately from product blockers. Network, API key, provider outage, and rate-limit failures should not be counted as grounding/schema/product blockers, but they do block treating that live validation run as evidence of model quality.

Live pass criteria before running the 12-JD benchmark:

- `blocker_count == 0`;
- `provider_failure_count == 0` for the validation run being used as benchmark gate evidence;
- no accepted expression has fewer than two positive seed resumes;
- no accepted expression repeats existing/sent/tried query families;
- no accepted expression is a deterministic rejected entity or generic term;
- every accepted expression has deterministic grounding records;
- schema failures are zero across the checked-in smoke fixture set.

Activation fixtures must label whether fallback is a blocker or warning. Repeated fallback on activation fixtures is at least a product-quality warning; it becomes a blocker when the fixture says a safe shared phrase is expected.

## Configuration And Env

After cleanup, `.env` and `.env.example` should expose only active LLM PRF settings, with Chinese comments:

- `SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MODEL_ID=deepseek-v4-flash`;
- `SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_REASONING_EFFORT=off`;
- `SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_TIMEOUT_SECONDS=3.0`;
- `SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_LIVE_HARNESS_TIMEOUT_SECONDS=30.0`;
- `SEEKTALENT_PRF_PROBE_PHRASE_PROPOSAL_MAX_OUTPUT_TOKENS=2048`.

Remove old PRF v1.5/sidecar/span/legacy backend settings from both files. If an old setting is still required by a test after cleanup, the implementation plan must explain why it remains active. Otherwise it should be deleted.

Removed PRF sidecar/span/legacy settings in process env or `.env` must not be silently ignored. Settings loading must include an explicit stale-config scanner. If it finds removed settings such as `SEEKTALENT_PRF_PROBE_PROPOSAL_BACKEND`, `SEEKTALENT_PRF_V1_5_MODE`, `SEEKTALENT_PRF_MODEL_BACKEND`, sidecar endpoint/profile/timeouts, sidecar model pins, or sidecar bakeoff promotion flags, settings validation should fail with a migration error that names the stale keys and tells the operator to remove them.

## Testing

Unit tests should cover:

- LLM PRF input uses `source_section`, not old `source_field`;
- normalized resume source selection uses skills, recent experience summaries, key achievements, and raw excerpts;
- scorecard fallback maps to explicit scorecard source sections;
- `scorecard_strength` is hint-only, support-ineligible, and cannot satisfy acceptance support by itself;
- source references resolve by `source_text_id` and hash, with index only as human-readable tie-break;
- source preparation records sanitizer version, dropped reason counts, original field path, dedupe key, and rank reason;
- company/location/school/title-dominated snippets are filtered or marked ineligible before LLM PRF input;
- raw/NFKC/casefold grounding with raw offset recovery;
- mixed CJK/ASCII boundary acceptance;
- unsafe substring rejection;
- conservative familying for `Flink CDC` / `flink-cdc` / `FlinkCDC` remains active;
- single-seed support rejection;
- existing/sent/tried family rejection;
- provider failure is not retried;
- structured-output schema failure gets exactly two output retries;
- schema-valid `candidates=[]` does not trigger output retries;
- timeout falls back to `generic_explore`;
- stale removed PRF env keys fail settings validation with a migration error;
- read-only legacy artifact parsing tolerates old sidecar/span metadata;
- low-quality rescue `candidate_feedback` behavior remains unchanged;
- no runtime branch can select `legacy_regex` or `sidecar_span` after cleanup;
- active import graph has no forbidden old proposal imports in runtime/config/orchestrator/second-lane code;
- `.env.example` no longer documents removed PRF sidecar settings;
- live validation summary and blocker classification.

Add an active import graph check over `src/seektalent` and tests for removed terms:

```text
legacy_regex
sidecar_span
prf_v1_5_mode
prf_model_backend
prf_sidecar
span_extractors
span_models
proposal_runtime
```

Allowed references:

- read-only legacy artifact parsers;
- stale-config scanner definitions and tests;
- docs and migration messages.

Forbidden references:

- active runtime selection;
- `WorkflowRuntime`;
- retrieval runtime;
- second-lane runtime;
- active settings fields;
- active proposal builders.

Run focused regression before any full benchmark:

```bash
uv run pytest tests/test_llm_prf.py tests/test_candidate_feedback.py tests/test_runtime_state_flow.py tests/test_llm_provider_config.py tests/test_llm_prf_bakeoff.py
uv run ruff check src tests
```

Then run the live PRF validation harness. Only after that should the 12-JD benchmark/eval be considered.

## Rollout

1. Update the plan from this spec before touching implementation.
2. Add or adjust tests for cleaned source provenance and old backend removal.
3. Implement LLM PRF bug fixes already identified by live smoke.
4. Remove old sidecar/span/legacy proposal runtime, config, env, docs, and tests.
5. Add the live validation harness and fixtures.
6. Run unit/lint regression.
7. Run live validation harness.
8. If validation passes, run one full JD smoke with eval disabled.
9. If PRF activates on realistic samples and no product tradeoff appears, run the 12-JD benchmark/eval for version `0.6.2`.

## Acceptance Criteria

- LLM PRF is the only typed second-lane PRF proposal path.
- Old PRF v1.5/sidecar/legacy proposal config is removed from settings and env files.
- Old sidecar/span proposal modules are deleted or no longer importable from active product paths.
- Low-quality rescue `candidate_feedback` remains unchanged.
- LLM PRF artifacts use `source_section` provenance instead of old `source_field`.
- LLM PRF source texts include explicit `support_eligible`, `hint_only`, `source_text_id`, original field path, preparation version, dedupe key, and rank reason.
- LLM PRF input primarily uses normalized resume evidence and avoids direct company/location/education/name metadata.
- Deterministic source-text sanitizer records versioned filtering metadata and prevents obvious company/location/school/title leakage before LLM proposal.
- Prompt and schema are capped enough to avoid routine truncation.
- Runtime PRF proposal timeout defaults to `3.0` seconds; manual live validation has a separate `30.0` second timeout.
- Structured-output failures get two retries; provider/network/timeout failures do not.
- Schema-valid `candidates=[]` is a successful no-proposal result, not a structured-output retry condition.
- Accepted expressions require at least two distinct fit seed resumes with deterministic grounding.
- Existing/sent/tried family conflicts are rejected.
- Conservative familying remains active on the LLM PRF path after old sidecar/span familying code is removed.
- Removed PRF env settings fail settings validation with a clear migration error.
- Historical PRF v1.5/sidecar/span artifacts remain readable through read-only compatibility models.
- Active import graph checks prove old proposal backends are not selectable from product runtime.
- Live validation harness exists and is manually runnable.
- Focused tests and lint pass before running any full benchmark.
