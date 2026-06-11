from __future__ import annotations

from pathlib import Path

from tests.settings_factory import make_settings


def test_settings_resolves_agent_memory_db_path_under_workspace_root(tmp_path: Path) -> None:
    settings = make_settings(workspace_root=str(tmp_path))

    assert settings.agent_memory_path == tmp_path / ".seektalent" / "agent_memory.sqlite3"


def test_settings_resolves_agent_memory_workspace_path_under_workspace_root(tmp_path: Path) -> None:
    settings = make_settings(workspace_root=str(tmp_path))

    assert settings.agent_memory_workspace_path == tmp_path / ".seektalent" / "agent_memory_workspace"
    assert ".external/codex-reference" not in str(settings.agent_memory_workspace_path)


def test_memory_settings_include_generation_recall_and_pipeline_limits(tmp_path: Path) -> None:
    from seektalent_agent_memory.store import MemoryStore

    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()

    settings = store.get_settings(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        now="2026-06-10T00:00:00.000000Z",
    )

    assert settings.memory_enabled is True
    assert settings.generation_enabled is True
    assert settings.recall_enabled is True
    assert settings.review_required is False
    assert settings.max_rollouts_per_startup == 4
    assert settings.max_rollout_age_days == 30
    assert settings.min_rollout_idle_hours == 6
    assert settings.max_stage1_outputs_for_phase2 == 20
    assert settings.max_unused_days == 180
    assert settings.summary_token_budget == 1200
    assert settings.rejected_retention_days == 30


def test_memory_settings_update_persists_generation_and_recall_toggles(tmp_path: Path) -> None:
    from seektalent_agent_memory.service import MemoryService
    from seektalent_agent_memory.store import MemoryStore

    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    service = MemoryService(store=store, now=lambda: "2026-06-10T00:00:00.000000Z")

    updated = service.update_settings(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        memory_enabled=True,
        generation_enabled=False,
        recall_enabled=False,
        review_required=True,
    )
    recalled = service.recall_for_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        conversation_id="agent_conv_1",
        turn_id="turn_1",
    )

    assert updated.generation_enabled is False
    assert updated.recall_enabled is False
    assert store.get_settings(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        now="2026-06-10T00:00:01.000000Z",
    ).generation_enabled is False
    assert recalled.context_text == ""
    assert recalled.reason_code == "agent_memory_recall_disabled"


def test_memory_settings_update_persists_retention_limits(tmp_path: Path) -> None:
    from seektalent_agent_memory.service import MemoryService
    from seektalent_agent_memory.store import MemoryStore

    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    service = MemoryService(store=store, now=lambda: "2026-06-10T00:00:00.000000Z")

    updated = service.update_settings(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        memory_enabled=True,
        generation_enabled=True,
        recall_enabled=True,
        review_required=True,
        candidate_retention_days=7,
        rejected_retention_days=3,
        source_excerpt_retention_days=2,
    )

    assert updated.candidate_retention_days == 7
    assert updated.rejected_retention_days == 3
    assert updated.source_excerpt_retention_days == 2
    persisted = store.get_settings(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        now="2026-06-10T00:00:01.000000Z",
    )
    assert persisted.candidate_retention_days == 7
    assert persisted.rejected_retention_days == 3
    assert persisted.source_excerpt_retention_days == 2


def test_memory_retention_sets_expiry_and_cleanup_removes_expired_review_data(tmp_path: Path) -> None:
    from seektalent_agent_memory.service import MemoryService
    from seektalent_agent_memory.store import MemoryStore

    now_value = {"value": "2026-06-10T00:00:00.000000Z"}
    store = MemoryStore(tmp_path / "agent_memory.sqlite3")
    store.initialize()
    service = MemoryService(store=store, now=lambda: now_value["value"])
    service.update_settings(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        memory_enabled=True,
        generation_enabled=True,
        recall_enabled=True,
        review_required=True,
        candidate_retention_days=1,
        rejected_retention_days=1,
        source_excerpt_retention_days=1,
    )
    accepted_candidate = service.create_candidate(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        conversation_id="agent_conv_1",
        category="summary_style",
        text="候选人总结先讲业务匹配，再讲风险。",
        source_message_ids=["m1"],
    )
    rejected_candidate = service.create_candidate(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        conversation_id="agent_conv_1",
        category="recruiting_preferences",
        text="偏好 toB SaaS 平台经验。",
        source_message_ids=["m2"],
    )

    fact = service.accept_candidate(
        candidate_id=accepted_candidate.candidate_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
        accepted_text=None,
    )
    rejected = service.reject_candidate(
        candidate_id=rejected_candidate.candidate_id,
        owner_user_id="user_1",
        workspace_id="workspace_1",
    )

    assert fact.expires_at == "2026-06-11T00:00:00.000000Z"
    assert rejected.expires_at == "2026-06-11T00:00:00.000000Z"

    now_value["value"] = "2026-06-12T00:00:00.000000Z"
    recalled = service.recall_for_conversation(
        owner_user_id="user_1",
        workspace_id="workspace_1",
        conversation_id="agent_conv_2",
        turn_id="turn_1",
    )
    cleanup = service.run_retention_cleanup(owner_user_id="user_1", workspace_id="workspace_1")

    assert recalled.fact_ids == []
    assert cleanup.deleted_fact_count == 1
    assert cleanup.purged_rejected_candidate_count == 1
    facts = store.list_facts(owner_user_id="user_1", workspace_id="workspace_1", include_deleted=True)
    assert facts[0].status == "deleted"
    assert facts[0].safe_evidence_excerpt is None
