from __future__ import annotations


def test_source_connection_routes_are_owned_by_dedicated_router_module() -> None:
    from seektalent_ui import workbench_routes, workbench_source_connection_routes

    expected_paths = {
        "/api/workbench/source-connections",
        "/api/workbench/source-connections/liepin",
        "/api/workbench/source-connections/{connection_id}",
        "/api/workbench/source-connections/{connection_id}/login",
        "/api/workbench/source-connections/{connection_id}/login/frame",
        "/api/workbench/source-connections/{connection_id}/login/snapshot",
        "/api/workbench/source-connections/{connection_id}/login/input",
        "/api/workbench/source-connections/{connection_id}/login/complete",
    }
    moved_handlers = {
        "complete_liepin_connection_login",
        "create_liepin_source_connection",
        "get_source_connection",
        "liepin_connection_login_frame",
        "liepin_connection_login_input",
        "liepin_connection_login_snapshot",
        "list_source_connections",
        "start_liepin_connection_login",
    }

    source_connection_paths = {route.path for route in workbench_source_connection_routes.router.routes}
    mounted_paths = {route.path for route in workbench_routes.router.routes}

    assert expected_paths <= source_connection_paths
    assert expected_paths <= mounted_paths
    assert not any(hasattr(workbench_routes, handler) for handler in moved_handlers)
