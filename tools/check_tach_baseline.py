from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = PROJECT_ROOT / "tools" / "tach_baseline.json"
LINE_NUMBER_RE = re.compile(r"^(\[FAIL\] .+?\.py):\d+:( .+)$")
SOURCE_BOUNDARY_MODULES = {
    "seektalent.runtime",
    "seektalent.source_contracts",
    "seektalent.source_adapters",
    "seektalent.sources",
    "seektalent.providers",
    "seektalent.opencli_browser",
}


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


def read_tach_dependencies() -> dict[str, list[str]]:
    tach_path = PROJECT_ROOT / "tach.toml"
    payload = tomllib.loads(tach_path.read_text(encoding="utf-8"))
    return {
        str(module["path"]): [str(dependency) for dependency in module.get("depends_on", [])]
        for module in payload.get("modules", [])
        if isinstance(module, dict) and "path" in module
    }


def _source_boundary_cycles(graph: dict[str, list[str]]) -> list[tuple[str, ...]]:
    cycles: set[tuple[str, ...]] = set()

    def visit(node: str, path: tuple[str, ...]) -> None:
        if node in path:
            cycle = (*path[path.index(node) :], node)
            if any(item in SOURCE_BOUNDARY_MODULES for item in cycle):
                cycles.add(cycle)
            return
        for dependency in graph.get(node, []):
            if dependency in graph:
                visit(dependency, (*path, node))

    for module in graph:
        visit(module, ())
    return sorted(cycles)


def tach_boundary_failures() -> list[str]:
    dependencies_by_module = read_tach_dependencies()
    failures: list[str] = []
    if "seektalent.source_contracts" not in dependencies_by_module:
        failures.append("[FAIL] tach.toml: seektalent.source_contracts module must exist")
    opencli_dependencies = dependencies_by_module.get("seektalent.opencli_browser")
    if opencli_dependencies is None:
        failures.append("[FAIL] tach.toml: seektalent.opencli_browser module must exist")
    else:
        forbidden_opencli_deps = {
            "seektalent.providers",
            "seektalent.sources",
            "seektalent.runtime",
            "seektalent.source_adapters",
            "seektalent_ui",
        }
        for dependency in sorted(forbidden_opencli_deps.intersection(opencli_dependencies)):
            failures.append(f"[FAIL] tach.toml: seektalent.opencli_browser must not depend on {dependency}")
    provider_dependencies = dependencies_by_module.get("seektalent.providers", [])
    if "seektalent.opencli_browser" not in provider_dependencies:
        failures.append("[FAIL] tach.toml: seektalent.providers must depend on seektalent.opencli_browser")
    runtime_dependencies = dependencies_by_module.get("seektalent.runtime", [])
    if "seektalent.sources" in runtime_dependencies:
        failures.append("[FAIL] tach.toml: seektalent.runtime must not depend on seektalent.sources")
    if "seektalent.source_adapters" in runtime_dependencies:
        failures.append("[FAIL] tach.toml: seektalent.runtime must not depend on seektalent.source_adapters")
    if "seektalent.providers" in runtime_dependencies:
        failures.append("[FAIL] tach.toml: seektalent.runtime must not depend on seektalent.providers")
    for cycle in _source_boundary_cycles(dependencies_by_module):
        failures.append("[FAIL] tach.toml: source boundary cycle: " + " -> ".join(cycle))
    return sorted(failures)


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
    current = sorted([*extract_failures(output), *tach_boundary_failures()])
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
