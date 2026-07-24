from __future__ import annotations

import base64
import binascii
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, NoReturn

from seektalent.strict_json import StrictJsonError, strict_json_object_loads


BROWSER_BRIDGE_MANIFEST_SCHEMA = "seektalent.browser_bridge_bundle.v1"
BROWSER_BRIDGE_IDENTITY_SCHEMA = "wtscli.bridge_identity.v1"
BROWSER_BRIDGE_IMPLEMENTATION = "seektalent-wtscli"
BROWSER_BRIDGE_PROTOCOL_NAME = "wtscli.browser-bridge"
BROWSER_BRIDGE_PROTOCOL_MAJOR = 1
BROWSER_BRIDGE_PROTOCOL_MINOR = 0
WTSCLI_VERSION = "0.1.0"
WTSCLI_FORK_COMMIT = "709622fc3fb3463f15551467fdf0d28571dfd049"
WTSCLI_UPSTREAM_TAG = "v1.8.6"
WTSCLI_UPSTREAM_COMMIT = "cad35e7a6a5ff3f7d6b859bfa4c45195c0390260"
WTSCLI_BUILD_ID = f"seektalent-wtscli-{WTSCLI_VERSION}+{WTSCLI_FORK_COMMIT[:12]}"
WTSCLI_EXTENSION_ID = "aijmoehobdolindhgdljiaiimngpghcn"
WTSCLI_EXTENSION_ORIGIN = f"chrome-extension://{WTSCLI_EXTENSION_ID}"
WTSCLI_PACKAGE = "wtscli"
WTSCLI_ENTRYPOINT = "wtscli"
WTSCLI_STATE_ROOT = "~/.seektalent/wtscli"
WTSCLI_ENV_PREFIX = "WTSCLI_"
WTSCLI_CONFIG_DIR_ENV = "WTSCLI_CONFIG_DIR"
WTSCLI_CACHE_DIR_ENV = "WTSCLI_CACHE_DIR"
WTSCLI_OWNERSHIP_FILE = "daemon/ownership.json"
WTSCLI_REQUEST_HEADER = ("X-WTSCLI", "1")
WTSCLI_RESPONSE_HEADER = ("X-WTSCLI-Bridge", "wtscli.browser-bridge.v1")
WTSCLI_OWNER_PROOF_HEADER = "X-WTSCLI-Owner"
WTSCLI_OWNERSHIP_HEADER = "X-WTSCLI-Ownership"
MAX_SAFE_INTEGER = (1 << 53) - 1
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
EXTENSION_ID_RE = re.compile(r"[a-p]{32}\Z")
REQUIRED_BROWSER_BRIDGE_CAPABILITIES = frozenset(
    {
        "browser.operation-deadline.v1",
        "browser.operations.v1",
        "control-fence.v1",
        "tab.close-verified.v1",
        "tab.create-in-existing-window.v1",
        "tab.find.v1",
        "tab.idle-deadline.v1",
    }
)
_CANONICAL_CAPABILITIES = tuple(sorted(REQUIRED_BROWSER_BRIDGE_CAPABILITIES))


BrowserBridgeManifestErrorCode = Literal[
    "integrity_failed",
    "wrong_implementation",
    "protocol_mismatch",
    "capability_missing",
]


class BrowserBridgeManifestError(RuntimeError):
    def __init__(self, code: BrowserBridgeManifestErrorCode) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class BrowserBridgeProtocolVersion:
    major: int
    minor: int


@dataclass(frozen=True, slots=True)
class BrowserBridgeEndpoint:
    host: str
    port: int


@dataclass(frozen=True, slots=True)
class BrowserBridgeTransportProtocol:
    name: str
    version: BrowserBridgeProtocolVersion


@dataclass(frozen=True, slots=True)
class BrowserBridgeTransportIdentity:
    request_header: tuple[str, str]
    response_header: tuple[str, str]
    owner_proof_header: str
    ownership_header: str
    protocol: BrowserBridgeTransportProtocol


@dataclass(frozen=True, slots=True)
class BrowserBridgeExtensionIdentity:
    id: str
    origin: str


