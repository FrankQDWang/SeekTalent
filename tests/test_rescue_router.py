from __future__ import annotations

from seektalent.models import StopGuidance, StopQualityGateStatus, TopPoolStrength
from seektalent.runtime.rescue_router import RescueInputs, choose_rescue_lane


def _guidance(
    status: StopQualityGateStatus = "broaden_required",
    *,
    top_pool_strength: TopPoolStrength = "weak",
    can_stop: bool = False,
) -> StopGuidance:
    return StopGuidance(
        can_stop=can_stop,
        reason="top pool weak",
        top_pool_strength=top_pool_strength,
        quality_gate_status=status,
    )


def _skipped(decision) -> list[tuple[str, str]]:
    return [(item.lane, item.reason) for item in decision.skipped_lanes]


def test_reserve_broaden_is_preferred_when_reserve_family_is_untried() -> None:
    decision = choose_rescue_lane(
        RescueInputs(
            stop_guidance=_guidance(),
            has_untried_reserve_family=True,
            has_feedback_seed_resumes=True,
            candidate_feedback_enabled=True,
            candidate_feedback_attempted=False,
            anchor_only_broaden_attempted=False,
        )
    )

    assert decision.selected_lane == "reserve_broaden"
    assert decision.skipped_lanes == []


def test_candidate_feedback_is_selected_before_anchor_only() -> None:
    decision = choose_rescue_lane(
        RescueInputs(
            stop_guidance=_guidance(),
            has_untried_reserve_family=False,
            has_feedback_seed_resumes=True,
            candidate_feedback_enabled=True,
            candidate_feedback_attempted=False,
            anchor_only_broaden_attempted=False,
        )
    )

    assert decision.selected_lane == "candidate_feedback"
    assert _skipped(decision) == [("reserve_broaden", "no_untried_reserve_family")]


def test_anchor_only_is_selected_after_feedback_branch_is_unavailable() -> None:
    decision = choose_rescue_lane(
        RescueInputs(
            stop_guidance=_guidance(),
            has_untried_reserve_family=False,
            has_feedback_seed_resumes=False,
            candidate_feedback_enabled=True,
            candidate_feedback_attempted=False,
            anchor_only_broaden_attempted=False,
        )
    )

    assert decision.selected_lane == "anchor_only"
    assert _skipped(decision) == [
        ("reserve_broaden", "no_untried_reserve_family"),
        ("candidate_feedback", "no_feedback_seed_resumes"),
    ]
    assert all(item.lane != "web_company_discovery" for item in decision.skipped_lanes)


def test_anchor_only_is_selected_after_feedback_lane_is_disabled() -> None:
    decision = choose_rescue_lane(
        RescueInputs(
            stop_guidance=_guidance(),
            has_untried_reserve_family=False,
            has_feedback_seed_resumes=False,
            candidate_feedback_enabled=False,
            candidate_feedback_attempted=True,
            anchor_only_broaden_attempted=False,
        )
    )

    assert decision.selected_lane == "anchor_only"
    assert _skipped(decision) == [
        ("reserve_broaden", "no_untried_reserve_family"),
        ("candidate_feedback", "disabled"),
    ]
    assert all(item.lane != "web_company_discovery" for item in decision.skipped_lanes)


def test_allow_stop_is_selected_outside_rescue_statuses() -> None:
    decision = choose_rescue_lane(
        RescueInputs(
            stop_guidance=_guidance(status="pass", can_stop=True),
            has_untried_reserve_family=True,
            has_feedback_seed_resumes=True,
            candidate_feedback_enabled=True,
            candidate_feedback_attempted=False,
            anchor_only_broaden_attempted=False,
        )
    )

    assert decision.selected_lane == "allow_stop"


def test_continue_controller_is_selected_outside_rescue_statuses_when_stop_is_disallowed() -> None:
    decision = choose_rescue_lane(
        RescueInputs(
            stop_guidance=_guidance(status="pass", can_stop=False),
            has_untried_reserve_family=True,
            has_feedback_seed_resumes=True,
            candidate_feedback_enabled=True,
            candidate_feedback_attempted=False,
            anchor_only_broaden_attempted=False,
        )
    )

    assert decision.selected_lane == "continue_controller"


def test_allow_stop_is_selected_after_anchor_only_was_already_attempted() -> None:
    decision = choose_rescue_lane(
        RescueInputs(
            stop_guidance=_guidance(),
            has_untried_reserve_family=False,
            has_feedback_seed_resumes=False,
            candidate_feedback_enabled=True,
            candidate_feedback_attempted=True,
            anchor_only_broaden_attempted=True,
        )
    )

    assert decision.selected_lane == "allow_stop"
    assert _skipped(decision) == [
        ("reserve_broaden", "no_untried_reserve_family"),
        ("candidate_feedback", "already_attempted"),
        ("anchor_only", "already_attempted"),
    ]
    assert all(item.lane != "web_company_discovery" for item in decision.skipped_lanes)
