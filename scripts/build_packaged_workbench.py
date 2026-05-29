from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "apps" / "web-svelte"
WEB_BUILD_DIR = WEB_DIR / "build"
PACKAGE_FRONTEND_DIR = ROOT / "src" / "seektalent_ui" / "static" / "workbench"


def main() -> int:
    if shutil.which("bun") is None:
        raise SystemExit("bun is required to build the packaged Workbench frontend.")
    subprocess.run(["bun", "install", "--frozen-lockfile"], cwd=WEB_DIR, check=True)
    subprocess.run(["bun", "run", "build"], cwd=WEB_DIR, check=True)
    _copy_frontend()
    _validate_frontend()
    print(f"Packaged Workbench frontend written to {PACKAGE_FRONTEND_DIR}")
    return 0


def _copy_frontend() -> None:
    if PACKAGE_FRONTEND_DIR.exists():
        shutil.rmtree(PACKAGE_FRONTEND_DIR)
    shutil.copytree(WEB_BUILD_DIR, PACKAGE_FRONTEND_DIR)


def _validate_frontend() -> None:
    required = [
        PACKAGE_FRONTEND_DIR / "200.html",
        PACKAGE_FRONTEND_DIR / "_app",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"Packaged frontend is incomplete: {', '.join(missing)}")
    source_maps = sorted(PACKAGE_FRONTEND_DIR.rglob("*.map"))
    if source_maps:
        joined = ", ".join(str(path.relative_to(PACKAGE_FRONTEND_DIR)) for path in source_maps[:5])
        raise SystemExit(f"Packaged frontend must not include source maps: {joined}")


if __name__ == "__main__":
    raise SystemExit(main())
