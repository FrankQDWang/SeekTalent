from __future__ import annotations

import re
from collections.abc import Mapping

LIEPIN_FILTER_SECTION_LABELS = {
    "legacy": "",
    "current": "目前城市",
    "expected": "期望城市",
    "experience": "工作年限",
    "age": "年龄",
    "education": "教育经历",
    "recruitment_type": "统招要求",
    "school_type": "院校要求",
}
RETRYABLE_NATIVE_FILTER_REASONS = frozenset(
    {
        "liepin_opencli_filter_unapplied",
        "liepin_opencli_stale_ref",
        "liepin_opencli_selector_not_found",
        "liepin_opencli_status_unavailable",
        "liepin_opencli_target_not_found",
        "liepin_opencli_timeout",
    }
)


def liepin_filter_actions(native_filters: Mapping[str, object]) -> tuple[tuple[str, str, str], ...]:
    actions: list[tuple[str, str, str]] = []
    city = native_filters.get("city")
    if isinstance(city, str) and city.strip():
        actions.append(("city", "legacy", city.strip()))
    elif (city_option := _object_mapping(city)) is not None:
        action = _filter_action_from_option("city", city_option)
        if action is not None:
            actions.append(action)
    experience = native_filters.get("experience")
    if (experience_option := _object_mapping(experience)) is not None:
        action = _filter_action_from_option("experience", experience_option)
        if action is not None:
            actions.append(action)
        else:
            label = _experience_label(experience_option)
            if label is not None:
                actions.append(("experience", "legacy", label))
    age = native_filters.get("age")
    if (age_option := _object_mapping(age)) is not None:
        action = _filter_action_from_option("age", age_option)
        if action is not None:
            actions.append(action)
        else:
            label = _age_label(age_option)
            if label is not None:
                actions.append(("age", "legacy", label))
    for key in ("degree", "recruitmentType"):
        option = _object_mapping(native_filters.get(key))
        if option is not None:
            action = _filter_action_from_option(key, option)
            if action is not None:
                actions.append(action)
    school_types = native_filters.get("schoolTypes")
    if isinstance(school_types, list):
        for raw_option in school_types:
            option = _object_mapping(raw_option)
            if option is not None:
                action = _filter_action_from_option("schoolTypes", option)
                if action is not None:
                    actions.append(action)
    return tuple(actions)


def skipped_liepin_filter_names(native_filters: Mapping[str, object]) -> tuple[str, ...]:
    known = {
        "city",
        "experience",
        "age",
        "degree",
        "recruitmentType",
        "schoolTypes",
        "partialReasonCodes",
        "requiredFilterNames",
        "optionalFilterNames",
        "sourceTarget",
    }
    return tuple(sorted(str(key) for key in native_filters if str(key) not in known))


def native_filter_is_required(native_filters: Mapping[str, object], filter_name: str) -> bool:
    optional = _native_filter_name_set(native_filters.get("optionalFilterNames"))
    if filter_name in optional:
        return False
    required = _native_filter_name_set(native_filters.get("requiredFilterNames"))
    return not required or filter_name in required


def liepin_filter_menu_label(filter_name: str, section: str) -> str | None:
    if section != "legacy":
        section_label = LIEPIN_FILTER_SECTION_LABELS.get(section)
        if section_label:
            return section_label
    return {
        "city": "城市",
        "experience": "工作经验",
        "age": "年龄",
    }.get(filter_name)


