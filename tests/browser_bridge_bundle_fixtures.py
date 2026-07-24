from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


WTSCLI_VERSION = "0.1.0"
WTSCLI_FORK_COMMIT = "709622fc3fb3463f15551467fdf0d28571dfd049"
WTSCLI_UPSTREAM_COMMIT = "cad35e7a6a5ff3f7d6b859bfa4c45195c0390260"
WTSCLI_BUILD_ID = f"seektalent-wtscli-{WTSCLI_VERSION}+{WTSCLI_FORK_COMMIT[:12]}"
WTSCLI_EXTENSION_ID = "aijmoehobdolindhgdljiaiimngpghcn"
WTSCLI_EXTENSION_KEY = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA2/7FY2yb1SRnotnd9BHxZr7neCELlxfCQHa32Atp"
    "A1kTrSxjDxAYtNGE7h4hm/IxWyI18WqVCtdNAdb1ENkH98Ah+IwXW8nQFdc4ZriV4dJ05EruQ5uW76zaoj"
    "gjgo18Dr3EaDt9aTG0sX5vVgq77+2bpqfUxiIdQeRiFnPL1b0Ctw++CbAQJMaXAU8FGs83EhcFI1Xw/bSNU"
    "XIGtSQlO4hUYTQXoDigy+coykLD3jPxynoHT2JK14MIkJtPHDxCxYS1hkNJ/fY14kXT5KCI3K1paxayrqgO"
    "+Kr3DtDxEc6sd8Jn+ap1xYEKBo2D9/jqHUwr/Jjd2EO7Q+d5EDe5/QIDAQAB"
)
WTSCLI_CAPABILITIES = (
    "browser.operation-deadline.v1",
    "browser.operations.v1",
    "control-fence.v1",
    "tab.close-verified.v1",
    "tab.create-in-existing-window.v1",
    "tab.find.v1",
    "tab.idle-deadline.v1",
)
WTSCLI_RUNTIME_IDENTITY: dict[str, Any] = {
    "endpoint": {"host": "127.0.0.1", "port": 19826},
    "transport": {
        "requestHeader": {"name": "X-WTSCLI", "value": "1"},
        "responseHeader": {
            "name": "X-WTSCLI-Bridge",
            "value": "wtscli.browser-bridge.v1",
        },
        "ownerProofHeader": {"name": "X-WTSCLI-Owner"},
        "ownershipHeader": {"name": "X-WTSCLI-Ownership"},
        "protocol": {
            "name": "wtscli.browser-bridge",
            "version": {"major": 1, "minor": 0},
        },
    },
    "extension": {
        "id": WTSCLI_EXTENSION_ID,
        "origin": f"chrome-extension://{WTSCLI_EXTENSION_ID}",
    },
    "state": {
        "rootDir": "~/.seektalent/wtscli",
        "envPrefix": "WTSCLI_",
        "configDirEnv": "WTSCLI_CONFIG_DIR",
        "cacheDirEnv": "WTSCLI_CACHE_DIR",
        "ownershipFile": "daemon/ownership.json",
    },
    "package": {"name": "wtscli", "entrypoint": "wtscli"},
}


def exact_browser_bridge_requirement():
    from seektalent.browser_bridge_manifest import parse_browser_bridge_requirement

    manifest = {
        "schemaVersion": "seektalent.browser_bridge_bundle.v1",
        "implementation": "seektalent-wtscli",
        "runtimeIdentity": WTSCLI_RUNTIME_IDENTITY,
        "upstreamBase": {
            "tag": "v1.8.6",
            "commit": WTSCLI_UPSTREAM_COMMIT,
        },
        "forkCommit": WTSCLI_FORK_COMMIT,
        "bridgeBuildId": WTSCLI_BUILD_ID,
        "protocolVersion": {"major": 1, "minor": 0},
        "capabilities": list(WTSCLI_CAPABILITIES),
        "cli": {
            "package": "wtscli",
            "entrypoint": "wtscli",
            "version": WTSCLI_VERSION,
            "asset": f"runtime/wtscli-{WTSCLI_VERSION}.tgz",
            "size": 1,
            "sha256": "0" * 64,
        },
        "extension": {
            "version": WTSCLI_VERSION,
            "id": WTSCLI_EXTENSION_ID,
            "origin": f"chrome-extension://{WTSCLI_EXTENSION_ID}",
            "directory": "extension",
            "treeSha256": "0" * 64,
            "manifestSha256": "0" * 64,
            "files": [
                {
                    "path": "manifest.json",
                    "size": 1,
                    "sha256": "0" * 64,
                }
            ],
        },
    }
    return parse_browser_bridge_requirement(json.dumps(manifest).encode())


