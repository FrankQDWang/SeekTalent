# Runtime Control State And Events

This compacted document preserves the content from the source documents below. Source headings are demoted one level so the merged document remains navigable without changing the substantive requirements.

## Source Documents

- `05-runtime-control-data-model.md`
- `06-event-snapshot-contract.md`

---

## Source: `05-runtime-control-data-model.md`

## Runtime Control Data Model

### Database Location

Use a dedicated local SQLite database, exposed through `AppSettings.runtime_control_db_path`:

```text
.seektalent/runtime_control.sqlite3
```

Relative values are resolved with the same workspace-root rules as the current Workbench, corpus, and provider DB paths. Do not resolve this path from the process current working directory directly.

Production default should resolve under the user's SeekTalent data root, not inside the repository. Development and tests may override `workspace_root`.

Do not add these tables to the existing Workbench database unless implementation proves a strong reason and records it in the goal ledger.

### Tables

#### `runtime_control_runs`

```sql
CREATE TABLE runtime_control_runs (
  runtime_run_id TEXT PRIMARY KEY,
  agent_conversation_id TEXT,
  workbench_session_id TEXT,
  approved_requirement_revision_id TEXT UNIQUE,
  status TEXT NOT NULL,
  current_stage TEXT NOT NULL,
  current_round INTEGER,
  latest_checkpoint_id TEXT,
  latest_event_seq INTEGER NOT NULL DEFAULT 0,
  source_ids_json TEXT NOT NULL,
  stop_reason_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);
```

Statuses:

```text
queued
starting
running
pause_requested
paused
resume_requested
cancellation_requested
cancelled
completed
failed
```

`workbench_session_id` may be null only while a run is `queued` or `starting`. A run exposed to the agent as `running`, `paused`, `completed`, `cancelled`, or `failed` must have either a valid `workbench_session_id` or a terminal reason code explaining why session creation failed.

#### `runtime_requirement_drafts`

