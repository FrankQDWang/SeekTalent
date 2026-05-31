from __future__ import annotations

import argparse
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
    ".sh",
    ".svelte",
    ".ts",
    ".tsx",
}

SCAN_PREFIXES = (
    "apps/",
    "scripts/",
    "src/",
)

IGNORED_PREFIXES = (
    "artifacts/",
    "docs/superpowers/",
    "tests/",
)

GENERATED_PREFIXES = (
    "src/seektalent_ui/resources/workbench/",
)

SENSITIVE_LOG_TERMS = (
    "authorization",
    "auth_headers",
    "cookie",
    "provider_payload",
    "provider_response",
    "raw_payload",
    "raw_provider_payload",
    "raw_provider_response",
    "raw_resume",
    "response.content",
    "response.headers",
    "response.text",
    "storage_state",
    "storagestate",
    "token",
)

RAW_PAYLOAD_KEYS = (
    "auth_headers",
    "provider_payload",
    "provider_response",
    "raw_payload",
    "raw_provider_payload",
    "raw_resume",
    "storageState",
    "storage_state",
)

LOG_SINK_RE = re.compile(
    r"\b(?:logger|log)\s*\.\s*(?:debug|info|warning|warn|error|exception|critical)\s*\(|\bprint\s*\(",
    re.IGNORECASE,
)
EXCEPTION_DETAIL_RE = re.compile(
    r"\bdetail\s*=\s*(?:str\s*\(\s*)?(?:exc|err|error|exception|e)(?:\s*\))?\b",
    re.IGNORECASE,
)
RAW_PAYLOAD_KEY_RE = re.compile(
    r"['\"]("
    + "|".join(re.escape(term) for term in RAW_PAYLOAD_KEYS)
    + r")['\"]\s*:",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AddedLine:
    path: str
    line_number: int
    text: str


@dataclass(frozen=True)
class PrivacyFinding:
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
    if "/tests/" in normalized or normalized.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")):
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


def check_added_lines(lines: Iterable[AddedLine]) -> list[PrivacyFinding]:
    findings: list[PrivacyFinding] = []
    for line in lines:
        if not should_scan_path(line.path):
            continue
        findings.extend(_find_line_issues(line))
    return findings


def _find_line_issues(line: AddedLine) -> list[PrivacyFinding]:
    text = line.text
    findings: list[PrivacyFinding] = []
    lowered = text.casefold()

    if LOG_SINK_RE.search(text) and any(term in lowered for term in SENSITIVE_LOG_TERMS):
        findings.append(
            PrivacyFinding(
                "sensitive-log-output",
                line.path,
                line.line_number,
                "Do not log raw provider responses, resumes, cookies, authorization values, or tokens.",
                text,
            )
        )
    if EXCEPTION_DETAIL_RE.search(text):
        findings.append(
            PrivacyFinding(
                "exception-detail-exposure",
                line.path,
                line.line_number,
                "Do not expose raw exception strings through API detail fields.",
                text,
            )
        )
    raw_key_match = RAW_PAYLOAD_KEY_RE.search(text)
    if raw_key_match is not None and "redact" not in lowered and "_hash" not in lowered:
        findings.append(
            PrivacyFinding(
                "raw-sensitive-payload-key",
                line.path,
                line.line_number,
                f"Do not add raw sensitive payload key '{raw_key_match.group(1)}' to output structures.",
                text,
            )
        )
    return findings


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
    parser = argparse.ArgumentParser(description="Block new raw sensitive data exposure in PR diffs.")
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()

    findings = check_added_lines(collect_added_lines(args.base))
    for finding in findings:
        print(f"{finding.path}:{finding.line_number}: {finding.rule_id}: {finding.message}")
        print(f"  {finding.text.strip()}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
