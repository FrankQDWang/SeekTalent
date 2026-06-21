from __future__ import annotations

CTS_TEXT_NATIVE_FIELDS_BY_FILTER_FIELD = {
    "company_names": "company",
    "work_content": "workContent",
}
CTS_ENUM_NATIVE_FIELDS_BY_FILTER_FIELD = {
    "degree_requirement": "degree",
    "school_type_requirement": "schoolType",
    "experience_requirement": "workExperienceRange",
}
CTS_DIRECT_TEXT_NATIVE_FILTERS = frozenset({"position"})
CTS_LOCATION_NATIVE_FILTER = "location"
CTS_INTEGER_CODE_FILTERS = frozenset(CTS_ENUM_NATIVE_FIELDS_BY_FILTER_FIELD.values())
CTS_ALLOWED_NATIVE_FILTERS = frozenset(
    {
        CTS_LOCATION_NATIVE_FILTER,
        *CTS_DIRECT_TEXT_NATIVE_FILTERS,
        *CTS_TEXT_NATIVE_FIELDS_BY_FILTER_FIELD.values(),
        *CTS_INTEGER_CODE_FILTERS,
    }
)
