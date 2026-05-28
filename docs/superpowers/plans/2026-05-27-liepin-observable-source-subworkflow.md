# Liepin Observable Source Subworkflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make deterministic Liepin OpenCLI retrieval visible as a structured runtime source subworkflow without implementing durable checkpoint/resume.

**Architecture:** Keep Liepin as one runtime source lane, but add typed substep events inside that lane. OpenCLI remains responsible for browser actions; a new workflow mapper converts OpenCLI action trace entries into public-safe `RuntimeSourceLaneEvent` substep events that are persisted and rendered in source node details. Durable replay, step-level runtime scheduling, and checkpoint recovery remain out of scope.

**Tech Stack:** Python 3.12, dataclasses, Pydantic models, pytest, existing OpenCLI runner, existing runtime source lane event storage, Svelte workbench runtime graph.

**Superseding note:** The follow-up OpenCLI execution contract intentionally leaves workflow-created Liepin detail tabs open. Any `cleanup_detail_tabs` references in this historical plan are legacy/reserved vocabulary, not current behavior. Safe source-run-owned detail-tab closing is deferred to the OpenCLI fork work tracked in `TODOS.md`.

---

## Spec Link

This plan implements:

`docs/superpowers/specs/2026-05-27-liepin-observable-source-subworkflow-design.md`

## Execution Notes

- Do not change controller query/filter ownership.
- Do not change CTS behavior.
- Do not change Liepin budgets or candidate selection semantics.
- Do not expose raw or normalized resume text in subworkflow events.
- Do not implement durable checkpoint/resume in this plan.
- Keep all changes scoped to runtime source lane event metadata, OpenCLI action trace projection, and UI detail rendering.
- The worktree is already dirty. Stage only files touched while implementing this plan.

## File Map

- Modify: `src/seektalent/runtime/source_lanes.py`
  - Extend runtime source lane event schema with workflow step fields and generic step event types.
- Create: `src/seektalent/providers/liepin/opencli_workflow.py`
  - Own public step names, safe action trace sanitization, and conversion to `RuntimeSourceLaneEvent`.
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
  - Add action trace entries for missing workflow boundaries and return `workflow_steps` in the final envelope.
- Modify: `src/seektalent/providers/liepin/opencli_retriever.py`
  - Preserve `workflowSteps` and `actionTraceRef` in the worker response request payload.
- Modify: `src/seektalent/providers/liepin/client.py`
  - Allow safe workflow fields through `_safe_search_request_payload()`.
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
  - Append workflow step events to `RuntimeSourceLaneResult.events`.
- Modify: `src/seektalent_ui/models.py`
  - Add a public response model for source workflow steps and add it to runtime source state and graph node details where needed.
- Modify: `src/seektalent_ui/workbench_routes.py`
  - Include latest safe step data in runtime source state responses.
- Modify: `src/seektalent_ui/runtime_graph.py`
  - Add a source-node detail section showing the Liepin step timeline.
- Modify: `apps/web-svelte/src/lib/components/NodeDetailPanel.svelte`
  - Render existing list/fact sections; no new component is required unless tests show the section is unreadable.
- Test: `tests/test_liepin_opencli_workflow.py`
- Test: `tests/test_pi_opencli_browser.py`
- Test: `tests/test_liepin_opencli_retriever.py`
- Test: `tests/test_liepin_runtime_source_lane.py`
- Test: `tests/test_workbench_runtime_graph.py`
- Test: `tests/test_workbench_api.py`
- Test: `apps/web-svelte/src/lib/components/NodeDetailPanel.test.ts` or existing runtime graph component tests if the repo already covers section rendering there.

---

### Task 1: Extend Runtime Source Lane Event Metadata

**Files:**
- Modify: `src/seektalent/runtime/source_lanes.py`
- Test: `tests/test_runtime_source_lanes.py`

- [ ] **Step 1: Write the failing event serialization test**

Append this test to `tests/test_runtime_source_lanes.py`:

```python
from seektalent.runtime.source_lanes import RuntimeSourceLaneEvent


def test_runtime_source_lane_event_serializes_safe_workflow_step_metadata() -> None:
    event = RuntimeSourceLaneEvent(
        schema_version="runtime_source_lane_event_v1",
        runtime_run_id="run-1",
        source_plan_id="run-1:source:liepin",
        source_lane_run_id="run-1:source:liepin:round:1:lane:1",
        source="liepin",
        attempt=1,
        event_seq=7,
        event_type="source_workflow_step_completed",
        status="completed",
        step_name="capture_detail",
        safe_counts={"details_opened": 1, "resumes_returned": 1},
        safe_metadata={"rank": 1, "open_mode": "cached_url", "url": "https://h.liepin.com/private"},
        artifact_refs=("artifact://protected/liepin-opencli/raw/run-1/1.json",),
    )

    payload = event.to_public_payload()

    assert payload["event_type"] == "source_workflow_step_completed"
    assert payload["step_name"] == "capture_detail"
    assert payload["safe_counts"] == {"details_opened": 1, "resumes_returned": 1}
    assert payload["safe_metadata"] == {"rank": 1, "open_mode": "cached_url"}
    assert payload["artifact_refs"] == ["artifact://protected/liepin-opencli/raw/run-1/1.json"]
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
.venv/bin/pytest -q tests/test_runtime_source_lanes.py::test_runtime_source_lane_event_serializes_safe_workflow_step_metadata
```

Expected:

```text
TypeError: RuntimeSourceLaneEvent.__init__() got an unexpected keyword argument 'step_name'
```

- [ ] **Step 3: Extend the event model**

In `src/seektalent/runtime/source_lanes.py`, update `RuntimeSourceLaneEventType` and `RuntimeSourceLaneEvent`:

```python
RuntimeSourceLaneEventType = Literal[
    "source_plan_created",
    "source_lane_started",
    "source_lane_completed",
    "source_lane_blocked",
    "source_lane_partial",
    "source_lane_failed",
    "source_lane_cancelled",
    "source_workflow_step_started",
    "source_workflow_step_completed",
    "source_workflow_step_failed",
    "detail_recommended",
    "detail_approved",
    "detail_leased",
    "detail_completed",
    "detail_blocked",
]

_SAFE_METADATA_KEYS = {
    "rank",
    "visible_cards",
    "cards_seen",
    "target_resumes",
    "resumes_returned",
    "closed_tabs",
    "open_mode",
}


@dataclass(frozen=True, kw_only=True)
class RuntimeSourceLaneEvent:
    schema_version: Literal["runtime_source_lane_event_v1"]
    runtime_run_id: str
    source_plan_id: str
    source_lane_run_id: str
    source: SourceKind
    attempt: int
    event_seq: int
    event_type: RuntimeSourceLaneEventType
    status: RuntimeSourceLaneStatus | None = None
    safe_counts: Mapping[str, int] = field(default_factory=dict)
    safe_reason_code: str | None = None
    artifact_refs: tuple[str, ...] = ()
    step_name: str | None = None
    safe_metadata: Mapping[str, str | int | bool | None] = field(default_factory=dict)

    def to_public_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "runtime_run_id": self.runtime_run_id,
            "source_plan_id": self.source_plan_id,
            "source_lane_run_id": self.source_lane_run_id,
            "source": self.source,
            "attempt": self.attempt,
            "event_seq": self.event_seq,
            "event_type": self.event_type,
            "status": self.status,
            "safe_counts": _sanitize_count_mapping(self.safe_counts),
            "safe_reason_code": _sanitize_reason_code(self.safe_reason_code),
            "artifact_refs": [ref for ref in (_sanitize_artifact_ref(ref) for ref in self.artifact_refs) if ref],
            "step_name": _sanitize_step_name(self.step_name),
            "safe_metadata": _sanitize_safe_metadata(self.safe_metadata),
        }
```

Add these helper functions near the existing sanitizers:

```python
def _sanitize_step_name(value: str | None) -> str | None:
    if value is None:
        return None
    text = "_".join(part for part in value.strip().casefold().split("_") if part)
    allowed = {
        "prepare_search",
        "apply_filters",
        "submit_search",
        "observe_cards",
        "cache_detail_urls",
        "open_detail",
        "capture_detail",
        "cleanup_detail_tabs",
        "finalize",
    }
    return text if text in allowed else None


def _sanitize_safe_metadata(values: Mapping[str, str | int | bool | None]) -> dict[str, str | int | bool]:
    result: dict[str, str | int | bool] = {}
    for key, value in values.items():
        if key not in _SAFE_METADATA_KEYS or value is None:
            continue
        if isinstance(value, bool):
            result[key] = value
            continue
        if isinstance(value, int):
            result[key] = value
            continue
        if isinstance(value, str):
            clean = " ".join(value.split())
            lowered = clean.casefold()
            if clean and len(clean) <= 80 and not any(token in lowered for token in _SENSITIVE_KEY_TOKENS):
                result[key] = clean
    return result
```

- [ ] **Step 4: Run the focused test and verify it passes**

Run:

```bash
.venv/bin/pytest -q tests/test_runtime_source_lanes.py::test_runtime_source_lane_event_serializes_safe_workflow_step_metadata
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/runtime/source_lanes.py tests/test_runtime_source_lanes.py
git commit -m "feat: add source workflow step event metadata"
```

---

### Task 2: Add Liepin OpenCLI Workflow Event Mapper

**Files:**
- Create: `src/seektalent/providers/liepin/opencli_workflow.py`
- Create: `tests/test_liepin_opencli_workflow.py`

- [ ] **Step 1: Write failing mapper tests**

Create `tests/test_liepin_opencli_workflow.py`:

```python
from __future__ import annotations

from seektalent.providers.liepin.opencli_workflow import workflow_steps_from_action_events


def test_workflow_steps_from_action_events_maps_successful_detail_flow() -> None:
    steps = workflow_steps_from_action_events(
        [
            {"action_kind": "visible_cards_observed", "visible_cards": 6, "cards_seen": 6, "target_resumes": 2},
            {"action_kind": "detail_urls_cached", "cached_detail_urls": 6},
            {"action_kind": "detail_candidate_selected", "rank": 1, "ref": "70"},
            {"action_kind": "open_detail_succeeded", "rank": 1, "open_mode": "cached_url"},
            {"action_kind": "capture_detail_succeeded", "rank": 1},
            {"action_kind": "cleanup_detail_tabs_after_capture", "ok": True, "closed_tabs": 1},
        ],
        final_status="succeeded",
        resumes_returned=1,
        action_trace_ref="artifact://protected/liepin-opencli/action-traces/run-1.json",
    )

    assert [step["step_name"] for step in steps] == [
        "observe_cards",
        "cache_detail_urls",
        "open_detail",
        "open_detail",
        "capture_detail",
        "cleanup_detail_tabs",
        "finalize",
    ]
    assert steps[0]["event_type"] == "source_workflow_step_completed"
    assert steps[0]["safe_counts"] == {"visible_cards": 6, "cards_seen": 6, "target_resumes": 2}
    assert steps[3]["safe_metadata"] == {"rank": 1, "open_mode": "cached_url"}
    assert steps[-1]["step_name"] == "finalize"
    assert steps[-1]["status"] == "completed"
    assert steps[-1]["artifact_refs"] == ["artifact://protected/liepin-opencli/action-traces/run-1.json"]


def test_workflow_steps_from_action_events_sanitizes_private_fields() -> None:
    steps = workflow_steps_from_action_events(
        [
            {
                "action_kind": "open_detail_failed",
                "rank": 1,
                "safe_reason_code": "liepin_opencli_detail_not_opened",
                "url": "https://h.liepin.com/resume/showresumedetail/private",
                "cookie": "secret",
            }
        ],
        final_status="partial",
        resumes_returned=0,
        action_trace_ref="artifact://protected/liepin-opencli/action-traces/run-2.json",
    )

    assert steps[0] == {
        "event_type": "source_workflow_step_failed",
        "step_name": "open_detail",
        "status": "failed",
        "safe_reason_code": "liepin_opencli_detail_not_opened",
        "safe_counts": {},
        "safe_metadata": {"rank": 1},
        "artifact_refs": [],
    }
```

- [ ] **Step 2: Run the tests and verify they fail because the module does not exist**

