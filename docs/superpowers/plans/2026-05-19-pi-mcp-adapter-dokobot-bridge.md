# Pi MCP Adapter DokoBot Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SeekTalent's Pi-first Liepin path load a pinned Pi MCP adapter and treat DokoBot MCP command/tool registration as an explicit, proven dependency instead of a fake static `.pi/mcp.json` assumption.

**Architecture:** Keep Pi vanilla and load capabilities through repo-pinned extensions: the existing Bailian provider extension plus a pinned Pi MCP adapter extension. Static setup writes Pi-owned MCP config only from explicit or proven DokoBot MCP command/tool settings; live readiness accepts DokoBot only through direct observed Pi tool events in this slice. Runtime and Workbench never call DokoBot directly.

**Tech Stack:** Python 3.12, Pydantic settings, argparse CLI, Bash dev launcher, Pi RPC, Pi extension packages, pytest, Bun package management.

---

Linked spec: [2026-05-19-pi-mcp-adapter-dokobot-bridge-design.md](../specs/2026-05-19-pi-mcp-adapter-dokobot-bridge-design.md)

## File Structure

- Modify `apps/web-svelte/package.json`
  - Add pinned `pi-mcp-adapter` dependency.
- Modify `apps/web-svelte/bun.lock`
  - Regenerate from `bun install`.
- Modify `scripts/start-dev-workbench.sh`
  - Resolve the repo-local Pi binary.
  - Resolve the repo-local `pi-mcp-adapter` extension entry.
  - Build `SEEKTALENT_LIEPIN_PI_COMMAND` with provider extension and MCP adapter extension.
  - Keep secrets in backend/Pi env only.
- Modify `src/seektalent/config.py`
  - Add root `.env` fields for DokoBot MCP server name, command, args JSON, direct tools JSON, and observed tools JSON.
  - Normalize empty strings to `None`/empty tuples.
  - Reject live `pi_agent` commands that omit the pinned MCP adapter marker.
- Modify `src/seektalent/dev_mode.py`
  - Feed the same root `.env` DokoBot MCP settings into dev-mode readiness diagnostics.
- Modify `src/seektalent/providers/pi_agent/local_setup.py`
  - Stop assuming `command: "dokobot", args: []` is a proven MCP server.
  - Generate `.pi/mcp.json` only when a DokoBot MCP command is configured.
  - Add safe reason codes for missing command/tool names.
- Modify `src/seektalent/providers/pi_agent/pi_external.py`
  - Add command validation for required extension paths.
  - Preserve extension checks without leaking full local paths.
- Modify `src/seektalent/providers/liepin/pi_executor.py`
  - Make expected observed DokoBot tool names configurable.
  - Keep strict envelope validation and safe event projection.
- Modify `src/seektalent/providers/liepin/pi_worker_client.py`
  - Carry configured expected observed tool names into the executor capability probe.
- Modify `src/seektalent/providers/liepin/client.py`
  - Pass configured expected DokoBot observed tool names into the Pi executor/client boundary.
- Modify `src/seektalent_ui/workbench_routes.py`
  - Preserve new `liepin_pi_*` reason codes in runtime source-state projection.
- Modify `apps/web-svelte/src/lib/workbench/sourceDisplay.ts`
  - Map new setup codes to business-facing browser-channel copy.
- Modify `docs/configuration.md`, `docs/development.md`, `README.md`, `src/seektalent/default.env`
  - Document the explicit Pi MCP adapter bridge and root `.env` knobs.
- Modify `src/seektalent/cli.py`
  - Add `pi-agent init` flags and list the new runtime env vars in CLI discovery output.
- Modify `TODOS.md`
  - Record the deferred work of shipping a known default DokoBot MCP command once DokoBot publishes or exposes one in local tooling.
  - Record protected adapter proxy-proof support as deferred unless implemented in this slice.

## Task 1: Pin The Pi MCP Adapter Dependency

**Files:**
- Modify: `apps/web-svelte/package.json`
- Modify: `apps/web-svelte/bun.lock`

- [ ] **Step 1: Add the dependency**

In `apps/web-svelte/package.json`, add this dependency next to `@earendil-works/pi-coding-agent`:

```json
"pi-mcp-adapter": "2.6.1"
```

Keep `@earendil-works/pi-coding-agent` unchanged.

- [ ] **Step 2: Regenerate Bun lockfile**

Run:

```bash
cd apps/web-svelte
bun install
```

Expected: `bun.lock` updates and `node_modules/pi-mcp-adapter/index.ts` exists.

- [ ] **Step 3: Verify the adapter entry exists**

Run:

```bash
test -f apps/web-svelte/node_modules/pi-mcp-adapter/index.ts
```

Expected: exit code `0`.

## Task 2: Add Explicit DokoBot MCP Config Fields

**Files:**
- Modify: `src/seektalent/config.py`
- Modify: `src/seektalent/cli.py`
- Modify: `src/seektalent/default.env`
- Test: `tests/test_liepin_config.py`

- [ ] **Step 1: Write failing config tests**

Append to `tests/test_liepin_config.py`:

```python
def test_liepin_dokobot_mcp_config_defaults_to_unproven(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_WORKER_MODE", "disabled")
    monkeypatch.delenv("SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND", raising=False)
    settings = AppSettings()

    assert settings.liepin_dokobot_mcp_server_name == "dokobot"
    assert settings.liepin_dokobot_mcp_command is None
    assert settings.liepin_dokobot_mcp_args == ()
    assert settings.liepin_dokobot_direct_tools == ()
    assert settings.liepin_dokobot_observed_tools == ()


def test_liepin_dokobot_mcp_json_fields_are_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_WORKER_MODE", "disabled")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME", "dokobot")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND", "dokobot-mcp")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_DOKOBOT_MCP_ARGS_JSON", '["--stdio"]')
    monkeypatch.setenv("SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON", '["read_page","click","type_text"]')
    monkeypatch.setenv("SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON", '["dokobot_read_page","dokobot_click","dokobot_type_text"]')

    settings = AppSettings()

    assert settings.liepin_dokobot_mcp_command == "dokobot-mcp"
    assert settings.liepin_dokobot_mcp_args == ("--stdio",)
    assert settings.liepin_dokobot_direct_tools == ("read_page", "click", "type_text")
    assert settings.liepin_dokobot_observed_tools == ("dokobot_read_page", "dokobot_click", "dokobot_type_text")
```

