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
