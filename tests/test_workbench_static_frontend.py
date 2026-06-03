from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from seektalent_ui import server
from seektalent_ui.resources import (
    frontend_available,
    package_frontend_dir,
    package_frontend_fallback_file,
)
from seektalent_ui.server import create_app
from tests.settings_factory import make_settings


def test_package_frontend_paths_resolve_inside_seektalent_ui_package() -> None:
    frontend_dir = package_frontend_dir()

    assert frontend_dir.name == "workbench"
    assert frontend_dir.parent.name == "static"
    assert "seektalent_ui" in frontend_dir.parts
    assert package_frontend_fallback_file() == frontend_dir / "200.html"


def test_frontend_available_requires_fallback_and_svelte_app(tmp_path: Path) -> None:
    root = tmp_path / "workbench"
    assert frontend_available(root) is False

    (root / "_app" / "immutable").mkdir(parents=True)
    (root / "200.html").write_text("<html></html>", encoding="utf-8")

    assert frontend_available(root) is True


def test_create_app_serves_packaged_frontend_shell(tmp_path: Path, monkeypatch) -> None:
    frontend_root = tmp_path / "frontend"
    (frontend_root / "_app" / "immutable").mkdir(parents=True)
    (frontend_root / "_app" / "immutable" / "entry.js").write_text("console.log('ok')", encoding="utf-8")
    (frontend_root / "200.html").write_text("<html>SeekTalent Workbench</html>", encoding="utf-8")
    monkeypatch.setattr("seektalent_ui.server.package_frontend_dir", lambda: frontend_root)

    app = create_app(settings=make_settings(workspace_root=str(tmp_path), mock_cts=True), serve_frontend=True)
    client = TestClient(app)

    shell = client.get("/")
    asset = client.get("/_app/immutable/entry.js")
    api_404 = client.get("/api/not-a-real-route")

    assert shell.status_code == 200
    assert "SeekTalent Workbench" in shell.text
    assert asset.status_code == 200
    assert "console.log" in asset.text
    assert api_404.status_code == 404


def test_packaged_workbench_startup_runs_prod_cleanup(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def fake_cleanup(settings):
        calls.append((settings.runtime_mode, settings.enable_flywheel))

    monkeypatch.setattr("seektalent_ui.server.cleanup_runtime_artifacts", fake_cleanup)
    create_app(
        settings=make_settings(
            workspace_root=str(tmp_path),
            runtime_mode="prod",
            liepin_api_token="unit-api-token",
            liepin_account_binding_secret="unit-account-secret",
            liepin_stream_token_secret="unit-stream-secret",
        ),
        serve_frontend=True,
    )

    assert calls == [("prod", False)]


def test_server_main_applies_liepin_opencli_overrides(tmp_path: Path, monkeypatch) -> None:
    captured = []

    def fake_create_app(**kwargs):
        captured.append(kwargs)
        return object()

    def fake_run(app, *, host, port):
        captured.append({"app": app, "host": host, "port": port})

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(server, "create_app", fake_create_app)
    monkeypatch.setattr(server.uvicorn, "run", fake_run)

    assert server.main(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
            "--runtime-mode",
            "prod",
            "--serve-frontend",
            "--liepin-worker-mode",
            "opencli",
            "--liepin-browser-action-backend",
            "opencli",
        ]
    ) == 0

    app_kwargs = captured[0]
    settings = app_kwargs["settings"]
    assert settings.runtime_mode == "prod"
    assert settings.liepin_worker_mode == "opencli"
    assert settings.liepin_browser_action_backend == "opencli"
    assert settings.liepin_opencli_command_argv[1:] == ("-m", "seektalent.opencli_launcher")
    assert Path(settings.liepin_opencli_command_argv[0]).exists()
    assert settings.liepin_api_token not in {"local-development-liepin-api-token", ""}
    assert settings.liepin_account_binding_secret not in {"local-development", ""}
    assert settings.liepin_stream_token_secret not in {"local-development", ""}
    assert app_kwargs["serve_frontend"] is True
