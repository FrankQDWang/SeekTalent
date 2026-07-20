"""Shared helpers for the production-unreachable native evidence probes."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path


class ProbeFailure(RuntimeError):
    """The native host did not provide the behavior the decision requires."""


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def run_executable(path: Path, *, timeout_seconds: float = 10) -> dict[str, int | str]:
    try:
        completed = subprocess.run(
            [str(path)],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeFailure(f"bounded executable did not exit within {timeout_seconds}s: {path}") from exc
    return {
        "returncode": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
    }


def red_popen_toctou(
    root: Path,
    trusted_source: Path,
    replacement_source: Path,
    *,
    record_open_descriptor_identity: bool = False,
) -> dict[str, object]:
    """Show that replacing an admitted path changes the image Popen starts."""
    candidate = root / f"candidate{trusted_source.suffix}"
    replacement = root / f"replacement{trusted_source.suffix}"
    shutil.copyfile(trusted_source, candidate)
    shutil.copyfile(replacement_source, replacement)
    candidate.chmod(candidate.stat().st_mode | 0o700)
    replacement.chmod(replacement.stat().st_mode | 0o700)

    before = run_executable(candidate)
    admitted_digest = sha256(candidate)
    descriptor: int | None = None
    admitted_descriptor_identity: dict[str, int] | None = None
    if record_open_descriptor_identity:
        descriptor = os.open(candidate, os.O_RDONLY)
        admitted = os.fstat(descriptor)
        admitted_descriptor_identity = {"device": admitted.st_dev, "inode": admitted.st_ino}
    try:
        os.replace(replacement, candidate)
        launched = os.lstat(candidate)
    finally:
        if descriptor is not None:
            os.close(descriptor)
    after = run_executable(candidate)
    launched_digest = sha256(candidate)
    if before == after or admitted_digest == launched_digest:
        raise ProbeFailure("Popen TOCTOU replacement reproducer did not distinguish the two images")
    result: dict[str, object] = {
        "admitted_sha256": admitted_digest,
        "launched_sha256": launched_digest,
        "before": before,
        "after": after,
        "path_replacement_changed_started_image": True,
    }
    if admitted_descriptor_identity is not None:
        launched_path_identity = {"device": launched.st_dev, "inode": launched.st_ino}
        if admitted_descriptor_identity == launched_path_identity:
            raise ProbeFailure("open descriptor did not retain a different identity across path replacement")
        result["admitted_open_descriptor_identity"] = admitted_descriptor_identity
        result["launched_path_identity"] = launched_path_identity
    return result
