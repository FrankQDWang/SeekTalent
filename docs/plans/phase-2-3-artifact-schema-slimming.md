# Task: Phase 2.3 Full-Chain Schema and Artifact Slimming

## Goal

Split Phase 2.3 into three independently gated stages:

1. Phase 2.3A Artifact Slimming Only.
2. Phase 2.3B Reflection + Finalizer Draft Slimming.
3. Phase 2.3C Scoring Schema Experiment.

This replaces the obsolete controller/reflection-only Phase 2.3 plan. The implementation work happens later; this plan defines the staged execution contract.

The phase target is to reduce run artifact size, reduce duplicated JSON, and reduce model-facing structured-output pressure without changing retrieval strategy, CTS filters, stop guidance, ranking semantics, eval/judge semantics, or business-readable trace.

## Non-Goals

- Do not implement code in this doc-only rewrite.
- Do not preserve old artifact compatibility unless a later milestone deliberately chooses it.
- Do not change retrieval policy, query compiler behavior, CTS clients, stop guidance, ranking sort key, eval/judge semantics, or W&B/Weave semantics as part of slimming.
- Do not collapse 2.3A, 2.3B, and 2.3C into one implementation/replay gate.
- Do not use no-eval replay alone to accept scoring schema removal.
- Do not add fallback model chains, retry chains, or generic recovery logic.

## Done Criteria

- `docs/plans/roadmap.md` and this plan agree on the new Phase 2.3 scope.
- The old active `phase-2-3-controller-reflection-schema-slimming.md` plan is removed or superseded.
- The plan clearly separates:
  - artifact persistence slimming;
  - model-facing context/schema slimming;
  - scoring schema experiment.
- The Phase 2.2.2 artifact-size baseline is recorded.
- Acceptance gates cover size, call artifact metadata, final public shape, eval/cached judge for scoring, and business-readable trace.

## Repo Entrypoints

Read first:

1. `AGENTS.md`
2. `README.md`
3. `docs/plans/roadmap.md`
4. `docs/outputs.md`
5. `src/seektalent/tracing.py`
6. `src/seektalent/runtime/orchestrator.py`
7. `src/seektalent/models.py`
8. `src/seektalent/reflection/critic.py`
9. `src/seektalent/finalize/finalizer.py`
10. `src/seektalent/scoring/scorer.py`
11. `tests/test_runtime_audit.py`
12. `tests/test_reflection_contract.py`
13. `tests/test_finalizer_contract.py`

Allowed during this doc-only rewrite:

- `docs/plans/roadmap.md`
- `docs/plans/phase-2-3-artifact-schema-slimming.md`
- deletion of `docs/plans/phase-2-3-controller-reflection-schema-slimming.md`

Do not edit during this doc-only rewrite:

- `src/`
- `tests/`
- `docs/outputs.md`
- prompts
- benchmark artifacts
- generated `runs/`

## Current Reality

- The old Phase 2.3 plan only covers controller/reflection schema slimming and explicitly excludes scorer/finalizer schemas.
- The desired scope is now full-chain slimming, but split by risk.
- `LLMCallSnapshot` currently persists full `user_payload` and full `structured_output`.
- Runtime also writes full controller/reflection/finalizer contexts separately, causing duplicated persisted JSON.
- `normalized_resumes.jsonl` duplicates normalized resumes that also exist under `resumes/{resume_id}.json`.
- `top_pool_snapshot.json` captures current global top pool and should be slimmed, not deleted, unless another canonical global-top-pool artifact replaces it.
- Finalizer currently asks the model for the full public `FinalResult`, although rank, score, fit bucket, source round, and many candidate signals are runtime-owned.
- Scoring schema fields such as `evidence`, `confidence`, `strengths`, and `weaknesses` can affect recruiter-visible explanation quality and need a stricter gate than no-eval replay.

Baseline artifact sizes from the existing Phase 2.2.2 no-eval runs, counting JSON/JSONL/MD artifacts:

- `agent_jd_004`: `834,571` bytes.
- `agent_jd_007`: `2,028,921` bytes.

Largest artifact classes observed in that baseline:

- `scoring_calls.jsonl`
- `reflection_call.json`
- `reflection_context.json`
- `normalized_resumes.jsonl`
- `events.jsonl`
- `finalizer_call.json`
- `finalizer_context.json`
- `top_pool_snapshot.json`
- `scorecards.jsonl`

## Target Behavior

### Phase 2.3A Artifact Slimming Only

Reduce persisted JSON duplication without changing model prompts, model output schemas, retrieval behavior, scoring behavior, finalization behavior, or eval behavior.

Planned contract:

- `LLMCallSnapshot` becomes metadata-only.
- Call artifacts do not persist full `user_payload` or full `structured_output`.
- Call artifacts include:
  - `input_artifact_refs`
  - `output_artifact_refs`
  - `input_payload_sha256`
  - `structured_output_sha256`
  - `prompt_chars`
  - `input_payload_chars`
  - `output_chars`
  - `input_summary`
  - `output_summary`
- `controller_call.json`, `reflection_call.json`, `finalizer_call.json`, and `scoring_calls.jsonl` point to inputs/outputs instead of embedding them.
- `normalized_resumes.jsonl` becomes `scoring_input_refs.jsonl`, because full normalized resumes already exist per resume.
- `top_pool_snapshot.json` becomes a slim global top-pool snapshot containing only resume id and ranking/sort-key facts.
- `events.jsonl` payloads are metadata-only or capped to small metrics.
- `docs/outputs.md` must be updated during the 2.3A implementation, not in the doc-only rewrite.

### Phase 2.3B Reflection + Finalizer Draft Slimming

Reduce model-facing output schemas where runtime already owns or can safely materialize the removed fields.

Planned contract:

- Reflection removes prose assessment fields from draft and persisted advice:
  - `strategy_assessment`
  - `quality_assessment`
  - `coverage_assessment`
- Reflection removes keyword/filter `critique` fields.
- Reflection persists structured advice plus runtime-built `reflection_summary`.
- `round_review.md` shows one business-readable reflection summary and the continue/stop recommendation.
- `round_review.md` does not show `Strategy assessment`, `Quality assessment`, or `Coverage assessment`.
- Finalizer introduces `FinalResultDraft` / `FinalCandidateDraft`.
- The model-facing finalizer draft contains only top-level `summary` and per-candidate `resume_id`, `match_summary`, and `why_selected`.
- Runtime materializes the existing public `FinalResult`.
- Public `FinalCandidate` shape remains unchanged in 2.3B.
- Missing/negative signals are not new public fields in 2.3B; if needed, they must be mapped into existing public fields only by an explicit later change.

Finalizer validator contract must stay hard:

- no unknown resume ids;
- no duplicate resume ids;
- every runtime top candidate must be included;
- runtime ranking order must be preserved;
- candidate count must remain unchanged.

### Phase 2.3C Scoring Schema Experiment

Treat scoring schema slimming as an experiment because it can change ranking explanation quality and recruiter-visible output quality.

Fields under review:

- `evidence`
- `confidence`
- `strengths`
- `weaknesses`

Before removing any scoring field:

- Define how public `strengths` and `weaknesses` are generated.
- Decide whether removed fields are derived by runtime, mapped from remaining fields, or removed from downstream display.
- Run eval/cached judge acceptance, not only no-eval replay.

## Milestones

### M0. Documentation Rewrite

Steps:

- Update `docs/plans/roadmap.md` so Phase 2.3 is full-chain slimming split into 2.3A/2.3B/2.3C.
- Replace the obsolete controller/reflection-only plan with this staged plan.
- Do not edit code, tests, prompts, `docs/outputs.md`, benchmark artifacts, or generated runs.

Acceptance:

- Planning docs no longer contradict each other.
- The old controller/reflection-only plan is not active.

Validation:

```bash
git diff -- docs/plans/roadmap.md docs/plans/phase-2-3-artifact-schema-slimming.md
git status --short
```

Expected:

- Only planning docs changed.

### M1. Execute Phase 2.3A Artifact Slimming Only

Steps:

- Record current artifact size baseline for overlap rows.
- Make call snapshots metadata-only.
- Replace duplicate full artifact dumps with refs, hashes, char counts, and short summaries.
- Convert `normalized_resumes.jsonl` to `scoring_input_refs.jsonl`.
- Slim `top_pool_snapshot.json` instead of deleting it.
- Cap `events.jsonl` payloads.
- Update `docs/outputs.md` to document the new artifact contract.

Acceptance:

- No call artifact contains full `user_payload`.
- No call artifact contains full `structured_output`.
- No slim context artifact contains full JD, full notes, full normalized resume, or full scored candidate dumps.
- `input_payload_sha256`, `structured_output_sha256`, `prompt_chars`, `input_payload_chars`, and `output_chars` exist per LLM call.
- JSON/JSONL/MD total size drops at least 40% on overlapping baseline rows.
- Business-readable markdown trace still explains each round.

Validation:

```bash
uv run pytest tests/test_runtime_audit.py tests/test_llm_lifecycle.py tests/test_ui_api.py
```

Run no-eval acceptance subset:

```bash
tmp_jds="$(mktemp /tmp/seektalent_phase_2_3a.XXXXXX.jsonl)"
jq -c '
  select(
    .jd_id == "agent_jd_004" or
    .jd_id == "agent_jd_007" or
    .jd_id == "llm_training_jd_001" or
    .jd_id == "bigdata_jd_001"
  )
' artifacts/benchmarks/phase_2_2_pilot.jsonl > "$tmp_jds"

out_dir="runs/phase_2_3a_artifact_slimming_no_eval_$(date +%Y%m%d_%H%M%S)"
uv run seektalent benchmark \
  --jds-file "$tmp_jds" \
  --env-file .env \
  --output-dir "$out_dir" \
  --benchmark-max-concurrency 1 \
  --disable-eval \
  --json
```

Expected:

- all targeted tests pass;
- 4 replay rows complete with `evaluation_result: null`;
- every row has `final_candidate_count > 0`;
- overlap rows meet the 40% artifact-size reduction gate.

Execution status, 2026-04-21:

- Implemented 2.3A code changes and bumped package version to `0.4.8`.
- User-requested replay was eval-enabled, not no-eval:
  - output dir: `runs/phase_2_3a_artifact_slimming_eval_0_4_8_20260421_101101`
  - `--benchmark-max-concurrency 1`
  - `SEEKTALENT_JUDGE_MAX_CONCURRENCY=5`
  - `--enable-eval`
- Replay completed all 4 rows with non-null `evaluation_result` and non-empty final candidates:
  - `agent_jd_004`: run `b2d04a72`, final total `0.1920`, final candidate count `10`
  - `agent_jd_007`: run `eeacf3a0`, final total `0.4212`, final candidate count `10`
  - `llm_training_jd_001`: run `3c911be0`, final total `0.7457`, final candidate count `10`
  - `bigdata_jd_001`: run `b148edff`, final total `0.6692`, final candidate count `10`
- W&B report issue observed: Weave printed transient `retry_attempt`, and the report could miss just-synced `0.4.8` runs because the W&B API is eventually consistent after `run.finish()`. Fixed by passing the just-finished eval row into report Markdown generation as `extra_rows`; manually rebuilt the report with the 4 corrected `0.4.8` rows.
- Contract validation passed on the replay artifacts:
  - no call snapshot contains top-level `user_payload` or `structured_output`
  - per-call hashes and char counts exist
  - `normalized_resumes.jsonl` is absent
  - per-round `scoring_input_refs.jsonl` exists for scored rounds
- Size result for the eval replay:
  - `agent_jd_004`: eval total `1,072,097` bytes; comparable core excluding eval/raw-resume/judge artifacts `589,136` bytes; core drop `29.41%`
  - `agent_jd_007`: eval total `1,529,414` bytes; comparable core excluding eval/raw-resume/judge artifacts `884,540` bytes; core drop `56.40%`
- The original M1 size gate was defined for no-eval total JSON/JSONL/MD. The eval replay is not directly comparable because it adds `evaluation/`, `raw_resumes/`, and `judge_packet.json`. After the replay, events and top-pool snapshots were further slimmed in code; rerun the overlap rows before closing the 40% size gate as fully accepted.

