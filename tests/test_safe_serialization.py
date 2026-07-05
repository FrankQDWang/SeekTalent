from __future__ import annotations

from seektalent.source_contracts.safe_serialization import sanitize_step_name


def test_sanitize_step_name_rejects_removed_cleanup_step() -> None:
    assert sanitize_step_name("cleanup_" + "detail_tabs") is None