def native_filter_selection_applied(state_text: str, *, section: str, label: str) -> bool:
    normalized_label = _normalize_liepin_filter_text(label)
    if not normalized_label:
        return False
    accepted_labels = {normalized_label}
    if section == "recruitment_type" and normalized_label == "统招本科":
        accepted_labels.add("统招")
    normalized_sections = {
        _normalize_liepin_filter_text(LIEPIN_FILTER_SECTION_LABELS.get(candidate) or "")
        for candidate in _city_section_lookup_order(section)
    }
    normalized_sections = {candidate for candidate in normalized_sections if candidate}
    lines = state_text.splitlines()
    in_target_section = False
    for index, raw_line in enumerate(lines):
        line = _normalize_liepin_filter_text(raw_line)
        if not line:
            continue
        if _line_starts_known_filter_section(raw_line):
            in_target_section = any(normalized_section in line for normalized_section in normalized_sections)
        has_label = any(candidate in line for candidate in accepted_labels)
        if not has_label:
            title_section = next(
                (normalized_section for normalized_section in normalized_sections if f"title={normalized_section}" in line),
                None,
            )
            if title_section:
                chip_text = _normalize_liepin_filter_text("".join(lines[index : index + 6]))
                if any(candidate in chip_text for candidate in accepted_labels):
                    return True
            continue
        if line.startswith(("已选", "当前条件", "筛选条件")):
            return True
        if any(normalized_section in line for normalized_section in normalized_sections) and "已选" in line:
            return True
        if in_target_section and _line_indicates_selected_filter(raw_line):
            return True
        if any(normalized_section in line for normalized_section in normalized_sections) and "<label" not in raw_line:
            return True
    return False


def native_filter_option_ref_in_section(state_text: str, *, section: str, label: str) -> str | None:
    if section == "legacy":
        return _native_filter_option_ref(state_text, label)
    if section in {"current", "expected"}:
        city_picker_open = native_filter_city_search_input_ref(state_text) is not None
        if city_picker_open:
            return _native_filter_city_result_option_ref(state_text, label)
        for candidate_section in _city_section_lookup_order(section):
            ref = _native_filter_option_ref_in_exact_section(
                state_text,
                section=candidate_section,
                label=label,
                city_picker_open=city_picker_open,
            )
            if ref is not None:
                return ref
        return None
    return _native_filter_option_ref_in_exact_section(
        state_text,
        section=section,
        label=label,
        city_picker_open=False,
    )


def _native_filter_option_ref_in_exact_section(
    state_text: str,
    *,
    section: str,
    label: str,
    city_picker_open: bool,
) -> str | None:
    section_label = LIEPIN_FILTER_SECTION_LABELS.get(section)
    if section_label is None:
        return None
    in_section = False
    fallback_dropdown_ref: str | None = None
    for line in state_text.splitlines():
        if _line_starts_known_filter_section(line) and section_label not in line and in_section:
            if city_picker_open:
                break
            return fallback_dropdown_ref
        if section_label in line:
            in_section = True
            continue
        if not in_section:
            continue
        match = re.search(rf"\[([A-Za-z0-9_-]{{1,64}})\]<label[^>]*>\s*{re.escape(label)}\s*</label>", line)
        if match is not None:
            return match.group(1)
    if city_picker_open:
        return _native_filter_city_result_option_ref(state_text, label)
    return None


def native_filter_control_ref_in_section(state_text: str, *, section: str) -> str | None:
    if section == "legacy":
        return None
    if section in {"current", "expected"}:
        for candidate_section in _city_section_lookup_order(section):
            ref = _native_filter_control_ref_in_exact_section(state_text, section=candidate_section)
            if ref is not None:
                return ref
        return None
    return _native_filter_control_ref_in_exact_section(state_text, section=section)


def _native_filter_control_ref_in_exact_section(state_text: str, *, section: str) -> str | None:
    section_label = LIEPIN_FILTER_SECTION_LABELS.get(section)
    if section_label is None:
        return None
    in_section = False
    fallback_dropdown_ref: str | None = None
    for line in state_text.splitlines():
        if _line_starts_known_filter_section(line) and section_label not in line and in_section:
            return None
        if section_label in line:
            in_section = True
            match = _line_ref_for_clickable_filter_control(line)
            if match is not None:
                return match
            continue
        if not in_section:
            continue
        match = _line_ref_for_clickable_filter_control(line)
        if match is not None:
            return match
        preferred_ref = _line_ref_for_filter_dropdown_value(line, section=section)
        if preferred_ref is not None:
            return preferred_ref
        other_city_ref = _line_ref_for_other_city_picker(line, section=section)
        if other_city_ref is not None:
            return other_city_ref
        fallback_ref = _line_ref_for_filter_dropdown_shell(line, section=section)
        if fallback_ref is not None and fallback_dropdown_ref is None:
            fallback_dropdown_ref = fallback_ref
    return fallback_dropdown_ref


