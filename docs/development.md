# Development

This project is optimized for local iteration, small diffs, and readable Python.

## Prerequisites

- Python `3.12+`
- `uv`
- Optional: Node.js and `pnpm` for the web UI

Install development dependencies:

```bash
uv sync --group dev
```

## Common commands

Run Python tests:

```bash
uv run pytest
```

Run the CLI help:

```bash
uv run deepmatch --help
```

Run the canonical `run` help:

```bash
uv run deepmatch run --help
```

Run the UI API help:

```bash
uv run deepmatch-ui-api --help
```

Run frontend tests:

```bash
cd apps/web-user-lite
pnpm test
```

## Mock CTS for development

`mock CTS` is a development-only path for local testing, regression checks, and prompt/runtime work.

Example:

```bash
uv run deepmatch run --jd "Python agent engineer" --mock-cts
```

Or set:

```dotenv
DEEPMATCH_MOCK_CTS=true
```

Notes:

- mock CTS avoids live CTS traffic
- mock CTS still requires a valid LLM provider key
- this mode is not the recommended path for end users

## Repo shape

Key directories:

- `src/deepmatch/` for the main Agent implementation and CLI
- `src/deepmatch_ui/` for the minimal backend API used by the web UI
- `apps/web-user-lite/` for the frontend
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
