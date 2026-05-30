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

If the existing `main` protection still requires the legacy `test` status, remove that requirement after this governance branch lands. The workflow keeps `test` as a transitional aggregate so this branch can satisfy the current protection without administrator bypass.

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
