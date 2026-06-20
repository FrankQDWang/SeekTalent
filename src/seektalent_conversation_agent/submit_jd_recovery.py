from __future__ import annotations

from seektalent_conversation_agent.models import AgentToolCallRecord, ConversationRecord
from seektalent_conversation_agent.tools import AgentToolAdapter


def should_repair_submit_replay_status(conversation: ConversationRecord) -> bool:
    return conversation.runtime_run_id is None and conversation.status in {"draft", "awaiting_requirement_confirmation"}


def normalize_optional_job_title(job_title: str | None) -> str | None:
    if job_title is None:
        return None
    normalized = job_title.strip()
    return normalized or None


def extracted_job_title_from_runtime_control(
    tool_adapter: AgentToolAdapter,
    *,
    draft_revision_id: str,
) -> str | None:
    runtime_store = tool_adapter.runtime_store
    if runtime_store is None:
        return None
    payload = runtime_store.get_extracted_requirement_sheet_json(draft_revision_id)
    job_title = payload.get("job_title")
    if not isinstance(job_title, str):
        return None
    normalized = job_title.strip()
    return normalized or None


def tool_call_draft_revision_id(tool_call: AgentToolCallRecord) -> str | None:
    if tool_call.result is None:
        return None
    draft_revision_id = tool_call.result.get("draftRevisionId")
    return draft_revision_id if isinstance(draft_revision_id, str) and draft_revision_id else None


def requirement_review_payload(draft: object) -> dict[str, object]:
    draft_revision_id = str(getattr(draft, "draft_revision_id"))
    return {
        "requirementDraft": {"draftRevisionId": draft_revision_id},
        "requirementDraftSnapshot": _requirement_draft_snapshot(draft),
    }


def requirement_review_message_idempotency_key(
    *,
    draft_revision_id: str,
    idempotency_key: str | None,
) -> str | None:
    if not idempotency_key:
        return None
    return f"requirement_review:{draft_revision_id}:{idempotency_key}"


def assistant_message_idempotency_key(idempotency_key: str | None) -> str | None:
    if not idempotency_key:
        return None
    return f"agent_turn_assistant:{idempotency_key}"


def _requirement_draft_snapshot(draft: object) -> dict[str, object]:
    sections = list(getattr(draft, "sections", ()) or ())
    return {
        "draftRevisionId": str(getattr(draft, "draft_revision_id")),
        "parentDraftRevisionId": _str_or_none(getattr(draft, "base_revision_id", None)),
        "status": str(getattr(draft, "status", "unknown")),
        "title": "需求确认",
        "summary": _requirement_draft_snapshot_summary(sections),
        "canConfirm": bool(getattr(draft, "can_confirm", False)),
        "unresolvedReviewItemCount": int(getattr(draft, "unresolved_review_item_count", 0) or 0),
        "sections": [_requirement_draft_section_snapshot(section) for section in sections],
        "otherInputPrompt": "其他",
    }


def _requirement_draft_section_snapshot(section: object) -> dict[str, object]:
    section_id = str(getattr(section, "section_id"))
    return {
        "sectionId": section_id,
        "displayName": str(getattr(section, "display_name", section_id)),
        "backendField": str(getattr(section, "backend_field", section_id)),
        "items": [_requirement_draft_item_snapshot(section_id, item) for item in getattr(section, "items", ()) or ()],
    }


def _requirement_draft_item_snapshot(section_id: str, item: object) -> dict[str, object]:
    status = str(getattr(item, "status", "unknown"))
    return {
        "itemId": str(getattr(item, "item_id")),
        "sectionId": section_id,
        "selected": bool(getattr(item, "selected", False)),
        "enabled": bool(getattr(item, "enabled", False)),
        "editable": bool(getattr(item, "editable", False)),
        "text": str(getattr(item, "text", "")),
        "status": status if status in {"resolved", "needs_review", "deleted", "moved", "rejected"} else "unknown",
        "source": str(getattr(item, "source", "unknown")),
        "allowedActions": [str(action) for action in getattr(item, "allowed_actions", ()) or ()],
    }


def _requirement_draft_snapshot_summary(sections: list[object]) -> str:
    selected_count = sum(
        1
        for section in sections
        for item in getattr(section, "items", ()) or ()
        if bool(getattr(item, "selected", False)) and getattr(item, "status", "") == "resolved"
    )
    return f"已生成 {selected_count} 条已选择需求，请确认后启动检索。"


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
