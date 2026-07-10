from __future__ import annotations

import os
import subprocess
import sys
import textwrap

from seektalent.providers.liepin.opencli_workflow import workflow_steps_from_action_events


def test_workflow_steps_from_action_events_maps_successful_detail_flow() -> None:
    steps = workflow_steps_from_action_events(
        [
            {"action_kind": "visible_cards_observed", "visible_cards": 6, "cards_seen": 6, "target_resumes": 2},
            {"action_kind": "detail_urls_cached", "cached_detail_urls": 6},
            {"action_kind": "detail_candidate_selected", "rank": 1, "ref": "70"},
            {"action_kind": "open_detail_succeeded", "rank": 1, "open_mode": "cached_url"},
            {"action_kind": "wait_detail_ready", "ok": True, "rank": 1},
            {"action_kind": "capture_detail_succeeded", "rank": 1},
            {"action_kind": "return_to_search_after_capture", "ok": True, "rank": 1},
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
        "wait_detail_ready",
        "capture_detail",
        "observe_cards",
        "finalize",
    ]
    assert steps[0]["event_type"] == "source_workflow_step_completed"
    assert steps[0]["safe_counts"] == {"visible_cards": 6, "cards_seen": 6, "target_resumes": 2}
    assert steps[3]["safe_metadata"] == {"rank": 1, "open_mode": "cached_url"}
    assert steps[4]["safe_metadata"] == {"rank": 1}
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
            {"action_kind": "visible_cards_refreshed_after_return", "visible_cards": 0, "cards_seen": 1},
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


def test_workflow_steps_merges_last_private_claim_outcomes_into_finalize_counts() -> None:
    steps = workflow_steps_from_action_events(
        [
            {
                "action_kind": "detail_claim_outcomes",
                "detail_claim_granted_count": 99,
                "detail_opened_count": 99,
                "detail_open_skipped_seen_count": 99,
                "detail_open_terminal_failure_count": 99,
            },
            {
                "action_kind": "detail_claim_outcomes",
                "detail_claim_granted_count": 1,
                "detail_opened_count": 1,
                "detail_open_skipped_seen_count": 0,
                "detail_open_terminal_failure_count": 0,
                "resumes_returned": 99,
                "cards_seen": 99,
                "provider_candidate_key_hash": "private-key",
                "res_id_encode": "private-subject",
                "url": "https://h.liepin.com/resume/showresumedetail/private",
                "ref": "private-ref",
                "logical_round_no": 4,
                "query_instance_id": "private-query",
                "raw_provider_value": "raw resume text",
            },
        ],
        final_status="succeeded",
        resumes_returned=1,
        action_trace_ref=None,
    )

    assert len(steps) == 1
    assert steps[0]["step_name"] == "finalize"
    assert steps[0]["safe_counts"] == {
        "resumes_returned": 1,
        "detail_claim_granted_count": 1,
        "detail_opened_count": 1,
        "detail_open_skipped_seen_count": 0,
        "detail_open_terminal_failure_count": 0,
    }
    assert "private" not in repr(steps)
    assert "raw resume text" not in repr(steps)


def test_workflow_steps_keeps_normal_finalize_count_shape_without_private_outcomes() -> None:
    steps = workflow_steps_from_action_events(
        [{"action_kind": "visible_cards_observed", "visible_cards": 1}],
        final_status="succeeded",
        resumes_returned=1,
        action_trace_ref=None,
    )

    assert steps[-1]["safe_counts"] == {"resumes_returned": 1}


def test_normal_workflow_safe_count_order_matches_base_allowlist_across_hash_seeds() -> None:
    script = textwrap.dedent(
        """
        from seektalent.providers.liepin.opencli_workflow import workflow_steps_from_action_events

        base_safe_count_keys = {
            "cached_detail_urls",
            "cards_seen",
            "resumes_returned",
            "target_resumes",
            "visible_cards",
            "attempts",
        }
        event = {
            "action_kind": "visible_cards_observed",
            "cached_detail_urls": 1,
            "cards_seen": 2,
            "resumes_returned": 3,
            "target_resumes": 4,
            "visible_cards": 5,
            "attempts": 6,
        }
        actual = workflow_steps_from_action_events(
            [event],
            final_status="succeeded",
            resumes_returned=0,
            action_trace_ref=None,
        )[0]["safe_counts"]
        expected = {key: event[key] for key in base_safe_count_keys}
        assert tuple(actual) == tuple(expected), (tuple(actual), tuple(expected))
        """
    )

    for hash_seed in ("0", "1"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=False,
            capture_output=True,
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_workflow_steps_from_action_events_maps_native_filter_verification() -> None:
    steps = workflow_steps_from_action_events(
        [
            {"action_kind": "open_native_filter_menu", "filter": "city", "ok": True},
            {"action_kind": "verify_native_filter", "filter": "city", "ok": True},
        ],
        final_status="succeeded",
        resumes_returned=0,
        action_trace_ref="artifact://protected/liepin-opencli/action-traces/run-3.json",
    )

    assert [step["step_name"] for step in steps] == ["apply_filters", "apply_filters", "finalize"]
    assert steps[1]["event_type"] == "source_workflow_step_completed"
    assert steps[1]["status"] == "completed"


def test_workflow_steps_from_action_events_maps_clear_native_filters() -> None:
    steps = workflow_steps_from_action_events(
        [
            {"action_kind": "clear_native_filters", "route_kind": "search", "ok": True},
        ],
        final_status="succeeded",
        resumes_returned=0,
        action_trace_ref="artifact://protected/liepin-opencli/action-traces/run-4.json",
    )

    assert [step["step_name"] for step in steps] == ["apply_filters", "finalize"]
    assert steps[0]["event_type"] == "source_workflow_step_completed"
    assert steps[0]["status"] == "completed"


def test_workflow_steps_from_action_events_drops_removed_cleanup_actions() -> None:
    removed_step = "cleanup_" + "detail_tabs"
    removed_count = "closed_" + "tabs"
    removed_actions = (
        removed_step,
        removed_step + "_after_capture",
        "visible_cards_refreshed_" + "after_" + "cleanup",
        "visible_cards_refresh_failed_" + "after_" + "cleanup",
    )

    steps = workflow_steps_from_action_events(
        [{"action_kind": action, "ok": True, removed_count: 3} for action in removed_actions],
        final_status="succeeded",
        resumes_returned=0,
        action_trace_ref=None,
    )

    assert [step["step_name"] for step in steps] == ["finalize"]


def test_workflow_steps_from_action_events_drops_removed_cleanup_count() -> None:
    removed_count = "closed_" + "tabs"

    steps = workflow_steps_from_action_events(
        [{"action_kind": "visible_cards_observed", "visible_cards": 2, removed_count: 3}],
        final_status="succeeded",
        resumes_returned=0,
        action_trace_ref=None,
    )

    assert steps[0]["step_name"] == "observe_cards"
    assert steps[0]["safe_counts"] == {"visible_cards": 2}
