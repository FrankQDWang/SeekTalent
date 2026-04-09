# Development

This repository is now optimized around the `v0.3 phase 3 bootstrap/execution/ranking core` cutover: small Python code, destructive cleanup, and zero compatibility layers.

## Prerequisites

- Python `3.12+`
- `uv`

Install dev tooling:

```bash
uv sync --group dev
```

## Common commands

CLI help:

```bash
uv run seektalent --help
```

Doctor:

```bash
uv run seektalent doctor
```

Inspect:

```bash
uv run seektalent inspect --json
```

Run tests:

```bash
uv run --group dev python -m pytest
```

Build a wheel:

```bash
uv build
```

Rerank API help:

```bash
uv run --group rerank seektalent-rerank-api --help
```

## Repo shape

- `src/seektalent/` for the stable contracts, bootstrap/execution/ranking core, CTS bridge, CLI, and gated runtime
- `src/seektalent_rerank/` for the rerank service
- `tests/` for Python tests
- `docs/v-*` for versioned specs and archives

## Current expectations

- Prefer destructive cleanup over compatibility shims
- Keep runtime behavior fail-fast and explicit
- Keep models close to use sites
- Do not reintroduce controller / reflection / finalize scaffolding before the matching phase spec requires it
