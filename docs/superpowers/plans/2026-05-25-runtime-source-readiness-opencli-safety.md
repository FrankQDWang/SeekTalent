# Runtime Source Readiness And OpenCLI Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop dual-source Runtime runs before scoring unless every selected source completes successfully, and harden the Runtime-owned Liepin PI/OpenCLI path for pacing, structured card reading, detail idempotency, and cleanup.

**Architecture:** Runtime owns selected-source readiness and decides whether a round may enter scoring. Workbench only starts Runtime jobs that satisfy the same selected-source readiness contract. OpenCLI remains the browser primitive layer; SeekTalent helper code owns Liepin-specific card extraction, pacing, tab ownership, and safe artifacts, while PI uses those bounded tools to decide which visible cards deserve detail opens against the full `RequirementSheet`.

**Tech Stack:** Python 3.12, Pydantic, asyncio, pytest, existing SeekTalent Runtime modules, existing PI RPC/OpenCLI helper, TypeScript PI extension.

---

## Spec Link

This plan implements:

`docs/superpowers/specs/2026-05-25-runtime-source-readiness-opencli-safety-design.md`

## Execution Notes

- Do not run live Liepin/OpenCLI/browser automation.
- Do not change scoring, reflection, finalizer, or requirement extraction behavior.
- Do not preserve degraded dual-source scoring as a fallback for CTS + Liepin. This slice intentionally makes selected-source readiness strict.
- Keep CTS-only selected runs working.
- Use fake adapters, fake commands, and fake PI transports in tests.
- Prefer small helper functions over broad abstractions.
- Commit after each task.

## File Map

Runtime readiness:

- Modify: `src/seektalent/runtime/source_round_dispatch.py`
  - Keep barrier semantics; do not convert source failure into premature dispatch return.
- Modify: `src/seektalent/runtime/orchestrator.py`
  - Add strict selected-source readiness check between source dispatch merge and scoring.
  - Raise or return a source-stage `RunStageError` before scoring when selected source coverage is not complete.
- Modify: `src/seektalent/runtime/source_lanes.py`
  - Keep source coverage payload safe and expose enough status/reason codes for UI and tests.

Workbench start gate:

- Modify: `src/seektalent_ui/workbench_routes.py`
  - Do not start runtime sourcing if a selected source was blocked by pre-start probing.
- Modify: `src/seektalent_ui/workbench_store.py`
  - Do not include blocked source runs in a runtime job that requires strict selected-source readiness.
  - Return a clear error/status when selected source runs are blocked before runtime start.
- Modify: `src/seektalent_ui/runtime_bridge.py`
  - Keep passing only selected, ready source kinds into `runtime.run(...)`.

OpenCLI safety and card reading:

- Modify: `src/seektalent/config.py`
  - Add bounded OpenCLI pacing settings with safe defaults.
- Modify: `src/seektalent/providers/liepin/client.py`
  - Pass pacing settings into PI/OpenCLI helper env.
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
  - Add deterministic-testable randomized pacing wrapper.
  - Add structured visible-card extraction.
  - Split detail-open state into pending/succeeded/failed/captured.
  - Ensure cleanup closes source-run-owned detail tabs.
- Modify: `src/seektalent/providers/pi_agent/opencli_browser_cli.py`
  - Expose the structured card extraction command.
- Modify: `src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts`
  - Add a read-only PI tool for structured visible-card extraction.
  - Keep mutating action budget unchanged.
- Modify: `src/seektalent/providers/pi_agent/pi_external.py`
  - Update `liepin.search_resumes` prompt contract to require structured card read before detail opens.
  - Verify cleanup still runs once per PI task session terminal lifecycle.
- Modify: `src/seektalent/providers/liepin/pi_executor.py`
  - Keep resume output validation unchanged except for any prompt/tool-name expectations needed by the new card-read tool.

Tests:

- Modify: `tests/test_runtime_multi_source_round_dispatch.py`
- Modify: `tests/test_runtime_state_flow.py`
- Modify: `tests/test_workbench_runtime_owned_execution.py`
- Modify: `tests/test_workbench_api.py`
- Modify: `tests/test_pi_opencli_browser.py`
- Modify: `tests/test_pi_external_agent.py`
- Modify: `tests/test_liepin_pi_executor.py`
- Modify: `tests/test_liepin_config.py`
- Modify: `tests/test_liepin_boundaries.py`

---

### Task 1: Add Strict Runtime Source Readiness Gate

**Files:**
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `tests/test_runtime_state_flow.py`
- Modify: `tests/test_runtime_multi_source_round_dispatch.py`

- [ ] **Step 1: Add a failing scoring-not-called regression for blocked Liepin**

In `tests/test_runtime_state_flow.py`, add a fake source-dispatch test near existing multi-source runtime tests. Use the existing Runtime/test factory helpers in this file. If there is no suitable helper, add a local scorer spy:

```python
class ScorerSpy:
    def __init__(self) -> None:
        self.calls = 0

    async def score_candidates_parallel(self, *, contexts, tracer):
        del contexts, tracer
        self.calls += 1
        return [], []


def _runtime_for_strict_source_tests(tmp_path: Path) -> WorkflowRuntime:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, min_rounds=1, max_rounds=1))
    _install_runtime_stubs(runtime, controller=SequenceController(), resume_scorer=ScorerSpy())
    return runtime
```

Add the test:

```python
def test_dual_source_run_stops_before_scoring_when_liepin_blocked(monkeypatch, tmp_path):
    runtime = _runtime_for_strict_source_tests(tmp_path)
    scorer = ScorerSpy()
    runtime.resume_scorer = scorer

    async def fake_dispatch_source_rounds(*, request, cts_adapter, liepin_adapter):
        del request, cts_adapter, liepin_adapter
        return SourceRoundDispatchResult(
            source_results=(
                SourceRoundAdapterResult(
                    source="cts",
                    status="completed",
                    candidates=(_make_candidate("cts-1"),),
                    raw_candidate_count=1,
                ),
                SourceRoundAdapterResult(
                    source="liepin",
                    status="blocked",
                    candidates=(),
                    raw_candidate_count=0,
                    safe_reason_code="liepin_opencli_risk_page",
                ),
            ),
            candidates=(_make_candidate("cts-1"),),
            raw_candidate_count=1,
        )

    monkeypatch.setattr(
        "seektalent.runtime.orchestrator.dispatch_source_rounds",
        fake_dispatch_source_rounds,
    )

    with pytest.raises(orchestrator_module.RunStageError, match="source"):
        runtime.run(
            job_title="AI Agent Engineer",
            jd="Build agentic retrieval workflows.",
            notes="",
            source_kinds=("cts", "liepin"),
            approved_requirement_sheet=_requirement_sheet(),
        )

    assert scorer.calls == 0
```

