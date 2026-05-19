# Pi + DokoBot Local Provisioning For Liepin Dev Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make local Liepin dev mode runnable and diagnosable through vanilla Pi with DokoBot MCP registered inside Pi, while preventing Runtime/Workbench from touching DokoBot directly.

**Architecture:** Add a small Pi local setup layer that can idempotently create a project-local `.pi/mcp.json`, inspect static setup without executing DokoBot, and expose optional live Pi capability probes separately. Runtime execution continues through `PiRpcAgentClient` only; DokoBot MCP remains a Pi-owned tool observed through Pi RPC events. Main Workbench UI uses recruiter-facing browser-channel copy; developer terms stay in settings/doctor/CLI diagnostics.

**Tech Stack:** Python 3.12, Pydantic settings, argparse CLI, FastAPI Workbench diagnostics, pytest, Svelte/Vitest status tests.

---

Linked spec: [2026-05-19-pi-dokobot-local-provisioning-liepin-dev-mode-design.md](../specs/2026-05-19-pi-dokobot-local-provisioning-liepin-dev-mode-design.md)

## File Structure

- Create `src/seektalent/providers/pi_agent/local_setup.py`
  - Owns project-local Pi MCP init and safe static diagnostics.
  - Reads only environment/config values and optional Pi-owned MCP config JSON.
  - Writes only workspace `.pi/mcp.json` when explicitly requested by `seektalent pi-agent init --project --write`.
  - Does not run DokoBot and does not import `DokoBotClient`.
- Modify `src/seektalent/config.py`
  - Add optional `liepin_pi_mcp_config_path`.
  - Normalize empty value and expose `liepin_pi_mcp_config_file_path`.
- Modify `src/seektalent/cli.py`
  - Add `seektalent pi-agent init --project --dry-run/--write`.
  - Add static `liepin_pi_local_setup` doctor check.
  - Add explicit `doctor --live-pi-agent --json` live capability probe.
  - Add `pi-agent` to the top-level CLI dispatch allowlist.
  - Respect `SEEKTALENT_WORKSPACE_ROOT` when settings cannot be constructed.
- Modify `src/seektalent/dev_mode.py`
  - Reuse `local_setup.py` in raw env and settings diagnostics.
  - Keep Pi/DokoBot wording in dev/settings diagnostics, not main recruiter Workbench.
- Modify `src/seektalent/providers/liepin/pi_executor.py`
  - Preserve observed-tool-only capability proof.
  - Map missing observed tools to `liepin_pi_dokobot_tool_unobserved`.
- Modify `src/seektalent/providers/liepin/runtime_lane.py`
  - Recognize new safe setup reason codes in runtime source-state projection.
- Modify `src/seektalent_ui/workbench_routes.py`
  - Add new safe setup/browser reason codes to `RUNTIME_SOURCE_REASON_CODES`.
  - Preserve those codes through `runtimeSourceState.sources[].reasonCode`.
- Modify `src/seektalent/providers/liepin/pi_worker_client.py`
  - Preserve safe reason codes from capability/session probes instead of collapsing all setup failures.
- Modify `apps/web-svelte/src/lib/workbench/sourceDisplay.ts`
  - Add recruiter-facing copy for safe setup reason codes without `Pi`, `DokoBot`, or `MCP` terms in the main Workbench path.
- Modify `README.md`, `docs/configuration.md`, `.env.example`
  - Document the project-local Pi init command and the Pi-owned MCP boundary.
- Modify `TODOS.md`
  - Capture deferred cleanup of legacy direct DokoBot CLI diagnostics if those modules remain.
  - Capture broader deferred platform work that should not block this slice.

## Task 1: Add Project-Local Pi MCP Init And Static Setup Diagnostics

**Files:**
- Create: `src/seektalent/providers/pi_agent/local_setup.py`
- Test: `tests/test_pi_dokobot_local_setup.py`

- [ ] **Step 1: Write failing setup/init tests**

Create `tests/test_pi_dokobot_local_setup.py` with tests covering all of these behaviors:

```python
from __future__ import annotations

import json
from pathlib import Path

from seektalent.providers.pi_agent.local_setup import (
    build_pi_agent_local_setup_status,
    init_project_pi_mcp_config,
)


def test_init_dry_run_does_not_write_project_mcp_config(tmp_path: Path) -> None:
    result = init_project_pi_mcp_config(
        workspace_root=tmp_path,
        dokobot_tool_name="dokobot",
        write=False,
    )

    assert result.status == "needs_write"
    assert result.reason_code == "liepin_pi_mcp_config_missing"
    assert not (tmp_path / ".pi" / "mcp.json").exists()
    assert str(tmp_path) not in json.dumps(result.to_public_payload())


def test_init_write_creates_project_mcp_config(tmp_path: Path) -> None:
    result = init_project_pi_mcp_config(
        workspace_root=tmp_path,
        dokobot_tool_name="dokobot",
        write=True,
    )

    config = tmp_path / ".pi" / "mcp.json"
    payload = json.loads(config.read_text(encoding="utf-8"))
    assert result.status == "written"
    assert payload == {
        "mcpServers": {
            "dokobot": {
                "command": "dokobot",
                "args": [],
            }
        }
    }


def test_init_preserves_existing_mcp_servers(tmp_path: Path) -> None:
    config = tmp_path / ".pi" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"mcpServers": {"other": {"command": "other", "args": ["--x"]}}}),
        encoding="utf-8",
    )

    init_project_pi_mcp_config(
        workspace_root=tmp_path,
        dokobot_tool_name="dokobot",
        write=True,
    )

    payload = json.loads(config.read_text(encoding="utf-8"))
    assert payload["mcpServers"]["other"] == {"command": "other", "args": ["--x"]}
    assert payload["mcpServers"]["dokobot"] == {"command": "dokobot", "args": []}


def test_init_write_refuses_invalid_existing_mcp_config_without_overwriting(tmp_path: Path) -> None:
    config = tmp_path / ".pi" / "mcp.json"
    config.parent.mkdir(parents=True)
    original = "{not-json"
    config.write_text(original, encoding="utf-8")

    result = init_project_pi_mcp_config(
        workspace_root=tmp_path,
        dokobot_tool_name="dokobot",
        write=True,
    )

    assert result.status == "blocked"
    assert result.reason_code == "liepin_pi_mcp_config_invalid"
    assert config.read_text(encoding="utf-8") == original
    public = json.dumps(result.to_public_payload())
    assert str(tmp_path) not in public
    assert original not in public


def test_init_refuses_user_global_pi_config_path(tmp_path: Path) -> None:
    outside = Path.home() / ".pi" / "agent" / "mcp.json"

    result = init_project_pi_mcp_config(
        workspace_root=tmp_path,
        dokobot_tool_name="dokobot",
        write=True,
        mcp_config_path=outside,
    )

    assert result.status == "blocked"
    assert result.reason_code == "liepin_pi_mcp_config_not_project_local"


def test_reports_missing_pi_executable_without_running_dokobot(tmp_path: Path) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": "missing-pi --mode rpc --no-session",
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": "dokobot",
        },
        workspace_root=tmp_path,
        which=lambda _name: None,
    )

    assert status.overall_status == "needs_setup"
    assert status.reason_code == "liepin_pi_command_missing"
    assert status.components["pi_command"].status == "needs_setup"
    assert "missing-pi" not in json.dumps(status.to_public_payload())


def test_reports_invalid_pi_mcp_config(tmp_path: Path) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")
    config = tmp_path / ".pi" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text("{not-json", encoding="utf-8")

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": "pi --mode rpc --no-session",
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": "dokobot",
            "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": str(config),
        },
        workspace_root=tmp_path,
        which=lambda name: "/usr/local/bin/pi" if name == "pi" else None,
    )

    assert status.overall_status == "invalid"
    assert status.reason_code == "liepin_pi_mcp_config_invalid"
    assert status.components["dokobot_mcp"].reason_code == "liepin_pi_mcp_config_invalid"


def test_reports_missing_dokobot_server_in_pi_mcp_config(tmp_path: Path) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")
    config = tmp_path / ".pi" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"mcpServers": {"other": {"command": "other"}}}), encoding="utf-8")

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": "pi --mode rpc --no-session",
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": "dokobot",
            "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": str(config),
        },
        workspace_root=tmp_path,
        which=lambda name: "/usr/local/bin/pi" if name == "pi" else None,
    )

    assert status.overall_status == "needs_setup"
    assert status.reason_code == "liepin_pi_dokobot_mcp_missing"
    assert status.components["dokobot_mcp"].reason_code == "liepin_pi_dokobot_mcp_missing"


def test_reports_configured_when_pi_and_dokobot_mcp_are_declared(tmp_path: Path) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")
    config = tmp_path / ".pi" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"mcpServers": {"dokobot": {"command": "dokobot", "args": []}}}),
        encoding="utf-8",
    )

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": "pi --mode rpc --no-session",
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": "dokobot",
            "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": str(config),
        },
        workspace_root=tmp_path,
        which=lambda name: f"/usr/local/bin/{name}" if name == "pi" else None,
    )

    public = status.to_public_payload()
    assert status.overall_status == "configured"
    assert public["components"]["pi_command"]["status"] == "configured"
    assert public["components"]["dokobot_mcp"]["status"] == "configured"
    assert str(tmp_path) not in json.dumps(public)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_pi_dokobot_local_setup.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'seektalent.providers.pi_agent.local_setup'`.

- [ ] **Step 3: Implement `local_setup.py`**

Create `src/seektalent/providers/pi_agent/local_setup.py` with these concrete contracts:

- `PiAgentLocalSetupStatus.to_public_payload()` returns only `overallStatus`, `reasonCode`, and component status/reason codes.
- `PiMcpInitResult.to_public_payload()` returns only `status`, `reasonCode`, `changed`, and `target="project"`.
  - It may also return `operations`, limited to safe enum-like values such as `create_config`, `add_dokobot_server`, `update_dokobot_server`, and `no_change`.
- `build_pi_agent_local_setup_status(...)` checks:
  - worker mode
  - account binding secret presence and non-placeholder value
  - Pi command is parseable and contains `--mode rpc --no-session`
  - Pi executable resolves via injected `which`
  - Liepin skill path exists
  - project/default MCP config exists and is valid JSON
  - expected DokoBot server exists and has a non-empty command
- `init_project_pi_mcp_config(...)`:
  - defaults target path to `workspace_root / ".pi" / "mcp.json"`
  - refuses paths outside `workspace_root / ".pi"`
  - refuses invalid existing MCP JSON with `status="blocked"` and `reason_code="liepin_pi_mcp_config_invalid"` without overwriting the file
  - preserves existing `mcpServers`
  - writes `{"command": "dokobot", "args": []}` for the expected server in this slice
  - writes only when `write=True`
  - reports safe `operations` in dry-run/write results without dumping raw config or paths
  - never calls `subprocess`, `dokobot`, MCP, Chrome, or Pi

Use `seektalent.resources.resolve_path_from_root(...)` for relative paths. Keep helper names literal: `_env_value`, `_resolve_optional_path`, `_arg_value`, `_dokobot_mcp_component`, `_summarize`.

