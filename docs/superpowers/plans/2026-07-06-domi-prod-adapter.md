# Domi Prod Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the prepared-machine Domi path use Domi Python, Domi Node, and Domi JWT for PyPI Workbench startup while preserving the existing non-Domi managed runtime path.

**Architecture:** Keep `seektalent workbench` as the core Workbench launch path. Add a thin `seektalent-domi` launcher that validates and normalizes Domi environment variables before delegating to Workbench. Keep OpenCLI package installation under SeekTalent's existing runtime root, but allow Node itself to come from Domi when Domi policy is selected.

**Tech Stack:** Python 3.12+, Pydantic settings, pytest, uv build, console scripts, existing OpenCLI launcher.

---

## Spec

Design source:

```text
docs/superpowers/specs/2026-07-06-domi-prod-adapter-design.md
```

## File Structure

- Modify `src/seektalent/product_env.py`: add `SEEKTALENT_PYTHON=sys.executable` to the packaged Workbench environment.
- Modify `tests/test_product_env.py`: prove `SEEKTALENT_PYTHON` is set to the current interpreter.
- Modify `src/seektalent/cli.py`: keep reason codes stable and change Workbench preflight messages to Chinese.
- Modify `tests/test_cli.py`: assert representative Chinese preflight messages.
- Modify `src/seektalent/opencli_launcher.py`: add Domi Node policy and external Node resolution without removing managed Node.
- Modify `tests/test_opencli_launcher.py`: cover Domi Node success and missing-node failure.
- Create `src/seektalent/domi_workbench.py`: validate Domi JWT/Node env, normalize env, and delegate to Workbench.
- Modify `pyproject.toml`: expose `seektalent-domi`.
- Create `tests/test_domi_workbench_launcher.py`: cover launcher env normalization and failure messages.
- Modify `tests/test_cli_packaging.py`: assert the built wheel exposes `seektalent-domi`.
- Modify `docs/development.md`, `docs/configuration.md`, and `docs/cli.md`: document the prepared-machine Domi path and manual Mac/Windows commands.

---

### Task 1: Pass The Current Python To OpenCLI Helper

**Files:**
- Modify: `src/seektalent/product_env.py`
- Modify: `tests/test_product_env.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_product_env.py`, add this import near the top:

```python
import sys
```

Add this test after `test_build_workbench_command_env_uses_managed_opencli_command`:

```python
def test_build_workbench_command_env_sets_helper_python_to_current_interpreter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    env = build_workbench_command_env(
        {
            "HOME": str(home),
            "PATH": "/usr/bin",
            "SEEKTALENT_TEXT_LLM_API_KEY": "test-key",
        }
    )

    assert env["SEEKTALENT_PYTHON"] == sys.executable
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/test_product_env.py::test_build_workbench_command_env_sets_helper_python_to_current_interpreter -q
```

Expected: fail with `KeyError: 'SEEKTALENT_PYTHON'`.

- [ ] **Step 3: Implement the minimal code**

In `src/seektalent/product_env.py`, add this import:

```python
import sys
```

In `build_workbench_command_env()`, add the helper Python assignment immediately after the OpenCLI backend settings:

```python
    env["SEEKTALENT_RUNTIME_MODE"] = "prod"
    env["SEEKTALENT_PROVIDER_NAME"] = "liepin"
    env["SEEKTALENT_LIEPIN_WORKER_MODE"] = "opencli"
    env["SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND"] = "opencli"
    env["SEEKTALENT_PYTHON"] = sys.executable
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
uv run pytest tests/test_product_env.py::test_build_workbench_command_env_sets_helper_python_to_current_interpreter -q
```

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/seektalent/product_env.py tests/test_product_env.py
git commit -m "fix: pass Workbench helper Python"
```

Expected: commit succeeds.

---

### Task 2: Localize Workbench Preflight Messages

**Files:**
- Modify: `src/seektalent/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing message assertions**