Use the local helper names already present in `tests/test_runtime_state_flow.py` where possible. Do not create a broad fixture file.

- [ ] **Step 2: Add parameterized failing coverage tests, including missing Liepin**

In the same test file, add:

```python
@pytest.mark.parametrize(
    ("case_name", "liepin_result"),
    [
        (
            "partial",
            SourceRoundAdapterResult(source="liepin", status="partial", candidates=(), raw_candidate_count=0),
        ),
        (
            "failed",
            SourceRoundAdapterResult(source="liepin", status="failed", candidates=(), raw_candidate_count=0),
        ),
        (
            "empty",
            SourceRoundAdapterResult(source="liepin", status="completed", candidates=(), raw_candidate_count=0),
        ),
        ("missing", None),
    ],
)
def test_dual_source_run_requires_every_selected_source_completed_with_candidates(
    monkeypatch,
    tmp_path,
    case_name,
    liepin_result,
):
    runtime = _runtime_for_strict_source_tests(tmp_path)
    scorer = ScorerSpy()
    runtime.resume_scorer = scorer
    source_results = [
        SourceRoundAdapterResult(
            source="cts",
            status="completed",
            candidates=(_make_candidate("cts-1"),),
            raw_candidate_count=1,
        )
    ]
    if liepin_result is not None:
        source_results.append(liepin_result)

    async def fake_dispatch_source_rounds(*, request, cts_adapter, liepin_adapter):
        del request, cts_adapter, liepin_adapter
        return SourceRoundDispatchResult(
            source_results=tuple(source_results),
            candidates=(_make_candidate("cts-1"),),
            raw_candidate_count=1,
        )

    monkeypatch.setattr("seektalent.runtime.orchestrator.dispatch_source_rounds", fake_dispatch_source_rounds)

    with pytest.raises(orchestrator_module.RunStageError):
        runtime.run(
            job_title="AI Agent Engineer",
            jd="Build agentic retrieval workflows.",
            notes="",
            source_kinds=("cts", "liepin"),
            approved_requirement_sheet=_requirement_sheet(),
        )

    assert scorer.calls == 0
```

This deliberately treats `completed` with zero Liepin candidates and a missing selected Liepin result as not ready for scoring.

- [ ] **Step 3: Add a passing CTS-only control test**

Add:

```python
def test_cts_only_run_can_score_without_liepin(monkeypatch, tmp_path):
    runtime = _runtime_for_strict_source_tests(tmp_path)
    scorer = ScorerSpy()
    runtime.resume_scorer = scorer

    async def fake_dispatch_source_rounds(*, request, cts_adapter, liepin_adapter):
        del cts_adapter, liepin_adapter
        assert request.selected_sources == ("cts",)
        return SourceRoundDispatchResult(
            source_results=(
                SourceRoundAdapterResult(
                    source="cts",
                    status="completed",
                    candidates=(_make_candidate("cts-1"),),
                    raw_candidate_count=1,
                ),
            ),
            candidates=(_make_candidate("cts-1"),),
            raw_candidate_count=1,
        )

    monkeypatch.setattr("seektalent.runtime.orchestrator.dispatch_source_rounds", fake_dispatch_source_rounds)

    runtime.run(
        job_title="AI Agent Engineer",
        jd="Build agentic retrieval workflows.",
        notes="",
        source_kinds=("cts",),
        approved_requirement_sheet=_requirement_sheet(),
    )

    assert scorer.calls >= 1
```

- [ ] **Step 4: Run the focused failing tests**

Run:

```bash
uv run pytest tests/test_runtime_state_flow.py -q
```

Expected before implementation: at least the new strict-readiness tests fail because scoring currently proceeds on degraded source coverage.

- [ ] **Step 5: Add a dispatch barrier regression**

In `tests/test_runtime_multi_source_round_dispatch.py`, add a focused async test proving `dispatch_source_rounds(...)` does not return after CTS finishes while Liepin is still running:

```python
@pytest.mark.asyncio
async def test_dispatch_waits_for_liepin_before_returning_when_cts_finishes_first() -> None:
    cts_finished = asyncio.Event()
    allow_liepin_finish = asyncio.Event()
    dispatch_returned = False

    request = SourceRoundDispatchRequest(
        runtime_run_id="run-1",
        round_no=1,
        logical_queries=(_dispatch("exploit", 7), _dispatch("generic_explore", 3)),
        selected_sources=("cts", "liepin"),
        seen_resume_ids=frozenset(),
        seen_dedup_keys=frozenset(),
        source_query_intents_by_source={},
        requirement_sheet=_requirement_sheet(),
    )

    async def cts_adapter(request):
        del request
        cts_finished.set()
        return SourceRoundAdapterResult(
            source="cts",
            status="completed",
            candidates=(_candidate("cts-1", "cts"),),
            raw_candidate_count=1,
        )

    async def liepin_adapter(request):
        del request
        await cts_finished.wait()
        await allow_liepin_finish.wait()
        return SourceRoundAdapterResult(
            source="liepin",
            status="completed",
            candidates=(_candidate("liepin-1", "liepin"),),
            raw_candidate_count=1,
        )

    async def run_dispatch():
        nonlocal dispatch_returned
        result = await dispatch_source_rounds(
            request=request,
            cts_adapter=cts_adapter,
            liepin_adapter=liepin_adapter,
        )
        dispatch_returned = True
        return result

    task = asyncio.create_task(run_dispatch())
    await asyncio.wait_for(cts_finished.wait(), timeout=1)
    await asyncio.sleep(0)
    assert dispatch_returned is False

    allow_liepin_finish.set()
    result = await asyncio.wait_for(task, timeout=1)

    assert dispatch_returned is True
    assert {item.source for item in result.source_results} == {"cts", "liepin"}
```

Expected before implementation: this should already pass if `TaskGroup` barrier semantics are intact. Keep it as a regression guard.

- [ ] **Step 6: Add a strict readiness helper in orchestrator**

