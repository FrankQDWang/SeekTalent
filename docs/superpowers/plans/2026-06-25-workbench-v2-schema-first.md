# Workbench v2 Schema-First Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a clean Workbench v2 path where every user turn is persisted as ordered transcript events, classified before action, normalized into runtime input only for recruitment intents, and rendered by the UI as a normal agent transcript.

**Architecture:** Implement v2 beside the old Workbench. Add a new `src/seektalent_workbench_v2` backend package for models, SQLite store, strict Bailian agent loop, runtime service, and view assembly; keep `src/seektalent_ui` as a thin route shim only. React gets a v2 API client and v2 transcript renderer that reads `transcriptEvents` directly and does not use old `transcriptGroups`.

**Tech Stack:** Python 3.12, FastAPI, SQLite, Pydantic, Bailian OpenAI-compatible strict native JSON schema output, existing `seektalent_runtime_control`, React 19, TanStack Query, Vitest, Playwright.

---

Spec: `docs/superpowers/specs/2026-06-25-workbench-v2-schema-first-design.md`

## Scope Check

This is a broad but single product slice: one new Workbench v2 conversation path. It is intentionally split into backend data/agent/runtime tasks, route wiring, frontend rendering, E2E, and cleanup guardrails. Old Workbench deletion is not part of this first implementation plan; it is a later cleanup PR after v2 manual acceptance.

## File Structure

Create:

- `src/seektalent_workbench_v2/__init__.py` — package marker and exported schema version.
- `src/seektalent_workbench_v2/models.py` — Pydantic models and event type literals for v2.
- `src/seektalent_workbench_v2/store.py` — SQLite schema and append-only transcript store.
- `src/seektalent_workbench_v2/agent_loop.py` — intent classification and Bailian strict structured output loop.
- `src/seektalent_workbench_v2/prompts/system.md` — system prompt for the general Workbench assistant.
- `src/seektalent_workbench_v2/runtime_service.py` — in-process adapter over stable runtime-control APIs.
- `src/seektalent_workbench_v2/service.py` — application service that appends events, calls the agent loop, and starts runtime.
- `src/seektalent_workbench_v2/views.py` — DB rows to API view models.
- `src/seektalent_ui/agent_workbench_v2_routes.py` — thin FastAPI route shim.
- `tests/test_workbench_v2_store.py`
- `tests/test_workbench_v2_agent_loop.py`
- `tests/test_workbench_v2_runtime_service.py`
- `tests/test_workbench_v2_routes.py`
- `tests/test_workbench_v2_boundaries.py`
- `tests/test_workbench_v2_prompt_contract.py`
- `apps/web-react/src/lib/api/workbenchV2Types.ts`
- `apps/web-react/src/lib/api/workbenchV2Client.ts`
- `apps/web-react/src/lib/api/workbenchV2.ts`
- `apps/web-react/src/components/workbench/TranscriptV2.tsx`
- `apps/web-react/src/components/workbench/TranscriptV2.css`
- `apps/web-react/src/components/workbench/RequirementFormEvent.tsx`
- `apps/web-react/src/components/workbench/RequirementFormEvent.css`
- `apps/web-react/src/components/workbench/ConversationScreenV2.tsx`
- `apps/web-react/src/components/workbench/ConversationScreenV2.css`
- `apps/web-react/tests/workbench-v2.spec.ts`

Modify:

- `src/seektalent/config.py` — add `workbench_conversation_model_id` and reasoning-effort settings.
- `src/seektalent/llm.py` — add `workbench_conversation` as a native strict structured output stage.
- `.env.example` — document the workbench conversation model config.
- `src/seektalent_ui/server.py` — include the v2 route shim and initialize `app.state.workbench_v2_service`.
- `src/seektalent/backup_group.py` — register the v2 SQLite database only after the store path exists.
- `apps/web-react/src/routes/conversation.tsx` — route `agentv2_` conversations to v2 hooks/components and make new conversations call v2 create.
- `apps/web-react/src/components/workbench/ConversationList.tsx` — accept v2 conversation summaries.
- `apps/web-react/src/components/workbench/HomeStartPanel.tsx` — submit arbitrary text instead of JD-only payload names.
- `apps/web-react/src/lib/query/keys.ts` — add v2 query keys.
- `apps/web-react/src/lib/api/schema.d.ts` — regenerate after FastAPI routes exist.

Do not modify old BFF projection logic except for route mounting:

- `src/seektalent_ui/agent_workbench_transcript.py`
- `src/seektalent_ui/agent_workbench_response.py`
- `src/seektalent_ui/agent_workbench_projection.py`
- `src/seektalent_conversation_agent/*`

## Task 1: Backend Models and Append-Only Store

**Files:**
- Create: `src/seektalent_workbench_v2/__init__.py`
- Create: `src/seektalent_workbench_v2/models.py`
- Create: `src/seektalent_workbench_v2/store.py`
- Test: `tests/test_workbench_v2_store.py`

- [ ] **Step 1: Write store tests first**

Create `tests/test_workbench_v2_store.py` with these tests:

```python
from __future__ import annotations

from pathlib import Path

from seektalent_workbench_v2.models import WorkbenchV2TranscriptEventInput
from seektalent_workbench_v2.store import WorkbenchV2Store


def test_store_appends_events_with_monotonic_steps(tmp_path: Path) -> None:
    store = WorkbenchV2Store(tmp_path / "workbench_v2.sqlite3")
    store.initialize()
    conversation = store.create_conversation(first_user_text="你好", idempotency_key="create-1")

    first = store.append_event(
        conversation.id,
        WorkbenchV2TranscriptEventInput(
            type="user_message",
            role="user",
            payload={"text": "你好"},
            status="completed",
        ),
    )
    second = store.append_event(
        conversation.id,
        WorkbenchV2TranscriptEventInput(
            type="assistant_message",
            role="assistant",
            payload={"text": "你好，我可以帮你处理招聘需求。"},
            status="completed",
        ),
    )

    assert first.step == 1
    assert second.step == 2
    view = store.get_conversation(conversation.id)
    assert [event.step for event in view.events] == [1, 2]
    assert [event.type for event in view.events] == ["user_message", "assistant_message"]


def test_store_replays_create_by_idempotency_key(tmp_path: Path) -> None:
    store = WorkbenchV2Store(tmp_path / "workbench_v2.sqlite3")
    store.initialize()

    first = store.create_conversation(first_user_text="你好", idempotency_key="same-key")
    replay = store.create_conversation(first_user_text="你好", idempotency_key="same-key")

    assert first.id == replay.id
    assert first.title == "你好"


def test_store_rejects_idempotency_payload_conflict(tmp_path: Path) -> None:
    store = WorkbenchV2Store(tmp_path / "workbench_v2.sqlite3")
    store.initialize()
    store.create_conversation(first_user_text="你好", idempotency_key="same-key")

    try:
        store.create_conversation(first_user_text="另一个需求", idempotency_key="same-key")
    except ValueError as exc:
        assert str(exc) == "workbench_v2_idempotency_conflict"
    else:
        raise AssertionError("conflicting idempotency key should fail")


def test_store_keeps_context_summary_as_event_and_conversation_field(tmp_path: Path) -> None:
    store = WorkbenchV2Store(tmp_path / "workbench_v2.sqlite3")
    store.initialize()
    conversation = store.create_conversation(first_user_text="长对话", idempotency_key="create-summary")

    event = store.append_context_summary(conversation.id, summary="用户正在招聘数据科学家，偏杭州。")

    assert event.type == "context_summary"
    refreshed = store.get_conversation(conversation.id)
    assert refreshed.conversation.context_summary == "用户正在招聘数据科学家，偏杭州。"
    assert refreshed.events[-1].payload["summary"] == "用户正在招聘数据科学家，偏杭州。"
```

- [ ] **Step 2: Run store tests and verify they fail**

Run:

```bash
uv run pytest tests/test_workbench_v2_store.py -q
```

Expected: import failure for `seektalent_workbench_v2`.

- [ ] **Step 3: Add v2 model definitions**

Create `src/seektalent_workbench_v2/models.py`:

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


WORKBENCH_V2_SCHEMA_VERSION = "agent.workbench.v2"

WorkbenchV2EventType = Literal[
    "user_message",
    "assistant_message",
    "assistant_status",
    "requirement_form",
    "requirement_form_confirmed",
    "runtime_progress",
    "runtime_result",
    "error",
    "context_summary",
]
WorkbenchV2Role = Literal["user", "assistant", "system", "runtime"]
WorkbenchV2EventStatus = Literal["pending", "running", "completed", "failed"]
WorkbenchV2RuntimeState = Literal["idle", "queued", "running", "completed", "failed", "cancelled"]


class WorkbenchV2Conversation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    created_at: str
    updated_at: str
    runtime_run_id: str | None = None
    runtime_state: WorkbenchV2RuntimeState = "idle"
    context_summary: str | None = None


class WorkbenchV2TranscriptEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: WorkbenchV2EventType
    role: WorkbenchV2Role
    payload: dict[str, object] = Field(default_factory=dict)
    status: WorkbenchV2EventStatus = "completed"
    parent_event_id: str | None = None
    dedupe_key: str | None = None


class WorkbenchV2TranscriptEvent(WorkbenchV2TranscriptEventInput):
    id: str
    conversation_id: str
    step: int
    created_at: str


class WorkbenchV2ConversationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation: WorkbenchV2Conversation
    events: list[WorkbenchV2TranscriptEvent] = Field(default_factory=list)
```

Create `src/seektalent_workbench_v2/__init__.py`:

```python
from seektalent_workbench_v2.models import WORKBENCH_V2_SCHEMA_VERSION

__all__ = ["WORKBENCH_V2_SCHEMA_VERSION"]
```

- [ ] **Step 4: Add SQLite store**

Create `src/seektalent_workbench_v2/store.py` with this schema and public methods:

```python
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from seektalent_ui.workbench_store_helpers import now_iso
from seektalent_workbench_v2.models import (
    WorkbenchV2Conversation,
    WorkbenchV2ConversationRecord,
    WorkbenchV2TranscriptEvent,
    WorkbenchV2TranscriptEventInput,
)