def _city_section_lookup_order(section: str) -> tuple[str, ...]:
    if section == "current":
        return ("current", "expected")
    if section == "expected":
        return ("expected", "current")
    return (section,)


def native_filter_city_search_input_ref(state_text: str) -> str | None:
    for line in state_text.splitlines():
        if ("input" not in line and "combobox" not in line) or "城市" not in line:
            continue
        match = re.search(r"\[([A-Za-z0-9_-]{1,64})\]", line)
        if match is not None:
            return match.group(1)
    return None


def native_filter_city_overseas_tab_ref(state_text: str) -> str | None:
    for line in _city_picker_candidate_lines(state_text):
        if _line_visible_filter_text(line) != "海外":
            continue
        match = re.search(r"\[([A-Za-z0-9_-]{1,64})\]", line)
        if match is not None:
            return match.group(1)
    return None


def native_filter_city_confirm_ref(state_text: str) -> str | None:
    candidate_lines = _city_picker_candidate_lines(state_text)
    for index, line in enumerate(candidate_lines):
        if "<button" not in line:
            continue
        nearby_text = _normalize_liepin_filter_text("".join(candidate_lines[index : index + 4]))
        if "确认" not in nearby_text:
            continue
        match = re.search(r"\[([A-Za-z0-9_-]{1,64})\]", line)
        if match is not None:
            return match.group(1)
    return None


def native_filter_city_picker_selection_contains(state_text: str, *, label: str) -> bool:
    normalized_label = _normalize_liepin_filter_text(label)
    if not normalized_label:
        return False
    candidate_lines = _city_picker_candidate_lines(state_text)
    for index, line in enumerate(candidate_lines):
        if "已选" not in line:
            continue
        selected_text = _normalize_liepin_filter_text("".join(candidate_lines[index : index + 10]))
        return normalized_label in selected_text
    return False


def native_filter_clear_filters_ref(state_text: str) -> str | None:
    lines = state_text.splitlines()
    for index, line in enumerate(lines):
        if "清空筛选条件" not in line:
            continue
        for candidate in (line, *reversed(lines[max(0, index - 2) : index])):
            match = re.search(r"\[([A-Za-z0-9_-]{1,64})\]", candidate)
            if match is not None:
                return match.group(1)
    return None


def native_filter_option_visible_in_section(state_text: str, *, section: str, label: str) -> bool:
    if native_filter_option_ref_in_section(state_text, section=section, label=label) is not None:
        return True
    if section == "legacy":
        return _native_filter_option_visible(state_text, label)
    return False


def _filter_action_from_option(filter_name: str, option: Mapping[str, object]) -> tuple[str, str, str] | None:
    section = str(option.get("section") or "").strip()
    label = str(option.get("label") or "").strip()
    if section and label:
        return filter_name, section, label
    return None


def _object_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items()}


def _native_filter_name_set(value: object) -> set[str]:
    if not isinstance(value, list | tuple):
        return set()
    return {str(item).strip() for item in value if isinstance(item, str) and item.strip()}


def _experience_label(experience: Mapping[str, object]) -> str | None:
    min_years = experience.get("minYears")
    max_years = experience.get("maxYears")
    if isinstance(min_years, int) and isinstance(max_years, int):
        return f"{min_years}-{max_years}年"
    if isinstance(min_years, int):
        return f"{min_years}年以上"
    if isinstance(max_years, int):
        return f"{max_years}年以下"
    return None


def _age_label(age: Mapping[str, object]) -> str | None:
    min_age = age.get("min")
    max_age = age.get("max")
    if isinstance(min_age, int) and isinstance(max_age, int):
        return f"{min_age}-{max_age}岁"
    if isinstance(max_age, int):
        return f"{max_age}岁以下"
    if isinstance(min_age, int):
        return f"{min_age}岁以上"
    return None


def _normalize_liepin_filter_text(value: str) -> str:
    return re.sub(r"\s+", "", value.strip())


def _native_filter_option_visible(state_text: str, label: str) -> bool:
    if _native_filter_option_ref(state_text, label) is not None:
        return True
    escaped_label = re.escape(label)
    return any(re.search(rf"(?:\bbutton\b|<button).*?{escaped_label}", line) for line in state_text.splitlines())


