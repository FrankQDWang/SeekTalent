# Agent Tool And Requirement Contracts

This compacted document preserves the content from the source documents below. Source headings are demoted one level so the merged document remains navigable without changing the substantive requirements.

## Source Documents

- `03-agent-tool-contract.md`
- `04-requirement-review-contract.md`

---

## Source: `03-agent-tool-contract.md`

## Agent Tool Contract

### Tool Surface

The conversational agent may call only this runtime-control surface.

```text
extract_requirements
get_requirement_draft
update_requirement_draft
amend_requirement_draft_from_text
resolve_requirement_review
confirm_requirements
start_workflow
get_workflow_snapshot
list_workflow_events
request_pause
request_cancel
resume_workflow
submit_next_round_requirement
get_runtime_detail
prepare_final_summary
```

This surface contains 15 operations. Goal 2 may implement them through a single agent-facing adapter or facade, but it must not silently drop `get_workflow_snapshot`, `list_workflow_events`, or running amendment review resolution because the current runtime-control implementation splits ownership across multiple classes.

### JSON Example Rules

JSON blocks in this document are shape examples, not permission to return schema-only empty payloads.

Examples use the agent/tool-facing camelCase names. The current Python runtime-control models persist the same concepts with snake_case fields such as `draft_revision_id`, `section_id`, `item_id`, and `runtime_run_id` until a conversation-agent API facade maps them.

For a JD or free-form requirement text that contains extractable hiring requirements, runtime-control tests must prove the returned draft contains real, non-empty, item-level editable sections, persisted selection state, stable item ids, and a non-empty draft/read model appropriate to the operation.

Empty arrays or objects may appear only when the business state is genuinely empty and the response carries an explicit status or reason code that explains why no items or facts exist.

Source ids in examples are placeholders for ids returned by the active source catalog or registry. Product code must not hard-code `cts` and `liepin` as the complete source universe, even when current fixtures or Workbench UI still expose those sources.

### Tool Semantics

#### `extract_requirements`

Input:

```json
{
  "conversationId": "agent_conv_01HX...",
  "jobTitle": "可选标题",
  "jdText": "用户输入的 JD 或招聘需求",
  "notes": "可选补充",
  "sourceIds": ["source_id_from_catalog_a", "source_id_from_catalog_b"],
  "idempotencyKey": "agent_conv_01HX:extract:1"
}
```

Output:

```json
{
  "conversationId": "agent_conv_01HX...",
  "draftRevisionId": "reqdraft_01HX...",
  "status": "draft_ready",
  "canConfirm": true,
  "unresolvedReviewItemCount": 0,
  "sections": [
    {
      "sectionKey": "must_have_capabilities",
      "items": [
        {
          "itemId": "reqitem_1",
          "selected": true,
          "text": "Python 后端 API 研发经验",
          "source": "extracted"
        }
      ]
    }
  ],
  "latest": true
}
```

The tool runs requirement extraction through runtime-control. It must persist the draft and return item-level editable sections.

#### `get_requirement_draft`

Input:

```json
{
  "conversationId": "agent_conv_01HX...",
  "draftRevisionId": "reqdraft_01HX..."
}
```

`draftRevisionId` may be omitted to read the latest draft for the conversation.

Output:

```json
{
  "conversationId": "agent_conv_01HX...",
  "draftRevisionId": "reqdraft_01HX...",
  "status": "draft_ready",
  "canConfirm": true,
  "unresolvedReviewItemCount": 0,
  "sections": [
    {
      "sectionKey": "must_have_capabilities",
      "items": [
        {
          "itemId": "reqitem_1",
          "selected": true,
          "text": "Python 后端 API 研发经验",
          "source": "extracted"
        }
      ]
    }
  ],
  "latest": true
}
```

The response shape must satisfy `04-operating-policies-and-runtime-contracts.md`. It is the canonical backend payload for the future requirement-review UI. Missing drafts return `requirement_draft_not_found`.

#### `update_requirement_draft`

Input:

```json
{
  "draftRevisionId": "reqdraft_01HX...",
  "baseRevisionId": "reqdraft_01HW...",
  "operations": [
    {"op": "set_selected", "itemId": "reqitem_1", "selected": false},
    {"op": "edit_text", "itemId": "reqitem_2", "text": "Python 后端 API"},
    {"op": "move_item", "itemId": "reqitem_3", "targetSection": "preferred_capabilities"}
  ],
  "idempotencyKey": "agent_conv_01HX:draft-edit:3"
}
```

Output:

```json
{
  "draftRevisionId": "reqdraft_01HY...",
  "status": "draft_ready",
  "sections": [
    {
      "sectionKey": "preferred_capabilities",
      "items": [
        {
          "itemId": "reqitem_3",
          "selected": true,
          "text": "Python 后端 API",
          "source": "user_edit"
        }
      ]
    }
  ]
}
```

The tool creates a new revision. It must reject stale base revisions with `requirement_draft_stale`.

Stale rejection output:

```json
{
  "status": "rejected",
  "reasonCode": "requirement_draft_stale",
  "latestDraftRevisionId": "reqdraft_01HZ...",
  "latestSections": [
    {
      "sectionKey": "must_have_capabilities",
      "items": [
        {
          "itemId": "reqitem_1",
          "selected": true,
          "text": "Python 后端 API 研发经验",
          "source": "extracted"
        }
      ]
    }
  ],
  "conflictSummary": {
    "baseRevisionId": "reqdraft_01HW...",
    "attemptedOperations": 3,
    "message": "The draft changed before this edit was applied."
  }
}
```

The agent must not silently merge stale edits. It must show the latest draft and ask the user to confirm whether to reapply the intended edit.

#### `amend_requirement_draft_from_text`

Input:

```json
{
  "draftRevisionId": "reqdraft_01HX...",
  "baseRevisionId": "reqdraft_01HX...",
  "text": "另外希望候选人有 Kafka 实战，频繁跳槽的不要。",
  "targetSectionHint": null,
  "idempotencyKey": "agent_conv_01HX:freeform-amend:4"
}
```

Output:

```json
{
  "draftRevisionId": "reqdraft_01HZ...",
  "status": "draft_ready",
  "sections": [
    {
      "sectionKey": "must_have_capabilities",
      "items": [
        {
          "itemId": "reqitem_10",
          "selected": true,
          "text": "Kafka 生产环境实战",
          "source": "user_free_text"
        }
      ]
    }
  ],
  "amendment": {
    "amendmentId": "reqamend_01HZ...",
    "status": "applied",
    "addedItemIds": ["reqitem_10", "reqitem_11"],
    "changedItemIds": []
  }
}
```

The tool normalizes free-form user input through the Workflow Runtime requirement parsing path. The conversational agent may pass a target section hint from UI context, but it must not decide final backend fields on its own.

If normalization is ambiguous, runtime control returns structured candidate items with `status="needs_review"` and the transcript must show them for user confirmation before final requirement confirmation.

#### `resolve_requirement_review`

Input:

```json
{
  "draftRevisionId": "reqdraft_01HZ...",
  "baseRevisionId": "reqdraft_01HZ...",
  "amendmentId": "reqamend_01HZ...",
  "runtimeRunId": null,
  "baseApprovedRequirementRevisionId": null,
  "targetRoundNo": null,
  "operations": [
    {
      "op": "accept_candidate",
      "reviewItemId": "reviewitem_1",
      "targetSection": "must_have_capabilities",
      "text": "Kafka 生产环境实战"
    },
    {
      "op": "reject_fragment",
      "reviewItemId": "reviewitem_2",
      "reasonCode": "not_a_requirement"
    }
  ],
  "idempotencyKey": "agent_conv_01HX:review-resolve:1"
}
```

Output:

```json
{
  "draftRevisionId": "reqdraft_01JA...",
  "status": "draft_ready",
  "resolvedAmendmentId": "reqamend_01HZ...",
  "sections": [
    {
      "sectionKey": "must_have_capabilities",
      "items": [
        {
          "itemId": "reqitem_10",
          "selected": true,
          "text": "Kafka 生产环境实战",
          "source": "user_review_resolution"
        }
      ]
    }
  ]
}
```

