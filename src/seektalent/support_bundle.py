"""Create a local, allowlisted execution support bundle."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib import metadata as importlib_metadata
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
from typing import Callable
from uuid import uuid4

from seektalent.config import AppSettings
from seektalent.browser_bridge_manifest import (
    BrowserBridgeExtensionFile,
    BrowserBridgeManifestError,
    WTSCLI_BUILD_ID,
    WTSCLI_EXTENSION_ID,
    WTSCLI_FORK_COMMIT,
    WTSCLI_VERSION,
    load_browser_bridge_requirement,
)
from seektalent.browser_bridge_runtime_receipt import (
    WTSCLI_PACKAGE_ARCHIVE_FILENAME,
    runtime_package_receipt,
    verify_installed_runtime_package,
)
from seektalent.domi_bootstrap import (
    INSTALL_RECEIPT_RELATIVE_PATH,
    INSTALL_RECEIPT_SCHEMA,
)
from seektalent.version import __version__
from seektalent.wtscli_lifecycle_supervisor import WtsCliLifecycleStatus


SUPPORT_BUNDLE_SCHEMA = "seektalent.execution-support-bundle.v2"


class SupportBundleError(RuntimeError):
    pass


def create_execution_support_bundle(
    *,
    settings: AppSettings,
    runtime_run_id: str | None = None,
    output_dir: Path | None = None,
    now: Callable[[], datetime] | None = None,
) -> Path:
    created_at = (now or (lambda: datetime.now(UTC)))().astimezone(UTC)
    parent = output_dir or settings.artifacts_path / "support-bundles"
    parent.mkdir(parents=True, exist_ok=True)
    root = parent / (
        f"execution-support-{created_at.strftime('%Y%m%dT%H%M%SZ')}-"
        f"{uuid4().hex[:12]}"
    )
    root.mkdir(mode=0o700)
    database_path = settings.runtime_control_path
    payload: dict[str, object] = {
        "schemaVersion": SUPPORT_BUNDLE_SCHEMA,
        "createdAt": created_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "package": {
            "name": "seektalent",
            "version": _installed_version(),
        },
        "executionIdentity": _execution_identity(),
        "runtimeControl": {
            "databaseAvailable": database_path.is_file(),
            "schemaVersion": None,
            "runs": [],
            "checkpoints": [],
            "sourceOperations": [],
            "browserLane": None,
            "browserLaneResolutions": [],
            "failureEnvelopes": [],
            "executionFailures": [],
            "componentHealth": [],
            "phaseDurations": [],
        },
        "privacy": {
            "allowlistOnly": True,
            "includesRawStderr": False,
            "includesDom": False,
            "includesBusinessText": False,
            "automaticUpload": False,
        },
    }
    if database_path.is_file():
        try:
            payload["runtimeControl"] = _runtime_control_payload(
                database_path,
                runtime_run_id=runtime_run_id,
            )
        except sqlite3.Error as exc:
            raise SupportBundleError("support_bundle_runtime_control_unavailable") from exc
    destination = root / "bundle.json"
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode() + b"\n"
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
    return destination


def _runtime_control_payload(
    database_path: Path,
    *,
    runtime_run_id: str | None,
) -> dict[str, object]:
    connection = sqlite3.connect(
        f"file:{database_path}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        selected_run_ids = _selected_run_ids(
            connection,
            runtime_run_id=runtime_run_id,
        )
        return {
            "databaseAvailable": True,
            "schemaVersion": schema_version,
            "runs": _runs(connection, selected_run_ids),
            "checkpoints": _checkpoints(connection, selected_run_ids),
            "sourceOperations": _source_operations(connection, selected_run_ids),
            "browserLane": _browser_lane(connection),
            "browserLaneResolutions": _browser_lane_resolutions(
                connection
            ),
            "failureEnvelopes": _failure_envelopes(connection, selected_run_ids),
            "executionFailures": _execution_failures(connection),
            "componentHealth": _component_health(connection),
            "phaseDurations": _phase_durations(
                connection,
                selected_run_ids,
            ),
        }
    finally:
        connection.close()


def _selected_run_ids(
    connection: sqlite3.Connection,
    *,
    runtime_run_id: str | None,
) -> tuple[str, ...]:
    if runtime_run_id is not None:
        row = connection.execute(
            "SELECT runtime_run_id FROM runtime_control_runs WHERE runtime_run_id = ?",
            (runtime_run_id,),
        ).fetchone()
        if row is None:
            raise SupportBundleError("support_bundle_runtime_run_not_found")
        return (str(row["runtime_run_id"]),)
    rows = connection.execute(
        """
        SELECT runtime_run_id
        FROM runtime_control_runs
        ORDER BY updated_at DESC
        LIMIT 20
        """
    ).fetchall()
    return tuple(str(row["runtime_run_id"]) for row in rows)


def _runs(
    connection: sqlite3.Connection,
    runtime_run_ids: tuple[str, ...],
) -> list[dict[str, object]]:
    return _query_for_runs(
        connection,
        """
        SELECT runtime_run_id, status, current_stage, current_round,
               latest_checkpoint_id, stop_reason_code, created_at, updated_at,
               completed_at
        FROM runtime_control_runs
        WHERE runtime_run_id IN ({placeholders})
        ORDER BY updated_at DESC
        """,
        runtime_run_ids,
    )


def _checkpoints(
    connection: sqlite3.Connection,
    runtime_run_ids: tuple[str, ...],
) -> list[dict[str, object]]:
    return _query_for_runs(
        connection,
        """
        SELECT checkpoint_id, runtime_run_id, stage, round_no, safe_boundary,
               schema_version, created_at
        FROM runtime_control_checkpoints
        WHERE runtime_run_id IN ({placeholders})
        ORDER BY created_at DESC
        """,
        runtime_run_ids,
    )


def _source_operations(
    connection: sqlite3.Connection,
    runtime_run_ids: tuple[str, ...],
) -> list[dict[str, object]]:
    rows = _query_for_runs(
        connection,
        """
        SELECT runtime_run_id, operation_id, source_id, operation_kind,
               runtime_attempt_no, operation_phase,
               source_operation_disposition, retry_posture,
               reconciliation_revision, main_commit_ref, ledger_revision
        FROM runtime_control_source_operations
        WHERE runtime_run_id IN ({placeholders})
        ORDER BY runtime_run_id, ledger_revision
        """,
        runtime_run_ids,
    )
    for row in rows:
        row["mainCommitted"] = row.pop("main_commit_ref") is not None
    return rows


def _browser_lane(
    connection: sqlite3.Connection,
) -> dict[str, object] | None:
    if not _table_exists(connection, "runtime_control_browser_lanes"):
        return None
    row = connection.execute(
        """
        SELECT lane_key, fencing_token, status, runtime_run_id,
               operation_id, operation_kind, heartbeat_at,
               lease_expires_at, released_at, last_failure_code, updated_at
        FROM runtime_control_browser_lanes
        WHERE lane_key = 'liepin_browser'
        """
    ).fetchone()
    return None if row is None else dict(row)


def _failure_envelopes(
    connection: sqlite3.Connection,
    runtime_run_ids: tuple[str, ...],
) -> list[dict[str, object]]:
    return _query_for_runs(
        connection,
        """
        SELECT failure_id, revision, run_id AS runtime_run_id,
               operation_id, attempt_no, component, domain,
               failure_kind, reason_code, current_outcome,
               occurred_at, observed_at
        FROM runtime_control_failure_envelope_revisions
        WHERE run_id IN ({placeholders})
        ORDER BY observed_at DESC
        """,
        runtime_run_ids,
    )


def _browser_lane_resolutions(
    connection: sqlite3.Connection,
) -> list[dict[str, object]]:
    if not _table_exists(
        connection,
        "runtime_control_browser_lane_resolutions",
    ):
        return []
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT lane_key, fencing_token, runtime_run_id,
                   operation_id, outcome, history_conclusion,
                   evidence_ref, evidence_digest, resolved_at
            FROM runtime_control_browser_lane_resolutions
            ORDER BY resolved_at DESC
            LIMIT 20
            """
        ).fetchall()
    ]


