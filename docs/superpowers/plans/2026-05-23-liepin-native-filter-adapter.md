# Liepin Native Filter Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Liepin apply Runtime-owned city, work-experience, and age filters through provider-specific browser actions, then verify the complete flow in real Chrome.

**Architecture:** Runtime remains the owner of query strategy, filter semantics, budget, and source scheduling. Liepin gets a provider-specific compiler that translates Runtime intent into per-search safe native filter targets, and the OpenCLI card-search runner applies each target payload before reading cards.

**Tech Stack:** Python 3.12, Pydantic, pytest, ruff, ty, Svelte/Vite build, SeekTalent PI/OpenCLI browser tooling, real Chrome QA.

---

Linked spec: `docs/superpowers/specs/2026-05-23-liepin-native-filter-adapter-design.md`

## File Structure

- Create `src/seektalent/providers/liepin/filter_compiler.py`
  - Owns Liepin-specific conversion from `RuntimeSourceQueryIntent` to safe browser-native per-city filter targets.
- Modify `src/seektalent/providers/liepin/source_compiler.py`
  - Uses the new compiler and passes native filter target payloads through `SearchRequest.provider_context`.
- Modify `src/seektalent/providers/liepin/pi_worker_client.py`
  - Forwards native filters from `SearchRequest.provider_context` into the executor.
- Modify `src/seektalent/providers/liepin/pi_executor.py`
  - Adds `native_filters` to the PI task payload.
- Modify `src/seektalent/providers/pi_agent/contracts.py`
  - Adds the safe native filter schema at the PI boundary.
- Modify `src/seektalent/providers/pi_agent/opencli_browser_cli.py`
  - Accepts `nativeFilters` from the tool payload and forwards it to the runner.
- Modify `src/seektalent/providers/pi_agent/pi_external.py`
  - Tells the PI agent to forward supplied native filters unchanged into the OpenCLI search tool.
- Modify `src/seektalent/providers/pi_agent/opencli_browser.py`
  - Applies native filters after keyword search and before card extraction.
- Modify `src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts`
  - Adds `nativeFilters` to the OpenCLI tool schema.
- Modify `src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md`
  - Documents that only supplied safe native filters may be applied.
- Modify `src/seektalent/runtime/public_events.py`
  - Ensures filter coverage reason codes are business-safe.
- Test `tests/test_liepin_native_filter_compiler.py`
  - Covers provider-specific filter plan compilation.
- Test `tests/test_liepin_source_compiler.py`
  - Covers `SearchRequest` propagation.
- Test `tests/test_liepin_pi_worker_client.py`
  - Covers worker-to-executor payload propagation.
- Test `tests/test_pi_opencli_browser.py`
  - Covers browser action ordering and action trace.
- Test `tests/test_pi_agent_boundaries.py`
  - Covers PI contract and public-safe payload boundaries.

## Task 1: Add Liepin Native Filter Compiler

**Files:**
- Create: `src/seektalent/providers/liepin/filter_compiler.py`
- Test: `tests/test_liepin_native_filter_compiler.py`

- [ ] **Step 1: Write failing compiler tests**

Add `tests/test_liepin_native_filter_compiler.py`:

```python
from __future__ import annotations

import json

from seektalent.providers.liepin.filter_compiler import compile_liepin_native_filters
from seektalent.runtime.source_filters import (
    RuntimeFilterIntent,
    RuntimeLocationExecutionIntent,
)
from seektalent.runtime.source_query_intent import RuntimeSourceQueryIntent
from seektalent.runtime.source_lanes import DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY


def _intent() -> RuntimeSourceQueryIntent:
    return RuntimeSourceQueryIntent(
        round_no=2,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        query_terms=("数据开发专家",),
        keyword_query="数据开发专家",
        requested_count=12,
        provider_scan_limit=12,
        source_plan_version="test",
        filter_intents=(
            RuntimeFilterIntent(
                field="experience_requirement",
                value=["min=3", "max=5"],
                required=False,
                origin="controller",
            ),
            RuntimeFilterIntent(
                field="age_requirement",
                value=["max=35"],
                required=False,
                origin="controller",
            ),
        ),
        location_intent=RuntimeLocationExecutionIntent(
            mode="balanced_all",
            allowed_locations=("上海", "北京", "深圳"),
            preferred_locations=(),
            priority_order=(),
            balanced_order=("北京", "深圳", "上海"),
            rotation_offset=1,
            target_new=12,
        ),
        age_intent=None,
    )


def test_compile_liepin_native_filters_uses_runtime_location_and_range_filters() -> None:
    plan = compile_liepin_native_filters(_intent(), budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY)

    assert [(target.city, target.requested_count) for target in plan.targets] == [
        ("北京", 4),
        ("深圳", 4),
        ("上海", 4),
    ]
    first_target = plan.targets[0]
    assert first_target.experience_min_years == 3
    assert first_target.experience_max_years == 5
    assert first_target.age_max == 35
    assert first_target.to_safe_payload() == {
        "city": "北京",
        "experience": {"minYears": 3, "maxYears": 5},
        "age": {"max": 35},
        "sourceTarget": {
            "phase": "balanced",
            "batchNo": 1,
            "requestedCount": 4,
        },
    }


def test_compile_liepin_native_filters_rejects_wrong_source() -> None:
    intent = _intent()
    cts_intent = RuntimeSourceQueryIntent(
        round_no=intent.round_no,
        source_kind="cts",
        query_role=intent.query_role,
        lane_type=intent.lane_type,
        query_instance_id=intent.query_instance_id,
        query_fingerprint=intent.query_fingerprint,
        query_terms=intent.query_terms,
        keyword_query=intent.keyword_query,
        requested_count=intent.requested_count,
        provider_scan_limit=intent.provider_scan_limit,
        source_plan_version=intent.source_plan_version,
        filter_intents=intent.filter_intents,
        location_intent=intent.location_intent,
        age_intent=intent.age_intent,
    )

    try:
        compile_liepin_native_filters(cts_intent, budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY)
    except ValueError as exc:
        assert str(exc) == "liepin_filter_compiler_wrong_source:cts"
    else:
        raise AssertionError("expected wrong-source compiler error")


def test_compile_liepin_native_filters_payload_is_json_safe() -> None:
    payloads = [
        target.to_safe_payload()
        for target in compile_liepin_native_filters(
            _intent(),
            budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY,
        ).targets
    ]
    encoded = json.dumps(payloads, ensure_ascii=False, sort_keys=True)

    assert "数据开发专家" not in encoded
    assert "cookie" not in encoded.lower()
    assert "authorization" not in encoded.lower()


def test_compile_liepin_native_filters_preserves_single_city_as_one_target() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        query_terms=("数据开发专家",),
        keyword_query="数据开发专家",
        requested_count=10,
        provider_scan_limit=10,
        source_plan_version="test",
        filter_intents=(),
        location_intent=RuntimeLocationExecutionIntent(
            mode="single",
            allowed_locations=("上海",),
            preferred_locations=(),
            priority_order=(),
            balanced_order=("上海",),
            rotation_offset=0,
            target_new=10,
        ),
        age_intent=None,
    )

    plan = compile_liepin_native_filters(
        intent,
        budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY,
    )

    assert len(plan.targets) == 1
    assert plan.targets[0].city == "上海"
    assert plan.targets[0].requested_count == 10
```

