# GitHub Ruleset Checklist

This file records the repository settings to use only when re-enabling a protected PR path. The current fast direct-main workflow keeps governance advisory instead of required.

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

For the fast direct-main workflow, keep only these checks as required if a ruleset is enabled:

- `quality-python`

`quality-python` is the only automatic code gate. It runs one short job for static quality, architecture imports, Workbench schema consistency, and privacy/agent-safety diff scans. It intentionally excludes pytest and frontend verification.

Do not require `workbench-contract`, `pr-governance`, or CodeQL for fast direct-main iteration. Workbench Contract and Governance are manual-only; CodeQL is weekly or manual.

If the existing `main` protection still requires the legacy `test` status, remove that requirement after this governance branch lands. `quality-python` is now the stable Python aggregate check.

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
