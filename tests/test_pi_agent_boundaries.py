from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from seektalent.providers.liepin.pi_skills import (
    DIRECT_REQUEST_FORBIDDEN_ACTIONS,
    get_liepin_pi_skill,
    is_liepin_skill_url_allowed,
)
from seektalent.providers.pi_agent.boundary_patterns import (
    FORBIDDEN_PROVIDER_OPERATIONS,
    PYTHON_FORBIDDEN_IMPORTS,
    TYPESCRIPT_FORBIDDEN_OPERATION_MARKERS,
    TYPESCRIPT_PROVIDER_ACTION_FORBIDDEN_OPERATION_MARKERS,
    TYPESCRIPT_SESSION_LIFECYCLE_ALLOWED_OPERATION_MARKERS,
)
from seektalent.providers.pi_agent.contracts import PiAgentTaskType
from tools.check_pi_agent_boundaries import (
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
    "from seektalent.providers.pi_agent.dokobot_client",
    "import seektalent.providers.pi_agent.dokobot_client",
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


def test_liepin_skill_recipe_reuses_canonical_forbidden_operations() -> None:
    assert DIRECT_REQUEST_FORBIDDEN_ACTIONS == FORBIDDEN_PROVIDER_OPERATIONS
    assert "page.request" in FORBIDDEN_PROVIDER_OPERATIONS
    assert "route.fetch" in FORBIDDEN_PROVIDER_OPERATIONS
    assert "page.evaluate" in FORBIDDEN_PROVIDER_OPERATIONS
    assert "CDPSession" in FORBIDDEN_PROVIDER_OPERATIONS
    assert "requests" in PYTHON_FORBIDDEN_IMPORTS
    assert "evaluate_script" in TYPESCRIPT_FORBIDDEN_OPERATION_MARKERS
    assert "fetch" in TYPESCRIPT_PROVIDER_ACTION_FORBIDDEN_OPERATION_MARKERS
    assert "storageState" in TYPESCRIPT_PROVIDER_ACTION_FORBIDDEN_OPERATION_MARKERS
    assert "storageState" in TYPESCRIPT_SESSION_LIFECYCLE_ALLOWED_OPERATION_MARKERS


def test_python_ast_scan_finds_raw_http_client_imports() -> None:
    files = {
        "src/seektalent/providers/pi_agent/example.py": (
            "import requests\n"
            "import httpx\n"
            "from urllib import request\n"
        ),
    }

    findings = find_forbidden_python_boundary_patterns(files)

    assert ("src/seektalent/providers/pi_agent/example.py", "requests") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "httpx") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "urllib.request") in findings


def test_python_ast_scan_finds_playwright_request_and_network_interception() -> None:
    files = {
        "src/seektalent/providers/pi_agent/example.py": (
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

    assert ("src/seektalent/providers/pi_agent/example.py", "page.request") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "context.request") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "page.context.request") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "playwright.request.new_context") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "page.route") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "page.wait_for_response") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "page.on(request)") in findings


def test_python_ast_scan_finds_playwright_api_request_context_imports() -> None:
    files = {
        "src/seektalent/providers/pi_agent/example.py": (
            "from playwright.async_api import APIRequestContext\n"
            "from playwright.sync_api import APIRequestContext as RequestContext\n"
        ),
    }

    findings = find_forbidden_python_boundary_patterns(files)

    assert ("src/seektalent/providers/pi_agent/example.py", "APIRequestContext") in findings


def test_python_ast_scan_finds_script_eval_cookie_storage_and_cdp() -> None:
    files = {
        "src/seektalent/providers/pi_agent/example.py": (
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

    assert ("src/seektalent/providers/pi_agent/example.py", "page.evaluate") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "page.evaluate_handle") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "page.add_init_script") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "context.add_cookies") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "context.set_extra_http_headers") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "context.storage_state") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "context.new_cdp_session") in findings


def test_python_ast_scan_finds_one_hop_forbidden_aliases() -> None:
    files = {
        "src/seektalent/providers/pi_agent/example.py": (
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

    assert ("src/seektalent/providers/pi_agent/example.py", "page.request") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "page.context.request") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "page.evaluate") in findings


def test_python_ast_scan_expands_page_context_alias_before_matching() -> None:
    files = {
        "src/seektalent/providers/pi_agent/example.py": (
            "ctx = page.context\n"
            "ctx.request.post('/api')\n"
        ),
    }

    findings = find_forbidden_python_boundary_patterns(files)

    assert ("src/seektalent/providers/pi_agent/example.py", "page.context.request") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "page.context") not in findings


def test_python_ast_scan_finds_computed_forbidden_request_access() -> None:
    files = {
        "src/seektalent/providers/pi_agent/example.py": (
            "page['request'].get('/api')\n"
            "page[\"evaluate\"]('document.cookie')\n"
        ),
    }

    findings = find_forbidden_python_boundary_patterns(files)

    assert ("src/seektalent/providers/pi_agent/example.py", "page.request") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "page.evaluate") in findings


