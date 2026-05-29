# UI

SeekTalent now includes a local-first internal recruiter workbench for scoped users, JD sessions, CTS + Liepin source cards, requirement triage, candidate review, SSE progress, and Liepin detail-open approval.

It is an internal local workbench, not a public SaaS surface and not a hosted recruiting SaaS. Packaged users only need Python, a browser, their three required keys, and a working local OpenCLI setup for Liepin. They do not install Bun, Vite, or a repository checkout.

## Product Boundary

The workbench is a first-class local product surface. Packaged users start it with `seektalent workbench`, which serves the built Svelte app from the FastAPI loopback origin and defaults Liepin to the local `opencli` command. Source-checkout developers can still start the backend and frontend separately.

## Components

- Backend API script: `seektalent-ui-api`
- Frontend app: `apps/web-svelte`
- Default backend address: `http://127.0.0.1:8011`
- Default frontend address: `http://127.0.0.1:5178`
- Workbench SQLite path: `.seektalent/workbench.sqlite3` under the configured workspace root, or the current working directory when no workspace root is configured

## Loopback Startup

Default startup binds the backend to loopback only:

```bash
uv run seektalent-ui-api
```

In another terminal:

```bash
cd apps/web-svelte
bun install
bun run dev -- --host 127.0.0.1 --port 5178
```

Open:

```text
http://127.0.0.1:5178
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
2. Select CTS and/or Liepin sources at session creation; source cards then show the selected source state in the left column.
3. Click the central `启动 Agent` action in the strategy graph. The agent extracts search criteria from JD/notes first and does not start source runs yet.
4. Review or edit the extracted criteria, then confirm and start the selected sources through the same central strategy action.
5. Watch the strategy graph, source cards, and running notes update from durable state and SSE events.
6. Click graph nodes to inspect node-scoped candidates in `节点详情`. The final shortlist appears in the `最终短名单` node detail.
7. Expand an individual candidate card only when a safe resume snapshot is needed.
8. For Liepin detail pages, approve or reject detail-open requests from the `详情审批` node detail.

CTS and Liepin source runs use separate execution lanes. CTS runs can execute in parallel; Liepin uses a single serial lane for provider safety.

## Interactive Strategy Graph

The workbench strategy graph is rendered with the Svelte frontend graph stack and laid out through ELK. It is not a workflow engine; it is a recruiter-facing projection of durable Workbench session events, source-run state, candidate evidence, and detail approval state.

Graph lanes separate shared job/requirement nodes from CTS and Liepin source work. The graph and running notes do not expose source filters; they show all sources selected for the current session. Nodes are clickable business objects: requirement breakdown, source queue state, CTS query/result/scoring/reflection rounds, Liepin card/detail approval steps, candidate aggregation, and final shortlist handoff. The right inspector has exactly two tabs: `运行笔记` and `节点详情`. Clicking a graph node opens `节点详情`. There is no standalone `候选人队列` tab; review-backed shortlist candidates are shown from the `最终短名单` node. Running notes are one-by-one business logs in a plain stream: no per-entry timestamp, no card frame, and no separate graph-node title above the text. For CTS, each completed round appears as one note summarizing query direction, recall, scoring, and reflection instead of one note per graph node. Running notes and candidate evidence actions can jump to related graph nodes when the backend-safe data contains the relationship.

CTS multi-round runs are rendered as workflow rows: `第 N 轮关键词 -> 召回 -> 评分 -> 反思`. Later rounds return to the keyword column on a lower row. For round `N > 1`, the keyword node has two business inputs: stable requirements and the previous round's reflection. The canvas can be panned and zoomed, and nodes can be dragged locally for readability; local drag positions are not persisted.

Candidate graph nodes do not embed full candidate arrays. When a user selects a recall, scoring, final, Liepin card, or detail-approval node, the frontend queries the backend for paginated node-scoped candidate summaries. Complete resume content is fetched only after expanding a single candidate card and is projected through the safe snapshot API.

At desktop widths the JD/source panel, strategy graph, activity log, and detail tabs are visible in the three-column workbench shell. Around 1024px the right-side activity and detail area stacks below the graph, so operators can still reach both the strategy graph and selected node details without horizontal scrolling.

Liepin card search is summary-first. Strong card matches can create agent-recommended detail-open requests automatically, including the candidate snapshot, match reason, and budget impact shown in the `详情审批` node detail. Liepin detail opening defaults to `human_confirm`, so an agent recommendation does not open the provider detail page until the user approves it. `bypass_confirm` skips only per-candidate confirmation; backend ledger, budget, lease, pacing, and risk-control checks still apply.

## Liepin Login

Liepin login is isolated from the main workbench at:

```text
/connections/liepin/{connectionId}/login
```

The web UI receives a safe handoff descriptor. It must never receive cookies, storage state, auth headers, CDP URLs, Playwright websocket URLs, worker URLs, raw provider payloads, or auth-bearing provider URLs.

## Svelte Workbench

`apps/web-svelte` is the active frontend app for the Workbench.

Installed PyPI users run:

```bash
export SEEKTALENT_TEXT_LLM_API_KEY=your-text-llm-key
export SEEKTALENT_CTS_TENANT_KEY=your-cts-tenant-key
export SEEKTALENT_CTS_TENANT_SECRET=your-cts-tenant-secret
seektalent workbench
```

That command serves the packaged static frontend from the backend origin. It expects `opencli` on `PATH` plus a connected OpenCLI browser plugin for Liepin, but it does not require Bun, Vite, or a source checkout.

For source-checkout testing of the Svelte Workbench with CTS + Liepin, use the explicit local launcher:

```bash
scripts/start-dev-workbench.sh
```

The launcher starts the backend on `127.0.0.1:8012` and Svelte frontend on `127.0.0.1:5178`, using the repo-local OpenCLI browser helper from `apps/web-svelte/node_modules/.bin/opencli`. It exports `SEEKTALENT_LIEPIN_WORKER_MODE=opencli` and `SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND=opencli` only for the launched backend process, keeping ordinary FastAPI startup tied to explicit configuration.

Current Svelte parity route map:

- `/login`: public login shell.
- `/setup`: first-admin bootstrap shell.
- `/sessions`: authenticated workbench shell with topbar, source navigation, session rail, session creation, and global event stream.
- `/sessions/{sessionId}`: authenticated session workbench with source cards, requirement triage, strategy graph, activity log, node details, final shortlist, candidate cards, detail requests, and session event stream.
- `/settings/sources`: source settings overview.
- `/settings/sources/liepin`: Liepin source connection status and management surface.
- `/connections/liepin/{connectionId}/login`: safe local browser connection status route. It does not recreate the legacy managed-browser fallback UI or iframe handoff.

The Svelte parity gate is:

```bash
./scripts/verify-dev-workbench.sh
```

The script starts a deterministic backend on `127.0.0.1:8012` for OpenAPI generation, runs Python semantic tests, Svelte check/lint/unit/build/e2e, scoped handwritten-code no-fallback checks, real-backend API smoke, and `git diff --check`.

## Data And Privacy Boundaries

Ordinary workbench APIs expose redacted metadata and stable refs, not raw provider payloads. Raw resume/profile material belongs behind corpus/provider-owned boundaries for authorized benchmark, debug, and manual-review use.

Memory rows must not store candidate PII or raw resume/profile material by default. Candidate data should not leak into ordinary SSE events, logs, diagnostics, normal artifacts, or security/audit notes.

The current implementation includes a first-class `security_audit_events` table and admin-only audit API for implemented sensitive workbench actions such as bootstrap, login/logout, source connection changes, Liepin detail policy changes, detail-open approval decisions, provider open actions, backup/restore, and feature-gate startup state. Audit metadata is redacted before persistence and must not contain passwords, session tokens, CSRF tokens, cookies, auth headers, browser storage, CDP endpoints, raw provider payloads, or raw resume/profile content.

## Live Liepin Smoke

Live Liepin smoke is manual and explicit:

```bash
uv run seektalent liepin-smoke --live \
  --tenant-id tenant-a \
  --workspace-id workspace-a \
  --actor-id actor-a \
  --connection-id conn_x \
  --compliance-gate-ref gate_x \
  --worker-mode opencli \
  --keyword "python" \
  --page-size 1 \
  --max-detail-opens 1