This tool resolves `needs_review` items produced by free-form amendment normalization. Draft-time resolution creates a new draft revision. Running next-round resolution creates or updates a pending approved requirement revision for a future round. It must reject stale base revisions with `requirement_draft_stale` or the running amendment equivalent reason code.

For draft-time amendments, the request uses `draftRevisionId` and `baseRevisionId`.

For running next-round amendments, the request uses `runtimeRunId`, `baseApprovedRequirementRevisionId`, and the amendment id. Runtime control validates that the base approved requirement revision is still the scheduled parent for that pending amendment before creating a resolved approved requirement revision.

Implementation readiness note: if runtime-control still only supports draft-time review resolution when Goal 2 starts, close that runtime-control gap before exposing running next-round review states through the conversation agent. The agent must not fake review scheduling or activation with local transcript state.

Allowed operations:

```text
accept_candidate
edit_candidate
move_candidate
reject_candidate
reject_fragment
```

Final requirement confirmation is blocked while the latest draft revision contains unresolved review-required items.

#### `confirm_requirements`

Input:

```json
{
  "draftRevisionId": "reqdraft_01HY...",
  "baseRevisionId": "reqdraft_01HY...",
  "idempotencyKey": "agent_conv_01HX:confirm:1"
}
```

Output:

```json
{
  "approvedRequirementRevisionId": "reqapproved_01HZ...",
  "requirementSheet": {
    "must_have_capabilities": ["Python 后端 API 研发经验"],
    "preferred_capabilities": ["Kafka 生产环境实战"],
    "hard_constraints": [],
    "exclusion_signals": ["频繁跳槽且无稳定项目经历"],
    "initial_query_term_pool": [
      {
        "term": "Python 后端",
        "enabled": true
      }
    ]
  },
  "status": "confirmed"
}
```

Only selected items become active runtime requirement input. Deselected items are preserved as revision history.

`confirm_requirements` must reject stale base revisions with `requirement_draft_stale`.

#### `start_workflow`

Input:

```json
{
  "approvedRequirementRevisionId": "reqapproved_01HZ...",
  "sourceIds": ["source_id_from_catalog_a", "source_id_from_catalog_b"],
  "idempotencyKey": "agent_conv_01HX:start:1"
}
```

Output:

```json
{
  "runtimeRunId": "runtime_run_01HZ...",
  "workbenchSessionId": "session_...",
  "status": "running"
}
```

Duplicate start with the same approved revision returns the existing run.

#### `get_workflow_snapshot`

Input:

```json
{
  "runtimeRunId": "runtime_run_01HZ..."
}
```

Output:

```json
{
  "runtimeRunId": "runtime_run_01HZ...",
  "status": "running",
  "currentStage": "scoring",
  "currentRound": 2,
  "latestEventsCursor": 241,
  "snapshot": {
    "eventSeq": 241,
    "selectedSourceIds": ["source_id_from_catalog_a"],
    "candidateCounts": {
      "rawReturned": 42,
      "uniqueIdentities": 28,
      "scored": 18
    }
  }
}
```

The snapshot payload follows `03-runtime-control-state-and-events.md`. It must be read from persisted runtime-control snapshot state, not reconstructed from agent memory, SDK session state, or frontend state.

#### `list_workflow_events`

Input:

```json
{
  "runtimeRunId": "runtime_run_01HZ...",
  "afterSeq": 128,
  "limit": 100
}
```

Output:

```json
{
  "events": [
    {
      "eventId": "rtevt_01HZ...",
      "eventSeq": 164,
      "eventType": "runtime_round_scoring_completed",
      "runtimeRunId": "runtime_run_01HZ..."
    }
  ],
  "nextCursor": 164
}
```

Events are persisted runtime-control events. The agent may summarize them but must not invent progress.

