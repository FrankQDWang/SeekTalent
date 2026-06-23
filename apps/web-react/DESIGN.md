# React Agent Workbench Design Contract

This document is the first-version design contract for the React Agent Workbench rebuild. It is production acceptance material, not inspiration notes.

## Design Sources

Primary sources:

- `找候选人.fig`, local designer export. Extracted metadata lives at `docs/superpowers/artifacts/react-agent-workbench-design/figma/meta.json`; thumbnail at `docs/superpowers/artifacts/react-agent-workbench-design/figma/thumbnail.png`.
- `WTS/*.png`, designer-provided workbench screen exports. Registered copies live under `docs/superpowers/artifacts/react-agent-workbench-design/wts/`.
- `docs/superpowers/artifacts/react-agent-workbench-design/transcript/*.png`, Codex transcript reference screenshots supplied by the user.

Not source material:

- retired legacy UI documents
- deleted legacy frontend visual behavior

Those retired materials may help identify stale references that must be removed during cutover, but they must not override this design contract.

## Product Shape

The app is a local-first recruiter workbench. It is not a landing page. The first viewport must be the usable work surface:

- requirement intake and confirmation;
- agent transcript and composer;
- strategy graph;
- candidate queue and detail evidence;
- right rail with `候选人 / 思考过程` tabs;
- source connection, pending actions, approvals, and final shortlist surfaces.

The visual tone is quiet, dense, and operational. The UI should support repeated work, scanning, comparison, and audit.

## Figma Metadata

The local export metadata currently records:

- file name: `找候选人`
- exported at: `2026-06-10T07:06:00.272Z`
- thumbnail size: `400x156`
- render coordinates: `9247x3615`

The `.fig` file is a local zip-style export from the designer, not a Figma account source. Build verification must use the extracted local artifacts and the SHA-256 manifest, not a remote Figma API assumption.

## Information Architecture

Desktop shell:

```text
+----------------------------------------------------------------------------------+
| Top bar: workspace, source status, run status, pending action, user controls      |
+-------------+------------------------------+------------------+------------------+
| Conversation| Transcript + composer         | Strategy graph   | Right rail       |
| list        | Requirement confirmation      | Strategy timeline| 候选人 / 思考过程 |
| Runs        | Codex-style run groups        | Workflow stages  | Candidate/detail |
| History     | Attachments and approvals     | Search strategy  | Timeline cards   |
+-------------+------------------------------+------------------+------------------+
| Final shortlist / review artifacts appear as route-level panels, not hidden logs  |
+----------------------------------------------------------------------------------+
```

Tablet shell:

```text
+---------------------------------------------------------------------+
| Top bar: run status, sources, pending action                        |
+---------------------------------------------------------------------+
| Segmented work tabs: Transcript | Strategy | Candidates | Final      |
+---------------------------------------------------------------------+
| Active tab content with sticky composer or action footer             |
+---------------------------------------------------------------------+
| Right rail content becomes an in-flow panel under Strategy/Candidates|
+---------------------------------------------------------------------+
```

Mobile shell:

```text
+----------------------------------------+
| Top bar: title, source status, actions  |
+----------------------------------------+
| Tabs: Chat | Graph | Candidates | Final |
+----------------------------------------+
| Active panel                            |
| - Chat: transcript + composer           |
| - Graph: read-only strategy timeline    |
| - Candidates: list, detail, approvals   |
| - Final: shortlist and export           |
+----------------------------------------+
```

## Visual Acceptance Map

