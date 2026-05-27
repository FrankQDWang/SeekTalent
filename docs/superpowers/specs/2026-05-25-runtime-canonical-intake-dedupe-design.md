# Runtime Canonical Intake And Deterministic Dedupe Design

## Summary

The active Runtime now owns requirement extraction and selected-source retrieval. The next boundary is the post-source intake path:

```text
RequirementSheet + source query intents
-> CTS adapter returns raw ResumeCandidate objects
-> Liepin PI adapter returns raw ResumeCandidate objects
-> Runtime merges raw candidates and source evidence
-> Runtime normalizes candidates once
-> Runtime deterministically groups same-person resumes into identities
-> Runtime chooses one canonical resume per identity
-> scorer sees only canonical, unscored identities
-> top pool, finalizer, reflection, and next controller context use identity-deduped candidates
```

This slice must not use an LLM to decide duplicate identity. Duplicate handling must be deterministic, inspectable, and conservative. Uncertain matches should be recorded as conflicts, not auto-merged.

## Current Code Facts

- `src/seektalent/models.py` already has the active candidate/identity surface:
  - `ResumeCandidate`
  - `NormalizedResume`
  - `RuntimeIdentitySignals`
  - `RuntimeCandidateIdentity`
  - `RuntimeIdentityConflict`
  - `RuntimeCanonicalResumeSelection`
  - `RuntimeSourceEvidence`
  - `RunState.candidate_store`
  - `RunState.normalized_store`
  - `RunState.candidate_identity_by_resume_id`
  - `RunState.candidate_identities`
  - `RunState.identity_conflicts`
  - `RunState.canonical_resume_by_identity_id`
- CTS raw candidate mapping in `src/seektalent/clients/cts_client.py` does not consistently write an explicit `provider` or `source` marker into `ResumeCandidate.raw`.
- Liepin mapping in `src/seektalent/providers/liepin/mapper.py` already writes safe source metadata including `provider="liepin"`.
- `src/seektalent/normalization.py::normalize_resume` has no explicit source/provider field in `NormalizedResume`, so useful source provenance can be lost before scoring, reflection, UI, or artifacts.
- `src/seektalent/providers/liepin/runtime_lane.py` pre-populates `RuntimeSourceLaneResult.normalized_store_updates` for detail-backed candidates.
- CTS candidates are normalized later by `src/seektalent/runtime/scoring_runtime.py::normalize_scoring_pool`.
- `src/seektalent/runtime/source_lanes.py` rebuilds identity state during source merge, but it depends on whatever is already in `run_state.normalized_store`.
- `RuntimeCandidateIdentityIndex` currently merges by exact identity keys:
  - protected contact hash
  - provider candidate key hash
  - non-masked name plus exact current company/title/school/work chronology fingerprint
- `choose_canonical_resume_for_identity` already has the right canonical preference shape:
  - detail evidence
  - newest collected timestamp
  - normalized completeness
  - source trust
  - provider rank
- `src/seektalent/runtime/scoring_runtime.py::build_scoring_pool` only dedupes by `resume_id` and existing scorecard `resume_id`.
- `src/seektalent/runtime/scoring_runtime.py::score_round` builds the top pool from resume-level scorecards, not identity-level canonical candidates.
- `src/seektalent/runtime/orchestrator.py::_apply_identity_top_pool` has identity-deduped top-pool logic, but the active multi-round `_run_rounds` scoring/finalization path does not consistently use it.
- `src/seektalent/runtime/reflection_context.py` and `src/seektalent/runtime/controller_context.py` do not expose source coverage, identity duplicate, conflict, or canonicalization summaries.

## Goals

- Make `job_title/JD/notes -> final top 10` keep one canonical Runtime data flow after source retrieval.
- Preserve the source fact for every processed resume:
  - CTS resumes must carry `provider/source = "cts"`.
  - Liepin resumes must carry `provider/source = "liepin"`.
  - `NormalizedResume` must expose the source provider used by downstream artifacts.
- Move active candidate normalization into Runtime-owned intake:
  - source adapters return raw candidates plus evidence
  - Runtime normalizes candidates before identity rebuild
  - scoring reuses normalized candidates instead of silently renormalizing the same object
