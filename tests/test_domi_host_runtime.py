from __future__ import annotations

import hashlib
import json
from pathlib import Path

import seektalent.domi_host_runtime as domi_host_runtime
from seektalent.domi_host_runtime import (
    HostRuntimeIdentity,
    validate_delivery_runtime_contract,
    validate_installed_runtime_identity,
)
from seektalent.installed_domi_release import validate_installed_release


def _identity(**overrides: object) -> HostRuntimeIdentity:
    values: dict[str, object] = {
        "platform": "macos-arm64",
        "os_family": "macos",
        "architecture": "arm64",
        "python_executable": "/Applications/Domi.app/python/bin/python",
        "python_version": "3.13.13",
        "python_implementation": "cpython",
        "python_cache_tag": "cpython-313",
        "python_soabi": "cpython-313-darwin",
        "python_executable_sha256": "a" * 64,
        "node_executable": "/Applications/Domi.app/node/bin/node",
        "node_version": "22.14.0",
        "node_executable_sha256": "b" * 64,
    }
    values.update(overrides)
    return HostRuntimeIdentity(**values)


def _contract() -> dict[str, object]:
    return {
        "platform": "macos-arm64",
        "os_family": "macos",
        "architecture": "arm64",
        "python_implementation": "cpython",
        "python_major_minor": "3.13",
        "python_cache_tag": "cpython-313",
        "python_soabi": "cpython-313-darwin",
        "node_version": "22.14.0",
    }


def test_delivery_runtime_contract_rejects_wrong_python_and_node() -> None:
    assert validate_delivery_runtime_contract(_identity(), _contract()) is None
    for identity in (
        _identity(python_version="3.12.11", python_cache_tag="cpython-312"),
        _identity(python_version="3.14.4", python_cache_tag="cpython-314"),
        _identity(python_soabi="cpython-313-wrong-platform"),
    ):
        assert (
            validate_delivery_runtime_contract(identity, _contract())
            == "domi_python_contract_mismatch"
        )
    assert (
        validate_delivery_runtime_contract(
            _identity(node_version="24.16.0"),
            _contract(),
        )
        == "domi_node_contract_mismatch"
    )


def test_installed_runtime_identity_requires_exact_path_soabi_and_binary() -> None:
    identity = _identity()
    receipt = identity.to_receipt_payload()
    receipt["schemaVersion"] = "seektalent.install-receipt.v2"
    assert validate_installed_runtime_identity(identity, receipt) is None

    for field, value in (
        ("pythonExecutable", "/different/python"),
        ("pythonSoabi", "cpython-314-darwin"),
        ("pythonExecutableSha256", "c" * 64),
        ("nodeExecutable", "/different/node"),
        ("nodeExecutableSha256", "d" * 64),
    ):
        changed = dict(receipt)
        changed[field] = value
        assert (
            validate_installed_runtime_identity(identity, changed)
            == "domi_host_runtime_changed_reinstall_required"
        )

    invalid = dict(receipt)
    invalid["schemaVersion"] = "seektalent.install-receipt.unknown"
    assert validate_installed_runtime_identity(identity, invalid) == (
        "seektalent_receipt_invalid"
    )


def test_acceptance_fixture_is_versioned_json_without_credentials() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "acceptance" / "fixtures" / "liepin-ai-agent-engineer-v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schemaVersion"] == "seektalent.acceptance-fixture.v1"
    assert payload["jobTitle"] == "AI Agent 工程师"
    assert len(payload["jd"]) > 500
    assert "JWT" not in path.read_text(encoding="utf-8")
    assert len(hashlib.sha256(path.read_bytes()).hexdigest()) == 64


def test_v1_install_receipt_requires_reinstall_upgrade(tmp_path: Path) -> None:
    root = tmp_path / ".seektalent"
    root.mkdir()
    (root / "install-receipt.json").write_text(
        json.dumps({"schemaVersion": "seektalent.install-receipt.v1"}),
        encoding="utf-8",
    )

    result = validate_installed_release(tmp_path)

    assert not result.ok
    assert result.reason_code == "install_receipt_upgrade_required"


def test_dev_product_runtime_requires_domi_node_without_local_fallback() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "start-dev-workbench.sh"
    ).read_text(encoding="utf-8")

    assert 'domi_node="${DOMI_NODE:-${SEEKTALENT_DOMI_NODE:-}}"' in script
    assert "Domi Node 22.14.0 is required" in script
    assert 'SEEKTALENT_WTSCLI_NODE="$(command -v node' not in script


def test_windows_runtime_probe_keeps_and_normalizes_host_architecture(
    monkeypatch,
) -> None:
    monkeypatch.setattr(domi_host_runtime.platform_module, "system", lambda: "Windows")
    monkeypatch.setattr(domi_host_runtime.platform_module, "machine", lambda: "")
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "AMD64")
    monkeypatch.delenv("PROCESSOR_ARCHITEW6432", raising=False)

    assert domi_host_runtime._host_platform() == (
        "windows",
        "x64",
        "windows-x64",
    )
    assert domi_host_runtime._sanitized_environment()["PROCESSOR_ARCHITECTURE"] == "AMD64"
