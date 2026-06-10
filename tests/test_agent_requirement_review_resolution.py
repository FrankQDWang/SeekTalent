from __future__ import annotations

from pathlib import Path

from tests.conversation_agent_test_support import build_service


def test_review_required_amendment_blocks_confirmation_until_resolution(tmp_path: Path) -> None:
    service, _conversation_store, _runtime_store = build_service(tmp_path)
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
        jd_text="需要 Python API。",
        notes=None,
        source_ids=["cts"],
        idempotency_key="submit-jd-1",
    )
    draft = submitted.requirement_draft
    amended = service.amend_requirement_draft_from_text(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=draft.draft_revision_id,
        base_revision_id=draft.draft_revision_id,
        text="需要确认：平台治理经验",
        target_section_hint="must_have_capabilities",
        idempotency_key="amend-1",
    )
    review_item = amended.requirement_draft.sections[0].items[-1]

    assert amended.conversation_reopen_state.pending_requirement_review_count == 1

    resolved = service.resolve_requirement_review(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=amended.requirement_draft.draft_revision_id,
        base_revision_id=amended.requirement_draft.draft_revision_id,
        amendment_id=review_item.amendment_id,
        operations=[
            {
                "op": "accept_candidate",
                "review_item_id": review_item.review_item_id,
            }
        ],
        idempotency_key="resolve-1",
    )

    assert resolved.conversation_reopen_state.pending_requirement_review_count == 0
    assert resolved.requirement_draft.can_confirm is True
