from __future__ import annotations

from tools.check_agent_safety_gate import AddedLine, check_added_lines


def _line(path: str, text: str, line_number: int = 1) -> AddedLine:
    return AddedLine(path=path, line_number=line_number, text=text)


def test_blocks_broad_action_payload_tool_entrypoint() -> None:
    findings = check_added_lines(
        [
            _line(
                "src/seektalent_conversation_agent/tools.py",
                "def run_action(action: str, payload: dict[str, object]) -> object:",
            )
        ]
    )

    assert [finding.rule_id for finding in findings] == ["broad-agent-tool-entrypoint"]


def test_blocks_multiline_and_reversed_broad_action_payload_tool_entrypoints() -> None:
    findings = check_added_lines(
        [
            _line("src/seektalent_conversation_agent/tools.py", "def run_action(", 10),
            _line("src/seektalent_conversation_agent/tools.py", "    action: str,", 11),
            _line("src/seektalent_conversation_agent/tools.py", "    payload: dict[str, object],", 12),
            _line("src/seektalent_conversation_agent/tools.py", ") -> object:", 13),
            _line(
                "src/seektalent_conversation_agent/tools.py",
                "def execute_tool(payload: Mapping[str, object], action: str) -> object:",
                20,
            ),
        ]
    )

    assert [finding.rule_id for finding in findings] == [
        "broad-agent-tool-entrypoint",
        "broad-agent-tool-entrypoint",
    ]
    assert [finding.line_number for finding in findings] == [10, 20]


def test_blocks_direct_forbidden_agent_imports() -> None:
    findings = check_added_lines(
        [
            _line("src/seektalent_conversation_agent/service.py", "from seektalent.providers import registry"),
            _line("src/seektalent_conversation_agent/service.py", "from seektalent.runtime import orchestrator"),
            _line(
                "src/seektalent_conversation_agent/service.py",
                "from seektalent.runtime.orchestrator import WorkflowRuntime",
            ),
            _line(
                "src/seektalent_conversation_agent/service.py",
                "from seektalent.source_adapters.liepin.adapter import build_liepin_source",
            ),
            _line(
                "src/seektalent_conversation_agent/service.py",
                "from seektalent_ui.workbench_store import WorkbenchStore",
            ),
            _line(
                "src/seektalent_conversation_agent/service.py",
                "from seektalent_ui.runtime_bridge import RuntimeBridge",
            ),
            _line(
                "src/seektalent_conversation_agent/service.py",
                "from seektalent_ui.runtime_graph import RuntimeGraphBuilder",
            ),
            _line(
                "src/seektalent_conversation_agent/service.py",
                "from seektalent.opencli_browser.automation import OpenCliBrowserAutomation",
            ),
            _line("src/seektalent_conversation_agent/service.py", "from playwright.async_api import Page"),
            _line("src/seektalent_conversation_agent/service.py", "from selenium.webdriver import Chrome"),
        ]
    )

    assert [finding.rule_id for finding in findings] == [
        "conversation-agent-provider-import",
        "conversation-agent-runtime-import",
        "conversation-agent-runtime-import",
        "conversation-agent-source-adapter-import",
        "conversation-agent-workbench-internal-import",
        "conversation-agent-workbench-internal-import",
        "conversation-agent-workbench-internal-import",
        "conversation-agent-browser-automation-import",
        "conversation-agent-browser-automation-import",
        "conversation-agent-browser-automation-import",
    ]


def test_blocks_alias_style_forbidden_agent_imports() -> None:
    findings = check_added_lines(
        [
            _line("src/seektalent_conversation_agent/service.py", "from seektalent import providers"),
            _line("src/seektalent_conversation_agent/service.py", "from seektalent import runtime"),
            _line("src/seektalent_conversation_agent/service.py", "from seektalent import source_adapters"),
            _line("src/seektalent_conversation_agent/service.py", "from seektalent_ui import workbench_store"),
            _line("src/seektalent_conversation_agent/service.py", "from seektalent_ui import runtime_bridge"),
            _line("src/seektalent_conversation_agent/service.py", "from seektalent_ui import runtime_graph"),
        ]
    )

    assert [finding.rule_id for finding in findings] == [
        "conversation-agent-provider-import",
        "conversation-agent-runtime-import",
        "conversation-agent-source-adapter-import",
        "conversation-agent-workbench-internal-import",
        "conversation-agent-workbench-internal-import",
        "conversation-agent-workbench-internal-import",
    ]


def test_blocks_shell_execution_in_conversation_agent() -> None:
    findings = check_added_lines(
        [
            _line("src/seektalent_conversation_agent/runtime.py", "import subprocess"),
            _line("src/seektalent_conversation_agent/runtime.py", "from subprocess import run"),
            _line("src/seektalent_conversation_agent/runtime.py", "subprocess.run(['opencli'], check=True)"),
        ]
    )

    assert [finding.rule_id for finding in findings] == [
        "conversation-agent-shell-execution",
        "conversation-agent-shell-execution",
        "conversation-agent-shell-execution",
    ]


def test_blocks_sensitive_trace_and_session_payloads() -> None:
    findings = check_added_lines(
        [
            _line("src/seektalent_conversation_agent/runtime.py", "trace_payload['raw_resume'] = text"),
            _line("src/seektalent_conversation_agent/store.py", "session_state['provider_payload'] = payload"),
        ]
    )

    assert [finding.rule_id for finding in findings] == [
        "sensitive-agent-trace",
        "sensitive-agent-session",
    ]


def test_blocks_sensitive_frontend_storage() -> None:
    findings = check_added_lines(
        [
            _line(
                "apps/web-svelte/src/routes/(app)/sessions/+page.svelte",
                "localStorage.setItem('provider_payload', JSON.stringify(payload));",
            )
        ]
    )

    assert [finding.rule_id for finding in findings] == ["sensitive-browser-storage"]


def test_frontend_sensitive_storage_does_not_emit_agent_session_finding() -> None:
    findings = check_added_lines(
        [
            _line(
                "apps/web-svelte/src/routes/(app)/sessions/+page.svelte",
                "sessionStorage.setItem('token', token);",
            )
        ]
    )

    assert [finding.rule_id for finding in findings] == ["sensitive-browser-storage"]


def test_allows_narrow_runtime_control_tool_names() -> None:
    findings = check_added_lines(
        [
            _line(
                "src/seektalent_conversation_agent/tools.py",
                "def start_workflow(input: StartWorkflowInput) -> StartWorkflowResult:",
            ),
            _line(
                "src/seektalent_conversation_agent/runtime.py",
                "from seektalent_runtime_control.service import RuntimeControlService",
            ),
        ]
    )

    assert findings == []


def test_ignores_tests_and_docs() -> None:
    findings = check_added_lines(
        [
            _line(
                "tests/test_conversation_agent_tools.py",
                "def run_action(action: str, payload: dict[str, object]):",
            ),
            _line(
                "docs/governance/goal-2-agent-safety-gate.md",
                "def run_action(action: str, payload: dict[str, object]):",
            ),
        ]
    )

    assert findings == []