### M2. Execute Phase 2.3B Reflection + Finalizer Draft Slimming

Steps:

- Remove Reflection prose assessment and critique fields.
- Keep runtime-built `reflection_summary`.
- Introduce finalizer draft models.
- Keep public `FinalResult` / `FinalCandidate` shape unchanged.
- Preserve finalizer validator contract.
- Update round review rendering to one business-readable reflection summary.

Acceptance:

- Reflection draft/persisted advice no longer has the removed fields.
- `round_review.md` does not contain `Strategy assessment`, `Quality assessment`, or `Coverage assessment`.
- Finalizer model-facing output is draft-only.
- Runtime public final result shape remains contract-tested.
- Final candidate ids, order, source round, and count follow runtime top candidates.

Validation:

```bash
uv run pytest \
  tests/test_reflection_contract.py \
  tests/test_finalizer_contract.py \
  tests/test_runtime_audit.py \
  tests/test_runtime_state_flow.py \
  tests/test_ui_mapper.py
```

Expected:

- all targeted tests pass;
- no-eval replay has no zero-final regression;
- final public JSON shape remains stable.

### M3. Execute Phase 2.3C Scoring Schema Experiment

Steps:

- Decide the exact scoring fields to remove or derive.
- Define public `strengths` / `weaknesses` generation before deleting model-facing fields.
- Run focused tests, then eval/cached judge.
- Compare candidate ids, ranking sort key stability, precision/nDCG, and sampled business trace quality.

Acceptance:

- Eval/cached judge completes.
- Precision/nDCG and final ids are compared against the current accepted baseline.
- Any explanation-quality loss is recorded with sampled trace evidence.
- If scoring schema removal changes ranking or public explanation quality without clear benefit, revert that removal.

Validation:

```bash
uv run pytest tests/test_finalizer_contract.py tests/test_runtime_audit.py tests/test_evaluation.py
```

Replay should use eval/cached judge. Do not accept 2.3C on no-eval evidence alone.

## Decision Log

- 2026-04-21: Old Phase 2.3 controller/reflection-only scope is obsolete.
- 2026-04-21: Split Phase 2.3 into 2.3A/2.3B/2.3C to separate low-risk artifact slimming, medium-risk draft schema slimming, and high-risk scoring schema experiment.
- 2026-04-21: No old artifact compatibility is required by default.
- 2026-04-21: `top_pool_snapshot.json` should be slimmed, not deleted, unless another canonical global-top-pool artifact replaces it.
- 2026-04-21: Scoring schema slimming requires eval/cached judge because it can alter recruiter-visible explanation quality.

## Risks

- Artifact slimming can remove debugging evidence if refs, hashes, summaries, and char counts are incomplete.
- Finalizer draft slimming can accidentally weaken ordering/candidate-count validation if the validator contract is not preserved.
- Scoring schema slimming can change explanation quality even when candidate count stays stable.
- A single combined replay would make regressions hard to attribute; keep the gates separate.

## Stop Rules

- Stop if implementation edits retrieval, CTS, stop guidance, ranking semantics, or eval semantics outside an explicitly updated plan.
- Stop if 2.3A artifacts cannot be traced through refs/hashes after removing embedded payloads.
- Stop if 2.3B changes public `FinalResult` / `FinalCandidate` shape without explicit approval.
- Stop if 2.3C lacks eval/cached judge evidence.
- Stop if any acceptance replay row produces zero final candidates.

## Status

- Current milestone: M1 Artifact Slimming Only.
- Last completed: M0 documentation rewrite; Phase 2.2.2 fixed `agent_jd_007` zero-final via generic anchor hygiene.
- Next action: implement 2.3A Artifact Slimming Only.

## Done Checklist

- [x] Roadmap updated
- [x] Obsolete controller/reflection-only plan removed or superseded
- [x] Phase 2.2.2 artifact-size baseline recorded
- [x] 2.3A artifact slimming gate defined
- [x] 2.3B reflection/finalizer draft gate defined
- [x] 2.3C scoring schema eval gate defined
- [x] Doc-only diff verified
