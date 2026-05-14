from __future__ import annotations

from pathlib import Path

from seektalent.providers.liepin.pi_skills import (
    DIRECT_REQUEST_FORBIDDEN_ACTIONS,
    get_liepin_pi_skill,
    is_liepin_skill_url_allowed,
)
from seektalent.providers.pi_agent.boundary_patterns import (
    FORBIDDEN_PROVIDER_OPERATIONS,
    PYTHON_FORBIDDEN_IMPORTS,
    PYTHON_FORBIDDEN_OPERATION_MARKERS,
    TYPESCRIPT_FORBIDDEN_OPERATION_MARKERS,
    TYPESCRIPT_PROVIDER_ACTION_FORBIDDEN_OPERATION_MARKERS,
    TYPESCRIPT_SESSION_LIFECYCLE_ALLOWED_OPERATION_MARKERS,
)
from seektalent.providers.pi_agent.contracts import PiAgentTaskType
from tools.check_pi_agent_boundaries import (
    collect_python_boundary_scan_files,
    find_forbidden_python_boundary_patterns,
)


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
            "req.get('/api')\n"
            "ctx_req.post('/api')\n"
            "eval_fn('document.cookie')\n"
        ),
    }

    findings = find_forbidden_python_boundary_patterns(files)

    assert ("src/seektalent/providers/pi_agent/example.py", "page.request") in findings
    assert ("src/seektalent/providers/pi_agent/example.py", "page.context.request") in findings
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


def test_liepin_skill_url_matcher_rejects_api_ajax_graphql_download_and_export_routes() -> None:
    skill = get_liepin_pi_skill(PiAgentTaskType.LIEPIN_SEARCH_CARDS)

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
