from __future__ import annotations

from pathlib import Path

from seektalent_conversation_agent.projection import project_runtime_event
from seektalent_runtime_control.models import RuntimeControlEvent


def test_activity_projection_maps_runtime_event_to_codex_like_lifecycle_item(tmp_path: Path) -> None:
    event = RuntimeControlEvent(
        event_id="rtevt_1",
        event_seq=7,
        runtime_run_id="runtime_run_1",
        event_type="runtime_score_completed",
        stage="scoring",
        round_no=2,
        source_id="cts",
        status="completed",
        summary="完成第 2 轮评分。",
        payload={"candidateCount": 5},
        workbench_event_global_seq=None,
        created_at="2026-06-09T00:00:07.000000Z",
    )

    projected = project_runtime_event(
        conversation_id="agent_conv_1",
        event=event,
        activity_id="agent_activity_1",
    )

    assert projected.activity_key == "agent_conv_1:runtime_run_1:scoring:2:cts"
    assert projected.activity_type == "scoring"
    assert projected.status == "completed"
    assert projected.source_event_seq_latest == 7
    assert "评分" in projected.title


def test_activity_projection_preserves_started_status_and_source_dispatch_type() -> None:
    event = RuntimeControlEvent(
        event_id="rtevt_dispatch_1",
        event_seq=3,
        runtime_run_id="runtime_run_1",
        event_type="runtime_round_source_dispatch",
        stage="source",
        round_no=1,
        source_id="liepin",
        status="started",
        summary="开始分发猎聘来源。",
        payload={"candidateCount": 0},
        workbench_event_global_seq=None,
        created_at="2026-06-09T00:00:03.000000Z",
    )

    projected = project_runtime_event(
        conversation_id="agent_conv_1",
        event=event,
        activity_id="agent_activity_1",
    )

    assert projected.activity_key == "agent_conv_1:runtime_run_1:source_dispatch:1:liepin"
    assert projected.activity_type == "source_dispatch"
    assert projected.status == "started"
    assert projected.completed_at is None
