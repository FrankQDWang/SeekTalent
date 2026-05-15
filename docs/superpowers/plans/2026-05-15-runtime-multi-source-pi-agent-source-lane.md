# Runtime Multi-Source Sourcing Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Runtime run CTS and Liepin as parallel multi-source lanes, merge likely same-person candidates, preserve all source evidence, select the freshest canonical resume, budget Liepin detail recommendations from provider-ranked cards, and return one unified Top 10.

**Architecture:** Runtime owns source planning, parallel lane lifecycle, budget policy, identity merge, canonical resume selection, scoring, finalization, and safe public events. Workbench owns display, persistence, approval/lease/budget/audit state, and consumes Runtime payloads. Provider adapters and PI Agent only execute bounded provider actions.

**Tech Stack:** Python 3.12, pytest, ruff, existing SeekTalent Runtime/Workbench modules, existing Liepin provider adapter/store contracts.

---

## Spec Link

This plan implements:

`docs/superpowers/specs/2026-05-15-runtime-multi-source-pi-agent-source-lane-design.md`

## Execution Notes

- Build on the current dirty working tree. Do not revert existing user or previous-agent changes.
- This repository uses staged `fw-*` gates. Do not push, merge, or release as part of this plan.
- Use tests first for each behavioral change.
- Keep public serializers allowlisted. Do not use `asdict()` for CLI, Workbench, notes, graph, or log payloads.
- Keep Workbench out of source-specific execution logic.

## File Map

Modify:

- `src/seektalent/runtime/source_lanes.py`
  - source budget policy
  - lane request shape
  - candidate identity records
  - canonical resume selection
  - identity-aware merge helpers
  - safe public payload serializers

- `src/seektalent/models.py`
  - `RunState` fields for identity, evidence, and canonical resume state
  - cloning behavior for lane-local state

- `src/seektalent/runtime/orchestrator.py`
  - full-run parallel source lane scheduling
  - terminal barrier
  - degraded finalization coverage
  - CTS budget cap for multi-source lane
  - unified Top 10 scoring after merge

- `src/seektalent/providers/liepin/runtime_lane.py`
  - provider-rank-first card policy
  - hard filter reason codes
  - per-run detail recommendation budget
  - detail recommendation public fields

- `src/seektalent/providers/liepin/adapter.py`
  - keep approved detail lease enforcement
  - expose only safe detail/card metadata to runtime lane

- `src/seektalent_ui/runtime_bridge.py`
  - pass source budget policy through Runtime lane request
  - consume new recommendation and coverage payload fields

- `src/seektalent_ui/workbench_store.py`
  - idempotent upsert for source events and detail recommendations if needed

- `src/seektalent_ui/workbench_routes.py`
  - expose only new safe public payloads if route output changes

- `TODOS.md`
  - record deferred UI/platform follow-ups once

Add or modify tests:

- `tests/test_runtime_source_lanes.py`
- `tests/test_liepin_runtime_source_lane.py`
- `tests/test_provider_registry.py`
- `tests/test_workbench_api.py`
- `tests/test_workbench_note_writer.py`
- new `tests/test_runtime_candidate_identity.py`

## Task 1: Add Runtime Source Budget Policy

Purpose: make CTS and Liepin source limits explicit instead of scattering budget constants through lanes.

- [ ] Add failing tests in `tests/test_runtime_source_lanes.py`:

```python
def test_default_source_budget_policy_is_public_safe():
    policy = RuntimeSourceBudgetPolicy.defaults()

    assert policy.cts_max_pages == 1
    assert policy.cts_page_size == 10
    assert policy.liepin_card_max_pages == 1
    assert policy.liepin_detail_open_limit_per_run > 0
    assert policy.final_top_k == 10
    assert policy.to_public_payload() == {
        "cts_max_pages": 1,
        "cts_page_size": 10,
        "liepin_card_max_pages": 1,
        "liepin_card_page_size": policy.liepin_card_page_size,
        "liepin_detail_open_limit_per_run": policy.liepin_detail_open_limit_per_run,
        "final_top_k": 10,
    }
```

