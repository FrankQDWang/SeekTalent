from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from seektalent.providers.liepin.opencli_local_state import locked_json_update


def test_locked_json_update_preserves_concurrent_appends(tmp_path: Path) -> None:
    path = tmp_path / "agent-events.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "seektalent.opencli_agent_events.v1",
                "events": [],
            }
        ),
        encoding="utf-8",
    )

    def append_index(index: int) -> None:
        def update(state: object) -> dict[str, object]:
            assert isinstance(state, dict)
            events = state["events"]
            assert isinstance(events, list)
            time.sleep(0.001)
            events.append({"index": index})
            return state

        locked_json_update(
            path,
            {
                "schema_version": "seektalent.opencli_agent_events.v1",
                "events": [],
            },
            update,
        )

    threads = [threading.Thread(target=append_index, args=(index,)) for index in range(40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == "seektalent.opencli_agent_events.v1"
    assert sorted(event["index"] for event in loaded["events"]) == list(range(40))


def test_locked_json_update_preserves_dict_schema(tmp_path: Path) -> None:
    path = tmp_path / "agent-events.json"

    updated = locked_json_update(
        path,
        {
            "schema_version": "seektalent.opencli_agent_events.v1",
            "events": [],
        },
        lambda state: {
            **state,
            "events": [*state["events"], {"action_kind": "open_search"}],
        },
    )

    assert updated == {
        "schema_version": "seektalent.opencli_agent_events.v1",
        "events": [{"action_kind": "open_search"}],
    }
    assert json.loads(path.read_text(encoding="utf-8")) == updated