def test_python_ast_scan_ignores_comments_and_inert_strings() -> None:
    files = {
        "src/seektalent/providers/pi_agent/example.py": (
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
    text = Path("src/seektalent/providers/pi_agent/opencli_browser.py").read_text(encoding="utf-8")

    assert "def run_restricted_browser_command" not in text
    assert "eval" in text
    assert "network" in text
    assert "upload" in text


def test_opencli_extension_exposes_agent_driven_resume_detail_tools() -> None:
    text = Path("src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts").read_text(
        encoding="utf-8"
    )
    legacy_resume_tool = "_".join(("seektalent", "opencli", "search", "liepin", "resumes"))

    assert legacy_resume_tool not in text
    assert "seektalent_opencli_open_liepin_detail" in text
    assert "seektalent_opencli_capture_liepin_detail_resume" in text
    assert "seektalent_opencli_finalize_liepin_resumes" in text
    assert "seektalent_opencli_eval" not in text
    assert "seektalent_opencli_cookies" not in text


def test_opencli_python_helper_does_not_expose_legacy_resume_search_tool() -> None:
    legacy_action = "search_resumes"
    browser_text = Path("src/seektalent/providers/pi_agent/opencli_browser.py").read_text(encoding="utf-8")
    cli_text = Path("src/seektalent/providers/pi_agent/opencli_browser_cli.py").read_text(encoding="utf-8")

    assert "def search_liepin_resumes(" not in browser_text
    assert f'action == "{legacy_action}"' not in cli_text


def test_liepin_skill_url_matcher_rejects_api_ajax_graphql_download_and_export_routes() -> None:
    skill = get_liepin_pi_skill(PiAgentTaskType.LIEPIN_SEARCH_CARDS)

    assert is_liepin_skill_url_allowed(skill, "https://h.liepin.com/search/getConditionItem#session")
    assert is_liepin_skill_url_allowed(skill, "https://www.liepin.com/zhaopin/?key=python")
    assert is_liepin_skill_url_allowed(skill, "https://www.liepin.com/lptjob/")
    assert not is_liepin_skill_url_allowed(skill, "https://www.liepin.com/api/search")
    assert not is_liepin_skill_url_allowed(skill, "https://www.liepin.com/ajax/search")
    assert not is_liepin_skill_url_allowed(skill, "https://www.liepin.com/graphql")
    assert not is_liepin_skill_url_allowed(skill, "https://www.liepin.com/resume/download")
    assert not is_liepin_skill_url_allowed(skill, "https://www.liepin.com/export/candidates")
    assert not is_liepin_skill_url_allowed(skill, "https://api-c.liepin.com/zhaopin/")
    assert not is_liepin_skill_url_allowed(skill, "https://www.liepin.com/zhaopin/?next=/api/search")
    assert not is_liepin_skill_url_allowed(skill, "https://www.liepin.com/zhaopin/?next=%2Fapi%2Fsearch")
    assert not is_liepin_skill_url_allowed(skill, "https://www.liepin.com/API/search")
    assert not is_liepin_skill_url_allowed(
        skill,
        "https://www.liepin.com/zhaopin/?redirect=https%3A%2F%2Fapi-c.liepin.com%2Fresume",
    )


def test_liepin_search_cards_task_accepts_safe_native_filters() -> None:
    from seektalent.providers.pi_agent.contracts import LiepinSearchCardsTask

    task = LiepinSearchCardsTask.model_validate(
        {
            "schema_version": "pi-agent-task-v1",
            "task_type": PiAgentTaskType.LIEPIN_SEARCH_CARDS,
            "session_id": "session-1",
            "source_run_id": "source-1",
            "connection_id": "conn-1",
            "artifact_policy": "protected_snapshots_only",
            "query_terms": ["数据开发专家"],
            "keyword_query": "数据开发专家",
            "max_pages": 1,
            "max_cards": 10,
            "stop_conditions": ["page_exhausted"],
            "native_filters": {
                "city": {"section": "expected", "label": "上海"},
                "experience": {"section": "experience", "label": "3-5年"},
                "age": {"section": "age", "label": "35岁以下"},
                "degree": {"section": "education", "label": "本科"},
                "recruitmentType": {"section": "recruitment_type", "label": "统招本科"},
                "schoolTypes": [
                    {"section": "school_type", "label": "211"},
                    {"section": "school_type", "label": "985"},
                ],
                "partialReasonCodes": ["source_filter_partial"],
            },
        }
    )

    assert task.native_filters is not None
    assert task.native_filters.city is not None
    assert task.native_filters.city.section == "expected"
    assert task.native_filters.city.label == "上海"
    assert task.native_filters.experience is not None
    assert task.native_filters.experience.label == "3-5年"
    assert task.native_filters.age is not None
    assert task.native_filters.age.label == "35岁以下"
    assert task.native_filters.degree is not None
    assert task.native_filters.degree.label == "本科"
    assert task.native_filters.recruitment_type is not None
    assert task.native_filters.recruitment_type.label == "统招本科"
    assert [item.label for item in task.native_filters.school_types] == ["211", "985"]


def test_liepin_search_cards_prompt_forwards_native_filters() -> None:
    from seektalent.providers.pi_agent.pi_external import _task_contract_for_prompt

    instruction = _task_contract_for_prompt('{"task":"liepin.search_cards"}')

    assert "nativeFilters" in instruction
    assert "when present" in instruction
