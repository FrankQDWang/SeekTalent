from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from seektalent.config import AppSettings
from seektalent.models import RequirementSheet
from seektalent_conversation_agent.budget import AgentBudgetPolicy
from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_conversation_agent.service import ConversationAgentService
from seektalent_conversation_agent.store import ConversationStore
from seektalent_conversation_agent.tools import AgentToolAdapter
from seektalent_runtime_control.commands import RuntimeCommandService
from seektalent_runtime_control.detail import RuntimeDetailService
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_runtime_control.requirements import RequirementDraft
from seektalent_runtime_control.service import RuntimeControlService
from seektalent_runtime_control.store import RuntimeControlStore


class RuntimeLikeAdapter:
    def __init__(self, runtime: object) -> None:
        self.runtime = runtime

    async def run_async(self, **kwargs: object) -> object:
        run_async = getattr(self.runtime, "run_async", None)
        if not callable(run_async):
            raise ConversationAgentError("agent_workflow_runtime_unavailable")
        result = run_async(**kwargs)
        if not isinstance(result, Awaitable):
            raise ConversationAgentError("agent_workflow_runtime_invalid_result")
        return await result


class RuntimeRequirementExecutor:
    def __init__(self, *, settings: AppSettings, runtime_factory: Callable[[AppSettings], object]) -> None:
        self.settings = settings
        self.runtime_factory = runtime_factory

    def extract_requirements(self, *, job_title: str, jd_text: str, notes: str | None) -> RequirementSheet:
        runtime = self.runtime_factory(self.settings)
        extractor = getattr(runtime, "extract_requirements", None)
        if not callable(extractor):
            raise ConversationAgentError("agent_requirement_extractor_unavailable")
        result = extractor(job_title=job_title, jd=jd_text, notes=notes or "", requirement_cache_scope=None)
        if not isinstance(result, RequirementSheet):
            raise ConversationAgentError("agent_requirement_extractor_invalid_result")
        return result

    def normalize_requirement_text(
        self,
        *,
        text: str,
        target_section_hint: str | None,
        current_draft: RequirementDraft,
    ) -> dict[str, object]:
        section_id = target_section_hint or "must_have_capabilities"
        return {"additions": [{"sectionId": section_id, "text": text, "source": "runtime_normalized"}]}


def build_agent_service(
    *,
    settings: AppSettings,
    runtime_factory: Callable[[AppSettings], object],
) -> ConversationAgentService:
    conversation_store = ConversationStore(settings.conversation_agent_path)
    conversation_store.initialize()
    runtime_store = RuntimeControlStore(settings.runtime_control_path)
    runtime_store.initialize()
    requirement_service = RuntimeControlService(
        store=runtime_store,
        executor=RuntimeRequirementExecutor(settings=settings, runtime_factory=runtime_factory),
    )
    command_service = RuntimeCommandService(store=runtime_store)
    workflow_executor = WorkflowRuntimeExecutor(
        store=runtime_store,
        settings=settings,
        runtime_factory=lambda: RuntimeLikeAdapter(runtime_factory(settings)),
    )
    detail_service = RuntimeDetailService(store=runtime_store)
    return ConversationAgentService(
        store=conversation_store,
        tool_adapter=AgentToolAdapter(
            runtime_store=runtime_store,
            requirement_service=requirement_service,
            command_service=command_service,
            workflow_executor=workflow_executor,
            detail_service=detail_service,
        ),
        now=_now,
        agent_model_name=settings.controller_model_id,
        budget_policy=AgentBudgetPolicy(
            turn_input_token_budget=settings.agent_turn_input_token_budget,
            turn_output_token_budget=settings.agent_turn_output_token_budget,
            conversation_token_budget=settings.agent_conversation_token_budget,
            compaction_trigger_token_budget=settings.agent_compaction_trigger_token_budget,
            monthly_cost_budget_cents=settings.agent_monthly_cost_budget_cents,
        ),
    )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
