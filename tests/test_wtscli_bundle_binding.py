from __future__ import annotations

import builtins
import copy
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from seektalent import browser_bridge_manifest, opencli_launcher
from seektalent.opencli_browser.contracts import OpenCliBrowserError
from seektalent.opencli_browser.daemon_transport import OpenCliDaemonClient
from seektalent.opencli_browser.reason_codes import OPENCLI_STATUS_UNAVAILABLE
from tests.browser_bridge_bundle_fixtures import (
    WTSCLI_BUILD_ID,
    WTSCLI_CAPABILITIES,
    WTSCLI_EXTENSION_ID,
    WTSCLI_FORK_COMMIT,
    WTSCLI_RUNTIME_IDENTITY,
    exact_browser_bridge_requirement,
    write_browser_bridge_bundle,
    write_daemon_ownership,
)


def test_exact_wtscli_bundle_manifest_is_the_only_typed_admission(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle_root)

    requirement = browser_bridge_manifest.load_browser_bridge_requirement(
        bundle_root / "bridge-manifest.json"
    )

    assert requirement.implementation == "seektalent-wtscli"
    assert requirement.fork_commit == WTSCLI_FORK_COMMIT
    assert requirement.bridge_build_id == WTSCLI_BUILD_ID
    assert requirement.runtime_identity.endpoint.host == "127.0.0.1"
    assert requirement.runtime_identity.endpoint.port == 19826
    assert requirement.runtime_identity.transport.request_header == ("X-WTSCLI", "1")
    assert requirement.runtime_identity.transport.response_header == (
        "X-WTSCLI-Bridge",
        "wtscli.browser-bridge.v1",
    )
    assert requirement.runtime_identity.transport.owner_proof_header == "X-WTSCLI-Owner"
    assert requirement.runtime_identity.transport.ownership_header == "X-WTSCLI-Ownership"
    assert requirement.runtime_identity.transport.protocol.name == "wtscli.browser-bridge"
    assert requirement.runtime_identity.extension.id == WTSCLI_EXTENSION_ID
    assert requirement.runtime_identity.state.root_dir == "~/.seektalent/wtscli"
    assert requirement.runtime_identity.state.env_prefix == "WTSCLI_"
    assert requirement.runtime_identity.package.name == "wtscli"
    assert requirement.runtime_identity.package.entrypoint == "wtscli"
    assert requirement.cli.package == requirement.runtime_identity.package.name
    assert requirement.cli.entrypoint == requirement.runtime_identity.package.entrypoint
    assert requirement.extension.id == requirement.runtime_identity.extension.id
    assert requirement.extension.origin == requirement.runtime_identity.extension.origin
    assert requirement.capabilities == frozenset(WTSCLI_CAPABILITIES)