class WorkbenchV2Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def create_conversation(self, *, first_user_text: str, idempotency_key: str | None) -> WorkbenchV2Conversation:
        digest = _payload_digest({"firstUserText": first_user_text})
        now = now_iso()
        with self._connect() as conn:
            if idempotency_key:
                row = conn.execute(
                    "SELECT conversation_id, payload_digest FROM workbench_v2_idempotency WHERE key = ?",
                    (idempotency_key,),
                ).fetchone()
                if row is not None:
                    if row["payload_digest"] != digest:
                        raise ValueError("workbench_v2_idempotency_conflict")
                    return self.get_conversation(row["conversation_id"]).conversation
            conversation_id = f"agentv2_{uuid4().hex}"
            title = _title_from_text(first_user_text)
            conn.execute(
                """
                INSERT INTO workbench_v2_conversations (
                    id, title, created_at, updated_at, runtime_state
                ) VALUES (?, ?, ?, ?, 'idle')
                """,
                (conversation_id, title, now, now),
            )
            if idempotency_key:
                conn.execute(
                    """
                    INSERT INTO workbench_v2_idempotency (key, conversation_id, payload_digest, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (idempotency_key, conversation_id, digest, now),
                )
            conn.commit()
            return self.get_conversation(conversation_id).conversation

    def append_event(
        self,
        conversation_id: str,
        event: WorkbenchV2TranscriptEventInput,
    ) -> WorkbenchV2TranscriptEvent:
        now = now_iso()
        event_id = f"agentv2_event_{uuid4().hex}"
        payload_json = json.dumps(event.payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if event.dedupe_key:
                row = conn.execute(
                    "SELECT id FROM workbench_v2_transcript_events WHERE conversation_id = ? AND dedupe_key = ?",
                    (conversation_id, event.dedupe_key),
                ).fetchone()
                if row is not None:
                    conn.commit()
                    return self.get_event(row["id"])
            next_step = int(
                conn.execute(
                    "SELECT COALESCE(MAX(step), 0) + 1 AS next_step FROM workbench_v2_transcript_events WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()["next_step"]
            )
            conn.execute(
                """
                INSERT INTO workbench_v2_transcript_events (
                    id, conversation_id, step, type, role, payload_json, status,
                    parent_event_id, dedupe_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    conversation_id,
                    next_step,
                    event.type,
                    event.role,
                    payload_json,
                    event.status,
                    event.parent_event_id,
                    event.dedupe_key,
                    now,
                ),
            )
            conn.execute(
                "UPDATE workbench_v2_conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            conn.commit()
        return self.get_event(event_id)

    def append_context_summary(self, conversation_id: str, *, summary: str) -> WorkbenchV2TranscriptEvent:
        event = self.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="context_summary",
                role="system",
                payload={"summary": summary},
                status="completed",
            ),
        )
        with self._connect() as conn:
            conn.execute(
                "UPDATE workbench_v2_conversations SET context_summary = ?, updated_at = ? WHERE id = ?",
                (summary, event.created_at, conversation_id),
            )
        return event

    def get_event(self, event_id: str) -> WorkbenchV2TranscriptEvent:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM workbench_v2_transcript_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise KeyError(event_id)
        return _event_from_row(row)

    def get_conversation(self, conversation_id: str) -> WorkbenchV2ConversationRecord:
        with self._connect() as conn:
            conversation_row = conn.execute(
                "SELECT * FROM workbench_v2_conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if conversation_row is None:
                raise KeyError(conversation_id)
            event_rows = conn.execute(
                "SELECT * FROM workbench_v2_transcript_events WHERE conversation_id = ? ORDER BY step",
                (conversation_id,),
            ).fetchall()
        return WorkbenchV2ConversationRecord(
            conversation=_conversation_from_row(conversation_row),
            events=[_event_from_row(row) for row in event_rows],
        )

    def list_conversations(self) -> list[WorkbenchV2Conversation]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workbench_v2_conversations ORDER BY updated_at DESC, created_at DESC"
            ).fetchall()
        return [_conversation_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
```

Add helper functions and `SCHEMA` in the same file:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS workbench_v2_conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    runtime_run_id TEXT,
    runtime_state TEXT NOT NULL CHECK(runtime_state IN ('idle','queued','running','completed','failed','cancelled')),
    context_summary TEXT
);

CREATE TABLE IF NOT EXISTS workbench_v2_transcript_events (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES workbench_v2_conversations(id) ON DELETE CASCADE,
    step INTEGER NOT NULL,
    type TEXT NOT NULL,
    role TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    parent_event_id TEXT,
    dedupe_key TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(conversation_id, step),
    UNIQUE(conversation_id, dedupe_key)
);

CREATE TABLE IF NOT EXISTS workbench_v2_idempotency (
    key TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES workbench_v2_conversations(id) ON DELETE CASCADE,
    payload_digest TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workbench_v2_events_conversation_step
ON workbench_v2_transcript_events(conversation_id, step);
"""


def _payload_digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _title_from_text(text: str) -> str:
    stripped = " ".join(text.strip().split())
    return stripped[:40] if stripped else "新对话"


def _conversation_from_row(row: sqlite3.Row) -> WorkbenchV2Conversation:
    return WorkbenchV2Conversation(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        runtime_run_id=row["runtime_run_id"],
        runtime_state=row["runtime_state"],
        context_summary=row["context_summary"],
    )


def _event_from_row(row: sqlite3.Row) -> WorkbenchV2TranscriptEvent:
    return WorkbenchV2TranscriptEvent(
        id=row["id"],
        conversation_id=row["conversation_id"],
        step=row["step"],
        type=row["type"],
        role=row["role"],
        payload=json.loads(row["payload_json"]),
        status=row["status"],
        parent_event_id=row["parent_event_id"],
        dedupe_key=row["dedupe_key"],
        created_at=row["created_at"],
    )
```

- [ ] **Step 5: Run store tests**

Run:

```bash
uv run pytest tests/test_workbench_v2_store.py -q
```

Expected: `4 passed`.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/seektalent_workbench_v2 tests/test_workbench_v2_store.py
git commit -m "feat: add Workbench v2 transcript store"
```

## Task 2: Bailian Strict Agent Contract and System Prompt

**Files:**
- Modify: `src/seektalent/config.py`
- Modify: `src/seektalent/llm.py`
- Modify: `.env.example`
- Create: `src/seektalent_workbench_v2/agent_loop.py`
- Create: `src/seektalent_workbench_v2/prompts/system.md`
- Test: `tests/test_llm_provider_config.py`
- Test: `tests/test_workbench_v2_agent_loop.py`
- Test: `tests/test_workbench_v2_prompt_contract.py`

- [ ] **Step 1: Write failing config and prompt tests**

Append tests:

```python
def test_workbench_conversation_stage_uses_bailian_native_strict_schema() -> None:
    from seektalent.llm import build_output_spec, resolve_stage_model_config, resolve_structured_output_mode
    from tests.test_llm_provider_config import _json_schema_capable_model
    from tests.settings_factory import make_settings

    stage = resolve_stage_model_config(make_settings(), stage="workbench_conversation")
    output_spec = build_output_spec(stage, _json_schema_capable_model(), dict)

    assert stage.provider_label == "bailian"
    assert stage.endpoint_kind == "bailian_openai_chat_completions"
    assert stage.model_id == "deepseek-v4-flash"
    assert stage.reasoning_effort == "off"
    assert resolve_structured_output_mode(stage) == "native_json_schema"
    assert output_spec.__class__.__name__ == "NativeOutput"
```

Create `tests/test_workbench_v2_prompt_contract.py`:

```python
from pathlib import Path


PROMPT = Path("src/seektalent_workbench_v2/prompts/system.md")


def test_system_prompt_requires_intent_classification_before_action() -> None:
    text = PROMPT.read_text(encoding="utf-8")

    assert "Classify every user turn before taking action." in text
    assert "Do not assume arbitrary text is a JD." in text
    assert "Pure chat" in text
    assert "progress question" in text
    assert "supplementary requirement" in text
    assert "jobTitle" in text
    assert "jd" in text
    assert "notes" in text
    assert "Never start runtime when jobTitle or jd is missing." in text
```

Create `tests/test_workbench_v2_agent_loop.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from seektalent_workbench_v2.agent_loop import WorkbenchV2AgentOutput, WorkbenchV2RuntimeInput


def test_agent_output_validates_pure_chat_without_runtime_input() -> None:
    output = WorkbenchV2AgentOutput.model_validate(
        {
            "intent": "chat",
            "message": "你好，我可以帮你处理招聘需求，也可以回答当前流程问题。",
            "needsClarification": False,
            "clarifyingQuestion": None,
            "runtimeInput": None,
            "requirementPatch": None,
            "memoryRead": None,
            "memoryWrite": None,
        }
    )

    assert output.intent == "chat"
    assert output.runtimeInput is None


def test_agent_output_validates_recruitment_input() -> None:
    output = WorkbenchV2AgentOutput.model_validate(
        {
            "intent": "extract_requirements",
            "message": "我已识别到这是一个数据科学家招聘需求，先整理需求供你确认。",
            "needsClarification": False,
            "clarifyingQuestion": None,
            "runtimeInput": {
                "jobTitle": "数据科学家",
                "jd": "负责指标体系、A/B Testing、SQL 和 Python 分析。",
                "notes": "杭州，5 年以上经验。",
            },
            "requirementPatch": None,
            "memoryRead": None,
            "memoryWrite": None,
        }
    )

    assert output.runtimeInput == WorkbenchV2RuntimeInput(
        jobTitle="数据科学家",
        jd="负责指标体系、A/B Testing、SQL 和 Python 分析。",
        notes="杭州，5 年以上经验。",
    )


def test_start_runtime_intent_requires_runtime_input() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2AgentOutput.model_validate(
            {
                "intent": "start_runtime",
                "message": "开始运行。",
                "needsClarification": False,
                "clarifyingQuestion": None,
                "runtimeInput": None,
                "requirementPatch": None,
                "memoryRead": None,
                "memoryWrite": None,
            }
        )


def test_clarification_requires_question() -> None:
    with pytest.raises(ValidationError):
        WorkbenchV2AgentOutput.model_validate(
            {
                "intent": "extract_requirements",
                "message": "我需要确认岗位名称。",
                "needsClarification": True,
                "clarifyingQuestion": None,
                "runtimeInput": None,
                "requirementPatch": None,
                "memoryRead": None,
                "memoryWrite": None,
            }
        )
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/test_llm_provider_config.py::test_workbench_conversation_stage_uses_bailian_native_strict_schema tests/test_workbench_v2_prompt_contract.py tests/test_workbench_v2_agent_loop.py -q
```

Expected: unsupported `workbench_conversation` stage and missing files.

- [ ] **Step 3: Add model config stage**

Modify `src/seektalent/config.py`. Add these field declarations to `TextLLMSettings`:

```python
workbench_conversation_model_id: str
workbench_conversation_reasoning_effort: ReasoningEffort
```

Add settings defaults:

```python
workbench_conversation_model_id: str = "deepseek-v4-flash"
workbench_conversation_reasoning_effort: ReasoningEffort = "off"
```

Add `workbench_conversation_model_id` to `TEXT_LLM_MODEL_ID_FIELDS`. Add both settings fields to `TextLLMSettings` and `AppSettings.text_llm`.

Modify `src/seektalent/llm.py`. Add this entry to `STAGE_MODEL_ATTR`:

```python
"workbench_conversation": "workbench_conversation_model_id",
```

In `_resolve_stage_reasoning_policy`, add:

```python
if stage == "workbench_conversation":
    effort = settings.workbench_conversation_reasoning_effort
    return False, effort
```

Ensure `workbench_conversation` is not in `PLAIN_TEXT_STAGES` or `OPENAI_PROMPTED_JSON_STAGES`, so `resolve_structured_output_mode` returns `native_json_schema` for the default Bailian OpenAI-compatible path.

Modify `.env.example` near the Text LLM section:

```dotenv
SEEKTALENT_WORKBENCH_CONVERSATION_MODEL_ID=deepseek-v4-flash
SEEKTALENT_WORKBENCH_CONVERSATION_REASONING_EFFORT=off
```

- [ ] **Step 4: Add agent output models and strict loop**

Create `src/seektalent_workbench_v2/agent_loop.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from agents import Agent, Runner
from pydantic import BaseModel, ConfigDict, Field, model_validator

from seektalent.config import AppSettings
from seektalent.llm import build_model, build_model_settings, build_output_spec, resolve_stage_model_config
from seektalent_workbench_v2.models import WorkbenchV2TranscriptEvent


WorkbenchV2Intent = Literal[
    "chat",
    "extract_requirements",
    "update_requirements",
    "confirm_requirements",
    "start_runtime",
    "get_runtime_status",
    "get_runtime_results",
    "read_memory",
    "write_memory",
]


class WorkbenchV2RuntimeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobTitle: str = Field(min_length=1)
    jd: str = Field(min_length=1)
    notes: str | None = None


class WorkbenchV2MemoryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    content: str = Field(min_length=1)


class WorkbenchV2AgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: WorkbenchV2Intent
    message: str = Field(min_length=1, max_length=2000)
    needsClarification: bool = False
    clarifyingQuestion: str | None = None
    runtimeInput: WorkbenchV2RuntimeInput | None = None
    requirementPatch: dict[str, object] | None = None
    memoryRead: dict[str, object] | None = None
    memoryWrite: WorkbenchV2MemoryWrite | None = None

    @model_validator(mode="after")
    def validate_action_requirements(self) -> "WorkbenchV2AgentOutput":
        if self.needsClarification and not self.clarifyingQuestion:
            raise ValueError("clarifyingQuestion is required when needsClarification is true")
        if self.intent == "start_runtime" and self.runtimeInput is None:
            raise ValueError("runtimeInput is required for start_runtime")
        return self


class WorkbenchV2AgentLoop(Protocol):
    async def run_turn(
        self,
        *,
        conversation_id: str,
        user_text: str,
        recent_events: list[WorkbenchV2TranscriptEvent],
        context_summary: str | None,
    ) -> WorkbenchV2AgentOutput:
        raise NotImplementedError


class BailianStrictWorkbenchV2AgentLoop:
    def __init__(self, *, settings: AppSettings, runner: Runner | None = None) -> None:
        self.settings = settings
        self.runner = runner or Runner()

    async def run_turn(
        self,
        *,
        conversation_id: str,
        user_text: str,
        recent_events: list[WorkbenchV2TranscriptEvent],
        context_summary: str | None,
    ) -> WorkbenchV2AgentOutput:
        config = resolve_stage_model_config(self.settings, stage="workbench_conversation")
        model = build_model(config)
        instructions = _system_prompt()
        prompt = _render_turn_prompt(
            conversation_id=conversation_id,
            user_text=user_text,
            recent_events=recent_events,
            context_summary=context_summary,
        )
        agent = Agent(
            name="SeekTalent Workbench v2 Agent",
            model=model,
            instructions=instructions,
            output_type=build_output_spec(config, model, WorkbenchV2AgentOutput),
            model_settings=build_model_settings(config),
        )
        result = await self.runner.run(agent, prompt)
        return WorkbenchV2AgentOutput.model_validate(getattr(result, "final_output", result))


def _system_prompt() -> str:
    return Path(__file__).with_name("prompts").joinpath("system.md").read_text(encoding="utf-8")
```

Add `_render_turn_prompt` to serialize recent events compactly:

```python
def _render_turn_prompt(
    *,
    conversation_id: str,
    user_text: str,
    recent_events: list[WorkbenchV2TranscriptEvent],
    context_summary: str | None,
) -> str:
    event_lines = [
        {
            "step": event.step,
            "type": event.type,
            "role": event.role,
            "status": event.status,
            "payload": event.payload,
        }
        for event in recent_events[-20:]
    ]
    return (
        f"conversation_id: {conversation_id}\n"
        f"context_summary: {context_summary or ''}\n"
        f"recent_events: {event_lines}\n"
        f"user_text: {user_text}"
    )
```

- [ ] **Step 5: Add the system prompt**

Create `src/seektalent_workbench_v2/prompts/system.md`:

```markdown
You are SeekTalent Workbench v2 Agent, a general recruiting workbench assistant.

Classify every user turn before taking action.
Do not assume arbitrary text is a JD.

Intent rules:
- Pure chat: answer directly, do not call runtime tools, and set runtimeInput to null.
- New JD or recruitment need: normalize the text into jobTitle, jd, and optional notes.
- Supplementary requirement: update the current requirement form instead of creating a new unrelated run.
- Requirement confirmation: confirm only the current requirement form.
- Progress question: read runtime status, do not edit requirements.
- Result or detail question: read runtime results, do not edit requirements.
- Memory request: read or write memory only when the user explicitly asks or when the source is explicit.

Runtime rules:
- Never start runtime when jobTitle or jd is missing.
- If jobTitle or jd is missing or ambiguous, ask one focused clarification question.
- Do not ask the user to manually split a pasted JD when the fields can be inferred.

Output rules:
- Return only the strict structured schema.
- Keep message concise and user-facing.
- Do not expose provider errors, stack traces, tool payloads, operation audits, or internal IDs unless the user asks for a specific ID.
- Long-term memory writes must include the exact source in memoryWrite.source.
```

- [ ] **Step 6: Run agent contract tests**

Run:

```bash
uv run pytest tests/test_llm_provider_config.py::test_workbench_conversation_stage_uses_bailian_native_strict_schema tests/test_workbench_v2_prompt_contract.py tests/test_workbench_v2_agent_loop.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit Task 2**

```bash
git add .env.example src/seektalent/config.py src/seektalent/llm.py src/seektalent_workbench_v2 tests/test_llm_provider_config.py tests/test_workbench_v2_agent_loop.py tests/test_workbench_v2_prompt_contract.py
git commit -m "feat: add Workbench v2 strict agent contract"
```

## Task 3: Runtime Service Boundary

**Files:**
- Create: `src/seektalent_workbench_v2/runtime_service.py`
- Test: `tests/test_workbench_v2_runtime_service.py`

- [ ] **Step 1: Write runtime service tests**

Create `tests/test_workbench_v2_runtime_service.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_workbench_v2.agent_loop import WorkbenchV2RuntimeInput
from seektalent_workbench_v2.runtime_service import WorkbenchV2RuntimeService
from tests.conversation_agent_test_support import DeterministicWorkflowRuntime, sample_requirement_sheet
from tests.settings_factory import make_settings


class DeterministicRequirementRuntime(DeterministicWorkflowRuntime):
    def extract_requirements(
        self,
        *,
        job_title: str,
        jd_text: str,
        notes: str | None,
        requirement_cache_scope: str | None,
    ):
        return sample_requirement_sheet(job_title=job_title)


def test_runtime_service_extracts_requirement_form(tmp_path: Path) -> None:
    runtime_store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    runtime_store.initialize()
    service = WorkbenchV2RuntimeService(
        settings=make_settings(runtime_control_db_path=str(tmp_path / "runtime_control.sqlite3")),
        runtime_store=runtime_store,
        runtime_factory=lambda: DeterministicRequirementRuntime(),
    )

    draft = service.extract_requirements(
        conversation_id="agentv2_1",
        runtime_input=WorkbenchV2RuntimeInput(jobTitle="数据科学家", jd="SQL Python A/B Testing", notes="杭州"),
    )

    assert draft.status == "draft_ready"
    assert draft.conversation_id == "agentv2_1"
    assert draft.sections


def test_runtime_service_refuses_start_without_required_fields(tmp_path: Path) -> None:
    runtime_store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    runtime_store.initialize()
    service = WorkbenchV2RuntimeService(
        settings=make_settings(runtime_control_db_path=str(tmp_path / "runtime_control.sqlite3")),
        runtime_store=runtime_store,
        runtime_factory=lambda: DeterministicRequirementRuntime(),
    )

    with pytest.raises(ValueError, match="workbench_v2_runtime_input_required"):
        service.start_run(conversation_id="agentv2_1", runtime_input=None, requirement_sheet=sample_requirement_sheet())


def test_runtime_service_enqueues_run_with_job_title_jd_and_notes(tmp_path: Path) -> None:
    runtime_store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    runtime_store.initialize()
    service = WorkbenchV2RuntimeService(
        settings=make_settings(runtime_control_db_path=str(tmp_path / "runtime_control.sqlite3")),
        runtime_store=runtime_store,
        runtime_factory=lambda: DeterministicRequirementRuntime(),
    )

    run = service.start_run(
        conversation_id="agentv2_1",
        runtime_input=WorkbenchV2RuntimeInput(jobTitle="数据科学家", jd="SQL Python A/B Testing", notes="杭州"),
        requirement_sheet=sample_requirement_sheet(job_title="数据科学家"),
    )

    assert run.runtime_run_id.startswith("rtrun_")
    assert run.status == "queued"
    snapshot = runtime_store.get_snapshot(runtime_run_id=run.runtime_run_id)
    assert snapshot.snapshot["workflowInput"]["jobTitle"] == "数据科学家"
    assert snapshot.snapshot["workflowInput"]["jdText"] == "SQL Python A/B Testing"
    assert snapshot.snapshot["workflowInput"]["notes"] == "杭州"
```

- [ ] **Step 2: Run runtime service tests and verify they fail**

Run:

```bash
uv run pytest tests/test_workbench_v2_runtime_service.py -q
```

Expected: missing `runtime_service.py`.

- [ ] **Step 3: Implement runtime service**

Create `src/seektalent_workbench_v2/runtime_service.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from seektalent.config import AppSettings
from seektalent.models import RequirementSheet
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_runtime_control.requirements import ApprovedRequirementRevision, draft_from_requirement_sheet
from seektalent_runtime_control.store import RuntimeControlStore
from seektalent_ui.workbench_store_helpers import now_iso
from seektalent_workbench_v2.agent_loop import WorkbenchV2RuntimeInput

RequirementExtractor = Callable[[WorkbenchV2RuntimeInput], RequirementSheet]


class WorkbenchV2RuntimeService:
    def __init__(
        self,
        *,
        settings: AppSettings,
        runtime_store: RuntimeControlStore,
        runtime_factory: Callable[[], object],
        requirement_extractor: RequirementExtractor | None = None,
    ) -> None:
        self.settings = settings
        self.runtime_store = runtime_store
        self.runtime_factory = runtime_factory
        self.requirement_extractor = requirement_extractor

    def extract_requirements(self, *, conversation_id: str, runtime_input: WorkbenchV2RuntimeInput):
        sheet = self._extract_requirement_sheet(runtime_input)
        return draft_from_requirement_sheet(
            conversation_id=conversation_id,
            draft_revision_id=f"reqdraft_{uuid4().hex}",
            base_revision_id=None,
            requirement_sheet=sheet,
            source="workbench_v2_agent",
            created_at=now_iso(),
        )

    def start_run(
        self,
        *,
        conversation_id: str,
        runtime_input: WorkbenchV2RuntimeInput | None,
        requirement_sheet: RequirementSheet,
    ):
        if runtime_input is None or not runtime_input.jobTitle.strip() or not runtime_input.jd.strip():
            raise ValueError("workbench_v2_runtime_input_required")
        approved = ApprovedRequirementRevision(
            approved_requirement_revision_id=f"reqapproved_{uuid4().hex}",
            draft_revision_id=None,
            agent_conversation_id=conversation_id,
            requirement_sheet=requirement_sheet,
            selected_item_ids=[],
            deselected_item_ids=[],
            created_at=now_iso(),
        )
        saved = self.runtime_store.save_approved_requirement(
            approved,
            idempotency_key=f"workbench-v2-approved:{conversation_id}",
        )
        executor = WorkflowRuntimeExecutor(
            store=self.runtime_store,
            settings=self.settings,
            runtime_factory=self.runtime_factory,
        )
        return executor.enqueue_workflow_run(
            conversation_id=conversation_id,
            workbench_session_id=None,
            approved_requirement=saved,
            job_title=runtime_input.jobTitle,
            jd_text=runtime_input.jd,
            notes=runtime_input.notes,
            source_ids=["liepin"],
            run_intent_id=f"workbench-v2:{conversation_id}:primary",
            start_idempotency_key=f"workbench-v2-start:{conversation_id}:primary",
        )

    def get_status(self, runtime_run_id: str) -> dict[str, object]:
        run = self.runtime_store.get_run(runtime_run_id)
        return {
            "runtimeRunId": run.runtime_run_id,
            "status": run.status,
            "stage": run.current_stage,
            "summary": _status_summary(run.status, run.current_stage),
        }

    def _extract_requirement_sheet(self, runtime_input: WorkbenchV2RuntimeInput) -> RequirementSheet:
        if self.requirement_extractor is not None:
            return self.requirement_extractor(runtime_input)
        runtime = self.runtime_factory()
        extractor = getattr(runtime, "extract_requirements", None)
        if callable(extractor):
            return extractor(
                job_title=runtime_input.jobTitle,
                jd_text=runtime_input.jd,
                notes=runtime_input.notes,
                requirement_cache_scope=None,
            )
        raise RuntimeError("workbench_v2_requirement_extractor_unavailable")


def _status_summary(status: str, stage: str | None) -> str:
    if status in {"queued", "running", "starting"}:
        return f"招聘流程正在{stage or status}。"
    if status == "completed":
        return "招聘流程已完成。"
    if status == "failed":
        return "招聘流程失败，请查看错误信息。"
    if status == "cancelled":
        return "招聘流程已取消。"
    return f"招聘流程状态：{status}。"
```

The production `runtime_service.py` must not import from `tests`; deterministic behavior belongs in tests through the injected runtime or `requirement_extractor`.

- [ ] **Step 4: Run runtime service tests**

Run:

```bash
uv run pytest tests/test_workbench_v2_runtime_service.py -q
```

Expected: all tests pass after production code has no `tests.*` import.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/seektalent_workbench_v2/runtime_service.py tests/test_workbench_v2_runtime_service.py
git commit -m "feat: add Workbench v2 runtime service"
```

## Task 4: Workbench v2 Application Service

**Files:**
- Create: `src/seektalent_workbench_v2/service.py`
- Create: `src/seektalent_workbench_v2/views.py`
- Modify: `src/seektalent_workbench_v2/models.py`
- Test: `tests/test_workbench_v2_service.py`

- [ ] **Step 1: Write service behavior tests**

Create `tests/test_workbench_v2_service.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from seektalent_workbench_v2.agent_loop import WorkbenchV2AgentOutput, WorkbenchV2RuntimeInput
from seektalent_workbench_v2.runtime_service import WorkbenchV2RuntimeService
from seektalent_workbench_v2.service import WorkbenchV2Service
from seektalent_workbench_v2.store import WorkbenchV2Store

pytestmark = pytest.mark.anyio


class FakeAgentLoop:
    def __init__(self, output: WorkbenchV2AgentOutput) -> None:
        self.output = output
        self.calls: list[str] = []

    async def run_turn(self, *, conversation_id, user_text, recent_events, context_summary):
        self.calls.append(user_text)
        return self.output


class FakeRuntimeService:
    def __init__(self) -> None:
        self.started_inputs: list[WorkbenchV2RuntimeInput] = []

    def extract_requirements(self, *, conversation_id, runtime_input):
        return {
            "conversationId": conversation_id,
            "status": "draft_ready",
            "sections": [
                {"sectionId": "must_have", "title": "必须满足", "items": [{"id": "sql", "text": "SQL", "selected": True}]}
            ],
        }

    def start_run(self, *, conversation_id, runtime_input, requirement_sheet):
        self.started_inputs.append(runtime_input)
        return type("Run", (), {"runtime_run_id": "rtrun_1", "status": "queued"})()

    def get_status(self, runtime_run_id):
        return {"runtimeRunId": runtime_run_id, "status": "queued", "summary": "workflow run queued"}


async def test_create_pure_chat_conversation_does_not_extract_requirements(tmp_path: Path) -> None:
    store = WorkbenchV2Store(tmp_path / "workbench_v2.sqlite3")
    store.initialize()
    runtime = FakeRuntimeService()
    service = WorkbenchV2Service(
        store=store,
        agent_loop=FakeAgentLoop(
            WorkbenchV2AgentOutput(
                intent="chat",
                message="你好，我可以帮你处理招聘需求。",
                runtimeInput=None,
                requirementPatch=None,
                memoryRead=None,
                memoryWrite=None,
            )
        ),
        runtime_service=runtime,
    )

    view = await service.create_conversation(message="你好", idempotency_key="hello-1")

    assert [event.type for event in view.transcriptEvents] == ["user_message", "assistant_message"]
    assert view.conversation.runtimeState == "idle"
    assert runtime.started_inputs == []


async def test_create_jd_conversation_appends_requirement_form(tmp_path: Path) -> None:
    store = WorkbenchV2Store(tmp_path / "workbench_v2.sqlite3")
    store.initialize()
    service = WorkbenchV2Service(
        store=store,
        agent_loop=FakeAgentLoop(
            WorkbenchV2AgentOutput(
                intent="extract_requirements",
                message="我已识别到招聘需求，请确认。",
                runtimeInput=WorkbenchV2RuntimeInput(jobTitle="数据科学家", jd="SQL Python", notes="杭州"),
                requirementPatch=None,
                memoryRead=None,
                memoryWrite=None,
            )
        ),
        runtime_service=FakeRuntimeService(),
    )

    view = await service.create_conversation(message="数据科学家，SQL Python，杭州", idempotency_key="jd-1")

    assert [event.type for event in view.transcriptEvents] == [
        "user_message",
        "assistant_status",
        "requirement_form",
        "assistant_message",
    ]
    assert view.requirementForm is not None


async def test_vague_recruitment_input_asks_clarification_and_does_not_start(tmp_path: Path) -> None:
    store = WorkbenchV2Store(tmp_path / "workbench_v2.sqlite3")
    store.initialize()
    runtime = FakeRuntimeService()
    service = WorkbenchV2Service(
        store=store,
        agent_loop=FakeAgentLoop(
            WorkbenchV2AgentOutput(
                intent="extract_requirements",
                message="需要确认岗位名称。",
                needsClarification=True,
                clarifyingQuestion="这个招聘需求对应的岗位名称是什么？",
                runtimeInput=None,
                requirementPatch=None,
                memoryRead=None,
                memoryWrite=None,
            )
        ),
        runtime_service=runtime,
    )

    view = await service.create_conversation(message="帮我找几个合适的人", idempotency_key="vague-1")

    assert [event.type for event in view.transcriptEvents] == ["user_message", "assistant_message"]
    assert "岗位名称" in view.transcriptEvents[-1].payload["text"]
    assert runtime.started_inputs == []
```

- [ ] **Step 2: Run service tests and verify they fail**

Run:

```bash
uv run pytest tests/test_workbench_v2_service.py -q
```

Expected: missing `WorkbenchV2Service`.

- [ ] **Step 3: Add API view models**

Extend `src/seektalent_workbench_v2/models.py`:

```python
class WorkbenchV2ConversationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversationId: str
    title: str
    status: str
    updatedAt: str


class WorkbenchV2ConversationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["agent.workbench.v2"] = "agent.workbench.v2"
    conversation: dict[str, object]
    transcriptEvents: list[dict[str, object]]
    requirementForm: dict[str, object] | None = None
    runtime: dict[str, object] | None = None


class WorkbenchV2ConversationListView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["agent.workbench.v2.list"] = "agent.workbench.v2.list"
    conversations: list[WorkbenchV2ConversationSummary]
```

- [ ] **Step 4: Add views**

Create `src/seektalent_workbench_v2/views.py`:

```python
from __future__ import annotations

from seektalent_workbench_v2.models import (
    WorkbenchV2Conversation,
    WorkbenchV2ConversationListView,
    WorkbenchV2ConversationRecord,
    WorkbenchV2ConversationSummary,
    WorkbenchV2ConversationView,
)


def conversation_view(record: WorkbenchV2ConversationRecord) -> WorkbenchV2ConversationView:
    requirement_form = None
    for event in reversed(record.events):
        if event.type == "requirement_form":
            requirement_form = event.payload
            break
        if event.type == "requirement_form_confirmed":
            requirement_form = {**event.payload, "readonly": True}
            break
    return WorkbenchV2ConversationView(
        conversation={
            "conversationId": record.conversation.id,
            "title": record.conversation.title,
            "runtimeState": record.conversation.runtime_state,
            "runtimeRunId": record.conversation.runtime_run_id,
            "createdAt": record.conversation.created_at,
            "updatedAt": record.conversation.updated_at,
        },
        transcriptEvents=[
            {
                "eventId": event.id,
                "step": event.step,
                "type": event.type,
                "role": event.role,
                "status": event.status,
                "payload": event.payload,
                "createdAt": event.created_at,
            }
            for event in record.events
            if event.type != "context_summary"
        ],
        requirementForm=requirement_form,
        runtime={
            "runtimeRunId": record.conversation.runtime_run_id,
            "state": record.conversation.runtime_state,
        }
        if record.conversation.runtime_run_id
        else None,
    )


def conversation_list_view(conversations: list[WorkbenchV2Conversation]) -> WorkbenchV2ConversationListView:
    return WorkbenchV2ConversationListView(
        conversations=[
            WorkbenchV2ConversationSummary(
                conversationId=conversation.id,
                title=conversation.title,
                status=conversation.runtime_state,
                updatedAt=conversation.updated_at,
            )
            for conversation in conversations
        ]
    )
```

- [ ] **Step 5: Add application service**

Create `src/seektalent_workbench_v2/service.py` with async-safe create/message entrypoints. FastAPI route methods that call the agent loop must be async and await this service directly.

```python
from __future__ import annotations

from seektalent_workbench_v2.agent_loop import WorkbenchV2AgentLoop, WorkbenchV2AgentOutput
from seektalent_workbench_v2.models import WorkbenchV2ConversationView, WorkbenchV2TranscriptEventInput
from seektalent_workbench_v2.runtime_service import WorkbenchV2RuntimeService
from seektalent_workbench_v2.store import WorkbenchV2Store
from seektalent_workbench_v2.views import conversation_list_view, conversation_view


class WorkbenchV2Service:
    def __init__(
        self,
        *,
        store: WorkbenchV2Store,
        agent_loop: WorkbenchV2AgentLoop,
        runtime_service: WorkbenchV2RuntimeService,
    ) -> None:
        self.store = store
        self.agent_loop = agent_loop
        self.runtime_service = runtime_service

    def list_conversations(self):
        return conversation_list_view(self.store.list_conversations())

    def get_conversation(self, conversation_id: str) -> WorkbenchV2ConversationView:
        return conversation_view(self.store.get_conversation(conversation_id))

    async def create_conversation(self, *, message: str, idempotency_key: str | None) -> WorkbenchV2ConversationView:
        conversation = self.store.create_conversation(first_user_text=message, idempotency_key=idempotency_key)
        self.store.append_event(
            conversation.id,
            WorkbenchV2TranscriptEventInput(
                type="user_message",
                role="user",
                payload={"text": message},
                status="completed",
                dedupe_key=f"user:create:{idempotency_key}" if idempotency_key else None,
            ),
        )
        output = await self.agent_loop.run_turn(
            conversation_id=conversation.id,
            user_text=message,
            recent_events=self.store.get_conversation(conversation.id).events,
            context_summary=conversation.context_summary,
        )
        self._apply_agent_output(conversation.id, output)
        return self.get_conversation(conversation.id)

    async def submit_message(self, *, conversation_id: str, message: str, idempotency_key: str | None) -> WorkbenchV2ConversationView:
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="user_message",
                role="user",
                payload={"text": message},
                status="completed",
                dedupe_key=f"user:message:{idempotency_key}" if idempotency_key else None,
            ),
        )
        record = self.store.get_conversation(conversation_id)
        output = await self.agent_loop.run_turn(
            conversation_id=conversation_id,
            user_text=message,
            recent_events=record.events,
            context_summary=record.conversation.context_summary,
        )
        self._apply_agent_output(conversation_id, output)
        return self.get_conversation(conversation_id)

    def _apply_agent_output(self, conversation_id: str, output: WorkbenchV2AgentOutput) -> None:
        if output.needsClarification:
            self.store.append_event(
                conversation_id,
                WorkbenchV2TranscriptEventInput(
                    type="assistant_message",
                    role="assistant",
                    payload={"text": output.clarifyingQuestion or output.message},
                    status="completed",
                ),
            )
            return
        if output.intent in {"extract_requirements", "update_requirements"} and output.runtimeInput is not None:
            self.store.append_event(
                conversation_id,
                WorkbenchV2TranscriptEventInput(
                    type="assistant_status",
                    role="assistant",
                    payload={"text": "正在整理需求"},
                    status="running",
                ),
            )
            draft = self.runtime_service.extract_requirements(conversation_id=conversation_id, runtime_input=output.runtimeInput)
            self.store.append_event(
                conversation_id,
                WorkbenchV2TranscriptEventInput(
                    type="requirement_form",
                    role="assistant",
                    payload={"runtimeInput": output.runtimeInput.model_dump(mode="json"), "draft": _dump(draft)},
                    status="completed",
                ),
            )
        self.store.append_event(
            conversation_id,
            WorkbenchV2TranscriptEventInput(
                type="assistant_message",
                role="assistant",
                payload={"text": output.message},
                status="completed",
            ),
        )


