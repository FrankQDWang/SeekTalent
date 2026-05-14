# Production Runtime Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add executable production guardrails before broad refactors: one verify gate, structured error contracts, artifact/version checks, privacy/secret scans, and shrink-only debt allowlists.

**Architecture:** Start with guard tests and scripts that observe the current repository, then promote them into CI. Keep the checks narrow and explicit so they catch real regressions without forcing a large code rewrite in this slice.

**Tech Stack:** Python 3.12, Bash, pytest, Ruff, ty, Tach, Bun, TypeScript, GitHub Actions, optional gitleaks when installed.

**Spec:** `docs/superpowers/specs/2026-05-13-production-runtime-guardrails-design.md`

---

## File Structure

- Add: `scripts/verify-all.sh`
  - Canonical local verification gate.
- Add: `tools/check_guardrails.py`
  - Repo guard checks for allowlists, legacy env keys, artifact schema markers, and unsafe patterns.
- Add: `tools/guardrail_allowlist.json`
  - Current debt counts with shrink-only policy.
- Modify: `.github/workflows/ci.yml`
  - Invoke canonical gate or equivalent ordered checks.
- Modify: `docs/development.md`
  - Document the single verification command.
- Test: `tests/test_guardrails.py`
  - Unit tests for guardrail helper functions.

## Task 1: Add Canonical Verify Script

**Files:**

- Add: `scripts/verify-all.sh`
- Modify: `docs/development.md`

- [ ] **Step 1: Create script**

  Add:

  ```bash
  #!/usr/bin/env bash
  set -euo pipefail

  uv sync --group dev
  uv run --group dev python tools/check_arch_imports.py
  uv run --group dev ruff check src tests experiments
  uv run --group dev ty check src tests
  uv run --group dev python tools/check_guardrails.py
  uv run --group dev python -m pytest -q

  if [[ "${SEEKTALENT_VERIFY_PYTHON_ONLY:-0}" == "1" ]]; then
    echo "SEEKTALENT_VERIFY_PYTHON_ONLY=1; skipped frontend and liepin-worker checks by explicit local override" >&2
    exit 0
  fi

  command -v bun >/dev/null 2>&1 || {
    echo "bun not found; frontend and liepin-worker checks are required" >&2
    exit 1
  }

  (cd apps/web && bun run test && bun run typecheck && bun run build)
  (cd apps/liepin-worker && bun run test && bun run typecheck && bun run boundary-check)
  ```

- [ ] **Step 2: Make executable**

  Run:

  ```bash
  chmod +x scripts/verify-all.sh
  ```

- [ ] **Step 3: Update docs**

  In `docs/development.md`, add:

  ```markdown
  Run the canonical local verification gate:

  ```bash
  ./scripts/verify-all.sh
  ```
  ```

- [ ] **Step 4: Run shell syntax check**

  ```bash
  bash -n scripts/verify-all.sh
  git diff --check -- scripts/verify-all.sh docs/development.md
  ```

  Expected: pass.

- [ ] **Step 5: Add skip-behavior test**

  Add a shell-level test or documented manual check proving the default script fails when `bun` is unavailable and only skips frontend/worker checks when `SEEKTALENT_VERIFY_PYTHON_ONLY=1` is set.

- [ ] **Step 6: Commit**

  ```bash
  git add scripts/verify-all.sh docs/development.md
  git commit -m "chore: add canonical verify gate"
  ```

## Task 2: Add Guardrail Checker

**Files:**

- Add: `tools/check_guardrails.py`
- Add: `tools/guardrail_allowlist.json`
- Test: `tests/test_guardrails.py`

- [ ] **Step 1: Add tests**

  Add:

  ```python
  from tools.check_guardrails import count_forbidden_patterns


  def test_guard_counts_broad_exception_pass() -> None:
      text = "try:\n    run()\nexcept Exception:\n    pass\n"

      counts = count_forbidden_patterns({"example.py": text})

      assert counts["swallowed_exception"] == 1


  def test_guard_counts_legacy_env_key() -> None:
      text = "SEEKTALENT_CONTROLLER_MODEL=openai:gpt\n"

      counts = count_forbidden_patterns({"README.md": text})

      assert counts["legacy_env_key"] == 1
  ```