def _execution_failures(
    connection: sqlite3.Connection,
) -> list[dict[str, object]]:
    if not _table_exists(
        connection,
        "runtime_control_execution_failures",
    ):
        return []
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT runtime_run_id, component, boundary,
                   safe_reason_code, exception_type,
                   exception_fingerprint, failure_role, occurred_at
            FROM runtime_control_execution_failures
            ORDER BY occurred_at DESC
            LIMIT 100
            """
        ).fetchall()
    ]


def _component_health(
    connection: sqlite3.Connection,
) -> list[dict[str, object]]:
    if not _table_exists(
        connection,
        "runtime_control_component_health",
    ):
        return []
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT component, alive, last_heartbeat_at, last_success_at,
                   first_failure_at, first_failure_type, failure_count,
                   restart_count, observed_at
            FROM runtime_control_component_health
            ORDER BY component
            """
        ).fetchall()
    ]


def _phase_durations(
    connection: sqlite3.Connection,
    runtime_run_ids: tuple[str, ...],
) -> list[dict[str, object]]:
    rows = _query_for_runs(
        connection,
        """
        SELECT runtime_run_id, stage, MIN(created_at) AS started_at,
               MAX(created_at) AS last_observed_at
        FROM runtime_control_events
        WHERE runtime_run_id IN ({placeholders})
        GROUP BY runtime_run_id, stage
        ORDER BY runtime_run_id, started_at
        """,
        runtime_run_ids,
    )
    for row in rows:
        started = datetime.fromisoformat(
            str(row["started_at"]).replace("Z", "+00:00")
        )
        observed = datetime.fromisoformat(
            str(row["last_observed_at"]).replace("Z", "+00:00")
        )
        row["durationSeconds"] = max(
            0.0,
            (observed - started).total_seconds(),
        )
    return rows


