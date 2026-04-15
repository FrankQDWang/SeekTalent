from __future__ import annotations

from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / ".env.example"
    target = repo_root / "src" / "seektalent" / "default.env"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Synced {source} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
