from __future__ import annotations

from pathlib import Path

from seektalent_ui.agent_routes import LocalAgentRateLimiter
from tests.test_conversation_agent_routes import _ensure_local_actor, _client


def test_memory_management_routes_return_ui_ready_dtos(tmp_path: Path) -> None:
    client = _client(tmp_path)
    actor_payload = _ensure_local_actor(client)
    user = actor_payload["user"]
    service = client.app.state.agent_memory_service
    candidate = service.create_candidate(
        owner_user_id=user["userId"],
        workspace_id=user["workspaceId"],
        conversation_id="agent_conv_1",
        category="recruiting_preferences",
        text="偏好 toB SaaS 平台经验",
        source_message_ids=["agent_msg_1"],
    )

    settings = client.get("/api/agent/memory/settings")
    updated_settings = client.put(
        "/api/agent/memory/settings",
        json={
            "memoryEnabled": True,
            "generationEnabled": True,
            "recallEnabled": True,
            "reviewRequired": False,
            "candidateRetentionDays": 7,
            "rejectedRetentionDays": 3,
            "sourceExcerptRetentionDays": 2,
        },
    )
    candidates = client.get("/api/agent/memory/candidates")
    accepted = client.post(
        f"/api/agent/memory/candidates/{candidate.candidate_id}/accept",
        json={"text": "偏好 toB SaaS 平台经验"},
    )
    facts = client.get("/api/agent/memory/facts")
    fact_id = accepted.json()["fact"]["factId"]
    edited = client.patch(
        f"/api/agent/memory/facts/{fact_id}",
        json={"text": "偏好企业级 SaaS 平台经验"},
    )
    deleted = client.delete(f"/api/agent/memory/facts/{fact_id}")
    cleared = client.post("/api/agent/memory/clear")

    assert settings.status_code == 200, settings.text
    assert settings.json()["schemaVersion"] == "agent.memory.v2"
    assert "generationEnabled" in settings.json()["settings"]
    assert "recallEnabled" in settings.json()["settings"]
    assert "maxRolloutsPerStartup" in settings.json()["settings"]
    assert "memory_enabled" not in settings.json()["settings"]
    assert updated_settings.json()["settings"]["reviewRequired"] is False
    assert updated_settings.json()["settings"]["candidateRetentionDays"] == 7
    assert candidates.json()["candidates"][0]["candidateId"] == candidate.candidate_id
    assert accepted.status_code == 200, accepted.text
    assert facts.json()["facts"][0]["factId"] == fact_id
    assert edited.json()["fact"]["text"] == "偏好企业级 SaaS 平台经验"
    assert deleted.json()["fact"]["status"] == "deleted"
    assert cleared.json()["clearResult"]["deletedFactCount"] == 0


def test_memory_candidate_reject_route_updates_review_state(tmp_path: Path) -> None:
    client = _client(tmp_path)
    actor_payload = _ensure_local_actor(client)
    user = actor_payload["user"]
    service = client.app.state.agent_memory_service
    candidate = service.create_candidate(
        owner_user_id=user["userId"],
        workspace_id=user["workspaceId"],
        conversation_id="agent_conv_1",
        category="summary_style",
        text="回答要简短",
        source_message_ids=["agent_msg_1"],
    )

    rejected = client.post(
        f"/api/agent/memory/candidates/{candidate.candidate_id}/reject",
    )

    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["candidate"]["status"] == "rejected"


def test_memory_candidate_accept_route_supports_accept_as_is(tmp_path: Path) -> None:
    client = _client(tmp_path)
    actor_payload = _ensure_local_actor(client)
    user = actor_payload["user"]
    service = client.app.state.agent_memory_service
    candidate = service.create_candidate(
        owner_user_id=user["userId"],
        workspace_id=user["workspaceId"],
        conversation_id="agent_conv_1",
        category="summary_style",
        text="候选人总结先讲业务匹配，再讲风险。",
        source_message_ids=["agent_msg_1"],
    )

    accepted = client.post(
        f"/api/agent/memory/candidates/{candidate.candidate_id}/accept",
        json={},
    )

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["fact"]["text"] == "候选人总结先讲业务匹配，再讲风险。"


def test_memory_job_summary_and_usage_routes_are_schema_versioned(tmp_path: Path) -> None:
    client = _client(tmp_path)
    actor_payload = _ensure_local_actor(client)
    user = actor_payload["user"]
    service = client.app.state.agent_memory_service
    service.store.save_summary(
        summary_id="memsummary_1",
        owner_user_id=user["userId"],
        workspace_id=user["workspaceId"],
        summary_text="v1\n\n用户偏好候选人总结先讲业务匹配，再讲风险。",
        fact_ids=[],
        created_at="2026-06-10T00:00:00.000000Z",
    )
    service.recall_for_conversation(
        owner_user_id=user["userId"],
        workspace_id=user["workspaceId"],
        conversation_id="agent_conv_1",
        turn_id="turn_1",
    )

    jobs = client.get("/api/agent/memory/jobs")
    summaries = client.get("/api/agent/memory/summaries")
    usage = client.get("/api/agent/memory/usage")
    run = client.post("/api/agent/memory/jobs/run")

    assert jobs.status_code == 200, jobs.text
    assert summaries.status_code == 200, summaries.text
    assert usage.status_code == 200, usage.text
    assert run.status_code == 200, run.text
    assert summaries.json()["schemaVersion"] == "agent.memory.v2"
    assert summaries.json()["summaries"][0]["summaryId"] == "memsummary_1"
    assert usage.json()["usage"][0]["summaryId"] == "memsummary_1"


def test_memory_write_routes_are_rate_limited_with_memory_schema(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.app.state.agent_rate_limiter = LocalAgentRateLimiter(max_writes_per_minute=1)
    _ensure_local_actor(client)

    first = client.put(
        "/api/agent/memory/settings",
        json={"memoryEnabled": True, "generationEnabled": True, "recallEnabled": True, "reviewRequired": True},
    )
    second = client.post("/api/agent/memory/clear")

    assert first.status_code == 200, first.text
    assert second.status_code == 429
    assert second.json()["schemaVersion"] == "agent.memory.v2"
    assert second.json()["reasonCode"] == "agent_rate_limited"


def test_memory_missing_candidate_error_is_schema_versioned(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)

    response = client.post(
        "/api/agent/memory/candidates/missing/accept",
        json={"text": "偏好企业级 SaaS 平台经验"},
    )

    assert response.status_code == 400
    assert response.json()["schemaVersion"] == "agent.memory.v2"
    assert response.json()["reasonCode"] == "agent_memory_candidate_not_found"


def test_memory_retention_cleanup_route_returns_ui_ready_result(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)

    response = client.post("/api/agent/memory/retention/run")

    assert response.status_code == 200, response.text
    assert response.json()["schemaVersion"] == "agent.memory.v2"
    assert response.json()["cleanupResult"]["deletedFactCount"] == 0
