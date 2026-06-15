from __future__ import annotations

import argparse
import ast
import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path


CODE_EXTENSIONS = {
    ".js",
    ".jsx",
    ".mjs",
    ".py",
    ".ts",
    ".tsx",
}

SCAN_PREFIXES = (
    "apps/web-react/src/",
    "src/seektalent_conversation_agent/",
)

IGNORED_PREFIXES = (
    "artifacts/",
    "docs/",
    "tests/",
)

GENERATED_PREFIXES = (
    "src/seektalent_ui/resources/workbench/",
)

TEST_FILE_SUFFIXES = (
    ".test.ts",
    ".test.tsx",
    ".spec.ts",
    ".spec.tsx",
)

BROAD_TOOL_RE = re.compile(
    r"\b(?:def|async\s+def)\s+\w*(?:action|tool|execute|run)\w*\s*"
    r"\([^)]*\baction\s*:\s*str\b[^)]*\bpayload\s*:\s*(?:dict|Mapping)\b",
    re.IGNORECASE,
)
SHELL_EXECUTION_RE = re.compile(
    r"\b(?:import\s+subprocess|from\s+subprocess\s+import|"
    r"subprocess\.(?:run|Popen|call|check_call|check_output)|os\.system|"
    r"asyncio\.create_subprocess_exec|asyncio\.create_subprocess_shell)\b"
)
TRACE_RE = re.compile(r"\btrace\w*\b", re.IGNORECASE)
SESSION_RE = re.compile(r"\bsession\w*\b", re.IGNORECASE)
SENSITIVE_RE = re.compile(
    r"\b(?:authorization|auth_headers|cookie|provider_payload|provider_response|raw_payload|"
    r"raw_provider_payload|raw_provider_response|raw_resume|storage_state|storageState|token)\b",
    re.IGNORECASE,
)
BROWSER_STORAGE_RE = re.compile(r"\b(?:localStorage|sessionStorage)\s*\.\s*setItem\s*\(", re.IGNORECASE)
BROAD_TOOL_NAME_RE = re.compile(r"(?:action|tool|execute|run)", re.IGNORECASE)
BROAD_TOOL_ACTION_ARG_RE = re.compile(r"(?:^|[,(]\s*)action\s*:\s*str\b", re.IGNORECASE)
BROAD_TOOL_PAYLOAD_ARG_RE = re.compile(
    r"(?:^|[,(]\s*)payload\s*:\s*(?:dict|Mapping)\b",
    re.IGNORECASE,
)

FORBIDDEN_AGENT_IMPORTS = (
    (
        "seektalent.providers",
        "conversation-agent-provider-import",
        "Conversation-agent code must call runtime-control APIs instead of importing providers.",
    ),
    (
        "seektalent.runtime",
        "conversation-agent-runtime-import",
        "Conversation-agent code must not import seektalent.runtime directly.",
    ),
    (
        "seektalent.source_adapters",
        "conversation-agent-source-adapter-import",
        "Conversation-agent code must not import source adapters directly.",
    ),
    (
        "seektalent_ui.workbench_store",
        "conversation-agent-workbench-internal-import",
        "Conversation-agent code must not import Workbench persistence or projection internals.",
    ),
    (
        "seektalent_ui.runtime_bridge",
        "conversation-agent-workbench-internal-import",
        "Conversation-agent code must not import Workbench persistence or projection internals.",
    ),
    (
        "seektalent_ui.runtime_graph",
        "conversation-agent-workbench-internal-import",
        "Conversation-agent code must not import Workbench persistence or projection internals.",
    ),
    (
        "seektalent.opencli_browser",
        "conversation-agent-browser-automation-import",
        "Conversation-agent code must not import browser automation directly.",
    ),
    (
        "playwright",
        "conversation-agent-browser-automation-import",
        "Conversation-agent code must not import browser automation directly.",
    ),
    (
        "selenium",
        "conversation-agent-browser-automation-import",
        "Conversation-agent code must not import browser automation directly.",
    ),
)


@dataclass(frozen=True)
class AddedLine:
    path: str
    line_number: int
    text: str


@dataclass(frozen=True)
class AgentSafetyFinding:
    rule_id: str
    path: str
    line_number: int
    message: str
    text: str


