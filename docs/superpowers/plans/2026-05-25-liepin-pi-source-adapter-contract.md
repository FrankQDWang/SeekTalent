# Liepin PI Source Adapter Contract Implementation Plan

> Superseded cleanup note (2026-05-28): this historical plan includes now-obsolete
> `cleanup_prompt` / `_cleanup_liepin_opencli_detail_tabs_after_rpc` snippets. Current
> code intentionally does not auto-close Liepin detail tabs. Real detail-tab closing is
> deferred to the root `TODOS.md` OpenCLI fork task.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Liepin a detail-backed PI Harness source adapter under the unified CLI runtime source flow.

**Architecture:** The runtime continues to own logical query allocation, source dispatch, merge, normalization, and scoring. CTS remains a direct API adapter; Liepin becomes a PI-backed adapter that receives the canonical `RequirementSheet`, runs `exploit` and `explore` child-agent jobs concurrently, validates structured full-resume output, performs one bounded semantic repair, and returns normalized candidate updates through `RuntimeSourceLaneResult`.

**Tech Stack:** Python 3.12, Pydantic, asyncio `TaskGroup`, pytest, existing SeekTalent runtime/source lane modules, existing PI RPC executor/client fakes.

---

## Spec Link

This plan implements:

`docs/superpowers/specs/2026-05-25-liepin-pi-source-adapter-contract-design.md`

## Execution Notes

- Do not run live Liepin/OpenCLI/browser automation in this slice.
- Do not change requirement extraction, scoring prompts, reflection prompts, or finalizer prompts.
- Do not add compatibility aliases for old Liepin PI payload fields.
- Do not make UI changes in this slice.
- Keep CTS behavior unchanged except for tests that prove the dual-source barrier.
- Treat `completed`, `partial`, `blocked`, and `failed` as terminal source states for the source barrier.
- Use fake PI/RPC clients in tests.
- Prefer deleting active old payload fields over preserving them behind compatibility branches.

## File Map

Runtime source contracts:

- Modify: `src/seektalent/runtime/source_round_dispatch.py`
  - Carry the canonical `RequirementSheet` on `SourceRoundDispatchRequest`.
  - Keep selected-source dispatch as the source barrier.
- Modify: `src/seektalent/runtime/source_lanes.py`
  - Carry the canonical `RequirementSheet` on `RuntimeSourceLaneRequest`.
  - Keep public payloads sanitized; expose counts and ids, not raw requirement text.
- Modify: `src/seektalent/runtime/orchestrator.py`
  - Pass `run_state.requirement_sheet` into the source dispatch request and Liepin adapter.
  - Ensure merge/scoring happen only after `dispatch_source_rounds()` returns both selected sources.
- Modify: `src/seektalent_ui/runtime_bridge.py`
  - Pass approved Workbench `RequirementSheet` into runtime source-lane calls instead of letting Workbench-owned Liepin paths omit it.

Liepin runtime adapter:

- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
  - Pass `RequirementSheet` into each lane request.
  - Run logical query lanes concurrently and merge results in logical query order.
  - Make detail-backed resume search the normal runtime lane path.
  - Populate `normalized_store_updates` for Liepin detail-backed candidates.
  - Remove active reliance on card-only detail recommendations for full runtime execution.
- Modify: `src/seektalent/providers/liepin/pi_worker_client.py`
  - Read `requirement_sheet_json` from provider context and pass it to `PiLiepinExecutor.search_resumes()`.
  - Stop reading `liepin_must_haves_json` and `liepin_nice_to_haves_json`.
- Modify: `src/seektalent/providers/liepin/pi_executor.py`
  - Replace active resume-search task schema with a requirement-sheet based payload.
  - Use a lane-scoped PI child-agent session for first output plus bounded repair.
  - Let the lane-scoped PI session own terminal cleanup instead of trusting cleanup claims in the PI envelope.
- Modify: `src/seektalent/providers/pi_agent/pi_external.py`
  - Add a lane-scoped JSON task session that keeps the same PI child-agent context open across first output validation and one repair prompt.
  - Update the active `liepin.search_resumes` task contract to v2 and remove must-have/nice-to-have wording.
  - Add an active `liepin.repair_resume_output` contract and expected v2 schema handling.
- Create: `src/seektalent/providers/liepin/pi_resume_contract.py`
  - Hold small Pydantic validation-gap and repair request models for the PI resume-search contract.

PI prompt and worker-facing docs:

- Modify: `src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md`
  - Mirror the active Python task contract so the skill body and runtime-injected prompt do not conflict.

Tests:

- Modify: `tests/test_runtime_multi_source_round_dispatch.py`
  - Add a slow-Liepin barrier regression.
  - Add a dispatch request assertion for `RequirementSheet`.
- Modify: `tests/test_workbench_runtime_owned_execution.py`
  - Assert Workbench runtime bridge source-lane calls pass the approved `RequirementSheet`.
- Modify: `tests/test_liepin_runtime_source_lane.py`
  - Add concurrency tests for Liepin `exploit`/`explore` logical lanes.
  - Replace the old shared-session serialization test with a two-child-agent concurrency test.
  - Add tests that detail-backed Liepin candidates populate normalized updates.
- Modify: `tests/test_liepin_pi_worker_client.py`
  - Add tests for requirement-sheet payload forwarding and old payload field removal.
- Modify: `tests/test_liepin_pi_executor.py`
  - Add tests for v2 resume task payload, output validation, semantic repair, and lane-session cleanup ordering.
- Modify: `tests/test_pi_external_agent.py`
  - Add tests that the active PI prompt contract advertises v2, does not mention old fields, supports repair, and cleans up only after the lane session is closed.
- Modify: `tests/test_runtime_source_adapter_boundary.py`
  - Add boundary assertions that Workbench/source callers use runtime source contracts instead of old active card-recommendation paths.

---

### Task 1: Add Dual-Source Barrier And RequirementSheet Contract Tests

**Files:**
- Modify: `tests/test_runtime_multi_source_round_dispatch.py`
- Modify: `tests/test_runtime_source_adapter_boundary.py`
- Modify: `src/seektalent/runtime/source_round_dispatch.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Test: `tests/test_runtime_multi_source_round_dispatch.py`

- [ ] **Step 1: Add a test helper for a canonical requirement sheet**

In `tests/test_runtime_multi_source_round_dispatch.py`, add this helper near the existing query helpers:

```python
from seektalent.models import RequirementSheet


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        job_title="AI Agent Engineer",
        title_anchor_terms=("AI Agent",),
        title_anchor_rationale="AI Agent is the searchable title anchor.",
        role_summary="Build agentic retrieval workflows.",
        must_have_capabilities=("LangGraph", "RAG"),
        preferred_capabilities=("evaluation",),
        exclusion_signals=("pure frontend",),
        hard_constraints={},
        preferences={"preferred_query_terms": ["LangGraph", "RAG"]},
        initial_query_term_pool=("LangGraph", "RAG", "agent workflow"),
        scoring_rationale="Prioritize agent workflow and retrieval evidence.",
    )
