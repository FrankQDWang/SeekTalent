from __future__ import annotations


def test_runtime_source_response_projection_is_owned_by_dedicated_module() -> None:
    from seektalent_ui import workbench_routes, workbench_runtime_source_response

    assert hasattr(workbench_runtime_source_response, "runtime_source_state_response")
    assert not hasattr(workbench_routes, "_runtime_source_state_response")
    assert not hasattr(workbench_routes, "_runtime_source_lane_state_response")