@dataclass(frozen=True, slots=True)
class BrowserBridgeStateIdentity:
    root_dir: str
    env_prefix: str
    config_dir_env: str
    cache_dir_env: str
    ownership_file: str

    def resolve_root(self, *, home: Path | None = None) -> Path:
        root_home = (home or Path.home()).expanduser()
        if self.root_dir == "~":
            return root_home
        if self.root_dir.startswith("~/"):
            return root_home / self.root_dir[2:]
        return Path(self.root_dir)

    def ownership_path(self, *, home: Path | None = None) -> Path:
        return self.resolve_root(home=home).joinpath(*self.ownership_file.split("/"))


@dataclass(frozen=True, slots=True)
class BrowserBridgePackageIdentity:
    name: str
    entrypoint: str


@dataclass(frozen=True, slots=True)
class BrowserBridgeRuntimeIdentity:
    endpoint: BrowserBridgeEndpoint
    transport: BrowserBridgeTransportIdentity
    extension: BrowserBridgeExtensionIdentity
    state: BrowserBridgeStateIdentity
    package: BrowserBridgePackageIdentity


@dataclass(frozen=True, slots=True)
class BrowserBridgeCliAsset:
    package: str
    entrypoint: str
    version: str
    asset: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class BrowserBridgeExtensionFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class BrowserBridgeExtensionAsset:
    version: str
    id: str
    origin: str
    directory: str
    tree_sha256: str
    manifest_sha256: str
    files: tuple[BrowserBridgeExtensionFile, ...]


@dataclass(frozen=True, slots=True)
class BrowserBridgeRequirement:
    implementation: str
    runtime_identity: BrowserBridgeRuntimeIdentity
    upstream_tag: str
    upstream_commit: str
    fork_commit: str
    bridge_build_id: str
    protocol_version: BrowserBridgeProtocolVersion
    capabilities: frozenset[str]
    cli: BrowserBridgeCliAsset
    extension: BrowserBridgeExtensionAsset

    @property
    def protocol_major(self) -> int:
        return self.protocol_version.major

    @property
    def protocol_minor(self) -> int:
        return self.protocol_version.minor


@dataclass(frozen=True, slots=True)
class BrowserBridgeRuntimePackageIdentity:
    implementation: str
    bridge_build_id: str
    runtime_identity: BrowserBridgeRuntimeIdentity
    protocol_version: BrowserBridgeProtocolVersion
    capabilities: frozenset[str]


@dataclass(frozen=True, slots=True)
class BrowserBridgeBundle:
    root: Path
    manifest_path: Path
    requirement: BrowserBridgeRequirement
    runtime_package: Path
    extension_dir: Path

    @property
    def bridge_build_id(self) -> str:
        return self.requirement.bridge_build_id

    @property
    def extension_version(self) -> str:
        return self.requirement.extension.version

    @property
    def fork_commit(self) -> str:
        return self.requirement.fork_commit


def load_browser_bridge_requirement(path: Path) -> BrowserBridgeRequirement:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BrowserBridgeManifestError("integrity_failed") from exc
    return parse_browser_bridge_requirement(raw)


