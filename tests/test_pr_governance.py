from __future__ import annotations

import importlib.util
import json
import re
import tomllib
from pathlib import Path

import pytest

from tools.check_pr_governance import (
    LineCountChange,
    MAJOR_REFACTOR_REQUIRED_VERIFICATION_BY_GOAL_ID,
    RUNTIME_PRODUCTION_READINESS_PROMPT_VERIFICATION,
    classify_path,
    evaluate_changed_files,
    is_active_dependency_control_file,
    is_dependency_control_file,
    layer_for_path,
    line_limit_for_path,
    merge_changed_file_sets,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OFFLINE_DISTRIBUTION_GOAL_ID = "offline-distribution-2026-07"
RUNTIME_PRODUCTION_READINESS_GOAL_ID = "runtime-production-readiness-2026-06"


def _module_exists(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _red_zone_review_payload(
    *,
    red_files: list[str],
    verification: list[str] | None = None,
    change_type: str = "refactor",
    summary: str = "Extract runtime graph section helpers without changing response semantics.",
    rationale: str = "The file is oversized and needs a small boundary extraction.",
) -> dict[str, object]:
    return {
        "schema_version": "seektalent.red_zone_change.v1",
        "change_type": change_type,
        "summary": summary,
        "rationale": rationale,
        "red_files": red_files,
        "verification": verification or ["scripts/verify-red-zone.sh"],
    }


def _major_refactor_goal_payload(
    *,
    red_files: list[str],
    goal_id: str = "source-decoupling-2026-06",
    touched_layers: list[str] | None = None,
    verification: list[str] | None = None,
    dependency_files: list[str] | None = None,
    dependency_rationale: str | None = None,
    config_env_files: list[str] | None = None,
    config_behavior_rationale: str | None = None,
    line_count_exemptions: list[str] | None = None,
    line_count_rationale: str | None = None,
) -> dict[str, object]:
    source_verification = [
        "uv run python tools/check_source_boundaries.py",
        "scripts/verify-source-decoupling.sh",
        "scripts/verify-red-zone.sh",
        "scripts/verify-dev-workbench.sh",
        "uv run pytest",
        "cd apps/web-react && pnpm test",
    ]
    bootstrap_verification = [
        "uv run pytest tests/test_pr_governance.py -q",
        "uv run ruff check tools/check_pr_governance.py tests/test_pr_governance.py",
        "uv run ty check tools/check_pr_governance.py tests/test_pr_governance.py",
    ]
    agent_safety_verification = [
        "uv run pytest tests/test_pr_governance.py -q",
        "uv run ruff check tools/check_pr_governance.py tests/test_pr_governance.py",
        "uv run ty check tools/check_pr_governance.py tests/test_pr_governance.py",
        "uv run pytest tests/test_agent_safety_gate.py tests/test_source_boundaries.py -q",
        "uv run python tools/check_agent_safety_gate.py --base origin/main",
        "uv run python tools/check_source_boundaries.py",
    ]
    runtime_control_verification = [
        "uv run pytest tests/test_runtime_control_*.py -q",
        "uv run python tools/check_source_boundaries.py",
        "uv run pytest",
    ]
    react_workbench_verification = [
        "scripts/verify-dev-workbench.sh",
        "uv run python scripts/build_packaged_workbench.py",
        (
            "PYTHONDONTWRITEBYTECODE=1 uv run --group dev python -m pytest "
            "tests/test_workbench_static_frontend.py tests/test_react_workbench_cutover_gate.py "
            "-q -p no:cacheprovider"
        ),
        (
            "PYTHONDONTWRITEBYTECODE=1 uv run --group dev python -m pytest "
            "tests/test_agent_workbench_contract.py -q -p no:cacheprovider"
        ),
        "pnpm --dir apps/web-react check",
        (
            "CI=1 pnpm --dir apps/web-react exec vitest run src/lib/stream/agentStream.test.ts "
            "src/lib/stream/agentStreamReducer.test.ts src/lib/stream/agentStreamView.test.ts --run"
        ),
    ]
    default_verification = (
        list(MAJOR_REFACTOR_REQUIRED_VERIFICATION_BY_GOAL_ID[OFFLINE_DISTRIBUTION_GOAL_ID])
        if goal_id == OFFLINE_DISTRIBUTION_GOAL_ID
        else bootstrap_verification
        if goal_id == "governance-bootstrap-2026-06"
        else agent_safety_verification
        if goal_id == "goal-2-agent-safety-gate-2026-06"
        else runtime_control_verification
        if goal_id == "runtime-control-plane-2026-06"
        else list(MAJOR_REFACTOR_REQUIRED_VERIFICATION_BY_GOAL_ID[RUNTIME_PRODUCTION_READINESS_GOAL_ID])
        if goal_id == RUNTIME_PRODUCTION_READINESS_GOAL_ID
        else react_workbench_verification
        if goal_id == "react-agent-workbench-rebuild-2026-06"
        else source_verification
    )
    payload: dict[str, object] = {
        "schema_version": "seektalent.major_refactor_goal.v1",
        "goal_id": goal_id,
        "change_type": "major_refactor",
        "summary": "Governance-controlled major refactor.",
        "rationale": "The refactor intentionally crosses normal PR guardrails and is covered by explicit verification.",
        "touched_layers": touched_layers
        or ["governance", "runtime", "provider", "sources", "bff", "frontend", "docs", "tests", "other"],
        "red_files": red_files,
        "verification": verification or default_verification,
        "deletion_targets": [
            "runtime CTS/Liepin/OpenCLI source glue",
            "provider imports of runtime source DTOs",
            "stale source-coupled docs",
        ],
        "risks": [
            "Current runtime/provider coupling must be removed before final verification.",
        ],
    }
    if dependency_files is not None:
        payload["dependency_files"] = dependency_files
    if dependency_rationale is not None:
        payload["dependency_rationale"] = dependency_rationale
    if config_env_files is not None:
        payload["config_env_files"] = config_env_files
    if config_behavior_rationale is not None:
        payload["config_behavior_rationale"] = config_behavior_rationale
    if line_count_exemptions is not None:
        payload["line_count_exemptions"] = line_count_exemptions
    if line_count_rationale is not None:
        payload["line_count_rationale"] = line_count_rationale
    return payload


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
    assert "Red-zone refactors require a structured review manifest" in tach_config


def test_runtime_production_readiness_bootstrap_manifest_uses_canonical_verification() -> None:
    manifest_path = (
        PROJECT_ROOT
        / "docs/governance/agent-goals/runtime-production-readiness-bootstrap-2026-06.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["verification"] == list(
        MAJOR_REFACTOR_REQUIRED_VERIFICATION_BY_GOAL_ID[RUNTIME_PRODUCTION_READINESS_GOAL_ID]
    )


def test_classify_path_red_runtime() -> None:
    assert classify_path("src/seektalent/runtime/orchestrator.py") == "red"


def test_classify_path_red_provider_registry() -> None:
    assert classify_path("src/seektalent/providers/registry.py") == "red"


def test_classify_path_red_conversation_agent() -> None:
    assert classify_path("src/seektalent_conversation_agent/service.py") == "red"


def test_classify_path_yellow_workbench_route() -> None:
    assert classify_path("src/seektalent_ui/workbench_routes.py") == "yellow"


def test_classify_path_green_docs() -> None:
    assert classify_path("docs/development.md") == "green"


def test_gitignore_is_governance_layer() -> None:
    assert layer_for_path(".gitignore") == "governance"


def test_dependency_control_files_are_dependency_layer() -> None:
    assert layer_for_path("pyproject.toml") == "dependencies"
    assert layer_for_path("apps/web-react/package.json") == "dependencies"
    assert is_dependency_control_file("requirements-dev.txt")


def test_deleted_app_dependency_control_files_are_not_active_dependency_changes(tmp_path: Path) -> None:
    assert not is_active_dependency_control_file("apps/retired-workbench/package.json", project_root=tmp_path)

    active_package = tmp_path / "apps" / "web-react" / "package.json"
    active_package.parent.mkdir(parents=True)
    active_package.write_text('{"private": true}\n', encoding="utf-8")

    assert is_active_dependency_control_file("apps/web-react/package.json", project_root=tmp_path)


def test_evaluate_changed_files_fails_cross_layer_runtime_and_frontend() -> None:
    result = evaluate_changed_files(
        [
            "src/seektalent/runtime/orchestrator.py",
            "apps/web-react/src/components/workbench/CandidateQueue.tsx",
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
            "apps/web-react/src/components/workbench/CandidateQueue.tsx",
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


def test_evaluate_changed_files_blocks_dependency_control_changes(tmp_path: Path) -> None:
    for path in (
        tmp_path / "apps" / "web-react" / "package.json",
        tmp_path / "apps" / "web-react" / "pnpm-lock.yaml",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")

    result = evaluate_changed_files(
        [
            "pyproject.toml",
            "uv.lock",
            "apps/web-react/package.json",
            "apps/web-react/pnpm-lock.yaml",
        ],
        max_files=15,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert (
        "dependency control files touched: apps/web-react/package.json, apps/web-react/pnpm-lock.yaml, "
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


def test_evaluate_changed_files_allows_config_env_behavior_with_major_refactor_manifest(
    tmp_path: Path,
) -> None:
    manifest_path = "docs/governance/agent-goals/config-runtime-source-decoupling-2026-06.json"
    config_env_files = [".env.example", "src/seektalent/config.py"]
    red_files = [*config_env_files, "src/seektalent/runtime/orchestrator.py"]
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            red_files=red_files,
            touched_layers=["other", "runtime"],
            config_env_files=config_env_files,
            config_behavior_rationale=(
                "The config keys are consumed by the runtime behavior changed in the same source-decoupling pass."
            ),
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            *red_files,
        ],
        max_files=1,
        max_layers=1,
        project_root=tmp_path,
    )

    assert result.ok
    assert any(
        message.startswith("config/env and behavior files touched together:") for message in result.messages
    )


def test_evaluate_changed_files_blocks_major_refactor_config_env_behavior_without_config_review(
    tmp_path: Path,
) -> None:
    manifest_path = "docs/governance/agent-goals/config-runtime-source-decoupling-2026-06.json"
    red_files = [".env.example", "src/seektalent/runtime/orchestrator.py"]
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(red_files=red_files, touched_layers=["runtime"]),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            *red_files,
        ],
        max_files=1,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any("major refactor goal manifest does not cover config/env files" in message for message in result.messages)


def test_evaluate_changed_files_allows_valid_security_remediation_manifest(tmp_path: Path) -> None:
    manifest_path = "docs/security/remediations/2026-05-31-liepin-boundaries.json"
    paths = [
        manifest_path,
        "src/seektalent/providers/liepin/store.py",
        "src/seektalent/providers/liepin/security.py",
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
                    "src/seektalent/providers/liepin/store.py",
                    "src/seektalent/providers/liepin/security.py",
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
        "src/seektalent/providers/liepin/security.py",
        *[f"tests/test_security_remediation_{index}.py" for index in range(14)],
    ]
    manifest = tmp_path / manifest_path
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "seektalent.security_remediation.v1",
                "findings": [{"id": "ST-SEC-002", "title": "Caller-provided account hash approval"}],
                "remediated_files": ["src/seektalent/providers/liepin/security.py"],
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
            "src/seektalent/providers/liepin/security.py",
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


def test_evaluate_changed_files_allows_valid_red_zone_review_manifest(tmp_path: Path) -> None:
    manifest_path = "docs/governance/red-zone/2026-06-01-runtime-graph-sections.json"
    _write_json(
        tmp_path / manifest_path,
        _red_zone_review_payload(
            red_files=["src/seektalent_ui/runtime_graph.py"],
            verification=["scripts/verify-red-zone.sh", "scripts/verify-dev-workbench.sh"],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            "src/seektalent_ui/runtime_graph.py",
            "tests/test_runtime_graph_boundaries.py",
        ],
        max_files=15,
        max_layers=1,
        project_root=tmp_path,
    )

    assert result.ok


def test_evaluate_changed_files_blocks_red_zone_review_missing_red_file(tmp_path: Path) -> None:
    manifest_path = "docs/governance/red-zone/2026-06-01-runtime-graph-sections.json"
    _write_json(
        tmp_path / manifest_path,
        _red_zone_review_payload(red_files=["src/seektalent_ui/runtime_graph.py"]),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            "src/seektalent_ui/runtime_graph.py",
            "src/seektalent_ui/workbench_store.py",
        ],
        max_files=15,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any("red-zone review manifest does not cover red-zone files" in message for message in result.messages)


def test_evaluate_changed_files_blocks_red_zone_review_for_governance_changes(tmp_path: Path) -> None:
    manifest_path = "docs/governance/red-zone/2026-06-01-governance.json"
    _write_json(
        tmp_path / manifest_path,
        _red_zone_review_payload(
            red_files=["tools/check_pr_governance.py"],
            summary="Change PR gate behavior.",
            rationale="Governance changes must not self-approve through red-zone review manifests.",
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            "tools/check_pr_governance.py",
        ],
        max_files=15,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any("red-zone review manifest cannot cover layers: governance" in message for message in result.messages)


def test_evaluate_changed_files_blocks_red_zone_review_without_red_zone_verification(tmp_path: Path) -> None:
    manifest_path = "docs/governance/red-zone/2026-06-01-runtime-graph-sections.json"
    _write_json(
        tmp_path / manifest_path,
        _red_zone_review_payload(
            red_files=["src/seektalent_ui/runtime_graph.py"],
            verification=["uv run pytest tests/test_runtime_graph_boundaries.py"],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            "src/seektalent_ui/runtime_graph.py",
        ],
        max_files=15,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any("red-zone review manifest must include scripts/verify-red-zone.sh" in message for message in result.messages)


def test_evaluate_changed_files_does_not_count_red_zone_review_manifest_against_file_budget(tmp_path: Path) -> None:
    manifest_path = "docs/governance/red-zone/2026-06-01-runtime-graph-sections.json"
    _write_json(
        tmp_path / manifest_path,
        _red_zone_review_payload(red_files=["src/seektalent_ui/runtime_graph.py"]),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            "src/seektalent_ui/runtime_graph.py",
        ],
        max_files=1,
        max_layers=1,
        project_root=tmp_path,
    )

    assert result.ok


def test_evaluate_changed_files_allows_source_decoupling_major_refactor_manifest(tmp_path: Path) -> None:
    manifest_path = "docs/governance/agent-goals/source-decoupling-2026-06.json"
    red_files = [
        "tools/check_pr_governance.py",
        "tools/check_source_boundaries.py",
        "src/seektalent/runtime/orchestrator.py",
        "src/seektalent/providers/registry.py",
        "src/seektalent/providers/liepin/filter_compiler.py",
        "scripts/verify-red-zone.sh",
    ]
    _write_json(tmp_path / manifest_path, _major_refactor_goal_payload(red_files=red_files))

    result = evaluate_changed_files(
        [
            manifest_path,
            *red_files,
            "scripts/verify-source-decoupling.sh",
            "tach.toml",
            "src/seektalent/sources/contracts.py",
            "src/seektalent_ui/workbench_routes.py",
            "apps/web-react/src/lib/stream/agentStream.ts",
            "apps/web-react/src/components/workbench/CandidateQueue.tsx",
            "docs/architecture.md",
            "docs/source-contracts.md",
            "tests/test_source_boundaries.py",
            "tests/test_runtime_source_lanes.py",
            "tests/test_liepin_provider_adapter.py",
            "tests/test_workbench_api.py",
        ],
        max_files=3,
        max_layers=1,
        project_root=tmp_path,
    )

    assert result.ok


def test_evaluate_changed_files_blocks_missing_major_refactor_manifest(tmp_path: Path) -> None:
    manifest_path = "docs/governance/agent-goals/deleted-major-refactor-2026-06.json"

    result = evaluate_changed_files(
        [
            manifest_path,
            "docs/governance/workbench-playbook.md",
        ],
        max_files=15,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert f"major refactor goal manifest missing: {manifest_path}" in result.messages


def test_evaluate_changed_files_allows_deleted_stale_major_refactor_manifests_when_active_goal_exists(
    tmp_path: Path,
) -> None:
    manifest_path = "docs/governance/agent-goals/react-agent-workbench-rebuild-2026-06.json"
    deleted_manifest_path = "docs/governance/agent-goals/source-decoupling-2026-06.json"
    red_files = ["tools/check_pr_governance.py"]
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            goal_id="react-agent-workbench-rebuild-2026-06",
            red_files=red_files,
            touched_layers=["frontend", "governance", "docs"],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            deleted_manifest_path,
            *red_files,
            "apps/web-react/src/workbench/file_1.tsx",
            "docs/old-goal-pack.md",
        ],
        deleted_paths=[deleted_manifest_path],
        max_layers=1,
        project_root=tmp_path,
    )

    assert result.ok
    assert not any("major refactor goal manifest missing" in message for message in result.messages)
    assert not any("only one major refactor goal manifest is allowed" in message for message in result.messages)


def test_evaluate_changed_files_blocks_major_refactor_manifest_missing_source_verification(
    tmp_path: Path,
) -> None:
    manifest_path = "docs/governance/agent-goals/source-decoupling-2026-06.json"
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            red_files=["tools/check_pr_governance.py"],
            verification=["scripts/verify-red-zone.sh", "uv run pytest"],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            "tools/check_pr_governance.py",
            "src/seektalent/runtime/orchestrator.py",
        ],
        max_files=15,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any("major refactor goal manifest must include verification" in message for message in result.messages)


def test_evaluate_changed_files_blocks_major_refactor_manifest_missing_red_file(tmp_path: Path) -> None:
    manifest_path = "docs/governance/agent-goals/source-decoupling-2026-06.json"
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(red_files=["tools/check_pr_governance.py"]),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            "tools/check_pr_governance.py",
            "src/seektalent/runtime/orchestrator.py",
        ],
        max_files=15,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any("major refactor goal manifest does not cover red-zone files" in message for message in result.messages)


def test_evaluate_changed_files_blocks_major_refactor_manifest_stale_red_file(tmp_path: Path) -> None:
    manifest_path = "docs/governance/agent-goals/source-decoupling-2026-06.json"
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            red_files=[
                "tools/check_pr_governance.py",
                "src/seektalent/runtime/orchestrator.py",
            ],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            "tools/check_pr_governance.py",
        ],
        max_files=15,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any("major refactor goal manifest references unchanged red-zone files" in message for message in result.messages)


def test_evaluate_changed_files_blocks_dependency_files_in_major_refactor_without_rationale(
    tmp_path: Path,
) -> None:
    manifest_path = "docs/governance/agent-goals/source-decoupling-2026-06.json"
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            red_files=["tools/check_pr_governance.py"],
            dependency_files=["pyproject.toml"],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            "tools/check_pr_governance.py",
            "pyproject.toml",
        ],
        max_files=15,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any("major refactor goal manifest must explain dependency files" in message for message in result.messages)


