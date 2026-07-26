from __future__ import annotations

import json

import pytest

from seektalent.diagnostics_redaction import (
    DiagnosticsRedactionError,
    project_diagnostics,
)
from seektalent.diagnostics_registry import REDACTION_RULES
from seektalent.diagnostics_scalar import NON_NEGATIVE_INTEGER, enum_values


CANARY = "forbidden-value-must-never-appear"

FORBIDDEN_CORPUS = (
    ("Cookie", f"session={CANARY}"),
    ("authorization", f"Bearer {CANARY}"),
    ("auth", CANARY),
    ("access_token", CANARY),
    ("password", CANARY),
    ("apiKey", CANARY),
    ("handoff_key", CANARY),
    ("control_key", CANARY),
    ("hmac_key", CANARY),
    ("nonce", CANARY),
    ("runtime_attempt_fence_token", CANARY),
    ("observed_provider_account_subject", "real-account@example.com"),
    ("prompt", "Find a VP Engineering"),
    ("jd", "private job description"),
    ("resume", "private candidate resume"),
    ("candidate_name", "Private Candidate"),
    ("company_name", "Private Company"),
    ("school_name", "Private University"),
    ("search_query", "private search terms"),
    ("dom", "<html>private visible text</html>"),
    ("visible_text", "private page body"),
    ("screenshot", CANARY),
    ("download", CANARY),
    ("clipboard", CANARY),
    ("url", f"https://example.com/private?token={CANARY}"),
    ("ip", "192.168.1.24"),
    ("ssid", "Private WiFi"),
    ("proxy_url", f"http://user:{CANARY}@proxy.internal"),
    ("certificate_subject", "CN=Private Corp"),
    ("hostname", "private-macbook"),
    ("username", "private-user"),
    ("absolute_path", "/Users/private/workspace"),
    ("chrome_profile_path", r"C:\Users\private\Chrome"),
    ("sqlite", f"raw database bytes {CANARY}"),
    ("wal", CANARY),
    ("shm", CANARY),
    ("stdout", CANARY),
    ("stderr", CANARY),
    ("log", CANARY),
    ("exception_detail", CANARY),
    ("env", f"API_KEY={CANARY}"),
    ("command_line", f"program --token {CANARY}"),
)


@pytest.mark.parametrize(("key", "value"), FORBIDDEN_CORPUS)
def test_recursive_projection_never_exposes_forbidden_corpus(key: str, value: str) -> None:
    payload = {
        "safe_count": 2,
        "nested": [
            {"safe_status": "ready", key: value},
            {"alias": {key.upper(): value}},
        ],
    }
    projected = project_diagnostics(
        payload,
        allowlist={
            "safe_count": NON_NEGATIVE_INTEGER,
            "nested": [{"safe_status": enum_values("ready"), "alias": {}}],
        },
    )
    serialized = json.dumps(projected.model_dump(mode="json"), sort_keys=True)

    assert projected.value == {
        "safe_count": 2,
        "nested": [{"safe_status": "ready"}, {"alias": {}}],
    }
    assert projected.redacted_count >= 1
    assert value not in serialized
    assert CANARY not in serialized
    assert value not in repr(projected)
    for item in projected.report:
        assert item.rule in REDACTION_RULES
        assert item.count >= 1
        assert value not in item.path
        assert key.lower() not in item.path.lower()


def test_projection_report_is_bounded_and_deterministic() -> None:
    payload = {f"secret_{index}": CANARY for index in range(64)}
    first = project_diagnostics(payload, allowlist={})
    second = project_diagnostics(payload, allowlist={})

    assert first == second
    assert first.redacted_count == 64
    assert len(first.report) <= 32
    assert all(len(item.path) <= 128 for item in first.report)
    assert CANARY not in repr(first)


@pytest.mark.parametrize(
    "payload",
    (
        {"safe_ref": "https://example.com/private"},
        {"safe_ref": "/Users/private/workspace"},
        {"safe_ref": r"C:\Users\private\workspace"},
        {"safe_ref": "Bearer secret-value"},
        {"safe_status": "<html>private</html>"},
        {"safe_status": "192.168.1.24"},
    ),
)
def test_sensitive_values_under_allowlisted_keys_fail_closed_without_leak(payload) -> None:
    value = next(iter(payload.values()))
    with pytest.raises(DiagnosticsRedactionError) as exc_info:
        project_diagnostics(payload, allowlist={next(iter(payload)): enum_values("ready")})
    assert value not in str(exc_info.value)
    assert value not in repr(exc_info.value)


