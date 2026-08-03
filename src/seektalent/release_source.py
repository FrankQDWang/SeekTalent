from __future__ import annotations

import subprocess
from pathlib import Path


def source_revision(repo_root: Path) -> str:
    """Return the exact revision of a clean release source checkout."""
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if dirty:
        raise RuntimeError("source_checkout_not_clean")
    return revision
