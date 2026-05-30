from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = PROJECT_ROOT / "tools" / "tach_baseline.json"
LINE_NUMBER_RE = re.compile(r"^(\[FAIL\] .+?\.py):\d+:( .+)$")


def normalize_failure(line: str) -> str:
    match = LINE_NUMBER_RE.match(line.strip())
    if not match:
        return line.strip()
    return f"{match.group(1)}:{match.group(2)}"


def extract_failures(output: str) -> list[str]:
    return sorted(
        normalize_failure(line)
        for line in output.splitlines()
        if line.strip().startswith("[FAIL]")
    )


def compare_violations(*, current: list[str], baseline: list[str]) -> list[str]:
    baseline_set = set(baseline)
    return sorted(line for line in current if line not in baseline_set)


def run_tach_check() -> tuple[int, str]:
    completed = subprocess.run(
        ["uv", "run", "tach", "check"],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return completed.returncode, completed.stdout


def read_baseline() -> list[str]:
    if not BASELINE_PATH.exists():
        return []
    payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return sorted(str(item) for item in payload["accepted_failures"])


def write_baseline(failures: list[str]) -> None:
    BASELINE_PATH.write_text(
        json.dumps({"accepted_failures": failures}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail when Tach reports new architecture violations.")
    parser.add_argument("--write-current", action="store_true")
    args = parser.parse_args()

    return_code, output = run_tach_check()
    current = extract_failures(output)
    if return_code != 0 and not current:
        print("Tach failed before reporting architecture failures:")
        print(output)
        return 1

    if args.write_current:
        write_baseline(current)
        print(f"wrote {len(current)} accepted Tach failures to {BASELINE_PATH}")
        return 0

    new_failures = compare_violations(current=current, baseline=read_baseline())
    if new_failures:
        print("New Tach architecture violations:")
        print("\n".join(new_failures))
        return 1
    print(f"Tach baseline ok: {len(current)} current accepted failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
