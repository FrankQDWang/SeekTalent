# Runtime-Authored Strategy Graph Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move strategy graph node/edge/detail/candidate-scope authorship to the backend so the Svelte UI renders Runtime graph data generically and dual-source node candidates stay consistent with Runtime semantics.

**Architecture:** Add a backend Runtime graph projection API that turns existing Runtime public events, Workbench runtime source state, requirement review, detail requests, review items, and final-top10 data into one graph contract. Update graph-candidates to resolve candidate lists through the same backend node candidate scopes. Replace frontend `runStory` business graph construction with a thin SvelteFlow adapter plus generic node detail renderer.

**Tech Stack:** Python 3.12, FastAPI/Pydantic, SQLite Workbench store, pytest, Svelte 5, TanStack Query, SvelteFlow, Vitest, Bun.

---

## Spec Link

This plan implements:

`docs/superpowers/specs/2026-05-26-runtime-authored-strategy-graph-contract-design.md`

## Execution Notes

- Execute as the next stacked PR after PR #6.
- Do not change Runtime retrieval, source dispatch, normalization, dedupe, scoring, reflection, or finalization behavior.
- Do not add LLM calls for graph summaries or node details.
- Do not preserve active frontend `runStory` graph construction for the session page.
- Commit after each task.

## File Map

Backend graph contract:

- Modify: `src/seektalent_ui/models.py`
  - Add `WorkbenchRuntimeGraphResponse`, node/edge/section/fact/candidate-scope models.
- Create: `src/seektalent_ui/runtime_graph.py`
  - Build backend-authored graph nodes and edges.
  - Convert safe structured payloads into deterministic natural text sections.
  - Resolve graph node candidate scopes.
- Modify: `src/seektalent_ui/workbench_routes.py`
  - Add `/api/workbench/sessions/{session_id}/runtime-graph`.
  - Make graph-candidates call backend graph scope resolution.
- Modify: `src/seektalent_ui/workbench_candidate_graph.py`
  - Replace duplicated node-id business parsing with candidate scopes from `runtime_graph.py`.
  - Fix dual-source `round-N-score` and round-scoped Liepin candidates.
- Add: `tests/test_workbench_runtime_graph.py`
- Modify: `tests/test_workbench_api.py`

Frontend graph contract:

- Modify: `apps/web-svelte/src/lib/api/workbench.ts`
  - Add `getRuntimeGraph(sessionId)`.
- Regenerate: `apps/web-svelte/src/lib/api/schema.d.ts`
- Create: `apps/web-svelte/src/lib/workbench/runtimeGraphView.ts`
  - Thin adapter from backend graph response to SvelteFlow node/edge display shape.
- Add: `apps/web-svelte/src/lib/workbench/runtimeGraphView.test.ts`
- Modify: `apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte`
  - Fetch runtime graph and stop using `buildRunStory call` for active graph rendering.
  - Pass full graph-candidate page response to node detail.
- Modify: `apps/web-svelte/src/lib/components/StrategyCanvas.svelte`
- Modify: `apps/web-svelte/src/lib/components/StrategyGraph.svelte`
- Modify: `apps/web-svelte/src/lib/components/NodeDetailPanel.svelte`
  - Render backend detail sections generically.
  - Use page-level candidate metadata.
- Modify: `apps/web-svelte/src/lib/components/GraphNodeCandidateList.svelte`
  - Reuse or embed inside `NodeDetailPanel`.
- Add or modify: `apps/web-svelte/src/lib/components/NodeDetailPanel.test.ts`
- Modify or delete active imports/tests for `apps/web-svelte/src/lib/workbench/runStory.ts`

---

## Task 1: Add Backend Runtime Graph Models And Safe Text Serializer

**Files:**
- Modify: `src/seektalent_ui/models.py`
- Create: `src/seektalent_ui/runtime_graph.py`
- Add: `tests/test_workbench_runtime_graph.py`

- [ ] **Step 1: Write failing serializer/model tests**

Create `tests/test_workbench_runtime_graph.py` with these initial tests:

```python
from __future__ import annotations

from seektalent_ui.runtime_graph import safe_natural_text, section_from_facts


def test_safe_natural_text_serializes_nested_business_values() -> None:
    text = safe_natural_text(
        {
            "hard_constraints": {"location": "上海", "age": "30-40"},
            "must_have_capabilities": ["Python 后端", "分布式系统"],
            "empty": [],
            "none": None,
        }
    )

    assert "hard_constraints：location=上海；age=30-40" in text
    assert "must_have_capabilities：Python 后端、分布式系统" in text
    assert "empty" not in text
    assert "none" not in text


def test_safe_natural_text_redacts_technical_and_secret_fields() -> None:
    text = safe_natural_text(
        {
            "runtimeRunId": "run_secret",
            "cookie": "secret-cookie",
            "artifact_path": "/Users/frank/private.json",
            "summary": "第 1 轮完成。",
            "counts": {"topPoolCount": 10},
        }
    )

    assert "第 1 轮完成" in text
    assert "topPoolCount=10" in text
    assert "run_secret" not in text
    assert "secret-cookie" not in text
    assert "/Users/frank" not in text


def test_section_from_facts_omits_empty_values() -> None:
    section = section_from_facts(
        "评分",
        [
            ("进入评分", "18 人"),
            ("空值", ""),
            ("无值", None),
        ],
    )

    assert section.heading == "评分"
    assert section.kind == "facts"
    assert [(fact.label, fact.value) for fact in section.facts] == [("进入评分", "18 人")]
```

- [ ] **Step 2: Run tests and confirm they fail**

Run:

```bash
uv run pytest tests/test_workbench_runtime_graph.py -q
```

Expected: FAIL because `seektalent_ui.runtime_graph` does not exist.

- [ ] **Step 3: Add graph response models**

Add these models to `src/seektalent_ui/models.py` near the graph-candidate models:

```python
WorkbenchRuntimeGraphSourceKind = Literal["cts", "liepin", "all"]
WorkbenchRuntimeGraphNodeStatus = Literal[
    "pending",
    "running",
    "completed",
    "partial",
    "blocked",
    "degraded",
    "failed",
    "cancelled",
]
WorkbenchRuntimeGraphSectionKind = Literal["text", "facts", "list"]
WorkbenchRuntimeGraphCandidateScopeKind = Literal[
    "none",
    "round_recall",
    "round_score",
    "final",
    "detail_approval",
]


class WorkbenchRuntimeGraphFactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: str


class WorkbenchRuntimeGraphSectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str
    kind: WorkbenchRuntimeGraphSectionKind
    text: str | None = None
    facts: list[WorkbenchRuntimeGraphFactResponse] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)


class WorkbenchRuntimeGraphCandidateScopeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scopeKind: WorkbenchRuntimeGraphCandidateScopeKind
    sourceKind: WorkbenchRuntimeGraphSourceKind = "all"
    roundNo: int | None = None
    reason: str | None = None


class WorkbenchRuntimeGraphNodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodeId: str
    kind: str
    label: str
    summaryText: str
    status: WorkbenchRuntimeGraphNodeStatus
    stage: str
    sourceKind: WorkbenchRuntimeGraphSourceKind = "all"
    lane: Literal["shared", "cts", "liepin"] = "shared"
    roundNo: int | None = None
    eventIds: list[str] = Field(default_factory=list)
    detailSections: list[WorkbenchRuntimeGraphSectionResponse] = Field(default_factory=list)
    candidateScope: WorkbenchRuntimeGraphCandidateScopeResponse


class WorkbenchRuntimeGraphEdgeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edgeId: str
    fromNodeId: str
    toNodeId: str
    label: str | None = None


class WorkbenchRuntimeGraphResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str
    generatedAt: str
    nodes: list[WorkbenchRuntimeGraphNodeResponse]
    edges: list[WorkbenchRuntimeGraphEdgeResponse]
    completionText: str | None = None
```

- [ ] **Step 4: Implement serializer helpers**

Create `src/seektalent_ui/runtime_graph.py` with this initial content:

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from seektalent_ui.models import (
    WorkbenchRuntimeGraphFactResponse,
    WorkbenchRuntimeGraphSectionResponse,
)

_REDACTED_KEYS = {
    "artifact",
    "artifact_path",
    "artifactPath",
    "auth",
    "authorization",
    "browser_endpoint",
    "cdp",
    "cookie",
    "cookies",
    "file",
    "filepath",
    "path",
    "provider_payload",
    "raw_payload",
    "runtimeRunId",
    "storage_state",
    "token",
    "url",
    "websocket",
}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def section_from_text(heading: str, text: str | None) -> WorkbenchRuntimeGraphSectionResponse | None:
    clean = _clean_text(text)
    if clean is None:
        return None
    return WorkbenchRuntimeGraphSectionResponse(heading=heading, kind="text", text=clean)


def section_from_facts(
    heading: str,
    facts: Sequence[tuple[str, object | None]],
) -> WorkbenchRuntimeGraphSectionResponse:
    visible = [
        WorkbenchRuntimeGraphFactResponse(label=label, value=value_text)
        for label, value in facts
        if (value_text := _value_text(value)) is not None
    ]
    return WorkbenchRuntimeGraphSectionResponse(heading=heading, kind="facts", facts=visible)


def section_from_list(
    heading: str,
    values: Sequence[object],
) -> WorkbenchRuntimeGraphSectionResponse:
    visible = [text for value in values if (text := _value_text(value)) is not None]
    return WorkbenchRuntimeGraphSectionResponse(heading=heading, kind="list", values=visible)


def safe_natural_text(value: object) -> str:
    lines = _natural_lines(value)
    return "\n".join(line for line in lines if line.strip())