def _query_for_runs(
    connection: sqlite3.Connection,
    sql: str,
    runtime_run_ids: tuple[str, ...],
) -> list[dict[str, object]]:
    if not runtime_run_ids:
        return []
    statement = sql.format(
        placeholders=", ".join("?" for _ in runtime_run_ids)
    )
    return [
        dict(row)
        for row in connection.execute(statement, runtime_run_ids).fetchall()
    ]


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (name,),
    ).fetchone() is not None


def _installed_version() -> str:
    try:
        return importlib_metadata.version("seektalent")
    except importlib_metadata.PackageNotFoundError:
        return __version__


def _execution_identity() -> dict[str, object]:
    install_root = Path(
        os.environ.get("SEEKTALENT_INSTALL_HOME", str(Path.home()))
    )
    receipt_path = install_root / INSTALL_RECEIPT_RELATIVE_PATH
    receipt: dict[str, object] | None = None
    receipt_reason: str | None = None
    if receipt_path.is_file() and not receipt_path.is_symlink():
        try:
            candidate = json.loads(receipt_path.read_text(encoding="utf-8"))
            if (
                isinstance(candidate, dict)
                and candidate.get("schemaVersion")
                == INSTALL_RECEIPT_SCHEMA
                and _receipt_matches_expected_identity(candidate)
            ):
                receipt = {
                    key: candidate.get(key)
                    for key in (
                        "schemaVersion",
                        "productVersion",
                        "sourceRevision",
                        "productBuildId",
                        "wheelSha256",
                        "deliveryManifestSha256",
                        "bridgeBuildId",
                        "wtscliVersion",
                        "wtscliForkCommit",
                        "extensionVersion",
                        "extensionIdSha256",
                        "hostPlatform",
                        "hostOsFamily",
                        "hostArchitecture",
                        "pythonVersion",
                        "pythonImplementation",
                        "pythonCacheTag",
                        "pythonSoabi",
                        "pythonExecutableSha256",
                        "nodeVersion",
                        "nodeExecutableSha256",
                        "acceptanceFixtureSchemaVersion",
                        "acceptanceFixtureSha256",
                    )
                }
                receipt["pythonExecutablePathSha256"] = _sha256_string(
                    candidate["pythonExecutable"]
                )
                receipt["nodeExecutablePathSha256"] = _sha256_string(
                    candidate["nodeExecutable"]
                )
            else:
                receipt_reason = "install_receipt_identity_mismatch"
        except (OSError, json.JSONDecodeError):
            receipt_reason = "install_receipt_invalid"
    else:
        receipt_reason = "install_receipt_missing"
    return {
        "expected": {
            "packageVersion": __version__,
            "bridgeBuildId": WTSCLI_BUILD_ID,
            "wtscliVersion": WTSCLI_VERSION,
            "wtscliForkCommit": WTSCLI_FORK_COMMIT,
            "extensionVersion": WTSCLI_VERSION,
            "extensionIdSha256": hashlib.sha256(
                WTSCLI_EXTENSION_ID.encode()
            ).hexdigest(),
        },
        "receiptAvailable": receipt is not None,
        "receiptReasonCode": receipt_reason,
        "receipt": receipt,
        "installedAssets": _installed_asset_identity(install_root),
        "launchFacts": _launch_facts(install_root, receipt),
    }