- [ ] **Step 2: Run compiler tests and verify failure**

Run:

```bash
uv run pytest tests/test_liepin_native_filter_compiler.py -q
```

Expected: FAIL because `seektalent.providers.liepin.filter_compiler` does not exist.

- [ ] **Step 3: Implement the compiler**

Create `src/seektalent/providers/liepin/filter_compiler.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from seektalent.retrieval.query_plan import allocate_balanced_city_targets
from seektalent.runtime.source_query_intent import RuntimeSourceQueryIntent
from seektalent.runtime.source_lanes import RuntimeSourceBudgetPolicy


@dataclass(frozen=True)
class LiepinNativeFilterTarget:
    phase: str
    batch_no: int
    requested_count: int
    city: str | None = None
    experience_min_years: int | None = None
    experience_max_years: int | None = None
    age_min: int | None = None
    age_max: int | None = None

    def to_safe_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        if self.city:
            payload["city"] = self.city
        experience: dict[str, int] = {}
        if self.experience_min_years is not None:
            experience["minYears"] = self.experience_min_years
        if self.experience_max_years is not None:
            experience["maxYears"] = self.experience_max_years
        if experience:
            payload["experience"] = experience
        age: dict[str, int] = {}
        if self.age_min is not None:
            age["min"] = self.age_min
        if self.age_max is not None:
            age["max"] = self.age_max
        if age:
            payload["age"] = age
        payload["sourceTarget"] = {
            "phase": self.phase,
            "batchNo": self.batch_no,
            "requestedCount": self.requested_count,
        }
        return payload


@dataclass(frozen=True)
class LiepinNativeFilterPlan:
    targets: tuple[LiepinNativeFilterTarget, ...]


def compile_liepin_native_filters(
    intent: RuntimeSourceQueryIntent,
    *,
    budget_policy: RuntimeSourceBudgetPolicy,
) -> LiepinNativeFilterPlan:
    del budget_policy
    if intent.source_kind != "liepin":
        raise ValueError(f"liepin_filter_compiler_wrong_source:{intent.source_kind}")
    experience_min: int | None = None
    experience_max: int | None = None
    age_min: int | None = None
    age_max: int | None = None
    for filter_intent in intent.filter_intents:
        if filter_intent.field == "experience_requirement":
            parsed = _parse_min_max(filter_intent.value)
            experience_min = parsed.get("min")
            experience_max = parsed.get("max")
        elif filter_intent.field == "age_requirement":
            parsed = _parse_min_max(filter_intent.value)
            age_min = parsed.get("min")
            age_max = parsed.get("max")
    targets = tuple(
        LiepinNativeFilterTarget(
            phase=phase,
            batch_no=batch_no,
            requested_count=requested_count,
            city=city,
            experience_min_years=experience_min,
            experience_max_years=experience_max,
            age_min=age_min,
            age_max=age_max,
        )
        for phase, batch_no, city, requested_count in _location_targets(intent)
    )
    return LiepinNativeFilterPlan(targets=targets)


def _location_targets(intent: RuntimeSourceQueryIntent) -> tuple[tuple[str, int, str | None, int], ...]:
    location = intent.location_intent
    if location is None or not location.allowed_locations:
        return (("balanced", 1, None, intent.provider_scan_limit),)
    if location.mode == "single":
        return (("balanced", 1, location.allowed_locations[0], intent.provider_scan_limit),)
    if location.mode == "priority_then_fallback" and location.priority_order:
        targets: list[tuple[str, int, str | None, int]] = []
        batch_no = 1
        for city in location.priority_order:
            targets.append(("priority", batch_no, city, intent.provider_scan_limit))
            batch_no += 1
        if location.balanced_order:
            for city, requested in allocate_balanced_city_targets(
                ordered_cities=list(location.balanced_order),
                target_new=intent.provider_scan_limit,
            ):
                targets.append(("balanced", batch_no, city, requested))
                batch_no += 1
        return tuple(targets)
    return tuple(
        ("balanced", batch_no, city, requested)
        for batch_no, (city, requested) in enumerate(
            allocate_balanced_city_targets(
                ordered_cities=list(location.balanced_order or location.allowed_locations),
                target_new=intent.provider_scan_limit,
            ),
            start=1,
        )
    )


def _parse_min_max(value: Any) -> dict[str, int]:
    items = value if isinstance(value, list) else [value]
    parsed: dict[str, int] = {}
    for item in items:
        text = str(item).strip()
        if text.startswith("min="):
            parsed["min"] = int(text.removeprefix("min="))
        elif text.startswith("max="):
            parsed["max"] = int(text.removeprefix("max="))
    return parsed
```

- [ ] **Step 4: Run compiler tests and verify pass**

Run:

```bash
uv run pytest tests/test_liepin_native_filter_compiler.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/providers/liepin/filter_compiler.py tests/test_liepin_native_filter_compiler.py
git commit -m "feat: add liepin native filter compiler"
```

## Task 2: Wire Liepin Source Compiler To Native Filters

**Files:**
- Modify: `src/seektalent/providers/liepin/source_compiler.py`
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Test: `tests/test_liepin_source_compiler.py`
- Test: `tests/test_liepin_runtime_source_lane.py`

- [ ] **Step 1: Write failing source compiler and runtime lane tests**

