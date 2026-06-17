from __future__ import annotations

import sqlite3
from pathlib import Path


def test_agent_memory_store_initializes_required_tables(tmp_path: Path) -> None:
    from seektalent_agent_memory.store import AGENT_MEMORY_SCHEMA_VERSION, MemoryStore

    db_path = tmp_path / "agent_memory.sqlite3"
    store = MemoryStore(db_path)

    store.initialize()

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    assert version == AGENT_MEMORY_SCHEMA_VERSION
    assert {
        "agent_memory_settings",
        "agent_memory_jobs",
        "agent_memory_stage1_outputs",
        "agent_memory_candidates",
        "agent_memory_facts",
        "agent_memory_summaries",
        "agent_memory_usage",
        "agent_memory_workspace_files",
    } <= tables


def test_memory_store_does_not_own_runtime_control_tables_or_progression_state(tmp_path: Path) -> None:
    from seektalent_agent_memory.store import MemoryStore

    db_path = tmp_path / "agent_memory.sqlite3"
    MemoryStore(db_path).initialize()

    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        columns = {
            row[1]
            for table in tables
            if table.startswith("agent_memory_")
            for row in conn.execute(f"PRAGMA table_info({table})")
        }

    assert "runtime_control_runs" not in tables
    assert "runtime_control_events" not in tables
    assert "runtime_control_stage_outputs" not in tables
    assert "runtime_control_commands" not in tables
    assert columns & {
        "runtime_run_id",
        "runtime_status",
        "run_intent_id",
        "start_idempotency_key",
        "run_kind",
        "current_stage",
        "current_round",
        "latest_event_seq",
        "latest_checkpoint_id",
        "event_type",
        "output_kind",
    } == set()


def test_memory_store_migrates_unshipped_v1_schema_to_v2(tmp_path: Path) -> None:
    db_path = tmp_path / "agent_memory.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE agent_memory_settings (
                owner_user_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                memory_enabled INTEGER NOT NULL,
                review_required INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(owner_user_id, workspace_id)
            );
            PRAGMA user_version = 1;
            """
        )

    from seektalent_agent_memory.store import AGENT_MEMORY_SCHEMA_VERSION, MemoryStore

    store = MemoryStore(db_path)
    store.initialize()

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        stage1_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'agent_memory_stage1_outputs'"
        ).fetchone()

    assert version == AGENT_MEMORY_SCHEMA_VERSION == 2
    assert stage1_exists is not None


def test_memory_candidate_accept_reject_delete_and_clear_are_scoped(tmp_path: Path) -> None:
    from seektalent_agent_memory.store import MemoryStore

    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    candidate = store.save_candidate(
        candidate_id="memcand_1",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        conversation_id="agent_conv_1",
        category="recruiting_preferences",
        text="偏好 toB SaaS 平台经验",
        safe_excerpt="偏好 toB SaaS 平台经验",
        source_message_ids=["agent_msg_1"],
        status="pending_review",
        reason_code=None,
        created_at="2026-06-09T00:00:00.000000Z",
    )
    fact = store.accept_candidate(
        candidate_id=candidate.candidate_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        accepted_text="偏好 toB SaaS 平台经验",
        accepted_at="2026-06-09T00:00:01.000000Z",
    )
    rejected = store.save_candidate(
        candidate_id="memcand_2",
        owner_user_id="user_1",
        workspace_id="workspace_1",
        conversation_id="agent_conv_1",
        category="summary_style",
        text="回答要简短",
        safe_excerpt="回答要简短",
        source_message_ids=["agent_msg_2"],
        status="pending_review",
        reason_code=None,
        created_at="2026-06-09T00:00:02.000000Z",
    )
    store.reject_candidate(
        candidate_id=rejected.candidate_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        rejected_at="2026-06-09T00:00:03.000000Z",
    )
    store.delete_fact(
        fact_id=fact.fact_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        deleted_at="2026-06-09T00:00:04.000000Z",
    )
    cleared = store.clear_scope(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        cleared_at="2026-06-09T00:00:05.000000Z",
    )

    assert store.list_candidates(owner_user_id="user_1", workspace_id="workspace_1")[0].status == "accepted"
    assert store.list_candidates(owner_user_id="user_1", workspace_id="workspace_1")[1].status == "rejected"
    assert store.list_facts(owner_user_id="user_1", workspace_id="workspace_1") == []
    assert cleared.deleted_fact_count == 0


def test_deleting_fact_invalidates_active_summary_and_prevents_recall(tmp_path: Path) -> None:
    from seektalent_agent_memory.service import MemoryService
    from seektalent_agent_memory.store import MemoryStore

    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    service = MemoryService(store=store, now=lambda: "2026-06-10T00:00:00.000000Z")
    candidate = service.create_candidate(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        conversation_id="agent_conv_1",
        category="summary_style",
        text="候选人总结先讲业务匹配，再讲风险。",
        source_message_ids=["m1"],
    )
    fact = service.accept_candidate(
        candidate_id=candidate.candidate_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        accepted_text=None,
    )
    service.consolidate(owner_user_id="user_1", workspace_id="workspace_1")

    service.delete_fact(
        fact_id=fact.fact_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
    )

    recalled = service.recall_for_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        conversation_id="agent_conv_2",
        turn_id="turn_1",
    )

    assert recalled.context_text == ""
