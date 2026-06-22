from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from seektalent.models import RequirementSheet
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.normalizer import merge_requirement_sheet_supplement
from seektalent_runtime_control.requirements import (
    ApprovedRequirementRevision,
    DraftOperation,
    RequirementAmendment,
    RequirementAmendmentSummary,
    RequirementDraft,
    RequirementDraftItem,
    RequirementDraftSection,
    ReviewItem,
    ReviewResolutionOperation,
    draft_from_requirement_sheet,
    requirement_sheet_from_draft,
)
from seektalent_runtime_control.store import RuntimeControlStore


class RequirementExecutor(Protocol):
    def extract_requirements(self, *, job_title: str | None, jd_text: str, notes: str | None) -> RequirementSheet: ...


class RuntimeControlService:
    def __init__(self, *, store: RuntimeControlStore, executor: RequirementExecutor) -> None:
        self.store = store
        self.executor = executor

    def extract_requirements(
        self,
        *,
        conversation_id: str,
        job_title: str | None,
        jd_text: str,
        notes: str | None,
        source_ids: list[str],
        idempotency_key: str,
    ) -> RequirementDraft:
        existing = self.store.get_requirement_draft_by_idempotency(
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return existing
        sheet = self.executor.extract_requirements(job_title=job_title, jd_text=jd_text, notes=notes)
        draft = draft_from_requirement_sheet(
            conversation_id=conversation_id,
            draft_revision_id=_new_id("reqdraft"),
            base_revision_id=None,
            requirement_sheet=sheet,
            source="extracted",
            created_at=_now(),
        )
        return self.store.save_requirement_draft(
            draft,
            extracted_requirement_sheet_json=sheet.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )

    def get_requirement_draft(
        self,
        *,
        conversation_id: str,
        draft_revision_id: str | None = None,
    ) -> RequirementDraft:
        if draft_revision_id is None:
            draft = self.store.get_latest_requirement_draft(conversation_id=conversation_id)
        else:
            draft = self.store.get_requirement_draft(draft_revision_id)
        if draft is None:
            raise RuntimeControlError("requirement_draft_not_found")
        return draft

    def update_requirement_draft(
        self,
        *,
        draft_revision_id: str,
        base_revision_id: str,
        operations: list[DraftOperation],
        idempotency_key: str,
    ) -> RequirementDraft:
        base = self._require_draft(draft_revision_id)
        existing = self.store.get_requirement_draft_by_idempotency(
            conversation_id=base.conversation_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return existing
        self._reject_stale(base, base_revision_id)
        updated = _copy_draft(base, base_revision_id=base.draft_revision_id, status="draft_ready")
        for operation in operations:
            _apply_operation(updated, operation)
        return self._save_revision(
            updated,
            base=base,
            idempotency_key=idempotency_key,
        )

    def amend_requirement_draft_from_text(
        self,
        *,
        draft_revision_id: str,
        base_revision_id: str,
        text: str,
        target_section_hint: str | None,
        idempotency_key: str,
    ) -> RequirementDraft:
        base = self._require_draft(draft_revision_id)
        existing_amendment = self.store.get_requirement_amendment_by_idempotency(
            conversation_id=base.conversation_id,
            idempotency_key=idempotency_key,
        )
        if existing_amendment is not None and existing_amendment.result_draft_revision_id is not None:
            return self._require_draft(existing_amendment.result_draft_revision_id)
        self._reject_stale(base, base_revision_id)

        extracted = RequirementSheet.model_validate(
            self.store.get_extracted_requirement_sheet_json(base.draft_revision_id)
        )
        base_sheet = requirement_sheet_from_draft(base, extracted)
        supplement = self.executor.extract_requirements(
            job_title=base_sheet.job_title,
            jd_text=text,
            notes=None,
        )
        merged_sheet = merge_requirement_sheet_supplement(base_sheet, supplement)
        normalized = {
            "requirementSheet": merged_sheet.model_dump(mode="json"),
            "extractedSupplement": supplement.model_dump(mode="json"),
            "reviewItems": [],
            "rejectedFragments": [],
        }
        amendment_id = _new_id("reqamend")
        updated = _copy_draft(base, base_revision_id=base.draft_revision_id, status="draft_ready")
        added_item_ids = _append_extracted_supplement(updated, supplement, amendment_id=amendment_id)
        review_items = _review_items(normalized)
        if review_items:
            _apply_review_items(updated, review_items, amendment_id=amendment_id)
            updated.status = "needs_review"
        amendment = RequirementAmendment(
            amendment_id=amendment_id,
            agent_conversation_id=base.conversation_id,
            base_draft_revision_id=base.draft_revision_id,
            result_draft_revision_id=updated.draft_revision_id,
            input_text=text,
            target_section_hint=target_section_hint,
            status="needs_review" if review_items else "applied",
            normalized_patch=dict(normalized),
            rejected_fragments=_list_payload(normalized.get("rejectedFragments")),
            review_items=review_items,
            idempotency_key=idempotency_key,
            created_at=updated.created_at,
        )
        updated.amendment = RequirementAmendmentSummary(
            amendment_id=amendment.amendment_id,
            status=amendment.status,
            added_item_ids=added_item_ids,
        )
        self.store.save_requirement_amendment(amendment)
        return self._save_revision(
            updated,
            base=base,
            idempotency_key=idempotency_key,
            extracted_requirement_sheet_json=merged_sheet.model_dump(mode="json"),
        )

    def resolve_requirement_review(
        self,
        *,
        draft_revision_id: str,
        base_revision_id: str,
        amendment_id: str,
        operations: list[ReviewResolutionOperation],
        idempotency_key: str,
    ) -> RequirementDraft:
        base = self._require_draft(draft_revision_id)
        existing = self.store.get_requirement_draft_by_idempotency(
            conversation_id=base.conversation_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return existing
        self._reject_stale(base, base_revision_id)
        amendment = self.store.get_requirement_amendment(amendment_id)
        if amendment is None:
            raise RuntimeControlError("requirement_draft_not_found")
        updated = _copy_draft(base, base_revision_id=base.draft_revision_id, status="draft_ready")
        for operation in operations:
            _apply_review_resolution(updated, operation, amendment_id=amendment_id)
        updated.amendment = RequirementAmendmentSummary(amendment_id=amendment_id, status="applied")
        return self._save_revision(updated, base=base, idempotency_key=idempotency_key)

    def confirm_requirements(
        self,
        *,
        draft_revision_id: str,
        base_revision_id: str,
        idempotency_key: str,
    ) -> ApprovedRequirementRevision:
        draft = self._require_draft(draft_revision_id)
        existing = self.store.get_approved_requirement_by_idempotency(
            conversation_id=draft.conversation_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return existing
        self._reject_stale(draft, base_revision_id)
        if draft.unresolved_review_item_count:
            raise RuntimeControlError("requirement_review_unresolved")
        extracted = RequirementSheet.model_validate(
            self.store.get_extracted_requirement_sheet_json(draft.draft_revision_id)
        )
        sheet = requirement_sheet_from_draft(draft, extracted)
        selected = [
            item.item_id
            for section in draft.sections
            for item in section.items
            if item.selected and item.status == "resolved"
        ]
        deselected = [
            item.item_id
            for section in draft.sections
            for item in section.items
            if not item.selected or item.status in {"deleted", "moved", "rejected"}
        ]
        approved = ApprovedRequirementRevision(
            approved_requirement_revision_id=_new_id("reqapproved"),
            draft_revision_id=draft.draft_revision_id,
            agent_conversation_id=draft.conversation_id,
            requirement_sheet=sheet,
            selected_item_ids=selected,
            deselected_item_ids=deselected,
            created_at=_now(),
        )
        return self.store.save_approved_requirement(approved, idempotency_key=idempotency_key)

    def _save_revision(
        self,
        draft: RequirementDraft,
        *,
        base: RequirementDraft,
        idempotency_key: str,
        extracted_requirement_sheet_json: dict[str, object] | None = None,
    ) -> RequirementDraft:
        _refresh_draft_state(draft)
        extracted = extracted_requirement_sheet_json or self.store.get_extracted_requirement_sheet_json(
            base.draft_revision_id
        )
        return self.store.save_requirement_draft(
            draft,
            extracted_requirement_sheet_json=extracted,
            idempotency_key=idempotency_key,
        )

    def _require_draft(self, draft_revision_id: str) -> RequirementDraft:
        draft = self.store.get_requirement_draft(draft_revision_id)
        if draft is None:
            raise RuntimeControlError("requirement_draft_not_found")
        return draft

    def _reject_stale(self, draft: RequirementDraft, base_revision_id: str) -> None:
        latest = self.store.get_latest_requirement_draft(conversation_id=draft.conversation_id)
        if latest is not None and latest.draft_revision_id != base_revision_id:
            raise RuntimeControlError(
                "requirement_draft_stale",
                payload={
                    "latestDraftRevisionId": latest.draft_revision_id,
                    "latestSections": [section.model_dump(mode="json") for section in latest.sections],
                },
            )


def _copy_draft(base: RequirementDraft, *, base_revision_id: str, status: str) -> RequirementDraft:
    return RequirementDraft(
        conversation_id=base.conversation_id,
        draft_revision_id=_new_id("reqdraft"),
        base_revision_id=base_revision_id,
        status=status,
        sections=deepcopy(base.sections),
        created_at=_now(),
    )


def _append_extracted_supplement(
    draft: RequirementDraft,
    supplement: RequirementSheet,
    *,
    amendment_id: str,
) -> list[str]:
    supplement_draft = draft_from_requirement_sheet(
        conversation_id=draft.conversation_id,
        draft_revision_id=_new_id("reqdraft"),
        base_revision_id=None,
        requirement_sheet=supplement,
        source="extracted_amendment",
        created_at=draft.created_at,
    )
    added_item_ids: list[str] = []
    for supplement_section in supplement_draft.sections:
        target_section = draft.section(supplement_section.section_id)
        seen = {_draft_item_key(item) for item in target_section.items if item.status != "deleted"}
        for item in supplement_section.items:
            key = _draft_item_key(item)
            if key in seen:
                continue
            appended = item.model_copy(
                deep=True,
                update={
                    "item_id": _new_id("reqitem"),
                    "source": "extracted_amendment",
                    "amendment_id": amendment_id,
                    "sort_order": (len(target_section.items) + 1) * 10,
                },
            )
            target_section.items.append(appended)
            seen.add(key)
            added_item_ids.append(appended.item_id)
    return added_item_ids


def _draft_item_key(item: RequirementDraftItem) -> tuple[str, str, str]:
    return (
        item.text.strip().casefold(),
        type(item.value).__name__,
        json.dumps(item.value, ensure_ascii=False, sort_keys=True) if isinstance(item.value, dict) else repr(item.value),
    )


def _refresh_draft_state(draft: RequirementDraft) -> None:
    count = sum(
        1
        for section in draft.sections
        for item in section.items
        if item.status == "needs_review"
    )
    draft.unresolved_review_item_count = count
    draft.can_confirm = count == 0 and draft.status == "draft_ready"


def _apply_operation(draft: RequirementDraft, operation: DraftOperation) -> None:
    section, item = _find_item(draft, operation.item_id)
    if operation.op == "set_selected":
        item.selected = bool(operation.selected)
    elif operation.op == "set_enabled":
        item.enabled = bool(operation.enabled)
    elif operation.op == "edit_text":
        item.text = _required_text(operation.text)
        item.value = item.text if not isinstance(item.value, dict) else {**item.value, "value": item.text}
        item.source = "user_edited"
    elif operation.op == "delete_item":
        item.selected = False
        item.status = "deleted"
    elif operation.op == "move_item":
        target_section_id = operation.target_section or ""
        target = draft.section(target_section_id)
        moved = item.model_copy(
            deep=True,
            update={
                "item_id": _new_id("reqitem"),
                "status": "resolved",
                "sort_order": (len(target.items) + 1) * 10,
                "source": "user_edited",
            },
        )
        target.items.append(moved)
        target.items.sort(key=lambda item: item.sort_order)
        item.selected = False
        item.status = "moved"
    else:
        raise RuntimeControlError("requirement_draft_invalid")
    section.items.sort(key=lambda item: item.sort_order)


def _apply_review_items(draft: RequirementDraft, review_items: list[ReviewItem], *, amendment_id: str) -> None:
    for review_item in review_items:
        target = review_item.candidate_section or "must_have_capabilities"
        section = draft.section(target)
        section.items.append(
            RequirementDraftItem(
                item_id=_new_id("reqitem"),
                selected=True,
                enabled=True,
                text=review_item.candidate_text,
                value=review_item.candidate_text,
                source="runtime_normalized",
                status="needs_review",
                review_item_id=review_item.review_item_id,
                amendment_id=amendment_id,
                sort_order=(len(section.items) + 1) * 10,
                allowed_actions=["accept_candidate", "edit_candidate", "reject_candidate"],
            )
        )
        section.items.sort(key=lambda item: item.sort_order)


def _apply_review_resolution(
    draft: RequirementDraft,
    operation: ReviewResolutionOperation,
    *,
    amendment_id: str,
) -> None:
    section, item = _find_review_item(draft, operation.review_item_id)
    if operation.op in {"reject_candidate", "reject_fragment"}:
        item.selected = False
        item.status = "rejected"
        return
    target_section = draft.section(operation.target_section or section.section_id)
    resolved = item.model_copy(
        deep=True,
        update={
            "item_id": _new_id("reqitem"),
            "text": operation.text or item.text,
            "value": operation.text or item.value,
            "status": "resolved",
            "selected": True,
            "source": "user_review_resolution",
            "amendment_id": amendment_id,
            "sort_order": (len(target_section.items) + 1) * 10,
        },
    )
    target_section.items.append(resolved)
    target_section.items.sort(key=lambda item: item.sort_order)
    item.selected = False
    item.status = "rejected"


def _find_item(draft: RequirementDraft, item_id: str) -> tuple[RequirementDraftSection, RequirementDraftItem]:
    for section in draft.sections:
        for item in section.items:
            if item.item_id == item_id:
                return section, item
    raise RuntimeControlError("requirement_draft_invalid")


def _find_review_item(draft: RequirementDraft, review_item_id: str) -> tuple[RequirementDraftSection, RequirementDraftItem]:
    for section in draft.sections:
        for item in section.items:
            if item.review_item_id == review_item_id:
                return section, item
    raise RuntimeControlError("requirement_draft_invalid")


def _review_items(normalized: dict[str, object]) -> list[ReviewItem]:
    result: list[ReviewItem] = []
    for raw_item in _list_payload(normalized.get("reviewItems")):
        item = _string_key_dict(raw_item)
        if not item:
            continue
        candidate_section = item.get("candidateSection")
        result.append(
            ReviewItem(
                review_item_id=_required_text(item.get("reviewItemId")),
                raw_text=str(item.get("rawText") or ""),
                candidate_text=str(item.get("candidateText") or ""),
                candidate_section=(str(candidate_section) if candidate_section else None),
                reason_code=str(item.get("reasonCode") or "requirement_amendment_ambiguous"),
            )
        )
    return result


def _list_payload(value: object) -> list[object]:
    return list(value) if isinstance(value, list | tuple) else []


def _string_key_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _required_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeControlError("requirement_draft_invalid")
    return text


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
