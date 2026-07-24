from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
PACKAGE_CONTEXT_ENV = "SEEKTALENT_STAGING_HELPER_PACKAGE_ROOT"


def install_browser_bridge(
    *,
    bundle_dir: Path,
    staging_home: Path,
    node: Path,
) -> dict[str, str]:
    from seektalent.browser_bridge_install import install_browser_bridge_bundle

    installed = install_browser_bridge_bundle(
        bundle_dir=bundle_dir,
        install_root=staging_home / ".seektalent",
        node=node,
    )
    return {
        "runtime": str(installed.runtime_dir),
        "extension": str(installed.extension_dir),
        "manifest": str(installed.manifest_path),
        "bridgeBuildId": installed.bridge_build_id,
        "extensionVersion": installed.extension_version,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--staging-home", type=Path)
    parser.add_argument("--node", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    package_context_result = _run_with_explicit_package_context()
    if package_context_result is not None:
        return package_context_result
    if args.verify_only:
        from seektalent.browser_bridge_manifest import (
            BrowserBridgeManifestError,
            load_browser_bridge_bundle,
        )

        try:
            bundle = load_browser_bridge_bundle(args.bundle_dir.resolve())
        except BrowserBridgeManifestError as exc:
            print(
                f"reason_code=browser_bridge_bundle_{exc.code}",
                file=sys.stderr,
            )
            return 1
        print(bundle.bridge_build_id)
        return 0
    if args.staging_home is None or args.node is None:
        parser.error("--staging-home and --node are required unless --verify-only is used")
    result = install_browser_bridge(
        bundle_dir=args.bundle_dir.resolve(),
        staging_home=args.staging_home.resolve(),
        node=args.node.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _run_with_explicit_package_context() -> int | None:
    source_root = str(SOURCE_ROOT)
    if os.environ.get(PACKAGE_CONTEXT_ENV) == source_root:
        return None
    env = {
        **os.environ,
        "PYTHONPATH": source_root,
        PACKAGE_CONTEXT_ENV: source_root,
    }
    completed = subprocess.run(
        (sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]),
        env=env,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