In `tests/test_cli.py`, update `test_workbench_command_requires_text_llm_key_before_launch` by adding this assertion after the existing reason-code assertion:

```python
    assert "未配置大模型 API Key" in captured.err
```

Update `test_workbench_command_requires_domi_jwt_for_domi_provider` by adding this assertion after the existing reason-code assertion:

```python
    assert "未获取到 Domi 大模型授权" in captured.err
```

Update `test_workbench_command_reports_opencli_extension_disconnected` by adding this assertion after the existing reason-code assertion:

```python
    assert "未检测到 Chrome 中的 OpenCLI 插件连接" in captured.err
```

Update `test_workbench_command_reports_liepin_login_required` by adding this assertion after the existing reason-code assertion:

```python
    assert "猎聘未登录" in captured.err
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest \
  tests/test_cli.py::test_workbench_command_requires_text_llm_key_before_launch \
  tests/test_cli.py::test_workbench_command_requires_domi_jwt_for_domi_provider \
  tests/test_cli.py::test_workbench_command_reports_opencli_extension_disconnected \
  tests/test_cli.py::test_workbench_command_reports_liepin_login_required \
  -q
```

Expected: fail because the current messages are English.

- [ ] **Step 3: Implement Chinese messages**

In `src/seektalent/cli.py`, update the credential messages inside `_workbench_startup_preflight()`:

```python
            _print_workbench_reason(
                "seektalent_domi_jwt_missing",
                "未获取到 Domi 大模型授权。请在当前终端设置 SEEKTALENT_DOMI_JWT 后重试。",
            )
            return False
    elif not str(env.get("SEEKTALENT_TEXT_LLM_API_KEY") or "").strip():
        _print_workbench_reason(
            "seektalent_text_llm_api_key_missing",
            "未配置大模型 API Key。请在当前终端或 ~/.seektalent/.env 中设置 SEEKTALENT_TEXT_LLM_API_KEY。",
        )
        return False
```

In `_workbench_startup_preflight()`, update the OpenCLI bootstrap failure message:

```python
        _print_workbench_reason(
            "liepin_opencli_bootstrap_failed",
            f"OpenCLI/Node 启动失败：{exc}",
        )
        return False
```

Replace the body of `_workbench_reason_message()` with:

```python
def _workbench_reason_message(reason: str) -> str:
    return {
        "liepin_opencli_login_required": "猎聘未登录。请在 Chrome 中打开猎聘并完成登录后重试。",
        "liepin_opencli_identity_intercept": "猎聘需要选择身份或企业。请在 Chrome 中处理后重试。",
        "liepin_opencli_risk_page": "猎聘风控或验证码阻断了自动化。请人工完成验证后重试。",
        "liepin_opencli_extension_disconnected": "未检测到 Chrome 中的 OpenCLI 插件连接。请确认 Chrome 已启动、OpenCLI 插件已安装并启用，然后重试。",
        "liepin_opencli_daemon_stale": "OpenCLI 浏览器桥接服务状态已过期。请重启 OpenCLI 插件或 Chrome 后重试。",
        "liepin_opencli_daemon_not_running": "OpenCLI 浏览器桥接服务未运行。请打开 Chrome，并确认 OpenCLI 插件已连接后重试。",
        "liepin_opencli_bootstrap_failed": "OpenCLI/Node 启动失败。",
        "liepin_opencli_config_invalid": "SeekTalent OpenCLI 配置无效。",
        "liepin_opencli_removed_config": "检测到已移除的 Liepin OpenCLI 清理配置。请删除旧的 tab 清理设置后重试。",
        "liepin_opencli_helper_empty_output": "OpenCLI 浏览器 helper 没有返回结构化输出。",
        "liepin_opencli_helper_invalid_input": "OpenCLI 浏览器 helper 收到了无效输入。",
        "liepin_opencli_helper_invalid_output": "OpenCLI 浏览器 helper 返回了无效 JSON。",
        "liepin_opencli_helper_output_too_large": "OpenCLI 浏览器 helper 输出超过安全传输上限。",
        "liepin_opencli_malformed_state": "OpenCLI 浏览器桥接返回了无效的猎聘页面状态。",
        "liepin_opencli_lease_malformed": "OpenCLI 浏览器租约状态无效。请删除过期的 SeekTalent OpenCLI 租约文件后重试。",
        "liepin_opencli_owned_marker_malformed": "OpenCLI 浏览器受控标签页标记无效。请删除过期的 SeekTalent OpenCLI 租约文件后重试。",
        "liepin_opencli_tab_response_malformed": "OpenCLI 浏览器标签页命令返回异常。请重启 OpenCLI/Chrome 后重试。",
        "liepin_opencli_search_not_ready": "猎聘搜索页面未就绪。请确认当前 Chrome 可以正常打开猎聘人才搜索页。",
        "liepin_opencli_results_not_ready": "猎聘搜索结果尚未就绪。请确认页面加载完成后重试。",
        "liepin_opencli_timeout": "OpenCLI 浏览器桥接响应超时。",
    }.get(reason, "OpenCLI/猎聘启动前检查失败。")
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
uv run pytest \
  tests/test_cli.py::test_workbench_command_requires_text_llm_key_before_launch \
  tests/test_cli.py::test_workbench_command_requires_domi_jwt_for_domi_provider \
  tests/test_cli.py::test_workbench_command_reports_opencli_extension_disconnected \
  tests/test_cli.py::test_workbench_command_reports_liepin_login_required \
  -q
```

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/seektalent/cli.py tests/test_cli.py
git commit -m "fix: localize Workbench preflight messages"
```

Expected: commit succeeds.

---

### Task 3: Add Domi Node Policy To OpenCLI Launcher

**Files:**
- Modify: `src/seektalent/opencli_launcher.py`
- Modify: `tests/test_opencli_launcher.py`

- [ ] **Step 1: Write failing Domi Node tests**

Add these tests after `test_ensure_opencli_runtime_uses_managed_node_when_system_npm_is_missing` in `tests/test_opencli_launcher.py`:

```python
def test_domi_node_policy_uses_explicit_domi_node_without_downloading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    domi_bin = tmp_path / "domi-bin"
    domi_node = _write_fake_node(domi_bin, exit_code=0)
    _write_fake_npm(domi_bin)
    _write_managed_opencli(tmp_path / "runtime")

    def fail_managed_node(*_args, **_kwargs):
        raise AssertionError("Domi policy must not download managed Node")

    monkeypatch.setenv("SEEKTALENT_OPENCLI_NODE_POLICY", "domi")
    monkeypatch.setenv("SEEKTALENT_DOMI_NODE", str(domi_node))
    monkeypatch.setattr(opencli_launcher, "_ensure_managed_node", fail_managed_node)

    runtime = opencli_launcher.ensure_opencli_runtime(root=tmp_path / "runtime")

    assert runtime.node == domi_node
    assert runtime.opencli_main.name == "main.js"


def test_domi_node_policy_requires_domi_node_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SEEKTALENT_OPENCLI_NODE_POLICY", "domi")
    monkeypatch.delenv("SEEKTALENT_OPENCLI_NODE", raising=False)
    monkeypatch.delenv("SEEKTALENT_DOMI_NODE", raising=False)
    monkeypatch.delenv("DOMI_NODE", raising=False)

    with pytest.raises(opencli_launcher.BootstrapError, match="domi_node_missing"):
        opencli_launcher.ensure_opencli_runtime(root=tmp_path / "runtime")


def test_domi_node_env_accepts_node_bin_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    domi_bin = tmp_path / "domi-bin"
    domi_node = _write_fake_node(domi_bin, exit_code=0)
    _write_fake_npm(domi_bin)
    _write_managed_opencli(tmp_path / "runtime")
    monkeypatch.setenv("SEEKTALENT_OPENCLI_NODE_POLICY", "domi")
    monkeypatch.setenv("DOMI_NODE", str(domi_bin))

    runtime = opencli_launcher.ensure_opencli_runtime(root=tmp_path / "runtime")

    assert runtime.node == domi_node
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest \
  tests/test_opencli_launcher.py::test_domi_node_policy_uses_explicit_domi_node_without_downloading \
  tests/test_opencli_launcher.py::test_domi_node_policy_requires_domi_node_env \
  tests/test_opencli_launcher.py::test_domi_node_env_accepts_node_bin_directory \
  -q