def should_scan_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized.endswith(".d.ts"):
        return False
    if normalized.startswith(IGNORED_PREFIXES) or normalized.startswith(GENERATED_PREFIXES):
        return False
    if "/tests/" in normalized or normalized.endswith(TEST_FILE_SUFFIXES):
        return False
    return normalized.startswith(SCAN_PREFIXES) and Path(normalized).suffix in CODE_EXTENSIONS


def parse_added_lines(diff_text: str) -> list[AddedLine]:
    added_lines: list[AddedLine] = []
    current_path: str | None = None
    current_line: int | None = None
    hunk_re = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("+++ b/"):
            current_path = raw_line.removeprefix("+++ b/")
            current_line = None
            continue
        if raw_line.startswith("+++ /dev/null"):
            current_path = None
            current_line = None
            continue
        if raw_line.startswith("@@"):
            match = hunk_re.search(raw_line)
            current_line = int(match.group(1)) if match else None
            continue
        if current_path is None or current_line is None:
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            added_lines.append(AddedLine(current_path, current_line, raw_line[1:]))
            current_line += 1
        elif raw_line.startswith("-"):
            continue
        else:
            current_line += 1

    return added_lines


def check_added_lines(lines: Iterable[AddedLine]) -> list[AgentSafetyFinding]:
    scanned_lines: list[AddedLine] = []
    findings: list[AgentSafetyFinding] = []
    for line in lines:
        if not should_scan_path(line.path):
            continue
        scanned_lines.append(line)
        findings.extend(_find_line_issues(line))
    findings.extend(_find_group_issues(scanned_lines))
    return _dedupe_findings(findings)


def _find_line_issues(line: AddedLine) -> list[AgentSafetyFinding]:
    findings: list[AgentSafetyFinding] = []
    text = line.text
    is_agent_path = line.path.replace("\\", "/").startswith("src/seektalent_conversation_agent/")

    if is_agent_path and BROAD_TOOL_RE.search(text):
        findings.append(
            AgentSafetyFinding(
                "broad-agent-tool-entrypoint",
                line.path,
                line.line_number,
                "Use narrow typed Agent tools instead of action-plus-payload dispatch.",
                text,
            )
        )
    if is_agent_path:
        findings.extend(_find_import_issues(line))
    if is_agent_path and SHELL_EXECUTION_RE.search(text):
        findings.append(
            AgentSafetyFinding(
                "conversation-agent-shell-execution",
                line.path,
                line.line_number,
                "Conversation-agent code must not execute shell or subprocess commands.",
                text,
            )
        )
    if is_agent_path and TRACE_RE.search(text) and SENSITIVE_RE.search(text):
        findings.append(
            AgentSafetyFinding(
                "sensitive-agent-trace",
                line.path,
                line.line_number,
                "Trace payloads must not include raw resumes, provider payloads, cookies, auth headers, "
                "storage state, or tokens.",
                text,
            )
        )
    if is_agent_path and SESSION_RE.search(text) and SENSITIVE_RE.search(text):
        findings.append(
            AgentSafetyFinding(
                "sensitive-agent-session",
                line.path,
                line.line_number,
                "Session state must not include raw resumes, provider payloads, cookies, auth headers, "
                "storage state, or tokens.",
                text,
            )
        )
    if BROWSER_STORAGE_RE.search(text) and SENSITIVE_RE.search(text):
        findings.append(
            AgentSafetyFinding(
                "sensitive-browser-storage",
                line.path,
                line.line_number,
                "Frontend storage must not persist raw provider payloads, resumes, cookies, auth headers, "
                "storage state, or tokens.",
                text,
            )
        )

    return findings


def _find_group_issues(lines: Sequence[AddedLine]) -> list[AgentSafetyFinding]:
    findings: list[AgentSafetyFinding] = []
    lines_by_path: dict[str, list[AddedLine]] = {}
    for line in lines:
        lines_by_path.setdefault(line.path, []).append(line)

    for path, path_lines in lines_by_path.items():
        normalized = path.replace("\\", "/")
        if not normalized.startswith("src/seektalent_conversation_agent/") or Path(normalized).suffix != ".py":
            continue
        findings.extend(_find_broad_tool_signature_issues(sorted(path_lines, key=lambda line: line.line_number)))
    return findings


