from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from seektalent_runtime_control.checkpoint_recovery import RuntimeRecoveryDecision
from seektalent_runtime_control.store import RuntimeControlStore


class RuntimeRecoveryService:
    def __init__(
        self,
        *,
        store: RuntimeControlStore,
        now: Callable[[], str] | None = None,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.now = now or _now
        self.fault_injector = fault_injector

    def recover_start_timeouts(self, *, resume_recoverable: bool) -> list[RuntimeRecoveryDecision]:
        now = self.now()
        decisions: list[RuntimeRecoveryDecision] = []
        for _ in range(100):
            decision = self.store.settle_next_expired_executor_lease(
                now=now,
                resume_recoverable=resume_recoverable,
                fault_injector=self.fault_injector,
            )
            if decision is None:
                break
            decisions.append(decision)
        return decisions


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