- [ ] **Step 2: Run the focused failing tests**

Run:

```bash
uv run pytest tests/test_liepin_config.py::test_liepin_dokobot_mcp_config_defaults_to_unproven tests/test_liepin_config.py::test_liepin_dokobot_mcp_json_fields_are_normalized -q
```

Expected: fail because the settings fields do not exist yet.

- [ ] **Step 3: Add settings fields and JSON tuple parser**

In `src/seektalent/config.py`, add fields to `AppSettings`:

```python
liepin_dokobot_mcp_server_name: str = "dokobot"
liepin_dokobot_mcp_command: str | None = None
liepin_dokobot_mcp_args_json: str = "[]"
liepin_dokobot_direct_tools_json: str = "[]"
liepin_dokobot_observed_tools_json: str = "[]"
```

Add properties:

```python
@property
def liepin_dokobot_mcp_args(self) -> tuple[str, ...]:
    return _json_string_tuple(self.liepin_dokobot_mcp_args_json, field_name="liepin_dokobot_mcp_args_json")

@property
def liepin_dokobot_direct_tools(self) -> tuple[str, ...]:
    return _json_string_tuple(self.liepin_dokobot_direct_tools_json, field_name="liepin_dokobot_direct_tools_json")

@property
def liepin_dokobot_observed_tools(self) -> tuple[str, ...]:
    return _json_string_tuple(self.liepin_dokobot_observed_tools_json, field_name="liepin_dokobot_observed_tools_json")
```

Add helper near the other normalization helpers:

```python
def _json_string_tuple(raw: str, *, field_name: str) -> tuple[str, ...]:
    text = (raw or "").strip()
    if not text:
        return ()
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must be a JSON array of strings") from exc
    if not isinstance(loaded, list) or not all(isinstance(item, str) and item.strip() for item in loaded):
        raise ValueError(f"{field_name} must be a JSON array of non-empty strings")
    return tuple(item.strip() for item in loaded)
```

If `src/seektalent/config.py` does not already import `json`, add:

```python
import json
```

In the existing settings normalizer, treat empty `liepin_dokobot_mcp_command` as `None` and strip `liepin_dokobot_mcp_server_name`, defaulting it back to `dokobot` when empty.

- [ ] **Step 4: Update default env**

Add to `src/seektalent/default.env`:

```dotenv
# DokoBot MCP server config used inside Pi only. Empty command means the server is not proven/configured.
SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME=dokobot
SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND=
SEEKTALENT_LIEPIN_DOKOBOT_MCP_ARGS_JSON=[]
SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON=[]
SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON=[]
```

- [ ] **Step 5: Update CLI env discovery**

In `src/seektalent/cli.py`, add the new variables to `OPTIONAL_RUNTIME_ENV_VARS`:

```python
"SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME",
"SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND",
"SEEKTALENT_LIEPIN_DOKOBOT_MCP_ARGS_JSON",
"SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON",
"SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON",
```

This keeps `seektalent inspect --json` and docs discovery aligned with the root `.env` contract. Do not include configured values in public inspect output.

- [ ] **Step 6: Run the config tests**

Run:

```bash
uv run pytest tests/test_liepin_config.py -q
```

Expected: pass.

## Task 3: Make Local Setup Refuse Fake DokoBot MCP Commands

**Files:**
- Modify: `src/seektalent/providers/pi_agent/local_setup.py`
- Modify: `src/seektalent/dev_mode.py`
- Modify: `tests/test_pi_dokobot_local_setup.py`
- Modify: `tests/test_dev_mode_readiness.py`

- [ ] **Step 1: Write failing local setup tests**

Add these tests to `tests/test_pi_dokobot_local_setup.py`:

```python
def test_init_reports_missing_dokobot_mcp_command_without_writing(tmp_path: Path) -> None:
    result = init_project_pi_mcp_config(
        workspace_root=tmp_path,
        dokobot_tool_name="dokobot",
        write=True,
        dokobot_mcp_command=None,
        dokobot_mcp_args=(),
        dokobot_direct_tools=(),
    )

    assert result.status == "blocked"
    assert result.reason_code == "liepin_pi_dokobot_mcp_command_missing"
    assert not (tmp_path / ".pi" / "mcp.json").exists()


def test_init_writes_explicit_dokobot_mcp_command_and_direct_tools(tmp_path: Path) -> None:
    result = init_project_pi_mcp_config(
        workspace_root=tmp_path,
        dokobot_tool_name="dokobot",
        write=True,
        dokobot_mcp_command="dokobot-mcp",
        dokobot_mcp_args=("--stdio",),
        dokobot_direct_tools=("read_page", "click", "type_text"),
    )

    payload = json.loads((tmp_path / ".pi" / "mcp.json").read_text(encoding="utf-8"))
    assert result.status == "written"
    assert payload["mcpServers"]["dokobot"] == {
        "command": "dokobot-mcp",
        "args": ["--stdio"],
        "lifecycle": "lazy",
        "directTools": ["read_page", "click", "type_text"],
    }


def test_static_setup_reports_missing_dokobot_mcp_command(tmp_path: Path) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": "pi --mode rpc --no-session",
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_DOKOBOT_TOOL_NAME": "dokobot",
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME": "dokobot",
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND": "",
        },
        workspace_root=tmp_path,
        which=lambda name: "/usr/local/bin/pi" if name == "pi" else None,
    )

    assert status.overall_status == "needs_setup"
    assert status.reason_code == "liepin_pi_dokobot_mcp_command_missing"
    assert status.components["dokobot_mcp"].reason_code == "liepin_pi_dokobot_mcp_command_missing"
    assert str(tmp_path) not in json.dumps(status.to_public_payload())


def test_static_setup_reports_missing_dokobot_tool_names(tmp_path: Path) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")
    mcp_config = tmp_path / ".pi" / "mcp.json"
    mcp_config.parent.mkdir()
    mcp_config.write_text(
        json.dumps({"mcpServers": {"dokobot": {"command": "dokobot-mcp", "args": [], "lifecycle": "lazy"}}}),
        encoding="utf-8",
    )

    status = build_pi_agent_local_setup_status(
        {
            "SEEKTALENT_LIEPIN_WORKER_MODE": "pi_agent",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_PI_COMMAND": "pi --mode rpc --no-session",
            "SEEKTALENT_LIEPIN_PI_SKILL_PATH": str(skill),
            "SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH": str(mcp_config),
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME": "dokobot",
            "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND": "dokobot-mcp",
            "SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON": "[]",
            "SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON": "[]",
        },
        workspace_root=tmp_path,
        which=lambda name: "/usr/local/bin/pi" if name == "pi" else None,
    )

    assert status.overall_status == "needs_setup"
    assert status.reason_code == "liepin_pi_dokobot_mcp_tool_names_missing"
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/test_pi_dokobot_local_setup.py::test_init_reports_missing_dokobot_mcp_command_without_writing tests/test_pi_dokobot_local_setup.py::test_init_writes_explicit_dokobot_mcp_command_and_direct_tools tests/test_pi_dokobot_local_setup.py::test_static_setup_reports_missing_dokobot_mcp_command -q
```

