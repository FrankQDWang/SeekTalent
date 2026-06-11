from __future__ import annotations

from dataclasses import dataclass

from seektalent_conversation_agent.errors import ConversationAgentError


_MIN_PROVIDER_REPORT_RATIO_PERCENT = 50


@dataclass(frozen=True)
class AgentBudgetPolicy:
    turn_input_token_budget: int
    turn_output_token_budget: int
    conversation_token_budget: int
    compaction_trigger_token_budget: int
    monthly_cost_budget_cents: int | None = None

    def check_turn(
        self,
        *,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
        conversation_tokens_before_turn: int,
        monthly_cost_cents_before_turn: int,
    ) -> None:
        if self.monthly_cost_budget_cents is not None and monthly_cost_cents_before_turn > self.monthly_cost_budget_cents:
            raise ConversationAgentError("agent_cost_budget_exceeded")
        if estimated_input_tokens > self.turn_input_token_budget:
            raise ConversationAgentError("agent_token_budget_exceeded")
        if estimated_output_tokens > self.turn_output_token_budget:
            raise ConversationAgentError("agent_token_budget_exceeded")
        if conversation_tokens_before_turn + estimated_input_tokens > self.conversation_token_budget:
            raise ConversationAgentError("agent_token_budget_exceeded")

    def should_compact(self, *, conversation_tokens_before_turn: int) -> bool:
        return conversation_tokens_before_turn >= self.compaction_trigger_token_budget

    def check_provider_report(
        self,
        *,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
        reported_input_tokens: int,
        reported_output_tokens: int,
        estimated_cost_cents: int,
        reported_cost_cents: int,
    ) -> None:
        _require_non_negative(
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=estimated_output_tokens,
            reported_input_tokens=reported_input_tokens,
            reported_output_tokens=reported_output_tokens,
            estimated_cost_cents=estimated_cost_cents,
            reported_cost_cents=reported_cost_cents,
        )
        self._check_underreported(
            field="input_tokens",
            estimated=estimated_input_tokens,
            reported=reported_input_tokens,
            reason_code="agent_usage_anomaly_detected",
        )
        self._check_underreported(
            field="output_tokens",
            estimated=estimated_output_tokens,
            reported=reported_output_tokens,
            reason_code="agent_usage_anomaly_detected",
        )
        self._check_underreported(
            field="cost_cents",
            estimated=estimated_cost_cents,
            reported=reported_cost_cents,
            reason_code="agent_cost_anomaly_detected",
        )

    def check_monthly_cost_after_turn(
        self,
        *,
        monthly_cost_cents_before_turn: int,
        reported_turn_cost_cents: int,
    ) -> None:
        _require_non_negative(
            monthly_cost_cents_before_turn=monthly_cost_cents_before_turn,
            reported_turn_cost_cents=reported_turn_cost_cents,
        )
        if self.monthly_cost_budget_cents is None:
            return
        if monthly_cost_cents_before_turn + reported_turn_cost_cents > self.monthly_cost_budget_cents:
            raise ConversationAgentError(
                "agent_cost_budget_exceeded",
                payload={
                    "monthlyCostCentsBeforeTurn": monthly_cost_cents_before_turn,
                    "reportedTurnCostCents": reported_turn_cost_cents,
                    "monthlyCostBudgetCents": self.monthly_cost_budget_cents,
                },
            )

    def _check_underreported(self, *, field: str, estimated: int, reported: int, reason_code: str) -> None:
        if estimated == 0:
            return
        if reported * 100 >= estimated * _MIN_PROVIDER_REPORT_RATIO_PERCENT:
            return
        raise ConversationAgentError(
            reason_code,
            payload={
                "field": field,
                "estimated": estimated,
                "reported": reported,
                "minimumRatioPercent": _MIN_PROVIDER_REPORT_RATIO_PERCENT,
            },
        )


def _require_non_negative(**values: int) -> None:
    for field, value in values.items():
        if value < 0:
            raise ConversationAgentError(
                "agent_usage_report_invalid",
                payload={"field": field, "value": value},
            )
