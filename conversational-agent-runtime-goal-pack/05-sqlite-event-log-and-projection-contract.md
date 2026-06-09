# SQLite Event Log And Projection Contract

## Purpose

This document centralizes the local event-log contract used by Goal 1 runtime control and Goal 2 conversational-agent transcript projection.

The product currently needs a durable local event log, not a distributed broker. The right baseline is:

```text
SQLite durable event log
+ runtime command/state tables
+ snapshots
+ cursor-based transcript projection
+ idempotent activity-item projection
+ gap detection and recovery
```

This is event-backed runtime control and event-driven projection. It is not full Event Sourcing, and it is not a Kafka, Temporal, NATS, Pulsar, or CloudEvents runtime dependency.

## Current Product Status

Goal 1 already implemented the core runtime-control event log:

- `src/seektalent_runtime_control/store.py` owns the runtime-control SQLite store.
- `runtime_control_events` stores ordered runtime events.
- `runtime_control_runs.latest_event_seq` is the per-run event cursor.
- `runtime_control_snapshots` stores the latest read model for each run.
- event writes use SQLite write transactions and allocate the next sequence inside the transaction.
- `list_events` detects event gaps and returns `runtime_event_gap_detected`.
- Goal 1 progress recorded passing event, recovery, checkpoint, and retention tests.

Goal 2 has not yet implemented the conversation-agent projection. Its contract is already defined in `goal-2-conversational-agent/SPEC.md` and this document makes the projection rules explicit for future execution.

## Runtime Event Log

### Runtime Event Table

The runtime-control store must keep an append-only logical event stream per `runtime_run_id`.

Required table:

```sql
CREATE TABLE runtime_control_events (
  event_id TEXT NOT NULL,
  runtime_run_id TEXT NOT NULL,
  event_seq INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  stage TEXT,
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

`event_seq` is per-run, contiguous, and strictly increasing from `1`.

`event_id` is globally unique enough for traceability, but `event_seq` is the cursor used for ordered reads and projection.

### Event Write Transaction

Every event append must run inside a SQLite write transaction.

Required write order:

1. `BEGIN IMMEDIATE`;
2. read `runtime_control_runs.latest_event_seq` for the run;
3. allocate `event_seq = latest_event_seq + 1`;
4. insert the `runtime_control_events` row;
5. update `runtime_control_runs.latest_event_seq`;
6. update run status, command/amendment state, checkpoint pointer, or final state when applicable;
7. replace `runtime_control_snapshots` when applicable;
8. commit.

The implementation must not allocate event sequence with non-transactional `SELECT MAX(event_seq) + 1`.

No reader may observe a run cursor that points past the persisted event row. If the transaction rolls back, neither the event row nor the cursor update may remain.

### Event Contract

Every event exposed through `list_workflow_events` must include enough structured data for deterministic projection:

```text
eventId
runtimeRunId
eventSeq
eventType
stage
roundNo
sourceId
status
summary
payload
createdAt
```

The `summary` is safe localized display text. Projection identity and lifecycle state must be derived from structured fields, not by parsing `summary`.

Payloads may include stable ids such as:

```text
commandId
requirementRevisionId
sourceDispatchId
checkpointId
finalSummaryId
workbenchSessionId
compactionSummaryId
memoryFactId
```

If a future projection needs an id that is not present, add the id to the event payload or persisted state. Do not synthesize hidden client-only ids for product-visible lifecycle state.

## Event Read Contract

`list_workflow_events(afterSeq, limit)` is the only event pagination contract used by the conversation agent.

Rules:

1. return events where `eventSeq > afterSeq`, ordered ascending;
2. cap `limit` to the service maximum;
3. return `nextCursor` equal to the highest returned event sequence;
4. return `afterSeq` as `nextCursor` when no event exists and the run cursor is also `afterSeq`;
5. detect when the first returned event is greater than `afterSeq + 1`;
6. detect when no row is returned but `runtime_control_runs.latest_event_seq > afterSeq`;
7. return `runtime_event_gap_detected` for either gap case.

The agent must not skip gaps. It must refresh `get_workflow_snapshot`, persist a recoverable sync error, and keep the prior rendered cursor until reconciliation succeeds.

## Snapshots

Snapshots are read models, not event replacements.

`runtime_control_snapshots` stores the latest compact state needed for current-status and recovery reads:

```text
runtimeRunId
status
currentStage
currentRound
latestEventSeq
candidateCounts
pendingCommands
pendingUserAction
artifactRefs
progressSummary
```

Snapshots may be replaced as the run advances. Events remain the audit and projection source.

The conversation agent may use snapshots to recover aggregate status after a gap or reload, but must not use snapshots to invent missing event-level progress narration.

## Conversation Projection

Goal 2 projects runtime events into conversation-agent stores.

Projection outputs:

```text
agent_transcript_messages
agent_transcript_activity_items
agent_runtime_links.latest_runtime_event_seq
agent_conversations.latest_rendered_runtime_event_seq
```

Projection sequence:

1. read `agent_conversations.latest_rendered_runtime_event_seq`;
2. call runtime control `list_workflow_events(afterSeq=latest_rendered_runtime_event_seq)`;
3. create append-only transcript messages for new events in event order;
4. upsert affected activity items by deterministic `activityKey`;
5. update `source_event_seq_latest` monotonically for activity items;
6. update `latest_rendered_runtime_event_seq` in the same transaction as transcript and activity writes.

Projection writes are derived state. If projection data is stale or missing, rebuild it from runtime-control events, snapshots, tool-call records, context summaries, and memory review records.

## Projection Idempotency

Transcript message idempotency:

```text
UNIQUE(conversation_id, source_runtime_run_id, source_runtime_event_seq)
```

Activity item idempotency:

```text
UNIQUE(conversation_id, activity_key)
```

Tool-call idempotency:

```text
UNIQUE(conversation_id, idempotency_key)
```

Runtime-control state-changing calls must carry an idempotency key when they can create rows or mutate state.

Duplicate projection from browser reload, parallel polling, reconnect, or process restart must not create duplicate transcript messages or activity items. Activity updates must never move `source_event_seq_latest` backward.

## Cursor Ownership

Runtime-control owns:

```text
runtime_control_runs.latest_event_seq
runtime_control_snapshots.latest_event_seq
```

Conversation-agent owns:

```text
agent_conversations.latest_rendered_runtime_event_seq
agent_runtime_links.latest_runtime_event_seq
agent_transcript_activity_items.source_event_seq_start
agent_transcript_activity_items.source_event_seq_latest
```

Compaction, reload, rename, archive, unarchive, memory recall, and detail answering must not advance runtime event cursors unless they are projecting real runtime events.

## Gap Recovery

When `runtime_event_gap_detected` is returned:

1. do not advance `latest_rendered_runtime_event_seq`;
2. refresh `get_workflow_snapshot`;
3. persist an error transcript message with source tool call id and reason code;
4. keep existing activity item state unchanged unless snapshot reconciliation explicitly marks a recoverable sync state;
5. allow retry from the same cursor;
6. require tests proving no duplicate messages or activity items after retry.

If reconciliation finds missing runtime-control or Workbench links, surface stable reason codes such as:

```text
runtime_event_gap_detected
runtime_link_broken
workbench_session_missing
```

Do not hide reconciliation failures behind generated assistant text.

## Retention And Compaction

Runtime event rows may be retained longer than heavy payloads.

After configured retention, large `payload_json` values may be compacted only if these fields remain:

```text
event_id
runtime_run_id
event_seq
event_type
stage
round_no
source_id
status
summary
created_at
workbench_event_global_seq
safe_refs
```

Retention must not corrupt cursor continuity. Removing an event row from the middle of an active or retained run is forbidden because it would create an artificial gap.

Conversation compaction summarizes model-input history only. It must not delete canonical transcript messages, activity items, runtime links, event cursors, requirement review state, command state, memory review state, or final-summary context that is still required for reload.

## Testing Requirements

Goal 1 runtime-control tests must cover:

- empty SQLite initialization;
- future schema version rejection;
- event append transaction rollback;
- duplicate event id rejection;
- duplicate event sequence rejection;
- concurrent event writers serialize without duplicate sequence numbers;
- `list_workflow_events` returns ordered pages;
- `runtime_event_gap_detected` for missing middle rows;
- no cursor update after failed append;
- snapshot replacement in the same transaction as event append;
- recovery after process restart.

Goal 2 conversation-agent tests must cover:

- cursor-based event projection;
- transcript message idempotency;
- activity item idempotency;
- activity `source_event_seq_latest` monotonicity;
- parallel browser polling or reconnect;
- process restart projection replay;
- gap detection without cursor advancement;
- snapshot refresh after gap;
- compaction does not advance runtime event cursors;
- archive, rename, reopen, memory recall, and detail answers do not advance event cursors.

## When To Introduce Heavier Event Infrastructure

Do not introduce Kafka, NATS, Pulsar, Temporal, CloudEvents, or an outbox worker for Goal 2 by default.

Consider heavier infrastructure only when one of these becomes true:

- workflow execution must scale across multiple machines;
- event consumers become independent services;
- many event streams need fan-out subscription;
- event throughput exceeds comfortable SQLite write/read bounds;
- durable timers, retries, or signals become too complex for the local executor;
- product requirements need cross-process workflow orchestration;
- external systems must subscribe to SeekTalent events.

Likely future mapping:

| Need | Candidate |
| --- | --- |
| durable long-running orchestration, timers, retries, signals | Temporal or similar durable workflow engine |
| high-throughput event fan-out and multiple independent consumers | Kafka, Pulsar, or NATS JetStream |
| cross-system event payload standardization | CloudEvents |
| reliable DB-to-broker publishing | transactional outbox/inbox |

Until those needs exist, keep the local SQLite event log and projection contract small, explicit, and test-backed.
