from __future__ import annotations

from pathlib import Path

import pytest

from seektalent import opencli_launcher


def test_managed_opencli_version_is_pinned_to_1_8_6() -> None:
    assert opencli_launcher.OPENCLI_PACKAGE == "@jackwener/opencli"
    assert opencli_launcher.OPENCLI_VERSION == "1.8.6"


def test_ensure_opencli_runtime_ignores_supported_system_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node = _write_fake_node(tmp_path / "bin", exit_code=0)
    managed_node = _write_fake_node(tmp_path / "managed-bin", exit_code=0)
    _write_fake_npm(node.parent)
    _write_managed_opencli(tmp_path / "runtime")
    monkeypatch.setenv("PATH", str(node.parent))
    monkeypatch.setattr(opencli_launcher, "_ensure_managed_node", lambda *_args, **_kwargs: managed_node)

    runtime = opencli_launcher.ensure_opencli_runtime(root=tmp_path / "runtime")

    assert runtime.node == managed_node
    assert runtime.opencli_main.name == "main.js"


def test_ensure_opencli_runtime_uses_managed_node_when_system_npm_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_fake_node(tmp_path / "system-bin", exit_code=0)
    managed_node = _write_fake_node(tmp_path / "managed-bin", exit_code=0)
    _write_managed_opencli(tmp_path / "runtime")
    monkeypatch.setenv("PATH", str(tmp_path / "system-bin"))
    monkeypatch.setattr(opencli_launcher, "_ensure_managed_node", lambda *_args, **_kwargs: managed_node)

    runtime = opencli_launcher.ensure_opencli_runtime(root=tmp_path / "runtime")

    assert runtime.node == managed_node


def test_launcher_delegates_to_managed_opencli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "node-argv.txt"
    node = _write_fake_node(tmp_path / "bin", exit_code=7, log_path=log_path)
    opencli_main = _write_managed_opencli(tmp_path / "runtime")
    monkeypatch.setattr(
        opencli_launcher,
        "ensure_opencli_runtime",
        lambda: opencli_launcher.OpenCliRuntime(node=node, opencli_main=opencli_main),
    )

    assert opencli_launcher.main(["browser", "seektalent-liepin", "state"]) == 7

    argv = log_path.read_text(encoding="utf-8").splitlines()
    assert argv[0].endswith("/node_modules/@jackwener/opencli/dist/src/main.js")
    assert argv[1:] == ["browser", "seektalent-liepin", "state"]


def test_opencli_install_requires_managed_npm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node = _write_fake_node(tmp_path / "managed-bin", exit_code=0)
    _write_fake_npm(tmp_path / "system-bin")
    monkeypatch.setenv("PATH", str(tmp_path / "system-bin"))

    with pytest.raises(opencli_launcher.BootstrapError):
        opencli_launcher._npm_for_node(node)


def test_domi_node_policy_uses_explicit_domi_node_without_downloading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    domi_node = _write_fake_node(tmp_path / "domi-bin", exit_code=0)
    _write_fake_npm(domi_node.parent)
    _write_managed_opencli(tmp_path / "runtime")
    monkeypatch.setenv("SEEKTALENT_OPENCLI_NODE_POLICY", "domi")
    monkeypatch.setenv("SEEKTALENT_DOMI_NODE", str(domi_node))
    monkeypatch.setattr(
        opencli_launcher,
        "_ensure_managed_node",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Domi policy must not download managed Node")
        ),
    )

    runtime = opencli_launcher.ensure_opencli_runtime(root=tmp_path / "runtime")

    assert runtime.node == domi_node
    assert runtime.opencli_main.name == "main.js"


def test_domi_node_policy_accepts_domi_bundled_npm_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    domi_node = _write_fake_node(tmp_path / "domi-node", exit_code=0)
    _write_domi_bundled_npm_cli(domi_node.parent)
    _write_managed_opencli(tmp_path / "runtime")
    monkeypatch.setenv("SEEKTALENT_OPENCLI_NODE_POLICY", "domi")
    monkeypatch.setenv("SEEKTALENT_DOMI_NODE", str(domi_node))
    monkeypatch.setattr(
        opencli_launcher,
        "_ensure_managed_node",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Domi policy must not download managed Node")
        ),
    )

    runtime = opencli_launcher.ensure_opencli_runtime(root=tmp_path / "runtime")

    assert runtime.node == domi_node


