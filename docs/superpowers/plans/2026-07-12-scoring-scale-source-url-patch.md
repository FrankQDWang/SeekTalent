# Scoring Scale and Liepin Source URL Patch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a coherent 0–100 recommendation scale and stable canonical Liepin source links in one patch release.

**Architecture:** Keep deterministic score weighting unchanged. Strengthen the LLM output boundary, centralize recommendation eligibility, and reuse the existing strict Liepin subject parser to canonicalize only identity-verified detail URLs.

**Tech Stack:** Python 3.12, Pydantic AI, pytest, React/TypeScript Workbench, SQLite runtime-control, GitHub Actions, PyPI.

## Global Constraints

- Keep weights at must-have 60, preferred 25, inverted risk 15.
- Recommendation eligibility is exactly `fit_bucket == "fit" and overall_score >= 60`.
- Use the existing bounded scoring output retry; add no generic retry path.
- Persist only canonical `https://h.liepin.com/resume/showresumedetail/?res_id_encode=<validated subject>` URLs.
- Preserve the user's unrelated root-checkout `.gitignore` modification.

---

### Task 1: Enforce the Anchored Scoring Scale

**Files:**
- Modify: `src/seektalent/prompts/scoring.md`
- Modify: `src/seektalent/scoring/scorer.py`
- Test: `tests/test_llm_fail_fast.py`
- Test: `tests/test_scoring_cache.py`

**Interfaces:**
- Consumes: `calculate_overall_score(...)` and per-run scoring validator deps.
- Produces: structured-output validation that rejects `fit` drafts with deterministic overall scores below 60.

- [ ] **Step 1: Write failing tests**

Add a sequential model-output test whose first draft is `fit` with `must_have_match_score=1` and `preferred_match_score=1`, then returns anchored scores. Assert one `ModelRetry`, one valid result, and overall score at least 60. Add a persistent-low-fit test that exhausts as `score_applicability_error` or a new bounded safe scoring-contract category without leaking provider output.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_llm_fail_fast.py tests/test_scoring_cache.py -q`

Expected: the low-score `fit` draft currently materializes instead of retrying.

- [ ] **Step 3: Implement the minimum validation**

Extend the scoring prompt with the approved five score bands. In the existing output validator, calculate overall score using the current applicability and raise `ModelRetry` when `draft.fit_bucket == "fit" and overall_score < 60`. Preserve strict applicability validation and existing output retry limits.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/test_llm_fail_fast.py tests/test_scoring_cache.py tests/test_llm_lifecycle.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/prompts/scoring.md src/seektalent/scoring/scorer.py tests/test_llm_fail_fast.py tests/test_scoring_cache.py
git commit -m "fix: enforce anchored candidate scoring"
```

### Task 2: Unify Recommendation Eligibility and Observation Copy

**Files:**
- Modify: `src/seektalent/candidate_quality.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `src/seektalent_workbench_v2/runtime_service.py`
- Modify: `src/seektalent_ui/agent_workbench_projection.py`
- Test: `tests/test_candidate_quality.py`
- Test: `tests/test_runtime_state_flow.py`
- Test: `tests/test_workbench_v2_runtime_service.py`
- Test: `tests/test_agent_workbench_contract.py`

**Interfaces:**
- Produces: `is_recommendation_eligible(*, score: int | None, fit_bucket: str | None) -> bool`.
- Consumers: both Workbench projections and round quality-comment assembly.

- [ ] **Step 1: Write failing tests**

Cover `fit/60`, `fit/59`, `not_fit/90`, and missing values. Assert both Workbench projections exclude `not_fit/90`. Assert the quality commenter is not called when a round has no eligible candidates and the deterministic sentence is emitted.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_candidate_quality.py tests/test_runtime_state_flow.py tests/test_workbench_v2_runtime_service.py tests/test_agent_workbench_contract.py -q`

Expected: `not_fit/90` remains visible and the LLM commenter receives ineligible candidates.

- [ ] **Step 3: Implement shared eligibility**

