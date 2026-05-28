# Runtime Canonical Intake And Deterministic Dedupe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Runtime post-source path normalize raw CTS/Liepin resumes once, deterministically dedupe identities without LLM calls, score only canonical unscored identities, and feed identity-deduped state into top pool, reflection, controller, and finalizer.

**Architecture:** Source adapters keep returning raw candidates plus source evidence. Runtime owns canonical intake: source provenance, normalization, deterministic identity grouping, conflict recording, canonical resume selection, scoring candidate selection, and identity-level top pool. Reflection and controller receive compact dedupe/source summaries so the next round can reason from the same Runtime contract as CLI.

**Tech Stack:** Python 3.12, Pydantic, asyncio Runtime, pytest, ruff, existing SeekTalent models/runtime modules.

---

## Spec Link

This plan implements:

`docs/superpowers/specs/2026-05-25-runtime-canonical-intake-dedupe-design.md`

## Execution Notes

- Do not use an LLM for duplicate identity decisions.
- Do not change requirement extraction, scoring prompt schema, CTS API retrieval, or Liepin PI browser workflow.
- Do not do UI cleanup in this slice.
- Keep raw candidates in `RunState.candidate_store`; dedupe controls what enters scoring and top pool.
- Scope challenge resolution: this plan touches more than 8 files because the active data flow crosses source merge, normalization, identity, scoring, finalizer, reflection, and controller contracts. The user explicitly confirmed keeping the complete post-source Runtime intake slice together. Execute sequentially; do not split into parallel worktrees.
- Summary metrics must be round-scoped. Do not derive latest-round source kinds, raw counts, or conflict counts from global `run_state.normalized_store` or cumulative `run_state.identity_conflicts` without filtering to the current round's candidates.
- Cross-round duplicate policy: if a new candidate maps to an already-scored identity, keep its raw candidate/evidence for audit and reflection but do not score it again in this slice. Score refresh for already-scored identities is out of scope.
- Commit after each task.

## Existing Runtime Reuse

- Reuse `RuntimeCandidateIdentityIndex` instead of adding a second identity service.
- Reuse `choose_canonical_resume_for_identity` for canonical selection; only make its result the active scoring/top-pool policy.
- Reuse `RunState.candidate_store`, `normalized_store`, `source_evidence_by_resume_id`, `candidate_identity_by_resume_id`, and `canonical_resume_by_identity_id`; do not introduce a parallel pool store.
- Reuse `top_candidates(run_state)` for finalizer context by ensuring `run_state.top_pool_ids` is identity-deduped before finalization.
- Keep `RuntimeSourceEvidence` as the source provenance contract; do not add another provider evidence model.

## Not In Scope

- LLM duplicate adjudication.
- Score refresh for already-scored identities when a later duplicate has better evidence.
- UI cleanup or Workbench display changes.
- Requirement extraction, scoring prompt, or Liepin PI browser workflow changes.
- General provider plugin architecture.

## File Map

Core models:

- Modify: `src/seektalent/models.py`
  - Add source provider metadata to `NormalizedResume`.
  - Extend `RuntimeIdentitySignals` with deterministic matching fields.
  - Extend `RuntimeIdentityConflict` with score/resume metadata.
  - Add a small `RuntimeCanonicalIntakeSummary` model for reflection/controller context.
  - Add optional summary fields to `RunState`, `ReflectionContext`, and `ControllerContext`.

Source provenance and normalization:

- Modify: `src/seektalent/clients/cts_client.py`
  - Add safe CTS provider/source markers to raw payloads.
- Modify: `src/seektalent/normalization.py`
  - Populate `NormalizedResume.source_provider`.
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
  - Stop returning active `normalized_store_updates`.
- Modify: `src/seektalent/runtime/source_lanes.py`
  - Keep source evidence merge and identity rebuild deterministic.
  - Add stronger identity match scoring and conflict recording.

Canonical intake and scoring:

- Create: `src/seektalent/runtime/candidate_intake.py`
  - Normalize merged raw candidates before identity rebuild.
  - Build canonical scorer inputs.
  - Build identity/source summary.
  - Select identity-deduped top candidates.
- Modify: `src/seektalent/runtime/orchestrator.py`
  - Call Runtime normalization before identity rebuild in source dispatch merge.
  - Use canonical intake before scoring.
  - Keep finalization revisions identity-based.
- Modify: `src/seektalent/runtime/scoring_runtime.py`
  - Reuse canonical intake and identity-level top-pool selection.
  - Reuse existing normalized resumes.
- Modify: `src/seektalent/runtime/reflection_context.py`
  - Include canonical intake/source dedupe summary.
- Modify: `src/seektalent/runtime/controller_context.py`
  - Include compact previous-round identity/source summary.
- Verify: `src/seektalent/runtime/finalize_context.py`
  - Finalization must consume `run_state.top_pool_ids` through `top_candidates(run_state)` and must not re-sort resume-level scorecards directly.

Tests:

- Modify: `tests/test_normalization.py`
- Modify: `tests/test_runtime_candidate_identity.py`
- Modify: `tests/test_runtime_source_lanes.py`
- Modify: `tests/test_runtime_state_flow.py`

---

### Task 1: Preserve Source Provider Through Normalization

**Files:**
- Modify: `src/seektalent/models.py`
- Modify: `src/seektalent/clients/cts_client.py`
- Modify: `src/seektalent/normalization.py`
- Modify: `tests/test_normalization.py`

- [ ] **Step 1: Write failing normalization provenance tests**

Add these tests to `tests/test_normalization.py`:

```python
from seektalent.models import ResumeCandidate
from seektalent.normalization import normalize_resume


def _candidate_with_raw(resume_id: str, raw: dict[str, object]) -> ResumeCandidate:
    return ResumeCandidate(
        resume_id=resume_id,
        source_resume_id=resume_id,
        snapshot_sha256=f"sha-{resume_id}",
        dedup_key=resume_id,
        search_text="senior ai infra engineer",
        raw=raw,
    )


def test_normalized_resume_preserves_cts_provider_from_raw() -> None:
    normalized = normalize_resume(
        _candidate_with_raw(
            "cts-1",
            {
                "provider": "cts",
                "source": "cts",
                "candidate_name": "Alice Chen",
                "current_title": "AI Infra Engineer",
            },
        )
    )

    assert normalized.source_provider == "cts"


def test_normalized_resume_preserves_liepin_provider_from_raw() -> None:
    normalized = normalize_resume(
        _candidate_with_raw(
            "liepin-1",
            {
                "provider": "liepin",
                "source": "liepin",
                "safe_card_summary": {"display_title": "AI Agent Engineer"},
            },
        )
    )

    assert normalized.source_provider == "liepin"
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/test_normalization.py::test_normalized_resume_preserves_cts_provider_from_raw tests/test_normalization.py::test_normalized_resume_preserves_liepin_provider_from_raw -q
```

Expected: both tests fail with `AttributeError` or Pydantic validation output showing `source_provider` is absent.

- [ ] **Step 3: Add source provider to the normalized model**

In `src/seektalent/models.py`, add the field to `NormalizedResume` directly after `used_fallback_id`:

```python
    source_provider: str | None = None
```

- [ ] **Step 4: Populate CTS raw provider/source markers**

In `src/seektalent/clients/cts_client.py::_normalize_candidate`, immediately after:

```python
        raw_payload = candidate.model_dump(mode="python", exclude_none=False)
```

add:

```python
        raw_payload["provider"] = "cts"
        raw_payload["source"] = "cts"
```

- [ ] **Step 5: Populate `source_provider` in normalization**

In `src/seektalent/normalization.py`, add this helper near `_safe_card_summary`:

```python
def _source_provider(raw: dict[str, Any]) -> str | None:
    for key in ("provider", "source", "source_provider"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().casefold()
    return None
```

Then pass the field in `normalize_resume(...)` when constructing `NormalizedResume`:

```python
        source_provider=_source_provider(raw),
```

- [ ] **Step 6: Verify provenance tests pass**

Run:

```bash
uv run pytest tests/test_normalization.py::test_normalized_resume_preserves_cts_provider_from_raw tests/test_normalization.py::test_normalized_resume_preserves_liepin_provider_from_raw -q
```

Expected: both tests pass.

- [ ] **Step 7: Commit**

Run:

```bash
git add src/seektalent/models.py src/seektalent/clients/cts_client.py src/seektalent/normalization.py tests/test_normalization.py
git commit -m "feat: preserve runtime source provider in normalization"
```

---

### Task 2: Move Active Normalization To Runtime Intake

**Files:**
- Create: `src/seektalent/runtime/candidate_intake.py`
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `src/seektalent/runtime/scoring_runtime.py`
- Modify: `tests/test_liepin_runtime_source_lane.py`
- Modify: `tests/test_runtime_state_flow.py`

- [ ] **Step 1: Rewrite the existing Liepin detail-backed test to require raw-only active results**

In `tests/test_liepin_runtime_source_lane.py`, replace the existing `test_liepin_detail_backed_lane_populates_normalized_updates` with this renamed test:

```python
def test_liepin_detail_backed_lane_returns_raw_candidates_without_normalized_updates() -> None:
    worker = SingleDetailWorker()
    result = asyncio.run(
        run_liepin_source_lane(
            settings=make_settings(),
            request=RuntimeSourceLaneRequest(
                source="liepin",
                lane_mode="card",
                job_title="AI Agent Engineer",
                jd="Build LangGraph and RAG systems.",
                notes="Prefer evaluation.",
                requirement_sheet=_requirement_sheet(),
                source_query_terms=("LangGraph", "RAG"),
                logical_query_instance_id="q-exploit",
                logical_query_role="exploit",
                logical_keyword_query="LangGraph RAG",
                logical_requested_count=7,
                logical_provider_scan_limit=30,
                liepin_context={"liepin_fetch_strategy": "detail_backed_resume_search"},
            ),
            worker_client=worker,
        )
    )

    assert result.status == "completed"
    assert result.candidate_store_updates
    assert result.normalized_store_updates == {}
```

- [ ] **Step 2: Run the failing Liepin lane test**

Run:

```bash
uv run pytest tests/test_liepin_runtime_source_lane.py::test_liepin_detail_backed_lane_returns_raw_candidates_without_normalized_updates -q
```

Expected: fails because detail-backed Liepin currently returns normalized updates.

- [ ] **Step 3: Write a failing Runtime merge normalization test**

In `tests/test_runtime_state_flow.py`, first extend `_make_candidate(...)` with a `raw` keyword:

```python
def _make_candidate(
    resume_id: str,
    *,
    source_round: int = 1,
    project_names: list[str] | None = None,
    work_summaries: list[str] | None = None,
    search_text: str = "python retrieval trace resume search",
    raw: dict[str, object] | None = None,
) -> ResumeCandidate:
    return ResumeCandidate(
        resume_id=resume_id,
        source_resume_id=resume_id,
        dedup_key=resume_id,
        source_round=source_round,
        now_location="上海",
        expected_location="上海",
        expected_job_category="Python Engineer",
        work_year=6,
        education_summaries=["复旦大学 计算机 本科"],
        work_experience_summaries=["Example Co | Python Engineer | Built retrieval workflows."],
        project_names=project_names or ["Resume search"],
        work_summaries=work_summaries or ["python", "retrieval", "trace"],
        search_text=search_text,
        raw=raw or {"resume_id": resume_id, "candidate_name": resume_id},
    )
```

Then add these helpers near `_runtime_for_strict_source_tests(...)`:

```python
def _run_state_for_canonical_intake_tests() -> RunState:
    requirement_sheet = _requirement_sheet()
    return RunState(
        input_truth=InputTruth(
            job_title="AI Agent Engineer",
            jd="Build agentic retrieval workflows.",
            notes="",
            job_title_sha256="job",
            jd_sha256="jd",
            notes_sha256="notes",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            job_title=requirement_sheet.job_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=requirement_sheet.must_have_capabilities,
            preferred_capabilities=requirement_sheet.preferred_capabilities,
            exclusion_signals=requirement_sheet.exclusion_signals,
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(),
    )


def _noop_tracer(tmp_path: Path) -> RunTracer:
    return RunTracer(tmp_path / "artifacts")
```

Then add:

```python
def test_source_dispatch_merge_normalizes_candidates_before_identity_rebuild(tmp_path) -> None:
    runtime = _runtime_for_strict_source_tests(tmp_path)
    run_state = _run_state_for_canonical_intake_tests()
    source_plan = build_runtime_source_plan(
        source_kinds=("cts", "liepin"),
        settings=runtime.settings,
        runtime_run_id="run-test",
        liepin_context={"status": "ready"},
    )
    cts = _make_candidate(
        "cts-1",
        raw={"provider": "cts", "candidate_name": "Alice Chen", "current_company": "Acme", "current_title": "AI Engineer"},
    )
    liepin = _make_candidate(
        "liepin-1",
        raw={"provider": "liepin", "candidate_name": "Alice Chen", "current_company": "Acme", "current_title": "AI Engineer"},
    )

    runtime._merge_source_round_dispatch_result(
        run_state=run_state,
        dispatch_result=SourceRoundDispatchResult(
            source_results=(
                SourceRoundAdapterResult(source="cts", status="completed", candidates=(cts,), raw_candidate_count=1),
                SourceRoundAdapterResult(source="liepin", status="completed", candidates=(liepin,), raw_candidate_count=1),
            ),
            candidates=(cts, liepin),
            raw_candidate_count=2,
        ),
        source_plan=source_plan,
        round_no=1,
        tracer=_noop_tracer(tmp_path),
    )

    assert set(run_state.normalized_store) == {"cts-1", "liepin-1"}
    assert run_state.normalized_store["cts-1"].source_provider == "cts"
    assert run_state.normalized_store["liepin-1"].source_provider == "liepin"
```

Also add source target accounting coverage:

```python
def test_source_dispatch_observation_counts_selected_sources_as_raw_targets(tmp_path) -> None:
    runtime = _runtime_for_strict_source_tests(tmp_path)
    cts_candidates = tuple(_make_candidate(f"cts-{index}", raw={"provider": "cts"}) for index in range(10))
    liepin_candidates = tuple(_make_candidate(f"liepin-{index}", raw={"provider": "liepin"}) for index in range(10))
    retrieval_plan = RoundRetrievalPlan(
        plan_version=1,
        round_no=1,
        query_terms=["python"],
        role_anchor_terms=["python"],
        must_have_anchor_terms=[],
        keyword_query="python",
        location_execution_plan=LocationExecutionPlan(mode="none", target_new=10),
        target_new=10,
        rationale="Target ten raw resumes per selected source.",
    )

    result = runtime._round_search_result_from_source_dispatch(
        round_no=1,
        retrieval_plan=retrieval_plan,
        query_states=(),
        dispatch_result=SourceRoundDispatchResult(
            source_results=(
                SourceRoundAdapterResult(source="cts", status="completed", candidates=cts_candidates, raw_candidate_count=10),
                SourceRoundAdapterResult(source="liepin", status="completed", candidates=liepin_candidates, raw_candidate_count=10),
            ),
            candidates=cts_candidates + liepin_candidates,
            raw_candidate_count=20,
        ),
        tracer=_noop_tracer(tmp_path),
    )

    assert result.search_observation.requested_count == 20
    assert result.search_observation.shortage_count == 0
    assert result.search_observation.unique_new_count == 20
```

- [ ] **Step 4: Run the failing Runtime merge test**

Run:

```bash
uv run pytest tests/test_runtime_state_flow.py::test_source_dispatch_merge_normalizes_candidates_before_identity_rebuild tests/test_runtime_state_flow.py::test_source_dispatch_observation_counts_selected_sources_as_raw_targets -q
```

Expected: fails because `_merge_source_round_dispatch_result` does not accept `tracer`, does not normalize dispatch candidates, and source observation still treats selected CTS + Liepin as a 10-candidate target.