In `src/seektalent/runtime/orchestrator.py`, add a small private helper near `_source_coverage_summary_from_dispatch`:

```python
def _source_round_not_ready_reason(
    *,
    coverage_summary: RuntimeSourceCoverageSummary,
    dispatch_result: SourceRoundDispatchResult,
) -> str | None:
    if coverage_summary.status == "complete":
        return None
    result_by_source = {result.source: result for result in dispatch_result.source_results}
    for source in coverage_summary.blocked_source_kinds:
        result = result_by_source.get(source)
        return (result.safe_reason_code if result is not None else None) or f"source_{source}_blocked"
    for source in coverage_summary.failed_source_kinds:
        result = result_by_source.get(source)
        return (result.safe_reason_code if result is not None else None) or f"source_{source}_failed"
    for source in coverage_summary.partial_source_kinds:
        result = result_by_source.get(source)
        return (result.safe_reason_code if result is not None else None) or f"source_{source}_partial"
    for source in coverage_summary.empty_source_kinds:
        return f"source_{source}_empty"
    for source in coverage_summary.missing_source_kinds:
        return f"source_{source}_missing"
    return f"source_coverage_{coverage_summary.status}"
```

If the file already has a better local naming pattern for source coverage helpers, follow that pattern.

- [ ] **Step 7: Use the helper before scoring**

In `_execute_multi_source_round_search(...)`, keep the existing `_merge_source_round_dispatch_result(...)` call exactly once. Do not add a second merge call. After the current per-source `source_result` events are emitted and before returning `RetrievalExecutionResult`, check readiness:

```python
if run_state.source_coverage_summary is None:
    raise RunStageError("source_lanes", "source_coverage_missing")
not_ready_reason = _source_round_not_ready_reason(
    coverage_summary=run_state.source_coverage_summary,
    dispatch_result=dispatch_result,
)
if not_ready_reason is not None:
    raise RunStageError("source_lanes", not_ready_reason)
```

Place this check before `_round_search_result_from_source_dispatch(...)` is called, so scoring never receives CTS-only candidates for a failed CTS+Liepin run. Do not add a CTS-only exception inside this helper. CTS-only naturally passes if CTS completed with candidates.

- [ ] **Step 8: Run the runtime tests**

Run:

```bash
uv run pytest tests/test_runtime_state_flow.py tests/test_runtime_multi_source_round_dispatch.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/seektalent/runtime/orchestrator.py tests/test_runtime_state_flow.py tests/test_runtime_multi_source_round_dispatch.py
git commit -m "fix: gate scoring on selected source readiness"
```

---

### Task 2: Block Workbench Runtime Start When Selected Source Is Blocked

**Files:**
- Modify: `src/seektalent_ui/workbench_routes.py`
- Modify: `src/seektalent_ui/workbench_store.py`
- Modify: `src/seektalent_ui/runtime_bridge.py`
- Modify: `tests/test_workbench_api.py`
- Modify: `tests/test_workbench_runtime_owned_execution.py`

- [ ] **Step 1: Add or update an API test for blocked Liepin at session start**

In `tests/test_workbench_api.py`, update `test_session_start_requires_approved_requirement_review_and_blocks_unconnected_liepin(...)` or add a focused new test that creates a CTS+Liepin session, lets the Liepin pre-start probe block the selected source, calls `/api/workbench/sessions/{session_id}/start`, and asserts no runtime job is queued.

Use the existing test client/session helpers in the file. The assertion shape should be:

```python
_approve_requirement_review(client, session["sessionId"])
start = _start_session(client, session["sessionId"])

assert start.status_code == 202, start.text
payload = start.json()
assert payload["sourceRuns"] == []
assert payload["runtimeJob"] is None
assert payload["blockedSources"] == [
    {
        "sourceRunId": runs["liepin"]["sourceRunId"],
        "sourceKind": "liepin",
        "reason": "source_browser_backend_unavailable",
    }
]
assert not FakeWorkbenchRuntime.started.wait(timeout=0.1)
```

Then assert the refreshed session still exposes the blocked Liepin card status and warning code:

```python
refreshed = client.get(f"/api/workbench/sessions/{session['sessionId']}")
cards = {card["sourceKind"]: card for card in refreshed.json()["sourceCards"]}
assert cards["liepin"]["status"] == "blocked"
assert cards["liepin"]["authState"] == "login_required"
assert cards["liepin"]["warningCode"] == "source_browser_backend_unavailable"
```

If this test already exists with `runtimeJob` queued, update the existing expectations instead of adding duplicate coverage.

- [ ] **Step 2: Add a failing store-level test**

In `tests/test_workbench_runtime_owned_execution.py`, add:

```python
def test_start_runtime_sourcing_job_rejects_blocked_selected_source(tmp_path):
    store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user, session = _approved_dual_source_session(store)
    refreshed = store.get_workbench_session(user=user, session_id=session.session_id)
    assert refreshed is not None
    liepin_run = next(source_run for source_run in refreshed.source_runs if source_run.source_kind == "liepin")
    store.block_source_run_for_start_probe(
        user=user,
        session_id=session.session_id,
        source_run_id=liepin_run.source_run_id,
        warning_code="liepin_opencli_risk_page",
        warning_message="Risk verification required.",
    )

    with pytest.raises(PermissionError, match="selected_source_blocked"):
        store.start_runtime_sourcing_job(
            user=user,
            session_id=session.session_id,
            idempotency_key="runtime",
        )
```

This uses the existing `_approved_dual_source_session(...)` helper and the public `get_workbench_session(...)` read path.

- [ ] **Step 3: Run the focused failing tests**

Run:

```bash
uv run pytest tests/test_workbench_api.py::test_session_start_requires_approved_requirement_review_and_blocks_unconnected_liepin tests/test_workbench_runtime_owned_execution.py::test_start_runtime_sourcing_job_rejects_blocked_selected_source -q
```

Expected: FAIL because runtime job currently still starts or the exact error is not returned.

- [ ] **Step 4: Enforce blocked-source rejection in the store**

In `src/seektalent_ui/workbench_store.py`, inside `start_runtime_sourcing_job(...)` after loading `source_runs`, add:

```python
blocked_selected = [source_run for source_run in source_runs if source_run.status == "blocked"]
if blocked_selected:
    raise PermissionError("selected_source_blocked")
```

Keep the error plain. This store method must never create `runtime_sourcing_jobs` when a selected source run is blocked.

