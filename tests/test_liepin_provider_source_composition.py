from __future__ import annotations

import inspect
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


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
        "click(self, *, target: 'str') -> 'OpenCliBrowserResult'",
        "extract_structured_liepin_cards(self, *, source_run_id: 'str', max_cards: 'int') -> 'OpenCliBrowserResult'",
        "extract_visible_liepin_cards(self, *, source_run_id: 'str', max_cards: 'int') -> 'OpenCliBrowserResult'",
        "fill(self, *, target: 'str', text: 'str') -> 'OpenCliBrowserResult'",
        "finalize_liepin_resumes(self, *, source_run_id: 'str', query: 'str', max_pages: 'int', max_cards: 'int', cards_seen: 'int | None' = None, target_resumes: 'int | None' = None) -> 'dict[str, object]'",
        "find(self, *, query: 'str') -> 'OpenCliBrowserResult'",
        "get_url(self) -> 'OpenCliBrowserResult'",
        "open_liepin_detail(self, *, source_run_id: 'str', ref: 'str', rank: 'int') -> 'OpenCliBrowserResult'",
        "open_liepin_tab(self, url: 'str') -> 'OpenCliBrowserResult'",
        "recover_connection(self) -> 'OpenCliBrowserResult'",
        "scroll(self, *, direction: 'str') -> 'OpenCliBrowserResult'",
        "search_liepin_cards(self, *, source_run_id: 'str', query: 'str', max_pages: 'int', max_cards: 'int', native_filters: 'Mapping[str, object] | None' = None) -> 'dict[str, object]'",
        "search_liepin_resumes(self, *, source_run_id: 'str', query: 'str', target_resumes: 'int', max_pages: 'int', max_cards: 'int', native_filters: 'Mapping[str, object] | None' = None) -> 'dict[str, object]'",
        "state(self) -> 'OpenCliBrowserResult'",
        "status(self) -> 'OpenCliBrowserResult'",
        "wait_liepin_detail_ready(self, *, source_run_id: 'str', rank: 'int') -> 'OpenCliBrowserResult'",
        "wait_time(self, *, seconds: 'int') -> 'OpenCliBrowserResult'",
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


def test_liepin_site_adapter_does_not_own_opencli_runtime_boundaries() -> None:
    text = _text("src/seektalent/providers/liepin/liepin_site_adapter.py")

    forbidden = (
        "subprocess.run",
        "SubprocessOpenCliCommandRunner",
        "SubprocessCurrentChromeTabOpener",
        ".run_raw(",
        '"browser", self._config.session',
    )

    assert all(item not in text for item in forbidden)


def test_liepin_site_parsing_module_owns_public_page_helpers() -> None:
    from seektalent.providers.liepin import liepin_site_parsing

    assert liepin_site_parsing.extract_allowed_click_refs("搜索 [ref=16] 查询") == ("16",)
    assert liepin_site_parsing.extract_liepin_search_input_ref(
        "包含全部关键词\n[3]<input role=combobox id=rc_select_1>"
    ) == "3"
    assert (
        liepin_site_parsing.classify_liepin_state(
            url="https://h.liepin.com/search/getConditionItem#session",
            text="请登录后继续",
        )
        == "liepin_opencli_login_required"
    )


def test_liepin_site_adapter_keeps_helper_compatibility_exports() -> None:
    from seektalent.providers.liepin.liepin_site_adapter import (
        build_observation,
        classify_liepin_state,
        extract_allowed_click_refs,
        extract_liepin_card_summaries,
    )

    assert build_observation("搜索 [ref=16] 查询")["allowedClickRefs"] == ("16",)
    assert (
        classify_liepin_state(url="https://h.liepin.com/search/getConditionItem#session", text="请登录")
        == "liepin_opencli_login_required"
    )
    assert extract_allowed_click_refs("搜索 [ref=16] 查询") == ("16",)
    assert isinstance(extract_liepin_card_summaries("候选人", max_cards=1), tuple)


def test_liepin_site_payloads_module_owns_current_blocked_cards_envelope() -> None:
    from seektalent.providers.liepin import liepin_site_payloads

    writes: list[tuple[str, str, object]] = []

    def write_pi_artifact(visibility: str, path: str, payload: object) -> str:
        writes.append((visibility, path, payload))
        return f"artifact://{path}"

    blocked = liepin_site_payloads.blocked_cards_envelope(
        source_run_id="run-1",
        query="python",
        safe_reason_code="liepin_opencli_login_required",
        safe_run_id="run-1",
        pages_visited=2,
        events=({"action_kind": "observe"},),
        write_pi_artifact=write_pi_artifact,
    )

    assert blocked == {
        "schema_version": "seektalent.pi_liepin_cards.v1",
        "status": "blocked",
        "stop_reason": "blocked_backend_unavailable",
        "safe_reason_code": "liepin_opencli_login_required",
        "source_run_id": "run-1",
        "query": "python",
        "cards_seen": 0,
        "cards_returned": 0,
        "pages_visited": 2,
        "action_trace_ref": "artifact://pi-trace/run-1/action-trace.json",
        "safe_summary_refs": [],
        "protected_snapshot_refs": [],
        "cards": [],
    }
    assert writes[0][0] == "protected"
    assert writes[0][1] == "pi-trace/run-1/action-trace.json"


def test_source_adapters_is_import_compatible_package() -> None:
    import seektalent.source_adapters as source_adapters

    assert not (ROOT / "src/seektalent/source_adapters.py").exists()
    assert (ROOT / "src/seektalent/source_adapters/__init__.py").exists()
    assert hasattr(source_adapters, "build_source_enabled_runtime")
    assert hasattr(source_adapters, "build_default_source_registry")
    assert hasattr(source_adapters, "build_source_lane_request_runner")
    assert hasattr(source_adapters, "default_source_round_adapter_provider")
    assert hasattr(source_adapters, "default_source_query_policies")
    assert hasattr(source_adapters, "public_source_reason_code")
    assert hasattr(source_adapters, "_run_cts_source_round")
    assert hasattr(source_adapters, "_run_liepin_source_round")
    assert hasattr(source_adapters, "_source_filter_warning_reason")


def test_source_adapters_package_splits_runtime_composition_responsibilities() -> None:
    expected = {
        "__init__.py",
        "runtime_factory.py",
        "registry.py",
        "query_policy.py",
        "round_adapters.py",
        "evidence.py",
    }

    package_root = ROOT / "src/seektalent/source_adapters"
    assert expected <= {path.name for path in package_root.glob("*.py")}
