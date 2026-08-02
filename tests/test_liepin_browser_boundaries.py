from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from seektalent.providers.liepin.browser_boundary_patterns import (
    FORBIDDEN_PROVIDER_OPERATIONS,
    PYTHON_FORBIDDEN_IMPORTS,
    TYPESCRIPT_FORBIDDEN_OPERATION_MARKERS,
    TYPESCRIPT_PROVIDER_ACTION_FORBIDDEN_OPERATION_MARKERS,
    TYPESCRIPT_SESSION_LIFECYCLE_ALLOWED_OPERATION_MARKERS,
)
from seektalent.opencli_browser.automation import OpenCliBrowserAutomation
from seektalent.opencli_browser.contracts import (
    OpenCliBrowserConfig,
    OpenCliBrowserError,
    OpenCliBrowserResult,
)
from seektalent.providers.liepin.liepin_opencli_policy import LIEPIN_RECRUITER_SEARCH_URL
from seektalent.providers.liepin.liepin_site_adapter import (
    LiepinOpenCliSiteConfig,
    LiepinSiteAdapter,
    _search_readiness_evidence,
)
from seektalent.providers.liepin.liepin_state_machine import LiepinStateSnapshot
from tools.check_liepin_browser_boundaries import (
    collect_python_boundary_scan_files,
    find_forbidden_python_boundary_patterns,
)

PRODUCT_DOKOBOT_BOUNDARY_PATHS = (
    Path("src/seektalent/runtime"),
    Path("src/seektalent_ui"),
    Path("src/seektalent/providers/liepin"),
    Path("src/seektalent/providers/registry.py"),
    Path("src/seektalent/cli.py"),
)
PRODUCT_DOKOBOT_FORBIDDEN_MARKERS = (
    "dokobot_client",
    "DokoBotClient",
    "DokoBotCapabilityProbe",
    "DokoBotActionSurface",
    "DokoBotActionTransportSession",
    "dokobot_action",
)
PRODUCT_DOKOBOT_RAW_COMMAND_PATTERNS = (
    re.compile(r"subprocess\.\w+\([^)]*[\"']dokobot[\"']"),
    re.compile(r"\[[\"']dokobot[\"']"),
)
PRODUCT_OPENCLI_RAW_COMMAND_PATTERNS = (
    re.compile(r"subprocess\.\w+\([^)]*[\"']opencli[\"']"),
    re.compile(r"Popen\([^)]*[\"']opencli[\"']"),
    re.compile(r"\[[\"']opencli[\"']"),
)


def find_direct_dokobot_boundary_violations(files: Mapping[Path, str]) -> list[str]:
    offenders: list[str] = []
    for path, text in files.items():
        for marker in PRODUCT_DOKOBOT_FORBIDDEN_MARKERS:
            if marker in text:
                offenders.append(f"{path} contains {marker}")
        for pattern in PRODUCT_DOKOBOT_RAW_COMMAND_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{path} directly executes dokobot")
    return offenders


def find_direct_opencli_boundary_violations(files: Mapping[Path, str]) -> list[str]:
    offenders: list[str] = []
    for path, text in files.items():
        for pattern in PRODUCT_OPENCLI_RAW_COMMAND_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{path} directly executes opencli")
    return offenders


def collect_dokobot_product_boundary_files(root: Path) -> dict[Path, str]:
    files: dict[Path, str] = {}
    for boundary_path in PRODUCT_DOKOBOT_BOUNDARY_PATHS:
        full_path = root / boundary_path
        paths = [full_path] if full_path.is_file() else sorted(full_path.rglob("*.py"))
        for path in paths:
            files[path.relative_to(root)] = path.read_text(encoding="utf-8")
    return files