- Keep source adapters decoupled:
  - CTS adapter owns CTS retrieval
  - Liepin PI adapter owns Liepin page execution and full-resume acquisition
  - Runtime owns normalization, identity, canonical selection, scoring intake, top pool, reflection context
- Confirm duplicates deterministically, without LLM calls.
- Deduplicate across rounds before scoring:
  - if a new resume maps to an already-scored identity, do not score it again
- Deduplicate across sources before scoring:
  - if CTS and Liepin produce the same person in the same round, score only the canonical resume
  - keep all source evidence attached to the identity
- Preserve uncertain duplicate signals:
  - medium-confidence duplicate candidates become `RuntimeIdentityConflict` records
  - medium-confidence candidates remain separate identities and may both be scored
- Make finalizer and top pool identity-deduped:
  - final top 10 means 10 candidate identities, not 10 resume documents
- Add reflection/controller context about the previous round's source and dedupe outcome.
- Keep canonical intake summaries round-scoped:
  - per-source counts and conflict counts describe the current round only
  - cumulative identity/source state may remain in `RunState`, but it must not be labeled as the latest round summary

## Non-Goals

- Do not use an LLM to determine whether two resumes are the same person.
- Do not rewrite scoring prompts or scoring schema.
- Do not redesign requirement extraction.
- Do not change the Liepin PI browser workflow or CTS API retrieval in this slice.
- Do not do UI cleanup in this slice.
- Do not delete legacy Workbench code in this slice.
- Do not merge uncertain identity matches automatically.
- Do not refresh scores for identities that were already scored in an earlier round.

## Target Runtime Data Flow

```text
SourceRoundDispatchResult
  source_results:
    cts: raw ResumeCandidate[] + RuntimeSourceEvidence[]
    liepin: raw ResumeCandidate[] + RuntimeSourceEvidence[]
  candidates: concatenated raw ResumeCandidate[]

Runtime canonical intake
  add candidates to RunState.candidate_store
  add source evidence to RunState.source_evidence_by_resume_id
  normalize candidates into RunState.normalized_store
  rebuild deterministic candidate identities
  choose canonical resume for every identity
  build a round-scoped source/identity intake summary
  build scoring intake:
    skip already-scored identities
    skip duplicate resumes for identities already represented in this round
    keep uncertain conflicts as separate scoring candidates

Scoring
  score canonical candidates only
  write scorecards by canonical resume_id
  update identity-deduped top_pool_ids

Reflection/controller/finalizer
  use identity-deduped top pool
  include source/identity/canonicalization summary
```

## Deterministic Identity Policy

### Strong Auto-Merge Signals

These signals merge candidates into one identity:

- `protected_contact_hashes` overlap: score `100`.
- same non-empty `provider_candidate_key_hash`: score `95`.
- non-masked normalized name plus strong profile corroboration: score `85` or higher.

The profile corroboration score is deterministic:

- normalized names must match exactly
- masked names cannot use profile-only auto-merge
- base visible-name score: `40`
- exact current company match: `+20`
- current title exact match or token overlap >= `0.6`: `+15`
- school overlap: `+15`
- work chronology fingerprint overlap: `+15`
- years of experience difference <= `1`: `+5`
- location overlap: `+5`
- skill token overlap >= `0.3`: `+5`
- cap profile score at `95`

Auto-merge threshold: `>= 85`.

### Medium-Confidence Conflict Signals

Medium-confidence matches are recorded but not merged:

- score `70` through `84`
- create a `RuntimeIdentityConflict`
- include candidate identity ids, resume ids, evidence ids, reason code, and match score
- keep the candidates as separate identities
- allow both canonical resumes to be scored

### Non-Match Signals

- score `< 70`: no merge and no conflict
- masked name plus company/title is not enough
- name-only match is not enough
- missing name is not enough unless contact or provider hash matches

## Canonical Resume Selection

Keep the existing canonical preference order and make it the single active scoring/top-pool policy:

1. detail evidence beats card evidence
2. newer `RuntimeSourceEvidence.collected_at` beats older evidence
3. higher `NormalizedResume.completeness_score` beats lower completeness
4. source trust breaks ties: Liepin detail source outranks CTS when all previous signals tie
5. better provider rank breaks remaining ties
6. resume id breaks final ties for deterministic output