def _natural_lines(value: object, *, prefix: str | None = None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        lines: list[str] = []
        for raw_key, raw_item in value.items():
            key = str(raw_key)
            if _is_redacted_key(key):
                continue
            item_text = _value_text(raw_item)
            if item_text is not None:
                lines.append(f"{key}：{item_text}" if prefix is None else f"{prefix}.{key}：{item_text}")
        return lines
    text = _value_text(value)
    return [text] if text is not None else []


def _value_text(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, Mapping):
        parts = []
        for raw_key, raw_item in value.items():
            key = str(raw_key)
            if _is_redacted_key(key):
                continue
            text = _value_text(raw_item)
            if text is not None:
                parts.append(f"{key}={text}")
        return "；".join(parts) if parts else None
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        parts = [text for item in value if (text := _value_text(item)) is not None]
        return "、".join(parts) if parts else None
    return _clean_text(str(value))


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    return text or None


def _is_redacted_key(key: str) -> bool:
    lowered = key.strip().casefold()
    return lowered in {item.casefold() for item in _REDACTED_KEYS} or any(
        token in lowered for token in ("cookie", "token", "authorization", "artifact", "storage")
    )
```

- [ ] **Step 5: Run serializer tests**

Run:

```bash
uv run pytest tests/test_workbench_runtime_graph.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent_ui/models.py src/seektalent_ui/runtime_graph.py tests/test_workbench_runtime_graph.py
git commit -m "feat: add runtime graph response contract"
```

---

## Task 2: Build Backend-Authored Runtime Graph Projection

**Files:**
- Modify: `src/seektalent_ui/runtime_graph.py`
- Modify: `tests/test_workbench_runtime_graph.py`

- [ ] **Step 1: Add graph projection tests**

Append these tests to `tests/test_workbench_runtime_graph.py`. If the repo already has session/source builders in another test module, copy their minimal dataclass fixtures into this file instead of importing private test helpers.

```python
from types import SimpleNamespace

from seektalent_ui.runtime_graph import build_runtime_graph


def _event(
    *,
    name: str,
    seq: int,
    stage: str,
    round_no: int | None = None,
    source: str | None = None,
    counts: dict[str, int] | None = None,
    status: str = "completed",
) -> SimpleNamespace:
    return SimpleNamespace(
        globalSeq=seq,
        eventName=name,
        sourceKind=source,
        sourceRunId=None,
        payload={
            "schemaVersion": "runtime_public_event_v1",
            "eventId": f"runtime-test:{round_no or 'final'}:{stage}:{source or 'all'}",
            "stage": stage,
            "roundNo": round_no,
            "sourceKind": source,
            "status": status,
            "counts": counts or {},
            "safeReasonCode": None,
        },
    )


def _session() -> SimpleNamespace:
    return SimpleNamespace(
        session_id="session-graph",
        job_title="Backend Engineer",
        jd_text="Build agentic recruiting workflow.",
        notes="Prefer search infra.",
        requirement_review=SimpleNamespace(
            status="approved",
            approved_at="2026-05-26T00:00:00Z",
            requirement_sheet={
                "job_title": "Backend Engineer",
                "must_have_capabilities": ["Python", "distributed systems"],
                "preferred_capabilities": ["agent workflow"],
                "hard_constraints": {"location": "上海"},
            },
        ),
        source_runs=[
            SimpleNamespace(source_kind="cts", status="completed"),
            SimpleNamespace(source_kind="liepin", status="completed"),
        ],
    )


def test_build_runtime_graph_authors_dual_source_round_score_scope() -> None:
    graph = build_runtime_graph(
        session=_session(),
        events=[
            _event(name="runtime_round_query_ready", seq=101, stage="round_query", round_no=1),
            _event(
                name="runtime_round_source_result",
                seq=131,
                stage="source_result",
                round_no=1,
                source="cts",
                counts={"roundReturned": 7, "roundIdentities": 7},
            ),
            _event(
                name="runtime_round_source_result",
                seq=132,
                stage="source_result",
                round_no=1,
                source="liepin",
                counts={"roundReturned": 3, "roundIdentities": 3},
            ),
            _event(
                name="runtime_round_scoring_completed",
                seq=170,
                stage="scoring",
                round_no=1,
                counts={"roundIdentities": 10, "topPoolCount": 10},
            ),
        ],
        runtime_source_state=None,
        detail_open_requests=[],
        final_top=None,
    )

    node_by_id = {node.nodeId: node for node in graph.nodes}
    assert node_by_id["round-1-source-cts"].candidateScope.scopeKind == "round_recall"
    assert node_by_id["round-1-source-cts"].candidateScope.sourceKind == "cts"
    assert node_by_id["round-1-source-liepin"].candidateScope.scopeKind == "round_recall"
    assert node_by_id["round-1-source-liepin"].candidateScope.sourceKind == "liepin"
    assert node_by_id["round-1-score"].candidateScope.scopeKind == "round_score"
    assert node_by_id["round-1-score"].candidateScope.sourceKind == "all"
    assert node_by_id["round-1-score"].candidateScope.roundNo == 1
    assert "10 位候选人进入 Top Pool" in node_by_id["round-1-score"].summaryText


def test_build_runtime_graph_non_candidate_nodes_have_none_scope() -> None:
    graph = build_runtime_graph(
        session=_session(),
        events=[_event(name="runtime_round_query_ready", seq=101, stage="round_query", round_no=1)],
        runtime_source_state=None,
        detail_open_requests=[],
        final_top=None,
    )

    node_by_id = {node.nodeId: node for node in graph.nodes}
    assert node_by_id["job"].candidateScope.scopeKind == "none"
    assert node_by_id["requirements"].candidateScope.scopeKind == "none"
    assert node_by_id["round-1-query"].candidateScope.scopeKind == "none"
    assert node_by_id["round-1-query"].detailSections
```

- [ ] **Step 2: Run graph projection tests and confirm failure**

Run:

```bash
uv run pytest tests/test_workbench_runtime_graph.py -q
```

Expected: FAIL because `build_runtime_graph call` is not implemented.

- [ ] **Step 3: Implement runtime graph projection**

Extend `src/seektalent_ui/runtime_graph.py` with `build_runtime_graph call`. Keep the implementation deterministic and do not call an LLM.

```python
from collections import defaultdict

from seektalent_ui.models import (
    WorkbenchRuntimeGraphCandidateScopeResponse,
    WorkbenchRuntimeGraphEdgeResponse,
    WorkbenchRuntimeGraphNodeResponse,
    WorkbenchRuntimeGraphResponse,
)


def build_runtime_graph(
    *,
    session: object,
    events: list[object],
    runtime_source_state: object | None,
    detail_open_requests: list[object],
    final_top: object | None,
) -> WorkbenchRuntimeGraphResponse:
    del runtime_source_state
    graph = _GraphBuilder(session_id=_attr_text(session, "session_id") or _attr_text(session, "sessionId") or "")
    job_title = _attr_text(session, "job_title") or _attr_text(session, "jobTitle") or "岗位"
    jd_text = _attr_text(session, "jd_text") or _attr_text(session, "jdText") or ""
    notes = _attr_text(session, "notes") or ""
    selected_sources = _session_sources(session)

    graph.add_node(
        node_id="job",
        kind="job",
        label=f"岗位需求 / {job_title}",
        summary_text=f"{job_title}，{_source_mode_text(selected_sources)}。",
        status="completed",
        stage="job",
        source_kind="all",
        lane="shared",
        detail_sections=_compact_sections(
            [
                section_from_facts(
                    "岗位",
                    [
                        ("岗位", job_title),
                        ("检索源", " / ".join(_source_label(source) for source in selected_sources)),
                    ],
                ),
                section_from_text("JD 摘要", jd_text[:500]),
                section_from_text("补充 notes", notes),
            ]
        ),
        candidate_scope=_none_scope("job_has_no_candidate_scope"),
    )

    requirement_review = getattr(session, "requirement_review", None)
    requirement_sheet = _requirement_sheet(requirement_review)
    if requirement_sheet is not None:
        graph.add_node(
            node_id="requirements",
            kind="requirements",
            label="需求拆解",
            summary_text=_requirements_summary(requirement_sheet),
            status="completed",
            stage="requirements",
            source_kind="all",
            lane="shared",
            detail_sections=_requirement_sections(requirement_sheet),
            candidate_scope=_none_scope("requirements_have_no_candidate_scope"),
        )
        graph.add_edge("job", "requirements", "提取约束")
        previous_node = "requirements"
    else:
        previous_node = "job"

    round_events = [_runtime_event(event) for event in events]
    round_events = [event for event in round_events if event is not None]
    rounds = sorted({event["roundNo"] for event in round_events if isinstance(event.get("roundNo"), int)})
    for round_no in rounds:
        events_for_round = [event for event in round_events if event.get("roundNo") == round_no]
        query_id = f"round-{round_no}-query"
        graph.add_node(
            node_id=query_id,
            kind="query",
            label=f"第 {round_no} 轮 · 查询包",
            summary_text=f"第 {round_no} 轮查询策略已生成。",
            status=_round_status(events_for_round, "round_query", None),
            stage="round_query",
            source_kind="all",
            lane="shared",
            round_no=round_no,
            event_ids=_event_ids(events_for_round, "round_query", None),
            detail_sections=[
                section_from_facts(
                    "查询包",
                    [
                        ("轮次", f"第 {round_no} 轮"),
                        ("Top Pool", _count_text(events_for_round, "round_query", None, "topPoolCount")),
                    ],
                )
            ],
            candidate_scope=_none_scope("round_query_has_no_candidate_scope"),
        )
        graph.add_edge(previous_node, query_id, "下一轮" if previous_node.endswith("feedback") else "开始检索")

        source_node_ids: list[str] = []
        for source in selected_sources:
            source_event = _last_event(events_for_round, "source_result", source) or _last_event(
                events_for_round, "source_dispatch", source
            )
            if source_event is None:
                continue
            node_id = f"round-{round_no}-source-{source}"
            counts = _counts(source_event)
            returned = counts.get("roundReturned", 0)
            identities = counts.get("roundIdentities", returned)
            graph.add_node(
                node_id=node_id,
                kind="source_result",
                label=f"第 {round_no} 轮 · {_source_label(source)} 检索",
                summary_text=f"{_source_label(source)} 返回 {returned} 份原始简历，形成 {identities} 位候选人。",
                status=str(source_event.get("status") or "completed"),
                stage="source_result",
                source_kind=source,
                lane=source,
                round_no=round_no,
                event_ids=[str(source_event.get("eventId"))],
                detail_sections=[
                    section_from_facts(
                        "来源结果",
                        [
                            ("来源", _source_label(source)),
                            ("原始返回", f"{returned} 份"),
                            ("候选人", f"{identities} 位"),
                            ("累计返回", _optional_count(counts.get("sourceCumulativeReturned"), "份")),
                            ("累计候选人", _optional_count(counts.get("sourceCumulativeIdentities"), "位")),
                            ("状态", str(source_event.get("status") or "completed")),
                            ("安全原因", source_event.get("safeReasonCode")),
                        ],
                    )
                ],
                candidate_scope=WorkbenchRuntimeGraphCandidateScopeResponse(
                    scopeKind="round_recall",
                    sourceKind=source,
                    roundNo=round_no,
                ),
            )
            graph.add_edge(query_id, node_id, "执行")
            source_node_ids.append(node_id)

        merge_id = f"round-{round_no}-merge"
        if len(source_node_ids) > 1:
            merge_event = _last_event(events_for_round, "merge", None)
            graph.add_node(
                node_id=merge_id,
                kind="merge",
                label=f"第 {round_no} 轮 · 合并去重",
                summary_text=f"第 {round_no} 轮完成跨源合并去重。",
                status=str(merge_event.get("status") if merge_event else "completed"),
                stage="merge",
                source_kind="all",
                lane="shared",
                round_no=round_no,
                event_ids=[str(merge_event.get("eventId"))] if merge_event else [],
                detail_sections=[
                    section_from_facts(
                        "合并去重",
                        [("身份数", _count_text(events_for_round, "merge", None, "mergedIdentities"))],
                    )
                ],
                candidate_scope=_none_scope("merge_node_uses_score_candidate_scope"),
            )
            for source_node_id in source_node_ids:
                graph.add_edge(source_node_id, merge_id, "证据合并")
            score_from = merge_id
        elif source_node_ids:
            score_from = source_node_ids[0]
        else:
            score_from = query_id

        score_event = _last_event(events_for_round, "scoring", None)
        if score_event is not None:
            counts = _counts(score_event)
            top_pool = counts.get("topPoolCount")
            identities = counts.get("roundIdentities")
            score_id = f"round-{round_no}-score"
            graph.add_node(
                node_id=score_id,
                kind="scoring",
                label=f"第 {round_no} 轮 · Top Pool",
                summary_text=_score_summary(round_no, identities, top_pool),
                status=str(score_event.get("status") or "completed"),
                stage="scoring",
                source_kind="all",
                lane="shared",
                round_no=round_no,
                event_ids=[str(score_event.get("eventId"))],
                detail_sections=[
                    section_from_facts(
                        "本轮评分",
                        [
                            ("进入评分", _optional_count(identities, "人")),
                            ("Top Pool", _optional_count(top_pool, "人")),
                            ("状态", str(score_event.get("status") or "completed")),
                        ],
                    )
                ],
                candidate_scope=WorkbenchRuntimeGraphCandidateScopeResponse(
                    scopeKind="round_score",
                    sourceKind="all",
                    roundNo=round_no,
                ),
            )
            graph.add_edge(score_from, score_id, "评分")
            previous_node = score_id

        feedback_event = _last_event(events_for_round, "feedback", None)
        if feedback_event is not None:
            feedback_id = f"round-{round_no}-feedback"
            graph.add_node(
                node_id=feedback_id,
                kind="feedback",
                label=f"第 {round_no} 轮 · 下一轮策略",
                summary_text=f"第 {round_no} 轮复盘完成，准备下一轮策略。",
                status=str(feedback_event.get("status") or "completed"),
                stage="feedback",
                source_kind="all",
                lane="shared",
                round_no=round_no,
                event_ids=[str(feedback_event.get("eventId"))],
                detail_sections=[
                    section_from_facts(
                        "复盘",
                        [("参与候选人", _count_text(events_for_round, "feedback", None, "feedbackCandidateCount"))],
                    )
                ],
                candidate_scope=_none_scope("feedback_has_no_candidate_scope"),
            )
            graph.add_edge(previous_node, feedback_id, "反馈")
            previous_node = feedback_id

    if detail_open_requests:
        graph.add_node(
            node_id="liepin-detail-approval",
            kind="detail_approval",
            label=f"详情审批 · {len(detail_open_requests)} 个",
            summary_text=f"猎聘详情审批队列有 {len(detail_open_requests)} 个请求。",
            status="completed",
            stage="detail_approval",
            source_kind="liepin",
            lane="liepin",
            detail_sections=_detail_request_sections(detail_open_requests),
            candidate_scope=WorkbenchRuntimeGraphCandidateScopeResponse(
                scopeKind="detail_approval",
                sourceKind="liepin",
                roundNo=None,
            ),
        )
        graph.add_edge(previous_node, "liepin-detail-approval", "详情队列")
        previous_node = "liepin-detail-approval"

    if final_top is not None and getattr(final_top, "items", []):
        graph.add_node(
            node_id="final-shortlist",
            kind="final",
            label=f"最终短名单 · {len(getattr(final_top, 'items', []))} 人",
            summary_text=f"最终 Top 10 已生成，共 {len(getattr(final_top, 'items', []))} 人。",
            status="completed",
            stage="final",
            source_kind="all",
            lane="shared",
            detail_sections=[
                section_from_facts(
                    "最终短名单",
                    [
                        ("候选人", f"{len(getattr(final_top, 'items', []))} 人"),
                        ("覆盖状态", getattr(final_top, "coverageStatus", None)),
                        ("完成版本", getattr(final_top, "finalizationRevision", None)),
                    ],
                )
            ],
            candidate_scope=WorkbenchRuntimeGraphCandidateScopeResponse(scopeKind="final", sourceKind="all"),
        )
        graph.add_edge(previous_node, "final-shortlist", "Top 10")

    return graph.response(completion_text="检索完成 · 候选人进入短名单" if final_top is not None else None)
```

Then add private helpers used above in the same module:

```python
class _GraphBuilder:
    def __init__(self, *, session_id: str) -> None:
        self.session_id = session_id
        self.nodes: list[WorkbenchRuntimeGraphNodeResponse] = []
        self.edges: list[WorkbenchRuntimeGraphEdgeResponse] = []

    def add_node(self, **kwargs: object) -> None:
        self.nodes.append(WorkbenchRuntimeGraphNodeResponse(**kwargs))

    def add_edge(self, from_node_id: str, to_node_id: str, label: str | None) -> None:
        self.edges.append(
            WorkbenchRuntimeGraphEdgeResponse(
                edgeId=f"{from_node_id}->{to_node_id}",
                fromNodeId=from_node_id,
                toNodeId=to_node_id,
                label=label,
            )
        )

    def response(self, *, completion_text: str | None) -> WorkbenchRuntimeGraphResponse:
        return WorkbenchRuntimeGraphResponse(
            sessionId=self.session_id,
            generatedAt=utc_now_iso(),
            nodes=self.nodes,
            edges=self.edges,
            completionText=completion_text,
        )


def _none_scope(reason: str) -> WorkbenchRuntimeGraphCandidateScopeResponse:
    return WorkbenchRuntimeGraphCandidateScopeResponse(scopeKind="none", sourceKind="all", reason=reason)


def _compact_sections(sections: list[WorkbenchRuntimeGraphSectionResponse | None]) -> list[WorkbenchRuntimeGraphSectionResponse]:
    return [section for section in sections if section is not None]


def _attr_text(value: object, name: str) -> str | None:
    item = getattr(value, name, None)
    if isinstance(item, str) and item.strip():
        return item.strip()
    return None


def _session_sources(session: object) -> list[str]:
    source_runs = getattr(session, "source_runs", None) or getattr(session, "sourceRuns", None) or []
    sources = []
    for source_run in source_runs:
        source = getattr(source_run, "source_kind", None) or getattr(source_run, "sourceKind", None)
        if source in {"cts", "liepin"} and source not in sources:
            sources.append(source)
    return sources or ["cts"]


def _source_label(source: str) -> str:
    return {"cts": "CTS", "liepin": "猎聘", "all": "全部来源"}.get(source, source)


def _source_mode_text(sources: list[str]) -> str:
    return "多源检索" if len(sources) > 1 else f"{_source_label(sources[0])} 检索"


def _requirement_sheet(requirement_review: object | None) -> Mapping[str, object] | None:
    if requirement_review is None:
        return None
    sheet = getattr(requirement_review, "requirement_sheet", None) or getattr(requirement_review, "requirementSheet", None)
    return sheet if isinstance(sheet, Mapping) else None


def _requirement_sections(sheet: Mapping[str, object]) -> list[WorkbenchRuntimeGraphSectionResponse]:
    return _compact_sections(
        [
            section_from_text("岗位摘要", _value_for_key(sheet, "role_summary")),
            section_from_list("必须能力", _list_for_key(sheet, "must_have_capabilities")),
            section_from_list("偏好能力", _list_for_key(sheet, "preferred_capabilities")),
            section_from_list("排除信号", _list_for_key(sheet, "exclusion_signals")),
            section_from_text("硬性约束", safe_natural_text(sheet.get("hard_constraints"))),
            section_from_text("偏好", safe_natural_text(sheet.get("preferences"))),
        ]
    )


def _requirements_summary(sheet: Mapping[str, object]) -> str:
    must = _list_for_key(sheet, "must_have_capabilities")
    if must:
        return f"已确认需求标准：{must[0]}"
    title = _value_for_key(sheet, "job_title")
    return f"已确认需求标准：{title or '岗位需求'}"


def _value_for_key(mapping: Mapping[str, object], key: str) -> str | None:
    value = mapping.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _list_for_key(mapping: Mapping[str, object], key: str) -> list[object]:
    value = mapping.get(key)
    return list(value) if isinstance(value, list) else []


def _runtime_event(event: object) -> dict[str, object] | None:
    payload = getattr(event, "payload", None)
    if not isinstance(payload, Mapping):
        return None
    stage = payload.get("stage")
    if not isinstance(stage, str):
        return None
    return {
        "eventId": payload.get("eventId") or f"event:{getattr(event, 'globalSeq', 0)}",
        "stage": stage,
        "roundNo": payload.get("roundNo"),
        "sourceKind": payload.get("sourceKind"),
        "status": payload.get("status") or "completed",
        "counts": payload.get("counts") if isinstance(payload.get("counts"), Mapping) else {},
        "safeReasonCode": payload.get("safeReasonCode"),
    }


def _last_event(events: list[dict[str, object]], stage: str, source: str | None) -> dict[str, object] | None:
    matching = [
        event for event in events if event.get("stage") == stage and event.get("sourceKind") == source
    ]
    return matching[-1] if matching else None


def _event_ids(events: list[dict[str, object]], stage: str, source: str | None) -> list[str]:
    return [str(event["eventId"]) for event in events if event.get("stage") == stage and event.get("sourceKind") == source]


def _round_status(events: list[dict[str, object]], stage: str, source: str | None) -> str:
    event = _last_event(events, stage, source)
    return str(event.get("status") if event else "completed")


def _counts(event: Mapping[str, object]) -> dict[str, int]:
    counts = event.get("counts")
    if not isinstance(counts, Mapping):
        return {}
    return {str(key): int(value) for key, value in counts.items() if isinstance(value, int)}


def _count_text(events: list[dict[str, object]], stage: str, source: str | None, key: str) -> str | None:
    event = _last_event(events, stage, source)
    if event is None:
        return None
    value = _counts(event).get(key)
    return str(value) if value is not None else None


def _optional_count(value: object, unit: str) -> str | None:
    return f"{value} {unit}" if isinstance(value, int) else None


def _score_summary(round_no: int, identities: int | None, top_pool: int | None) -> str:
    if top_pool is not None:
        return f"第 {round_no} 轮评分完成，{top_pool} 位候选人进入 Top Pool。"
    if identities is not None:
        return f"第 {round_no} 轮评分完成，{identities} 位候选人完成评分。"
    return f"第 {round_no} 轮评分完成。"


def _detail_request_sections(detail_open_requests: list[object]) -> list[WorkbenchRuntimeGraphSectionResponse]:
    summaries = []
    for request in detail_open_requests:
        candidate = getattr(request, "candidate", None)
        name = getattr(candidate, "display_name", None) or getattr(candidate, "displayName", None) or "猎聘候选人"
        status = getattr(request, "status", "")
        summaries.append(f"{name} · {status}")
    return [
        section_from_facts("详情审批", [("请求数", f"{len(detail_open_requests)} 个")]),
        section_from_list("请求摘要", summaries),
    ]
```

- [ ] **Step 4: Run graph projection tests**

Run:

```bash
uv run pytest tests/test_workbench_runtime_graph.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent_ui/runtime_graph.py tests/test_workbench_runtime_graph.py
git commit -m "feat: project runtime-authored strategy graph"
```

---

## Task 3: Expose Runtime Graph API And Regenerate Schema

**Files:**
- Modify: `src/seektalent_ui/workbench_routes.py`
- Modify: `apps/web-svelte/src/lib/api/workbench.ts`
- Regenerate: `apps/web-svelte/src/lib/api/schema.d.ts`
- Modify: `tests/test_workbench_api.py`

- [ ] **Step 1: Add failing API route test**

Add this test to `tests/test_workbench_api.py` near the graph-candidate tests:

```python
def test_runtime_graph_endpoint_returns_backend_authored_nodes(tmp_path: Path) -> None:
    _reset_fake_runtime()
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client, source_kinds=["cts", "liepin"])
    _approve_requirement_review(session_id=session["sessionId"], client=client)

    start = _start_session(client, session["sessionId"])
    assert start.status_code == 202, start.text
    assert FakeWorkbenchRuntime.started.wait(timeout=1)
    FakeWorkbenchRuntime.release.set()

    response = client.get(f"/api/workbench/sessions/{session['sessionId']}/runtime-graph")
    assert response.status_code == 200, response.text
    payload = response.json()
    node_by_id = {node["nodeId"]: node for node in payload["nodes"]}
    assert "job" in node_by_id
    assert "requirements" in node_by_id
    assert any(node["nodeId"].startswith("round-") for node in payload["nodes"])
    assert node_by_id["job"]["candidateScope"]["scopeKind"] == "none"
