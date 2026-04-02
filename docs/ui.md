# UI

The repository includes a minimal local web UI for using the Agent in a browser.

It is not a hosted product surface and it does not replace the CLI.

## Components

- Backend API script: `cv-match-ui-api`
- Frontend app: `apps/web-user-lite`
- Default backend address: `http://127.0.0.1:8011`
- Default frontend address: `http://127.0.0.1:5176`

## Start the backend

```bash
uv run cv-match-ui-api
```

Optional flags:

```text
--host
--port
```

## Start the frontend

```bash
cd apps/web-user-lite
pnpm install
pnpm dev
```

Then open:

```text
http://127.0.0.1:5176
```

## What the UI covers

- entering JD text
- entering sourcing preference text
- creating a run
- polling run status
- showing the final shortlist
- expanding candidate detail

## What the UI does not cover

- historical run browsing
- trace inspection
- authentication or user accounts
- multi-user coordination
- advanced run management

## Backend API shape

The local UI backend currently exposes:

- `POST /api/runs`
- `GET /api/runs/{runId}`
- `GET /api/runs/{runId}/candidates/{candidateId}`

This API is intentionally small and local in scope.

## Agent notes

- The UI backend uses the same Agent runtime and model preflight as the CLI.
- In-memory run state is used for the local UI server process; this is not a persistent service.

## Related docs

- [Configuration](configuration.md)
- [CLI](cli.md)
