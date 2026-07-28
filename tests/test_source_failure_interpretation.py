from __future__ import annotations

from pathlib import Path

import pytest

from seektalent.source_contracts.liepin_reason_codes import (
    LIEPIN_WORKER_SAFE_REASON_CODES,
)
from seektalent.source_contracts.safe_serialization import sanitize_reason_code
from seektalent.sources.liepin.reason_codes import (
    LIEPIN_FAILURE_POLICIES,
    PUBLIC_SOURCE_PROBLEMS,
    interpret_liepin_failure,
    public_source_problem_code,
    public_source_problem_message,
)


ROOT = Path(__file__).resolve().parents[1]


def test_every_liepin_worker_reason_has_exactly_one_canonical_policy_entry() -> None:
    assert LIEPIN_WORKER_SAFE_REASON_CODES <= LIEPIN_FAILURE_POLICIES.keys()
    assert len(LIEPIN_FAILURE_POLICIES) == len(set(LIEPIN_FAILURE_POLICIES))


def test_every_public_problem_is_registered_and_has_an_explicit_message_boundary() -> None:
    policy_problem_codes = {
        policy.public_problem_code for policy in LIEPIN_FAILURE_POLICIES.values()
    }

    assert policy_problem_codes <= PUBLIC_SOURCE_PROBLEMS.keys()
    for problem_code, problem in PUBLIC_SOURCE_PROBLEMS.items():
        assert sanitize_reason_code(problem_code) == problem_code
        message = public_source_problem_message(problem_code, source_label="猎聘")
        if problem.user_facing:
            assert message is not None
            assert message.strip()
            assert problem_code not in message
        else:
            assert message is None

    assert {
        code for code, problem in PUBLIC_SOURCE_PROBLEMS.items() if not problem.user_facing
    } == {
        "job_lease_expired",
        "relay_pending_worker",
        "source_filter_applied",
    }


@pytest.mark.parametrize(
    (
        "internal_reason",
        "public_problem_code",
        "source_operation_disposition",
        "user_action_code",
    ),
    [
        ("liepin_host_tab_missing", "source_browser_host_required", "user_action_required", "open_liepin_host"),
        ("liepin_opencli_login_required", "source_login_required", "user_action_required", "log_in_to_liepin"),
        ("liepin_browser_account_mismatch", "source_account_mismatch", "incompatible", None),
        (
            "liepin_opencli_identity_intercept",
            "source_identity_confirmation_required",
            "user_action_required",
            "complete_identity_check",
        ),
        (
            "liepin_opencli_risk_page",
            "source_risk_or_verification_required",
            "user_action_required",
            "complete_liepin_risk_check",
        ),
        (
            "liepin_opencli_unknown_modal",
            "source_browser_interaction_required",
            "user_action_required",
            "resolve_liepin_modal",
        ),
        (
            "liepin_opencli_extension_disconnected",
            "source_browser_extension_disconnected",
            "failed",
            None,
        ),
        (
            "liepin_opencli_bridge_protocol_mismatch",
            "source_browser_backend_incompatible",
            "incompatible",
            None,
        ),
        (
            "liepin_opencli_bridge_integrity_failed",
            "source_browser_installation_invalid",
            "incompatible",
            None,
        ),
        ("liepin_opencli_daemon_not_running", "source_browser_backend_unavailable", "failed", None),
        ("liepin_opencli_timeout", "source_browser_timeout", "failed", None),
        ("liepin_opencli_search_not_ready", "source_browser_timeout", "failed", None),
        ("liepin_opencli_stale_ref", "source_browser_reference_stale", "failed", None),
        ("liepin_opencli_filter_unapplied", "source_filter_unavailable", "incompatible", None),
        ("liepin_opencli_budget_exhausted", "source_budget_exhausted", "partial", None),
        ("liepin_opencli_forbidden_command", "source_browser_policy_blocked", "incompatible", None),
        ("liepin_opencli_selector_not_found", "source_provider_failed", "failed", None),
        ("cancelled_by_user", "source_cancelled", "cancelled", None),
    ],
)
def test_representative_liepin_failures_have_stable_interpretations(
    internal_reason: str,
    public_problem_code: str,
    source_operation_disposition: str,
    user_action_code: str | None,
) -> None:
    interpretation = interpret_liepin_failure(
        internal_reason,
        operation="search",
        affected_scope_ref="scope-ref",
    )

    assert interpretation.internal_reason == internal_reason
    assert interpretation.public_problem_code == public_problem_code
    assert interpretation.source_operation_disposition == source_operation_disposition
    assert (
        interpretation.user_action.code if interpretation.user_action is not None else None
    ) == user_action_code


