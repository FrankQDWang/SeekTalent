# Runtime-Authored Strategy Graph Contract Design

## Summary

The active Svelte Workbench still reconstructs the strategy graph in frontend code. That was acceptable for the earlier single-source/CTS-oriented Workbench, but it is now a data-flow bug in the dual-source Runtime.

The specific symptom is that the frontend graph labels `round-N-score` as a Runtime Top Pool / scoring node, while the backend graph-candidate endpoint parses the same node id as CTS-only scoring. The broader problem is worse: the frontend decides which runtime nodes exist, what they mean, what fields appear in node details, and how candidates attach to those nodes.

This spec moves strategy graph authorship to the backend. Runtime/Workbench backend projects the graph nodes, edges, node detail text, safe structured sections, and candidate scopes. The frontend renders that contract with generic templates. It must not hard-code runtime business node kinds such as `ctsRoundScoring`, `liepinCardSearch`, or `aggregation` as the source of truth.

No new LLM calls are introduced. Node `summaryText`, `detailSections`, and natural text are deterministic projections from already-existing Runtime public events, Runtime source state, requirement sheets, detail-open requests, candidate review items, and final-top10 data.

## Current Code Facts

- `apps/web-svelte/src/lib/workbench/runStory.ts` builds graph nodes, edges, detail payloads, logs, and completion text in frontend TypeScript.
- `apps/web-svelte/src/routes/(app)/sessions/[sessionId]/+page.svelte` fetches session/events/candidates/final-top10/detail-open requests and calls `buildRunStory call`.
- The page fetches `detailOpenRequestsQuery`, but it does not pass `detailOpenRequests` into `buildRunStory call`, so Liepin detail request summaries are missing from graph node details.
- `apps/web-svelte/src/lib/components/NodeDetailPanel.svelte` contains a switch over frontend detail payload kinds such as `ctsRoundResults`, `ctsRoundScoring`, `liepinCardSearch`, and `aggregation`.
- `src/seektalent_ui/workbench_candidate_graph.py::parse_graph_node_ref function` separately parses frontend node ids to decide candidate scope.
- `parse_graph_node_ref("round-N-score")` returns a CTS scoring scope, even though the dual-source Runtime graph uses `round-N-score` as the all-source scoring/top-pool node.
- `round-N-source-liepin` candidate lookup is review-backed and currently has no explicit `source_round == N` filter in `workbench_candidate_graph.py`.
- `apps/web-svelte/src/lib/components/GraphNodeCandidateList.svelte` already knows how to render page-level graph-candidate metadata such as coverage, truncation, and recoverable-empty state, but the active node detail panel does not use that component.
- Runtime already emits safe public events through `seektalent.runtime.public_events` with stage, round, source, status, counts, and safe reason code.
- Workbench session responses already include `runtimeSourceState`, which exposes selected sources, coverage, finalization revision/reason, merge counts, canonical resume counts, and per-source safe counts.
- Running notes and final-top10 were already moved toward Runtime-owned safe projections in prior PRs; this spec does not reopen those flows except where the graph needs their existing public facts.

## Goals

- Make backend Runtime/Workbench projection the only active source of strategy graph node and edge semantics.
- Add one canonical UI-facing graph API:

```text
GET /api/workbench/sessions/{session_id}/runtime-graph
```

- Return graph nodes with:
  - stable `nodeId`
  - user-facing label and deterministic natural text
  - stage/source/round/status metadata
  - safe generic detail sections
  - explicit candidate scope, or explicit no-candidate state
- Make `graph-candidates` resolve candidate lists from the backend-authored candidate scope for the selected node, not from duplicated frontend node-id assumptions.
- Fix dual-source candidate scopes:
  - `round-N-score` is all-source Runtime scoring/top-pool scope.
  - `round-N-source-cts` is CTS recall scope for that round.
  - `round-N-source-liepin` is Liepin recall/card scope for that round.
  - final shortlist is final all-source scope.
  - detail approval is Liepin detail-open request scope.
- Make frontend graph rendering generic:
  - no runtime business node construction in frontend.
  - no frontend switch over business detail payload kinds.
  - unknown safe detail structures render as deterministic natural text, not raw JSON.
- Preserve the existing graph interaction:
  - layout remains left-to-right.
  - click node opens the node detail rail.
  - clicking a node loads candidates for that node when the backend declares a candidate scope.
- Use every meaningful backend graph-candidate page field in the UI:
  - total counts
  - coverage warnings
  - truncation
  - recoverable-empty reason
  - generated timestamp when useful for debugging freshness.

## Non-Goals

- Do not add any LLM output field for graph display.
- Do not add any LLM call to summarize node details.
- Do not change Runtime retrieval, source dispatch, normalization, dedupe, scoring, reflection, or finalizer behavior.
- Do not redesign the graph visual style beyond what is necessary for generic rendering.
- Do not display raw JSON as the primary node detail UI.
- Do not preserve frontend runtime graph compatibility aliases. The active session page must use the backend graph contract.
- Do not remove historical docs under `docs/superpowers/**` or `docs/v-0.2/**`.

## Product Contract

### Backend Runtime Graph API

`GET /api/workbench/sessions/{session_id}/runtime-graph` returns:

```json
{
  "sessionId": "session_example",
  "generatedAt": "2026-05-26T00:00:00Z",
  "nodes": [],
  "edges": [],
  "completionText": null
}
```

Each node is authored by the backend:

```json
{
  "nodeId": "round-1-score",
  "kind": "scoring",
  "label": "第 1 轮 · Top Pool",
  "summaryText": "第 1 轮评分完成，10 位候选人进入 Top Pool。",
  "status": "completed",
  "stage": "scoring",
  "sourceKind": "all",
  "lane": "shared",
  "roundNo": 1,
  "eventIds": ["runtime_run:1:scoring:all"],
  "detailSections": [],
  "candidateScope": {
    "scopeKind": "round_score",
    "sourceKind": "all",
    "roundNo": 1
  }
}
```

