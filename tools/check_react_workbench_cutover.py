from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


STALE_REFERENCE_PATTERNS = (
    re.compile(r"apps/web-" + "sv" + "elte"),
    re.compile(r"docs/ui\.md"),
    re.compile(r"docs/archive/legacy-ui-design\.md"),
    re.compile(r"docs/v-0\.[12]/design\.md"),
)
BUN_PATTERN = re.compile(r"\bBun\b|bun\.lockb?")
FIXTURE_PATTERN = re.compile(r"apps/web-react/src/test/fixtures|src/test/fixtures|test/fixtures")


@dataclass(frozen=True)
class CutoverViolation:
    path: str
    line_no: int
    reason: str
    line: str


def collect_violations(files: Iterable[tuple[str, str]]) -> list[CutoverViolation]:
    violations: list[CutoverViolation] = []
    for path, text in files:
        if _is_production_react_source(path):
            violations.extend(_fixture_violations(path, text))
        if _is_active_cutover_surface(path):
            violations.extend(_stale_reference_violations(path, text))
    return violations


def tracked_files(root: Path) -> list[tuple[str, str]]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    files: list[tuple[str, str]] = []
    for path in result.stdout.splitlines():
        full_path = root / path
        if not full_path.is_file():
            continue
        files.append((path, full_path.read_text(encoding="utf-8", errors="ignore")))
    return files


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    violations = collect_violations(tracked_files(root))
    if not violations:
        return 0
    for violation in violations:
        print(
            f"{violation.path}:{violation.line_no}: {violation.reason}: {violation.line}",
            file=sys.stderr,
        )
    return 1


def _fixture_violations(path: str, text: str) -> list[CutoverViolation]:
    return [
        CutoverViolation(path=path, line_no=line_no, reason="production React source imports test fixtures", line=line)
        for line_no, line in _matching_lines(text, FIXTURE_PATTERN)
    ]


def _stale_reference_violations(path: str, text: str) -> list[CutoverViolation]:
    violations: list[CutoverViolation] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if any(pattern.search(line) for pattern in STALE_REFERENCE_PATTERNS):
            violations.append(CutoverViolation(path=path, line_no=line_no, reason="stale legacy UI reference", line=line))
        if BUN_PATTERN.search(line) and not _allowed_bun_reference(path, line):
            violations.append(CutoverViolation(path=path, line_no=line_no, reason="Bun reference outside Liepin worker allowlist", line=line))
    return violations


def _matching_lines(text: str, pattern: re.Pattern[str]) -> Iterable[tuple[int, str]]:
    for line_no, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            yield line_no, line


def _is_production_react_source(path: str) -> bool:
    if not path.startswith("apps/web-react/src/"):
        return False
    if path.startswith("apps/web-react/src/test/"):
        return False
    return not (
        path.endswith(".test.ts")
        or path.endswith(".test.tsx")
        or path.endswith(".spec.ts")
        or path.endswith(".spec.tsx")
        or path.endswith(".stories.ts")
        or path.endswith(".stories.tsx")
    )


def _is_active_cutover_surface(path: str) -> bool:
    if path in {"tools/check_pr_governance.py", "tools/check_react_workbench_cutover.py"}:
        return False
    if path.startswith("docs/governance/agent-goals/"):
        return False
    if path.startswith("docs/superpowers/"):
        return False
    if path.startswith("docs/plans/completed/"):
        return False
    if path.startswith("docs/governance/agent-goals/") and path.endswith("-progress.md"):
        return False
    return (
        path in {"README.md", "AGENTS.md", "PRODUCT.md"}
        or path.startswith("docs/")
        or path.startswith("seektalent_codex_goal_pack/")
        or path.startswith("scripts/")
        or path.startswith("tools/")
        or path.startswith(".github/")
    )


def _allowed_bun_reference(path: str, line: str) -> bool:
    if "apps/liepin-worker" in line:
        return True
    if path in {"scripts/verify-red-zone.sh", "tools/check_pr_governance.py", ".github/dependabot.yml"}:
        return True
    if path in {
        "docs/architecture-dependencies.md",
        "docs/configuration.md",
        "docs/references/pinpin-liepin-mapping-notes.md",
    }:
        return "liepin" in line.casefold() or "worker" in line.casefold()
    return False


if __name__ == "__main__":
    raise SystemExit(main())
