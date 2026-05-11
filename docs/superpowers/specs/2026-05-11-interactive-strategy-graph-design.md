# Interactive Strategy Graph Design

## Context

The recruiter workbench currently has a `检索策略图` that is generated from real backend session events and source-run state:

- `apps/web/src/runStory.ts` derives graph nodes, graph edges, criteria, and business log entries.
- `apps/web/src/recruiterAnimation.ts` defines the current `RecruiterGraphNode`, `RecruiterGraphEdge`, and `RecruiterLogEntry` types.
- `apps/web/src/app.tsx` renders the graph with absolute-positioned HTML nodes and SVG paths.
- `apps/web/src/styles.css` gives the graph the warm-paper Recruiter Agent visual language.

This is enough for a passive run timeline. It is not enough for the product direction now selected: the strategy graph is a core interaction surface. Recruiters need to click a workflow node and inspect what happened at that step: which keywords were used, which candidates were found, how they scored, what reflection concluded, and why the next action was chosen.

The upstream GStack design doc is:

`/Users/frankqdwang/.gstack/projects/FrankQDWang-SeekTalent/frankqdwang-main-design-20260511-111657.md`

The selected product direction is Approach C: use `@xyflow/react` for the interactive graph and `elkjs` for automatic graph layout. The right-lower workbench area uses tabs: `候选人队列` and `节点详情`.

## Decision

Upgrade the strategy graph from a hand-rendered visual projection to an interactive, inspectable graph while keeping backend state authoritative.

Use three layers:

1. Business graph model
   - `buildRunStory()` remains the source of truth for the frontend graph story.
   - It accepts a single object input containing `WorkbenchSession`, `WorkbenchEvent[]`, safe candidate review items, safe detail-open requests, and `sourceFilter`.
   - It converts those frontend-safe API payloads into recruiter-facing graph nodes, edges, log entries, and node detail payloads.
   - It must not import React Flow or ELK.

2. Layout adapter
   - A new frontend adapter converts business nodes and edges into ELK input.
   - ELK computes source-aware positions.
   - If ELK layout fails, the UI falls back to the existing deterministic percentage coordinates.

3. React Flow presentation
   - `@xyflow/react` renders nodes and edges.
   - Custom nodes preserve the existing warm-paper style.
   - React state owns selected node, viewport, and tab selection.
   - The node inspector tab displays node-specific business details.

The backend does not change for this slice. Existing Workbench APIs, SSE event recovery, candidate queue API, source-run materialized state, and detail approval APIs remain the data source.

## Requirements

### Functional Requirements

- The center strategy graph renders through React Flow.
- The graph supports pan, zoom, fit view, and clickable nodes.
- The graph keeps the existing source filter behavior:
  - `All sources` shows all enabled source lanes.
  - `CTS` shows CTS graph nodes and logs only.
  - `Liepin` shows Liepin graph nodes and logs only.
- Empty graph state still shows the central `启动检索` button.
- Source selection remains session creation time only.
- Per-source start buttons and `启动全部` remain removed.
- Clicking a graph node selects it and switches the right-lower tab to `节点详情`.
- Right-lower tabs are:
  - `候选人队列`
  - `节点详情`
- `候选人队列` remains the default when there is no selected node.
- Switching back to `候选人队列` preserves selected-node state.
- If the selected node disappears after source filtering, the selection is cleared and the right-lower tab returns to `候选人队列`.
- Running-note entries can reference graph node ids and select the related graph node.
- Candidate evidence actions must link at evidence granularity where possible, using safe `evidenceId`, `sourceRunId`, `sourceKind`, and `evidenceLevel` data before falling back to review-item-level links.
- The selected node has a visible selected state in the graph.

### Node Detail Requirements

Each graph node must carry enough data to render a recruiter-facing node inspector.

Required node fields:

- `id`
- `kind`
- `label`
- `detail`
- `tone`
- `sourceKind`
- `sourceLabel`
- `lane`
- `detailKind`
- `detailPayload`
- `eventIds`
- `sourceRunId`
- `candidateReviewItemIds`
- `candidateEvidenceRefs`
- `detailOpenRequestIds`

