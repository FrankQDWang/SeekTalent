from __future__ import annotations

import importlib.util
import json
import re
import tomllib
from pathlib import Path

from tools.check_pr_governance import (
    LineCountChange,
    classify_path,
    evaluate_changed_files,
    is_dependency_control_file,
    layer_for_path,
    line_limit_for_path,
    merge_changed_file_sets,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _module_exists(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def test_tach_config_references_existing_modules() -> None:
    payload = tomllib.loads((PROJECT_ROOT / "tach.toml").read_text(encoding="utf-8"))

    missing = [module["path"] for module in payload["modules"] if not _module_exists(module["path"])]

    assert missing == []


def test_tach_config_tracks_provider_boundary() -> None:
    tach_config = (PROJECT_ROOT / "tach.toml").read_text(encoding="utf-8")
    payload = tomllib.loads(tach_config)
    module_paths = {module["path"] for module in payload["modules"]}

    assert "seektalent.providers" in module_paths
    assert "Provider boundary is a red-zone integration surface" in tach_config


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


def test_dependency_control_files_are_dependency_layer() -> None:
    assert layer_for_path("pyproject.toml") == "dependencies"
    assert layer_for_path("apps/web-svelte/package.json") == "dependencies"
    assert is_dependency_control_file("apps/liepin-worker/bun.lock")
    assert is_dependency_control_file("requirements-dev.txt")


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


def test_evaluate_changed_files_allows_backend_architecture_radar_cleanup() -> None:
    result = evaluate_changed_files(
        [
            "tach.toml",
            "tools/tach_baseline.json",
            "src/seektalent/runtime/exact_llm_cache.py",
            "src/seektalent/cache/exact_llm_cache.py",
            "src/seektalent/requirements/extractor.py",
        ],
        max_files=15,
        max_layers=1,
    )

    assert result.ok


def test_evaluate_changed_files_blocks_architecture_radar_with_frontend() -> None:
    result = evaluate_changed_files(
        [
            "tach.toml",
            "tools/tach_baseline.json",
            "src/seektalent/runtime/orchestrator.py",
            "apps/web-svelte/src/lib/components/SourceCard.svelte",
        ],
        max_files=15,
        max_layers=1,
    )

    assert not result.ok
    assert any("cross-layer" in message for message in result.messages)


def test_evaluate_changed_files_blocks_prompt_runtime_changes_even_with_architecture_radar() -> None:
    result = evaluate_changed_files(
        [
            "tach.toml",
            "src/seektalent/runtime/orchestrator.py",
            "src/seektalent/prompts/source_planning.py",
        ],
        max_files=15,
        max_layers=1,
    )

    assert not result.ok
    assert (
        "prompt and runtime files touched together: "
        "src/seektalent/prompts/source_planning.py, src/seektalent/runtime/orchestrator.py"
    ) in result.messages


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


def test_evaluate_changed_files_blocks_red_zone() -> None:
    result = evaluate_changed_files(
        ["src/seektalent/runtime/orchestrator.py"],
        max_files=15,
        max_layers=1,
    )

    assert not result.ok
    assert result.red_files == ["src/seektalent/runtime/orchestrator.py"]
    assert "red-zone files touched" in result.messages[0]


def test_evaluate_changed_files_blocks_dependency_control_changes() -> None:
    result = evaluate_changed_files(
        [
            "pyproject.toml",
            "uv.lock",
            "apps/web-svelte/package.json",
            "apps/web-svelte/bun.lock",
        ],
        max_files=15,
        max_layers=1,
    )

    assert not result.ok
    assert (
        "dependency control files touched: apps/web-svelte/bun.lock, apps/web-svelte/package.json, "
        "pyproject.toml, uv.lock"
    ) in result.messages


def test_evaluate_changed_files_blocks_config_env_behavior_even_with_security_manifest(tmp_path: Path) -> None:
    manifest_path = "docs/security/remediations/2026-05-31-config-runtime.json"
    manifest = tmp_path / manifest_path
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "seektalent.security_remediation.v1",
                "findings": [{"id": "ST-SEC-009", "title": "Runtime config coupling"}],
                "remediated_files": [
                    ".env.example",
                    "src/seektalent/runtime/orchestrator.py",
                ],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            ".env.example",
            "src/seektalent/runtime/orchestrator.py",
        ],
        max_files=15,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert (
        "config/env and behavior files touched together: .env.example, "
        "src/seektalent/runtime/orchestrator.py"
    ) in result.messages


def test_evaluate_changed_files_allows_valid_security_remediation_manifest(tmp_path: Path) -> None:
    manifest_path = "docs/security/remediations/2026-05-31-liepin-boundaries.json"
    paths = [
        manifest_path,
        "apps/liepin-worker/src/server.ts",
        "src/seektalent/providers/liepin/store.py",
        "src/seektalent_ui/workbench_routes.py",
    ]
    manifest = tmp_path / manifest_path
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "seektalent.security_remediation.v1",
                "findings": [{"id": "ST-SEC-002", "title": "Caller-provided account hash approval"}],
                "remediated_files": [
                    "apps/liepin-worker/src/server.ts",
                    "src/seektalent/providers/liepin/store.py",
                ],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_changed_files(paths, max_files=15, max_layers=1, project_root=tmp_path)

    assert result.ok


def test_evaluate_changed_files_does_not_count_security_manifest_against_file_budget(tmp_path: Path) -> None:
    manifest_path = "docs/security/remediations/2026-05-31-liepin-boundaries.json"
    paths = [
        manifest_path,
        "apps/liepin-worker/src/server.ts",
        *[f"tests/test_security_remediation_{index}.py" for index in range(14)],
    ]
    manifest = tmp_path / manifest_path
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "seektalent.security_remediation.v1",
                "findings": [{"id": "ST-SEC-002", "title": "Caller-provided account hash approval"}],
                "remediated_files": ["apps/liepin-worker/src/server.ts"],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_changed_files(paths, max_files=15, max_layers=1, project_root=tmp_path)

    assert result.ok


def test_evaluate_changed_files_blocks_security_remediation_missing_red_file(tmp_path: Path) -> None:
    manifest_path = "docs/security/remediations/2026-05-31-liepin-boundaries.json"
    manifest = tmp_path / manifest_path
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "seektalent.security_remediation.v1",
                "findings": [{"id": "ST-SEC-002", "title": "Caller-provided account hash approval"}],
                "remediated_files": ["src/seektalent/providers/liepin/store.py"],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            "apps/liepin-worker/src/server.ts",
            "src/seektalent/providers/liepin/store.py",
        ],
        max_files=15,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any("security remediation manifest does not cover red-zone files" in message for message in result.messages)