def test_duplicate_manifest_key_is_rejected_even_when_both_values_match(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle_root)
    manifest_path = bundle_root / "bridge-manifest.json"
    raw = manifest_path.read_text(encoding="utf-8")
    raw = raw.replace(
        '"implementation": "seektalent-wtscli",',
        '"implementation": "seektalent-wtscli",\n  "implementation": "seektalent-wtscli",',
        1,
    )
    manifest_path.write_text(raw, encoding="utf-8")

    with pytest.raises(browser_bridge_manifest.BrowserBridgeManifestError) as captured:
        browser_bridge_manifest.load_browser_bridge_requirement(manifest_path)

    assert captured.value.code == "integrity_failed"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest.__setitem__("implementation", "seektalent-opencli"),
        lambda manifest: manifest["runtimeIdentity"]["endpoint"].__setitem__("port", 19825),
        lambda manifest: manifest["runtimeIdentity"]["transport"]["requestHeader"].__setitem__(
            "name", "X-OpenCLI"
        ),
        lambda manifest: manifest["runtimeIdentity"]["package"].__setitem__(
            "name", "@jackwener/opencli"
        ),
        lambda manifest: manifest["runtimeIdentity"]["extension"].__setitem__(
            "origin", "chrome-extension://legacy"
        ),
        lambda manifest: manifest["runtimeIdentity"]["transport"]["protocol"].__setitem__(
            "name", "opencli.browser-bridge"
        ),
        lambda manifest: manifest["cli"].__setitem__("package", "@jackwener/opencli"),
        lambda manifest: manifest["extension"].__setitem__("id", "b" * 32),
        lambda manifest: manifest.__setitem__("forkCommit", "60ae80db9ed96a0813eea12d5e24aa8e5c6ec863"),
        lambda manifest: manifest["cli"].__setitem__("asset", "../legacy-opencli.tgz"),
        lambda manifest: manifest.__setitem__("unexpectedAuthority", "accepted"),
        lambda manifest: manifest.pop("runtimeIdentity"),
        lambda manifest: manifest.__setitem__("runtimeIdentity", []),
        lambda manifest: manifest["cli"].__setitem__("size", True),
        lambda manifest: manifest.__setitem__("capabilities", [*WTSCLI_CAPABILITIES, 1]),
    ],
)
def test_inconsistent_or_legacy_bundle_authority_fails_closed(
    tmp_path: Path,
    mutate,
) -> None:
    bundle_root = tmp_path / "bundle"
    manifest = write_browser_bridge_bundle(bundle_root)
    mutated = copy.deepcopy(manifest)
    mutate(mutated)
    manifest_path = bundle_root / "bridge-manifest.json"
    manifest_path.write_text(json.dumps(mutated), encoding="utf-8")

    with pytest.raises(browser_bridge_manifest.BrowserBridgeManifestError):
        browser_bridge_manifest.load_browser_bridge_requirement(manifest_path)


