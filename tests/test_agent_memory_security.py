from __future__ import annotations

from pathlib import Path

from tests.test_conversation_agent_routes import _client


def test_memory_routes_use_local_actor_without_workbench_auth(tmp_path: Path) -> None:
    client = _client(tmp_path)

    settings = client.get("/api/agent/memory/settings")
    assert settings.status_code == 200

    ok = client.put(
        "/api/agent/memory/settings",
        json={"memoryEnabled": True, "generationEnabled": True, "recallEnabled": True, "reviewRequired": True},
    )
    assert ok.status_code == 200
