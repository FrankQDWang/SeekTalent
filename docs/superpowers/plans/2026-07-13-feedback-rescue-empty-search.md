# Candidate Feedback Rescue And Empty Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make candidate-feedback rescue discover grounded Top-5 resume terms through strict native structured output, and make a successful zero-card Liepin search complete normally instead of failing the run.

**Architecture:** Reuse the existing LLM PRF proposal, grounding, and deterministic policy gate as the only active candidate-feedback term discovery path. Keep the runtime rescue router authoritative, but make its feedback callback asynchronous and materialize only a policy-approved expression. At the Liepin boundary, finalize a successfully submitted and extracted empty result before candidate identity/detail-open handling.

**Tech Stack:** Python 3.12, Pydantic/Pydantic AI native JSON Schema output, pytest, existing runtime and Liepin workflow contracts.

---

### Task 1: Enforce native strict output for LLM PRF

**Files:**
- Modify: `src/seektalent/llm.py`
- Modify: `src/seektalent/candidate_feedback/llm_prf.py`
- Modify: `tests/test_llm_provider_config.py`
- Modify: `tests/test_llm_prf.py`

- [x] **Step 1: Write failing tests** asserting `candidate_feedback` and `prf_probe_phrase_proposal` resolve to `native_json_schema`, `LLMPRFExtractor` receives `NativeOutput(..., strict=True)`, and its schema-repair retry budget is one.
- [x] **Step 2: Run** `uv run pytest tests/test_llm_provider_config.py tests/test_llm_prf.py -q` and confirm the new assertions fail because both stages currently use prompted JSON and two output retries.
- [x] **Step 3: Add both stages to `OPENAI_NATIVE_JSON_SCHEMA_STAGES`, remove them from `OPENAI_PROMPTED_JSON_STAGES`, require `NativeOutput` in `LLMPRFExtractor._build_agent()`, and set `LLM_PRF_OUTPUT_RETRIES = 1`.
- [x] **Step 4: Run** `uv run pytest tests/test_llm_provider_config.py tests/test_llm_prf.py -q` and confirm all tests pass.

### Task 2: Route candidate-feedback rescue through grounded Top-K LLM PRF

**Files:**
- Modify: `src/seektalent/runtime/round_decision_runtime.py`
- Modify: `src/seektalent/runtime/rescue_execution_runtime.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `tests/test_controller_contract.py`
- Modify: `tests/test_runtime_state_flow.py`
- Modify: `tests/test_candidate_feedback.py`

- [x] **Step 1: Write failing tests** proving the rescue callback is awaited, an LLM proposal grounded in two distinct eligible Top-5 resumes becomes the forced query, a phrase present only in `reasoning_summary` is absent from proposal evidence, and an empty/rejected proposal advances to anchor-only without failing.
- [x] **Step 2: Run the named tests** in the six files and confirm failures identify the current synchronous deterministic extraction path.
- [x] **Step 3: Change the rescue callback contract to `Awaitable[SearchControllerDecision | None]` and await it in pre-controller and post-controller rescue paths.**

```python
force_candidate_feedback_decision: Callable[..., Awaitable[SearchControllerDecision | None]]
feedback_decision = await force_candidate_feedback_decision(...)
```

- [x] **Step 4: Generalize `_build_llm_prf_policy_decision()` to accept `round_no` and `retrieval_query_terms`, so both second-lane PRF and rescue use the same Top-5 input preparation, provider call, exact-source grounding, two-resume support gate, and artifacts.**

```python
async def _build_llm_prf_policy_decision(
    self,
    *,
    run_state: RunState,
    round_no: int,
    retrieval_query_terms: list[str],
    tracer: RunTracer,
) -> _PRFBackendSelection:
    ...
```

- [x] **Step 5: Materialize the accepted grounded expression as a `candidate_feedback` query term; record a null decision when no safe expression exists; skip a duplicate second LLM PRF call in a round already rescued through candidate feedback.**

```python
accepted = selection.prf_decision.accepted_expression if selection.prf_decision is not None else None
if accepted is None:
    return None
```

- [x] **Step 6: Remove `reasoning_summary` from deterministic feedback source fields and update runtime/docs assertions so narrative summaries cannot become search terms even in dormant helpers.**

```python
def _resume_field_texts(resume: ScoredCandidate) -> dict[str, list[str]]:
    return {
        "evidence": list(resume.evidence),
        "strengths": list(resume.strengths),
        "matched_must_haves": list(resume.matched_must_haves),
        "matched_preferences": list(resume.matched_preferences),
    }
