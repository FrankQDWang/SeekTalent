from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal, cast

from seektalent_ui.models import (
    SourceKind,
    WorkbenchRuntimeGraphFactResponse,
    WorkbenchRuntimeGraphCandidateScopeResponse,
    WorkbenchRuntimeGraphEdgeResponse,
    WorkbenchRuntimeGraphNodeResponse,
    WorkbenchRuntimeGraphNodeStatus,
    WorkbenchRuntimeGraphResponse,
    WorkbenchRuntimeGraphSectionResponse,
    WorkbenchRuntimeGraphSourceKind,
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
_REDACTED_KEY_LOOKUP = {item.casefold() for item in _REDACTED_KEYS}


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
    return lowered in _REDACTED_KEY_LOOKUP or any(
        token in lowered for token in ("cookie", "token", "authorization", "artifact", "storage")
    )


def build_runtime_graph(
    *,
    session: object,
    events: Sequence[object],
    runtime_source_state: object | None,
    detail_open_requests: Sequence[object],
    final_top: object | None,
) -> WorkbenchRuntimeGraphResponse:
    graph = _GraphBuilder(session_id=_attr_text(session, "session_id") or _attr_text(session, "sessionId") or "")
    job_title = _attr_text(session, "job_title") or _attr_text(session, "jobTitle") or "岗位"
    jd_text = _attr_text(session, "jd_text") or _attr_text(session, "jdText") or ""
    notes = _attr_text(session, "notes") or ""
    selected_sources = _session_sources(session)

    graph.add_node(
        nodeId="job",
        kind="job",
        label=f"岗位需求 / {job_title}",
        summaryText=f"{job_title}，{_source_mode_text(selected_sources)}。",
        status="completed",
        stage="job",
        sourceKind="all",
        lane="shared",
        detailSections=_compact_sections(
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
        candidateScope=_none_scope("job_has_no_candidate_scope"),
    )

    requirement_review = getattr(session, "requirement_review", None)
    requirement_sheet = _requirement_sheet(requirement_review)
    if requirement_sheet is not None:
        graph.add_node(
            nodeId="requirements",
            kind="requirements",
            label="需求拆解",
            summaryText=_requirements_summary(requirement_sheet),
            status="completed",
            stage="requirements",
            sourceKind="all",
            lane="shared",
            detailSections=_requirement_sections(requirement_sheet),
            candidateScope=_none_scope("requirements_have_no_candidate_scope"),
        )
        graph.add_edge("job", "requirements", "提取约束")
        previous_node = "requirements"
    else:
        previous_node = "job"

    round_events = [_runtime_event(event) for event in events]
    round_events = [event for event in round_events if event is not None]
    rounds = sorted({event["roundNo"] for event in round_events if isinstance(event.get("roundNo"), int)})
    if not rounds and _runtime_graph_has_started(session=session, runtime_source_state=runtime_source_state):
        rounds = [1]
    for round_no in rounds:
        events_for_round = [event for event in round_events if event.get("roundNo") == round_no]
        query_id = f"round-{round_no}-query"
        graph.add_node(
            nodeId=query_id,
            kind="query",
            label=f"第 {round_no} 轮 · 查询包",
            summaryText=f"第 {round_no} 轮查询策略已生成。",
            status=_round_status(events_for_round, "round_query", None),
            stage="round_query",
            sourceKind="all",
            lane="shared",
            roundNo=round_no,
            eventIds=_event_ids(events_for_round, "round_query", None),
            detailSections=[
                section_from_facts(
                    "查询包",
                    [
                        ("轮次", f"第 {round_no} 轮"),
                        ("Top Pool", _count_text(events_for_round, "round_query", None, "topPoolCount")),
                    ],
                )
            ],
            candidateScope=_none_scope("round_query_has_no_candidate_scope"),
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
                nodeId=node_id,
                kind="source_result",
                label=f"第 {round_no} 轮 · {_source_label(source)} 检索",
                summaryText=f"{_source_label(source)} 返回 {returned} 份原始简历，形成 {identities} 位候选人。",
                status=_safe_status(source_event.get("status")),
                stage="source_result",
                sourceKind=source,
                lane=source,
                roundNo=round_no,
                eventIds=[str(source_event.get("eventId"))],
                detailSections=[
                    section_from_facts(
                        "来源结果",
                        [
                            ("来源", _source_label(source)),
                            ("原始返回", f"{returned} 份"),
                            ("候选人", f"{identities} 位"),
                            ("累计返回", _optional_count(counts.get("sourceCumulativeReturned"), "份")),
                            ("累计候选人", _optional_count(counts.get("sourceCumulativeIdentities"), "位")),
                            ("状态", _safe_status(source_event.get("status"))),
                            ("安全原因", source_event.get("safeReasonCode")),
                        ],
                    )
                ],
                candidateScope=WorkbenchRuntimeGraphCandidateScopeResponse(
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
                nodeId=merge_id,
                kind="merge",
                label=f"第 {round_no} 轮 · 合并去重",
                summaryText=f"第 {round_no} 轮完成跨源合并去重。",
                status=_safe_status(merge_event.get("status") if merge_event else None),
                stage="merge",
                sourceKind="all",
                lane="shared",
                roundNo=round_no,
                eventIds=[str(merge_event.get("eventId"))] if merge_event else [],
                detailSections=[
                    section_from_facts(
                        "合并去重",
                        [("身份数", _count_text(events_for_round, "merge", None, "mergedIdentities"))],
                    )
                ],
                candidateScope=_none_scope("merge_node_uses_score_candidate_scope"),
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
                nodeId=score_id,
                kind="scoring",
                label=f"第 {round_no} 轮 · Top Pool",
                summaryText=_score_summary(round_no, identities, top_pool),
                status=_safe_status(score_event.get("status")),
                stage="scoring",
                sourceKind="all",
                lane="shared",
                roundNo=round_no,
                eventIds=[str(score_event.get("eventId"))],
                detailSections=[
                    section_from_facts(
                        "本轮评分",
                        [
                            ("进入评分", _optional_count(identities, "人")),
                            ("Top Pool", _optional_count(top_pool, "人")),
                            ("状态", _safe_status(score_event.get("status"))),
                        ],
                    )
                ],
                candidateScope=WorkbenchRuntimeGraphCandidateScopeResponse(
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
                nodeId=feedback_id,
                kind="feedback",
                label=f"第 {round_no} 轮 · 下一轮策略",
                summaryText=f"第 {round_no} 轮复盘完成，准备下一轮策略。",
                status=_safe_status(feedback_event.get("status")),
                stage="feedback",
                sourceKind="all",
                lane="shared",
                roundNo=round_no,
                eventIds=[str(feedback_event.get("eventId"))],
                detailSections=[
                    section_from_facts(
                        "复盘",
                        [("参与候选人", _count_text(events_for_round, "feedback", None, "feedbackCandidateCount"))],
                    )
                ],
                candidateScope=_none_scope("feedback_has_no_candidate_scope"),
            )
            graph.add_edge(previous_node, feedback_id, "反馈")
            previous_node = feedback_id

    if detail_open_requests:
        graph.add_node(
            nodeId="liepin-detail-approval",
            kind="detail_approval",
            label=f"详情审批 · {len(detail_open_requests)} 个",
            summaryText=f"猎聘详情审批队列有 {len(detail_open_requests)} 个请求。",
            status="completed",
            stage="detail_approval",
            sourceKind="liepin",
            lane="liepin",
            detailSections=_detail_request_sections(detail_open_requests),
            candidateScope=WorkbenchRuntimeGraphCandidateScopeResponse(
                scopeKind="detail_approval",
                sourceKind="liepin",
                roundNo=None,
            ),
        )
        graph.add_edge(previous_node, "liepin-detail-approval", "详情队列")
        previous_node = "liepin-detail-approval"

    final_top_items = getattr(final_top, "items", []) if final_top is not None else []
    if final_top_items:
        graph.add_node(
            nodeId="final-shortlist",
            kind="final",
            label=f"最终短名单 · {len(final_top_items)} 人",
            summaryText=f"最终 Top 10 已生成，共 {len(final_top_items)} 人。",
            status="completed",
            stage="final",
            sourceKind="all",
            lane="shared",
            detailSections=[
                section_from_facts(
                    "最终短名单",
                    [
                        ("候选人", f"{len(final_top_items)} 人"),
                        ("覆盖状态", getattr(final_top, "coverageStatus", None)),
                        ("完成版本", getattr(final_top, "finalizationRevision", None)),
                    ],
                )
            ],
            candidateScope=WorkbenchRuntimeGraphCandidateScopeResponse(scopeKind="final", sourceKind="all"),
        )
        graph.add_edge(previous_node, "final-shortlist", "Top 10")

    return graph.response(completion_text="检索完成 · 候选人进入短名单" if final_top is not None else None)


def candidate_scope_for_node_id(
    *,
    session: object,
    events: Sequence[object],
    runtime_source_state: object | None,
    detail_open_requests: Sequence[object],
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


class _GraphBuilder:
    def __init__(self, *, session_id: str) -> None:
        self.session_id = session_id
        self.nodes: list[WorkbenchRuntimeGraphNodeResponse] = []
        self.edges: list[WorkbenchRuntimeGraphEdgeResponse] = []

    def add_node(
        self,
        *,
        nodeId: str,
        kind: str,
        label: str,
        summaryText: str,
        status: WorkbenchRuntimeGraphNodeStatus,
        stage: str,
        candidateScope: WorkbenchRuntimeGraphCandidateScopeResponse,
        sourceKind: WorkbenchRuntimeGraphSourceKind = "all",
        lane: Literal["shared", "cts", "liepin"] = "shared",
        roundNo: int | None = None,
        eventIds: list[str] | None = None,
        detailSections: list[WorkbenchRuntimeGraphSectionResponse] | None = None,
    ) -> None:
        self.nodes.append(
            WorkbenchRuntimeGraphNodeResponse(
                nodeId=nodeId,
                kind=kind,
                label=label,
                summaryText=summaryText,
                status=status,
                stage=stage,
                sourceKind=sourceKind,
                lane=lane,
                roundNo=roundNo,
                eventIds=eventIds or [],
                detailSections=detailSections or [],
                candidateScope=candidateScope,
            )
        )

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


def _compact_sections(
    sections: list[WorkbenchRuntimeGraphSectionResponse | None],
) -> list[WorkbenchRuntimeGraphSectionResponse]:
    return [section for section in sections if section is not None]


def _attr_text(value: object, name: str) -> str | None:
    item = getattr(value, name, None)
    if isinstance(item, str) and item.strip():
        return item.strip()
    return None


def _session_sources(session: object) -> list[SourceKind]:
    source_runs = getattr(session, "source_runs", None) or getattr(session, "sourceRuns", None) or []
    sources: list[SourceKind] = []
    for source_run in source_runs:
        source = getattr(source_run, "source_kind", None) or getattr(source_run, "sourceKind", None)
        if source == "cts" and source not in sources:
            sources.append("cts")
        elif source == "liepin" and source not in sources:
            sources.append("liepin")
    default_sources: list[SourceKind] = ["cts"]
    return sources or default_sources


def _runtime_graph_has_started(*, session: object, runtime_source_state: object | None) -> bool:
    source_runs = getattr(session, "source_runs", None) or getattr(session, "sourceRuns", None) or []
    for source_run in source_runs:
        status = getattr(source_run, "status", None)
        if status in {"running", "completed", "partial", "failed", "blocked", "cancelled"}:
            return True
    for source_state in getattr(runtime_source_state, "sources", []) or []:
        status = getattr(source_state, "status", None)
        if status in {"running", "completed", "partial", "failed", "blocked", "cancelled"}:
            return True
    return False


def _source_label(source: WorkbenchRuntimeGraphSourceKind) -> str:
    return {"cts": "CTS", "liepin": "猎聘", "all": "全部来源"}.get(source, source)


def _source_mode_text(sources: Sequence[SourceKind]) -> str:
    return "多源检索" if len(sources) > 1 else f"{_source_label(sources[0])} 检索"


def _requirement_sheet(requirement_review: object | None) -> Mapping[str, object] | None:
    if requirement_review is None:
        return None
    sheet = getattr(requirement_review, "requirement_sheet", None) or getattr(
        requirement_review, "requirementSheet", None
    )
    if isinstance(sheet, Mapping):
        return sheet
    model_dump = getattr(sheet, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dumped if isinstance(dumped, Mapping) else None
    return None


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
    matching = [event for event in events if event.get("stage") == stage and event.get("sourceKind") == source]
    return matching[-1] if matching else None


def _event_ids(events: list[dict[str, object]], stage: str, source: str | None) -> list[str]:
    return [str(event["eventId"]) for event in events if event.get("stage") == stage and event.get("sourceKind") == source]


def _round_status(events: list[dict[str, object]], stage: str, source: str | None) -> WorkbenchRuntimeGraphNodeStatus:
    event = _last_event(events, stage, source)
    return _safe_status(event.get("status") if event else None)


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


def _detail_request_sections(detail_open_requests: Sequence[object]) -> list[WorkbenchRuntimeGraphSectionResponse]:
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


def _safe_status(value: object) -> WorkbenchRuntimeGraphNodeStatus:
    status = str(value or "completed")
    if status in {"pending", "running", "completed", "partial", "blocked", "degraded", "failed", "cancelled"}:
        return cast(WorkbenchRuntimeGraphNodeStatus, status)
    return "completed"
