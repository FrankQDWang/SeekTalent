# PyPI Bundled Workbench Local Product Design

## Goal

Ship SeekTalent 0.6.4 as a PyPI package that includes the Python backend, CLI, packaged prompts/specs, and the built Svelte Workbench frontend. A non-developer user should install the package, create a three-line `.env`, run one command, and use the local Workbench in a browser without installing Node, Bun, Svelte dependencies, or cloning the repository.

## Product Contract

The packaged user path is:

```bash
pipx install seektalent
seektalent init
# edit only these three values
seektalent workbench
```

The generated packaged `.env` contains only:

```env
SEEKTALENT_TEXT_LLM_API_KEY=
SEEKTALENT_CTS_TENANT_KEY=
SEEKTALENT_CTS_TENANT_SECRET=
```

All other settings use product defaults. Source checkout developers keep the full `.env.example` surface and can keep using `scripts/start-dev-workbench.sh`, Vite, Bun, OpenCLI, and dev-mode flywheel.

## Packaging Boundary

The PyPI package must include the built frontend static output, not the frontend development toolchain.

Included in PyPI:

- `seektalent` Python package
- `seektalent_ui` Python package
- packaged prompts under `seektalent/prompts/`
- packaged CTS spec
- packaged user env template
- built SvelteKit static frontend under `seektalent_ui/static/workbench/`

Not included as runtime requirements for the user:

- `apps/web-svelte/node_modules`
- Bun
- Vite dev server
- Svelte source compilation on the user machine
- repository checkout
- OpenCLI/Liepin browser bridge by default

The release builder needs Bun to produce the static frontend before `uv build`. The installed PyPI user does not.

## Runtime Entrypoints

`seektalent workbench` is the user-facing packaged command. It starts the local FastAPI backend in prod mode and serves the packaged Svelte app from the same origin. The browser loads the UI from the backend origin, so the packaged product does not need a separate frontend process or CORS origin.

`seektalent-ui-api` remains available as a lower-level API server for development and diagnostics.

## Data Roots

Packaged prod defaults resolve local data under `~/.seektalent/`:

| Data | Default path | Cleanup policy |
| --- | --- | --- |
| run artifacts | `~/.seektalent/artifacts/runs/YYYY/MM/DD/run_*` | auto-delete partitions older than 7 days |
| benchmark execution artifacts | `~/.seektalent/artifacts/benchmark-executions/YYYY/MM/DD/benchmark_*` | auto-delete partitions older than 7 days |
| exact LLM cache | `~/.seektalent/cache` | cleared on startup/run cleanup |
| workbench database | `~/.seektalent/workbench.sqlite3` or workspace-root `.seektalent/workbench.sqlite3` | retained |
| corpus database | `~/.seektalent/corpus.sqlite3` or workspace-root `.seektalent/corpus.sqlite3` | retained |
| workbench backups | `~/.seektalent/backups/` or workspace-root `.seektalent/backups/` | retained |
| logs | `~/.seektalent/logs/` or workspace-root `.seektalent/logs/` | auto-delete only log files older than 7 days when implemented |
| flywheel database | dev only by default | prod default disabled |

The retained databases are not temporary garbage. They hold user accounts, workbench sessions, long-lived recruiting notes, corpus metadata, and recovery backups. They need explicit user action or a later product retention policy before deletion.

## Cleanup Triggers

Cleanup runs:

- before `seektalent run`
- before `seektalent benchmark`
- before interactive TUI startup
- during `seektalent workbench` startup
- through an explicit maintenance command if added later

Cleanup must be best-effort and local-only. A cleanup failure must not delete unrelated paths, must not leave the app half-started without a clear error, and must not remove retained databases or backups.

## Privacy And Upload Contract

By default, the packaged product keeps business data local. It sends network requests only for the selected provider/LLM workflow:

- LLM API calls receive prompt content required for extraction, scoring, reflection, finalization, and optional Workbench note writing.
- CTS API calls receive the tenant credentials and search requests required for candidate retrieval.
- `seektalent inspect --json`, `doctor`, local cleanup, and local Workbench startup do not upload provider secrets, cookies, raw resumes, local databases, or browser sessions.

Remote eval logging through W&B/Weave must remain disabled by default for packaged users and require explicit opt-in configuration.

## Security Contract

The packaged Workbench binds to loopback by default. Non-loopback LAN bind still requires explicit `--lan` or `SEEKTALENT_UI_LAN=1`, plus allowed Host/Origin configuration. Static frontend routes must use the same Host/Origin guard posture as Workbench API routes.

The packaged frontend must be served from local package resources. Requests must not read arbitrary files from the user filesystem via path traversal.

## Build And Release Contract

The release process is:

```bash
python scripts/build_packaged_workbench.py
uv build --clear
uv publish --dry-run
uv publish --token "$PYPI_TOKEN"
```

The build script must fail if the frontend build output is missing `200.html` or SvelteKit `_app` assets. The wheel test must unzip the wheel and assert the packaged frontend files exist.

Publishing to PyPI remains a separate explicit release gate. The plan may document the command, but implementation must not publish without a later user instruction.

## Success Criteria

1. `uv build --clear` creates a wheel containing `seektalent_ui/static/workbench/200.html` and SvelteKit `_app` assets.
2. Installing the wheel into a fresh virtualenv without Node/Bun allows `seektalent --help`, `seektalent init`, `seektalent inspect --json`, and `seektalent workbench --help` to run.
3. A packaged server can return the frontend shell from `/` and keep `/api/*` routes handled by FastAPI.
4. Packaged `seektalent init` writes only the three required credential variables.
5. Prod startup cleanup removes only eligible short-lived artifacts/caches and does not delete `workbench.sqlite3`, `corpus.sqlite3`, backups, or user account/session records.
6. Dev source checkout behavior remains intact: Vite dev server still works, full `.env.example` remains available, and flywheel stays enabled by default in dev.