```

- [ ] **Step 2: Add a failing test that dispatch carries the requirement sheet**

Add this test:

```python
async def test_dispatch_request_carries_requirement_sheet_to_sources() -> None:
    request = SourceRoundDispatchRequest(
        runtime_run_id="run-1",
        round_no=1,
        logical_queries=(_dispatch("exploit", 7),),
        selected_sources=("cts", "liepin"),
        seen_resume_ids=frozenset(),
        seen_dedup_keys=frozenset(),
        requirement_sheet=_requirement_sheet(),
    )
    seen: dict[str, str] = {}

    async def cts_adapter(source_request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        assert source_request.requirement_sheet is not None
        seen["cts"] = source_request.requirement_sheet.job_title
        return SourceRoundAdapterResult(source="cts", status="completed")

    async def liepin_adapter(source_request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        assert source_request.requirement_sheet is not None
        seen["liepin"] = source_request.requirement_sheet.job_title
        return SourceRoundAdapterResult(source="liepin", status="completed")

    await dispatch_source_rounds(request=request, cts_adapter=cts_adapter, liepin_adapter=liepin_adapter)

    assert seen == {"cts": "AI Agent Engineer", "liepin": "AI Agent Engineer"}
```

- [ ] **Step 3: Add a failing test that CTS cannot advance the barrier while Liepin is still running**

Add this test:

```python
async def test_dispatch_waits_for_liepin_terminal_state_after_cts_finishes_first() -> None:
    request = SourceRoundDispatchRequest(
        runtime_run_id="run-1",
        round_no=1,
        logical_queries=(_dispatch("exploit", 7),),
        selected_sources=("cts", "liepin"),
        seen_resume_ids=frozenset(),
        seen_dedup_keys=frozenset(),
        requirement_sheet=_requirement_sheet(),
    )
    cts_finished = asyncio.Event()
    allow_liepin_to_finish = asyncio.Event()

    async def cts_adapter(source_request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        del source_request
        cts_finished.set()
        return SourceRoundAdapterResult(source="cts", status="completed")

    async def liepin_adapter(source_request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        del source_request
        await cts_finished.wait()
        await allow_liepin_to_finish.wait()
        return SourceRoundAdapterResult(source="liepin", status="completed")

    dispatch_task = asyncio.create_task(
        dispatch_source_rounds(request=request, cts_adapter=cts_adapter, liepin_adapter=liepin_adapter)
    )
    await asyncio.wait_for(cts_finished.wait(), timeout=1)
    await asyncio.sleep(0)

    assert not dispatch_task.done()

    allow_liepin_to_finish.set()
    result = await asyncio.wait_for(dispatch_task, timeout=1)
    assert [source_result.source for source_result in result.source_results] == ["cts", "liepin"]
```

- [ ] **Step 4: Run the new tests and confirm they fail on the missing contract only**

Run:

```bash
pytest tests/test_runtime_multi_source_round_dispatch.py::test_dispatch_request_carries_requirement_sheet_to_sources tests/test_runtime_multi_source_round_dispatch.py::test_dispatch_waits_for_liepin_terminal_state_after_cts_finishes_first -q
```

Expected:

```text
FAILED ... TypeError: SourceRoundDispatchRequest.__init__() got an unexpected keyword argument 'requirement_sheet'
```

The barrier test may pass after the constructor is added because `dispatch_source_rounds()` already uses `TaskGroup`.

- [ ] **Step 5: Add `requirement_sheet` to the dispatch request**

In `src/seektalent/runtime/source_round_dispatch.py`, import `RequirementSheet` under `TYPE_CHECKING` and add the field:

```python
if TYPE_CHECKING:
    from seektalent.models import RequirementSheet
    from seektalent.runtime.retrieval_runtime import RetrievalExecutionResult
    from seektalent.runtime.source_lanes import RuntimeSourceLaneResult
```

Update the dataclass:

```python
@dataclass(frozen=True)
class SourceRoundDispatchRequest:
    runtime_run_id: str
    round_no: int
    logical_queries: tuple[LogicalQueryDispatch, ...]
    selected_sources: tuple[SourceKind, ...]
    seen_resume_ids: frozenset[str]
    seen_dedup_keys: frozenset[str]
    requirement_sheet: "RequirementSheet"
    source_query_intents_by_source: Mapping[SourceKind, tuple[RuntimeSourceQueryIntent, ...]] = field(default_factory=dict)
```

Update all existing active `SourceRoundDispatchRequest(...)` test fixtures in `tests/test_runtime_multi_source_round_dispatch.py` and `tests/test_runtime_source_adapter_boundary.py` to pass `_requirement_sheet()`. This is a hard contract, not a compatibility alias.

- [ ] **Step 6: Pass the runtime requirement sheet from orchestrator**

In `src/seektalent/runtime/orchestrator.py`, update the `SourceRoundDispatchRequest(...)` construction inside the source round path:

Only change the request construction; keep the existing `cts_adapter` and `liepin_adapter` lambdas unchanged:

```python
request=SourceRoundDispatchRequest(
    runtime_run_id=tracer.run_id,
    round_no=round_no,
    logical_queries=logical_queries,
    selected_sources=tuple(lane.source for lane in source_plan),
    seen_resume_ids=frozenset(seen_resume_ids),
    seen_dedup_keys=frozenset(seen_dedup_keys),
    source_query_intents_by_source=source_query_intents_by_source,
    requirement_sheet=run_state.requirement_sheet,
),
```

- [ ] **Step 7: Run the dispatch tests**

Run:

```bash
pytest tests/test_runtime_multi_source_round_dispatch.py::test_dispatch_request_carries_requirement_sheet_to_sources tests/test_runtime_multi_source_round_dispatch.py::test_dispatch_waits_for_liepin_terminal_state_after_cts_finishes_first -q
```

Expected:

```text
2 passed
```

- [ ] **Step 8: Commit**

```bash
git add tests/test_runtime_multi_source_round_dispatch.py tests/test_runtime_source_adapter_boundary.py src/seektalent/runtime/source_round_dispatch.py src/seektalent/runtime/orchestrator.py
git commit -m "test: lock runtime source requirement contract"
```

---

### Task 2: Carry RequirementSheet Into Liepin Lane Requests

**Files:**
- Modify: `src/seektalent/runtime/source_lanes.py`
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Modify: `src/seektalent_ui/runtime_bridge.py`
- Modify: `tests/test_liepin_runtime_source_lane.py`
- Modify: `tests/test_runtime_source_lanes.py`
- Modify: `tests/test_workbench_runtime_owned_execution.py`
- Test: `tests/test_liepin_runtime_source_lane.py`
- Test: `tests/test_workbench_runtime_owned_execution.py`

- [ ] **Step 1: Add a failing Liepin lane request propagation test**

In `tests/test_liepin_runtime_source_lane.py`, reuse the existing `FakeWorker` and add this helper near the top of the file:

```python
from seektalent.models import RequirementSheet


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        job_title="AI Agent Engineer",
        title_anchor_terms=("AI Agent",),
        title_anchor_rationale="AI Agent is the searchable title anchor.",
        role_summary="Build agentic retrieval workflows.",
        must_have_capabilities=("LangGraph", "RAG"),
        preferred_capabilities=("evaluation",),
        exclusion_signals=("pure frontend",),
        hard_constraints={},
        preferences={"preferred_query_terms": ["LangGraph", "RAG"]},
        initial_query_term_pool=("LangGraph", "RAG", "agent workflow"),
        scoring_rationale="Prioritize agent workflow and retrieval evidence.",
    )
```

Add this test:

```python
def test_liepin_lane_passes_requirement_sheet_json_to_worker_context() -> None:
    worker = FakeWorker()
    result = asyncio.run(run_liepin_source_lane(
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
        ),
        worker_client=worker,
    ))

    assert result.status == "completed"
    provider_context = worker.search_calls[0]["provider_context"]
    requirement_payload = json.loads(provider_context["liepin_requirement_sheet_json"])
    assert requirement_payload["job_title"] == "AI Agent Engineer"
    assert requirement_payload["must_have_capabilities"] == ["LangGraph", "RAG"]
    assert "liepin_must_haves_json" not in provider_context
    assert "liepin_nice_to_haves_json" not in provider_context
```

- [ ] **Step 2: Run the test and confirm it fails on the missing `requirement_sheet` field**

Run:

```bash
pytest tests/test_liepin_runtime_source_lane.py::test_liepin_lane_passes_requirement_sheet_json_to_worker_context -q
```

Expected:

```text
FAILED ... TypeError: RuntimeSourceLaneRequest.__init__() got an unexpected keyword argument 'requirement_sheet'
```

- [ ] **Step 3: Add `requirement_sheet` to `RuntimeSourceLaneRequest`**

In `src/seektalent/runtime/source_lanes.py`, import for type checking:

```python
if TYPE_CHECKING:
    from seektalent.models import RequirementSheet
```

Add the field:

```python
@dataclass(frozen=True, kw_only=True)
class RuntimeSourceLaneRequest:
    source: SourceKind
    lane_mode: RuntimeSourceLaneMode
    job_title: str
    jd: str
    notes: str | None
    requirement_sheet: "RequirementSheet"
    runtime_run_id: str | None = None
```

Keep the existing fields after `runtime_run_id` in their current order.

Update `to_public_payload()` with sanitized counts:

```python
"requirement_sheet": {
    "job_title": self.requirement_sheet.job_title,
    "must_have_count": len(self.requirement_sheet.must_have_capabilities),
    "preferred_count": len(self.requirement_sheet.preferred_capabilities),
    "exclusion_count": len(self.requirement_sheet.exclusion_signals),
},
```

The public payload must include only counts and `job_title`, not full JD text, notes, raw capabilities, hard constraints, preferences, or scoring rationale.

Update existing `RuntimeSourceLaneRequest(...)` fixtures in `tests/test_runtime_source_lanes.py`, `tests/test_liepin_runtime_source_lane.py`, and `src/seektalent_ui/runtime_bridge.py` to pass a real requirement sheet. This prevents active source lanes from silently running on `job_title`/JD only.

Do not use this optional shape:

```python
"requirement_sheet": (
    {
        "job_title": self.requirement_sheet.job_title,
        "must_have_count": len(self.requirement_sheet.must_have_capabilities),
        "preferred_count": len(self.requirement_sheet.preferred_capabilities),
        "exclusion_count": len(self.requirement_sheet.exclusion_signals),
    }
    if self.requirement_sheet is not None
    else None
),
```

- [ ] **Step 4: Serialize requirement sheet into Liepin provider context**

In `src/seektalent/providers/liepin/runtime_lane.py`, add a helper:

```python
def _requirement_sheet_provider_context(request: RuntimeSourceLaneRequest) -> dict[str, str]:
    return {
        "liepin_requirement_sheet_json": json.dumps(
            request.requirement_sheet.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        )
    }
```

Import `json` at the top of the file.

In `_card_search_request()` and `_detail_provider_context()`, add the helper expansion as the first item in the existing `provider_context` literal:

```python
**_requirement_sheet_provider_context(request),
```

Remove active additions of these keys from the same provider context:

```python
"liepin_must_haves_json"
"liepin_nice_to_haves_json"
```

- [ ] **Step 5: Pass the requirement sheet from the Liepin adapter into lane requests**

In `src/seektalent/providers/liepin/runtime_lane.py`, add a parameter to `run_liepin_logical_query_bundle()`:

```python
requirement_sheet: RequirementSheet,
```

Import the type under `TYPE_CHECKING`.

When constructing `RuntimeSourceLaneRequest`, pass:

```python
requirement_sheet=requirement_sheet,
```

In `src/seektalent/runtime/orchestrator.py`, update `_execute_liepin_source_round_adapter()`:

```python
result = await run_liepin_logical_query_bundle(
    settings=self.settings,
    runtime_run_id=tracer.run_id,
    source_plan_id=liepin_plan.source_plan_id,
    job_title=str(getattr(input_truth, "job_title", "")),
    jd=str(getattr(input_truth, "jd", "")),
    notes=str(getattr(input_truth, "notes", "") or ""),
    requirement_sheet=request.requirement_sheet,
    logical_queries=request.logical_queries,
    source_query_intents=request.source_query_intents_by_source.get("liepin"),
    source_budget_policy=liepin_plan.source_budget_policy,
    liepin_context=liepin_context,
)
```

In `src/seektalent_ui/runtime_bridge.py`, pass the approved Workbench requirement sheet into both direct source-lane calls:

```python
requirement_sheet=approved_requirement_sheet,
```

Add the keyword to the existing card-mode `RuntimeSourceLaneRequest(...)` in `run_liepin_card_source_run()` and the existing detail-mode `RuntimeSourceLaneRequest(...)` in `run_liepin_detail_open_intent()`. Keep every other existing argument unchanged.

In `tests/test_workbench_runtime_owned_execution.py`, extend the Liepin detail-open runtime bridge test:

```python
assert detail_request.requirement_sheet.job_title == session.job_title
assert list(detail_request.requirement_sheet.must_have_capabilities) == ["Python"]
```

If a direct Liepin card-source-run bridge test exists in the file, add the same assertion there. If it does not exist, do not create a broad new Workbench fixture only for this slice.

- [ ] **Step 6: Run the Liepin propagation test**

Run:

```bash
pytest tests/test_liepin_runtime_source_lane.py::test_liepin_lane_passes_requirement_sheet_json_to_worker_context tests/test_workbench_runtime_owned_execution.py::test_leased_liepin_detail_open_intent_executes_detail_lane_and_persists_detail_evidence -q
```

Expected:

```text
2 passed
```

- [ ] **Step 7: Commit**

```bash
git add tests/test_liepin_runtime_source_lane.py tests/test_runtime_source_lanes.py tests/test_workbench_runtime_owned_execution.py src/seektalent/runtime/source_lanes.py src/seektalent/providers/liepin/runtime_lane.py src/seektalent/runtime/orchestrator.py src/seektalent_ui/runtime_bridge.py
git commit -m "feat: pass requirement sheet into liepin source lanes"
```

---

### Task 3: Replace Old PI Resume Payload Fields With RequirementSheet Payload

**Files:**
- Modify: `src/seektalent/providers/liepin/pi_worker_client.py`
- Modify: `src/seektalent/providers/liepin/pi_executor.py`
- Modify: `src/seektalent/providers/pi_agent/pi_external.py`
- Modify: `src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md`
- Modify: `tests/test_liepin_pi_worker_client.py`
- Modify: `tests/test_liepin_pi_executor.py`
- Modify: `tests/test_pi_external_agent.py`
- Test: `tests/test_liepin_pi_worker_client.py`
- Test: `tests/test_liepin_pi_executor.py`

- [ ] **Step 1: Add a failing worker-client test for requirement-sheet forwarding**

In `tests/test_liepin_pi_worker_client.py`, add `import json` and reuse the existing `FakeExecutor.captured_search_kwargs`.

Add this test:

```python
def test_pi_worker_client_forwards_requirement_sheet_and_not_old_fields() -> None:
    executor = FakeExecutor(
        result=LiepinPiResumeSearchResult(
            status=PiLiepinResultStatus.SUCCEEDED,
            stop_reason=PiLiepinStopReason.COMPLETED,
            safe_reason_code="completed",
            resume_search=_resume_response(),
        )
    )
    client = _client(executor)
    request = SearchRequest(
        query_terms=("LangGraph", "RAG"),
        query_role="primary",
        keyword_query="LangGraph RAG",
        adapter_notes=(),
        runtime_constraints=(),
        fetch_mode="detail",
        page_size=7,
        provider_context={
            "liepin_requirement_sheet_json": json.dumps(_requirement_sheet().model_dump(mode="json")),
            "liepin_max_cards": 30,
            "liepin_max_pages": 1,
        },
    )

    asyncio.run(client.search(request, round_no=1, trace_id="run-1:lane:1"))

    assert executor.captured_search_kwargs is not None
    call = executor.captured_search_kwargs
    assert call["requirement_sheet"]["job_title"] == "AI Agent Engineer"
    assert call["target_resumes"] == 7
    assert "must_haves" not in call
    assert "nice_to_haves" not in call
```

Add a missing-contract test so the worker does not silently fall back to old JD/title-only behavior:

```python
def test_pi_worker_client_requires_requirement_sheet_for_resume_search() -> None:
    executor = FakeExecutor(
        result=LiepinPiResumeSearchResult(
            status=PiLiepinResultStatus.SUCCEEDED,
            stop_reason=PiLiepinStopReason.COMPLETED,
            safe_reason_code="completed",
            resume_search=_resume_response(),
        )
    )
    client = _client(executor)
    request = SearchRequest(
        query_terms=("LangGraph", "RAG"),
        query_role="primary",
        keyword_query="LangGraph RAG",
        adapter_notes=(),
        runtime_constraints=(),
        fetch_mode="detail",
        page_size=7,
        provider_context={"liepin_max_cards": 30, "liepin_max_pages": 1},
    )

    with pytest.raises(LiepinWorkerModeError) as exc:
        asyncio.run(client.search(request, round_no=1, trace_id="run-1:lane:1"))
    assert exc.value.code == "requirement_sheet_missing"
```

- [ ] **Step 2: Add a failing executor test for the PI task payload**

In `tests/test_liepin_pi_executor.py`, add:

```python
def test_search_resumes_sends_requirement_sheet_payload_without_old_fields() -> None:
    transport = FakeRpcTransport(
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text=_valid_resumes_json(source_run_id="run-1", query="LangGraph RAG", returned=7),
            events=({"type": "tool_execution_start", "toolName": "opencli"},),
        )
    )
    executor = _resume_executor_with_transport(transport, source_run_id="run-1", returned=7)

    result = executor.search_resumes(
        source_run_id="run-1",
        keyword_query="LangGraph RAG",
        query_terms=("LangGraph", "RAG"),
        target_resumes=7,
        max_cards=30,
        max_pages=1,
        requirement_sheet=_requirement_sheet().model_dump(mode="json"),
        connection_id="connection-1",
        provider_account_hash="account-hash",
    )

    assert result.status == PiLiepinResultStatus.SUCCEEDED
    prompt = transport.prompts[0]
    assert '"schema_version": "seektalent.pi_liepin_resumes.v2"' in prompt
    assert '"requirement_sheet"' in prompt
    assert '"job_title": "AI Agent Engineer"' in prompt
    assert '"must_haves"' not in prompt
    assert '"nice_to_haves"' not in prompt
```

Add `_v2_resume_tool_payload()`, `_valid_resumes_json()`, `_resume_executor_with_transport()`, and `_requirement_sheet()` helpers in the same test file using the existing card-envelope helper style. `_valid_resumes_json()` should serialize `_v2_resume_tool_payload()` with `ensure_ascii=False`. Register all artifact refs used by the generated resume envelope.

- [ ] **Step 3: Run the new PI tests and confirm failures**

Run:

```bash
pytest tests/test_liepin_pi_worker_client.py::test_pi_worker_client_forwards_requirement_sheet_and_not_old_fields tests/test_liepin_pi_executor.py::test_search_resumes_sends_requirement_sheet_payload_without_old_fields -q
```

Expected:

```text
FAILED ... unexpected keyword argument 'requirement_sheet'
```

or:

```text
FAILED ... KeyError: 'requirement_sheet'
```

- [ ] **Step 4: Parse requirement sheet in the PI worker client**

In `src/seektalent/providers/liepin/pi_worker_client.py`, replace `_json_string_tuple()` usage for old fields with:

```python
def _json_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return {str(key): item for key, item in parsed.items()}
```

Update the executor call:

```python
requirement_sheet = _json_object(request.provider_context.get("liepin_requirement_sheet_json"))
if requirement_sheet is None:
    raise LiepinWorkerModeError(
        "Liepin PI resume search requires the canonical requirement sheet.",
        code="requirement_sheet_missing",
    )

result = await asyncio.to_thread(
    self._executor.search_resumes,
    source_run_id=trace_id,
    keyword_query=request.keyword_query or " ".join(request.query_terms),
    query_terms=tuple(request.query_terms),
    max_pages=_positive_int(request.provider_context.get("liepin_max_pages"), default=1),
    target_resumes=request.page_size,
    max_cards=_positive_int(request.provider_context.get("liepin_max_cards"), default=request.page_size),
    requirement_sheet=requirement_sheet,
    connection_id=connection_id,
    provider_account_hash=task_provider_account_hash,
    native_filters=_native_filters_from_request(request),
)
```

Remove `_json_string_tuple()` if no active caller remains in the file.

- [ ] **Step 5: Update `PiLiepinExecutor.search_resumes()` signature and task payload**

In `src/seektalent/providers/liepin/pi_executor.py`, change the method signature:

```python
def search_resumes(
    self,
    *,
    source_run_id: str,
    keyword_query: str,
    query_terms: Sequence[str],
    target_resumes: int,
    max_cards: int,
    max_pages: int,
    requirement_sheet: Mapping[str, object],
    connection_id: str | None = None,
    provider_account_hash: str | None = None,
    native_filters: Mapping[str, object] | None = None,
) -> LiepinPiResumeSearchResult:
```

Change the task payload:

```python
task: dict[str, object] = {
    "task": "liepin.search_resumes",
    "schema_version": "seektalent.pi_liepin_resumes.v2",
    "source_run_id": tool_source_run_id,
    "query": keyword_query,
    "query_terms": list(query_terms),
    "target_resumes": target_resumes,
    "max_cards": max_cards,
    "max_pages": max_pages,
    "requirement_sheet": dict(requirement_sheet),
    "mode": "detail_backed_resume_search",
    "rank_policy": "preserve_provider_rank_exclude_clear_mismatch_only",
}
```

Remove `"must_haves"` and `"nice_to_haves"` from this active task.

- [ ] **Step 6: Accept v2 resume envelopes**

In `_PiLiepinResumesEnvelope`, change schema version:

```python
schema_version: Literal["seektalent.pi_liepin_resumes.v2"]
```

In partial recovery, emit:

```python
"schema_version": "seektalent.pi_liepin_resumes.v2",
```

Keep the existing v1 card search schema unchanged.

- [ ] **Step 7: Add failing tests for the active Python PI task contract**

In `tests/test_pi_external_agent.py`, add:

```python
def test_liepin_resume_task_contract_uses_v2_requirement_sheet_not_old_fields(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text='{"schema_version":"seektalent.pi_liepin_resumes.v2","status":"blocked","stop_reason":"blocked_backend_unavailable","source_run_id":"run-1","query":"LangGraph RAG","cards_seen":0,"resumes_returned":0,"pages_visited":0,"detail_pages_opened":0,"action_trace_ref":"artifact://protected/pi-trace/run-1","protected_snapshot_refs":[],"resumes":[]}',
        ),
    )

    client.run_json_task_result(
        json.dumps(
            {
                "task": "liepin.search_resumes",
                "schema_version": "seektalent.pi_liepin_resumes.v2",
                "source_run_id": "run-1",
                "query": "LangGraph RAG",
                "query_terms": ["LangGraph", "RAG"],
                "target_resumes": 7,
                "max_cards": 30,
                "max_pages": 1,
                "requirement_sheet": {"job_title": "AI Agent Engineer"},
            },
            ensure_ascii=False,
        )
    )

    prompt = client.transport_for_test.prompts[0]
    assert "seektalent.pi_liepin_resumes.v2" in prompt
    assert "requirement_sheet" in prompt
    assert "must-have and nice-to-have" not in prompt
    assert "must_haves" not in prompt
    assert "nice_to_haves" not in prompt
