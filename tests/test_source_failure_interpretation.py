from __future__ import annotations

from pathlib import Path

import pytest

from seektalent.source_contracts.safe_serialization import sanitize_reason_code
from seektalent.sources.liepin.reason_codes import (
    LIEPIN_FAILURE_POLICIES,
    LIEPIN_PRODUCTION_FAILURE_REASON_CODES,
    PUBLIC_SOURCE_PROBLEMS,
    public_source_problem_code,
    public_source_problem_message,
)


ROOT = Path(__file__).resolve().parents[1]


def test_canonical_inventory_is_the_policy_keyset() -> None:
    assert (
        LIEPIN_PRODUCTION_FAILURE_REASON_CODES
        == LIEPIN_FAILURE_POLICIES.keys()
    )


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
        "source_cleanup_pending",
        "source_filter_applied",
    }


def test_unknown_internal_reason_fails_closed_without_leaking_raw_reason() -> None:
    internal_reason = "liepin_private_selector_dump_x9"
    public_problem = public_source_problem_code(internal_reason)
    message = public_source_problem_message(public_problem, source_label="猎聘")

    assert public_problem == "source_unknown"
    assert message is not None
    assert internal_reason not in message


@pytest.mark.parametrize(
    ("reason_code", "expected"),
    [
        ("source_filter_unsupported", "source_filter_unsupported"),
        ("source_age_filter_unsupported", "source_filter_unsupported"),
        ("source_location_filter_unsupported", "source_filter_unsupported"),
        ("liepin_opencli_filter_unapplied", "source_filter_unavailable"),
        ("liepin_opencli_filter_option_unavailable", "source_filter_unavailable"),
        ("liepin_opencli_filter_clear_failed", "source_filter_unavailable"),
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
        ("liepin_opencli_budget_exhausted", "source_budget_exhausted"),
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
        ("partial_budget_exhausted", "source_budget_exhausted"),
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


def test_production_consumers_do_not_depend_on_removed_duplicate_maps_or_scaffolding() -> None:
    forbidden = {
        "FailureInterpretation",
        "interpret_liepin_failure",
        "LIEPIN_PUBLIC_EVENT_REASON_MAP",
        "LIEPIN_SOURCE_LANE_REASON_CODE_MAP",
        "LIEPIN_WORKER_SAFE_REASON_CODES",
    }
    for path in (ROOT / "src").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for symbol in forbidden:
            assert symbol not in source, f"{path.relative_to(ROOT)} still uses {symbol}"

    policy_source = (
        ROOT / "src/seektalent/sources/liepin/reason_codes.py"
    ).read_text(encoding="utf-8")
    assert "SourceOperationDisposition" not in policy_source
    assert "source_operation_disposition" not in policy_source
    assert "source_lane_reason_code" not in policy_source
    assert "effect_unknown" not in policy_source
