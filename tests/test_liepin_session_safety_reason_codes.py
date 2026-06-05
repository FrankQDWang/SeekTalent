from __future__ import annotations

from seektalent.sources.liepin.runtime_lane import runtime_safe_reason_code_from_worker_failure_code
from seektalent.runtime.public_events import public_source_reason_code


def test_connection_safety_expired_maps_to_login_required_runtime_reason() -> None:
    assert runtime_safe_reason_code_from_worker_failure_code("connection_safety_expired") == "blocked_login_required"


def test_blocked_login_required_maps_to_public_login_required_reason() -> None:
    assert public_source_reason_code("blocked_login_required") == "source_login_required"