```

Add:

```python
def test_liepin_repair_task_contract_is_recognized_by_python_prompt_builder(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        PiRpcTaskResult(
            status=PiRpcTaskStatus.SUCCEEDED,
            final_text='{"schema_version":"seektalent.pi_liepin_resumes.v2","status":"blocked","stop_reason":"blocked_backend_unavailable","source_run_id":"run-1","query":"LangGraph RAG","cards_seen":0,"resumes_returned":0,"pages_visited":0,"detail_pages_opened":0,"action_trace_ref":"artifact://protected/pi-trace/run-1","protected_snapshot_refs":[],"resumes":[]}',
        ),
    )

    client.run_json_task_result(
        json.dumps(
            {
                "task": "liepin.repair_resume_output",
                "schema_version": "seektalent.pi_liepin_resume_repair.v1",
                "source_run_id": "run-1",
                "query": "LangGraph RAG",
                "missing": {"resume_count": 2, "protected_snapshot_refs": [], "detail_payloads": []},
            },
            ensure_ascii=False,
        )
    )

    prompt = client.transport_for_test.prompts[0]
    assert "Continue from the current search context" in prompt
    assert "seektalent.pi_liepin_resumes.v2" in prompt
```

Add a local `_v2_resume_tool_payload()` helper in `tests/test_pi_external_agent.py`, then add a v2 tool-envelope extraction regression next to the existing v1 tool-result tests:

```python
def test_liepin_resume_task_accepts_v2_finalize_tool_envelope(tmp_path: Path) -> None:
    tool_payload = _v2_resume_tool_payload(source_run_id="run-1", query="LangGraph RAG", returned=7)
    client = PiRpcAgentClient(
        command=("pi", "--mode", "rpc", "--no-session", "--no-skills", "--skill", "skill.md"),
        skill_path=Path("skill.md"),
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=tmp_path / "artifacts" / "pi-agent",
        transport=FakeRpcTransport(
            PiRpcTaskResult(
                status=PiRpcTaskStatus.SUCCEEDED,
                final_text="not json",
                events=(
                    {
                        "type": "tool_execution_end",
                        "toolName": "seektalent_opencli_finalize_liepin_resumes",
                        "result": json.dumps(tool_payload, ensure_ascii=False),
                    },
                ),
            )
        ),
    )

    result = client.run_json_task_result(json.dumps({"task": "liepin.search_resumes"}, ensure_ascii=False))

    assert result.ok is True
    assert result.envelope == tool_payload
```

- [ ] **Step 8: Update the active Python PI task contract**

In `src/seektalent/providers/pi_agent/pi_external.py`, update `_task_contract_for_prompt()` for `liepin.search_resumes` so it says:

```python
if task_name == "liepin.search_resumes":
    return (
        "For task liepin.search_resumes, use the low-level SeekTalent OpenCLI tools as an agent-driven browser "
        "loop. The input task uses snake_case fields and includes requirement_sheet as the source of truth. "
        "Map sourceRunId=input source_run_id, query=input query, maxPages=input max_pages, maxCards=input max_cards, "
        "nativeFilters=input native_filters, and target_resumes controls how many complete detail resumes to capture. "
        "Use query_terms only as this lane's search query. Preserve Liepin provider rank and exclude only cards that "
        "are clearly mismatched against requirement_sheet. Return seektalent.pi_liepin_resumes.v2. "
        "Do not use or emit must_haves or nice_to_haves. "
        "Call seektalent_opencli_status, seektalent_opencli_open_liepin_tab, seektalent_opencli_state, "
        "seektalent_opencli_fill, seektalent_opencli_click, seektalent_opencli_wait_time, "
        "seektalent_opencli_open_liepin_detail, seektalent_opencli_capture_liepin_detail_resume, and "
        "seektalent_opencli_finalize_liepin_resumes. Do not call any tool outside the listed browser tools.\n"
    )
if task_name == "liepin.repair_resume_output":
    return (
        "For task liepin.repair_resume_output, continue from the current search context. Do not restart the full search. "
        "Use the missing object to open additional ranked detail pages or repair missing protected refs/detail payloads. "
        "Return the full seektalent.pi_liepin_resumes.v2 envelope as the final raw JSON object.\n"
    )