Do not add new env vars for server name vs command binary in this slice. Treat `SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME` as the expected Pi tool prefix and MCP server key. The generated command is the literal `dokobot`; alternate command forms are deferred.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_pi_dokobot_local_setup.py -q
```

Expected: PASS.

## Task 2: Add Config And CLI Surfaces

**Files:**
- Modify: `src/seektalent/config.py`
- Modify: `src/seektalent/cli.py`
- Test: `tests/test_liepin_config.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing config tests**

Append to `tests/test_liepin_config.py`:

```python
def test_pi_agent_accepts_optional_mcp_config_path(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    mcp_path = tmp_path / ".pi" / "mcp.json"
    skill_path.write_text("Liepin skill", encoding="utf-8")
    settings = AppSettings(
        _env_file=None,
        workspace_root=str(tmp_path),
        liepin_worker_mode="pi_agent",
        liepin_pi_command="pi --mode rpc --no-session",
        liepin_pi_skill_path=str(skill_path),
        liepin_pi_mcp_config_path=str(mcp_path),
        liepin_pi_dokobot_tool_name="dokobot",
        liepin_account_binding_secret="non-placeholder-secret",
    )

    assert settings.liepin_pi_mcp_config_file_path == mcp_path


def test_empty_pi_mcp_config_path_normalizes_to_none(tmp_path: Path) -> None:
    settings = AppSettings(
        _env_file=None,
        workspace_root=str(tmp_path),
        liepin_pi_mcp_config_path="",
    )

    assert settings.liepin_pi_mcp_config_path is None
```

- [ ] **Step 2: Add settings field**

In `src/seektalent/config.py`:

- add `liepin_pi_mcp_config_path: str | None = None`
- add `"liepin_pi_mcp_config_path"` to the existing empty-string normalizer field list
- add:

```python
@property
def liepin_pi_mcp_config_file_path(self) -> Path | None:
    if self.liepin_pi_mcp_config_path is None:
        return None
    return self.resolve_workspace_path(self.liepin_pi_mcp_config_path)
```

- [ ] **Step 3: Write failing CLI init and doctor tests**

Append to `tests/test_cli.py`:

```python
def test_pi_agent_init_dry_run_does_not_write_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["pi-agent", "init", "--project", "--workspace-root", str(tmp_path), "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "needs_write"
    assert payload["operations"] == ["create_config", "add_dokobot_server"]
    assert not (tmp_path / ".pi" / "mcp.json").exists()
    assert str(tmp_path) not in json.dumps(payload)


def test_pi_agent_init_write_creates_project_mcp_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["pi-agent", "init", "--project", "--workspace-root", str(tmp_path), "--write", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "written"
    assert payload["operations"] == ["create_config", "add_dokobot_server"]
    assert (tmp_path / ".pi" / "mcp.json").is_file()
    assert str(tmp_path) not in json.dumps(payload)


def test_doctor_json_reports_pi_local_setup_without_leaking_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"SEEKTALENT_WORKSPACE_ROOT={tmp_path}",
                "SEEKTALENT_LIEPIN_WORKER_MODE=pi_agent",
                "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET=account-secret",
                "SEEKTALENT_LIEPIN_PI_COMMAND=missing-pi --mode rpc --no-session",
                f"SEEKTALENT_LIEPIN_PI_SKILL_PATH={skill.name}",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", "")

    assert main(["doctor", "--env-file", str(env_file), "--output-dir", str(tmp_path / "runs"), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    setup_check = checks["liepin_pi_local_setup"]

    assert setup_check["ok"] is False
    assert "liepin_pi_command_missing" in setup_check["message"]
    assert str(tmp_path) not in json.dumps(setup_check)
    assert "account-secret" not in json.dumps(payload)


def test_doctor_resolves_relative_pi_paths_against_env_workspace_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")
    config = tmp_path / ".pi" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"mcpServers":{"dokobot":{"command":"dokobot","args":[]}}}', encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"SEEKTALENT_WORKSPACE_ROOT={tmp_path}",
                "SEEKTALENT_LIEPIN_WORKER_MODE=pi_agent",
                "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET=account-secret",
                "SEEKTALENT_LIEPIN_PI_COMMAND=pi --mode rpc --no-session",
                "SEEKTALENT_LIEPIN_PI_SKILL_PATH=liepin_search_cards.md",
                "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH=.pi/mcp.json",
            ]
        ),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pi_bin = bin_dir / "pi"
    pi_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    pi_bin.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    main(["doctor", "--env-file", str(env_file), "--output-dir", str(tmp_path / "runs"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}

    assert "reason=configured" in checks["liepin_pi_local_setup"]["message"]


def test_doctor_pi_setup_runs_when_settings_fail_for_missing_secret(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"SEEKTALENT_WORKSPACE_ROOT={tmp_path}",
                "SEEKTALENT_LIEPIN_WORKER_MODE=pi_agent",
                "SEEKTALENT_LIEPIN_PI_COMMAND=pi --mode rpc --no-session",
                "SEEKTALENT_LIEPIN_PI_SKILL_PATH=liepin_search_cards.md",
            ]
        ),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pi_bin = bin_dir / "pi"
    pi_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    pi_bin.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    assert main(["doctor", "--env-file", str(env_file), "--output-dir", str(tmp_path / "runs"), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}

    assert "liepin_pi_account_secret_missing" in checks["liepin_pi_local_setup"]["message"]


def test_doctor_pi_setup_reports_invalid_command_when_settings_fail(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"SEEKTALENT_WORKSPACE_ROOT={tmp_path}",
                "SEEKTALENT_LIEPIN_WORKER_MODE=pi_agent",
                "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET=account-secret",
                "SEEKTALENT_LIEPIN_PI_COMMAND=pi --mode json --no-session",
            ]
        ),
        encoding="utf-8",
    )

    assert main(["doctor", "--env-file", str(env_file), "--output-dir", str(tmp_path / "runs"), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}

    assert "liepin_pi_command_invalid" in checks["liepin_pi_local_setup"]["message"]
```

