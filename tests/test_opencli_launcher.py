from __future__ import annotations

from pathlib import Path

import pytest

from seektalent import opencli_launcher


def test_managed_opencli_version_is_pinned_to_1_8_6() -> None:
    assert opencli_launcher.OPENCLI_PACKAGE == "@jackwener/opencli"
    assert opencli_launcher.OPENCLI_VERSION == "1.8.6"


def test_ensure_opencli_runtime_rejects_without_domi_node_even_if_system_node_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node = _write_fake_node(tmp_path / "bin", exit_code=0)
    _write_fake_npm(node.parent)
    _write_managed_opencli(tmp_path / "runtime")
    monkeypatch.setenv("PATH", str(node.parent))
    monkeypatch.delenv("SEEKTALENT_OPENCLI_NODE", raising=False)
    monkeypatch.delenv("SEEKTALENT_DOMI_NODE", raising=False)
    monkeypatch.delenv("DOMI_NODE", raising=False)

    with pytest.raises(opencli_launcher.BootstrapError, match="domi_node_missing"):
        opencli_launcher.ensure_opencli_runtime(root=tmp_path / "runtime")


def test_ensure_opencli_runtime_does_not_download_replacement_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_fake_node(tmp_path / "system-bin", exit_code=0)
    _write_managed_opencli(tmp_path / "runtime")
    monkeypatch.setenv("PATH", str(tmp_path / "system-bin"))
    monkeypatch.delenv("SEEKTALENT_OPENCLI_NODE", raising=False)
    monkeypatch.delenv("SEEKTALENT_DOMI_NODE", raising=False)
    monkeypatch.delenv("DOMI_NODE", raising=False)

    with pytest.raises(opencli_launcher.BootstrapError, match="domi_node_missing"):
        opencli_launcher.ensure_opencli_runtime(root=tmp_path / "runtime")


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


def test_opencli_install_requires_npm_from_domi_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node = _write_fake_node(tmp_path / "managed-bin", exit_code=0)
    _write_fake_npm(tmp_path / "system-bin")
    monkeypatch.setenv("PATH", str(tmp_path / "system-bin"))

    with pytest.raises(opencli_launcher.BootstrapError):
        opencli_launcher._npm_for_node(node)


def test_ensure_opencli_runtime_uses_explicit_domi_node_without_downloading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    domi_node = _write_fake_node(tmp_path / "domi-bin", exit_code=0)
    _write_fake_npm(domi_node.parent)
    opencli_main = _write_managed_opencli(tmp_path / "runtime")
    monkeypatch.setenv("SEEKTALENT_DOMI_NODE", str(domi_node))
    calls = _patch_existing_opencli_subprocess(
        monkeypatch,
        node=domi_node,
        opencli_main=opencli_main,
    )

    runtime = opencli_launcher.ensure_opencli_runtime(root=tmp_path / "runtime")

    assert runtime.node == domi_node
    assert runtime.opencli_main.name == "main.js"
    assert calls == [[str(domi_node), "--version"], [str(domi_node), str(opencli_main), "--help"]]


def test_existing_opencli_is_probed_with_domi_node_without_downloading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "node-argv.txt"
    domi_node = _write_fake_node(tmp_path / "domi-bin", exit_code=0, log_path=log_path)
    _write_fake_npm(domi_node.parent)
    opencli_main = _write_managed_opencli(tmp_path / "runtime")
    monkeypatch.setenv("SEEKTALENT_DOMI_NODE", str(domi_node))
    _patch_existing_opencli_subprocess(
        monkeypatch,
        node=domi_node,
        opencli_main=opencli_main,
        log_path=log_path,
    )

    runtime = opencli_launcher.ensure_opencli_runtime(root=tmp_path / "runtime")

    assert runtime.node == domi_node
    assert runtime.opencli_main == opencli_main
    assert log_path.read_text(encoding="utf-8").splitlines() == [str(opencli_main), "--help"]


def test_existing_opencli_does_not_require_npm_from_domi_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    domi_node = _write_fake_node(tmp_path / "domi-bin", exit_code=0)
    opencli_main = _write_managed_opencli(tmp_path / "runtime")
    monkeypatch.setenv("SEEKTALENT_DOMI_NODE", str(domi_node))
    calls = _patch_existing_opencli_subprocess(
        monkeypatch,
        node=domi_node,
        opencli_main=opencli_main,
    )

    runtime = opencli_launcher.ensure_opencli_runtime(root=tmp_path / "runtime")

    assert runtime.node == domi_node
    assert runtime.opencli_main == opencli_main
    assert all("npm" not in part for call in calls for part in call)