def _find_broad_tool_signature_issues(lines: Sequence[AddedLine]) -> list[AgentSafetyFinding]:
    findings: list[AgentSafetyFinding] = []
    current_block: list[AddedLine] = []
    paren_depth = 0

    for line in lines:
        stripped = line.text.lstrip()
        if not current_block:
            if re.match(r"(?:async\s+def|def)\s+\w+", stripped):
                current_block = [line]
                paren_depth = line.text.count("(") - line.text.count(")")
                if paren_depth <= 0 and line.text.rstrip().endswith(":"):
                    findings.extend(_finding_for_broad_tool_signature(current_block))
                    current_block = []
            continue

        current_block.append(line)
        paren_depth += line.text.count("(") - line.text.count(")")
        if paren_depth <= 0 and line.text.rstrip().endswith(":"):
            findings.extend(_finding_for_broad_tool_signature(current_block))
            current_block = []

    return findings


def _finding_for_broad_tool_signature(lines: Sequence[AddedLine]) -> list[AgentSafetyFinding]:
    signature = " ".join(line.text.strip() for line in lines)
    first_line = lines[0]
    name_match = re.search(r"\b(?:async\s+def|def)\s+(\w+)", signature)
    if name_match is None or not BROAD_TOOL_NAME_RE.search(name_match.group(1)):
        return []
    if not (BROAD_TOOL_ACTION_ARG_RE.search(signature) and BROAD_TOOL_PAYLOAD_ARG_RE.search(signature)):
        return []
    return [
        AgentSafetyFinding(
            "broad-agent-tool-entrypoint",
            first_line.path,
            first_line.line_number,
            "Use narrow typed Agent tools instead of action-plus-payload dispatch.",
            signature,
        )
    ]


def _find_import_issues(line: AddedLine) -> list[AgentSafetyFinding]:
    findings: list[AgentSafetyFinding] = []
    imported_modules = _imported_modules(line.text)
    reported_rules: set[str] = set()
    for forbidden_module, rule_id, message in FORBIDDEN_AGENT_IMPORTS:
        if rule_id in reported_rules:
            continue
        if any(_is_module_or_child(module_name, forbidden_module) for module_name in imported_modules):
            findings.append(AgentSafetyFinding(rule_id, line.path, line.line_number, message, line.text))
            reported_rules.add(rule_id)
    return findings


def _imported_modules(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
            modules.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _is_module_or_child(module_name: str, forbidden_module: str) -> bool:
    return module_name == forbidden_module or module_name.startswith(f"{forbidden_module}.")


def _dedupe_findings(findings: Iterable[AgentSafetyFinding]) -> list[AgentSafetyFinding]:
    deduped: list[AgentSafetyFinding] = []
    seen: set[tuple[str, str, int, str]] = set()
    for finding in findings:
        key = (finding.rule_id, finding.path, finding.line_number, finding.text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def collect_added_lines(base: str) -> list[AddedLine]:
    merge_base = _git_output(["merge-base", base, "HEAD"]).strip()
    added_lines = [
        *parse_added_lines(_git_output(["diff", "--unified=0", "--diff-filter=AM", f"{merge_base}...HEAD"])),
        *parse_added_lines(_git_output(["diff", "--cached", "--unified=0", "--diff-filter=AM"])),
        *parse_added_lines(_git_output(["diff", "--unified=0", "--diff-filter=AM"])),
        *_untracked_added_lines(),
    ]
    return _dedupe_added_lines(added_lines)


def _untracked_added_lines() -> list[AddedLine]:
    paths = _git_output(["ls-files", "--others", "--exclude-standard"]).splitlines()
    added_lines: list[AddedLine] = []
    for path in paths:
        if not should_scan_path(path):
            continue
        file_path = Path(path)
        if not file_path.is_file():
            continue
        for index, text in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
            added_lines.append(AddedLine(path, index, text))
    return added_lines


def _dedupe_added_lines(lines: Iterable[AddedLine]) -> list[AddedLine]:
    seen: set[AddedLine] = set()
    deduped: list[AddedLine] = []
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        deduped.append(line)
    return deduped


def _git_output(args: Sequence[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description="Block unsafe conversational-agent patterns in PR diffs.")
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()

    findings = check_added_lines(collect_added_lines(args.base))
    for finding in findings:
        print(f"{finding.path}:{finding.line_number}: {finding.rule_id}: {finding.message}")
        print(f"  {finding.text.strip()}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