Goal 2 also projects these events into durable transcript activity items. Tool output must therefore preserve structured fields needed for deterministic projection, including `eventType`, `stage`, `roundNo`, `sourceId`, `status`, `payload`, and stable ids in `payload` such as command id, requirement revision id, source dispatch id, checkpoint id, and final summary id. The agent must not parse localized event summary text to decide activity identity or lifecycle status.

#### `request_pause`, `request_cancel`, `resume_workflow`

Each writes a command row and returns command state.

```json
{
  "commandId": "rtcmd_01HZ...",
  "status": "accepted",
  "effectiveAt": "next_safe_boundary"
}
```

The runtime control plane later emits command-applied or command-rejected events.

#### `submit_next_round_requirement`

Input:

```json
{
  "runtimeRunId": "runtime_run_01HZ...",
  "text": "刚才忘了说，下一轮请加上 Kafka 实战，频繁跳槽的不要。",
  "targetSectionHint": null,
  "idempotencyKey": "runtime_run_01HZ:amend:1"
}
```

Amendment statuses:

```text
pending_target_round
needs_review
applied
rejected
superseded
```

Output:

```json
{
  "amendmentId": "reqamend_01JA...",
  "status": "pending_target_round",
  "targetRoundNo": 3,
  "effectiveBoundary": "before_round_controller",
  "reviewRequired": false,
  "approvedRequirementRevisionId": "reqapproved_01JA..."
}
```

The tool accepts free-form text during an active run. Runtime control normalizes the text through the Workflow Runtime requirement parsing path, creates a new requirement revision derived from the currently active approved revision, and schedules it for the next not-yet-locked round.

`submit_next_round_requirement` normally returns `pending_target_round`, `needs_review`, or `rejected`. `applied` is reached later when runtime-control applies the amendment at `before_round_controller`; `superseded` is reached only after explicit replacement/withdrawal or idempotent replay resolution.

The patch does not apply at arbitrary safe boundaries. It applies only immediately before `runtime_round_input_locked` for the target round. If the current round has already emitted `runtime_round_input_locked`, the target is a later round.

Multiple next-round amendments for the same future round accumulate in creation order by default. A later user addition must not automatically supersede an earlier addition for the same `(runtimeRunId, targetRoundNo)`.

Supersession is allowed only when the user explicitly replaces or withdraws a pending amendment, or when the same idempotency key is replayed. Superseded amendments must retain their audit rows and emit `runtime_command_superseded` or `runtime_next_round_requirement_superseded`.

If a running amendment normalizes to `needs_review`, runtime control returns a review payload and does not activate the requirement change until the user resolves it. If the target round locks before resolution, runtime control retargets the resolved amendment to the next not-yet-locked round. If no future round exists, it rejects the resolution with `runtime_no_future_round_available`.

#### `get_runtime_detail`

Input:

```json
{
  "runtimeRunId": "runtime_run_01HZ...",
  "kind": "candidate_score",
  "roundNo": 2,
  "sourceId": null,
  "candidateId": "cand_01HZ...",
  "commandId": null,
  "checkpointId": null,
  "eventId": null,
  "includeArtifacts": false,
  "limit": 20
}
```

Output:

```json
{
  "kind": "candidate_score",
  "runtimeRunId": "runtime_run_01HZ...",
  "roundNo": 2,
  "title": "候选人评分详情",
  "summary": "该候选人匹配 Python 后端和 Kafka 实战要求。",
  "facts": [
    {
      "label": "匹配能力",
      "value": "Python 后端 API 研发经验",
      "sourceEventId": "rtevt_..."
    }
  ],
  "sourceEventIds": ["rtevt_..."],
  "sourceCheckpointIds": [],
  "artifactRefs": [],
  "redactions": []
}
```

`get_runtime_detail` must return only facts backed by events, checkpoints, Workbench-visible records, or approved artifact refs.

#### `prepare_final_summary`

Input:

```json
{
  "runtimeRunId": "runtime_run_01HZ...",
  "userInstruction": "请重点说明为什么推荐前三位候选人。",
  "sourceSnapshotEventSeq": 241,
  "idempotencyKey": "runtime_run_01HZ:final-summary:1"
}
```

Output:

```json
{
  "summaryId": "rtfinalsummary_01JA...",
  "runtimeRunId": "runtime_run_01HZ...",
  "status": "completed",
  "summary": "Run status: completed. 候选人 01HZ: Python 后端 API 研发经验。请重点说明为什么推荐前三位候选人。",
  "facts": [
    {
      "label": "Candidate",
      "value": "候选人 01HZ: Python 后端 API 研发经验"
    }
  ],
  "sourceEventIds": ["rtevt_01HZ..."],
  "sourceSnapshotEventSeq": 241,
  "latestSnapshotEventSeq": 241,
  "userInstruction": "请重点说明为什么推荐前三位候选人。"
}
```

The tool is available only after the workflow reaches `completed`, `cancelled`, or `failed`. If the run is still active, return `runtime_run_not_completed`.

The final summary must be grounded in the final snapshot, runtime events, allowed detail read models, and the user's final instruction. The agent may phrase the final response, but it must not create new candidate facts.

### Command Conflict Rules

State-changing command tools use command conflict groups:

```text
lifecycle: request_pause, request_cancel, resume_workflow
requirement_amendment: submit_next_round_requirement, resolve_requirement_review for running amendments
```

Rules:

1. Reusing the same idempotency key returns the existing command or amendment result.
2. `request_cancel` is a terminal intent. Once accepted, it supersedes pending pause/resume commands and rejects new pause/resume/amendment commands with `runtime_command_conflict`.
3. `request_pause` is accepted only while the run is `running` or `resume_requested`.
4. `resume_workflow` is accepted only while the run is `paused`.
5. A duplicate pending lifecycle command returns the existing pending command rather than creating another row.
6. Conflicting lifecycle commands return `runtime_command_conflict` with the current accepted command id and status.
7. Next-round requirement amendments accumulate unless the user explicitly replaces or withdraws a specific pending amendment.
8. Command acceptance, rejection, supersession, and application must emit runtime events.

### Reason Codes

All tools return stable reason codes for failures:

```text
requirement_draft_stale
requirement_draft_not_found
requirement_amendment_stale
requirement_draft_invalid
requirement_amendment_ambiguous
requirement_amendment_unclassifiable
requirement_not_confirmed
runtime_run_not_found
runtime_run_not_running
runtime_run_not_paused
runtime_command_conflict
runtime_command_duplicate
runtime_safe_boundary_pending
runtime_round_input_already_locked
runtime_no_future_round_available
runtime_run_not_completed
runtime_event_gap_detected
runtime_link_broken
runtime_executor_start_timeout
runtime_executor_lease_expired
runtime_checkpoint_unavailable
runtime_artifact_policy_blocked
source_id_unavailable
workbench_session_missing
requirement_review_unresolved
```

---

## Source: `04-requirement-review-contract.md`

## Requirement Review Contract

### Source Runtime Model

The current runtime uses `RequirementSheet` with these key fields:

```text
must_have_capabilities
preferred_capabilities
exclusion_signals
hard_constraints
initial_query_term_pool
```

The agent-facing confirmation model must preserve that structure while adding item-level selection and revision metadata.

### Draft Section Model

```json
{
  "sectionId": "must_have_capabilities",
  "displayName": "必须满足",
  "backendField": "must_have_capabilities",
  "items": [
    {
      "itemId": "reqitem_01HZ...",
      "selected": true,
      "editable": true,
      "text": "Python 后端 API 开发",
      "value": "Python 后端 API 开发",
      "source": "extracted",
      "status": "resolved",
      "reviewItemId": null,
      "amendmentId": null,
      "sourceSpanRefs": [],
      "sortOrder": 10,
      "allowedActions": ["select", "edit", "delete", "move_to_preferred_capabilities"]
    }
  ]
}
```

Item `source` values:

```text
extracted
user_added
user_edited
runtime_normalized
```

