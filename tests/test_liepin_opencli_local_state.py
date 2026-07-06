from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from seektalent.providers.liepin.opencli_local_state import locked_json_update, opencli_state_lock


@pytest.mark.skipif(os.name != "posix", reason="POSIX flock behavior only applies on POSIX")
def test_opencli_state_lock_is_reentrant_for_same_thread_same_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fcntl

    calls: list[int] = []
    original_flock = fcntl.flock

    def tracking_flock(fd: int, operation: int) -> None:
        calls.append(operation)
        original_flock(fd, operation)

    monkeypatch.setattr(fcntl, "flock", tracking_flock)
    path = tmp_path / "agent-events.json"

    with opencli_state_lock(path):
        with opencli_state_lock(path):
            path.write_text("{}", encoding="utf-8")

    assert calls.count(fcntl.LOCK_EX) == 1
    assert calls.count(fcntl.LOCK_UN) == 1


@pytest.mark.skipif(os.name != "posix", reason="cross-process lock contention test uses POSIX interpreters")
def test_locked_json_update_preserves_subprocess_appends(tmp_path: Path) -> None:
    path = tmp_path / "agent-events.json"
    subprocess_count = 16
    script = """
import json
import sys
import time
from pathlib import Path

from seektalent.providers.liepin.opencli_local_state import locked_json_update

path = Path(sys.argv[1])
index = int(sys.argv[2])

def update(state):
    events = state["events"]
    time.sleep(0.02)
    events.append({"index": index})
    return state

locked_json_update(
    path,
    {"schema_version": "seektalent.opencli_agent_events.v1", "events": []},
    update,
)
"""
    env = os.environ.copy()
    src_path = str(Path.cwd() / "src")
    env["PYTHONPATH"] = f"{src_path}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else src_path
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(path), str(index)],
            cwd=Path.cwd(),
            env=env,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(subprocess_count)
    ]

    errors = [stderr for process in processes if (stderr := process.communicate(timeout=10)[1]) or process.returncode]

    assert errors == []
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == "seektalent.opencli_agent_events.v1"
    assert sorted(event["index"] for event in loaded["events"]) == list(range(subprocess_count))


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