def test_evaluate_changed_files_allows_major_refactor_line_count_exemptions(tmp_path: Path) -> None:
    manifest_path = "docs/governance/agent-goals/source-decoupling-2026-06.json"
    exempt_paths = [
        "src/seektalent/runtime/source_lanes.py",
        "tests/test_runtime_source_lanes.py",
    ]
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            red_files=[
                "src/seektalent/runtime/source_lanes.py",
                "tools/check_pr_governance.py",
            ],
            line_count_exemptions=exempt_paths,
            line_count_rationale="These pre-existing oversized files are touched surgically during source decoupling.",
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            *exempt_paths,
            "tools/check_pr_governance.py",
        ],
        line_changes=[
            LineCountChange("src/seektalent/runtime/source_lanes.py", base_lines=1317, head_lines=1341),
            LineCountChange("tests/test_runtime_source_lanes.py", base_lines=2003, head_lines=2004),
            LineCountChange("tools/check_pr_governance.py", base_lines=771, head_lines=771),
        ],
        max_files=1,
        max_layers=1,
        project_root=tmp_path,
    )

    assert result.ok


def test_evaluate_changed_files_allows_governance_bootstrap_major_refactor_manifest(tmp_path: Path) -> None:
    manifest_path = "docs/governance/agent-goals/governance-bootstrap-2026-06.json"
    red_files = [
        ".github/workflows/ci.yml",
        "tools/check_pr_governance.py",
    ]
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            goal_id="governance-bootstrap-2026-06",
            red_files=red_files,
            touched_layers=["governance", "docs", "tests"],
            line_count_exemptions=["tools/check_pr_governance.py"],
            line_count_rationale="The bootstrap keeps the existing gate stable while adding explicit manifest parsing.",
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            *red_files,
            "tests/test_pr_governance.py",
        ],
        line_changes=[LineCountChange("tools/check_pr_governance.py", base_lines=594, head_lines=700)],
        project_root=tmp_path,
    )

    assert result.ok


