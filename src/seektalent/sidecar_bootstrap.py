"""Minimal packaged sidecar process boundary.

The bootstrap deliberately owns no product behavior. It keeps only its
inherited stdin pipe open and exits when the parent closes that pipe.
"""

from __future__ import annotations

import sys


def main() -> int:
    while sys.stdin.buffer.read(64 * 1024):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