| Asset                                                     | Owner route/component                                | Storybook owner                            | Playwright screenshot                  |
| --------------------------------------------------------- | ---------------------------------------------------- | ------------------------------------------ | -------------------------------------- |
| `figma/thumbnail.png`                                     | `WorkbenchShell` macro layout                        | `WorkbenchShell/FigmaThumbnailReference`   | `workbench-shell-figma-reference`      |
| `wts/首页初始状态.png`                                    | `/agent-workbench`, `HomeStartPanel`                 | `HomeStartPanel/Initial`                   | `workbench-home-initial`               |
| `wts/首页输入文字状态.png`                                | `Composer`                                           | `Composer/RequirementDraft`                | `workbench-home-draft`                 |
| `wts/需求确认.png`                                        | `RequirementReviewPanel`                             | `RequirementReviewPanel/NeedsConfirmation` | `workbench-requirement-review`         |
| `wts/检索策略图.png`                                      | `StrategyGraphCanvas`                                | `StrategyGraphCanvas/SearchStrategy`       | `workbench-strategy-graph`             |
| `wts/思考过程.png`                                        | `ThinkingProcessRail` with 候选人 / 思考过程 tabs    | `ThinkingProcessRail/RoundTimeline`        | `workbench-thinking-process`           |
| `wts/候选人列表空状态.png`                                | `CandidateQueue`                                     | `CandidateQueue/Empty`                     | `workbench-candidates-empty`           |
| `wts/候选人列表页面.png`                                  | `CandidateQueue`                                     | `CandidateQueue/Populated`                 | `workbench-candidates-list`            |
| `wts/候选人详情侧边栏.png`                                | `CandidateDetailDrawer`                              | `CandidateDetailDrawer/Summary`            | `workbench-candidate-detail`           |
| `wts/简历详情完整内容.png`                                | `ResumeEvidencePanel`                                | `ResumeEvidencePanel/FullContent`          | `workbench-resume-full`                |
| `transcript/codex-transcript-01-full-collapsed.png`       | `Transcript`, collapsed run group                    | `Transcript/CollapsedRunGroup`             | `workbench-transcript-collapsed`       |
| `transcript/codex-transcript-02-full-expanded.png`        | `Transcript`, expanded run group                     | `Transcript/ExpandedRunGroup`              | `workbench-transcript-expanded`        |
| `transcript/codex-transcript-03-toolread-detail.png`      | `TranscriptOperationEvent`, expanded details         | `Transcript/ToolReadDetails`               | `workbench-transcript-tool-detail`     |
| `transcript/codex-transcript-04-web-search-running.png`   | `TranscriptOperationEvent`, running operation row    | `Transcript/WebSearchRunning`              | `workbench-transcript-web-running`     |
| `transcript/codex-transcript-05-file-search-complete.png` | `TranscriptOperationEvent`, completed operation row  | `Transcript/FileSearchComplete`            | `workbench-transcript-file-complete`   |
| `transcript/codex-transcript-06-file-read-running.png`    | `TranscriptOperationEvent`, running operation row    | `Transcript/FileReadRunning`               | `workbench-transcript-file-running`    |
| `transcript/codex-transcript-07-guided-followup.png`      | `Transcript`, guided follow-up and composer boundary | `Transcript/GuidedFollowup`                | `workbench-transcript-guided-followup` |

Every manifest row must map to one owner above. Unowned design assets fail the design gate.

## Component Taxonomy

- `WorkbenchShell`: application frame, responsive regions, top status bar, source state, pending-action slot.
- `ConversationNav`: conversation/run list and archived run access.
- `Transcript`: semantic transcript groups, active stream tail, composer boundary, context dividers.
- `TranscriptRunGroup`: collapsed/expanded run grouping and processed-time row.
- `TranscriptOperationEvent`: operation lifecycle row with status and optional details.
- `TranscriptEventDetails`: structured detail lines, safe refs, source ids, command output snippets, error reasons.
- `Composer`: user input, attachments, submit state, stop/regenerate controls when supported by BFF.
- `RequirementReviewPanel`: requirement draft, confirmation, missing fields, approval controls.
- `ActivityTimeline`: compact runtime progress outside the main transcript when needed.
- `StrategyGraphCanvas`: read-only strategy timeline graph, workflow stages, source/search nodes, and backend progress state. Nodes are not selectable and the surface has no pan, zoom, drag, or detail-drawer interaction.
- `ThinkingProcessRail`: round timeline cards for `关键词`, `observation`, and `反思和下一轮变更`.
- `CandidateQueue`: candidate list, filters, score summaries, selection state.
- `CandidateDetailDrawer`: candidate summary, evidence, approval actions, resume-safe refs.
- `ResumeEvidencePanel`: complete resume/evidence view with safe redaction boundaries.
- `DetailApprovalPanel`: cost/sensitive action approval, pending state, audit outcome.
- `FinalShortlistPanel`: reviewed shortlist, rationale, export-ready artifact.
- `SourceConnectionStatus`: source health, auth state, and recoverable source errors.
- `PendingActionControls`: approval banners and blocking next actions.