def parse_browser_bridge_requirement(raw: bytes) -> BrowserBridgeRequirement:
    payload = _strict_object(raw)
    _require_fields(
        payload,
        {
            "schemaVersion",
            "implementation",
            "runtimeIdentity",
            "upstreamBase",
            "forkCommit",
            "bridgeBuildId",
            "protocolVersion",
            "capabilities",
            "cli",
            "extension",
        },
    )
    if payload["schemaVersion"] != BROWSER_BRIDGE_MANIFEST_SCHEMA:
        _fail()
    implementation = _required_string(payload["implementation"])
    if implementation != BROWSER_BRIDGE_IMPLEMENTATION:
        _fail("wrong_implementation")

    runtime_identity = _parse_runtime_identity(payload["runtimeIdentity"])
    upstream = _required_mapping(payload["upstreamBase"], {"tag", "commit"})
    upstream_tag = _required_string(upstream["tag"])
    upstream_commit = _git_sha(upstream["commit"])
    if upstream_tag != WTSCLI_UPSTREAM_TAG or upstream_commit != WTSCLI_UPSTREAM_COMMIT:
        _fail()

    fork_commit = _git_sha(payload["forkCommit"])
    bridge_build_id = _required_string(payload["bridgeBuildId"])
    if fork_commit != WTSCLI_FORK_COMMIT or bridge_build_id != WTSCLI_BUILD_ID:
        _fail()

    protocol_version = _parse_protocol_version(payload["protocolVersion"])
    if protocol_version != BrowserBridgeProtocolVersion(
        BROWSER_BRIDGE_PROTOCOL_MAJOR,
        BROWSER_BRIDGE_PROTOCOL_MINOR,
    ):
        _fail("protocol_mismatch")
    if runtime_identity.transport.protocol.version != protocol_version:
        _fail("protocol_mismatch")

    capabilities = _parse_capabilities(payload["capabilities"])
    cli = _parse_cli(payload["cli"])
    extension = _parse_extension(payload["extension"])
    if cli.package != runtime_identity.package.name or cli.entrypoint != runtime_identity.package.entrypoint:
        _fail()
    if extension.id != runtime_identity.extension.id or extension.origin != runtime_identity.extension.origin:
        _fail()

    return BrowserBridgeRequirement(
        implementation=implementation,
        runtime_identity=runtime_identity,
        upstream_tag=upstream_tag,
        upstream_commit=upstream_commit,
        fork_commit=fork_commit,
        bridge_build_id=bridge_build_id,
        protocol_version=protocol_version,
        capabilities=capabilities,
        cli=cli,
        extension=extension,
    )


def load_runtime_package_identity(path: Path) -> BrowserBridgeRuntimePackageIdentity:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BrowserBridgeManifestError("integrity_failed") from exc
    payload = _strict_object(raw)
    _require_fields(
        payload,
        {
            "schemaVersion",
            "implementation",
            "bridgeBuildId",
            "runtimeIdentity",
            "protocolVersion",
            "capabilities",
        },
    )
    if payload["schemaVersion"] != BROWSER_BRIDGE_IDENTITY_SCHEMA:
        _fail()
    implementation = _required_string(payload["implementation"])
    if implementation != BROWSER_BRIDGE_IMPLEMENTATION:
        _fail("wrong_implementation")
    bridge_build_id = _required_string(payload["bridgeBuildId"])
    if bridge_build_id != WTSCLI_BUILD_ID:
        _fail()
    runtime_identity = _parse_runtime_identity(payload["runtimeIdentity"])
    protocol_version = _parse_protocol_version(payload["protocolVersion"])
    if protocol_version != runtime_identity.transport.protocol.version:
        _fail("protocol_mismatch")
    return BrowserBridgeRuntimePackageIdentity(
        implementation=implementation,
        bridge_build_id=bridge_build_id,
        runtime_identity=runtime_identity,
        protocol_version=protocol_version,
        capabilities=_parse_capabilities(payload["capabilities"]),
    )


def load_browser_bridge_bundle(root: Path) -> BrowserBridgeBundle:
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise BrowserBridgeManifestError("integrity_failed") from exc
    manifest_path = resolved_root / "bridge-manifest.json"
    requirement = load_browser_bridge_requirement(manifest_path)
    runtime_package = _bundle_path(resolved_root, requirement.cli.asset)
    extension_dir = _bundle_path(resolved_root, requirement.extension.directory)
    try:
        if (
            runtime_package.is_symlink()
            or not runtime_package.is_file()
            or runtime_package.stat().st_size != requirement.cli.size
            or _sha256(runtime_package) != requirement.cli.sha256
        ):
            _fail()
        if extension_dir.is_symlink() or not extension_dir.is_dir():
            _fail()
        actual_files = _extension_files(extension_dir)
        if actual_files != requirement.extension.files:
            _fail()
        tree_text = "".join(f"{item.sha256}  {item.path}\n" for item in actual_files)
        if hashlib.sha256(tree_text.encode()).hexdigest() != requirement.extension.tree_sha256:
            _fail()
        extension_manifest_path = extension_dir / "manifest.json"
        if (
            extension_manifest_path.is_symlink()
            or _sha256(extension_manifest_path) != requirement.extension.manifest_sha256
        ):
            _fail()
        extension_manifest = strict_json_object_loads(extension_manifest_path.read_bytes())
        if (
            extension_manifest.get("version") != requirement.extension.version
            or _extension_id(extension_manifest.get("key")) != requirement.extension.id
        ):
            _fail()
    except (OSError, StrictJsonError):
        _fail()
    return BrowserBridgeBundle(
        root=resolved_root,
        manifest_path=manifest_path,
        requirement=requirement,
        runtime_package=runtime_package,
        extension_dir=extension_dir,
    )