def test_legacy_bundle_is_rejected_before_install_or_process_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seektalent import browser_bridge_install

    bundle_root = tmp_path / "bundle"
    manifest = write_browser_bridge_bundle(bundle_root)
    manifest["implementation"] = "seektalent-opencli"
    (bundle_root / "bridge-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    install_root = tmp_path / "home" / ".seektalent"
    monkeypatch.setattr(
        browser_bridge_install.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("invalid bundle must not start a process"),
    )
    monkeypatch.setattr(
        browser_bridge_install.os,
        "replace",
        lambda *_args, **_kwargs: pytest.fail("invalid bundle must not replace files"),
    )

    with pytest.raises(browser_bridge_manifest.BrowserBridgeManifestError):
        browser_bridge_install.install_browser_bridge_bundle(
            bundle_dir=bundle_root,
            install_root=install_root,
            node=tmp_path / "missing-node",
        )

    assert not install_root.exists()


def test_bundle_admission_verifies_runtime_and_extension_bytes(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle_root)

    admitted = browser_bridge_manifest.load_browser_bridge_bundle(bundle_root)

    assert admitted.runtime_package.name == "wtscli-0.1.0.tgz"
    assert admitted.extension_dir.name == "extension"
    (admitted.runtime_package).write_bytes(b"tampered")
    with pytest.raises(browser_bridge_manifest.BrowserBridgeManifestError):
        browser_bridge_manifest.load_browser_bridge_bundle(bundle_root)


def test_bundle_admission_derives_and_verifies_exact_extension_id(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle_root, extension_key="Zm9yZWlnbi1leHRlbnNpb24ta2V5")

    with pytest.raises(browser_bridge_manifest.BrowserBridgeManifestError):
        browser_bridge_manifest.load_browser_bridge_bundle(bundle_root)


def test_shared_install_uses_only_wts_paths_and_preserves_legacy_sentinels(tmp_path: Path) -> None:
    from seektalent.browser_bridge_install import install_browser_bridge_bundle

    bundle_root = tmp_path / "bundle"
    install_root = tmp_path / "home" / ".seektalent"
    expected_state_root = install_root / "wtscli"
    write_browser_bridge_bundle(
        bundle_root,
        runtime_main=(
            "import os\n"
            f"assert os.environ['WTSCLI_CONFIG_DIR'] != {str(expected_state_root)!r}\n"
            "assert os.environ['WTSCLI_CACHE_DIR'] == "
            "os.path.join(os.environ['WTSCLI_CONFIG_DIR'], 'cache')\n"
            "print('0.1.0')\n"
        ),
    )
    legacy_state = tmp_path / "home" / ".opencli" / "sentinel"
    legacy_runtime = install_root / "opencli-runtime" / "sentinel"
    legacy_extension = install_root / "chrome-extension" / "opencli" / "sentinel"
    for sentinel in (legacy_state, legacy_runtime, legacy_extension):
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("legacy-untouched", encoding="utf-8")

    result = install_browser_bridge_bundle(
        bundle_dir=bundle_root,
        install_root=install_root,
        node=Path(sys.executable),
    )

    assert result.runtime_dir == install_root / "wtscli-runtime" / "wtscli" / "0.1.0"
    assert result.runtime_main == result.runtime_dir / "node_modules" / "wtscli" / "dist" / "src" / "main.js"
    assert result.extension_dir == install_root / "chrome-extension" / "wtscli"
    assert result.manifest_path == install_root / "browser-bridge" / "bridge-manifest.json"
    assert all(sentinel.read_text(encoding="utf-8") == "legacy-untouched" for sentinel in (
        legacy_state,
        legacy_runtime,
        legacy_extension,
    ))


def test_candidate_npm_and_probes_drop_global_node_injection_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seektalent import browser_bridge_install

    requirement = exact_browser_bridge_requirement()
    captured_envs: list[dict[str, str]] = []

    class Completed:
        returncode = 0

        def __init__(self, stdout: str = "") -> None:
            self.stdout = stdout

    def fake_run(argv: tuple[str, ...], **kwargs: object) -> Completed:
        env = kwargs["env"]
        assert isinstance(env, dict)
        captured_envs.append(dict(env))
        return Completed("0.1.0\n" if argv[-1] == "--version" else "")

    monkeypatch.setenv("NODE_PATH", "/ambient/global-node-modules")
    monkeypatch.setenv("Node_Options", "--require=/ambient/injected.js")
    monkeypatch.setenv("SEEKTALENT_UNRELATED_SENTINEL", "preserved")
    monkeypatch.setattr(
        browser_bridge_install,
        "_npm_cli_for_node",
        lambda _node: tmp_path / "npm-cli.js",
    )
    monkeypatch.setattr(browser_bridge_install.subprocess, "run", fake_run)
    candidate_home = tmp_path / "candidate-home"
    runtime_dir = tmp_path / "stage" / "runtime"

    browser_bridge_install._install_runtime_with_npm(
        runtime_package=tmp_path / "wtscli.tgz",
        runtime_dir=runtime_dir,
        node=tmp_path / "node",
        requirement=requirement,
        state_home=candidate_home,
    )
    browser_bridge_install._probe_wtscli(
        node=tmp_path / "node",
        main=tmp_path / "main.js",
        requirement=requirement,
        state_home=candidate_home,
    )

    assert len(captured_envs) == 3
    for env in captured_envs:
        assert not any(
            key.upper() in {"NODE_PATH", "NODE_OPTIONS"}
            for key in env
        )
        assert env["SEEKTALENT_UNRELATED_SENTINEL"] == "preserved"
        assert env["HOME"] == str(candidate_home)
        assert env["USERPROFILE"] == str(candidate_home)


def test_failed_pair_activation_rolls_back_wts_slot_and_preserves_legacy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seektalent import browser_bridge_install

    first_bundle = tmp_path / "bundle-first"
    second_bundle = tmp_path / "bundle-second"
    write_browser_bridge_bundle(first_bundle, runtime_main='print("0.1.0")\n# first\n')
    write_browser_bridge_bundle(second_bundle, runtime_main='print("0.1.0")\n# second\n')
    install_root = tmp_path / "home" / ".seektalent"
    legacy = tmp_path / "home" / ".opencli" / "sentinel"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy-untouched", encoding="utf-8")
    installed = browser_bridge_install.install_browser_bridge_bundle(
        bundle_dir=first_bundle,
        install_root=install_root,
        node=Path(sys.executable),
    )
    previous_main = installed.runtime_main.read_bytes()
    previous_extension = (installed.extension_dir / "dist" / "background.js").read_bytes()
    previous_manifest = installed.manifest_path.read_bytes()
    real_replace = os.replace
    failed = False

    def fail_extension_activation(source: str | Path, target: str | Path) -> None:
        nonlocal failed
        if not failed and Path(target) == installed.extension_dir and ".stage-" in str(source):
            failed = True
            raise OSError("injected extension activation failure")
        real_replace(source, target)

    monkeypatch.setattr(browser_bridge_install.os, "replace", fail_extension_activation)

    with pytest.raises(OSError, match="injected extension activation failure"):
        browser_bridge_install.install_browser_bridge_bundle(
            bundle_dir=second_bundle,
            install_root=install_root,
            node=Path(sys.executable),
        )

    assert failed is True
    assert installed.runtime_main.read_bytes() == previous_main
    assert (installed.extension_dir / "dist" / "background.js").read_bytes() == previous_extension
    assert installed.manifest_path.read_bytes() == previous_manifest
    assert legacy.read_text(encoding="utf-8") == "legacy-untouched"


def test_staged_help_probe_and_late_failure_leave_real_wts_state_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seektalent import browser_bridge_install

    first_bundle = tmp_path / "bundle-first"
    second_bundle = tmp_path / "bundle-second"
    write_browser_bridge_bundle(first_bundle)
    write_browser_bridge_bundle(
        second_bundle,
        runtime_main=(
            "import os\n"
            "import pathlib\n"
            "import sys\n"
            "if '--help' in sys.argv:\n"
            "    state = pathlib.Path(os.environ['WTSCLI_CONFIG_DIR'])\n"
            "    state.mkdir(parents=True, exist_ok=True)\n"
            "    (state / 'candidate-help-write').write_text('mutated')\n"
            "print('0.1.0' if '--version' in sys.argv else 'help')\n"
        ),
    )
    home = tmp_path / "home"
    install_root = home / ".seektalent"
    installed = browser_bridge_install.install_browser_bridge_bundle(
        bundle_dir=first_bundle,
        install_root=install_root,
        node=Path(sys.executable),
    )
    state_sentinel = install_root / "wtscli" / "sentinel"
    state_sentinel.parent.mkdir(parents=True)
    state_sentinel.write_text("previous-state", encoding="utf-8")
    before = _tree_snapshot(home)
    real_replace = os.replace
    failed = False

    def fail_extension_activation(source: str | Path, target: str | Path) -> None:
        nonlocal failed
        if (
            not failed
            and Path(target) == installed.extension_dir
            and ".stage-" in str(source)
        ):
            failed = True
            raise OSError("injected late activation failure")
        real_replace(source, target)

    monkeypatch.setattr(
        browser_bridge_install.os,
        "replace",
        fail_extension_activation,
    )

    with pytest.raises(OSError, match="injected late activation failure"):
        browser_bridge_install.install_browser_bridge_bundle(
            bundle_dir=second_bundle,
            install_root=install_root,
            node=Path(sys.executable),
        )

    assert failed is True
    assert _tree_snapshot(home) == before


def test_fresh_install_parent_preparation_failure_removes_created_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seektalent.browser_bridge_install import install_browser_bridge_bundle

    bundle_root = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle_root)
    home = tmp_path / "home"
    home.mkdir()
    sentinel = home / "sentinel"
    sentinel.write_text("previous-state", encoding="utf-8")
    install_root = home / ".seektalent"
    fail_at = install_root / "chrome-extension"
    real_mkdir = Path.mkdir

    def fail_midway(
        path: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        if path == fail_at:
            raise OSError("injected parent preparation failure")
        real_mkdir(
            path,
            mode=mode,
            parents=parents,
            exist_ok=exist_ok,
        )

    monkeypatch.setattr(Path, "mkdir", fail_midway)

    with pytest.raises(browser_bridge_manifest.BrowserBridgeManifestError):
        install_browser_bridge_bundle(
            bundle_dir=bundle_root,
            install_root=install_root,
            node=Path(sys.executable),
        )

    assert sentinel.read_text(encoding="utf-8") == "previous-state"
    assert not install_root.exists()
    assert sorted(path.name for path in home.iterdir()) == ["sentinel"]


def test_prepared_runtime_must_contain_the_exact_manifest_declared_wtscli_package(
    tmp_path: Path,
) -> None:
    from seektalent.browser_bridge_install import install_browser_bridge_bundle

    bundle_root = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle_root)
    prepared_install = install_browser_bridge_bundle(
        bundle_dir=bundle_root,
        install_root=tmp_path / "prepared-home" / ".seektalent",
        node=Path(sys.executable),
    )
    prepared_runtime = tmp_path / "prepared-runtime"
    shutil.copytree(prepared_install.runtime_dir, prepared_runtime)
    prepared_main = (
        prepared_runtime
        / "node_modules"
        / "wtscli"
        / "dist"
        / "src"
        / "main.js"
    )
    prepared_main.write_text('print("tampered")\n', encoding="utf-8")
    install_root = tmp_path / "home" / ".seektalent"

    with pytest.raises(browser_bridge_manifest.BrowserBridgeManifestError):
        install_browser_bridge_bundle(
            bundle_dir=bundle_root,
            install_root=install_root,
            node=Path(sys.executable),
            prepared_runtime_dir=prepared_runtime,
        )

    assert not (install_root / "wtscli-runtime" / "wtscli" / "0.1.0").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="creating symlinks is not portable on Windows")
