from __future__ import annotations

from seektalent.clients.cts_models import CandidateSearchRequest
from seektalent.models import CTSQuery


ALLOWED_NATIVE_FILTERS = {
    "company",
    "position",
    "school",
    "workContent",
    "location",
    "degree",
    "schoolType",
    "workExperienceRange",
    "gender",
    "age",
}


def build_cts_request_payload(query: CTSQuery) -> tuple[dict[str, object], list[str]]:
    payload: dict[str, object] = {
        "keyword": query.keyword_query or None,
        "page": query.page,
        "pageSize": query.page_size,
    }
    notes = [
        "Dedup stays in runtime; CTS request does not receive seen ids.",
        "The project never forwards the full JD to CTS.",
        *query.adapter_notes,
    ]
    for field, value in query.native_filters.items():
        if field not in ALLOWED_NATIVE_FILTERS:
            raise ValueError(f"Unsupported native filter: {field}")
        if field != "location" and isinstance(value, list):
            raise ValueError(f"Native filter `{field}` must not be a list.")
        if field in {"degree", "schoolType", "workExperienceRange", "gender", "age"} and not isinstance(value, int):
            raise ValueError(f"Native filter `{field}` must be an integer code.")
        payload[field] = value
    request = CandidateSearchRequest.model_validate(payload)
    return request.model_dump(exclude_none=True), notes