Expected: fail because `init_project_pi_mcp_config` does not accept the new arguments and still writes a fake `dokobot` command.

- [ ] **Step 3: Extend local setup signatures**

Change `init_project_pi_mcp_config(...)` to accept:

```python
dokobot_mcp_command: str | None = None,
dokobot_mcp_args: tuple[str, ...] = (),
dokobot_direct_tools: tuple[str, ...] = (),
```

At the start of the function, after resolving the target path, add:

```python
command = (dokobot_mcp_command or "").strip()
if not command:
    return PiMcpInitResult(
        status="blocked",
        reason_code="liepin_pi_dokobot_mcp_command_missing",
        changed=False,
    )
```

Replace the old expected server:

```python
expected_server: dict[str, object] = {
    "command": command,
    "args": list(dokobot_mcp_args),
    "lifecycle": "lazy",
}
if dokobot_direct_tools:
    expected_server["directTools"] = list(dokobot_direct_tools)
```

- [ ] **Step 4: Make static diagnostics use the new env keys**

In `_dokobot_mcp_component(...)`, read:

```python
configured_server_name = _env_value(env, "SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME") or dokobot_tool_name
configured_command = _env_value(env, "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND")
if not configured_command:
    return PiAgentLocalSetupComponent("needs_setup", "liepin_pi_dokobot_mcp_command_missing")
```

Load `server = mcp_servers.get(configured_server_name)`. After loading the MCP server, verify:

```python
if str(server.get("command") or "").strip() != configured_command:
    return PiAgentLocalSetupComponent("needs_setup", "liepin_pi_dokobot_mcp_missing")
```

If `SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON` is empty and `SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON` is empty, return:

```python
PiAgentLocalSetupComponent("needs_setup", "liepin_pi_dokobot_mcp_tool_names_missing")
```

- [ ] **Step 5: Feed new keys through dev-mode readiness**

In `src/seektalent/dev_mode.py`, update `_pi_mcp_components_from_settings(...)` so the synthetic env passed to `build_pi_agent_local_setup_status(...)` includes:

```python
"SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME": settings.liepin_dokobot_mcp_server_name,
"SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND": settings.liepin_dokobot_mcp_command,
"SEEKTALENT_LIEPIN_DOKOBOT_MCP_ARGS_JSON": settings.liepin_dokobot_mcp_args_json,
"SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON": settings.liepin_dokobot_direct_tools_json,
"SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON": settings.liepin_dokobot_observed_tools_json,
```

Add or update a focused test in `tests/test_dev_mode_readiness.py`:

```python
def test_dev_mode_status_uses_configured_dokobot_mcp_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skill = tmp_path / "liepin_search_cards.md"
    skill.write_text("Liepin skill", encoding="utf-8")
    mcp_config = tmp_path / ".pi" / "mcp.json"
    mcp_config.parent.mkdir()
    mcp_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "dokobot": {
                        "command": "dokobot-mcp",
                        "args": ["--stdio"],
                        "lifecycle": "lazy",
                        "directTools": ["read_page", "click", "type_text"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SEEKTALENT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("SEEKTALENT_LIEPIN_WORKER_MODE", "pi_agent")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET", "secret")
    monkeypatch.setenv(
        "SEEKTALENT_LIEPIN_PI_COMMAND",
        "pi --mode rpc --no-session --extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts",
    )
    monkeypatch.setenv("SEEKTALENT_LIEPIN_PI_SKILL_PATH", str(skill))
    monkeypatch.setenv("SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH", str(mcp_config))
    monkeypatch.setenv("SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND", "dokobot-mcp")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON", '["read_page","click","type_text"]')

    status = build_dev_mode_status(AppSettings())

    assert any(item.name == "liepin_pi_dokobot_mcp" and item.status == "configured" for item in status.components)
```

- [ ] **Step 6: Run local setup tests**

Run:

```bash
uv run pytest tests/test_pi_dokobot_local_setup.py -q
uv run pytest tests/test_dev_mode_readiness.py -q
```

Expected: pass.

## Task 4: Require The MCP Adapter Extension In Pi Commands

