from __future__ import annotations

from seektalent.opencli_browser.fault_isolation import isolated_call


def test_isolated_call_returns_the_primary_value() -> None:
    assert isolated_call(lambda: "ok", lambda _exc: None) == "ok"


def test_isolated_call_reports_and_swallows_optional_safeguard_failures() -> None:
    failures: list[str] = []

    def fail() -> str:
        raise RuntimeError("lock failed")

    assert isolated_call(fail, lambda exc: failures.append(type(exc).__name__)) is None
    assert failures == ["RuntimeError"]


def test_isolated_call_also_contains_diagnostic_failures() -> None:
    def fail() -> str:
        raise RuntimeError("lock failed")

    def broken_diagnostic(_exc: Exception) -> None:
        raise RuntimeError("logger failed")

    assert isolated_call(fail, broken_diagnostic) is None