def test_evaluate_changed_files_allows_offline_distribution_major_refactor_manifest(
    tmp_path: Path,
) -> None:
    manifest_path = "docs/governance/agent-goals/offline-distribution-2026-07.json"
    red_files = [
        ".github/workflows/build-macos-intel-offline.yml",
        "tools/check_pr_governance.py",
    ]
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            goal_id=OFFLINE_DISTRIBUTION_GOAL_ID,
            red_files=red_files,
            touched_layers=["governance", "docs", "tests", "other"],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            *red_files,
            "scripts/build_offline_macos_intel.py",
            "tests/test_build_offline_macos_intel.py",
            "tests/test_pr_governance.py",
            "docs/references/offline-distribution.md",
            "CONTEXT.md",
        ],
        project_root=tmp_path,
    )

    assert result.ok


def test_evaluate_changed_files_allows_goal_2_agent_safety_gate_manifest(tmp_path: Path) -> None:
    manifest_path = "docs/governance/agent-goals/goal-2-agent-safety-gate-2026-06.json"
    red_files = [
        ".github/CODEOWNERS",
        ".github/dependabot.yml",
        ".github/workflows/ci.yml",
        ".github/workflows/codeql.yml",
        "tools/check_agent_safety_gate.py",
        "tools/check_pr_governance.py",
        "tools/check_source_boundaries.py",
    ]
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            goal_id="goal-2-agent-safety-gate-2026-06",
            red_files=red_files,
            touched_layers=["governance", "docs", "tests", "other"],
            line_count_exemptions=[
                "tools/check_agent_safety_gate.py",
                "tools/check_pr_governance.py",
                "tests/test_pr_governance.py",
            ],
            line_count_rationale=(
                "The Agent safety checker is a new diff gate with focused rules and tests; "
                "the existing governance checker and its tests are already oversized and receive "
                "only focused red-zone and manifest coverage in this change."
            ),
            verification=[
                "uv run pytest tests/test_pr_governance.py -q",
                "uv run ruff check tools/check_pr_governance.py tests/test_pr_governance.py",
                "uv run ty check tools/check_pr_governance.py tests/test_pr_governance.py",
                "uv run pytest tests/test_agent_safety_gate.py tests/test_source_boundaries.py -q",
                "uv run python tools/check_agent_safety_gate.py --base origin/main",
                "uv run python tools/check_source_boundaries.py",
            ],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            *red_files,
            "tests/test_agent_safety_gate.py",
            "tests/test_pr_governance.py",
            "tests/test_source_boundaries.py",
            "docs/governance/goal-2-agent-safety-gate.md",
        ],
        line_changes=[
            LineCountChange("tools/check_agent_safety_gate.py", base_lines=0, head_lines=220),
            LineCountChange("tools/check_pr_governance.py", base_lines=891, head_lines=910),
            LineCountChange("tests/test_pr_governance.py", base_lines=1043, head_lines=1085),
        ],
        project_root=tmp_path,
    )

    assert result.ok