```

Expected: fail because Domi Node policy is not implemented.

- [ ] **Step 3: Implement Domi Node resolution**

In `src/seektalent/opencli_launcher.py`, add these constants after `PROVIDER_SECRET_ENV_VARS`:

```python
OPENCLI_NODE_POLICY_ENV = "SEEKTALENT_OPENCLI_NODE_POLICY"
EXPLICIT_OPENCLI_NODE_ENV = "SEEKTALENT_OPENCLI_NODE"
DOMI_NODE_ENV_VARS = ("SEEKTALENT_DOMI_NODE", "DOMI_NODE")
```

Replace `ensure_opencli_runtime()` with:

```python
def ensure_opencli_runtime(
    *,
    root: Path | None = None,
    node_version: str = NODE_VERSION,
    opencli_version: str = OPENCLI_VERSION,
) -> OpenCliRuntime:
    runtime_root = (root or RUNTIME_ROOT).expanduser()
    runtime_root.mkdir(parents=True, exist_ok=True)
    node_policy = (os.environ.get(OPENCLI_NODE_POLICY_ENV) or "").strip().lower()
    external_node = _configured_node_from_env()
    with _runtime_lock(runtime_root):
        if node_policy == "domi":
            if external_node is None:
                raise BootstrapError(
                    "domi_node_missing: SEEKTALENT_DOMI_NODE or DOMI_NODE is required when SEEKTALENT_OPENCLI_NODE_POLICY=domi"
                )
            node = _ensure_external_node(external_node)
        elif external_node is not None:
            node = _ensure_external_node(external_node)
        else:
            node = _ensure_managed_node(runtime_root, node_version=node_version)
        opencli_main = _ensure_managed_opencli(runtime_root, node=node, opencli_version=opencli_version)
    return OpenCliRuntime(node=node, opencli_main=opencli_main)
```

Add these helpers after `ensure_opencli_runtime()`:

```python
def _configured_node_from_env() -> Path | None:
    for key in (EXPLICIT_OPENCLI_NODE_ENV, *DOMI_NODE_ENV_VARS):
        raw = os.environ.get(key)
        if raw and raw.strip():
            return _resolve_node_env_path(raw)
    return None


def _resolve_node_env_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_dir():
        return path / ("node.exe" if sys.platform == "win32" else "node")
    return path


def _ensure_external_node(node: Path) -> Path:
    if not node.exists():
        raise BootstrapError(f"domi_node_missing: Node runtime is not executable: {node}")
    npm = _npm_for_node(node)
    if not npm.exists():
        raise BootstrapError(f"domi_node_missing: npm is missing beside Node runtime: {node}")
    return node
```

Replace `_npm_for_node()` with:

```python
def _npm_for_node(node: Path) -> Path:
    npm = node.parent / ("npm.cmd" if sys.platform == "win32" else "npm")
    if npm.exists():
        return npm
    raise BootstrapError(f"Node npm is missing beside {node}")
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
uv run pytest tests/test_opencli_launcher.py -q
```

Expected: all OpenCLI launcher tests pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/seektalent/opencli_launcher.py tests/test_opencli_launcher.py
git commit -m "feat: support Domi Node for OpenCLI"
```

Expected: commit succeeds.

---

### Task 4: Add Thin Domi Workbench Launcher

**Files:**
- Create: `src/seektalent/domi_workbench.py`
- Create: `tests/test_domi_workbench_launcher.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing launcher tests**

Create `tests/test_domi_workbench_launcher.py`:

```python
from __future__ import annotations