- [ ] **Step 5: Add Runtime normalization helper**

Create `src/seektalent/runtime/candidate_intake.py` with:

```python
from __future__ import annotations

from collections.abc import Iterable

from seektalent.models import NormalizedResume, ResumeCandidate, RunState
from seektalent.normalization import normalize_resume
from seektalent.tracing import RunTracer


def normalize_runtime_candidates(
    *,
    run_state: RunState,
    candidates: Iterable[ResumeCandidate],
    round_no: int,
    tracer: RunTracer | None = None,
) -> dict[str, NormalizedResume]:
    normalized_updates: dict[str, NormalizedResume] = {}
    for candidate in candidates:
        existing = run_state.normalized_store.get(candidate.resume_id)
        if existing is not None:
            normalized_updates[candidate.resume_id] = existing
            continue
        if tracer is not None:
            tracer.emit(
                "resume_normalization_started",
                round_no=round_no,
                resume_id=candidate.resume_id,
                summary=candidate.compact_summary(),
            )
        normalized = normalize_resume(candidate)
        run_state.normalized_store[normalized.resume_id] = normalized
        normalized_updates[normalized.resume_id] = normalized
        if tracer is not None:
            tracer.write_json(
                f"resumes/{normalized.resume_id}.json",
                normalized.model_dump(mode="json"),
            )
    return normalized_updates
```

- [ ] **Step 6: Stop Liepin active lane pre-normalization**

In `src/seektalent/providers/liepin/runtime_lane.py`:

1. Remove the import:

```python
from seektalent.normalization import normalize_resume
```

2. Replace both detail-backed normalized update expressions with:

```python
    normalized_updates = {}
```

- [ ] **Step 7: Normalize during source dispatch merge**

In `src/seektalent/runtime/orchestrator.py`, import:

```python
from seektalent.runtime.candidate_intake import normalize_runtime_candidates
```

Change `_merge_source_round_dispatch_result` signature to:

```python
    def _merge_source_round_dispatch_result(
        self,
        *,
        run_state: RunState,
        dispatch_result: SourceRoundDispatchResult,
        source_plan: tuple[RuntimeSourceLanePlan, ...],
        round_no: int,
        tracer: RunTracer,
    ) -> None:
```

Before `rebuild_candidate_identities(...)`, add:

```python
        for candidate in dispatch_result.candidates:
            run_state.candidate_store[candidate.resume_id] = candidate
            if candidate.resume_id not in run_state.seen_resume_ids:
                run_state.seen_resume_ids.append(candidate.resume_id)
        normalize_runtime_candidates(
            run_state=run_state,
            candidates=dispatch_result.candidates,
            round_no=round_no,
            tracer=tracer,
        )
```

Update the call site in `_execute_multi_source_round_search(...)` to pass `round_no=round_no` and `tracer=tracer`.

In `_round_search_result_from_source_dispatch(...)`, make the source target explicit:

```python
        selected_source_count = max(1, len(dispatch_result.source_results))
        requested_source_count = retrieval_plan.target_new * selected_source_count
```

Then change `SearchObservation(...)` to use source acquisition accounting:

```python
            requested_count=requested_source_count,
            shortage_count=max(0, requested_source_count - len(candidates)),
            exhausted_reason="target_satisfied"
            if len(candidates) >= requested_source_count
            else "source_lanes_exhausted",
```

This preserves the final top-pool target of 10 while making selected CTS + Liepin report a 20-resume raw source target.

- [ ] **Step 8: Reuse normalized resumes in scoring**

In `src/seektalent/runtime/scoring_runtime.py::normalize_scoring_pool`, before emitting `resume_normalization_started`, add:

```python
        existing = normalized_store.get(candidate.resume_id)
        if existing is not None:
            normalized_pool.append(existing)
            continue
```

This keeps scoring inputs stable while preventing adapter-specific renormalization.

- [ ] **Step 9: Verify Runtime normalization tests pass**

Run:

```bash
uv run pytest tests/test_liepin_runtime_source_lane.py::test_liepin_detail_backed_lane_returns_raw_candidates_without_normalized_updates tests/test_runtime_state_flow.py::test_source_dispatch_merge_normalizes_candidates_before_identity_rebuild tests/test_runtime_state_flow.py::test_source_dispatch_observation_counts_selected_sources_as_raw_targets -q
```

Expected: both tests pass.

- [ ] **Step 10: Commit**

Run:

```bash
git add src/seektalent/runtime/candidate_intake.py src/seektalent/providers/liepin/runtime_lane.py src/seektalent/runtime/orchestrator.py src/seektalent/runtime/scoring_runtime.py tests/test_liepin_runtime_source_lane.py tests/test_runtime_state_flow.py
git commit -m "feat: normalize source candidates in runtime intake"
```

---

### Task 3: Add Deterministic Identity Scores And Conflicts

**Files:**
- Modify: `src/seektalent/models.py`
- Modify: `src/seektalent/runtime/source_lanes.py`
- Modify: `tests/test_runtime_candidate_identity.py`

- [ ] **Step 1: Write failing auto-merge and conflict tests**

In `tests/test_runtime_candidate_identity.py`, add:

```python
def test_identity_index_auto_merges_visible_name_with_strong_profile_corroborration() -> None:
    index = RuntimeCandidateIdentityIndex()
    cts_identity = index.upsert_candidate(
        resume_id="cts-1",
        evidence_id="evidence-cts",
        signals=_signals(
            name="Alice Chen",
            masked=False,
            company="Acme Robotics",
            title="Senior AI Engineer",
            school=("Tsinghua University",),
            chronology=("acme robotics:senior ai engineer:2024-present",),
            provider_hash="cts-provider",
        ),
    )
    liepin_identity = index.upsert_candidate(
        resume_id="liepin-1",
        evidence_id="evidence-liepin",
        signals=_signals(
            name="Alice Chen",
            masked=False,
            company="Acme Robotics",
            title="AI Engineer",
            school=("Tsinghua University",),
            chronology=("acme robotics:ai engineer:2024-present",),
            provider_hash="liepin-provider",
        ),
    )

    assert liepin_identity.identity_id == cts_identity.identity_id
    assert index.conflicts() == ()


def test_identity_index_records_medium_confidence_conflict_without_merge() -> None:
    index = RuntimeCandidateIdentityIndex()
    first = index.upsert_candidate(
        resume_id="cts-1",
        evidence_id="evidence-cts",
        signals=_signals(
            name="Alice Chen",
            masked=False,
            company="Acme Robotics",
            title="Senior AI Engineer",
            school=("Tsinghua University",),
            chronology=(),
            provider_hash="cts-provider",
        ),
    )
    second = index.upsert_candidate(
        resume_id="liepin-1",
        evidence_id="evidence-liepin",
        signals=_signals(
            name="Alice Chen",
            masked=False,
            company="Acme Robotics",
            title="Senior AI Engineer",
            school=(),
            chronology=(),
            provider_hash="liepin-provider",
        ),
    )

    assert second.identity_id != first.identity_id
    conflicts = index.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].match_score == 75
    assert set(conflicts[0].resume_ids) == {"cts-1", "liepin-1"}
```

Keep the existing masked-name and name-only tests unchanged.

- [ ] **Step 2: Run the failing identity tests**

Run:

```bash
uv run pytest tests/test_runtime_candidate_identity.py::test_identity_index_auto_merges_visible_name_with_strong_profile_corroborration tests/test_runtime_candidate_identity.py::test_identity_index_records_medium_confidence_conflict_without_merge -q
```

Expected: fails because `RuntimeCandidateIdentityIndex.conflicts` and `RuntimeIdentityConflict.match_score` do not exist.

- [ ] **Step 3: Extend identity models**

In `src/seektalent/models.py`, add fields to `RuntimeIdentitySignals` after `protected_contact_hashes`:

```python
    years_of_experience: int | None = None
    location_norms: tuple[str, ...] = ()
    skill_norms: tuple[str, ...] = ()
```