def test_evaluate_changed_files_allows_runtime_control_major_refactor_manifest(tmp_path: Path) -> None:
    manifest_path = "docs/governance/agent-goals/runtime-control-plane-2026-06.json"
    red_files = [
        "src/seektalent/config.py",
        "tools/check_pr_governance.py",
        "tools/check_source_boundaries.py",
    ]
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            goal_id="runtime-control-plane-2026-06",
            red_files=red_files,
            touched_layers=["dependencies", "governance", "other"],
            dependency_files=["pyproject.toml"],
            dependency_rationale="The runtime-control package is added to the build backend module list.",
            line_count_exemptions=[
                "src/seektalent/config.py",
                "src/seektalent_runtime_control/store.py",
                "tests/test_pr_governance.py",
                "tools/check_pr_governance.py",
            ],
            line_count_rationale=(
                "The store owns the initial SQLite runtime-control schema and transaction surface; "
                "config.py was already oversized and receives only path/retention settings; "
                "the governance files are already oversized and receive focused goal-id coverage."
            ),
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            *red_files,
            "pyproject.toml",
            "src/seektalent_runtime_control/store.py",
            "src/seektalent_runtime_control/models.py",
            "tests/test_pr_governance.py",
            "tools/check_pr_governance.py",
        ],
        line_changes=[
            LineCountChange("src/seektalent/config.py", base_lines=760, head_lines=779),
            LineCountChange("src/seektalent_runtime_control/store.py", base_lines=None, head_lines=1682),
            LineCountChange("tests/test_pr_governance.py", base_lines=981, head_lines=1036),
            LineCountChange("tools/check_pr_governance.py", base_lines=886, head_lines=891),
        ],
        max_files=15,
        max_layers=1,
        project_root=tmp_path,
    )

    assert result.ok


