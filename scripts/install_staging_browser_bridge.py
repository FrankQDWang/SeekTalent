from __future__ import annotations

import argparse
import json
from pathlib import Path

from seektalent.browser_bridge_install import install_browser_bridge_bundle


def install_browser_bridge(
    *,
    bundle_dir: Path,
    staging_home: Path,
    node: Path,
) -> dict[str, str]:
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
    parser.add_argument("--staging-home", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    args = parser.parse_args()
    result = install_browser_bridge(
        bundle_dir=args.bundle_dir.resolve(),
        staging_home=args.staging_home.resolve(),
        node=args.node.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
