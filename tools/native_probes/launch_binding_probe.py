"""Stable CLI for native immutable-slot launch-binding evidence."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
from pathlib import Path

from launch_binding_common import ProbeFailure


def run_probe() -> dict[str, object]:
    if os.name == "nt":
        platform_result = "windows"
        from launch_binding_windows import probe
    elif sys.platform == "darwin":
        platform_result = "macos"
        from launch_binding_macos import probe
    else:
        raise ProbeFailure(f"native evidence is only defined for Windows or macOS, not {sys.platform}")
    with tempfile.TemporaryDirectory(prefix="seektalent-launch-binding-") as temporary:
        evidence = probe(Path(temporary))
    return {
        "schema_version": "seektalent.native_launch_binding_probe.v1",
        "platform": platform_result,
        "architecture": platform.machine().lower(),
        "host": {
            "os_release": platform.release(),
            "os_build": platform.version(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "python_build": list(platform.python_build()),
        },
        "evidence": evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    try:
        result = run_probe()
    except ProbeFailure as exc:
        print(f"native launch-binding probe failed: {exc}", file=sys.stderr)
        return 1
    if arguments.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
