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
    ".ts",
    ".tsx",
}

SCAN_PREFIXES = (
    "apps/",
    "scripts/",
    "src/",
    "tools/",
)

IGNORED_PREFIXES = (
    "artifacts/",
    "docs/superpowers/",
    "tests/",
)

GENERATED_PREFIXES = (
    "apps/web-react/tests/storybook-visual.spec.ts-snapshots/",
    "src/seektalent_ui/static/workbench/",
    "src/seektalent_ui/resources/workbench/",
)

TYPED_BOUNDARY_PREFIXES = (
    "src/seektalent_ui/agent_workbench_",
)

TYPED_BOUNDARY_FILES = {
    "src/seektalent_ui/event_routes.py",
}

TEST_PATH_MARKERS = (
    "/tests/",
    "tests/",
)

TEST_FILE_SUFFIXES = (
    ".test.ts",
    ".test.tsx",
    ".spec.ts",
    ".spec.tsx",
)

BROAD_EXCEPTION_RE = re.compile(r"^\s*except\s*(?::|(?:Base)?Exception\b)")
TYPE_IGNORE_RE = re.compile(r"#\s*type:\s*ignore\b", re.IGNORECASE)
NOQA_RE = re.compile(r"#\s*noqa\b", re.IGNORECASE)
IMPORT_PATH_MUTATION_RE = re.compile(r"^\s*sys\.path\.(?:append|insert|extend)\s*\(")
TYPING_ANY_IMPORT_RE = re.compile(r"\bfrom\s+typing\s+import\s+.*\bAny\b|\btyping\.Any\b")
TYPING_ANY_ANNOTATION_RE = re.compile(r"(?:^|[,( ])(?:\w+\s*)?:\s*.*\bAny\b|->\s*.*\bAny\b")
CAST_RE = re.compile(r"\bcast\s*\(")
FALLBACK_RE = re.compile(r"\bfallback(?:\b|_)|\bbest[ -]effort\b|\bbackup[ _-]path\b", re.IGNORECASE)
BLOCKING_RULE_IDS = {
    "type-ignore",
    "ruff-noqa",
    "import-path-mutation",
}


@dataclass(frozen=True)
class AddedLine:
    path: str
    line_number: int
    text: str


@dataclass(frozen=True)
class BadSmellFinding:
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
    if is_test_path(normalized):
        return False
    return normalized.startswith(SCAN_PREFIXES) and Path(normalized).suffix in CODE_EXTENSIONS


def is_typed_boundary_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized in TYPED_BOUNDARY_FILES or normalized.startswith(TYPED_BOUNDARY_PREFIXES)


def is_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized.startswith(TEST_PATH_MARKERS) or "/tests/" in normalized or normalized.endswith(TEST_FILE_SUFFIXES)


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


def collect_findings(lines: Iterable[AddedLine], *, changed_paths: Sequence[str]) -> list[BadSmellFinding]:
    has_test_changes = any(is_test_path(path) for path in changed_paths)
    findings: list[BadSmellFinding] = []
    for line in lines:
        if not should_scan_path(line.path):
            continue
        findings.extend(_find_line_issues(line, has_test_changes=has_test_changes))
    return findings


def check_added_lines(lines: Iterable[AddedLine], *, changed_paths: Sequence[str]) -> list[BadSmellFinding]:
    return [
        finding
        for finding in collect_findings(lines, changed_paths=changed_paths)
        if finding.rule_id in BLOCKING_RULE_IDS
    ]


def _find_line_issues(line: AddedLine, *, has_test_changes: bool) -> list[BadSmellFinding]:
    text = line.text
    findings: list[BadSmellFinding] = []

    if BROAD_EXCEPTION_RE.search(text):
        findings.append(
            BadSmellFinding(
                "broad-exception-handler",
                line.path,
                line.line_number,
                "Avoid broad exception handlers unless the PR adds focused behavior coverage.",
                text,
            )
        )
    if TYPE_IGNORE_RE.search(text):
        findings.append(
            BadSmellFinding(
                "type-ignore",
                line.path,
                line.line_number,
                "Avoid type ignore comments in new code; narrow the type or isolate the boundary.",
                text,
            )
        )
    if NOQA_RE.search(text):
        findings.append(
            BadSmellFinding(
                "ruff-noqa",
                line.path,
                line.line_number,
                "Avoid new Ruff noqa suppressions; fix the lint issue or isolate the boundary.",
                text,
            )
        )
    if IMPORT_PATH_MUTATION_RE.search(text):
        findings.append(
            BadSmellFinding(
                "import-path-mutation",
                line.path,
                line.line_number,
                "Avoid mutating sys.path in new code; fix packaging/import boundaries instead.",
                text,
            )
        )
    if not is_typed_boundary_path(line.path) and (
        TYPING_ANY_IMPORT_RE.search(text) or TYPING_ANY_ANNOTATION_RE.search(text)
    ):
        findings.append(
            BadSmellFinding(
                "typing-any",
                line.path,
                line.line_number,
                "Avoid new Any usage outside an explicit typed boundary.",
                text,
            )
        )
    if not is_typed_boundary_path(line.path) and CAST_RE.search(text):
        findings.append(
            BadSmellFinding(
                "typing-cast",
                line.path,
                line.line_number,
                "Avoid new typing casts; prefer validating or narrowing the value explicitly.",
                text,
            )
        )
    if not has_test_changes and FALLBACK_RE.search(text):
        findings.append(
            BadSmellFinding(
                "untested-fallback-path",
                line.path,
                line.line_number,
                "Fallback or best-effort paths need focused tests in the same PR.",
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


def changed_paths(base: str) -> list[str]:
    merge_base = _git_output(["merge-base", base, "HEAD"]).strip()
    paths = [
        *_git_output(["diff", "--name-only", f"{merge_base}...HEAD"]).splitlines(),
        *_git_output(["diff", "--cached", "--name-only"]).splitlines(),
        *_git_output(["diff", "--name-only"]).splitlines(),
        *_git_output(["ls-files", "--others", "--exclude-standard"]).splitlines(),
    ]
    return sorted({path for path in paths if path})


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
    parser = argparse.ArgumentParser(description="Block new AI-prone maintenance smells in PR diffs.")
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()

    paths = changed_paths(args.base)
    findings = collect_findings(collect_added_lines(args.base), changed_paths=paths)
    for finding in findings:
        severity = "error" if finding.rule_id in BLOCKING_RULE_IDS else "warning"
        print(f"{finding.path}:{finding.line_number}: {severity}: {finding.rule_id}: {finding.message}")
        print(f"  {finding.text.strip()}")
    return 1 if any(finding.rule_id in BLOCKING_RULE_IDS for finding in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