## State Ownership

| State                            | Owner                           | React responsibility                                        |
| -------------------------------- | ------------------------------- | ----------------------------------------------------------- |
| active conversation/run identity | BFF snapshot and route params   | select and render                                           |
| composer draft before submit     | React local                     | edit, validate basic emptiness, send                        |
| requirement draft/review         | BFF                             | render and submit typed actions                             |
| transcript groups/events         | BFF snapshot and durable stream | render, animate, preserve local collapse state              |
| transcript collapse/expand       | React local                     | store by stable `groupId` and `eventId`                     |
| active stream cursor             | BFF stream ledger               | send Last-Event-ID, hold gap state without advancing cursor |
| tool/source/command lifecycle    | BFF semantic events             | render status and details, no raw payload parsing           |
| strategy graph nodes/edges       | BFF projection                  | render read-only timeline/swimlane structure only           |
| thinking process                 | BFF `thinkingProcess` model     | render round cards, no runtime payload parsing              |
| candidate queue                  | BFF                             | render, sort/filter locally only when BFF allows            |
| detail approval                  | BFF                             | render pending/accepted/rejected/applied state              |
| source connection                | BFF                             | render status and reconnect affordance                      |
| final shortlist                  | BFF                             | render and export using BFF artifact refs                   |
| archived/completed run status    | BFF                             | render as read-only when locked                             |

## State Matrix

| State              | Required behavior                                                                                  |
| ------------------ | -------------------------------------------------------------------------------------------------- |
| loading            | skeletons in persistent regions; no layout jump when data arrives                                  |
| empty              | show usable requirement start state from `首页初始状态.png`                                        |
| drafting           | composer and requirement draft reflect `首页输入文字状态.png`                                      |
| needs confirmation | requirement review panel matches `需求确认.png`; pending action visible                            |
| running            | transcript active group updates in place; graph and thinking process can update independently      |
| partial stream     | completed rows stay committed; active row mutates; do not reorder committed cells                  |
| disconnected       | show recoverable stream banner; retain snapshot; do not advance cursor                             |
| stream gap         | show explicit gap recovery state and request snapshot/replay; do not fabricate events              |
| permission denied  | show source or approval-specific denial; do not expose raw provider payload                        |
| failed             | failed group/tool/candidate action remains inspectable with reason code                            |
| completed          | final shortlist and review artifacts become primary; composer enters follow-up mode if BFF permits |
| archived           | read-only transcript, graph, candidates, and final artifacts                                       |

## Strategy Graph And Thinking Process

`WTS/检索策略图.png` constrains the read-only strategy timeline surface. It is the search strategy and runtime stage surface. It is not a details drawer, selectable node graph, pan/zoom canvas, or debug topology explorer.

`WTS/思考过程.png` constrains the right rail. The prior node-detail area becomes a `候选人 / 思考过程` segmented rail. The `思考过程` tab renders a round-based timeline from BFF `thinkingProcess`.

Required thinking-process card mapping:

| Card               | BFF source facts                                                                                                                                 |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `关键词`           | runtime round progress `query_terms`, `keyword_query`, and `executed_queries`                                                                    |
| `observation`      | scoring/search facts plus post-scoring LLM `resume_quality_comment` and counts                                                                   |
| `反思和下一轮变更` | `reflection_summary`, `reflection_rationale`, `suggestedActivateTerms`, `suggestedKeepTerms`, `suggestedDeprioritizeTerms`, `suggestedDropTerms` |

React must not inspect raw `RuntimeControlEvent.payload` to build these cards. The BFF projects them into typed round timeline items.

## Transcript Contract

The transcript does not follow `WTS/思考过程.png`. It follows the supplied Codex transcript references for interaction shape.

