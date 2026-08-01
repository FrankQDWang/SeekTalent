from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from seektalent.runtime.orchestrator import RunArtifacts, WorkflowRuntime

__all__ = ["RunArtifacts", "WorkflowRuntime"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from seektalent.runtime.orchestrator import RunArtifacts, WorkflowRuntime

        return {"RunArtifacts": RunArtifacts, "WorkflowRuntime": WorkflowRuntime}[name]
    raise AttributeError(name)
