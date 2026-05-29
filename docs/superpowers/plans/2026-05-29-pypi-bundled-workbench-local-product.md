# PyPI Bundled Workbench Local Product Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the built Svelte Workbench frontend inside the SeekTalent PyPI wheel and make `seektalent workbench` a one-command local product startup with three-line user configuration.

**Architecture:** Release builders compile `apps/web-svelte` once and copy static output into `src/seektalent_ui/static/workbench/` before `uv build`; installed users receive only Python plus static JS/CSS assets. FastAPI serves API routes and packaged frontend routes from one loopback origin in prod mode, while source checkout developers keep the existing Vite/Bun flow.

**Tech Stack:** Python 3.12, FastAPI/Starlette `StaticFiles` and `FileResponse`, uv/uv_build, SvelteKit `adapter-static`, Bun for release-time frontend builds, pytest, ruff.

---

Spec: `docs/superpowers/specs/2026-05-29-pypi-bundled-workbench-local-product-design.md`

## File Structure

- Create `src/seektalent_ui/resources.py`
  - Owns package-resource paths for the built Workbench frontend.
- Create `src/seektalent_ui/static/.gitkeep`
  - Keeps the static package directory present before generated frontend assets exist.
- Modify `src/seektalent_ui/server.py`
  - Serves the packaged Workbench SPA from package resources and exposes `seektalent-ui-api --serve-frontend` behavior through app creation.
- Modify `src/seektalent_ui/network_guard.py`
  - Treats packaged frontend paths as guarded Workbench paths.
- Create `scripts/build_packaged_workbench.py`
  - Builds Svelte with Bun, copies `apps/web-svelte/build` into `src/seektalent_ui/static/workbench`, and validates required files.
- Modify `src/seektalent/cli.py`
  - Adds `seektalent workbench`, keeps direct command dispatch, and reports packaged frontend availability in `inspect --json`.
- Modify `src/seektalent/default.env`
  - Makes the packaged init template three lines only.
- Keep `.env.example`
  - Source checkout developer template remains full-featured.
- Modify `src/seektalent/resources.py`
  - Keeps source `.env.example` and packaged minimal env template separate.
- Modify `src/seektalent/runtime/lifecycle.py`
  - Documents and enforces prod cleanup scope; only short-lived generated artifacts/logs are eligible for automatic cleanup.
- Modify docs: `README.md`, `README.zh-CN.md`, `docs/cli.md`, `docs/cli.zh-CN.md`, `docs/configuration.md`, `docs/outputs.md`, `docs/ui.md`
  - Documents PyPI bundled Workbench, three-line config, local data roots, cleanup, and privacy contract.
- Modify: `docs/development.md`
  - Documents release build and dry-run publish commands.
- Test files:
  - `tests/test_cli_packaging.py`
  - `tests/test_cli.py`
  - `tests/test_resources.py`
  - `tests/test_runtime_lifecycle.py`
  - `tests/test_workbench_network_guard.py`
  - Create `tests/test_workbench_static_frontend.py`

### Task 1: Add Packaged Frontend Resource Boundary

**Files:**
- Create: `src/seektalent_ui/resources.py`
- Create: `src/seektalent_ui/static/.gitkeep`
- Test: `tests/test_workbench_static_frontend.py`

- [ ] **Step 1: Write the failing resource tests**

Create `tests/test_workbench_static_frontend.py`:

```python
from __future__ import annotations

from pathlib import Path

from seektalent_ui.resources import (
    frontend_available,
    package_frontend_dir,
    package_frontend_fallback_file,
)


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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
uv run pytest tests/test_workbench_static_frontend.py -q
```

Expected: FAIL because `seektalent_ui.resources` does not exist.

- [ ] **Step 3: Implement resource helpers**

Create `src/seektalent_ui/resources.py`:

