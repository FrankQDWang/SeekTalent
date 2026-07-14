from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


def initial_state() -> dict[str, Any]:
    return {
        "scopes": {},
        "tabs": {},
        "businessResults": {},
        "diagnostics": [],
        "metrics": {},
        "eventCount": 0,
        "lastEvent": None,
    }


def reduce_state(state: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    """Apply one prototype lifecycle event without mutating the input state."""
    next_state = deepcopy(dict(state))
    kind = str(event["kind"])
    scope_id = event.get("scopeId")
    tab_token = event.get("tabToken")

    if kind == "scope_activated":
        next_state["scopes"][scope_id] = {
            "state": "active",
            "fenceToken": event["fenceToken"],
        }
    elif kind == "scope_superseded":
        next_state["scopes"][scope_id]["state"] = "superseded"
    elif kind == "scope_reclaim_requested":
        next_state["scopes"][scope_id]["state"] = "reclaim_requested"
    elif kind == "scope_reclaimed":
        next_state["scopes"][scope_id]["state"] = "reclaimed"
    elif kind == "tab_created":
        next_state["tabs"][tab_token] = {
            "scopeId": scope_id,
            "state": "owned",
            "kind": event.get("tabKind", "detail"),
        }
    elif kind == "tab_reclaim_requested":
        next_state["tabs"][tab_token]["state"] = "reclaim_requested"
    elif kind == "tab_reclaim_failed":
        next_state["tabs"][tab_token]["state"] = "reclaim_failed"
    elif kind == "tab_reclaimed":
        next_state["tabs"][tab_token]["state"] = "reclaimed"
        next_state["tabs"][tab_token]["outcome"] = event["outcome"]
    elif kind == "business_result":
        next_state["businessResults"][event["source"]] = deepcopy(event["result"])
    elif kind == "diagnostic":
        next_state["diagnostics"].append(
            {
                "code": event["code"],
                "scopeId": scope_id,
                "tabToken": tab_token,
            }
        )
    elif kind == "metric":
        next_state["metrics"][event["name"]] = event["value"]
    else:
        raise ValueError(f"unknown prototype event: {kind}")

    next_state["eventCount"] += 1
    next_state["lastEvent"] = deepcopy(dict(event))
    return next_state
