from __future__ import annotations

import argparse
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


FULL_RUN_EVENTS = {"merge_group", "schedule", "workflow_dispatch"}

PYTHON_PREFIXES = (
    ".github/workflows/",
    "src/seektalent/",
    "src/seektalent_conversation_agent/",
    "src/seektalent_ui/",
    "src/seektalent_runtime_control/",
    "tests/",
    "tools/",
    "experiments/",
)
PYTHON_FILES = (
    "pyproject.toml",
    "uv.lock",
    "tach.toml",
    "tools/tach_baseline.json",
    ".github/workflows/python-quality.yml",
)

WORKBENCH_PREFIXES = (
    "apps/web-react/",
    "src/seektalent_conversation_agent/",
    "src/seektalent_ui/",
    "src/seektalent_runtime_control/",
    "tests/test_agent_workbench",
    "tests/test_workbench",
    "tests/test_runtime_control",
)
WORKBENCH_FILES = (
    "pyproject.toml",
    "uv.lock",
    "scripts/verify-dev-workbench.sh",
    "scripts/start-dev-workbench.sh",
    "scripts/build_packaged_workbench.py",
    "tools/check_workbench_schema_modes.py",
    "tools/check_react_workbench_cutover.py",
    "tools/check_react_workbench_design_acceptance.py",
    "tests/test_agent_workbench_contract.py",
    "tests/test_react_workbench_cutover_gate.py",
    "src/seektalent/workbench_internal_secrets.py",
    ".github/workflows/workbench-contract.yml",
)


@dataclass(frozen=True)
class ChangeScope:
    python_quality: bool
    workbench_contract: bool
    reason: str


def classify_paths(paths: Iterable[str], *, event_name: str) -> ChangeScope:
    normalized_paths = tuple(_normalize_path(path) for path in paths if path)

    if event_name in FULL_RUN_EVENTS:
        return ChangeScope(python_quality=True, workbench_contract=True, reason=f"{event_name}:full-run")
    if event_name == "push":
        workbench_contract = any(
            _matches(path, prefixes=WORKBENCH_PREFIXES, files=WORKBENCH_FILES)
            for path in normalized_paths
        )
        if not normalized_paths:
            return ChangeScope(
                python_quality=True,
                workbench_contract=False,
                reason="push:python-quality,no-paths",
            )
        reason = "push:python-quality,paths:" + ",".join(normalized_paths[:20])
        if len(normalized_paths) > 20:
            reason += f",+{len(normalized_paths) - 20}-more"
        return ChangeScope(
            python_quality=True,
            workbench_contract=workbench_contract,
            reason=reason,
        )
    if not normalized_paths:
        return ChangeScope(python_quality=True, workbench_contract=True, reason="no-paths:full-run")

    python_quality = any(_matches(path, prefixes=PYTHON_PREFIXES, files=PYTHON_FILES) for path in normalized_paths)
    workbench_contract = any(_matches(path, prefixes=WORKBENCH_PREFIXES, files=WORKBENCH_FILES) for path in normalized_paths)
    reason = "paths:" + ",".join(normalized_paths[:20])
    if len(normalized_paths) > 20:
        reason += f",+{len(normalized_paths) - 20}-more"
    return ChangeScope(python_quality=python_quality, workbench_contract=workbench_contract, reason=reason)


def changed_paths(*, base: str, head: str = "HEAD") -> list[str]:
    commands = (
        ("git", "diff", "--name-only", "--diff-filter=ACDMRT", f"{base}...{head}"),
        ("git", "diff", "--name-only", "--diff-filter=ACDMRT", base, head),
    )
    for command in commands:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode == 0:
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    raise SystemExit(f"Could not determine changed files against {base}.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", "pull_request"))
    parser.add_argument("--base", default=_default_base())
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--changed-file", action="append", default=None)
    args = parser.parse_args()

    paths = args.changed_file if args.changed_file is not None else changed_paths(base=args.base, head=args.head)
    scope = classify_paths(paths, event_name=args.event)
    output_lines = (
        f"python_quality={_bool_output(scope.python_quality)}",
        f"workbench_contract={_bool_output(scope.workbench_contract)}",
        f"reason={scope.reason}",
    )

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as output_file:
            output_file.write("\n".join(output_lines) + "\n")

    for line in output_lines:
        print(line)


def _default_base() -> str:
    base_ref = os.environ.get("GITHUB_BASE_REF")
    if base_ref:
        return f"origin/{base_ref}"
    return "origin/main"


def _matches(path: str, *, prefixes: tuple[str, ...], files: tuple[str, ...]) -> bool:
    return path in files or any(path.startswith(prefix) for prefix in prefixes)


def _normalize_path(path: str) -> str:
    normalized = path.strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _bool_output(value: bool) -> str:
    return "true" if value else "false"


if __name__ == "__main__":
    main()