def _dump(value: object) -> object:
    method = getattr(value, "model_dump", None)
    if callable(method):
        return method(mode="json")
    return value
```

- [ ] **Step 6: Run service tests**

Run:

```bash
uv run pytest tests/test_workbench_v2_service.py tests/test_workbench_v2_store.py tests/test_workbench_v2_agent_loop.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/seektalent_workbench_v2 tests/test_workbench_v2_service.py
git commit -m "feat: add Workbench v2 service layer"
```

## Task 5: FastAPI Route Shim and Server Wiring

**Files:**
- Create: `src/seektalent_ui/agent_workbench_v2_routes.py`
- Modify: `src/seektalent_ui/server.py`
- Modify: `src/seektalent/backup_group.py`
- Test: `tests/test_workbench_v2_routes.py`
- Test: `tests/test_workbench_v2_boundaries.py`

- [ ] **Step 1: Write route and boundary tests**

Create `tests/test_workbench_v2_routes.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from seektalent_ui.server import create_app
from tests.settings_factory import make_settings


class FakeWorkbenchV2RouteService:
    async def create_conversation(self, *, message: str, idempotency_key: str | None):
        return {
            "schemaVersion": "agent.workbench.v2",
            "conversation": {
                "conversationId": "agentv2_route",
                "title": message,
                "runtimeState": "idle",
                "runtimeRunId": None,
                "createdAt": "2026-06-25T00:00:00Z",
                "updatedAt": "2026-06-25T00:00:00Z",
            },
            "transcriptEvents": [
                {
                    "eventId": "event_1",
                    "step": 1,
                    "type": "user_message",
                    "role": "user",
                    "status": "completed",
                    "payload": {"text": message},
                    "createdAt": "2026-06-25T00:00:00Z",
                }
            ],
            "requirementForm": None,
            "runtime": None,
        }

    def get_conversation(self, conversation_id: str):
        raise KeyError(conversation_id)


