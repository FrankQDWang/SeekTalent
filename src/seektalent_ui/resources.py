from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent


def package_frontend_dir() -> Path:
    return PACKAGE_ROOT / "static" / "workbench"


def package_frontend_fallback_file() -> Path:
    return package_frontend_dir() / "200.html"


def frontend_available(root: Path | None = None) -> bool:
    frontend_root = root or package_frontend_dir()
    return (frontend_root / "200.html").is_file() and (frontend_root / "_app").is_dir()