def test_domi_node_policy_rejects_unusable_external_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    domi_node = _write_fake_node(tmp_path / "domi-bin", exit_code=0)
    _write_fake_npm(domi_node.parent)
    _write_managed_opencli(tmp_path / "runtime")
    monkeypatch.setenv("SEEKTALENT_OPENCLI_NODE_POLICY", "domi")
    monkeypatch.setenv("SEEKTALENT_DOMI_NODE", str(domi_node))
    version_probe_calls: list[tuple[str, ...]] = []

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "not node"

    def fake_run(argv, **_kwargs):
        version_probe_calls.append(tuple(str(part) for part in argv))
        return Completed()

    monkeypatch.setattr(opencli_launcher.subprocess, "run", fake_run)

    with pytest.raises(opencli_launcher.BootstrapError, match="domi_node_missing"):
        opencli_launcher.ensure_opencli_runtime(root=tmp_path / "runtime")
    assert version_probe_calls == [(str(domi_node), "--version")]


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


def test_opencli_install_env_excludes_provider_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node = _write_fake_node(tmp_path / "managed-bin", exit_code=0)
    _write_fake_npm(node.parent)
    captured_env: dict[str, str] = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(_argv, **kwargs):
        captured_env.update(kwargs["env"])
        _write_managed_opencli(tmp_path / "runtime")
        return Completed()

    monkeypatch.setenv("SEEKTALENT_DOMI_JWT", "domi-secret-jwt")
    monkeypatch.setenv("SEEKTALENT_DOMI_LLM_BASE_URL", "https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1")
    monkeypatch.setenv("SEEKTALENT_DOMI_LLM_CHANNEL", "seek_talent")
    monkeypatch.setenv("SEEKTALENT_TEXT_LLM_API_KEY", "text-secret-key")
    monkeypatch.setattr(opencli_launcher.subprocess, "run", fake_run)

    opencli_launcher._ensure_managed_opencli(
        tmp_path / "runtime",
        node=node,
        opencli_version=opencli_launcher.OPENCLI_VERSION,
    )

    assert "SEEKTALENT_DOMI_JWT" not in captured_env
    assert "SEEKTALENT_DOMI_LLM_BASE_URL" not in captured_env
    assert "SEEKTALENT_DOMI_LLM_CHANNEL" not in captured_env
    assert "SEEKTALENT_TEXT_LLM_API_KEY" not in captured_env
    assert str(node.parent) in captured_env["PATH"]


def test_opencli_install_uses_domi_bundled_npm_cli_when_npm_cmd_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node = _write_fake_node(tmp_path / "domi-node", exit_code=0)
    npm_cli = _write_domi_bundled_npm_cli(node.parent)
    captured_argv: list[str] = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **_kwargs):
        captured_argv.extend(str(part) for part in argv)
        _write_managed_opencli(tmp_path / "runtime")
        return Completed()

    monkeypatch.setattr(opencli_launcher.subprocess, "run", fake_run)

    opencli_launcher._ensure_managed_opencli(
        tmp_path / "runtime",
        node=node,
        opencli_version=opencli_launcher.OPENCLI_VERSION,
    )

    assert captured_argv[:2] == [str(node), str(npm_cli)]
    assert "install" in captured_argv


def _write_managed_opencli(root: Path) -> Path:
    package_dir = root / "opencli" / opencli_launcher.OPENCLI_VERSION / "node_modules" / "@jackwener" / "opencli"
    main = package_dir / "dist" / "src" / "main.js"
    main.parent.mkdir(parents=True)
    main.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    (package_dir / "package.json").write_text(
        f'{{"version": "{opencli_launcher.OPENCLI_VERSION}"}}\n',
        encoding="utf-8",
    )
    return main


def _write_fake_node(bin_dir: Path, *, exit_code: int, log_path: Path | None = None) -> Path:
    node = bin_dir / "node"
    node.parent.mkdir(parents=True)
    log_line = f'printf "%s\\n" "$@" > {log_path!s}\n' if log_path else ""
    node.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "v24.16.0"; exit 0; fi\n'
        f"{log_line}"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    node.chmod(0o755)
    return node


def _write_fake_npm(bin_dir: Path) -> Path:
    npm = bin_dir / "npm"
    npm.parent.mkdir(parents=True, exist_ok=True)
    npm.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    npm.chmod(0o755)
    return npm


def _write_domi_bundled_npm_cli(node_dir: Path) -> Path:
    npm_cli = node_dir / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    npm_cli.parent.mkdir(parents=True, exist_ok=True)
    npm_cli.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    return npm_cli
