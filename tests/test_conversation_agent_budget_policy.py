from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from seektalent_conversation_agent.budget import AgentBudgetPolicy
from seektalent_conversation_agent.errors import ConversationAgentError
from tests.conversation_agent_test_support import build_service


def test_budget_policy_requires_compaction_or_rejects_over_budget_turn(tmp_path: Path) -> None:
    policy = AgentBudgetPolicy(
        turn_input_token_budget=10,
        turn_output_token_budget=5,
        conversation_token_budget=12,
        compaction_trigger_token_budget=8,
        monthly_cost_budget_cents=50,
    )

    with pytest.raises(ConversationAgentError) as exc_info:
        policy.check_turn(
            estimated_input_tokens=11,
            estimated_output_tokens=1,
            conversation_tokens_before_turn=1,
            monthly_cost_cents_before_turn=0,
        )

    assert exc_info.value.reason_code == "agent_token_budget_exceeded"


def test_budget_policy_fails_closed_when_monthly_cost_budget_is_exceeded(tmp_path: Path) -> None:
    policy = AgentBudgetPolicy(
        turn_input_token_budget=100,
        turn_output_token_budget=100,
        conversation_token_budget=200,
        compaction_trigger_token_budget=150,
        monthly_cost_budget_cents=50,
    )

    with pytest.raises(ConversationAgentError) as exc_info:
        policy.check_turn(
            estimated_input_tokens=10,
            estimated_output_tokens=10,
            conversation_tokens_before_turn=10,
            monthly_cost_cents_before_turn=51,
        )

    assert exc_info.value.reason_code == "agent_cost_budget_exceeded"


def test_budget_policy_detects_underreported_provider_tokens(tmp_path: Path) -> None:
    policy = AgentBudgetPolicy(
        turn_input_token_budget=1_000,
        turn_output_token_budget=1_000,
        conversation_token_budget=2_000,
        compaction_trigger_token_budget=1_500,
    )

    with pytest.raises(ConversationAgentError) as exc_info:
        policy.check_provider_report(
            estimated_input_tokens=400,
            estimated_output_tokens=100,
            reported_input_tokens=20,
            reported_output_tokens=100,
            estimated_cost_cents=40,
            reported_cost_cents=40,
        )

    assert exc_info.value.reason_code == "agent_usage_anomaly_detected"
    assert exc_info.value.payload["field"] == "input_tokens"


def test_budget_policy_detects_underreported_provider_cost(tmp_path: Path) -> None:
    policy = AgentBudgetPolicy(
        turn_input_token_budget=1_000,
        turn_output_token_budget=1_000,
        conversation_token_budget=2_000,
        compaction_trigger_token_budget=1_500,
    )

    with pytest.raises(ConversationAgentError) as exc_info:
        policy.check_provider_report(
            estimated_input_tokens=400,
            estimated_output_tokens=100,
            reported_input_tokens=390,
            reported_output_tokens=100,
            estimated_cost_cents=40,
            reported_cost_cents=1,
        )

    assert exc_info.value.reason_code == "agent_cost_anomaly_detected"
    assert exc_info.value.payload["field"] == "cost_cents"


class CountingRunner:
    def __init__(self, *, usage: object | None = None) -> None:
        self.calls = 0
        self.usage = usage

    async def run(self, agent, prompt: str) -> object:
        self.calls += 1
        return SimpleNamespace(final_output="已收到", usage=lambda: self.usage)


def test_conversation_agent_service_enforces_budget_before_model_run(tmp_path: Path) -> None:
    service, store, _runtime_store = build_service(tmp_path)
    runner = CountingRunner()
    service.agent_runner = runner
    service.budget_policy = AgentBudgetPolicy(
        turn_input_token_budget=1,
        turn_output_token_budget=100,
        conversation_token_budget=10,
        compaction_trigger_token_budget=8,
    )
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Data Platform Engineer",
    )

    with pytest.raises(ConversationAgentError) as exc_info:
        import asyncio

        asyncio.run(
            service.run_agent_turn(
                conversation_id=conversation.conversation_id,
                owner_user_id="user_1",
                workspace_id="workspace_1",
                user_message="这个输入明显超过一个 token",
            )
        )

    assert exc_info.value.reason_code == "agent_token_budget_exceeded"
    assert runner.calls == 0
    with store._connect() as conn:
        row = conn.execute("SELECT status, reason_code FROM agent_tool_calls WHERE tool_name = 'agent_model_run'").fetchone()
    assert row["status"] == "failed"
    assert row["reason_code"] == "agent_token_budget_exceeded"


