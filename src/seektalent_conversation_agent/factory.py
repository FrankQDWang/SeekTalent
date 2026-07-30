from __future__ import annotations

import time
from typing import Protocol

from seektalent.config import AppSettings
from seektalent.models import RequirementSheet
from seektalent.prompting import PromptRegistry
from seektalent_conversation_agent.budget import AgentBudgetPolicy
from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_conversation_agent.service import ConversationAgentService
from seektalent_conversation_agent.source_selection import RuntimeSourceSelectionResolver
from seektalent_conversation_agent.store import ConversationStore
from seektalent_conversation_agent.service_actions import AgentServiceActionAdapter
from seektalent_runtime_control.commands import RuntimeCommandService
from seektalent_runtime_control.detail import RuntimeDetailService
from seektalent_runtime_control.executor import (
    SourceContextProvider,
    WorkflowRuntimeExecutor,
)
from seektalent_runtime_control.service import RuntimeControlService
from seektalent_runtime_control.store import RuntimeControlStore


class ApplicationRuntimeFactory(Protocol):
    def __call__(
        self,
        settings: AppSettings,
        *,
        source_operation_executor: object | None = None,
    ) -> object: ...


class RuntimeRequirementExecutor:
    def __init__(
        self,
        *,
        settings: AppSettings,
        runtime_factory: ApplicationRuntimeFactory,
    ) -> None:
        self.settings = settings
        self.runtime_factory = runtime_factory

    def extract_requirements(
        self,
        *,
        job_title: str | None,
        jd_text: str,
        notes: str | None,
        requirement_cache_scope: str | None = None,
    ) -> RequirementSheet:
        runtime = self.runtime_factory(self.settings)
        extractor = getattr(runtime, "extract_requirements", None)
        if not callable(extractor):
            raise ConversationAgentError("agent_requirement_extractor_unavailable")
        result = extractor(
            job_title=job_title,
            jd=jd_text,
            notes=notes or "",
            requirement_cache_scope=requirement_cache_scope,
        )
        if not isinstance(result, RequirementSheet):
            raise ConversationAgentError("agent_requirement_extractor_invalid_result")
        return result


def build_agent_service(
    *,
    settings: AppSettings,
    runtime_factory: ApplicationRuntimeFactory,
    source_context_provider: SourceContextProvider | None = None,
) -> ConversationAgentService:
    conversation_store = ConversationStore(settings.conversation_agent_path)
    conversation_store.initialize()
    runtime_store = RuntimeControlStore(settings.runtime_control_path)
    runtime_store.initialize()
    requirement_executor = RuntimeRequirementExecutor(settings=settings, runtime_factory=runtime_factory)
    requirement_service = RuntimeControlService(
        store=runtime_store,
        executor=requirement_executor,
    )
    command_service = RuntimeCommandService(store=runtime_store, requirement_extractor=requirement_executor)
    def workflow_runtime_factory(
        *,
        source_operation_executor: object | None = None,
    ) -> object:
        if settings.liepin_worker_mode != "opencli":
            return runtime_factory(settings)
        return runtime_factory(
            settings,
            source_operation_executor=source_operation_executor,
        )

    workflow_executor = WorkflowRuntimeExecutor(
        store=runtime_store,
        settings=settings,
        runtime_factory=workflow_runtime_factory,
        command_service=command_service,
        source_context_provider=source_context_provider,
    )
    detail_service = RuntimeDetailService(store=runtime_store)
    agent_prompt = PromptRegistry(settings.prompt_dir).load("conversation_agent")
    return ConversationAgentService(
        store=conversation_store,
        service_action_adapter=AgentServiceActionAdapter(
            runtime_store=runtime_store,
            requirement_service=requirement_service,
            command_service=command_service,
            workflow_executor=workflow_executor,
            detail_service=detail_service,
        ),
        now=_now,
        agent_model_name=settings.controller_model_id,
        agent_instructions=agent_prompt.content,
        budget_policy=AgentBudgetPolicy(
            turn_input_token_budget=settings.agent_turn_input_token_budget,
            turn_output_token_budget=settings.agent_turn_output_token_budget,
            conversation_token_budget=settings.agent_conversation_token_budget,
            compaction_trigger_token_budget=settings.agent_compaction_trigger_token_budget,
            monthly_cost_budget_cents=settings.agent_monthly_cost_budget_cents,
        ),
        source_selection_resolver=RuntimeSourceSelectionResolver(
            registered_runtime_source_ids={"cts", "liepin"},
            default_runtime_source_ids=("liepin",),
        ),
    )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