Add `tests/test_liepin_source_compiler.py`:

```python
from __future__ import annotations

import json

from seektalent.providers.liepin.source_compiler import compile_liepin_source_query_intents
from seektalent.runtime.source_filters import RuntimeFilterIntent, RuntimeLocationExecutionIntent
from seektalent.runtime.source_query_intent import RuntimeSourceQueryIntent


def test_liepin_source_compiler_passes_native_filters_to_provider_context() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=1,
        source_kind="liepin",
        query_role="explore",
        lane_type="generic_explore",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        query_terms=("数据开发专家",),
        keyword_query="数据开发专家",
        requested_count=10,
        provider_scan_limit=10,
        source_plan_version="test",
        filter_intents=(
            RuntimeFilterIntent(
                field="experience_requirement",
                value=["min=3", "max=5"],
                required=False,
                origin="controller",
            ),
            RuntimeFilterIntent(
                field="age_requirement",
                value=["max=35"],
                required=False,
                origin="controller",
            ),
        ),
        location_intent=RuntimeLocationExecutionIntent(
            mode="single",
            allowed_locations=("上海",),
            preferred_locations=(),
            priority_order=("上海",),
            balanced_order=("上海",),
            rotation_offset=0,
            target_new=10,
        ),
        age_intent=None,
    )

    compiled = compile_liepin_source_query_intents((intent,))
    request = compiled.queries[0].search_request

    assert compiled.unsupported_filters == ()
    assert len(compiled.queries) == 1
    assert request.provider_filters == {}
    assert json.loads(str(request.provider_context["liepin_native_filters_json"])) == {
        "city": "上海",
        "experience": {"minYears": 3, "maxYears": 5},
        "age": {"max": 35},
        "sourceTarget": {"phase": "balanced", "batchNo": 1, "requestedCount": 10},
    }


def test_liepin_source_compiler_expands_balanced_city_targets() -> None:
    intent = RuntimeSourceQueryIntent(
        round_no=2,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="query-1",
        query_fingerprint="fp-1",
        query_terms=("数据开发专家",),
        keyword_query="数据开发专家",
        requested_count=12,
        provider_scan_limit=12,
        source_plan_version="test",
        filter_intents=(),
        location_intent=RuntimeLocationExecutionIntent(
            mode="balanced_all",
            allowed_locations=("上海", "北京", "深圳"),
            preferred_locations=(),
            priority_order=(),
            balanced_order=("北京", "深圳", "上海"),
            rotation_offset=1,
            target_new=12,
        ),
        age_intent=None,
    )

    compiled = compile_liepin_source_query_intents((intent,))

    payloads = [
        json.loads(str(query.search_request.provider_context["liepin_native_filters_json"]))
        for query in compiled.queries
    ]
    assert [payload["city"] for payload in payloads] == ["北京", "深圳", "上海"]
    assert [payload["sourceTarget"]["requestedCount"] for payload in payloads] == [4, 4, 4]
```

Add to `tests/test_liepin_runtime_source_lane.py`:

```python
import json

from seektalent.runtime.source_filters import RuntimeLocationExecutionIntent
from seektalent.runtime.source_query_intent import RuntimeSourceQueryIntent


def test_liepin_logical_query_bundle_executes_filter_targets_until_provider_scan_limit() -> None:
    class TargetWorker(FakeWorker):
        async def search(
            self,
            request: SearchRequest,
            *,
            round_no: int,
            trace_id: str,
            provider_account_hash: str | None = None,
        ) -> SearchResult:
            native_filters = json.loads(str(request.provider_context["liepin_native_filters_json"]))
            city = str(native_filters["city"])
            self.search_calls.append(
                {
                    "request": request,
                    "provider_context": request.provider_context,
                    "native_filters": native_filters,
                    "round_no": round_no,
                    "trace_id": trace_id,
                    "provider_account_hash": provider_account_hash,
                }
            )
            candidates: list[ResumeCandidate] = []
            snapshots: list[ProviderSnapshot] = []
            for offset in range(2):
                provider_key = f"{city}-{offset}"
                raw_payload = {"candidateId": provider_key}
                candidates.append(
                    ResumeCandidate(
                        resume_id=f"liepin-{provider_key}",
                        source_resume_id=provider_key,
                        snapshot_sha256=sha256_json(raw_payload),
                        dedup_key=provider_key,
                        search_text=f"{city} 数据开发专家 {offset}",
                        raw={},
                    )
                )
                snapshots.append(
                    ProviderSnapshot(
                        provider_name="liepin",
                        payload_kind="card",
                        raw_payload=raw_payload,
                        normalized_text=f"{city} 数据开发专家 {offset}",
                        provider_subject_id=provider_key,
                        provider_listing_id=None,
                        synthetic_candidate_fingerprint=provider_key,
                        identity_confidence="provider_subject_id",
                        extraction_source="test",
                        extractor_version="test",
                        pii_classification="no_direct_contact",
                        retention_policy="provider_snapshot_7d",
                        access_scope="local_run_only",
                        redaction_state="raw_provider_payload",
                        score_evidence_source="card_only",
                    )
                )
            return SearchResult(
                candidates=candidates,
                provider_snapshots=snapshots,
                diagnostics=[],
                exhausted=True,
                raw_candidate_count=len(candidates),
            )

    worker = TargetWorker()
    logical_query = LogicalQueryDispatch(
        round_no=2,
        query_role="exploit",
        lane_type="exploit",
        query_terms=("数据开发专家",),
        keyword_query="数据开发专家",
        query_instance_id="runtime-query-1",
        query_fingerprint="runtime-fingerprint-1",
        requested_count=4,
        source_plan_version="7",
    )
    intent = RuntimeSourceQueryIntent(
        round_no=2,
        source_kind="liepin",
        query_role="exploit",
        lane_type="exploit",
        query_instance_id="runtime-query-1",
        query_fingerprint="runtime-fingerprint-1",
        query_terms=("数据开发专家",),
        keyword_query="数据开发专家",
        requested_count=4,
        provider_scan_limit=4,
        source_plan_version="7",
        filter_intents=(),
        location_intent=RuntimeLocationExecutionIntent(
            mode="priority_then_fallback",
            allowed_locations=("上海", "北京", "深圳"),
            preferred_locations=("上海",),
            priority_order=("上海",),
            balanced_order=("北京", "深圳"),
            rotation_offset=0,
            target_new=4,
        ),
        age_intent=None,
    )

    result = asyncio.run(
        run_liepin_logical_query_bundle(
            settings=make_settings(),
            runtime_run_id="runtime-run-1",
            source_plan_id="plan-liepin",
            job_title="数据开发专家",
            jd="负责数据平台建设",
            notes="Python",
            logical_queries=(logical_query,),
            source_budget_policy=RuntimeSourceBudgetPolicy(liepin_card_page_size=30, liepin_max_cards=30),
            liepin_context={"provider_account_hash": "acct_hash_123"},
            source_query_intents=(intent,),
            worker_client=worker,
        )
    )

    assert [call["trace_id"] for call in worker.search_calls] == [
        "plan-liepin:round:2:lane:1:target:1",
        "plan-liepin:round:2:lane:1:target:2",
    ]
    assert [call["native_filters"]["city"] for call in worker.search_calls] == ["上海", "北京"]
    assert len(result.candidate_store_updates) == 4
    assert all(item.query_fingerprint == "runtime-fingerprint-1" for item in result.source_evidence_updates)
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/test_liepin_source_compiler.py tests/test_liepin_runtime_source_lane.py::test_liepin_logical_query_bundle_executes_filter_targets_until_provider_scan_limit -q
```