def test_evaluate_changed_files_allows_runtime_production_readiness_goal_id(tmp_path: Path) -> None:
    manifest_path = "docs/governance/agent-goals/runtime-production-readiness-bootstrap-2026-06.json"
    red_files = ["tools/check_pr_governance.py"]
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            goal_id=RUNTIME_PRODUCTION_READINESS_GOAL_ID,
            red_files=red_files,
            touched_layers=["docs", "governance", "tests"],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            "docs/governance/runtime-production-readiness-matrix.md",
            *red_files,
            "tests/test_pr_governance.py",
        ],
        max_files=1,
        max_layers=1,
        project_root=tmp_path,
    )

    assert result.ok


def test_evaluate_changed_files_allows_prompt_layer_major_refactor_manifest(tmp_path: Path) -> None:
    manifest_path = "docs/governance/agent-goals/runtime-production-readiness-prompt-2026-06.json"
    red_files = ["src/seektalent/prompts/source_planning.py"]
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            goal_id=RUNTIME_PRODUCTION_READINESS_GOAL_ID,
            red_files=red_files,
            touched_layers=["prompts"],
            verification=[
                *MAJOR_REFACTOR_REQUIRED_VERIFICATION_BY_GOAL_ID[
                    RUNTIME_PRODUCTION_READINESS_GOAL_ID
                ],
                RUNTIME_PRODUCTION_READINESS_PROMPT_VERIFICATION,
            ],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            *red_files,
        ],
        max_files=1,
        max_layers=1,
        project_root=tmp_path,
    )

    assert result.ok


