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

The single-developer workflow has no required automatic Python status check. `scripts/verify-local-quality.sh` is the publishing gate, and `Python Quality` remains available only through manual dispatch.

The path-filtered `Native launch-binding probe` supplies Windows x64 and macOS Intel evidence for relevant delivery changes. Do not configure it as an unconditional required check because unrelated pull requests do not trigger it.

Do not require `quality-python`, `workbench-contract`, `pr-governance`, or CodeQL for fast direct-main iteration. Python Quality, Workbench Contract, and Governance are manual-only; CodeQL is weekly or manual.

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