def test_install_rejects_wts_parent_symlinked_into_legacy_state(tmp_path: Path) -> None:
    from seektalent.browser_bridge_install import install_browser_bridge_bundle

    bundle_root = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle_root)
    install_root = tmp_path / "home" / ".seektalent"
    install_root.mkdir(parents=True)
    legacy_root = tmp_path / "home" / ".opencli"
    legacy_root.mkdir()
    sentinel = legacy_root / "sentinel"
    sentinel.write_text("legacy-untouched", encoding="utf-8")
    (install_root / "wtscli-runtime").symlink_to(legacy_root, target_is_directory=True)

    with pytest.raises(browser_bridge_manifest.BrowserBridgeManifestError):
        install_browser_bridge_bundle(
            bundle_dir=bundle_root,
            install_root=install_root,
            node=Path(sys.executable),
        )

    assert sentinel.read_text(encoding="utf-8") == "legacy-untouched"
    assert not (legacy_root / "wtscli").exists()


class _Response:
    status = 200
    will_close = False

    def __init__(self, payload: object, headers: Mapping[str, str]) -> None:
        self._payload = payload
        self._headers = {name.lower(): value for name, value in headers.items()}

    def read(self, _amount: int) -> bytes:
        return json.dumps(self._payload).encode()

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self._headers.get(name.lower(), default)