```python
from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent


def package_frontend_dir() -> Path:
    return PACKAGE_ROOT / "static" / "workbench"


def package_frontend_fallback_file() -> Path:
    return package_frontend_dir() / "200.html"


def frontend_available(root: Path | None = None) -> bool:
    frontend_root = root or package_frontend_dir()
    return (frontend_root / "200.html").is_file() and (frontend_root / "_app").is_dir()
```

Create `src/seektalent_ui/static/.gitkeep` as an empty file.

- [ ] **Step 4: Run the resource tests**

Run:

```bash
uv run pytest tests/test_workbench_static_frontend.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent_ui/resources.py src/seektalent_ui/static/.gitkeep tests/test_workbench_static_frontend.py
git commit -m "feat: add packaged workbench resource boundary"
```

### Task 2: Serve Packaged Frontend From FastAPI

**Files:**
- Modify: `src/seektalent_ui/server.py`
- Modify: `src/seektalent_ui/network_guard.py`
- Test: `tests/test_workbench_static_frontend.py`
- Test: `tests/test_workbench_network_guard.py`

- [ ] **Step 1: Add failing static serving tests**

Append to `tests/test_workbench_static_frontend.py`:

```python
from fastapi.testclient import TestClient

from seektalent_ui.server import create_app
from tests.settings_factory import make_settings


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
```

Append to `tests/test_workbench_network_guard.py`:

```python
def test_host_guard_rejects_unknown_hosts_for_packaged_frontend_routes(tmp_path, monkeypatch) -> None:
    frontend_root = tmp_path / "frontend"
    (frontend_root / "_app" / "immutable").mkdir(parents=True)
    (frontend_root / "200.html").write_text("<html>SeekTalent Workbench</html>", encoding="utf-8")
    monkeypatch.setattr("seektalent_ui.server.package_frontend_dir", lambda: frontend_root)
    settings = make_settings(workspace_root=str(tmp_path), mock_cts=True)
    guard = build_network_guard(
        bind_host="0.0.0.0",
        port=8011,
        lan_enabled=True,
        allowed_hosts={"recruiting.internal"},
    )
    client = TestClient(
        create_app(settings=settings, network_guard=guard, serve_frontend=True),
        base_url="http://recruiting.internal",
        client=("203.0.113.10", 50000),
    )

    rejected_root = client.get("/", headers={"Host": "evil.example"})
    rejected_spa = client.get("/sessions/session-1", headers={"Host": "evil.example"})
    allowed_spa = client.get("/sessions/session-1", headers={"Host": "recruiting.internal"})

    assert rejected_root.status_code == 403
    assert rejected_spa.status_code == 403
    assert allowed_spa.status_code == 200
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
uv run pytest tests/test_workbench_static_frontend.py tests/test_workbench_network_guard.py::test_host_guard_rejects_unknown_hosts_for_packaged_frontend -q
```

Expected: FAIL because `create_app` has no `serve_frontend` parameter and static frontend paths are not guarded.

- [ ] **Step 3: Update network guard**

In `src/seektalent_ui/network_guard.py`, replace the guarded-prefix handling with helpers that distinguish API Workbench routes from packaged frontend fallback routes:

```python
WORKBENCH_GUARDED_PREFIXES = ("/api/auth", "/api/workbench")
```

Replace `is_workbench_path` and add `is_guarded_workbench_path`:

```python
def is_workbench_path(path: str) -> bool:
    return path.startswith(WORKBENCH_GUARDED_PREFIXES)


def is_packaged_frontend_path(path: str) -> bool:
    return path != "/api" and not path.startswith("/api/")


def is_guarded_workbench_path(path: str, *, serve_frontend: bool = False) -> bool:
    return is_workbench_path(path) or (serve_frontend and is_packaged_frontend_path(path))
```

- [ ] **Step 4: Update server static serving**

In `src/seektalent_ui/server.py`, update imports:

```python
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from seektalent_ui.resources import frontend_available, package_frontend_dir
```

