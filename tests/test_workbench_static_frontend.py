from __future__ import annotations

import shlex
from pathlib import Path

from fastapi.testclient import TestClient

from seektalent.product_env import MANAGED_OPENCLI_COMMAND_MARKER
from seektalent.workbench_internal_secrets import INTERNAL_LIEPIN_ENV_VARS
from seektalent_ui import server
from seektalent_ui.resources import (
    frontend_available,
    package_frontend_dir,
    package_frontend_fallback_file,
)
from seektalent_ui.server import create_app
from seektalent_ui.workbench_paths import agent_workbench_stream_db_path, liepin_db_path, workbench_db_path
from tests.settings_factory import make_settings


def test_package_frontend_paths_resolve_inside_seektalent_ui_package() -> None:
    frontend_dir = package_frontend_dir()

    assert frontend_dir.name == "workbench"
    assert frontend_dir.parent.name == "static"
    assert "seektalent_ui" in frontend_dir.parts
    assert package_frontend_fallback_file() == frontend_dir / "200.html"


def test_frontend_available_requires_fallback_and_built_react_app(tmp_path: Path) -> None:
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
    (frontend_root / "secret.txt").write_text("do not serve through catch-all", encoding="utf-8")
    monkeypatch.setattr("seektalent_ui.static_frontend.package_frontend_dir", lambda: frontend_root)

    app = create_app(settings=make_settings(workspace_root=str(tmp_path), mock_cts=True, provider_name="cts"), serve_frontend=True)
    client = TestClient(app)

    shell = client.get("/")
    catch_all = client.get("/secret.txt")
    asset = client.get("/_app/immutable/entry.js")
    api_404 = client.get("/api/not-a-real-route")

    assert shell.status_code == 200
    assert "SeekTalent Workbench" in shell.text
    assert catch_all.status_code == 200
    assert "SeekTalent Workbench" in catch_all.text
    assert "do not serve through catch-all" not in catch_all.text
    assert asset.status_code == 200
    assert "console.log" in asset.text
    assert api_404.status_code == 404


def test_packaged_workbench_startup_runs_prod_cleanup(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def fake_cleanup(settings):
        calls.append((settings.runtime_mode, settings.enable_flywheel))

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
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


def test_prod_workbench_databases_use_user_data_root(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("HOME", str(home))
    settings = make_settings(
        runtime_mode="prod",
        workspace_root=str(workspace),
        liepin_connector_db_path=".seektalent/liepin_connector.sqlite3",
    )

    assert workbench_db_path(settings) == home / ".seektalent" / "workbench.sqlite3"
    assert agent_workbench_stream_db_path(settings) == home / ".seektalent" / "agent_workbench_stream.sqlite3"
    assert liepin_db_path(settings) == home / ".seektalent" / "liepin_connector.sqlite3"


def test_dev_workbench_databases_use_workspace_root(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("HOME", str(home))
    settings = make_settings(
        runtime_mode="dev",
        workspace_root=str(workspace),
        liepin_connector_db_path=".seektalent/liepin_connector.sqlite3",
    )

    assert workbench_db_path(settings) == workspace / ".seektalent" / "workbench.sqlite3"
    assert agent_workbench_stream_db_path(settings) == workspace / ".seektalent" / "agent_workbench_stream.sqlite3"
    assert liepin_db_path(settings) == workspace / ".seektalent" / "liepin_connector.sqlite3"


def test_server_main_applies_liepin_opencli_overrides(tmp_path: Path, monkeypatch) -> None:
    captured = []
    managed_command = "/domi/node /home/user/.seektalent/opencli/main.js"

    def fake_create_app(**kwargs):
        captured.append(kwargs)
        return object()

    def fake_run(app, *, host, port):
        captured.append({"app": app, "host": host, "port": port})

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "SEEKTALENT_LIEPIN_OPENCLI_COMMAND=apps/web-react/node_modules/.bin/opencli",
                "SEEKTALENT_LIEPIN_OPENCLI_SESSION=dev-liepin-session",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_COMMAND", managed_command)
    monkeypatch.setenv(MANAGED_OPENCLI_COMMAND_MARKER, "1")
    for name in INTERNAL_LIEPIN_ENV_VARS:
        monkeypatch.setenv(name, "local-development")
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
    assert settings.liepin_opencli_session == "seektalent-liepin"
    assert shlex.split(settings.liepin_opencli_command) == shlex.split(managed_command)
    assert "seektalent.opencli_launcher" not in settings.liepin_opencli_command
    assert settings.liepin_api_token not in {"local-development-liepin-api-token", ""}
    assert settings.liepin_account_binding_secret not in {"local-development", ""}
    assert settings.liepin_stream_token_secret not in {"local-development", ""}
    assert app_kwargs["serve_frontend"] is True