class _Connection:
    sock = None

    def __init__(self, response: _Response) -> None:
        self.response = response
        self.timeout = 0.0
        self.closed = False
        self.requests: list[tuple[str, str, bytes | None, dict[str, str]]] = []

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.requests.append((method, path, body, dict(headers or {})))

    def getresponse(self) -> _Response:
        return self.response

    def close(self) -> None:
        self.closed = True


def test_daemon_client_uses_manifest_endpoint_markers_and_ownership_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle_root)
    requirement = browser_bridge_manifest.load_browser_bridge_requirement(
        bundle_root / "bridge-manifest.json"
    )
    home = tmp_path / "home"
    _ownership_path, token, token_hash = write_daemon_ownership(home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    status = {
        "ok": True,
        "daemonVersion": "0.1.0",
        "implementation": "seektalent-wtscli",
        "bridgeBuildId": WTSCLI_BUILD_ID,
        "protocolVersion": {"major": 1, "minor": 0},
        "transportProtocol": WTSCLI_RUNTIME_IDENTITY["transport"]["protocol"],
        "ownerTokenHash": token_hash,
        "capabilities": list(WTSCLI_CAPABILITIES),
        "extensionConnected": True,
        "extensionVersion": "0.1.0",
        "extensionImplementation": "seektalent-wtscli",
        "extensionBridgeBuildId": WTSCLI_BUILD_ID,
        "extensionProtocolVersion": {"major": 1, "minor": 0},
        "extensionCapabilities": list(WTSCLI_CAPABILITIES),
        "port": 19826,
    }
    connection = _Connection(
        _Response(
            status,
            {
                "X-WTSCLI-Bridge": "wtscli.browser-bridge.v1",
                "X-WTSCLI-Owner": token_hash,
            },
        )
    )
    factory_calls: list[tuple[str, int, float]] = []

    def factory(host: str, port: int, timeout: float) -> _Connection:
        factory_calls.append((host, port, timeout))
        return connection

    client = OpenCliDaemonClient(requirement=requirement, connection_factory=factory)
    client.verify_bridge()

    assert factory_calls == [("127.0.0.1", 19826, 2.0)]
    request_headers = connection.requests[0][3]
    assert request_headers["X-WTSCLI"] == "1"
    assert request_headers["X-WTSCLI-Ownership"] == token
    assert "X-OpenCLI" not in request_headers


def test_daemon_client_rejects_legacy_or_foreign_response_marker_without_reuse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle_root)
    requirement = browser_bridge_manifest.load_browser_bridge_requirement(
        bundle_root / "bridge-manifest.json"
    )
    home = tmp_path / "home"
    write_daemon_ownership(home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    connection = _Connection(
        _Response(
            {"ok": True, "implementation": "seektalent-opencli"},
            {"X-OpenCLI": "1"},
        )
    )
    client = OpenCliDaemonClient(
        requirement=requirement,
        connection_factory=lambda *_args: connection,
    )

    with pytest.raises(OpenCliBrowserError) as captured:
        client.verify_bridge()

    assert captured.value.safe_reason_code == OPENCLI_STATUS_UNAVAILABLE
    assert connection.closed is True
    assert len(connection.requests) == 1


def test_managed_launcher_derives_wts_package_entrypoint_and_state_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seektalent.browser_bridge_install import install_browser_bridge_bundle

    bundle_root = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle_root)
    install_root = tmp_path / "home" / ".seektalent"
    installed = install_browser_bridge_bundle(
        bundle_dir=bundle_root,
        install_root=install_root,
        node=Path(sys.executable),
    )
    monkeypatch.setattr(opencli_launcher, "_verify_domi_node", lambda _node: None)
    monkeypatch.setattr(opencli_launcher, "_probe_opencli_cli", lambda **_kwargs: None)
    runtime = opencli_launcher.ensure_opencli_runtime(
        root=install_root / "wtscli-runtime",
        env={"SEEKTALENT_DOMI_NODE": sys.executable},
    )

    assert runtime.opencli_main == installed.runtime_main
    assert "/node_modules/wtscli/" in runtime.opencli_main.as_posix()
    assert "@jackwener/opencli" not in runtime.opencli_main.as_posix()
    monkeypatch.setenv("OPENCLI_CONFIG_DIR", "/legacy/opencli")
    monkeypatch.setenv("OPENCLI_DAEMON_PORT", "19825")
    monkeypatch.setenv("WTSCLI_CONFIG_DIR", "/ambient/wtscli")
    env = opencli_launcher.opencli_subprocess_env(
        node_bin_dir=runtime.node_bin_dir,
        requirement=runtime.requirement,
    )
    assert not any(name.startswith("OPENCLI_") for name in env)
    assert env["WTSCLI_CONFIG_DIR"] == str(Path.home() / ".seektalent" / "wtscli")
    assert env["WTSCLI_CACHE_DIR"] == str(Path.home() / ".seektalent" / "wtscli" / "cache")


