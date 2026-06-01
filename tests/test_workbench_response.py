from __future__ import annotations


def test_workbench_response_module_owns_route_projection_helpers() -> None:
    from seektalent_ui import workbench_response, workbench_routes

    expected_helpers = {
        "candidate_review_item_response",
        "detail_open_request_response",
        "dev_mode_status_response",
        "runtime_final_top_candidate_response",
        "runtime_sourcing_job_response",
        "security_audit_event_response",
        "session_response",
        "source_connection_response",
        "source_run_policy_response",
    }

    assert expected_helpers <= set(dir(workbench_response))
    assert not any(hasattr(workbench_routes, f"_{name}") for name in expected_helpers)