Expected: FAIL because the current Liepin compiler marks filters unsupported, does not emit `liepin_native_filters_json`, and `run_liepin_logical_query_bundle(...)` executes only `compiled_queries[index - 1]` instead of all native filter targets for the logical query.

- [ ] **Step 3: Update source compiler**

Modify `src/seektalent/providers/liepin/source_compiler.py`:

```python
import json

from seektalent.providers.liepin.filter_compiler import LiepinNativeFilterTarget, compile_liepin_native_filters
from seektalent.runtime.source_lanes import DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY
```

Inside `compile_liepin_source_query_intents`, before constructing `SearchRequest`:

```python
    native_filter_plan = compile_liepin_native_filters(
        intent,
        budget_policy=DEFAULT_RUNTIME_SOURCE_BUDGET_POLICY,
    )
    for target_index, target in enumerate(native_filter_plan.targets, start=1):
        native_filters = target.to_safe_payload()
        query_unsupported = _unsupported_filters(intent, native_filter_target=target)
```

Set these `SearchRequest` fields. Keep `provider_filters` empty because `SearchRequest.provider_filters` only supports flat `ConstraintValue` values and the native filter payload is nested:

```python
provider_filters={},
page_size=target.requested_count,
provider_context={
    "liepin_max_cards": str(target.requested_count),
    "query_instance_id": intent.query_instance_id,
    "query_fingerprint": intent.query_fingerprint,
    "runtime_query_role": intent.query_role,
    "lane_type": intent.lane_type,
    "source_plan_version": intent.source_plan_version,
    "liepin_native_filters_json": json.dumps(native_filters, ensure_ascii=False, sort_keys=True),
    "liepin_source_filter_target_index": str(target_index),
},
```

Change `_unsupported_filters` signature:

```python
def _unsupported_filters(
    intent: RuntimeSourceQueryIntent,
    *,
    native_filter_target: LiepinNativeFilterTarget,
) -> tuple[UnsupportedSourceFilter, ...]:
```

Append one `LiepinCompiledQuery` per `native_filter_plan.targets` item. Keep unsupported entries only for fields that are still not covered by the target payload. City, experience, and age are covered when their payload fields exist.

Modify `src/seektalent/providers/liepin/runtime_lane.py` so `run_liepin_logical_query_bundle(...)` executes compiled queries, not just `logical_queries[index - 1]`. Each compiled query must keep the original logical query identity/fingerprint, but its `source_lane_run_id` should include the target index:

```python
source_lane_run_id=f"{source_plan_id}:round:{compiled_query.intent.round_no}:lane:{logical_index}:target:{target_index}"
```

Stop executing additional targets for the same logical query once that logical query has collected `intent.provider_scan_limit` unique candidates. This is what keeps priority/fallback city execution aligned with Runtime target semantics instead of blindly scanning every city.

Do not rebuild a fresh `SearchRequest` that drops the compiled target context. The runtime lane must preserve these compiled request fields when calling the provider:

- `query_terms`
- `keyword_query`
- `query_role`
- `page_size`
- `provider_filters={}`
- `provider_context["liepin_native_filters_json"]`
- `provider_context["liepin_source_filter_target_index"]`
- Runtime identity fields: `query_instance_id`, `query_fingerprint`, `runtime_query_role`, `lane_type`, and `source_plan_version`

- [ ] **Step 4: Run source compiler and runtime lane tests**

Run:

```bash
uv run pytest tests/test_liepin_source_compiler.py tests/test_liepin_runtime_source_lane.py tests/test_runtime_source_adapter_boundary.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/providers/liepin/source_compiler.py src/seektalent/providers/liepin/runtime_lane.py tests/test_liepin_source_compiler.py tests/test_liepin_runtime_source_lane.py
git commit -m "feat: pass liepin native filters through source compiler"
```

## Task 3: Extend PI/OpenCLI Boundary With Safe Native Filters

**Files:**
- Modify: `src/seektalent/providers/pi_agent/contracts.py`
- Modify: `src/seektalent/providers/liepin/pi_worker_client.py`
- Modify: `src/seektalent/providers/liepin/pi_executor.py`
- Modify: `src/seektalent/providers/pi_agent/opencli_browser_cli.py`
- Modify: `src/seektalent/providers/pi_agent/pi_external.py`
- Modify: `src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts`
- Modify: `src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md`
- Test: `tests/test_liepin_pi_worker_client.py`
- Test: `tests/test_pi_agent_boundaries.py`

- [ ] **Step 1: Write failing boundary tests**

Add to `tests/test_pi_agent_boundaries.py`:

```python
def test_liepin_search_cards_task_accepts_safe_native_filters() -> None:
    from seektalent.providers.pi_agent.contracts import LiepinSearchCardsTask, PiAgentTaskType

    task = LiepinSearchCardsTask.model_validate(
        {
            "schema_version": "pi-agent-task-v1",
            "task_type": PiAgentTaskType.LIEPIN_SEARCH_CARDS,
            "session_id": "session-1",
            "source_run_id": "source-1",
            "connection_id": "conn-1",
            "artifact_policy": "protected_snapshots_only",
            "query_terms": ["数据开发专家"],
            "keyword_query": "数据开发专家",
            "max_pages": 1,
            "max_cards": 10,
            "stop_conditions": ["page_exhausted"],
            "native_filters": {
                "city": "上海",
                "experience": {"minYears": 3, "maxYears": 5},
                "age": {"max": 35},
                "partialReasonCodes": [],
            },
        }
    )

    assert task.native_filters is not None
    assert task.native_filters.city == "上海"
    assert task.native_filters.experience.min_years == 3
    assert task.native_filters.age.max == 35


def test_liepin_search_cards_prompt_forwards_native_filters() -> None:
    from seektalent.providers.pi_agent.pi_external import _task_instruction

    instruction = _task_instruction("liepin.search_cards")

    assert "nativeFilters" in instruction
    assert "when present" in instruction
```

Add to `tests/test_liepin_pi_worker_client.py`:

```python
def test_pi_worker_forwards_native_filters_to_executor() -> None:
    import asyncio
    from types import SimpleNamespace

    from seektalent.core.retrieval.provider_contract import SearchRequest
    from seektalent.providers.liepin.pi_worker_client import LiepinPiWorkerClient

    class FakeExecutor:
        def __init__(self) -> None:
            self.native_filters = None

        def search_cards(self, **kwargs):
            self.native_filters = kwargs["native_filters"]
            from seektalent.providers.liepin.pi_executor import (
                LiepinPiCardSearchResult,
                PiLiepinResultStatus,
                PiLiepinStopReason,
            )
            return LiepinPiCardSearchResult(
                status=PiLiepinResultStatus.FAILED,
                stop_reason=PiLiepinStopReason.BLOCKED_BACKEND_UNAVAILABLE,
                safe_reason_code="blocked_backend_unavailable",
            )

    executor = FakeExecutor()
    client = LiepinPiWorkerClient(
        executor=executor,
        session_id="session-1",
        connection_id="conn-1",
        provider_account_lock_key="account-1",
        expected_opencli_declared_tool_names=("seektalent_opencli_search_liepin_cards",),
        opencli_status_probe=SimpleNamespace(status=lambda: SimpleNamespace(ok=True, safe_reason_code="configured")),
    )
    request = SearchRequest(
        query_terms=["数据开发专家"],
        query_role="primary",
        keyword_query="数据开发专家",
        adapter_notes=[],
        runtime_constraints=[],
        fetch_mode="summary",
        page_size=10,
        provider_filters={},
        provider_context={"liepin_native_filters_json": "{\"city\":\"上海\"}"},
    )

    try:
        asyncio.run(client.search(request, round_no=1, trace_id="trace-1"))
    except Exception:
        pass

    assert executor.native_filters == {"city": "上海"}
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_pi_agent_boundaries.py::test_liepin_search_cards_task_accepts_safe_native_filters tests/test_liepin_pi_worker_client.py::test_pi_worker_forwards_native_filters_to_executor -q
```

Expected: FAIL because native filter boundary fields are not defined or forwarded.

- [ ] **Step 3: Add PI contract models**

In `src/seektalent/providers/pi_agent/contracts.py`, add:

```python
class LiepinExperienceFilter(PiBoundaryModel):
    min_years: int | None = Field(default=None, alias="minYears", ge=0, le=50)
    max_years: int | None = Field(default=None, alias="maxYears", ge=0, le=50)


class LiepinAgeFilter(PiBoundaryModel):
    min: int | None = Field(default=None, ge=16, le=80)
    max: int | None = Field(default=None, ge=16, le=80)


class LiepinNativeFilters(PiBoundaryModel):
    city: NonEmptyStr | None = None
    experience: LiepinExperienceFilter | None = None
    age: LiepinAgeFilter | None = None
    partial_reason_codes: list[NonEmptyStr] = Field(default_factory=list, alias="partialReasonCodes")
```

Add to `LiepinSearchCardsTask`:

```python
native_filters: LiepinNativeFilters | None = None
```

- [ ] **Step 4: Forward native filters through worker and executor**

In `src/seektalent/providers/liepin/pi_worker_client.py`, parse `liepin_native_filters_json`:

```python
import json


def _native_filters_from_request(request: SearchRequest) -> dict[str, object] | None:
    raw = request.provider_context.get("liepin_native_filters_json")
    if not isinstance(raw, str) or not raw.strip():
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        return None
    return parsed
```

Pass it into `self._executor.search_cards(...)`:

```python
native_filters=_native_filters_from_request(request),
```

In `src/seektalent/providers/liepin/pi_executor.py`, add parameter:

```python
native_filters: Mapping[str, object] | None = None,
```

Add to task payload when present:

```python
if native_filters:
    task["native_filters"] = dict(native_filters)
```

- [ ] **Step 5: Forward native filters through OpenCLI CLI and extension**

In `src/seektalent/providers/pi_agent/opencli_browser_cli.py`, pass payload filters:

```python
native_filters = payload.get("nativeFilters") or payload.get("native_filters")
```

Then call:

```python
return runner.search_liepin_cards(
    source_run_id=str(payload.get("sourceRunId") or payload.get("source_run_id") or ""),
    query=str(payload.get("query") or ""),
    max_pages=_payload_int(payload, "maxPages", "max_pages", default=1),
    max_cards=_payload_int(payload, "maxCards", "max_cards", default=10),
    native_filters=native_filters if isinstance(native_filters, dict) else None,
)
```

In `src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts`, add:

```ts
nativeFilters: Type.Optional(Type.Object({}, { additionalProperties: true })),
```

to `seektalent_opencli_search_liepin_cards` parameters.

In `src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md`, add:

```markdown
- If `nativeFilters` is supplied, pass it unchanged to `seektalent_opencli_search_liepin_cards`.
  Do not invent filters from JD text or browser page text.
```

In `src/seektalent/providers/pi_agent/pi_external.py`, update the `liepin.search_cards` instruction text so the agent forwards `native_filters` from the input task as `nativeFilters` when present:

```python
"query, maxPages, maxCards, and nativeFilters from the input task when present, then return that tool result exactly as the final raw "
```

- [ ] **Step 6: Run boundary tests**

Run:

```bash
uv run pytest tests/test_pi_agent_boundaries.py tests/test_liepin_pi_worker_client.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/seektalent/providers/pi_agent/contracts.py src/seektalent/providers/liepin/pi_worker_client.py src/seektalent/providers/liepin/pi_executor.py src/seektalent/providers/pi_agent/opencli_browser_cli.py src/seektalent/providers/pi_agent/pi_external.py src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md tests/test_pi_agent_boundaries.py tests/test_liepin_pi_worker_client.py
git commit -m "feat: carry liepin native filters across pi boundary"
```

## Task 4: Apply Native Filters In OpenCLI Card Search

**Files:**
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
- Test: `tests/test_pi_opencli_browser.py`

- [ ] **Step 1: Write failing browser-ordering test**

Add to `tests/test_pi_opencli_browser.py`:

```python
def test_search_liepin_cards_applies_native_filters_before_reading_cards(tmp_path: Path) -> None:
    state_before = (
        "<span>包含全部关键词</span>\n"
        "  [25]<div />\n"
        "    [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = (
        "[41]<button><span>城市</span></button>\n"
        "[42]<button><span>工作经验</span></button>\n"
        "[43]<button><span>年龄</span></button>\n"
        "[44]<button><span>上海</span></button>\n"
        "[45]<button><span>3-5年</span></button>\n"
        "[46]<button><span>35岁以下</span></button>"
    )
    state_after_filters = (
        "已选 上海 3-5年 35岁以下\n"
        "王** 男 34岁 工作5年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "某数据公司 · 数据开发专家 2021.01-至今"
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): [state_before, state_after_search, state_after_filters],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--text", "上海"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--text", "3-5年"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--text", "35岁以下"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={
            "city": "上海",
            "experience": {"minYears": 3, "maxYears": 5},
            "age": {"max": 35},
        },
    )

    assert envelope["status"] == "succeeded"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {"action_kind": "apply_native_filter", "filter": "city", "value": "上海", "ok": True} in trace["events"]
    click_search_index = commands.calls.index(
        ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索")
    )
    click_city_index = commands.calls.index(
        ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--text", "上海")
    )
    assert click_search_index < click_city_index


def test_search_liepin_cards_records_filter_failure_without_blocking_cards(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = "王** 男 34岁 工作5年 硕士 上海\n求职期望：上海 数据开发专家"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", "https://h.liepin.com/search/getConditionItem#session"): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-1"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): [state_before, state_after_search, state_after_search],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--text", "上海"): subprocess.CalledProcessError(1, "opencli"),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": "上海"},
    )

    assert envelope["status"] == "succeeded"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "apply_native_filter",
        "filter": "city",
        "value": "上海",
        "ok": False,
        "safe_reason_code": "liepin_opencli_status_unavailable",
    } in trace["events"]
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/test_pi_opencli_browser.py::test_search_liepin_cards_applies_native_filters_before_reading_cards -q
```

Expected: FAIL because `search_liepin_cards` does not accept or apply `native_filters`.

- [ ] **Step 3: Add native filter application helpers**

In `OpenCliBrowserRunner.search_liepin_cards`, add parameter:

```python
native_filters: Mapping[str, object] | None = None,
```

After the keyword search click and initial result `state()`, before `extract_liepin_card_summaries(...)`, call:

```python
if native_filters:
    final_state = self._apply_liepin_native_filters(
        native_filters=native_filters,
        current_state=final_state,
        events=events,
    )
```

Add these helpers on `OpenCliBrowserRunner` and keep `_liepin_filter_labels(...)`, `_experience_label(...)`, `_age_label(...)`, and `_validate_native_filter_label(...)` as module-level helpers:

```python
def _apply_liepin_native_filters(
    self,
    *,
    native_filters: Mapping[str, object],
    current_state: OpenCliBrowserResult,
    events: list[dict[str, object]],
) -> OpenCliBrowserResult:
    del current_state
    for filter_name, label in _liepin_filter_labels(native_filters):
        try:
            self._click_native_filter_option(label)
            events.append({"action_kind": "apply_native_filter", "filter": filter_name, "value": label, "ok": True})
            self.wait_time(seconds=1)
        except OpenCliBrowserError as exc:
            events.append(
                {
                    "action_kind": "apply_native_filter",
                    "filter": filter_name,
                    "value": label,
                    "ok": False,
                    "safe_reason_code": exc.safe_reason_code,
                }
            )
    for filter_name in _skipped_liepin_filter_names(native_filters):
        events.append({"action_kind": "skip_native_filter", "filter": filter_name, "ok": True})
    refreshed = self.state()
    events.append({"action_kind": "observe_after_native_filters", "route_kind": "search", "ok": refreshed.ok})
    return refreshed


def _click_native_filter_option(self, label: str) -> None:
    _validate_native_filter_label(label)
    self._run_browser_command("click", ("--role", "button", "--text", label))
    self._touch_lease()


def _liepin_filter_labels(native_filters: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    labels: list[tuple[str, str]] = []
    city = native_filters.get("city")
    if isinstance(city, str) and city.strip():
        labels.append(("city", city.strip()))
    experience = native_filters.get("experience")
    if isinstance(experience, Mapping):
        label = _experience_label(experience)
        if label is not None:
            labels.append(("experience", label))
    age = native_filters.get("age")
    if isinstance(age, Mapping):
        label = _age_label(age)
        if label is not None:
            labels.append(("age", label))
    return tuple(labels)


def _skipped_liepin_filter_names(native_filters: Mapping[str, object]) -> tuple[str, ...]:
    known = {"city", "experience", "age", "partialReasonCodes", "sourceTarget"}
    return tuple(sorted(str(key) for key in native_filters if str(key) not in known))


def _experience_label(experience: Mapping[str, object]) -> str | None:
    min_years = experience.get("minYears")
    max_years = experience.get("maxYears")
    if isinstance(min_years, int) and isinstance(max_years, int):
        return f"{min_years}-{max_years}年"
    if isinstance(min_years, int):
        return f"{min_years}年以上"
    if isinstance(max_years, int):
        return f"{max_years}年以下"
    return None


def _age_label(age: Mapping[str, object]) -> str | None:
    min_age = age.get("min")
    max_age = age.get("max")
    if isinstance(min_age, int) and isinstance(max_age, int):
        return f"{min_age}-{max_age}岁"
    if isinstance(max_age, int):
        return f"{max_age}岁以下"
    if isinstance(min_age, int):
        return f"{min_age}岁以上"
    return None


def _validate_native_filter_label(label: str) -> None:
    normalized = label.strip()
    if not normalized or len(normalized) > 32:
        raise OpenCliBrowserError("liepin_opencli_forbidden_command")
    forbidden = ("cookie", "Authorization", "Bearer", "storage", "\n", "\r", "\x00")
    if any(fragment in normalized for fragment in forbidden):
        raise OpenCliBrowserError("liepin_opencli_forbidden_command")
```