def test_install_binds_manifest_declared_wts_package_receipt_to_runtime_slot(
    tmp_path: Path,
) -> None:
    from seektalent.browser_bridge_install import install_browser_bridge_bundle

    bundle_root = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle_root)
    installed = install_browser_bridge_bundle(
        bundle_dir=bundle_root,
        install_root=tmp_path / "home" / ".seektalent",
        node=Path(sys.executable),
    )

    assert (installed.runtime_dir / ".seektalent-wtscli-package.tgz").is_file()
    assert (installed.runtime_dir / ".seektalent-wtscli-package-receipt.json").is_file()


def test_runtime_receipt_uses_host_independent_mixed_case_path_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from seektalent.browser_bridge_install import install_browser_bridge_bundle
    from seektalent.browser_bridge_manifest import load_browser_bridge_requirement
    from seektalent.browser_bridge_runtime_receipt import (
        verify_installed_runtime_package,
    )

    bundle_root = tmp_path / "bundle"
    write_browser_bridge_bundle(
        bundle_root,
        runtime_extra_files={
            "LICENSE": "license\n",
            "README.md": "readme\n",
        },
    )
    installed = install_browser_bridge_bundle(
        bundle_dir=bundle_root,
        install_root=tmp_path / "home" / ".seektalent",
        node=Path(sys.executable),
    )
    requirement = load_browser_bridge_requirement(installed.manifest_path)
    real_sorted = builtins.sorted

    def windows_path_sorted(iterable, *, key=None, reverse=False):
        items = list(iterable)
        if key is None and items and all(isinstance(item, Path) for item in items):
            return real_sorted(
                items,
                key=lambda item: str(item).casefold(),
                reverse=reverse,
            )
        return real_sorted(items, key=key, reverse=reverse)

    monkeypatch.setattr(builtins, "sorted", windows_path_sorted)

    verify_installed_runtime_package(
        installed.runtime_dir,
        requirement=requirement,
    )


