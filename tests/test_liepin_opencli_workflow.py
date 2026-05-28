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


def test_workflow_steps_from_action_events_maps_real_resume_flow_actions_and_partial_reason() -> None:
    steps = workflow_steps_from_action_events(
        [
            {"action_kind": "search_cards_started", "ok": True},
            {"action_kind": "apply_filters_started", "ok": True},
            {"action_kind": "apply_filters_completed", "ok": True},
            {"action_kind": "search_submitted", "ok": True, "cards_seen": 1},
            {"action_kind": "visible_cards_refreshed_after_cleanup", "visible_cards": 0, "cards_seen": 1},
        ],
        final_status="partial",
        final_reason_code="partial_timeout",
        resumes_returned=1,
        action_trace_ref="artifact://protected/liepin-opencli/action-traces/run-2.json",
    )

    assert [step["step_name"] for step in steps] == [
        "prepare_search",
        "apply_filters",
        "apply_filters",
        "submit_search",
        "observe_cards",
        "finalize",
    ]
    assert steps[0]["event_type"] == "source_workflow_step_started"
    assert steps[0]["status"] == "running"
    assert steps[-1]["event_type"] == "source_workflow_step_failed"
    assert steps[-1]["status"] == "partial"
    assert steps[-1]["safe_reason_code"] == "partial_timeout"
    assert "refresh_cards" not in repr(steps)


def test_workflow_steps_from_action_events_sanitizes_private_fields() -> None:
    steps = workflow_steps_from_action_events(
        [
            {
                "action_kind": "open_detail_failed",
                "rank": 1,
                "safe_reason_code": "liepin_opencli_detail_not_opened",
                "url": "https://h.liepin.com/resume/showresumedetail/private",
                "cookie": "secret",
                "provider_id": "provider-secret",
                "raw_resume": "raw resume text",
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
    assert "liepin.com" not in repr(steps)
    assert "secret" not in repr(steps)
    assert "raw resume text" not in repr(steps)