def test_v2_create_pure_chat_route(tmp_path) -> None:
    app = create_app(settings=make_settings(local_data_root=str(tmp_path)))
    app.state.workbench_v2_service = FakeWorkbenchV2RouteService()
    client = TestClient(app)

    response = client.post(
        "/api/agent/workbench/v2/conversations",
        json={"message": "你好", "idempotencyKey": "hello-route"},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["schemaVersion"] == "agent.workbench.v2"
    assert payload["conversation"]["conversationId"].startswith("agentv2_")
    assert [event["type"] for event in payload["transcriptEvents"]][:1] == ["user_message"]


def test_v2_missing_conversation_returns_404(tmp_path) -> None:
    app = create_app(settings=make_settings(local_data_root=str(tmp_path)))
    app.state.workbench_v2_service = FakeWorkbenchV2RouteService()
    client = TestClient(app)

    response = client.get("/api/agent/workbench/v2/conversations/agentv2_missing")

    assert response.status_code == 404
    assert response.json()["detail"]["reasonCode"] == "workbench_v2_conversation_not_found"
```

Create `tests/test_workbench_v2_boundaries.py`:

```python
from __future__ import annotations

import ast
from pathlib import Path


FORBIDDEN_RUNTIME_IMPORT_PREFIXES = (
    "seektalent_workbench_v2",
    "seektalent_ui.agent_workbench_v2_routes",
)
FORBIDDEN_V2_IMPORT_PREFIXES = (
    "seektalent_ui.agent_workbench_transcript",
    "seektalent_ui.agent_workbench_response",
    "seektalent_ui.agent_workbench_projection",
    "seektalent_conversation_agent.first_turn_store",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_runtime_does_not_depend_on_workbench_v2() -> None:
    for path in Path("src/seektalent").rglob("*.py"):
        for imported in _imports(path):
            assert not imported.startswith(FORBIDDEN_RUNTIME_IMPORT_PREFIXES), f"{path} imports {imported}"


def test_workbench_v2_does_not_import_old_projection_path() -> None:
    for path in Path("src/seektalent_workbench_v2").rglob("*.py"):
        for imported in _imports(path):
            assert not imported.startswith(FORBIDDEN_V2_IMPORT_PREFIXES), f"{path} imports {imported}"
```

- [ ] **Step 2: Run route tests and verify they fail**

Run:

```bash
uv run pytest tests/test_workbench_v2_routes.py tests/test_workbench_v2_boundaries.py -q
```

Expected: route 404 and missing package imports before wiring.

- [ ] **Step 3: Add route shim**

Create `src/seektalent_ui/agent_workbench_v2_routes.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from seektalent_workbench_v2.models import WorkbenchV2ConversationView

router = APIRouter(prefix="/api/agent/workbench/v2")


class WorkbenchV2CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    idempotencyKey: str | None = None


class WorkbenchV2MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    idempotencyKey: str | None = None


@router.get("/conversations")
def list_conversations(request: Request):
    return _service(request).list_conversations()


@router.post("/conversations", response_model=WorkbenchV2ConversationView, status_code=status.HTTP_201_CREATED)
async def create_conversation(payload: WorkbenchV2CreateRequest, request: Request) -> WorkbenchV2ConversationView:
    return await _service(request).create_conversation(message=payload.message, idempotency_key=payload.idempotencyKey)


@router.get("/conversations/{conversation_id}", response_model=WorkbenchV2ConversationView)
def get_conversation(conversation_id: str, request: Request) -> WorkbenchV2ConversationView:
    try:
        return _service(request).get_conversation(conversation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"reasonCode": "workbench_v2_conversation_not_found"}) from exc


@router.post("/conversations/{conversation_id}/messages", response_model=WorkbenchV2ConversationView)
async def submit_message(
    conversation_id: str,
    payload: WorkbenchV2MessageRequest,
    request: Request,
) -> WorkbenchV2ConversationView:
    try:
        return await _service(request).submit_message(
            conversation_id=conversation_id,
            message=payload.message,
            idempotency_key=payload.idempotencyKey,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"reasonCode": "workbench_v2_conversation_not_found"}) from exc


