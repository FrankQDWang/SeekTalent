from __future__ import annotations

from fastapi import HTTPException, Request

from seektalent_ui.workbench_store import WorkbenchStore, WorkbenchUser


def get_workbench_store(request: Request) -> WorkbenchStore:
    store = getattr(request.app.state, "workbench_store", None)
    if not isinstance(store, WorkbenchStore):
        raise HTTPException(status_code=500, detail="Workbench store is not configured.")
    return store


def local_workbench_user(request: Request) -> WorkbenchUser:
    return get_workbench_store(request).ensure_local_actor()


def local_workbench_read_user(request: Request) -> WorkbenchUser:
    return get_workbench_store(request).ensure_local_actor()


def local_workbench_write_user(request: Request) -> WorkbenchUser:
    return get_workbench_store(request).ensure_local_actor()