def test_conversation_agent_service_detects_provider_usage_anomaly(tmp_path: Path) -> None:
    service, store, _runtime_store = build_service(tmp_path)
    service.agent_runner = CountingRunner(
        usage=SimpleNamespace(input_tokens=1, output_tokens=2, total_tokens=3, cost_cents=0)
    )
    service.budget_policy = AgentBudgetPolicy(
        turn_input_token_budget=1_000,
        turn_output_token_budget=100,
        conversation_token_budget=2_000,
        compaction_trigger_token_budget=1_500,
    )
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Data Platform Engineer",
    )

    with pytest.raises(ConversationAgentError) as exc_info:
        import asyncio

        asyncio.run(
            service.run_agent_turn(
                conversation_id=conversation.conversation_id,
                owner_user_id="user_1",
                workspace_id="workspace_1",
                user_message="请帮我整理候选人总结偏好。" * 40,
            )
        )

    assert exc_info.value.reason_code == "agent_usage_anomaly_detected"
    with store._connect() as conn:
        row = conn.execute("SELECT status, reason_code FROM agent_tool_calls WHERE tool_name = 'agent_model_run'").fetchone()
    assert row["status"] == "failed"
    assert row["reason_code"] == "agent_usage_anomaly_detected"


def test_conversation_agent_service_fails_when_reported_turn_cost_exceeds_monthly_budget(tmp_path: Path) -> None:
    service, store, _runtime_store = build_service(tmp_path)
    service.agent_runner = CountingRunner(
        usage=SimpleNamespace(input_tokens=20, output_tokens=5, total_tokens=25, cost_cents=10)
    )
    service.budget_policy = AgentBudgetPolicy(
        turn_input_token_budget=1_000,
        turn_output_token_budget=100,
        conversation_token_budget=2_000,
        compaction_trigger_token_budget=1_500,
        monthly_cost_budget_cents=50,
    )
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Data Platform Engineer",
    )
    store.save_tool_call(
        tool_call_id="prior_agent_model_run",
        conversation_id=conversation.conversation_id,
        tool_name="agent_model_run",
        status="completed",
        args={"modelName": "previous"},
        result={"reportedCostCents": 45},
        reason_code=None,
        started_at="2026-06-08T00:00:00.000000Z",
        completed_at="2026-06-08T00:00:01.000000Z",
    )

    with pytest.raises(ConversationAgentError) as exc_info:
        import asyncio

        asyncio.run(
            service.run_agent_turn(
                conversation_id=conversation.conversation_id,
                owner_user_id="user_1",
                workspace_id="workspace_1",
                user_message="继续整理候选人总结。",
            )
        )

    assert exc_info.value.reason_code == "agent_cost_budget_exceeded"
    with store._connect() as conn:
        row = conn.execute(
            """
            SELECT status, reason_code
            FROM agent_tool_calls
            WHERE tool_name = 'agent_model_run' AND tool_call_id != 'prior_agent_model_run'
            """
        ).fetchone()
        assistant_count = conn.execute(
            "SELECT COUNT(*) FROM agent_transcript_messages WHERE role = 'assistant'"
        ).fetchone()[0]
    assert row["status"] == "failed"
    assert row["reason_code"] == "agent_cost_budget_exceeded"
    assert assistant_count == 0


def test_conversation_agent_service_replays_failed_idempotent_turn_as_same_error(tmp_path: Path) -> None:
    service, store, _runtime_store = build_service(tmp_path)
    runner = CountingRunner(
        usage=SimpleNamespace(input_tokens=20, output_tokens=5, total_tokens=25, cost_cents=10)
    )
    service.agent_runner = runner
    service.budget_policy = AgentBudgetPolicy(
        turn_input_token_budget=1_000,
        turn_output_token_budget=100,
        conversation_token_budget=2_000,
        compaction_trigger_token_budget=1_500,
        monthly_cost_budget_cents=50,
    )
    conversation = service.create_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        title="Data Platform Engineer",
    )
    store.save_tool_call(
        tool_call_id="prior_agent_model_run",
        conversation_id=conversation.conversation_id,
        tool_name="agent_model_run",
        status="completed",
        args={"modelName": "previous"},
        result={"reportedCostCents": 45},
        reason_code=None,
        started_at="2026-06-08T00:00:00.000000Z",
        completed_at="2026-06-08T00:00:01.000000Z",
    )

    for _ in range(2):
        with pytest.raises(ConversationAgentError) as exc_info:
            import asyncio

            asyncio.run(
                service.run_agent_turn(
                    conversation_id=conversation.conversation_id,
                    owner_user_id="user_1",
                    workspace_id="workspace_1",
                    user_message="继续整理候选人总结。",
                    idempotency_key="user-text-cost-overrun",
                )
            )
        assert exc_info.value.reason_code == "agent_cost_budget_exceeded"

    with store._connect() as conn:
        assistant_count = conn.execute(
            "SELECT COUNT(*) FROM agent_transcript_messages WHERE role = 'assistant'"
        ).fetchone()[0]
    assert runner.calls == 1
    assert assistant_count == 0
