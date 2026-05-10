# Workbench Internal Rollout Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the post-M6 operator rollout gates into a repeatable local readiness command plus a concise runbook, without starting cloud, Storybook, or benchmark work.

**Architecture:** Keep this in the existing `seektalent-ui-maintenance` surface because M6 already put backup, restore, and workbench database integrity there. The new command runs automated local checks that are safe on a developer/operator machine, writes redacted readiness evidence under `.seektalent/rollout-readiness/`, and explicitly marks live LAN and real Liepin account checks as manual gates.

**Tech Stack:** Python 3.12, SQLite, existing `WorkbenchStore`, existing `seektalent-ui-maintenance`, pytest.

---

## File Structure

- Modify `src/seektalent_ui/maintenance.py`
  - Add `run_rollout_readiness()`.
  - Add `seektalent-ui-maintenance rollout-readiness`.
  - Reuse existing schema validation, backup, verify, restore, and read-path smoke behavior.
- Modify `tests/test_workbench_maintenance.py`
  - Add tests for successful readiness evidence generation and missing database failure.
- Modify `docs/ui.md`
  - Add the M7 readiness command to the internal rollout runbook.
- Modify `docs/superpowers/2026-05-09-multi-source-workbench-execution.md`
  - Add the M7 execution entry after implementation and verification.

## Scope Boundaries

- Do not automate real Liepin login or detail consumption in this milestone.
- Do not start the backend, Vite server, or Liepin worker from the readiness command.
- Do not store cookies, browser storage, provider payloads, raw resumes, candidate PII, auth headers, or CDP/Playwright internals in readiness evidence.
- Do not introduce Storybook.
- Do not start the static benchmark/search-engine work.

## Task 1: Automated Rollout Readiness Command

**Files:**
- Modify: `src/seektalent_ui/maintenance.py`
- Test: `tests/test_workbench_maintenance.py`

- [x] **Step 1: Write failing tests**

Add tests that create the existing workbench fixture, run `run_rollout_readiness()`, and verify:

- status is `manual_required`, because live LAN and real Liepin account checks are intentionally manual;
- workbench schema, backup, backup verify, and restore-to-temp checks pass;
- JSON and Markdown evidence files are written with restrictive permissions;
- evidence contains command guidance, not secrets or raw provider data;
- missing database exits with a clear failure.

- [x] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/test_workbench_maintenance.py -q
```

Expected: new tests fail because `run_rollout_readiness` and `rollout-readiness` do not exist yet.

- [x] **Step 3: Implement command**

Add a small readiness result model in `maintenance.py`:

- `ReadinessCheck(name, status, message, evidence)`
- `RolloutReadinessResult(status, report_path, markdown_path, checks)`

Implement `run_rollout_readiness(workspace_root: Path, output_dir: Path | None = None)`:

1. Resolve the workspace root and workbench database path.
2. Validate the workbench schema with `_validate_workbench_schema`.
3. Create a backup with `backup_workbench`.
4. Verify the backup with `verify_backup`.
5. Restore the backup into a temporary workspace and run a read-path smoke through `WorkbenchStore`.
6. Add manual-required checks for:
   - real-device LAN access;
   - real Liepin login relay;
   - real provider account budget/detail behavior.
7. Write JSON and Markdown reports under `.seektalent/rollout-readiness/` or the supplied output directory.
8. Use `0o700` for the report directory and `0o600` for report files.

Wire parser command:

```bash
uv run seektalent-ui-maintenance rollout-readiness --workspace-root .
```

Return exit code `0` when automated checks pass even if manual gates remain; return `1` when an automated check fails.

- [x] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/test_workbench_maintenance.py -q
uv run seektalent-ui-maintenance --help
```

Expected: maintenance tests pass and help lists `rollout-readiness`.

## Task 2: Operator Runbook And Execution Log

**Files:**
- Modify: `docs/ui.md`
- Modify: `docs/superpowers/2026-05-09-multi-source-workbench-execution.md`

- [x] **Step 1: Update docs**

Add an "Internal Rollout Readiness" section to `docs/ui.md` with:

```bash
uv run seektalent-ui-maintenance rollout-readiness --workspace-root .
```

Explain that this command validates local durable state, backup/verify/restore, and readiness evidence, but still requires a human to perform live LAN and real Liepin login checks before business use.

- [x] **Step 2: Update execution log**

Append an M7 section to `docs/superpowers/2026-05-09-multi-source-workbench-execution.md` after all implementation and verification commands have passed.

## Task 3: Verification

**Files:**
- No code changes unless verification exposes a defect.

- [x] **Step 1: Backend verification**

Run:

```bash
uv run pytest tests/test_workbench_maintenance.py tests/test_workbench_security_audit.py -q
uv run ruff check src/seektalent_ui/maintenance.py tests/test_workbench_maintenance.py
uv run ty check src/seektalent_ui/maintenance.py
uv run seektalent-ui-maintenance --help
```

- [x] **Step 2: Workbench regression smoke**

Run:

```bash
uv run pytest tests/test_workbench_api.py tests/test_workbench_auth_security.py tests/test_workbench_network_guard.py tests/test_ui_api.py tests/test_ui_mapper.py -q
```

- [x] **Step 3: Diff hygiene**

Run:

```bash
git diff --check
git status --short
```

## Self-Review

- Spec coverage: This plan addresses the only post-M6 gates that can be automated without consuming real provider budget or requiring another physical device.
- Placeholder scan: No implementation placeholder is allowed in committed docs; manual gates must be labeled as manual gates, not claimed as automated.
- Type consistency: The new command stays in `maintenance.py`; no new service layer is introduced.