```

Update `_expected_liepin_tool_schema()`:

```python
if task_name in {"liepin.search_resumes", "liepin.repair_resume_output"}:
    return "seektalent.pi_liepin_resumes.v2"
```

Update `_liepin_tool_name_for_schema()` so v2 tool envelopes can be extracted from `seektalent_opencli_finalize_liepin_resumes` events:

```python
def _liepin_tool_name_for_schema(schema: str) -> str:
    return {
        "seektalent.pi_liepin_cards.v1": "seektalent_opencli_search_liepin_cards",
        "seektalent.pi_liepin_resumes.v1": "seektalent_opencli_finalize_liepin_resumes",
        "seektalent.pi_liepin_resumes.v2": "seektalent_opencli_finalize_liepin_resumes",
    }[schema]
```

- [ ] **Step 9: Update the PI skill instructions**

In `src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md`, update the active `liepin.search_resumes` section so it states:

```markdown
For `liepin.search_resumes`, read `requirement_sheet` as the source of truth.
Use `query_terms` only as the search query for this lane.
Preserve Liepin provider rank. Exclude only cards that are clearly mismatched against the requirement sheet.
Open detail pages until `target_resumes` full resumes are returned or a terminal blocked/partial state is reached.
Return `seektalent.pi_liepin_resumes.v2`.
Do not return `must_haves` or `nice_to_haves`; those are not active contract fields.
For `liepin.repair_resume_output`, continue from the current search context and return the repaired full v2 envelope. Do not restart the search.
```

- [ ] **Step 10: Run the PI payload and prompt-contract tests**

Run:

```bash
pytest tests/test_liepin_pi_worker_client.py::test_pi_worker_client_forwards_requirement_sheet_and_not_old_fields tests/test_liepin_pi_worker_client.py::test_pi_worker_client_requires_requirement_sheet_for_resume_search tests/test_liepin_pi_executor.py::test_search_resumes_sends_requirement_sheet_payload_without_old_fields tests/test_pi_external_agent.py::test_liepin_resume_task_contract_uses_v2_requirement_sheet_not_old_fields tests/test_pi_external_agent.py::test_liepin_repair_task_contract_is_recognized_by_python_prompt_builder tests/test_pi_external_agent.py::test_liepin_resume_task_accepts_v2_finalize_tool_envelope -q
```

Expected:

```text
6 passed
```

- [ ] **Step 11: Commit**

```bash
git add tests/test_liepin_pi_worker_client.py tests/test_liepin_pi_executor.py tests/test_pi_external_agent.py src/seektalent/providers/liepin/pi_worker_client.py src/seektalent/providers/liepin/pi_executor.py src/seektalent/providers/pi_agent/pi_external.py src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md
git commit -m "feat: use requirement sheet in liepin pi resume tasks"
```

---

### Task 4: Add Stateful PI Lane Repair And Terminal Cleanup

**Files:**
- Modify: `src/seektalent/providers/pi_agent/pi_external.py`
- Modify: `src/seektalent/providers/liepin/pi_executor.py`
- Create: `src/seektalent/providers/liepin/pi_resume_contract.py`
- Modify: `tests/test_pi_external_agent.py`
- Modify: `tests/test_liepin_pi_executor.py`
- Test: `tests/test_pi_external_agent.py`
- Test: `tests/test_liepin_pi_executor.py`

- [ ] **Step 1: Add failing tests for a lane-scoped PI JSON task session**

In `tests/test_pi_external_agent.py`, add a fake session transport:

```python
class SequentialSession:
    def __init__(self, *results: PiRpcTaskResult) -> None:
        self.results = list(results)
        self.prompts: list[str] = []
        self.closed = False

    def request(self, *, prompt: str) -> PiRpcTaskResult:
        self.prompts.append(prompt)
        if not self.results:
            raise AssertionError("unexpected extra Pi RPC session request")
        return self.results.pop(0)

    def close(self) -> None:
        self.closed = True


class SequentialSessionTransport(FakeRpcTransport):
    def __init__(self, *results: PiRpcTaskResult) -> None:
        super().__init__(results[0])
        self.session = SequentialSession(*results)

    def open_session(self, command: PiRpcCommand) -> SequentialSession:
        self.commands.append(command)
        return self.session
```

Add:

```python
def test_json_task_session_keeps_context_for_repair_and_cleans_up_once(monkeypatch, tmp_path: Path) -> None:
    cleanup_calls = []
    monkeypatch.setattr(
        pi_external,
        "_cleanup_liepin_opencli_detail_tabs_after_rpc",
        lambda *, prompt, env: cleanup_calls.append({"prompt": prompt, "env": dict(env)}),
    )
    first = PiRpcTaskResult(
        status=PiRpcTaskStatus.SUCCEEDED,
        final_text='{"schema_version":"seektalent.pi_liepin_resumes.v2","status":"succeeded","stop_reason":"completed","source_run_id":"run-1","query":"LangGraph RAG","cards_seen":7,"resumes_returned":5,"pages_visited":1,"detail_pages_opened":5,"action_trace_ref":"artifact://protected/pi-trace/run-1","protected_snapshot_refs":[],"resumes":[]}',
    )
    repaired = PiRpcTaskResult(
        status=PiRpcTaskStatus.SUCCEEDED,
        final_text='{"schema_version":"seektalent.pi_liepin_resumes.v2","status":"succeeded","stop_reason":"completed","source_run_id":"run-1","query":"LangGraph RAG","cards_seen":9,"resumes_returned":7,"pages_visited":1,"detail_pages_opened":7,"action_trace_ref":"artifact://protected/pi-trace/run-1","protected_snapshot_refs":[],"resumes":[]}',
    )
    transport = SequentialSessionTransport(first, repaired)
    skill_path = _skill(tmp_path)
    client = PiRpcAgentClient(
        command=build_pi_rpc_argv("pi --mode rpc --no-session", skill_path=skill_path),
        skill_path=skill_path,
        dokobot_tool_name="dokobot",
        timeout_seconds=120,
        artifact_root=tmp_path / "artifacts" / "pi-agent",
        env={
            "SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND": "opencli",
            "SEEKTALENT_LIEPIN_OPENCLI_SESSION": "session",
        },
        transport=transport,
    )
    search_prompt = json.dumps({"task": "liepin.search_resumes", "source_run_id": "run-1"})
    repair_prompt = json.dumps({"task": "liepin.repair_resume_output", "source_run_id": "run-1"})

    with client.open_json_task_session(cleanup_prompt=search_prompt) as session:
        first_result = session.run_json_task_result(search_prompt)
        repair_result = session.run_json_task_result(repair_prompt)
        assert first_result.ok
        assert repair_result.ok
        assert cleanup_calls == []

    assert transport.session.closed is True
    assert len(transport.session.prompts) == 2
    assert cleanup_calls == [{"prompt": search_prompt, "env": cleanup_calls[0]["env"]}]
```

This test must import `seektalent.providers.pi_agent.pi_external as pi_external`, `PiRpcCommand`, and `build_pi_rpc_argv`.

- [ ] **Step 2: Run the PI session test and confirm the missing API**

Run:

```bash
pytest tests/test_pi_external_agent.py::test_json_task_session_keeps_context_for_repair_and_cleans_up_once -q
```

Expected:

```text
FAILED ... AttributeError: 'PiRpcAgentClient' object has no attribute 'open_json_task_session'
```

- [ ] **Step 3: Add a lane-scoped session API to `pi_external.py`**

In `src/seektalent/providers/pi_agent/pi_external.py`, add protocols near `PiRpcTransport`:

```python
class PiRpcSession(Protocol):
    def request(self, *, prompt: str) -> PiRpcTaskResult: ...
    def close(self) -> None: ...


class PiRpcSessionTransport(PiRpcTransport, Protocol):
    def open_session(self, command: PiRpcCommand) -> PiRpcSession: ...
```

Add `PiJsonTaskSession`:

```python
class PiJsonTaskSession:
    def __init__(
        self,
        *,
        client: PiRpcAgentClient,
        session: PiRpcSession,
        command_env: Mapping[str, str],
        cleanup_prompt: str,
    ) -> None:
        self._client = client
        self._session = session
        self._command_env = dict(command_env)
        self._cleanup_prompt = cleanup_prompt
        self._closed = False

    def run_json_task_result(self, prompt: str) -> PiExternalTaskResult:
        if self._closed:
            raise RuntimeError("pi_json_task_session_closed")
        return self._client._run_json_task_result_in_session(self._session, prompt, command_env=self._command_env)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._session.close()
        finally:
            _cleanup_liepin_opencli_detail_tabs_after_rpc(prompt=self._cleanup_prompt, env=self._command_env)

    def __enter__(self) -> PiJsonTaskSession:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()
```

Add methods to `PiRpcAgentClient`:

```python
def open_json_task_session(self, *, cleanup_prompt: str) -> PiJsonTaskSession:
    command_env = _task_scoped_env(
        {**self._env, "SEEKTALENT_PI_ARTIFACT_ROOT": str(self._artifact_root)},
        cleanup_prompt,
    )
    command = PiRpcCommand(
        argv=self._command,
        timeout_seconds=self._timeout_seconds,
        artifact_root=self._artifact_root,
        resume_capture_idle_timeout_seconds=self._resume_capture_idle_timeout_seconds,
        env=command_env,
    )
    transport = self._transport
    if not hasattr(transport, "open_session"):
        raise RuntimeError("pi_rpc_transport_does_not_support_sessions")
    session = transport.open_session(command)  # type: ignore[attr-defined]
    return PiJsonTaskSession(client=self, session=session, command_env=command_env, cleanup_prompt=cleanup_prompt)
```

Refactor the existing `_run_json_task_result_once()` parsing after `rpc_result = ...` into a private helper:

```python
def _pi_external_task_result_from_rpc_result(
    *,
    rpc_result: PiRpcTaskResult,
    task_name: str | None,
) -> PiExternalTaskResult:
    observed_tool_names = _observed_tool_names(rpc_result.events)
    safe_reason_code = _safe_tool_reason_code(rpc_result.events)
    safe_events = _safe_rpc_events(rpc_result.events)
    expected_tool_schema = _expected_liepin_tool_schema(task_name)
    if expected_tool_schema is not None:
        tool_envelope = _strict_liepin_envelope_from_tool_events(
            rpc_result.events,
            expected_schema=expected_tool_schema,
        )
        if tool_envelope is not None:
            return PiExternalTaskResult(
                ok=True,
                envelope=tool_envelope,
                safe_reason_code=safe_reason_code,
                observed_tool_names=observed_tool_names,
                events=safe_events,
            )
    if rpc_result.status != PiRpcTaskStatus.SUCCEEDED:
        return PiExternalTaskResult(
            ok=False,
            error_code=_external_code_for_rpc_status(rpc_result.status),
            safe_reason_code=safe_reason_code,
            safe_message=_safe_external_message(rpc_result.safe_message),
            observed_tool_names=observed_tool_names,
            events=safe_events,
        )
    try:
        envelope = parse_strict_json_object(rpc_result.final_text)
    except ValueError:
        return PiExternalTaskResult(
            ok=False,
            error_code=PiExternalAgentErrorCode.MALFORMED_OUTPUT,
            safe_reason_code=safe_reason_code,
            safe_message="pi output did not contain exactly one valid JSON envelope",
            observed_tool_names=observed_tool_names,
            events=safe_events,
        )
    return PiExternalTaskResult(
        ok=True,
        envelope=envelope,
        safe_reason_code=safe_reason_code,
        observed_tool_names=observed_tool_names,
        events=safe_events,
    )