def test_evaluate_changed_files_rejects_unsupported_informal_major_refactor_layers(
    tmp_path: Path,
) -> None:
    manifest_path = "docs/governance/agent-goals/runtime-production-readiness-bootstrap-2026-06.json"
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            goal_id=RUNTIME_PRODUCTION_READINESS_GOAL_ID,
            red_files=["tools/check_pr_governance.py"],
            touched_layers=["api", "settings", "ui", "cli", "governance"],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            "tools/check_pr_governance.py",
        ],
        max_files=1,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any(
        "major refactor goal manifest cannot cover layers: api, cli, settings, ui" in message
        for message in result.messages
    )


@pytest.mark.parametrize(
    "missing_command",
    MAJOR_REFACTOR_REQUIRED_VERIFICATION_BY_GOAL_ID[RUNTIME_PRODUCTION_READINESS_GOAL_ID],
)
def test_evaluate_changed_files_enforces_runtime_production_readiness_full_gate(
    tmp_path: Path,
    missing_command: str,
) -> None:
    manifest_path = "docs/governance/agent-goals/runtime-production-readiness-bootstrap-2026-06.json"
    verification = [
        command
        for command in MAJOR_REFACTOR_REQUIRED_VERIFICATION_BY_GOAL_ID[
            RUNTIME_PRODUCTION_READINESS_GOAL_ID
        ]
        if command != missing_command
    ]
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            goal_id=RUNTIME_PRODUCTION_READINESS_GOAL_ID,
            red_files=["tools/check_pr_governance.py"],
            touched_layers=["docs", "governance", "tests"],
            verification=verification,
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            "docs/governance/runtime-production-readiness-matrix.md",
            "tools/check_pr_governance.py",
            "tests/test_pr_governance.py",
        ],
        max_files=1,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any(
        f"major refactor goal manifest must include verification `{missing_command}`" in message
        for message in result.messages
    )


def test_evaluate_changed_files_enforces_runtime_production_readiness_file_budget(
    tmp_path: Path,
) -> None:
    manifest_path = "docs/governance/agent-goals/runtime-production-readiness-bootstrap-2026-06.json"
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            goal_id=RUNTIME_PRODUCTION_READINESS_GOAL_ID,
            red_files=["tools/check_pr_governance.py"],
            touched_layers=["docs", "governance", "tests"],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            "tools/check_pr_governance.py",
            *[f"docs/governance/runtime-readiness-{index}.md" for index in range(60)],
        ],
        max_files=1,
        max_major_refactor_files=5,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any("major refactor changes too many non-generated files: 61 > 60" in message for message in result.messages)


