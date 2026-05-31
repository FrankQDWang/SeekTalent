# Docs

Use this file as the docs entrypoint. Code, tests, generated contracts, and verification scripts are the source of truth when a doc disagrees with the repository.

## Active Docs

- [README.md](../README.md): product shape, install, quick start, and main CLI usage.
- [cli.md](cli.md): CLI command contract.
- [configuration.md](configuration.md): environment variables and runtime configuration.
- [outputs.md](outputs.md): run artifacts and diagnostics.
- [ui.md](ui.md): local Workbench operation and verification.
- [development.md](development.md): contributor commands and repository conventions.
- [architecture.md](architecture.md): current component map and runtime sequence.

## Governance Docs

- [governance/ai-coding-policy.md](governance/ai-coding-policy.md): PR size, risk zones, and verification rules.
- [governance/github-ruleset-checklist.md](governance/github-ruleset-checklist.md): GitHub branch/ruleset settings.

## Historical Material

Files under `docs/archive/`, `docs/superpowers/`, `docs/plans/`, and `docs/v-*` are historical unless a current plan explicitly revives them. Do not use them as current product or architecture truth.

Markdown under `src/seektalent/prompts/` is runtime prompt material, not documentation.

## Doc Rule

Do not add a new doc unless it is release-facing, required by a governance gate, or needed as a short-lived implementation plan. Delete stale docs when possible; archive only when the file preserves external coordination, product decisions, or review evidence.