def _parse_runtime_identity(value: object) -> BrowserBridgeRuntimeIdentity:
    payload = _required_mapping(value, {"endpoint", "transport", "extension", "state", "package"})
    endpoint_payload = _required_mapping(payload["endpoint"], {"host", "port"})
    endpoint = BrowserBridgeEndpoint(
        host=_required_string(endpoint_payload["host"]),
        port=_safe_int(endpoint_payload["port"], positive=True),
    )
    if endpoint != BrowserBridgeEndpoint("127.0.0.1", 19826):
        _fail()

    transport_payload = _required_mapping(
        payload["transport"],
        {"requestHeader", "responseHeader", "ownerProofHeader", "ownershipHeader", "protocol"},
    )
    request_header = _header(transport_payload["requestHeader"], with_value=True)
    response_header = _header(transport_payload["responseHeader"], with_value=True)
    owner_proof_header = _header(transport_payload["ownerProofHeader"], with_value=False)[0]
    ownership_header = _header(transport_payload["ownershipHeader"], with_value=False)[0]
    protocol_payload = _required_mapping(transport_payload["protocol"], {"name", "version"})
    transport_protocol = BrowserBridgeTransportProtocol(
        name=_required_string(protocol_payload["name"]),
        version=_parse_protocol_version(protocol_payload["version"]),
    )
    transport = BrowserBridgeTransportIdentity(
        request_header=(request_header[0], request_header[1]),
        response_header=(response_header[0], response_header[1]),
        owner_proof_header=owner_proof_header,
        ownership_header=ownership_header,
        protocol=transport_protocol,
    )
    if (
        transport.request_header != WTSCLI_REQUEST_HEADER
        or transport.response_header != WTSCLI_RESPONSE_HEADER
        or transport.owner_proof_header != WTSCLI_OWNER_PROOF_HEADER
        or transport.ownership_header != WTSCLI_OWNERSHIP_HEADER
        or transport.protocol
        != BrowserBridgeTransportProtocol(
            BROWSER_BRIDGE_PROTOCOL_NAME,
            BrowserBridgeProtocolVersion(
                BROWSER_BRIDGE_PROTOCOL_MAJOR,
                BROWSER_BRIDGE_PROTOCOL_MINOR,
            ),
        )
    ):
        _fail("protocol_mismatch")

    extension_payload = _required_mapping(payload["extension"], {"id", "origin"})
    extension = BrowserBridgeExtensionIdentity(
        id=_extension_id_value(extension_payload["id"]),
        origin=_required_string(extension_payload["origin"]),
    )
    if extension != BrowserBridgeExtensionIdentity(WTSCLI_EXTENSION_ID, WTSCLI_EXTENSION_ORIGIN):
        _fail()

    state_payload = _required_mapping(
        payload["state"],
        {"rootDir", "envPrefix", "configDirEnv", "cacheDirEnv", "ownershipFile"},
    )
    state = BrowserBridgeStateIdentity(
        root_dir=_required_string(state_payload["rootDir"]),
        env_prefix=_required_string(state_payload["envPrefix"]),
        config_dir_env=_required_string(state_payload["configDirEnv"]),
        cache_dir_env=_required_string(state_payload["cacheDirEnv"]),
        ownership_file=_relative_path(state_payload["ownershipFile"]),
    )
    if state != BrowserBridgeStateIdentity(
        root_dir=WTSCLI_STATE_ROOT,
        env_prefix=WTSCLI_ENV_PREFIX,
        config_dir_env=WTSCLI_CONFIG_DIR_ENV,
        cache_dir_env=WTSCLI_CACHE_DIR_ENV,
        ownership_file=WTSCLI_OWNERSHIP_FILE,
    ):
        _fail()

    package_payload = _required_mapping(payload["package"], {"name", "entrypoint"})
    package = BrowserBridgePackageIdentity(
        name=_required_string(package_payload["name"]),
        entrypoint=_required_string(package_payload["entrypoint"]),
    )
    if package != BrowserBridgePackageIdentity(WTSCLI_PACKAGE, WTSCLI_ENTRYPOINT):
        _fail()
    return BrowserBridgeRuntimeIdentity(
        endpoint=endpoint,
        transport=transport,
        extension=extension,
        state=state,
        package=package,
    )


