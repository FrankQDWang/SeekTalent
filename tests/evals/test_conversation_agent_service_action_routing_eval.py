from __future__ import annotations

from seektalent_conversation_agent.service_actions import AGENT_SERVICE_ACTION_NAMES, AgentServiceActionAdapter


def test_service_action_routing_eval_all_declared_actions_have_adapter_methods() -> None:
    adapter_methods = {
        "extract_requirements",
        "get_requirement_draft",
        "update_requirement_draft",
        "amend_requirement_draft_from_text",
        "resolve_requirement_review",
        "confirm_requirements",
        "start_workflow",
        "get_workflow_snapshot",
        "list_workflow_events",
        "request_pause",
        "request_cancel",
        "resume_workflow",
        "submit_next_round_requirement",
        "get_runtime_detail",
        "prepare_final_summary",
    }

    assert set(AGENT_SERVICE_ACTION_NAMES) == adapter_methods
    assert all(hasattr(AgentServiceActionAdapter, name) for name in adapter_methods)