def test_browser_boundary_reuses_canonical_forbidden_operations() -> None:
    assert "page.request" in FORBIDDEN_PROVIDER_OPERATIONS
    assert "route.fetch" in FORBIDDEN_PROVIDER_OPERATIONS
    assert "page.evaluate" in FORBIDDEN_PROVIDER_OPERATIONS
    assert "CDPSession" in FORBIDDEN_PROVIDER_OPERATIONS
    assert "requests" in PYTHON_FORBIDDEN_IMPORTS
    assert "evaluate_script" in TYPESCRIPT_FORBIDDEN_OPERATION_MARKERS
    assert "fetch" in TYPESCRIPT_PROVIDER_ACTION_FORBIDDEN_OPERATION_MARKERS
    assert "storageState" in TYPESCRIPT_PROVIDER_ACTION_FORBIDDEN_OPERATION_MARKERS
    assert "storageState" in TYPESCRIPT_SESSION_LIFECYCLE_ALLOWED_OPERATION_MARKERS


def test_liepin_browser_boundary_names_do_not_reference_pi_agent() -> None:
    registry_text = Path("src/seektalent/providers/liepin/browser_boundary_registry.json").read_text(
        encoding="utf-8"
    )
    patterns_text = Path("src/seektalent/providers/liepin/browser_boundary_patterns.py").read_text(
        encoding="utf-8"
    )
    tool_text = Path("tools/check_liepin_browser_boundaries.py").read_text(encoding="utf-8")

    assert "liepin-browser-boundary-registry-v1" in registry_text
    assert "pi-agent-boundary-registry-v1" not in registry_text
    assert "PI Agent boundary" not in patterns_text
    assert "Liepin browser boundary" in patterns_text
    assert "PI Agent provider boundary" not in tool_text
    assert "Liepin browser boundary" in tool_text


def test_python_ast_scan_finds_raw_http_client_imports() -> None:
    files = {
        "src/seektalent/providers/liepin/example.py": (
            "import requests\n"
            "import httpx\n"
            "from urllib import request\n"
        ),
    }

    findings = find_forbidden_python_boundary_patterns(files)

    assert ("src/seektalent/providers/liepin/example.py", "requests") in findings
    assert ("src/seektalent/providers/liepin/example.py", "httpx") in findings
    assert ("src/seektalent/providers/liepin/example.py", "urllib.request") in findings


def test_python_ast_scan_finds_playwright_request_and_network_interception() -> None:
    files = {
        "src/seektalent/providers/liepin/example.py": (
            "page.request.get('/api')\n"
            "context.request.get('/api')\n"
            "page.context.request.post('/api')\n"
            "playwright.request.new_context()\n"
            "page.route('**/api/**', handler)\n"
            "page.wait_for_response('**/api/**')\n"
            "page.on('request', handler)\n"
        ),
    }

    findings = find_forbidden_python_boundary_patterns(files)

    assert ("src/seektalent/providers/liepin/example.py", "page.request") in findings
    assert ("src/seektalent/providers/liepin/example.py", "context.request") in findings
    assert ("src/seektalent/providers/liepin/example.py", "page.context.request") in findings
    assert ("src/seektalent/providers/liepin/example.py", "playwright.request.new_context") in findings
    assert ("src/seektalent/providers/liepin/example.py", "page.route") in findings
    assert ("src/seektalent/providers/liepin/example.py", "page.wait_for_response") in findings
    assert ("src/seektalent/providers/liepin/example.py", "page.on(request)") in findings


def test_python_ast_scan_finds_playwright_api_request_context_imports() -> None:
    files = {
        "src/seektalent/providers/liepin/example.py": (
            "from playwright.async_api import APIRequestContext\n"
            "from playwright.sync_api import APIRequestContext as RequestContext\n"
        ),
    }

    findings = find_forbidden_python_boundary_patterns(files)

    assert ("src/seektalent/providers/liepin/example.py", "APIRequestContext") in findings