Update `to_public_payload()` with:

```python
            "years_of_experience": self.years_of_experience,
            "location_norms": list(self.location_norms),
            "skill_norms": list(self.skill_norms),
```

Add fields to `RuntimeIdentityConflict`:

```python
    resume_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    match_score: int | None = Field(default=None, ge=0, le=100)
```

Keep `evidence_ids` only once in the class. Update `to_public_payload()` with:

```python
            "resume_ids": list(self.resume_ids),
            "match_score": self.match_score,
```

- [ ] **Step 4: Store signals and conflicts in the identity index**

In `src/seektalent/runtime/source_lanes.py::RuntimeCandidateIdentityIndex.__init__`, add:

```python
        self._signals_by_identity_id: dict[str, RuntimeIdentitySignals] = {}
        self._conflicts_by_id: dict[str, RuntimeIdentityConflict] = {}
```

Add this public method to the class:

```python
    def conflicts(self) -> tuple[RuntimeIdentityConflict, ...]:
        return tuple(self._conflicts_by_id[key] for key in sorted(self._conflicts_by_id))
```

Import `RuntimeIdentityConflict` from `seektalent.models`.

- [ ] **Step 5: Add deterministic match helpers**

In `src/seektalent/runtime/source_lanes.py`, add these helpers below `_strongest_signal_code(...)`:

```python
def _identity_match_score(left: RuntimeIdentitySignals, right: RuntimeIdentitySignals) -> int:
    if set(left.protected_contact_hashes) & set(right.protected_contact_hashes):
        return 100
    if left.provider_candidate_key_hash and left.provider_candidate_key_hash == right.provider_candidate_key_hash:
        return 95
    if left.is_masked_name or right.is_masked_name:
        return 0
    if not left.normalized_name or left.normalized_name != right.normalized_name:
        return 0

    score = 40
    if left.current_company_norm and left.current_company_norm == right.current_company_norm:
        score += 20
    if _same_or_similar_text(left.current_title_norm, right.current_title_norm, threshold=0.6):
        score += 15
    if set(left.school_norms) & set(right.school_norms):
        score += 15
    if set(left.work_chronology_fingerprints) & set(right.work_chronology_fingerprints):
        score += 15
    if (
        left.years_of_experience is not None
        and right.years_of_experience is not None
        and abs(left.years_of_experience - right.years_of_experience) <= 1
    ):
        score += 5
    if set(left.location_norms) & set(right.location_norms):
        score += 5
    if _token_overlap(left.skill_norms, right.skill_norms) >= 0.3:
        score += 5
    return min(score, 95)


def _same_or_similar_text(left: str | None, right: str | None, *, threshold: float) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    return _token_overlap((left,), (right,)) >= threshold


def _token_overlap(left_values: tuple[str, ...], right_values: tuple[str, ...]) -> float:
    left_tokens = _identity_tokens(left_values)
    right_tokens = _identity_tokens(right_values)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _identity_tokens(values: tuple[str, ...]) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        tokens.update(token for token in re.split(r"[\s,/|:;()_-]+", value) if token)
    return tokens
```

- [ ] **Step 6: Use deterministic scores in `upsert_candidate`**

In `RuntimeCandidateIdentityIndex.upsert_candidate(...)`, after collecting `existing_identity_ids`, add:

```python
        scored_identity_ids: list[tuple[int, str]] = []
        for identity_id, existing_signals in self._signals_by_identity_id.items():
            if identity_id in existing_identity_ids:
                continue
            score = _identity_match_score(existing_signals, signals)
            if score >= 85:
                existing_identity_ids.add(identity_id)
            elif score >= 70:
                scored_identity_ids.append((score, identity_id))
```

After `_ensure_identity(...)`, before returning, record conflicts for medium scores:

```python
        for score, conflict_identity_id in scored_identity_ids:
            if conflict_identity_id == target_identity_id:
                continue
            conflict_identity = self._identities[conflict_identity_id]
            conflict_id = _stable_identity_id(
                "conflict:"
                + "||".join(sorted([target_identity_id, conflict_identity_id, resume_id, evidence_id]))
            )
            self._conflicts_by_id[conflict_id] = RuntimeIdentityConflict(
                conflict_id=conflict_id,
                candidate_identity_ids=tuple(sorted([target_identity_id, conflict_identity_id])),
                resume_ids=tuple(sorted(set(conflict_identity.resume_ids) | {resume_id})),
                reason_code="medium_confidence_identity_match",
                evidence_ids=tuple(sorted(set(conflict_identity.evidence_ids) | {evidence_id})),
                match_score=score,
            )
```

After updating `self._identities[target_identity_id]`, add:

```python
        self._signals_by_identity_id[target_identity_id] = _merge_identity_signals(
            self._signals_by_identity_id.get(target_identity_id),
            signals,
        )
```

When merging identities in `_merge_identity`, also merge stored signals:

```python
        self._signals_by_identity_id[target_identity_id] = _merge_identity_signals(
            self._signals_by_identity_id.get(target_identity_id),
            self._signals_by_identity_id.pop(old_identity_id, None),
        )
```

- [ ] **Step 7: Add signal merge helper**

In `src/seektalent/runtime/source_lanes.py`, add:

```python
def _merge_identity_signals(
    left: RuntimeIdentitySignals | None,
    right: RuntimeIdentitySignals | None,
) -> RuntimeIdentitySignals:
    if left is None:
        return right or RuntimeIdentitySignals()
    if right is None:
        return left
    return RuntimeIdentitySignals(
        normalized_name=left.normalized_name or right.normalized_name,
        is_masked_name=left.is_masked_name and right.is_masked_name,
        current_company_norm=left.current_company_norm or right.current_company_norm,
        current_title_norm=left.current_title_norm or right.current_title_norm,
        school_norms=tuple(sorted(set(left.school_norms) | set(right.school_norms))),
        work_chronology_fingerprints=tuple(
            sorted(set(left.work_chronology_fingerprints) | set(right.work_chronology_fingerprints))
        ),
        provider_candidate_key_hash=left.provider_candidate_key_hash or right.provider_candidate_key_hash,
        protected_contact_hashes=tuple(sorted(set(left.protected_contact_hashes) | set(right.protected_contact_hashes))),
        years_of_experience=left.years_of_experience if left.years_of_experience is not None else right.years_of_experience,
        location_norms=tuple(sorted(set(left.location_norms) | set(right.location_norms))),
        skill_norms=tuple(sorted(set(left.skill_norms) | set(right.skill_norms))),
    )
```

- [ ] **Step 8: Populate new signals from normalized resumes**

In `_identity_signals_for_candidate(...)`, add:

```python
        years_of_experience=normalized.years_of_experience if normalized else candidate.work_year,
        location_norms=tuple(_normalize_identity_text(item) for item in (normalized.locations if normalized else []) if item),
        skill_norms=tuple(_normalize_identity_text(item) for item in (normalized.skills if normalized else []) if item),
```

- [ ] **Step 9: Write conflicts into RunState**

In `_rebuild_identity_state(...)`, after:

```python
    run_state.identity_aliases_by_canonical_id = aliases_by_canonical_id
```

add:

```python
    run_state.identity_conflicts = list(index.conflicts())
```

- [ ] **Step 10: Verify identity tests pass**

Run:

```bash
uv run pytest tests/test_runtime_candidate_identity.py -q
```

Expected: all identity tests pass, including existing masked-name and name-only safeguards.

- [ ] **Step 11: Commit**

Run:

```bash
git add src/seektalent/models.py src/seektalent/runtime/source_lanes.py tests/test_runtime_candidate_identity.py
git commit -m "feat: add deterministic runtime identity conflicts"
```

---

### Task 4: Build Canonical Scoring Intake Before Scorer Calls

