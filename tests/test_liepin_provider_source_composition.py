from __future__ import annotations

import inspect
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _line_count(path: str) -> int:
    return len(_text(path).splitlines())


def test_liepin_site_adapter_public_methods_stay_compatible() -> None:
    from seektalent.providers.liepin.liepin_site_adapter import LiepinSiteAdapter

    signatures = {
        f"{name}{inspect.signature(member)}"
        for name, member in inspect.getmembers(LiepinSiteAdapter, inspect.isfunction)
        if not name.startswith("_")
    }

    assert signatures == {
        "apply_liepin_native_filters(self, *, source_run_id: 'str', native_filters: 'Mapping[str, object]') -> 'OpenCliBrowserResult'",
        "capture_liepin_detail_resume(self, *, source_run_id: 'str', rank: 'int') -> 'OpenCliBrowserResult'",
        "cleanup_idle_lease(self, *, force: 'bool' = False) -> 'OpenCliBrowserResult'",
        "cleanup_orphaned_tabs(self, *, force: 'bool' = False) -> 'OpenCliBrowserResult'",
        "click(self, *, target: 'str') -> 'OpenCliBrowserResult'",
        "extract_visible_liepin_cards(self, *, source_run_id: 'str', max_cards: 'int') -> 'OpenCliBrowserResult'",
        "fill(self, *, target: 'str', text: 'str') -> 'OpenCliBrowserResult'",
        "finalize_liepin_resumes(self, *, source_run_id: 'str', query: 'str', max_pages: 'int', max_cards: 'int', cards_seen: 'int | None' = None, target_resumes: 'int | None' = None) -> 'dict[str, object]'",
        "find(self, *, query: 'str') -> 'OpenCliBrowserResult'",
        "get_url(self) -> 'OpenCliBrowserResult'",
        "open_liepin_detail(self, *, source_run_id: 'str', ref: 'str', rank: 'int') -> 'OpenCliBrowserResult'",
        "open_liepin_tab(self, url: 'str') -> 'OpenCliBrowserResult'",
        "scroll(self, *, direction: 'str') -> 'OpenCliBrowserResult'",
        "search_liepin_cards(self, *, source_run_id: 'str', query: 'str', max_pages: 'int', max_cards: 'int', native_filters: 'Mapping[str, object] | None' = None) -> 'dict[str, object]'",
        "search_liepin_resumes(self, *, source_run_id: 'str', query: 'str', target_resumes: 'int', max_pages: 'int', max_cards: 'int', native_filters: 'Mapping[str, object] | None' = None) -> 'dict[str, object]'",
        "state(self) -> 'OpenCliBrowserResult'",
        "status(self) -> 'OpenCliBrowserResult'",
        "wait_time(self, *, seconds: 'int') -> 'OpenCliBrowserResult'",
        "watch_idle_lease(self) -> 'OpenCliBrowserResult'",
    }


def test_source_adapters_current_public_exports_stay_compatible() -> None:
    import seektalent.source_adapters as source_adapters

    for name in (
        "build_source_enabled_runtime",
        "build_default_source_registry",
        "build_source_lane_request_runner",
        "default_source_round_adapter_provider",
        "default_source_query_policies",
        "public_source_reason_code",
        "_run_cts_source_round",
        "_run_liepin_source_round",
        "_source_filter_warning_reason",
        "run_liepin_logical_query_bundle",
    ):
        assert hasattr(source_adapters, name)


@pytest.mark.xfail(strict=True, reason="Task 2 moves Liepin helpers and brings liepin_site_adapter.py below 2500 lines.")
def test_liepin_site_adapter_remains_below_ai_redline_after_first_provider_split() -> None:
    assert _line_count("src/seektalent/providers/liepin/liepin_site_adapter.py") < 2500


def test_liepin_site_adapter_does_not_own_opencli_runtime_boundaries() -> None:
    text = _text("src/seektalent/providers/liepin/liepin_site_adapter.py")

    forbidden = (
        "subprocess.run",
        "SubprocessOpenCliCommandRunner",
        "SubprocessChromeWindowCounter",
        "SubprocessBlankChromeWindowCloser",
        "SubprocessCurrentChromeTabOpener",
        ".run_raw(",
        '"browser", self._config.session',
    )

    assert all(item not in text for item in forbidden)
