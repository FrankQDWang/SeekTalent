# Production AI Coding Governance Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a merge-time governance gate so AI-coded changes cannot silently corrupt red-zone runtime, provider, Workbench, config, prompt, or CI contracts.

**Architecture:** Start with ownership and PR intent, then wire the strongest existing Workbench verification into CI, then add deterministic PR path/size governance, Tach no-regression, and a red-zone smoke command. This avoids production behavior changes while making future refactors safer.

**Tech Stack:** GitHub Actions, CODEOWNERS, Python 3.12, pytest, Ruff, ty, Tach, Bash, Bun, SvelteKit verification scripts.

**Spec:** `docs/superpowers/specs/2026-05-30-production-ai-coding-governance-gate-design.md`

---

## File Structure

- Add: `.github/CODEOWNERS`
  - Red-zone ownership map for runtime, providers, Workbench persistence, config, prompts, CI, scripts, and tools.
- Add: `.github/pull_request_template.md`
  - Required PR evidence fields for AI-coded changes.
- Create: `docs/governance/ai-coding-policy.md`
  - Human-readable governance contract referenced by the PR template.
- Create: `docs/governance/github-ruleset-checklist.md`
  - Exact GitHub settings that make CODEOWNERS and required checks enforceable after this branch lands.
- Modify: `.github/workflows/ci.yml`
  - Add `merge_group`, split Python quality, Workbench contract, and PR governance jobs.
- Modify: `apps/web-svelte/src/lib/api/schema.d.ts`
  - Generated OpenAPI schema refresh if the newly required Workbench contract detects drift on the clean main baseline.
- Add: `tools/check_pr_governance.py`
  - Path classifier and PR size/cross-layer/risk gate.
- Add: `tests/test_pr_governance.py`
  - Unit tests for path classification and threshold behavior.
- Add: `tools/check_tach_baseline.py`
  - Tach no-regression wrapper around the current architecture drift.
- Add: `tools/tach_baseline.json`
  - Current accepted Tach violations.
- Add: `tests/test_tach_baseline.py`
  - Unit tests for Tach violation parsing and comparison.
- Add: `scripts/verify-red-zone.sh`
  - Focused runtime/provider smoke command for red-zone changes.
- Modify: `docs/development.md`
  - Document governance commands and when to run them.

## Execution Notes

- Do not modify runtime behavior in this plan.
- Do not change Workbench product behavior in this plan.
- Do not try to make Tach clean before creating the baseline gate.
- Keep each task as a separately reviewable commit.
- Stage only files touched by the current task.
- If a command fails because existing branch work is already failing, record the exact failure in the PR body and do not hide it by weakening a gate.

---

### Task 1: Add Ownership And PR Evidence Contract

**Files:**
- Create: `.github/CODEOWNERS`
- Create: `.github/pull_request_template.md`
- Create: `docs/governance/ai-coding-policy.md`
- Create: `docs/governance/github-ruleset-checklist.md`
- Modify: `docs/development.md`

- [ ] **Step 1: Create CODEOWNERS**

Create `.github/CODEOWNERS` with:

```text
# SeekTalent code ownership.
# Red-zone paths require the repository owner until trusted maintainers are added.

# Runtime orchestration and contracts
/src/seektalent/runtime/ @FrankQDWang
/src/seektalent/models.py @FrankQDWang
/src/seektalent/config.py @FrankQDWang
/src/seektalent/default.env @FrankQDWang
/.env.example @FrankQDWang
/src/seektalent/prompts/ @FrankQDWang

# Provider, retrieval, and browser automation boundaries
/src/seektalent/providers/ @FrankQDWang
/src/seektalent/core/retrieval/ @FrankQDWang
/apps/liepin-worker/ @FrankQDWang

# Workbench backend persistence and runtime projections
/src/seektalent_ui/workbench_store.py @FrankQDWang
/src/seektalent_ui/runtime_bridge.py @FrankQDWang
/src/seektalent_ui/runtime_graph.py @FrankQDWang

# CI, governance, and verification
/.github/ @FrankQDWang
/tools/ @FrankQDWang
/scripts/verify-dev-workbench.sh @FrankQDWang
/scripts/verify-red-zone.sh @FrankQDWang
```

- [ ] **Step 2: Add pull request template**

Create `.github/pull_request_template.md` with:

````markdown
## Scope

