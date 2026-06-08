# Acceptance Criteria

## Product Acceptance

1. The new-session screen supports natural-language intake as the primary flow.
2. The system can ask clarifying questions.
3. The system can produce a recruiter-facing confirmation.
4. The user can edit `jobTitle`, `jdText`, `notes`, and catalog-backed source selection before confirming.
5. Confirmation creates exactly one Workbench session.
6. Confirmation starts requirement preparation through the existing Workbench path.
7. The existing requirement review and runtime start flow remain intact.
8. No runtime refactor is included.
9. After workflow start, the conversation can answer progress/result questions from read-only Workbench state.
10. Initial version does not mutate in-progress runtime workflow state.
11. Source selection is based on the current Workbench source catalog or registry-facing API, not a fixed CTS/Liepin source universe.

## Codex Isolation Acceptance

1. `CODEX_HOME` resolves under `.seektalent/codex_home`.
2. Codex memory resolves under `.seektalent/codex_home/memories`.
3. No code path reads or writes `~/.codex`.
4. Tests fail if `CODEX_HOME` is missing or resolves to `~/.codex`.
5. Codex cwd resolves to `.seektalent/codex_workspace`, not the repository root.
6. The generated Codex config does not contain `requires_openai_auth = true`.
7. The default credential env key is `SEEKTALENT_TEXT_LLM_API_KEY`.

## Provider Acceptance

1. OpenAI API is not required for default local operation.
2. DashScope/OpenAI-compatible config is generated or validated.
3. A provider smoke check exists.
4. Missing credentials produce `provider_not_configured`.
5. Provider incompatibility produces `provider_smoke_failed`.
6. The app never silently switches to OpenAI.

## Codex App Server Acceptance

1. Integration uses Codex App Server directly.
2. Integration does not use the Codex SDK.
3. Missing or incompatible App Server produces `codex_harness_unavailable`.
4. Tests use a fake App Server transport without requiring a real Codex binary.

## Commercial Packaging Acceptance

1. SeekTalent can call Codex App Server locally without requiring a SaaS control plane.
2. The implementation documents whether Codex artifacts are redistributed or operator-installed.
3. Redistributed Codex artifacts retain the applicable Apache-2.0 license notice.
4. Product packaging excludes `~/.codex`.
5. Product packaging excludes Codex auth state.
6. Product packaging excludes Codex memory directories and thread history.
7. Product packaging may include project-local Codex config templates only if they do not contain credentials.

## API Acceptance

Required routes:

```text
POST /api/intake/conversations
GET  /api/intake/conversations/{conversation_id}
POST /api/intake/conversations/{conversation_id}/messages
PUT  /api/intake/conversations/{conversation_id}/draft
POST /api/intake/conversations/{conversation_id}/confirm
GET  /api/intake/source-catalog
POST /api/intake/codex-smoke
POST /api/intake/memory/reset
```

All mutation routes require CSRF user auth.

## State Acceptance

Tests must cover:

- create conversation;
- append user message;
- assistant clarification;
- assistant draft;
- edit draft;
- confirm latest draft;
- reject stale draft confirmation;
- duplicate confirm returns existing Workbench session;
- Codex failure records named error;
- Workbench handoff failure records named error.

## Frontend Acceptance

Tests must cover:

- initial empty intake page;
- sending a message;
- showing clarification;
- showing confirmation;
- editing the confirmation;
- confirming and navigating to `/sessions/{sessionId}`;
- upward-scrolling transcript behavior;
- progress question after session creation;
- read-only workflow progress answer;
- provider not configured state;
- stale confirmation state.

## Verification Commands

Focused Python tests:

```bash
uv run --group dev python -m pytest \
  tests/test_intake_paths.py \
  tests/test_intake_store.py \
  tests/test_intake_codex_config.py \
  tests/test_intake_codex_app_server.py \
  tests/test_intake_source_catalog.py \
  tests/test_intake_service.py \
  tests/test_intake_workbench_bridge.py \
  tests/test_intake_workflow_reader.py \
  tests/test_workbench_api.py \
  -q
```

Python lint:

```bash
uv run --group dev ruff check src tests
```

Frontend checks:

```bash
cd apps/web-svelte
bun run check
bun run lint
bun run test
bun run build
```

Workbench integration gate:

```bash
scripts/verify-dev-workbench.sh
```

If red-zone paths are touched:

```bash
scripts/verify-red-zone.sh
```

## Forbidden Final State

The work is not accepted if any of these are true:

- implementation requires OpenAI API by default;
- implementation touches `~/.codex`;
- implementation packages `~/.codex`, Codex auth state, Codex memory, or Codex thread history;
- implementation redistributes Codex artifacts without the applicable Apache-2.0 notice;
- implementation uses the Codex SDK;
- implementation modifies runtime internals;
- implementation mutates in-progress runtime workflow state in the initial version;
- implementation treats `cts` and `liepin` as the complete future source universe instead of current catalog entries;
- confirmation creates duplicate Workbench sessions;
- Codex memory is the only place where draft data exists;
- provider smoke is absent;
- frontend has no failure state for provider/config errors;
- tests use only mocked frontend behavior without backend coverage;
- generated OpenAPI schema is stale.