def write_browser_bridge_bundle(
    root: Path,
    *,
    runtime_main: str = 'print("0.1.0")\n',
    extension_key: str = WTSCLI_EXTENSION_KEY,
    runtime_extra_files: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    root.mkdir(parents=True)
    runtime_dir = root / "runtime"
    extension_dir = root / "extension"
    package_source = root / ".package-source"
    package_dir = package_source / "package"
    main = package_dir / "dist" / "src" / "main.js"
    runtime_dir.mkdir()
    main.parent.mkdir(parents=True)
    main.write_text(runtime_main, encoding="utf-8")
    main.chmod(0o755)

    package_json = {
        "name": "wtscli",
        "version": WTSCLI_VERSION,
        "bin": {"wtscli": "dist/src/main.js"},
    }
    (package_dir / "package.json").write_text(
        json.dumps(package_json, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    bridge_identity = {
        "schemaVersion": "wtscli.bridge_identity.v1",
        "implementation": "seektalent-wtscli",
        "bridgeBuildId": WTSCLI_BUILD_ID,
        "runtimeIdentity": WTSCLI_RUNTIME_IDENTITY,
        "protocolVersion": {"major": 1, "minor": 0},
        "capabilities": list(WTSCLI_CAPABILITIES),
    }
    (package_dir / "bridge-identity.json").write_text(
        json.dumps(bridge_identity, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    for relative_path, content in (runtime_extra_files or {}).items():
        extra_file = package_dir / relative_path
        extra_file.parent.mkdir(parents=True, exist_ok=True)
        extra_file.write_text(content, encoding="utf-8")

    runtime_package = runtime_dir / f"wtscli-{WTSCLI_VERSION}.tgz"
    with tarfile.open(runtime_package, "w:gz") as archive:
        archive.add(package_dir, arcname="package")
    shutil.rmtree(package_source)

    (extension_dir / "dist").mkdir(parents=True)
    (extension_dir / "dist" / "background.js").write_text("bridge\n", encoding="utf-8")
    extension_manifest = {
        "manifest_version": 3,
        "name": "WTSCLI",
        "version": WTSCLI_VERSION,
        "key": extension_key,
        "background": {"service_worker": "dist/background.js", "type": "module"},
    }
    (extension_dir / "manifest.json").write_text(
        json.dumps(extension_manifest, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    extension_tree_sha256, extension_files = extension_tree(extension_dir)

    manifest: dict[str, Any] = {
        "schemaVersion": "seektalent.browser_bridge_bundle.v1",
        "implementation": "seektalent-wtscli",
        "runtimeIdentity": WTSCLI_RUNTIME_IDENTITY,
        "upstreamBase": {
            "tag": "v1.8.6",
            "commit": WTSCLI_UPSTREAM_COMMIT,
        },
        "forkCommit": WTSCLI_FORK_COMMIT,
        "bridgeBuildId": WTSCLI_BUILD_ID,
        "protocolVersion": {"major": 1, "minor": 0},
        "capabilities": list(WTSCLI_CAPABILITIES),
        "cli": {
            "package": "wtscli",
            "entrypoint": "wtscli",
            "version": WTSCLI_VERSION,
            "asset": f"runtime/wtscli-{WTSCLI_VERSION}.tgz",
            "size": runtime_package.stat().st_size,
            "sha256": sha256(runtime_package),
        },
        "extension": {
            "version": WTSCLI_VERSION,
            "id": WTSCLI_EXTENSION_ID,
            "origin": f"chrome-extension://{WTSCLI_EXTENSION_ID}",
            "directory": "extension",
            "treeSha256": extension_tree_sha256,
            "manifestSha256": sha256(extension_dir / "manifest.json"),
            "files": extension_files,
        },
    }
    (root / "bridge-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def extension_tree(extension_dir: Path) -> tuple[str, list[dict[str, object]]]:
    files: list[dict[str, object]] = []
    for file_path in sorted(extension_dir.rglob("*")):
        if file_path.is_file():
            files.append(
                {
                    "path": file_path.relative_to(extension_dir).as_posix(),
                    "size": file_path.stat().st_size,
                    "sha256": sha256(file_path),
                }
            )
    tree_text = "".join(f"{item['sha256']}  {item['path']}\n" for item in files)
    return hashlib.sha256(tree_text.encode()).hexdigest(), files


def sha256(file_path: Path) -> str:
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def write_daemon_ownership(home: Path, *, token: str = "ab" * 32) -> tuple[Path, str, str]:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    ownership_path = home / ".seektalent" / "wtscli" / "daemon" / "ownership.json"
    ownership_path.parent.mkdir(parents=True)
    ownership_path.write_text(
        json.dumps(
            {
                "schemaVersion": "wtscli.daemon_ownership.v1",
                "endpoint": {"host": "127.0.0.1", "port": 19826},
                "token": token,
                "tokenHash": token_hash,
                "pid": 12345,
                "createdAt": "2026-07-24T00:00:00.000Z",
            }
        ),
        encoding="utf-8",
    )
    return ownership_path, token, token_hash
