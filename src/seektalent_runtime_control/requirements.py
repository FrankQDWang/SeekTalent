from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from seektalent.models import HardConstraintSlots, QueryTermCandidate, RequirementSheet


SECTION_ORDER = (
    "must_have_capabilities",
    "preferred_capabilities",
    "hard_constraints",
    "exclusion_signals",
    "initial_query_term_pool",
)
SECTION_DISPLAY_NAMES = {
    "must_have_capabilities": "必须满足",
    "preferred_capabilities": "加分项",
    "hard_constraints": "硬性筛选条件",
    "exclusion_signals": "排除信号",
    "initial_query_term_pool": "检索关键词",
}
SECTION_BACKEND_FIELDS = {
    "must_have_capabilities": "must_have_capabilities",
    "preferred_capabilities": "preferred_capabilities",
    "hard_constraints": "hard_constraints",
    "exclusion_signals": "exclusion_signals",
    "initial_query_term_pool": "initial_query_term_pool[].term",
}


class RequirementDraftItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    selected: bool = True
    enabled: bool = True
    editable: bool = True
    text: str
    value: object
    source: str
    status: str = "resolved"
    review_item_id: str | None = None
    amendment_id: str | None = None
    source_span_refs: list[str] = Field(default_factory=list)
    sort_order: int
    allowed_actions: list[str] = Field(default_factory=list)


class RequirementDraftSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str
    display_name: str
    backend_field: str
    items: list[RequirementDraftItem] = Field(default_factory=list)

    def find_item(self, item_id: str) -> RequirementDraftItem:
        for item in self.items:
            if item.item_id == item_id:
                return item
        raise KeyError(item_id)


class RequirementAmendmentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amendment_id: str
    status: str
    added_item_ids: list[str] = Field(default_factory=list)
    changed_item_ids: list[str] = Field(default_factory=list)


class RequirementDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    draft_revision_id: str
    base_revision_id: str | None = None
    status: str
    sections: list[RequirementDraftSection]
    created_at: str
    latest: bool = True
    can_confirm: bool = True
    unresolved_review_item_count: int = 0
    amendment: RequirementAmendmentSummary | None = None

    @model_validator(mode="after")
    def fill_review_counts(self) -> RequirementDraft:
        count = sum(
            1
            for section in self.sections
            for item in section.items
            if item.status == "needs_review"
        )
        self.unresolved_review_item_count = count
        self.can_confirm = count == 0 and self.status == "draft_ready"
        return self

    def section(self, section_id: str) -> RequirementDraftSection:
        for section in self.sections:
            if section.section_id == section_id:
                return section
        raise KeyError(section_id)


class DraftOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["set_selected", "edit_text", "delete_item", "move_item", "set_enabled"]
    item_id: str
    selected: bool | None = None
    text: str | None = None
    target_section: str | None = None
    enabled: bool | None = None


class ReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_item_id: str
    raw_text: str
    candidate_text: str
    candidate_section: str | None = None
    reason_code: str


class ReviewResolutionOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["accept_candidate", "edit_candidate", "move_candidate", "reject_candidate", "reject_fragment"]
    review_item_id: str
    target_section: str | None = None
    text: str | None = None
    reason_code: str | None = None


class RequirementAmendment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amendment_id: str
    agent_conversation_id: str
    runtime_run_id: str | None = None
    base_draft_revision_id: str | None = None
    result_draft_revision_id: str | None = None
    base_approved_requirement_revision_id: str | None = None
    result_approved_requirement_revision_id: str | None = None
    target_round_no: int | None = None
    effective_boundary: str | None = None
    applied_event_id: str | None = None
    input_text: str
    target_section_hint: str | None = None
    status: str
    normalized_patch: dict[str, object] = Field(default_factory=dict)
    rejected_fragments: list[object] = Field(default_factory=list)
    review_items: list[ReviewItem] = Field(default_factory=list)
    provenance: dict[str, object] = Field(default_factory=dict)
    resolved_patch: dict[str, object] | None = None
    superseded_by_amendment_id: str | None = None
    resolved_at: str | None = None
    idempotency_key: str
    created_at: str


class ApprovedRequirementRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved_requirement_revision_id: str
    draft_revision_id: str | None = None
    base_approved_requirement_revision_id: str | None = None
    source_amendment_id: str | None = None
    agent_conversation_id: str
    requirement_sheet: RequirementSheet
    selected_item_ids: list[str]
    deselected_item_ids: list[str]
    created_at: str
    status: str = "confirmed"


