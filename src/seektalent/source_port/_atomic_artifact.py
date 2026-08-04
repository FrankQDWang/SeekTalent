"""Atomic publication for content-addressed Source Port artifacts."""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import os
from pathlib import Path
from tempfile import mkstemp


def publish_content_addressed_bytes(
    root: Path,
    payload: bytes,
    digest: str,
    *,
    fault_injector: Callable[[str], None] | None = None,
) -> bool:
    """Publish canonical bytes without ever exposing a partial final file."""
    if sha256(payload).hexdigest() != digest:
        raise ValueError("content_addressed_artifact_digest_mismatch")
    artifact_root = root.resolve(strict=False)
    artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = artifact_root / f"{digest}.json"
    try:
        existing = path.read_bytes()
    except FileNotFoundError:
        existing = None
    if existing == payload:
        return False

    descriptor, temporary_name = mkstemp(
        prefix=f".{digest}.",
        suffix=".tmp",
        dir=artifact_root,
    )
    temporary_path = Path(temporary_name)
    published = False
    try:
        os.chmod(temporary_path, 0o600)
        _inject_fault(fault_injector, "after_temporary_created")
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            _inject_fault(fault_injector, "after_temporary_written")
            os.fsync(handle.fileno())
        _inject_fault(fault_injector, "after_temporary_fsynced")
        os.replace(temporary_path, path)
        published = True
        _inject_fault(fault_injector, "after_final_replaced")
        _persist_directory(artifact_root)
        _inject_fault(fault_injector, "after_directory_fsynced")
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        if not published:
            temporary_path.unlink(missing_ok=True)
        raise
    return True


def _persist_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _inject_fault(
    fault_injector: Callable[[str], None] | None,
    point: str,
) -> None:
    if fault_injector is not None:
        fault_injector(point)


__all__ = ["publish_content_addressed_bytes"]