Initial `detailKind` variants:

- `job`
- `requirements`
- `sourceQueue`
- `ctsRoundQuery`
- `ctsRoundResults`
- `ctsRoundScoring`
- `reflection`
- `liepinCardSearch`
- `liepinCardCandidates`
- `liepinDetailApproval`
- `aggregation`

The inspector must show:

- Job node: job title, session source mode, and JD summary.
- Requirements node: approved triage as `confirmed`; draft triage as `draft`; otherwise runtime-extracted criteria as `runtime`.
- Source queue node: source status, auth state, scanned count, unique candidates, warnings.
- CTS query node: round number and generated query terms.
- CTS result node: raw candidate count and unique candidate count.
- CTS scoring node: scored count, fit count, not-fit count.
- Reflection node: reflection summary, rationale, and next direction when present.
- Liepin card search node: scanned card count and unique candidate count.
- Liepin candidate node: card-level candidate count and highest AI detail score.
- Liepin detail approval node: request count, leased count, blocked count, safe request ids or safe candidate summaries, and budget text.
- Aggregation node: candidate count and best score.

### Data Integrity Requirements

- Node details must be derived only from:
  - `WorkbenchSession`
  - `WorkbenchEvent`
  - candidate review queue API data already safe for frontend display
  - detail approval API data already safe for frontend display
- `buildRunStory()` must receive candidate review items and detail-open requests as optional safe inputs, not infer those relationships only from event payloads.
- Candidate-to-graph linking must prefer evidence-specific relationships over broad review-item relationships so CTS and Liepin evidence for the same candidate do not jump to the wrong source lane.
- Node details must not expose raw provider payloads.
- Node details must not expose cookies, auth headers, storage state, CDP URLs, Playwright endpoints, or auth-bearing provider URLs.
- Warning display must use an allowlist keyed by backend-safe warning codes; unknown provider/backend messages must fall back to a generic safe message rather than rendering raw text.
- Reflection text and model output are rendered as escaped text, not HTML.
- If a node references data not present in the current API payloads, the inspector must show an explicit empty state such as `本节点暂无候选人明细` instead of fake data.

### Layout Requirements

- ELK layout direction is left-to-right within each visible source lane.
- Shared job and requirements nodes stay on the left.
- Source lanes stay vertically separated when multiple sources are visible.
- Aggregation stays on the right.
- The graph remains compact enough to fit inside the current strategy panel.
- The layout adapter has a deterministic fallback that uses current percentage coordinates plus the same vertical lane stacking rules.
- React Flow parent container has stable dimensions so the canvas is never blank.
- React Flow stylesheet is imported in the global frontend stylesheet.
- At 1024px desktop/tablet width, node details remain reachable after selecting a graph node; the right-lower inspector may move below the strategy panel or into a drawer, but it must not disappear.

### Visual Requirements

- Preserve the Recruiter Agent design language:
  - high-density internal tool
  - warm paper background
  - muted borders
  - compact node cards
  - source badges
  - restrained colors
- Do not let React Flow default styling dominate the app.
- Do not show generic diagram-editor affordances that business users do not need.
- Controls are allowed if they are small and visually quiet.
- Mini map is deferred unless graph navigation becomes hard in manual QA.

### Non-Goals

- Do not change Python Workbench APIs in this slice.
- Do not move workflow execution or source-run logic into the frontend.
- Do not persist user-dragged graph positions in this slice.
- Do not render every candidate as its own graph node in this slice.
- Do not add Storybook in this slice.
- Do not rebuild the candidate queue.
- Do not change Liepin detail approval semantics.

## File-Level Shape

Expected frontend file changes:

- `apps/web/package.json`
  - Add `@xyflow/react`.
  - Add `elkjs`.
- `apps/web/bun.lock`
  - Updated by `bun add`.
- `apps/web/src/recruiterAnimation.ts`
  - Extend graph/log types with node detail metadata and evidence-specific graph refs.