Item `status` values:

```text
resolved
needs_review
rejected
deleted
moved
```

Only `resolved` items can be included in an approved `RequirementSheet`.

### Section Definitions

#### 必须满足

- `sectionId`: `must_have_capabilities`
- `backendField`: `must_have_capabilities`
- value type: string list
- move target: `preferred_capabilities`

#### 加分项

- `sectionId`: `preferred_capabilities`
- `backendField`: `preferred_capabilities`
- value type: string list
- move target: `must_have_capabilities`

#### 硬性筛选条件

- `sectionId`: `hard_constraints`
- `backendField`: `hard_constraints`
- value type: structured constraint item
- supported constraint keys:
  - `locations`
  - `degree_requirement`
  - `experience_requirement`
  - `age_requirement`
  - `gender_requirement`
  - `school_names`
  - `school_type_requirement`
  - `company_names`
- move target: none

#### 排除信号

- `sectionId`: `exclusion_signals`
- `backendField`: `exclusion_signals`
- value type: string list
- move target: none

#### 检索关键词

- `sectionId`: `initial_query_term_pool`
- `backendField`: `initial_query_term_pool[].term`
- value type: query term item
- actions:
  - select/unselect;
  - enable/disable;
  - edit;
  - delete.

### Revision Rules

Every edit creates a new draft revision.

```text
draft_revision_id changes on every edit
base_revision_id prevents stale updates
selected=false removes the item from active runtime input
deleted=true preserves the item in audit history but excludes it from active runtime input
move creates a new item in the target field and marks the old field occurrence moved
```

For every state-changing draft operation, the service compares the request `base_revision_id` with the latest draft revision for the conversation. If they differ, the operation fails with `requirement_draft_stale` and returns the latest draft revision and sections. The service must not perform implicit branch merge.

### Free-Form Amendment Rules

Free-form user additions create a requirement amendment record and then a new draft revision.

Rules:

1. the agent captures the user's raw text and optional target section hint;
2. runtime control sends the raw text plus current draft context to Workflow Runtime requirement normalization;
3. normalization returns structured additions, edits, moves, rejected fragments, and review-required items;
4. accepted additions become ordinary draft items with `source=user_added` or `source=runtime_normalized`;
5. uncertain additions become draft items with review-required status and cannot be included in the approved `RequirementSheet` until resolved;
6. rejected fragments are shown in the transcript with a reason code and are not silently dropped.

Review-required items keep:

- `amendmentId`;
- `reviewItemId`;
- original raw fragment;
- normalized candidate value;
- candidate backend field when runtime normalization can infer one;
- reason code explaining why user resolution is required;
- allowed resolution actions.

### Review Resolution Rules

`resolve_requirement_review` creates a new draft revision and marks review-required items as resolved or rejected.

Rules:

1. the request must include `base_revision_id`;
2. stale base revisions are rejected with the latest draft payload;
3. accepted or edited candidates become `resolved` draft items;
4. rejected candidates remain in audit history and are excluded from active runtime input;
5. target backend fields must come from runtime-control allowed section definitions;
6. if the user edits free-form text during resolution, runtime control validates the edited value against the target field shape before accepting it;
7. all resolved items preserve provenance pointing to the original amendment and review item.

If review resolution belongs to a running next-round amendment and the original target round locks before resolution, runtime control retargets the resolved patch to the next not-yet-locked round. If no future round exists, it rejects with `runtime_no_future_round_available`.

The confirmed `RequirementSheet` is built only from selected, resolved, non-deleted draft items.

### Conversion To RequirementSheet

On confirmation:

1. include only selected, non-deleted items;
2. preserve section order;
3. validate `RequirementSheet` with existing model validation;
4. store the approved `RequirementSheet` JSON;
5. store the full draft revision history for audit and transcript replay.

Confirmation must fail if the latest draft revision contains unresolved free-form amendments.

### Privacy

The draft may store JD text and extracted requirement text. It must not store candidate private data, raw provider payloads, cookies, auth headers, browser state, or Codex auth state.
