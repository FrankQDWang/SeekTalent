from __future__ import annotations

from datetime import UTC, datetime

from seektalent_ui.workbench_store_helpers import (
    bounded_text,
    json_to_dict,
    json_to_list,
    parse_iso,
    safe_candidate_text,
    stable_id,
)


def test_workbench_store_helpers_parse_safe_json_defaults() -> None:
    assert json_to_list('["a", 2, "b"]') == ["a", "b"]
    assert json_to_list("{bad") == []
    assert json_to_dict('{"x": 1, "y": "z"}') == {"x": 1, "y": "z"}
    assert json_to_dict("[1, 2]") == {}


def test_workbench_store_helpers_bound_redact_and_hash_values() -> None:
    assert bounded_text("  abcdef  ", 3) == "abc"
    assert safe_candidate_text("hello document.cookie", 100) is not None
    assert stable_id("item", "session", "candidate") == stable_id("item", "session", "candidate")
    assert stable_id("item", "session", "candidate").startswith("item_")


def test_workbench_store_helpers_parse_iso_as_utc() -> None:
    assert parse_iso("2026-01-02T03:04:05").tzinfo == UTC
    assert parse_iso("2026-01-02T03:04:05+08:00") == datetime(2026, 1, 1, 19, 4, 5, tzinfo=UTC)