def _service(request: Request):
    return request.app.state.workbench_v2_service
```

- [ ] **Step 4: Wire server state**

Modify `src/seektalent_ui/server.py`:

```python
from seektalent_ui import agent_workbench_v2_routes
from seektalent_workbench_v2.agent_loop import BailianStrictWorkbenchV2AgentLoop
from seektalent_workbench_v2.runtime_service import WorkbenchV2RuntimeService
from seektalent_workbench_v2.service import WorkbenchV2Service
from seektalent_workbench_v2.store import WorkbenchV2Store
```

Inside `create_app`, after runtime-control state exists:

```python
app.state.workbench_v2_store = WorkbenchV2Store(app_settings.resolve_workspace_path(".seektalent/workbench_v2.sqlite3"))
app.state.workbench_v2_store.initialize()
app.state.workbench_v2_service = WorkbenchV2Service(
    store=app.state.workbench_v2_store,
    agent_loop=BailianStrictWorkbenchV2AgentLoop(settings=app_settings),
    runtime_service=WorkbenchV2RuntimeService(
        settings=app_settings,
        runtime_store=app.state.runtime_control_store,
        runtime_factory=runtime_factory,
    ),
)
```

Register route:

```python
app.include_router(agent_workbench_v2_routes.router)
```

Route tests inject a fake `workbench_v2_service` through app state after `create_app`; production wiring always uses `BailianStrictWorkbenchV2AgentLoop`.

- [ ] **Step 5: Register backup database**

Modify `src/seektalent/backup_group.py` to include:

```python
ProductDatabaseSpec("workbench_v2", workspace_root / ".seektalent" / "workbench_v2.sqlite3")
```

Keep this in the product DB list with other local SQLite DBs.

- [ ] **Step 6: Run route and boundary tests**

Run:

```bash
uv run pytest tests/test_workbench_v2_routes.py tests/test_workbench_v2_boundaries.py -q
```

Expected: all selected tests pass without calling a live model provider. Use a fake loop in `create_app` tests if needed.

- [ ] **Step 7: Commit Task 5**

```bash
git add src/seektalent_ui/agent_workbench_v2_routes.py src/seektalent_ui/server.py src/seektalent/backup_group.py tests/test_workbench_v2_routes.py tests/test_workbench_v2_boundaries.py
git commit -m "feat: expose Workbench v2 API routes"
```

## Task 6: Frontend v2 API Client and Hooks

**Files:**
- Create: `apps/web-react/src/lib/api/workbenchV2Types.ts`
- Create: `apps/web-react/src/lib/api/workbenchV2Client.ts`
- Create: `apps/web-react/src/lib/api/workbenchV2.ts`
- Modify: `apps/web-react/src/lib/query/keys.ts`
- Test: `apps/web-react/src/lib/api/workbenchV2.test.ts`

- [ ] **Step 1: Write client tests**

Create `apps/web-react/src/lib/api/workbenchV2.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import {
  normalizeWorkbenchV2Conversation,
  type WorkbenchV2ConversationView,
} from "./workbenchV2Types";

