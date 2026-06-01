from __future__ import annotations

import base64

from seektalent_ui.models import WorkbenchRuntimeGraphCandidateScopeResponse
from seektalent_ui.workbench_graph_cursors import decode_graph_candidate_cursor, encode_graph_candidate_cursor
from seektalent_ui.workbench_graph_node_refs import (
    node_ref_from_candidate_scope,
    node_scope_response,
    parse_graph_node_ref,
    runtime_node_ref_without_scope,
)


def test_graph_node_refs_parse_legacy_and_runtime_candidate_scopes() -> None:
    legacy = parse_graph_node_ref("cts-round-2-score")
    assert legacy is not None
    assert legacy.source_kind == "cts"
    assert legacy.node_kind == "scoring"
    assert legacy.round_no == 2

    recall = parse_graph_node_ref("cts-round-4-result")
    assert recall is not None
    assert recall.source_kind == "cts"
    assert recall.node_kind == "recall"
    assert recall.round_no == 4

    assert parse_graph_node_ref("round-2-score") is None

    runtime = node_ref_from_candidate_scope(
        "round-3-source-liepin",
        WorkbenchRuntimeGraphCandidateScopeResponse(scopeKind="round_recall", sourceKind="liepin", roundNo=3),
    )
    assert runtime.source_kind == "liepin"
    assert runtime.node_kind == "liepin_card"
    assert runtime.round_no == 3

    unsupported = runtime_node_ref_without_scope("requirements")
    assert unsupported is not None
    assert unsupported.has_candidate_index is False
    assert node_scope_response(session_id="session-1", node=runtime).roundId == "3"


def test_graph_candidate_cursor_round_trips_and_rejects_wrong_context() -> None:
    cursor = encode_graph_candidate_cursor(75, session_id="session-1", node_id="round-1-source-cts", secret="secret")

    assert (
        decode_graph_candidate_cursor(cursor, session_id="session-1", node_id="round-1-source-cts", secret="secret")
        == 75
    )
    assert (
        decode_graph_candidate_cursor(cursor, session_id="session-2", node_id="round-1-source-cts", secret="secret")
        is None
    )
    assert (
        decode_graph_candidate_cursor(cursor, session_id="session-1", node_id="round-2-source-cts", secret="secret")
        is None
    )
    assert (
        decode_graph_candidate_cursor(
            cursor[:-1] + "A",
            session_id="session-1",
            node_id="round-1-source-cts",
            secret="secret",
        )
        is None
    )
    assert decode_graph_candidate_cursor(cursor.removeprefix("cur_"), session_id="session-1", node_id="round-1-source-cts", secret="secret") is None
    assert (
        decode_graph_candidate_cursor(
            "cur_not-base64",
            session_id="session-1",
            node_id="round-1-source-cts",
            secret="secret",
        )
        is None
    )

    wrong_length_payload = base64.urlsafe_b64encode(b"too-short").decode("ascii")
    assert (
        decode_graph_candidate_cursor(
            "cur_" + wrong_length_payload,
            session_id="session-1",
            node_id="round-1-source-cts",
            secret="secret",
        )
        is None
    )

    for offset in (0, 2**63, 2**64 - 1):
        boundary_cursor = encode_graph_candidate_cursor(
            offset,
            session_id="session-1",
            node_id="round-1-source-cts",
            secret="secret",
        )
        assert (
            decode_graph_candidate_cursor(
                boundary_cursor,
                session_id="session-1",
                node_id="round-1-source-cts",
                secret="secret",
            )
            == offset
        )