- [ ] Implement `RuntimeSourceBudgetPolicy` in `src/seektalent/runtime/source_lanes.py`.
- [ ] Use literal domain names, not generic configuration wrappers.
- [ ] Keep the public payload count-only and secret-free.
- [ ] Run:

```bash
uv run pytest tests/test_runtime_source_lanes.py -q
```

Expected result: the new budget test passes.

## Task 2: Extend Lane Request And Plan With Budget Context

Purpose: every source lane should know the same runtime-owned budget policy.

- [ ] Add failing tests in `tests/test_runtime_source_lanes.py`:

```python
def test_runtime_source_lane_request_includes_budget_policy_publicly():
    request = RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="card",
        job_title="数字前端工程师",
        jd="需要 Verilog 和芯片经验",
        notes=None,
        source_budget_policy=RuntimeSourceBudgetPolicy.defaults(),
    )

    payload = request.to_public_payload()

    assert payload["source"] == "liepin"
    assert payload["lane_mode"] == "card"
    assert payload["source_budget_policy"]["cts_page_size"] == 10
    assert "provider_context" not in payload
```

- [ ] Add `source_budget_policy` to `RuntimeSourceLaneRequest`.
- [ ] Add budget fields to `RuntimeSourceLanePlan.to_public_payload()`.
- [ ] Make Workbench and full Runtime callers pass `RuntimeSourceBudgetPolicy.defaults()` when they do not specify a policy.
- [ ] Run:

```bash
uv run pytest tests/test_runtime_source_lanes.py tests/test_workbench_api.py -q
```

Expected result: request and Workbench route tests pass.

## Task 3: Add Candidate Identity And Canonical Resume Models

Purpose: merge CTS and Liepin at the person level without losing per-source evidence.

- [ ] Create `tests/test_runtime_candidate_identity.py`.
- [ ] Add failing tests for strong same-provider identity:

```python
def test_same_provider_key_maps_to_same_identity():
    first = make_source_evidence(
        source="liepin",
        provider="liepin",
        candidate_resume_id="liepin-card-1",
        provider_candidate_key_hash="hash-a",
        evidence_level="card",
    )
    second = make_source_evidence(
        source="liepin",
        provider="liepin",
        candidate_resume_id="liepin-detail-1",
        provider_candidate_key_hash="hash-a",
        evidence_level="detail",
    )

    index = RuntimeCandidateIdentityIndex()
    first_identity = index.identity_for_evidence(first, candidate=make_candidate("王某", "海光集成电路", "高级主管工程师"))
    second_identity = index.identity_for_evidence(second, candidate=make_candidate("王某", "海光集成电路", "高级主管工程师"))

    assert second_identity.identity_id == first_identity.identity_id
```

- [ ] Add failing tests for ambiguous name-only matches:

```python
def test_name_only_match_does_not_auto_merge():
    index = RuntimeCandidateIdentityIndex()

    first = index.identity_for_evidence(
        make_source_evidence(candidate_resume_id="cts-1", provider_candidate_key_hash="hash-1"),
        candidate=make_candidate("王某", "A 公司", "后端工程师"),
    )
    second = index.identity_for_evidence(
        make_source_evidence(candidate_resume_id="liepin-1", provider_candidate_key_hash="hash-2"),
        candidate=make_candidate("王某", "B 公司", "前端工程师"),
    )

    assert second.identity_id != first.identity_id
    assert index.conflict_reasons
```

- [ ] Implement small dataclasses in `src/seektalent/runtime/source_lanes.py`:

```python
@dataclass(frozen=True)
class RuntimeCandidateIdentity:
    identity_id: str
    match_confidence: Literal["strong", "medium", "weak", "ambiguous"]
    safe_match_reason_codes: tuple[str, ...] = ()

@dataclass(frozen=True)
class RuntimeCanonicalResumeSelection:
    identity_id: str
    resume_id: str
    source_evidence_id: str
    safe_reason_codes: tuple[str, ...]
```

- [ ] Add a focused `RuntimeCandidateIdentityIndex` helper in the same module unless local code shape strongly favors a separate module.
- [ ] Use deterministic ids derived from safe evidence ids and stable candidate fields. Do not include raw contact data in public ids.
- [ ] Run:

```bash
uv run pytest tests/test_runtime_candidate_identity.py -q
```

