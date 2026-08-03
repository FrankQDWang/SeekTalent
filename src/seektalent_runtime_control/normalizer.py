from __future__ import annotations

from collections.abc import Mapping

from seektalent.models import QueryTermCandidate, RequirementSheet, unique_strings
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


def merge_requirement_sheet_supplement(
    base: RequirementSheet,
    supplement: RequirementSheet,
    *,
    effective_round_no: int = 1,
) -> RequirementSheet:
    supplement = prepare_requirement_supplement(
        supplement,
        effective_round_no=effective_round_no,
    )
    values = base.model_dump(mode="python")
    supplement_values = supplement.model_dump(mode="python")
    for field in ("must_have_capabilities", "preferred_capabilities", "exclusion_signals"):
        values[field] = unique_strings([*values.get(field, []), *supplement_values.get(field, [])])

    hard_constraints = _string_key_dict(values.get("hard_constraints"))
    supplement_hard_constraints = _string_key_dict(supplement_values.get("hard_constraints"))
    for field in ("locations", "school_names", "company_names"):
        hard_constraints[field] = unique_strings(
            [*_string_list_payload(hard_constraints.get(field)), *_string_list_payload(supplement_hard_constraints.get(field))]
        )
    for field in (
        "degree_requirement",
        "school_type_requirement",
        "experience_requirement",
        "gender_requirement",
        "age_requirement",
    ):
        if hard_constraints.get(field) is None and supplement_hard_constraints.get(field) is not None:
            hard_constraints[field] = supplement_hard_constraints[field]
    values["hard_constraints"] = hard_constraints

    preferences = _string_key_dict(values.get("preferences"))
    supplement_preferences = _string_key_dict(supplement_values.get("preferences"))
    for field in (
        "preferred_locations",
        "preferred_companies",
        "preferred_domains",
        "preferred_backgrounds",
        "preferred_query_terms",
    ):
        preferences[field] = unique_strings(
            [*_string_list_payload(preferences.get(field)), *_string_list_payload(supplement_preferences.get(field))]
        )
    values["preferences"] = preferences

    values["initial_query_term_pool"] = _merge_query_terms(
        _list_payload(values.get("initial_query_term_pool")),
        _list_payload(supplement_values.get("initial_query_term_pool")),
        effective_round_no=effective_round_no,
    )
    values["scoring_rationale"] = _merge_rationale(
        str(values.get("scoring_rationale") or ""),
        str(supplement_values.get("scoring_rationale") or ""),
    )
    return RequirementSheet.model_validate(values)


def prepare_requirement_supplement(
    supplement: RequirementSheet,
    *,
    effective_round_no: int,
) -> RequirementSheet:
    return supplement.model_copy(
        update={
            "initial_query_term_pool": [
                item.model_copy(
                    update={
                        "first_added_round": effective_round_no,
                        "active": item.queryability == "admitted",
                    }
                )
                for item in supplement.initial_query_term_pool
            ]
        }
    )


def _default_requirement_section(section_id: str | None) -> str | None:
    if section_id in {"must_have_capabilities", "preferred_capabilities", "exclusion_signals"}:
        return section_id
    if section_id is None:
        return "must_have_capabilities"
    return None


def _list_payload(value: object) -> list[object]:
    return list(value) if isinstance(value, list | tuple) else []


def _string_list_payload(value: object) -> list[str]:
    return [item for item in _list_payload(value) if isinstance(item, str)]


def _string_key_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _merge_query_terms(
    base_terms: list[object],
    supplement_terms: list[object],
    *,
    effective_round_no: int,
) -> list[QueryTermCandidate]:
    output = [QueryTermCandidate.model_validate(term) for term in base_terms]
    index = {
        _query_term_key(term.term): position
        for position, term in enumerate(output)
        if _query_term_key(term.term)
    }
    for term in supplement_terms:
        mapped_term = _object_mapping(term)
        if not mapped_term:
            continue
        candidate = QueryTermCandidate.model_validate(mapped_term)
        key = _query_term_key(candidate.term)
        if not key:
            continue
        existing_position = index.get(key)
        if existing_position is None:
            output.append(
                candidate.model_copy(
                    update={
                        "first_added_round": effective_round_no,
                        "active": candidate.queryability == "admitted",
                    }
                )
            )
            index[key] = len(output) - 1
            continue
        existing = output[existing_position]
        if existing.source == "job_title" and candidate.source == "job_title":
            continue
        if existing.queryability != "admitted":
            output[existing_position] = existing.model_copy(update={"active": False})
        elif candidate.queryability != "admitted":
            output[existing_position] = candidate.model_copy(
                update={"first_added_round": effective_round_no, "active": False}
            )
        else:
            output[existing_position] = existing.model_copy(
                update={"first_added_round": effective_round_no, "active": True}
            )
    return output


def _query_term_key(term: str) -> str:
    return " ".join(term.split()).casefold()


def _object_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(key, str)}


def _merge_rationale(base: str, supplement: str) -> str:
    base = base.strip()
    supplement = supplement.strip()
    if not supplement or supplement.casefold() == base.casefold():
        return base
    if not base:
        return supplement
    return f"{base} Additional user requirement: {supplement}"