**Files:**
- Modify: `src/seektalent/runtime/candidate_intake.py`
- Modify: `src/seektalent/runtime/scoring_runtime.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `tests/test_runtime_state_flow.py`

- [ ] **Step 1: Write a failing same-round cross-source scoring intake test**

In `tests/test_runtime_state_flow.py`, add this helper near `_run_state_for_canonical_intake_tests(...)`:

```python
def _scored_candidate(
    resume_id: str,
    *,
    source_round: int = 1,
    overall_score: int = 88,
) -> ScoredCandidate:
    return ScoredCandidate(
        resume_id=resume_id,
        fit_bucket="fit",
        overall_score=overall_score,
        must_have_match_score=90,
        preferred_match_score=80,
        risk_score=10,
        reasoning_summary=f"{resume_id} matches the role.",
        evidence=["Python retrieval evidence."],
        confidence="high",
        matched_must_haves=["Python"],
        source_round=source_round,
    )
```

Then add:

```python
def test_canonical_scoring_intake_scores_one_candidate_for_same_identity(tmp_path) -> None:
    run_state = _run_state_for_canonical_intake_tests()
    cts = _make_candidate(
        "cts-1",
        raw={"provider": "cts", "candidate_name": "Alice Chen", "current_company": "Acme", "current_title": "Senior AI Engineer"},
    )
    liepin = _make_candidate(
        "liepin-1",
        raw={"provider": "liepin", "candidate_name": "Alice Chen", "current_company": "Acme", "current_title": "AI Engineer"},
    )
    run_state.candidate_store = {cts.resume_id: cts, liepin.resume_id: liepin}
    normalize_runtime_candidates(run_state=run_state, candidates=(cts, liepin), round_no=1, tracer=None)
    rebuild_candidate_identities(run_state, source_order={"cts": 0, "liepin": 1})

    intake = build_canonical_scoring_intake(
        run_state=run_state,
        round_no=1,
        new_candidates=[cts, liepin],
        selected_source_kinds=("cts", "liepin"),
        source_raw_targets={"cts": 10, "liepin": 10},
    )

    assert len(intake.scoring_candidates) == 1
    assert intake.summary.auto_merged_duplicate_count == 1
    assert intake.summary.source_raw_targets == {"cts": 10, "liepin": 10}
    assert intake.summary.per_source_raw_counts == {"cts": 1, "liepin": 1}
    assert set(intake.summary.canonical_resume_ids) == {intake.scoring_candidates[0].resume_id}
```

Add imports:

```python
from seektalent.runtime.candidate_intake import build_canonical_scoring_intake, normalize_runtime_candidates
from seektalent.runtime.source_lanes import rebuild_candidate_identities
```

- [ ] **Step 2: Write a failing cross-round already-scored identity test**

In `tests/test_runtime_state_flow.py`, add:

```python
def test_canonical_scoring_intake_skips_already_scored_identity() -> None:
    run_state = _run_state_for_canonical_intake_tests()
    old_candidate = _make_candidate(
        "cts-old",
        raw={"provider": "cts", "candidate_name": "Alice Chen", "current_company": "Acme", "current_title": "AI Engineer"},
    )
    new_candidate = _make_candidate(
        "liepin-new",
        raw={"provider": "liepin", "candidate_name": "Alice Chen", "current_company": "Acme", "current_title": "AI Engineer"},
    )
    run_state.candidate_store = {old_candidate.resume_id: old_candidate, new_candidate.resume_id: new_candidate}
    normalize_runtime_candidates(run_state=run_state, candidates=(old_candidate, new_candidate), round_no=1, tracer=None)
    rebuild_candidate_identities(run_state, source_order={"cts": 0, "liepin": 1})
    run_state.scorecards_by_resume_id[old_candidate.resume_id] = _scored_candidate(old_candidate.resume_id, source_round=1)

    intake = build_canonical_scoring_intake(
        run_state=run_state,
        round_no=2,
        new_candidates=[new_candidate],
        selected_source_kinds=("cts", "liepin"),
        source_raw_targets={"cts": 10, "liepin": 10},
    )

    assert intake.scoring_candidates == []
    assert intake.summary.skipped_already_scored_identity_count == 1
```

Also add a regression proving the latest summary is not polluted by cumulative conflicts from older rounds:

```python
def test_canonical_scoring_intake_conflict_count_is_round_scoped() -> None:
    run_state = _run_state_for_canonical_intake_tests()
    candidate = _make_candidate(
        "cts-new",
        raw={"provider": "cts", "candidate_name": "Bob Lee", "current_company": "Beta", "current_title": "Data Engineer"},
    )
    run_state.candidate_store = {candidate.resume_id: candidate}
    normalize_runtime_candidates(run_state=run_state, candidates=(candidate,), round_no=2, tracer=None)
    rebuild_candidate_identities(run_state, source_order={"cts": 0, "liepin": 1})
    run_state.identity_conflicts = [
        RuntimeIdentityConflict(
            conflict_id="conflict-old",
            candidate_identity_ids=("identity-old-a", "identity-old-b"),
            resume_ids=("old-a", "old-b"),
            reason_code="medium_confidence_identity_match",
            match_score=75,
        )
    ]

    intake = build_canonical_scoring_intake(
        run_state=run_state,
        round_no=2,
        new_candidates=[candidate],
        selected_source_kinds=("cts", "liepin"),
        source_raw_targets={"cts": 10, "liepin": 10},
    )

    assert intake.summary.uncertain_conflict_count == 0
```

Add imports:

```python
from seektalent.models import RuntimeIdentityConflict
```

- [ ] **Step 3: Run the failing canonical intake tests**

Run:

```bash
uv run pytest tests/test_runtime_state_flow.py::test_canonical_scoring_intake_scores_one_candidate_for_same_identity tests/test_runtime_state_flow.py::test_canonical_scoring_intake_skips_already_scored_identity tests/test_runtime_state_flow.py::test_canonical_scoring_intake_conflict_count_is_round_scoped -q
```

Expected: fails because `build_canonical_scoring_intake` and `RuntimeCanonicalIntakeSummary` do not exist.

- [ ] **Step 4: Add canonical intake summary model**

In `src/seektalent/models.py`, add this model near the runtime identity models:

```python
class RuntimeCanonicalIntakeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_no: int | None = None
    selected_source_kinds: tuple[RuntimeSourceKind, ...] = ()
    source_raw_targets: dict[str, int] = Field(default_factory=dict)
    raw_candidate_count: int = 0
    normalized_candidate_count: int = 0
    identity_count: int = 0
    auto_merged_duplicate_count: int = 0
    uncertain_conflict_count: int = 0
    skipped_already_scored_identity_count: int = 0
    scoring_candidate_count: int = 0
    canonical_resume_ids: tuple[str, ...] = ()
    per_source_raw_counts: dict[str, int] = Field(default_factory=dict)
    per_source_normalized_counts: dict[str, int] = Field(default_factory=dict)

    def to_public_payload(self) -> dict[str, object]:
        return {
            "schema_version": "runtime_canonical_intake_summary_v1",
            "round_no": self.round_no,
            "selected_source_kinds": list(self.selected_source_kinds),
            "source_raw_targets": dict(self.source_raw_targets),
            "raw_candidate_count": self.raw_candidate_count,
            "normalized_candidate_count": self.normalized_candidate_count,
            "identity_count": self.identity_count,
            "auto_merged_duplicate_count": self.auto_merged_duplicate_count,
            "uncertain_conflict_count": self.uncertain_conflict_count,
            "skipped_already_scored_identity_count": self.skipped_already_scored_identity_count,
            "scoring_candidate_count": self.scoring_candidate_count,
            "canonical_resume_ids": list(self.canonical_resume_ids),
            "per_source_raw_counts": dict(self.per_source_raw_counts),
            "per_source_normalized_counts": dict(self.per_source_normalized_counts),
        }
```

Add to `RunState`:

```python
    latest_canonical_intake_summary: RuntimeCanonicalIntakeSummary | None = None
```

- [ ] **Step 5: Add canonical intake dataclass and builder**

In `src/seektalent/runtime/candidate_intake.py`, extend imports:

```python
from collections import Counter
from dataclasses import dataclass