Expected result: strong matches merge; ambiguous matches stay separate.

## Task 4: Extend RunState For Identity-Aware Source State

Purpose: Runtime needs first-class state for identities, evidence by identity, and canonical selection.

- [ ] Add failing tests in `tests/test_runtime_candidate_identity.py`:

```python
def test_run_state_preserves_evidence_by_identity_after_clone():
    run_state = RunState()
    identity = RuntimeCandidateIdentity(identity_id="identity-1", match_confidence="strong")
    evidence = make_source_evidence(candidate_resume_id="resume-1", evidence_id="evidence-1")

    run_state.candidate_identity_store[identity.identity_id] = identity
    run_state.source_evidence_by_identity_id[identity.identity_id] = [evidence]

    clone = run_state.clone_empty_for_lane("cts")

    assert clone.candidate_identity_store == {}
    assert clone.source_evidence_by_identity_id == {}
```

- [ ] Add fields to `RunState` in `src/seektalent/models.py`:

```python
candidate_identity_store: dict[str, RuntimeCandidateIdentity]
candidate_identity_by_resume_id: dict[str, str]
source_evidence_by_identity_id: dict[str, list[RuntimeSourceEvidence]]
canonical_resume_by_identity_id: dict[str, RuntimeCanonicalResumeSelection]
identity_conflict_reasons: dict[str, list[str]]
source_coverage_status: str | None
missing_source_kinds: list[str]
```

- [ ] Avoid a circular import between `models.py` and `source_lanes.py`. If needed, use postponed annotations and import only under `TYPE_CHECKING`.
- [ ] Ensure lane-local cloning starts empty for lane outputs.
- [ ] Run:

```bash
uv run pytest tests/test_runtime_candidate_identity.py tests/test_runtime_source_lanes.py -q
```

Expected result: RunState supports identity state without contaminating lane-local state.

## Task 5: Replace Resume-Id Merge With Identity-Aware Merge

Purpose: preserve all source evidence and avoid wrong overwrites when the same person appears in CTS and Liepin.

- [ ] Add failing tests in `tests/test_runtime_source_lanes.py`:

```python
def test_apply_source_lane_result_merges_same_person_from_cts_and_liepin():
    run_state = RunState()
    cts_result = make_lane_result(
        source="cts",
        candidates=[make_candidate("resume-cts", name="王某", company="海光集成电路", title="高级主管工程师")],
        evidence=[make_source_evidence(source="cts", candidate_resume_id="resume-cts", provider_candidate_key_hash="cts-hash")],
    )
    liepin_result = make_lane_result(
        source="liepin",
        candidates=[make_candidate("resume-liepin", name="王某", company="海光集成电路", title="高级主管工程师")],
        evidence=[make_source_evidence(source="liepin", candidate_resume_id="resume-liepin", provider_candidate_key_hash="liepin-hash")],
    )

    apply_source_lane_result(run_state, cts_result)
    apply_source_lane_result(run_state, liepin_result)

    assert len(run_state.candidate_identity_store) == 1
    identity_id = next(iter(run_state.candidate_identity_store))
    assert {e.source for e in run_state.source_evidence_by_identity_id[identity_id]} == {"cts", "liepin"}
```

- [ ] Add failing idempotency test:

```python
def test_apply_source_lane_result_is_idempotent_for_same_evidence_id():
    run_state = RunState()
    result = make_lane_result(
        source="liepin",
        candidates=[make_candidate("resume-liepin")],
        evidence=[make_source_evidence(evidence_id="evidence-1", candidate_resume_id="resume-liepin")],
    )

    apply_source_lane_result(run_state, result)
    apply_source_lane_result(run_state, result)

    identity_id = run_state.candidate_identity_by_resume_id["resume-liepin"]
    assert [e.evidence_id for e in run_state.source_evidence_by_identity_id[identity_id]] == ["evidence-1"]
```

- [ ] Update `apply_source_lane_result()` in `src/seektalent/runtime/source_lanes.py`.
- [ ] Keep `candidate_store` and `normalized_store` for display and scoring compatibility, but make identity stores the source of merge truth.
- [ ] Append evidence once by stable evidence id.
- [ ] Sort evidence deterministically:
  - source plan order
  - evidence level card before detail when timestamps tie
  - collected timestamp
  - evidence id
