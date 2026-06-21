from __future__ import annotations

from seektalent.clients.cts_models import CandidateSearchRequest
from seektalent.cts_filter_policy import CTS_ALLOWED_NATIVE_FILTERS, CTS_INTEGER_CODE_FILTERS
from seektalent.models import CTSQuery


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
        if field not in CTS_ALLOWED_NATIVE_FILTERS:
            raise ValueError(f"Unsupported native filter: {field}")
        if field != "location" and isinstance(value, list):
            raise ValueError(f"Native filter `{field}` must not be a list.")
        if field in CTS_INTEGER_CODE_FILTERS and not _is_integer_code(value):
            raise ValueError(f"Native filter `{field}` must be an integer code.")
        payload[field] = value
    request = CandidateSearchRequest.model_validate(payload)
    return request.model_dump(exclude_none=True), notes


def _is_integer_code(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
