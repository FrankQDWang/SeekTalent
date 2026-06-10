from __future__ import annotations

from seektalent_conversation_agent.projection import project_runtime_event
from seektalent_runtime_control.models import RuntimeControlEvent


def test_grounding_eval_progress_activity_is_derived_from_runtime_event() -> None:
    event = RuntimeControlEvent(
        event_id="rtevt_grounding_1",
        event_seq=3,
        runtime_run_id="runtime_run_1",
        event_type="runtime_source_result",
        stage="source",
        round_no=1,
        source_id="cts",
        status="completed",
        summary="CTS 返回 3 个候选人。",
        payload={"candidateCount": 3},
        workbench_event_global_seq=None,
        created_at="2026-06-09T00:00:03.000000Z",
    )

    projected = project_runtime_event(conversation_id="agent_conv_1", event=event, activity_id="activity_1")

    assert projected.summary == event.summary
    assert projected.source_event_id_latest == event.event_id
    assert projected.payload["candidateCount"] == 3