- [ ] Run:

```bash
uv run pytest tests/test_runtime_source_lanes.py tests/test_runtime_candidate_identity.py -q
```

Expected result: same-person source records merge into one identity and duplicate lane result application is stable.

## Task 6: Implement Canonical Resume Selection

Purpose: final scoring should use the best available resume per identity while retaining evidence.

- [ ] Add failing tests in `tests/test_runtime_candidate_identity.py`:

```python
def test_canonical_selection_prefers_detail_over_card():
    selection = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        candidates={
            "card": make_candidate("card", completeness_score=20),
            "detail": make_candidate("detail", completeness_score=80),
        },
        evidence=[
            make_source_evidence(evidence_id="card-evidence", candidate_resume_id="card", evidence_level="card"),
            make_source_evidence(evidence_id="detail-evidence", candidate_resume_id="detail", evidence_level="detail"),
        ],
    )

    assert selection.resume_id == "detail"
    assert "detail_evidence" in selection.safe_reason_codes
```

- [ ] Add freshness tie-break test:

```python
def test_canonical_selection_prefers_newer_resume_when_both_are_detail():
    selection = choose_canonical_resume_for_identity(
        identity_id="identity-1",
        candidates={
            "old": make_candidate("old", resume_updated_at="2024-01-01"),
            "new": make_candidate("new", resume_updated_at="2026-01-01"),
        },
        evidence=[
            make_source_evidence(candidate_resume_id="old", evidence_level="detail"),
            make_source_evidence(candidate_resume_id="new", evidence_level="detail"),
        ],
    )

    assert selection.resume_id == "new"
```

- [ ] Implement `choose_canonical_resume_for_identity()` in `src/seektalent/runtime/source_lanes.py`.
- [ ] Use deterministic sort keys:
  - detail evidence
  - parsed resume update timestamp
  - current work recency
  - normalized resume completeness
  - source trust
  - provider rank
  - resume id
- [ ] Call canonical selection from `apply_source_lane_result()` after identity evidence changes.
- [ ] Run:

```bash
uv run pytest tests/test_runtime_candidate_identity.py tests/test_runtime_source_lanes.py -q
```

Expected result: canonical resume choice is deterministic and evidence-preserving.

## Task 7: Make Full Source Lanes Run In Parallel

Purpose: CTS and Liepin should run as parallel source searches, then merge into one final pool.

- [ ] Add failing async test in `tests/test_runtime_source_lanes.py` or an orchestrator-focused test file:

```python
@pytest.mark.asyncio
async def test_full_source_lanes_start_cts_and_liepin_before_barrier():
    started = []
    release = asyncio.Event()

    runtime = make_runtime_with_lane_hooks(
        cts_hook=lambda: started.append("cts"),
        liepin_hook=lambda: started.append("liepin"),
        release=release,
    )

    task = asyncio.create_task(runtime.run(job_title="工程师", jd="JD", source_kinds=("cts", "liepin")))

    await wait_until(lambda: set(started) == {"cts", "liepin"})
    release.set()
    result = await task

    assert result.source_coverage_status == "complete"
```

- [ ] Update `_run_full_source_lanes()` in `src/seektalent/runtime/orchestrator.py`.
- [ ] Use `asyncio.gather(lane_tasks, return_exceptions=True)` or equivalent structured task handling.
- [ ] Convert provider exceptions to safe failed lane results. Do not let raw exception messages enter public events.
- [ ] Merge lane results only after selected lanes are terminal.
- [ ] Do not let CTS mutate the active final `RunState` before its lane result is returned.
- [ ] Run:

```bash
uv run pytest tests/test_runtime_source_lanes.py -q
```

Expected result: both selected full-run lanes start before finalization and return one merged result.

## Task 8: Add Finalization Coverage Semantics

Purpose: users must know when the Top 10 came from all selected sources or only available sources.

- [ ] Add failing tests:

```python
@pytest.mark.asyncio
async def test_blocked_liepin_and_completed_cts_finalizes_with_degraded_coverage():
    runtime = make_runtime_with_lane_results(
        cts=make_lane_result(source="cts", status="completed", candidates=[make_candidate("cts-1")]),
        liepin=make_lane_result(source="liepin", status="blocked", candidates=[]),
    )

    result = await runtime.run(job_title="工程师", jd="JD", source_kinds=("cts", "liepin"))

    assert result.source_coverage_status == "degraded"
    assert result.missing_source_kinds == ["liepin"]
    assert len(result.candidates) <= 10
```

- [ ] Store coverage on `RunState` and expose it through existing runtime result payloads.
- [ ] Use `complete` when all selected lanes completed or partial lanes produced accepted candidates.
- [ ] Use `degraded` when at least one selected source blocked, failed, timed out, or produced no usable candidates.
- [ ] Use `empty` when no selected lane produced candidates.
- [ ] Update Workbench notes/graph context to include safe coverage status.
- [ ] Run:

```bash
uv run pytest tests/test_runtime_source_lanes.py tests/test_workbench_note_writer.py -q
```

Expected result: finalization scope is explicit and public-safe.

## Task 9: Cap CTS Multi-Source Lane To One Page Of 10

Purpose: the first multi-source version should respect the CTS budget the product expects.

- [ ] Add failing test around `_run_cts_source_lane()`:

```python
@pytest.mark.asyncio
async def test_cts_source_lane_uses_one_page_of_ten_in_multi_source_mode():
    captured_requests = []
    runtime = make_runtime_with_cts_capture(captured_requests)

    await runtime._run_cts_source_lane(
        RuntimeSourceLaneRequest(
            source="cts",
            lane_mode="card",
            job_title="工程师",
            jd="JD",
            notes=None,
            source_budget_policy=RuntimeSourceBudgetPolicy.defaults(),
        )
    )

    assert len(captured_requests) == 1
    assert captured_requests[0].page_size == 10
```

- [ ] Modify the CTS source lane path in `src/seektalent/runtime/orchestrator.py` to use the runtime source budget.
- [ ] Keep CTS-only legacy behavior unchanged outside the multi-source source-lane path.
- [ ] Record CTS source evidence for every returned candidate.
- [ ] Run:

```bash
uv run pytest tests/test_runtime_source_lanes.py tests/test_cli.py -q
```

Expected result: multi-source CTS is capped to one page of 10, while CLI compatibility tests remain green.

## Task 10: Implement Provider-Rank-First Liepin Card Policy

Purpose: Liepin recommendations should primarily respect the search engine's card order while filtering obvious non-fits.

- [ ] Add failing tests in `tests/test_liepin_runtime_source_lane.py`:

```python
def test_liepin_detail_recommendations_preserve_provider_rank_after_hard_filters():
    candidates = [
        make_liepin_card("rank-1", provider_rank=1, title="数字前端工程师", tags=("verilog",)),
        make_liepin_card("rank-2", provider_rank=2, title="数字前端专家", tags=("verilog", "FTI", "SDP")),
    ]

    recommendations = detail_recommendations_for_liepin_cards(
        candidates,
        job_title="数字前端工程师",
        jd="需要 verilog",
        budget_policy=RuntimeSourceBudgetPolicy(liepin_detail_open_limit_per_run=2),
    )

    assert [r.source_candidate_resume_id for r in recommendations] == ["rank-1", "rank-2"]
```

- [ ] Add hard-filter test:

```python
def test_liepin_card_hard_filter_blocks_obvious_wrong_title():
    candidates = [
        make_liepin_card("rank-1", provider_rank=1, title="销售经理"),
        make_liepin_card("rank-2", provider_rank=2, title="数字前端工程师"),
    ]

    recommendations = detail_recommendations_for_liepin_cards(
        candidates,
        job_title="数字前端工程师",
        jd="芯片数字前端",
        budget_policy=RuntimeSourceBudgetPolicy(liepin_detail_open_limit_per_run=2),
    )

    assert [r.source_candidate_resume_id for r in recommendations] == ["rank-2"]
    assert recommendations[0].safe_reason_codes
```

- [ ] Add fields to `RuntimeDetailRecommendation`:
  - `provider_rank`
  - `card_policy_rank`
  - `hard_filter_status`
  - `budget_reason_code`