def _launch_facts(
    install_root: Path,
    receipt: dict[str, object] | None,
) -> dict[str, object]:
    owner_classification = "unknown"
    extension_activation = "unknown"
    manifest_path = install_root / ".seektalent" / "browser-bridge" / "bridge-manifest.json"
    try:
        requirement = load_browser_bridge_requirement(manifest_path)
        state_root = requirement.runtime_identity.state.resolve_root(home=install_root)
        status_payload = json.loads(
            (state_root / "seektalent-wtscli-supervisor-status.json").read_text(
                encoding="utf-8"
            )
        )
        status = WtsCliLifecycleStatus.from_payload(status_payload)
    except (BrowserBridgeManifestError, OSError, json.JSONDecodeError):
        status = None
    port_open = _local_port_open(19826)
    if port_open is False:
        owner_classification = "absent"
    elif port_open is True:
        owner_classification = (
            "owned_exact"
            if status is not None
            and status.bridge_build_id == WTSCLI_BUILD_ID
            and status.daemon_owned
            else "foreign"
        )
    if status is not None:
        extension_activation = "active" if status.extension_connected else "inactive"
    return {
        "hostPlatform": None if receipt is None else receipt.get("hostPlatform"),
        "hostOsFamily": None if receipt is None else receipt.get("hostOsFamily"),
        "hostArchitecture": None if receipt is None else receipt.get("hostArchitecture"),
        "productBuildId": None if receipt is None else receipt.get("productBuildId"),
        "pythonVersion": None if receipt is None else receipt.get("pythonVersion"),
        "pythonCacheTag": None if receipt is None else receipt.get("pythonCacheTag"),
        "pythonSoabi": None if receipt is None else receipt.get("pythonSoabi"),
        "pythonExecutablePathSha256": None
        if receipt is None
        else receipt.get("pythonExecutablePathSha256"),
        "pythonExecutableSha256": None
        if receipt is None
        else receipt.get("pythonExecutableSha256"),
        "nodeVersion": None if receipt is None else receipt.get("nodeVersion"),
        "nodeExecutablePathSha256": None
        if receipt is None
        else receipt.get("nodeExecutablePathSha256"),
        "nodeExecutableSha256": None
        if receipt is None
        else receipt.get("nodeExecutableSha256"),
        "port19826OwnerClassification": owner_classification,
        "extensionActivation": extension_activation,
    }


def _local_port_open(port: int) -> bool | None:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
            return True
    except ConnectionRefusedError:
        return False
    except OSError:
        return None


def _sha256_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return hashlib.sha256(value.encode()).hexdigest()


def _installed_asset_identity(
    install_root: Path,
) -> dict[str, object]:
    asset_root = install_root / ".seektalent"
    manifest_path = (
        asset_root / "browser-bridge" / "bridge-manifest.json"
    )
    reasons: list[str] = []
    try:
        requirement = load_browser_bridge_requirement(manifest_path)
    except (BrowserBridgeManifestError, OSError):
        requirement = None
        reasons.append(
            "browser_bridge_manifest_missing"
            if not manifest_path.is_file()
            else "browser_bridge_manifest_identity_mismatch"
        )
    runtime_verified = False
    extension_verified = False
    observed: dict[str, object] | None = None
    if requirement is not None:
        observed = {
            "bridgeBuildId": requirement.bridge_build_id,
            "wtscliPackage": requirement.cli.package,
            "wtscliVersion": requirement.cli.version,
            "wtscliForkCommit": requirement.fork_commit,
            "extensionVersion": requirement.extension.version,
            "extensionIdSha256": hashlib.sha256(
                requirement.extension.id.encode()
            ).hexdigest(),
            "extensionTreeSha256": (
                requirement.extension.tree_sha256
            ),
            "runtimeArchiveSha256": None,
            "runtimeTreeSha256": None,
        }
        runtime_dir = (
            asset_root
            / f"{requirement.cli.package}-runtime"
            / requirement.cli.package
            / requirement.cli.version
        )
        try:
            verify_installed_runtime_package(
                runtime_dir,
                requirement=requirement,
            )
            runtime_receipt = runtime_package_receipt(
                runtime_dir / WTSCLI_PACKAGE_ARCHIVE_FILENAME,
                requirement=requirement,
            )
            observed["runtimeArchiveSha256"] = (
                runtime_receipt.source_sha256
            )
            observed["runtimeTreeSha256"] = (
                runtime_receipt.tree_sha256
            )
            runtime_verified = True
        except (BrowserBridgeManifestError, OSError):
            reasons.append(
                "wtscli_runtime_missing"
                if not runtime_dir.is_dir()
                else "wtscli_runtime_integrity_mismatch"
            )
        extension_dir = (
            asset_root
            / "chrome-extension"
            / requirement.cli.package
        )
        try:
            actual_files = _support_extension_files(extension_dir)
            if actual_files != requirement.extension.files:
                raise BrowserBridgeManifestError("integrity_failed")
            extension_verified = True
        except (BrowserBridgeManifestError, OSError):
            reasons.append(
                "wtscli_extension_missing"
                if not extension_dir.is_dir()
                else "wtscli_extension_integrity_mismatch"
            )
    return {
        "status": "verified" if not reasons else "mismatch",
        "manifestVerified": requirement is not None,
        "runtimeVerified": runtime_verified,
        "extensionVerified": extension_verified,
        "observed": observed,
        "reasonCodes": reasons,
    }


