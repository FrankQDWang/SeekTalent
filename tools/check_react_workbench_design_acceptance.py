from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path


BACKTICK_PATTERN = re.compile(r"`([^`]+)`")
IMAGE_ASSET_SUFFIXES = {".jpeg", ".jpg", ".png", ".webp"}
SUPPLEMENTAL_VISUAL_OWNERS = {
    "StrategyGraphCanvas/LargeSearchStrategy": "workbench-strategy-graph-large"
}
LEGACY_STORY_FIXTURE_IMPORTS = (
    'from "../../test/fixtures/agentWorkbench"',
    'from "../../test/fixtures/agentWorkbenchStates"',
)


@dataclass(frozen=True)
class AcceptanceViolation:
    reason: str
    value: str


@dataclass(frozen=True)
class VisualAcceptanceRow:
    asset: str
    owner_route_component: str
    story_owner: str
    screenshot_owner: str


def collect_violations(root: Path) -> list[AcceptanceViolation]:
    design_path = root / "apps/web-react/DESIGN.md"
    artifact_root = root / "docs/superpowers/artifacts/react-agent-workbench-design"
    manifest_path = artifact_root / "MANIFEST.sha256"
    storybook_gate = root / "apps/web-react/tests/storybook-a11y.spec.ts"
    visual_gate = root / "apps/web-react/tests/storybook-visual.spec.ts"
    design_text = design_path.read_text(encoding="utf-8")
    test_text = storybook_gate.read_text(encoding="utf-8")
    visual_text = visual_gate.read_text(encoding="utf-8")
    violations: list[AcceptanceViolation] = []

    rows = _acceptance_rows(design_text)
    manifest_assets = _manifest_image_assets(manifest_path)
    mapped_assets = {row.asset for row in rows}
    known_owners = _known_owner_symbols(design_text, rows)

    for asset in sorted(manifest_assets - mapped_assets):
        violations.append(
            AcceptanceViolation(reason="manifest asset missing visual owner", value=asset)
        )
    for asset in sorted(mapped_assets - manifest_assets):
        violations.append(
            AcceptanceViolation(reason="visual owner missing manifest asset", value=asset)
        )

    for row in rows:
        if not (artifact_root / row.asset).is_file():
            violations.append(AcceptanceViolation(reason="missing design asset", value=row.asset))

        owner_values = _backtick_values(row.owner_route_component)
        if not owner_values:
            violations.append(
                AcceptanceViolation(reason="route/component owner missing", value=row.asset)
            )
        for owner in owner_values:
            if not _valid_owner(owner, known_owners):
                violations.append(
                    AcceptanceViolation(
                        reason="unknown route/component owner",
                        value=f"{row.asset}: {owner}",
                    )
                )

        story_id = _storybook_id(row.story_owner)
        if f"/iframe.html?id={story_id}" not in test_text:
            violations.append(
                AcceptanceViolation(
                    reason="Storybook owner missing from a11y gate",
                    value=row.story_owner,
                )
            )
        if f'name: "{row.screenshot_owner}"' not in visual_text:
            violations.append(
                AcceptanceViolation(
                    reason="Playwright screenshot owner missing from visual gate",
                    value=row.screenshot_owner,
                )
            )
    for story_owner, screenshot_owner in SUPPLEMENTAL_VISUAL_OWNERS.items():
        story_id = _storybook_id(story_owner)
        if f"/iframe.html?id={story_id}" not in test_text:
            violations.append(
                AcceptanceViolation(
                    reason="supplemental Storybook owner missing from a11y gate",
                    value=story_owner,
                )
            )
        if f'name: "{screenshot_owner}"' not in visual_text:
            violations.append(
                AcceptanceViolation(
                    reason="supplemental Playwright screenshot owner missing from visual gate",
                    value=screenshot_owner,
                )
            )
    for path in (root / "apps/web-react/src/components").glob("**/*.stories.tsx"):
        text = path.read_text(encoding="utf-8")
        for legacy_import in LEGACY_STORY_FIXTURE_IMPORTS:
            if legacy_import in text:
                violations.append(
                    AcceptanceViolation(
                        reason="Storybook story imports legacy design fixture directly",
                        value=path.relative_to(root).as_posix(),
                    )
                )
    if not rows:
        violations.append(AcceptanceViolation(reason="visual acceptance map is empty", value=str(design_path)))
    return violations


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    violations = collect_violations(root)
    if not violations:
        return 0
    for violation in violations:
        print(f"{violation.reason}: {violation.value}", file=sys.stderr)
    return 1


def _acceptance_rows(text: str) -> list[VisualAcceptanceRow]:
    rows: list[VisualAcceptanceRow] = []
    in_section = False
    for line in text.splitlines():
        if line == "## Visual Acceptance Map":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        columns = [column.strip() for column in line.split("|")]
        if len(columns) < 5:
            continue
        asset = _backtick_value(columns[1])
        owner = columns[2]
        story = _backtick_value(columns[3])
        screenshot = _backtick_value(columns[4])
        if asset is not None and story is not None and screenshot is not None:
            rows.append(
                VisualAcceptanceRow(
                    asset=asset,
                    owner_route_component=owner,
                    story_owner=story,
                    screenshot_owner=screenshot,
                )
            )
    return rows


def _manifest_image_assets(manifest_path: Path) -> set[str]:
    artifact_prefix = "docs/superpowers/artifacts/react-agent-workbench-design/"
    assets: set[str] = set()
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        path = Path(parts[1])
        if path.suffix.lower() not in IMAGE_ASSET_SUFFIXES:
            continue
        value = path.as_posix()
        if value.startswith(artifact_prefix):
            value = value.removeprefix(artifact_prefix)
        assets.add(value)
    return assets


def _known_owner_symbols(text: str, rows: list[VisualAcceptanceRow]) -> set[str]:
    symbols = {row.story_owner.split("/", maxsplit=1)[0] for row in rows}
    in_section = False
    for line in text.splitlines():
        if line == "## Component Taxonomy":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        match = re.match(r"- `([^`]+)`:", line)
        if match is not None:
            symbols.add(match.group(1))
    return symbols


def _valid_owner(value: str, known_owners: set[str]) -> bool:
    return value.startswith("/") or value in known_owners


def _backtick_value(value: str) -> str | None:
    match = BACKTICK_PATTERN.search(value)
    if match is None:
        return None
    return match.group(1)


def _backtick_values(value: str) -> list[str]:
    return BACKTICK_PATTERN.findall(value)


def _storybook_id(story_owner: str) -> str:
    component, story = story_owner.split("/", maxsplit=1)
    return f"workbench-{component.lower()}--{_story_export_id(story)}"


def _story_export_id(value: str) -> str:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", value).replace("_", "-")
    return words.lower()


if __name__ == "__main__":
    raise SystemExit(main())