import os

from seektalent import domi_workbench


def test_prepare_domi_env_requires_jwt(monkeypatch) -> None:
    env: dict[str, str] = {"DOMI_NODE": "/opt/domi/node/bin/node"}

    error = domi_workbench.prepare_domi_env(env)

    assert error == ("seektalent_domi_jwt_missing", "未获取到 Domi 大模型授权。请在当前终端设置 SEEKTALENT_DOMI_JWT 后重试。")


def test_prepare_domi_env_requires_node(monkeypatch) -> None:
    env: dict[str, str] = {"SEEKTALENT_DOMI_JWT": "jwt"}

    error = domi_workbench.prepare_domi_env(env)

    assert error == ("domi_node_missing", "未找到 Domi Node 运行时。请在当前终端设置 SEEKTALENT_DOMI_NODE 或 DOMI_NODE 后重试。")


def test_prepare_domi_env_sets_provider_and_node_policy() -> None:
    env = {
        "SEEKTALENT_DOMI_JWT": "jwt",
        "DOMI_NODE": "/opt/domi/node/bin/node",
    }

    error = domi_workbench.prepare_domi_env(env)

    assert error is None
    assert env["SEEKTALENT_TEXT_LLM_PROVIDER_LABEL"] == "domi"
    assert env["SEEKTALENT_OPENCLI_NODE_POLICY"] == "domi"
    assert env["SEEKTALENT_OPENCLI_NODE"] == "/opt/domi/node/bin/node"
    assert env["SEEKTALENT_DOMI_LLM_CHANNEL"] == "seek_talent"


