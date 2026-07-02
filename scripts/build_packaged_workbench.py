from __future__ import annotations

import shutil
import subprocess
import sys
from argparse import ArgumentParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "apps" / "web-react"
WEB_BUILD_DIR = WEB_DIR / "dist"
PACKAGE_FRONTEND_DIR = ROOT / "src" / "seektalent_ui" / "static" / "workbench"


def main(argv: list[str] | None = None) -> int:
    _parser().parse_args(argv)
    pnpm = _pnpm_command()
    subprocess.run([*pnpm, "install", "--frozen-lockfile"], cwd=WEB_DIR, check=True)
    subprocess.run([*pnpm, "exec", "vite", "build"], cwd=WEB_DIR, check=True)
    _copy_frontend()
    _validate_frontend()
    print(f"Packaged Workbench frontend written to {PACKAGE_FRONTEND_DIR}")
    return 0


def _pnpm_command() -> list[str]:
    if shutil.which("corepack") is not None:
        return ["corepack", "pnpm"]
    if shutil.which("pnpm") is not None:
        return ["pnpm"]
    raise SystemExit("pnpm is required to build the packaged Workbench frontend.")


def _parser() -> ArgumentParser:
    return ArgumentParser(description="Build and package the React Workbench frontend.")


def _copy_frontend() -> None:
    if PACKAGE_FRONTEND_DIR.exists():
        shutil.rmtree(PACKAGE_FRONTEND_DIR)
    shutil.copytree(WEB_BUILD_DIR, PACKAGE_FRONTEND_DIR)
    index_html = PACKAGE_FRONTEND_DIR / "index.html"
    if index_html.exists():
        shutil.copy2(index_html, PACKAGE_FRONTEND_DIR / "200.html")


def _validate_frontend() -> None:
    required = [
        PACKAGE_FRONTEND_DIR / "200.html",
        PACKAGE_FRONTEND_DIR / "_app",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"Packaged frontend is incomplete: {', '.join(missing)}")
    if not any(path.is_file() for path in (PACKAGE_FRONTEND_DIR / "_app").rglob("*")):
        raise SystemExit("Packaged frontend is incomplete: _app has no assets")
    source_maps = sorted(PACKAGE_FRONTEND_DIR.rglob("*.map"))
    if source_maps:
        joined = ", ".join(str(path.relative_to(PACKAGE_FRONTEND_DIR)) for path in source_maps[:5])
        raise SystemExit(f"Packaged frontend must not include source maps: {joined}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