def test_projection_rejects_unbounded_or_invalid_allowlist_without_input_repr() -> None:
    with pytest.raises(DiagnosticsRedactionError, match="diagnostics_redaction_invalid_allowlist"):
        project_diagnostics({"safe": CANARY}, allowlist={"safe": object()})
    with pytest.raises(DiagnosticsRedactionError, match="diagnostics_redaction_too_deep"):
        project_diagnostics(
            {"a": {"b": {"c": {"d": 1}}}},
            allowlist={"a": {"b": {"c": {"d": NON_NEGATIVE_INTEGER}}}},
        )


def test_safe_looking_alias_cannot_admit_business_content() -> None:
    canary = "Private Candidate Jane Doe resume content"
    with pytest.raises(DiagnosticsRedactionError) as exc_info:
        project_diagnostics(
            {"safe_status": canary},
            allowlist={"safe_status": enum_values("ready", "not_ready")},
        )
    assert canary not in str(exc_info.value)
    assert canary not in repr(exc_info.value)
    assert canary not in repr(exc_info.value.__dict__)


def test_invalid_allowlist_is_rejected_before_payload_walk() -> None:
    with pytest.raises(DiagnosticsRedactionError, match="diagnostics_redaction_invalid_allowlist"):
        project_diagnostics({}, allowlist={"missing": object()})


@pytest.mark.parametrize("value", (-(2**53), 2**53))
def test_projection_rejects_integers_outside_ijson_safe_range(value: int) -> None:
    with pytest.raises(DiagnosticsRedactionError) as exc_info:
        project_diagnostics(
            {"safe_count": value},
            allowlist={"safe_count": NON_NEGATIVE_INTEGER},
        )
    assert str(value) not in str(exc_info.value)
    assert str(value) not in repr(exc_info.value)


def test_projection_has_a_global_node_and_key_budget() -> None:
    payload = {
        "groups": [
            {f"safe_{item}": item for item in range(64)}
            for _ in range(32)
        ]
    }
    allowlist = {
        "groups": [
            {f"safe_{item}": NON_NEGATIVE_INTEGER for item in range(64)}
        ]
    }
    with pytest.raises(DiagnosticsRedactionError, match="diagnostics_redaction_budget_exceeded"):
        project_diagnostics(payload, allowlist=allowlist)


def test_projection_node_budget_has_an_exact_deterministic_boundary() -> None:
    allowlist = {"groups": [[NON_NEGATIVE_INTEGER]]}
    within_budget = {"groups": [[item] * 32 for item in range(30)]}
    over_budget = {"groups": [[item] * 32 for item in range(31)]}

    assert project_diagnostics(within_budget, allowlist=allowlist).value == within_budget
    with pytest.raises(DiagnosticsRedactionError, match="diagnostics_redaction_budget_exceeded"):
        project_diagnostics(over_budget, allowlist=allowlist)


def test_projection_key_budget_has_an_exact_deterministic_boundary() -> None:
    object_contract = {f"safe_{item}": NON_NEGATIVE_INTEGER for item in range(64)}
    allowlist = {"groups": [object_contract]}
    within_budget = {
        "groups": [
            {f"safe_{item}": item for item in range(64)}
            for _ in range(7)
        ] + [{f"safe_{item}": item for item in range(63)}]
    }
    over_budget = {
        "groups": [
            {f"safe_{item}": item for item in range(64)}
            for _ in range(8)
        ]
    }

    assert project_diagnostics(within_budget, allowlist=allowlist).value == within_budget
    with pytest.raises(DiagnosticsRedactionError, match="diagnostics_redaction_budget_exceeded"):
        project_diagnostics(over_budget, allowlist=allowlist)


@pytest.mark.parametrize(
    "payload",
    (
        {"safe_status": "Private Candidate Jane Doe resume content"},
        {"SAFE_STATUS": "Private Candidate Jane Doe resume content"},
        {"nested": {"safe_status": "Private Candidate Jane Doe resume content"}},
        {"items": [{"safe_status": "Private Candidate Jane Doe resume content"}]},
    ),
)
def test_business_content_cannot_enter_through_alias_case_nesting_or_list(payload) -> None:
    contracts = {
        "safe_status": enum_values("ready", "not_ready"),
        "nested": {"safe_status": enum_values("ready", "not_ready")},
        "items": [{"safe_status": enum_values("ready", "not_ready")}],
    }
    try:
        projection = project_diagnostics(payload, allowlist=contracts)
    except DiagnosticsRedactionError as exc:
        serialized = repr(exc.__dict__)
    else:
        serialized = json.dumps(projection.model_dump(mode="json"), sort_keys=True)
    assert "Private Candidate Jane Doe" not in serialized