Add the shared predicate in `candidate_quality.py`. Replace score-only projection filters. In the orchestrator, filter `scored_this_round` through the predicate before comment generation; when empty, use `本轮暂无达到 60 分推荐标准且满足硬性条件的候选人。` without an LLM call.

- [ ] **Step 4: Verify GREEN**

Run the RED command again. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/candidate_quality.py src/seektalent/runtime/orchestrator.py src/seektalent_workbench_v2/runtime_service.py src/seektalent_ui/agent_workbench_projection.py tests/test_candidate_quality.py tests/test_runtime_state_flow.py tests/test_workbench_v2_runtime_service.py tests/test_agent_workbench_contract.py
git commit -m "fix: align candidate recommendation surfaces"
```

### Task 3: Restore Canonical Liepin Source URLs

**Files:**
- Modify: `src/seektalent/providers/liepin/liepin_site_parsing.py`
- Modify: `src/seektalent/providers/liepin/liepin_site_adapter.py`
- Test: `tests/test_liepin_opencli_browser.py`
- Test: `tests/test_liepin_provider_mapping.py`
- Test: `tests/test_workbench_v2_runtime_service.py`

**Interfaces:**
- Produces: `canonical_liepin_detail_source_url(detail_url: str) -> str | None`.
- Consumes: the same strict subject parsing semantics as `stable_liepin_detail_candidate_key_hash`.

- [ ] **Step 1: Write failing tests**

Assert a verified URL with volatile parameters canonicalizes to only `res_id_encode`; claim-aware capture stores that URL. Assert duplicate subjects, encoded key/value aliases, invalid subjects, wrong hosts, and identity mismatches return no URL or fail with the existing safe identity-mismatch error.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_liepin_opencli_browser.py tests/test_liepin_provider_mapping.py tests/test_workbench_v2_runtime_service.py -q`

Expected: claim-aware capture omits `sourceUrl`.

- [ ] **Step 3: Implement canonicalization and propagation**

Extract the strict `res_id_encode` subject once, use it for both hashing and canonical URL creation, and add the canonical URL to `detail_payload` only after claim-aware identity verification succeeds.

- [ ] **Step 4: Verify GREEN**

Run the RED command again. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/providers/liepin/liepin_site_parsing.py src/seektalent/providers/liepin/liepin_site_adapter.py tests/test_liepin_opencli_browser.py tests/test_liepin_provider_mapping.py tests/test_workbench_v2_runtime_service.py
git commit -m "fix: restore canonical Liepin source links"
```

### Task 4: Verify, Version, Publish, and Install

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/seektalent/version.py`
- Modify: `scripts/install-seektalent-domi.sh`
- Modify: `scripts/install-seektalent-domi.ps1`
- Modify: `uv.lock`

- [ ] **Step 1: Run verification**

Run:

```bash
uv run pytest -q
uv run ruff check src tests tools
uv run ty check src/seektalent/candidate_quality.py src/seektalent/scoring/scorer.py src/seektalent/providers/liepin/liepin_site_parsing.py src/seektalent/providers/liepin/liepin_site_adapter.py
SEEKTALENT_VERIFY_SKIP_PYTHON_PREFLIGHT=1 scripts/verify-dev-workbench.sh
```

Expected: all commands exit 0.

- [ ] **Step 2: Bump the next unused patch version**

Update all five version sources consistently, run the Domi/version tests, build distributions, and inspect wheel metadata.

- [ ] **Step 3: Commit and integrate**

Commit only patch-owned files, fast-forward local `main`, rerun the full test suite on merged `main`, and preserve the unrelated `.gitignore` change.

- [ ] **Step 4: Release**

Push `main`, create and push the matching version tag, publish a GitHub Release, monitor Python Quality, Workbench Contract, CodeQL, and Publish Python Package to success.

- [ ] **Step 5: Install from PyPI**

Wait until the version is present in both PyPI JSON and Simple API, run the tagged Domi install script, and verify the shim and installed metadata report the new version.
