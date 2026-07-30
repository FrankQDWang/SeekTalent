from __future__ import annotations

import inspect
import json
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
        "run_liepin_details_operation(self, *, source_run_id: 'str', card_ref: 'str', rank: 'int', open_mode: 'str', provider_candidate_key_hash: 'str | None' = None, expected_provider_candidate_key_hash: 'str | None' = None) -> 'tuple[dict[str, object], OpenCliBrowserResult]'",
        "scroll(self, *, direction: 'str') -> 'OpenCliBrowserResult'",
        "search_liepin_cards(self, *, source_run_id: 'str', query: 'str', max_pages: 'int', max_cards: 'int', native_filters: 'Mapping[str, object] | None' = None) -> 'dict[str, object]'",
        "search_liepin_resumes(self, *, source_run_id: 'str', query: 'str', target_resumes: 'int', max_pages: 'int', max_cards: 'int', native_filters: 'Mapping[str, object] | None' = None) -> 'dict[str, object]'",
        "session_status_probe(self, *, connection_id: 'str', provider_account_hash: 'str | None') -> 'SessionStatus'",
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


def test_liepin_cards_artifacts_do_not_overwrite_between_queries_in_one_lane() -> None:
    from seektalent.providers.liepin import liepin_site_payloads

    writes: dict[tuple[str, str], object] = {}

    def write_pi_artifact(visibility: str, path: str, payload: object) -> str:
        writes[(visibility, path)] = payload
        return f"artifact://{path}"

    first_identity = liepin_site_payloads.cards_trace_identity(
        query="SENTINEL_SHARED_QUERY",
        native_filters={"city": {"section": "expected", "label": "苏州"}},
        max_pages=1,
        max_cards=10,
    )
    second_identity = liepin_site_payloads.cards_trace_identity(
        query="SENTINEL_SHARED_QUERY",
        native_filters={"city": {"section": "expected", "label": "杭州"}},
        max_pages=1,
        max_cards=10,
    )
    blocked_identity = liepin_site_payloads.cards_trace_identity(
        query="SENTINEL_SHARED_QUERY",
        native_filters={"city": {"section": "expected", "label": "广州"}},
        max_pages=1,
        max_cards=10,
    )
    assert first_identity != second_identity
    assert len(first_identity) == len(second_identity) == 64
    assert "SENTINEL_SHARED_QUERY" not in first_identity

    first = liepin_site_payloads.cards_envelope(
        source_run_id="lane-1",
        query="SENTINEL_SHARED_QUERY",
        safe_run_id="lane-1",
        trace_identity=first_identity,
        pages_visited=1,
        events=({"action_kind": "first_observe"},),
        state_text="first safe state",
        cards=({"display_title": "first candidate"},),
        write_pi_artifact=write_pi_artifact,
    )
    second = liepin_site_payloads.cards_envelope(
        source_run_id="lane-1",
        query="SENTINEL_SHARED_QUERY",
        safe_run_id="lane-1",
        trace_identity=second_identity,
        pages_visited=1,
        events=({"action_kind": "second_observe"},),
        state_text="second safe state is longer",
        cards=({"display_title": "second candidate"},),
        write_pi_artifact=write_pi_artifact,
    )

    blocked = liepin_site_payloads.blocked_cards_envelope(
        source_run_id="lane-1",
        query="SENTINEL_SHARED_QUERY",
        safe_reason_code="liepin_opencli_malformed_state",
        safe_run_id="lane-1",
        trace_identity=blocked_identity,
        pages_visited=1,
        events=({"action_kind": "observe"},),
        write_pi_artifact=write_pi_artifact,
    )

    first_card = first["cards"][0]
    second_card = second["cards"][0]
    first_refs = {
        first["action_trace_ref"],
        first["protected_snapshot_refs"][0],
        first_card["provider_candidate_key_material_ref"],
        first_card["safe_card_summary_ref"],
        first_card["protected_snapshot_ref"],
    }
    second_refs = {
        second["action_trace_ref"],
        second["protected_snapshot_refs"][0],
        second_card["provider_candidate_key_material_ref"],
        second_card["safe_card_summary_ref"],
        second_card["protected_snapshot_ref"],
    }
    assert first_refs.isdisjoint(second_refs)
    assert blocked["action_trace_ref"] not in first_refs | second_refs

    first_summary_path = str(first_card["safe_card_summary_ref"]).removeprefix(
        "artifact://"
    )
    first_trace_path = str(first["action_trace_ref"]).removeprefix("artifact://")
    first_page_path = str(first["protected_snapshot_refs"][0]).removeprefix(
        "artifact://"
    )
    first_provider_path = str(
        first_card["provider_candidate_key_material_ref"]
    ).removeprefix("artifact://")
    first_card_path = str(first_card["protected_snapshot_ref"]).removeprefix(
        "artifact://"
    )
    second_provider_path = str(
        second_card["provider_candidate_key_material_ref"]
    ).removeprefix("artifact://")
    assert writes[("protected", first_trace_path)] == {
        "schema_version": "seektalent.opencli_action_trace.v1",
        "mode": "card",
        "source": "liepin",
        "status": "succeeded",
        "stop_reason": "completed",
        "events": ({"action_kind": "first_observe"},),
        "cards_seen": 1,
    }
    assert writes[("public-summary", first_summary_path)] == {
        "display_title": "first candidate"
    }
    assert writes[("protected", first_page_path)] == {
        "schema_version": "seektalent.opencli_state_snapshot.v1",
        "chars": len("first safe state"),
    }
    assert writes[("protected", first_card_path)] == {
        "schema_version": "seektalent.opencli_card_snapshot.v1",
        "rank": 1,
        "summary": {"display_title": "first candidate"},
    }
    assert writes[("protected", first_provider_path)] != writes[
        ("protected", second_provider_path)
    ]

    trace_paths = {
        path
        for visibility, path in writes
        if visibility == "protected" and path.endswith("/action-trace.json")
    }
    assert len(trace_paths) == 3
    all_paths = "\n".join(path for _visibility, path in writes)
    assert "SENTINEL_SHARED_QUERY" not in all_paths
    assert "苏州" not in all_paths
    assert "杭州" not in all_paths
    assert "广州" not in all_paths
    encoded_traces = json.dumps(
        [writes[("protected", path)] for path in sorted(trace_paths)],
        ensure_ascii=False,
    )
    assert "SENTINEL_SHARED_QUERY" not in encoded_traces


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
