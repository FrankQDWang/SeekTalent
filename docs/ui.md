# UI

SeekTalent now includes a local-first internal recruiter workbench for scoped users, JD sessions, CTS + Liepin source cards, requirement triage, candidate review, SSE progress, and Liepin detail-open approval.

It is an internal business tool, not a public SaaS surface. Business users only need a browser on the same trusted network. They do not install Node.js, Bun, Playwright, browser extensions, or provider plugins.

## Components

- Backend API script: `seektalent-ui-api`
- Frontend app: `apps/web`
- Default backend address: `http://127.0.0.1:8011`
- Default frontend address: `http://127.0.0.1:5176`
- Workbench SQLite path: `.seektalent/workbench.sqlite3` under the configured workspace root, or the current working directory when no workspace root is configured

## Loopback Startup

Default startup binds the backend to loopback only:

```bash
uv run seektalent-ui-api
```

In another terminal:

```bash
cd apps/web
bun install
bun run dev
```

Open:

```text
http://127.0.0.1:5176
```

The Vite dev server proxies `/api` to `http://127.0.0.1:8011`.

## LAN Startup

LAN mode is explicit. A non-loopback bind without `--lan` or `SEEKTALENT_UI_LAN=1` is rejected.

Example for a trusted WiFi LAN:

```bash
uv run seektalent-ui-api \
  --host 0.0.0.0 \
  --port 8011 \
  --lan \
  --allowed-host 192.168.1.23 \
  --allowed-host seektalent.local \
  --allowed-origin http://192.168.1.23:5176 \
  --allowed-origin http://seektalent.local:5176
```

At startup the backend prints:

- bind address and URL
- allowed Host headers
- allowed Origins
- cookie posture
- proxy-header posture

On plain HTTP, cookies are not `Secure`; HTTPS requests set `Secure` cookies. Do not expose the backend to an untrusted network. Do not put the data root in iCloud Drive, Dropbox, a shared sync folder, or the repository.

## Accounts And Sessions

Use `/setup` to bootstrap the first local admin. After an admin exists, use `/login`.

The workbench uses HttpOnly cookies for local auth and CSRF tokens for mutating routes. Logout clears the session client state. Expired or invalid sessions are redirected to login.

Sessions are scoped to the current user/workspace. A JD plus optional notes is one workbench session.

## Workbench Flow

Typical flow:

1. Create a JD session.
2. Edit and approve the requirement triage gate.
3. Start CTS and/or Liepin source runs from the source cards.
4. Watch the strategy panel and source cards update from durable state and SSE events.
5. Review merged candidates in the right rail.
6. Add notes, mark promising, or reject.
7. For Liepin detail pages, approve or reject detail-open requests in the approval queue.

CTS and Liepin source runs use separate execution lanes. CTS runs can execute in parallel; Liepin uses a single serial lane for provider safety.

Liepin card search is summary-first. Strong card matches can create agent-recommended detail-open requests automatically, including the candidate snapshot, match reason, and budget impact shown in the approval queue. Liepin detail opening defaults to `human_confirm`, so an agent recommendation does not open the provider detail page until the user approves it. `bypass_confirm` skips only per-candidate confirmation; backend ledger, budget, lease, pacing, and risk-control checks still apply.

## Liepin Login

Liepin login is isolated from the main workbench at:

```text
/connections/liepin/{connectionId}/login
```

The web UI receives a safe handoff descriptor. It must never receive cookies, storage state, auth headers, CDP URLs, Playwright websocket URLs, worker URLs, raw provider payloads, or auth-bearing provider URLs.

## Data And Privacy Boundaries

Ordinary workbench APIs expose redacted metadata and stable refs, not raw provider payloads. Raw resume/profile material belongs behind corpus/provider-owned boundaries for authorized benchmark, debug, and manual-review use.

Memory rows must not store candidate PII or raw resume/profile material by default. Candidate data should not leak into ordinary SSE events, logs, diagnostics, normal artifacts, or security/audit notes.

The current implementation includes a first-class `security_audit_events` table and admin-only audit API for implemented sensitive workbench actions such as bootstrap, login/logout, source connection changes, Liepin detail policy changes, detail-open approval decisions, provider open actions, backup/restore, and feature-gate startup state. Audit metadata is redacted before persistence and must not contain passwords, session tokens, CSRF tokens, cookies, auth headers, browser storage, CDP endpoints, raw provider payloads, or raw resume/profile content.

## Runtime And Error Boundaries

Source runs are durable workbench records. The UI should treat source cards as the current materialized state and SSE as the progress stream, not as the source of truth after refresh.

Current recovery behavior is intentionally conservative:

- server restart reconciles expired running jobs through the workbench store;
- Liepin detail-open leases expire and can stop blocking the next lease;
- source-run pause/resume/cancel controls are not first-class UI/API actions yet;
- user-visible errors should use safe reason codes such as login expired, verification required, budget blocked, or provider unavailable, not raw exceptions or provider payloads.

Recruiter time-saved and quality counters are estimates for operator context. They are not billing, compliance, or benchmark metrics.

## Input And Rendering Safety

