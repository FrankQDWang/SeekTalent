from __future__ import annotations

import argparse
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


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

ARCHITECTURE_RADAR_FILES = {
    "tach.toml",
    "tools/tach_baseline.json",
}

BACKEND_ARCHITECTURE_CLEANUP_LAYERS = {
    "governance",
    "other",
    "provider",
    "runtime",
}

CODE_EXTENSIONS = {
    ".cjs",
    ".js",
    ".jsx",
    ".mjs",
    ".py",
    ".svelte",
    ".ts",
    ".tsx",
}

TEST_PATH_PARTS = (
    "/tests/",
    "tests/",
)

DEFAULT_MAX_PROD_FILE_LINES = 600
DEFAULT_MAX_TEST_FILE_LINES = 900


@dataclass(frozen=True)
class GovernanceResult:
    ok: bool
    messages: list[str]
    red_files: list[str]


@dataclass(frozen=True)
class LineCountChange:
    path: str
    base_lines: int | None
    head_lines: int | None


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


def line_limit_for_path(
    path: str,
    *,
    max_prod_file_lines: int = DEFAULT_MAX_PROD_FILE_LINES,
    max_test_file_lines: int = DEFAULT_MAX_TEST_FILE_LINES,
) -> int | None:
    if is_generated(path) or Path(path).suffix not in CODE_EXTENSIONS:
        return None
    if any(part in path for part in TEST_PATH_PARTS):
        return max_test_file_lines
    return max_prod_file_lines


def merge_changed_file_sets(*file_sets: Sequence[str]) -> list[str]:
    return sorted({path.strip() for file_set in file_sets for path in file_set if path.strip()})


def is_backend_architecture_cleanup(paths: Sequence[str], layers: Sequence[str]) -> bool:
    return any(path in ARCHITECTURE_RADAR_FILES for path in paths) and set(layers) <= BACKEND_ARCHITECTURE_CLEANUP_LAYERS


def evaluate_line_counts(
    line_changes: Sequence[LineCountChange],
    *,
    max_prod_file_lines: int = DEFAULT_MAX_PROD_FILE_LINES,
    max_test_file_lines: int = DEFAULT_MAX_TEST_FILE_LINES,
) -> list[str]:
    messages: list[str] = []
    for change in line_changes:
        if change.head_lines is None:
            continue
        limit = line_limit_for_path(
            change.path,
            max_prod_file_lines=max_prod_file_lines,
            max_test_file_lines=max_test_file_lines,
        )
        if limit is None:
            continue
        if change.base_lines is None and change.head_lines > limit:
            messages.append(f"new file too long: {change.path} has {change.head_lines} lines > {limit}")
        elif change.base_lines is not None and change.base_lines > limit and change.head_lines > change.base_lines:
            messages.append(
                f"oversized file grew: {change.path} grew from {change.base_lines} to {change.head_lines} lines "
                f"(limit {limit})"
            )
        elif change.base_lines is not None and change.base_lines <= limit and change.head_lines > limit:
            messages.append(f"file too long: {change.path} has {change.head_lines} lines > {limit}")
    return messages


def evaluate_changed_files(
    paths: Sequence[str],
    *,
    max_files: int = 15,
    max_layers: int = 1,
    line_changes: Sequence[LineCountChange] = (),
    max_prod_file_lines: int = DEFAULT_MAX_PROD_FILE_LINES,
    max_test_file_lines: int = DEFAULT_MAX_TEST_FILE_LINES,
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
    if len(layers) > max_layers and not is_backend_architecture_cleanup(non_generated, layers):
        messages.append(f"cross-layer change touches {len(layers)} layers: {', '.join(layers)}")
    if red_files:
        messages.append("red-zone files touched: " + ", ".join(red_files))
    messages.extend(
        evaluate_line_counts(
            line_changes,
            max_prod_file_lines=max_prod_file_lines,
            max_test_file_lines=max_test_file_lines,
        )
    )

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


def count_lines_in_bytes(content: bytes) -> int:
    if not content:
        return 0
    return content.count(b"\n") + (0 if content.endswith(b"\n") else 1)


def count_file_lines(path: str) -> int | None:
    file_path = Path(path)
    if not file_path.is_file():
        return None
    return count_lines_in_bytes(file_path.read_bytes())


def count_git_file_lines(revision: str, path: str) -> int | None:
    completed = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return count_lines_in_bytes(completed.stdout)


def changed_file_line_counts(base: str, paths: Sequence[str]) -> list[LineCountChange]:
    merge_base = subprocess.check_output(["git", "merge-base", base, "HEAD"], text=True).strip()
    return [
        LineCountChange(
            path,
            base_lines=count_git_file_lines(merge_base, path),
            head_lines=count_file_lines(path),
        )
        for path in paths
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check PR size and path governance.")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--max-files", type=int, default=15)
    parser.add_argument("--max-layers", type=int, default=1)
    parser.add_argument("--max-prod-file-lines", type=int, default=DEFAULT_MAX_PROD_FILE_LINES)
    parser.add_argument("--max-test-file-lines", type=int, default=DEFAULT_MAX_TEST_FILE_LINES)
    args = parser.parse_args()

    paths = changed_files(args.base)
    result = evaluate_changed_files(
        paths,
        max_files=args.max_files,
        max_layers=args.max_layers,
        line_changes=changed_file_line_counts(args.base, paths),
        max_prod_file_lines=args.max_prod_file_lines,
        max_test_file_lines=args.max_test_file_lines,
    )
    for message in result.messages:
        print(message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