def test_evaluate_changed_files_blocks_security_remediation_for_governance_changes(tmp_path: Path) -> None:
    manifest_path = "docs/security/remediations/2026-05-31-governance.json"
    manifest = tmp_path / manifest_path
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "seektalent.security_remediation.v1",
                "findings": [{"id": "ST-SEC-008", "title": "PR gates self-bypassable"}],
                "remediated_files": [
                    "tools/check_pr_governance.py",
                    "src/seektalent/providers/liepin/store.py",
                ],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            "tools/check_pr_governance.py",
            "src/seektalent/providers/liepin/store.py",
        ],
        max_files=15,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any("security remediation manifest cannot cover layers: governance" in message for message in result.messages)


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
        ["src/seektalent/oversized_module.py"],
        line_changes=[LineCountChange("src/seektalent/oversized_module.py", base_lines=1000, head_lines=900)],
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


def test_publish_pypi_workflow_pins_actions_to_commit_shas() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/publish-pypi.yml").read_text(encoding="utf-8")
    mutable_refs = [
        ref
        for ref in re.findall(r"uses:\s*[-\w/]+@([^\s]+)", workflow)
        if not re.fullmatch(r"[0-9a-f]{40}", ref)
    ]

    assert mutable_refs == []


def test_ci_pr_governance_runs_base_branch_gate_scripts() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "git show \"$base_ref:tools/check_pr_governance.py\"" in workflow
    assert "git show \"$base_ref:tools/check_privacy_gate.py\"" in workflow
    assert "git show \"$base_ref:tools/check_ai_bad_smells.py\"" in workflow
    assert "python /tmp/seektalent-pr-gates/check_pr_governance.py" in workflow
    assert "python /tmp/seektalent-pr-gates/check_privacy_gate.py" in workflow
    assert "python /tmp/seektalent-pr-gates/check_ai_bad_smells.py" in workflow
