from __future__ import annotations

from pathlib import Path

from tests.test_conversation_agent_routes import _bootstrap_and_login, _client, _csrf_header


def test_memory_write_routes_require_csrf_and_read_routes_require_auth(tmp_path: Path) -> None:
    client = _client(tmp_path)

    unauthenticated = client.get("/api/agent/memory/settings")
    assert unauthenticated.status_code == 401

    _bootstrap_and_login(client)
    missing_csrf = client.put(
        "/api/agent/memory/settings",
        json={"memoryEnabled": True, "generationEnabled": True, "recallEnabled": True, "reviewRequired": True},
    )
    assert missing_csrf.status_code == 403

    ok = client.put(
        "/api/agent/memory/settings",
        json={"memoryEnabled": False, "generationEnabled": False, "recallEnabled": True, "reviewRequired": True},
        headers=_csrf_header(client),
    )
    assert ok.status_code == 200
