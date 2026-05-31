from tools.check_pr_governance import (
    LineCountChange,
    classify_path,
    evaluate_changed_files,
    layer_for_path,
    line_limit_for_path,
    merge_changed_file_sets,
)


def test_classify_path_red_runtime() -> None:
    assert classify_path("src/seektalent/runtime/orchestrator.py") == "red"


def test_classify_path_red_provider_registry() -> None:
    assert classify_path("src/seektalent/providers/registry.py") == "red"


def test_classify_path_red_liepin_worker() -> None:
    assert classify_path("apps/liepin-worker/src/server.ts") == "red"


def test_classify_path_yellow_workbench_route() -> None:
    assert classify_path("src/seektalent_ui/workbench_routes.py") == "yellow"


def test_classify_path_green_docs() -> None:
    assert classify_path("docs/development.md") == "green"


def test_gitignore_is_governance_layer() -> None:
    assert layer_for_path(".gitignore") == "governance"


def test_evaluate_changed_files_fails_cross_layer_runtime_and_frontend() -> None:
    result = evaluate_changed_files(
        [
            "src/seektalent/runtime/orchestrator.py",
            "apps/web-svelte/src/lib/components/SourceCard.svelte",
        ],
        max_files=15,
        max_layers=1,
    )

    assert not result.ok
    assert "cross-layer" in result.messages[0]


def test_evaluate_changed_files_allows_single_layer_tests() -> None:
    result = evaluate_changed_files(
        [
            "tests/test_runtime_state_flow.py",
            "tests/test_runtime_audit.py",
        ],
        max_files=15,
        max_layers=1,
    )

    assert result.ok


def test_evaluate_changed_files_reports_red_zone_without_blocking() -> None:
    result = evaluate_changed_files(
        ["src/seektalent/runtime/orchestrator.py"],
        max_files=15,
        max_layers=1,
    )

    assert result.ok
    assert result.red_files == ["src/seektalent/runtime/orchestrator.py"]
    assert "red-zone files touched" in result.messages[0]


def test_evaluate_changed_files_fails_too_many_non_generated_files() -> None:
    result = evaluate_changed_files(
        [f"docs/file_{index}.md" for index in range(16)],
        max_files=15,
        max_layers=1,
    )

    assert not result.ok
    assert "too many non-generated files changed" in result.messages[0]


def test_evaluate_changed_files_ignores_generated_schema() -> None:
    result = evaluate_changed_files(
        ["apps/web-svelte/src/lib/api/schema.d.ts"],
        max_files=0,
        max_layers=0,
    )

    assert result.ok


def test_evaluate_changed_files_ignores_local_artifact_and_superpowers_dirs() -> None:
    result = evaluate_changed_files(
        [
            ".gitignore",
            "artifacts/JDs/agent_jd/agent_jd_003.md",
            "docs/superpowers/plans/2026-05-30-production-ai-coding-governance-gate.md",
            "docs/superpowers/specs/2026-05-30-production-ai-coding-governance-gate-design.md",
        ],
        max_files=1,
        max_layers=1,
    )

    assert result.ok


def test_line_limit_for_path_separates_production_and_test_files() -> None:
    assert line_limit_for_path("src/seektalent/runtime/new_module.py") == 600
    assert line_limit_for_path("tests/test_runtime_state_flow.py") == 900
    assert line_limit_for_path("docs/development.md") is None


def test_evaluate_changed_files_blocks_new_production_file_over_line_limit() -> None:
    result = evaluate_changed_files(
        ["src/seektalent/runtime/new_module.py"],
        line_changes=[LineCountChange("src/seektalent/runtime/new_module.py", base_lines=None, head_lines=601)],
    )

    assert not result.ok
    assert any("new file too long" in message for message in result.messages)


def test_evaluate_changed_files_blocks_existing_oversized_file_growth() -> None:
    result = evaluate_changed_files(
        ["src/seektalent/runtime/orchestrator.py"],
        line_changes=[LineCountChange("src/seektalent/runtime/orchestrator.py", base_lines=4594, head_lines=4595)],
    )

    assert not result.ok
    assert any("oversized file grew" in message for message in result.messages)


def test_evaluate_changed_files_allows_existing_oversized_file_to_shrink() -> None:
    result = evaluate_changed_files(
        ["src/seektalent/runtime/orchestrator.py"],
        line_changes=[LineCountChange("src/seektalent/runtime/orchestrator.py", base_lines=4594, head_lines=4500)],
    )

    assert result.ok


def test_evaluate_changed_files_ignores_generated_file_line_counts() -> None:
    result = evaluate_changed_files(
        ["src/seektalent_ui/resources/workbench/large_schema.py"],
        line_changes=[
            LineCountChange("src/seektalent_ui/resources/workbench/large_schema.py", base_lines=None, head_lines=5000)
        ],
    )

    assert result.ok


def test_evaluate_changed_files_does_not_exempt_schema_suffixes() -> None:
    result = evaluate_changed_files(
        ["apps/web-svelte/src/lib/api/schema.d.ts.tmp"],
        max_files=0,
        max_layers=0,
    )

    assert not result.ok
    assert "too many non-generated files changed" in result.messages[0]


def test_merge_changed_file_sets_includes_local_working_tree_files() -> None:
    assert merge_changed_file_sets(
        ["src/seektalent/runtime/orchestrator.py"],
        ["docs/development.md"],
        ["tools/check_pr_governance.py"],
        ["tests/test_pr_governance.py"],
    ) == [
        "docs/development.md",
        "src/seektalent/runtime/orchestrator.py",
        "tests/test_pr_governance.py",
        "tools/check_pr_governance.py",
    ]
