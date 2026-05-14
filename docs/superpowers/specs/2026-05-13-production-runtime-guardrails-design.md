# Production Runtime Guardrails Design

## Purpose

SeekTalent has grown into a production-grade Python + TypeScript agent runtime. The next refactor should not be an aesthetic rewrite. It should turn engineering quality into executable constraints: typed boundaries, artifact replay, CI guardrails, structured errors, secret/privacy scanning, and technical-debt budgets that can only shrink.

## Product Contract

The runtime must be auditable and safe under local production use:

- external inputs are schema-validated;
- LLM outputs are typed-parsed;
- workflow stages have typed inputs, typed outputs, and versioned artifacts;
- errors are classified and explainable;
- raw provider data stays behind protected snapshot boundaries;
- dangerous operations have preview/confirmation/recovery;
- CI is the same gate developers run locally.

## Current Code Facts

- Python config already uses Pydantic settings and many Pydantic models.
- TypeScript web and worker apps already use strict TypeScript configurations.
- CI currently runs architecture import guard, Ruff, ty, and pytest.
- `docs/development.md` documents Ruff, ty, Tach, Python tests, frontend tests, and worker checks.
- `docs/ui.md` documents backend, frontend, visual, and Liepin worker verification commands.
- Existing tests already cover many Liepin privacy and boundary rules.
- There is no single repo-level `verify` command that covers Python, frontend, worker, visual, secret/privacy, and guard checks together.

## Decisions

1. Add one canonical local verification entrypoint.
2. Treat guardrail tests as product code, not optional lint.
3. Add allowlists only where needed, and make allowlists shrink-only.
4. Add schema/version checks for artifacts and workflow stage payloads.
5. Add structured error envelopes at CLI, API, worker, and runtime boundaries.
6. Add secret and privacy scans to CI before broader refactors.
7. Use Tach as an architecture radar first, then promote selected dependency rules to required gates.
8. CI must not silently skip frontend or Liepin worker checks. Local developer convenience modes may be explicit, but the shared gate must fail when required runtimes are unavailable.

## Non-Goals

- No broad rewrite before guardrails exist.
- No new framework layer just to look organized.
- No forced conversion of every internal dict to Pydantic.
- No replacing SQLite with Postgres in this slice.
- No attempt to fix every historical maintenance note at once.

## Guardrail Categories

### Type Guardrails

- Python: reduce `dict[str, Any]` in runtime/business paths.
- TypeScript: forbid `any` in business UI and worker boundaries unless allowlisted.
- LLM output: typed parse with bounded retry only for schema/parse failures.
- External input: Pydantic or Zod validation before use.

### Artifact Guardrails

- Every workflow stage artifact has schema version.
- Raw LLM/provider responses are either protected artifacts or explicitly absent with reason.
- Artifact refs include content hash and logical artifact name.
- Replay fixtures prove old artifacts remain readable.

### Error Guardrails

- Stable `code`, user-safe message, hint, stage, and context.
- Preserve cause internally without exposing secrets.
- Distinguish config, provider, auth, data, invariant, budget, and user-action errors.

### CI Guardrails

- Python lint/type/test.
- Frontend test/type/build.
- Liepin worker test/type/boundary.
- Architecture import guard.
- Secret scan.
- Privacy scan.
- Artifact compatibility scan.
- Guard allowlist check.

## Acceptance Criteria

- `scripts/verify-all.sh` or equivalent runs the canonical local gate.
- CI invokes the same gate or the same ordered subcommands.
- CI installs Bun and runs frontend plus Liepin worker gates; missing Bun is a setup failure in CI, not a skipped check.
- New guard tests cover swallowed exceptions, raw provider leakage, legacy env keys, artifact schema versions, and allowlist shrink-only behavior.
- Documentation tells developers which command is authoritative.
- The first guarded refactor lands only after these checks exist.