def draft_from_requirement_sheet(
    *,
    conversation_id: str,
    draft_revision_id: str,
    base_revision_id: str | None,
    requirement_sheet: RequirementSheet,
    source: str,
    created_at: str,
) -> RequirementDraft:
    sections = [_empty_section(section_id) for section_id in SECTION_ORDER]
    _add_text_items(sections[0], requirement_sheet.must_have_capabilities, source=source)
    _add_text_items(sections[1], requirement_sheet.preferred_capabilities, source=source)
    _add_hard_constraint_items(sections[2], requirement_sheet.hard_constraints, source=source)
    _add_text_items(sections[3], requirement_sheet.exclusion_signals, source=source)
    _add_query_term_items(sections[4], requirement_sheet.initial_query_term_pool, source=source)
    return RequirementDraft(
        conversation_id=conversation_id,
        draft_revision_id=draft_revision_id,
        base_revision_id=base_revision_id,
        status="draft_ready",
        sections=sections,
        created_at=created_at,
    )


def requirement_sheet_from_draft(draft: RequirementDraft, extracted_sheet: RequirementSheet) -> RequirementSheet:
    hard_constraints = extracted_sheet.hard_constraints.model_copy(deep=True)
    hard_constraints.locations = []
    hard_constraints.school_names = []
    hard_constraints.company_names = []
    for item in _active_items(draft.section("hard_constraints")):
        value_payload = _string_key_dict(item.value)
        field = value_payload.get("field")
        value = value_payload.get("value")
        if field == "locations" and isinstance(value, str):
            hard_constraints.locations.append(value)
        elif field == "school_names" and isinstance(value, str):
            hard_constraints.school_names.append(value)
        elif field == "company_names" and isinstance(value, str):
            hard_constraints.company_names.append(value)

    query_terms = []
    for item in _active_items(draft.section("initial_query_term_pool")):
        value = _string_key_dict(item.value)
        value["term"] = item.text
        value["active"] = item.enabled
        query_terms.append(QueryTermCandidate.model_validate(value))

    return extracted_sheet.model_copy(
        update={
            "must_have_capabilities": [item.text for item in _active_items(draft.section("must_have_capabilities"))],
            "preferred_capabilities": [item.text for item in _active_items(draft.section("preferred_capabilities"))],
            "exclusion_signals": [item.text for item in _active_items(draft.section("exclusion_signals"))],
            "hard_constraints": hard_constraints,
            "initial_query_term_pool": query_terms,
        }
    )


def _active_items(section: RequirementDraftSection) -> list[RequirementDraftItem]:
    return [item for item in section.items if item.selected and item.status == "resolved"]


def _string_key_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _empty_section(section_id: str) -> RequirementDraftSection:
    return RequirementDraftSection(
        section_id=section_id,
        display_name=SECTION_DISPLAY_NAMES[section_id],
        backend_field=SECTION_BACKEND_FIELDS[section_id],
        items=[],
    )


def _add_text_items(section: RequirementDraftSection, values: list[str], *, source: str) -> None:
    for value in values:
        section.items.append(
            RequirementDraftItem(
                item_id=_item_id(section.section_id, value, len(section.items)),
                text=value,
                value=value,
                source=source,
                sort_order=(len(section.items) + 1) * 10,
                allowed_actions=_allowed_actions(section.section_id),
            )
        )


def _add_hard_constraint_items(section: RequirementDraftSection, constraints: HardConstraintSlots, *, source: str) -> None:
    for field, values in (
        ("locations", constraints.locations),
        ("school_names", constraints.school_names),
        ("company_names", constraints.company_names),
    ):
        for value in values:
            section.items.append(
                RequirementDraftItem(
                    item_id=_item_id(section.section_id, f"{field}:{value}", len(section.items)),
                    text=value,
                    value={"field": field, "value": value},
                    source=source,
                    sort_order=(len(section.items) + 1) * 10,
                    allowed_actions=_allowed_actions(section.section_id),
                )
            )


def _add_query_term_items(
    section: RequirementDraftSection,
    query_terms: list[QueryTermCandidate],
    *,
    source: str,
) -> None:
    for candidate in query_terms:
        section.items.append(
            RequirementDraftItem(
                item_id=_item_id(section.section_id, candidate.term, len(section.items)),
                text=candidate.term,
                value=candidate.model_dump(mode="json"),
                source=source,
                enabled=candidate.active,
                sort_order=(len(section.items) + 1) * 10,
                allowed_actions=_allowed_actions(section.section_id),
            )
        )


def _allowed_actions(section_id: str) -> list[str]:
    if section_id == "must_have_capabilities":
        return ["select", "edit", "delete", "move_to_preferred_capabilities"]
    if section_id == "preferred_capabilities":
        return ["select", "edit", "delete", "move_to_must_have_capabilities"]
    if section_id == "initial_query_term_pool":
        return ["select", "enable", "edit", "delete"]
    return ["select", "edit", "delete"]


def _item_id(section_id: str, text: str, index: int) -> str:
    import hashlib

    digest = hashlib.sha1(f"{section_id}:{index}:{text}".encode("utf-8")).hexdigest()[:16]
    return f"reqitem_{digest}"