All raw resume candidates remain in `RunState.candidate_store`. The scorer receives only the canonical resume selected for each unscored identity.

Cross-round duplicate policy:

- A candidate that maps to an already-scored identity is not scored again in this slice.
- Runtime keeps the new raw candidate and source evidence for audit/debug/reflection.
- If the new duplicate changes canonical evidence, the canonical selection store may update, but top pool/finalizer must still use a scored resume for that identity.
- Score refresh for already-scored identities is explicitly out of scope for this slice.

Same-round duplicate policy:

- If CTS and Liepin return the same unscored identity in the same round, Runtime picks the canonical resume before scoring.
- The scorer receives one candidate for that identity.
- The losing raw resume remains attached as source evidence and duplicate context.

## Source Target Accounting

The active dual-source round should treat source acquisition and scoring intake as different counts:

- CTS selected source target: 10 raw full resumes per round.
- Liepin selected source target: 10 raw full resumes per round, split by logical query budget as exploit 7 and explore 3.
- Runtime merged raw intake may therefore contain up to 20 raw candidates before identity dedupe.
- `SearchObservation.requested_count` should not imply that the merged dual-source target is only 10 raw resumes.
- The round intake summary must expose selected source kinds, per-source raw targets, and per-source raw counts.
- Scoring intake may be lower than raw intake after cross-round and cross-source dedupe.
- Final top pool remains 10 identities.

## Reflection And Controller Context

Reflection should receive the previous round's round-scoped canonical intake summary:

- selected source kinds
- per-source raw targets
- per-source raw candidate counts
- per-source normalized candidate counts
- identity count after deterministic merge
- auto-merged duplicate count
- skipped already-scored identity count
- uncertain identity conflict count
- canonical resume ids entering scoring

Controller should receive a compact version of the latest identity/source summary so the next round can reason about whether the problem is source coverage, duplicate-heavy queries, or low-quality candidates.

## Acceptance Criteria

1. `ResumeCandidate.raw` for CTS candidates includes a safe provider/source marker.
2. Liepin candidates keep their existing provider/source marker.
3. `NormalizedResume` exposes source provider metadata for both CTS and Liepin.
4. Active Liepin runtime lane no longer pre-populates normalized store updates for scoring.
5. Runtime source merge normalizes new raw candidates before identity rebuild.
6. Scoring reuses already-normalized candidates and does not create a separate adapter-specific normalization path.
7. Deterministic identity tests prove protected contact hash overlap auto-merges across sources.
8. Deterministic identity tests prove provider candidate hash auto-merges.
9. Deterministic identity tests prove non-masked name plus strong corroboration auto-merges.
10. Deterministic identity tests prove masked name plus company/title does not auto-merge.
11. Deterministic identity tests prove score `70` through `84` records a conflict without merging.
12. Cross-round intake tests prove a new resume for an already-scored identity is not scored again.
13. Cross-source intake tests prove same-person CTS/Liepin candidates in the same round produce one scorer input.
14. Cross-source intake tests prove all source evidence remains attached to the identity after dedupe.
15. Canonical selection tests prove detail evidence beats older or less complete card evidence.
16. Top pool tests prove final `run_state.top_pool_ids` contains one scored resume per identity.
17. Finalizer context uses identity-deduped top candidates.
18. Reflection context includes canonical intake/source dedupe summary.
19. Controller context includes compact previous-round identity/source summary.
20. No code path calls an LLM to decide duplicate identity.
21. Source target accounting tests prove selected CTS + Liepin reports a 20-resume raw source target while the final top pool remains 10 identities.
22. Round summary tests prove latest conflict/raw-count metrics are current-round values, not cumulative values from older rounds.
23. Focused tests pass:
    - `uv run pytest tests/test_normalization.py tests/test_runtime_candidate_identity.py tests/test_runtime_source_lanes.py tests/test_runtime_state_flow.py tests/test_flywheel_runtime.py -q`
24. Static checks pass for changed files:
    - `uv run ruff check src/seektalent tests`
