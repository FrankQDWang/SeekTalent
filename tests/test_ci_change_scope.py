from __future__ import annotations

from tools.ci_change_scope import classify_paths


def test_pull_request_docs_only_skips_expensive_checks() -> None:
    scope = classify_paths(
        [
            "docs/development.md",
            "conversational-agent-runtime-goal-pack/00-index.md",
        ],
        event_name="pull_request",
    )

    assert not scope.python_quality
    assert not scope.workbench_contract


def test_pull_request_python_change_runs_python_quality_only() -> None:
    scope = classify_paths(["src/seektalent/runtime/orchestrator.py"], event_name="pull_request")

    assert scope.python_quality
    assert not scope.workbench_contract


def test_pull_request_workbench_frontend_change_runs_workbench_contract() -> None:
    scope = classify_paths(["apps/web-svelte/src/lib/workbench/viewModels.ts"], event_name="pull_request")

    assert not scope.python_quality
    assert scope.workbench_contract


def test_pull_request_workbench_backend_change_runs_workbench_contract() -> None:
    scope = classify_paths(["src/seektalent_ui/workbench_routes.py"], event_name="pull_request")

    assert scope.python_quality
    assert scope.workbench_contract


def test_pull_request_runtime_control_change_runs_workbench_contract() -> None:
    scope = classify_paths(["src/seektalent_runtime_control/store.py"], event_name="pull_request")

    assert scope.python_quality
    assert scope.workbench_contract


def test_pull_request_dependency_change_runs_all_expensive_checks() -> None:
    scope = classify_paths(["uv.lock"], event_name="pull_request")

    assert scope.python_quality
    assert scope.workbench_contract


def test_pull_request_workflow_change_runs_python_quality() -> None:
    scope = classify_paths([".github/workflows/governance.yml"], event_name="pull_request")

    assert scope.python_quality
    assert not scope.workbench_contract


def test_main_push_runs_all_expensive_checks_even_for_docs() -> None:
    scope = classify_paths(["docs/development.md"], event_name="push")

    assert scope.python_quality
    assert scope.workbench_contract


def test_merge_group_runs_all_expensive_checks_even_for_docs() -> None:
    scope = classify_paths(["docs/development.md"], event_name="merge_group")

    assert scope.python_quality
    assert scope.workbench_contract