def test_python_ast_scan_finds_script_eval_cookie_storage_and_cdp() -> None:
    files = {
        "src/seektalent/providers/liepin/example.py": (
            "page.evaluate('fetch(\"/api/resume\")')\n"
            "page.evaluate_handle('document.cookie')\n"
            "page.add_init_script('localStorage.setItem(\"x\", \"y\")')\n"
            "context.add_cookies([])\n"
            "context.set_extra_http_headers({})\n"
            "context.storage_state(path='auth.json')\n"
            "context.new_cdp_session(page)\n"
        ),
    }

    findings = find_forbidden_python_boundary_patterns(files)

    assert ("src/seektalent/providers/liepin/example.py", "page.evaluate") in findings
    assert ("src/seektalent/providers/liepin/example.py", "page.evaluate_handle") in findings
    assert ("src/seektalent/providers/liepin/example.py", "page.add_init_script") in findings
    assert ("src/seektalent/providers/liepin/example.py", "context.add_cookies") in findings
    assert ("src/seektalent/providers/liepin/example.py", "context.set_extra_http_headers") in findings
    assert ("src/seektalent/providers/liepin/example.py", "context.storage_state") in findings
    assert ("src/seektalent/providers/liepin/example.py", "context.new_cdp_session") in findings


def test_python_ast_scan_finds_one_hop_forbidden_aliases() -> None:
    files = {
        "src/seektalent/providers/liepin/example.py": (
            "req = page.request\n"
            "ctx_req = page.context.request\n"
            "eval_fn = page.evaluate\n"
            "ctx = page.context\n"
            "req.get('/api')\n"
            "ctx_req.post('/api')\n"
            "ctx.request.post('/api')\n"
            "eval_fn('document.cookie')\n"
        ),
    }

    findings = find_forbidden_python_boundary_patterns(files)

    assert ("src/seektalent/providers/liepin/example.py", "page.request") in findings
    assert ("src/seektalent/providers/liepin/example.py", "page.context.request") in findings
    assert ("src/seektalent/providers/liepin/example.py", "page.evaluate") in findings


def test_python_ast_scan_expands_page_context_alias_before_matching() -> None:
    files = {
        "src/seektalent/providers/liepin/example.py": (
            "ctx = page.context\n"
            "ctx.request.post('/api')\n"
        ),
    }

    findings = find_forbidden_python_boundary_patterns(files)

    assert ("src/seektalent/providers/liepin/example.py", "page.context.request") in findings
    assert ("src/seektalent/providers/liepin/example.py", "page.context") not in findings


def test_python_ast_scan_finds_computed_forbidden_request_access() -> None:
    files = {
        "src/seektalent/providers/liepin/example.py": (
            "page['request'].get('/api')\n"
            "page[\"evaluate\"]('document.cookie')\n"
        ),
    }

    findings = find_forbidden_python_boundary_patterns(files)

    assert ("src/seektalent/providers/liepin/example.py", "page.request") in findings
    assert ("src/seektalent/providers/liepin/example.py", "page.evaluate") in findings


def test_python_ast_scan_ignores_comments_and_inert_strings() -> None:
    files = {
        "src/seektalent/providers/liepin/example.py": (
            "# page.request is only documented here\n"
            "note = 'page.request and route.fetch are inert text'\n"
            "await_safe_click = 'await page.get_by_text(\"Next\").click()'\n"
        ),
    }

    assert find_forbidden_python_boundary_patterns(files) == []


def test_python_boundary_scan_passes_current_source_roots() -> None:
    files = collect_python_boundary_scan_files(root=Path.cwd())

    assert find_forbidden_python_boundary_patterns(files) == []


def test_runtime_and_workbench_product_paths_do_not_touch_dokobot_directly() -> None:
    files = collect_dokobot_product_boundary_files(root=Path.cwd())

    assert find_direct_dokobot_boundary_violations(files) == []


def test_dokobot_product_boundary_scan_matches_plan_scope() -> None:
    assert Path("src/seektalent/runtime") in PRODUCT_DOKOBOT_BOUNDARY_PATHS
    assert Path("src/seektalent_ui") in PRODUCT_DOKOBOT_BOUNDARY_PATHS
    assert Path("src/seektalent/providers/liepin") in PRODUCT_DOKOBOT_BOUNDARY_PATHS
    assert Path("src/seektalent/providers/registry.py") in PRODUCT_DOKOBOT_BOUNDARY_PATHS
    assert Path("src/seektalent/cli.py") in PRODUCT_DOKOBOT_BOUNDARY_PATHS
    assert "DokoBotActionSurface" in PRODUCT_DOKOBOT_FORBIDDEN_MARKERS
    assert "DokoBotActionTransportSession" in PRODUCT_DOKOBOT_FORBIDDEN_MARKERS
    assert "dokobot_action" in PRODUCT_DOKOBOT_FORBIDDEN_MARKERS