from seektalent.evaluation import TOP_K
from seektalent.models import RuntimeCanonicalIntakeSummary, ScoredCandidate, scored_candidate_sort_key
```

Add:

```python
@dataclass(frozen=True, kw_only=True)
class CanonicalScoringIntake:
    scoring_candidates: list[ResumeCandidate]
    summary: RuntimeCanonicalIntakeSummary


def build_canonical_scoring_intake(
    *,
    run_state: RunState,
    round_no: int,
    new_candidates: list[ResumeCandidate],
    selected_source_kinds: tuple[str, ...] = (),
    source_raw_targets: dict[str, int] | None = None,
) -> CanonicalScoringIntake:
    scored_identity_ids = {
        run_state.candidate_identity_by_resume_id.get(resume_id, resume_id)
        for resume_id in run_state.scorecards_by_resume_id
    }
    candidate_by_resume_id = {candidate.resume_id: candidate for candidate in new_candidates}
    first_resume_by_identity: dict[str, str] = {}
    per_source_counts: Counter[str] = Counter()
    per_source_normalized_counts: Counter[str] = Counter()
    for candidate in new_candidates:
        normalized = run_state.normalized_store.get(candidate.resume_id)
        provider = normalized.source_provider if normalized is not None else None
        per_source_counts[provider or "unknown"] += 1
        if normalized is not None:
            per_source_normalized_counts[provider or "unknown"] += 1
        identity_id = run_state.candidate_identity_by_resume_id.get(candidate.resume_id, candidate.resume_id)
        first_resume_by_identity.setdefault(identity_id, candidate.resume_id)

    scoring_candidates: list[ResumeCandidate] = []
    skipped_already_scored = 0
    for identity_id, first_resume_id in first_resume_by_identity.items():
        if identity_id in scored_identity_ids:
            skipped_already_scored += 1
            continue
        canonical = run_state.canonical_resume_by_identity_id.get(identity_id)
        canonical_resume_id = canonical.canonical_resume_id if canonical is not None else first_resume_id
        candidate = candidate_by_resume_id.get(canonical_resume_id) or run_state.candidate_store.get(canonical_resume_id)
        if candidate is None:
            continue
        scoring_candidates.append(candidate)

    duplicate_count = max(0, len(new_candidates) - len(first_resume_by_identity))
    new_resume_ids = set(candidate_by_resume_id)
    round_conflicts = [
        conflict
        for conflict in run_state.identity_conflicts
        if new_resume_ids & set(conflict.resume_ids)
    ]
    if not selected_source_kinds and run_state.source_coverage_summary is not None:
        selected_source_kinds = tuple(run_state.source_coverage_summary.selected_source_kinds)
    summary = RuntimeCanonicalIntakeSummary(
        round_no=round_no,
        selected_source_kinds=tuple(selected_source_kinds),
        source_raw_targets=dict(sorted((source_raw_targets or {}).items())),
        raw_candidate_count=len(new_candidates),
        normalized_candidate_count=sum(1 for candidate in new_candidates if candidate.resume_id in run_state.normalized_store),
        identity_count=len(first_resume_by_identity),
        auto_merged_duplicate_count=duplicate_count,
        uncertain_conflict_count=len(round_conflicts),
        skipped_already_scored_identity_count=skipped_already_scored,
        scoring_candidate_count=len(scoring_candidates),
        canonical_resume_ids=tuple(candidate.resume_id for candidate in scoring_candidates),
        per_source_raw_counts=dict(sorted(per_source_counts.items())),
        per_source_normalized_counts=dict(sorted(per_source_normalized_counts.items())),
    )
    run_state.latest_canonical_intake_summary = summary
    return CanonicalScoringIntake(scoring_candidates=scoring_candidates, summary=summary)
```

- [ ] **Step 6: Add identity-level top-pool selector**

In `src/seektalent/runtime/candidate_intake.py`, add:

```python
def select_identity_top_candidates(run_state: RunState) -> list[ScoredCandidate]:
    selected: list[ScoredCandidate] = []
    seen_identity_ids: set[str] = set()
    for scored in sorted(run_state.scorecards_by_resume_id.values(), key=scored_candidate_sort_key):
        identity_id = run_state.candidate_identity_by_resume_id.get(scored.resume_id, scored.resume_id)
        if identity_id in seen_identity_ids:
            continue
        canonical = run_state.canonical_resume_by_identity_id.get(identity_id)
        selected_resume_id = canonical.canonical_resume_id if canonical is not None else scored.resume_id
        selected_score = run_state.scorecards_by_resume_id.get(selected_resume_id, scored)
        selected.append(selected_score)
        seen_identity_ids.add(identity_id)
        if len(selected) >= TOP_K:
            break
    run_state.top_pool_ids = [candidate.resume_id for candidate in selected]
    return selected
```

- [ ] **Step 7: Use canonical intake in scoring**

In `src/seektalent/runtime/scoring_runtime.py`, import:

```python
from seektalent.runtime.candidate_intake import build_canonical_scoring_intake, select_identity_top_candidates
```

Extend `score_round(...)` with source accounting inputs:

```python
    selected_source_kinds: tuple[str, ...] = (),
    source_raw_targets: dict[str, int] | None = None,
```

Replace the current scoring pool construction:

```python
    scoring_pool = build_scoring_pool(
        new_candidates=new_candidates,
        scorecards_by_resume_id=run_state.scorecards_by_resume_id,
    )
```

with:

```python
    canonical_intake = build_canonical_scoring_intake(
        run_state=run_state,
        round_no=round_no,
        new_candidates=new_candidates,
        selected_source_kinds=selected_source_kinds,
        source_raw_targets=source_raw_targets,
    )
    scoring_pool = build_scoring_pool(
        new_candidates=canonical_intake.scoring_candidates,
        scorecards_by_resume_id=run_state.scorecards_by_resume_id,
    )
```

Replace:

```python
    global_ranked_candidates = sorted(run_state.scorecards_by_resume_id.values(), key=scored_candidate_sort_key)
    current_top_candidates = global_ranked_candidates[:TOP_K]
    run_state.top_pool_ids = [item.resume_id for item in current_top_candidates]
```

with:

```python
    current_top_candidates = select_identity_top_candidates(run_state)
```

In `src/seektalent/runtime/orchestrator.py`, extend the private `_score_round(...)` wrapper and its call to `score_round(...)` with the same two inputs. In `_run_rounds(...)`, pass the selected source target explicitly:

```python
                    selected_source_kinds=tuple(lane.source for lane in source_plan),
                    source_raw_targets={lane.source: target_new for lane in source_plan},
```

- [ ] **Step 8: Make orchestrator delegate the existing identity top-pool helper**

In `src/seektalent/runtime/orchestrator.py`, import `select_identity_top_candidates` from `candidate_intake`.

Replace `_apply_identity_top_pool(...)` body with:

```python
    def _apply_identity_top_pool(self, run_state: RunState) -> list[ScoredCandidate]:
        return select_identity_top_candidates(run_state)