Run:

```bash
.venv/bin/pytest -q tests/test_liepin_opencli_workflow.py
```

Expected:

```text
ModuleNotFoundError: No module named 'seektalent.providers.liepin.opencli_workflow'
```

- [ ] **Step 3: Implement the mapper**

Create `src/seektalent/providers/liepin/opencli_workflow.py`:

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence


ACTION_TO_STEP_EVENT = {
    "search_cards_started": ("prepare_search", "source_workflow_step_completed", "completed"),
    "apply_filters_started": ("apply_filters", "source_workflow_step_started", "running"),
    "apply_filters_completed": ("apply_filters", "source_workflow_step_completed", "completed"),
    "search_submitted": ("submit_search", "source_workflow_step_completed", "completed"),
    "visible_cards_observed": ("observe_cards", "source_workflow_step_completed", "completed"),
    "detail_urls_cached": ("cache_detail_urls", "source_workflow_step_completed", "completed"),
    "detail_candidate_selected": ("open_detail", "source_workflow_step_started", "running"),
    "open_detail_succeeded": ("open_detail", "source_workflow_step_completed", "completed"),
    "open_detail_failed": ("open_detail", "source_workflow_step_failed", "failed"),
    "observe_detail": ("capture_detail", "source_workflow_step_completed", "completed"),
    "capture_detail_succeeded": ("capture_detail", "source_workflow_step_completed", "completed"),
    "capture_detail_failed": ("capture_detail", "source_workflow_step_failed", "failed"),
    "cleanup_detail_tabs_after_capture": ("cleanup_detail_tabs", "source_workflow_step_completed", "completed"),
    "visible_cards_refresh_failed_after_cleanup": ("observe_cards", "source_workflow_step_failed", "failed"),
    "detail_target_not_met": ("finalize", "source_workflow_step_failed", "failed"),
}

COUNT_KEYS = {
    "visible_cards",
    "cards_seen",
    "target_resumes",
    "resumes_returned",
    "cached_detail_urls",
    "closed_tabs",
}
METADATA_KEYS = {"rank", "open_mode"}


def workflow_steps_from_action_events(
    events: Sequence[Mapping[str, object]],
    *,
    final_status: str,
    resumes_returned: int,
    action_trace_ref: str | None,
) -> list[dict[str, object]]:
    steps: list[dict[str, object]] = []
    for event in events:
        action_kind = str(event.get("action_kind") or "")
        mapped = ACTION_TO_STEP_EVENT.get(action_kind)
        if mapped is None:
            continue
        step_name, event_type, status = mapped
        if action_kind == "cleanup_detail_tabs_after_capture" and event.get("ok") is False:
            event_type = "source_workflow_step_failed"
            status = "failed"
        step = {
            "event_type": event_type,
            "step_name": step_name,
            "status": status,
            "safe_counts": _safe_counts(event),
            "safe_metadata": _safe_metadata(event),
            "artifact_refs": [],
        }
        reason = _safe_reason_code(event.get("safe_reason_code"))
        if reason is not None:
            step["safe_reason_code"] = reason
        steps.append(step)
    steps.append(
        {
            "event_type": "source_workflow_step_completed" if final_status == "succeeded" else "source_workflow_step_failed",
            "step_name": "finalize",
            "status": "completed" if final_status == "succeeded" else "failed",
            "safe_counts": {"resumes_returned": resumes_returned},
            "safe_metadata": {},
            "artifact_refs": [action_trace_ref] if _safe_artifact_ref(action_trace_ref) else [],
        }
    )
    return steps


def _safe_counts(event: Mapping[str, object]) -> dict[str, int]:
    result: dict[str, int] = {}
    for key in COUNT_KEYS:
        value = event.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            result[key] = value
    return result


def _safe_metadata(event: Mapping[str, object]) -> dict[str, str | int]:
    result: dict[str, str | int] = {}
    for key in METADATA_KEYS:
        value = event.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            result[key] = value
        elif isinstance(value, str) and value and len(value) <= 80:
            result[key] = value
    return result


