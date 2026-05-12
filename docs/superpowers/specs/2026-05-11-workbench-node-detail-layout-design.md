# Workbench Node Detail Layout Design

## Purpose

The recruiter workbench should make the real CTS and Liepin search process understandable to business users. The strategy graph is a workflow projection, not the workflow engine. CTS still runs through the existing runtime and flywheel/corpus stores. Liepin still runs through the protected worker, connection state, detail approval, and ledger. The UI must adapt to those backend facts instead of inventing design-only fields.

This spec covers the workbench UI, graph candidate read model, node details, safe resume snapshot projection, and recruiter-facing notes. It does not redesign the CTS runtime, Liepin worker, benchmark method, or multi-source execution policy.

## Current Code Facts

- CTS CLI/runtime already works and writes durable runtime/flywheel/corpus artifacts.
- `RunArtifacts` has `run_id` and `run_dir`; Workbench source runs persist the runtime `run_id` internally when CTS allocates it.
- Flywheel already stores `run_queries` and `query_resume_hits`, including `round_no`, `query_instance_id`, `query_fingerprint`, `lane_type`, `query_role`, scoring fields, and final candidate status.
- Corpus already stores `resume_documents` and `resume_observations`, including normalized text/sections, artifact refs, eligibility flags, and per-run/query observation metadata.
- `candidate_review_items` is for review/final/actionable candidates. It must not become the recall pool.
- `buildRunStory()` creates graph nodes from durable session events plus review/detail read models. Review items are used for final/actionable candidate aggregation, not as a recall pool.
- `StrategyGraph` already uses `@xyflow/react` and ELK. React Flow is the correct interaction surface for pan, zoom, and local node drag. Pretext is not used for graph dragging.

## Layout

The workbench keeps the three-column shell:

1. Left session column.
2. Center strategy graph.
3. Right inspector column.

The left `岗位简报` is a fixed-height collapsible card. Collapsed state shows job title, session status, source count, JD preview, and notes preview when notes exist. Expanded state shows the full JD and notes in the left column.

The left criteria area is agent-first. Empty triage does not render blank textareas. The center graph shows `启动 Agent`; clicking it calls triage prepare and does not start sources. After the agent extracts criteria, the left card shows a readonly review card with `确认并开始检索` and `修改`. Textareas appear only after `修改`. Human confirmation is the default; bypass remains a configuration mode outside this layout change.

Source selection happens when the session is created and is represented by source cards in the left column. The graph and running notes do not show source filter selectors. The central graph start button starts the selected session sources through the existing session start API.

The right inspector column has exactly two tabs:

- `运行笔记`
- `节点详情`

`运行笔记` is the default tab. Clicking a graph node switches to `节点详情`. There is no standalone right-rail `候选人队列` tab. Candidate review data remains an internal read/action model; it becomes visible only through the graph node that owns the business context.

Node-scoped candidates appear in the node detail for the workflow node that produced, scored, approved, or aggregated them. The final shortlist appears in the `最终短名单` graph node's `节点详情`. Do not turn every candidate into a top-level graph node.

## Strategy Graph

The graph remains an interactive React Flow surface laid out by ELK plus local post-processing. ELK computes initial structure; the workbench applies domain-specific round-row layout after ELK.

The graph is a left-to-right business map. The start node is initially anchored near the left canvas border at vertical center. The final node is initially anchored near the right canvas border at vertical center. Intermediate nodes expand from left to right and may fan upward/downward like a horizontal mind map.

The user can pan and zoom the full canvas. Nodes can be dragged locally to resolve overlap or temporarily organize a complex run. User-dragged positions take precedence over automatic relayout during the current session view. Dragged node positions are not persisted in this version.

The graph uses virtual content bounds, not viewport clamping. Long multi-round runs may extend beyond the visible panel; users pan/zoom to inspect them. CTS round nodes must not be clamped to the visible viewport bottom because that causes later rounds to overlap.