Do not route native filter labels through `click(target=...)`; the generic click policy intentionally only allows search/next controls. Native filter clicking must stay in this dedicated helper so only compiler-produced safe labels can reach `--role button --text`.

If a specific label click fails because the provider UI label differs, preserve source-scoped degradation by recording the failed filter action and continuing to extract cards from the current result state.

- [ ] **Step 4: Run browser tests**

Run:

```bash
uv run pytest tests/test_pi_opencli_browser.py::test_search_liepin_cards_applies_native_filters_before_reading_cards tests/test_pi_opencli_browser.py::test_search_liepin_cards_records_filter_failure_without_blocking_cards tests/test_pi_opencli_browser.py::test_search_liepin_cards_runs_bounded_opencli_flow_and_writes_valid_artifacts -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/providers/pi_agent/opencli_browser.py tests/test_pi_opencli_browser.py
git commit -m "feat: apply liepin native filters in opencli card search"
```

## Task 5: Add Business-Safe Filter Coverage Events

**Files:**
- Modify: `src/seektalent/runtime/public_events.py`
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Test: `tests/test_runtime_multi_source_round_dispatch.py`

- [ ] **Step 1: Write failing public reason test**

Add to `tests/test_runtime_multi_source_round_dispatch.py`:

```python
import json


def test_liepin_filter_partial_reason_is_public_safe() -> None:
    from seektalent.runtime.public_events import public_source_reason_code

    assert public_source_reason_code("source_location_filter_partial") == "source_filter_partial"
    assert public_source_reason_code("source_filter_applied") == "source_filter_applied"


def test_public_runtime_filter_payload_does_not_expose_browser_terms() -> None:
    from seektalent.runtime.public_events import make_runtime_public_event

    event = make_runtime_public_event(
        runtime_run_id="run-1",
        stage="source_result",
        event_seq=1,
        round_no=1,
        source_kind="liepin",
        status="partial",
        counts={"roundReturned": 1},
        safe_reason_code="source_location_filter_partial",
    )
    encoded = json.dumps(event, ensure_ascii=False, sort_keys=True)

    forbidden = ("OpenCLI", "DokoBot", "mcp", "pi_agent", "cookie", "authorization", "raw_provider_payload", "raw_resume")
    assert all(term.lower() not in encoded.lower() for term in forbidden)
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/test_runtime_multi_source_round_dispatch.py::test_liepin_filter_partial_reason_is_public_safe -q
```

Expected: FAIL until reason mapping exists.

- [ ] **Step 3: Add safe reason mapping**

In `src/seektalent/runtime/public_events.py`, add these values to `PUBLIC_SOURCE_REASON_CODES`:

```python
"source_filter_applied",
"source_filter_partial",
"source_filter_unavailable",
```

Add these entries to `_PUBLIC_REASON_MAP`:

```python
"source_location_filter_partial": "source_filter_partial",
"source_age_filter_unsupported": "source_filter_unavailable",
"source_location_filter_unsupported": "source_filter_unavailable",
"source_filter_unsupported": "source_filter_unavailable",
"source_filter_applied": "source_filter_applied",
```

- [ ] **Step 4: Run public reason tests**

Run:

```bash
uv run pytest tests/test_runtime_multi_source_round_dispatch.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/runtime/public_events.py src/seektalent/providers/liepin/runtime_lane.py tests/test_runtime_multi_source_round_dispatch.py
git commit -m "feat: expose safe liepin filter coverage reasons"
```

## Task 6: Full Local Verification