```

Both one-shot and session calls must use that helper so strict JSON parsing, safe reason handling, observed tools, and expected schema handling stay identical.

Add:

```python
def _run_json_task_result_in_session(
    self,
    session: PiRpcSession,
    prompt: str,
    *,
    command_env: Mapping[str, str],
) -> PiExternalTaskResult:
    del command_env
    task_name = _task_name_from_prompt(prompt)
    rpc_result = session.request(prompt=self._build_prompt(prompt, strict_retry=False))
    return _pi_external_task_result_from_rpc_result(rpc_result=rpc_result, task_name=task_name)
```

Keep existing `run_json_task_result()` one-shot behavior for probes and card search. Do not call cleanup between prompts in the session path; `PiJsonTaskSession.close()` owns cleanup.

- [ ] **Step 4: Add the subprocess session implementation**

In `src/seektalent/providers/pi_agent/pi_external.py`, add `_SubprocessPiRpcSession` and `SubprocessPiRpcTransport.open_session()`.

The session implementation should reuse the existing request loop logic, but it must not stop the process after the first successful JSON envelope. The shape:

```python
class _SubprocessPiRpcSession:
    def __init__(self, *, command: PiRpcCommand, process_factory) -> None:
        self._command = command
        self._process_factory = process_factory
        self._process = _start_pi_rpc_process(command, process_factory)
        self._stdout_lines: queue.Queue[str | None] = queue.Queue()
        self._stderr_chunks: list[str] = []
        self._request_seq = 0
        threading.Thread(target=_drain_stdout, args=(self._process.stdout, self._stdout_lines), daemon=True).start()
        threading.Thread(target=_drain_stderr, args=(self._process.stderr, self._stderr_chunks), daemon=True).start()

    def request(self, *, prompt: str) -> PiRpcTaskResult:
        self._request_seq += 1
        request_id = f"seektalent-{self._request_seq}"
        self._process.stdin.write(json.dumps({"id": request_id, "type": "prompt", "message": prompt}) + "\n")
        self._process.stdin.flush()
        return _read_pi_rpc_task_result(
            process=self._process,
            stdout_lines=self._stdout_lines,
            stderr_chunks=self._stderr_chunks,
            timeout_seconds=self._command.timeout_seconds,
            request_id=request_id,
            stop_process_on_result=False,
            resume_capture_idle_timeout_seconds=self._command.resume_capture_idle_timeout_seconds,
        )

    def close(self) -> None:
        _stop_process(self._process)
```

Extracting `_start_pi_rpc_process()` and `_read_pi_rpc_task_result()` from the current `SubprocessPiRpcTransport.request()` is expected in this step. The one-shot `request()` should call the same helper with `stop_process_on_result=True` so behavior stays identical.

When `stop_process_on_result=False`, do not return immediately when `_liepin_tool_envelope_from_event(event)` finds the final tool envelope. Store that envelope, keep consuming the current prompt stream until the matching `agent_end`, then return the stored tool envelope as `final_text`. Otherwise the next repair prompt can read the previous prompt's leftover `agent_end` and lose the stateful-session guarantee. The one-shot path can still stop immediately on the tool envelope because it terminates the process.

- [ ] **Step 5: Add a subprocess session regression test**

In `tests/test_pi_external_agent.py`, add:

```python
def test_subprocess_session_accepts_two_prompts_before_close(tmp_path: Path) -> None:
    first = json.dumps(
        {
            "type": "agent_end",
            "messages": [{"role": "assistant", "content": '{"first":true}'}],
        }
    )
    second = json.dumps(
        {
            "type": "agent_end",
            "messages": [{"role": "assistant", "content": '{"second":true}'}],
        }
    )
    process = _LongRunningRpcProcess(
        (
            json.dumps({"type": "response", "command": "prompt", "success": True}) + "\n",
            first + "\n",
            json.dumps({"type": "response", "command": "prompt", "success": True}) + "\n",
            second + "\n",
        )
    )
    transport = SubprocessPiRpcTransport(process_factory=lambda *args, **kwargs: process)
    session = transport.open_session(
        PiRpcCommand(argv=("pi", "--mode", "rpc"), timeout_seconds=30, artifact_root=tmp_path)
    )

    first_result = session.request(prompt="first prompt")
    second_result = session.request(prompt="second prompt")

    assert first_result.status == PiRpcTaskStatus.SUCCEEDED
    assert json.loads(first_result.final_text) == {"first": True}
    assert second_result.status == PiRpcTaskStatus.SUCCEEDED
    assert json.loads(second_result.final_text) == {"second": True}
    assert process.returncode is None

    session.close()
    assert process.returncode in {-15, 0}
```

Add a second subprocess session regression for the tool-envelope drain case:

```python
def test_subprocess_session_drains_tool_envelope_before_next_prompt(tmp_path: Path) -> None:
    resume_payload = _v2_resume_tool_payload(source_run_id="run-1", query="LangGraph RAG", returned=7)
    tool_event = json.dumps(
        {
            "type": "tool_execution_result",
            "toolName": "seektalent_opencli_finalize_liepin_resumes",
            "result": json.dumps(resume_payload, ensure_ascii=False),
        },
        ensure_ascii=False,
    )
    first_agent_end = json.dumps({"type": "agent_end", "messages": [{"role": "assistant", "content": "ignored because tool envelope wins"}]})
    second_agent_end = json.dumps({"type": "agent_end", "messages": [{"role": "assistant", "content": '{"second":true}'}]})
    process = _LongRunningRpcProcess(
        (
            json.dumps({"type": "response", "command": "prompt", "success": True}) + "\n",
            tool_event + "\n",
            first_agent_end + "\n",
            json.dumps({"type": "response", "command": "prompt", "success": True}) + "\n",
            second_agent_end + "\n",
        )
    )
    transport = SubprocessPiRpcTransport(process_factory=lambda *args, **kwargs: process)
    session = transport.open_session(
        PiRpcCommand(argv=("pi", "--mode", "rpc"), timeout_seconds=30, artifact_root=tmp_path)
    )

    first_result = session.request(prompt="first prompt")
    second_result = session.request(prompt="second prompt")

    assert json.loads(first_result.final_text) == resume_payload
    assert json.loads(second_result.final_text) == {"second": True}

    session.close()
```

- [ ] **Step 6: Run the PI session tests**

Run:

```bash
pytest tests/test_pi_external_agent.py::test_json_task_session_keeps_context_for_repair_and_cleans_up_once tests/test_pi_external_agent.py::test_subprocess_session_accepts_two_prompts_before_close tests/test_pi_external_agent.py::test_subprocess_session_drains_tool_envelope_before_next_prompt -q
```

Expected:

```text
3 passed
```

- [ ] **Step 7: Add failing executor tests for stateful semantic repair**

In `tests/test_liepin_pi_executor.py`, add:

```python
def test_search_resumes_repairs_underfilled_output_inside_one_pi_session() -> None:
    first = _valid_resumes_json(source_run_id="run-1", query="LangGraph RAG", returned=5, target=7)
    repaired = _valid_resumes_json(source_run_id="run-1", query="LangGraph RAG", returned=7, target=7)
    from tests.test_pi_external_agent import SequentialSessionTransport

    transport = SequentialSessionTransport(
        PiRpcTaskResult(status=PiRpcTaskStatus.SUCCEEDED, final_text=first, events=({"type": "tool_execution_start", "toolName": "opencli"},)),
        PiRpcTaskResult(status=PiRpcTaskStatus.SUCCEEDED, final_text=repaired, events=({"type": "tool_execution_start", "toolName": "opencli"},)),
    )
    executor = _resume_executor_with_transport(transport, source_run_id="run-1", returned=7)

    result = executor.search_resumes(
        source_run_id="run-1",
        keyword_query="LangGraph RAG",
        query_terms=("LangGraph", "RAG"),
        target_resumes=7,
        max_cards=30,
        max_pages=1,
        requirement_sheet=_requirement_sheet().model_dump(mode="json"),
    )

    assert result.status == PiLiepinResultStatus.SUCCEEDED
    assert result.resume_search is not None
    assert len(result.resume_search.resumes) == 7
    assert len(transport.session.prompts) == 2
    assert '"task": "liepin.search_resumes"' in transport.session.prompts[0]
    assert '"task": "liepin.repair_resume_output"' in transport.session.prompts[1]
    assert '"resume_count": 2' in transport.session.prompts[1]
    assert transport.session.closed is True
```

Add a second contract-invalid but parseable envelope test. `_valid_resumes_json()` should build stable ids such as `liepin-detail-1`, `liepin-detail-2`, and should not emit a `cleanup` field:

```python
def test_search_resumes_repairs_missing_detail_contract_inside_one_pi_session() -> None:
    first_payload = json.loads(_valid_resumes_json(source_run_id="run-1", query="LangGraph RAG", returned=7, target=7))
    first_payload["resumes"][1].pop("detail_payload")
    first_payload["resumes"][2].pop("protected_snapshot_ref")
    repaired = _valid_resumes_json(source_run_id="run-1", query="LangGraph RAG", returned=7, target=7)
    from tests.test_pi_external_agent import SequentialSessionTransport

    transport = SequentialSessionTransport(
        PiRpcTaskResult(status=PiRpcTaskStatus.SUCCEEDED, final_text=json.dumps(first_payload), events=({"type": "tool_execution_start", "toolName": "opencli"},)),
        PiRpcTaskResult(status=PiRpcTaskStatus.SUCCEEDED, final_text=repaired, events=({"type": "tool_execution_start", "toolName": "opencli"},)),
    )
    executor = _resume_executor_with_transport(transport, source_run_id="run-1", returned=7)

    result = executor.search_resumes(
        source_run_id="run-1",
        keyword_query="LangGraph RAG",
        query_terms=("LangGraph", "RAG"),
        target_resumes=7,
        max_cards=30,
        max_pages=1,
        requirement_sheet=_requirement_sheet().model_dump(mode="json"),
    )

    assert result.status == PiLiepinResultStatus.SUCCEEDED
    assert len(transport.session.prompts) == 2
    assert '"task": "liepin.repair_resume_output"' in transport.session.prompts[1]
    assert '"detail_payloads": ["liepin-detail-2"]' in transport.session.prompts[1]
    assert '"protected_snapshot_refs": ["liepin-detail-3"]' in transport.session.prompts[1]
    assert transport.session.closed is True