@pytest.mark.parametrize("mutation", ["same_size_restored_mtime", "extra_file"])
def test_launcher_rejects_post_install_wts_package_tree_tampering_before_spawn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    from seektalent.browser_bridge_install import install_browser_bridge_bundle

    bundle_root = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle_root)
    install_root = tmp_path / "home" / ".seektalent"
    installed = install_browser_bridge_bundle(
        bundle_dir=bundle_root,
        install_root=install_root,
        node=Path(sys.executable),
    )
    monkeypatch.setattr(opencli_launcher, "_verify_domi_node", lambda _node: None)
    monkeypatch.setattr(opencli_launcher, "_probe_opencli_cli", lambda **_kwargs: None)
    opencli_launcher.ensure_opencli_runtime(
        root=install_root / "wtscli-runtime",
        env={"SEEKTALENT_DOMI_NODE": sys.executable},
    )

    package_dir = installed.runtime_dir / "node_modules" / "wtscli"
    if mutation == "same_size_restored_mtime":
        original_stat = installed.runtime_main.stat()
        original = installed.runtime_main.read_bytes()
        tampered = original.replace(b"0.1.0", b"9.9.9", 1)
        assert tampered != original
        assert len(tampered) == len(original)
        installed.runtime_main.write_bytes(tampered)
        os.utime(
            installed.runtime_main,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
    else:
        (package_dir / "unexpected-runtime-file.js").write_text(
            "unexpected\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        opencli_launcher.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail(
            "tampered WTS package bytes must be rejected before spawn"
        ),
    )

    with pytest.raises(
        opencli_launcher.BootstrapError,
        match="opencli_bridge_integrity_failed",
    ):
        opencli_launcher.ensure_opencli_runtime(
            root=install_root / "wtscli-runtime",
            env={"SEEKTALENT_DOMI_NODE": sys.executable},
        )