- [ ] **Step 5: Return a safe non-start response with structured blocked sources**

In `src/seektalent_ui/workbench_routes.py`, after the pre-start probe loop and before calling `store.start_runtime_sourcing_job(...)`, return a normal start response without a runtime job when the route has just blocked a selected source:

```python
if blocked:
    return WorkbenchSessionStartResponse(
        sessionId=session_id,
        sourceRuns=started,
        runtimeJob=None,
        blockedSources=blocked,
    )
```

Add a small route-local helper near the other response helpers to preserve visibility when the store catches a pre-existing blocked source:

```python
def _session_start_blocked_sources(session: WorkbenchSessionResponse) -> list[WorkbenchSessionStartBlockedSourceResponse]:
    return [
        WorkbenchSessionStartBlockedSourceResponse(
            sourceRunId=source_run.sourceRunId,
            sourceKind=source_run.sourceKind,
            reason=_public_runtime_source_reason_code(source_run.warningCode or "")
            or "source_provider_failed",
        )
        for source_run in session.sourceRuns
        if source_run.status == "blocked"
    ]
```

If local model attribute names differ, use the exact names from the existing response model. Then map `selected_source_blocked` without returning a bare `409`:

```python
except PermissionError as exc:
    detail = str(exc)
    if detail == "selected_source_blocked":
        refreshed = store.get_workbench_session(user=user, session_id=session_id)
        if refreshed is None:
            raise HTTPException(status_code=404, detail="Not found.") from exc
        return WorkbenchSessionStartResponse(
            sessionId=session_id,
            sourceRuns=started,
            runtimeJob=None,
            blockedSources=_session_start_blocked_sources(_workbench_session_response(refreshed)),
        )
    raise HTTPException(status_code=409, detail=detail) from exc
```

Preserve existing handling for requirement-review errors.

- [ ] **Step 6: Keep runtime bridge narrow**

In `src/seektalent_ui/runtime_bridge.py`, leave `source_kinds=context.job.source_kinds` unchanged if Task 2 store rejection already prevents blocked sources. Add a defensive assertion before constructing `run_kwargs` only if `WorkbenchRuntimeSourcingJobContext` exposes source run statuses:

```python
if any(source_run.status == "blocked" for source_run in context.source_runs):
    raise RuntimeError("selected_source_blocked")
```

If the context does not expose source runs, do not widen it in this task; the store gate is the source of truth.

- [ ] **Step 7: Run Workbench tests**

Run:

```bash
uv run pytest tests/test_workbench_api.py tests/test_workbench_runtime_owned_execution.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/seektalent_ui/workbench_routes.py src/seektalent_ui/workbench_store.py src/seektalent_ui/runtime_bridge.py tests/test_workbench_api.py tests/test_workbench_runtime_owned_execution.py
git commit -m "fix: block runtime start for blocked selected sources"
```

---

### Task 3: Add Deterministic-Testable OpenCLI Pacing

**Files:**
- Modify: `src/seektalent/config.py`
- Modify: `src/seektalent/providers/liepin/client.py`
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
- Modify: `tests/test_liepin_config.py`
- Modify: `tests/test_pi_opencli_browser.py`

- [ ] **Step 1: Add failing config defaults test**

In `tests/test_liepin_config.py`, extend the OpenCLI defaults test:

```python
assert settings.liepin_opencli_pacing_enabled is True
assert settings.liepin_opencli_pacing_min_ms == 700
assert settings.liepin_opencli_pacing_max_ms == 1800
```

Add validation assertions:

```python
monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_PACING_MIN_MS", "2000")
monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_PACING_MAX_MS", "1000")
with pytest.raises(ValueError, match="liepin_opencli_pacing"):
    AppSettings(_env_file=None)
```

- [ ] **Step 2: Add failing helper pacing test**

In `tests/test_pi_opencli_browser.py`, add a fake sleeper:

```python
def test_opencli_mutating_actions_apply_pacing(tmp_path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("seektalent.providers.pi_agent.opencli_browser.time.sleep", sleeps.append)
    monkeypatch.setattr("seektalent.providers.pi_agent.opencli_browser.random.uniform", lambda low, high: low)

    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "fill", "--role", "combobox", "--nth", "0", "python"): "{}",
        }
    )
    runner = _runner(
        commands,
        lease_dir=tmp_path,
        pacing_enabled=True,
        pacing_min_ms=700,
        pacing_max_ms=1800,
    )

    runner.fill(target="搜索", text="python")

    assert sleeps == [0.7]
```

`OpenCliBrowserConfig` is frozen, so do not mutate `runner._config` in tests.

Update the local `_runner(...)` helper signature in `tests/test_pi_opencli_browser.py`:

```python
def _runner(
    commands: FakeCommands,
    *,
    allowed_click_refs: tuple[str, ...] = (),
    lease_dir: Path | None = None,
    detail_open_timeout_seconds: int = 5,
    idle_close_seconds: int = 120,
    close_blank_window: bool = True,
    blank_window_closer: FakeBlankWindowCloser | None = None,
    pacing_enabled: bool = False,
    pacing_min_ms: int = 0,
    pacing_max_ms: int = 0,
) -> OpenCliBrowserRunner:
    return OpenCliBrowserRunner(
        config=OpenCliBrowserConfig(
            command=("opencli",),
            session="seektalent-liepin",
            timeout_seconds=10,
            policy=default_liepin_opencli_policy(
                allowed_hosts=("www.liepin.com", "h.liepin.com"),
                allowed_start_urls=("https://h.liepin.com/search/getConditionItem#session",),
            ),
            allowed_click_refs=allowed_click_refs,
            lease_dir=lease_dir,
            artifact_root=lease_dir,
            detail_open_timeout_seconds=detail_open_timeout_seconds,
            idle_close_seconds=idle_close_seconds,
            close_blank_window=close_blank_window,
            cleanup_worker_enabled=False,
            pacing_enabled=pacing_enabled,
            pacing_min_ms=pacing_min_ms,
            pacing_max_ms=pacing_max_ms,
        ),
        commands=commands,
        window_counter=FakeWindowCounter(),
        blank_window_closer=blank_window_closer,
    )
```

- [ ] **Step 3: Run focused failing tests**

```bash
uv run pytest tests/test_liepin_config.py::test_liepin_opencli_backend_defaults_to_disabled tests/test_pi_opencli_browser.py::test_opencli_mutating_actions_apply_pacing -q
```

