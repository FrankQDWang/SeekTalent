# Task 2 Report: Remove LLM Ownership Of Total Score

## Outcome

- Removed `overall_score` from `ScoredCandidateDraft`; runtime now materializes it from Task 1's weighted-score policy.
- Made preferred and risk dimensions nullable across draft, persisted scorecard, and top-pool view models.
- Enforced policy applicability during materialization and timeout fallback construction.
- Bumped the scoring cache schema to `scored_candidate.v2` and expanded scoring trace summaries with all score dimensions.
- Updated the scoring prompt and both scoring contract documents to make runtime ownership explicit.

## TDD Evidence

- RED: `uv run pytest -q tests/test_scoring_cache.py -k 'draft_schema or materializer'` produced 3 expected failures because the draft still required `overall_score`, nullable dimensions were rejected, and the materializer lacked `scoring_policy`.
- GREEN: the same command passed `3 passed, 20 deselected` after implementation.
- Nullable sort regression: `uv run pytest -q tests/test_v02_models.py -k sort_key` passed `1 passed, 5 deselected`.

## Verification

- `uv run pytest -q tests/test_scoring_cache.py tests/test_runtime_state_flow.py tests/test_llm_lifecycle.py tests/test_runtime_audit.py tests/test_v02_models.py` -> `160 passed`.
- `uv run ruff check ...` across all changed Python files -> `All checks passed!`.
- `git diff --check` -> clean.
- Stale-contract search found only the new explicit statements that the model does not output `overall_score`.

## Deviations And Blockers

- No blockers.
- `tests/test_runtime_state_flow.py` required verification but no source edit: its persisted `ScoredCandidate` fixtures correctly retain runtime-owned `overall_score` and none construct `ScoredCandidateDraft`.
- Pre-existing dirty docs and untracked `.superpowers` artifacts were left untouched; only this report is added under `.superpowers/sdd`.