describe("Workbench v2 API types", () => {
  it("normalizes transcript events ordered by step", () => {
    const view = normalizeWorkbenchV2Conversation({
      schemaVersion: "agent.workbench.v2",
      conversation: {
        conversationId: "agentv2_1",
        title: "你好",
        runtimeState: "idle",
        runtimeRunId: null,
        createdAt: "2026-06-25T00:00:00Z",
        updatedAt: "2026-06-25T00:00:01Z",
      },
      transcriptEvents: [
        {
          eventId: "e2",
          step: 2,
          type: "assistant_message",
          role: "assistant",
          status: "completed",
          payload: { text: "你好" },
          createdAt: "2026-06-25T00:00:01Z",
        },
        {
          eventId: "e1",
          step: 1,
          type: "user_message",
          role: "user",
          status: "completed",
          payload: { text: "你好" },
          createdAt: "2026-06-25T00:00:00Z",
        },
      ],
      requirementForm: null,
      runtime: null,
    });

    expect(view.transcriptEvents.map((event) => event.eventId)).toEqual(["e1", "e2"]);
  });
});
```

- [ ] **Step 2: Run frontend API test and verify it fails**

Run:

```bash
pnpm --dir apps/web-react test -- --run src/lib/api/workbenchV2.test.ts
```

Expected: missing module.

- [ ] **Step 3: Add v2 types**

Create `apps/web-react/src/lib/api/workbenchV2Types.ts`:

```ts
export type WorkbenchV2EventType =
  | "user_message"
  | "assistant_message"
  | "assistant_status"
  | "requirement_form"
  | "requirement_form_confirmed"
  | "runtime_progress"
  | "runtime_result"
  | "error"
  | "context_summary";

export type WorkbenchV2TranscriptEvent = {
  eventId: string;
  step: number;
  type: WorkbenchV2EventType;
  role: "user" | "assistant" | "system" | "runtime";
  status: "pending" | "running" | "completed" | "failed";
  payload: Record<string, unknown>;
  createdAt: string;
};

export type WorkbenchV2ConversationView = {
  schemaVersion: "agent.workbench.v2";
  conversation: {
    conversationId: string;
    title: string;
    runtimeState: "idle" | "queued" | "running" | "completed" | "failed" | "cancelled";
    runtimeRunId: string | null;
    createdAt: string;
    updatedAt: string;
  };
  transcriptEvents: WorkbenchV2TranscriptEvent[];
  requirementForm: Record<string, unknown> | null;
  runtime: Record<string, unknown> | null;
};