```

- [ ] **Step 2: Run API test and confirm failure**

Run:

```bash
uv run pytest tests/test_workbench_api.py::test_runtime_graph_endpoint_returns_backend_authored_nodes -q
```

Expected: FAIL with 404 for `/runtime-graph`.

- [ ] **Step 3: Add API route**

In `src/seektalent_ui/workbench_routes.py`, import the new response model and graph builder:

```python
from seektalent_ui.models import WorkbenchRuntimeGraphResponse
from seektalent_ui.runtime_graph import build_runtime_graph
```

Add the route near other session-level Workbench routes:

```python
@router.get(
    "/api/workbench/sessions/{session_id}/runtime-graph",
    response_model=WorkbenchRuntimeGraphResponse,
)
def get_session_runtime_graph(
    session_id: str,
    request: Request,
    user: WorkbenchUser = Depends(require_current_user),
) -> WorkbenchRuntimeGraphResponse:
    store = get_workbench_store(request)
    session = store.get_workbench_session(user=user, session_id=session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Not found.")
    events = store.list_workbench_events(user=user, session_id=session_id, after_global_seq=0, limit=5000)
    detail_open_requests = store.list_liepin_detail_open_requests(user=user, session_id=session_id)
    final_top = _final_top_candidate_list_for_runtime_graph(
        request=request,
        store=store,
        user=user,
        session_id=session_id,
    )
    return build_runtime_graph(
        session=session,
        events=events,
        runtime_source_state=_runtime_source_state_response(store=store, user=user, session=session),
        detail_open_requests=detail_open_requests,
        final_top=final_top,
    )
```

Add this private helper in the same file:

```python
def _final_top_candidate_list_for_runtime_graph(
    *,
    request: Request,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
) -> WorkbenchFinalTopCandidateListResponse | None:
    try:
        return list_session_final_top_candidates(session_id=session_id, request=request, user=user)
    except HTTPException:
        return None
```

- [ ] **Step 4: Run backend API test**

Run:

```bash
uv run pytest tests/test_workbench_api.py::test_runtime_graph_endpoint_returns_backend_authored_nodes -q
```

Expected: PASS.

- [ ] **Step 5: Regenerate OpenAPI TypeScript schema**

Run the repo's existing schema generation command. The frontend package currently exposes `api:gen`, and it expects the FastAPI backend to serve OpenAPI on `127.0.0.1:8012`.

```bash
tmp_root="$(mktemp -d)"
env SEEKTALENT_WORKSPACE_ROOT="$tmp_root" SEEKTALENT_WORKBENCH_ENABLED=true \
  uv run seektalent-ui-api --host 127.0.0.1 --port 8012 &
api_pid=$!
trap 'kill "$api_pid" 2>/dev/null || true; rm -rf "$tmp_root"' EXIT
for _ in {1..150}; do
  curl -fsS http://127.0.0.1:8012/openapi.json >/dev/null && break
  sleep 0.2
done
cd apps/web-svelte && bun run api:gen
```

Expected:

- `apps/web-svelte/src/lib/api/schema.d.ts` contains `WorkbenchRuntimeGraphResponse`.
- The route path `/api/workbench/sessions/{session_id}/runtime-graph` appears in the generated paths.

If another process already owns port `8012`, stop it only if it is the Workbench API from this repo. Otherwise start the API on a free local port and temporarily run `openapi-typescript http://127.0.0.1:<PORT>/openapi.json -o src/lib/api/schema.d.ts && prettier --write src/lib/api/schema.d.ts`.

- [ ] **Step 6: Add frontend API wrapper**

Add to `apps/web-svelte/src/lib/api/workbench.ts`:

```ts
export async function getRuntimeGraph(sessionId: string) {
	return requireData(
		await api.GET('/api/workbench/sessions/{session_id}/runtime-graph', {
			params: { path: { session_id: sessionId } }
		})
	);
}
```

- [ ] **Step 7: Commit**

```bash
git add src/seektalent_ui/workbench_routes.py tests/test_workbench_api.py apps/web-svelte/src/lib/api/workbench.ts apps/web-svelte/src/lib/api/schema.d.ts
git commit -m "feat: expose runtime graph api"
```

---

## Task 4: Make Graph Candidates Use Backend Candidate Scopes

**Files:**
- Modify: `src/seektalent_ui/runtime_graph.py`
- Modify: `src/seektalent_ui/workbench_candidate_graph.py`
- Modify: `tests/test_workbench_api.py`

- [ ] **Step 1: Extend existing API test helpers with source-round and runtime-event support**

In `tests/test_workbench_api.py`, extend the existing `_insert_review_candidate` helper so tests can create review items from different Runtime rounds:

```python
def _insert_review_candidate(
    tmp_path: Path,
    client: TestClient,
    *,
    session_id: str,
    review_item_id: str,
    evidence: list[dict[str, object]],
    display_name: str = "Graph Candidate",
    summary: str = "Safe graph summary.",
    aggregate_score: int = 88,
    source_round: int | None = None,
) -> None:
```

Update its `candidate_review_items` insert to include `source_round`:

```sql
INSERT INTO candidate_review_items (
    review_item_id, tenant_id, workspace_id, user_id, session_id,
    primary_evidence_id, display_name, title, company, location, summary,
    aggregate_score, fit_bucket, source_round, review_status, note, created_at, updated_at
)
VALUES (?, 'local', ?, ?, ?, ?, ?, 'Backend Engineer', 'SearchCo', 'Shanghai',
        ?, ?, 'fit', ?, 'new', '', ?, ?)
```

Add this helper beside the graph-candidate tests:

```python
from seektalent.runtime.public_events import make_runtime_public_event


def _append_runtime_graph_event(
    tmp_path: Path,
    client: TestClient,
    *,
    session_id: str,
    stage: str,
    event_seq: int,
    round_no: int | None,
    source_kind: str | None = None,
    counts: dict[str, int] | None = None,
) -> None:
    store = client.app.state.workbench_store
    user = store.get_user_by_session(session_digest=_session_digest(client))
    assert user is not None
    del tmp_path
    event = make_runtime_public_event(
        runtime_run_id="runtime-run-graph-contract",
        stage=stage,
        event_seq=event_seq,
        round_no=round_no,
        source_kind=source_kind,
        status="completed",
        counts=counts or {},
        created_at="2026-05-26T00:00:00Z",
    )
    store.append_runtime_public_event_by_ids(
        tenant_id=user.tenant_id,
        workspace_id=user.workspace_id,
        user_id=user.user_id,
        session_id=session_id,
        source_kind=source_kind,
        payload=event,
    )
```

- [ ] **Step 2: Add failing candidate-scope API tests**

Add these tests near `test_runtime_graph_source_nodes_are_accepted_by_graph_candidates_api` in `tests/test_workbench_api.py`:

```python
def test_runtime_round_score_graph_candidates_include_cts_and_liepin_review_items(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client, source_kinds=["cts", "liepin"])
    runs = {run["sourceKind"]: run for run in session["sourceRuns"]}
    _append_runtime_graph_event(
        tmp_path,
        client,
        session_id=session["sessionId"],
        stage="source_result",
        event_seq=101,
        round_no=1,
        source_kind="cts",
        counts={"roundReturned": 7, "roundIdentities": 7},
    )
    _append_runtime_graph_event(
        tmp_path,
        client,
        session_id=session["sessionId"],
        stage="source_result",
        event_seq=102,
        round_no=1,
        source_kind="liepin",
        counts={"roundReturned": 3, "roundIdentities": 3},
    )
    _append_runtime_graph_event(
        tmp_path,
        client,
        session_id=session["sessionId"],
        stage="scoring",
        event_seq=103,
        round_no=1,
        counts={"roundIdentities": 10, "topPoolCount": 10},
    )
    _insert_review_candidate(
        tmp_path,
        client,
        session_id=session["sessionId"],
        review_item_id="review-cts-round-1",
        display_name="CTS Round 1",
        source_round=1,
        evidence=[
            {
                "evidence_id": "evidence-cts-round-1",
                "source_run_id": runs["cts"]["sourceRunId"],
                "source_kind": "cts",
                "evidence_level": "detail",
                "provider_candidate_key_hash": "cts-provider-1",
                "score": 91,
            }
        ],
    )
    _insert_review_candidate(
        tmp_path,
        client,
        session_id=session["sessionId"],
        review_item_id="review-liepin-round-1",
        display_name="Liepin Round 1",
        source_round=1,
        evidence=[
            {
                "evidence_id": "evidence-liepin-round-1",
                "source_run_id": runs["liepin"]["sourceRunId"],
                "source_kind": "liepin",
                "evidence_level": "detail",
                "provider_candidate_key_hash": "liepin-provider-1",
                "score": 89,
            }
        ],
    )

    response = client.get(f"/api/workbench/sessions/{session['sessionId']}/graph-candidates?node_id=round-1-score")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["nodeScope"] == {
        "sessionId": session["sessionId"],
        "source": "all",
        "roundId": "1",
        "nodeKind": "scoring",
    }
    assert {item["sourceKind"] for item in payload["items"]} == {"cts", "liepin"}
    assert {item["displayName"] for item in payload["items"]} == {"CTS Round 1", "Liepin Round 1"}


def test_runtime_liepin_source_graph_candidates_filter_to_selected_round(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client, source_kinds=["cts", "liepin"])
    runs = {run["sourceKind"]: run for run in session["sourceRuns"]}
    for round_no in (1, 2):
        _append_runtime_graph_event(
            tmp_path,
            client,
            session_id=session["sessionId"],
            stage="source_result",
            event_seq=200 + round_no,
            round_no=round_no,
            source_kind="liepin",
            counts={"roundReturned": 3, "roundIdentities": 3},
        )
        _insert_review_candidate(
            tmp_path,
            client,
            session_id=session["sessionId"],
            review_item_id=f"review-liepin-round-{round_no}",
            display_name=f"Liepin Round {round_no}",
            source_round=round_no,
            evidence=[
                {
                    "evidence_id": f"evidence-liepin-round-{round_no}",
                    "source_run_id": runs["liepin"]["sourceRunId"],
                    "source_kind": "liepin",
                    "evidence_level": "detail",
                    "provider_candidate_key_hash": f"liepin-provider-{round_no}",
                    "score": 80 + round_no,
                }
            ],
        )

    response = client.get(
        f"/api/workbench/sessions/{session['sessionId']}/graph-candidates?node_id=round-2-source-liepin"
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["nodeScope"] == {
        "sessionId": session["sessionId"],
        "source": "liepin",
        "roundId": "2",
        "nodeKind": "liepin_card",
    }
    assert [item["displayName"] for item in payload["items"]] == ["Liepin Round 2"]


def test_runtime_graph_non_candidate_node_returns_recoverable_empty(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client, source_kinds=["cts", "liepin"])
    _approve_requirement_review(client=client, session_id=session["sessionId"])

    response = client.get(f"/api/workbench/sessions/{session['sessionId']}/graph-candidates?node_id=requirements")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["items"] == []
    assert payload["recoveryState"] == "recoverable_empty"
    assert payload["recoveryReason"] == "node_has_no_candidate_scope"


def test_runtime_round_score_candidate_snapshot_resolves_from_new_scope(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _bootstrap_and_login(client)
    session = _create_session(client, source_kinds=["cts", "liepin"])
    cts_run = next(run for run in session["sourceRuns"] if run["sourceKind"] == "cts")
    _insert_cts_graph_candidate_fixture(
        tmp_path,
        client,
        session_id=session["sessionId"],
        source_run_id=cts_run["sourceRunId"],
        runtime_run_id="runtime-run-secret-graph",
        count=1,
    )
    _append_runtime_graph_event(
        tmp_path,
        client,
        session_id=session["sessionId"],
        stage="scoring",
        event_seq=103,
        round_no=1,
        counts={"roundIdentities": 1, "topPoolCount": 1},
    )
    _insert_review_candidate(
        tmp_path,
        client,
        session_id=session["sessionId"],
        review_item_id="review-cts-round-1",
        display_name="CTS Round 1",
        source_round=1,
        evidence=[
            {
                "evidence_id": "evidence-cts-round-1",
                "source_run_id": cts_run["sourceRunId"],
                "source_kind": "cts",
                "evidence_level": "detail",
                "provider_candidate_key_hash": "cts-provider-1",
                "resume_id": "resume-1",
                "score": 91,
            }
        ],
    )

    candidates = client.get(
        f"/api/workbench/sessions/{session['sessionId']}/graph-candidates?node_id=round-1-score"
    )
    assert candidates.status_code == 200, candidates.text
    candidate = next(item for item in candidates.json()["items"] if item["displayName"] == "CTS Round 1")
    assert candidate["canExpandResume"] is True

    snapshot = client.get(
        f"/api/workbench/sessions/{session['sessionId']}/graph-candidates/{candidate['graphCandidateId']}/resume-snapshot"
    )

    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["status"] == "ready"
```

- [ ] **Step 3: Run candidate-scope tests and confirm failure**

Run:

```bash
uv run pytest \
  tests/test_workbench_api.py::test_runtime_round_score_graph_candidates_include_cts_and_liepin_review_items \
  tests/test_workbench_api.py::test_runtime_liepin_source_graph_candidates_filter_to_selected_round \
  tests/test_workbench_api.py::test_runtime_graph_non_candidate_node_returns_recoverable_empty \
  tests/test_workbench_api.py::test_runtime_round_score_candidate_snapshot_resolves_from_new_scope \
  -q
```

Expected: FAIL because `round-1-score` is CTS-only, `round-2-source-liepin` is not round-scoped, or non-candidate runtime graph nodes still return unsupported/404-style behavior.

- [ ] **Step 4: Add backend node-scope resolver**

In `src/seektalent_ui/runtime_graph.py`, add:

```python
def candidate_scope_for_node_id(
    *,
    session: object,
    events: list[object],
    runtime_source_state: object | None,
    detail_open_requests: list[object],
    final_top: object | None,
    node_id: str,
) -> WorkbenchRuntimeGraphCandidateScopeResponse | None:
    graph = build_runtime_graph(
        session=session,
        events=events,
        runtime_source_state=runtime_source_state,
        detail_open_requests=detail_open_requests,
        final_top=final_top,
    )
    for node in graph.nodes:
        if node.nodeId == node_id:
            return node.candidateScope
    return None
```

- [ ] **Step 5: Replace duplicated candidate node parsing**

In `src/seektalent_ui/workbench_candidate_graph.py`:

1. Keep `GraphNodeRef`, but build it from `WorkbenchRuntimeGraphCandidateScopeResponse`.
2. Change `list_graph_candidates call` to load the session, events, detail requests, and a final-top projection, then call `candidate_scope_for_node_id call`.
3. If the scope is missing or `scopeKind == "none"`, return `_empty_graph_candidate_response(session_id=session_id, node=node, recovery_reason="node_has_no_candidate_scope")`.
4. Map scopes:

```python
def _node_from_candidate_scope(node_id: str, scope: WorkbenchRuntimeGraphCandidateScopeResponse) -> GraphNodeRef:
    if scope.scopeKind == "round_recall":
        source = cast(Literal["cts", "liepin"], scope.sourceKind)
        return GraphNodeRef(
            node_id=node_id,
            source_kind=source,
            node_kind="recall" if source == "cts" else "liepin_card",
            round_no=scope.roundNo,
        )
    if scope.scopeKind == "round_score":
        return GraphNodeRef(node_id=node_id, source_kind="all", node_kind="scoring", round_no=scope.roundNo)
    if scope.scopeKind == "final":
        return GraphNodeRef(node_id=node_id, source_kind="all", node_kind="final")
    if scope.scopeKind == "detail_approval":
        return GraphNodeRef(node_id=node_id, source_kind="liepin", node_kind="detail_approval")
    return GraphNodeRef(node_id=node_id, source_kind="all", node_kind="recall", has_candidate_index=False)
```

Build the graph projection context inside `workbench_candidate_graph.py` without importing `workbench_routes.py`, to avoid a route/module cycle:

```python
from types import SimpleNamespace

from seektalent_ui.final_top_candidates import project_final_top_candidates


def _runtime_graph_context(
    *,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
) -> tuple[object, list[object], object | None, list[object], object | None] | None:
    session = store.get_workbench_session(user=user, session_id=session_id)
    if session is None:
        return None
    events = store.list_workbench_events(user=user, session_id=session_id, after_global_seq=0, limit=5000)
    detail_open_requests = store.list_liepin_detail_open_requests(user=user, session_id=session_id)
    final_review_items = store.list_runtime_final_top_review_items(user=user, session_id=session_id)
    final_top = SimpleNamespace(items=project_final_top_candidates(final_review_items, limit=10))
    return session, events, None, detail_open_requests, final_top
```

Use the same context helper for both `list_graph_candidates call` and `resolve_graph_candidate call`.

- [ ] **Step 6: Make resume snapshot resolution use the same runtime graph scopes**

Update `resolve_graph_candidate function` and `_candidate_node_refs function` in `workbench_candidate_graph.py` so the no-`node_id` path enumerates backend-authored runtime graph nodes rather than hard-coded CTS-only `round-N-score` refs.

Required behavior:

- `resolve_graph_candidate` with `node_id=None` builds the runtime graph context once.
- It iterates graph nodes whose `candidateScope.scopeKind` is not `"none"`.
- It converts each node's `candidateScope` through `_node_from_candidate_scope`.
- It still returns the same `ResolvedGraphCandidate` shape used by `resume_snapshot_projection.py`.
- It must find a candidate id that was generated for `round-1-score` with `sourceKind="all"` and `nodeKind="scoring"`.

- [ ] **Step 7: Implement all-source round scoring candidates**

Update `_all_candidates function` in `workbench_candidate_graph.py`:

```python
    if node.source_kind == "all" and node.node_kind == "scoring":
        candidates = _review_backed_candidates(
            settings=settings,
            graph_secret=graph_secret,
            store=store,
            user=user,
            session_id=session_id,
            node=node,
        )
        return _candidate_collection(candidates)
```

Update `_select_review_evidence function` so all-source scoring filters by `source_round`:

```python
def _select_review_evidence(
    evidence: list[WorkbenchCandidateEvidence],
    node: GraphNodeRef,
    *,
    item_source_round: int | None = None,
) -> WorkbenchCandidateEvidence | None:
    if node.round_no is not None and item_source_round != node.round_no:
        return None
    if node.source_kind in {"cts", "liepin"}:
        return _select_source_evidence(evidence, cast(Literal["cts", "liepin"], node.source_kind))
    return _strongest_evidence(evidence)
```

Update the call site in `_review_backed_candidates function`:

```python
        evidence = _select_review_evidence(item.evidence, node, item_source_round=item.source_round)
```

- [ ] **Step 8: Run candidate graph tests**

Run:

```bash
uv run pytest \
  tests/test_workbench_api.py::test_runtime_round_score_graph_candidates_include_cts_and_liepin_review_items \
  tests/test_workbench_api.py::test_runtime_liepin_source_graph_candidates_filter_to_selected_round \
  tests/test_workbench_api.py::test_runtime_graph_non_candidate_node_returns_recoverable_empty \
  tests/test_workbench_api.py::test_runtime_round_score_candidate_snapshot_resolves_from_new_scope \
  -q
```

Expected: PASS.

- [ ] **Step 9: Run related API tests**

Run:

```bash
uv run pytest tests/test_workbench_api.py -q
```

Expected: PASS or only unrelated existing branch failures. Fix failures caused by this task before continuing.

- [ ] **Step 10: Commit**

```bash
git add src/seektalent_ui/runtime_graph.py src/seektalent_ui/workbench_candidate_graph.py tests/test_workbench_api.py
git commit -m "fix: resolve graph candidates from runtime graph scopes"
```

---

## Task 5: Add Frontend Runtime Graph Adapter And Fetching

**Files:**
- Create: `apps/web-svelte/src/lib/workbench/runtimeGraphView.ts`
- Add: `apps/web-svelte/src/lib/workbench/runtimeGraphView.test.ts`
- Modify: `apps/web-svelte/src/lib/query/keys.ts`
- Modify: `apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte`

- [ ] **Step 1: Write adapter tests**

Create `apps/web-svelte/src/lib/workbench/runtimeGraphView.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { runtimeGraphToStory, workbenchNotesToLogEntries } from './runtimeGraphView';
import type { components } from '$lib/api/schema';

type RuntimeGraph = components['schemas']['WorkbenchRuntimeGraphResponse'];
type WorkbenchEvent = components['schemas']['WorkbenchEventResponse'];

describe('runtimeGraphToStory', () => {
	it('maps backend authored graph without changing node ids or business kinds', () => {
		const graph: RuntimeGraph = {
			sessionId: 'session-1',
			generatedAt: '2026-05-26T00:00:00Z',
			completionText: null,
			nodes: [
				{
					nodeId: 'round-1-score',
					kind: 'scoring',
					label: '第 1 轮 · Top Pool',
					summaryText: '第 1 轮评分完成，10 位候选人进入 Top Pool。',
					status: 'completed',
					stage: 'scoring',
					sourceKind: 'all',
					lane: 'shared',
					roundNo: 1,
					eventIds: ['runtime-test:1:scoring:all'],
					detailSections: [],
					candidateScope: { scopeKind: 'round_score', sourceKind: 'all', roundNo: 1, reason: null }
				}
			],
			edges: []
		};

		const story = runtimeGraphToStory(graph);

		expect(story.graphNodes).toHaveLength(1);
		expect(story.graphNodes[0].id).toBe('round-1-score');
		expect(story.graphNodes[0].kind).toBe('评分');
		expect(story.graphNodes[0].detailPayload?.kind).toBe('runtimeGraphNode');
		expect(story.graphNodes[0].detailPayload?.node.nodeId).toBe('round-1-score');
	});

	it('keeps Workbench running notes from public events without rebuilding graph semantics', () => {
		const events: WorkbenchEvent[] = [
			{
				globalSeq: 42,
				eventName: 'workbench_note_created',
				sourceKind: null,
				sourceRunId: null,
				payload: {
					text: 'CTS 和猎聘已完成本轮检索，正在合并候选人。',
					eventSeq: 42,
					noteKind: 'progress',
					statusHint: 'new_progress'
				}
			} as WorkbenchEvent
		];

		expect(workbenchNotesToLogEntries(events)).toMatchObject([
			{
				id: 'workbench-note-42',
				text: 'CTS 和猎聘已完成本轮检索，正在合并候选人。',
				tag: 'SYS',
				sourceKind: 'all'
			}
		]);
	});
});
```

- [ ] **Step 2: Run adapter test and confirm failure**

Run:

```bash
cd apps/web-svelte && bun run test -- src/lib/workbench/runtimeGraphView.test.ts
```

Expected: FAIL because `runtimeGraphView.ts` does not exist.

- [ ] **Step 3: Add generic runtime graph detail type**

Modify `apps/web-svelte/src/lib/workbench/recruiterAnimation.ts` by adding a backend node payload variant:

```ts
type WorkbenchRuntimeGraphNode = components['schemas']['WorkbenchRuntimeGraphNodeResponse'];

export type RecruiterGraphDetailPayload =
	| {
			kind: 'runtimeGraphNode';
			node: WorkbenchRuntimeGraphNode;
	  }
	| /* keep the existing variants temporarily until Task 7 removes active usage */;
```

Do not add any business-specific detail variants for the new backend graph.

- [ ] **Step 4: Implement adapter**

Create `apps/web-svelte/src/lib/workbench/runtimeGraphView.ts`:

```ts
import type { components } from '$lib/api/schema';
import type {
	RecruiterGraphEdge,
	RecruiterLogEntry,
	RecruiterGraphNode,
	RecruiterLane,
	RecruiterTone,
	SourceKind
} from './recruiterAnimation';

type RuntimeGraph = components['schemas']['WorkbenchRuntimeGraphResponse'];
type RuntimeGraphNode = components['schemas']['WorkbenchRuntimeGraphNodeResponse'];
type RuntimeGraphEdge = components['schemas']['WorkbenchRuntimeGraphEdgeResponse'];
type WorkbenchEvent = components['schemas']['WorkbenchEventResponse'];

export type RuntimeGraphStory = {
	criteria: null;
	graphNodes: RecruiterGraphNode[];
	graphEdges: RecruiterGraphEdge[];
	logEntries: RecruiterLogEntry[];
	completionText: string | null;
};

const kindLabels: Record<string, RecruiterGraphNode['kind']> = {
	job: '岗位',
	requirements: '拆解',
	query: '检索',
	round_query: '检索',
	source_result: '检索',
	merge: '命中',
	scoring: '评分',
	feedback: '反思',
	detail_approval: '详情审批',
	final: '排序'
};

export function runtimeGraphToStory(graph: RuntimeGraph, events: WorkbenchEvent[] = []): RuntimeGraphStory {
	return {
		criteria: null,
		graphNodes: graph.nodes.map(runtimeNodeToRecruiterNode),
		graphEdges: graph.edges.map(runtimeEdgeToRecruiterEdge),
		logEntries: workbenchNotesToLogEntries(events),
		completionText: graph.completionText
	};
}

export function workbenchNotesToLogEntries(events: WorkbenchEvent[]): RecruiterLogEntry[] {
	return events
		.filter((event) => event.eventName === 'workbench_note_created')
		.map((event) => {
			const payload = event.payload as Record<string, unknown>;
			const sequence = Number(payload.eventSeq ?? payload.event_seq ?? event.globalSeq);
			return {
				id: `workbench-note-${String(sequence)}`,
				at: Number.isFinite(sequence) ? sequence : event.globalSeq,
				tag: 'SYS',
				text: String(payload.text ?? '').trim(),
				sourceKind: event.sourceKind ?? 'all',
				sourceLabel: event.sourceKind === 'cts' ? 'CTS' : event.sourceKind === 'liepin' ? '猎聘' : '全部来源',
				lane: event.sourceKind ?? 'shared',
				relatedNodeId: undefined
			};
		})
		.filter((entry) => entry.text.length > 0)
		.sort((left, right) => left.at - right.at || left.id.localeCompare(right.id));
}

function runtimeNodeToRecruiterNode(node: RuntimeGraphNode): RecruiterGraphNode {
	const sourceKind = node.sourceKind === 'cts' || node.sourceKind === 'liepin' ? node.sourceKind : 'all';
	return {
		id: node.nodeId,
		at: node.roundNo ?? 0,
		kind: kindLabels[node.kind] ?? kindLabels[node.stage] ?? '检索',
		label: node.label,
		detail: node.summaryText,
		x: 0,
		y: 50,
		tone: toneForStatus(node.status),
		sourceKind,
		sourceLabel: sourceLabel(sourceKind),
		lane: laneForNode(node),
		detailKind: undefined,
		detailPayload: { kind: 'runtimeGraphNode', node },
		eventIds: node.eventIds,
		sourceRunId: null,
		candidateReviewItemIds: [],
		candidateEvidenceRefs: [],
		detailOpenRequestIds: []
	};
}

function runtimeEdgeToRecruiterEdge(edge: RuntimeGraphEdge): RecruiterGraphEdge {
	return {
		from: edge.fromNodeId,
		to: edge.toNodeId,
		label: edge.label ?? undefined,
		tone: 'blue'
	};
}

function toneForStatus(status: RuntimeGraphNode['status']): RecruiterTone {
	if (status === 'completed') return 'green';
	if (status === 'running') return 'blue';
	if (status === 'partial' || status === 'blocked' || status === 'degraded') return 'amber';
	if (status === 'failed' || status === 'cancelled') return 'rose';
	return 'neutral';
}

function laneForNode(node: RuntimeGraphNode): RecruiterLane {
	if (node.lane === 'cts' || node.lane === 'liepin') return node.lane;
	return 'shared';
}

function sourceLabel(sourceKind: SourceKind | 'all') {
	if (sourceKind === 'cts') return 'CTS';
	if (sourceKind === 'liepin') return '猎聘';
	return '全部来源';
}
```

- [ ] **Step 5: Add query key**

Modify `apps/web-svelte/src/lib/query/keys.ts`:

```ts
runtimeGraph: (sessionId: string) => ['workbench', 'sessions', sessionId, 'runtime-graph'] as const,
```

- [ ] **Step 6: Wire page data fetch**

In `apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte`:

1. Import `getRuntimeGraph`.
2. Import `runtimeGraphToStory`.
3. Add a query:

```ts
const runtimeGraphQuery = createQuery(() => ({
	queryKey: workbenchKeys.runtimeGraph(data.sessionId),
	queryFn: () => getRuntimeGraph(data.sessionId),
	enabled: Boolean(sessionQuery.data)
}));
```

4. Replace the `story` derived value and keep Workbench note events attached to the story:

```ts
const story = $derived(
	runtimeGraphQuery.data
		? runtimeGraphToStory(runtimeGraphQuery.data, eventsQuery.data?.events ?? [])
		: null
);
```

5. In `refreshSession`, invalidate `workbenchKeys.runtimeGraph(data.sessionId)`.
6. Pass `runtimeGraphQuery.isPending` and `runtimeGraphQuery.error` into `StrategyCanvas` loading/error props. Do not keep `eventsQuery.isPending` as the graph loading state after the graph moves to `/runtime-graph`.
7. Keep `eventsQuery` for `ActivityLog` running notes and requirement-preparation status only. Do not use it to construct graph nodes.

- [ ] **Step 7: Run frontend adapter test**

Run:

```bash
cd apps/web-svelte && bun run test -- src/lib/workbench/runtimeGraphView.test.ts
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/web-svelte/src/lib/workbench/runtimeGraphView.ts apps/web-svelte/src/lib/workbench/runtimeGraphView.test.ts apps/web-svelte/src/lib/workbench/recruiterAnimation.ts apps/web-svelte/src/lib/query/keys.ts apps/web-svelte/src/routes/'(app)'/sessions/'[sessionId]'/+page.svelte
git commit -m "feat: render backend-authored runtime graph"
```

---

## Task 6: Replace Business-Specific Node Details With Generic Sections

**Files:**
- Modify: `apps/web-svelte/src/lib/components/NodeDetailPanel.svelte`
- Modify: `apps/web-svelte/src/lib/components/GraphNodeCandidateList.svelte`
- Add or modify: `apps/web-svelte/src/lib/components/NodeDetailPanel.test.ts`
- Modify: `apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte`

- [ ] **Step 1: Write node detail tests**

Create `apps/web-svelte/src/lib/components/NodeDetailPanel.test.ts`:

```ts
import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import NodeDetailPanel from './NodeDetailPanel.svelte';
import type { RecruiterGraphNode } from '$lib/workbench/recruiterAnimation';
import type { components } from '$lib/api/schema';

type RuntimeNode = components['schemas']['WorkbenchRuntimeGraphNodeResponse'];
type CandidatePage = components['schemas']['WorkbenchGraphCandidateListResponse'];

function runtimeNode(overrides: Partial<RuntimeNode> = {}): RuntimeNode {
	return Object.assign({
		nodeId: 'round-1-score',
		kind: 'scoring',
		label: '第 1 轮 · Top Pool',
		summaryText: '第 1 轮评分完成，10 位候选人进入 Top Pool。',
		status: 'completed',
		stage: 'scoring',
		sourceKind: 'all',
		lane: 'shared',
		roundNo: 1,
		eventIds: [],
		detailSections: [
			{
				heading: '本轮评分',
				kind: 'facts',
				text: null,
				facts: [
					{ label: '进入评分', value: '10 人' },
					{ label: 'Top Pool', value: '10 人' }
				],
				values: []
			}
		],
		candidateScope: { scopeKind: 'round_score', sourceKind: 'all', roundNo: 1, reason: null }
	}, overrides);
}

function graphNode(node: RuntimeNode): RecruiterGraphNode {
	return {
		id: node.nodeId,
		at: 1,
		kind: '评分',
		label: node.label,
		detail: node.summaryText,
		x: 0,
		y: 50,
		tone: 'green',
		sourceKind: 'all',
		sourceLabel: '全部来源',
		lane: 'shared',
		detailPayload: { kind: 'runtimeGraphNode', node },
		eventIds: [],
		sourceRunId: null,
		candidateReviewItemIds: [],
		candidateEvidenceRefs: [],
		detailOpenRequestIds: []
	};
}

function emptyPage(nodeId: string, recoveryReason = 'node_has_no_candidate_scope'): CandidatePage {
	return {
		nodeId,
		nodeScope: { sessionId: 'session-1', source: 'all', roundId: null, nodeKind: 'recall' },
		items: [],
		nextCursor: null,
		totalSourceResults: 0,
		totalGraphCandidates: 0,
		totalEstimate: 0,
		coverage: {
			sourceResultIdsSeen: [],
			missingSafeIdentityCount: 0,
			missingSnapshotCount: 0,
			forbiddenSnapshotCount: 0,
			droppedRows: 0
		},
		truncated: false,
		generatedAt: '2026-05-26T00:00:00Z',
		recoveryState: 'recoverable_empty',
		recoveryReason
	};
}

describe('NodeDetailPanel runtime graph details', () => {
	it('renders backend-authored natural text and fact sections', () => {
		render(NodeDetailPanel, {
			props: {
				node: graphNode(runtimeNode()),
				graphCandidatePage: emptyPage('round-1-score')
			}
		});

		expect(screen.getByText('第 1 轮 · Top Pool')).toBeInTheDocument();
		expect(screen.getByText('第 1 轮评分完成，10 位候选人进入 Top Pool。')).toBeInTheDocument();
		expect(screen.getByText('本轮评分')).toBeInTheDocument();
		expect(screen.getByText('进入评分')).toBeInTheDocument();
		expect(screen.getAllByText('10 人').length).toBeGreaterThan(0);
	});

	it('renders recoverable empty candidate state without surfacing a 404', () => {
		render(NodeDetailPanel, {
			props: {
				node: graphNode(runtimeNode({ nodeId: 'requirements', label: '需求拆解' })),
				graphCandidatePage: emptyPage('requirements')
			}
		});

		expect(screen.getByText('候选人索引需要恢复')).toBeInTheDocument();
		expect(screen.getByText('node_has_no_candidate_scope')).toBeInTheDocument();
	});
});
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
cd apps/web-svelte && bun run test -- src/lib/components/NodeDetailPanel.test.ts
```

Expected: FAIL because `NodeDetailPanel` does not accept `graphCandidatePage` and still uses business payload switches.

- [ ] **Step 3: Change NodeDetailPanel props**

In `NodeDetailPanel.svelte`, replace `graphCandidates?: WorkbenchGraphCandidateSummary[]` with:

```ts
type WorkbenchGraphCandidateListResponse =
	components['schemas']['WorkbenchGraphCandidateListResponse'];

type NodeDetailPanelProps = {
	node: RecruiterGraphNode | null;
	graphCandidatePage?: WorkbenchGraphCandidateListResponse | null;
	graphCandidatesLoading?: boolean;
	graphCandidatesError?: string | null;
	selectedGraphCandidateId?: string | null;
	resumeSnapshot?: WorkbenchGraphCandidateResumeSnapshot | null;
	resumeSnapshotLoading?: boolean;
	resumeSnapshotError?: string | null;
	onSelectGraphCandidate?: (candidate: WorkbenchGraphCandidateSummary) => void;
};
```

Derive candidates from the page:

```ts
const graphCandidates = $derived(graphCandidatePage?.items ?? []);
```

- [ ] **Step 4: Render runtime graph detail sections generically**

Add these helpers to `NodeDetailPanel.svelte`:

```ts
const runtimeNode = $derived(
	node?.detailPayload?.kind === 'runtimeGraphNode' ? node.detailPayload.node : null
);
const runtimeSections = $derived(runtimeNode?.detailSections ?? []);

function candidateScopeText() {
	const scope = runtimeNode?.candidateScope;
	if (!scope || scope.scopeKind === 'none') {
		return scope?.reason ?? '该节点没有候选人列表。';
	}
	return [scope.scopeKind, scope.sourceKind, scope.roundNo ? `第 ${scope.roundNo} 轮` : null]
		.filter(Boolean)
		.join(' · ');
}
```

In the markup, before candidate rendering, add a runtime branch:

```svelte
{#if runtimeNode}
	<section class="node-detail-section" aria-label="节点业务细节">
		<section class="node-detail-block">
			<span>节点说明</span>
			<p>{runtimeNode.summaryText}</p>
		</section>
		<section class="node-detail-block">
			<span>候选人范围</span>
			<p>{candidateScopeText()}</p>
		</section>
		{#each runtimeSections as section (`${section.heading}-${section.kind}`)}
			<section class="node-detail-block">
				<span>{section.heading}</span>
				{#if section.kind === 'text'}
					<p class:muted={!section.text}>{section.text || '暂无数据'}</p>
				{:else if section.kind === 'facts'}
					{#if section.facts.length > 0}
						<div class="node-detail-facts">
							{#each section.facts as fact (`${fact.label}-${fact.value}`)}
								<div class="node-detail-row">
									<span>{fact.label}</span>
									<strong>{fact.value}</strong>
								</div>
							{/each}
						</div>
					{:else}
						<p class="muted">暂无数据</p>
					{/if}
				{:else}
					{#if section.values.length > 0}
						<ul>
							{#each section.values as value (value)}
								<li>{value}</li>
							{/each}
						</ul>
					{:else}
						<p class="muted">暂无数据</p>
					{/if}
				{/if}
			</section>
		{/each}
	</section>
{:else if detailItems.length > 0}
	<!-- keep the existing legacy branch only until Task 7 removes active usage -->
{/if}
```

- [ ] **Step 5: Use graph candidate page metadata**

Move `GraphNodeCandidateList.svelte` into `NodeDetailPanel` or render the same fields directly. The active UI must display:

- loaded item count
- total graph candidates
- source total
- coverage missing snapshot / forbidden snapshot / dropped rows
- recoverable-empty reason
- truncated notice

The simplest implementation is to import `GraphNodeCandidateList` and replace the current candidate section with:

```svelte
<GraphNodeCandidateList
	sessionId={sessionId}
	{node}
	page={graphCandidatePage ?? null}
	loading={graphCandidatesLoading}
	error={graphCandidatesError}
	onSelectGraphCandidate={selectGraphCandidate}
	selectedGraphCandidateId={selectedGraphCandidateId}
/>
```

If `GraphNodeCandidateList` currently lacks `onSelectGraphCandidate`, add those props and make each card button call the callback.

Also remove `detailKind`-based title selection from `GraphNodeCandidateList.svelte`. Runtime graph nodes should derive the candidate-list title from backend data:

```ts
function graphCandidateListTitle(currentNode: RecruiterGraphNode, page: WorkbenchGraphCandidateListResponse | null) {
	const scope = page?.nodeScope;
	if (scope?.nodeKind === 'scoring') return '评分简历';
	if (scope?.nodeKind === 'final') return '最终候选人';
	if (scope?.nodeKind === 'detail_approval') return '待处理简历';
	if (scope?.source === 'cts') return 'CTS 召回简历';
	if (scope?.source === 'liepin') return '猎聘简历';
	return currentNode.label;
}
```

- [ ] **Step 6: Update page prop wiring**

In the session page, pass the full candidate page:

```svelte
<NodeDetailPanel
	node={selectedNode}
	graphCandidatePage={graphCandidatesQuery.data ?? null}
	graphCandidatesLoading={graphCandidatesQuery.isPending && Boolean(selectedNode)}
	graphCandidatesError={graphCandidatesQuery.error
		? safeErrorMessage(graphCandidatesQuery.error, '候选人加载失败')
		: null}
	selectedGraphCandidateId={selectedGraphCandidate?.graphCandidateId ?? null}
	resumeSnapshot={resumeSnapshotQuery.data ?? null}
	resumeSnapshotLoading={resumeSnapshotQuery.isPending && Boolean(selectedGraphCandidate)}
	resumeSnapshotError={resumeSnapshotQuery.error
		? safeErrorMessage(resumeSnapshotQuery.error, '简历摘要加载失败')
		: null}
	onSelectGraphCandidate={(candidate) => {
		selectedGraphCandidate = candidate;
	}}
/>
```

- [ ] **Step 7: Run component tests**

Run:

```bash
cd apps/web-svelte && bun run test -- src/lib/components/NodeDetailPanel.test.ts
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/web-svelte/src/lib/components/NodeDetailPanel.svelte apps/web-svelte/src/lib/components/GraphNodeCandidateList.svelte apps/web-svelte/src/lib/components/NodeDetailPanel.test.ts apps/web-svelte/src/routes/'(app)'/sessions/'[sessionId]'/+page.svelte
git commit -m "refactor: render runtime graph node details generically"
```

---

## Task 7: Remove Active Frontend Runtime Graph Duplication

**Files:**
- Modify or delete: `apps/web-svelte/src/lib/workbench/runStory.ts`
- Modify or delete: `apps/web-svelte/src/lib/workbench/runStory.test.ts`
- Modify: `apps/web-svelte/src/lib/components/StrategyCanvas.svelte`
- Modify: `apps/web-svelte/src/lib/components/ActivityLog.svelte`
- Modify: `apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte`

- [ ] **Step 1: Scan active frontend graph duplication**

Run:

```bash
rg -n "buildRunStory\\(|ctsRoundScoring|ctsRoundResults|liepinCardSearch|liepinCardCandidates|liepinDetailApproval|detailKind" apps/web-svelte/src/routes apps/web-svelte/src/lib/components apps/web-svelte/src/lib/workbench
```

Expected before cleanup: matches in `runStory.ts`, `NodeDetailPanel.svelte`, and tests.

- [ ] **Step 2: Remove active `buildRunStory` session usage**

The session page must not import `buildRunStory`. Confirm the import is gone:

```bash
rg -n "buildRunStory" apps/web-svelte/src/routes
```

Expected: no output.

If `ActivityLog` still needs story-shaped data, give it either:

- runtime graph response derived log entries from `runtimeGraphView.ts`, or
- the existing running notes/event data it already receives.

Do not reintroduce frontend business graph construction to support activity notes.

- [ ] **Step 3: Delete or quarantine `runStory.ts`**

If no active component imports `runStory.ts`, delete:

```bash
git rm apps/web-svelte/src/lib/workbench/runStory.ts apps/web-svelte/src/lib/workbench/runStory.test.ts
```

Use the `RuntimeGraphStory` type already exported by `apps/web-svelte/src/lib/workbench/runtimeGraphView.ts`:

```ts
export type RuntimeGraphStory = {
	criteria: null;
	graphNodes: RecruiterGraphNode[];
	graphEdges: RecruiterGraphEdge[];
	logEntries: RecruiterLogEntry[];
	completionText: string | null;
};
```

Then update `StrategyCanvas.svelte`, `StrategyGraph.svelte`, and `ActivityLog.svelte` imports from `RunStory` to `RuntimeGraphStory` or an even narrower local prop type.

- [ ] **Step 4: Remove runtime business detail switches from active NodeDetailPanel**

After the generic runtime branch is active, remove the switch cases for:

- `ctsRoundQuery`
- `ctsRoundResults`
- `ctsRoundScoring`
- `reflection`
- `liepinCardSearch`
- `liepinCardCandidates`
- `liepinDetailApproval`
- `aggregation`

The only active node detail path for strategy graph nodes should be `runtimeGraphNode`.

- [ ] **Step 5: Run cleanup scans**

Run:

```bash
rg -n "buildRunStory\\(" apps/web-svelte/src/routes apps/web-svelte/src/lib/components
rg -n "ctsRoundScoring|ctsRoundResults|liepinCardSearch|liepinCardCandidates|liepinDetailApproval|aggregation" apps/web-svelte/src/lib/components
```

Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add apps/web-svelte/src
git commit -m "refactor: remove frontend runtime graph reconstruction"
```

---

## Task 8: End-To-End Verification And Manual Test Readiness

**Files:**
- Modify tests only if this task finds assertions that still point to the deleted frontend graph contract.

- [ ] **Step 1: Run backend focused tests**

Run:

```bash
uv run pytest tests/test_workbench_runtime_graph.py tests/test_workbench_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend focused tests**

Run:

```bash
cd apps/web-svelte && bun run test -- src/lib/workbench/runtimeGraphView.test.ts src/lib/components/NodeDetailPanel.test.ts
```

Expected: PASS.

- [ ] **Step 3: Run frontend quality gates**

Run:

```bash
cd apps/web-svelte && bun run check && bun run lint && bun run build
```

Expected: PASS.

- [ ] **Step 4: Run cleanup acceptance scans**

Run:

```bash
rg -n "buildRunStory\\(" apps/web-svelte/src/routes apps/web-svelte/src/lib/components
rg -n "ctsRoundScoring|ctsRoundResults|liepinCardSearch|liepinCardCandidates|liepinDetailApproval|aggregation" apps/web-svelte/src/lib/components
rg -n "runtime-graph" src/seektalent_ui apps/web-svelte/src
```

Expected:

- First command: no output.
- Second command: no output.
- Third command: shows backend route, frontend API wrapper, query key, and runtime graph adapter usage.

- [ ] **Step 5: Run manual dual-source smoke test**

Start the app using the repo's normal Workbench dev command. Then run one dual-source session with CTS + Liepin.

Manual assertions:

- Requirements node appears from backend graph.
- Round query node appears.
- CTS and Liepin source nodes appear for the same round when both sources run.
- `round-N-score` appears once as all-source Top Pool/scoring node.
- Clicking `round-N-score` shows both CTS and Liepin candidates when both were scored.
- Clicking `round-N-source-liepin` shows only that round's Liepin candidates.
- Clicking `requirements` shows detail sections and no-candidate state, not a red candidate load error.
- Liepin detail approval node shows request summaries and budget/status text.
- Final node shows final candidate scope.

- [ ] **Step 6: Commit verification fixes**

If the verification task required test or wiring fixes, commit them:

```bash
git add src apps tests
git commit -m "test: verify runtime-authored graph contract"
```

If no files changed, do not create an empty commit.
