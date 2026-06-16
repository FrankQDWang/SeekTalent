from tools.check_react_workbench_cutover import collect_violations


def test_cutover_gate_rejects_production_fixture_import() -> None:
    violations = collect_violations(
        [
            (
                "apps/web-react/src/routes/conversation.tsx",
                'import { fixture } from "../test/fixtures/agentWorkbench";',
            )
        ]
    )

    assert [violation.reason for violation in violations] == ["production React source imports test fixtures"]


def test_cutover_gate_allows_storybook_fixture_imports() -> None:
    violations = collect_violations(
        [
            (
                "apps/web-react/src/components/workbench/Transcript.stories.tsx",
                'import { fixture } from "../../test/fixtures/agentWorkbench";',
            )
        ]
    )

    assert violations == []


def test_cutover_gate_rejects_retired_frontend_references() -> None:
    retired_path = "apps/web-" + "sv" + "elte"
    violations = collect_violations(
        [
            ("docs/development.md", f"Current frontend path: {retired_path}"),
            ("docs/archive/old-plan.md", f"Historical path: {retired_path}"),
            ("scripts/start-dev-workbench.sh", f"Current frontend path: {retired_path}"),
        ]
    )

    assert [violation.path for violation in violations] == [
        "docs/development.md",
        "docs/archive/old-plan.md",
        "scripts/start-dev-workbench.sh",
    ]


def test_cutover_gate_allows_liepin_worker_bun_only() -> None:
    violations = collect_violations(
        [
            ("docs/configuration.md", "The Bun apps/liepin-worker connector remains supported."),
            ("README.md", "Install Bun for the React Workbench."),
        ]
    )

    assert [violation.path for violation in violations] == ["README.md"]