Expected: FAIL because pacing settings/helper do not exist.

- [ ] **Step 4: Add settings**

In `src/seektalent/config.py`, add fields near existing OpenCLI settings:

```python
liepin_opencli_pacing_enabled: bool = True
liepin_opencli_pacing_min_ms: int = 700
liepin_opencli_pacing_max_ms: int = 1800
```

In `validate_ranges(...)`, add:

```python
if self.liepin_opencli_pacing_min_ms < 0 or self.liepin_opencli_pacing_max_ms < 0:
    raise ValueError("liepin_opencli_pacing values must be non-negative")
if self.liepin_opencli_pacing_min_ms > self.liepin_opencli_pacing_max_ms:
    raise ValueError("liepin_opencli_pacing_min_ms must be <= liepin_opencli_pacing_max_ms")
```

- [ ] **Step 5: Pass settings into OpenCLI env**

In `src/seektalent/providers/liepin/client.py`, add to `opencli_env`:

```python
"SEEKTALENT_LIEPIN_OPENCLI_PACING_ENABLED": "true" if settings.liepin_opencli_pacing_enabled else "false",
"SEEKTALENT_LIEPIN_OPENCLI_PACING_MIN_MS": str(settings.liepin_opencli_pacing_min_ms),
"SEEKTALENT_LIEPIN_OPENCLI_PACING_MAX_MS": str(settings.liepin_opencli_pacing_max_ms),
```

- [ ] **Step 6: Implement pacing wrapper**

In `src/seektalent/providers/pi_agent/opencli_browser.py`, import `random` if absent. Add config fields to `OpenCliBrowserConfig`:

```python
pacing_enabled: bool = True
pacing_min_ms: int = 700
pacing_max_ms: int = 1800
```

Add method:

```python
def _pace_before_action(self, action: str) -> None:
    if not self._config.pacing_enabled:
        return
    if action not in {"fill", "click", "scroll", "apply_liepin_filters", "open_liepin_detail"}:
        return
    low = max(0, self._config.pacing_min_ms) / 1000
    high = max(self._config.pacing_max_ms, self._config.pacing_min_ms) / 1000
    if high <= 0:
        return
    time.sleep(random.uniform(low, high))
```

Call it at the beginning of `fill`, `click`, `scroll`, `apply_liepin_native_filters`, and `open_liepin_detail`.

- [ ] **Step 7: Parse env into runner config**

In `opencli_browser_cli._runner_from_env()`, parse from `os.environ` using the current helper signatures:

```python
pacing_enabled=_env_bool(os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_PACING_ENABLED"), default=True),
pacing_min_ms=int(os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_PACING_MIN_MS") or "700"),
pacing_max_ms=int(os.environ.get("SEEKTALENT_LIEPIN_OPENCLI_PACING_MAX_MS") or "1800"),
```

Keep test helper defaults disabled to avoid slowing unrelated unit tests; production/env defaults remain enabled.

- [ ] **Step 8: Run tests**

```bash
uv run pytest tests/test_liepin_config.py tests/test_pi_opencli_browser.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/seektalent/config.py src/seektalent/providers/liepin/client.py src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/providers/pi_agent/opencli_browser_cli.py tests/test_liepin_config.py tests/test_pi_opencli_browser.py
git commit -m "feat: add bounded opencli pacing"
```

---

### Task 4: Add Structured Visible Liepin Card Extraction Tool

**Files:**
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
- Modify: `src/seektalent/providers/pi_agent/opencli_browser_cli.py`
- Modify: `src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts`
- Modify: `src/seektalent/providers/pi_agent/pi_external.py`
- Modify: `tests/test_pi_opencli_browser.py`
- Modify: `tests/test_pi_external_agent.py`
- Modify: `tests/test_liepin_boundaries.py`

- [ ] **Step 1: Add failing Python helper test**

In `tests/test_pi_opencli_browser.py`, add:

```python
def test_extract_visible_liepin_cards_returns_structured_safe_cards(tmp_path):
    state_text = (
        "[70]<button><span>查看完整简历</span></button>\n"
        "王** 40岁 工作14年 硕士 上海\n"
        "数据仓库 数据治理 Python Hive\n"
        "某科技公司 · 大数据开发工程师2022.08-至今(3年9个月)\n"
        "沈阳工业大学 · 本科\n"
        "[71]<button><span>查看完整简历</span></button>\n"
        "李** 29岁 工作6年 本科 杭州\n"
        "Flink Spark 实时数仓\n"
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "state"): state_text,
        }
    )

    result = _runner(commands, lease_dir=tmp_path).extract_visible_liepin_cards(source_run_id="run-1", max_cards=10)

    assert result.ok is True
    payload = json.loads(result.private_output)
    assert payload["schema_version"] == "seektalent.opencli_liepin_visible_cards.v1"
    first = payload["cards"][0]
    assert first["provider_rank"] == 1
    assert first["ref"] == "70"
    assert first["current_or_recent_company"] == "某科技公司"
    assert first["current_or_recent_title"].startswith("大数据开发工程师")
    assert first["education_level"] == "硕士"
    assert first["work_years"] == 14
    assert "数据仓库" in first["visible_text"]
    assert "raw_html" not in json.dumps(payload)
    assert "cookie" not in json.dumps(payload).lower()
```

- [ ] **Step 2: Add failing TS extension declaration test**

In `tests/test_pi_external_agent.py`, extend `test_opencli_pi_extension_exposes_only_restricted_tools`:

```python
assert "seektalent_opencli_extract_visible_liepin_cards" in text
```

Add a contract assertion:

```python
def test_liepin_resume_prompt_requires_structured_visible_card_read(tmp_path):
    prompt = json.dumps({"task": "liepin.search_resumes", "source_run_id": "run-1"}, ensure_ascii=False)
    contract = _task_contract_for_prompt(prompt)
    assert "seektalent_opencli_extract_visible_liepin_cards" in contract
    assert "read visible Liepin cards" in contract
```

- [ ] **Step 3: Run focused failing tests**

```bash
uv run pytest tests/test_pi_opencli_browser.py::test_extract_visible_liepin_cards_returns_structured_safe_cards tests/test_pi_external_agent.py::test_opencli_pi_extension_exposes_only_restricted_tools tests/test_pi_external_agent.py::test_liepin_resume_prompt_requires_structured_visible_card_read -q
```