def test_domi_workbench_main_delegates_to_workbench(monkeypatch, capsys) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("SEEKTALENT_DOMI_JWT", "jwt")
    monkeypatch.setenv("SEEKTALENT_DOMI_NODE", "/opt/domi/node/bin/node")

    def fake_main(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    monkeypatch.setattr(domi_workbench, "seektalent_main", fake_main)

    assert domi_workbench.main(["--port", "8022"]) == 0

    assert calls == [["workbench", "--port", "8022"]]
    assert os.environ["SEEKTALENT_TEXT_LLM_PROVIDER_LABEL"] == "domi"
    assert os.environ["SEEKTALENT_OPENCLI_NODE_POLICY"] == "domi"
    assert capsys.readouterr().err == ""


def test_domi_workbench_main_prints_reason_code(monkeypatch, capsys) -> None:
    monkeypatch.delenv("SEEKTALENT_DOMI_JWT", raising=False)
    monkeypatch.delenv("SEEKTALENT_DOMI_NODE", raising=False)
    monkeypatch.delenv("DOMI_NODE", raising=False)

    assert domi_workbench.main([]) == 1

    captured = capsys.readouterr()
    assert "reason_code=seektalent_domi_jwt_missing" in captured.err
    assert "未获取到 Domi 大模型授权" in captured.err
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_domi_workbench_launcher.py -q
```

Expected: fail with `ImportError` because `seektalent.domi_workbench` does not exist.

- [ ] **Step 3: Implement the launcher**

Create `src/seektalent/domi_workbench.py`:

```python
from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping, Sequence

from seektalent.cli import main as seektalent_main


DOMI_NODE_KEYS = ("SEEKTALENT_DOMI_NODE", "DOMI_NODE")


def main(argv: Sequence[str] | None = None) -> int:
    error = prepare_domi_env(os.environ)
    if error is not None:
        reason_code, message = error
        print(f"reason_code={reason_code} {message}", file=sys.stderr)
        return 1
    return seektalent_main(["workbench", *list(sys.argv[1:] if argv is None else argv)])


def prepare_domi_env(env: MutableMapping[str, str]) -> tuple[str, str] | None:
    if not str(env.get("SEEKTALENT_DOMI_JWT") or "").strip():
        return (
            "seektalent_domi_jwt_missing",
            "未获取到 Domi 大模型授权。请在当前终端设置 SEEKTALENT_DOMI_JWT 后重试。",
        )
    node = _first_env(env, DOMI_NODE_KEYS)
    if node is None:
        return (
            "domi_node_missing",
            "未找到 Domi Node 运行时。请在当前终端设置 SEEKTALENT_DOMI_NODE 或 DOMI_NODE 后重试。",
        )
    env["SEEKTALENT_TEXT_LLM_PROVIDER_LABEL"] = "domi"
    env.setdefault("SEEKTALENT_DOMI_LLM_CHANNEL", "seek_talent")
    env["SEEKTALENT_OPENCLI_NODE_POLICY"] = "domi"
    env["SEEKTALENT_OPENCLI_NODE"] = node
    return None


def _first_env(env: MutableMapping[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = str(env.get(key) or "").strip()
        if value:
            return value
    return None


if __name__ == "__main__":
    raise SystemExit(main())
```

In `pyproject.toml`, add the script:

```toml
seektalent-domi = "seektalent.domi_workbench:main"
```

Place it under `[project.scripts]` next to `seektalent`.

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
uv run pytest tests/test_domi_workbench_launcher.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/seektalent/domi_workbench.py tests/test_domi_workbench_launcher.py pyproject.toml
git commit -m "feat: add Domi Workbench launcher"
```

Expected: commit succeeds.

---

### Task 5: Document And Package The Domi Adapter

**Files:**
- Modify: `tests/test_cli_packaging.py`
- Modify: `docs/development.md`
- Modify: `docs/configuration.md`
- Modify: `docs/cli.md`

- [ ] **Step 1: Add packaging assertion for the console script**

In `tests/test_cli_packaging.py`, after the existing `cli` path assignment, add:

```python
    domi_cli = bin_dir / ("seektalent-domi.exe" if os.name == "nt" else "seektalent-domi")
```

After the package install command, add:

```python
    assert domi_cli.exists()
```

- [ ] **Step 2: Run packaging test to verify pass**

Run:

```bash
uv run pytest tests/test_cli_packaging.py::test_built_wheel_runs_outside_repo -q
```

Expected: pass after Task 4 added the console script.

- [ ] **Step 3: Update development docs**

In `docs/development.md`, add this subsection immediately after `## Domi Runtime Smoke`:

````markdown
### Prepared-Machine Domi Workbench

Use this path when testing the installed package with Domi's own Python, Domi's own Node, and a manually provided Domi JWT.

Required inputs:

```bash
export DOMI_PYTHON="/Applications/Domi.app/Contents/Resources/extraResources/python/runtime/bin/python"
export DOMI_NODE="<path to Domi node executable or node bin directory>"
export SEEKTALENT_DOMI_JWT="<manually pasted Domi JWT>"
export SEEKTALENT_DOMI_LLM_CHANNEL="seek_talent"
```

Install and run:

```bash
"${DOMI_PYTHON}" -m pip install -U seektalent
"${DOMI_PYTHON}" -m seektalent.domi_workbench --port 8011
```

The launcher sets `SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi`, requires Domi Node, and delegates to `seektalent workbench`. It does not read Domi Electron storage and does not install the Chrome OpenCLI extension.
````

- [ ] **Step 4: Update configuration docs**

In `docs/configuration.md`, add this section after the installed PyPI Workbench paragraph:

````markdown
## Prepared-Machine Domi Workbench

When a test machine already has Domi installed, SeekTalent can run with Domi's Python, Domi's Node, and a Domi JWT provided in the terminal environment.

Required environment:

```env
SEEKTALENT_DOMI_JWT=<manually pasted Domi JWT>
SEEKTALENT_DOMI_NODE=<path to Domi node executable or node bin directory>
```

`DOMI_NODE` is accepted as an alias for `SEEKTALENT_DOMI_NODE`.

Run the installed launcher from the Domi Python environment:

```bash
seektalent-domi --port 8011
```

or:

```bash
python -m seektalent.domi_workbench --port 8011
```

This path sets `SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi` and `SEEKTALENT_OPENCLI_NODE_POLICY=domi` before starting Workbench. Missing Domi JWT or Domi Node fails before the server launches.
````

- [ ] **Step 5: Update CLI docs**

In `docs/cli.md`, add `seektalent-domi` to the command table and add this short section after the `seektalent workbench` section:

````markdown
## `seektalent-domi`

`seektalent-domi` is the prepared-machine Domi launcher. It expects the process to run from the Domi Python environment and requires:

```env
SEEKTALENT_DOMI_JWT=<manually pasted Domi JWT>
SEEKTALENT_DOMI_NODE=<path to Domi node executable or node bin directory>
```

It sets Domi LLM and Domi Node policy variables, then delegates to `seektalent workbench` with the same command-line arguments.
````

- [ ] **Step 6: Run docs and focused packaging checks**

Run:

```bash
uv run pytest tests/test_cli_packaging.py::test_built_wheel_runs_outside_repo -q
uv run pytest tests/test_product_env.py tests/test_cli.py::test_workbench_command_requires_domi_jwt_for_domi_provider tests/test_opencli_launcher.py tests/test_domi_workbench_launcher.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

Run:

```bash
git add tests/test_cli_packaging.py docs/development.md docs/configuration.md docs/cli.md
git commit -m "docs: document Domi Workbench adapter"
```

Expected: commit succeeds.

---

## Final Verification

Run:

```bash
uv run pytest \
  tests/test_product_env.py \
  tests/test_cli.py::test_workbench_command_requires_text_llm_key_before_launch \
  tests/test_cli.py::test_workbench_command_requires_domi_jwt_for_domi_provider \
  tests/test_cli.py::test_workbench_command_reports_opencli_extension_disconnected \
  tests/test_cli.py::test_workbench_command_reports_liepin_login_required \
  tests/test_opencli_launcher.py \
  tests/test_domi_workbench_launcher.py \
  tests/test_cli_packaging.py::test_built_wheel_runs_outside_repo \
  -q
```

Expected: all selected tests pass.

Run:

```bash
git diff --check
```

Expected: no whitespace errors.

Mac manual smoke after focused tests pass:

```bash
export DOMI_PYTHON="/Applications/Domi.app/Contents/Resources/extraResources/python/runtime/bin/python"
export DOMI_NODE="<path to Domi node executable or node bin directory>"
export SEEKTALENT_DOMI_JWT="<manually pasted Domi JWT>"
export SEEKTALENT_DOMI_LLM_CHANNEL="seek_talent"

"${DOMI_PYTHON}" -m pip install -U seektalent
"${DOMI_PYTHON}" -m seektalent.domi_workbench --port 8011
```

Expected:

- missing JWT prints `reason_code=seektalent_domi_jwt_missing`;
- missing Node prints `reason_code=domi_node_missing`;
- OpenCLI helper receives Domi Python through `SEEKTALENT_PYTHON`;
- OpenCLI uses Domi Node through `SEEKTALENT_OPENCLI_NODE`;
- Workbench starts only after Domi JWT and OpenCLI/Liepin preflight pass.

Windows manual verification is the same contract using `seektalent-domi.exe` or `python -m seektalent.domi_workbench` from the Domi Python environment.

## Plan Self-Review

- Spec coverage: the tasks cover helper Python, Chinese preflight messages, Domi Node policy, Domi launcher, packaging, docs, and Mac/Windows manual acceptance.
- Scope: this stays under the prepared-machine adapter slice and does not implement Domi storage, native messaging, extension install, or `domi/` browser-backend replacement.
- Type consistency: Domi launcher uses `prepare_domi_env()`, OpenCLI launcher uses `SEEKTALENT_OPENCLI_NODE_POLICY`, `SEEKTALENT_OPENCLI_NODE`, `SEEKTALENT_DOMI_NODE`, and `DOMI_NODE` consistently.
- Test coverage: automated tests cover deterministic env and packaging behavior; live browser and Windows checks remain manual by design.