def _native_filter_option_ref(state_text: str, label: str) -> str | None:
    escaped_label = re.escape(label)
    pattern = re.compile(rf"\[([A-Za-z0-9_-]{{1,64}})\]<label[^>]*>\s*{escaped_label}\s*</label>")
    for line in state_text.splitlines():
        match = pattern.search(line)
        if match is not None:
            return match.group(1)
    return None


def _native_filter_city_result_option_ref(state_text: str, label: str) -> str | None:
    candidate_lines = _city_picker_candidate_lines(state_text)
    exact_ref = _native_filter_option_ref("\n".join(candidate_lines), label)
    if exact_ref is not None:
        return exact_ref
    normalized_label = _normalize_liepin_filter_text(label)
    if not normalized_label:
        return None
    candidates: list[tuple[int, int, str]] = []
    for line in candidate_lines:
        if "input" in line or "combobox" in line:
            continue
        score = _city_result_match_score(line, normalized_label)
        if score is None:
            continue
        match = re.search(r"\[([A-Za-z0-9_-]{1,64})\]", line)
        if match is not None:
            candidates.append((score, len(candidates), match.group(1)))
    if not candidates:
        return None
    return min(candidates)[2]


def _city_result_match_score(line: str, normalized_label: str) -> int | None:
    text = re.sub(r"\[[^\]]+\]", "", line)
    text = re.sub(r"<[^>]*>", "", text)
    normalized_line = _normalize_liepin_filter_text(text)
    if normalized_line == f"全{normalized_label}":
        return -1
    if normalized_line == normalized_label:
        return 0
    if normalized_line.endswith(f"·{normalized_label}"):
        return 1
    if normalized_line.endswith(normalized_label):
        return 2
    if f"·{normalized_label}" in normalized_line:
        return 3
    return None


def _city_picker_candidate_lines(state_text: str) -> tuple[str, ...]:
    lines = tuple(state_text.splitlines())
    marker_indexes = [
        index
        for index, line in enumerate(lines)
        if "请选择城市" in line or (("input" in line or "combobox" in line) and "城市" in line)
    ]
    if not marker_indexes:
        return lines
    candidate_lines: list[str] = []
    for line in lines[min(marker_indexes) :]:
        if candidate_lines and _line_starts_known_filter_section(line):
            break
        candidate_lines.append(line)
    return tuple(candidate_lines)


def _line_ref_for_clickable_filter_control(line: str) -> str | None:
    if "button" not in line and "combobox" not in line:
        return None
    match = re.search(r"\[([A-Za-z0-9_-]{1,64})\]", line)
    return match.group(1) if match is not None else None


def _line_ref_for_filter_dropdown_shell(line: str, *, section: str) -> str | None:
    if section != "recruitment_type" or "<div" not in line:
        return None
    match = re.search(r"\[([A-Za-z0-9_-]{1,64})\]", line)
    return match.group(1) if match is not None else None


def _line_ref_for_filter_dropdown_value(line: str, *, section: str) -> str | None:
    if section != "recruitment_type" or "统招/非统招" not in line:
        return None
    match = re.search(r"\[([A-Za-z0-9_-]{1,64})\]", line)
    return match.group(1) if match is not None else None


def _line_ref_for_other_city_picker(line: str, *, section: str) -> str | None:
    if section not in {"current", "expected"} or "其他" not in line:
        return None
    if "<label" not in line and "button" not in line and _line_visible_filter_text(line) != "其他":
        return None
    match = re.search(r"\[([A-Za-z0-9_-]{1,64})\]", line)
    return match.group(1) if match is not None else None


def _line_visible_filter_text(line: str) -> str:
    text = re.sub(r"\[[^\]]+\]", "", line)
    text = re.sub(r"<[^>]*>", "", text)
    return _normalize_liepin_filter_text(text)


def _line_starts_known_filter_section(line: str) -> bool:
    return any(label and label in line for label in LIEPIN_FILTER_SECTION_LABELS.values())


def _line_indicates_selected_filter(line: str) -> bool:
    lowered = line.lower()
    return bool(
        re.search(
            r"(?:[-_]checked\b|\bchecked\b|\bselected\b|\bactive\b|aria-(?:checked|selected)=['\"]?true)",
            lowered,
        )
    )