Layout must prevent node overlap as the run grows. Prefer existing framework capability first: React Flow for interaction and ELK for initial horizontal layout. If ELK output or domain row stacking still overlaps, apply a small post-layout collision pass that pushes overlapping nodes apart without changing source-of-truth run data. Force-directed layout is not the default because this is a directional workflow map, not a free-form network.

## Multi-Round CTS Layout

Multi-round CTS iteration is shown as stacked horizontal rows inside the left-to-right map, not as a circular loop or a long single horizontal chain.

Each round starts again from a shared round-start column between the left anchored start node and the right anchored final node:

```text
第 1 轮关键词 -> 召回 -> 评分 -> 反思
第 2 轮关键词 -> 召回 -> 评分 -> 反思
第 3 轮关键词 -> 召回 -> 评分 -> 反思
```

All `第 N 轮关键词` nodes are aligned to that round-start column, not to the canvas border. For `N > 1`, the keyword node has two incoming business edges:

1. From `需求拆解`, labeled as stable requirement context.
2. From `第 N-1 轮反思`, labeled as reflection-driven adjustment.

The graph may summarize lane information on the row. Detailed exploit/explore lane breakdown appears in node detail.

## CTS Internal Search Lanes

CTS round details are lane-driven. The UI must not invent fixed lanes.

The backend-owned fields are:

- `lane_type`
- `query_role`
- `query_instance_id`
- `query_fingerprint`
- query terms

Known lane meanings:

- `exploit`: main narrowing query path.
- `prf_probe`: candidate-feedback/PRF-derived exploration.
- `generic_explore`: fallback exploration when PRF is unavailable or rejected.

If a round only executed `exploit`, the UI shows one lane. If a round executed `prf_probe` or `generic_explore`, the UI labels those lanes in recruiter language. Unknown backend-defined lane values are displayed as safe backend labels and are not dropped.

## Source-Run Runtime Link

Workbench needs a stable internal link from a Workbench `source_run_id` to the runtime/flywheel `run_id`.

`source_runs.runtime_run_id` is added as an internal field. It is written as soon as the CTS runtime allocates a `run_id`, not only when the runtime completes. Normal session/source card API responses do not expose `runtime_run_id` and never expose `run_dir`.

Graph candidate reads use:

```text
session_id + source_run_id -> runtime_run_id -> flywheel/corpus stores
```

The CTS source-run lifecycle is:

```text
queued -> running -> runtime_run_id_attached -> completed | failed
```

`runtime_run_id_attached` is an internal persistence milestone, not a user-facing status. Completion must be idempotent. If a source run is missing the internal runtime link, graph candidate reads return a scoped recoverable empty state with a business-safe reason such as `runtime_link_missing`. This layout slice does not expose an automatic UI or read-path repair/backfill operation.

## Graph Candidate Read Model

Workbench does not add a `candidate_graph_relationships` shadow table and does not copy the recall pool into `candidate_review_items`.

The graph candidate read model is generated from authoritative stores:

- CTS recall and scoring nodes read from `flywheel.query_resume_hits` and `flywheel.run_queries`.
- Resume display metadata reads from `corpus.resume_documents` and `resume_observations`.
- Final/actionable candidates read from `candidate_review_items` and `candidate_evidence`.
- Liepin candidate/detail nodes read from existing Liepin review/evidence/detail request state.

The API returns safe `GraphCandidateSummary` rows for the selected graph node only. It does not return full resumes or raw provider payloads.

Graph candidate reads are paginated and stably ordered:

```http
GET /api/workbench/sessions/{session_id}/graph-candidates?node_id={node_id}&limit=50&cursor={cursor}
```

Default `limit` is 50. The backend enforces a maximum limit. The response returns `nextCursor`, `totalEstimate`, and `truncated`. Cursor values are opaque and server-verifiable. Sorting is stable per node kind:

- CTS recall nodes: round, lane, query order, provider rank, then deterministic candidate key.
- CTS scoring nodes: fit bucket, score descending, then deterministic candidate key.
- Final nodes: final score descending, review status, then deterministic candidate key.
- Liepin approval nodes: pending first, then most recently updated.

`graphCandidateId` is opaque. It must not expose `runtime_run_id`, resume document ids, artifact ids, provider ids, provider keys, detail URLs, or filesystem paths. The server resolves it only inside the authenticated tenant/workspace/user/session/node scope and must be able to reject forged, stale, cross-session, or cross-node ids.

Minimum safe shape:

```ts
type GraphCandidateSummary = {
  graphCandidateId: string;
  sourceKind: 'cts' | 'liepin';
  sourceRunId: string;
  nodeKind: 'recall' | 'scoring' | 'final' | 'liepin_card' | 'detail_approval';
  roundNo: number | null;
  laneType: string | null;
  queryRole: string | null;
  relationshipKind: 'recalled' | 'new' | 'scored' | 'fit' | 'not_fit' | 'final' | 'detail_requested';
  displayName: string;
  title: string;
  company: string;
  location: string;
  sourceBadges: string[];
  score: number | null;
  fitBucket: string | null;
  summary: string;
  matchedMustHaves: string[];
  strengths: string[];
  missingRisks: string[];
  reviewItemId: string | null;
  evidenceLevel: 'card' | 'detail' | 'final' | null;
  detailOpenRequestId: string | null;
  canExpandResume: boolean;
  canMarkPromising: boolean;
  canReject: boolean;
  canSaveNote: boolean;
  canRequestDetail: boolean;
  canOpenProvider: boolean;
};

type GraphCandidateListResponse = {
  nodeId: string;
  items: GraphCandidateSummary[];
  nextCursor: string | null;
  totalEstimate: number | null;
  truncated: boolean;
  generatedAt: string;
};
```

Capabilities are backend-derived. The frontend does not guess which actions are allowed.

## Resume Snapshot Projection

Candidate cards show safe summaries by default. Complete resume content is loaded lazily only after the user expands an individual candidate card.

The UI uses a session-scoped graph-candidate endpoint:

```http
GET /api/workbench/sessions/{session_id}/graph-candidates/{graph_candidate_id}/resume-snapshot
```

The endpoint resolves the graph candidate to corpus/review state inside the authenticated tenant/workspace/user/session/node scope and returns a safe display model. It does not read from a Workbench-owned copied snapshot table.

The projection is allowlist based. It can include normalized profile, work experience, education, projects, skills, and safe source evidence text. It must not include cookies, authorization headers, storage state, CDP/WebSocket endpoints, provider account hashes, raw provider control payloads, auth-bearing provider URLs, or raw artifact paths.

Complete resume snapshots are allowed in authenticated UI detail views and benchmark/corpus storage. They are fetched only while the individual candidate card is expanded. They are not allowed in:

- memory rows
- session events
- SSE responses
- running-note payloads
- graph story payloads
- public artifacts
- ordinary logs
- persisted frontend query cache, `localStorage`, or `sessionStorage`
- frontend error boundaries, console logs, or test debug output

Snapshot queries use short-lived in-memory cache only. Session, workspace, or logout changes clear snapshot queries. Collapsing a card may leave a short-lived in-memory cache entry, but it must not be persisted.

## Node Details

`节点详情` renders structured payload first, then node-scoped candidates or approval cards where relevant.

Node types:

- `岗位`: job title, JD preview, notes preview, and source mode.
- `需求拆解`: must-haves, nice-to-haves, query hints, exclusions, and triage status.
- `第 N 轮关键词`: query terms, query label, search direction, and lane breakdown.
- `CTS 召回`: recall counts plus this round's recalled/new candidate cards.
- `CTS 评分`: scored candidate cards, fit/not-fit labels, score, and reasons.
- `反思`: summary, rationale, and next direction.
- `猎聘简介抓取`: scanned card count, unique candidate count, and related candidates.
- `猎聘候选人初筛`: candidate cards, score, source badge, and AI screening reasons.
- `详情审批`: detail request cards with approval status, budget impact, blocked reason, and approve/reject controls.
- `最终短名单`: aggregated review-backed candidates with final score and source badges.

