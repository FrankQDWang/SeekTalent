from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class ExecutionComponentHealth:
    name: str
    alive: bool
    last_heartbeat_at: str | None
    last_success_at: str | None
    first_failure_at: str | None
    first_failure_type: str | None
    failure_count: int
    restart_count: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class ExecutionHealthTracker:
    def __init__(
        self,
        name: str,
        *,
        initial: ExecutionComponentHealth | None = None,
    ) -> None:
        if initial is not None and initial.name != name:
            raise ValueError("execution_health_component_mismatch")
        self.name = name
        self._lock = threading.Lock()
        self._last_heartbeat_at = (
            None if initial is None else initial.last_heartbeat_at
        )
        self._last_success_at = (
            None if initial is None else initial.last_success_at
        )
        self._first_failure_at = (
            None if initial is None else initial.first_failure_at
        )
        self._first_failure_type = (
            None if initial is None else initial.first_failure_type
        )
        self._failure_count = (
            0 if initial is None else initial.failure_count
        )
        self._restart_count = (
            0 if initial is None else initial.restart_count
        )

    def heartbeat(self) -> None:
        with self._lock:
            self._last_heartbeat_at = _now()

    def success(self) -> None:
        with self._lock:
            now = _now()
            self._last_heartbeat_at = now
            self._last_success_at = now

    def failure(self, error: BaseException) -> None:
        with self._lock:
            now = _now()
            self._last_heartbeat_at = now
            self._failure_count += 1
            if self._first_failure_at is None:
                self._first_failure_at = now
                self._first_failure_type = type(error).__name__

    def restarted(self) -> None:
        with self._lock:
            self._restart_count += 1
            self._last_heartbeat_at = _now()

    def snapshot(self, *, alive: bool) -> ExecutionComponentHealth:
        with self._lock:
            return ExecutionComponentHealth(
                name=self.name,
                alive=alive,
                last_heartbeat_at=self._last_heartbeat_at,
                last_success_at=self._last_success_at,
                first_failure_at=self._first_failure_at,
                first_failure_type=self._first_failure_type,
                failure_count=self._failure_count,
                restart_count=self._restart_count,
            )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