Expected: FAIL because the tool does not exist.

- [ ] **Step 4: Implement helper method**

In `src/seektalent/providers/pi_agent/opencli_browser.py`, add:

```python
def extract_visible_liepin_cards(self, *, source_run_id: str, max_cards: int) -> OpenCliBrowserResult:
    try:
        if max_cards < 1 or max_cards > 50:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        state = self.state()
        if not state.ok:
            return state
        state_text = state.private_output or str(state.observation.get("text") or "")
        summaries = list(extract_liepin_card_summaries(state_text, max_cards=max_cards))
        targets = _merge_liepin_detail_targets(
            _rank_liepin_detail_targets(state_text, max_cards=max_cards),
            self._find_liepin_result_card_detail_targets(state_text=state_text, max_cards=max_cards),
            max_cards=max_cards,
        )
        cards: list[dict[str, object]] = []
        for index, target in enumerate(targets, start=1):
            summary = summaries[index - 1] if index - 1 < len(summaries) else {}
            visible_text = str(summary.get("normalized_card_text") or target.block_text)
            cards.append(
                {
                    "provider_rank": index,
                    "ref": target.ref,
                    "visible_text": _safe_visible_card_text(visible_text),
                    "display_title": summary.get("display_title"),
                    "current_or_recent_company": summary.get("current_or_recent_company"),
                    "current_or_recent_title": summary.get("current_or_recent_title"),
                    "city": summary.get("city"),
                    "expected_city": summary.get("expected_city"),
                    "education_level": summary.get("education_level"),
                    "work_years": summary.get("work_years"),
                    "age": summary.get("age"),
                    "school_names": summary.get("school_names") or [],
                    "skill_tags": summary.get("skill_tags") or [],
                    "job_intention": summary.get("job_intention"),
                    "recent_experience_text": summary.get("recent_experience_text"),
                }
            )
        payload = {
            "schema_version": "seektalent.opencli_liepin_visible_cards.v1",
            "source_run_id": source_run_id,
            "cards": cards,
            "card_count": len(cards),
        }
        return OpenCliBrowserResult(
            ok=True,
            action="extract_visible_liepin_cards",
            counts={"cards": len(cards)},
            private_output=json.dumps(payload, ensure_ascii=False),
        )
    except OpenCliBrowserError as exc:
        return OpenCliBrowserResult(ok=False, action="extract_visible_liepin_cards", safe_reason_code=exc.safe_reason_code)
```

Add `_safe_visible_card_text(...)` near existing safe text helpers:

```python
def _safe_visible_card_text(text: str) -> str:
    cleaned = _bounded_public_text("\n".join(_clean_state_lines(text)), max_chars=1200)
    return cleaned
```

This deliberately reuses existing `extract_liepin_card_summaries(...)` so the new tool returns the visible title/company/location/education/experience fragments required by the spec instead of only raw text.

- [ ] **Step 5: Add CLI command**

In `src/seektalent/providers/pi_agent/opencli_browser_cli.py`, add a command branch inside `_run_action(...)` using the current payload helpers:

```python
if action == "extract_visible_liepin_cards":
    return runner.extract_visible_liepin_cards(
        source_run_id=str(payload.get("sourceRunId") or payload.get("source_run_id") or ""),
        max_cards=_payload_int(payload, "maxCards", "max_cards", default=10),
    )
```

Do not read stdin again inside `_run_action(...)`; `main()` has already parsed the JSON payload.

- [ ] **Step 6: Add TS extension tool**

In `src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts`, add the tool to `capabilitiesPayload().capabilities.tools`:

```ts
"seektalent_opencli_extract_visible_liepin_cards",
```

Register:

```ts
pi.registerTool({
  name: "seektalent_opencli_extract_visible_liepin_cards",
  label: "Read visible Liepin cards",
  description: "Read structured visible Liepin result cards from the current search results page without clicking or opening details.",
  parameters: Type.Object({
    sourceRunId: Type.String(),
    maxCards: Type.Optional(Type.Number()),
  }),
  async execute(_toolCallId: string, params: ToolParams) {
    return textResult(await runAction("extract_visible_liepin_cards", params));
  },
});
```

Do not add this action to `MUTATING_ACTIONS`.

- [ ] **Step 7: Update PI prompt contract**

In `src/seektalent/providers/pi_agent/pi_external.py`, update the `liepin.search_resumes` contract to say:

```text
After search results render and native filters are applied, call seektalent_opencli_extract_visible_liepin_cards to read visible Liepin cards before opening detail pages.
Use requirement_sheet to decide which visible cards are clear mismatches and which should be opened.
```

Add the tool name to the listed browser tools.

- [ ] **Step 8: Run tests**

```bash
uv run pytest tests/test_pi_opencli_browser.py tests/test_pi_external_agent.py tests/test_liepin_boundaries.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/providers/pi_agent/opencli_browser_cli.py src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts src/seektalent/providers/pi_agent/pi_external.py tests/test_pi_opencli_browser.py tests/test_pi_external_agent.py tests/test_liepin_boundaries.py
git commit -m "feat: expose structured liepin card reads"
```

---

### Task 5: Fix Detail Open Idempotency State

**Files:**
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
- Modify: `tests/test_pi_opencli_browser.py`

- [ ] **Step 1: Add failing failed-open retry test**

In `tests/test_pi_opencli_browser.py`, add:

```python
def test_failed_detail_open_does_not_mark_ref_reusable(tmp_path, monkeypatch):
    monkeypatch.setattr("seektalent.providers.pi_agent.opencli_browser.time.sleep", lambda _: None)
    commands = EvalCommands(
        eval_output="null",
        outputs={
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "王** 40岁 工作14年 硕士 上海\n"
                "[70]<button><span>查看完整简历</span></button>"
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): "[]",
            ("opencli", "browser", "seektalent-liepin", "click", "70"): subprocess.CalledProcessError(1, ["opencli"]),
        }
    )
    runner = _runner(commands, lease_dir=tmp_path)

    first = runner.open_liepin_detail(source_run_id="run-1", ref="70", rank=1)
    second = runner.open_liepin_detail(source_run_id="run-1", ref="70", rank=1)

    assert first.ok is False
    assert second.counts.get("reused") != 1
```

This uses the existing `EvalCommands` helper, which returns the configured value for any OpenCLI `eval` command.