**Files:**
- Modify: `src/seektalent/providers/pi_agent/pi_external.py`
- Modify: `src/seektalent/config.py`
- Modify: `tests/test_pi_external_agent.py`
- Modify: `tests/test_liepin_config.py`

- [ ] **Step 1: Write failing command validation tests**

Append to `tests/test_pi_external_agent.py`:

```python
def test_build_pi_rpc_argv_preserves_required_provider_and_mcp_extensions(tmp_path: Path) -> None:
    skill_path = _skill(tmp_path)
    command = (
        "pi --mode rpc --no-session "
        "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
        "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts "
        "--provider bailian --model deepseek-v4-flash"
    )

    argv = build_pi_rpc_argv(
        command,
        skill_path=skill_path,
        required_extension_markers=("pi_extensions/bailian_deepseek.ts", "pi-mcp-adapter/index.ts"),
    )

    assert "src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts" in argv
    assert "apps/web-svelte/node_modules/pi-mcp-adapter/index.ts" in argv
    assert "--skill" in argv


def test_build_pi_rpc_argv_rejects_missing_mcp_adapter_extension(tmp_path: Path) -> None:
    skill_path = _skill(tmp_path)
    command = (
        "pi --mode rpc --no-session "
        "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
        "--provider bailian --model deepseek-v4-flash"
    )

    with pytest.raises(ValueError, match="liepin_pi_command must include required extension"):
        build_pi_rpc_argv(
            command,
            skill_path=skill_path,
            required_extension_markers=("pi_extensions/bailian_deepseek.ts", "pi-mcp-adapter/index.ts"),
        )


def test_build_pi_rpc_argv_rejects_missing_provider_extension(tmp_path: Path) -> None:
    skill_path = _skill(tmp_path)
    command = (
        "pi --mode rpc --no-session "
        "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts "
        "--provider bailian --model deepseek-v4-flash"
    )

    with pytest.raises(ValueError, match="liepin_pi_command must include required extension"):
        build_pi_rpc_argv(
            command,
            skill_path=skill_path,
            required_extension_markers=("pi_extensions/bailian_deepseek.ts", "pi-mcp-adapter/index.ts"),
        )
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/test_pi_external_agent.py::test_build_pi_rpc_argv_preserves_required_provider_and_mcp_extensions tests/test_pi_external_agent.py::test_build_pi_rpc_argv_rejects_missing_mcp_adapter_extension tests/test_pi_external_agent.py::test_build_pi_rpc_argv_rejects_missing_provider_extension -q
```

Expected: fail because `build_pi_rpc_argv` has no `required_extension_markers` parameter.

- [ ] **Step 3: Extend `build_pi_rpc_argv`**

Change the signature in `src/seektalent/providers/pi_agent/pi_external.py`:

```python
def build_pi_rpc_argv(
    command: str,
    *,
    skill_path: Path,
    required_extension_markers: tuple[str, ...] = (),
) -> tuple[str, ...]:
```

After validating `--no-session`, add:

```python
joined = " ".join(argv)
for marker in required_extension_markers:
    if marker not in joined:
        raise ValueError("liepin_pi_command must include required extension")
```

Keep the existing `--skill` rejection and `--no-skills --skill` append behavior unchanged.

- [ ] **Step 4: Enforce required extension markers from AppSettings**

Append to `tests/test_liepin_config.py`:

```python
def test_pi_agent_command_requires_provider_and_mcp_adapter_extensions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_WORKER_MODE", "pi_agent")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET", "account-secret")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_PI_COMMAND", "pi --mode rpc --no-session")

    with pytest.raises(ValueError, match="required extension"):
        AppSettings()


def test_pi_agent_command_rejects_missing_provider_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_WORKER_MODE", "pi_agent")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET", "account-secret")
    monkeypatch.setenv(
        "SEEKTALENT_LIEPIN_PI_COMMAND",
        "pi --mode rpc --no-session --extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts",
    )

    with pytest.raises(ValueError, match="required extension"):
        AppSettings()


def test_pi_agent_command_accepts_required_provider_and_mcp_adapter_extensions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_WORKER_MODE", "pi_agent")
    monkeypatch.setenv("SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET", "account-secret")
    monkeypatch.setenv(
        "SEEKTALENT_LIEPIN_PI_COMMAND",
        "pi --mode rpc --no-session "
        "--extension src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts "
        "--extension apps/web-svelte/node_modules/pi-mcp-adapter/index.ts",
    )

    settings = AppSettings()

    assert "src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts" in settings.liepin_pi_command_argv
    assert "apps/web-svelte/node_modules/pi-mcp-adapter/index.ts" in settings.liepin_pi_command_argv
```

Then update `AppSettings.liepin_pi_command_argv` so it passes the adapter marker when `liepin_worker_mode == "pi_agent"`:

```python
markers = (
    "pi_extensions/bailian_deepseek.ts",
    "pi-mcp-adapter/index.ts",
) if self.liepin_worker_mode == "pi_agent" else ()
return build_pi_rpc_argv(
    self.liepin_pi_command,
    skill_path=self.liepin_pi_skill_file_path,
    required_extension_markers=markers,
)
```

This is the critical integration step. Extending `build_pi_rpc_argv(...)` alone is not enough; `validate_liepin_worker_config(...)` must exercise the marker check through `self.liepin_pi_command_argv`.

- [ ] **Step 5: Run Pi external and config tests**

Run:

```bash
uv run pytest tests/test_pi_external_agent.py -q
uv run pytest tests/test_liepin_config.py -q
```

Expected: pass.

## Task 5: Build The Dev Launcher Around The Pinned Adapter

**Files:**
- Modify: `scripts/start-dev-workbench.sh`
- Test: `tests/test_pi_dokobot_local_setup.py`

- [ ] **Step 1: Add script expectations to a Python test**

Append to `tests/test_pi_dokobot_local_setup.py`:

```python
def test_dev_launcher_mentions_pinned_pi_mcp_adapter() -> None:
    script = Path("scripts/start-dev-workbench.sh").read_text(encoding="utf-8")

    assert "node_modules/pi-mcp-adapter/index.ts" in script
    assert "--extension $PI_MCP_ADAPTER_EXTENSION" in script
    assert "SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME" in script
    assert "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND" in script
    assert "SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON" in script
    assert "SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON" in script
    assert 'if [[ -n "$DOKOBOT_MCP_COMMAND" ]]' in script
    assert "DokoBot MCP command is not configured" in script
```

- [ ] **Step 2: Run the failing launcher test**

Run:

```bash
uv run pytest tests/test_pi_dokobot_local_setup.py::test_dev_launcher_mentions_pinned_pi_mcp_adapter -q
```

Expected: fail because the script does not mention the adapter yet.

- [ ] **Step 3: Add adapter and DokoBot env handling to the script**

In `scripts/start-dev-workbench.sh`, define:

```bash
PI_MCP_ADAPTER_EXTENSION="$WEB_DIR/node_modules/pi-mcp-adapter/index.ts"
```

After the existing Pi binary check, add:

```bash
if [[ ! -f "$PI_MCP_ADAPTER_EXTENSION" ]]; then
  echo "Repo-local Pi MCP adapter is missing: apps/web-svelte/node_modules/pi-mcp-adapter/index.ts" >&2
  exit 1
fi
```

Read the new root `.env` values:

```bash
DOKOBOT_MCP_SERVER_NAME="$(env_or_file SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME)"
DOKOBOT_MCP_COMMAND="$(env_or_file SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND)"
DOKOBOT_MCP_ARGS_JSON="$(env_or_file SEEKTALENT_LIEPIN_DOKOBOT_MCP_ARGS_JSON)"
DOKOBOT_DIRECT_TOOLS_JSON="$(env_or_file SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON)"
DOKOBOT_OBSERVED_TOOLS_JSON="$(env_or_file SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON)"
DOKOBOT_MCP_SERVER_NAME="${DOKOBOT_MCP_SERVER_NAME:-dokobot}"
DOKOBOT_MCP_ARGS_JSON="${DOKOBOT_MCP_ARGS_JSON:-[]}"
DOKOBOT_DIRECT_TOOLS_JSON="${DOKOBOT_DIRECT_TOOLS_JSON:-[]}"
DOKOBOT_OBSERVED_TOOLS_JSON="${DOKOBOT_OBSERVED_TOOLS_JSON:-[]}"
```

Change the generated Pi command to include the adapter extension:

```bash
PI_COMMAND="$PI_BIN --mode rpc --no-session --extension $PI_EXTENSION --extension $PI_MCP_ADAPTER_EXTENSION --provider bailian --model $PI_MODEL"
```

When calling `seektalent pi-agent init`, pass the configured DokoBot MCP fields once the CLI flags exist in Task 6:

```bash
if [[ -n "$DOKOBOT_MCP_COMMAND" ]]; then
  uv run seektalent pi-agent init \
    --project \
    --workspace-root "$ROOT" \
    --mcp-config-path "$MCP_CONFIG_PATH" \
    --dokobot-mcp-server-name "$DOKOBOT_MCP_SERVER_NAME" \
    --dokobot-mcp-command "$DOKOBOT_MCP_COMMAND" \
    --dokobot-mcp-args-json "$DOKOBOT_MCP_ARGS_JSON" \
    --dokobot-direct-tools-json "$DOKOBOT_DIRECT_TOOLS_JSON" \
    --write >/dev/null
else
  echo "DokoBot MCP command is not configured; starting Workbench with Liepin browser channel blocked." >&2
fi
```

Do not call `seektalent pi-agent init --write` with an empty DokoBot MCP command. The development workbench must still start so users can use CTS and see the Liepin source blocked with `liepin_pi_dokobot_mcp_command_missing`.

When starting the backend, pass:

```bash
  SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND="$DOKOBOT_MCP_COMMAND" \
  SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME="$DOKOBOT_MCP_SERVER_NAME" \
  SEEKTALENT_LIEPIN_DOKOBOT_MCP_ARGS_JSON="$DOKOBOT_MCP_ARGS_JSON" \
  SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON="$DOKOBOT_DIRECT_TOOLS_JSON" \
  SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON="$DOKOBOT_OBSERVED_TOOLS_JSON" \
```

- [ ] **Step 4: Run shell syntax and launcher tests**

Run:

```bash
bash -n scripts/start-dev-workbench.sh
uv run pytest tests/test_pi_dokobot_local_setup.py::test_dev_launcher_mentions_pinned_pi_mcp_adapter -q
```

Expected: pass.

## Task 6: Add CLI Flags For Explicit MCP Server Generation

