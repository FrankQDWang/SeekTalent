from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence

from seektalent.providers.liepin.opencli_workflow import workflow_steps_from_action_events
from seektalent.providers.liepin.liepin_site_parsing import _safe_artifact_segment

JsonObject = dict[str, object]
ArtifactWriter = Callable[[str, str, object], str]
AgentEventReader = Callable[[str], list[dict[str, object]]]
FORBIDDEN_CARD_SUMMARY_KEYS = {
    "visible_text",
    "normalized_card_text",
    "normalizedCardText",
    "raw_html",
    "inner_html",
    "inner_text",
    "fullText",
    "full_text",
    "rawText",
    "page_text",
    "pageText",
}
_CARD_SUMMARY_SCALAR_FIELDS = (
    "display_title",
    "current_or_recent_company",
    "current_or_recent_title",
    "gender",
    "city",
    "expected_city",
    "education_level",
    "job_intention",
    "active_status",
)
_CARD_SUMMARY_INT_FIELDS = ("age", "work_years")
_CARD_SUMMARY_LIST_FIELDS = ("badges", "school_names", "major_names", "skill_tags")
_CARD_SUMMARY_EXPERIENCE_FIELDS = ("company", "title", "date_range", "duration")
_CARD_SUMMARY_EDUCATION_FIELDS = ("school", "major", "degree", "recruitment_type", "date_range")
_CARD_SUMMARY_TEXT_MAX_CHARS = 180
_CARD_SUMMARY_LIST_TEXT_MAX_CHARS = 80
_CARD_SUMMARY_LIST_MAX_ITEMS = 20
_CARD_SUMMARY_PREVIEW_MAX_ITEMS = 5