- [ ] **Step 2: Add captured-resume reuse test**

Add:

```python
def test_captured_detail_resume_reuse_is_allowed_without_duplicate_open(tmp_path):
    runner = _runner(FakeCommands(outputs={}), lease_dir=tmp_path)
    safe_run_id = "run-1"
    runner._write_collected_resumes(
        safe_run_id,
        [
            {
                "provider_rank": 1,
                "candidate_resume_id": "liepin-opencli-detail-run-1-1",
                "protected_snapshot_ref": "artifact://protected/pi-detail/run-1/1.json",
                "normalized_text": "Python RAG",
            }
        ],
    )

    result = runner.open_liepin_detail(source_run_id="run-1", ref="70", rank=1)

    assert result.ok is True
    assert result.counts["reused"] == 1
```

- [ ] **Step 3: Run failing tests**

```bash
uv run pytest tests/test_pi_opencli_browser.py::test_failed_detail_open_does_not_mark_ref_reusable tests/test_pi_opencli_browser.py::test_captured_detail_resume_reuse_is_allowed_without_duplicate_open -q
```

Expected: FAIL because idempotency only sees `open_detail` events.

- [ ] **Step 4: Replace boolean open event check with state check**

In `src/seektalent/providers/pi_agent/opencli_browser.py`, replace `_detail_ref_was_opened(...)` with:

```python
def _detail_ref_open_state(self, *, source_run_id: str, ref: str, rank: int) -> str | None:
    safe_run_id = _safe_artifact_segment(source_run_id)
    if any(int(item.get("provider_rank") or 0) == rank for item in self._read_collected_resumes(safe_run_id)):
        return "captured"
    state: str | None = None
    for event in self._read_agent_events(safe_run_id):
        if event.get("ref") != ref:
            continue
        if event.get("action_kind") == "open_detail_succeeded":
            state = "succeeded"
        elif event.get("action_kind") in {"open_detail_failed", "open_detail_timeout"}:
            state = "failed"
    return state
```

Update `open_liepin_detail(...)`:

```python
open_state = self._detail_ref_open_state(source_run_id=source_run_id, ref=ref, rank=rank)
if open_state in {"captured", "succeeded"}:
    return OpenCliBrowserResult(ok=True, action="open_liepin_detail", counts={"rank": rank, "reused": 1})
```

After `_open_liepin_detail_ref_controlled(...)` succeeds, append:

```python
self._append_agent_event(source_run_id, {"action_kind": "open_detail_succeeded", "route_kind": "detail", "ref": ref, "rank": rank})
```

When click/claim fails, append `open_detail_failed` or keep `open_detail_timeout` as the failed state.

- [ ] **Step 5: Ensure pending event is not success**

Keep the initial event but rename it if useful:

```python
{"action_kind": "open_detail_requested", "route_kind": "detail", "ref": ref, "rank": rank}
```

Update tests expecting `open_detail` to accept the new event name, or keep both event names but only treat `open_detail_succeeded` as reusable.

- [ ] **Step 6: Run OpenCLI helper tests**

```bash
uv run pytest tests/test_pi_opencli_browser.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/seektalent/providers/pi_agent/opencli_browser.py tests/test_pi_opencli_browser.py
git commit -m "fix: track liepin detail open idempotency states"
```

---

### Task 6: Harden PI Session Cleanup Paths

**Files:**
- Modify: `src/seektalent/providers/pi_agent/pi_external.py`
- Modify: `src/seektalent/providers/liepin/pi_executor.py`
- Modify: `tests/test_pi_external_agent.py`
- Modify: `tests/test_liepin_pi_executor.py`

- [ ] **Step 1: Add cleanup-on-terminal parameterized test**

In `tests/test_pi_external_agent.py`, add:

```python
@pytest.mark.parametrize("status", [PiRpcTaskStatus.SUCCEEDED, PiRpcTaskStatus.TIMEOUT, PiRpcTaskStatus.FAILED])
def test_json_task_session_cleans_up_once_for_terminal_status(monkeypatch, tmp_path, status):
    cleanup_calls = []
    monkeypatch.setattr(
        pi_external,
        "_cleanup_liepin_opencli_detail_tabs_after_rpc",
        lambda *, prompt, env: cleanup_calls.append((prompt, dict(env))),
    )
    client = PiRpcAgentClient(
        command=("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", "skill.md"),
        skill_path=Path("skill.md"),
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=tmp_path / "artifacts" / "pi-agent",
        env={"SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND": "opencli", "SEEKTALENT_LIEPIN_OPENCLI_SESSION": "session"},
        transport=FakeRpcTransport(PiRpcTaskResult(status=status, tool_final_text='{"schema_version":"seektalent.pi_liepin_resumes.v2"}')),
    )
    prompt = json.dumps({"task": "liepin.search_resumes", "source_run_id": "run-1"}, ensure_ascii=False)

    with client.open_json_task_session(cleanup_prompt=prompt) as session:
        session.run_json_task_result(prompt)

    assert len(cleanup_calls) == 1
```

- [ ] **Step 2: Add repair-failure cleanup test in executor**

In `tests/test_liepin_pi_executor.py`, add a fake `PiJsonTaskSession` whose first result needs repair and second result fails. Assert cleanup close is called once.

Test shape:

```python
def test_resume_search_cleanup_runs_after_repair_failure(tmp_path):
    client = FakePiClientWithSession(
        first=_ok_resume_envelope_missing_detail(),
        repair=_failed_external_result("liepin_opencli_timeout"),
    )
    executor = PiLiepinExecutor(client=client, key_hasher=_hasher(), artifact_registry=_registry(tmp_path))

    result = executor.search_resumes(
        source_run_id="run-1",
        keyword_query="python",
        query_terms=("python",),
        target_resumes=1,
        max_cards=5,
        max_pages=1,
        requirement_sheet=_requirement_sheet(),
    )

    assert result.status in {PiLiepinResultStatus.BLOCKED, PiLiepinResultStatus.FAILED}
    assert client.closed_sessions == 1
```

Use existing fake classes in this test file if present.

- [ ] **Step 3: Run focused tests**

```bash
uv run pytest tests/test_pi_external_agent.py::test_json_task_session_cleans_up_once_for_terminal_status tests/test_liepin_pi_executor.py::test_resume_search_cleanup_runs_after_repair_failure -q
```

Expected: PASS if previous slice already did enough; otherwise FAIL and fix.

