from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from seektalent.models import StopGuidance

RescueLane = Literal[
    "reserve_broaden",
    "candidate_feedback",
    "anchor_only",
    "continue_controller",
    "allow_stop",
]

RESCUE_STATUSES = {"broaden_required", "low_quality_exhausted"}


class SkippedRescueLane(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lane: str
    reason: str


class RescueInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stop_guidance: StopGuidance
    has_untried_reserve_family: bool
    has_feedback_seed_resumes: bool
    candidate_feedback_enabled: bool
    candidate_feedback_attempted: bool
    anchor_only_broaden_attempted: bool


class RescueDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_lane: RescueLane
    skipped_lanes: list[SkippedRescueLane] = Field(default_factory=list)


def choose_rescue_lane(inputs: RescueInputs) -> RescueDecision:
    status = inputs.stop_guidance.quality_gate_status
    if status not in RESCUE_STATUSES:
        if inputs.stop_guidance.can_stop:
            return RescueDecision(selected_lane="allow_stop")
        return RescueDecision(selected_lane="continue_controller")

    skipped_lanes: list[SkippedRescueLane] = []

    if inputs.has_untried_reserve_family:
        return RescueDecision(selected_lane="reserve_broaden", skipped_lanes=skipped_lanes)
    skipped_lanes.append(SkippedRescueLane(lane="reserve_broaden", reason="no_untried_reserve_family"))

    if inputs.candidate_feedback_enabled and not inputs.candidate_feedback_attempted and inputs.has_feedback_seed_resumes:
        return RescueDecision(selected_lane="candidate_feedback", skipped_lanes=skipped_lanes)
    if not inputs.candidate_feedback_enabled:
        reason = "disabled"
    elif inputs.candidate_feedback_attempted:
        reason = "already_attempted"
    else:
        reason = "no_feedback_seed_resumes"
    skipped_lanes.append(SkippedRescueLane(lane="candidate_feedback", reason=reason))

    if not inputs.anchor_only_broaden_attempted:
        return RescueDecision(selected_lane="anchor_only", skipped_lanes=skipped_lanes)

    skipped_lanes.append(SkippedRescueLane(lane="anchor_only", reason="already_attempted"))
    return RescueDecision(selected_lane="allow_stop", skipped_lanes=skipped_lanes)
