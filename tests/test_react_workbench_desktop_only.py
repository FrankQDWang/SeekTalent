from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKBENCH_SOURCE_PATHS = [
    ROOT / "apps/web-react/src/components/workbench",
    ROOT / "apps/web-react/src/routes/conversation.tsx",
    ROOT / "apps/web-react/src/lib/api/agentWorkbench.ts",
    ROOT / "apps/web-react/src/lib/api/client.ts",
]

FORBIDDEN_WORKBENCH_PATTERNS = [
    "@media (max-width",
    "@media(max-width",
    "@media (width <",
    "useCompactWorkspace",
    "compactWorkspace",
    'orientation="vertical"',
    "orientation='vertical'",
    "row-resize",
    "home-start-panel--collapsing",
    "HOME_START_PANEL_COLLAPSE_MS",
]

PACKAGED_FRONTEND_REQUIRED_PATTERNS = [
    "/api/agent/workbench/conversations/from-jd",
]
PACKAGED_FRONTEND_FORBIDDEN_PATTERNS = [
    "submitJd",
    "home-start-panel--collapsing",
    "matchMedia('(max-width: 1080px)')",
    'orientation="vertical"',
]


def test_react_workbench_keeps_desktop_only_layout_contract() -> None:
    violations: list[str] = []

    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_WORKBENCH_PATTERNS:
            if pattern in text:
                violations.append(f"{path.relative_to(ROOT)} contains {pattern!r}")

    assert violations == []


def test_packaged_workbench_frontend_matches_first_turn_contract() -> None:
    packaged_dir = ROOT / "src/seektalent_ui/static/workbench"
    bundle_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in packaged_dir.rglob("*")
        if path.suffix in {".html", ".js", ".css"}
    )

    violations = [
        f"packaged frontend is missing {pattern!r}"
        for pattern in PACKAGED_FRONTEND_REQUIRED_PATTERNS
        if pattern not in bundle_text
    ]
    violations.extend(
        f"packaged frontend still contains {pattern!r}"
        for pattern in PACKAGED_FRONTEND_FORBIDDEN_PATTERNS
        if pattern in bundle_text
    )

    assert violations == []


def _source_files() -> list[Path]:
    files: list[Path] = []
    for path in WORKBENCH_SOURCE_PATHS:
        if path.is_file():
            files.append(path)
            continue
        files.extend(
            candidate
            for candidate in path.rglob("*")
            if candidate.suffix in {".css", ".ts", ".tsx"} and candidate.is_file()
        )
    return files
