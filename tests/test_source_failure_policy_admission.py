from __future__ import annotations

import ast
from pathlib import Path

import pytest

from seektalent.sources.liepin.reason_codes import (
    LIEPIN_FAILURE_POLICIES,
    public_source_problem_code,
    public_source_problem_message,
)
from seektalent.sources.liepin.runtime_lane import (
    runtime_reason_code_from_worker_failure_code,
)


ROOT = Path(__file__).resolve().parents[1]
LIEPIN_PRODUCTION_PATHS = (
    ROOT / "src/seektalent/providers/liepin",
    ROOT / "src/seektalent/sources/liepin",
)
TYPED_FAILURE_CALLS = {
    "LiepinTransition",
    "LiepinWorkerModeError",
    "LiepinWorkerPartialSearchError",
    "OpenCliBrowserError",
    "OpenCliBrowserResult",
    "ProviderFirstPageExpansionError",
    "ProviderFirstPageExpansionResult",
    "ProviderSearchError",
    "SourceFirstPageExpansionError",
    "TransitionResult",
}
FAILURE_KEYWORDS = {
    "blocked_reason_code",
    "code",
    "reason_code",
    "safe_reason_code",
    "stop_reason_code",
}
REVIEWED_REACHABLE_FAILURE_CAUSES = {
    "liepin_first_page_continuation_cleanup_failed",
    "liepin_first_page_expansion_blocked",
    "liepin_first_page_expansion_partial",
    "liepin_opencli_candidate_identity_mismatch",
    "liepin_opencli_card_extract_failed",
    "liepin_opencli_fill_verification_failed",
    "liepin_opencli_filter_clear_failed",
    "liepin_opencli_filter_option_unavailable",
    "liepin_opencli_search_restore_failed",
    "liepin_protected_artifact_root_missing",
    "liepin_verify_session_gate_missing",
    "requirement_sheet_missing",
}


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _reason_literals(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value} if node.value else set()
    if isinstance(node, ast.BoolOp):
        return set().union(*(_reason_literals(value) for value in node.values))
    if isinstance(node, ast.IfExp):
        return _reason_literals(node.body) | _reason_literals(node.orelse)
    if isinstance(node, ast.Call) and _call_name(node) == "str" and node.args:
        return _reason_literals(node.args[0])
    return set()


def _typed_production_failure_causes() -> set[str]:
    causes: set[str] = set()
    paths = [
        path
        for directory in LIEPIN_PRODUCTION_PATHS
        for path in directory.rglob("*.py")
    ]
    paths.append(ROOT / "src/seektalent/liepin_verify_session_gate.py")
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) in TYPED_FAILURE_CALLS:
                if _call_name(node) == "OpenCliBrowserError" and node.args:
                    causes.update(_reason_literals(node.args[0]))
                for keyword in node.keywords:
                    if keyword.arg in FAILURE_KEYWORDS:
                        causes.update(_reason_literals(keyword.value))
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=False):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "safe_reason_code"
                    ):
                        causes.update(_reason_literals(value))
    return causes


def test_typed_production_failure_causes_are_in_the_canonical_policy() -> None:
    production_causes = _typed_production_failure_causes()

    assert REVIEWED_REACHABLE_FAILURE_CAUSES <= production_causes
    assert production_causes <= LIEPIN_FAILURE_POLICIES.keys()


@pytest.mark.parametrize(
    ("internal_reason", "public_problem"),
    [
        ("liepin_opencli_fill_verification_failed", "source_provider_failed"),
        ("liepin_opencli_filter_option_unavailable", "source_filter_unavailable"),
        ("liepin_opencli_filter_clear_failed", "source_filter_unavailable"),
        ("liepin_opencli_card_extract_failed", "source_provider_failed"),
        ("liepin_opencli_search_restore_failed", "source_partial"),
        (
            "liepin_opencli_candidate_identity_mismatch",
            "source_browser_reference_stale",
        ),
        ("liepin_protected_artifact_root_missing", "source_configuration_invalid"),
        (
            "liepin_verify_session_gate_missing",
            "source_runtime_unavailable",
        ),
        ("liepin_first_page_expansion_blocked", "source_provider_failed"),
        ("liepin_first_page_expansion_partial", "source_partial"),
        (
            "liepin_first_page_continuation_cleanup_failed",
            "source_cleanup_pending",
        ),
        ("requirement_sheet_missing", "source_provider_failed"),
    ],
)
def test_runtime_preserves_the_cause_before_the_canonical_public_projection(
    internal_reason: str,
    public_problem: str,
) -> None:
    runtime_reason = runtime_reason_code_from_worker_failure_code(
        internal_reason
    )

    assert runtime_reason == internal_reason
    assert public_source_problem_code(runtime_reason) == public_problem
    assert public_source_problem_code(internal_reason) == public_problem


def test_host_required_message_accepts_any_liepin_subpage() -> None:
    message = public_source_problem_message(
        "liepin_host_tab_missing",
        source_label="猎聘",
    )

    assert message is not None
    assert "任意 h.liepin.com 页面" in message
    assert "人才搜索页" not in message


def test_ambiguous_host_window_has_an_actionable_public_problem() -> None:
    problem = public_source_problem_code("liepin_host_window_ambiguous")
    message = public_source_problem_message(problem, source_label="猎聘")

    assert problem == "source_browser_window_ambiguous"
    assert message is not None
    assert "只保留一个猎聘窗口" in message


def test_removed_cleanup_config_has_accurate_reclamation_guidance() -> None:
    problem = public_source_problem_code("liepin_opencli_removed_config")
    message = public_source_problem_message(problem, source_label="猎聘")

    assert problem == "source_removed_cleanup_config"
    assert message is not None
    assert "旧" in message
    assert "cleanup" in message
    assert "60 秒" in message


def test_config_invalid_does_not_use_version_mismatch_guidance() -> None:
    assert (
        public_source_problem_code("liepin_opencli_config_invalid")
        == "source_configuration_invalid"
    )
    assert public_source_problem_code(
        "liepin_opencli_config_invalid"
    ) != public_source_problem_code("liepin_opencli_bridge_protocol_mismatch")


def test_ordinary_unknown_message_does_not_claim_reconciliation() -> None:
    problem = public_source_problem_code("failed_internal_error")
    message = public_source_problem_message(problem, source_label="猎聘")

    assert problem == "source_unknown"
    assert message is not None
    assert "核对本次操作状态" not in message
    assert "无法确定具体原因" in message


def test_unknown_runtime_reason_fails_closed_to_the_same_public_problem() -> None:
    internal_reason = "new_private_worker_failure"
    runtime_reason = runtime_reason_code_from_worker_failure_code(
        internal_reason
    )

    assert runtime_reason == internal_reason
    assert public_source_problem_code(runtime_reason) == "source_unknown"
    assert public_source_problem_code(internal_reason) == "source_unknown"