JD text is bounded by backend validation. The frontend must render JD, notes, event payloads, candidate summaries, and provider-derived text as text, not trusted HTML.

Treat JD text, notes, profile snippets, and provider-returned content as prompt-injection capable input. Do not let those fields request tool use, reveal secrets, bypass Liepin budgets, change audit policy, or alter memory-writing rules.

## Backup And Rollback Runbook

The M6 workbench includes a first-class SQLite backup/restore command. Backups include only the workbench database and intentionally exclude browser profiles and raw provider session state. Each backup has sibling metadata recording the metadata schema, app version, git commit when available, retention policy, required workbench tables, required columns and indexes, integrity check, and excluded data classes.

To disable the new workbench during internal rollout, start the backend with:

```bash
uv run seektalent-ui-api --disable-workbench
```

or set:

```bash
SEEKTALENT_WORKBENCH_ENABLED=false
```

When disabled, `/api/auth/*` and `/api/workbench/*` return a maintenance response; older non-workbench APIs are not disabled by this gate. Startup records the evaluated gate state in `security_audit_events`.

Create and verify a backup:

```bash
uv run seektalent-ui-maintenance backup --workspace-root .
uv run seektalent-ui-maintenance verify-backup .seektalent/backups/workbench-YYYYMMDDTHHMMSSffffffZ.sqlite3
```

Restore into a test workspace:

```bash
backup_path=".seektalent/backups/workbench-YYYYMMDDTHHMMSSffffffZ.sqlite3"
uv run seektalent-ui-maintenance restore "${backup_path}" --workspace-root /tmp/workbench-restore --yes
```

Stop/restore/restart:

```bash
# stop the backend first
backup_path=".seektalent/backups/workbench-YYYYMMDDTHHMMSSffffffZ.sqlite3"
uv run seektalent-ui-maintenance restore "${backup_path}" --workspace-root . --yes
uv run seektalent-ui-api
```

Backup and restore actions write system audit rows. Restore requires valid sibling metadata, current canonical workbench column signatures, explicit index DDL, foreign-key integrity, required column-definition fragments, no triggers/views, and a real workbench read-path smoke check. It builds a verified temporary database through SQLite's backup API, quarantines the stopped target database plus SQLite sidecars, replaces the target, writes the restore audit row, and restores the original database if the post-replace step fails.

Smoke after restore:

- login succeeds
- `/sessions` loads
- a known session detail page loads
- source cards render
- candidate queue renders
- detail-open ledger rows remain readable

## Internal Rollout Readiness

Before opening the M7 workbench to internal business use, run the local readiness check from the repository root:

```bash
uv run seektalent-ui-maintenance rollout-readiness --workspace-root .
```

This command is the automated local gate for durable workbench state. It validates the local database readiness path, backup creation, backup verification, restore-to-temp behavior, and redacted readiness evidence written under `.seektalent/rollout-readiness/`.

It does not replace the human rollout gates. Before business use, an operator still needs to verify:

- real-device LAN access from the intended trusted network;
- real Liepin login through the isolated login flow;
- real provider account budget and detail-open behavior.

Do not treat the readiness report as proof of live LAN reachability, provider login validity, or provider budget safety. Those checks require a real device, a real Liepin account session, and explicit operator approval.

## Verification

Backend:

```bash
uv run pytest tests/test_workbench_api.py tests/test_workbench_auth_security.py tests/test_workbench_network_guard.py tests/test_ui_api.py tests/test_ui_mapper.py -q
uv run pytest tests/test_workbench_security_audit.py tests/test_workbench_maintenance.py -q
uv run pytest tests/test_liepin_api_scope.py tests/test_liepin_boundaries.py tests/test_liepin_compliance_gate.py tests/test_liepin_corpus_integration.py tests/test_liepin_detail_ledger.py tests/test_liepin_detail_policy.py tests/test_liepin_detail_integration.py tests/test_liepin_provider_adapter.py tests/test_liepin_verified_loop.py tests/test_liepin_worker_client.py tests/test_liepin_worker_runtime.py -q
```

Frontend:

```bash
cd apps/web
bun --bun playwright install chromium
bun run test
bun run typecheck
bun run build
bun run test:visual
```

Liepin worker:

```bash
cd apps/liepin-worker
bun run test
bun run typecheck
bun run boundary-check
```

`bun run test:visual` uses Playwright plus `odiff-bin` against tracked local baselines. On this development machine it also compares desktop key frames against the extracted reference frames from `/Users/frankqdwang/Documents/工作/seektalent/references/output/recruiter-agent-design-system/frames/` with a tolerant 8% structural threshold. If that external package is not present, the reference-frame comparison is skipped and the tracked local regression baselines still run.

To make the extracted design package a hard gate, run:

```bash
SEEKTALENT_REFERENCE_FRAME_DIR_REQUIRED=1 bun run test:visual
```

Update local baselines only after intentional UI changes:

```bash
cd apps/web
UPDATE_VISUAL_BASELINES=1 bun run test:visual
```

## Related Docs

- [Configuration](configuration.md)
- [CLI](cli.md)
