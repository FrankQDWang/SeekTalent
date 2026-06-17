from __future__ import annotations

from pathlib import Path

import pytest

from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_runtime_control.models import RuntimeControlEventInput, RuntimeRunRecord, RuntimeRunSnapshot
from tests.conversation_agent_test_support import build_service, save_approved_requirement


def test_default_runtime_actions_target_active_run_and_historical_requires_explicit_id(tmp_path: Path) -> None:
    service, _conversation_store, runtime_store = build_service(tmp_path)
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Python 平台负责人",
    )
    _create_runtime_run(
        service=service,
        runtime_store=runtime_store,
        conversation_id=conversation.conversation_id,
        runtime_run_id="runtime_run_active",
        event_id="rtevt_active",
        snapshot_status="running",
        linked_at="2026-06-09T00:00:01.000000Z",
        make_active=True,
    )
    _create_runtime_run(
        service=service,
        runtime_store=runtime_store,
        conversation_id=conversation.conversation_id,
        runtime_run_id="runtime_run_historical",
        event_id="rtevt_historical",
        snapshot_status="completed",
        linked_at="2026-06-09T00:00:02.000000Z",
        make_active=False,
        run_kind="rerun",
        link_reason="rerun",
    )

    default_snapshot = service.get_workflow_snapshot(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        runtime_run_id=None,
    )
    historical_snapshot = service.get_workflow_snapshot(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        runtime_run_id="runtime_run_historical",
    )

    assert default_snapshot.runtime_run_id == "runtime_run_active"
    assert historical_snapshot.runtime_run_id == "runtime_run_historical"


def test_conversation_agent_rejects_runtime_id_linked_to_another_conversation(tmp_path: Path) -> None:
    service, _conversation_store, runtime_store = build_service(tmp_path)
    first = service.store.create_conversation(
        conversation_id="agent_conv_first",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Python 平台负责人",
        created_at="2026-06-09T00:00:00.000000Z",
    )
    second = service.store.create_conversation(
        conversation_id="agent_conv_second",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Go 平台负责人",
        created_at="2026-06-09T00:00:00.000000Z",
    )
    _create_runtime_run(
        service=service,
        runtime_store=runtime_store,
        conversation_id=first.conversation_id,
        runtime_run_id="runtime_run_first",
        event_id="rtevt_first",
        snapshot_status="running",
        linked_at="2026-06-09T00:00:01.000000Z",
        make_active=True,
    )
    _create_runtime_run(
        service=service,
        runtime_store=runtime_store,
        conversation_id=second.conversation_id,
        runtime_run_id="runtime_run_second",
        event_id="rtevt_second",
        snapshot_status="running",
        linked_at="2026-06-09T00:00:02.000000Z",
        make_active=True,
    )

    with pytest.raises(ConversationAgentError) as exc_info:
        service.get_workflow_snapshot(
            conversation_id=first.conversation_id,
            owner_user_id="user_1",
            workspace_id="workspace_1",
            runtime_run_id="runtime_run_second",
        )

    assert exc_info.value.reason_code == "agent_runtime_run_not_linked"


def test_polling_historical_runtime_run_uses_its_own_rendered_cursor(tmp_path: Path) -> None:
    service, _conversation_store, runtime_store = build_service(tmp_path)
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Python 平台负责人",
    )
    _create_runtime_run(
        service=service,
        runtime_store=runtime_store,
        conversation_id=conversation.conversation_id,
        runtime_run_id="runtime_run_active",
        event_id="rtevt_active",
        snapshot_status="running",
        linked_at="2026-06-09T00:00:01.000000Z",
        make_active=True,
    )
    _create_runtime_run(
        service=service,
        runtime_store=runtime_store,
        conversation_id=conversation.conversation_id,
        runtime_run_id="runtime_run_historical",
        event_id="rtevt_historical",
        snapshot_status="completed",
        linked_at="2026-06-09T00:00:02.000000Z",
        make_active=False,
        run_kind="rerun",
        link_reason="rerun",
    )
    service.store.update_rendered_runtime_cursor(
        conversation_id=conversation.conversation_id,
        runtime_run_id="runtime_run_active",
        latest_event_seq=99,
        updated_at="2026-06-09T00:00:03.000000Z",
    )

    response = service.poll_runtime_events(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        runtime_run_id="runtime_run_historical",
        limit=10,
    )

    historical_messages = [
        message for message in response.messages if message.source_runtime_run_id == "runtime_run_historical"
    ]
    assert historical_messages
    assert response.conversation_reopen_state.runtime_run_id == "runtime_run_active"
    historical_link = next(
        link
        for link in response.conversation_reopen_state.linked_runtime_runs
        if link.runtime_run_id == "runtime_run_historical"
    )
    assert historical_link.latest_event_seq == 1


def _create_runtime_run(
    *,
    service,
    runtime_store,
    conversation_id: str,
    runtime_run_id: str,
    event_id: str,
    snapshot_status: str,
    linked_at: str,
    make_active: bool,
    run_kind: str = "primary",
    link_reason: str = "start",
) -> None:
    approved = save_approved_requirement(
        runtime_store,
        conversation_id=conversation_id,
        approved_requirement_revision_id=f"reqapproved_{runtime_run_id}",
    )
    run_intent_id = f"workflow:{conversation_id}:{approved.approved_requirement_revision_id}:{run_kind}"
    runtime_store.create_run(
        RuntimeRunRecord(
            runtime_run_id=runtime_run_id,
            run_intent_id=run_intent_id,
            start_idempotency_key=run_intent_id,
            run_kind=run_kind,
            agent_conversation_id=conversation_id,
            workbench_session_id=None,
            approved_requirement_revision_id=approved.approved_requirement_revision_id,
            status=snapshot_status,
            current_stage="runtime",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-06-09T00:00:00.000000Z",
            updated_at="2026-06-09T00:00:00.000000Z",
            completed_at=None,
        )
    )
    runtime_store.append_event(
        RuntimeControlEventInput(
            event_id=event_id,
            runtime_run_id=runtime_run_id,
            event_type="runtime_snapshot_ready",
            stage="runtime",
            round_no=1,
            source_id=None,
            status="completed",
            summary="snapshot ready",
            payload={"snapshot": True},
            created_at=linked_at,
        ),
        snapshot=RuntimeRunSnapshot(
            runtime_run_id=runtime_run_id,
            status=snapshot_status,
            current_stage="runtime",
            current_round=1,
            latest_event_seq=1,
            snapshot={"runtimeRunId": runtime_run_id},
            updated_at=linked_at,
        ),
    )
    service.store.link_runtime_run(
        conversation_id=conversation_id,
        runtime_run_id=runtime_run_id,
        workbench_session_id=None,
        approved_requirement_revision_id=approved.approved_requirement_revision_id,
        run_intent_id=run_intent_id,
        run_kind=run_kind,
        link_reason=link_reason,
        make_active=make_active,
        linked_at=linked_at,
    )