- [ ] **Step 4: Implement CLI surfaces**

In `src/seektalent/cli.py`:

- add a `pi-agent` subparser with `init`
- add `"pi-agent"` to `KNOWN_COMMANDS`; otherwise `main(["pi-agent", ...])` will not route to the exec parser
- require `--project` for `pi-agent init`; do not add global/user target flags
- support exactly one of `--dry-run` or `--write`; default to dry-run only if existing CLI style already uses default-safe behavior
- support `--workspace-root`, defaulting to the loaded workspace root or `Path.cwd()`
- call `init_project_pi_mcp_config(...)`
- add `PI_LOCAL_SETUP_ENV_KEYS`, including:
  - `SEEKTALENT_WORKSPACE_ROOT`
  - `SEEKTALENT_LIEPIN_WORKER_MODE`
  - `SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET`
  - `SEEKTALENT_LIEPIN_PI_COMMAND`
  - `SEEKTALENT_LIEPIN_PI_SKILL_PATH`
  - `SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME`
  - `SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH`
- add `_doctor_workspace_root_for_pi_setup(args, settings, env)`:
  - use `settings.project_root` if settings exists
  - else use `SEEKTALENT_WORKSPACE_ROOT` from env-file/env when present
  - else use `Path.cwd()`
- add `_liepin_pi_local_setup_check(settings, env, workspace_root)` that returns `DoctorCheck("liepin_pi_local_setup", ...)`
- keep the static Pi setup check path-only safe: no raw paths, commands, or secrets in the `liepin_pi_local_setup` message. Do not try to make the whole existing `doctor --json` path-free in this slice; existing developer diagnostics may already show non-sensitive package/output/data-root paths.

- [ ] **Step 5: Add explicit live probe only**

Add `doctor --live-pi-agent --json`.

Do not add `seektalent pi-agent probe --json` in this slice. Keep the live probe surface to one explicit entry point; a dedicated `pi-agent probe` command can be revisited after the doctor live probe proves useful.

The live path may construct the configured Liepin Pi worker and call its existing capability/session probe. It must be opt-in and must not be implied by `doctor --json`. Tests should monkeypatch `seektalent.cli.build_liepin_worker_client` or the exact local factory used by CLI so the test does not launch a real Pi process.

Required tests:

- default `doctor --json` never calls the live worker factory
- `doctor --live-pi-agent --json` calls the fake worker factory and reports `liepin_pi_dokobot_tool_unobserved` when the fake returns that code
- live probe calls setup/capability readiness before session probing; capability failure stops with the setup reason instead of returning `liepin_browser_login_required`
- after capability readiness succeeds, non-ready Liepin session maps to `liepin_browser_login_required`, `liepin_browser_probe_unavailable`, or `liepin_browser_account_mismatch`
- live output still does not expose raw Pi events, local paths, cookies, or secrets

- [ ] **Step 6: Run config and CLI tests**

```bash
uv run pytest \
  tests/test_liepin_config.py::test_pi_agent_accepts_optional_mcp_config_path \
  tests/test_liepin_config.py::test_empty_pi_mcp_config_path_normalizes_to_none \
  tests/test_cli.py::test_pi_agent_init_dry_run_does_not_write_file \
  tests/test_cli.py::test_pi_agent_init_write_creates_project_mcp_file \
  tests/test_cli.py::test_doctor_json_reports_pi_local_setup_without_leaking_paths \
  tests/test_cli.py::test_doctor_resolves_relative_pi_paths_against_env_workspace_root \
  tests/test_cli.py::test_doctor_pi_setup_runs_when_settings_fail_for_missing_secret \
  tests/test_cli.py::test_doctor_pi_setup_reports_invalid_command_when_settings_fail \
  tests/test_cli.py::test_doctor_json_does_not_leak_provider_secrets \
  -q
```

Expected: PASS.

## Task 3: Add Workbench Dev Diagnostics For Static Pi MCP Setup

**Files:**
- Modify: `src/seektalent/dev_mode.py`
- Test: `tests/test_dev_mode_readiness.py`

- [ ] **Step 1: Write failing diagnostics tests**

Append to `tests/test_dev_mode_readiness.py`:

```python
def test_raw_env_diagnostics_reports_missing_pi_mcp_config(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("Liepin skill", encoding="utf-8")
    payload = build_dev_mode_env_diagnostics(
        {
            "SEEKTALENT_WORKSPACE_ROOT": str(tmp_path),
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": "pi --mode rpc --no-session",
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill_path),
            "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": str(tmp_path / ".pi" / "mcp.json"),
        },
        workspace_root=tmp_path,
    )
    components = {component.name: component for component in payload.components}

    assert payload.overallStatus == "needs_setup"
    assert components["liepin_pi_mcp_config"].status == "needs_setup"
    assert components["liepin_pi_mcp_config"].reasonCode == "liepin_pi_mcp_config_missing"


def test_raw_env_diagnostics_reports_missing_dokobot_mcp_server(tmp_path: Path) -> None:
    skill_path = tmp_path / "liepin_search_cards.md"
    skill_path.write_text("Liepin skill", encoding="utf-8")
    config = tmp_path / ".pi" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"mcpServers":{"other":{"command":"other"}}}', encoding="utf-8")

    payload = build_dev_mode_env_diagnostics(
        {
            "SEEKTALENT_WORKSPACE_ROOT": str(tmp_path),
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": "pi --mode rpc --no-session",
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill_path),
            "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": str(config),
            "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": "dokobot",
        },
        workspace_root=tmp_path,
    )
    components = {component.name: component for component in payload.components}

    assert components["liepin_pi_dokobot_mcp"].status == "needs_setup"
    assert components["liepin_pi_dokobot_mcp"].reasonCode == "liepin_pi_dokobot_mcp_missing"
    assert str(tmp_path) not in payload.model_dump_json()
```

