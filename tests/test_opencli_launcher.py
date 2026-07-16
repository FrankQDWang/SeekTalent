from __future__ import annotations

import json
from pathlib import Path

import pytest

from seektalent import opencli_launcher
from seektalent.opencli_browser.daemon_transport import REQUIRED_OPENCLI_BRIDGE_CAPABILITIES


def test_managed_opencli_version_is_pinned_to_wtscli_0_1_0() -> None:
    assert opencli_launcher.OPENCLI_PACKAGE == "@jackwener/opencli"
    assert opencli_launcher.OPENCLI_VERSION == "0.1.0"


def test_ensure_opencli_runtime_rejects_without_domi_node_even_if_system_node_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node = _write_fake_node(tmp_path / "bin", exit_code=0)
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


def test_ensure_opencli_runtime_uses_explicit_domi_node_without_downloading(
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
    assert runtime.opencli_main.name == "main.js"
    assert runtime.bridge_manifest == tmp_path / "browser-bridge" / "bridge-manifest.json"
    assert calls == [[str(domi_node), "--version"], [str(domi_node), str(opencli_main), "--help"]]


def test_existing_opencli_is_probed_with_domi_node_without_downloading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "node-argv.txt"
    domi_node = _write_fake_node(tmp_path / "domi-bin", exit_code=0, log_path=log_path)
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


def test_verified_existing_opencli_is_not_reprobed_on_next_runtime_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    domi_node = _write_fake_node(tmp_path / "domi-bin", exit_code=0)
    runtime_root = tmp_path / "runtime"
    opencli_main = _write_managed_opencli(runtime_root)
    monkeypatch.setenv("SEEKTALENT_DOMI_NODE", str(domi_node))
    calls = _patch_existing_opencli_subprocess(
        monkeypatch,
        node=domi_node,
        opencli_main=opencli_main,
    )

    first_runtime = opencli_launcher.ensure_opencli_runtime(root=runtime_root)
    second_runtime = opencli_launcher.ensure_opencli_runtime(root=runtime_root)

    assert first_runtime.node == second_runtime.node == domi_node
    assert first_runtime.opencli_main == second_runtime.opencli_main == opencli_main
    assert calls == [[str(domi_node), "--version"], [str(domi_node), str(opencli_main), "--help"]]


def test_verification_stamp_write_tolerates_concurrent_matching_writer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    domi_node = _write_fake_node(tmp_path / "domi-bin", exit_code=0)
    runtime_root = tmp_path / "runtime"
    opencli_main = _write_managed_opencli(runtime_root)
    install_dir = opencli_launcher._opencli_install_dir(runtime_root, opencli_launcher.OPENCLI_VERSION)
    package_json = opencli_launcher._opencli_package_json_path(install_dir)
    bridge_identity = opencli_launcher._opencli_bridge_identity_path(install_dir)
    bridge_manifest = opencli_launcher._bridge_manifest_path(runtime_root)
    stamp_path = opencli_launcher._verification_stamp_path(install_dir)
    opencli_launcher._write_verification_stamp(
        stamp_path,
        node=domi_node,
        opencli_main=opencli_main,
        package_json=package_json,
        bridge_identity=bridge_identity,
        bridge_manifest=bridge_manifest,
        opencli_version=opencli_launcher.OPENCLI_VERSION,
    )

    def lose_replace_race(self: Path, target: Path) -> None:
        raise FileNotFoundError(f"concurrent replace already handled {self} -> {target}")

    monkeypatch.setattr(Path, "replace", lose_replace_race)

    opencli_launcher._write_verification_stamp(
        stamp_path,
        node=domi_node,
        opencli_main=opencli_main,
        package_json=package_json,
        bridge_identity=bridge_identity,
        bridge_manifest=bridge_manifest,
        opencli_version=opencli_launcher.OPENCLI_VERSION,
    )


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


def test_missing_opencli_fails_without_running_npm_or_any_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    domi_node = _write_fake_node(tmp_path / "domi-bin", exit_code=0)
    runtime_root = tmp_path / "runtime"
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append([str(part) for part in argv])
        raise AssertionError("missing offline runtime must not launch a subprocess")

    monkeypatch.setattr(opencli_launcher.subprocess, "run", fake_run)

    with pytest.raises(opencli_launcher.BootstrapError, match="opencli_offline_runtime_missing"):
        opencli_launcher.ensure_opencli_runtime(
            root=runtime_root,
            env={"SEEKTALENT_DOMI_NODE": str(domi_node)},
        )

    assert calls == []


def test_installed_opencli_requires_the_paired_bridge_manifest(tmp_path: Path) -> None:
    domi_node = _write_fake_node(tmp_path / "domi-bin", exit_code=0)
    _write_managed_opencli(tmp_path / "runtime")
    (tmp_path / "browser-bridge" / "bridge-manifest.json").unlink()

    with pytest.raises(opencli_launcher.BootstrapError, match="opencli_bridge_integrity_failed"):
        opencli_launcher.ensure_opencli_runtime(
            root=tmp_path / "runtime",
            env={"SEEKTALENT_DOMI_NODE": str(domi_node)},
        )


def test_installed_opencli_rejects_runtime_from_another_bridge_build(tmp_path: Path) -> None:
    domi_node = _write_fake_node(tmp_path / "domi-bin", exit_code=0)
    _write_managed_opencli(tmp_path / "runtime")
    install_dir = opencli_launcher._opencli_install_dir(
        tmp_path / "runtime", opencli_launcher.OPENCLI_VERSION
    )
    identity_path = opencli_launcher._opencli_bridge_identity_path(install_dir)
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["bridgeBuildId"] = "seektalent-opencli-0.1.0+stale"
    identity_path.write_text(json.dumps(identity), encoding="utf-8")

    with pytest.raises(opencli_launcher.BootstrapError, match="opencli_bridge_build_mismatch"):
        opencli_launcher.ensure_opencli_runtime(
            root=tmp_path / "runtime",
            env={"SEEKTALENT_DOMI_NODE": str(domi_node)},
        )


def test_existing_opencli_must_be_executable_by_domi_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    domi_node = _write_fake_node(tmp_path / "domi-bin", exit_code=9)
    opencli_main = _write_managed_opencli(tmp_path / "runtime")
    monkeypatch.setenv("SEEKTALENT_DOMI_NODE", str(domi_node))
    _patch_existing_opencli_subprocess(
        monkeypatch,
        node=domi_node,
        opencli_main=opencli_main,
        probe_returncode=9,
    )

    with pytest.raises(opencli_launcher.BootstrapError, match="WTSCLI 0\\.1\\.0 usability probe failed"):
        opencli_launcher.ensure_opencli_runtime(root=tmp_path / "runtime")


def test_ensure_opencli_runtime_rejects_unusable_domi_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    domi_node = _write_fake_node(tmp_path / "domi-bin", exit_code=0)
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
    opencli_main = _write_managed_opencli(tmp_path / "runtime")
    monkeypatch.setenv("DOMI_NODE", str(domi_bin))
    _patch_existing_opencli_subprocess(
        monkeypatch,
        node=domi_node,
        opencli_main=opencli_main,
    )

    runtime = opencli_launcher.ensure_opencli_runtime(root=tmp_path / "runtime")

    assert runtime.node == domi_node


def test_opencli_subprocess_env_excludes_provider_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    node = _write_fake_node(tmp_path / "managed-bin", exit_code=0)
    monkeypatch.setenv("SEEKTALENT_DOMI_JWT", "domi-secret-jwt")
    monkeypatch.setenv("SEEKTALENT_DOMI_LLM_BASE_URL", "https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1")
    monkeypatch.setenv("SEEKTALENT_DOMI_LLM_CHANNEL", "seek_talent")
    monkeypatch.setenv("SEEKTALENT_TEXT_LLM_API_KEY", "text-secret-key")
    captured_env = opencli_launcher.opencli_subprocess_env(node_bin_dir=node.parent)

    assert "SEEKTALENT_DOMI_JWT" not in captured_env
    assert "SEEKTALENT_DOMI_LLM_BASE_URL" not in captured_env
    assert "SEEKTALENT_DOMI_LLM_CHANNEL" not in captured_env
    assert "SEEKTALENT_TEXT_LLM_API_KEY" not in captured_env
    assert str(node.parent) in captured_env["PATH"]


def _write_managed_opencli(root: Path) -> Path:
    package_dir = root / "opencli" / opencli_launcher.OPENCLI_VERSION / "node_modules" / "@jackwener" / "opencli"
    main = package_dir / "dist" / "src" / "main.js"
    main.parent.mkdir(parents=True)
    main.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    (package_dir / "package.json").write_text(
        f'{{"version": "{opencli_launcher.OPENCLI_VERSION}"}}\n',
        encoding="utf-8",
    )
    bridge_identity = {
        "implementation": "seektalent-opencli",
        "bridgeBuildId": "seektalent-opencli-0.1.0+test",
        "protocolVersion": {"major": 1, "minor": 0},
        "capabilities": sorted(REQUIRED_OPENCLI_BRIDGE_CAPABILITIES),
    }
    (package_dir / "bridge-identity.json").write_text(
        json.dumps(bridge_identity),
        encoding="utf-8",
    )
    bridge_manifest = root.parent / "browser-bridge" / "bridge-manifest.json"
    bridge_manifest.parent.mkdir(parents=True, exist_ok=True)
    bridge_manifest.write_text(
        json.dumps(
            {
                "schemaVersion": "seektalent.browser_bridge_bundle.v1",
                **bridge_identity,
            }
        ),
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