Replace the `is_workbench_path` import with `is_guarded_workbench_path`.

Change the `create_app` signature to:

```python
def create_app(
    settings: AppSettings | None = None,
    *,
    runtime_factory=WorkflowRuntime,
    network_guard: NetworkGuard | None = None,
    dev_mode_env_diagnostics: DevModeStatus | None = None,
    serve_frontend: bool = False,
) -> FastAPI:
```

Add this helper above `main`:

```python
def mount_packaged_frontend(app: FastAPI) -> None:
    frontend_root = package_frontend_dir()
    if not frontend_available(frontend_root):
        return
    app.mount("/_app", StaticFiles(directory=frontend_root / "_app"), name="workbench_static")

    @app.get("/", include_in_schema=False)
    @app.get("/{path:path}", include_in_schema=False)
    async def packaged_frontend(path: str = "") -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found.")
        candidate = (frontend_root / path).resolve(strict=False)
        resolved_root = frontend_root.resolve(strict=False)
        if candidate.is_file() and (candidate == resolved_root or resolved_root in candidate.parents):
            return FileResponse(candidate)
        return FileResponse(frontend_root / "200.html")
```

Call it immediately before `return app` inside `create_app`:

```python
    if serve_frontend:
        mount_packaged_frontend(app)

    return app
```

Update the middleware guard condition:

```python
        if not is_guarded_workbench_path(request.url.path, serve_frontend=serve_frontend):
            return await call_next(request)
```

- [ ] **Step 5: Run the tests**

Run:

```bash
uv run pytest tests/test_workbench_static_frontend.py tests/test_workbench_network_guard.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent_ui/server.py src/seektalent_ui/network_guard.py tests/test_workbench_static_frontend.py tests/test_workbench_network_guard.py
git commit -m "feat: serve packaged workbench frontend"
```

### Task 3: Build Frontend Assets Into The Python Package

**Files:**
- Create: `scripts/build_packaged_workbench.py`
- Modify: `tests/test_cli_packaging.py`
- Generated release asset: `src/seektalent_ui/static/workbench/**`

- [ ] **Step 1: Add failing wheel-content assertions**

In `tests/test_cli_packaging.py`, after `archive_names = set(archive.namelist())`, add:

```python
    assert "seektalent_ui/static/workbench/200.html" in archive_names
    assert any(name.startswith("seektalent_ui/static/workbench/_app/") for name in archive_names)
    assert not any(name.startswith("seektalent_ui/static/workbench/") and name.endswith(".map") for name in archive_names)
```

- [ ] **Step 2: Run the packaging test and verify it fails**

Run:

```bash
uv run pytest tests/test_cli_packaging.py::test_built_wheel_runs_outside_repo -q
```

Expected: FAIL because the wheel does not contain packaged frontend assets.

- [ ] **Step 3: Add the release build script**

Create `scripts/build_packaged_workbench.py`:

```python
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "apps" / "web-svelte"
WEB_BUILD_DIR = WEB_DIR / "build"
PACKAGE_FRONTEND_DIR = ROOT / "src" / "seektalent_ui" / "static" / "workbench"


def main() -> int:
    if shutil.which("bun") is None:
        raise SystemExit("bun is required to build the packaged Workbench frontend.")
    subprocess.run(["bun", "install", "--frozen-lockfile"], cwd=WEB_DIR, check=True)
    subprocess.run(["bun", "run", "build"], cwd=WEB_DIR, check=True)
    _copy_frontend()
    _validate_frontend()
    print(f"Packaged Workbench frontend written to {PACKAGE_FRONTEND_DIR}")
    return 0


def _copy_frontend() -> None:
    if PACKAGE_FRONTEND_DIR.exists():
        shutil.rmtree(PACKAGE_FRONTEND_DIR)
    shutil.copytree(WEB_BUILD_DIR, PACKAGE_FRONTEND_DIR)


def _validate_frontend() -> None:
    required = [
        PACKAGE_FRONTEND_DIR / "200.html",
        PACKAGE_FRONTEND_DIR / "_app",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"Packaged frontend is incomplete: {', '.join(missing)}")
    source_maps = sorted(PACKAGE_FRONTEND_DIR.rglob("*.map"))
    if source_maps:
        joined = ", ".join(str(path.relative_to(PACKAGE_FRONTEND_DIR)) for path in source_maps[:5])
        raise SystemExit(f"Packaged frontend must not include source maps: {joined}")


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Keep generated frontend assets as release assets**

Do not add `src/seektalent_ui/static/workbench/` to `.gitignore`. The built frontend is a release artifact that must be present in both the wheel and the sdist uploaded to PyPI. The release builder refreshes the directory before each release, and the generated files are committed with the release branch.

- [ ] **Step 5: Build frontend assets and rerun packaging test**

Run:

```bash
python scripts/build_packaged_workbench.py
uv run pytest tests/test_cli_packaging.py::test_built_wheel_runs_outside_repo -q
```

Expected: PASS. The working tree now contains generated files under `src/seektalent_ui/static/workbench/`, and the wheel contains the same files without source maps.

- [ ] **Step 6: Commit**

```bash
git add scripts/build_packaged_workbench.py tests/test_cli_packaging.py src/seektalent_ui/static/.gitkeep src/seektalent_ui/static/workbench
git commit -m "build: package workbench frontend assets"
```

### Task 4: Add `seektalent workbench` Packaged Launcher

**Files:**
- Modify: `src/seektalent/cli.py`
- Modify: `src/seektalent_ui/server.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_cli_packaging.py`

- [ ] **Step 1: Add failing CLI tests**

In `tests/test_cli.py`, add:

```python
def test_workbench_command_is_in_inspect_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inspect", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)

    assert "workbench" in payload["commands"]
    assert payload["local_product"]["default_frontend"] == "packaged_static"