def _parse_cli(value: object) -> BrowserBridgeCliAsset:
    payload = _required_mapping(
        value,
        {"package", "entrypoint", "version", "asset", "size", "sha256"},
    )
    cli = BrowserBridgeCliAsset(
        package=_required_string(payload["package"]),
        entrypoint=_required_string(payload["entrypoint"]),
        version=_required_string(payload["version"]),
        asset=_relative_path(payload["asset"]),
        size=_safe_int(payload["size"], positive=True),
        sha256=_sha256_value(payload["sha256"]),
    )
    if (
        cli.package != WTSCLI_PACKAGE
        or cli.entrypoint != WTSCLI_ENTRYPOINT
        or cli.version != WTSCLI_VERSION
        or cli.asset != f"runtime/wtscli-{WTSCLI_VERSION}.tgz"
    ):
        _fail()
    return cli


def _parse_extension(value: object) -> BrowserBridgeExtensionAsset:
    payload = _required_mapping(
        value,
        {
            "version",
            "id",
            "origin",
            "directory",
            "treeSha256",
            "manifestSha256",
            "files",
        },
    )
    raw_files = payload["files"]
    if not isinstance(raw_files, list) or not raw_files or len(raw_files) > 4096:
        _fail()
    files = tuple(_parse_extension_file(item) for item in raw_files)
    paths = tuple(item.path for item in files)
    if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
        _fail()
    extension = BrowserBridgeExtensionAsset(
        version=_required_string(payload["version"]),
        id=_extension_id_value(payload["id"]),
        origin=_required_string(payload["origin"]),
        directory=_relative_path(payload["directory"]),
        tree_sha256=_sha256_value(payload["treeSha256"]),
        manifest_sha256=_sha256_value(payload["manifestSha256"]),
        files=files,
    )
    if (
        extension.version != WTSCLI_VERSION
        or extension.id != WTSCLI_EXTENSION_ID
        or extension.origin != WTSCLI_EXTENSION_ORIGIN
        or extension.directory != "extension"
    ):
        _fail()
    return extension


def _parse_extension_file(value: object) -> BrowserBridgeExtensionFile:
    payload = _required_mapping(value, {"path", "size", "sha256"})
    return BrowserBridgeExtensionFile(
        path=_relative_path(payload["path"]),
        size=_safe_int(payload["size"], positive=False),
        sha256=_sha256_value(payload["sha256"]),
    )


def _parse_protocol_version(value: object) -> BrowserBridgeProtocolVersion:
    payload = _required_mapping(value, {"major", "minor"})
    return BrowserBridgeProtocolVersion(
        major=_safe_int(payload["major"], positive=False),
        minor=_safe_int(payload["minor"], positive=False),
    )