export type WorkbenchV2ConversationListView = {
  schemaVersion: "agent.workbench.v2.list";
  conversations: Array<{
    conversationId: string;
    title: string;
    status: string;
    updatedAt: string;
  }>;
};

export function normalizeWorkbenchV2Conversation(input: WorkbenchV2ConversationView): WorkbenchV2ConversationView {
  return {
    ...input,
    transcriptEvents: [...input.transcriptEvents].sort((left, right) => left.step - right.step),
  };
}
```

- [ ] **Step 4: Add v2 client and hooks**

Create `apps/web-react/src/lib/api/workbenchV2Client.ts` using existing `api` helpers from `client.ts` or a small `fetch` wrapper:

```ts
import { normalizeWorkbenchV2Conversation, type WorkbenchV2ConversationListView, type WorkbenchV2ConversationView } from "./workbenchV2Types";

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(`workbench_v2_http_${response.status}`);
  return (await response.json()) as T;
}

export async function listWorkbenchV2Conversations(): Promise<WorkbenchV2ConversationListView> {
  return json<WorkbenchV2ConversationListView>(await fetch("/api/agent/workbench/v2/conversations"));
}

export async function createWorkbenchV2Conversation(payload: {
  message: string;
  idempotencyKey?: string;
}): Promise<WorkbenchV2ConversationView> {
  const view = await json<WorkbenchV2ConversationView>(
    await fetch("/api/agent/workbench/v2/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
  return normalizeWorkbenchV2Conversation(view);
}

export async function getWorkbenchV2Conversation(conversationId: string): Promise<WorkbenchV2ConversationView> {
  const view = await json<WorkbenchV2ConversationView>(
    await fetch(`/api/agent/workbench/v2/conversations/${encodeURIComponent(conversationId)}`),
  );
  return normalizeWorkbenchV2Conversation(view);
}

export async function submitWorkbenchV2Message(
  conversationId: string,
  payload: { message: string; idempotencyKey?: string },
): Promise<WorkbenchV2ConversationView> {
  const view = await json<WorkbenchV2ConversationView>(
    await fetch(`/api/agent/workbench/v2/conversations/${encodeURIComponent(conversationId)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
  return normalizeWorkbenchV2Conversation(view);
}
```

Create `apps/web-react/src/lib/api/workbenchV2.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "../query/keys";
import {
  createWorkbenchV2Conversation,
  getWorkbenchV2Conversation,
  listWorkbenchV2Conversations,
  submitWorkbenchV2Message,
} from "./workbenchV2Client";

export function useWorkbenchV2Conversations() {
  return useQuery({
    queryKey: queryKeys.workbenchV2Conversations,
    queryFn: listWorkbenchV2Conversations,
  });
}

export function useWorkbenchV2Conversation(conversationId: string) {
  return useQuery({
    queryKey: queryKeys.workbenchV2Conversation(conversationId),
    queryFn: () => getWorkbenchV2Conversation(conversationId),
    refetchInterval: 2000,
  });
}

export function useCreateWorkbenchV2Conversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createWorkbenchV2Conversation,
    onSuccess: (view) => {
      queryClient.setQueryData(queryKeys.workbenchV2Conversation(view.conversation.conversationId), view);
      void queryClient.invalidateQueries({ queryKey: queryKeys.workbenchV2Conversations });
    },
  });
}

export function useSubmitWorkbenchV2Message(conversationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { message: string; idempotencyKey?: string }) =>
      submitWorkbenchV2Message(conversationId, payload),
    onSuccess: (view) => {
      queryClient.setQueryData(queryKeys.workbenchV2Conversation(conversationId), view);
    },
  });
}
```

Modify `apps/web-react/src/lib/query/keys.ts`:

```ts
workbenchV2Conversations: ["agent", "workbench", "v2", "conversations"] as const,
workbenchV2Conversation: (conversationId: string) =>
  ["agent", "workbench", "v2", "conversations", conversationId] as const,
```

- [ ] **Step 5: Run frontend API test**

Run:

```bash
pnpm --dir apps/web-react test -- --run src/lib/api/workbenchV2.test.ts
```

Expected: selected test passes.

- [ ] **Step 6: Commit Task 6**

```bash
git add apps/web-react/src/lib/api/workbenchV2* apps/web-react/src/lib/query/keys.ts
git commit -m "feat: add Workbench v2 frontend API client"
```

## Task 7: Frontend Transcript v2 Renderer and Route Integration

**Files:**
- Create: `apps/web-react/src/components/workbench/TranscriptV2.tsx`
- Create: `apps/web-react/src/components/workbench/TranscriptV2.css`
- Create: `apps/web-react/src/components/workbench/RequirementFormEvent.tsx`
- Create: `apps/web-react/src/components/workbench/RequirementFormEvent.css`
- Create: `apps/web-react/src/components/workbench/ConversationScreenV2.tsx`
- Create: `apps/web-react/src/components/workbench/ConversationScreenV2.css`
- Modify: `apps/web-react/src/routes/conversation.tsx`
- Modify: `apps/web-react/src/components/workbench/HomeStartPanel.tsx`
- Test: `apps/web-react/src/components/workbench/TranscriptV2.test.tsx`
- Test: `apps/web-react/src/components/workbench/RequirementFormEvent.test.tsx`
- Test: `apps/web-react/src/components/workbench/ConversationScreenV2.test.tsx`

- [ ] **Step 1: Write renderer tests**

Create `apps/web-react/src/components/workbench/TranscriptV2.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TranscriptV2 } from "./TranscriptV2";
import type { WorkbenchV2TranscriptEvent } from "../../lib/api/workbenchV2Types";

const events: WorkbenchV2TranscriptEvent[] = [
  {
    eventId: "u1",
    step: 1,
    type: "user_message",
    role: "user",
    status: "completed",
    payload: { text: "你好" },
    createdAt: "2026-06-25T00:00:00Z",
  },
  {
    eventId: "a1",
    step: 2,
    type: "assistant_message",
    role: "assistant",
    status: "completed",
    payload: { text: "你好，我可以帮你处理招聘需求。" },
    createdAt: "2026-06-25T00:00:01Z",
  },
];

describe("TranscriptV2", () => {
  it("renders flat agent transcript without legacy processed group header", () => {
    render(<TranscriptV2 events={events} />);

    expect(screen.getByText("你好")).toBeInTheDocument();
    expect(screen.getByText("你好，我可以帮你处理招聘需求。")).toBeInTheDocument();
    expect(screen.queryByText("已处理")).not.toBeInTheDocument();
  });

  it("renders long JD text without truncating it", () => {
    const longText = "岗位描述 ".repeat(300);
    render(
      <TranscriptV2
        events={[
          {
            ...events[0],
            payload: { text: longText },
          },
        ]}
      />,
    );

    expect(screen.getByText(longText.trim())).toBeInTheDocument();
  });
});
```

Create `apps/web-react/src/components/workbench/RequirementFormEvent.test.tsx` with a checkbox toggle test:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RequirementFormEvent } from "./RequirementFormEvent";

describe("RequirementFormEvent", () => {
  it("allows checkbox cancellation before confirmation", async () => {
    const onChange = vi.fn();
    render(
      <RequirementFormEvent
        readonly={false}
        form={{
          draft: {
            sections: [
              {
                section_id: "must_have_capabilities",
                display_name: "必须满足",
                items: [{ item_id: "sql", text: "SQL", selected: true }],
              },
            ],
          },
        }}
        onChange={onChange}
      />,
    );

    await userEvent.click(screen.getByRole("checkbox", { name: "SQL" }));

    expect(onChange).toHaveBeenCalledWith({ itemId: "sql", selected: false });
  });
});
```

- [ ] **Step 2: Run renderer tests and verify they fail**

Run:

```bash
pnpm --dir apps/web-react test -- --run src/components/workbench/TranscriptV2.test.tsx src/components/workbench/RequirementFormEvent.test.tsx
```

Expected: missing components.

- [ ] **Step 3: Add `RequirementFormEvent`**

Create `apps/web-react/src/components/workbench/RequirementFormEvent.tsx`:

```tsx
import "./RequirementFormEvent.css";

type RequirementFormEventProps = {
  form: Record<string, unknown>;
  readonly: boolean;
  onChange?: (change: { itemId: string; selected: boolean }) => void;
};

export function RequirementFormEvent({ form, readonly, onChange }: RequirementFormEventProps) {
  const sections = extractSections(form);
  return (
    <section className="requirement-form-event" aria-label="需求确认">
      {sections.map((section) => (
        <div className="requirement-form-event__section" key={section.id}>
          <h3>{section.title}</h3>
          <div className="requirement-form-event__items">
            {section.items.map((item) => (
              <label className="requirement-form-event__item" key={item.id}>
                <input
                  checked={item.selected}
                  disabled={readonly}
                  onChange={(event) => onChange?.({ itemId: item.id, selected: event.currentTarget.checked })}
                  type="checkbox"
                />
                <span>{item.text}</span>
              </label>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}

function extractSections(form: Record<string, unknown>) {
  const draft = form.draft as Record<string, unknown> | undefined;
  const rawSections = Array.isArray(draft?.sections) ? draft.sections : [];
  return rawSections.map((section) => {
    const record = section as Record<string, unknown>;
    const rawItems = Array.isArray(record.items) ? record.items : [];
    return {
      id: String(record.section_id ?? record.sectionId ?? record.display_name ?? record.displayName),
      title: String(record.display_name ?? record.displayName ?? record.section_id ?? record.sectionId),
      items: rawItems.map((item) => {
        const itemRecord = item as Record<string, unknown>;
        return {
          id: String(itemRecord.item_id ?? itemRecord.id),
          text: String(itemRecord.text),
          selected: itemRecord.selected !== false,
        };
      }),
    };
  });
}
```

- [ ] **Step 4: Add `TranscriptV2`**

Create `apps/web-react/src/components/workbench/TranscriptV2.tsx`:

```tsx
import type { WorkbenchV2TranscriptEvent } from "../../lib/api/workbenchV2Types";
import { RequirementFormEvent } from "./RequirementFormEvent";
import "./TranscriptV2.css";

type TranscriptV2Props = {
  events: readonly WorkbenchV2TranscriptEvent[];
  onRequirementChange?: (change: { itemId: string; selected: boolean }) => void;
};

export function TranscriptV2({ events, onRequirementChange }: TranscriptV2Props) {
  return (
    <section aria-label="Agent transcript" className="transcript-v2">
      {events.map((event) => (
        <article className="transcript-v2__event" data-role={event.role} data-type={event.type} key={event.eventId}>
          {renderEvent(event, onRequirementChange)}
        </article>
      ))}
    </section>
  );
}

function renderEvent(
  event: WorkbenchV2TranscriptEvent,
  onRequirementChange?: (change: { itemId: string; selected: boolean }) => void,
) {
  if (event.type === "requirement_form" || event.type === "requirement_form_confirmed") {
    return (
      <RequirementFormEvent
        form={event.payload}
        onChange={onRequirementChange}
        readonly={event.type === "requirement_form_confirmed"}
      />
    );
  }
  const text = event.payload.text ?? event.payload.summary ?? "";
  return <p className="transcript-v2__text">{String(text)}</p>;
}
```

Create CSS with a real scroll container:

```css
.transcript-v2 {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  flex-direction: column;
  gap: 16px;
  padding: 24px;
}

.transcript-v2__event {
  max-width: 920px;
}

.transcript-v2__event[data-role="user"] {
  align-self: flex-end;
}

.transcript-v2__text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 0;
}
```

- [ ] **Step 5: Add `ConversationScreenV2` and route switch**

Create `ConversationScreenV2.tsx` that uses `TranscriptV2`, the existing `MessageComposer`, and a right surface that appears when `runtimeState !== "idle"`.

Modify `apps/web-react/src/routes/conversation.tsx`:

```tsx
if (conversationId.startsWith("agentv2_")) {
  return <ExistingConversationV2Flow conversationId={conversationId} />;
}
```

Modify the new conversation flow to call `useCreateWorkbenchV2Conversation` with:

```ts
{ message: text, idempotencyKey: crypto.randomUUID() }
```

The submit label remains generic; do not use "JD-only" naming in user-facing text.

- [ ] **Step 6: Run frontend component tests**

Run:

```bash
pnpm --dir apps/web-react test -- --run src/components/workbench/TranscriptV2.test.tsx src/components/workbench/RequirementFormEvent.test.tsx src/components/workbench/ConversationScreenV2.test.tsx
```

Expected: selected tests pass.

- [ ] **Step 7: Commit Task 7**

```bash
git add apps/web-react/src/components/workbench apps/web-react/src/routes/conversation.tsx
git commit -m "feat: render Workbench v2 transcript"
```

## Task 8: End-to-End Flow and Regression Verification

**Files:**
- Create: `apps/web-react/tests/workbench-v2.spec.ts`
- Modify: `apps/web-react/src/test/fixtures/agentWorkbenchBff.ts` only if v2 Storybook fixtures need shared data.
- Test: backend and frontend verification commands.

- [ ] **Step 1: Add Playwright v2 flow**

Create `apps/web-react/tests/workbench-v2.spec.ts`:

```ts
import { expect, test } from "@playwright/test";

test("Workbench v2 supports chat, JD form, confirmation, and progress question", async ({ page }) => {
  await page.goto("/conversations/new");
  await page.getByPlaceholder("输入下一步要求").fill("你好");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("你好")).toBeVisible();
  await expect(page.getByText("已处理")).toHaveCount(0);

  await page.getByPlaceholder("输入下一步要求").fill("数据科学家，负责 SQL、Python、A/B Testing，杭州，5年以上。");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByLabel("需求确认")).toBeVisible();

  const sql = page.getByRole("checkbox", { name: "SQL" });
  await sql.uncheck();
  await expect(sql).not.toBeChecked();

  await page.getByRole("button", { name: "确认需求" }).click();
  await expect(page.getByText(/queued|运行|流程/)).toBeVisible();

  await page.getByPlaceholder("输入下一步要求").fill("现在进度如何");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText(/进度|状态|queued|running/)).toBeVisible();
});
```

If the composer accessible name differs in the current UI, update the selector to the existing label and keep the test user-visible.

- [ ] **Step 2: Run focused backend tests**

Run:

```bash
uv run pytest tests/test_workbench_v2_store.py tests/test_workbench_v2_agent_loop.py tests/test_workbench_v2_runtime_service.py tests/test_workbench_v2_service.py tests/test_workbench_v2_routes.py tests/test_workbench_v2_boundaries.py tests/test_workbench_v2_prompt_contract.py -q
```

Expected: all selected backend tests pass.

- [ ] **Step 3: Run focused frontend tests**

Run:

```bash
pnpm --dir apps/web-react test -- --run src/lib/api/workbenchV2.test.ts src/components/workbench/TranscriptV2.test.tsx src/components/workbench/RequirementFormEvent.test.tsx src/components/workbench/ConversationScreenV2.test.tsx
```

Expected: all selected frontend tests pass.

- [ ] **Step 4: Regenerate OpenAPI types after backend route exists**

Start backend in another terminal:

```bash
./scripts/start-dev-workbench.sh
```

Then run:

```bash
pnpm --dir apps/web-react api:gen
```

Expected: `apps/web-react/src/lib/api/schema.d.ts` updates with `/api/agent/workbench/v2/*` paths.

- [ ] **Step 5: Run full local workbench verification**

Run:

```bash
scripts/verify-dev-workbench.sh
```

Expected: script completes without failures.

- [ ] **Step 6: Commit Task 8**

```bash
git add apps/web-react tests src apps/web-react/src/lib/api/schema.d.ts
git commit -m "test: cover Workbench v2 end to end"
```

## Task 9: Manual Test Gate and Old Path Cleanup Decision

**Files:**
- Modify: `docs/superpowers/plans/2026-06-25-workbench-v2-schema-first.md` only to check completed boxes during execution.

- [ ] **Step 1: Manual run from the clean worktree**

Run:

```bash
./scripts/start-dev-workbench.sh
```

Open `http://127.0.0.1:5178/conversations/new`.

Manual cases:

1. Type `你好`.
2. Paste a complete JD block with a visible title and requirements.
3. Paste vague text such as `帮我找几个合适的人` and verify the agent asks one clarification question.
4. Add a supplementary requirement after a form appears.
5. Uncheck a requirement checkbox.
6. Confirm requirements and verify runtime state changes to queued or running.
7. Ask `现在进度如何`.
8. Refresh the page and verify transcript events are still ordered and visible.

- [ ] **Step 2: Confirm no old UI markers appear in v2**

Manual assertions:

```text
No "已处理" group header in v2 transcript.
No full-screen requirement form outside transcript.
No clipped long JD text.
No bottom white gap caused by the transcript container.
No duplicate "正在处理需求 / 正在思考" rows for one agent turn.
```

- [ ] **Step 3: Record cleanup follow-up**

If manual testing passes, create a follow-up cleanup issue or plan for deleting:

```text
old first-turn/outbox Workbench projection path
old contaminated dirty-main v2 package if it still exists
legacy transcriptGroups-only v2 rendering code
```

Do not delete old Workbench paths in this PR.

- [ ] **Step 4: Final commit if manual-test docs changed**

```bash
git add docs/superpowers/plans/2026-06-25-workbench-v2-schema-first.md
git commit -m "docs: record Workbench v2 manual validation"
```

## Plan Self-Review

- Spec coverage: Tasks 1-5 cover the v2 SQLite transcript store, strict Bailian agent contract, runtime service boundary, async application service, and FastAPI route shim. Tasks 6-8 cover v2 frontend API, flat transcript rendering, requirement form embedding, right-surface transition entrypoint, and E2E coverage. Task 9 keeps old Workbench deletion out of scope and records the manual acceptance gate.
- Input classification coverage: Task 2 system prompt and schema require every user turn to be classified before action. Task 4 service tests cover pure chat, clear recruitment input, and vague recruitment input. Runtime start remains gated by `jobTitle` and `jd`.
- Storage coverage: Task 1 persists all transcript-visible data as ordered `workbench_v2_transcript_events` rows. The UI tasks read only `transcriptEvents` and do not use old `transcriptGroups`.
- Boundary coverage: Task 3 wraps stable runtime-control APIs without reverse dependency from `src/seektalent` to v2. Task 5 adds AST boundary tests for runtime and old projection imports.
- Red-flag scan: Checked the plan for banned planning phrases and event-loop nesting calls; no matches remain.
- Type consistency: Backend uses `WorkbenchV2RuntimeInput.jobTitle`, `jd`, and `notes`; API and frontend use `conversationId`, `runtimeState`, `runtimeRunId`, `transcriptEvents`, `requirementForm`, and `runtime` consistently.
- Testability: Backend route tests inject a fake app-state service and do not call Bailian. Runtime service tests inject deterministic runtime behavior through test-only classes, not production imports.

## Final Verification

Run before opening a PR:

```bash
uv run pytest tests/test_workbench_v2_store.py tests/test_workbench_v2_agent_loop.py tests/test_workbench_v2_runtime_service.py tests/test_workbench_v2_service.py tests/test_workbench_v2_routes.py tests/test_workbench_v2_boundaries.py tests/test_workbench_v2_prompt_contract.py -q
pnpm --dir apps/web-react test -- --run src/lib/api/workbenchV2.test.ts src/components/workbench/TranscriptV2.test.tsx src/components/workbench/RequirementFormEvent.test.tsx src/components/workbench/ConversationScreenV2.test.tsx
scripts/verify-dev-workbench.sh
git status --short --branch
```

Expected:

```text
Backend focused tests pass.
Frontend focused tests pass.
verify-dev-workbench passes.
Worktree contains only intentional committed changes or expected generated files ready to commit.
```