```sql
CREATE TABLE runtime_requirement_drafts (
  draft_revision_id TEXT PRIMARY KEY,
  agent_conversation_id TEXT NOT NULL,
  base_revision_id TEXT,
  status TEXT NOT NULL,
  sections_json TEXT NOT NULL,
  extracted_requirement_sheet_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

#### `runtime_requirement_amendments`

```sql
CREATE TABLE runtime_requirement_amendments (
  amendment_id TEXT PRIMARY KEY,
  agent_conversation_id TEXT NOT NULL,
  runtime_run_id TEXT,
  base_draft_revision_id TEXT,
  result_draft_revision_id TEXT,
  base_approved_requirement_revision_id TEXT,
  result_approved_requirement_revision_id TEXT,
  target_round_no INTEGER,
  effective_boundary TEXT,
  applied_event_id TEXT,
  input_text TEXT NOT NULL,
  target_section_hint TEXT,
  status TEXT NOT NULL,
  normalized_patch_json TEXT NOT NULL,
  rejected_fragments_json TEXT NOT NULL,
  review_items_json TEXT NOT NULL,
  resolved_patch_json TEXT,
  superseded_by_amendment_id TEXT,
  resolved_at TEXT,
  idempotency_key TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(agent_conversation_id, idempotency_key),
  UNIQUE(runtime_run_id, idempotency_key)
);
```

Draft-time amendments use `base_draft_revision_id`. Running next-round amendments use `base_approved_requirement_revision_id` and `runtime_run_id`. Implementations must not fill unavailable ids with synthetic or sentinel values.

Statuses:

```text
applied
pending_target_round
needs_review
rejected
superseded
```

Running next-round amendments accumulate in creation order for a target round. They are not automatically superseded by a later amendment for the same target round. `superseded_by_amendment_id` is set only when the user explicitly replaces or withdraws a pending amendment, or when idempotency replay resolves to an existing amendment.

#### `runtime_approved_requirements`

```sql
CREATE TABLE runtime_approved_requirements (
  approved_requirement_revision_id TEXT PRIMARY KEY,
  draft_revision_id TEXT,
  base_approved_requirement_revision_id TEXT,
  source_amendment_id TEXT,
  agent_conversation_id TEXT NOT NULL,
  requirement_sheet_json TEXT NOT NULL,
  selected_item_ids_json TEXT NOT NULL,
  deselected_item_ids_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

Draft-confirmed approved requirements set `draft_revision_id`. Running next-round requirement amendments set `base_approved_requirement_revision_id` and `source_amendment_id` instead. Implementations must not reuse an older draft id for an amendment-derived approved requirement revision.

#### `runtime_control_commands`

```sql
CREATE TABLE runtime_control_commands (
  command_id TEXT PRIMARY KEY,
  runtime_run_id TEXT NOT NULL,
  command_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,
  conflict_group TEXT NOT NULL,
  supersedes_command_id TEXT,
  superseded_by_command_id TEXT,
  target_round_no INTEGER,
  idempotency_key TEXT NOT NULL,
  requested_by TEXT,
  requested_at TEXT NOT NULL,
  applied_at TEXT,
  rejected_reason_code TEXT,
  UNIQUE(runtime_run_id, idempotency_key)
);
```

Command types:

```text
pause
resume
cancel
apply_next_round_requirement
```

Command statuses:

```text
accepted
pending_safe_boundary
applied
rejected
superseded
```

Conflict groups:

```text
lifecycle
requirement_amendment
```

Command rows record accepted, rejected, applied, and superseded states. Reusing an idempotency key returns the existing row. A new command that conflicts with an already accepted pending command must either reject with `runtime_command_conflict` or explicitly supersede according to the command conflict rules in `02-agent-tool-and-requirement-contracts.md`.

#### `runtime_control_checkpoints`

```sql
CREATE TABLE runtime_control_checkpoints (
  checkpoint_id TEXT PRIMARY KEY,
  runtime_run_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  round_no INTEGER,
  safe_boundary TEXT NOT NULL,
  run_state_json TEXT NOT NULL,
  source_plan_json TEXT NOT NULL,
  pending_commands_json TEXT NOT NULL,
  artifact_manifest_ref TEXT,
  schema_version TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

#### `runtime_control_executor_leases`

```sql
CREATE TABLE runtime_control_executor_leases (
  lease_id TEXT PRIMARY KEY,
  runtime_run_id TEXT NOT NULL,
  executor_id TEXT NOT NULL,
  attempt_no INTEGER NOT NULL,
  status TEXT NOT NULL,
  acquired_at TEXT NOT NULL,
  heartbeat_at TEXT,
  lease_expires_at TEXT NOT NULL,
  released_at TEXT,
  reason_code TEXT,
  UNIQUE(runtime_run_id, attempt_no)
);
```

Statuses:

```text
active
released
expired
failed
```

At most one lease may be `active` for a runtime run. The store must enforce this in the same transaction that starts or resumes an executor. The executor must include its `executor_id` when writing started, heartbeat, checkpoint, failed, completed, paused, or cancelled state.

#### `runtime_control_events`

```sql
CREATE TABLE runtime_control_events (
  event_id TEXT PRIMARY KEY,
  runtime_run_id TEXT NOT NULL,
  event_seq INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  stage TEXT NOT NULL,
  round_no INTEGER,
  source_id TEXT,
  status TEXT NOT NULL,
  summary TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  workbench_event_global_seq INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE(runtime_run_id, event_seq),
  UNIQUE(runtime_run_id, event_id)
);
```

Event writes must run under SQLite `BEGIN IMMEDIATE`. The writer reads `runtime_control_runs.latest_event_seq`, allocates `latest_event_seq + 1`, inserts the event row, updates `runtime_control_runs.latest_event_seq`, and replaces `runtime_control_snapshots` when applicable in the same transaction.

The implementation must not allocate `event_seq` with a non-transactional `SELECT MAX(event_seq) + 1` outside the write transaction.

Tests must cover duplicate event ids, duplicate sequence numbers, concurrent writers, rollback after failed transaction, and replay after process restart.

Readers must treat a missing sequence between `afterSeq + 1` and the returned event list as `runtime_event_gap_detected`, not as normal end-of-stream.

#### `runtime_control_snapshots`

```sql
CREATE TABLE runtime_control_snapshots (
  runtime_run_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  current_stage TEXT NOT NULL,
  current_round INTEGER,
  latest_event_seq INTEGER NOT NULL,
  snapshot_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

### Migration Version

Use `PRAGMA user_version`.

Initial version:

```text
1
```

Tests must prove empty initialization, idempotent initialization, and rejection of unsupported future versions.

---

## Source: `06-event-snapshot-contract.md`

## Event And Snapshot Contract

### Event Principles

Events are the only source for transcript progress narration.

The agent can summarize and localize events. It must not invent stages, counts, candidates, or command state that are not present in events or snapshots.

Events also drive Codex-like working-process activity items in Goal 2. The activity projection may group several events into one durable UI item, but every status transition, count update, source name, command state, and completion/failure state must be traceable to a runtime-control event, runtime-control snapshot, tool-call result, context-compaction record, or advisory-memory review record.

### Event Shape

```json
{
  "eventId": "rtevt_01HZ...",
  "runtimeRunId": "runtime_run_01HZ...",
  "eventSeq": 120,
  "eventType": "runtime_round_scoring_completed",
  "stage": "scoring",
  "roundNo": 1,
  "sourceId": null,
  "status": "completed",
  "summary": "第 1 轮评分完成：12 人进入评分，fit 4 人。",
  "payload": {
    "newlyScoredCount": 12,
    "fitCount": 4,
    "topPoolCount": 8
  },
  "createdAt": "2026-06-08T00:00:00Z"
}
```

### Required Event Types

Requirement phase:

```text
requirement_extraction_started
requirement_extraction_completed
requirement_draft_updated
requirement_confirmed
```

Run lifecycle:

```text
runtime_run_queued
runtime_executor_starting
runtime_executor_started
runtime_executor_start_failed
runtime_executor_lease_expired
runtime_run_started
runtime_run_paused
runtime_run_resumed
runtime_run_cancelled
runtime_run_failed
runtime_run_completed
```

Round lifecycle:

```text
runtime_round_controller_started
runtime_round_controller_completed
runtime_round_input_locked
runtime_round_query_ready
runtime_round_source_dispatch
runtime_round_source_result
runtime_round_merge_completed
runtime_round_scoring_started
runtime_round_scoring_completed
runtime_round_feedback_completed
runtime_round_completed
runtime_finalization_started
runtime_finalization_completed
```

Command lifecycle:

```text
runtime_command_accepted
runtime_command_pending_safe_boundary
runtime_command_applied
runtime_command_rejected
runtime_command_superseded
```

Requirement amendment lifecycle:

```text
runtime_next_round_requirement_submitted
runtime_next_round_requirement_normalized
runtime_next_round_requirement_needs_review
runtime_next_round_requirement_applied
runtime_next_round_requirement_rejected
runtime_next_round_requirement_superseded
runtime_requirement_revision_activated
```

Checkpoint lifecycle:

```text
runtime_checkpoint_written
runtime_checkpoint_restored
```

Integrity lifecycle:

```text
runtime_event_gap_detected
runtime_link_reconciliation_failed
```

### Event Sequence Rules

For each `runtimeRunId`, `eventSeq` is contiguous and strictly increasing from `1`.

`list_workflow_events(afterSeq, limit)` must:

1. return events where `eventSeq > afterSeq`, ordered ascending;
2. cap `limit` to the service maximum;
3. include `nextCursor` equal to the highest returned `eventSeq`, or `afterSeq` when no event exists;
4. detect if the first returned event is greater than `afterSeq + 1`;
5. return `runtime_event_gap_detected` if a gap is detected.

The agent must not skip over a gap. It should refresh the snapshot and display a recoverable error state until reconciliation succeeds.

### Activity Projection Inputs

Runtime events must contain enough structured data for the conversation agent to build deterministic activity keys without parsing localized text. The minimum fields are:

```text
runtimeRunId
eventSeq
eventType
stage
roundNo
sourceId
status
payload
createdAt
```

Goal 2 derives activity keys from those fields plus stable ids in `payload` when present, such as command id, requirement revision id, source dispatch id, checkpoint id, compaction summary id, memory fact id, or final summary id.

Projection examples:

| Event family | Activity key shape | Activity type |
| --- | --- | --- |
| `requirement_extraction_*` | `requirement_extraction:{conversationId}:{draftRevisionId}` | `requirement_extraction` |
| `runtime_run_queued`, `runtime_run_started` | `workflow_start:{runtimeRunId}` | `workflow_start` |
| `runtime_round_query_ready` | `query_generation:{runtimeRunId}:round:{roundNo}` | `query_generation` |
| `runtime_round_source_dispatch`, `runtime_round_source_result` | `source:{runtimeRunId}:round:{roundNo}:{sourceId}` | `source_dispatch` or `source_result` |
| `runtime_round_scoring_started`, `runtime_round_scoring_completed` | `scoring:{runtimeRunId}:round:{roundNo}` | `scoring` |
| `runtime_command_*` | `command:{runtimeRunId}:{commandId}` | `command` |
| `runtime_next_round_requirement_*`, `runtime_requirement_revision_activated` | `next_requirement:{runtimeRunId}:{revisionId}` | `next_round_requirement` |
| `runtime_finalization_*` | `finalization:{runtimeRunId}` | `finalization` |

If an implementation needs a lifecycle item that cannot be derived from persisted fields, it must add a real runtime-control event or persisted id. It must not synthesize a hidden client-only id, parse Chinese summary text, or infer completion from elapsed time.

### Snapshot Shape

```json
{
  "runtimeRunId": "runtime_run_01HZ...",
  "status": "running",
  "currentStage": "scoring",
  "currentRound": 2,
  "stopReasonCode": null,
  "progressSummary": "第 2 轮正在评分新增候选人。",
  "selectedSourceIds": ["source_id_from_catalog_a", "source_id_from_catalog_b"],
  "candidateCounts": {
    "rawReturned": 42,
    "uniqueIdentities": 28,
    "scored": 18,
    "topPool": 10
  },
  "topCandidatesPreview": [],
  "pendingUserAction": null,
  "pendingCommands": [],
  "latestEventsCursor": 241,
  "artifactRefs": []
}
```

### Detail Read Model

`get_runtime_detail` supports these query kinds:

```text
round_query
source_result
candidate_score
reflection
final_candidate
command
checkpoint
```

Detail request shape:

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

Each detail response must include:

```json
{
  "kind": "reflection",
  "runtimeRunId": "runtime_run_01HZ...",
  "roundNo": 2,
  "title": "第 2 轮反思",
  "summary": "关键词需要收窄到分布式系统和 Python 后端。",
  "facts": [
    {
      "label": "反思结论",
      "value": "关键词需要收窄到分布式系统和 Python 后端。",
      "sourceEventId": "rtevt_..."
    }
  ],
  "sourceEventIds": ["rtevt_..."],
  "artifactRefs": []
}
```

The detail response must not expose raw provider payloads, cookies, auth headers, browser storage, or raw resume text unless the existing Workbench resume snapshot allow policy explicitly permits the requested user to see it.

### Final Summary Read Model

`prepare_final_summary` creates or returns an idempotent final summary record. It is available only after the runtime run reaches `completed`, `cancelled`, or `failed`.

Request shape:

```json
{
  "runtimeRunId": "runtime_run_01HZ...",
  "userInstruction": "请重点说明前三位候选人的推荐理由。",
  "sourceSnapshotEventSeq": 241,
  "idempotencyKey": "runtime_run_01HZ:final-summary:1"
}
```

The summary must be grounded in:

- final snapshot;
- runtime events up to `sourceSnapshotEventSeq`;
- permitted detail read models;
- Workbench-visible candidate facts;
- user final instruction.

If `sourceSnapshotEventSeq` is stale compared with the latest terminal snapshot, return the latest terminal cursor so the agent can refresh before summarizing.
