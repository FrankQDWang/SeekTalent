# Svelte 5 Frontend Spike Report

## Result

Status: superseded

The original Svelte UI was a technical spike. It proved the local SvelteKit direction and has since been replaced by the active Svelte Workbench app in this directory.

## What Was Proven

- `apps/web-svelte` builds as a SvelteKit + Svelte 5 TypeScript SPA/static app with `@sveltejs/adapter-static` fallback output.
- OpenAPI types can be generated from the FastAPI backend through `openapi-typescript`.
- The typed frontend wrapper can cover Workbench auth, session, candidate, graph, source-connection, detail-request, and resume-snapshot APIs without hand-written schema drift.
- TanStack Svelte Query works for Workbench read/write flows and cache invalidation.
- Svelte graph rendering with `@xyflow/svelte` and ELK can render non-trivial recruiter-facing strategy graph nodes, support selection, local drag, zoom, and pan.
- Safe UI projection is practical: route tests assert that raw artifact paths, auth headers, cookies, CSRF labels, and raw provider payload strings are not rendered.

## What Was Not Proven

- The spike UI was not a full Workbench replacement at the time it was written.
- The spike did not include the final parity auth shell, session rail, source settings, Liepin connection route, or session-detail layout contract.
- The spike did not prove SSE as the primary freshness path.
- The live backend smoke used isolated state and did not provide a seeded non-trivial Workbench session.
- Production bundle optimization remains open; Svelte Flow and ELK still need route-level splitting or worker evaluation if real graphs grow.

## Migration Recommendation

Keep `apps/web-svelte` as the active Workbench frontend. The old spike dashboard and dev-mode readiness posture should not be treated as the primary UI.

## Evidence To Carry Forward

- OpenAPI generation: `bun run api:gen` reads `http://127.0.0.1:8012/openapi.json` and writes `src/lib/api/schema.d.ts`.
- Svelte Query evidence: unit and e2e coverage exercises auth, session list/detail, candidate review, final Top 10, source connections, and detail-open request wrappers.
- Graph evidence: Svelte Flow plus ELK can render the Workbench strategy graph and support node selection, graph-candidate lazy loading, resume-snapshot lazy loading, local drag, zoom, and pan.
- Security evidence: Playwright and unit tests check that protected paths, cookies, auth headers, CSRF labels, raw provider payload markers, and local data-root posture are not rendered in primary UI surfaces.
- Current parity gate: `./scripts/verify-dev-workbench.sh`.

## Deferred Items

- Automate visual snapshot baselines for the Svelte Workbench routes.
- Optimize Svelte bundle and graph performance after parity.
- Broaden source connection UX after the browser-backed live path stabilizes.