```

Import `SequentialSessionTransport` from `tests.test_pi_external_agent` after creating it in Step 1. Cleanup is asserted through `PiJsonTaskSession.close()`.

- [ ] **Step 8: Run the repair tests and confirm the executor still uses the old one-shot path**

Run:

```bash
pytest tests/test_liepin_pi_executor.py::test_search_resumes_repairs_underfilled_output_inside_one_pi_session tests/test_liepin_pi_executor.py::test_search_resumes_repairs_missing_detail_contract_inside_one_pi_session -q
```

Expected:

```text
FAILED ... assert <PiLiepinResultStatus.FAILED: 'failed'> == <PiLiepinResultStatus.SUCCEEDED: 'succeeded'>
```

- [ ] **Step 9: Create the resume contract module**

Create `src/seektalent/providers/liepin/pi_resume_contract.py`:

```python
from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PiResumeValidationGap(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    resume_count: int = Field(ge=0)
    protected_snapshot_refs: list[str] = Field(default_factory=list)
    detail_payloads: list[str] = Field(default_factory=list)

    @property
    def needs_repair(self) -> bool:
        return bool(self.resume_count or self.protected_snapshot_refs or self.detail_payloads)


class PiResumeRepairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task: Literal["liepin.repair_resume_output"] = "liepin.repair_resume_output"
    schema_version: Literal["seektalent.pi_liepin_resume_repair.v1"] = "seektalent.pi_liepin_resume_repair.v1"
    source_run_id: str
    query: str
    missing: PiResumeValidationGap
    instruction: str = (
        "Continue from the current search context. Do not restart the full search. "
        "Open additional ranked cards or repair missing detail payloads until the lane contract is satisfied."
    )


def _resume_label(resume: object, *, index: int) -> str:
    if isinstance(resume, Mapping):
        candidate_id = resume.get("candidate_resume_id")
        if isinstance(candidate_id, str) and candidate_id:
            return candidate_id
    return f"resume index {index}"


def validation_gap_for_resume_payload(payload: Mapping[str, object], *, target: int) -> PiResumeValidationGap:
    raw_resumes = payload.get("resumes")
    resumes = raw_resumes if isinstance(raw_resumes, list) else []
    returned = payload.get("resumes_returned")
    returned_count = returned if isinstance(returned, int) else len(resumes)
    observed_count = min(returned_count, len(resumes))
    missing_count = max(0, target - observed_count)
    protected_snapshot_refs: list[str] = []
    detail_payloads: list[str] = []

    for index, resume in enumerate(resumes, start=1):
        label = _resume_label(resume, index=index)
        if not isinstance(resume, Mapping):
            detail_payloads.append(label)
            protected_snapshot_refs.append(label)
            continue
        if not isinstance(resume.get("protected_snapshot_ref"), str):
            protected_snapshot_refs.append(label)
        detail_payload = resume.get("detail_payload")
        if not isinstance(detail_payload, Mapping) or not detail_payload:
            detail_payloads.append(label)

    return PiResumeValidationGap(
        resume_count=missing_count,
        protected_snapshot_refs=protected_snapshot_refs,
        detail_payloads=detail_payloads,
    )
```

- [ ] **Step 10: Import the repair contract in the executor**

In `src/seektalent/providers/liepin/pi_executor.py`, import:

```python
from seektalent.providers.liepin.pi_resume_contract import (
    PiResumeRepairRequest,
    validation_gap_for_resume_payload,
)
```

- [ ] **Step 11: Use the lane-scoped session in `search_resumes()`**

In `PiLiepinExecutor.search_resumes()`, replace the one-shot call:

```python
task_result = self._client.run_json_task_result(json.dumps(task, ensure_ascii=False))
```

with a lane-scoped session:

```python
task_json = json.dumps(task, ensure_ascii=False)
with self._client.open_json_task_session(cleanup_prompt=task_json) as pi_session:
    task_result = pi_session.run_json_task_result(task_json)
    if not task_result.ok or task_result.envelope is None:
        recovered = self._recover_partial_resume_search_from_collected_artifacts(
            task_result=task_result,
            source_run_id=source_run_id,
            tool_source_run_id=tool_source_run_id,
            keyword_query=keyword_query,
            target_resumes=target_resumes,
            max_pages=max_pages,
            max_cards=max_cards,
        )
        if recovered is not None:
            return recovered
        return _resume_result_from_external_error(task_result)
    raw_envelope = task_result.envelope
    gap = validation_gap_for_resume_payload(raw_envelope, target=target_resumes)
    if raw_envelope.get("status") == "succeeded" and gap.needs_repair:
        repair = PiResumeRepairRequest(
            source_run_id=tool_source_run_id,
            query=keyword_query,
            missing=gap,
        )
        repair_result = pi_session.run_json_task_result(repair.model_dump_json())
        if not repair_result.ok or repair_result.envelope is None:
            return _resume_result_from_external_error(repair_result)
        raw_envelope = repair_result.envelope
    envelope = _PiLiepinResumesEnvelope.model_validate(raw_envelope)
```

Then run `_validate_resume_envelope(...)` and `_map_resume_search(...)` on the repaired envelope. Keep the existing final validation requiring exact target count. Do not repair identity/control-plane failures such as `source_run_id`, `query`, schema mismatch, budget overflow, or unsafe public payload; those should continue to fail fast after strict validation.

- [ ] **Step 12: Run the stateful repair and cleanup tests**

Run:

```bash
pytest tests/test_pi_external_agent.py::test_json_task_session_keeps_context_for_repair_and_cleans_up_once tests/test_pi_external_agent.py::test_subprocess_session_accepts_two_prompts_before_close tests/test_pi_external_agent.py::test_subprocess_session_drains_tool_envelope_before_next_prompt tests/test_liepin_pi_executor.py::test_search_resumes_repairs_underfilled_output_inside_one_pi_session tests/test_liepin_pi_executor.py::test_search_resumes_repairs_missing_detail_contract_inside_one_pi_session -q
```

Expected:

```text
5 passed
```

- [ ] **Step 13: Commit**

```bash
git add tests/test_pi_external_agent.py tests/test_liepin_pi_executor.py src/seektalent/providers/pi_agent/pi_external.py src/seektalent/providers/liepin/pi_executor.py src/seektalent/providers/liepin/pi_resume_contract.py
git commit -m "feat: repair liepin pi resume output"
```

---

### Task 5: Run Liepin Exploit And Explore Child Agents In Parallel

**Files:**
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Modify: `tests/test_liepin_runtime_source_lane.py`
- Test: `tests/test_liepin_runtime_source_lane.py`

- [ ] **Step 1: Replace the old shared-session serialization test**

In `tests/test_liepin_runtime_source_lane.py`, replace `test_liepin_logical_query_bundle_serializes_shared_opencli_detail_searches()` with:

```python
def test_liepin_logical_query_bundle_runs_independent_child_agents_in_parallel() -> None:
```

This replacement is intentional. The old test enforced one shared OpenCLI/browser session. The runtime contract now requires one PI child-agent lifecycle per lane, so `exploit` and `generic_explore` can run concurrently without sharing mutable browser resources.

- [ ] **Step 2: Add the concurrent worker fake**

In `tests/test_liepin_runtime_source_lane.py`, add:

```python
class ParallelDetailWorker(FakeWorker):
    def __init__(self) -> None:
        super().__init__()
        self.started = []
        self.both_started = asyncio.Event()
        self.release = asyncio.Event()

    async def search(self, request, *, round_no: int, trace_id: str):
        del round_no, trace_id
        self.started.append({"page_size": request.page_size, "trace_id": trace_id})
        if len(self.started) == 2:
            self.both_started.set()
        await self.release.wait()
        candidates = []
        snapshots = []
        for index in range(request.page_size):
            resume_id = f"liepin-{trace_id}-{index}"
            raw_payload = {
                "provider_candidate_key_hash": f"hash-{trace_id}-{index}",
                "provider_snapshot_ref": f"artifact://protected/pi-detail/{trace_id}/{index}",
                "safe_summary_ref": f"artifact://public-summary/pi-detail/{trace_id}/{index}",
                "fullText": f"{request.keyword_query} detail resume {index}",
            }
            candidates.append(
                ResumeCandidate(
                    resume_id=resume_id,
                    source_resume_id=None,
                    snapshot_sha256=sha256_json(raw_payload),
                    dedup_key=resume_id,
                    search_text=f"{request.keyword_query} detail resume {index}",
                    raw=raw_payload,
                )
            )
            snapshots.append(
                ProviderSnapshot(
                    provider_name="liepin",
                    payload_kind="detail",
                    raw_payload=raw_payload,
                    normalized_text=f"{request.keyword_query} detail resume {index}",
                    provider_subject_id=str(raw_payload["provider_candidate_key_hash"]),
                    provider_listing_id=None,
                    synthetic_candidate_fingerprint=resume_id,
                    identity_confidence="provider_subject_id",
                    extraction_source="test",
                    extractor_version="pi-agent-liepin-detail-v1",
                    pii_classification="no_direct_contact",
                    retention_policy="provider_snapshot_30d",
                    access_scope="local_run_only",
                    redaction_state="redacted",
                    score_evidence_source="detail_enriched",
                )
            )
        return SearchResult(
            candidates=candidates,
            provider_snapshots=snapshots,
            raw_candidate_count=len(candidates),
            exhausted=True,
        )
```

- [ ] **Step 3: Add the replacement concurrency test body**

Use this body for `test_liepin_logical_query_bundle_runs_independent_child_agents_in_parallel()`:

```python
async def _run_parallel_liepin_bundle(worker: ParallelDetailWorker):
    task = asyncio.create_task(
        run_liepin_logical_query_bundle(
            settings=make_settings(),
            runtime_run_id="run-1",
            source_plan_id="run-1:source:1:liepin",
            job_title="AI Agent Engineer",
            jd="Build LangGraph and RAG systems.",
            notes="Prefer evaluation.",
            requirement_sheet=_requirement_sheet(),
            logical_queries=(
                LogicalQueryDispatch(
                    round_no=1,
                    query_role="exploit",
                    lane_type="exploit",
                    query_terms=("LangGraph", "RAG"),
                    keyword_query="LangGraph RAG",
                    query_instance_id="q-exploit",
                    query_fingerprint="fingerprint-exploit",
                    requested_count=7,
                    source_plan_version="7",
                ),
                LogicalQueryDispatch(
                    round_no=1,
                    query_role="explore",
                    lane_type="generic_explore",
                    query_terms=("agent workflow", "evaluation"),
                    keyword_query="agent workflow evaluation",
                    query_instance_id="q-explore",
                    query_fingerprint="fingerprint-explore",
                    requested_count=3,
                    source_plan_version="7",
                ),
            ),
            source_budget_policy=RuntimeSourceBudgetPolicy.defaults(),
            liepin_context={"backend_mode": "pi_agent"},
            worker_client=worker,
        )
    )

    await asyncio.wait_for(worker.both_started.wait(), timeout=1)
    assert sorted(item["page_size"] for item in worker.started) == [3, 7]
    assert not task.done()
    worker.release.set()
    result = await asyncio.wait_for(task, timeout=1)
    assert result.status == "completed"
    assert len(result.candidate_store_updates) == 10


def test_liepin_logical_query_bundle_runs_independent_child_agents_in_parallel() -> None:
    asyncio.run(_run_parallel_liepin_bundle(ParallelDetailWorker()))
```

- [ ] **Step 4: Run the concurrency test and confirm it times out**

Run:

```bash
pytest tests/test_liepin_runtime_source_lane.py::test_liepin_logical_query_bundle_runs_independent_child_agents_in_parallel -q
```

Expected:

```text
FAILED ... TimeoutError
```

Sequential execution blocks on the first lane and never starts the second lane.

- [ ] **Step 5: Replace sequential lane execution with `TaskGroup`**

In `src/seektalent/providers/liepin/runtime_lane.py`, replace:

```python
merged_result: RuntimeSourceLaneResult | None = None
for index, logical_query in enumerate(logical_queries, start=1):
    logical_result = await run_logical_query(index, logical_query)
    merged_result = (
        logical_result
        if merged_result is None
        else merge_liepin_card_lane_results(merged_result, logical_result)
    )
```

with:

```python
tasks: dict[int, asyncio.Task[RuntimeSourceLaneResult]] = {}
async with asyncio.TaskGroup() as task_group:
    for index, logical_query in enumerate(logical_queries, start=1):
        tasks[index] = task_group.create_task(run_logical_query(index, logical_query))

merged_result: RuntimeSourceLaneResult | None = None
for index in sorted(tasks):
    logical_result = tasks[index].result()
    merged_result = (
        logical_result
        if merged_result is None
        else merge_liepin_card_lane_results(merged_result, logical_result)
    )
```

Add `import asyncio` at the top of the file.

- [ ] **Step 6: Preserve lane target counts**

In the `RuntimeSourceLaneRequest(...)` construction inside `run_logical_query()`, make sure the request uses:

```python
logical_requested_count=logical_query.requested_count,
logical_provider_scan_limit=logical_provider_scan_limit,
```

In `_card_search_request()`, make sure `page_size` for detail-backed runtime search is the lane target:

```python
page_size=int(request.logical_requested_count or 10)
```

and `liepin_max_cards` remains the scan limit:

```python
"liepin_max_cards": int(request.logical_provider_scan_limit or budget.liepin_max_cards),
```

- [ ] **Step 7: Run the concurrency test**

Run:

```bash
pytest tests/test_liepin_runtime_source_lane.py::test_liepin_logical_query_bundle_runs_independent_child_agents_in_parallel -q
```

Expected:

```text
1 passed
```

- [ ] **Step 8: Commit**

```bash
git add tests/test_liepin_runtime_source_lane.py src/seektalent/providers/liepin/runtime_lane.py
git commit -m "feat: run liepin logical lanes concurrently"
```

---

### Task 6: Normalize Liepin Detail-Backed Candidates In The Source Lane

**Files:**
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Modify: `tests/test_liepin_runtime_source_lane.py`
- Test: `tests/test_liepin_runtime_source_lane.py`

- [ ] **Step 1: Add a failing normalization test**

In `tests/test_liepin_runtime_source_lane.py`, add:

```python
class SingleDetailWorker(FakeWorker):
    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult:
        self.search_calls.append(
            {
                "request": request,
                "provider_context": request.provider_context,
                "page_size": request.page_size,
                "round_no": round_no,
                "trace_id": trace_id,
                "provider_account_hash": provider_account_hash,
            }
        )
        raw_payload = {
            "provider_candidate_key_hash": "hash-detail-1",
            "provider_snapshot_ref": "artifact://protected/pi-detail/run-1/1",
            "safe_summary_ref": "artifact://public-summary/pi-detail/run-1/1",
            "score_evidence_source": "detail_enriched",
            "fullText": "LangGraph RAG detail resume",
        }
        candidate = ResumeCandidate(
            resume_id="liepin-detail-1",
            source_resume_id=None,
            snapshot_sha256=sha256_json(raw_payload),
            dedup_key="liepin-detail-1",
            search_text="LangGraph RAG detail resume",
            raw=raw_payload,
        )
        snapshot = ProviderSnapshot(
            provider_name="liepin",
            payload_kind="detail",
            raw_payload=raw_payload,
            normalized_text="LangGraph RAG detail resume",
            provider_subject_id="hash-detail-1",
            provider_listing_id=None,
            synthetic_candidate_fingerprint="liepin-detail-1",
            identity_confidence="provider_subject_id",
            extraction_source="test",
            extractor_version="pi-agent-liepin-detail-v1",
            pii_classification="no_direct_contact",
            retention_policy="provider_snapshot_30d",
            access_scope="local_run_only",
            redaction_state="redacted",
            score_evidence_source="detail_enriched",
        )
        return SearchResult(candidates=[candidate], provider_snapshots=[snapshot], raw_candidate_count=1)


def test_liepin_detail_backed_lane_populates_normalized_updates() -> None:
    worker = SingleDetailWorker()
    result = asyncio.run(run_liepin_source_lane(
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
    ))

    assert result.status == "completed"
    assert result.candidate_store_updates
    assert set(result.normalized_store_updates) == set(result.candidate_store_updates)
    first = next(iter(result.normalized_store_updates.values()))
    assert "LangGraph" in first.search_text or "RAG" in first.search_text
```

- [ ] **Step 2: Run the normalization test and confirm failure**

Run:

```bash
pytest tests/test_liepin_runtime_source_lane.py::test_liepin_detail_backed_lane_populates_normalized_updates -q
```

Expected:

```text
FAILED ... assert set() == ...
```

- [ ] **Step 3: Populate normalized updates for detail-backed Liepin results**

In `src/seektalent/providers/liepin/runtime_lane.py`, import:

```python
from seektalent.normalization import normalize_resume
```

In `_card_lane_result_from_search_result()`, after `candidates = ...`, add:

```python
normalized_updates = (
    {candidate.resume_id: normalize_resume(candidate) for candidate in candidates}
    if detail_backed
    else {}
)
```

Pass it into `RuntimeSourceLaneResult`:

```python
normalized_store_updates=normalized_updates,
```

In `_run_detail_lane()`, add the same normalized update construction and pass it into the result.

- [ ] **Step 4: Run the normalization test**

Run:

```bash
pytest tests/test_liepin_runtime_source_lane.py::test_liepin_detail_backed_lane_populates_normalized_updates -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_liepin_runtime_source_lane.py src/seektalent/providers/liepin/runtime_lane.py
git commit -m "feat: normalize liepin detail-backed candidates"
```

---

### Task 7: Clean Active Legacy Liepin Runtime Paths

**Files:**
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Modify: `src/seektalent/providers/liepin/pi_worker_client.py`
- Modify: `src/seektalent/providers/liepin/pi_executor.py`
- Modify: `tests/test_runtime_source_adapter_boundary.py`
- Test: `tests/test_runtime_source_adapter_boundary.py`

- [ ] **Step 1: Add boundary tests that old active PI fields are gone**

In `tests/test_runtime_source_adapter_boundary.py`, add:

```python
def test_liepin_active_pi_resume_path_does_not_use_old_requirement_fields() -> None:
    files = [
        Path("src/seektalent/providers/liepin/pi_worker_client.py"),
        Path("src/seektalent/providers/liepin/pi_executor.py"),
    ]
    active_text = "\n".join(path.read_text() for path in files)

    assert "liepin_must_haves_json" not in active_text
    assert "liepin_nice_to_haves_json" not in active_text
    assert '"must_haves"' not in active_text
    assert '"nice_to_haves"' not in active_text
```

Add:

```python
def test_liepin_runtime_full_source_path_is_detail_backed_not_recommendation_first() -> None:
    text = Path("src/seektalent/providers/liepin/runtime_lane.py").read_text()

    assert "detail_backed_resume_search" in text
    assert "detail_recommended" in text
    assert "_detail_recommendations_for_candidates(" not in text.split("def _card_lane_result_from_search_result", 1)[1].split("def _run_detail_lane", 1)[0]
```

This keeps historical event support visible while preventing the normal full-source lane from using recommendation-first detail approval.

- [ ] **Step 2: Run boundary tests and confirm failures on current legacy fields**

Run:

```bash
pytest tests/test_runtime_source_adapter_boundary.py::test_liepin_active_pi_resume_path_does_not_use_old_requirement_fields tests/test_runtime_source_adapter_boundary.py::test_liepin_runtime_full_source_path_is_detail_backed_not_recommendation_first -q
```

Expected:

```text
FAILED ... assert 'liepin_must_haves_json' not in ...
```

and, if the recommendation path still sits in the active detail-backed block:

```text
FAILED ... _detail_recommendations_for_candidates(
```

- [ ] **Step 3: Remove old active field helpers**

In `src/seektalent/providers/liepin/pi_worker_client.py`, delete `_json_string_tuple()` when no active caller remains. Keep `_native_filters_from_request()` and `_json_object()` because native filters and requirement-sheet payloads are still active.

In `src/seektalent/providers/liepin/pi_executor.py`, remove parameters and payload keys:

```python
must_haves
nice_to_haves
```

The only resume-search requirement field should be:

```python
requirement_sheet
```

- [ ] **Step 4: Make recommendation-first output inactive for detail-backed runtime source execution**

In `_card_lane_result_from_search_result()`, keep detail recommendations only for a true card-only lane:

```python
detail_recommendations = (
    ()
    if detail_backed
    else _detail_recommendations_for_candidates(
        source_plan_id=source_plan_id,
        candidates=candidates,
        evidence_updates=evidence_updates,
        query_terms=query_terms,
        job_title=request.job_title,
        max_recommendations=budget.liepin_max_detail_recommendations,
        budget_policy_version=budget.policy_version,
    )
)
```

If this shape already exists after Task 6, leave it unchanged and rely on the boundary test.

Do not delete `RuntimeDetailRecommendation` or approval lease models in this slice because other tested Workbench/detail-open paths may still import them.

- [ ] **Step 5: Run boundary tests**

Run:

```bash
pytest tests/test_runtime_source_adapter_boundary.py::test_liepin_active_pi_resume_path_does_not_use_old_requirement_fields tests/test_runtime_source_adapter_boundary.py::test_liepin_runtime_full_source_path_is_detail_backed_not_recommendation_first -q
```

Expected:

```text
2 passed
```

- [ ] **Step 6: Run focused Liepin regression tests**

Run:

```bash
pytest tests/test_liepin_pi_worker_client.py tests/test_liepin_pi_executor.py tests/test_liepin_runtime_source_lane.py -q
```

Expected:

```text
passed
```

- [ ] **Step 7: Commit**

```bash
git add tests/test_runtime_source_adapter_boundary.py src/seektalent/providers/liepin/runtime_lane.py src/seektalent/providers/liepin/pi_worker_client.py src/seektalent/providers/liepin/pi_executor.py
git commit -m "refactor: remove legacy liepin pi resume fields"
```

---

### Task 8: Verify End-To-End Source Runtime Slice

**Files:**
- Read: `src/seektalent/runtime/orchestrator.py`
- Read: `src/seektalent/runtime/source_round_dispatch.py`
- Read: `src/seektalent/providers/liepin/runtime_lane.py`
- Read: `src/seektalent/providers/liepin/pi_worker_client.py`
- Read: `src/seektalent/providers/liepin/pi_executor.py`
- Test: focused runtime and Liepin tests

- [ ] **Step 1: Run source dispatch tests**

Run:

```bash
pytest tests/test_runtime_multi_source_round_dispatch.py tests/test_runtime_source_adapter_boundary.py tests/test_workbench_runtime_owned_execution.py -q
```

Expected:

```text
passed
```

- [ ] **Step 2: Run Liepin adapter tests**

Run:

```bash
pytest tests/test_liepin_runtime_source_lane.py tests/test_liepin_pi_worker_client.py tests/test_liepin_pi_executor.py -q
```

Expected:

```text
passed
```

- [ ] **Step 3: Run runtime state flow tests that cover merge and scoring inputs**

Run:

```bash
pytest tests/test_runtime_state_flow.py tests/test_runtime_audit.py -q
```

Expected:

```text
passed
```

- [ ] **Step 4: Run grep acceptance checks**

Run:

```bash
rg -n "liepin_must_haves_json|liepin_nice_to_haves_json|\"must_haves\"|\"nice_to_haves\"|must-have and nice-to-have" src/seektalent/providers/liepin src/seektalent/providers/pi_agent/pi_external.py src/seektalent/runtime
```

Expected:

```text
no output
```

Run:

```bash
rg -n "requirement_sheet|seektalent.pi_liepin_resumes.v2|liepin.repair_resume_output|open_json_task_session" src/seektalent/providers/liepin src/seektalent/providers/pi_agent/pi_external.py src/seektalent/runtime src/seektalent_ui/runtime_bridge.py tests/test_liepin_pi_executor.py tests/test_liepin_pi_worker_client.py tests/test_liepin_runtime_source_lane.py tests/test_pi_external_agent.py tests/test_workbench_runtime_owned_execution.py
```

Expected: output includes the new active contract in source and tests.

- [ ] **Step 5: Run static checks used by the project**

Run:

```bash
ruff check src/seektalent/providers/liepin src/seektalent/providers/pi_agent/pi_external.py src/seektalent/runtime src/seektalent_ui/runtime_bridge.py tests/test_pi_external_agent.py tests/test_liepin_pi_executor.py tests/test_liepin_pi_worker_client.py tests/test_liepin_runtime_source_lane.py tests/test_runtime_multi_source_round_dispatch.py tests/test_runtime_source_adapter_boundary.py tests/test_workbench_runtime_owned_execution.py
```

Expected:

```text
All checks passed!
```

- [ ] **Step 6: Commit verification fixes if any focused tests revealed small issues**

Only create this commit if Step 1 through Step 5 required code changes:

```bash
git add src/seektalent/providers/liepin src/seektalent/providers/pi_agent/pi_external.py src/seektalent/runtime src/seektalent_ui/runtime_bridge.py tests
git commit -m "test: verify liepin pi source adapter contract"
```

## Self-Review

- Spec coverage:
  - Unified runtime ownership: Tasks 1 and 8.
  - Dual-source barrier: Task 1.
  - Liepin exploit/explore PI parallelism: Task 5.
  - RequirementSheet into PI request: Tasks 2 and 3.
  - Old payload field removal: Tasks 3 and 7.
  - Structured full-resume output validation and repair: Task 4.
  - Runtime-owned terminal cleanup: Task 4.
  - Shared normalization: Task 6.
  - No live OpenCLI dependency: all tests use fakes.
- Placeholder scan:
  - The plan uses concrete files, commands, snippets, and expected results.
  - The plan does not include deferred implementation markers.
- Type consistency:
  - `SourceRoundDispatchRequest.requirement_sheet` flows into `RuntimeSourceLaneRequest.requirement_sheet`.
  - `liepin_requirement_sheet_json` flows from source lane provider context into `LiepinPiWorkerClient.search()`.
  - `requirement_sheet` flows from worker client into `PiLiepinExecutor.search_resumes()`.
  - `seektalent.pi_liepin_resumes.v2` is used by PI task payload, envelope validation, partial recovery, and tests.

## Plan Review Addendum

### NOT in scope

- Live Liepin/OpenCLI/browser execution: this slice uses fake PI/RPC clients only because live browser automation can trigger provider risk control.
- Requirement extraction, scoring, reflection, and finalizer prompt changes: those were handled or deferred in separate slices and should not move while source contracts are changing.
- UI candidate/result display: this slice changes backend/source contracts and Workbench bridge inputs only; frontend rendering of backend fields remains a later UI slice.
- Global Liepin throttling/risk-control policy: terminal blocked states are made explicit, but a provider-safe live-run scheduler is separate work.
- React UI deletion: the user wants old UI cleanup, but this slice is the source adapter contract; deletion should be a separate UI cleanup plan after backend data flow is stable.
- Generic provider-agent abstraction: keep Liepin PI concrete until a second browser-backed source creates real pressure.

### What already exists

- `dispatch_source_rounds()` already uses `asyncio.TaskGroup`; the plan reuses it and adds the missing hard `RequirementSheet` contract plus a barrier regression.
- `WorkflowRuntime` already owns requirement extraction, source dispatch, merge, normalization, scoring, reflection, and finalization; the plan keeps that as the core instead of creating a Workbench-owned source flow.
- `run_liepin_logical_query_bundle()` and `run_liepin_source_lane()` already exist; the plan changes their data contract and concurrency instead of adding a parallel Liepin runtime.
- `PiLiepinExecutor.search_resumes()` already validates structured resume output; the plan upgrades it to v2, required requirement-sheet input, stateful repair, and runtime-owned cleanup.
- `PiRpcAgentClient` already owns the real Python prompt contract and cleanup call; the plan updates this active runtime surface, not only markdown skill text.
- `runtime_bridge.py` already has approved Workbench `RequirementSheet` objects; the plan now passes them into direct source-lane calls instead of letting those paths omit the canonical contract.

### Review findings folded into the plan

- [P1] Stateful repair originally used a stateless one-shot PI client. The plan now requires a lane-scoped PI RPC session so search and repair run in the same child-agent context.
- [P1] Persistent subprocess sessions can leave the first prompt's `agent_end` in the stream after a tool envelope. The plan now requires draining through `agent_end` before accepting the next repair prompt.
- [P1] v2 tool envelopes would not be recognized unless `_liepin_tool_name_for_schema()` maps `seektalent.pi_liepin_resumes.v2` to `seektalent_opencli_finalize_liepin_resumes`. The plan now adds that regression.
- [P1] `RequirementSheet` must be a hard active contract, not optional compatibility. The plan now requires it through dispatch, lane request, Workbench bridge, worker client, and executor.
- [P2] Semantic repair was undercount-only. The plan now classifies repairable parsed-envelope gaps for missing resume count, protected detail refs, and detail payloads.

### Test coverage diagram

```text
CODE PATHS                                                PLANNED COVERAGE
[+] Source round dispatch
  ├── [★★★] carries RequirementSheet                      test_dispatch_request_carries_requirement_sheet_to_sources
  └── [★★★] CTS fast / Liepin slow barrier                 test_dispatch_waits_for_liepin_terminal_state_after_cts_finishes_first

[+] Runtime source-lane request and Workbench bridge
  ├── [★★★] Liepin provider_context gets sheet JSON        test_liepin_lane_passes_requirement_sheet_json_to_worker_context
  ├── [★★ ] public payload exposes counts only             existing source-lane public payload test updated
  └── [★★ ] Workbench direct detail lane passes sheet      test_leased_liepin_detail_open_intent_executes_detail_lane_and_persists_detail_evidence

[+] PI worker/executor/prompt contract
  ├── [★★★] worker requires sheet and removes old fields   test_pi_worker_client_*requirement_sheet*
  ├── [★★★] executor emits v2 sheet payload                test_search_resumes_sends_requirement_sheet_payload_without_old_fields
  ├── [★★★] Python prompt advertises v2 + repair           tests in test_pi_external_agent.py
  └── [★★★] v2 finalize tool envelope recognized           test_liepin_resume_task_accepts_v2_finalize_tool_envelope

[+] Stateful PI session and repair
  ├── [★★★] same session handles search then repair        test_json_task_session_keeps_context_for_repair_and_cleans_up_once
  ├── [★★★] subprocess session accepts two prompts         test_subprocess_session_accepts_two_prompts_before_close
  ├── [★★★] tool envelope stream drains before next prompt test_subprocess_session_drains_tool_envelope_before_next_prompt
  ├── [★★★] underfilled output repairs in one session      test_search_resumes_repairs_underfilled_output_inside_one_pi_session
  └── [★★★] missing detail fields repair in one session    test_search_resumes_repairs_missing_detail_contract_inside_one_pi_session

[+] Liepin lane runtime
  ├── [★★★] exploit/explore run as independent agents      test_liepin_logical_query_bundle_runs_independent_child_agents_in_parallel
  ├── [★★★] lane counts preserve 7/3 target budgets        same concurrency test
  ├── [★★★] detail candidates enter normalization store    test_liepin_detail_backed_lane_populates_normalized_updates
  └── [★★ ] old recommendation-first path disconnected     runtime adapter boundary test
```

Legend: `★★★` behavior + edge/error path, `★★` behavior path. No live E2E or live LLM eval is required in this slice; prompt/tool changes are covered by contract tests and fake RPC events.

### Failure modes

| Failure mode | Covered by plan | Handling expected | User-visible behavior |
| --- | --- | --- | --- |
| CTS returns quickly while Liepin is still running | Yes | Source barrier waits for selected source terminal states | Runtime does not score CTS-only results early |
| Active Liepin path lacks `RequirementSheet` | Yes | Worker raises `requirement_sheet_missing` | Source lane fails/blocks with safe reason instead of silent bad search |
| PI output is underfilled | Yes | One semantic repair in same child-agent session | Lane succeeds after repair or returns terminal failed/partial |
| PI output lacks protected refs/detail payloads | Yes | Repair request names missing fields only | No full restart; child agent supplements current context |
| Raw malformed JSON | Yes through existing strict JSON retry | No semantic repair without parsed envelope | Terminal malformed/failed if retry fails |
| v2 tool envelope comes from OpenCLI finalize tool | Yes | Python tool schema accepts v2 finalize event | Runtime reads structured envelope even if final text is not JSON |
| Persistent PI session leaves stale stream output | Yes | Session drains current prompt through `agent_end` | Repair prompt cannot consume previous prompt output |
| Liepin risk-control/login challenge | Partly | Safe blocked reason and cleanup; no live scheduler in scope | Source returns blocked and runtime can degrade after barrier |
| Detail-backed candidates miss normalization | Yes | Source lane fills `normalized_store_updates` | Scoring sees normalized Liepin resumes like CTS |

No critical silent failure remains in the plan after the amendments above.

### Parallelization

| Workstream | Tasks | Modules touched | Depends on |
| --- | --- | --- | --- |
| Runtime contract | 1, 2 | `src/seektalent/runtime`, `src/seektalent_ui` | None |
| PI contract/session | 3, 4 | `src/seektalent/providers/pi_agent`, `src/seektalent/providers/liepin` | None |
| Liepin lane runtime | 5, 6 | `src/seektalent/providers/liepin`, `src/seektalent/runtime` | Runtime contract |
| Legacy cleanup and verification | 7, 8 | runtime + Liepin + tests | Runtime contract + PI contract/session + Liepin lane runtime |

Recommended worktree split: run Runtime contract and PI contract/session in parallel worktrees. Merge both, then run Liepin lane runtime, then legacy cleanup/verification. Do not run Tasks 5-7 before Tasks 1-4 merge because they touch the same adapter boundary and will create avoidable conflicts.

### Implementation Tasks From Review

- [ ] **T1 (P1, human: ~2h / CC: ~20min)** — PI RPC session — keep repair in the same child-agent context and drain each prompt through `agent_end`.
  - Surfaced by: Architecture review.
  - Files: `src/seektalent/providers/pi_agent/pi_external.py`, `tests/test_pi_external_agent.py`.
  - Verify: PI session tests in Task 4.
- [ ] **T2 (P1, human: ~45min / CC: ~10min)** — PI schema bridge — map v2 resume envelopes to the existing finalize tool.
  - Surfaced by: Code quality/data-flow review.
  - Files: `src/seektalent/providers/pi_agent/pi_external.py`, `tests/test_pi_external_agent.py`.
  - Verify: `test_liepin_resume_task_accepts_v2_finalize_tool_envelope`.
- [ ] **T3 (P1, human: ~1h / CC: ~15min)** — RequirementSheet hard contract — remove optional fallback through runtime, Workbench bridge, worker client, and executor.
  - Surfaced by: Architecture/code-quality review.
  - Files: `src/seektalent/runtime`, `src/seektalent_ui/runtime_bridge.py`, `src/seektalent/providers/liepin`.
  - Verify: Tasks 1-3 tests plus grep acceptance.
- [ ] **T4 (P2, human: ~1h / CC: ~15min)** — Repair gap classifier — classify undercount, missing protected refs, and missing detail payloads before strict validation.
  - Surfaced by: Test review.
  - Files: `src/seektalent/providers/liepin/pi_resume_contract.py`, `src/seektalent/providers/liepin/pi_executor.py`.
  - Verify: missing-detail repair executor test.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | not run | Not required for this source-contract slice |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | skipped | Outside voice not run; AskUserQuestion tooling unavailable in this host mode |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 5 findings folded into plan, 0 critical gaps remain |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | not applicable | No UI/UX changes in this slice |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | not run | Not required |

- **UNRESOLVED:** 0.
- **VERDICT:** ENG CLEARED for implementation. Execute the plan task-by-task; do not start live Liepin/OpenCLI verification in this slice.
