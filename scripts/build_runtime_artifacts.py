from __future__ import annotations

from pathlib import Path

from seektalent.canonical_cases import build_all_canonical_artifacts


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    build_all_canonical_artifacts(repo_root=repo_root)


if __name__ == "__main__":
    main()