def _parse_capabilities(value: object) -> frozenset[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        _fail()
    capabilities = tuple(item for item in value if isinstance(item, str))
    missing = REQUIRED_BROWSER_BRIDGE_CAPABILITIES - set(capabilities)
    if missing:
        _fail("capability_missing")
    if capabilities != _CANONICAL_CAPABILITIES:
        _fail()
    return frozenset(capabilities)


def _strict_object(raw: bytes) -> dict[str, object]:
    if type(raw) is not bytes:
        _fail()
    try:
        return strict_json_object_loads(raw)
    except StrictJsonError:
        _fail()


def _required_mapping(value: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        _fail()
    payload = {key: item for key, item in value.items() if isinstance(key, str)}
    _require_fields(payload, fields)
    return payload


def _require_fields(payload: dict[str, object], fields: set[str]) -> None:
    if set(payload) != fields:
        _fail()


def _required_string(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        _fail()
    return value


def _safe_int(value: object, *, positive: bool) -> int:
    if type(value) is not int or value < (1 if positive else 0) or value > MAX_SAFE_INTEGER:
        _fail()
    return value


def _header(value: object, *, with_value: bool) -> tuple[str, str]:
    fields = {"name", "value"} if with_value else {"name"}
    payload = _required_mapping(value, fields)
    name = _required_string(payload["name"])
    header_value = _required_string(payload["value"]) if with_value else ""
    return name, header_value


def _git_sha(value: object) -> str:
    parsed = _required_string(value)
    if GIT_SHA_RE.fullmatch(parsed) is None:
        _fail()
    return parsed


def _sha256_value(value: object) -> str:
    parsed = _required_string(value)
    if SHA256_RE.fullmatch(parsed) is None:
        _fail()
    return parsed


def _extension_id_value(value: object) -> str:
    parsed = _required_string(value)
    if EXTENSION_ID_RE.fullmatch(parsed) is None:
        _fail()
    return parsed


def _relative_path(value: object) -> str:
    parsed = _required_string(value)
    if (
        parsed != unicodedata.normalize("NFC", parsed)
        or "\\" in parsed
        or ":" in parsed
        or "\x00" in parsed
        or parsed.startswith("/")
        or parsed.endswith("/")
        or "//" in parsed
        or PurePosixPath(parsed).is_absolute()
    ):
        _fail()
    parts = parsed.split("/")
    if any(part in {"", ".", ".."} or any(ord(character) < 0x20 for character in part) for part in parts):
        _fail()
    return parsed


def _bundle_path(root: Path, value: str) -> Path:
    candidate = root.joinpath(*value.split("/"))
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        _fail()
    if not resolved.is_relative_to(root):
        _fail()
    return candidate


def _extension_files(extension_dir: Path) -> tuple[BrowserBridgeExtensionFile, ...]:
    files: list[BrowserBridgeExtensionFile] = []
    for candidate in sorted(extension_dir.rglob("*")):
        if candidate.is_symlink():
            _fail()
        if candidate.is_file():
            files.append(
                BrowserBridgeExtensionFile(
                    path=candidate.relative_to(extension_dir).as_posix(),
                    size=candidate.stat().st_size,
                    sha256=_sha256(candidate),
                )
            )
    return tuple(files)


def _extension_id(value: object) -> str:
    if not isinstance(value, str) or not value:
        _fail()
    try:
        key = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        _fail()
    digest = hashlib.sha256(key).digest()[:16]
    return "".join(chr(ord("a") + nibble) for byte in digest for nibble in (byte >> 4, byte & 0x0F))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fail(code: BrowserBridgeManifestErrorCode = "integrity_failed") -> NoReturn:
    raise BrowserBridgeManifestError(code)


__all__ = [
    "BROWSER_BRIDGE_IMPLEMENTATION",
    "BROWSER_BRIDGE_MANIFEST_SCHEMA",
    "BROWSER_BRIDGE_PROTOCOL_MAJOR",
    "BROWSER_BRIDGE_PROTOCOL_MINOR",
    "REQUIRED_BROWSER_BRIDGE_CAPABILITIES",
    "WTSCLI_FORK_COMMIT",
    "WTSCLI_VERSION",
    "BrowserBridgeBundle",
    "BrowserBridgeManifestError",
    "BrowserBridgeRequirement",
    "load_browser_bridge_bundle",
    "load_browser_bridge_requirement",
    "load_runtime_package_identity",
    "parse_browser_bridge_requirement",
]