- [ ] **Step 2: Run failing tests**

  ```bash
  uv run pytest tests/test_guardrails.py -q
  ```

  Expected: import failure.

- [ ] **Step 3: Implement checker helpers**

  Implement `count_forbidden_patterns(files: Mapping[str, str]) -> dict[str, int]` for:

  - `except Exception: pass`;
  - removed legacy text LLM env keys;
  - `type: ignore`;
  - `Any` in `src/seektalent/runtime`;
  - raw provider payload phrases in UI code.

- [ ] **Step 4: Add CLI behavior**

  `python tools/check_guardrails.py` should scan tracked source files, compare counts to `tools/guardrail_allowlist.json`, fail when counts exceed the allowlist, and pass when counts are equal or lower.

- [ ] **Step 5: Seed allowlist**

  Run:

  ```bash
  uv run python tools/check_guardrails.py --write-current
  ```

  Expected: writes current counts to `tools/guardrail_allowlist.json`.

- [ ] **Step 6: Run verification**

  ```bash
  uv run pytest tests/test_guardrails.py -q
  uv run python tools/check_guardrails.py
  uv run ruff check tools/check_guardrails.py tests/test_guardrails.py
  ```

  Expected: pass.

- [ ] **Step 7: Commit**

  ```bash
  git add tools/check_guardrails.py tools/guardrail_allowlist.json tests/test_guardrails.py
  git commit -m "test: add production guardrail checker"
  ```

## Task 3: Wire CI To The Canonical Gate

**Files:**

- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/publish-pypi.yml`

- [ ] **Step 1: Replace duplicated Python check block**

  In both workflows, replace the inline Python checks with:

  ```yaml
  - name: Run checks
    run: ./scripts/verify-all.sh
  ```

- [ ] **Step 2: Add Bun availability to CI**

  Add Bun setup before the verify step in both workflows:

  ```yaml
  - uses: oven-sh/setup-bun@v2
    with:
      bun-version: "1.3.13"
  ```

  CI must not set `SEEKTALENT_VERIFY_PYTHON_ONLY=1`.

- [ ] **Step 3: Validate workflow syntax locally**

  Run:

  ```bash
  git diff --check -- .github/workflows/ci.yml .github/workflows/publish-pypi.yml
  ```

  Expected: no whitespace errors.

- [ ] **Step 4: Commit**

  ```bash
  git add .github/workflows/ci.yml .github/workflows/publish-pypi.yml
  git commit -m "ci: use canonical verify gate"
  ```

## Task 4: Add Structured Error Contract Tests

**Files:**

- Add: `src/seektalent/errors.py`
- Test: `tests/test_errors.py`

- [ ] **Step 1: Add tests**

  Add:

  ```python
  from seektalent.errors import ErrorCode, UserSafeError


  def test_user_safe_error_payload_has_code_message_hint_and_context() -> None:
      error = UserSafeError(
          code=ErrorCode.CONFIG_MISSING,
          message="Missing provider configuration.",
          hint="Run seektalent doctor.",
          context={"stage": "doctor"},
      )

      payload = error.to_payload()

      assert payload["code"] == "config_missing"
      assert payload["hint"] == "Run seektalent doctor."
      assert payload["context"]["stage"] == "doctor"
  ```

- [ ] **Step 2: Run failing test**

  ```bash
  uv run pytest tests/test_errors.py -q
  ```

  Expected: import failure.

- [ ] **Step 3: Implement minimal error types**

  Add `ErrorCode` enum and `UserSafeError` dataclass. Do not migrate all existing errors in this task.

- [ ] **Step 4: Run tests**

  ```bash
  uv run pytest tests/test_errors.py -q
  ```

  Expected: pass.

- [ ] **Step 5: Commit**

  ```bash
  git add src/seektalent/errors.py tests/test_errors.py
  git commit -m "feat: add user safe error contract"
  ```

## Self-Review

- Spec coverage: verify gate, CI, allowlists, structured errors, and guard tests are covered.
- Placeholder scan: all checks and files are named.
- Type consistency: guard count names appear in tests before checker use.