- [ ] Implement a small Liepin card policy function in `src/seektalent/providers/liepin/runtime_lane.py`.
- [ ] Keep provider rank primary for all cards that pass hard filters.
- [ ] Use safe reason codes only.
- [ ] Run:

```bash
uv run pytest tests/test_liepin_runtime_source_lane.py -q
```

Expected result: Liepin card recommendations are provider-rank-first and hard-filtered.

## Task 11: Enforce Liepin Detail Recommendation Budget

Purpose: Runtime should recommend only the allowed number of detail opens per run.

- [ ] Add failing budget tests:

```python
def test_liepin_detail_recommendations_stop_at_budget_limit():
    candidates = [
        make_liepin_card(f"rank-{index}", provider_rank=index, title="数字前端工程师")
        for index in range(1, 6)
    ]

    recommendations = detail_recommendations_for_liepin_cards(
        candidates,
        job_title="数字前端工程师",
        jd="芯片数字前端",
        budget_policy=RuntimeSourceBudgetPolicy(liepin_detail_open_limit_per_run=2),
    )

    assert [r.provider_rank for r in recommendations] == [1, 2]
    assert {r.budget_reason_code for r in recommendations} == {"within_run_detail_budget"}
```

- [ ] Add duplicate identity skip test:

```python
def test_liepin_detail_budget_skips_identity_already_detail_enriched():
    recommendations = detail_recommendations_for_liepin_cards(
        [make_liepin_card("rank-1", provider_rank=1), make_liepin_card("rank-2", provider_rank=2)],
        job_title="工程师",
        jd="JD",
        budget_policy=RuntimeSourceBudgetPolicy(liepin_detail_open_limit_per_run=2),
        identities_with_detail={"identity-rank-1"},
    )

    assert [r.source_candidate_resume_id for r in recommendations] == ["rank-2"]
```

- [ ] Apply budget after hard filters and before returning public lane result.
- [ ] Do not fetch detail resumes in the card lane.
- [ ] Include safe counts in lane events:
  - cards seen
  - cards filtered
  - detail recommendations emitted
  - detail budget limit
- [ ] Run:

```bash
uv run pytest tests/test_liepin_runtime_source_lane.py tests/test_runtime_source_lanes.py -q
```

Expected result: detail recommendations are budgeted, deterministic, and safe.

## Task 12: Keep Approved Detail Lease As Separate Detail Lane

Purpose: card search and detail fetch must stay separated so future approval UI can plug in safely.

- [ ] Add or update tests in `tests/test_liepin_runtime_source_lane.py`:

```python
@pytest.mark.asyncio
async def test_liepin_detail_lane_requires_approved_lease():
    request = RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="detail",
        job_title="工程师",
        jd="JD",
        notes=None,
        source_budget_policy=RuntimeSourceBudgetPolicy.defaults(),
        approved_detail_lease=None,
    )

    result = await run_liepin_source_lane(request, adapter=make_liepin_adapter())

    assert result.status == "blocked"
    assert result.blocked_reason_code == "blocked_approval_missing"
```

- [ ] Ensure `RuntimeSourceLaneRequest(lane_mode="detail")` carries `approved_detail_lease`.
- [ ] Ensure `run_liepin_source_lane()` rejects missing or invalid leases.
- [ ] Ensure `LiepinProviderAdapter` still enforces lease validity before detail fetch.
- [ ] Do not add approval UI.
- [ ] Run:

```bash
uv run pytest tests/test_liepin_runtime_source_lane.py tests/test_liepin_provider_adapter.py -q
```

Expected result: detail fetch is impossible without an approved lease.

## Task 13: Score And Return Unified Top 10 By Identity

Purpose: after merge, final output should rank the shared multi-source pool once.

- [ ] Add failing integration-style test:

```python
@pytest.mark.asyncio
async def test_cts_and_liepin_multi_source_run_returns_unified_top_ten():
    runtime = make_runtime_with_lane_results(
        cts=make_lane_result(source="cts", candidates=[make_candidate(f"cts-{i}") for i in range(10)]),
        liepin=make_lane_result(source="liepin", candidates=[make_candidate(f"liepin-{i}") for i in range(10)]),
    )

    result = await runtime.run(job_title="工程师", jd="JD", source_kinds=("cts", "liepin"))

    assert len(result.candidates) == 10
    assert result.source_coverage_status == "complete"
    assert {candidate.source_context.primary_source for candidate in result.candidates} <= {"cts", "liepin"}
```