```

The smoke command requires local BYOK settings, an approved Liepin compliance gate and connection, a working OpenCLI browser helper, and an already logged-in local Liepin browser session. It is not part of the default automated gate.

## Runtime And Error Boundaries

Source runs are durable workbench records. The UI should treat source cards as the current materialized state and SSE as the progress stream, not as the source of truth after refresh.

Starting a session returns the Runtime-owned `runtimeJob` plus `blockedSources`; it no longer returns a `sourceRuns` snapshot. Clients that need source-run state should read the session payload. `runtimeJob.sourceKinds` describes the actual runnable source scope for that job, so blocked or already completed sources are excluded even when they remain selected on the session.

Legacy `source_run_jobs` rows are retained only for historical database shape and backup compatibility. Primary source execution now goes through `runtime_sourcing_jobs`, whose job scope is bound to the source-run ids selected for that Runtime job; stale legacy rows are ignored after upgrade.

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
- `最终短名单` node detail renders the final candidate shortlist
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
uv run pytest tests/test_workbench_api.py tests/test_workbench_security_audit.py tests/test_workbench_auth_security.py tests/test_dev_mode_readiness.py tests/test_workbench_network_guard.py tests/test_liepin_boundaries.py tests/test_liepin_api_scope.py -q
uv run pytest tests/test_workbench_security_audit.py tests/test_workbench_maintenance.py -q
uv run pytest tests/test_liepin_api_scope.py tests/test_liepin_boundaries.py tests/test_liepin_compliance_gate.py tests/test_liepin_corpus_integration.py tests/test_liepin_detail_ledger.py tests/test_liepin_detail_policy.py tests/test_liepin_detail_integration.py tests/test_liepin_provider_adapter.py tests/test_liepin_verified_loop.py tests/test_liepin_worker_client.py tests/test_liepin_worker_runtime.py -q
```

Frontend:

```bash
cd apps/web-svelte
bun run check
bun run lint
bun run test
bun run build
bun run test:e2e
```

Liepin worker:

```bash
cd apps/liepin-worker
bun run test
bun run typecheck
bun run boundary-check
```

## Related Docs

- [Configuration](configuration.md)
- [CLI](cli.md)
