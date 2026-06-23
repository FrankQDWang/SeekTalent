from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path


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
    story_owner: str
    screenshot_owner: str
    standard: str


DESIGN_ASSET_ROWS = (
    VisualAcceptanceRow(
        asset="figma/thumbnail.png",
        story_owner="WorkbenchShell/FigmaThumbnailReference",
        screenshot_owner="workbench-shell-figma-reference",
        standard="designer",
    ),
    VisualAcceptanceRow(
        asset="wts/首页初始状态.png",
        story_owner="HomeStartPanel/Initial",
        screenshot_owner="workbench-home-initial",
        standard="designer",
    ),
    VisualAcceptanceRow(
        asset="wts/首页输入文字状态.png",
        story_owner="Composer/RequirementDraft",
        screenshot_owner="workbench-home-draft",
        standard="designer",
    ),
    VisualAcceptanceRow(
        asset="wts/需求确认.png",
        story_owner="RequirementReviewPanel/NeedsConfirmation",
        screenshot_owner="workbench-requirement-review",
        standard="designer",
    ),
    VisualAcceptanceRow(
        asset="wts/检索策略图.png",
        story_owner="StrategyGraphCanvas/SearchStrategy",
        screenshot_owner="workbench-strategy-graph",
        standard="designer",
    ),
    VisualAcceptanceRow(
        asset="wts/思考过程.png",
        story_owner="ThinkingProcessRail/RoundTimeline",
        screenshot_owner="workbench-thinking-process",
        standard="designer",
    ),
    VisualAcceptanceRow(
        asset="wts/候选人列表空状态.png",
        story_owner="CandidateQueue/Empty",
        screenshot_owner="workbench-candidates-empty",
        standard="designer",
    ),
    VisualAcceptanceRow(
        asset="wts/候选人列表页面.png",
        story_owner="CandidateQueue/Populated",
        screenshot_owner="workbench-candidates-list",
        standard="designer",
    ),
    VisualAcceptanceRow(
        asset="wts/候选人详情侧边栏.png",
        story_owner="CandidateDetailDrawer/Summary",
        screenshot_owner="workbench-candidate-detail",
        standard="designer",
    ),
    VisualAcceptanceRow(
        asset="wts/简历详情完整内容.png",
        story_owner="ResumeEvidencePanel/FullContent",
        screenshot_owner="workbench-resume-full",
        standard="designer",
    ),
    VisualAcceptanceRow(
        asset="transcript/codex-transcript-01-full-collapsed.png",
        story_owner="Transcript/CollapsedRunGroup",
        screenshot_owner="workbench-transcript-collapsed",
        standard="codex-transcript",
    ),
    VisualAcceptanceRow(
        asset="transcript/codex-transcript-02-full-expanded.png",
        story_owner="Transcript/ExpandedRunGroup",
        screenshot_owner="workbench-transcript-expanded",
        standard="codex-transcript",
    ),
    VisualAcceptanceRow(
        asset="transcript/codex-transcript-03-toolread-detail.png",
        story_owner="Transcript/ToolReadDetails",
        screenshot_owner="workbench-transcript-tool-detail",
        standard="codex-transcript",
    ),
    VisualAcceptanceRow(
        asset="transcript/codex-transcript-04-web-search-running.png",
        story_owner="Transcript/WebSearchRunning",
        screenshot_owner="workbench-transcript-web-running",
        standard="codex-transcript",
    ),
    VisualAcceptanceRow(
        asset="transcript/codex-transcript-05-file-search-complete.png",
        story_owner="Transcript/FileSearchComplete",
        screenshot_owner="workbench-transcript-file-complete",
        standard="codex-transcript",
    ),
    VisualAcceptanceRow(
        asset="transcript/codex-transcript-06-file-read-running.png",
        story_owner="Transcript/FileReadRunning",
        screenshot_owner="workbench-transcript-file-running",
        standard="codex-transcript",
    ),
    VisualAcceptanceRow(
        asset="transcript/codex-transcript-07-guided-followup.png",
        story_owner="Transcript/GuidedFollowup",
        screenshot_owner="workbench-transcript-guided-followup",
        standard="codex-transcript",
    ),
)


def collect_violations(root: Path) -> list[AcceptanceViolation]:
    artifact_root = root / "docs/superpowers/artifacts/react-agent-workbench-design"
    manifest_path = artifact_root / "MANIFEST.sha256"
    storybook_gate = root / "apps/web-react/tests/storybook-a11y.spec.ts"
    visual_gate = root / "apps/web-react/tests/storybook-visual.spec.ts"
    test_text = storybook_gate.read_text(encoding="utf-8")
    visual_text = visual_gate.read_text(encoding="utf-8")
    violations: list[AcceptanceViolation] = []

    rows = list(DESIGN_ASSET_ROWS)
    manifest_assets = _manifest_image_assets(manifest_path)
    mapped_assets = {row.asset for row in rows}

    for asset in sorted(manifest_assets - mapped_assets):
        violations.append(
            AcceptanceViolation(reason="asset manifest entry missing visual owner", value=asset)
        )
    for asset in sorted(mapped_assets - manifest_assets):
        violations.append(
            AcceptanceViolation(reason="visual owner missing asset manifest entry", value=asset)
        )

    for row in rows:
        if not (artifact_root / row.asset).is_file():
            violations.append(
                AcceptanceViolation(reason=f"missing {row.standard} asset", value=row.asset)
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
    return violations


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    violations = collect_violations(root)
    if not violations:
        return 0
    for violation in violations:
        print(f"{violation.reason}: {violation.value}", file=sys.stderr)
    return 1


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


def _storybook_id(story_owner: str) -> str:
    component, story = story_owner.split("/", maxsplit=1)
    return f"workbench-{component.lower()}--{_story_export_id(story)}"


def _story_export_id(value: str) -> str:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", value).replace("_", "-")
    return words.lower()


if __name__ == "__main__":
    raise SystemExit(main())