**Files:**
- No new source files unless prior tasks expose a test-only issue.

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
uv run pytest tests/test_liepin_native_filter_compiler.py tests/test_liepin_source_compiler.py tests/test_runtime_source_adapter_boundary.py tests/test_runtime_multi_source_round_dispatch.py tests/test_liepin_pi_worker_client.py tests/test_pi_agent_boundaries.py tests/test_pi_opencli_browser.py -q
```

Expected: PASS.

- [ ] **Step 2: Run lint and type checks**

Run:

```bash
uv run ruff check src/seektalent/providers/liepin/filter_compiler.py src/seektalent/providers/liepin/source_compiler.py src/seektalent/providers/liepin/runtime_lane.py src/seektalent/providers/liepin/pi_worker_client.py src/seektalent/providers/liepin/pi_executor.py src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/providers/pi_agent/opencli_browser_cli.py src/seektalent/providers/pi_agent/pi_external.py src/seektalent/providers/pi_agent/contracts.py tests/test_liepin_native_filter_compiler.py tests/test_liepin_source_compiler.py tests/test_liepin_runtime_source_lane.py tests/test_liepin_pi_worker_client.py tests/test_pi_agent_boundaries.py tests/test_pi_opencli_browser.py
uv run ty check src/seektalent/providers/liepin/filter_compiler.py src/seektalent/providers/liepin/source_compiler.py src/seektalent/providers/liepin/runtime_lane.py src/seektalent/providers/liepin/pi_worker_client.py src/seektalent/providers/liepin/pi_executor.py src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/providers/pi_agent/opencli_browser_cli.py src/seektalent/providers/pi_agent/pi_external.py src/seektalent/providers/pi_agent/contracts.py
```

Expected: both commands PASS.

- [ ] **Step 3: Run package and frontend builds**

Run:

```bash
uv build
cd apps/web-svelte && bun run build
```

Expected: both commands PASS. Existing Vite chunk-size warnings are acceptable only if there are no errors.

- [ ] **Step 4: Commit verification-only fixes if needed**

If Step 1, 2, or 3 requires code fixes, commit them:

```bash
git add src/seektalent/providers/liepin/filter_compiler.py src/seektalent/providers/liepin/source_compiler.py src/seektalent/providers/liepin/runtime_lane.py src/seektalent/providers/liepin/pi_worker_client.py src/seektalent/providers/liepin/pi_executor.py src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/providers/pi_agent/opencli_browser_cli.py src/seektalent/providers/pi_agent/pi_external.py src/seektalent/providers/pi_agent/contracts.py src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md src/seektalent/runtime/public_events.py tests/test_liepin_native_filter_compiler.py tests/test_liepin_source_compiler.py tests/test_liepin_runtime_source_lane.py tests/test_liepin_pi_worker_client.py tests/test_pi_agent_boundaries.py tests/test_pi_opencli_browser.py tests/test_runtime_multi_source_round_dispatch.py
git commit -m "fix: stabilize liepin native filter verification"
```

## Task 7: Real Chrome QA With Filter Verification

**Files:**
- No source file changes expected.
- Artifacts: screenshots under `/tmp/seektalent-liepin-native-filter-qa/`.

- [ ] **Step 1: Start the local Workbench**

Run:

```bash
scripts/start-dev-workbench.sh
```

Expected: backend and Svelte dev URLs are printed; no startup errors.

- [ ] **Step 2: Open Chrome, not the Codex in-app browser**

Use the Chrome automation skill or direct Chrome operation. Navigate Chrome to the Svelte dev URL printed by the server.

Expected: Workbench sessions page loads with the user's real saved login state. Do not paste or store credentials in files.

- [ ] **Step 3: Create a real session with prior session data**

Use the prior real "数据开发专家" session input. Do not invent new JD data.

Capture screenshot:

```text
/tmp/seektalent-liepin-native-filter-qa/01-form-filled.png
```

Expected: form fields match the prior real session input.

- [ ] **Step 4: Start the agent and capture regular screenshots**

Start the agent like a human user. Capture screenshots at these checkpoints:

```text
/tmp/seektalent-liepin-native-filter-qa/02-agent-started.png
/tmp/seektalent-liepin-native-filter-qa/03-sourcing-started.png
/tmp/seektalent-liepin-native-filter-qa/04-liepin-filter-applied.png
/tmp/seektalent-liepin-native-filter-qa/05-mid-run.png
/tmp/seektalent-liepin-native-filter-qa/06-final.png
```

Expected:

- Strategy graph starts from active Runtime state, not pre-finished downstream nodes.
- CTS and Liepin both appear as Runtime-owned source branches.
- Liepin source card does not claim unsupported filters for city, experience, or age when native filter actions were attempted.

- [ ] **Step 5: Inspect Liepin browser state during search**

During the Liepin search, capture the page state or screenshot after native filter application.

Expected one of these outcomes:

- Preferred: visible filter chip or equivalent page state shows city and range filters such as `上海`, `3-5年`, `35岁以下`.
- Acceptable source-scoped degradation: action trace records attempted filters and a safe filter partial/unavailable reason, while CTS continues and Runtime completes.

- [ ] **Step 6: Verify database/event evidence**

Query the local Workbench database for the new session:

```bash
export SEEKTALENT_QA_SESSION_ID="session id copied from the Workbench URL after creating the QA session"
python - <<'PY'
import json
import sqlite3
import os
from pathlib import Path

db = Path(".seektalent/workbench.sqlite3")
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
session_id = os.environ["SEEKTALENT_QA_SESSION_ID"]

events = conn.execute(
    "select event_type, payload_json from session_events where session_id = ? order by created_at",
    (session_id,),
).fetchall()
print("events", len(events))
for row in events:
    payload = json.loads(row["payload_json"] or "{}")
    if row["event_type"] in {"runtime_round_source_result", "source_run_completed", "runtime_finalization_completed"}:
        print(row["event_type"], json.dumps(payload, ensure_ascii=False, sort_keys=True)[:1200])

final_rows = conn.execute(
    "select count(*) as count from runtime_candidate_identity_snapshots where session_id = ?",
    (session_id,),
).fetchone()
print("identity_snapshots", final_rows["count"])
PY
```

Expected:

- Liepin source has source result events.
- Runtime finalization completed.
- Final identity snapshot count is 10.
- Public payload output does not contain `OpenCLI`, `DokoBot`, `mcp`, `pi_agent`, `cookie`, `authorization`, `raw_provider_payload`, `raw_resume`, or local paths.

- [ ] **Step 7: Clean Chrome and OpenCLI state**

Close the Chrome tab/windows opened for QA. Then run:

```bash
env \
  NODE_PATH="$PWD/apps/web-svelte/node_modules" \
  PYTHONPATH="$PWD/src" \
  SEEKTALENT_LIEPIN_OPENCLI_COMMAND="$PWD/apps/web-svelte/node_modules/.bin/opencli" \
  SEEKTALENT_LIEPIN_OPENCLI_LEASE_DIR="$PWD/.seektalent/opencli_leases" \
  uv run python -m seektalent.providers.pi_agent.opencli_browser_cli cleanup_orphaned_tabs \
    <<< '{"force":true}'
```

Expected:

- Cleanup returns `ok: true`.
- No extra SeekTalent/OpenCLI test tabs remain in Chrome.
- The dev server is stopped after QA.

- [ ] **Step 8: Record QA summary in final response**

Report:

- Created session id.
- Screenshot directory.
- Whether Liepin native filters were visibly applied or source-scoped degraded.
- CTS and Liepin source completion states.
- Final Top 10 count.
- Commands run for test/build verification.
- Cleanup result.

Do not include credentials, cookies, raw browser state, or full JD text in the final response.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | not run | This is a focused follow-up to the accepted Runtime/Core + source adapter direction. |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | not run | Outside voice not run in this fw-plan-review pass. |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 3 | cleared | No open P0/P1 engineering blockers. Task 2 now includes a runtime-lane multi-target stop test, and the balanced-all fixture matches Runtime semantics. |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | skipped | No Workbench app layout or frontend interaction design change; real Chrome QA remains required. |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | not run | Not needed for this provider adapter plan. |

- **RESOLVED:** Task 2 now adds `test_liepin_logical_query_bundle_executes_filter_targets_until_provider_scan_limit`, which verifies target-indexed lane ids, native filter provider context, unique candidate accumulation, and stop-before-extra-target behavior.
- **RESOLVED:** Task 1's balanced-all fixture now uses `priority_order=()`.
- **VERDICT:** CLEARED FOR `fw-build`.