def test_evaluate_changed_files_blocks_major_refactor_without_deletion_targets(
    tmp_path: Path,
) -> None:
    manifest_path = "docs/governance/agent-goals/runtime-production-readiness-bootstrap-2026-06.json"
    payload = _major_refactor_goal_payload(
        goal_id=RUNTIME_PRODUCTION_READINESS_GOAL_ID,
        red_files=["tools/check_pr_governance.py"],
        touched_layers=["docs", "governance", "tests"],
    )
    del payload["deletion_targets"]
    _write_json(tmp_path / manifest_path, payload)

    result = evaluate_changed_files(
        [
            manifest_path,
            "docs/governance/runtime-production-readiness-matrix.md",
            "tools/check_pr_governance.py",
            "tests/test_pr_governance.py",
        ],
        max_files=1,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any("major refactor goal manifest must list deletion_targets" in message for message in result.messages)


def test_evaluate_changed_files_blocks_major_refactor_without_risks(tmp_path: Path) -> None:
    manifest_path = "docs/governance/agent-goals/runtime-production-readiness-bootstrap-2026-06.json"
    payload = _major_refactor_goal_payload(
        goal_id=RUNTIME_PRODUCTION_READINESS_GOAL_ID,
        red_files=["tools/check_pr_governance.py"],
        touched_layers=["docs", "governance", "tests"],
    )
    del payload["risks"]
    _write_json(tmp_path / manifest_path, payload)

    result = evaluate_changed_files(
        [
            manifest_path,
            "docs/governance/runtime-production-readiness-matrix.md",
            "tools/check_pr_governance.py",
            "tests/test_pr_governance.py",
        ],
        max_files=1,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any("major refactor goal manifest must list risks" in message for message in result.messages)


def test_evaluate_changed_files_allows_runtime_production_readiness_prompt_only_diff(
    tmp_path: Path,
) -> None:
    manifest_path = "docs/governance/agent-goals/runtime-production-readiness-prompt-2026-06.json"
    red_files = ["src/seektalent/prompts/runtime_contract.py"]
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            goal_id=RUNTIME_PRODUCTION_READINESS_GOAL_ID,
            red_files=red_files,
            touched_layers=["prompts"],
            verification=[
                *MAJOR_REFACTOR_REQUIRED_VERIFICATION_BY_GOAL_ID[
                    RUNTIME_PRODUCTION_READINESS_GOAL_ID
                ],
                RUNTIME_PRODUCTION_READINESS_PROMPT_VERIFICATION,
            ],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            *red_files,
        ],
        max_files=1,
        max_layers=1,
        project_root=tmp_path,
    )

    assert result.ok


def test_evaluate_changed_files_requires_prompt_safety_gate_for_runtime_readiness_prompt_diff(
    tmp_path: Path,
) -> None:
    manifest_path = "docs/governance/agent-goals/runtime-production-readiness-prompt-2026-06.json"
    red_files = ["src/seektalent/prompts/runtime_contract.py"]
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            goal_id=RUNTIME_PRODUCTION_READINESS_GOAL_ID,
            red_files=red_files,
            touched_layers=["prompts"],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            *red_files,
        ],
        max_files=1,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any(
        f"major refactor goal manifest must include verification `{RUNTIME_PRODUCTION_READINESS_PROMPT_VERIFICATION}`"
        in message
        for message in result.messages
    )


def test_evaluate_changed_files_requires_prompt_safety_gate_for_any_major_refactor_prompt_diff(
    tmp_path: Path,
) -> None:
    manifest_path = "docs/governance/agent-goals/source-decoupling-prompts-2026-06.json"
    red_files = ["src/seektalent/prompts/controller.md"]
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            goal_id="source-decoupling-2026-06",
            red_files=red_files,
            touched_layers=["prompts"],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            *red_files,
        ],
        max_files=1,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any(
        f"major refactor goal manifest must include verification `{RUNTIME_PRODUCTION_READINESS_PROMPT_VERIFICATION}`"
        in message
        for message in result.messages
    )


def test_evaluate_changed_files_blocks_runtime_production_readiness_prompt_runtime_diff(
    tmp_path: Path,
) -> None:
    manifest_path = "docs/governance/agent-goals/runtime-production-readiness-bootstrap-2026-06.json"
    red_files = [
        "src/seektalent/prompts/runtime_contract.py",
        "src/seektalent/runtime/orchestrator.py",
    ]
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            goal_id=RUNTIME_PRODUCTION_READINESS_GOAL_ID,
            red_files=red_files,
            touched_layers=["prompts", "runtime"],
            verification=[
                *MAJOR_REFACTOR_REQUIRED_VERIFICATION_BY_GOAL_ID[
                    RUNTIME_PRODUCTION_READINESS_GOAL_ID
                ],
                RUNTIME_PRODUCTION_READINESS_PROMPT_VERIFICATION,
            ],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            *red_files,
        ],
        max_files=1,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert (
        "prompt and runtime files touched together: "
        "src/seektalent/prompts/runtime_contract.py, src/seektalent/runtime/orchestrator.py"
    ) in result.messages


def test_evaluate_changed_files_blocks_major_refactor_line_count_exemption_without_rationale(
    tmp_path: Path,
) -> None:
    manifest_path = "docs/governance/agent-goals/source-decoupling-2026-06.json"
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            red_files=["tools/check_pr_governance.py"],
            line_count_exemptions=["tools/check_pr_governance.py"],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            "tools/check_pr_governance.py",
        ],
        line_changes=[LineCountChange("tools/check_pr_governance.py", base_lines=771, head_lines=771)],
        max_files=15,
        max_layers=1,
        project_root=tmp_path,
    )

    assert not result.ok
    assert any("major refactor goal manifest must explain line count exemptions" in message for message in result.messages)


def test_evaluate_changed_files_fails_too_many_non_generated_files() -> None:
    result = evaluate_changed_files(
        [f"docs/file_{index}.md" for index in range(31)],
    )

    assert not result.ok
    assert "too many non-generated files changed: 31 > 30" in result.messages[0]


