from __future__ import annotations

from seektalent.models import QueryTermCandidate, RequirementSheet
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.requirements import ApprovedRequirementRevision


class DefaultRequirementNormalizer:
    def normalize_next_round_requirement_text(
        self,
        *,
        text: str,
        target_section_hint: str | None,
        current_requirement: ApprovedRequirementRevision,
    ) -> dict[str, object]:
        sheet = current_requirement.requirement_sheet
        values = sheet.model_dump(mode="python")
        section_id = _default_requirement_section(target_section_hint)
        if section_id is not None:
            section_values = values.get(section_id)
            if isinstance(section_values, list):
                values[section_id] = [*section_values, text]
        query_terms = values.get("initial_query_term_pool")
        if isinstance(query_terms, list) and text not in {
            str(item.get("term")) for item in query_terms if isinstance(item, dict)
        }:
            query_terms.append(
                QueryTermCandidate(
                    term=text,
                    source="notes",
                    category="domain",
                    priority=90,
                    evidence="User added this runtime requirement.",
                    first_added_round=0,
                ).model_dump(mode="python")
            )
        rationale = str(values.get("scoring_rationale") or "").strip()
        values["scoring_rationale"] = (
            f"{rationale} Runtime user amendment: {text}."
            if rationale
            else f"Runtime user amendment: {text}."
        )
        requirement_sheet = RequirementSheet.model_validate(values)
        return {
            "requirementSheet": requirement_sheet.model_dump(mode="json"),
            "reviewItems": [],
            "rejectedFragments": [],
        }


def apply_next_round_patch(sheet: RequirementSheet, normalized: dict[str, object]) -> RequirementSheet:
    requirement_sheet_payload = normalized.get("requirementSheet")
    if requirement_sheet_payload is not None:
        if _list_payload(normalized.get("additions")):
            raise RuntimeControlError(
                "requirement_sheet_patch_conflict",
                payload={"message": "Patch must not contain both requirementSheet and additions."},
            )
        return RequirementSheet.model_validate(requirement_sheet_payload)
    values = sheet.model_dump(mode="python")
    for raw_addition in _list_payload(normalized.get("additions")):
        addition = _string_key_dict(raw_addition)
        if not addition:
            continue
        section_id = str(addition.get("sectionId") or "must_have_capabilities")
        text = str(addition.get("text") or "").strip()
        section_values = values.get(section_id)
        if not text or not isinstance(section_values, list):
            continue
        values[section_id] = [*section_values, text]
    return RequirementSheet.model_validate(values)


def _default_requirement_section(section_id: str | None) -> str | None:
    if section_id in {"must_have_capabilities", "preferred_capabilities", "exclusion_signals"}:
        return section_id
    if section_id is None:
        return "must_have_capabilities"
    return None


def _list_payload(value: object) -> list[object]:
    return list(value) if isinstance(value, list | tuple) else []


def _string_key_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}
