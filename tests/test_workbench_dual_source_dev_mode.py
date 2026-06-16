from __future__ import annotations

from pathlib import Path

from tests.test_workbench_api import _ensure_local_actor, _client, _create_session


def test_dev_mode_dual_source_session_uses_cts_and_liepin_without_secret_payloads(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _ensure_local_actor(client)

    session = _create_session(client, source_kinds=["cts", "liepin"])
    dev_mode = client.get("/api/workbench/dev-mode/status")
    final_top = client.get(f"/api/workbench/sessions/{session['sessionId']}/final-top10")

    assert dev_mode.status_code == 200
    assert final_top.status_code == 200
    assert {source["sourceKind"] for source in session["sourceCards"]} == {"cts", "liepin"}
    assert final_top.json()["items"] == []
    serialized = f"{dev_mode.text}\n{final_top.text}"
    assert "secret-token" not in serialized
    assert "raw_provider_payload" not in serialized
