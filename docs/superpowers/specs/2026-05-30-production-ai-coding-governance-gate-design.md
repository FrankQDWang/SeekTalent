# Production AI Coding Governance Gate Design

## Summary

SeekTalent is moving from fast solo AI coding into production and multi-person AI-assisted development. The first productization move should not be a physical repo split or broad architecture rewrite. It should make `main` hard to corrupt.

This design adds a merge-time governance gate for AI-coded changes:

- critical paths are owned;
- pull requests declare risk, scope, touched contracts, and verification;
- repository ruleset settings are documented so CODEOWNERS and required checks become real merge gates;
- CI runs the Workbench contract that already catches real product regressions;
- PR size and path spread are checked by machine;
- architecture drift is baselined before it is enforced;
- red-zone runtime changes have a focused replay smoke command.

The goal is not to make the codebase beautiful in one pass. The goal is to stop uncontrolled changes from entering production while preserving delivery speed.

## Problem

The current repository already has the right product direction: local-first recruiter workbench, deterministic runtime orchestration, bounded LLM structured output, explicit provider lanes, persisted artifacts, and local privacy boundaries.

The risk has shifted:

- multiple AI workers can edit runtime, providers, BFF, frontend, prompts, config, and tests at the same time;
- a PR can be syntactically green while violating product semantics;
- big stacked PRs can hide cross-layer changes;
- reviewers may not have time to read every diff;
- low-cost models can make plausible but unsafe changes in high-risk files.

If the project goes to production with only the current lightweight gate, code rot will not show up as one obvious failure. It will show up as drift: fallback growth, config/env behavior mismatch, raw provider data leakage, stale Workbench projections, prompt-only behavior changes, or runtime contracts that stop meaning the same thing.

## Current Code Facts

- `docs/architecture.md` says CLI, Python API, and local UI API converge on `WorkflowRuntime`.
- `src/seektalent/runtime/orchestrator.py` is the largest runtime integration surface.
- `src/seektalent_ui/workbench_routes.py`, `src/seektalent_ui/server.py`, and `src/seektalent_ui/workbench_store.py` are not a thin BFF yet.
- `.github/workflows/ci.yml` runs Python architecture import checks, Ruff, ty, and pytest.
- `scripts/verify-dev-workbench.sh` already runs stronger Workbench verification, including Python semantic tests, OpenAPI schema drift, Svelte check/lint/test/build/e2e, real backend smoke, legacy-copy scans, and `git diff --check`.
- `tools/check_arch_imports.py` only prevents core `src/seektalent` imports from `seektalent_ui` or `experiments`.
- `tach.toml` exists as an architecture radar, but `uv run tach check` currently reports known dependency drift.
- `pyproject.toml` ignores all ty rules for `tests/**`.
- There is no `.github/CODEOWNERS`.
- There is no pull request template.
- There is no PR size/path/risk gate.
- GitHub Actions does not currently include a `merge_group` trigger for merge queue readiness.

## Product Contract

After this work, every non-trivial AI-coded change must answer these questions before it can merge:

1. What risk zone did it touch?
2. Which paths were allowed for this task?
3. Which contracts changed?
4. Which checks prove behavior stayed intact?
5. Did it cross too many layers or grow too large?
6. Did it touch red-zone files that require owner approval and focused verification?

The contract is intentionally mechanical. It does not assume humans will read every line.

## Risk Zones

### Red Zone

Only the owner or a trusted maintainer should merge changes here:

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

### Yellow Zone

Assignable, but requires contract tests and Workbench verification:

- `src/seektalent_ui/server.py`
- `src/seektalent_ui/workbench_routes.py`
- `src/seektalent_ui/models.py`
- `src/seektalent_ui/job_runner.py`
- `src/seektalent_ui/*projection*.py`
- OpenAPI schema and generated API consumer files
- Workbench graph, candidate, note, and source-card projections

### Green Zone

Lower risk, still verified:

- isolated `apps/web-svelte` display components
- frontend unit and e2e tests
- docs
- fixtures
- pure display mapping
- black-box regression tests

