from __future__ import annotations

import argparse
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass


RED_PREFIXES = (
    ".github/",
    "tools/",
    "src/seektalent/runtime/",
    "src/seektalent/prompts/",
    "src/seektalent/providers/",
    "src/seektalent/core/retrieval/",
    "apps/liepin-worker/",
)

RED_FILES = {
    ".env.example",
    "src/seektalent/default.env",
    "src/seektalent/models.py",
    "src/seektalent/config.py",
    "src/seektalent_ui/workbench_store.py",
    "src/seektalent_ui/runtime_bridge.py",
    "src/seektalent_ui/runtime_graph.py",
    "scripts/verify-dev-workbench.sh",
    "scripts/verify-red-zone.sh",
}

YELLOW_PREFIXES = (
    "src/seektalent_ui/",
    "apps/web-svelte/src/lib/api/",
)

GREEN_PREFIXES = (
    "docs/",
    "tests/",
    "apps/web-svelte/src/",
    "apps/web-svelte/tests/",
)

GENERATED_DIR_PREFIXES = (
    "artifacts/",
    "docs/superpowers/",
    "src/seektalent_ui/resources/workbench/",
)

GENERATED_FILES = {
    "apps/web-svelte/src/lib/api/schema.d.ts",
}


@dataclass(frozen=True)
class GovernanceResult:
    ok: bool
    messages: list[str]
    red_files: list[str]


def classify_path(path: str) -> str:
    if path in RED_FILES or path.startswith(RED_PREFIXES):
        return "red"
    if path.startswith(YELLOW_PREFIXES):
        return "yellow"
    if path.startswith(GREEN_PREFIXES):
        return "green"
    return "neutral"


def layer_for_path(path: str) -> str:
    if path.startswith("src/seektalent/runtime/"):
        return "runtime"
    if path.startswith("src/seektalent/providers/"):
        return "provider"
    if path.startswith("src/seektalent_ui/"):
        return "bff"
    if path.startswith("apps/web-svelte/"):
        return "frontend"
    if path == ".gitignore" or path.startswith(".github/") or path.startswith("tools/") or path.startswith("scripts/"):
        return "governance"
    if path.startswith("docs/"):
        return "docs"
    if path.startswith("tests/"):
        return "tests"
    return "other"


def is_generated(path: str) -> bool:
    return path in GENERATED_FILES or path.startswith(GENERATED_DIR_PREFIXES)


def merge_changed_file_sets(*file_sets: Sequence[str]) -> list[str]:
    return sorted({path.strip() for file_set in file_sets for path in file_set if path.strip()})


def evaluate_changed_files(
    paths: Sequence[str],
    *,
    max_files: int = 15,
    max_layers: int = 1,
) -> GovernanceResult:
    non_generated = sorted(path for path in paths if path and not is_generated(path))
    layers = sorted(
        {
            layer_for_path(path)
            for path in non_generated
            if layer_for_path(path) not in {"docs", "tests"}
        }
    )
    red_files = sorted(path for path in non_generated if classify_path(path) == "red")
    messages: list[str] = []

    if len(non_generated) > max_files:
        messages.append(f"too many non-generated files changed: {len(non_generated)} > {max_files}")
    if len(layers) > max_layers:
        messages.append(f"cross-layer change touches {len(layers)} layers: {', '.join(layers)}")
    if red_files:
        messages.append("red-zone files touched: " + ", ".join(red_files))

    blocking = [message for message in messages if not message.startswith("red-zone files touched:")]
    return GovernanceResult(ok=not blocking, messages=messages, red_files=red_files)


def changed_files(base: str) -> list[str]:
    merge_base = subprocess.check_output(["git", "merge-base", base, "HEAD"], text=True).strip()
    committed = subprocess.check_output(["git", "diff", "--name-only", f"{merge_base}...HEAD"], text=True)
    unstaged = subprocess.check_output(["git", "diff", "--name-only"], text=True)
    staged = subprocess.check_output(["git", "diff", "--cached", "--name-only"], text=True)
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], text=True)
    return merge_changed_file_sets(
        committed.splitlines(),
        unstaged.splitlines(),
        staged.splitlines(),
        untracked.splitlines(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Check PR size and path governance.")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--max-files", type=int, default=15)
    parser.add_argument("--max-layers", type=int, default=1)
    args = parser.parse_args()

    result = evaluate_changed_files(
        changed_files(args.base),
        max_files=args.max_files,
        max_layers=args.max_layers,
    )
    for message in result.messages:
        print(message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
