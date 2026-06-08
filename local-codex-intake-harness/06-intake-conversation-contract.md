# Intake Conversation Contract

## Conversation States

The state machine is explicit:

```text
new
collecting
clarifying
draft_ready
confirmed
session_created
requirement_prepare_started
failed
```

State transitions:

```text
new -> collecting
collecting -> clarifying
collecting -> draft_ready
clarifying -> collecting
clarifying -> draft_ready
draft_ready -> collecting
draft_ready -> confirmed
confirmed -> session_created
session_created -> requirement_prepare_started
any non-terminal state -> failed
```

Terminal states:

```text
requirement_prepare_started
failed
```

## Structured Draft

Codex output must be parsed into this logical shape:

```json
{
  "state": "draft_ready",
  "assistantMarkdown": "我理解这次招聘目标是...",
  "missingQuestions": [],
  "draft": {
    "jobTitle": "Senior Backend Engineer",
    "jdText": "Build real-time data platform services with Python and Flink...",
    "notes": "Prefer Shanghai or Hangzhou. Exclude pure CRUD-only profiles.",
    "sourceIds": ["cts", "liepin"]
  },
  "confidence": 0.86,
  "reasonCode": null
}
```

Required draft fields:

- `jobTitle`: non-empty, max 256 chars;
- `jdText`: non-empty, max 20000 chars;
- `notes`: max 5000 chars;
- `sourceIds`: non-empty list of ids from the current Workbench source catalog.

`cts` and `liepin` may appear in examples only because they are current source ids in this repository. They must not be encoded as the complete future source universe.

## Clarification Output

If the input is insufficient, Codex should return:

```json
{
  "state": "clarifying",
  "assistantMarkdown": "我还需要确认两个点：候选人年限和优先城市。",
  "missingQuestions": [
    "候选人年限或职级范围是什么？",
    "优先城市是否限定为上海/杭州？"
  ],
  "draft": null,
  "confidence": 0.42,
  "reasonCode": "missing_required_intake_fields"
}
```

## Confirmation Output

The confirmation shown to the user must include:

- role/title;
- core responsibilities;
- must-have capabilities;
- preferred capabilities;
- constraints;
- exclusions;
- selected source ids and labels;
- what happens after confirmation.

It must not hide assumptions. If Codex inferred something, the assistant markdown should say it was inferred.

## User Edits

The user can edit the draft before confirmation. Edits must be stored as canonical intake draft state, not only frontend state.

Supported edits:

- `jobTitle`;
- `jdText`;
- `notes`;
- `sourceIds`.

After an edit, the draft remains `draft_ready` unless validation fails.

## Stale Confirmation Guard

Confirmation must include the latest draft revision id. If the frontend confirms an older revision, the API returns:

```text
409 intake_confirmation_stale
```

## Error Contract

Every public error response must include:

```json
{
  "reasonCode": "intake_confirmation_stale",
  "message": "当前确认内容已过期，请重新确认。"
}
```

The message may be localized. The reason code must be stable.

## Prompt Requirements

The prompt sent to Codex must instruct:

- answer in Chinese by default;
- produce a recruiter-facing confirmation;
- never fabricate credentials, salary, company facts, or candidate facts;
- distinguish user-provided facts from inferred assumptions;
- ask a clarification question when title or hiring goal is too vague;
- output strict JSON matching the intake contract.