The frontend may derive color from `status`, `sourceKind`, and `stage`, but it must not derive business meaning from hard-coded node id parsing.

### Detail Sections

Node detail uses generic safe sections. Initial section kinds:

- `text`
- `facts`
- `list`

Example:

```json
{
  "heading": "本轮评分",
  "kind": "facts",
  "facts": [
    {"label": "进入评分", "value": "18 人"},
    {"label": "Top Pool", "value": "10 人"},
    {"label": "覆盖状态", "value": "全部渠道已完成"}
  ]
}
```

The backend may include a deterministic `detailText` for each section or node. If a structured value does not fit a known section shape, backend converts it with a safe deterministic serializer:

```text
硬性条件：Python 后端、分布式系统
偏好：Agent workflow、招聘产品经验
过滤：地点=上海；年龄=30-40
```

The serializer must:

- preserve user-facing field labels.
- omit empty/null fields.
- redact or omit unsafe technical fields.
- never output cookies, auth headers, browser endpoints, file paths, artifact paths, raw provider payloads, or full raw resumes.
- not call an LLM.

### Candidate Scope

Node candidate lookup uses backend-authored `candidateScope`.

Supported initial scopes:

- `none`: this node intentionally has no candidate list.
- `round_recall`: source result candidates for a specific round/source.
- `round_score`: all-source scored/top-pool candidates for a specific round.
- `final`: final Top 10 / final shortlist candidates.
- `detail_approval`: Liepin detail-open request candidates.

`round-N-score` must use:

```json
{"scopeKind": "round_score", "sourceKind": "all", "roundNo": N}
```

It must not use CTS-only flywheel scoring rows as the whole node candidate list in a dual-source session.

### Graph Candidates API

`GET /api/workbench/sessions/{session_id}/graph-candidates?node_id=NODE_ID` remains the click-through API, but it must resolve the node through the same backend graph projection:

```text
node_id
-> runtime graph node
-> candidateScope
-> candidate projection
```

If a node has no candidate scope, the endpoint returns a recoverable empty response rather than 404:

```json
{
  "nodeId": "requirements",
  "items": [],
  "recoveryState": "recoverable_empty",
  "recoveryReason": "node_has_no_candidate_scope"
}
```

### Frontend Contract

The Svelte session page fetches:

- session
- runtime graph
- final-top10
- candidate review items
- detail-open requests
- running notes/events where still needed by notes UI

The strategy graph uses the runtime graph response. The frontend may adapt backend nodes to the existing SvelteFlow component shape, but that adapter is presentational only:

- map `nodeId` to flow node id.
- map backend label/summary/status/source to display props.
- map backend edges to flow edges.
- do not create or rename business nodes.
- do not infer detail payloads.
- do not parse node ids for business meaning.

Node detail renders:

- node heading
- backend `summaryText`
- backend `detailSections`
- graph candidate page metadata
- candidate cards
- resume snapshot when a candidate is selected.

The old frontend `runStory.ts` business graph construction must not be active in the session page after this change.

## Data Flow

```text
job_title + JD + notes
-> Runtime requirement extraction
-> approved RequirementSheet
-> Runtime dual-source round loop
-> Runtime public events + Workbench runtimeSourceState + review/final/detail projections
-> backend runtime graph projector
-> /runtime-graph
-> Svelte generic graph renderer
-> click node
-> graph-candidates resolves backend candidateScope
-> generic node/candidate detail renderer
```

## Acceptance Criteria

- `/api/workbench/sessions/{session_id}/runtime-graph` exists and returns backend-authored nodes and edges.
- Svelte session page no longer calls `buildRunStory call` for the active strategy graph.
- `rg -n "buildRunStory\\(" apps/web-svelte/src/routes apps/web-svelte/src/lib/components` returns no active session graph usage.
- `NodeDetailPanel.svelte` no longer switches on runtime business detail payload kinds such as `ctsRoundScoring`, `liepinCardSearch`, or `aggregation`.
- Clicking `round-N-score` in a dual-source run shows all-source scoring/top-pool candidate data, including Liepin candidates when they were scored.
- Clicking a candidate from `round-N-score` can still open its safe resume snapshot when `canExpandResume` is true.
- Clicking `round-N-source-liepin` in a multi-round run only shows Liepin candidates for that round.
- Clicking non-candidate nodes such as `job`, `requirements`, `source-plan`, or `round-N-query` shows node details and an explicit no-candidate state, not a 404 error.
- Liepin detail approval node details include detail request summaries and budget/status text without relying on frontend `detailOpenRequests` plumbing.
- Graph candidate page metadata (`coverage`, `truncated`, `recoveryState`, totals) is visible in node detail.
- Node details are readable natural text/generic sections, not raw JSON.
- No graph-display code introduces a new LLM call.
- Backend tests cover graph contract generation, no-candidate nodes, dual-source `round-N-score`, round-scoped Liepin source nodes, and detail approval sections.
- Frontend tests cover rendering backend-authored nodes, clicking nodes, generic section rendering, recoverable-empty candidate state, and dual-source score candidate request wiring.
- `uv run pytest tests/test_workbench_runtime_graph.py tests/test_workbench_api.py -q` passes.
- `cd apps/web-svelte && bun run test -- src/lib/workbench/runtimeGraphView.test.ts src/lib/components/NodeDetailPanel.test.ts` passes.
- `cd apps/web-svelte && bun run check && bun run lint && bun run build` passes.
