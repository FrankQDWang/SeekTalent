# Data Storage And Migrations

## Database

Use a dedicated local SQLite database:

```text
.seektalent/intake.sqlite3
```

Do not add intake tables to the existing Workbench database unless implementation discovers a strong reason during build. Keeping intake persistence separate reduces parallel development conflicts with Workbench/runtime refactors.

## Tables

Recommended schema:

```sql
CREATE TABLE intake_conversations (
  conversation_id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  codex_thread_id TEXT,
  status TEXT NOT NULL,
  latest_draft_revision_id TEXT,
  workbench_session_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  confirmed_at TEXT,
  failed_reason_code TEXT
);

CREATE TABLE intake_messages (
  message_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  reason_code TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (conversation_id) REFERENCES intake_conversations(conversation_id)
);

CREATE TABLE intake_drafts (
  draft_revision_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  job_title TEXT NOT NULL,
  jd_text TEXT NOT NULL,
  notes TEXT NOT NULL,
  source_ids_json TEXT NOT NULL,
  confirmation_markdown TEXT NOT NULL,
  confidence REAL NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (conversation_id) REFERENCES intake_conversations(conversation_id)
);

CREATE TABLE intake_errors (
  error_id TEXT PRIMARY KEY,
  conversation_id TEXT,
  reason_code TEXT NOT NULL,
  public_message TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

## Migration Version

The store must maintain a schema version using `PRAGMA user_version`.

Initial version:

```text
1
```

Tests must prove:

- database initializes from empty file;
- initialization is idempotent;
- unsupported future `user_version` raises a named error;
- messages and drafts are scoped by user/workspace;
- duplicate confirmation does not create duplicate sessions.

## IDs

Use stable local ids:

```text
conversation_id: intake_<ULID>
message_id: msg_<ULID>
draft_revision_id: draft_<ULID>
error_id: intake_error_<ULID>
```

The exact prefix can differ, but tests must assert ids are non-empty and stable.

## Retention

Default retention is local indefinite retention because this is a local Workbench. The reset action must support:

- clearing one intake conversation;
- clearing project-local Codex memories;
- preserving existing Workbench sessions unless the user explicitly deletes them through existing Workbench mechanisms.

## Privacy

Do not store:

- API keys;
- cookies;
- Codex auth tokens;
- raw provider transport payloads;
- browser state;
- candidate private data from runtime.

Storing the user's intake text is allowed because it is the product input.

## Backup And Portability

The intake database is local project data. It should be safe to delete when the user wants to clear intake history. Deleting it must not corrupt Workbench session data, runtime artifacts, or Codex global user memory.
