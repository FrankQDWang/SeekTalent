# Development

This project is optimized for local iteration, small diffs, and readable Python.

## Prerequisites

- Python `3.12+`
- `uv`
- Optional: Bun for the web UI

Install development dependencies:

```bash
uv sync --group dev
```

## Common commands

Run Ruff lint checks:

```bash
uv run ruff check src tests experiments
```

Ruff is a standalone quality check, not part of `pytest`.
`experiments/` is included in the required Ruff gate.
It includes required anti-silent-exception checks: do not add swallowed exceptions, empty catches, broad catches,
or useless `try`/`except` wrappers. A local `noqa` is acceptable only at a clear runtime or CLI boundary.

Run ty type checks:

```bash
uv run ty check src tests
uv run ty check src/seektalent/runtime/orchestrator.py
uv run ty check --watch src tests
```

ty is a standalone required CI check, not part of `pytest`.

Run the required architecture import guard:

```bash
uv run python tools/check_arch_imports.py
```

The architecture import guard prevents core `src/seektalent` code from importing UI or experiment modules.

Run Tach architecture dependency observations:

```bash
uv run tach check
uv run tach report src/seektalent/runtime --raw
uv run tach report src/seektalent_ui --raw
uv run tach show --mermaid -o /tmp/seektalent-tach-stage2-graph.md
uv run tach map -o /tmp/seektalent-tach-stage2-map.json
```

Tach is a local advisory architecture radar in this phase, not a required CI gate. It tracks coarse `src/` module direction only; `tests/`, `experiments/`, and generated graph/map files stay out of the committed checks. If `uv run tach check` reports dependency drift, either update `tach.toml` to match the intended dependency direction or simplify the import that crossed a boundary.

Run Python tests:

```bash
uv run pytest
```

## Test Typing

Use `tests.settings_factory.make_settings()` when tests need `AppSettings`. Do not call `AppSettings(_env_file=None)` directly in tests.

Keep dynamic test boundaries local. For monkeypatches, stubs, fake clients, or third-party typing gaps, prefer a local `cast(Any, ...)` at the boundary. Do not add global ty ignores, bulk suppressions, or production abstractions just to satisfy tests.

Run the CLI help:

```bash
uv run seektalent --help
uv run seektalent exec --help
```

Run the canonical `run` help:

```bash
uv run seektalent run --help
uv run seektalent exec run --help
```

Run the UI API help:

```bash
uv run seektalent-ui-api --help
```

Start the local Svelte workbench with the repo-local Pi dependency and project-local Pi MCP adapter bridge:

```bash
scripts/start-dev-workbench.sh
```

This launcher is the product development preset for the CTS + Liepin local Workbench. Pi is part of that preset, not an optional ad hoc startup flag. The launcher installs Svelte dependencies when needed, loads the repo-local Bailian provider extension and pinned `pi-mcp-adapter` extension, exports the Pi-backed Liepin settings for the launched backend process, maps Pi to the same root `.env` Runtime text LLM provider/model (`deepseek-v4-flash` by default), and then starts both the backend and Svelte frontend. A plain low-level `seektalent-ui-api` command does not mutate `.pi/mcp.json` or silently enable Liepin when `SEEKTALENT_LIEPIN_WORKER_MODE=disabled`.

The launcher never creates or edits `.pi/mcp.json`. Register DokoBot MCP inside Pi with the explicit `seektalent pi-agent init --project --write` setup command after the real DokoBot MCP command is known. Live Liepin browser runs also require `SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON` to name the Pi tool events that prove DokoBot browser actions were observed. Until those values are configured and Pi has warmed/reconnected its MCP metadata, Liepin should report a blocked browser channel while CTS remains usable.

Static and live diagnostics:

```bash
uv run seektalent doctor --json
uv run seektalent doctor --live-pi-agent --json
SEEKTALENT_LIVE_PI_AGENT=1 uv run pytest tests/test_liepin_live_pi_agent.py -q
```

Run frontend tests:

```bash
cd apps/web-svelte
bun run test
```

Build packaged Workbench assets before building release distributions:

```bash
python scripts/build_packaged_workbench.py
```

## Mock CTS for development

`mock CTS` is a development-only path for local testing, regression checks, and prompt/runtime work.

It is not available in the published PyPI CLI.

Example:

```bash
SEEKTALENT_MOCK_CTS=true uv run seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer"
```

Or set it in a source-checkout env file:

```dotenv
SEEKTALENT_MOCK_CTS=true
```

Notes:

- mock CTS avoids live CTS traffic
- mock CTS still requires a valid LLM provider key
- the published CLI rejects `--mock-cts`; use the env setting in a source checkout instead
- this mode is not the recommended path for end users

## Env template source

- `.env.example` is the only env template you should edit by hand.
- `src/seektalent/default.env` is the minimal packaged user template used by installed wheels.
- Tests enforce that the packaged template contains only `SEEKTALENT_TEXT_LLM_API_KEY`, `SEEKTALENT_CTS_TENANT_KEY`, and `SEEKTALENT_CTS_TENANT_SECRET`.

## PyPI Release Build

Build frontend assets before building Python distributions:

```bash
python scripts/build_packaged_workbench.py
uv build --clear
uv publish --dry-run
```

Publishing requires an explicit release gate:

```bash
uv publish --token "$PYPI_TOKEN"
```

## Repo shape

Key directories:

- `src/seektalent/` for the main Agent implementation and CLI
- `src/seektalent_ui/` for the minimal backend API used by the web UI
- `apps/web-svelte/` for the frontend
- `tests/` for Python tests
- `docs/v-*` for versioned historical design notes

## Contributor expectations

- Prefer small, surgical changes over broad rewrites.
- Keep Agent behavior explicit.
- Do not add defensive fallback layers unless the task genuinely requires them.
- Keep models and configuration close to usage.

## Release-facing docs

The public entry points for users are:

- `README.md`
- `docs/configuration.md`
- `docs/cli.md`
- `docs/ui.md`
- `docs/architecture.md`
- `docs/outputs.md`

When behavior changes, update those docs before adding more design commentary.
