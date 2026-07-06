from __future__ import annotations

from seektalent.source_contracts.safe_serialization import sanitize_count_mapping, sanitize_step_name


def test_sanitize_step_name_rejects_removed_cleanup_step() -> None:
    assert sanitize_step_name("cleanup_" + "detail_tabs") is None


def test_sanitize_count_mapping_rejects_removed_cleanup_count() -> None:
    assert sanitize_count_mapping({"visible_cards": 2, "closed_" + "tabs": 3}) == {"visible_cards": 2}