```

This keeps existing callers working while removing a second implementation of identity top-pool selection.

- [ ] **Step 9: Verify canonical intake tests pass**

Run:

```bash
uv run pytest tests/test_runtime_state_flow.py::test_canonical_scoring_intake_scores_one_candidate_for_same_identity tests/test_runtime_state_flow.py::test_canonical_scoring_intake_skips_already_scored_identity tests/test_runtime_state_flow.py::test_canonical_scoring_intake_conflict_count_is_round_scoped -q
```

Expected: both tests pass.

- [ ] **Step 10: Commit**

Run:

```bash
git add src/seektalent/models.py src/seektalent/runtime/candidate_intake.py src/seektalent/runtime/scoring_runtime.py src/seektalent/runtime/orchestrator.py tests/test_runtime_state_flow.py
git commit -m "feat: score canonical runtime identities"
```

---

### Task 5: Feed Canonical Intake Summary Into Reflection And Controller

**Files:**
- Modify: `src/seektalent/models.py`
- Modify: `src/seektalent/runtime/reflection_context.py`
- Modify: `src/seektalent/runtime/controller_context.py`
- Modify: `tests/test_runtime_state_flow.py`

- [ ] **Step 1: Write failing reflection context test**

In `tests/test_runtime_state_flow.py`, add this helper near `_run_state_for_canonical_intake_tests(...)`:

```python
def _round_state_for_reflection_tests(round_no: int) -> RoundState:
    controller_decision = SearchControllerDecision(
        thought_summary="Search one more round.",
        action="search_cts",
        decision_rationale="Need more candidates.",
        proposed_query_terms=["python", "retrieval"],
        proposed_filter_plan=ProposedFilterPlan(),
    )
    retrieval_plan = RoundRetrievalPlan(
        plan_version=1,
        round_no=round_no,
        query_terms=["python", "retrieval"],
        role_anchor_terms=["python"],
        must_have_anchor_terms=["retrieval"],
        keyword_query="python retrieval",
        location_execution_plan=LocationExecutionPlan(mode="none", target_new=10),
        target_new=10,
        rationale="Need more candidates.",
    )
    return RoundState(
        round_no=round_no,
        controller_decision=controller_decision,
        retrieval_plan=retrieval_plan,
        search_observation=SearchObservation(
            round_no=round_no,
            requested_count=10,
            raw_candidate_count=20,
            unique_new_count=17,
            shortage_count=0,
            fetch_attempt_count=2,
            new_resume_ids=[],
        ),
    )
```

Then add:

```python
def test_reflection_context_includes_latest_canonical_intake_summary() -> None:
    run_state = _run_state_for_canonical_intake_tests()
    run_state.latest_canonical_intake_summary = RuntimeCanonicalIntakeSummary(
        round_no=1,
        selected_source_kinds=("cts", "liepin"),
        source_raw_targets={"cts": 10, "liepin": 10},
        raw_candidate_count=20,
        normalized_candidate_count=20,
        identity_count=17,
        auto_merged_duplicate_count=3,
        uncertain_conflict_count=1,
        skipped_already_scored_identity_count=2,
        scoring_candidate_count=15,
        canonical_resume_ids=("resume-1",),
        per_source_raw_counts={"cts": 10, "liepin": 10},
    )
    round_state = _round_state_for_reflection_tests(round_no=1)

    context = build_reflection_context(run_state=run_state, round_state=round_state)

    assert context.canonical_intake_summary is not None
    assert context.canonical_intake_summary.auto_merged_duplicate_count == 3
    assert context.canonical_intake_summary.source_raw_targets == {"cts": 10, "liepin": 10}
    assert context.canonical_intake_summary.per_source_raw_counts == {"cts": 10, "liepin": 10}
```

Add imports:

```python
from seektalent.models import RuntimeCanonicalIntakeSummary
from seektalent.runtime.controller_context import build_controller_context
from seektalent.runtime.reflection_context import build_reflection_context
```

- [ ] **Step 2: Write failing controller context test**

In `tests/test_runtime_state_flow.py`, add:

```python
def test_controller_context_includes_latest_canonical_intake_summary() -> None:
    run_state = _run_state_for_canonical_intake_tests()
    run_state.latest_canonical_intake_summary = RuntimeCanonicalIntakeSummary(
        round_no=1,
        selected_source_kinds=("cts", "liepin"),
        source_raw_targets={"cts": 10, "liepin": 10},
        raw_candidate_count=20,
        normalized_candidate_count=20,
        identity_count=18,
        auto_merged_duplicate_count=2,
        uncertain_conflict_count=0,
        skipped_already_scored_identity_count=1,
        scoring_candidate_count=17,
        canonical_resume_ids=("resume-1", "resume-2"),
        per_source_raw_counts={"cts": 10, "liepin": 10},
    )

    context = build_controller_context(
        run_state=run_state,
        round_no=2,
        min_rounds=1,
        max_rounds=3,
        target_new=10,
    )

    assert context.latest_canonical_intake_summary is not None
    assert context.latest_canonical_intake_summary.identity_count == 18
```

- [ ] **Step 3: Run the failing context tests**

Run:

```bash
uv run pytest tests/test_runtime_state_flow.py::test_reflection_context_includes_latest_canonical_intake_summary tests/test_runtime_state_flow.py::test_controller_context_includes_latest_canonical_intake_summary -q
```

Expected: fails because the context fields are absent.

- [ ] **Step 4: Add context fields**

In `src/seektalent/models.py`, add to `ReflectionContext`:

```python
    canonical_intake_summary: RuntimeCanonicalIntakeSummary | None = None
```

Add to `ControllerContext`:

```python
    latest_canonical_intake_summary: RuntimeCanonicalIntakeSummary | None = None
```

- [ ] **Step 5: Populate reflection context**

In `src/seektalent/runtime/reflection_context.py::build_reflection_context`, add:

```python
        canonical_intake_summary=run_state.latest_canonical_intake_summary,
```

- [ ] **Step 6: Populate controller context**

In `src/seektalent/runtime/controller_context.py::build_controller_context`, add:

```python
        latest_canonical_intake_summary=run_state.latest_canonical_intake_summary,
```

- [ ] **Step 7: Verify context tests pass**

Run:

```bash
uv run pytest tests/test_runtime_state_flow.py::test_reflection_context_includes_latest_canonical_intake_summary tests/test_runtime_state_flow.py::test_controller_context_includes_latest_canonical_intake_summary -q
```

Expected: both tests pass.

- [ ] **Step 8: Commit**

Run:

```bash
git add src/seektalent/models.py src/seektalent/runtime/reflection_context.py src/seektalent/runtime/controller_context.py tests/test_runtime_state_flow.py
git commit -m "feat: expose canonical intake summary to runtime contexts"
```

---

### Task 6: Verify End-To-End Runtime Canonical Intake

**Files:**
- Modify: `tests/test_runtime_source_lanes.py`
- Modify: `tests/test_runtime_state_flow.py`
- Modify: changed source files from previous tasks

- [ ] **Step 1: Add source evidence preservation regression**

In `tests/test_runtime_source_lanes.py`, add:

```python
def test_identity_merge_preserves_cts_and_liepin_source_evidence() -> None:
    run_state = _run_state()
    cts = _candidate("cts-1").model_copy(
        update={"raw": {"provider": "cts", "candidate_name": "Alice Chen", "current_company": "Acme"}}
    )
    liepin = _candidate("liepin-1").model_copy(
        update={"raw": {"provider": "liepin", "candidate_name": "Alice Chen", "current_company": "Acme"}}
    )
    run_state.candidate_store = {cts.resume_id: cts, liepin.resume_id: liepin}
    normalize_runtime_candidates(run_state=run_state, candidates=(cts, liepin), round_no=1, tracer=None)
    run_state.source_evidence_by_resume_id = {
        "cts-1": [
            _evidence("evidence-cts", resume_id="cts-1", source="cts", evidence_level="detail").model_copy(
                update={"provider_candidate_key_hash": "same-person-hash"}
            )
        ],
        "liepin-1": [
            _evidence("evidence-liepin", resume_id="liepin-1", source="liepin", evidence_level="detail").model_copy(
                update={"provider_candidate_key_hash": "same-person-hash"}
            )
        ],
    }

    rebuild_candidate_identities(run_state, source_order={"cts": 0, "liepin": 1})

    identity_ids = set(run_state.candidate_identity_by_resume_id.values())
    assert len(identity_ids) == 1
    identity_id = next(iter(identity_ids))
    assert {item.source for item in run_state.source_evidence_by_identity_id[identity_id]} == {"cts", "liepin"}
