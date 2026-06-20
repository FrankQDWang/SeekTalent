from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from seektalent_conversation_agent.errors import ConversationAgentError
from seektalent_ui.workbench_store import WorkbenchUser


@dataclass
class LocalAgentRateLimiter:
    max_writes_per_minute: int = 60
    window_seconds: int = 60
    now: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def check(self, *, user_id: str, conversation_id: str) -> None:
        self._check_bucket(("user", user_id))
        if conversation_id != "new":
            self._check_bucket((user_id, conversation_id))

    def _check_bucket(self, key: tuple[str, str]) -> None:
        bucket = self._hits[key]
        current = self.now()
        while bucket and current - bucket[0] >= self.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.max_writes_per_minute:
            raise ConversationAgentError("agent_rate_limited")
        bucket.append(current)


def check_agent_write_rate(request: Request, *, user: WorkbenchUser, conversation_id: str) -> None:
    limiter = getattr(request.app.state, "agent_rate_limiter", None)
    if not isinstance(limiter, LocalAgentRateLimiter):
        return
    limiter.check(user_id=user.user_id, conversation_id=conversation_id)