Green-zone work must not touch security copy, provider boundaries, raw data handling, or runtime contracts without becoming yellow or red.

## Decisions

1. Do not physically split the repo in this slice.
2. Do not restructure `WorkflowRuntime` in this slice.
3. Do not split `seektalent_ui` into a separate repo in this slice.
4. Add ownership, PR template, CI wiring, and PR governance before refactoring.
5. Promote `scripts/verify-dev-workbench.sh` into CI before broad Workbench changes continue.
6. Add `merge_group` to CI so a future merge queue can run required checks.
7. Treat Tach as no-regression baseline first, not a clean hard gate.
8. Create a small PR governance script before introducing broad Semgrep/security gates.
9. Define a red-zone smoke command before letting non-owner AI workers touch runtime/provider paths.
10. Document the exact GitHub ruleset settings required to make CODEOWNERS and required checks enforceable.
11. Leave stricter Ruff, ty, Semgrep, secret scanning, dependency audit, and replay expansion to follow-up plans after this gate lands.

## Target Architecture

```text
AI worker or human contributor
-> issue/spec with allowed paths and non-goals
-> branch / stacked PR
-> PR template declares risk, paths, contracts, commands
-> CODEOWNERS requests owner review for red-zone paths
-> GitHub ruleset requires pull request review, code owner review, required checks, and conversation resolution
-> CI quality-python
-> CI workbench-contract when Workbench paths are touched or always in required gate
-> CI pr-governance
-> optional merge queue runs the same checks through merge_group
-> squash merge to main only after checks and owner gate pass
```

## Acceptance Criteria

- `.github/CODEOWNERS` exists and covers red-zone paths.
- `.github/pull_request_template.md` requires risk class, allowed paths, touched contracts, generated files, large change justification, AI execution metadata, invariants, verification, and rollback.
- `docs/governance/ai-coding-policy.md` describes red/yellow/green zones, AI model permissions, PR size thresholds, forbidden cross-layer edits, and required verification.
- `docs/governance/github-ruleset-checklist.md` describes the required repository settings that turn CODEOWNERS, status checks, and merge queue readiness into an enforced gate.
- `.github/workflows/ci.yml` includes `merge_group`.
- CI includes a Workbench contract job that runs `scripts/verify-dev-workbench.sh` after installing Python and Svelte dependencies.
- Any generated OpenAPI schema drift exposed by the new Workbench contract is committed with the governance gate.
- `tools/check_pr_governance.py` fails oversized or cross-layer PRs using deterministic path classification.
- `tools/check_pr_governance.py` treats red-zone paths as high-risk and prints the exact red files touched.
- `tools/check_tach_baseline.py` records current Tach violations and fails only on new violations.
- `scripts/verify-red-zone.sh` runs the focused runtime/provider smoke test set for red-zone changes.
- `docs/development.md` documents the new governance commands.
- The new checks are covered by tests and can run locally without GitHub-only state.

## Non-Goals

- No broad runtime rewrite.
- No BFF physical split.
- No frontend repo split.
- No change to product behavior, Workbench UI behavior, provider behavior, runtime scoring, prompts, or finalization.
- No full Semgrep/security/dependency audit implementation in this slice.
- No attempt to make Tach clean in one pass.
- No attempt to remove all `Any`, `type: ignore`, broad exceptions, or test typing debt in one pass.
- No merge queue enablement through GitHub settings automation. The repo owner configures the ruleset after this code lands using the checklist added by this slice.

## Plan Review Decisions

- `scripts/verify-dev-workbench.sh` should be required on every PR in the first implementation. If CI cost becomes painful, path-based optimization can be planned later without weakening the first production gate.
- `tools/check_pr_governance.py` should report red-zone files but not fail solely for red-zone touches. GitHub CODEOWNERS plus the documented ruleset enforces owner approval; local CI cannot reliably know final reviewer identity.
- Tach baseline comparison should normalize line numbers before comparing failures so harmless line shifts do not look like new architecture violations.
- Release workflow hardening remains a follow-up plan. This slice focuses on pull requests and merge queue readiness.