def _support_extension_files(
    extension_dir: Path,
) -> tuple[BrowserBridgeExtensionFile, ...]:
    if extension_dir.is_symlink() or not extension_dir.is_dir():
        raise BrowserBridgeManifestError("integrity_failed")
    files: list[BrowserBridgeExtensionFile] = []
    for candidate in extension_dir.rglob("*"):
        if candidate.is_symlink():
            raise BrowserBridgeManifestError("integrity_failed")
        if not candidate.is_file():
            continue
        files.append(
            BrowserBridgeExtensionFile(
                path=candidate.relative_to(extension_dir).as_posix(),
                size=candidate.stat().st_size,
                sha256=hashlib.sha256(
                    candidate.read_bytes()
                ).hexdigest(),
            )
        )
    return tuple(sorted(files, key=lambda item: item.path))


def _receipt_matches_expected_identity(
    receipt: dict[str, object],
) -> bool:
    source_revision = receipt.get("sourceRevision")
    product_version = receipt.get("productVersion")
    extension_hash = hashlib.sha256(
        WTSCLI_EXTENSION_ID.encode()
    ).hexdigest()
    return (
        product_version == _installed_version()
        and isinstance(source_revision, str)
        and len(source_revision) == 40
        and all(
            character in "0123456789abcdef"
            for character in source_revision
        )
        and receipt.get("productBuildId")
        == f"seektalent-{product_version}+{source_revision}"
        and _sha256_text(receipt.get("wheelSha256"))
        and _sha256_text(receipt.get("deliveryManifestSha256"))
        and receipt.get("bridgeBuildId") == WTSCLI_BUILD_ID
        and receipt.get("wtscliVersion") == WTSCLI_VERSION
        and receipt.get("wtscliForkCommit") == WTSCLI_FORK_COMMIT
        and receipt.get("extensionVersion") == WTSCLI_VERSION
        and receipt.get("extensionIdSha256") == extension_hash
        and receipt.get("hostPlatform")
        in {"macos-arm64", "macos-x86_64", "windows-x64"}
        and receipt.get("hostOsFamily") in {"macos", "windows"}
        and receipt.get("hostArchitecture") in {"arm64", "x64"}
        and receipt.get("pythonImplementation") == "cpython"
        and isinstance(receipt.get("pythonVersion"), str)
        and str(receipt.get("pythonVersion")).startswith("3.13.")
        and receipt.get("pythonCacheTag") == "cpython-313"
        and isinstance(receipt.get("pythonSoabi"), str)
        and bool(receipt.get("pythonSoabi"))
        and isinstance(receipt.get("pythonExecutable"), str)
        and bool(receipt.get("pythonExecutable"))
        and _sha256_text(receipt.get("pythonExecutableSha256"))
        and isinstance(receipt.get("nodeExecutable"), str)
        and bool(receipt.get("nodeExecutable"))
        and receipt.get("nodeVersion") == "22.14.0"
        and _sha256_text(receipt.get("nodeExecutableSha256"))
        and receipt.get("acceptanceFixtureSchemaVersion")
        == "seektalent.acceptance-fixture.v1"
        and _sha256_text(receipt.get("acceptanceFixtureSha256"))
    )


def _sha256_text(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(
            character in "0123456789abcdef"
            for character in value
        )
    )
