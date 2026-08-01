from __future__ import annotations

import argparse
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass


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


SENSITIVE_TRACE_RE = re.compile(
    r"\b(?:trace|session)\w*\b.*\b(?:authorization|cookie|raw_resume|storage_state|token)\b",
    re.IGNORECASE,
)
NO_RAW_BROWSER_STORAGE_RE = re.compile(
    r"\b(?:localStorage|sessionStorage)\s*\.\s*setItem\s*\(.*\b(?:cookie|token|raw_resume|provider_payload)\b",
    re.IGNORECASE,
)


def parse_added_lines(diff_text: str) -> list[AddedLine]:
    result: list[AddedLine] = []
    path: str | None = None
    line_no: int | None = None
    hunk_re = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for raw in diff_text.splitlines():
        if raw.startswith("+++ b/"):
            path, line_no = raw[6:], None
        elif raw.startswith("+++ /dev/null"):
            path, line_no = None, None
        elif raw.startswith("@@"):
            match = hunk_re.search(raw)
            line_no = int(match.group(1)) if match else None
        elif path is not None and line_no is not None:
            if raw.startswith("+") and not raw.startswith("+++"):
                result.append(AddedLine(path, line_no, raw[1:]))
                line_no += 1
            elif not raw.startswith("-"):
                line_no += 1
    return result


def check_added_lines(lines: Iterable[AddedLine]) -> list[AgentSafetyFinding]:
    findings: list[AgentSafetyFinding] = []
    for line in lines:
        if SENSITIVE_TRACE_RE.search(line.text):
            findings.append(
                AgentSafetyFinding(
                    "sensitive-runtime-trace",
                    line.path,
                    line.line_number,
                    "Runtime traces and sessions must not contain credentials or raw sensitive payloads.",
                    line.text,
                )
            )
        if NO_RAW_BROWSER_STORAGE_RE.search(line.text):
            findings.append(
                AgentSafetyFinding(
                    "sensitive-browser-storage",
                    line.path,
                    line.line_number,
                    "Browser storage must not persist credentials or raw provider payloads.",
                    line.text,
                )
            )
    return findings


def collect_added_lines(base: str) -> list[AddedLine]:
    merge_base = subprocess.run(
        ["git", "merge-base", base, "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    diff = subprocess.run(
        ["git", "diff", "--unified=0", "--diff-filter=AM", f"{merge_base}...HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return parse_added_lines(diff)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()
    findings = check_added_lines(collect_added_lines(args.base))
    for finding in findings:
        print(f"{finding.path}:{finding.line_number}: {finding.message}: {finding.text}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
