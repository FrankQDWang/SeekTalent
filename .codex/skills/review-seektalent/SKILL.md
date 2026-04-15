---
name: review-seektalent
description: Review-only inspection for this SeekTalent repository. Use when Codex needs to inspect project code against the repo-root AGENTS.md rules, especially for dead code, stale compatibility paths, fallback or retry chains, defensive clutter, abstraction bloat, unused models or wrappers, or other local overengineering in src/, tests/, and root Python/config files, and must return findings plus cleanup suggestions without editing files.
---

# Review SeekTalent

## Core Rules

1. Read repo-root `AGENTS.md` first and treat it as the primary style authority.
2. Stay in review mode. Do not edit files.
3. Prefer deletion-oriented recommendations over additive refactors.
4. Treat compatibility code as suspicious by default. Require a current, concrete reason to keep it.
5. Treat "might be useful later" as no justification.

## Default Scope

Review these paths unless the user narrows or expands scope:

- `src/`
- `tests/`
- `pyproject.toml`
- root-level Python/config entry points tied to runtime behavior

Ignore noise unless the user explicitly asks for it:

- `dist/`
- `runs/`
- `.venv/`
- `.tmp-seektalent-venv/`
- `.pytest_cache/`
- `.idea/`
- `.obsidian/`
- `__pycache__/`
- generated artifacts

## What to Hunt

Prioritize findings in this order:

1. Dead code
   - Unused modules, functions, classes, constants, and one-off wrappers
   - Branches that cannot be reached or are no longer called
   - Duplicate paths where one path is clearly canonical
2. Compatibility bloat
   - Backward-compat branches with no active caller
   - Multi-path parsing or execution kept only "just in case"
   - Adapter layers that preserve old shapes without present need
3. Defensive clutter
   - Fallback chains
   - Retry logic outside the AGENTS.md structured-output exception
   - Swallowed exceptions
   - Validation theater and broad guard rails with no real bug behind them
4. Abstraction bloat
   - Helpers, managers, base classes, or protocol layers without clear payoff
   - Pass-through Pydantic models or wrappers that add no domain meaning
   - Functions extracted only to look clean while harming readability
5. Test drag
   - Tests that mainly protect obsolete behavior or compatibility ballast
   - Test structure that mirrors unnecessary indirection instead of real behavior

## Review Standard

- Prefer the smallest explanation that proves a cleanup is warranted.
- Use local evidence. Check references with `rg` before calling something dead.
- Separate "unnecessary" from "incorrect". This skill is mainly for the former.
- If a path is ugly but currently required, say so instead of forcing a deletion claim.
- Do not recommend framework-shaped rewrites, architecture resets, or speculative future-proofing.

## Output Contract

Start with findings. Each finding should include:

- file or files involved
- what looks deletable or unjustified
- why it conflicts with `AGENTS.md`
- concrete evidence such as missing callers, duplicated flow, or obsolete compatibility behavior
- the smallest cleanup suggestion

After findings, optionally include:

- open questions that block a deletion decision
- a short cleanup order if there are many findings

If no meaningful issues are found, say so plainly and mention any remaining uncertainty.

## Example Requests

- `Use $review-seektalent to inspect src/ for dead wrappers and compatibility branches.`
- `Use $review-seektalent to review recent changes for code bloat without modifying anything.`
- `Use $review-seektalent to find deletable abstractions in the runtime flow.`