- [ ] **Step 2: Implement static dev diagnostics**

In `src/seektalent/dev_mode.py`:

- import `build_pi_agent_local_setup_status`
- call it from `build_dev_mode_env_diagnostics(...)` with the raw env and provided `workspace_root`
- call it from `build_dev_mode_status(settings)` with a minimal env derived from settings
- append only setup components to dev diagnostics:
  - `liepin_pi_mcp_config`
  - `liepin_pi_dokobot_mcp`
  - optionally `liepin_pi_command` and `liepin_pi_skill` if not already represented
- keep labels diagnostic-facing, e.g. `Pi MCP config`, `DokoBot MCP`
- do not include file paths or command strings in the payload

This task does not add main Workbench UI content.

- [ ] **Step 3: Run readiness tests**

```bash
uv run pytest tests/test_dev_mode_readiness.py -q
```

Expected: PASS.

## Task 4: Preserve Precise Live Pi/DokoBot Capability Reason Codes

**Files:**
- Modify: `src/seektalent/providers/liepin/pi_executor.py`
- Modify: `src/seektalent/providers/liepin/pi_worker_client.py`
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Modify: `src/seektalent_ui/workbench_routes.py`
- Test: `tests/test_liepin_pi_executor.py`
- Test: `tests/test_liepin_pi_worker_client.py`
- Test: `tests/test_workbench_liepin_browser_session_probe.py`

- [ ] **Step 1: Write failing capability test**

Append to `tests/test_liepin_pi_executor.py`:

```python
def test_capability_probe_returns_precise_reason_when_dokobot_tools_not_observed() -> None:
    result_json = (
        '{"schema_version":"seektalent.pi_capability_probe.v1","status":"ready",'
        '"pi_version":"0.1.0","read_tool_name":"dokobot.read",'
        '"action_tool_names":["dokobot.navigate","dokobot.click","dokobot.type_text"],'
        '"proof_kind":"trusted_manifest_and_observed_tool_event",'
        '"capability_manifest_ref":"artifact://protected/pi-capability/run-1/manifest",'
        '"tool_evidence_ref":"artifact://protected/pi-capability/run-1/tool-events",'
        '"allowed_hosts":["liepin.com"],"stop_reason":null}'
    )
    executor = PiLiepinExecutor(
        client=_client(result_json, observed_tool_names=("dokobot.read",)),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/pi-capability/run-1/manifest",
            "artifact://protected/pi-capability/run-1/tool-events",
        ),
    )

    result = executor.probe_capabilities(expected_dokobot_tool_name="dokobot")

    assert result.ready is False
    assert result.safe_reason_code == "liepin_pi_dokobot_tool_unobserved"
```

- [ ] **Step 2: Implement precise capability result**

In `src/seektalent/providers/liepin/pi_executor.py`, keep schema/manifest failures mapped to `blocked_backend_unavailable`, but return `PiLiepinCapabilityProbeResult(ready=False, safe_reason_code="liepin_pi_dokobot_tool_unobserved")` when required DokoBot tools are declared but not observed in the Pi RPC event stream.

Use this distinction:

- `proof_kind`, artifact ref, host, or envelope validation failure: `blocked_backend_unavailable`
- declared tool set missing any required DokoBot tool: `blocked_backend_unavailable`
- declared tool set includes all required DokoBot tools but `task_result.observed_tool_names` misses at least one required tool: `liepin_pi_dokobot_tool_unobserved`

This prevents a fake self-report from passing while still giving a precise reason when Pi starts but DokoBot tool events are not observed.

- [ ] **Step 3: Ensure worker preserves safe reason**

Append to `tests/test_liepin_pi_worker_client.py`:

```python
def test_ensure_ready_preserves_unobserved_dokobot_tool_reason() -> None:
    executor = FakePiLiepinExecutor(
        capability=PiLiepinCapabilityProbeResult(
            ready=False,
            safe_reason_code="liepin_pi_dokobot_tool_unobserved",
        )
    )
    client = LiepinPiWorkerClient(
        executor=executor,
        session_id="session-1",
        connection_id="connection-1",
        provider_account_lock_key="lock-1",
        dokobot_tool_name="dokobot",
    )

    with pytest.raises(LiepinWorkerModeError) as exc_info:
        asyncio.run(client.ensure_ready())

    assert exc_info.value.code == "liepin_pi_dokobot_tool_unobserved"
```

If it fails, change `LiepinPiWorkerClient.ensure_ready(...)` to use `code=capability.safe_reason_code or "blocked_backend_unavailable"`.

- [ ] **Step 4: Preserve session probe reason semantics**

Add `tests/test_liepin_pi_worker_client.py` coverage for `session_status(...)`:

```python
def test_session_status_preserves_probe_unavailable_reason() -> None:
    client = _client(
        FakeExecutor(
            session_result=PiLiepinSessionProbeResult(
                status="failed",
                connection_id="connection-1",
                safe_reason_code="liepin_browser_probe_unavailable",
            )
        )
    )

    with pytest.raises(LiepinWorkerModeError) as exc_info:
        asyncio.run(client.session_status(connection_id="connection-1"))

    assert exc_info.value.code == "liepin_browser_probe_unavailable"
```

Then update `LiepinPiWorkerClient.session_status(...)` so:

- `ready` still returns `SessionStatus(status="ready", provider_account_hash=...)`
- `login_required`, `revoked`, and `missing` return `SessionStatus(status="login_required")` for legacy call sites, but live probe CLI/start-probe helpers can inspect or map `safe_reason_code` before flattening if needed
- `failed` with `safe_reason_code` raises `LiepinWorkerModeError(..., code=safe_reason_code)`

The live probe CLI added in Task 2 must call `ensure_ready()` first, then `session_status()`. A capability failure must stop with the Pi/DokoBot setup reason; only after capability readiness succeeds may the result become `liepin_browser_login_required`.

- [ ] **Step 5: Preserve runtime and Workbench reason codes**

In `src/seektalent/providers/liepin/runtime_lane.py`, add these codes to the existing safe reason normalization:

```python
{
    "liepin_pi_disabled",
    "liepin_pi_command_missing",
    "liepin_pi_command_invalid",
    "liepin_pi_skill_missing",
    "liepin_pi_account_secret_missing",
    "liepin_pi_mcp_config_missing",
    "liepin_pi_mcp_config_invalid",
    "liepin_pi_mcp_config_not_project_local",
    "liepin_pi_dokobot_mcp_missing",
    "liepin_pi_dokobot_tool_unobserved",
    "liepin_browser_login_required",
    "liepin_browser_probe_unavailable",
    "liepin_browser_account_mismatch",
}
```

In `src/seektalent_ui/workbench_routes.py`, add the same public source-run reason codes to `RUNTIME_SOURCE_REASON_CODES`. Without this, `_runtime_source_reason_code(...)` drops the new code even if Runtime preserved it.

Extend `tests/test_workbench_liepin_browser_session_probe.py` so blocked Liepin source runs with `liepin_pi_command_missing`, `liepin_pi_mcp_config_missing`, and `liepin_pi_dokobot_tool_unobserved` keep that reason in `session.runtimeSourceState.sources[].reasonCode`.

- [ ] **Step 6: Add CTS independence regression**

Add a session/source-run level test in `tests/test_workbench_liepin_browser_session_probe.py`:

- create a session with `sourceKinds=["cts", "liepin"]`
- fake Liepin worker raises `LiepinWorkerModeError(code="liepin_pi_command_missing")`
- start the session
- assert CTS source run is queued/running/completed according to existing job-runner test conventions
- assert Liepin source run is blocked with `reasonCode="liepin_pi_command_missing"`
- assert the response/notes/source card payload do not contain `Pi`, `DokoBot`, or `MCP` in recruiter-facing copy

This guards the product requirement that CTS remains usable when the local browser channel is not configured.

- [ ] **Step 7: Run focused live capability tests**

```bash
uv run pytest \
  tests/test_liepin_pi_executor.py \
  tests/test_liepin_pi_worker_client.py \
  tests/test_workbench_liepin_browser_session_probe.py \
  -q
```

Expected: PASS.

## Task 5: Fence Runtime, Workbench, Liepin, And CLI Away From Direct DokoBot

**Files:**
- Modify: `tests/test_pi_agent_boundaries.py`
- Modify: `TODOS.md`

- [ ] **Step 1: Add failing static boundary test**

Append to `tests/test_pi_agent_boundaries.py`:

```python
import re
from pathlib import Path


def test_product_paths_do_not_import_or_execute_dokobot_directly() -> None:
    root = Path(__file__).resolve().parents[1]
    product_paths = [
        root / "src" / "seektalent" / "runtime",
        root / "src" / "seektalent_ui",
        root / "src" / "seektalent" / "providers" / "liepin",
        root / "src" / "seektalent" / "providers" / "registry.py",
        root / "src" / "seektalent" / "cli.py",
    ]
    forbidden_markers = (
        "from seektalent.providers.pi_agent.dokobot_client",
        "import seektalent.providers.pi_agent.dokobot_client",
        "DokoBotClient",
        "DokoBotCapabilityProbe",
        "DokoBotActionSurface",
        "DokoBotActionTransportSession",
        "dokobot_action",
    )
    raw_command_patterns = (
        re.compile(r"subprocess\.\w+\([^)]*[\"']dokobot[\"']"),
        re.compile(r"\[[\"']dokobot[\"']"),
    )
    offenders: list[str] = []
    for path in product_paths:
        files = [path] if path.is_file() else sorted(path.rglob("*.py"))
        for file_path in files:
            text = file_path.read_text(encoding="utf-8")
            for marker in forbidden_markers:
                if marker in text:
                    offenders.append(f"{file_path.relative_to(root)} contains {marker}")
            for pattern in raw_command_patterns:
                if pattern.search(text):
                    offenders.append(f"{file_path.relative_to(root)} directly executes dokobot")
    assert offenders == []
```

This test deliberately excludes `src/seektalent/providers/pi_agent/local_setup.py`, because writing a static `.pi/mcp.json` declaration is allowed. It includes `src/seektalent/cli.py`, because the CLI must not execute DokoBot.

- [ ] **Step 2: Remove product-path violations only**

If the test fails, remove only live product-path imports or command execution. Do not delete `src/seektalent/providers/pi_agent/dokobot_client.py` or `tests/test_dokobot_capabilities.py` in this task. Those are legacy diagnostics and must remain fenced until a separate cleanup.

- [ ] **Step 3: Capture deferred cleanup in `TODOS.md`**

Under `Runtime Multi-Source Platform Follow-Ups`, add one deferred item if it is not already present:

```markdown
- Legacy direct DokoBot CLI diagnostics cleanup: decide whether to remove or isolate `src/seektalent/providers/pi_agent/dokobot_client.py` and `capabilities.py` after the Pi-owned MCP path is stable; these modules must not become Runtime/Workbench live execution paths.
```