def test_partial_timeout_preserves_partial_result_semantics() -> None:
    interpretation = interpret_liepin_failure(
        "liepin_opencli_timeout",
        operation="search",
        cards_collected=True,
    )

    assert interpretation.public_problem_code == "source_browser_timeout"
    assert interpretation.source_operation_disposition == "partial"
    assert interpretation.source_lane_reason_code == "partial_timeout"


def test_transport_unknown_requires_reconciliation_without_authorizing_retry() -> None:
    interpretation = interpret_liepin_failure(
        "new_private_transport_reason",
        operation="search",
        effect_unknown=True,
    )

    assert interpretation.public_problem_code == "source_unknown"
    assert interpretation.source_operation_disposition == "reconciliation_unknown"
    assert not hasattr(interpretation, "retry_posture")
    assert not hasattr(interpretation, "safe_retry")


def test_unknown_internal_reason_fails_closed_without_leaking_raw_reason() -> None:
    internal_reason = "liepin_private_selector_dump_x9"
    interpretation = interpret_liepin_failure(internal_reason, operation="search")

    assert interpretation.internal_reason == internal_reason
    assert interpretation.public_problem_code == "source_unknown"
    assert interpretation.source_lane_reason_code == "failed_internal_error"
    assert interpretation.user_action is None
    assert public_source_problem_code(internal_reason) == "source_unknown"
    assert internal_reason not in public_source_problem_message(
        interpretation.public_problem_code,
        source_label="猎聘",
    )


@pytest.mark.parametrize(
    ("reason_code", "expected"),
    [
        ("source_filter_unsupported", "source_filter_unsupported"),
        ("source_age_filter_unsupported", "source_filter_unsupported"),
        ("source_location_filter_unsupported", "source_filter_unsupported"),
        ("liepin_opencli_filter_unapplied", "source_filter_unavailable"),
    ],
)
def test_filter_unavailable_and_unsupported_remain_distinct(
    reason_code: str,
    expected: str,
) -> None:
    assert public_source_problem_code(reason_code) == expected


@pytest.mark.parametrize(
    ("internal_reason", "expected_public_problem"),
    [
        ("blocked_login_required", "source_login_required"),
        ("blocked_backend_unavailable", "source_browser_backend_unavailable"),
        ("blocked_compliance", "source_risk_or_verification_required"),
        ("failed_provider_error", "source_provider_failed"),
        ("partial_timeout", "source_browser_timeout"),
        ("partial_budget_exhausted", "source_budget_exhausted"),
        ("cancelled_by_user", "source_cancelled"),
        ("failed_internal_error", "source_unknown"),
    ],
)
def test_source_lane_serialization_uses_the_same_public_problem(
    internal_reason: str,
    expected_public_problem: str,
) -> None:
    assert sanitize_reason_code(internal_reason) == expected_public_problem


@pytest.mark.parametrize(
    "internal_reason",
    [
        "liepin_opencli_search_not_ready",
        "liepin_opencli_search_input_unapplied",
    ],
)
def test_search_readiness_never_reports_browser_bridge_unavailable(
    internal_reason: str,
) -> None:
    public_problem = public_source_problem_code(internal_reason)
    message = public_source_problem_message(public_problem, source_label="猎聘")

    assert public_problem == "source_browser_timeout"
    assert message is not None
    assert "浏览器桥" not in message


