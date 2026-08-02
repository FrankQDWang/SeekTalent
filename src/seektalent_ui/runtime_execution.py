from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from seektalent.config import AppSettings
from seektalent.providers.liepin.runtime_context import local_opencli_liepin_source_context
from seektalent.source_adapters import build_source_enabled_runtime
from seektalent.wtscli_lifecycle_supervisor import WtsCliLifecycleSupervisor
from seektalent_runtime_control.commands import RuntimeCommandService
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_workbench_v2.runtime_service import (
    WorkbenchV2RequirementExtractor,
    build_workbench_v2_requirement_extractor,
)


DEV_PROD_PARITY_DIFFERENCE_ALLOWLIST = frozenset(
    {
        "frontend_delivery",
        "data_directory",
        "artifact_policy",
        "flywheel_policy",
        "installation_validation",
        "dev_diagnostics",
    }
)


@dataclass(frozen=True, slots=True)
class RuntimeExecutionBundle:
    store: RuntimeControlStore
    command_service: RuntimeCommandService
    executor: WorkflowRuntimeExecutor
    requirement_extractor: WorkbenchV2RequirementExtractor


def build_runtime_execution(
    settings: AppSettings,
    *,
    runtime_factory: Callable[..., object] = build_source_enabled_runtime,
    wtscli_lifecycle_supervisor: WtsCliLifecycleSupervisor | None = None,
) -> RuntimeExecutionBundle:
    store = RuntimeControlStore(settings.runtime_control_path)
    store.initialize()
    requirement_extractor = build_workbench_v2_requirement_extractor(settings)
    command_service = RuntimeCommandService(
        store=store,
        requirement_extractor=requirement_extractor,
    )

    def factory(
        *,
        source_operation_executor: object | None,
        wtscli_lifecycle_supervisor: WtsCliLifecycleSupervisor | None = None,
    ) -> object:
        supervisor = wtscli_lifecycle_supervisor or wtscli_lifecycle_supervisor_outer
        kwargs: dict[str, object] = {
            "source_operation_executor": source_operation_executor,
        }
        if supervisor is not None:
            kwargs["wtscli_lifecycle_supervisor"] = supervisor
        return runtime_factory(settings, **kwargs)

    wtscli_lifecycle_supervisor_outer = wtscli_lifecycle_supervisor
    executor = WorkflowRuntimeExecutor(
        store=store,
        settings=settings,
        runtime_factory=factory,
        command_service=command_service,
        source_context_provider=local_opencli_liepin_source_context,
        wtscli_lifecycle_supervisor=wtscli_lifecycle_supervisor,
    )
    return RuntimeExecutionBundle(
        store=store,
        command_service=command_service,
        executor=executor,
        requirement_extractor=requirement_extractor,
    )