def test_dokobot_product_boundary_scan_catches_runtime_violations() -> None:
    files = {
        Path("src/seektalent/runtime/example.py"): "DokoBotActionSurface()\n",
        Path("src/seektalent/providers/registry.py"): "subprocess.run(['dokobot'])\n",
    }

    findings = find_direct_dokobot_boundary_violations(files)

    assert "src/seektalent/runtime/example.py contains DokoBotActionSurface" in findings
    assert "src/seektalent/providers/registry.py directly executes dokobot" in findings


def test_runtime_and_workbench_product_paths_do_not_execute_opencli_directly() -> None:
    files = collect_dokobot_product_boundary_files(root=Path.cwd())

    assert find_direct_opencli_boundary_violations(files) == []


def test_opencli_product_boundary_scan_catches_direct_execution() -> None:
    files = {
        Path("src/seektalent/runtime/example.py"): "subprocess.run(['opencli', 'browser', 'status'])\n",
        Path("src/seektalent_ui/example.py"): "Popen(['opencli'])\n",
    }

    findings = find_direct_opencli_boundary_violations(files)

    assert "src/seektalent/runtime/example.py directly executes opencli" in findings
    assert "src/seektalent_ui/example.py directly executes opencli" in findings


def test_opencli_helper_does_not_expose_generic_browser_command_escape_hatch() -> None:
    text = "\n".join(
        [
            Path("src/seektalent/opencli_browser/automation.py").read_text(encoding="utf-8"),
            Path("src/seektalent/opencli_browser/runtime.py").read_text(encoding="utf-8"),
        ]
    )

    assert "def run_restricted_browser_command" not in text
    assert "eval" in text
    assert "network" in text
    assert "upload" in text


def test_opencli_extension_exposes_agent_driven_resume_detail_tools() -> None:
    text = Path("src/seektalent/providers/liepin/opencli_extensions/seektalent_opencli_browser.ts").read_text(
        encoding="utf-8"
    )
    legacy_resume_tool = "_".join(("seektalent", "opencli", "search", "liepin", "resumes"))

    assert legacy_resume_tool not in text
    assert "seektalent_opencli_open_liepin_detail" in text
    assert "seektalent_opencli_capture_liepin_detail_resume" in text
    assert "seektalent_opencli_finalize_liepin_resumes" in text
    assert "seektalent_opencli_eval" not in text
    assert "seektalent_opencli_cookies" not in text


def test_opencli_extension_exposes_only_restricted_tools() -> None:
    text = Path("src/seektalent/providers/liepin/opencli_extensions/seektalent_opencli_browser.ts").read_text(
        encoding="utf-8"
    )

    assert "seektalent_opencli_status" in text
    assert "seektalent_opencli_search_liepin_cards" not in text
    assert "seektalent_opencli_extract_structured_liepin_cards" in text
    assert "seektalent_opencli_extract_visible_liepin_cards" in text
    assert "seektalent_opencli_capabilities" in text
    assert "seektalent_opencli_state" in text
    assert "seektalent_opencli_open_liepin_tab" in text
    assert "seektalent_opencli_get_url" in text
    assert "seektalent_opencli_find" in text
    assert "seektalent_opencli_fill" in text
    assert "seektalent_opencli_click" in text
    assert "seektalent_opencli_scroll" in text
    assert "seektalent_opencli_wait_time" in text
    assert "browser eval" not in text
    assert "browser network" not in text
    assert "document.cookie" not in text
    assert "child.stderr.on" in text
    assert "MAX_OUTPUT_CHARS" in text
    assert "terminalReason" in text
    assert 'import type { ExtensionAPI } from "@earendil-works/pi-coding-agent"' in text
    assert ("type " + "ExtensionAPI = {") not in text
    assert "async execute(_toolCallId: string, params: ToolParams" in text
    assert "stateReady" in text
    assert "requires a fresh non-terminal state" in text
    assert "details: {}" in text
    assert "SEEKTALENT_LIEPIN_OPENCLI_TIMEOUT_SECONDS" in text
    assert 'action === "search_cards"' not in text