@pytest.mark.parametrize(
    ("internal_reason", "expected_public_problem"),
    [
        ("liepin_opencli_login_required", "source_login_required"),
        ("liepin_browser_account_mismatch", "source_account_mismatch"),
        (
            "liepin_opencli_extension_disconnected",
            "source_browser_extension_disconnected",
        ),
        (
            "liepin_opencli_daemon_not_running",
            "source_browser_backend_unavailable",
        ),
        ("liepin_opencli_search_not_ready", "source_browser_timeout"),
        ("liepin_opencli_stale_ref", "source_browser_reference_stale"),
        (
            "liepin_opencli_risk_page",
            "source_risk_or_verification_required",
        ),
        (
            "liepin_opencli_unknown_modal",
            "source_browser_interaction_required",
        ),
        ("liepin_opencli_filter_unapplied", "source_filter_unavailable"),
        ("source_filter_unsupported", "source_filter_unsupported"),
        ("liepin_opencli_budget_exhausted", "source_budget_exhausted"),
        ("new_private_failure_reason", "source_unknown"),
    ],
)
def test_internal_reason_projects_consistently_through_all_user_surfaces(
    internal_reason: str,
    expected_public_problem: str,
) -> None:
    from seektalent import cli
    from seektalent.progress import ProgressEvent
    from seektalent.runtime.public_events import make_runtime_public_event
    from seektalent.source_adapters import public_source_reason_code
    from seektalent_runtime_control.events import normalize_progress_event
    from seektalent_ui.event_routes import _drop_broad_runtime_fields
    from seektalent_ui.workbench_response import source_runtime_warning_message
    from seektalent_workbench_v2.runtime_display import (
        normalize_runtime_progress_payload,
    )

    public_problem = public_source_reason_code(internal_reason)
    message = public_source_problem_message(
        expected_public_problem,
        source_label="猎聘",
    )
    public_event = make_runtime_public_event(
        runtime_run_id="runtime-run-1",
        stage="source_result",
        event_seq=1,
        round_no=1,
        source_kind="liepin",
        status="blocked",
        safe_reason_code=internal_reason,
    )
    control_event = normalize_progress_event(
        ProgressEvent(
            type="runtime_public_event",
            message="private source failure",
            payload=dict(public_event),
        ),
        runtime_run_id="runtime-run-1",
        now="2026-07-28T00:00:00Z",
    )
    workbench_payload = normalize_runtime_progress_payload(
        {
            "runtimeEventType": "runtime_round_source_result",
            "status": "blocked",
            "stage": "source_result",
            "roundNo": 1,
            "sourceKind": "liepin",
            "safeReasonCode": internal_reason,
        }
    )

    assert message is not None
    assert public_problem == expected_public_problem
    assert public_event["safeReasonCode"] == expected_public_problem
    assert control_event.payload["safeReasonCode"] == expected_public_problem
    assert message in control_event.summary
    assert workbench_payload["safeReasonCode"] == expected_public_problem
    assert message in workbench_payload["summary"]
    assert source_runtime_warning_message(expected_public_problem) == message
    assert cli._workbench_reason_message(internal_reason) == message
    assert _drop_broad_runtime_fields(
        {"safeReasonCode": internal_reason}
    ) == {"safeReasonCode": expected_public_problem}
    if internal_reason != expected_public_problem:
        assert internal_reason not in repr(control_event.payload)
        assert internal_reason not in repr(workbench_payload)


def test_production_consumers_do_not_depend_on_removed_duplicate_maps() -> None:
    forbidden = {
        "LIEPIN_PUBLIC_EVENT_REASON_MAP",
        "LIEPIN_SOURCE_LANE_REASON_CODE_MAP",
    }
    for path in (ROOT / "src").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for symbol in forbidden:
            assert symbol not in source, f"{path.relative_to(ROOT)} still uses {symbol}"
