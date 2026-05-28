from __future__ import annotations

from types import SimpleNamespace

from seektalent_ui.runtime_graph import build_runtime_graph, safe_natural_text, section_from_facts


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


def _event(
    *,
    name: str,
    seq: int,
    stage: str,
    round_no: int | None = None,
    source: str | None = None,
    counts: dict[str, int] | None = None,
    details: dict[str, object] | None = None,
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
            "details": details or {},
            "safeReasonCode": None,
        },
    )


def _progress_event(
    *,
    name: str,
    seq: int,
    round_no: int,
    payload: dict,
) -> SimpleNamespace:
    return SimpleNamespace(
        globalSeq=seq,
        eventName=name,
        sourceKind=None,
        sourceRunId=None,
        payload={
            "type": name,
            "message": "runtime progress",
            "roundNo": round_no,
            "timestamp": "2026-05-26T00:00:00Z",
            "payload": payload,
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
                name="runtime_round_merge_completed",
                seq=160,
                stage="merge",
                round_no=1,
                counts={"mergedIdentities": 6},
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
    assert node_by_id["round-1-merge"].candidateScope.scopeKind == "round_score"
    assert node_by_id["round-1-merge"].candidateScope.sourceKind == "all"
    assert node_by_id["round-1-merge"].candidateScope.roundNo == 1
    assert node_by_id["round-1-score"].candidateScope.scopeKind == "round_score"
    assert node_by_id["round-1-score"].candidateScope.sourceKind == "all"
    assert node_by_id["round-1-score"].candidateScope.roundNo == 1
    assert "10 位候选人进入 Top Pool" in node_by_id["round-1-score"].summaryText


def test_build_runtime_graph_labels_liepin_returned_count_as_cards() -> None:
    graph = build_runtime_graph(
        session=_session(),
        events=[
            _event(name="runtime_round_query_ready", seq=101, stage="round_query", round_no=1),
            _event(
                name="runtime_round_source_result",
                seq=132,
                stage="source_result",
                round_no=1,
                source="liepin",
                counts={
                    "roundReturned": 6,
                    "roundIdentities": 2,
                    "sourceCumulativeReturned": 6,
                    "sourceCumulativeIdentities": 2,
                },
            ),
        ],
        runtime_source_state=None,
        detail_open_requests=[],
        final_top=None,
    )

    node = {item.nodeId: item for item in graph.nodes}["round-1-source-liepin"]
    assert node.summaryText == "猎聘浏览 6 张卡片，形成 2 份详情简历。"
    facts = {(fact.label, fact.value) for section in node.detailSections for fact in section.facts}
    assert ("浏览卡片", "6 张") in facts
    assert ("详情简历", "2 份") in facts
    assert ("累计浏览卡片", "6 张") in facts
    assert ("累计详情简历", "2 份") in facts


def test_build_runtime_graph_feedback_node_renders_reflection_details() -> None:
    graph = build_runtime_graph(
        session=_session(),
        events=[
            _event(name="runtime_round_query_ready", seq=101, stage="round_query", round_no=1),
            _event(
                name="runtime_round_scoring_completed",
                seq=170,
                stage="scoring",
                round_no=1,
                counts={"roundIdentities": 8, "topPoolCount": 5},
            ),
            _event(
                name="runtime_round_feedback_completed",
                seq=180,
                stage="feedback",
                round_no=1,
                counts={"feedbackCandidateCount": 5},
                details={
                    "reflectionSummary": "本轮缺少实时数仓经验，下一轮扩大 Flink 关键词。",
                    "reflectionRationale": "Top Pool 里多数候选人偏 BI，和岗位要求的数据工程主线不一致。",
                    "suggestStop": False,
                    "suggestedKeepTerms": ["数据开发"],
                    "suggestedActivateTerms": ["Flink"],
                    "suggestedDropTerms": ["BI 报表"],
                    "suggestedAddFilterFields": ["work_content"],
                },
            ),
        ],
        runtime_source_state=None,
        detail_open_requests=[],
        final_top=None,
    )

    node = {item.nodeId: item for item in graph.nodes}["round-1-feedback"]
    section_by_heading = {section.heading: section for section in node.detailSections}
    assert section_by_heading["反思总结"].text == "本轮缺少实时数仓经验，下一轮扩大 Flink 关键词。"
    assert section_by_heading["反思理由"].text == "Top Pool 里多数候选人偏 BI，和岗位要求的数据工程主线不一致。"
    assert section_by_heading["关键词建议"].values == [
        "保留：数据开发",
        "启用：Flink",
        "丢弃：BI 报表",
    ]
    assert section_by_heading["筛选字段建议"].values == ["新增：work_content"]


def test_build_runtime_graph_does_not_synthesize_merge_before_backend_event() -> None:
    graph = build_runtime_graph(
        session=_session(),
        events=[
            _event(name="runtime_round_query_ready", seq=101, stage="round_query", round_no=1),
            _event(
                name="runtime_round_source_dispatch",
                seq=131,
                stage="source_dispatch",
                round_no=1,
                source="cts",
                status="running",
            ),
            _event(
                name="runtime_round_source_dispatch",
                seq=132,
                stage="source_dispatch",
                round_no=1,
                source="liepin",
                status="running",
            ),
        ],
        runtime_source_state=None,
        detail_open_requests=[],
        final_top=None,
    )

    node_ids = {node.nodeId for node in graph.nodes}
    assert "round-1-source-cts" in node_ids
    assert "round-1-source-liepin" in node_ids
    assert "round-1-merge" not in node_ids
    assert "round-1-score" not in node_ids


def test_runtime_graph_liepin_source_node_shows_workflow_step_timeline() -> None:
    graph = build_runtime_graph(
        session=_session(),
        events=[
            _event(name="runtime_round_query_ready", seq=101, stage="round_query", round_no=1),
            _event(
                name="runtime_round_source_dispatch",
                seq=131,
                stage="source_dispatch",
                round_no=1,
                source="liepin",
                status="running",
            ),
            SimpleNamespace(
                globalSeq=132,
                eventName="runtime_source_workflow_step_completed",
                sourceKind="liepin",
                sourceRunId=None,
                payload={
                    "schema_version": "runtime_source_lane_event_v1",
                    "source": "liepin",
                    "source_lane_run_id": "run-1:source:liepin:round:1:lane:1",
                    "event_type": "source_workflow_step_completed",
                    "status": "completed",
                    "step_name": "capture_detail",
                    "safe_counts": {"details_opened": 1},
                    "safe_metadata": {"rank": 1},
                },
            ),
        ],
        runtime_source_state=None,
        detail_open_requests=[],
        final_top=None,
    )

    node_by_id = {node.nodeId: node for node in graph.nodes}
    sections = {section.heading: section for section in node_by_id["round-1-source-liepin"].detailSections}

    assert "猎聘步骤" in sections
    assert any("capture_detail" in value and "details_opened=1" in value for value in sections["猎聘步骤"].values)


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


def test_build_runtime_graph_query_node_renders_runtime_query_package_details() -> None:
    graph = build_runtime_graph(
        session=_session(),
        events=[
            _event(name="runtime_round_query_ready", seq=101, stage="round_query", round_no=1),
            _progress_event(
                name="search_started",
                seq=102,
                round_no=1,
                payload={
                    "stage": "search",
                    "planned_queries": [
                        {
                            "lane_type": "exploit",
                            "query_role": "exploit",
                            "keyword_query": "数据开发 数据流程",
                            "query_terms": ["数据开发", "数据流程"],
                            "requested_count": 7,
                        }
                    ],
                },
            ),
            _event(name="runtime_round_query_ready", seq=201, stage="round_query", round_no=2),
            _progress_event(
                name="search_started",
                seq=202,
                round_no=2,
                payload={
                    "stage": "search",
                    "planned_queries": [
                        {
                            "lane_type": "exploit",
                            "query_role": "exploit",
                            "keyword_query": "数据开发 ETL",
                            "query_terms": ["数据开发", "ETL"],
                            "requested_count": 7,
                        },
                        {
                            "lane_type": "generic_explore",
                            "query_role": "explore",
                            "keyword_query": "数据仓库 ClickHouse",
                            "query_terms": ["数据仓库", "ClickHouse"],
                            "requested_count": 3,
                        },
                    ],
                },
            ),
        ],
        runtime_source_state=None,
        detail_open_requests=[],
        final_top=None,
    )

    node_by_id = {node.nodeId: node for node in graph.nodes}
    round_1_lists = [section.values for section in node_by_id["round-1-query"].detailSections if section.kind == "list"]
    round_2_lists = [section.values for section in node_by_id["round-2-query"].detailSections if section.kind == "list"]

    assert round_1_lists == [["exploit：数据开发 数据流程（数据开发、数据流程） · 目标 7 份"]]
    assert round_2_lists == [
        [
            "exploit：数据开发 ETL（数据开发、ETL） · 目标 7 份",
            "explore：数据仓库 ClickHouse（数据仓库、ClickHouse） · 目标 3 份",
        ]
    ]