def test_workbench_help_uses_packaged_launcher(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["workbench", "--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "Start the local SeekTalent Workbench" in output
    assert "--host" in output
    assert "--port" in output


def test_workbench_command_runs_packaged_frontend_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_server_main(argv):
        calls.append(argv)
        return 0

    monkeypatch.setattr("seektalent_ui.server.main", fake_server_main)

    assert main(["workbench", "--port", "8123"]) == 0

    argv = calls[0]
    assert "--serve-frontend" in argv
    assert argv[argv.index("--runtime-mode") + 1] == "prod"
    assert argv[argv.index("--port") + 1] == "8123"
```

In `tests/test_cli_packaging.py`, after the `inspect_result` block, add:

```python
    workbench_help = subprocess.run(
        [str(cli), "workbench", "--help"],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Start the local SeekTalent Workbench" in workbench_help.stdout

    serve_check = subprocess.run(
        [
            str(python),
            "-c",
            "from pathlib import Path\n"
            "from fastapi.testclient import TestClient\n"
            "from seektalent.config import AppSettings\n"
            "from seektalent_ui.server import create_app\n"
            "settings = AppSettings(_env_file=None, runtime_mode='prod', workspace_root=str(Path.cwd()), "
            "mock_cts=True, text_llm_api_key='test-key', cts_tenant_key='cts-key', cts_tenant_secret='cts-secret')\n"
            "response = TestClient(create_app(settings=settings, serve_frontend=True)).get('/')\n"
            "assert response.status_code == 200, response.text\n"
            "assert '<html' in response.text.lower()\n"
            "print('ok')\n",
        ],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert serve_check.stdout.strip() == "ok"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_cli.py::test_workbench_command_is_in_inspect_json tests/test_cli.py::test_workbench_help_uses_packaged_launcher tests/test_cli.py::test_workbench_command_runs_packaged_frontend_in_prod -q
```

Expected: FAIL because `workbench` is not a CLI command.

- [ ] **Step 3: Add server argv support for serving frontend**

In `src/seektalent_ui/server.py`, add this parser option:

```python
    parser.add_argument("--serve-frontend", action="store_true", help="Serve packaged Workbench static frontend.")
    parser.add_argument("--runtime-mode", choices=["dev", "prod"], default=None)
```

Change both `AppSettings().with_overrides(...)` calls in `main` to include:

```python
            runtime_mode=args.runtime_mode,
```

Change the app creation in `main`:

```python
                serve_frontend=args.serve_frontend,
```

- [ ] **Step 4: Add workbench command in CLI**

In `src/seektalent/cli.py`, add `"workbench"` to `KNOWN_COMMANDS`.

Add this command handler near `_inspect_command`:

```python
def _workbench_command(args: argparse.Namespace) -> int:
    from seektalent_ui.server import main as server_main

    argv = [
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--runtime-mode",
        "prod",
        "--serve-frontend",
    ]
    if args.lan:
        argv.append("--lan")
    for host in args.allowed_host or []:
        argv.extend(["--allowed-host", host])
    for origin in args.allowed_origin or []:
        argv.extend(["--allowed-origin", origin])
    return server_main(argv)
```

In `build_exec_parser`, add:

```python
    workbench_parser = subparsers.add_parser(
        "workbench",
        help="Start the local SeekTalent Workbench with packaged frontend.",
        description="Start the local SeekTalent Workbench.",
    )
    workbench_parser.add_argument("--host", default="127.0.0.1")
    workbench_parser.add_argument("--port", type=int, default=8011)
    workbench_parser.add_argument("--lan", action="store_true")
    workbench_parser.add_argument("--allowed-host", action="append", default=[])
    workbench_parser.add_argument("--allowed-origin", action="append", default=[])
    workbench_parser.set_defaults(handler=_workbench_command)
```

In `_inspect_payload`, include `workbench` in the command specs and set:

```python
        "default_frontend": "packaged_static",
```

for the local product payload when packaged static frontend is available.

- [ ] **Step 5: Run tests**

Run:

```bash
uv run pytest tests/test_cli.py tests/test_cli_packaging.py::test_built_wheel_runs_outside_repo -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/cli.py src/seektalent_ui/server.py tests/test_cli.py tests/test_cli_packaging.py
git commit -m "feat: add packaged workbench launcher"
```

### Task 5: Make Packaged Init Three Lines

**Files:**
- Modify: `src/seektalent/default.env`
- Modify: `src/seektalent/resources.py`
- Modify: `tests/test_resources.py`
- Modify: `tests/test_cli_packaging.py`
- Docs: `README.md`, `README.zh-CN.md`, `docs/configuration.md`

- [ ] **Step 1: Add failing tests for packaged minimal env**

Replace the mirror assertion in `tests/test_resources.py` with:

```python
def test_source_env_example_remains_full_dev_template() -> None:
    text = Path(".env.example").read_text(encoding="utf-8")

    assert "SEEKTALENT_MAX_ROUNDS" in text
    assert "SEEKTALENT_TEXT_LLM_API_KEY" in text


def test_packaged_default_env_is_minimal_user_template() -> None:
    text = package_env_example_file().read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]

    assert lines == [
        "SEEKTALENT_TEXT_LLM_API_KEY=",
        "SEEKTALENT_CTS_TENANT_KEY=",
        "SEEKTALENT_CTS_TENANT_SECRET=",
    ]
```

In `tests/test_cli_packaging.py`, after `seektalent init`, add:

```python
    env_lines = [
        line for line in (work_dir / ".env").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert env_lines == [
        "SEEKTALENT_TEXT_LLM_API_KEY=",
        "SEEKTALENT_CTS_TENANT_KEY=",
        "SEEKTALENT_CTS_TENANT_SECRET=",
    ]
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
uv run pytest tests/test_resources.py tests/test_cli_packaging.py::test_built_wheel_runs_outside_repo -q
```

Expected: FAIL because packaged `default.env` is still a full template.

- [ ] **Step 3: Replace packaged default env**

Set `src/seektalent/default.env` to exactly:

```env
SEEKTALENT_TEXT_LLM_API_KEY=
SEEKTALENT_CTS_TENANT_KEY=
SEEKTALENT_CTS_TENANT_SECRET=
```

Leave `.env.example` unchanged as the source checkout developer template.

- [ ] **Step 4: Keep resource selection explicit**

In `src/seektalent/resources.py`, keep `read_env_example_template()` behavior:

```python
def env_example_template_file() -> Path:
    repo_file = repo_env_example_file()
    if repo_file.exists():
        return repo_file
    return package_env_example_file()
```

This means source checkout `seektalent init` uses `.env.example`; installed wheel `seektalent init` uses the three-line packaged template.

- [ ] **Step 5: Update docs**

In `README.md` and `docs/configuration.md`, replace any packaged setup language with this exact Markdown:

````markdown
For installed PyPI users, `seektalent init` writes a minimal `.env` with only three required values:

```env
SEEKTALENT_TEXT_LLM_API_KEY=
SEEKTALENT_CTS_TENANT_KEY=
SEEKTALENT_CTS_TENANT_SECRET=
```

All other runtime, output, cleanup, and model settings use product defaults. Source checkout developers should use `.env.example` for the full development configuration surface.
````

Mirror the same meaning in Chinese in `README.zh-CN.md`.

- [ ] **Step 6: Run tests**

Run:

```bash
uv run pytest tests/test_resources.py tests/test_cli_packaging.py::test_built_wheel_runs_outside_repo -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/seektalent/default.env src/seektalent/resources.py tests/test_resources.py tests/test_cli_packaging.py README.md README.zh-CN.md docs/configuration.md
git commit -m "feat: make packaged env init minimal"
```

### Task 6: Lock Prod Cleanup And Workbench Startup Trigger

**Files:**
- Modify: `src/seektalent/runtime/lifecycle.py`
- Modify: `src/seektalent_ui/server.py`
- Test: `tests/test_runtime_lifecycle.py`
- Test: `tests/test_workbench_static_frontend.py`

- [ ] **Step 1: Add failing cleanup trigger test**

Append to `tests/test_workbench_static_frontend.py`:

```python
def test_packaged_workbench_startup_runs_prod_cleanup(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def fake_cleanup(settings):
        calls.append((settings.runtime_mode, settings.enable_flywheel))

    monkeypatch.setattr("seektalent_ui.server.cleanup_runtime_artifacts", fake_cleanup)
    create_app(settings=make_settings(workspace_root=str(tmp_path), runtime_mode="prod"), serve_frontend=True)

    assert calls == [("prod", False)]
```

- [ ] **Step 2: Add retained database cleanup regression**

Append to `tests/test_runtime_lifecycle.py`:

```python
def test_prod_cleanup_keeps_workbench_corpus_and_backups(tmp_path: Path) -> None:
    settings = make_settings(
        runtime_mode="prod",
        workspace_root=str(tmp_path),
        artifacts_dir=str(tmp_path / "artifacts"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    retained_paths = [
        tmp_path / ".seektalent" / "workbench.sqlite3",
        tmp_path / ".seektalent" / "corpus.sqlite3",
        tmp_path / ".seektalent" / "backups" / "workbench.sqlite3",
    ]
    for path in retained_paths:
        _write_file(path)

    cleanup_runtime_artifacts(settings, now=datetime(2026, 5, 29, 12, 0, 0))

    for path in retained_paths:
        assert path.exists()
```

- [ ] **Step 3: Run tests and verify startup cleanup fails**

Run:

```bash
uv run pytest tests/test_workbench_static_frontend.py::test_packaged_workbench_startup_runs_prod_cleanup tests/test_runtime_lifecycle.py::test_prod_cleanup_keeps_workbench_corpus_and_backups -q
```

Expected: first test FAILS because `create_app` does not trigger cleanup; second test should PASS or expose an accidental cleanup bug.

- [ ] **Step 4: Trigger cleanup in server startup path**

In `src/seektalent_ui/server.py`, import:

```python
from seektalent.runtime.lifecycle import cleanup_runtime_artifacts
```

Inside `create_app`, after `app_settings = settings or AppSettings()`, add:

```python
    if serve_frontend and app_settings.runtime_mode == "prod":
        cleanup_runtime_artifacts(app_settings)
```

- [ ] **Step 5: Run lifecycle tests**

Run:

```bash
uv run pytest tests/test_runtime_lifecycle.py tests/test_workbench_static_frontend.py::test_packaged_workbench_startup_runs_prod_cleanup -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/runtime/lifecycle.py src/seektalent_ui/server.py tests/test_runtime_lifecycle.py tests/test_workbench_static_frontend.py
git commit -m "feat: run prod cleanup on workbench startup"
```

### Task 7: Document Privacy, Data Roots, And PyPI Release Commands

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/cli.md`
- Modify: `docs/cli.zh-CN.md`
- Modify: `docs/configuration.md`
- Modify: `docs/outputs.md`
- Modify: `docs/ui.md`

- [ ] **Step 1: Update CLI docs with packaged workbench**

In `docs/cli.md`, add `seektalent workbench` to the command table:

```markdown
| `seektalent workbench` | Start the local Workbench with the packaged frontend. |
```

Add a section:

````markdown
## `seektalent workbench`

Installed PyPI users run:

```bash
seektalent init
seektalent workbench
```

The command starts the FastAPI backend and serves the packaged Svelte Workbench from the same loopback origin. It does not require Bun, Node, Vite, or a repository checkout on the user's machine.
````

Mirror this in `docs/cli.zh-CN.md`.

- [ ] **Step 2: Update output/data root docs**

In `docs/outputs.md`, add a packaged prod table:

````markdown
## Packaged Prod Data Roots

Packaged `seektalent workbench` uses prod defaults under `~/.seektalent/` unless `SEEKTALENT_WORKSPACE_ROOT` is set.

| Data | Cleanup |
| --- | --- |
| `artifacts/runs/YYYY/MM/DD/run_*` | partitions older than 7 days are removed during startup/run cleanup |
| `artifacts/benchmark-executions/YYYY/MM/DD/benchmark_*` | partitions older than 7 days are removed during startup/run cleanup |
| `cache/` | exact LLM cache is cleared during cleanup |
| `workbench.sqlite3` | retained |
| `corpus.sqlite3` | retained |
| `backups/` | retained |
| `flywheel.sqlite3` | dev only by default |
````

- [ ] **Step 3: Update privacy docs**

In `docs/configuration.md`, add:

````markdown
## Privacy Defaults

`doctor`, `inspect --json`, cleanup, and Workbench startup do not upload local databases, provider cookies, browser sessions, raw resumes, or configured secrets. Runtime network calls are limited to the configured LLM provider and CTS provider. Remote eval logging through W&B/Weave is off by default and requires explicit configuration.
````

Mirror concise equivalents in `README.md` and `README.zh-CN.md`.

- [ ] **Step 4: Document release commands without publishing**

In `docs/development.md`, add:

````markdown
## PyPI Release Build

Build frontend assets before building Python distributions:

```bash
python scripts/build_packaged_workbench.py
uv build --clear
uv publish --dry-run
```

Publishing requires an explicit release gate:

```bash
uv publish --token "$PYPI_TOKEN"
```
````

- [ ] **Step 5: Run docs grep checks**

Run:

```bash
rg -n "Python-only or PyPI-style installs do not yet bundle|does not yet bundle the Node dependency tree|target packaged launcher" README.md README.zh-CN.md docs
```

Expected: no stale statement says PyPI does not bundle the Workbench frontend after this feature.

- [ ] **Step 6: Commit**

```bash
git add README.md README.zh-CN.md docs/cli.md docs/cli.zh-CN.md docs/configuration.md docs/outputs.md docs/ui.md docs/development.md
git commit -m "docs: document packaged workbench product contract"
```

### Task 8: Full Release Verification

**Files:**
- No code changes expected

- [ ] **Step 1: Run Python checks**

Run:

```bash
uv run ruff check src tests
uv run pytest tests/test_cli.py tests/test_cli_packaging.py tests/test_resources.py tests/test_runtime_lifecycle.py tests/test_workbench_static_frontend.py tests/test_workbench_network_guard.py -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend checks**

Run:

```bash
cd apps/web-svelte
bun run check
bun run lint
bun run test
bun run build
```

Expected: all commands PASS and `apps/web-svelte/build/200.html` exists.

- [ ] **Step 3: Build packaged frontend and distributions**

Run:

```bash
python scripts/build_packaged_workbench.py
uv build --clear
```

Expected: `dist/seektalent-0.6.4-py3-none-any.whl` and the sdist exist.

- [ ] **Step 4: Inspect wheel contents**

Run:

```bash
python - <<'PY'
import zipfile
from pathlib import Path

wheel = max(Path("dist").glob("seektalent-*.whl"))
with zipfile.ZipFile(wheel) as archive:
    names = set(archive.namelist())
print(wheel)
assert "seektalent_ui/static/workbench/200.html" in names
assert any(name.startswith("seektalent_ui/static/workbench/_app/") for name in names)
assert not any(name.startswith("seektalent_ui/static/workbench/") and name.endswith(".map") for name in names)
assert "seektalent/default.env" in names
PY
```

Expected: command exits 0.

- [ ] **Step 5: Dry-run publish**

Run:

```bash
uv publish --dry-run
```

Expected: command validates publishable distributions without upload.

- [ ] **Step 6: Stop before real PyPI upload**

Do not run the real upload in this implementation plan. Real upload requires the separate explicit command:

```bash
uv publish --token "$PYPI_TOKEN"
```

## Self-Review

- Spec coverage: covered bundled frontend assets, PyPI wheel verification, `seektalent workbench`, three-line config, prod cleanup trigger, retained DB policy, privacy docs, and explicit publish gate.
- Placeholder scan: no task uses unresolved placeholders; paths, commands, code snippets, and expected results are specified.
- Type consistency: new functions are consistently named `package_frontend_dir`, `package_frontend_fallback_file`, `frontend_available`, and `mount_packaged_frontend`; CLI command is consistently `workbench`.

## GSTACK REVIEW REPORT

Verdict: APPROVED FOR `fw-build` after plan amendments on 2026-05-29.

Engineering findings resolved:

- Fixed CLI help acceptance: `main(["workbench", "--help"])` must assert `SystemExit(0)` instead of return code 0 because the existing CLI uses argparse help semantics.
- Forced packaged launcher into prod mode: `seektalent workbench` now passes `--runtime-mode prod` instead of depending on PyPI install detection.
- Guarded packaged frontend SPA routes: Host/Origin protection now covers non-API frontend routes such as `/sessions/session-1`, not only `/` and `/_app`.
- Kept static fallback path testable and consistent: mounted frontend returns `frontend_root / "200.html"` so monkeypatched package roots and real package roots use the same path.
- Added wheel-serving verification from an installed package: packaging tests now instantiate the installed app with `serve_frontend=True` and assert `/` serves HTML.
- Blocked frontend source maps from release assets and wheel contents.

Design review: skipped. This plan packages and serves the existing Svelte Workbench and changes launch/setup behavior; it does not alter UI layout, visual design, interaction patterns, or copy in the frontend app.

Build gate: do not run real PyPI upload during `fw-build`; stop after `uv publish --dry-run` unless the user explicitly opens a separate release gate.