Run:

```bash
rg -n "Legacy direct DokoBot CLI diagnostics cleanup" TODOS.md
```

Expected: exactly one match.

- [ ] **Step 4: Run boundary test**

```bash
uv run pytest tests/test_pi_agent_boundaries.py::test_product_paths_do_not_import_or_execute_dokobot_directly -q
```

Expected: PASS.

## Task 6: Add Recruiter-Facing Main Workbench Copy

**Files:**
- Modify: `apps/web-svelte/src/lib/workbench/sourceDisplay.ts`
- Test: `apps/web-svelte/src/lib/workbench/sourceDisplay.test.ts`

- [ ] **Step 1: Add failing UI copy tests**

Create or extend `apps/web-svelte/src/lib/workbench/sourceDisplay.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';
import { sourceReasonLabel } from './sourceDisplay';

describe('sourceReasonLabel', () => {
  it('maps setup reasons to recruiter-facing browser-channel copy', () => {
    const labels = [
      sourceReasonLabel('liepin_pi_disabled'),
      sourceReasonLabel('liepin_pi_command_missing'),
      sourceReasonLabel('liepin_pi_mcp_config_missing'),
      sourceReasonLabel('liepin_pi_dokobot_mcp_missing'),
      sourceReasonLabel('liepin_pi_dokobot_tool_unobserved')
    ];

    for (const label of labels) {
      expect(label).toContain('浏览器');
      expect(label).not.toMatch(/Pi|DokoBot|MCP/);
    }
  });

  it('keeps Chrome login wording for Liepin login state', () => {
    expect(sourceReasonLabel('liepin_browser_login_required')).toContain('本机 Chrome 登录猎聘');
  });
});
```

- [ ] **Step 2: Add safe copy mappings**

In `apps/web-svelte/src/lib/workbench/sourceDisplay.ts`, extend `sourceReasonLabel(...)` with business-facing labels:

```typescript
const labels: Record<string, string> = {
  liepin_pi_disabled: '浏览器检索通道未启用，请到本机设置开启后重试。',
  liepin_pi_command_missing: '浏览器检索通道不可用，请到本机设置检查浏览器助手后重试。',
  liepin_pi_command_invalid: '浏览器检索通道配置无效，请到本机设置检查浏览器助手后重试。',
  liepin_pi_skill_missing: '浏览器检索通道缺少本地检索技能，请到本机设置检查后重试。',
  liepin_pi_account_secret_missing: '本地账号绑定尚未完成，请到本机设置检查后重试。',
  liepin_pi_mcp_config_missing: '浏览器检索通道尚未启用，请到本机设置检查浏览器助手后重试。',
  liepin_pi_mcp_config_invalid: '浏览器检索通道配置无法读取，请到本机设置检查后重试。',
  liepin_pi_dokobot_mcp_missing: '浏览器检索通道尚未接入本机浏览器助手，请到本机设置检查后重试。',
  liepin_pi_dokobot_tool_unobserved: '浏览器检索通道不可用，请到本机设置检查浏览器助手后重试。',
  liepin_browser_login_required: '请在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。',
  liepin_browser_probe_unavailable: '浏览器检索通道不可用，请确认本机应用和浏览器助手正常后重试。',
  liepin_browser_account_mismatch: '当前 Chrome 中的猎聘账号与此工作台绑定不一致，请切换账号后重试。'
};
```

Settings/dev diagnostics may still mention Pi and DokoBot MCP; this restriction is for recruiter-facing Workbench source cards/run notes.

- [ ] **Step 3: Run UI focused tests**

```bash
cd apps/web-svelte && npm run test -- sourceDisplay.test.ts SourceCard.test.ts runStory.test.ts
```

Expected: PASS.

## Task 7: Update Local Setup Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/configuration.md`
- Modify: `.env.example`
- Modify: `TODOS.md`

- [ ] **Step 1: Locate existing configuration docs**

```bash
rg -n "SEEKTALENT_LIEPIN_WORKER_MODE|liepin_pi|DokoBot|Pi" README.md docs .env.example
```

- [ ] **Step 2: Add the project-local Pi setup contract**

Add this text to `docs/configuration.md` under the Liepin/Pi settings section:

````markdown
### Liepin Pi + DokoBot local setup

Live Liepin browser search uses Pi as the external agent harness. DokoBot MCP is registered inside Pi; SeekTalent Runtime does not call DokoBot directly.

Initialize the project-local Pi MCP declaration:

```bash
seektalent pi-agent init --project --dry-run
seektalent pi-agent init --project --write
```

Required local settings:

```env
SEEKTALENT_LIEPIN_WORKER_MODE=pi_agent
SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET=replace-with-local-random-secret
SEEKTALENT_LIEPIN_PI_COMMAND=pi --mode rpc --no-session
SEEKTALENT_LIEPIN_PI_SKILL_PATH=src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md
SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME=dokobot
SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH=.pi/mcp.json
```

Expected project-local Pi MCP config:

```json
{
  "mcpServers": {
    "dokobot": {
      "command": "dokobot",
      "args": []
    }
  }
}
```

SeekTalent may write this project file when explicitly asked, but it does not edit `~/.pi/agent/mcp.json`, install Pi, install the DokoBot extension, or execute DokoBot directly. After adding or changing Pi MCP tools, restart Pi before relying on the live Liepin path because Pi may cache tool metadata at startup.
````

- [ ] **Step 3: Update README with a short pointer**

Add:

```markdown
### Local Liepin browser search

Liepin browser search runs through `pi_agent`: SeekTalent starts Pi in RPC mode with the repo-owned Liepin skill, and Pi uses DokoBot MCP from inside its own runtime. Runtime and Workbench never call DokoBot directly. Run `seektalent pi-agent init --project --dry-run` to inspect the project-local Pi MCP setup and `seektalent doctor --json` for static readiness diagnostics.
```

- [ ] **Step 4: Update `.env.example`**

Add the config keys without a real secret:

```env
SEEKTALENT_LIEPIN_WORKER_MODE=pi_agent
SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET=
SEEKTALENT_LIEPIN_PI_COMMAND=pi --mode rpc --no-session
SEEKTALENT_LIEPIN_PI_SKILL_PATH=src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md
SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME=dokobot
SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH=.pi/mcp.json
```

- [ ] **Step 5: Run docs smoke check**

```bash
rg -n "seektalent pi-agent init --project|Runtime and Workbench never call DokoBot directly|SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH" README.md docs .env.example
```

Expected: all phrases/keys are found.

- [ ] **Step 6: Update deferred platform follow-ups**

Add these deferred bullets under `Runtime Multi-Source Platform Follow-Ups` in `TODOS.md` if they are not already present:

```markdown
- Global doctor public-safe JSON mode: design a separate path-redacted public diagnostic payload if doctor output is later shown outside developer/operator surfaces; the current doctor may show non-sensitive local paths by design.
- Generic external-agent local setup model: extract `PiAgentLocalSetupStatus` into a provider-agnostic external-agent readiness model only after a second browser-backed source needs it.
- Browser capability abstraction: map provider requirements to generic `browser.read`, `browser.navigate`, `browser.click`, and `browser.type_text` capabilities once another MCP/browser helper is planned; keep the first slice DokoBot-specific.
- Shared safe public payload/redaction utility: consolidate path/secret/raw-output redaction across CLI, Workbench, Runtime events, and external-agent diagnostics after this Pi setup path proves stable.
- Dedicated `pi-agent probe` CLI: consider adding `seektalent pi-agent probe --json` only after `doctor --live-pi-agent --json` has stabilized and there is a clear operator need for a separate live Pi probe entry point.
```

Run:

```bash
rg -n "Global doctor public-safe JSON mode|Generic external-agent local setup model|Browser capability abstraction|Shared safe public payload/redaction utility|Dedicated `pi-agent probe` CLI" TODOS.md
```

Expected: exactly one match per deferred bullet.

## Task 8: End-To-End Verification

**Files:**
- No file edits in this task.

- [ ] **Step 1: Run Python focused tests**

```bash
uv run pytest \
  tests/test_pi_dokobot_local_setup.py \
  tests/test_pi_agent_boundaries.py \
  tests/test_cli.py \
  tests/test_dev_mode_readiness.py \
  tests/test_liepin_config.py \
  tests/test_liepin_pi_executor.py \
  tests/test_liepin_pi_worker_client.py \
  tests/test_workbench_liepin_browser_session_probe.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run Svelte focused tests**

```bash
cd apps/web-svelte && npm run test -- sourceDisplay.test.ts SourceCard.test.ts runStory.test.ts
```

Expected: PASS.

- [ ] **Step 3: Run type/static checks**

```bash
uv run ruff check \
  src/seektalent/config.py \
  src/seektalent/cli.py \
  src/seektalent/providers/pi_agent/local_setup.py \
  src/seektalent/providers/liepin/pi_executor.py \
  src/seektalent/providers/liepin/pi_worker_client.py \
  src/seektalent/providers/liepin/runtime_lane.py \
  src/seektalent/dev_mode.py \
  src/seektalent_ui/workbench_routes.py \
  tests/test_pi_dokobot_local_setup.py \
  tests/test_pi_agent_boundaries.py
cd apps/web-svelte && npm run check
```

Expected: PASS.

- [ ] **Step 4: Run leak and boundary checks**

```bash
uv run pytest tests/test_cli.py::test_doctor_json_does_not_leak_provider_secrets -q
uv run pytest tests/test_pi_agent_boundaries.py::test_product_paths_do_not_import_or_execute_dokobot_directly -q
rg -n "Legacy direct DokoBot CLI diagnostics cleanup" TODOS.md
rg -n "Global doctor public-safe JSON mode|Generic external-agent local setup model|Browser capability abstraction|Shared safe public payload/redaction utility|Dedicated `pi-agent probe` CLI" TODOS.md
```

Expected: pytest PASS. Each TODO grep target appears exactly once.

- [ ] **Step 5: Run diff hygiene**

```bash
git diff --check
```

Expected: no output and exit code 0.

## Self-Review Checklist

- [ ] The spec's hard boundary is covered by Task 4 and Task 5.
- [ ] Project-level `.pi/mcp.json` provisioning is covered by Task 1 and Task 2.
- [ ] Static setup and live capability are separate; default doctor does not launch Pi or DokoBot.
- [ ] `SEEKTALENT_WORKSPACE_ROOT` is respected for env-file relative paths when settings cannot be constructed.
- [ ] `pi-agent` is routed through the top-level CLI dispatch.
- [ ] `workbench_routes.py` preserves new safe reason codes in `runtimeSourceState`.
- [ ] Runtime never gains a direct DokoBot CLI/MCP path.
- [ ] Pi MCP config is treated as Pi-owned; SeekTalent only writes a project-local declaration, diagnoses it, and verifies observed Pi RPC tool events.
- [ ] Main Workbench copy does not mention Pi, DokoBot, or MCP.
- [ ] The plan does not require forking Pi or installing Pi/DokoBot from inside SeekTalent.
- [ ] CTS behavior remains independent when Liepin is unavailable.
- [ ] New Pi setup/live probe payloads avoid local paths, cookies, provider ids, raw Pi output, raw DokoBot output, and secrets.
