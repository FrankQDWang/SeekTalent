from __future__ import annotations

from pathlib import Path

from tests.conversation_agent_test_support import build_service


def test_extraction_backed_amendment_can_confirm_without_review_resolution(tmp_path: Path) -> None:
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
        text="另外希望有平台治理经验",
        target_section_hint="must_have_capabilities",
        idempotency_key="amend-1",
    )
    added_item = amended.requirement_draft.section("must_have_capabilities").items[-1]

    assert added_item.text == "平台治理经验"
    assert added_item.source == "extracted_amendment"
    assert amended.conversation_reopen_state.pending_requirement_review_count == 0

    confirmed = service.confirm_requirements(
        conversation_id=conversation.conversation_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        draft_revision_id=amended.requirement_draft.draft_revision_id,
        base_revision_id=amended.requirement_draft.draft_revision_id,
        idempotency_key="confirm-1",
    )

    assert confirmed.conversation_reopen_state.pending_requirement_review_count == 0
    assert confirmed.conversation_reopen_state.approved_requirement_revision_id is not None
