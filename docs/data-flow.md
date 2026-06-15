# Data Flow

SeekTalent has one runtime path and multiple product surfaces. CLI, Python API, and Workbench requests all converge on `WorkflowRuntime`; the Workbench BFF stores and projects runtime-owned facts for the React UI.

## Run Flow

1. Entry points collect `job_title`, JD text, and optional notes.
2. `WorkflowRuntime` extracts requirements, plans rounds, dispatches selected sources, scores candidates, reflects on round quality, and finalizes the ranked shortlist.
3. Source adapters under `src/seektalent/sources/` translate source-neutral plans into CTS or Liepin execution paths.
4. Provider adapters under `src/seektalent/providers/` handle concrete CTS/Liepin transport, mapping, safety, and browser/worker boundaries.
5. Runtime writes artifacts and public events through explicit state and tracing objects.

Runtime code does not import concrete provider modules. Provider construction enters through retrieval/source boundaries.

## Workbench Flow

The local Workbench API in `src/seektalent_ui` is a BFF over runtime facts:

1. Session routes persist job input, source settings, source-run state, review items, final candidates, runtime events, and graph projections in SQLite.
2. Runtime execution writes session state and public event rows through Workbench persistence helpers.
3. BFF routes project frontend response shapes from persisted runtime facts.
4. `apps/web-react` calls BFF API adapter functions in `src/lib/api`, consumes query keys from `src/lib/query/keys.ts`, and renders typed Agent Workbench view models.

Initial Workbench loading is intentionally split:

- session summary
- candidate review queue
- final Top 10
- runtime graph
- graph candidate page
- lazy resume snapshot
- incremental SSE events

Do not replace this with a single large session payload, and do not introduce GraphQL for the current architecture.

## Event Flow

Workbench event rows include a monotonic `globalSeq`.

- Initial event queries page from `after_seq=0`.
- SSE streams resume from the highest cached `globalSeq` when available.
- Streamed events append into the query cache with duplicate suppression and sorting.
- Only derived surfaces affected by the event category are invalidated.

The event page itself is not refetched from page zero on every stream update.

## Frontend Boundary

Generated OpenAPI schema types stay behind React API/workbench adapter modules. Production React components consume explicit Agent Workbench DTO aliases and TanStack Query hooks from the BFF adapter layer, not generated schema internals, runtime/provider payloads, or fixture data.
