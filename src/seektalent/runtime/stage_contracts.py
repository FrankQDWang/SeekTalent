from __future__ import annotations

from typing import TypedDict


class ControllerStageState(TypedDict):
    call_id: str
    call_payload: dict[str, object]
    prompt: str
    prompt_cache_key: str | None
    prompt_cache_retention: str | None
    artifacts: list[str]
    started_at: str
    controller_latency_ms: int


class FinalizerStageState(TypedDict):
    call_id: str
    artifacts: list[str]
    latency_ms: int