def test_opencli_extension_marks_card_extractors_as_fresh_state() -> None:
    text = Path("src/seektalent/providers/liepin/opencli_extensions/seektalent_opencli_browser.ts").read_text(
        encoding="utf-8"
    )

    assert 'action === "extract_visible_liepin_cards"' in text
    assert 'action === "extract_structured_liepin_cards"' in text
    extract_branch_start = text.index('action === "extract_visible_liepin_cards"')
    extract_branch = text[extract_branch_start : extract_branch_start + 500]
    assert "stateReady = parsed.ok === true" in extract_branch
    assert "terminalReason = null" in extract_branch


def test_opencli_python_helper_exposes_no_direct_cards_or_resume_search_action() -> None:
    site_text = Path("src/seektalent/providers/liepin/liepin_site_adapter.py").read_text(encoding="utf-8")
    cli_text = Path("src/seektalent/providers/liepin/opencli_browser_cli.py").read_text(encoding="utf-8")

    assert hasattr(LiepinSiteAdapter, "search_liepin_resumes")
    assert "def search_liepin_resumes(" in site_text
    assert "WtsCliLifecycleSupervisor.attach" in cli_text
    assert "daemon restart" not in cli_text
    assert 'action == "search_cards"' not in cli_text
    assert 'action == "search_resumes"' not in cli_text
    assert "runner.search_liepin_cards(" not in cli_text
    assert "runner.search_liepin_resumes(" not in cli_text


def test_search_readiness_evidence_exposes_only_safe_surface_state() -> None:
    evidence = _search_readiness_evidence(
        LiepinStateSnapshot(
            ok=True,
            url="https://h.liepin.com/search/getConditionItem",
            text="[11]<input type=search role=combobox id=rc_select_0 />",
        )
    )

    assert evidence == {
        "state_ok": True,
        "url_host": "h.liepin.com",
        "url_path": "/search/getConditionItem",
        "search_surface_url": True,
        "search_input_ref_present": False,
        "search_button_ref_present": False,
        "terminal_reason": None,
    }


def test_liepin_opencli_policy_rejects_api_ajax_graphql_download_and_export_routes() -> None:
    browser_config = OpenCliBrowserConfig(
        session="seektalent-test",
        timeout_seconds=10,
        pacing_enabled=False,
    )
    runner = LiepinSiteAdapter(
        browser_config=browser_config,
        site_config=LiepinOpenCliSiteConfig(
            allowed_hosts=("www.liepin.com", "h.liepin.com", "c.liepin.com", "lpt.liepin.com"),
            allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
        ),
        automation=OpenCliBrowserAutomation(config=browser_config),
    )

    runner._validate_tab_new_url(LIEPIN_RECRUITER_SEARCH_URL)
    runner._validate_tab_new_url("https://www.liepin.com/resume/showresumedetail/?res_id=resume-1")

    for blocked_url in (
        "https://www.liepin.com/api/search",
        "https://www.liepin.com/ajax/search",
        "https://www.liepin.com/graphql",
        "https://www.liepin.com/resume/download",
        "https://www.liepin.com/export/candidates",
        "https://api-c.liepin.com/zhaopin/",
        "https://www.liepin.com/zhaopin/?next=/api/search",
        "https://www.liepin.com/zhaopin/?next=%2Fapi%2Fsearch",
        "https://www.liepin.com/API/search",
        "https://www.liepin.com/zhaopin/?redirect=https%3A%2F%2Fapi-c.liepin.com%2Fresume",
    ):
        assert _opencli_tab_url_is_blocked(runner, blocked_url), f"{blocked_url} should be blocked"