def _safe_reason_code(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if not all(part.replace("_", "").isalnum() for part in value.split("_")):
        return None
    return value[:128]


def _safe_artifact_ref(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value if value.startswith("artifact://protected/") and ".." not in value else None
```

- [ ] **Step 4: Run mapper tests and verify they pass**

Run:

```bash
.venv/bin/pytest -q tests/test_liepin_opencli_workflow.py
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/providers/liepin/opencli_workflow.py tests/test_liepin_opencli_workflow.py
git commit -m "feat: map Liepin OpenCLI actions to workflow steps"
```

---

### Task 3: Emit Workflow Steps From OpenCLI Runner Envelopes

**Files:**
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
- Modify: `tests/test_pi_opencli_browser.py`

- [ ] **Step 1: Write failing OpenCLI envelope test**

Add this assertion to `tests/test_pi_opencli_browser.py::test_search_liepin_resumes_leaves_detail_tabs_open_and_restores_search_for_next_capture` after the existing `resumes_returned` assertion:

```python
    workflow_steps = envelope["workflow_steps"]
    assert not any(step["step_name"] == "cleanup_detail_tabs" for step in workflow_steps)
    assert any(step["step_name"] == "finalize" and step["status"] == "completed" for step in workflow_steps)
```

Add this assertion to `tests/test_pi_opencli_browser.py::test_finalize_liepin_resumes_marks_partial_when_target_is_not_met` after the existing `resumes_returned` assertion:

```python
    assert finalized["workflow_steps"][-1]["step_name"] == "finalize"
    assert finalized["workflow_steps"][-1]["status"] == "failed"
    assert finalized["workflow_steps"][-1]["safe_counts"] == {"resumes_returned": 1}
```

- [ ] **Step 2: Run the focused tests and verify they fail with missing `workflow_steps`**

Run:

```bash
.venv/bin/pytest -q tests/test_pi_opencli_browser.py::test_search_liepin_resumes_leaves_detail_tabs_open_and_restores_search_for_next_capture tests/test_pi_opencli_browser.py::test_finalize_liepin_resumes_marks_partial_when_target_is_not_met
```

Expected:

```text
KeyError: 'workflow_steps'
```

- [ ] **Step 3: Add workflow step generation to OpenCLI browser envelopes**

In `src/seektalent/providers/pi_agent/opencli_browser.py`, import the mapper:

```python
from seektalent.providers.liepin.opencli_workflow import workflow_steps_from_action_events
```

Inside `finalize_liepin_resumes()`, after `action_trace_ref` is created and before returning the final envelope, compute:

```python
workflow_steps = workflow_steps_from_action_events(
    events,
    final_status=status,
    resumes_returned=len(resumes),
    action_trace_ref=action_trace_ref,
)
```

Include this field in the returned envelope:

```python
"workflow_steps": workflow_steps,
```

In blocked envelope builders that write an action trace, also include:

```python
"workflow_steps": workflow_steps_from_action_events(
    self._read_agent_events(safe_run_id),
    final_status="blocked",
    resumes_returned=0,
    action_trace_ref=action_trace_ref,
),
```

- [ ] **Step 4: Add missing action trace boundaries**

In `search_liepin_resumes()`, after visible cards are observed and `remember_detail_urls(card_items)` has run, append:

```python
self._append_agent_event(
    source_run_id,
    {
        "action_kind": "detail_urls_cached",
        "route_kind": "search",
        "ok": True,
        "cached_detail_urls": len(detail_urls_by_rank),
    },
)
```

In `search_liepin_cards()`, add or reuse events so the action trace includes `search_cards_started`, `apply_filters_started`, `apply_filters_completed`, and `search_submitted`. If `apply_liepin_native_filters()` already appends events, keep existing event names and add only missing events at the call site.

- [ ] **Step 5: Run focused OpenCLI tests**

Run:

```bash
.venv/bin/pytest -q tests/test_pi_opencli_browser.py::test_search_liepin_resumes_leaves_detail_tabs_open_and_restores_search_for_next_capture tests/test_pi_opencli_browser.py::test_finalize_liepin_resumes_marks_partial_when_target_is_not_met
```

Expected:

```text
2 passed
```

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/providers/pi_agent/opencli_browser.py tests/test_pi_opencli_browser.py
git commit -m "feat: include Liepin OpenCLI workflow steps in envelopes"
```

---

### Task 4: Propagate Workflow Steps Into Runtime Source Lane Events

**Files:**
- Modify: `src/seektalent/providers/liepin/opencli_retriever.py`
- Modify: `src/seektalent/providers/liepin/client.py`
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Test: `tests/test_liepin_opencli_retriever.py`
- Test: `tests/test_liepin_runtime_source_lane.py`

- [ ] **Step 1: Write failing retriever propagation test**

Add to `tests/test_liepin_opencli_retriever.py`:

```python
def test_opencli_retriever_preserves_workflow_steps_in_request_payload() -> None:
    runner = FakeOpenCliResumeRunner(
        envelope={
            "status": "succeeded",
            "resumes": [],
            "cards_seen": 2,
            "action_trace_ref": "artifact://protected/liepin-opencli/action-traces/run-1.json",
            "workflow_steps": [
                {
                    "event_type": "source_workflow_step_completed",
                    "step_name": "observe_cards",
                    "status": "completed",
                    "safe_counts": {"visible_cards": 2},
                    "safe_metadata": {},
                    "artifact_refs": [],
                }
            ],
        }
    )
    retriever = LiepinOpenCliResumeRetriever(runner=runner)

    response = retriever.search_resumes(
        LiepinOpenCliResumeRequest(
            source_run_id="run-1",
            keyword_query="数据开发 Python",
            query_terms=["数据开发", "Python"],
            target_resumes=2,
            max_cards=6,
            max_pages=1,
            requirement_sheet={},
        )
    )

    assert response.request_payload["actionTraceRef"] == "artifact://protected/liepin-opencli/action-traces/run-1.json"
    assert response.request_payload["workflowSteps"][0]["step_name"] == "observe_cards"
```

If `FakeOpenCliResumeRunner` does not exist yet, add this minimal fake at the top of that test file:

```python
class FakeStatus:
    ok = True
    safe_reason_code = "configured"


class FakeOpenCliResumeRunner:
    def __init__(self, *, envelope: dict[str, object]) -> None:
        self.envelope = envelope

    def status(self) -> FakeStatus:
        return FakeStatus()

    def search_liepin_resumes(self, **kwargs: object) -> dict[str, object]:
        return self.envelope
```

- [ ] **Step 2: Write failing runtime lane event test**

Add to `tests/test_liepin_runtime_source_lane.py`:

```python
def test_liepin_runtime_lane_appends_opencli_workflow_step_events() -> None:
    class WorkflowWorker:
        async def ensure_ready(self, on_event=None) -> None:
            return None

        async def search(self, request, *, round_no, trace_id, provider_account_hash=None):
            del request, round_no, trace_id, provider_account_hash
            payload = {
                "providerCandidateKeyHash": "candidate-hash",
                "providerRank": 1,
                "protectedSnapshotRef": "artifact://protected/liepin-opencli/raw/run-1/1.json",
                "actionTraceRef": "artifact://protected/liepin-opencli/action-traces/run-1.json",
                "fullText": "完整原始简历文本",
            }
            detail = LiepinWorkerCandidateDetail(
                payload=payload,
                normalized_text="完整原始简历文本",
                provider_subject_id="candidate-hash",
                synthetic_candidate_fingerprint="fingerprint-1",
                identity_confidence="provider_subject_id",
                extraction_source="dom_fallback",
                extractor_version="liepin-opencli-deterministic-v1",
                pii_classification="no_direct_contact",
                retention_policy="provider_snapshot_7d",
                access_scope="local_run_only",
                redaction_state="raw_provider_payload",
            )
            response = LiepinResumeSearchResponse(
                resumes=[detail],
                requestPayload={
                    "workflowSteps": [
                        {
                            "event_type": "source_workflow_step_completed",
                            "step_name": "capture_detail",
                            "status": "completed",
                            "safe_counts": {"details_opened": 1},
                            "safe_metadata": {"rank": 1},
                            "artifact_refs": ["artifact://protected/liepin-opencli/raw/run-1/1.json"],
                        }
                    ],
                    "actionTraceRef": "artifact://protected/liepin-opencli/action-traces/run-1.json",
                },
                rawCandidateCount=1,
            )
            return liepin_resume_search_response_to_search_result(response)

    request = RuntimeSourceLaneRequest(
        source="liepin",
        lane_mode="card",
        job_title="数据开发专家",
        jd="JD",
        notes="",
        runtime_run_id="run-1",
        source_plan_id="run-1:source:liepin",
        source_lane_run_id="run-1:source:liepin:round:1:lane:1",
        source_query_terms=("数据开发", "Python"),
        logical_query_role="exploit",
        logical_keyword_query="数据开发 Python",
        logical_requested_count=2,
        logical_provider_scan_limit=6,
    )

    result = asyncio.run(run_liepin_source_lane(settings=make_settings(), request=request, worker_client=WorkflowWorker()))

    workflow_events = [event for event in result.events if event.step_name == "capture_detail"]
    assert len(workflow_events) == 1
    assert workflow_events[0].event_type == "source_workflow_step_completed"
    assert workflow_events[0].safe_counts == {"details_opened": 1}
```

Use existing imports in the file where available. Add missing imports:

```python
from seektalent.providers.liepin.client import liepin_resume_search_response_to_search_result
from seektalent.providers.liepin.worker_contracts import LiepinResumeSearchResponse, LiepinWorkerCandidateDetail
```

- [ ] **Step 3: Run the new tests and verify they fail**

Run:

```bash
.venv/bin/pytest -q tests/test_liepin_opencli_retriever.py::test_opencli_retriever_preserves_workflow_steps_in_request_payload tests/test_liepin_runtime_source_lane.py::test_liepin_runtime_lane_appends_opencli_workflow_step_events
```

Expected:

```text
KeyError: 'workflowSteps'
```

- [ ] **Step 4: Preserve workflow steps in OpenCLI retriever response**

In `src/seektalent/providers/liepin/opencli_retriever.py`, update `_response_from_opencli_envelope()`:

```python
workflow_steps = envelope.get("workflow_steps")
request_payload: dict[str, object] = {
    "source": "liepin",
    "backend": "opencli",
    "opencliStatus": status,
    "safeReasonCode": envelope.get("safe_reason_code") or envelope.get("stop_reason"),
    "actionTraceRef": action_trace_ref,
}
if isinstance(workflow_steps, list):
    request_payload["workflowSteps"] = workflow_steps

return LiepinResumeSearchResponse(
    resumes=resumes,
    exhausted=status == "succeeded",
    requestPayload=request_payload,
    rawCandidateCount=int(envelope.get("cards_seen") or len(resumes)),
)
```

- [ ] **Step 5: Allow safe request payload keys**

In `src/seektalent/providers/liepin/client.py`, update `_safe_search_request_payload()`:

```python
def _safe_search_request_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "keyword",
        "pageSize",
        "cursor",
        "round",
        "traceId",
        "providerFilters",
        "backend",
        "opencliStatus",
        "safeReasonCode",
        "actionTraceRef",
        "workflowSteps",
    }
    return {key: value for key, value in payload.items() if key in allowed_keys}
```

- [ ] **Step 6: Convert workflow steps to runtime lane events**

In `src/seektalent/providers/liepin/runtime_lane.py`, add helper functions:

```python
def _workflow_events_from_search_result(
    *,
    search_result: SearchResult,
    runtime_run_id: str,
    source_plan_id: str,
    source_lane_run_id: str,
    attempt: int,
    start_seq: int,
) -> tuple[RuntimeSourceLaneEvent, ...]:
    raw_steps = search_result.request_payload.get("workflowSteps")
    if not isinstance(raw_steps, list):
        return ()
    events: list[RuntimeSourceLaneEvent] = []
    for offset, raw_step in enumerate(raw_steps, start=0):
        if not isinstance(raw_step, Mapping):
            continue
        event_type = str(raw_step.get("event_type") or "")
        if event_type not in {
            "source_workflow_step_started",
            "source_workflow_step_completed",
            "source_workflow_step_failed",
        }:
            continue
        status = _workflow_step_status(raw_step.get("status"))
        events.append(
            RuntimeSourceLaneEvent(
                schema_version="runtime_source_lane_event_v1",
                runtime_run_id=runtime_run_id,
                source_plan_id=source_plan_id,
                source_lane_run_id=source_lane_run_id,
                source="liepin",
                attempt=attempt,
                event_seq=start_seq + offset,
                event_type=cast(RuntimeSourceLaneEventType, event_type),
                status=status,
                step_name=str(raw_step.get("step_name") or ""),
                safe_counts=_int_mapping(raw_step.get("safe_counts")),
                safe_metadata=_safe_metadata_mapping(raw_step.get("safe_metadata")),
                safe_reason_code=str(raw_step.get("safe_reason_code") or "") or None,
                artifact_refs=tuple(ref for ref in _string_list(raw_step.get("artifact_refs"))),
            )
        )
    return tuple(events)


def _workflow_step_status(value: object) -> RuntimeSourceLaneStatus | None:
    if value in {"completed", "blocked", "partial", "failed", "cancelled"}:
        return cast(RuntimeSourceLaneStatus, value)
    if value == "running":
        return None
    return None


def _int_mapping(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(item, int) and not isinstance(item, bool)}


def _safe_metadata_mapping(value: object) -> dict[str, str | int | bool]:
    if not isinstance(value, Mapping):
        return {}
    output: dict[str, str | int | bool] = {}
    for key, item in value.items():
        if isinstance(item, bool | int | str):
            output[str(key)] = item
    return output


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))
```

In `_card_lane_result_from_search_result()`, replace the `events=` argument with:

```python
base_events = _card_lane_events(
    runtime_run_id=runtime_run_id,
    source_plan_id=source_plan_id,
    source_lane_run_id=source_lane_run_id,
    attempt=request.attempt,
    raw_candidate_count=search_result.raw_candidate_count,
    candidate_count=len(candidates),
    detail_recommendation_count=len(detail_recommendations),
    detail_backed=detail_backed,
    status=status,
    stop_reason_code=stop_reason_code,
)
workflow_events = _workflow_events_from_search_result(
    search_result=search_result,
    runtime_run_id=runtime_run_id,
    source_plan_id=source_plan_id,
    source_lane_run_id=source_lane_run_id,
    attempt=request.attempt,
    start_seq=len(base_events) + 1,
)
```

Then use:

```python
events=base_events + workflow_events,
```

- [ ] **Step 7: Run focused propagation tests**

Run:

```bash
.venv/bin/pytest -q tests/test_liepin_opencli_retriever.py::test_opencli_retriever_preserves_workflow_steps_in_request_payload tests/test_liepin_runtime_source_lane.py::test_liepin_runtime_lane_appends_opencli_workflow_step_events
```

Expected:

```text
2 passed
```

- [ ] **Step 8: Commit**

```bash
git add src/seektalent/providers/liepin/opencli_retriever.py src/seektalent/providers/liepin/client.py src/seektalent/providers/liepin/runtime_lane.py tests/test_liepin_opencli_retriever.py tests/test_liepin_runtime_source_lane.py
git commit -m "feat: propagate Liepin workflow steps to runtime events"
```

---

### Task 5: Surface Workflow Steps In Workbench Runtime Graph

**Files:**
- Modify: `src/seektalent_ui/runtime_graph.py`
- Modify: `src/seektalent_ui/models.py`
- Test: `tests/test_workbench_runtime_graph.py`

- [ ] **Step 1: Write failing graph detail test**

Add to `tests/test_workbench_runtime_graph.py`:

```python
def test_runtime_graph_liepin_source_node_shows_workflow_step_timeline() -> None:
    session = SimpleNamespace(
        session_id="session-1",
        job_title="数据开发专家",
        jd_text="JD",
        notes="",
        source_kinds=["cts", "liepin"],
    )
    events = [
        SimpleNamespace(
            event_id="evt-1",
            event_name="runtime_source_workflow_step_completed",
            round_no=1,
            payload_json={
                "schema_version": "runtime_source_lane_event_v1",
                "source": "liepin",
                "source_lane_run_id": "run-1:source:liepin:round:1:lane:1",
                "event_type": "source_workflow_step_completed",
                "step_name": "capture_detail",
                "status": "completed",
                "safe_counts": {"details_opened": 1},
                "safe_metadata": {"rank": 1},
            },
            created_at="2026-05-27T00:00:00Z",
        )
    ]

    graph = build_runtime_graph(
        session=session,
        events=events,
        runtime_source_state=None,
        detail_open_requests=[],
        final_top=None,
    )

    liepin_nodes = [node for node in graph.nodes if node.sourceKind == "liepin"]
    assert liepin_nodes
    values = [
        value
        for node in liepin_nodes
        for section in node.detailSections
        if section.heading == "猎聘步骤"
        for value in (section.values or [])
    ]
    assert any("capture_detail" in value and "details_opened=1" in value for value in values)
```

Use the imports already present in the file. If missing, add:

```python
from types import SimpleNamespace
from seektalent_ui.runtime_graph import build_runtime_graph
```

- [ ] **Step 2: Run the graph test and verify it fails**

Run:

```bash
.venv/bin/pytest -q tests/test_workbench_runtime_graph.py::test_runtime_graph_liepin_source_node_shows_workflow_step_timeline
```

Expected:

```text
AssertionError: assert []
```

- [ ] **Step 3: Add graph step extraction helpers**

In `src/seektalent_ui/runtime_graph.py`, add helpers near `_details()`:

```python
def _workflow_step_events(events_for_round: Sequence[Mapping[str, object]], source_kind: str) -> list[Mapping[str, object]]:
    result: list[Mapping[str, object]] = []
    for event in events_for_round:
        if event.get("source") != source_kind:
            continue
        if event.get("eventType") not in {
            "source_workflow_step_started",
            "source_workflow_step_completed",
            "source_workflow_step_failed",
        }:
            continue
        result.append(event)
    return result


def _workflow_step_section(events_for_round: Sequence[Mapping[str, object]], source_kind: str):
    values: list[str] = []
    for event in _workflow_step_events(events_for_round, source_kind):
        step_name = _value_text(event.get("stepName")) or "unknown_step"
        event_type = _value_text(event.get("eventType")) or "unknown_event"
        status = _value_text(event.get("status")) or "running"
        counts = _value_text(event.get("safeCounts")) or ""
        reason = _value_text(event.get("safeReasonCode")) or ""
        parts = [step_name, event_type, status, counts, reason]
        values.append(" · ".join(part for part in parts if part))
    return section_from_list("猎聘步骤", values) if values else None
```

If `_runtime_event()` uses camelCase keys, use the camelCase field names above. If it preserves snake_case, adjust helper lookups to check both:

```python
step_name = _value_text(event.get("stepName") or event.get("step_name")) or "unknown_step"
```

- [ ] **Step 4: Add the section to Liepin source nodes**

Find the code path that builds source retrieval nodes in `build_runtime_graph()`. Add `_workflow_step_section(events_for_round, source_kind)` to the source node `detailSections` list only for `source_kind == "liepin"`.

The resulting detail section list should include:

```python
detailSections=_compact_sections(
    [
        existing_facts_section,
        _workflow_step_section(events_for_round, source_kind) if source_kind == "liepin" else None,
        existing_candidate_section,
    ]
)
```

Preserve existing sections and ordering except for adding this new section after source facts.

- [ ] **Step 5: Run graph tests**

Run:

```bash
.venv/bin/pytest -q tests/test_workbench_runtime_graph.py::test_runtime_graph_liepin_source_node_shows_workflow_step_timeline tests/test_workbench_runtime_graph.py
```

Expected:

```text
all selected tests passed
```

- [ ] **Step 6: Commit**

```bash
git add src/seektalent_ui/runtime_graph.py src/seektalent_ui/models.py tests/test_workbench_runtime_graph.py
git commit -m "feat: show Liepin workflow steps in runtime graph"
```

---

### Task 6: Include Latest Workflow Step In Runtime Source State API

**Files:**
- Modify: `src/seektalent_ui/models.py`
- Modify: `src/seektalent_ui/workbench_routes.py`
- Test: `tests/test_workbench_api.py`

- [ ] **Step 1: Write failing API model/response test**

Add to `tests/test_workbench_api.py` near existing runtime source state tests:

```python
def test_runtime_source_state_includes_latest_workflow_step(client, seeded_user_session) -> None:
    session_id = seeded_user_session.session_id
    store = seeded_user_session.store
    store.append_session_event(
        user=seeded_user_session.user,
        session_id=session_id,
        event_name="runtime_source_workflow_step_completed",
        schema_version="runtime_source_lane_event_v1",
        payload={
            "schema_version": "runtime_source_lane_event_v1",
            "runtime_run_id": "run-1",
            "source_plan_id": "run-1:source:liepin",
            "source_lane_run_id": "run-1:source:liepin:round:1:lane:1",
            "source": "liepin",
            "attempt": 1,
            "event_seq": 3,
            "event_type": "source_workflow_step_completed",
            "status": "completed",
            "step_name": "capture_detail",
            "safe_counts": {"details_opened": 1},
            "safe_metadata": {"rank": 1},
        },
    )

    response = client.get(f"/api/workbench/sessions/{session_id}")

    assert response.status_code == 200
    sources = response.json()["runtimeSourceState"]["sources"]
    liepin = next(source for source in sources if source["sourceKind"] == "liepin")
    assert liepin["latestWorkflowStep"]["stepName"] == "capture_detail"
    assert liepin["latestWorkflowStep"]["eventType"] == "source_workflow_step_completed"
    assert liepin["latestWorkflowStep"]["safeCounts"] == {"details_opened": 1}
```

If the test suite uses a different fixture name for seeded sessions, adapt to the closest existing runtime source state API test fixture in `tests/test_workbench_api.py`.

- [ ] **Step 2: Run the API test and verify it fails**

Run:

```bash
.venv/bin/pytest -q tests/test_workbench_api.py::test_runtime_source_state_includes_latest_workflow_step
```

Expected:

```text
KeyError: 'latestWorkflowStep'
```

- [ ] **Step 3: Add response model**

In `src/seektalent_ui/models.py`, add:

```python
class WorkbenchRuntimeSourceWorkflowStepResponse(BaseModel):
    eventType: str
    stepName: str
    status: RuntimeSourceDisplayStatus | None = None
    safeCounts: dict[str, int] = Field(default_factory=dict)
    safeReasonCode: str | None = None
```

Then add to `WorkbenchRuntimeSourceLaneStateResponse`:

```python
latestWorkflowStep: WorkbenchRuntimeSourceWorkflowStepResponse | None = None
```

- [ ] **Step 4: Populate latest workflow step**

In `src/seektalent_ui/workbench_routes.py`, update `_runtime_source_lane_state_response()`:

```python
latest_workflow_step = None
payload = latest_state.payload_json if isinstance(latest_state.payload_json, Mapping) else {}
event_type = str(payload.get("event_type") or latest_state.event_type or "")
step_name = payload.get("step_name")
if event_type.startswith("source_workflow_step_") and isinstance(step_name, str) and step_name:
    latest_workflow_step = WorkbenchRuntimeSourceWorkflowStepResponse(
        eventType=event_type,
        stepName=step_name,
        status=_runtime_source_display_status(str(payload.get("status") or latest_state.status or "")),
        safeCounts={
            str(key): value
            for key, value in (payload.get("safe_counts") or {}).items()
            if isinstance(value, int) and not isinstance(value, bool)
        },
        safeReasonCode=str(payload.get("safe_reason_code") or "") or None,
    )
```

Pass `latestWorkflowStep=latest_workflow_step` into `WorkbenchRuntimeSourceLaneStateResponse(...)`.

If `_runtime_source_display_status()` does not exist, use the existing status normalization helper in that file.

- [ ] **Step 5: Run API tests**

Run:

```bash
.venv/bin/pytest -q tests/test_workbench_api.py::test_runtime_source_state_includes_latest_workflow_step tests/test_workbench_api.py
```

Expected:

```text
all selected tests passed
```

- [ ] **Step 6: Commit**

```bash
git add src/seektalent_ui/models.py src/seektalent_ui/workbench_routes.py tests/test_workbench_api.py
git commit -m "feat: expose latest Liepin workflow step in source state"
```

---

### Task 7: Frontend Type And Rendering Verification

**Files:**
- Modify: `apps/web-svelte/src/lib/api/schema.d.ts`
- Modify: `apps/web-svelte/src/lib/components/SourceCard.svelte`
- Test: `apps/web-svelte/src/lib/components/SourceCard.test.ts`

- [ ] **Step 1: Regenerate or update API schema**

If the repo has an API schema generation command, run it. If not, update `apps/web-svelte/src/lib/api/schema.d.ts` manually to include:

```ts
WorkbenchRuntimeSourceWorkflowStepResponse: {
	eventType: string;
	stepName: string;
	status?: components['schemas']['RuntimeSourceDisplayStatus'] | null;
	safeCounts?: Record<string, number>;
	safeReasonCode?: string | null;
};
```

Then add:

```ts
latestWorkflowStep?: components['schemas']['WorkbenchRuntimeSourceWorkflowStepResponse'] | null;
```

to `WorkbenchRuntimeSourceLaneStateResponse`.

- [ ] **Step 2: Write failing source card test**

In `apps/web-svelte/src/lib/components/SourceCard.test.ts`, add a test using the existing render helper:

```ts
it('shows latest Liepin workflow step when present', () => {
	const session = buildSession({
		runtimeSourceState: {
			selectedSourceKinds: ['liepin'],
			coverageStatus: 'pending',
			reasonCode: null,
			sources: [
				{
					sourceKind: 'liepin',
					status: 'running',
					eventType: 'source_workflow_step_completed',
					sourceLaneRunId: 'run-1:source:liepin:round:1:lane:1',
					attempt: 1,
					eventSeq: 3,
					candidateCount: 1,
					rawCandidateCount: 6,
					detailState: null,
					reasonCode: null,
					latestWorkflowStep: {
						eventType: 'source_workflow_step_completed',
						stepName: 'capture_detail',
						status: 'completed',
						safeCounts: { details_opened: 1 },
						safeReasonCode: null
					}
				}
			]
		}
	});

	render(SourceCard, { props: { card: session.sourceCards[0], session } });

	expect(screen.getByText(/capture_detail/)).toBeInTheDocument();
	expect(screen.getByText(/details_opened=1/)).toBeInTheDocument();
});
```

Use the existing `buildSession`/render helpers in that file. If helper names differ, adapt the test to the existing file structure.

- [ ] **Step 3: Run the frontend test and verify it fails**

Run:

```bash
cd apps/web-svelte
bun test src/lib/components/SourceCard.test.ts
```

Expected:

```text
Unable to find an element with the text: /capture_detail/
```

- [ ] **Step 4: Render the latest step compactly**

In `apps/web-svelte/src/lib/components/SourceCard.svelte`, add a helper:

```ts
function workflowStepText(sourceState: RuntimeSourceState | null | undefined) {
	const step = sourceState?.latestWorkflowStep;
	if (!step) return null;
	const counts = Object.entries(step.safeCounts ?? {})
		.map(([key, value]) => `${key}=${value}`)
		.join(' · ');
	return counts ? `${step.stepName} · ${counts}` : step.stepName;
}
```

Use the existing source state variable for the card and render:

```svelte
{#if workflowStepText(runtimeSource)}
	<small class="source-card-workflow-step">{workflowStepText(runtimeSource)}</small>
{/if}
```

Place it near the existing status/subtitle text. Do not add borders or nested cards.

- [ ] **Step 5: Run frontend tests**

Run:

```bash
cd apps/web-svelte
bun test src/lib/components/SourceCard.test.ts
```

Expected:

```text
all tests pass
```

- [ ] **Step 6: Commit**

```bash
git add apps/web-svelte/src/lib/api/schema.d.ts apps/web-svelte/src/lib/components/SourceCard.svelte apps/web-svelte/src/lib/components/SourceCard.test.ts
git commit -m "feat: show latest Liepin workflow step in source card"
```

---

### Task 8: End-To-End Verification

**Files:**
- No new source files unless failures expose a missing import or schema mismatch.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_liepin_opencli_workflow.py \
  tests/test_pi_opencli_browser.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_runtime_source_lanes.py \
  tests/test_workbench_runtime_graph.py \
  tests/test_workbench_api.py
```

Expected:

```text
all selected tests passed
```

- [ ] **Step 2: Run full backend tests**

Run:

```bash
.venv/bin/pytest -q
```

Expected:

```text
all tests passed
```

- [ ] **Step 3: Run focused frontend tests with Bun**

Run:

```bash
cd apps/web-svelte
bun test src/lib/components/SourceCard.test.ts
```

Expected:

```text
all tests passed
```

- [ ] **Step 4: Run frontend checks if available**

If `apps/web-svelte/package.json` contains a `check` script, run:

```bash
cd apps/web-svelte
bun run check
```

Expected:

```text
no type or Svelte check failures
```

- [ ] **Step 5: Restart local dev workbench**

Stop the current dev workbench process group, then start it using the repo script:

```bash
python - <<'PY'
import os
from pathlib import Path

root = Path('/Users/frankqdwang/Agents/SeekTalent-0.2.4')
log = root / '.seektalent/logs/dev-workbench.log'
pidfile = root / '.seektalent/logs/dev-workbench.pid'
log.parent.mkdir(parents=True, exist_ok=True)
pid = os.fork()
if pid:
    pidfile.write_text(str(pid), encoding='utf-8')
    print(pid)
    raise SystemExit(0)
os.setsid()
pid2 = os.fork()
if pid2:
    raise SystemExit(0)
os.chdir(root)
with open(log, 'ab', buffering=0) as handle:
    os.dup2(handle.fileno(), 1)
    os.dup2(handle.fileno(), 2)
with open(os.devnull, 'rb') as handle:
    os.dup2(handle.fileno(), 0)
os.environ['SEEKTALENT_LIEPIN_OPENCLI_START_DAEMON'] = '1'
os.execv('/bin/zsh', ['/bin/zsh', '-lc', './scripts/start-dev-workbench.sh'])
PY
```

- [ ] **Step 6: Verify local endpoints**

Run:

```bash
curl -fsS http://127.0.0.1:8012/openapi.json >/dev/null
curl -fsS http://127.0.0.1:5178/ >/dev/null
apps/web-svelte/node_modules/.bin/opencli daemon status
```

Expected:

```text
OpenAPI request succeeds
Frontend request succeeds
OpenCLI reports Daemon: running and Extension: connected
```

- [ ] **Step 7: Manual smoke validation**

Start one manual two-source run from the workbench and confirm:

```text
Liepin source card shows a latest workflow step while running.
Liepin source node details include a "猎聘步骤" timeline.
The timeline includes observe_cards, open_detail, capture_detail, cleanup_detail_tabs, and finalize.
No raw or normalized resume text appears in the step timeline.
The source node candidate list still shows original recalled resumes.
No generated detail tab remains as about:blank after the run finishes.
```

- [ ] **Step 8: Final commit**

```bash
git status --short
git add docs/superpowers/specs/2026-05-27-liepin-observable-source-subworkflow-design.md docs/superpowers/plans/2026-05-27-liepin-observable-source-subworkflow.md
git commit -m "docs: plan Liepin observable source subworkflow"
```

If implementation commits were already made per task, this final commit should only include the plan/spec files.

---

## Self-Review

- Spec coverage:
  - Typed step vocabulary: Task 2.
  - Runtime event schema: Task 1.
  - OpenCLI action trace projection: Task 3.
  - Runtime propagation: Task 4.
  - Workbench graph visibility: Task 5.
  - Runtime source state visibility: Task 6.
  - Frontend display: Task 7.
  - Verification and local restart: Task 8.
- Placeholder scan:
  - This plan contains no `TBD`, `TODO`, "implement later", or unbounded "add tests" instruction.
- Type consistency:
  - `workflow_steps` is the OpenCLI envelope key.
  - `workflowSteps` is the worker/request payload key.
  - `step_name` is the runtime source lane event payload key.
  - `stepName` is the frontend/API response key.
