from __future__ import annotations

from pathlib import Path
from types import NoneType
from typing import get_args

from seektalent_conversation_agent.models import ConversationAgentResponse, ConversationReopenState
from seektalent_runtime_control.models import RuntimeFinalSummary
from seektalent_runtime_control.requirements import RequirementDraft


def test_conversation_agent_response_uses_concrete_runtime_contract_types() -> None:
    assert set(get_args(ConversationAgentResponse.model_fields["requirement_draft"].annotation)) == {
        RequirementDraft,
        NoneType,
    }
    assert set(get_args(ConversationAgentResponse.model_fields["final_summary"].annotation)) == {
        RuntimeFinalSummary,
        NoneType,
    }


def test_conversation_agent_response_serializes_workflow_and_runtime_ids() -> None:
    response = ConversationAgentResponse(
        conversation_reopen_state=ConversationReopenState(
            conversation_id="agent_conv_contract",
            title="Find platform engineers",
            status="completed",
            is_archived=False,
            latest_message_seq=3,
            latest_activity_seq=2,
            latest_rendered_runtime_event_seq=11,
            runtime_run_id="runtime_run_contract",
            workbench_session_id="session_contract",
            workflow_start_intent_id="workflow_intent_contract",
            latest_draft_revision_id="reqdraft_contract",
            approved_requirement_revision_id="reqapproved_contract",
            pending_user_action=None,
            allowed_actions=[],
            last_opened_at="2026-06-23T00:00:00Z",
        ),
        requirement_draft=RequirementDraft(
            conversation_id="agent_conv_contract",
            draft_revision_id="reqdraft_contract",
            base_revision_id=None,
            status="draft_ready",
            sections=[],
            created_at="2026-06-23T00:00:01Z",
        ),
        job_request_revision_id="jobreq_contract",
        requirement_draft_revision_id="reqdraft_contract",
        workflow_start_intent_id="workflow_intent_contract",
        final_summary=RuntimeFinalSummary(
            summary_id="runtime_final_summary_contract",
            runtime_run_id="runtime_run_contract",
            status="completed",
            summary="Final shortlist ready.",
            facts=[],
            source_event_ids=[],
            source_snapshot_event_seq=10,
            latest_snapshot_event_seq=11,
            created_at="2026-06-23T00:00:02Z",
        ),
    )

    payload = response.model_dump(mode="json")

    assert payload["job_request_revision_id"] == "jobreq_contract"
    assert payload["requirement_draft_revision_id"] == "reqdraft_contract"
    assert payload["workflow_start_intent_id"] == "workflow_intent_contract"
    assert payload["requirement_draft"]["draft_revision_id"] == "reqdraft_contract"
    assert payload["final_summary"]["runtime_run_id"] == "runtime_run_contract"
    assert payload["conversation_reopen_state"]["runtime_run_id"] == "runtime_run_contract"
    assert payload["conversation_reopen_state"]["workflow_start_intent_id"] == "workflow_intent_contract"
    assert payload["conversation_reopen_state"]["approved_requirement_revision_id"] == "reqapproved_contract"


def test_frontend_schema_does_not_accept_fresh_workflow_start_inputs() -> None:
    schema_text = Path("apps/web-react/src/lib/api/schema.d.ts").read_text(encoding="utf-8")
    start_schema = schema_text.split("/** WorkflowStartRequest */", maxsplit=1)[1].split("};", maxsplit=1)[0]

    assert "WorkflowStartRequest: Record<string, never>" in start_schema
    assert "jobTitle" not in start_schema
    assert "jdText" not in start_schema
    assert "notes" not in start_schema
    assert "sourceIds" not in start_schema


def test_packaged_workbench_bundle_uses_operation_events() -> None:
    bundle_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/seektalent_ui/static/workbench/_app").glob("*.js")
    )

    assert "operation.started" in bundle_text
    assert "tool.started" not in bundle_text