```

Add imports:

```python
from seektalent.runtime.candidate_intake import normalize_runtime_candidates
from seektalent.runtime.source_lanes import rebuild_candidate_identities
```

- [ ] **Step 2: Add top-pool identity uniqueness regression**

In `tests/test_runtime_state_flow.py`, add:

```python
def test_identity_top_pool_contains_one_scorecard_per_identity() -> None:
    run_state = _run_state_for_canonical_intake_tests()
    run_state.candidate_identity_by_resume_id = {
        "cts-1": "identity-1",
        "liepin-1": "identity-1",
        "cts-2": "identity-2",
    }
    run_state.canonical_resume_by_identity_id = {
        "identity-1": RuntimeCanonicalResumeSelection(
            identity_id="identity-1",
            canonical_resume_id="liepin-1",
            safe_reason_codes=("detail_evidence",),
        ),
        "identity-2": RuntimeCanonicalResumeSelection(
            identity_id="identity-2",
            canonical_resume_id="cts-2",
            safe_reason_codes=("provider_rank_preserved",),
        ),
    }
    run_state.scorecards_by_resume_id = {
        "cts-1": _scored_candidate("cts-1", overall_score=95),
        "liepin-1": _scored_candidate("liepin-1", overall_score=90),
        "cts-2": _scored_candidate("cts-2", overall_score=80),
    }

    selected = select_identity_top_candidates(run_state)

    assert [item.resume_id for item in selected] == ["liepin-1", "cts-2"]
    assert run_state.top_pool_ids == ["liepin-1", "cts-2"]
```

Also add the finalizer regression:

```python
def test_finalize_context_uses_identity_deduped_top_pool() -> None:
    run_state = _run_state_for_canonical_intake_tests()
    run_state.candidate_identity_by_resume_id = {
        "cts-1": "identity-1",
        "liepin-1": "identity-1",
        "cts-2": "identity-2",
    }
    run_state.canonical_resume_by_identity_id = {
        "identity-1": RuntimeCanonicalResumeSelection(
            identity_id="identity-1",
            canonical_resume_id="liepin-1",
            safe_reason_codes=("detail_evidence",),
        ),
        "identity-2": RuntimeCanonicalResumeSelection(
            identity_id="identity-2",
            canonical_resume_id="cts-2",
            safe_reason_codes=("provider_rank_preserved",),
        ),
    }
    run_state.scorecards_by_resume_id = {
        "cts-1": _scored_candidate("cts-1", overall_score=95),
        "liepin-1": _scored_candidate("liepin-1", overall_score=90),
        "cts-2": _scored_candidate("cts-2", overall_score=80),
    }
    select_identity_top_candidates(run_state)

    context = build_finalize_context(
        run_state=run_state,
        rounds_executed=1,
        stop_reason="max_rounds_reached",
        run_id="run-test",
        run_dir="/tmp/run-test",
    )

    assert [item.resume_id for item in context.top_candidates] == ["liepin-1", "cts-2"]
```

Add imports:

```python
from seektalent.models import RuntimeCanonicalResumeSelection
from seektalent.runtime.candidate_intake import select_identity_top_candidates
from seektalent.runtime.finalize_context import build_finalize_context
```

- [ ] **Step 3: Run focused runtime tests**

Run:

```bash
uv run pytest tests/test_normalization.py tests/test_runtime_candidate_identity.py tests/test_runtime_source_lanes.py tests/test_runtime_state_flow.py tests/test_flywheel_runtime.py -q
```

Expected: all tests pass.

- [ ] **Step 4: Run static checks**

Run:

```bash
uv run ruff check src/seektalent tests
```

Expected: pass with no lint errors.

- [ ] **Step 5: Inspect for accidental LLM duplicate adjudication**

Run:

```bash
rg -n "duplicate|dedupe|identity|same person|same-person" src/seektalent | rg "llm|model|prompt|openai|completion|structured"
```

Expected: no result showing an LLM prompt or model call that decides duplicate identity. Existing scoring/reflection/model references may appear only if they do not adjudicate duplicate identity.

- [ ] **Step 6: Inspect source provenance and active role boundaries**

Run:

```bash
rg -n "normalized_store_updates|source_provider|provider\"\\] = \"cts\"|latest_canonical_intake_summary" src/seektalent tests
```

Expected:

- `normalized_store_updates` remains as a result field but active Liepin detail-backed paths return `{}`.
- CTS raw mapping sets provider/source markers.
- `NormalizedResume` exposes `source_provider`.
- Reflection/controller context exposes `latest_canonical_intake_summary`.

- [ ] **Step 7: Commit verification fixes**

If Step 3 through Step 6 required code fixes, commit them:

```bash
git add src/seektalent tests
git commit -m "test: verify runtime canonical intake dedupe"
```

If no fixes were required, do not create an empty commit.

---

## Final Verification

Run:

```bash
uv run pytest tests/test_normalization.py tests/test_runtime_candidate_identity.py tests/test_runtime_source_lanes.py tests/test_runtime_state_flow.py tests/test_flywheel_runtime.py -q
uv run ruff check src/seektalent tests
```

Expected: both commands pass.

Then inspect:

```bash
git status --short
git log --oneline -6
```

Expected: working tree contains only intentional changes, and commits are task-sized.

## GSTACK REVIEW REPORT

**Verdict:** CLEARED after plan repair.

**Scope Challenge Resolution:** The plan intentionally touches more than 8 files because this is one active Runtime data-flow boundary, not independent feature work. The user confirmed keeping the complete post-source canonical intake slice together. Implementation must stay sequential and task-sized.

**Architecture Review:** Issues found and fixed in this plan:

- Source target accounting is now explicit: selected CTS + Liepin reports a 20-resume raw acquisition target while final top pool remains 10 identities.
- Canonical intake summary is now round-scoped and receives selected sources/raw targets directly, instead of deriving them from global `normalized_store`.
- Conflict counts are filtered to conflicts touching current-round candidates, instead of using cumulative `run_state.identity_conflicts`.
- Cross-round duplicate policy is explicit: preserve raw/evidence, skip scoring, no score refresh in this slice.
- Finalizer coverage is explicit through a regression proving it consumes identity-deduped `top_pool_ids`.

**Code Quality Review:** No remaining blocking issues in the plan. Keep implementation small by reusing `RuntimeCandidateIdentityIndex`, `choose_canonical_resume_for_identity`, `RuntimeSourceEvidence`, and `top_candidates(run_state)`.

**Test Review:**

```text
Runtime source dispatch
  -> merge candidates/evidence
     -> [TEST] Runtime merge normalizes candidates before identity rebuild
     -> [TEST] selected CTS + Liepin raw target is 20
  -> Runtime normalization
     -> [TEST] CTS/Liepin provider source preserved
     -> [TEST] Liepin active lane returns raw candidates only
  -> deterministic identity rebuild
     -> [TEST] contact/provider/profile strong signals auto-merge
     -> [TEST] masked/name-only safeguards remain
     -> [TEST] medium confidence records conflict without merge
  -> canonical scoring intake
     -> [TEST] same-round CTS/Liepin duplicate scores once
     -> [TEST] already-scored identity is skipped
     -> [TEST] conflict summary is round-scoped
  -> identity top pool/finalizer
     -> [TEST] top pool has one scorecard per identity
     -> [TEST] finalizer context uses identity-deduped top pool
  -> reflection/controller
     -> [TEST] both contexts receive canonical intake summary
```

**Performance Review:** No blocking issue. Identity matching is in-memory over the current candidate set and existing run state. If candidate volume grows beyond current round-scale usage, add profiling before optimizing.

**Not In Scope:** UI cleanup, LLM duplicate adjudication, score refresh for already-scored identities, source acquisition redesign, generic provider plugin architecture.

**What Already Exists:** Existing identity, canonical selection, source evidence, and top-pool helpers are reused. The plan does not introduce a parallel candidate pool or second identity service.

**Failure Modes Covered:**

- Missing provider provenance: covered by normalization tests.
- Adapter pre-normalization drift: covered by Liepin raw-only lane test.
- Global summary pollution across rounds: covered by round-scoped conflict test.
- Cross-source duplicate double scoring: covered by canonical scoring intake test.
- Finalizer resume-level duplication: covered by finalizer context test.

**Worktree Parallelization Strategy:** Sequential implementation, no parallelization opportunity. Tasks share `models.py`, `source_lanes.py`, `candidate_intake.py`, and runtime tests, so parallel worktrees would create avoidable merge conflicts.
