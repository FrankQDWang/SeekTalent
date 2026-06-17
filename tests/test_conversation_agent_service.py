from __future__ import annotations

from pathlib import Path

from seektalent_runtime_control.requirements import DraftOperation

from tests.conversation_agent_test_support import build_service


def test_submit_jd_persists_user_message_and_requirement_review(tmp_path: Path) -> None:
    service, _conversation_store, _runtime_store = build_service(tmp_path)
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="资深 Python 后端",
    )

    response = service.submit_jd(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        job_title="Python 平台负责人",
        jd_text="需要 Python API、平台工程和检索排序。",
        notes="优先 toB SaaS",
        source_ids=["cts"],
        idempotency_key="submit-jd-1",
    )

    assert response.conversation_reopen_state.status == "awaiting_requirement_confirmation"
    assert response.requirement_draft is not None
    assert [section.display_name for section in response.requirement_draft.sections] == [
        "必须满足",
        "加分项",
        "硬性筛选条件",
        "排除信号",
        "检索关键词",
    ]
    assert all(item.selected for section in response.requirement_draft.sections for item in section.items)
    assert [message.message_type for message in response.messages] == ["user_text", "requirement_review"]


def test_requirement_edit_amend_review_resolution_confirm_and_workflow_start(tmp_path: Path) -> None:
    service, _conversation_store, runtime_store = build_service(tmp_path)
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="资深 Python 后端",
    )
    submitted = service.submit_jd(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        job_title="Python 平台负责人",
        jd_text="需要 Python API、平台工程和检索排序。",
        notes=None,
        source_ids=["cts"],
        idempotency_key="submit-jd-1",
    )
    draft = submitted.requirement_draft
    assert draft is not None
    first_item = draft.sections[0].items[0]

    edited = service.update_requirement_draft(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=draft.draft_revision_id,
        base_revision_id=draft.draft_revision_id,
        operations=[DraftOperation(op="edit_text", item_id=first_item.item_id, text="Python API 设计")],
        idempotency_key="edit-1",
    )
    amended = service.amend_requirement_draft_from_text(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=edited.requirement_draft.draft_revision_id,
        base_revision_id=edited.requirement_draft.draft_revision_id,
        text="需要确认：有平台治理经验",
        target_section_hint="must_have_capabilities",
        idempotency_key="amend-1",
    )
    review_item = amended.requirement_draft.sections[0].items[-1]
    resolved = service.resolve_requirement_review(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=amended.requirement_draft.draft_revision_id,
        base_revision_id=amended.requirement_draft.draft_revision_id,
        amendment_id=review_item.amendment_id,
        operations=[
            {
                "op": "edit_candidate",
                "review_item_id": review_item.review_item_id,
                "text": "平台治理经验",
            }
        ],
        idempotency_key="resolve-1",
    )
    confirmed = service.confirm_requirements(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=resolved.requirement_draft.draft_revision_id,
        base_revision_id=resolved.requirement_draft.draft_revision_id,
        idempotency_key="confirm-1",
    )
    started = service.start_workflow(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        job_title="Python 平台负责人",
        jd_text="需要 Python API、平台工程和检索排序。",
        notes=None,
        source_ids=["cts"],
    )

    assert confirmed.conversation_reopen_state.approved_requirement_revision_id.startswith("reqapproved_")
    assert started.conversation_reopen_state.runtime_run_id == "runtime_run_1"
    assert started.conversation_reopen_state.status == "running"
    assert len(started.conversation_reopen_state.linked_runtime_runs) == 1
    linked_run = started.conversation_reopen_state.linked_runtime_runs[0]
    assert linked_run.runtime_run_id == "runtime_run_1"
    assert linked_run.is_active is True
    assert linked_run.run_kind == "primary"
    assert linked_run.run_intent_id == (
        f"workflow:{conversation.conversation_id}:"
        f"{confirmed.conversation_reopen_state.approved_requirement_revision_id}:primary"
    )
    assert runtime_store.get_run("runtime_run_1").status == "queued"
    events = runtime_store.list_events(runtime_run_id="runtime_run_1", after_seq=0, limit=10).events
    assert [event.event_type for event in events] == ["runtime_run_queued"]