def test_missing_opencli_installs_pinned_cli_with_domi_node_and_probes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    domi_node = _write_fake_node(tmp_path / "domi-bin", exit_code=0)
    npm = _write_fake_npm(domi_node.parent)
    runtime_root = tmp_path / "runtime"
    expected_main = (
        runtime_root
        / "opencli"
        / opencli_launcher.OPENCLI_VERSION
        / "node_modules"
        / "@jackwener"
        / "opencli"
        / "dist"
        / "src"
        / "main.js"
    )
    calls: list[list[str]] = []

    class Completed:
        def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(argv, **_kwargs):
        argv_list = [str(part) for part in argv]
        calls.append(argv_list)
        if argv_list == [str(domi_node), "--version"]:
            return Completed(stdout="v24.16.0\n")
        if argv_list[:2] == [str(npm), "install"]:
            _write_managed_opencli(runtime_root)
            return Completed()
        if argv_list == [str(domi_node), str(expected_main), "--help"]:
            return Completed(stdout="Usage: opencli\n")
        raise AssertionError(f"Unexpected subprocess call: {argv_list}")

    monkeypatch.setattr(opencli_launcher.subprocess, "run", fake_run)

    runtime = opencli_launcher.ensure_opencli_runtime(
        root=runtime_root,
        env={"SEEKTALENT_DOMI_NODE": str(domi_node)},
    )

    assert runtime.node == domi_node
    assert runtime.opencli_main == expected_main
    assert any(call[:2] == [str(npm), "install"] for call in calls)
    assert f"{opencli_launcher.OPENCLI_PACKAGE}@{opencli_launcher.OPENCLI_VERSION}" in calls[1]
    assert calls[-1] == [str(domi_node), str(expected_main), "--help"]


def test_existing_opencli_must_be_executable_by_domi_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    domi_node = _write_fake_node(tmp_path / "domi-bin", exit_code=9)
    _write_fake_npm(domi_node.parent)
    opencli_main = _write_managed_opencli(tmp_path / "runtime")
    monkeypatch.setenv("SEEKTALENT_DOMI_NODE", str(domi_node))
    _patch_existing_opencli_subprocess(
        monkeypatch,
        node=domi_node,
        opencli_main=opencli_main,
        probe_returncode=9,
    )

    with pytest.raises(opencli_launcher.BootstrapError, match="OpenCLI 1\\.8\\.6 usability probe failed"):
        opencli_launcher.ensure_opencli_runtime(root=tmp_path / "runtime")


def test_ensure_opencli_runtime_accepts_domi_bundled_npm_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    domi_node = _write_fake_node(tmp_path / "domi-node", exit_code=0)
    _write_domi_bundled_npm_cli(domi_node.parent)
    opencli_main = _write_managed_opencli(tmp_path / "runtime")
    monkeypatch.setenv("SEEKTALENT_DOMI_NODE", str(domi_node))
    _patch_existing_opencli_subprocess(
        monkeypatch,
        node=domi_node,
        opencli_main=opencli_main,
    )

    runtime = opencli_launcher.ensure_opencli_runtime(root=tmp_path / "runtime")

    assert runtime.node == domi_node


def test_ensure_opencli_runtime_rejects_unusable_domi_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    domi_node = _write_fake_node(tmp_path / "domi-bin", exit_code=0)
    _write_fake_npm(domi_node.parent)
    _write_managed_opencli(tmp_path / "runtime")
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


def test_node_version_probe_decodes_subprocess_output_as_utf8(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node = _write_fake_node(tmp_path / "domi-bin", exit_code=0)
    captured_kwargs: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = "v22.14.0\n"
        stderr = ""

    def fake_run(_argv, **kwargs):
        captured_kwargs.update(kwargs)
        return Completed()

    monkeypatch.setattr(opencli_launcher.subprocess, "run", fake_run)

    opencli_launcher._probe_node_version(node)

    assert captured_kwargs["encoding"] == "utf-8"
    assert captured_kwargs["errors"] == "replace"


def test_ensure_opencli_runtime_requires_domi_node_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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
    opencli_main = _write_managed_opencli(tmp_path / "runtime")
    monkeypatch.setenv("DOMI_NODE", str(domi_bin))
    _patch_existing_opencli_subprocess(
        monkeypatch,
        node=domi_node,
        opencli_main=opencli_main,
    )

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
    captured_kwargs: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kwargs):
        captured_argv.extend(str(part) for part in argv)
        captured_kwargs.update(kwargs)
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
    assert captured_kwargs["encoding"] == "utf-8"
    assert captured_kwargs["errors"] == "replace"


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


def _patch_existing_opencli_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    *,
    node: Path,
    opencli_main: Path,
    log_path: Path | None = None,
    probe_returncode: int = 0,
) -> list[list[str]]:
    calls: list[list[str]] = []

    class Completed:
        def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(argv, **_kwargs):
        argv_list = [str(part) for part in argv]
        calls.append(argv_list)
        if argv_list == [str(node), "--version"]:
            return Completed(stdout="v24.16.0\n")
        if argv_list == [str(node), str(opencli_main), "--help"]:
            if log_path is not None:
                log_path.write_text(f"{opencli_main}\n--help\n", encoding="utf-8")
            return Completed(
                returncode=probe_returncode,
                stdout="Usage: opencli\n" if probe_returncode == 0 else "",
                stderr="probe failed\n" if probe_returncode != 0 else "",
            )
        raise AssertionError(f"Unexpected subprocess call: {argv_list}")

    monkeypatch.setattr(opencli_launcher.subprocess, "run", fake_run)
    return calls


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