```
- [x] **Step 7: Run** `uv run pytest tests/test_candidate_feedback.py tests/test_controller_contract.py tests/test_runtime_state_flow.py tests/test_llm_prf.py -q` and confirm all tests pass.

### Task 3: Treat a successful zero-card Liepin result as exhausted completion

**Files:**
- Modify: `src/seektalent/providers/liepin/liepin_search_workflow.py`
- Modify: `src/seektalent/providers/liepin/liepin_site_payloads.py`
- Modify: `tests/test_liepin_search_workflow.py`
- Modify: `tests/test_liepin_runtime_source_lane.py`

- [x] **Step 1: Write a failing workflow test** where search submission and structured-card extraction succeed with zero cards, asserting `status == "succeeded"`, zero resumes, and no identity/detail-open failure.
- [x] **Step 2: Write a failing offline integration test** covering `LiepinSearchWorkflow -> OpenCLI envelope mapping -> run_liepin_logical_query_bundle`, asserting the logical query completes empty and returns a terminal query execution outcome instead of `failed_provider_error`.
- [x] **Step 3: Run** the two named tests and confirm they fail with `liepin_opencli_candidate_identity_missing`.
- [x] **Step 4: Return through normal finalization immediately after a successful zero-card observation, and make the payload finalizer classify a zero-card/zero-resume result as succeeded even when the requested target is positive.**

```python
if not card_items:
    return self._site.finalize_liepin_resumes(
        source_run_id=request.source_run_id,
        query=request.query,
        max_pages=request.max_pages,
        max_cards=request.max_cards,
        cards_seen=0,
        target_resumes=request.target_resumes,
    )
```
- [x] **Step 5: Run** `uv run pytest tests/test_liepin_search_workflow.py tests/test_liepin_runtime_source_lane.py tests/test_liepin_opencli_retriever.py -q` and confirm all tests pass.

### Task 4: Documentation and full verification

**Files:**
- Modify: `docs/configuration.md`
- Modify: `docs/llm-context-composition.zh-CN.md`
- Modify: `docs/outputs.md`

- [x] **Step 1: Update current behavior docs** to state that active candidate feedback uses strict native LLM PRF proposals plus deterministic grounding/policy, and that empty provider results are normal completed outcomes.
- [x] **Step 2: Run focused gates:** `uv run --group dev ruff check src tests`, `uv run --group dev ty check`, the source-boundary check from `.github/workflows/python-quality.yml`, and `uv run --group dev tach check`.
- [x] **Step 3: Run full tests:** `uv run --group dev python -m pytest -q -n auto --dist=loadfile`.
- [x] **Step 4: Run the Workbench contract:** `uv run --group dev python -m pytest tests/test_agent_workbench_contract.py -q -p no:cacheprovider`.
- [x] **Step 5: Commit the implementation only after all fresh verification outputs are green.**

### Task 5: Brooks review loop and release

**Files:**
- Modify during review: only files implicated by concrete Brooks findings.
- Modify for release: `pyproject.toml`, `src/seektalent/version.py`, `scripts/install-seektalent-domi.sh`, `scripts/install-seektalent-domi.ps1`, `uv.lock`.

- [x] **Step 1: Dispatch a Brooks PR-review subagent** over the feature diff and test evidence, requiring Symptom -> Source -> Consequence -> Remedy findings or `CLEAR`.
- [ ] **Step 2: For every finding, add or update a failing test where behavior is affected, implement the smallest fix, rerun focused/full gates, and dispatch a fresh Brooks review. Repeat until `CLEAR`.**
- [ ] **Step 3: Fast-forward merge to local `main`, preserve the pre-existing `.gitignore` and reference-document changes, and rerun the full verification suite on merged `main`.**
- [ ] **Step 4: Bump the next patch version consistently, build and inspect distribution metadata, commit, push `main`, create and push the annotated version tag, and publish the GitHub release.**
- [ ] **Step 5: Wait for Python Quality, Workbench Contract, CodeQL, and package publishing to succeed; verify the version is available from the package index.**
- [ ] **Step 6: Install the released version through `uv tool` and the Domi bootstrap script, then verify both local entry points report the new version.**