Any recalled resume that belongs to a selected recall/scoring node must be visible in that node's candidate list, even when it is not a final candidate or review-backed candidate. Recall nodes show this round's recalled resumes. Scoring nodes show resumes that entered scoring or already have scoring metadata. The final shortlist node is the only node that means final shortlist.

Candidate cards have a fixed collapsed height. Collapsed cards show safe identity, title/company/location, source badges, score/fit bucket, short summary, matched must-haves, strengths, and risks. Expanding a card fetches the safe resume snapshot. A failed snapshot fetch is localized to that card.

Candidate cards must not disguise missing data as a real candidate. If a real safe identity or resume summary is available from CTS runtime/flywheel/corpus projection, show it. If the safe snapshot or normalized summary is missing or forbidden, show a clear business-safe state such as `简历快照未写入`, `简历快照受限`, or `简历摘要暂不可展示`. Do not show opaque hash suffixes as if they were candidate names.

The `最终短名单` node renders review-backed candidates and exposes review actions: mark promising, reject, save note, request detail, and open provider where allowed. Recall-only graph candidates remain read-only except safe snapshot expansion when allowed.

## Liepin Detail Approval

The `详情审批` node is an interactive approval surface. Pending request cards show the candidate summary, AI recommendation, budget impact, current detail-open mode, and `批准打开` / `暂不打开`. Approved/leased/opened requests show budget reservation and provider action. Rejected requests show that no quota was consumed. Blocked requests show the blocked reason.

Approving or rejecting invalidates detail requests, session-scoped graph candidates, graph candidate snapshots, session/source cards, and the session list.

The approve/reject endpoints re-check scope, request status, budget state, and backend capability at mutation time. Frontend capability flags are display hints only. Double approve, approve-after-reject, reject-after-approve, and blocked approve are rejected or idempotently resolved without consuming extra quota. Ledger writes and request state updates are atomic.

Approval discoverability remains visible through:

- pending count on the left source card
- human-action-required running note
- badge count on the `详情审批` graph node
- session list pending approval badge when available

## Running Notes

`运行笔记` is a business narrative layer, not a technical log and not a node list.

The graph answers "where the workflow is." Running notes answer "what this means for the search."

Running notes are generated by a lightweight model-based Note Writer as the primary user-facing path. The UI does not directly map raw runtime events to user-visible notes. Technical audit, replay, and debugging evidence remain in backend events and artifacts under the repository artifact system; those technical logs are not the right-inspector user experience.

While a session is running, the Note Writer wakes every 10 seconds and receives a compact fact projection for the current session. It returns one plain Chinese note string, without requiring structured output. The output length target is 20 to 60 Chinese characters.

The Note Writer context includes:

- job title and confirmed requirement criteria
- session status, active source, active business stage, current graph node, and current round when known
- recall, scoring, detail-approval, and final-shortlist counts
- recent business facts derived from runtime/workbench events
- previously visible running notes, capped to the latest 15 entries when the log is long
- a status hint such as new progress, waiting, human action required, completed, failed, or canceled

The Note Writer context must not include full resume snapshots, raw provider payloads, cookies, auth state, raw artifact paths, filesystem paths, debug stack traces, or unredacted technical logs.

The Note Writer receives enough current facts and prior visible notes to write the next user-facing progress note. The product does not hard-code a "same stage means same note" rule into the prompt. The model decides how to describe the current progress based on context. The last waiting note remains visually alive through the waiting shimmer described below while the session is still working.

Suggested Note Writer system prompt:

```text
你是招聘工作台的运行笔记撰写器。你的读者是猎头和招聘业务人员，不是工程师。

你只写一条中文运行笔记，解释当前 agent 正在做什么、为什么还需要等待、或者刚刚取得了什么进展。

要求：
- 只输出一条自然中文句子。
- 输出 20 到 60 个中文字。
- 不要 Markdown。
- 不要时间。
- 不要技术 ID、runtime、controller、event、artifact、cursor、job id。
- 不要暴露内部错误栈或开发日志。
- 不要编造候选人数量、评分、阶段或结论。
- 你会看到此前已经展示给用户的运行笔记，请结合当前上下文写出当前进展。
- 语气专业、克制、给业务人员可读。
```

Notes always show all selected sources and use source badges for source-specific entries. Entries render as a plain streaming log: no per-entry timestamp, no card frame, and no separate graph-node title above the log text. Raw event names are not shown in the right inspector. This slice does not expose a developer log in `运行笔记`.

The newest note uses a high-performance text reveal: render the full text once in a light base layer and reveal a darker overlay with CSS `clip-path` or mask animation. Do not implement character streaming by repeatedly updating React state per character or by wrapping every character in its own long-lived DOM node.

When the latest note represents an ongoing wait, the note shows a white diagonal `/` shimmer bar sweeping left to right. The shimmer indicates the agent is still working; it is not a timestamp or progress percentage.

Good notes include:

- the round goal or query change reason
- recall quality changes
- scoring outcome and what it implies
- reflection conclusion and next direction
- Liepin serial progress, budget, approval, or blocked status
- human action required

Example:

```text
CTS 第 2 轮：用“实时平台 / Flink CDC”放宽搜索，搜到 8 人，3 人进入评分，1 人 fit。反思认为 Kafka 关键词过窄。
```

Running notes do not select graph nodes and do not switch the right inspector to node detail. Process narration and data inspection stay separate: clicking graph nodes opens `节点详情`; running notes remain a readable process stream.

## Event Replay And Idempotency

Workbench events remain the durable audit stream for UI reconstruction. New or migrated events include schema metadata sufficient for replay:

- global sequence
- session sequence when a session exists
- source run id when source-specific
- event name
- schema version
- occurred/ingested timestamp when available
- idempotency key when the producer can provide one

`buildRunStory()` must tolerate duplicate, missing, unknown, and out-of-order events. Duplicate events do not create duplicate graph nodes, facts, or candidate counts. Unknown event names are hidden from business notes. Legacy composite `runtime_round_completed` remains supported, but split runtime events are preferred.

## Failure And Recovery States

The UI must render recoverable states without exposing backend internals:

- `runtime_link_missing`: CTS runtime data may exist, but Workbench cannot yet link to it.
- `runtime_artifacts_unavailable`: the runtime link exists, but flywheel/corpus data cannot be read.
- `candidate_resolution_failed`: a graph node exists, but its candidates cannot be resolved.
- `snapshot_forbidden`: the user cannot open the selected candidate snapshot.
- `snapshot_not_found`: the candidate no longer resolves to a safe snapshot.
- `detail_request_stale`: the approval request changed before the user acted.
- `detail_request_blocked`: budget or lease rules prevented detail opening.

Business UI shows concise action-oriented text. Developer logs may include redacted technical context.

The snapshot endpoint and frontend status union are limited to `ready`, `snapshot_forbidden`, and `snapshot_not_found` in this slice.

## Privacy And Security

All candidate and resume data is tenant/workspace/user/session scoped.

Graph/story/event/note payloads contain only safe summaries and identifiers. Complete resume content appears only after authenticated user action against the snapshot endpoint. Logs and exception serializers continue using centralized redaction.

All new API routes use explicit response models and allowlist projection. Field-level authorization is enforced by construction: routes return only fields defined in the response model and never serialize internal runtime objects, raw provider payloads, exception reprs, or storage rows directly.

## Testing Requirements

Backend:

- CTS start attaches `source_runs.runtime_run_id` as soon as the runtime allocates `run_id`.
- CTS completion is idempotent and does not lose the runtime link on retry.
- Missing CTS runtime links are surfaced as recoverable empty graph-candidate states instead of exposing runtime internals. Automatic operational repair is outside this UI layout slice.
- Session/source APIs do not expose `runtime_run_id`, `run_dir`, or artifact paths.
- Graph candidate API enforces tenant/workspace/user/session/node scope.
- Graph candidate API uses opaque candidate ids, pagination, stable ordering, and response models.
- Graph candidate API reads CTS recall/scoring candidates from flywheel/corpus by runtime run id.
- Graph candidate API reads final/actionable candidates from review/evidence state.
- Resume snapshot endpoint enforces scope and returns allowlisted display data.
- Complete resume data does not enter `session_events`, SSE responses, running notes, graph story payloads, memory rows, frontend persistent cache, logs, or error payloads.
- Detail approval approve/reject remains scoped and CSRF protected.
- Detail approval double-submit, stale status, cross-user, and budget exhaustion cases are tested.
- Event replay is idempotent under duplicate/out-of-order events.

Frontend:

- Empty triage starts with `启动 Agent`, not blank editable criteria fields.
- Prepare calls triage prepare and does not start sources.
- Confirm calls approve then start.
- The right inspector has exactly `运行笔记` and `节点详情`.
- Source filters are absent from the graph and running notes.
- There is no standalone `候选人队列` tab; final shortlist candidates render inside the `最终短名单` node detail.
- Selecting a candidate-bearing graph node lazily fetches graph candidates.
- Any recalled resume is visible from the relevant recall/scoring node detail, not only from the final shortlist.
- Candidate cards do not present opaque hash placeholders as candidate names when real safe resume information is missing.
- Switching nodes aborts or ignores stale graph candidate and snapshot responses.
- Expanding a candidate card lazily fetches a safe resume snapshot.
- Resume snapshot queries use short-lived in-memory cache and are cleared on session/workspace/logout changes.
- Detail approval nodes can approve/reject pending requests.
- Pending detail approvals remain discoverable from source indicators, running notes, and the `详情审批` node.
- Strategy graph starts near the left canvas edge at vertical center, ends near the right canvas edge at vertical center, expands left-to-right, avoids node overlap, and preserves local user drags during the current session view.
- CTS single-lane rounds do not invent explore lanes.
- Running notes are generated by the lightweight Note Writer from compact session facts plus the latest 15 visible notes, do not expose raw event names, and do not duplicate every graph node as a log line.
- The latest waiting note remains alive through the shimmer treatment while the session is still working.
- Graph node interactions remain keyboard-accessible.

## Non-Goals

- Persisting custom graph node positions.
- Turning every candidate into a top-level graph node.
- Adding `candidate_graph_relationships` or `candidate_resume_snapshots` shadow tables.
- Copying recall pool candidates into `candidate_review_items`.
- Replacing React Flow or ELK.
- Introducing Pretext for graph interaction.
- Adding a third right-rail candidate tab.
- Changing source execution semantics.
- Exposing provider raw payloads in normal UI.

## Acceptance Criteria

- A recruiter can start with a JD, let the agent extract criteria, confirm criteria, and run selected sources from the central graph action.
- The graph shows selected-source workflow by default without source filters.
- Multi-round CTS expands left-to-right between the anchored start and final nodes, aligns keyword nodes to a shared round-start column, avoids overlap, and shows both stable requirement and previous-reflection inputs for `N > 1`.
- Clicking CTS recall/scoring/final nodes shows candidates for that exact node.
- Node candidate lists are paginated, stable, and do not overload the graph.
- CTS node details expose real exploit/PRF/generic lane structure when backend data exists.
- Clicking Liepin candidate/detail/final nodes shows related candidates or approval requests.
- Candidate cards are compact by default and expandable for safe complete resume snapshots.
- Detail approval remains usable from `节点详情`.
- Running notes tell a business-readable story across selected sources.
- Complete resume data stays out of memory, events, running notes, graph payloads, and public artifacts.