def _safe_card_summary_payload(summary: Mapping[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for field in _CARD_SUMMARY_SCALAR_FIELDS:
        text = _safe_card_summary_text(summary.get(field))
        if text is not None:
            payload[field] = text
    for field in _CARD_SUMMARY_INT_FIELDS:
        value = _safe_card_summary_int(summary.get(field))
        if value is not None:
            payload[field] = value
    masked_name = summary.get("masked_name")
    if isinstance(masked_name, bool):
        payload["masked_name"] = masked_name
    for field in _CARD_SUMMARY_LIST_FIELDS:
        values = _safe_card_summary_text_list(summary.get(field))
        if values:
            payload[field] = values
    experience_preview = _safe_card_summary_preview(
        summary.get("experience_preview"),
        text_fields=_CARD_SUMMARY_EXPERIENCE_FIELDS,
        bool_fields=("is_current",),
    )
    if experience_preview:
        payload["experience_preview"] = experience_preview
    education_preview = _safe_card_summary_preview(
        summary.get("education_preview"),
        text_fields=_CARD_SUMMARY_EDUCATION_FIELDS,
    )
    if education_preview:
        payload["education_preview"] = education_preview
    return payload


def _safe_card_summary_text(value: object, *, max_chars: int = _CARD_SUMMARY_TEXT_MAX_CHARS) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", value).strip()
    return text[:max_chars] if text else None


def _safe_card_summary_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _safe_card_summary_text_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for raw_item in value:
        item = _safe_card_summary_text(raw_item, max_chars=_CARD_SUMMARY_LIST_TEXT_MAX_CHARS)
        if item is None or item in seen:
            continue
        seen.add(item)
        items.append(item)
        if len(items) >= _CARD_SUMMARY_LIST_MAX_ITEMS:
            break
    return items


def _safe_card_summary_preview(
    value: object,
    *,
    text_fields: tuple[str, ...],
    bool_fields: tuple[str, ...] = (),
) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    previews: list[dict[str, object]] = []
    for raw_item in value[:_CARD_SUMMARY_PREVIEW_MAX_ITEMS]:
        if not isinstance(raw_item, Mapping):
            continue
        item: dict[str, object] = {}
        for field in text_fields:
            text = _safe_card_summary_text(raw_item.get(field))
            if text is not None:
                item[field] = text
        for field in bool_fields:
            bool_value = raw_item.get(field)
            if isinstance(bool_value, bool):
                item[field] = bool_value
        if item:
            previews.append(item)
    return previews


def blocked_cards_envelope(
    *,
    source_run_id: str,
    query: str,
    safe_reason_code: str,
    safe_run_id: str,
    pages_visited: int,
    events: Sequence[Mapping[str, object]],
    write_pi_artifact: ArtifactWriter,
) -> dict[str, object]:
    action_trace_ref = write_pi_artifact(
        "protected",
        f"pi-trace/{safe_run_id}/action-trace.json",
        {
            "schema_version": "seektalent.opencli_action_trace.v1",
            "mode": "card",
            "source": "liepin",
            "status": "blocked",
            "stop_reason": "blocked_backend_unavailable",
            "safe_reason_code": safe_reason_code,
            "events": events,
        },
    )
    return {
        "schema_version": "seektalent.pi_liepin_cards.v1",
        "status": "blocked",
        "stop_reason": "blocked_backend_unavailable",
        "safe_reason_code": safe_reason_code,
        "source_run_id": source_run_id,
        "query": query,
        "cards_seen": 0,
        "cards_returned": 0,
        "pages_visited": pages_visited,
        "action_trace_ref": action_trace_ref,
        "safe_summary_refs": [],
        "protected_snapshot_refs": [],
        "cards": [],
    }


def cards_envelope(
    *,
    source_run_id: str,
    query: str,
    safe_run_id: str,
    pages_visited: int,
    events: Sequence[Mapping[str, object]],
    state_text: str,
    cards: Sequence[Mapping[str, object]],
    write_pi_artifact: ArtifactWriter,
) -> dict[str, object]:
    action_trace_ref = write_pi_artifact(
        "protected",
        f"pi-trace/{safe_run_id}/action-trace.json",
        {
            "schema_version": "seektalent.opencli_action_trace.v1",
            "mode": "card",
            "source": "liepin",
            "status": "succeeded",
            "stop_reason": "completed",
            "events": events,
            "cards_seen": len(cards),
        },
    )
    page_snapshot_ref = write_pi_artifact(
        "protected",
        f"pi-page/{safe_run_id}/search-state.json",
        {"schema_version": "seektalent.opencli_state_snapshot.v1", "chars": len(state_text)},
    )
    envelope_cards: list[dict[str, object]] = []
    safe_summary_refs: list[str] = []
    protected_snapshot_refs: list[str] = [page_snapshot_ref]
    for rank, summary in enumerate(cards, start=1):
        safe_summary = _safe_card_summary_payload(summary)
        digest = hashlib.sha256(json.dumps(safe_summary, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
        provider_material_ref = write_pi_artifact(
            "protected",
            f"pi-provider-key/{safe_run_id}/{rank}.txt",
            f"liepin-opencli:{safe_run_id}:{rank}:{digest}",
        )
        safe_summary_ref = write_pi_artifact(
            "public-summary",
            f"pi-card/{safe_run_id}/{rank}.json",
            safe_summary,
        )
        protected_snapshot_ref = write_pi_artifact(
            "protected",
            f"pi-card/{safe_run_id}/{rank}.json",
            {"schema_version": "seektalent.opencli_card_snapshot.v1", "rank": rank, "summary": safe_summary},
        )
        safe_summary_refs.append(safe_summary_ref)
        protected_snapshot_refs.append(protected_snapshot_ref)
        envelope_cards.append(
            {
                "provider_rank": rank,
                "provider_candidate_key_material_ref": provider_material_ref,
                "candidate_resume_id": f"liepin-opencli-{safe_run_id}-{rank}-{digest}",
                "display_name_masked": bool(summary.get("display_name_masked", summary.get("masked_name", True))),
                "safe_card_summary": safe_summary,
                "safe_card_summary_ref": safe_summary_ref,
                "protected_snapshot_ref": protected_snapshot_ref,
            }
        )
    return {
        "schema_version": "seektalent.pi_liepin_cards.v1",
        "status": "succeeded",
        "stop_reason": "completed",
        "source_run_id": source_run_id,
        "query": query,
        "cards_seen": len(envelope_cards),
        "cards_returned": len(envelope_cards),
        "pages_visited": pages_visited,
        "action_trace_ref": action_trace_ref,
        "safe_summary_refs": safe_summary_refs,
        "protected_snapshot_refs": protected_snapshot_refs,
        "cards": envelope_cards,
    }


def resumes_envelope(
    *,
    source_run_id: str,
    query: str,
    safe_run_id: str,
    pages_visited: int,
    events: Sequence[Mapping[str, object]],
    cards_seen: int,
    max_cards: int,
    resumes: list[dict[str, object]],
    protected_snapshot_refs: list[str],
    target_resumes: int | None = None,
    write_pi_artifact: ArtifactWriter,
) -> dict[str, object]:
    returned_count = len(resumes)
    target_count = max(0, int(target_resumes or 0))
    status = "partial" if target_count and returned_count < target_count else "succeeded"
    stop_reason = "partial_timeout" if status == "partial" else "completed"
    action_trace_ref = write_pi_artifact(
        "protected",
        f"liepin-opencli/trace/{safe_run_id}/action-trace.json",
        {
            "schema_version": "seektalent.opencli_action_trace.v1",
            "mode": "detail_backed_resume_search",
            "source": "liepin",
            "status": status,
            "stop_reason": stop_reason,
            "target_resumes": target_count,
            "max_cards": max_cards,
            "events": events,
            "cards_seen": cards_seen,
            "resumes_returned": returned_count,
        },
    )
    workflow_steps = workflow_steps_from_action_events(
        events,
        final_status=status,
        final_reason_code=stop_reason,
        resumes_returned=returned_count,
        action_trace_ref=action_trace_ref,
    )
    return {
        "schema_version": "seektalent.liepin_opencli_resumes.v1",
        "status": status,
        "stop_reason": stop_reason,
        "safe_reason_code": stop_reason if status == "partial" else None,
        "source_run_id": source_run_id,
        "query": query,
        "cards_seen": cards_seen,
        "cards_excluded": [],
        "resumes_returned": returned_count,
        "pages_visited": pages_visited,
        "detail_pages_opened": returned_count,
        "action_trace_ref": action_trace_ref,
        "workflow_steps": workflow_steps,
        "protected_snapshot_refs": protected_snapshot_refs,
        "resumes": resumes,
    }


def blocked_resumes_envelope(
    *,
    source_run_id: str,
    query: str,
    safe_reason_code: str,
    cards_seen: int,
    write_pi_artifact: ArtifactWriter,
    read_agent_events: AgentEventReader,
) -> dict[str, object]:
    safe_run_id = _safe_artifact_segment(source_run_id)
    action_trace_ref = write_pi_artifact(
        "protected",
        f"liepin-opencli/trace/{safe_run_id}/action-trace.json",
        {
            "schema_version": "seektalent.opencli_action_trace.v1",
            "mode": "detail_backed_resume_search",
            "source": "liepin",
            "safe_reason_code": safe_reason_code,
            "events": read_agent_events(safe_run_id),
        },
    )
    workflow_steps = workflow_steps_from_action_events(
        read_agent_events(safe_run_id),
        final_status="blocked",
        final_reason_code=safe_reason_code,
        resumes_returned=0,
        action_trace_ref=action_trace_ref,
    )
    return {
        "schema_version": "seektalent.liepin_opencli_resumes.v1",
        "status": "blocked",
        "stop_reason": safe_reason_code,
        "safe_reason_code": safe_reason_code,
        "source_run_id": source_run_id,
        "query": query,
        "cards_seen": cards_seen,
        "resumes_returned": 0,
        "pages_visited": 1,
        "detail_pages_opened": 0,
        "action_trace_ref": action_trace_ref,
        "workflow_steps": workflow_steps,
        "protected_snapshot_refs": [],
        "resumes": [],
    }