- `apps/web/src/runStory.ts`
  - Change `buildRunStory()` to accept a single object input.
  - Populate node detail metadata, evidence refs, and related ids from session, events, candidate review items, and detail-open requests.
- `apps/web/src/runStory.test.ts`
  - Cover detail payloads and graph-node/log-node references.
- `apps/web/src/strategyGraphLayout.ts`
  - New pure layout adapter for ELK and fallback placement.
- `apps/web/src/strategyGraphLayout.test.ts`
  - Cover lane stacking, aggregation placement, and fallback placement.
- `apps/web/src/StrategyGraph.tsx`
  - New React Flow graph component and custom node renderer.
- `apps/web/src/setupTests.ts`
  - Add React Flow DOM measurement mocks for Vitest/jsdom.
- `apps/web/src/NodeDetailPanel.tsx`
  - New selected-node inspector.
- `apps/web/src/app.tsx`
  - Replace hand-rendered graph with `StrategyGraph`.
  - Add selected node state.
  - Add right-lower `候选人队列` / `节点详情` tabs.
- `apps/web/src/app.test.tsx`
  - Cover node selection, tab switching, reflection details, source filter clearing selection, candidate evidence linking, and central start behavior.
- `apps/web/src/styles.css`
  - Import React Flow CSS globally.
  - Restyle React Flow node/edge/canvas classes to match the current visual system.
- `apps/web/tests/visual/workbench.visual.spec.ts`
  - Add or update visual checks for interactive graph states.

## Test Requirements

Frontend unit tests:

- `buildRunStory()` produces detail payloads for:
  - CTS query/result/scoring/reflection nodes.
  - Liepin card search/candidate/detail approval nodes.
  - aggregation node.
- source filter does not leak nodes or logs across sources.
- source filter clears a now-hidden selected node and returns to `候选人队列`.
- log entries can carry `relatedNodeId`.
- candidate evidence can map to graph nodes through evidence-specific refs and only fall back to `candidateReviewItemIds` when no more specific relationship exists.
- detail-open requests can map to graph nodes through `detailOpenRequestIds`.
- ELK layout adapter returns stable lane-stacked fallback positions if layout fails.

Frontend component tests:

- central `启动检索` button still appears in empty graph state.
- no `启动全部`, `启动 CTS`, or `启动猎聘` buttons reappear.
- clicking a reflection node switches to `节点详情`.
- reflection inspector shows the actual reflection summary.
- switching back to `候选人队列` keeps the selected node active.
- selecting a running-note entry selects the related node where available.
- selecting a candidate evidence action selects the related strategy node where available.
- CTS-only sessions render an interactive graph and candidate queue.

Regression commands:

```bash
cd apps/web && bun run test
cd apps/web && bun run typecheck
cd apps/web && bun run build
cd apps/web && bun run test:visual
uv run pytest tests/test_workbench_api.py -q
git diff --check
```

Manual browser verification:

- Create or seed a reproducible CTS or multi-source session for manual verification; do not depend on a machine-local session id.
- Verify the strategy graph renders through React Flow and is not blank.
- Verify pan/zoom and fit view work.
- Verify clicking a reflection node opens `节点详情`.
- Verify `候选人队列` tab is still available.
- Verify source filter still controls graph and running notes.
- Verify central `启动检索` is still the only run-start action.
- Verify the Playwright visual path no longer depends on the deleted playback UI (`Start playback`, `.playback-bar`, `.elapsed`, or old `.graph-node` selectors).

## External References

- React Flow official quick start documents `@xyflow/react`, stylesheet import, parent sizing, nodes, edges, and `fitView`: https://reactflow.dev/learn/getting-started/installation-and-requirements
- React Flow provider docs explain when `ReactFlowProvider` is needed for flow state outside the canvas: https://reactflow.dev/api-reference/react-flow-provider
- elkjs README documents that ELK computes layout only, not rendering, and provides `elk.layout(graph)` with layered layout options: https://github.com/kieler/elkjs
