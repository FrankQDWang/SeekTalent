from __future__ import annotations

PROTECTED_ATTRIBUTE_FIELDS: frozenset[str] = frozenset(
    {
        "age_requirement",
        "gender_requirement",
        "school_names",
    }
)
PROTECTED_ATTRIBUTE_LIST_TEXT = "age_requirement, gender_requirement, and school_names"
PROTECTED_ATTRIBUTE_FILTER_ADVICE_TEXT = (
    f"{PROTECTED_ATTRIBUTE_LIST_TEXT} are excluded from LLM filter advice."
)
PROTECTED_ATTRIBUTE_SCORING_TEXT = f"{PROTECTED_ATTRIBUTE_LIST_TEXT} are excluded from LLM scoring."