Codex source anchors, used as read-only reference:

- Responses SSE is normalized in `.external/codex-reference/codex-rs/codex-api/src/sse/responses.rs` around `process_responses_event`, `OutputTextDelta`, `OutputItemDone`, `ToolCallInputDelta`, and `ReasoningSummaryDelta`.
- Protocol lifecycle events are defined in `.external/codex-reference/codex-rs/protocol/src/protocol.rs` around `EventMsg`, `ItemStarted`, `ItemCompleted`, `AgentMessageContentDelta`, `McpToolCallBegin/End`, `WebSearchBegin/End`, and `ExecCommandBegin/OutputDelta/End`.
- TUI transcript behavior is described in `.external/codex-reference/codex-rs/tui/src/chatwidget.rs` and `.external/codex-reference/codex-rs/tui/src/history_cell/mod.rs`, including committed cells, active cells, and transcript lines.

Rules:

- The BFF emits semantic transcript groups and lifecycle events.
- React renders grouped cells, not a plain ordered message list.
- Run groups can be collapsed or expanded.
- Tool/source/command rows pair start, delta, completion, failure, and cancellation by stable IDs.
- Details expand from structured payloads: file/source refs, safe labels, command output snippets, status reasons, and counts.
- Context compression renders as a divider cell.
- Attachments render as safe thumbnails or refs beside the relevant user or guided follow-up message.
- Pending, running, succeeded, failed, cancelled, and superseded states have distinct visual treatments.
- The active cell updates in place during streaming; committed cells do not jump or reorder.
- The BFF may expose live `message.delta`, but durable replay relies on `message.completed` plus lifecycle events and stream ledger rows.
- React must not parse raw shell output, raw runtime logs, provider payloads, source payloads, or localized tool summary strings.
- The Codex checkout is reference material, not a runtime dependency and not a source of product copy.

If transcript lifecycle, active-cell behavior, grouping, details, or stream replay semantics are unclear, stop and inspect `.external/codex-reference` before coding. Guessing from screenshots alone fails this design contract.

## BFF Event Profile Summary

| Source fact          | BFF event kind                                                                          | Frontend cell/surface          | Durable       |
| -------------------- | --------------------------------------------------------------------------------------- | ------------------------------ | ------------- |
| user message         | `message.created`, `message.completed`                                                  | transcript message cell        | yes           |
| assistant token      | `message.delta`                                                                         | active transcript message      | live optional |
| assistant completion | `message.completed`                                                                     | committed transcript message   | yes           |
| activity lifecycle   | `activity.upserted`                                                                     | operation/activity row         | yes           |
| operation lifecycle  | `operation.started`, `operation.outputDelta`, `operation.completed`, `operation.failed` | operation row and details      | yes           |
| source search        | `sourceSearch.started`, `sourceSearch.completed`, `sourceSearch.failed`                 | source/tool row                | yes           |
| web search reference | `webSearch.started`, `webSearch.completed`                                              | web/source row                 | yes           |
| command lifecycle    | `command.started`, `command.outputDelta`, `command.completed`, `command.failed`         | command row and bounded output | yes           |
| runtime stage        | `runtime.stageChanged`                                                                  | graph and activity state       | yes           |
| strategy graph       | `strategyGraph.changed`                                                                 | read-only strategy timeline    | yes           |
| thinking process     | `thinkingProcess.changed`                                                               | right rail timeline            | yes           |
| candidate            | `candidate.upserted`                                                                    | candidate queue/detail         | yes           |
| detail approval      | `detailApproval.changed`                                                                | approval panel                 | yes           |
| pending action       | `pendingAction.changed`                                                                 | banner/control slot            | yes           |
| source connection    | `sourceConnection.changed`                                                              | source status                  | yes           |
| final summary        | `finalSummary.updated`                                                                  | final shortlist panel          | yes           |
| context compaction   | `context.compacted`                                                                     | transcript divider             | yes           |
| replay gap           | `stream.gap`                                                                            | recoverable stream state       | yes           |

## Layout And Interaction Details