- [ ] **Step 4: Fix cleanup only if tests fail**

If cleanup is not guaranteed, update `PiJsonTaskSession.close()` in `src/seektalent/providers/pi_agent/pi_external.py` so it is idempotent:

```python
def close(self) -> None:
    if self._closed:
        return
    self._closed = True
    try:
        self._session.close()
    finally:
        _cleanup_liepin_opencli_detail_tabs_after_rpc(prompt=self._cleanup_prompt, env=self._command_env)
```

If the session already has equivalent logic, keep production unchanged and retain the tests.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_pi_external_agent.py tests/test_liepin_pi_executor.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/providers/pi_agent/pi_external.py src/seektalent/providers/liepin/pi_executor.py tests/test_pi_external_agent.py tests/test_liepin_pi_executor.py
git commit -m "test: lock pi opencli cleanup lifecycle"
```

---

### Task 7: Final Verification And Boundary Checks

**Files:**
- Modify only files touched by earlier tasks if verification exposes a bug.

- [ ] **Step 1: Run focused test suite**

```bash
uv run pytest tests/test_runtime_multi_source_round_dispatch.py tests/test_runtime_state_flow.py tests/test_workbench_runtime_owned_execution.py tests/test_workbench_api.py tests/test_pi_opencli_browser.py tests/test_pi_external_agent.py tests/test_liepin_pi_executor.py tests/test_liepin_config.py tests/test_liepin_boundaries.py -q
```

Expected: PASS.

- [ ] **Step 2: Run changed-file lint**

Run:

```bash
uv run ruff check src/seektalent/runtime/orchestrator.py src/seektalent_ui/workbench_routes.py src/seektalent_ui/workbench_store.py src/seektalent_ui/runtime_bridge.py src/seektalent/config.py src/seektalent/providers/liepin/client.py src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/providers/pi_agent/opencli_browser_cli.py src/seektalent/providers/pi_agent/pi_external.py src/seektalent/providers/liepin/pi_executor.py
```

Expected: PASS.

- [ ] **Step 3: Search for accidental degraded dual-source fallback wording**

Run:

```bash
rg "available_sources_only|source_lanes_degraded|degraded" src/seektalent src/seektalent_ui tests
```

Expected: Existing coverage/reporting terms may remain, but no active CTS+Liepin Runtime scoring path should treat Liepin blocked/failed/partial/empty as acceptable.

- [ ] **Step 4: Search for fixed wait-only OpenCLI pacing**

Run:

```bash
rg "wait_time\\(seconds=(1|2|3)\\)|time\\.sleep\\((1|2|3)\\)" src/seektalent/providers/pi_agent/opencli_browser.py
```

Expected: Fixed waits may remain for render polling, but mutating actions must also go through `_pace_before_action(...)`.

- [ ] **Step 5: Commit final fixes if any**

If verification required code changes:

```bash
git add src/seektalent/runtime/orchestrator.py src/seektalent_ui/workbench_routes.py src/seektalent_ui/workbench_store.py src/seektalent_ui/runtime_bridge.py src/seektalent/config.py src/seektalent/providers/liepin/client.py src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/providers/pi_agent/opencli_browser_cli.py src/seektalent/providers/pi_agent/pi_external.py src/seektalent/providers/liepin/pi_executor.py tests/test_runtime_state_flow.py tests/test_workbench_api.py tests/test_workbench_runtime_owned_execution.py tests/test_pi_opencli_browser.py tests/test_pi_external_agent.py tests/test_liepin_pi_executor.py tests/test_liepin_config.py tests/test_liepin_boundaries.py
git commit -m "fix: complete runtime source readiness safety"
```

If no code changes were needed, do not create an empty commit.

## Self-Review

- Spec coverage:
  - Strict selected-source readiness: Task 1.
  - Workbench blocked-source start gate: Task 2.
  - OpenCLI randomized pacing: Task 3.
  - Structured visible-card read tool: Task 4.
  - Detail tab idempotency state: Task 5.
  - PI cleanup lifecycle: Task 6.
  - Verification and boundary scans: Task 7.
- Placeholder scan:
  - No placeholder implementation steps are intentionally left.
- Type consistency:
  - `SourceRoundDispatchResult`, `SourceRoundAdapterResult`, `RunStageError`, `OpenCliBrowserResult`, `PiRpcAgentClient`, and `PiLiepinExecutor.search_resumes(...)` match current code names.
  - New OpenCLI tool name is consistently `seektalent_opencli_extract_visible_liepin_cards`.

---

## Plan Review Revision Log

This plan was revised after `fw-plan-review` found six blockers.

- Task 1 now handles missing selected source results through `RuntimeSourceCoverageSummary`.
- Task 1 now keeps `_merge_source_round_dispatch_result(...)` as a single existing merge point and raises before scoring receives the retrieval result.
- Task 1 now includes a `dispatch_source_rounds(...)` barrier regression for CTS finishing before Liepin.
- Task 2 now preserves the structured Workbench `blockedSources` response and returns `202` with `runtimeJob: null` instead of a bare `409` for selected-source blocking.
- Task 3 now passes pacing config through the frozen `OpenCliBrowserConfig` constructor instead of mutating `runner._config`.
- Task 4 now reuses `extract_liepin_card_summaries(...)` so visible-card extraction returns structured title/company/location/education/experience fragments.

Next gate: run `fw-plan-review` again before `fw-build`.

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `fw-ceo-review` | Scope & strategy | 0 | SKIPPED | User explicitly kept this slice in planning/review flow without CEO gate. |
| Codex Review | `codex review` | Independent 2nd opinion | 0 | SKIPPED | Not requested for this planning gate. |
| Eng Review | `fw-plan-review` | Architecture & tests (required) | 2 | CLEAR | Previous 6 blockers are resolved in the plan; no new blocking architecture, code-quality, test, or performance gaps found. |
| Design Review | `fw-plan-review` conditional design screen | UI/UX gaps | 1 | CLEAR | No frontend visual changes; Workbench user-visible blocked-source state stays structured through `blockedSources` and has API coverage. |
| DX Review | `plan-devex-review` | Developer experience gaps | 0 | SKIPPED | Not relevant to this backend/runtime safety slice. |

- **UNRESOLVED:** 0.
- **VERDICT:** ENG CLEARED. Conditional design screen clear. Ready for `fw-build` after user confirmation.