- [ ] Update finalization in `src/seektalent/runtime/orchestrator.py` to score canonical resumes after all selected lane results are merged.
- [ ] Keep `TOP_K = 10` as the final shortlist contract.
- [ ] Ensure multi-source context is available to note generation and graph rendering.
- [ ] Run:

```bash
uv run pytest tests/test_runtime_source_lanes.py tests/test_workbench_note_writer.py -q
```

Expected result: final candidates are one unified Top 10 across selected sources.

## Task 14: Update Workbench Source-Run Persistence For Stable Ids

Purpose: async source events and recommendations must be idempotent across retry and refresh.

- [ ] Add failing tests in `tests/test_workbench_api.py` or a store-focused test:

```python
def test_workbench_upserts_detail_recommendation_by_recommendation_id(tmp_path):
    store = WorkbenchStore(tmp_path / "workbench.sqlite")
    recommendation = make_detail_recommendation(recommendation_id="rec-1")

    store.upsert_detail_recommendations(run_id="run-1", recommendations=[recommendation])
    store.upsert_detail_recommendations(run_id="run-1", recommendations=[recommendation])

    assert store.list_detail_recommendations(run_id="run-1") == [recommendation.to_public_payload()]
```

- [ ] Add event sequence test:

```python
def test_workbench_ignores_older_source_event_sequence(tmp_path):
    store = WorkbenchStore(tmp_path / "workbench.sqlite")

    store.upsert_source_event(make_source_event(source_lane_run_id="lane-1", event_seq=2, event_type="source_lane_completed"))
    store.upsert_source_event(make_source_event(source_lane_run_id="lane-1", event_seq=1, event_type="source_lane_started"))

    assert store.get_source_lane_state("lane-1")["event_type"] == "source_lane_completed"
```

- [ ] Implement or adjust upsert helpers in `src/seektalent_ui/workbench_store.py`.
- [ ] Keep Workbench persistence generic. It should store Runtime public payloads, not call Liepin provider logic.
- [ ] Run:

```bash
uv run pytest tests/test_workbench_api.py -q
```

Expected result: repeated recommendation/event writes are idempotent and order-safe.

## Task 15: Harden Public Payload Serializers

Purpose: new identity, budget, and recommendation payloads must not reintroduce leakage.

- [ ] Add leakage tests in `tests/test_runtime_source_lanes.py`:

```python
def test_public_payloads_do_not_include_raw_provider_or_secrets():
    result = make_lane_result(
        source="liepin",
        posture={
            "provider_token": "secret-token",
            "cookie": "secret-cookie",
            "safe_count": 1,
        },
        event_payloads=[
            RuntimeSourceLaneEvent(
                event_type="source_lane_completed",
                source="liepin",
                safe_counts={"cards_seen": 1},
                safe_reason_code="ok",
            )
        ],
    )

    payload = result.to_public_payload()
    rendered = json.dumps(payload, ensure_ascii=False)

    assert "secret-token" not in rendered
    assert "secret-cookie" not in rendered
    assert "raw_resume" not in rendered
```

- [ ] Ensure these objects all expose allowlisted `to_public_payload()` methods:
  - `RuntimeSourceBudgetPolicy`
  - `RuntimeSourceLaneRequest`
  - `RuntimeSourceLanePlan`
  - `RuntimeSourceLaneEvent`
  - `RuntimeSourceEvidence`
  - `RuntimeDetailRecommendation`
  - `RuntimeSourceLaneResult`
  - `RuntimeCandidateIdentity`
  - `RuntimeCanonicalResumeSelection`
- [ ] Remove public serialization paths that call `asdict()` on these dataclasses.
- [ ] Run:

```bash
uv run pytest tests/test_runtime_source_lanes.py tests/test_cli.py tests/test_workbench_api.py -q
```

Expected result: public payloads remain allowlisted and existing leakage tests remain green.

## Task 16: Update Notes And Graph Context For Multi-Source Evidence

Purpose: recruiter-facing notes should know when evidence came from CTS, Liepin card, Liepin detail, or a degraded run.