- Cards use 8px radius or less unless a component already has a tighter system token.
- Use icons for tool buttons and status rows where a familiar icon exists.
- Keep hero-scale typography out of tool panels, cards, sidebars, transcript rows, and graph labels.
- Fixed-format controls such as tabs, icon buttons, score chips, and candidate rows need stable dimensions.
- Text must not overflow buttons, pills, graph nodes, or status rows at 375px.
- The graph canvas is read-only: it must fit the visible viewport without drag, pan, zoom, reset controls, selectable nodes, or clickable node details.
- The right rail must preserve tab state while graph and transcript stream.
- Candidate detail surfaces must keep source/evidence references separate from raw resume/provider payloads.

## Motion Policy

- Stream updates should be buffered into smooth UI commits, ideally animation-frame driven with small batches.
- Mutate the active transcript row in place instead of remounting whole transcript groups.
- Keep graph layout stable during streaming; new nodes may enter with a short fade/slide only if it does not move focused content.
- Honor `prefers-reduced-motion` by disabling non-essential transitions and using immediate state changes.
- Loading skeletons must reserve final dimensions to prevent layout shift.

## Responsive And Accessibility Acceptance

Required viewport checks:

- 375px mobile
- 768px tablet
- 1440px desktop
- wide desktop

Acceptance:

- No overlapping text or controls at any required viewport.
- Keyboard access for tabs, composer, graph controls, transcript group toggles, candidate list, approvals, and final actions.
- Visible focus rings on interactive elements.
- Accessible names for icon-only controls.
- WCAG AA text contrast for body text, muted text, buttons, errors, and status rows.
- Reduced-motion mode remains usable.
- Transcript and thinking-process updates announce important status changes without flooding screen readers.

## Fixture And Story Rules

- Typed fixtures may exist only under `apps/web-react/src/test/fixtures/`.
- Production code must not import fixtures.
- Storybook and visual tests may use fixtures to cover known states.
- Fixtures must represent the BFF contract shape closely enough to catch design drift: transcript groups, stream cursor, thinking process, strategy graph, candidates, pending actions, source connection, detail approval, and final summary.
- Page/screen stories must cover `ConversationScreen/Initial`, `ConversationScreen/RequirementReview`, `ConversationScreen/RunningWithStream`, `ConversationScreen/SourceExpired`, `ConversationScreen/PermissionDenied`, `ConversationScreen/Failed`, `ConversationScreen/Completed`, and `ConversationScreen/Archived`.
- Storybook interaction tests must cover transcript group collapse/expand, tool detail expansion, thinking-process tab switching, and composer submit behavior.
- Storybook visual tests must assert the Playwright screenshot owners listed in the Visual Acceptance Map across the required responsive viewport checks.

## Visual Regression Source List

All files under `docs/superpowers/artifacts/react-agent-workbench-design/` are registered in `MANIFEST.sha256`.

The required visual regression owners are:

- `WorkbenchShell/FigmaThumbnailReference`
- `HomeStartPanel/Initial`
- `Composer/RequirementDraft`
- `RequirementReviewPanel/NeedsConfirmation`
- `StrategyGraphCanvas/SearchStrategy`
- `ThinkingProcessRail/RoundTimeline`
- `CandidateQueue/Empty`
- `CandidateQueue/Populated`
- `CandidateDetailDrawer/Summary`
- `ResumeEvidencePanel/FullContent`
- `Transcript/CollapsedRunGroup`
- `Transcript/ExpandedRunGroup`
- `Transcript/ToolReadDetails`
- `Transcript/WebSearchRunning`
- `Transcript/FileSearchComplete`
- `Transcript/FileReadRunning`
- `Transcript/GuidedFollowup`

The build gate fails if a registered visual reference lacks a route, component, story, or Playwright screenshot owner.

Supplemental non-asset regression coverage:

- `StrategyGraphCanvas/LargeSearchStrategy` exercises the dense read-only strategy timeline with requirements, round query, Liepin source_result, Top Pool, feedback, and final_summary coverage. Playwright owner: `workbench-strategy-graph-large`.
