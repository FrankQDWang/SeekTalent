from __future__ import annotations

from seektalent_ui.models import WorkbenchUserResponse, WorkbenchWorkspaceResponse
from seektalent_ui.workbench_store import WorkbenchUser, WorkbenchWorkspace


def user_response(user: WorkbenchUser) -> WorkbenchUserResponse:
    return WorkbenchUserResponse(
        userId=user.user_id,
        email=user.email,
        displayName=user.display_name,
        role=user.role,
        workspaceId=user.workspace_id,
    )


def workspace_response(workspace: WorkbenchWorkspace) -> WorkbenchWorkspaceResponse:
    return WorkbenchWorkspaceResponse(id=workspace.workspace_id, name=workspace.name)