**Files:**
- Modify: `src/seektalent/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Append to `tests/test_cli.py`:

```python
def test_pi_agent_init_requires_dokobot_mcp_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([
        "pi-agent",
        "init",
        "--project",
        "--workspace-root",
        str(tmp_path),
        "--write",
        "--json",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["reasonCode"] == "liepin_pi_dokobot_mcp_command_missing"
    assert not (tmp_path / ".pi" / "mcp.json").exists()


def test_pi_agent_init_writes_configured_dokobot_mcp_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([
        "pi-agent",
        "init",
        "--project",
        "--workspace-root",
        str(tmp_path),
        "--write",
        "--dokobot-mcp-server-name",
        "dokobot",
        "--dokobot-mcp-command",
        "dokobot-mcp",
        "--dokobot-mcp-args-json",
        '["--stdio"]',
        "--dokobot-direct-tools-json",
        '["read_page","click","type_text"]',
        "--json",
    ])

    payload = json.loads(capsys.readouterr().out)
    config = json.loads((tmp_path / ".pi" / "mcp.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["reasonCode"] == "configured"
    assert config["mcpServers"]["dokobot"]["command"] == "dokobot-mcp"
    assert config["mcpServers"]["dokobot"]["args"] == ["--stdio"]
    assert config["mcpServers"]["dokobot"]["directTools"] == ["read_page", "click", "type_text"]
```

- [ ] **Step 2: Run the failing CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py::test_pi_agent_init_requires_dokobot_mcp_command tests/test_cli.py::test_pi_agent_init_writes_configured_dokobot_mcp_command -q
```

Expected: fail because the CLI flags do not exist and init still writes the fake default.

- [ ] **Step 3: Add CLI arguments**

In the `pi-agent init` parser in `src/seektalent/cli.py`, add:

```python
pi_agent_init_parser.add_argument("--dokobot-mcp-server-name", default="dokobot")
pi_agent_init_parser.add_argument("--dokobot-mcp-command", default=None)
pi_agent_init_parser.add_argument("--dokobot-mcp-args-json", default="[]")
pi_agent_init_parser.add_argument("--dokobot-direct-tools-json", default="[]")
```

Parse JSON arrays with a local helper:

```python
def _json_string_tuple_arg(raw: str, *, field_name: str) -> tuple[str, ...]:
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{field_name} must be a JSON array of strings") from exc
    if not isinstance(loaded, list) or not all(isinstance(item, str) and item.strip() for item in loaded):
        raise SystemExit(f"{field_name} must be a JSON array of non-empty strings")
    return tuple(item.strip() for item in loaded)
```

Pass the parsed values into `init_project_pi_mcp_config(...)`. The new `--dokobot-mcp-server-name` should feed the existing `dokobot_tool_name`/server-key parameter. Keep the old hidden `--dokobot-tool-name` only as backward-compatible alias if needed, but the plan and docs should use `--dokobot-mcp-server-name`.

Update the existing `test_pi_agent_init_dry_run_does_not_write_file` and `test_pi_agent_init_write_creates_project_mcp_file` in `tests/test_cli.py`. The no-command case should now expect `exit_code == 1`, `status == "blocked"`, and `reasonCode == "liepin_pi_dokobot_mcp_command_missing"`; success cases must pass `--dokobot-mcp-command`.

- [ ] **Step 4: Run CLI tests**

Run:

```bash
uv run pytest tests/test_cli.py -k 'pi_agent or liepin_pi' -q
```

Expected: pass.

## Task 7: Make Capability Probe Use Configured Observed Tools

**Files:**
- Modify: `src/seektalent/providers/liepin/pi_executor.py`
- Modify: `src/seektalent/providers/liepin/pi_worker_client.py`
- Modify: `src/seektalent/providers/liepin/client.py`
- Test: `tests/test_liepin_pi_executor.py`
- Test: `tests/test_liepin_pi_worker_client.py`

- [ ] **Step 1: Write failing executor test**

Add to `tests/test_liepin_pi_executor.py`:

```python
def _capability_executor(
    *,
    envelope: dict[str, object],
    observed_tool_names: tuple[str, ...],
) -> PiLiepinExecutor:
    return PiLiepinExecutor(
        client=_client(json.dumps(envelope), observed_tool_names=observed_tool_names),
        key_hasher=FakeProviderKeyHasher(),
        artifact_registry=_registry(
            "artifact://protected/capability/manifest.json",
            "artifact://protected/capability/tools.json",
        ),
    )


def test_capability_probe_requires_configured_observed_dokobot_tools(tmp_path: Path) -> None:
    del tmp_path
    executor = _capability_executor(
        envelope={
            "schema_version": "seektalent.pi_capability_probe.v1",
            "status": "ready",
            "read_tool_name": "dokobot_read_page",
            "action_tool_names": ["dokobot_click", "dokobot_type_text"],
            "proof_kind": "trusted_manifest_and_observed_tool_event",
            "capability_manifest_ref": "artifact://protected/capability/manifest.json",
            "tool_evidence_ref": "artifact://protected/capability/tools.json",
            "allowed_hosts": ["liepin.com"],
        },
        observed_tool_names=("dokobot_read_page", "dokobot_click", "dokobot_type_text"),
    )

    result = executor.probe_capabilities(
        expected_dokobot_tool_name="dokobot",
        expected_observed_tool_names=("dokobot_read_page", "dokobot_click", "dokobot_type_text"),
    )

    assert result.ready is True


def test_capability_probe_blocks_missing_configured_observed_tool(tmp_path: Path) -> None:
    del tmp_path
    executor = _capability_executor(
        envelope={
            "schema_version": "seektalent.pi_capability_probe.v1",
            "status": "ready",
            "read_tool_name": "dokobot_read_page",
            "action_tool_names": ["dokobot_click", "dokobot_type_text"],
            "proof_kind": "trusted_manifest_and_observed_tool_event",
            "capability_manifest_ref": "artifact://protected/capability/manifest.json",
            "tool_evidence_ref": "artifact://protected/capability/tools.json",
            "allowed_hosts": ["liepin.com"],
        },
        observed_tool_names=("dokobot_read_page", "dokobot_click"),
    )

    result = executor.probe_capabilities(
        expected_dokobot_tool_name="dokobot",
        expected_observed_tool_names=("dokobot_read_page", "dokobot_click", "dokobot_type_text"),
    )

    assert result.ready is False
    assert result.safe_reason_code == "liepin_pi_dokobot_tool_unobserved"
```

Use the existing `_client(...)`, `_registry(...)`, `FakeProviderKeyHasher`, and `PiLiepinExecutor` helpers from `tests/test_liepin_pi_executor.py`. Do not reference `_executor_with_result`; that helper does not exist in the current test file.

- [ ] **Step 2: Run the failing executor tests**

Run:

```bash
uv run pytest tests/test_liepin_pi_executor.py::test_capability_probe_requires_configured_observed_dokobot_tools tests/test_liepin_pi_executor.py::test_capability_probe_blocks_missing_configured_observed_tool -q
```

Expected: fail because `probe_capabilities` does not accept `expected_observed_tool_names`.

- [ ] **Step 3: Extend executor signature**

Change `PiLiepinExecutor.probe_capabilities(...)` to:

```python
def probe_capabilities(
    self,
    *,
    expected_dokobot_tool_name: str,
    expected_observed_tool_names: Sequence[str] = (),
) -> PiLiepinCapabilityProbeResult:
```

Inside validation, compute:

```python
required_observed = tuple(expected_observed_tool_names) or (
    f"{expected_dokobot_tool_name}.read",
    f"{expected_dokobot_tool_name}.navigate",
    f"{expected_dokobot_tool_name}.click",
    f"{expected_dokobot_tool_name}.type_text",
)
missing = [tool for tool in required_observed if tool not in task_result.observed_tool_names]
if missing:
    return PiLiepinCapabilityProbeResult(ready=False, safe_reason_code="liepin_pi_dokobot_tool_unobserved")
```

Keep the existing envelope, artifact-ref, and allowed-host validations.

If `expected_observed_tool_names` is empty, keep the existing fallback to `dokobot.read`, `dokobot.navigate`, `dokobot.click`, and `dokobot.type_text` for backward compatibility with older tests.

- [ ] **Step 4: Pass configured observed tools from client construction**

In `tests/test_liepin_pi_worker_client.py`, add `from collections.abc import Sequence` if it is not already imported. Update `FakeExecutor.probe_capabilities(...)` so it accepts and records the new argument:

```python
captured_capability_kwargs: dict[str, object] | None = None

def probe_capabilities(
    self,
    *,
    expected_dokobot_tool_name: str,
    expected_observed_tool_names: Sequence[str] = (),
) -> PiLiepinCapabilityProbeResult:
    self.captured_capability_kwargs = {
        "expected_dokobot_tool_name": expected_dokobot_tool_name,
        "expected_observed_tool_names": tuple(expected_observed_tool_names),
    }
    return PiLiepinCapabilityProbeResult(
        ready=self.capability_ready,
        safe_reason_code=None if self.capability_ready else "blocked_backend_unavailable",
    )
```

Update any test-local monkeypatched `probe_capabilities(...)` functions to accept `expected_observed_tool_names: Sequence[str] = ()`.

Add a worker-client focused test:

```python
def test_pi_worker_client_passes_configured_observed_tools_to_capability_probe() -> None:
    executor = FakeExecutor(capability_ready=True)
    client = LiepinPiWorkerClient(
        executor=executor,
        session_id="session-1",
        connection_id="connection-1",
        provider_account_lock_key="account-1",
        dokobot_tool_name="dokobot",
        expected_observed_tool_names=("dokobot_read_page", "dokobot_click", "dokobot_type_text"),
    )

    asyncio.run(client.ensure_ready())

    assert executor.captured_capability_kwargs == {
        "expected_dokobot_tool_name": "dokobot",
        "expected_observed_tool_names": ("dokobot_read_page", "dokobot_click", "dokobot_type_text"),
    }
```

In `src/seektalent/providers/liepin/pi_worker_client.py`, add:

```python
expected_observed_tool_names: tuple[str, ...] = ()
```

Store it on the instance and pass it into `executor.probe_capabilities(...)` from `ensure_ready(...)`.

In `src/seektalent/providers/liepin/client.py`, when constructing `LiepinPiWorkerClient`, pass `settings.liepin_dokobot_observed_tools` into `expected_observed_tool_names`.

This implementation accepts direct observed tool events only. Do not treat a generic adapter proxy tool such as `mcp` as proof of DokoBot browser readiness in this slice.

- [ ] **Step 5: Run Liepin Pi tests**

Run:

```bash
uv run pytest tests/test_liepin_pi_executor.py tests/test_liepin_pi_worker_client.py -q
```

Expected: pass.

## Task 8: Preserve New Safe Reason Codes Through Workbench And UI

**Files:**
- Modify: `src/seektalent_ui/workbench_routes.py`
- Modify: `apps/web-svelte/src/lib/workbench/sourceDisplay.ts`
- Test: existing backend/source display tests

- [ ] **Step 1: Add reason codes to backend projection**

In `src/seektalent_ui/workbench_routes.py`, add these strings to `RUNTIME_SOURCE_REASON_CODES`:

```python
"liepin_pi_mcp_adapter_missing",
"liepin_pi_mcp_adapter_unavailable",
"liepin_pi_dokobot_mcp_command_missing",
"liepin_pi_dokobot_mcp_tool_names_missing",
"liepin_pi_dokobot_tool_unobserved",
```

- [ ] **Step 2: Add business-facing UI copy**

In `apps/web-svelte/src/lib/workbench/sourceDisplay.ts`, map all setup/adapter/tool-name failures to:

```ts
{
  label: '浏览器检索通道不可用',
  description: '请到本机设置检查浏览器助手后重试。'
}
```

Keep `liepin_browser_login_required` mapped to:

```ts
{
  label: '需登录猎聘',
  description: '请在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。'
}
```

Do not use the words `Pi`, `DokoBot`, or `MCP` in main Workbench source card copy.

Because `apps/web-svelte/src/lib/workbench/sourceDisplay.test.ts` already exists, add a mandatory case that feeds `liepin_pi_mcp_adapter_missing`, `liepin_pi_dokobot_mcp_command_missing`, and `liepin_pi_dokobot_tool_unobserved` and asserts the returned label/description do not contain `Pi`, `DokoBot`, or `MCP`.

- [ ] **Step 3: Run focused frontend/backend checks**

Run:

```bash
uv run pytest tests/test_workbench_routes.py -k 'runtime_source or liepin' -q
cd apps/web-svelte && bun run test -- --run
```

Expected: pass. If the backend test selection has no matching tests, run the nearest existing Workbench route test file that covers runtime source-state projection.

## Task 9: Documentation And Deferred Work

**Files:**
- Modify: `docs/configuration.md`
- Modify: `docs/development.md`
- Modify: `README.md`
- Modify: `TODOS.md`

- [ ] **Step 1: Document root `.env` configuration**

In `docs/configuration.md`, document:

```dotenv
SEEKTALENT_LIEPIN_WORKER_MODE=pi_agent
SEEKTALENT_LIEPIN_PI_COMMAND=
SEEKTALENT_LIEPIN_PI_MODEL_ID=deepseek-v4-flash
SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME=dokobot
SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND=
SEEKTALENT_LIEPIN_DOKOBOT_MCP_ARGS_JSON=[]
SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON=[]
SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON=[]
```

State explicitly:

- empty `SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND` means Liepin live browser channel is not configured;
- DokoBot MCP is loaded only inside Pi;
- Runtime/Workbench do not call DokoBot directly;
- `scripts/start-dev-workbench.sh` uses root `.env` and passes secrets only to backend/Pi.

- [ ] **Step 2: Document local verification**

In `docs/development.md`, add:

```bash
scripts/start-dev-workbench.sh
uv run seektalent doctor --json
uv run seektalent doctor --live-pi-agent --json
```

Add a skipped-by-default live command shape:

```bash
SEEKTALENT_LIVE_PI_AGENT=1 uv run pytest tests/test_liepin_live_pi_agent.py -q
```

The doc must say this live smoke is expected to block with `liepin_pi_dokobot_mcp_command_missing` until a real DokoBot MCP server command/tool set is configured.

- [ ] **Step 3: Add deferred work entry**

In `TODOS.md`, add one entry under the existing local-product/platform follow-ups:

```markdown
- Confirm and pin the official DokoBot MCP server startup command/tool names once DokoBot exposes a stable local command or config export. Current Pi bridge refuses to fake this; it accepts explicit root `.env` command/tool settings and blocks Liepin live search until they are proven.
- Add protected Pi MCP adapter proxy-proof validation if direct DokoBot tools cannot be exposed reliably. The first implementation accepts direct observed tool events only.
```

- [ ] **Step 4: Run docs grep**

Run:

```bash
rg -n "DokoBot MCP server startup command" TODOS.md docs/configuration.md docs/development.md README.md
```

Expected: the deferred command-name limitation appears only in developer/config docs and the deferred work list, not main Workbench UI code.

## Task 10: Final Verification

**Files:**
- No new files unless prior tasks identify missing focused tests.

- [ ] **Step 1: Run Python verification**

Run:

```bash
uv run ruff check \
  src/seektalent/config.py \
  src/seektalent/cli.py \
  src/seektalent/dev_mode.py \
  src/seektalent/providers/pi_agent/local_setup.py \
  src/seektalent/providers/pi_agent/pi_external.py \
  src/seektalent/providers/liepin/client.py \
  src/seektalent/providers/liepin/pi_executor.py \
  src/seektalent/providers/liepin/pi_worker_client.py \
  src/seektalent_ui/workbench_routes.py \
  tests/test_liepin_config.py \
  tests/test_cli.py \
  tests/test_dev_mode_readiness.py \
  tests/test_pi_dokobot_local_setup.py \
  tests/test_pi_external_agent.py \
  tests/test_liepin_pi_executor.py \
  tests/test_liepin_pi_worker_client.py
```

Expected: pass.

- [ ] **Step 2: Run focused tests**

Run:

```bash
uv run pytest \
  tests/test_liepin_config.py \
  tests/test_pi_dokobot_local_setup.py \
  tests/test_pi_external_agent.py \
  tests/test_liepin_pi_executor.py \
  tests/test_liepin_pi_worker_client.py \
  tests/test_dev_mode_readiness.py \
  tests/test_cli.py -k 'pi_agent or liepin_pi' -q
```

Expected: pass.

- [ ] **Step 3: Run frontend checks**

Run:

```bash
cd apps/web-svelte
bun run check
bun run build
```

Expected: pass. The existing Vite large chunk warning is acceptable if no new error appears.

- [ ] **Step 4: Run static boundary checks**

Run:

```bash
rg -n "DokoBotClient|DokoBotCapabilityProbe|subprocess\\..*dokobot|dokobot_action|server_managed_browser|login/frame|login/snapshot|storageState" \
  src/seektalent/runtime \
  src/seektalent/providers/liepin/runtime_lane.py \
  src/seektalent/providers/liepin/pi_executor.py \
  src/seektalent/providers/liepin/pi_worker_client.py \
  src/seektalent/providers/liepin/client.py \
  apps/web-svelte/src/routes \
  apps/web-svelte/src/lib/components \
  apps/web-svelte/src/lib/workbench \
  apps/web-svelte/src/lib/api/workbench.ts
```

Expected: no matches in the new Pi/DokoBot product path and Svelte primary flow. Do not scan `src/seektalent_ui/workbench_routes.py` or generated `apps/web-svelte/src/lib/api/schema.d.ts` in this slice because existing legacy managed-browser endpoints are still present but not expanded or linked by this plan.

- [ ] **Step 5: Validate generated Pi command shape without secrets**

Run:

```bash
bash -n scripts/start-dev-workbench.sh
rg -n "SEEKTALENT_PI_BAILIAN_API_KEY|SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET" scripts/start-dev-workbench.sh
```

Expected: env var names appear, but no literal provider key or generated secret appears in the file.

## Self-Review Checklist

- Spec coverage: Tasks 1, 4, 5 cover pinned adapter and Pi command shape, including settings validation and launcher non-abort behavior; Tasks 2, 3, 6 cover explicit DokoBot MCP configuration and dev-mode diagnostics; Task 7 covers direct observed tool proof; Task 8 covers user-facing source-state projection; Task 9 covers docs/deferred proxy-proof work; Task 10 covers verification.
- Placeholder scan: The plan contains no placeholder tokens, no open-ended implementation steps, and no instruction to fake DokoBot MCP command/tool defaults.
- Type consistency: `dokobot_mcp_command`, `dokobot_mcp_args`, `dokobot_direct_tools`, and `expected_observed_tool_names` are introduced before later tasks use them.