def test_evaluate_changed_files_allows_default_file_budget() -> None:
    result = evaluate_changed_files([f"docs/file_{index}.md" for index in range(30)])

    assert result.ok


def test_evaluate_changed_files_blocks_major_refactor_over_soft_file_budget(tmp_path: Path) -> None:
    manifest_path = "docs/governance/agent-goals/governance-bootstrap-2026-06.json"
    red_files = ["tools/check_pr_governance.py"]
    extra_files = [f"docs/governance/file_{index}.md" for index in range(60)]
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            goal_id="governance-bootstrap-2026-06",
            red_files=red_files,
            touched_layers=["governance", "docs"],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            *red_files,
            *extra_files,
        ],
        project_root=tmp_path,
    )

    assert not result.ok
    assert any("major refactor changes too many non-generated files: 61 > 60" in message for message in result.messages)


def test_evaluate_changed_files_allows_react_workbench_major_refactor_file_budget(tmp_path: Path) -> None:
    manifest_path = "docs/governance/agent-goals/react-agent-workbench-rebuild-2026-06.json"
    red_files = ["tools/check_pr_governance.py"]
    extra_files = [f"apps/web-react/src/workbench/file_{index}.tsx" for index in range(499)]
    _write_json(
        tmp_path / manifest_path,
        _major_refactor_goal_payload(
            goal_id="react-agent-workbench-rebuild-2026-06",
            red_files=red_files,
            touched_layers=["frontend", "governance"],
        ),
    )

    result = evaluate_changed_files(
        [
            manifest_path,
            *red_files,
            *extra_files,
        ],
        max_layers=1,
        project_root=tmp_path,
    )

    assert result.ok


def test_evaluate_changed_files_ignores_generated_schema() -> None:
    result = evaluate_changed_files(
        ["apps/web-react/src/lib/api/schema.d.ts"],
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
    assert line_limit_for_path("src/seektalent/runtime/new_module.py") == 2500
    assert line_limit_for_path("tests/test_runtime_state_flow.py") == 5000
    assert line_limit_for_path("docs/development.md") is None


def test_evaluate_changed_files_blocks_new_production_file_over_line_limit() -> None:
    result = evaluate_changed_files(
        ["src/seektalent/runtime/new_module.py"],
        line_changes=[LineCountChange("src/seektalent/runtime/new_module.py", base_lines=None, head_lines=2501)],
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
        ["apps/web-react/src/lib/api/schema.d.ts.tmp"],
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
    workflow = (PROJECT_ROOT / ".github/workflows/governance.yml").read_text(encoding="utf-8")

    assert "PULL_REQUEST_BASE_SHA" in workflow
    assert "git show \"$BASE_REF:tools/check_pr_governance.py\"" in workflow
    assert "git show \"$BASE_REF:tools/check_privacy_gate.py\"" in workflow
    assert "git show \"$BASE_REF:tools/check_ai_bad_smells.py\"" in workflow
    assert "python /tmp/seektalent-pr-gates/check_pr_governance.py" in workflow
    assert "python /tmp/seektalent-pr-gates/check_privacy_gate.py" in workflow
    assert "python /tmp/seektalent-pr-gates/check_ai_bad_smells.py" in workflow


def test_ci_pr_governance_runs_agent_safety_gate() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/governance.yml").read_text(encoding="utf-8")

    assert "git show \"$BASE_REF:tools/check_agent_safety_gate.py\"" in workflow
    assert "AGENT_SAFETY_GATE=$gate_dir/check_agent_safety_gate.py" in workflow
    assert "AGENT_SAFETY_GATE=tools/check_agent_safety_gate.py" in workflow
    assert "Run Agent safety gate" in workflow
    assert "python \"$AGENT_SAFETY_GATE\" --base" in workflow


def test_ci_pr_governance_bootstrap_requires_label_and_proposed_gate() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/governance.yml").read_text(encoding="utf-8")

    assert "governance-bootstrap" in workflow
    assert "Base governance failed; validating proposed governance gate." in workflow
    assert "python tools/check_pr_governance.py --base" in workflow
    assert "Release\\ *" not in workflow


def test_ci_workflows_resolve_merge_group_base_before_pull_request_base() -> None:
    for workflow_path in (
        ".github/workflows/governance.yml",
        ".github/workflows/workbench-contract.yml",
        ".github/workflows/python-quality.yml",
    ):
        workflow = (PROJECT_ROOT / workflow_path).read_text(encoding="utf-8")

        assert (
            'for candidate in "$MERGE_GROUP_BASE_SHA" "$PULL_REQUEST_BASE_SHA" '
            '"$PUSH_BEFORE_SHA" "origin/${GITHUB_BASE_REF:-main}" "origin/main"; do'
        ) in workflow


def test_ci_ty_check_covers_governance_tools() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/python-quality.yml").read_text(encoding="utf-8")

    assert "uv run --group dev ty check src tests tools" in workflow


def test_verify_red_zone_runs_source_decoupling_gate() -> None:
    script = (PROJECT_ROOT / "scripts/verify-red-zone.sh").read_text(encoding="utf-8")

    assert "scripts/verify-source-decoupling.sh" in script