def test_all_delivery_entrypoints_bind_the_exact_merged_wtscli_bundle() -> None:
    root = Path(__file__).resolve().parents[1]
    staging = (root / "scripts" / "install-seektalent-staging.sh").read_text(encoding="utf-8")
    posix = (root / "scripts" / "install-seektalent-domi.sh").read_text(encoding="utf-8")
    powershell = (root / "scripts" / "install-seektalent-domi.ps1").read_text(encoding="utf-8")
    offline = (root / "scripts" / "offline" / "install-offline-macos-intel.sh").read_text(
        encoding="utf-8"
    )
    workflow = (root / ".github" / "workflows" / "build-macos-intel-offline.yml").read_text(
        encoding="utf-8"
    )
    native_workflow = (
        root / ".github" / "workflows" / "native-launch-binding-probe.yml"
    ).read_text(encoding="utf-8")

    assert WTSCLI_FORK_COMMIT in staging
    assert WTSCLI_FORK_COMMIT in workflow
    assert "60ae80db9ed96a0813eea12d5e24aa8e5c6ec863" not in staging + workflow
    assert "SEEKTALENT_WTSCLI_BUNDLE_DIR" in posix
    assert "SEEKTALENT_WTSCLI_BUNDLE_DIR" in powershell
    assert "--browser-bridge-bundle-dir" in posix
    assert "--browser-bridge-bundle-dir" in powershell
    assert "--browser-bridge-bundle-dir" in offline
    assert "--browser-bridge-prepared-runtime-dir" in offline
    assert "browser_bridge_runtime_sha256" in offline
    assert "opencli-runtime/opencli" not in offline
    assert "@jackwener/opencli" not in offline
    assert "repository: FrankQDWang/wtscli" in native_workflow
    assert f"ref: {WTSCLI_FORK_COMMIT}" in native_workflow
    assert "build-exact-wtscli-bundle:" in native_workflow
    assert "needs: build-exact-wtscli-bundle" in native_workflow
    assert "actions/download-artifact@v4" in native_workflow
    assert "name: exact-wtscli-browser-bridge" in native_workflow
    assert native_workflow.count("repository: FrankQDWang/wtscli") == 1
    assert native_workflow.count("npm run build:seektalent-bundle") == 1
    assert "npm run build:seektalent-bundle" in native_workflow
    assert "SEEKTALENT_EXACT_WTSCLI_BUNDLE" in native_workflow


def test_native_ci_installs_the_real_exact_wtscli_bundle_when_supplied(
    tmp_path: Path,
) -> None:
    from seektalent.browser_bridge_install import install_browser_bridge_bundle

    raw_bundle = os.environ.get("SEEKTALENT_EXACT_WTSCLI_BUNDLE")
    if not raw_bundle:
        pytest.skip("exact WTSCLI source bundle is built only by the native CI matrix")
    node = shutil.which("node")
    assert node is not None
    legacy_state = tmp_path / "home" / ".opencli" / "sentinel"
    legacy_runtime = tmp_path / "home" / ".seektalent" / "opencli-runtime" / "sentinel"
    legacy_extension = (
        tmp_path / "home" / ".seektalent" / "chrome-extension" / "opencli" / "sentinel"
    )
    for sentinel in (legacy_state, legacy_runtime, legacy_extension):
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("legacy-untouched", encoding="utf-8")

    installed = install_browser_bridge_bundle(
        bundle_dir=Path(raw_bundle),
        install_root=tmp_path / "home" / ".seektalent",
        node=Path(node),
    )

    assert installed.runtime_main.is_file()
    assert installed.extension_dir.is_dir()
    assert installed.bridge_build_id == WTSCLI_BUILD_ID
    assert (
        installed.runtime_dir / "node_modules" / "ws" / "package.json"
    ).is_file()
    completed = subprocess.run(
        (str(node), str(installed.runtime_main), "--help"),
        env={
            **os.environ,
            "WTSCLI_CONFIG_DIR": str(tmp_path / "home" / ".seektalent" / "wtscli"),
            "WTSCLI_CACHE_DIR": str(
                tmp_path / "home" / ".seektalent" / "wtscli" / "cache"
            ),
        },
        check=False,
        capture_output=True,
        encoding="utf-8",
        timeout=20,
    )
    assert completed.returncode == 0
    assert "Usage: wtscli" in completed.stdout
    assert all(
        sentinel.read_text(encoding="utf-8") == "legacy-untouched"
        for sentinel in (legacy_state, legacy_runtime, legacy_extension)
    )


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, bytes], ...]:
    entries: list[tuple[str, str, bytes]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append((relative, "symlink", os.readlink(path).encode()))
        elif path.is_dir():
            entries.append((relative, "dir", b""))
        else:
            entries.append((relative, "file", path.read_bytes()))
    return tuple(entries)
