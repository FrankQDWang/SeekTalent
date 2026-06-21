from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from seektalent_ui.resources import frontend_available, package_frontend_dir


def mount_packaged_frontend(app: FastAPI) -> None:
    frontend_root = package_frontend_dir()
    if not frontend_available(frontend_root):
        return
    app.mount("/_app", StaticFiles(directory=frontend_root / "_app"), name="workbench_static")

    @app.get("/", include_in_schema=False)
    @app.get("/{path:path}", include_in_schema=False)
    async def packaged_frontend(path: str = "") -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found.")
        return FileResponse(frontend_root / "200.html")