def test_stale_filter_ref_is_not_reissued_inside_one_browser_effect() -> None:
    class StaleRefAutomation:
        def __init__(self) -> None:
            self.click_calls = 0
            self.state_calls = 0

        def click_ref(self, _ref: str) -> str:
            self.click_calls += 1
            raise OpenCliBrowserError("liepin_opencli_stale_ref")

        def run_browser_command(self, _command: str, _args: tuple[str, ...]) -> str:
            self.state_calls += 1
            return "https://h.liepin.com/search/getConditionItem"

    browser_config = OpenCliBrowserConfig(
        session="seektalent-stale-ref",
        timeout_seconds=10,
        pacing_enabled=False,
    )
    automation = StaleRefAutomation()
    runner = LiepinSiteAdapter(
        browser_config=browser_config,
        site_config=LiepinOpenCliSiteConfig(
            allowed_hosts=("h.liepin.com",),
            allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
        ),
        automation=automation,  # type: ignore[arg-type]
    )

    with pytest.raises(OpenCliBrowserError) as caught:
        runner._click_native_filter_ref("70")

    assert caught.value.safe_reason_code == "liepin_opencli_stale_ref"
    assert automation.click_calls == 1
    assert automation.state_calls == 0


@pytest.mark.parametrize("effect", ("fill", "search_click"))
def test_search_effect_stale_ref_is_not_reissued(
    effect: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    search_url = LIEPIN_RECRUITER_SEARCH_URL
    search_state = OpenCliBrowserResult(
        ok=True,
        action="state",
        private_output=(
            f"URL: {search_url}\n"
            "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
            "[29]<button><span>搜 索</span></button>"
        ),
    )

    class SearchAutomation:
        def wait_for_page_url(self, **_kwargs: object) -> OpenCliBrowserResult:
            return OpenCliBrowserResult(ok=True, action="wait", private_output=search_url)

    runner = LiepinSiteAdapter(
        browser_config=OpenCliBrowserConfig(
            session="seektalent-search-stale-ref",
            timeout_seconds=10,
            pacing_enabled=False,
        ),
        site_config=LiepinOpenCliSiteConfig(
            allowed_hosts=("h.liepin.com",),
            allowed_start_urls=(search_url,),
            artifact_root=tmp_path,
        ),
        automation=SearchAutomation(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(runner, "open_liepin_tab", lambda _url: search_state)
    monkeypatch.setattr(runner, "state", lambda: search_state)
    monkeypatch.setattr(runner, "_current_url_or_none", lambda: search_url)
    monkeypatch.setattr(
        runner,
        "_clear_liepin_native_filters_if_needed",
        lambda **_kwargs: search_state,
    )

    effect_calls = 0

    def fail_effect(*_args: object, **_kwargs: object) -> None:
        nonlocal effect_calls
        effect_calls += 1
        raise OpenCliBrowserError("liepin_opencli_stale_ref")

    if effect == "fill":
        monkeypatch.setattr(runner, "fill", fail_effect)
    else:
        monkeypatch.setattr(runner, "fill", lambda **_kwargs: None)
        monkeypatch.setattr(runner, "_liepin_search_query_value_from_dom", lambda **_kwargs: "AI Agent")
        monkeypatch.setattr(runner, "_click_liepin_search_button", fail_effect)

    envelope = runner._search_liepin_cards_once(
        source_run_id=f"stale-{effect}",
        query="AI Agent",
        max_pages=1,
        max_cards=1,
        native_filters=None,
        recovering_search_surface=False,
    )

    assert envelope["safe_reason_code"] == "liepin_opencli_stale_ref"
    assert effect_calls == 1


@pytest.mark.parametrize(
    "reason",
    (
        "liepin_opencli_filter_unapplied",
        "liepin_opencli_stale_ref",
        "liepin_opencli_selector_not_found",
        "liepin_opencli_status_unavailable",
        "liepin_opencli_timeout",
    ),
)
def test_native_filter_effect_failure_does_not_repeat_selection(
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = LiepinSiteAdapter(
        browser_config=OpenCliBrowserConfig(
            session="seektalent-filter-stale-ref",
            timeout_seconds=10,
            pacing_enabled=False,
        ),
        site_config=LiepinOpenCliSiteConfig(
            allowed_hosts=("h.liepin.com",),
            allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
        ),
        automation=object(),  # type: ignore[arg-type]
    )
    state = OpenCliBrowserResult(ok=True, action="state", private_output="filter-ready")
    monkeypatch.setattr(
        "seektalent.providers.liepin.liepin_site_adapter.native_filter_selection_applied",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "seektalent.providers.liepin.liepin_site_adapter.native_filter_option_visible_in_section",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(runner, "state", lambda: state)
    selection_calls = 0

    def fail_selection(*_args: object, **_kwargs: object) -> None:
        nonlocal selection_calls
        selection_calls += 1
        raise OpenCliBrowserError(reason)

    monkeypatch.setattr(runner, "_click_native_filter_option", fail_selection)

    with pytest.raises(OpenCliBrowserError) as caught:
        runner._select_liepin_native_filter(
            filter_name="experience",
            section="experience",
            label="5-10年",
            current_state=state,
            events=[],
        )

    assert caught.value.safe_reason_code == reason
    assert selection_calls == 1


def test_city_filter_never_uses_page_text_when_focused_probe_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = LiepinSiteAdapter(
        browser_config=OpenCliBrowserConfig(
            session="seektalent-city-probe-unavailable",
            timeout_seconds=10,
            pacing_enabled=False,
        ),
        site_config=LiepinOpenCliSiteConfig(
            allowed_hosts=("h.liepin.com",),
            allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
        ),
        automation=object(),  # type: ignore[arg-type]
    )
    state = OpenCliBrowserResult(ok=True, action="state", private_output="page text claims 上海 is visible")
    monkeypatch.setattr(
        "seektalent.providers.liepin.liepin_site_adapter.native_filter_selection_applied",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "seektalent.providers.liepin.liepin_site_adapter.native_filter_option_visible_in_section",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(runner, "state", lambda: state)

    def unavailable_probe(**_kwargs: object) -> str:
        raise OpenCliBrowserError("liepin_opencli_status_unavailable")

    monkeypatch.setattr(runner, "_liepin_city_picker_control_ref", unavailable_probe)
    page_text_clicks = 0

    def click_page_text(*_args: object, **_kwargs: object) -> None:
        nonlocal page_text_clicks
        page_text_clicks += 1

    monkeypatch.setattr(runner, "_click_native_filter_option", click_page_text)

    with pytest.raises(OpenCliBrowserError, match="liepin_opencli_status_unavailable"):
        runner._select_liepin_native_filter(
            filter_name="city",
            section="expected",
            label="上海",
            current_state=state,
            events=[],
        )

    assert page_text_clicks == 0


def test_detail_url_unavailable_never_falls_back_to_clicking_card_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = LiepinSiteAdapter(
        browser_config=OpenCliBrowserConfig(
            session="seektalent-detail-url-unavailable",
            timeout_seconds=10,
            pacing_enabled=False,
        ),
        site_config=LiepinOpenCliSiteConfig(
            allowed_hosts=("h.liepin.com",),
            allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
        ),
        automation=object(),  # type: ignore[arg-type]
    )
    state = OpenCliBrowserResult(ok=True, action="state", private_output="detail card visible")
    monkeypatch.setattr(runner, "_detail_ref_open_state", lambda **_kwargs: None)
    monkeypatch.setattr(runner, "_state_with_liepin_detail_ref", lambda _ref: state)
    monkeypatch.setattr(runner, "_open_liepin_detail_ref_controlled", lambda *_args, **_kwargs: False)
    result = runner._open_liepin_detail(
        source_run_id="detail-url-unavailable",
        ref="70",
        rank=1,
        emit_events=False,
    )

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_detail_not_opened"
    adapter_source = (
        Path(__file__).resolve().parents[1]
        / "src/seektalent/providers/liepin/liepin_site_adapter.py"
    ).read_text(encoding="utf-8")
    assert "_click_liepin_detail_ref" not in adapter_source


def _opencli_tab_url_is_blocked(runner: LiepinSiteAdapter, url: str) -> bool:
    try:
        runner._validate_tab_new_url(url)
    except OpenCliBrowserError:
        return True
    return False