- Risk class: green / yellow / red
- Allowed paths:
- Forbidden paths:
- Touched contracts:
- Generated files:
- Large change justification:
- Rollback plan:

## AI Execution

- Tool/model used:
- Context files supplied:
- Commands run:
- Checks intentionally skipped:
- Reason for skipped checks:

## Invariants

- [ ] No raw provider payload in API, events, logs, ordinary artifacts, or audit notes.
- [ ] No prompt-only behavior change without replay or contract evidence.
- [ ] No config/env behavior drift.
- [ ] No new fallback without a named failure mode and test.
- [ ] No broad cross-layer edit across runtime, provider, BFF, frontend, and prompts.
- [ ] OpenAPI schema updated or proven unchanged when API models changed.
- [ ] Docs updated when user-facing behavior changed.

## Verification

```text
Paste command output summaries here. Include failures exactly.
```
````

- [ ] **Step 3: Add governance policy**

Create `docs/governance/ai-coding-policy.md` with:

```markdown
# AI Coding Governance Policy

SeekTalent allows fast AI-assisted coding, but `main` is protected by boundaries and evidence.

## Risk Zones

Red-zone paths require owner review and focused verification:

- `src/seektalent/runtime/**`
- `src/seektalent/models.py`
- `src/seektalent/config.py`
- `.env.example`
- `src/seektalent/default.env`
- `src/seektalent/prompts/**`
- `src/seektalent/providers/**`
- `src/seektalent/core/retrieval/**`
- `apps/liepin-worker/**`
- `src/seektalent_ui/workbench_store.py`
- `src/seektalent_ui/runtime_bridge.py`
- `src/seektalent_ui/runtime_graph.py`
- `.github/**`
- `tools/**`
- `scripts/verify-dev-workbench.sh`
- `scripts/verify-red-zone.sh`

Yellow-zone paths may be delegated, but require contract tests and Workbench verification:

- `src/seektalent_ui/server.py`
- `src/seektalent_ui/workbench_routes.py`
- `src/seektalent_ui/models.py`
- `src/seektalent_ui/job_runner.py`
- `src/seektalent_ui/*projection*.py`
- `apps/web-svelte/src/lib/api/schema.d.ts`
- Workbench graph, note, candidate, and source-card projections

Green-zone paths are lower-risk display, docs, fixtures, and black-box test changes.

## PR Size Rules

- Ordinary PRs should touch one layer.
- Ordinary PRs should keep non-generated changed files at or below 15.
- Ordinary PRs above 500 changed lines must explain why the change is not split. This slice enforces file count and path spread by machine first.
- Red-zone PRs must be draft until verification evidence is present.
- PRs must not combine prompt, runtime, provider, BFF, frontend, and config changes. If a plan needs multiple layers, split the work into stacked PRs or land a separate owner-reviewed governance change that adjusts the gate.

## Required Evidence

- Green: relevant lint/test command.
- Yellow: relevant contract tests plus `scripts/verify-dev-workbench.sh`.
- Red: focused runtime/provider tests plus `scripts/verify-red-zone.sh`; add Workbench verification if a Workbench path changed.

## Model Permission

Low-cost or unfamiliar models may propose red-zone patches, but the patch must stay draft until owner review and red-zone verification are complete.
```

- [ ] **Step 4: Add GitHub ruleset checklist**

Create `docs/governance/github-ruleset-checklist.md` with:

```markdown
# GitHub Ruleset Checklist

This file records the repository settings that must be enabled after the governance gate lands. The files in this branch define ownership and CI checks; GitHub settings make those checks enforceable.

Apply this to the default branch, currently `main`.

## Required Pull Request Rules

- Require a pull request before merging.
- Require approvals.
- Require review from Code Owners.
- Dismiss stale pull request approvals when new commits are pushed.
- Require conversation resolution before merging.
- Block force pushes.
- Block deletions.

## Required Status Checks

Require these checks before merging:

- `quality-python`
- `workbench-contract`
- `pr-governance`

The workflow includes `pull_request` and `merge_group` triggers so the same required checks can report for direct PR validation and merge queue validation.

Do not reuse these job names in another workflow. Required status checks become ambiguous when multiple workflows publish the same job name.

## Merge Queue

If merge queue is enabled:

- Require merge queue on `main`.
- Keep "Only merge non-failing pull requests" enabled.
- Use squash merge unless the release process needs another method.
- Start with a small maximum group size until the Workbench contract runtime is known.

## Owner Setup

- Verify every CODEOWNERS entry names a GitHub user or team with write access.
- Replace `@FrankQDWang` with a visible team after trusted maintainers exist.
- Re-check CODEOWNERS ownership in GitHub's file view after this file lands on `main`.
```

- [ ] **Step 5: Document contributor workflow**

Append this section to `docs/development.md` after "Contributor expectations":

````markdown
## AI Coding Governance

Before opening a non-trivial PR, read `docs/governance/ai-coding-policy.md`.

Repository owners should also keep `docs/governance/github-ruleset-checklist.md` in sync with the active branch protection or ruleset settings.

Use these local checks:

```bash
uv run python tools/check_pr_governance.py --base origin/main
uv run python tools/check_tach_baseline.py
```

For red-zone runtime, provider, prompt, config, CI, or Workbench persistence changes:

```bash
scripts/verify-red-zone.sh
```

For Workbench, BFF, OpenAPI, or Svelte changes:

```bash
scripts/verify-dev-workbench.sh
```
````

- [ ] **Step 6: Verify formatting**

Run:

```bash
git diff --check -- .github/CODEOWNERS .github/pull_request_template.md docs/governance/ai-coding-policy.md docs/governance/github-ruleset-checklist.md docs/development.md
```

Expected: no whitespace errors.

- [ ] **Step 7: Commit**

```bash
git add .github/CODEOWNERS .github/pull_request_template.md docs/governance/ai-coding-policy.md docs/governance/github-ruleset-checklist.md docs/development.md
git commit -m "chore: add ai coding governance contract"
```

---

### Task 2: Wire CI For Merge Queue Readiness And Workbench Contract

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify if generated drift appears: `apps/web-svelte/src/lib/api/schema.d.ts`

- [ ] **Step 1: Replace CI workflow with split jobs**

Replace `.github/workflows/ci.yml` with:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
  merge_group:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  quality-python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
      - name: Install uv
        uses: astral-sh/setup-uv@v8.1.0
        with:
          enable-cache: true
          cache-dependency-glob: uv.lock
      - name: Install dependencies
        run: uv sync --locked --group dev
      - name: Check architecture imports
        run: uv run --group dev python tools/check_arch_imports.py
      - name: Check Tach baseline
        run: uv run --group dev python tools/check_tach_baseline.py
      - name: Ruff
        run: uv run --group dev ruff check src tests experiments tools
      - name: Ty
        run: uv run --group dev ty check src tests
      - name: Pytest
        run: uv run --group dev python -m pytest -q
      - name: Minimize uv cache
        if: always()
        run: uv cache prune --ci

  workbench-contract:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
      - name: Install uv
        uses: astral-sh/setup-uv@v8.1.0
        with:
          enable-cache: true
          cache-dependency-glob: uv.lock
      - uses: oven-sh/setup-bun@v2
        with:
          bun-version: "1.3.13"
      - name: Install Python dependencies
        run: uv sync --locked --group dev
      - name: Install Svelte dependencies
        run: cd apps/web-svelte && bun install --frozen-lockfile
      - name: Install Playwright browsers
        run: cd apps/web-svelte && bunx playwright install --with-deps chromium
      - name: Run Workbench contract
        run: scripts/verify-dev-workbench.sh
      - name: Minimize uv cache
        if: always()
        run: uv cache prune --ci

  pr-governance:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
      - name: Install uv
        uses: astral-sh/setup-uv@v8.1.0
        with:
          enable-cache: true
          cache-dependency-glob: uv.lock
      - name: Install dependencies
        run: uv sync --locked --group dev
      - name: Run PR governance
        run: uv run --group dev python tools/check_pr_governance.py --base "origin/${GITHUB_BASE_REF:-main}"
```

- [ ] **Step 2: Validate workflow syntax by local whitespace check**

Run:

```bash
git diff --check -- .github/workflows/ci.yml
```

Expected: no whitespace errors.

- [ ] **Step 3: Run Workbench contract once on the clean branch**

Run the same command CI will run:

```bash
scripts/verify-dev-workbench.sh
```

Expected: pass. If it fails only because `apps/web-svelte/src/lib/api/schema.d.ts` changes after `bun run api:gen`, keep that generated schema update in this task and rerun the command.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml apps/web-svelte/src/lib/api/schema.d.ts
git commit -m "ci: add governance and workbench gates"
```

---

### Task 3: Add PR Size And Path Governance Script

**Files:**
- Create: `tools/check_pr_governance.py`
- Create: `tests/test_pr_governance.py`

- [ ] **Step 1: Add unit tests**

Create `tests/test_pr_governance.py` with:

```python
from tools.check_pr_governance import classify_path, evaluate_changed_files, merge_changed_file_sets


def test_classify_path_red_runtime() -> None:
    assert classify_path("src/seektalent/runtime/orchestrator.py") == "red"


def test_classify_path_red_provider_registry() -> None:
    assert classify_path("src/seektalent/providers/registry.py") == "red"


def test_classify_path_red_liepin_worker() -> None:
    assert classify_path("apps/liepin-worker/src/server.ts") == "red"


def test_classify_path_yellow_workbench_route() -> None:
    assert classify_path("src/seektalent_ui/workbench_routes.py") == "yellow"


def test_classify_path_green_docs() -> None:
    assert classify_path("docs/development.md") == "green"


def test_evaluate_changed_files_fails_cross_layer_runtime_and_frontend() -> None:
    result = evaluate_changed_files(
        [
            "src/seektalent/runtime/orchestrator.py",
            "apps/web-svelte/src/lib/components/SourceCard.svelte",
        ],
        max_files=15,
        max_layers=1,
    )

    assert not result.ok
    assert "cross-layer" in result.messages[0]


def test_evaluate_changed_files_allows_single_layer_tests() -> None:
    result = evaluate_changed_files(
        [
            "tests/test_runtime_state_flow.py",
            "tests/test_runtime_audit.py",
        ],
        max_files=15,
        max_layers=1,
    )

    assert result.ok


def test_evaluate_changed_files_reports_red_zone_without_blocking() -> None:
    result = evaluate_changed_files(
        ["src/seektalent/runtime/orchestrator.py"],
        max_files=15,
        max_layers=1,
    )

    assert result.ok
    assert result.red_files == ["src/seektalent/runtime/orchestrator.py"]
    assert "red-zone files touched" in result.messages[0]


def test_evaluate_changed_files_fails_too_many_non_generated_files() -> None:
    result = evaluate_changed_files(
        [f"docs/file_{index}.md" for index in range(16)],
        max_files=15,
        max_layers=1,
    )

    assert not result.ok
    assert "too many non-generated files changed" in result.messages[0]


def test_evaluate_changed_files_ignores_generated_schema() -> None:
    result = evaluate_changed_files(
        ["apps/web-svelte/src/lib/api/schema.d.ts"],
        max_files=0,
        max_layers=0,
    )

    assert result.ok


def test_evaluate_changed_files_does_not_exempt_schema_suffixes() -> None:
    result = evaluate_changed_files(
        ["apps/web-svelte/src/lib/api/schema.d.ts.tmp"],
        max_files=0,
        max_layers=0,
    )

    assert not result.ok
    assert "too many non-generated files changed" in result.messages[0]


def test_merge_changed_file_sets_includes_local_working_tree_files() -> None:
    assert merge_changed_file_sets(
        ["src/seektalent/runtime/orchestrator.py"],
        ["docs/development.md"],
        ["tools/check_pr_governance.py"],
        ["tests/test_pr_governance.py"],
    ) == [
        "docs/development.md",
        "src/seektalent/runtime/orchestrator.py",
        "tests/test_pr_governance.py",
        "tools/check_pr_governance.py",
    ]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_pr_governance.py -q
```

Expected: import failure because `tools/check_pr_governance.py` does not exist yet.

- [ ] **Step 3: Implement governance script**

Create `tools/check_pr_governance.py` with:

```python
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
    if path.startswith(".github/") or path.startswith("tools/") or path.startswith("scripts/"):
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
    layers = sorted({layer_for_path(path) for path in non_generated if layer_for_path(path) not in {"docs", "tests"}})
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

    result = evaluate_changed_files(changed_files(args.base), max_files=args.max_files, max_layers=args.max_layers)
    for message in result.messages:
        print(message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run focused tests and lint**

Run:

```bash
uv run pytest tests/test_pr_governance.py -q
uv run ruff check tools/check_pr_governance.py tests/test_pr_governance.py
uv run python tools/check_pr_governance.py --base origin/main --max-layers 6
```

Expected: tests and Ruff pass. The local governance command may print red-zone files. It exits zero only when the changed file count and layer count are within the configured limits; an already-oversized cleanup branch should fail here and be split before merge.

- [ ] **Step 5: Commit**

```bash
git add tools/check_pr_governance.py tests/test_pr_governance.py
git commit -m "ci: add pr governance gate"
```

---

### Task 4: Add Tach No-Regression Baseline

**Files:**
- Create: `tools/check_tach_baseline.py`
- Create: `tools/tach_baseline.json`
- Create: `tests/test_tach_baseline.py`

- [ ] **Step 1: Add parser tests**

Create `tests/test_tach_baseline.py` with:

```python
from tools.check_tach_baseline import compare_violations, extract_failures, normalize_failure


def test_normalize_failure_removes_line_numbers() -> None:
    assert (
        normalize_failure("[FAIL] src/a.py:123: Cannot use x")
        == "[FAIL] src/a.py: Cannot use x"
    )


def test_extract_failures_keeps_only_fail_lines() -> None:
    output = """Configuration
[WARN] ignored
Internal Dependencies
[FAIL] src/a.py:1: Cannot use x
[FAIL] src/b.py:2: Cannot use y
"""

    assert extract_failures(output) == [
        "[FAIL] src/a.py: Cannot use x",
        "[FAIL] src/b.py: Cannot use y",
    ]


def test_compare_violations_fails_on_new_failure() -> None:
    result = compare_violations(
        current=["[FAIL] src/a.py: Cannot use x", "[FAIL] src/b.py: Cannot use y"],
        baseline=["[FAIL] src/a.py: Cannot use x"],
    )

    assert result == ["[FAIL] src/b.py: Cannot use y"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_tach_baseline.py -q
```

Expected: import failure because `tools/check_tach_baseline.py` does not exist yet.

- [ ] **Step 3: Implement Tach baseline wrapper**

Create `tools/check_tach_baseline.py` with:

```python
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
```

- [ ] **Step 4: Seed current baseline**

Run:

```bash
uv run python tools/check_tach_baseline.py --write-current
```

Expected: `tools/tach_baseline.json` is written with the current Tach failures, normalized without line numbers.

- [ ] **Step 5: Run verification**

Run:

```bash
uv run pytest tests/test_tach_baseline.py -q
uv run python tools/check_tach_baseline.py
uv run ruff check tools/check_tach_baseline.py tests/test_tach_baseline.py
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add tools/check_tach_baseline.py tools/tach_baseline.json tests/test_tach_baseline.py
git commit -m "ci: add tach no-regression baseline"
```

---

### Task 5: Add Red-Zone Runtime Smoke Command

**Files:**
- Create: `scripts/verify-red-zone.sh`
- Modify: `docs/development.md`

- [ ] **Step 1: Create red-zone verification script**

Create `scripts/verify-red-zone.sh` with:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

uv run pytest \
  tests/test_runtime_state_flow.py \
  tests/test_runtime_audit.py \
  tests/test_runtime_source_lanes.py \
  tests/test_runtime_multi_source_round_dispatch.py \
  tests/test_workbench_runtime_graph.py \
  tests/test_workbench_runtime_owned_execution.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_worker_client.py \
  tests/test_cts_provider_adapter.py \
  tests/test_provider_registry.py \
  -q

uv run python tools/check_arch_imports.py
uv run python tools/check_tach_baseline.py

command -v bun >/dev/null 2>&1 || {
  echo "bun not found; red-zone Liepin worker verification requires Bun" >&2
  exit 1
}
(
  cd apps/liepin-worker
  bun install --frozen-lockfile
  bun run boundary-check
  bun run typecheck
  bun run test
)

git diff --check
```

- [ ] **Step 2: Make script executable**

Run:

```bash
chmod +x scripts/verify-red-zone.sh
```

- [ ] **Step 3: Document red-zone smoke**

In `docs/development.md`, ensure the AI Coding Governance section includes:

```markdown
`scripts/verify-red-zone.sh` is the focused smoke command for runtime, provider, prompt, config, CI, tools, and Workbench persistence changes. It does not replace the full PR gate; it gives red-zone reviewers a fast signal before broader CI finishes.
```

- [ ] **Step 4: Verify shell script**

Run:

```bash
bash -n scripts/verify-red-zone.sh
git diff --check -- scripts/verify-red-zone.sh docs/development.md
```

Expected: pass.

- [ ] **Step 5: Run focused smoke**

Run:

```bash
scripts/verify-red-zone.sh
```

Expected: pass before merging this governance branch. If an existing branch failure appears, record the failing test and do not weaken the command.

- [ ] **Step 6: Commit**

```bash
git add scripts/verify-red-zone.sh docs/development.md
git commit -m "test: add red-zone verification smoke"
```

---

### Task 6: Final Governance Verification

**Files:**
- No new files beyond prior tasks.

- [ ] **Step 1: Run governance unit tests**

Run:

```bash
uv run pytest tests/test_pr_governance.py tests/test_tach_baseline.py -q
```

Expected: pass.

- [ ] **Step 2: Run Python quality gate**

Run:

```bash
uv run python tools/check_arch_imports.py
uv run python tools/check_tach_baseline.py
uv run ruff check tools tests/test_pr_governance.py tests/test_tach_baseline.py
uv run ty check src tests
```

Expected: pass or expose pre-existing `ty` failures already present before this governance work. Do not add new ignores.

- [ ] **Step 3: Run Workbench contract gate**

Run:

```bash
scripts/verify-dev-workbench.sh
```

Expected: pass with Svelte dependencies installed and local backend smoke completed.

- [ ] **Step 4: Run red-zone smoke**

Run:

```bash
scripts/verify-red-zone.sh
```

Expected: pass.

- [ ] **Step 5: Run final diff checks**

Run:

```bash
uv run python tools/check_pr_governance.py --base origin/main --max-layers 6
git diff --check
```

Expected: governance script reports red-zone files touched by this governance branch but exits zero with the widened local layer allowance. `git diff --check` passes.

- [ ] **Step 6: Commit final docs or verification fixes**

If Step 1 through Step 5 required documentation or script fixes, commit only those files:

```bash
git add .github docs/governance docs/development.md tools tests scripts/verify-red-zone.sh
git commit -m "docs: finalize ai coding governance gate"
```

Skip this commit if there are no changes after the prior task commits.

---

## Self-Review Checklist

- Spec coverage:
  - CODEOWNERS covered by Task 1.
  - PR template covered by Task 1.
  - AI coding policy covered by Task 1.
  - GitHub ruleset checklist covered by Task 1.
  - CI `merge_group` covered by Task 2.
  - Workbench contract in CI covered by Task 2.
  - PR size/path gate covered by Task 3.
  - Tach baseline covered by Task 4.
  - Red-zone smoke covered by Task 5.
  - Developer docs covered by Tasks 1 and 5.
- Placeholder scan:
  - No step uses "fill in later" language.
  - Every code-writing step includes exact file content or exact snippet.
- Type consistency:
  - `GovernanceResult`, `classify_path`, and `evaluate_changed_files` names match tests.
  - `normalize_failure`, `extract_failures`, and `compare_violations` names match tests.

## GSTACK REVIEW REPORT

Decision: approve full scope after scope challenge option A, with required plan fixes applied before build.

Findings resolved in this review:

- CODEOWNERS alone is not an enforcement mechanism. The plan now adds `docs/governance/github-ruleset-checklist.md` so branch protection or rulesets require code owner review, required checks, and conversation resolution after the branch lands.
- Merge queue readiness depends on `merge_group` checks reporting. The CI plan already includes `merge_group`; the ruleset checklist now calls out that required checks must be configured against `quality-python`, `workbench-contract`, and `pr-governance`.
- Exact Tach output matching is too brittle because harmless line-number shifts would appear as new architecture violations. Task 4 now normalizes Tach failure line numbers before comparison.
- PR governance tests were under-specified for the important behavior. Task 3 now covers red-zone reporting, oversized PR failure, and generated schema exclusion.
- The red-zone smoke set missed Workbench runtime projection tests even though `runtime_graph.py` and `runtime_bridge.py` are red-zone paths. Task 5 now includes the focused Workbench runtime graph and owned-execution tests.

Remaining accepted tradeoffs:

- `workbench-contract` runs on every PR in the first implementation. This is intentionally conservative until CI runtime data says otherwise.
- `check_pr_governance.py` reports red-zone files but does not fail solely on red-zone touches. Owner enforcement belongs to CODEOWNERS plus the GitHub ruleset because local CI cannot know final reviewer identity.
- PR size enforcement starts with non-generated file count and path spread. Line-count enforcement can be added after the bootstrap gate lands, while large-line-change justification is handled in the PR template and policy.
- Release workflow hardening is left to a follow-up plan; this plan protects pull requests and merge queue readiness first.
