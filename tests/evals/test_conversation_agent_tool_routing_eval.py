from __future__ import annotations

from seektalent_conversation_agent.tools import AGENT_RUNTIME_TOOL_NAMES, AgentToolAdapter


def test_tool_routing_eval_all_declared_tools_have_adapter_methods() -> None:
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

    assert set(AGENT_RUNTIME_TOOL_NAMES) == adapter_methods
    assert all(hasattr(AgentToolAdapter, name) for name in adapter_methods)