- [ ] Add or update tests in `tests/test_workbench_note_writer.py`:

```python
def test_run_notes_include_multi_source_context_without_raw_resume():
    note = render_run_note(
        source_coverage_status="degraded",
        missing_source_kinds=["liepin"],
        source_evidence=[
            make_public_evidence(source="cts", evidence_level="detail"),
            make_public_evidence(source="liepin", evidence_level="card"),
        ],
    )

    assert "CTS" in note
    assert "Liepin" in note
    assert "degraded" in note
    assert "raw_resume" not in note
```

- [ ] Update note/graph builders to consume public Runtime payloads only.
- [ ] Display coverage gaps and source branches without provider-specific raw fields.
- [ ] Keep UI copy business-facing.
- [ ] Run:

```bash
uv run pytest tests/test_workbench_note_writer.py tests/test_workbench_api.py -q
```

Expected result: notes and graph context reflect multi-source state safely.

## Task 17: Record Deferred Product Follow-Ups Once

Purpose: keep first implementation scoped while preserving the platform roadmap.

- [ ] Update `TODOS.md` with one section for deferred multi-source source-run follow-ups if the section does not already exist.
- [ ] Include:
  - human card-review UI
  - manual detail-open approval UI
  - manual source budget editing UI
  - lane health/cost/quality metrics
  - automatic source strategy optimization
  - broader source capability descriptor
  - trusted DokoBot action manifest and conformance suite
  - future A2A bridge only if PI Agent becomes out-of-process with independent lifecycle and identity
- [ ] Run:

```bash
rg -n "Multi-Source Source-Run Follow-Ups|human card-review UI|trusted DokoBot action manifest" TODOS.md
```

Expected result: the deferred scope exists once and does not duplicate older sections.

## Task 18: Full Verification

Purpose: prove the multi-source contract works without breaking existing product behavior.

- [ ] Run focused tests:

```bash
uv run pytest \
  tests/test_runtime_candidate_identity.py \
  tests/test_runtime_source_lanes.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_provider_registry.py \
  tests/test_workbench_api.py \
  tests/test_workbench_note_writer.py \
  tests/test_liepin_provider_adapter.py \
  tests/test_liepin_session_store.py \
  tests/test_cli.py \
  -q
```

Expected result: all selected tests pass.

- [ ] Run lint:

```bash
uv run ruff check \
  src/seektalent/runtime/source_lanes.py \
  src/seektalent/runtime/orchestrator.py \
  src/seektalent/providers/liepin/runtime_lane.py \
  src/seektalent/providers/liepin/adapter.py \
  src/seektalent/models.py \
  src/seektalent_ui/runtime_bridge.py \
  src/seektalent_ui/workbench_store.py \
  src/seektalent_ui/workbench_routes.py \
  tests/test_runtime_candidate_identity.py \
  tests/test_runtime_source_lanes.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_workbench_api.py \
  tests/test_workbench_note_writer.py \
  tests/test_cli.py
```

Expected result: ruff passes.

- [ ] Run whitespace check:

```bash
git diff --check
```

Expected result: no whitespace errors.

- [ ] Run public leakage scan over generated JSON fixtures or direct CLI outputs used by existing tests:

```bash
uv run pytest tests/test_cli.py tests/test_runtime_source_lanes.py tests/test_liepin_runtime_source_lane.py -q
```

Expected result: no provider key, token, cookie, session secret, approval secret, raw HTML, raw resume, or raw provider payload appears in public output tests.

## Completion Criteria

The plan is complete when:

- CTS and Liepin selected together start as parallel full-run lanes.
- CTS multi-source lane is capped to one page of 10.
- Liepin card lane emits provider-rank-first detail recommendations within budget.
- Liepin detail fetch requires an approved detail lease.
- Runtime merges same-person candidates into identities while preserving all source evidence.
- Runtime selects a canonical resume per identity deterministically.
- Runtime returns one unified Top 10 across selected sources.
- Workbench source-run persistence handles stable ids and out-of-order events.
- Notes and graph context use multi-source public payloads only.
- Public serializers are allowlisted and leakage tests pass.
- Deferred UI/platform items are recorded once in `TODOS.md`.
